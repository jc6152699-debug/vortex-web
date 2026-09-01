"""
Orquestación de extremo a extremo: arma los patrones de carga (DL, PL,
LL, sismo en X y en Y), resuelve el análisis matricial para cada uno,
combina resultados según las combinaciones de carga gobernantes y corre
la verificación de diseño de cada paral y cada viga del modelo.

Reutilizado tanto por `examples/run_example.py` como por la interfaz
gráfica (`vortex.gui`), para no duplicar la lógica de combinación de
patrones de carga.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..geometry.model import MemberKind, RackModel
from ..loads.dead_live import dead_load_uprights
from ..loads.combinations import lrfd_combinations, LoadCase, Combination
from ..loads import seismic as sm
from ..design.upright_cfs import check_upright_compression_bending, UprightCheckResult
from ..design.beam import check_beam, check_deflection, BeamCheckResult
from .solve import analyze, AnalysisResult, MemberLoad, NodalLoad


@dataclass
class SeismicInputs:
    soil_type: str
    aa: float
    av: float
    essential: bool = False
    hazardous_contents: bool = False
    public_access: bool = False
    pl_promedio_ratio: float = 1.0   # PLpromedio/PLmaxima, dirección longitudinal


@dataclass
class PipelineInputs:
    pl_per_level_kn: float           # por bahía y nivel
    ll_kn_m2: float
    seismic: SeismicInputs
    k_long: float = 1.7                # factor de longitud efectiva, dirección no arriostrada
    k_trans: float = 1.0                 # factor de longitud efectiva, dirección arriostrada


@dataclass
class MemberResultRow:
    member_id: int
    label: str
    kind: str
    combo: str
    ratio: float
    detail: str
    raw_force: float = 0.0   # kN (parales, P) o kN*m (vigas, Mmax) — demanda gobernante


@dataclass
class PipelineResult:
    patterns: Dict[str, AnalysisResult]
    seismic_transversal: sm.SeismicResult
    seismic_longitudinal: sm.SeismicResult
    combos: List[Combination]
    member_rows: Dict[int, MemberResultRow] = field(default_factory=dict)

    def max_ratio(self) -> float:
        return max((r.ratio for r in self.member_rows.values()), default=0.0)


def _nodes_at_elevation(model: RackModel, z: float, tol: float = 1e-6) -> List[int]:
    return [nid for nid, n in model.nodes.items() if abs(n.z - z) < tol]


def run_full_check(model: RackModel, inputs: PipelineInputs) -> PipelineResult:
    dl_by_member = dead_load_uprights(model)
    dl_total = sum(dl_by_member.values())
    n_levels = model.n_levels
    dl_per_level = dl_total / n_levels if n_levels else 0.0
    n_bays_total = model.n_bays

    dl_loads = [
        MemberLoad(member_id=mid, wz=-(w / model.member_length(model.members[mid])))
        for mid, w in dl_by_member.items() if model.member_length(model.members[mid]) > 1e-9
    ]
    pl_loads = []
    for m in model.members_of_kind(MemberKind.BEAM):
        w_pl = (inputs.pl_per_level_kn / 2.0) / model.bay_length
        pl_loads.append(MemberLoad(member_id=m.id, wz=-w_pl))

    pl_total = inputs.pl_per_level_kn * n_bays_total
    levels_w = [
        sm.LevelWeight(i, model.level_elevations[i],
                        weight_kn=sm.seismic_weight(pl=pl_total, dl=dl_per_level, ll=0.0, plrf=1.0))
        for i in range(1, n_levels + 1)
    ]
    height_total = model.level_elevations[-1] if model.level_elevations else 0.0

    seis_trans = sm.compute_seismic(
        direction=sm.SeismicDirection.TRANSVERSAL,
        soil_type=inputs.seismic.soil_type, aa=inputs.seismic.aa, av=inputs.seismic.av,
        pl=pl_total, dl=dl_per_level, ll=0.0, height_m=height_total, levels=levels_w,
        essential=inputs.seismic.essential, hazardous_contents=inputs.seismic.hazardous_contents,
        public_access=inputs.seismic.public_access,
    )
    seis_long = sm.compute_seismic(
        direction=sm.SeismicDirection.LONGITUDINAL,
        soil_type=inputs.seismic.soil_type, aa=inputs.seismic.aa, av=inputs.seismic.av,
        pl=pl_total, dl=dl_per_level, ll=0.0, height_m=height_total, levels=levels_w,
        pl_promedio=inputs.seismic.pl_promedio_ratio * pl_total, pl_maxima=pl_total,
        essential=inputs.seismic.essential, hazardous_contents=inputs.seismic.hazardous_contents,
        public_access=inputs.seismic.public_access,
    )

    el_x_loads, el_y_loads = [], []
    for lv, fx in seis_long.fx_by_level.items():
        nids = _nodes_at_elevation(model, model.level_elevations[lv])
        for nid in nids:
            el_x_loads.append(NodalLoad(node_id=nid, fx=fx / len(nids)))
    for lv, fy in seis_trans.fx_by_level.items():
        nids = _nodes_at_elevation(model, model.level_elevations[lv])
        for nid in nids:
            el_y_loads.append(NodalLoad(node_id=nid, fy=fy / len(nids)))

    patterns = {
        "DL": analyze(model, [], dl_loads),
        "PL": analyze(model, [], pl_loads),
        "EL_X": analyze(model, el_x_loads, []),
        "EL_Y": analyze(model, el_y_loads, []),
    }

    combos = lrfd_combinations(apply_el_factor_10=True)
    combo_gravity = next(c for c in combos if c.id == "2" and "granizo" in c.description)
    combo_seismic = next(c for c in combos if c.id == "5")

    result = PipelineResult(
        patterns=patterns, seismic_transversal=seis_trans, seismic_longitudinal=seis_long,
        combos=combos,
    )

    w_pl_beam = (inputs.pl_per_level_kn / 2.0) / model.bay_length

    for member in model.members_of_kind(MemberKind.UPRIGHT):
        best_ratio, best_combo, best_detail, best_p = -1.0, "", "", 0.0
        for combo, el_pattern in ((combo_gravity, None), (combo_seismic, "EL_X"), (combo_seismic, "EL_Y")):
            f_dl = combo.factors.get(LoadCase.DL, 0.0)
            f_pl = combo.factors.get(LoadCase.PL, 0.0)
            f_el = combo.factors.get(LoadCase.EL, 0.0)
            mf_dl = patterns["DL"].member_forces[member.id]
            mf_pl = patterns["PL"].member_forces[member.id]
            P = f_dl * mf_dl.P_i + f_pl * mf_pl.P_i
            M2 = f_dl * mf_dl.M2_i + f_pl * mf_pl.M2_i
            M3 = f_dl * mf_dl.M3_i + f_pl * mf_pl.M3_i
            V2 = f_dl * mf_dl.V2_i + f_pl * mf_pl.V2_i
            V3 = f_dl * mf_dl.V3_i + f_pl * mf_pl.V3_i
            if el_pattern is not None:
                mf_el = patterns[el_pattern].member_forces[member.id]
                P += f_el * mf_el.P_i
                M2 += f_el * mf_el.M2_i
                M3 += f_el * mf_el.M3_i
                V2 += f_el * mf_el.V2_i
                V3 += f_el * mf_el.V3_i
            L = model.member_length(member)
            KLy = inputs.k_long * L
            KLz = inputs.k_trans * L
            r = check_upright_compression_bending(
                member.section, combo.id, P=abs(P), M2=abs(M2), M3=abs(M3),
                V2=abs(V2), V3=abs(V3), KLy=KLy, KLz=KLz,
            )
            if r.ratio > best_ratio:
                best_ratio, best_combo, best_p = r.ratio, combo.label(), abs(P)
                best_detail = (
                    f"P={abs(P):.1f}/{r.Pa:.1f}kN, M2={abs(M2):.2f}/{r.Ma2:.2f}, "
                    f"M3={abs(M3):.2f}/{r.Ma3:.2f}kN·m, V={max(abs(V2),abs(V3)):.1f}/{r.Va:.1f}kN"
                )
        result.member_rows[member.id] = MemberResultRow(
            member_id=member.id, label=member.label, kind="Paral",
            combo=best_combo, ratio=best_ratio, detail=best_detail, raw_force=best_p,
        )

    for member in model.members_of_kind(MemberKind.BEAM):
        f_dl = combo_gravity.factors.get(LoadCase.DL, 0.0)
        f_pl = combo_gravity.factors.get(LoadCase.PL, 0.0)
        mf_dl = patterns["DL"].member_forces[member.id]
        mf_pl = patterns["PL"].member_forces[member.id]
        w_dl_beam = dl_by_member.get(member.id, 0.0) / model.bay_length

        MF = type(mf_dl)
        mf_combo = MF(
            member_id=member.id,
            P_i=0.0, V2_i=0.0, V3_i=f_dl * mf_dl.V3_i + f_pl * mf_pl.V3_i,
            T_i=0.0, M2_i=f_dl * mf_dl.M2_i + f_pl * mf_pl.M2_i, M3_i=0.0,
            P_j=0.0, V2_j=0.0, V3_j=f_dl * mf_dl.V3_j + f_pl * mf_pl.V3_j,
            T_j=0.0, M2_j=f_dl * mf_dl.M2_j + f_pl * mf_pl.M2_j, M3_j=0.0,
            r_int_z=(0.0, 0.0), r_int_y=mf_pl.r_int_y,
        )
        r = check_beam(
            member.section, combo_gravity.label(), mf_combo,
            w_local_z=-(f_dl * w_dl_beam + f_pl * w_pl_beam), L=model.bay_length,
        )
        r = check_deflection(r, member.section, mf_pl, w_local_z_service=-w_pl_beam, L=model.bay_length)
        result.member_rows[member.id] = MemberResultRow(
            member_id=member.id, label=member.label, kind="Viga",
            combo=combo_gravity.label(), ratio=r.ratio,
            detail=f"M={r.Mmax:.2f}kN·m, δ={r.deflection_max * 1000:.1f}mm (lim L/{180:.0f})",
            raw_force=r.Mmax,
        )

    return result
