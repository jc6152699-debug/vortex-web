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
from ..design.beam import check_beam, check_deflection, BeamCheckResult, beam_moment_at, beam_shear_at
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
    # Factor de carga EL en las combinaciones 5/6 de LRFD (numeral 2.2):
    # True -> 1.0 (relajación permitida cuando el sismo se calculó según
    # el numeral 2.7, ver loads.combinations); False -> 1.5 (valor base
    # de la ecuación LRFD sin relajar, usado literalmente en el proyecto
    # de referencia — ver examples/run_example.py). Ambos son válidos
    # según la norma; esta opción permite reproducir la elección exacta
    # de un proyecto existente.
    apply_el_factor_10: bool = True


@dataclass
class MemberResultRow:
    member_id: int
    label: str
    kind: str
    combo: str
    ratio: float
    detail: str
    raw_force: float = 0.0   # kN (parales, P) o kN*m (vigas, Mmax) — demanda gobernante
    length_m: float = 0.0       # m, longitud del elemento (columna "H[m]" del anexo)
    upright_check: Optional[UprightCheckResult] = None   # resultado completo (parales)
    beam_check: Optional[BeamCheckResult] = None            # resultado completo (vigas)


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

    combos = lrfd_combinations(apply_el_factor_10=inputs.apply_el_factor_10)
    combo_gravity = next(c for c in combos if c.id == "2" and "granizo" in c.description)
    combo_seismic = next(c for c in combos if c.id == "5")

    result = PipelineResult(
        patterns=patterns, seismic_transversal=seis_trans, seismic_longitudinal=seis_long,
        combos=combos,
    )

    w_pl_beam = (inputs.pl_per_level_kn / 2.0) / model.bay_length

    for member in model.members_of_kind(MemberKind.UPRIGHT):
        best_ratio, best_combo, best_detail, best_p, best_r = -1.0, "", "", 0.0, None
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
                best_ratio, best_combo, best_p, best_r = r.ratio, combo.label(), abs(P), r
                best_detail = (
                    f"P={abs(P):.1f}/{r.Pa:.1f}kN, M2={abs(M2):.2f}/{r.Ma2:.2f}, "
                    f"M3={abs(M3):.2f}/{r.Ma3:.2f}kN·m, V={max(abs(V2),abs(V3)):.1f}/{r.Va:.1f}kN"
                )
        result.member_rows[member.id] = MemberResultRow(
            member_id=member.id, label=member.label, kind="Paral",
            combo=best_combo, ratio=best_ratio, detail=best_detail, raw_force=best_p,
            length_m=model.member_length(member), upright_check=best_r,
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
            raw_force=r.Mmax, length_m=model.bay_length, beam_check=r,
        )

    return result


@dataclass
class ElementForceRow:
    """Una fila de la tabla 'Element Forces - Frames', en el mismo formato
    de columnas que usa SAP2000 — para chequeo cruzado directo contra una
    memoria de cálculo existente."""
    item: int
    frame: int         # id del elemento (equivalente a "Frame" en SAP2000)
    label: str
    output_case: str
    station_m: float
    P: float
    M3: float
    V2: float
    M2: float
    V3: float


def _sap_style_combo_label(combo: Combination) -> str:
    """Etiqueta compacta 'DL+EL+PL' (sólo términos con factor no nulo, sin
    LL/SL/RL/Imp), igual a la convención de nombres de combinación que usa
    SAP2000 en la columna OutputCase del proyecto de referencia (p.ej.
    '1.4DL+1.2PL', '1.2DL+1.5EL+0.85PL')."""
    terms = []
    for lc in (LoadCase.DL, LoadCase.EL, LoadCase.PL):
        f = combo.factors.get(lc, 0.0)
        if f:
            terms.append(f"{f:g}{lc.value}")
    return "+".join(terms)


