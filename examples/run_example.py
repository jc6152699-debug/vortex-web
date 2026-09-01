"""
Ejemplo de extremo a extremo: reconstruye el caso de referencia de los
anexos del proyecto (estantería selectiva de 9.50 m de altura, 6 niveles,
2400 kg por par de vigas, ciudad Medellín — perfil de suelo D), corre el
pipeline completo (geometría -> cargas -> sismo -> análisis matricial 3D
-> verificación de elementos -> memoria de cálculo) y guarda el reporte
.docx resultante.

Ejecutar:  python3 examples/run_example.py
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vortex.geometry import RackParameters, build_selective_rack
from vortex.geometry.model import MemberKind
from vortex.sections import default_catalog
from vortex.loads import seismic as sm
from vortex.loads.dead_live import dead_load_uprights
from vortex.loads.combinations import lrfd_combinations, LoadCase
from vortex.analysis.solve import analyze, NodalLoad, MemberLoad
from vortex.design.upright_cfs import check_upright_compression_bending
from vortex.design.beam import check_beam, check_deflection
from vortex.report import ProjectInfo, ReportData, generate_memoria
from vortex.units import kgf_to_kn


def main() -> None:
    catalog = default_catalog()
    upright = catalog["PARAL 122x2.5mm"]
    beam = catalog["VIGA CAJA 160x60x1.5mm"]  # sección real del proyecto de referencia
    brace = catalog["DIAGONAL TUBULAR 30x30x2.0mm"]

    n_bays = 4
    level_heights = [1.20, 1.80, 1.80, 1.80, 1.80, 1.80]
    params = RackParameters(
        n_bays=n_bays, bay_length=2.44, frame_depth=1.06,
        level_heights=level_heights,
        upright_section=upright, beam_section=beam, brace_section=brace,
        base_fixity="pinned",
    )
    model = build_selective_rack(params)
    print(f"Modelo: {len(model.nodes)} nudos, {len(model.members)} elementos "
          f"({params.n_frames} marcos x {n_bays} bahías x {len(level_heights)} niveles)")

    # ---------------- Cargas ------------------------------------------
    pl_per_level_kgf = 2400.0
    pl_per_level_kn = kgf_to_kn(pl_per_level_kgf)   # ~23.54 kN, por bahía y nivel
    ll_kn_m2 = 0.0

    dl_by_member = dead_load_uprights(model)

    dl_loads = [
        MemberLoad(member_id=mid, wz=-(w / model.member_length(model.members[mid])))
        for mid, w in dl_by_member.items() if model.member_length(model.members[mid]) > 1e-9
    ]

    pl_loads = []
    for m in model.members_of_kind(MemberKind.BEAM):
        w_pl = (pl_per_level_kn / 2.0) / params.bay_length  # kN/m, repartido frente+fondo
        pl_loads.append(MemberLoad(member_id=m.id, wz=-w_pl))

    # ---------------- Sismo (NTC 5689 numeral 2.7) ---------------------
    height_total = model.level_elevations[-1]
    dl_total = sum(dl_by_member.values())
    n_levels = len(level_heights)
    n_bays_total = n_bays * (params.n_frames)
    dl_per_level = dl_total / n_levels

    levels_w = [
        sm.LevelWeight(i, model.level_elevations[i],
                        weight_kn=sm.seismic_weight(
                            pl=pl_per_level_kn * n_bays, dl=dl_per_level, ll=0.0, plrf=1.0))
        for i in range(1, n_levels + 1)
    ]

    seis_trans = sm.compute_seismic(
        direction=sm.SeismicDirection.TRANSVERSAL, soil_type="D", aa=0.15, av=0.20,
        pl=pl_per_level_kn * n_bays, dl=dl_per_level, ll=0.0,
        height_m=height_total, levels=levels_w,
    )
    seis_long = sm.compute_seismic(
        direction=sm.SeismicDirection.LONGITUDINAL, soil_type="D", aa=0.15, av=0.20,
        pl=pl_per_level_kn * n_bays, dl=dl_per_level, ll=0.0,
        height_m=height_total, levels=levels_w,
        pl_promedio=0.76 * pl_per_level_kn * n_bays, pl_maxima=pl_per_level_kn * n_bays,
    )
    print(f"Sismo transversal: Cs={seis_trans.cs:.5f}  V={seis_trans.v_base:.2f} kN")
    print(f"Sismo longitudinal: Cs={seis_long.cs:.5f}  V={seis_long.v_base:.2f} kN")

    def _nodes_at_level(lv: int):
        elev = model.level_elevations[lv]
        return [nid for nid, n in model.nodes.items() if abs(n.z - elev) < 1e-6]

    el_x_loads = []
    for lv, fx in seis_long.fx_by_level.items():
        nids = _nodes_at_level(lv)
        for nid in nids:
            el_x_loads.append(NodalLoad(node_id=nid, fx=fx / len(nids)))

    el_y_loads = []
    for lv, fy in seis_trans.fx_by_level.items():
        nids = _nodes_at_level(lv)
        for nid in nids:
            el_y_loads.append(NodalLoad(node_id=nid, fy=fy / len(nids)))

    # ---------------- Análisis (superposición lineal) -------------------
    res_dl = analyze(model, [], dl_loads)
    res_pl = analyze(model, [], pl_loads)
    res_elx = analyze(model, el_x_loads, [])
    res_ely = analyze(model, el_y_loads, [])
    print("Análisis matricial 3D resuelto (4 patrones: DL, PL, EL_X, EL_Y)")

    # ---------------- Combinación gobernante (LRFD 5: sismo) -----------
    combos = lrfd_combinations(apply_el_factor_10=True)
    combo5 = next(c for c in combos if c.id == "5")
    f_dl = combo5.factors.get(LoadCase.DL, 0.0)
    f_pl = combo5.factors.get(LoadCase.PL, 0.0)
    f_el = combo5.factors.get(LoadCase.EL, 0.0)

    design_rows = []

    # Paral crítico: base del marco intermedio, tramo piso->nivel1
    base_upright = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.frame_index == n_bays // 2 and m.level_index == 0 and m.side == "frente"
    )
    mf_dl = res_dl.member_forces[base_upright.id]
    mf_pl = res_pl.member_forces[base_upright.id]
    mf_elx = res_elx.member_forces[base_upright.id]
    mf_ely = res_ely.member_forces[base_upright.id]

    P = f_dl * mf_dl.P_i + f_pl * mf_pl.P_i + f_el * max(abs(mf_elx.P_i), abs(mf_ely.P_i))
    M2 = f_dl * mf_dl.M2_i + f_pl * mf_pl.M2_i + f_el * max(abs(mf_elx.M2_i), abs(mf_ely.M2_i))
    M3 = f_dl * mf_dl.M3_i + f_pl * mf_pl.M3_i + f_el * max(abs(mf_elx.M3_i), abs(mf_ely.M3_i))

    KLy = 1.7 * level_heights[0]  # dirección no arriostrada (longitudinal), k=1.7
    KLz = 1.0 * level_heights[0]   # dirección arriostrada (transversal, diagonales)
    upright_result = check_upright_compression_bending(
        upright, combo5.id, P=abs(P), M2=abs(M2), M3=abs(M3), KLy=KLy, KLz=KLz,
    )
    design_rows.append({
        "elemento": base_upright.label, "tipo": "Paral",
        "combo": combo5.label(),
        "demanda_capacidad": f"P={abs(P):.1f}/{upright_result.Pa:.1f} kN",
        "ratio": upright_result.ratio,
    })

    # Viga crítica: primer nivel, bahía central
    critical_beam = next(
        m for m in model.members_of_kind(MemberKind.BEAM)
        if m.bay_index == n_bays // 2 and m.level_index == 1 and m.side == "frente"
    )
    mf_beam_pl = res_pl.member_forces[critical_beam.id]
    mf_beam_dl = res_dl.member_forces[critical_beam.id]
    w_pl_beam = (pl_per_level_kn / 2.0) / params.bay_length
    w_dl_beam = dl_by_member[critical_beam.id] / params.bay_length

    combo2 = next(c for c in combos if c.id == "2" and "granizo" in c.description)
    f2_dl, f2_pl = combo2.factors.get(LoadCase.DL, 0.0), combo2.factors.get(LoadCase.PL, 0.0)

    mf_combo = type(mf_beam_pl)(
        member_id=critical_beam.id,
        P_i=f2_dl * mf_beam_dl.P_i + f2_pl * mf_beam_pl.P_i,
        V2_i=0.0, V3_i=f2_dl * mf_beam_dl.V3_i + f2_pl * mf_beam_pl.V3_i,
        T_i=0.0, M2_i=f2_dl * mf_beam_dl.M2_i + f2_pl * mf_beam_pl.M2_i, M3_i=0.0,
        P_j=0.0, V2_j=0.0, V3_j=f2_dl * mf_beam_dl.V3_j + f2_pl * mf_beam_pl.V3_j,
        T_j=0.0, M2_j=f2_dl * mf_beam_dl.M2_j + f2_pl * mf_beam_pl.M2_j, M3_j=0.0,
        r_int_z=(0.0, 0.0), r_int_y=mf_beam_pl.r_int_y,
    )
    beam_result = check_beam(
        beam, combo2.label(), mf_combo,
        w_local_z=-(f2_dl * w_dl_beam + f2_pl * w_pl_beam), L=params.bay_length,
    )
    beam_result = check_deflection(
        beam_result, beam, mf_beam_pl, w_local_z_service=-w_pl_beam, L=params.bay_length,
    )
    design_rows.append({
        "elemento": critical_beam.label, "tipo": "Viga",
        "combo": combo2.label(),
        "demanda_capacidad": f"M={beam_result.Mmax:.2f} kN·m, δ={beam_result.deflection_max * 1000:.1f}mm",
        "ratio": beam_result.ratio,
    })

    print("Verificación de elementos:")
    for row in design_rows:
        print(f"  {row['elemento']:30s} {row['tipo']:6s} ratio={row['ratio']:.2f}  {row['demanda_capacidad']}")

    # ---------------- Memoria de cálculo --------------------------------
    project = ProjectInfo(
        titulo="ESTANTERÍA SELECTIVA 9.50m x 6 NIVELES x 2400kg/NIVEL",
        ciudad="Medellín", fecha=datetime.date.today().strftime("%Y-%m-%d"),
        ingeniero="[Nombre del ingeniero calculista]",
        especialidad="Esp. Estructuras", matricula="[M.P.]",
        cliente="[Cliente]",
    )
    report_data = ReportData(
        project=project, model=model,
        dl_note="DL = Peso propio de la estructura (calculado automáticamente por sección y material).",
        ll_kn_m2=ll_kn_m2, pl_per_level_kn=pl_per_level_kn,
        seismic_transversal=seis_trans, seismic_longitudinal=seis_long,
        material_names=[upright.material.name, beam.material.name],
        upright_section=upright, beam_section=beam, brace_section=brace,
        method_name="LRFD", combos=combos, design_rows=design_rows,
    )
    out_path = os.path.join(os.path.dirname(__file__), "memoria_ejemplo.docx")
    generate_memoria(report_data, out_path)
    print(f"\nMemoria de cálculo generada: {out_path}")


if __name__ == "__main__":
    main()
