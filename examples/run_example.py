"""
Ejemplo de extremo a extremo, y validación contra un proyecto real: usa
la geometría, secciones y cargas reales de los anexos del proyecto de
referencia (estantería selectiva de 9.50 m de altura, 6 niveles, 2400 kg
por par de vigas, ciudad Medellín — perfil de suelo D; secciones PARAL
122x2.5mm y VIGA 160x60x1.5mm tomadas de la tabla "Frame Section
Assignments" de la memoria de cálculo anexa), corre el pipeline completo
de Vortex (geometría -> cargas -> sismo -> análisis matricial 3D ->
verificación de TODOS los parales y vigas -> memoria de cálculo), y
compara el resultado contra las fuerzas reales reportadas por el
calculista (tabla "Element Forces - Frames", exportada de SAP2000).

Ejecutar:  python3 examples/run_example.py
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vortex.geometry import (
    RackParameters, build_selective_rack,
    brace_levels_per_panel_for_angle, resulting_brace_angle_deg, brace_panel_count,
)
from vortex.geometry.model import MemberKind
from vortex.sections import default_catalog
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.report import ProjectInfo, ReportData, generate_memoria
from vortex.units import kgf_to_kn

# Referencia real (memoria de cálculo anexa, tabla "Element Forces -
# Frames", combinación 1.4DL+1.2PL, paral interior de la base): SAP2000
# reporta P entre estos dos valores a lo largo del primer tramo del paral.
REFERENCE_P_RANGE_KN = (73.241, 84.329)


def main() -> None:
    catalog = default_catalog()
    upright = catalog["PARAL 122x2.5mm"]
    # Sección real de la memoria de cálculo anexa (tabla "Frame Section
    # Assignments": SectionType=Tube, DesignSect="VIGA 160X60X1.5X244";
    # 244 = longitud en cm, no es una propiedad de la sección transversal).
    beam = catalog["VIGA CAJA 160x60x1.5mm"]
    # Riostra real del plano de fabricación RIOSTRA_Y_VIGA.pdf (LOGIBOT,
    # Autodesk Inventor, lámina 1/5) — proyecto distinto al de la memoria
    # de cálculo, usado aquí sólo para ilustrar secciones de catálogo real.
    brace = catalog["RIOSTRA 25x40x10x1.5mm"]

    n_bays = 4
    frame_depth = 1.06
    level_heights = [1.20, 1.80, 1.80, 1.80, 1.80, 1.80]

    # Arriostramiento configurable por ángulo objetivo (incluye 70°, como
    # en el plano de fabricación): el número de niveles por panel de
    # diagonal se deriva geométricamente de la profundidad de marco y la
    # altura de nivel disponibles.
    target_angle_deg = 70.0
    brace_levels_per_panel = brace_levels_per_panel_for_angle(
        target_angle_deg, frame_depth, level_heights,
    )
    real_angle = resulting_brace_angle_deg(frame_depth, level_heights, brace_levels_per_panel)
    n_panels = brace_panel_count(len(level_heights), brace_levels_per_panel)
    print(
        f"Arriostramiento: ángulo objetivo {target_angle_deg:.0f}° -> "
        f"{brace_levels_per_panel} nivel(es)/panel, {n_panels} diagonal(es)/marco, "
        f"ángulo real {real_angle:.0f}°"
    )

    params = RackParameters(
        n_bays=n_bays, bay_length=2.44, frame_depth=frame_depth,
        level_heights=level_heights,
        upright_section=upright, beam_section=beam, brace_section=brace,
        base_fixity="pinned", brace_levels_per_panel=brace_levels_per_panel,
    )
    model = build_selective_rack(params)
    print(f"Modelo: {len(model.nodes)} nudos, {len(model.members)} elementos "
          f"({params.n_frames} marcos x {n_bays} bahías x {len(level_heights)} niveles)")

    pl_per_level_kn = kgf_to_kn(2400.0)  # PL = 2400 kgf por bahía y nivel (anexo)

    inputs = PipelineInputs(
        pl_per_level_kn=pl_per_level_kn, ll_kn_m2=0.0,
        seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20, pl_promedio_ratio=0.76),
        # El proyecto de referencia usa la combinación literal
        # 1.2DL+1.5EL+0.85PL (EL sin relajar); se reproduce esa misma
        # elección aquí en vez de la relajación opcional EL=1.0.
        apply_el_factor_10=False,
    )
    result = run_full_check(model, inputs)
    print(f"Sismo transversal: Cs={result.seismic_transversal.cs:.5f}  "
          f"V={result.seismic_transversal.v_base:.2f} kN")
    print(f"Sismo longitudinal: Cs={result.seismic_longitudinal.cs:.5f}  "
          f"V={result.seismic_longitudinal.v_base:.2f} kN")
    print(f"Análisis matricial 3D resuelto ({len(result.patterns)} patrones de carga), "
          f"{len(result.member_rows)} elementos verificados (todos los parales y vigas)")

    # ---------------- Validación contra la memoria de cálculo real -----
    # Combinación 1.4DL+1.2PL, paral interior de la base (mismo tipo de
    # elemento y combinación que la tabla "Element Forces - Frames" del
    # anexo), comparado directamente contra el rango real de SAP2000.
    base_upright = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.frame_index == n_bays // 2 and m.level_index == 0 and m.side == "frente"
    )
    mf_dl = result.patterns["DL"].member_forces[base_upright.id]
    mf_pl = result.patterns["PL"].member_forces[base_upright.id]
    p_computed = abs(1.4 * mf_dl.P_i + 1.2 * mf_pl.P_i)
    lo, hi = REFERENCE_P_RANGE_KN
    within = lo * 0.85 <= p_computed <= hi * 1.15
    print("\nValidación contra la memoria de cálculo real (paral interior de la "
          "base, combinación 1.4DL+1.2PL):")
    print(f"  Vortex (modelo 3D completo): P = {p_computed:.1f} kN")
    print(f"  SAP2000 (memoria anexa):     P = {lo:.1f} - {hi:.1f} kN")
    print(f"  {'✓ dentro de un rango razonable' if within else '✗ fuera del rango esperado — revisar'}")
    print(
        "  Nota: la tabla CHEQUEO/RESISTENCIA PARAL del anexo aplica un momento y "
        "cortante ENVOLVENTE (constante) a cada paral, un método más simplificado "
        "y conservador que la verificación miembro-por-miembro que hace Vortex; "
        "no se espera una coincidencia exacta en M/V, sólo en el orden de magnitud "
        "de P bajo la misma combinación de carga."
    )

    worst = sorted(result.member_rows.values(), key=lambda r: -r.ratio)[:5]
    print("\nElementos más críticos (de todos los verificados):")
    for row in worst:
        print(f"  {row.label:30s} {row.kind:6s} ratio={row.ratio:.2f}  {row.detail}")
    n_fail = sum(1 for r in result.member_rows.values() if r.ratio > 1.0)
    print(f"\n{n_fail} de {len(result.member_rows)} elementos no cumplen "
          f"(ratio > 1.0) con las secciones de catálogo usadas en este ejemplo.")

    # ---------------- Memoria de cálculo --------------------------------
    project = ProjectInfo(
        titulo="ESTANTERÍA SELECTIVA 9.50m x 6 NIVELES x 2400kg/NIVEL",
        ciudad="Medellín", fecha=datetime.date.today().strftime("%Y-%m-%d"),
        ingeniero="[Nombre del ingeniero calculista]",
        especialidad="Esp. Estructuras", matricula="[M.P.]",
        cliente="[Cliente]",
    )
    design_rows = [
        {"elemento": r.label, "tipo": r.kind, "combo": r.combo,
         "demanda_capacidad": r.detail, "ratio": r.ratio}
        for r in result.member_rows.values()
    ]
    report_data = ReportData(
        project=project, model=model,
        dl_note="DL = Peso propio de la estructura (calculado automáticamente por sección y material).",
        ll_kn_m2=0.0, pl_per_level_kn=pl_per_level_kn,
        seismic_transversal=result.seismic_transversal,
        seismic_longitudinal=result.seismic_longitudinal,
        material_names=[upright.material.name, beam.material.name],
        upright_section=upright, beam_section=beam, brace_section=brace,
        method_name="LRFD", combos=result.combos, design_rows=design_rows,
        member_rows_detail=list(result.member_rows.values()),
    )
    out_path = os.path.join(os.path.dirname(__file__), "memoria_ejemplo.docx")
    generate_memoria(report_data, out_path)
    print(f"\nMemoria de cálculo generada: {out_path}")


if __name__ == "__main__":
    main()