def element_forces_table(
    model: RackModel,
    result: PipelineResult,
    inputs: PipelineInputs,
    el_pattern: str = "EL_X",
    n_stations: int = 3,
    kinds: tuple = (MemberKind.UPRIGHT, MemberKind.BEAM),
) -> List[ElementForceRow]:
    """
    Reconstruye la tabla 'TABLE: Element Forces - Frames' (columnas
    Frame, OutputCase, P, M3, V2, M2, V3) a partir de los patrones de
    carga ya resueltos en `result.patterns`, para las tres combinaciones
    reales del proyecto de referencia (1.4DL+1.2PL, 1.2DL+1.4PL,
    1.2DL+1.5EL+0.85PL), muestreada en `n_stations` estaciones por
    elemento (3 por defecto, como en la tabla original: extremo i, medio,
    extremo j).

    M2/V3 de vigas se calculan con la fórmula exacta de equilibrio bajo
    carga uniforme (`design.beam.beam_moment_at`/`beam_shear_at`); el
    resto de componentes (P, M3, V2 siempre; M2/V3 de parales y
    diagonales) se interpolan linealmente entre los extremos, lo cual es
    EXACTO en este modelo: el peso propio (única carga distribuida sobre
    parales) actúa en la dirección global Z, que para un paral vertical
    es puramente axial (sin componente transversal), y ningún patrón de
    carga aplica carga distribuida en el eje local y de ningún elemento.
    """
    dl_by_member = dead_load_uprights(model)
    w_pl_beam = (inputs.pl_per_level_kn / 2.0) / model.bay_length

    combo_ids = ("1", "2", "5")
    seen_ids = set()
    combos = []
    for c in result.combos:
        if c.id in combo_ids and c.id not in seen_ids:
            combos.append(c)
            seen_ids.add(c.id)  # una sola variante por id (LL=SL=RL=0 en este proyecto)

    rows: List[ElementForceRow] = []
    item = 1
    for member in model.members.values():
        if member.kind not in kinds:
            continue
        L = model.member_length(member)
        w_dl = dl_by_member.get(member.id, 0.0) / L if L > 1e-9 else 0.0

        for combo in combos:
            f_dl = combo.factors.get(LoadCase.DL, 0.0)
            f_pl = combo.factors.get(LoadCase.PL, 0.0)
            f_el = combo.factors.get(LoadCase.EL, 0.0)

            mf_dl = result.patterns["DL"].member_forces[member.id]
            mf_pl = result.patterns["PL"].member_forces[member.id]
            mf_el = result.patterns[el_pattern].member_forces[member.id] if f_el else None

            def combine(attr_i: str, attr_j: str):
                vi = f_dl * getattr(mf_dl, attr_i) + f_pl * getattr(mf_pl, attr_i)
                vj = f_dl * getattr(mf_dl, attr_j) + f_pl * getattr(mf_pl, attr_j)
                if mf_el is not None:
                    vi += f_el * getattr(mf_el, attr_i)
                    vj += f_el * getattr(mf_el, attr_j)
                return vi, vj

            P_i, P_j = combine("P_i", "P_j")
            M3_i, M3_j = combine("M3_i", "M3_j")
            V2_i, V2_j = combine("V2_i", "V2_j")
            M2_i, M2_j = combine("M2_i", "M2_j")
            V3_i, V3_j = combine("V3_i", "V3_j")

            w_z_combo = (f_dl * w_dl + f_pl * w_pl_beam) if member.kind == MemberKind.BEAM else 0.0

            MF = type(mf_dl)
            mf_combo = MF(
                member_id=member.id,
                P_i=P_i, V2_i=V2_i, V3_i=V3_i, T_i=0.0, M2_i=M2_i, M3_i=M3_i,
                P_j=P_j, V2_j=V2_j, V3_j=V3_j, T_j=0.0, M2_j=M2_j, M3_j=M3_j,
                r_int_z=(0.0, 0.0), r_int_y=(0.0, 0.0),
            )

            for k in range(n_stations):
                x = L * k / (n_stations - 1) if n_stations > 1 else 0.0
                t = x / L if L > 1e-9 else 0.0
                P = P_i + (P_j - P_i) * t
                M3 = M3_i + (M3_j - M3_i) * t
                V2 = V2_i + (V2_j - V2_i) * t
                if member.kind == MemberKind.BEAM:
                    M2 = beam_moment_at(mf_combo, w_z_combo, L, x)
                    V3 = beam_shear_at(mf_combo, w_z_combo, x)
                else:
                    M2 = M2_i + (M2_j - M2_i) * t
                    V3 = V3_i + (V3_j - V3_i) * t
                rows.append(ElementForceRow(
                    item=item, frame=member.id, label=member.label,
                    output_case=_sap_style_combo_label(combo), station_m=x,
                    P=P, M3=M3, V2=V2, M2=M2, V3=V3,
                ))
                item += 1
    return rows


def write_element_forces_csv(rows: List[ElementForceRow], path: str) -> str:
    """Guarda `element_forces_table` en un .csv con las mismas columnas
    (Frame, OutputCase, P, M3, V2, M2, V3) que la tabla 'Element Forces -
    Frames' exportada de SAP2000, para diff directo en Excel."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ITEM", "Frame", "Label", "OutputCase", "Station[m]",
                          "P[KN]", "M3[KN-m]", "V2[KN]", "M2[KN-m]", "V3[KN]"])
        for r in rows:
            writer.writerow([r.item, r.frame, r.label, r.output_case, f"{r.station_m:.4f}",
                              f"{r.P:.4f}", f"{r.M3:.4f}", f"{r.V2:.4f}", f"{r.M2:.4f}", f"{r.V3:.4f}"])
    return path
