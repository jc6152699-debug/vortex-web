"""
Validación de `element_forces_table` (tabla "Element Forces - Frames"
para chequeo cruzado) contra la relación entre combinaciones observada en
la memoria de cálculo real de referencia: P bajo 1.2DL+1.4PL / P bajo
1.4DL+1.2PL ≈ 1.161 en el anexo (frame 7: -86.28/-74.29), y Vortex debe
reproducir una relación muy cercana para el mismo tipo de elemento
(paral interior de la base), aunque los valores absolutos difieran por
la diferencia de topología documentada en el README.
"""
import csv
import math

from vortex.geometry import RackParameters, build_selective_rack
from vortex.geometry.model import MemberKind
from vortex.sections.catalog import default_catalog
from vortex.analysis import (
    PipelineInputs, SeismicInputs, run_full_check,
    element_forces_table, write_element_forces_csv,
)
from vortex.units import kgf_to_kn

REFERENCE_RATIO_COMBO2_OVER_COMBO1 = 86.28 / 74.29  # memoria anexa, frame 7


def _build():
    catalog = default_catalog()
    n_bays = 4
    level_heights = [1.20, 1.80, 1.80, 1.80, 1.80, 1.80]
    params = RackParameters(
        n_bays=n_bays, bay_length=2.44, frame_depth=1.06, level_heights=level_heights,
        upright_section=catalog["PARAL 122x2.5mm"],
        beam_section=catalog["VIGA CAJA 160x60x1.5mm"],
        brace_section=catalog["DIAGONAL TUBULAR 30x30x2.0mm"],
        base_fixity="pinned",
    )
    model = build_selective_rack(params)
    inputs = PipelineInputs(
        pl_per_level_kn=kgf_to_kn(2400.0), ll_kn_m2=0.0,
        seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20, pl_promedio_ratio=0.76),
        apply_el_factor_10=False,
    )
    result = run_full_check(model, inputs)
    return model, result, inputs, n_bays


def test_element_forces_table_has_three_real_combos_per_member():
    model, result, inputs, n_bays = _build()
    rows = element_forces_table(model, result, inputs, el_pattern="EL_X")
    labels = {r.output_case for r in rows}
    assert labels == {"1.4DL+1.2PL", "1.2DL+1.4PL", "1.2DL+1.5EL+0.85PL"}


def test_element_forces_table_three_stations_per_member_per_combo():
    model, result, inputs, n_bays = _build()
    rows = element_forces_table(model, result, inputs, el_pattern="EL_X")
    base = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.frame_index == n_bays // 2 and m.level_index == 0 and m.side == "frente"
    )
    rows_base = [r for r in rows if r.frame == base.id]
    assert len(rows_base) == 3 * 3  # 3 combos x 3 estaciones


def test_combo_ratio_matches_reference_memoria_closely():
    model, result, inputs, n_bays = _build()
    rows = element_forces_table(model, result, inputs, el_pattern="EL_X")
    base = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.frame_index == n_bays // 2 and m.level_index == 0 and m.side == "frente"
    )
    p1 = next(r.P for r in rows if r.frame == base.id and r.output_case == "1.4DL+1.2PL" and r.station_m == 0.0)
    p2 = next(r.P for r in rows if r.frame == base.id and r.output_case == "1.2DL+1.4PL" and r.station_m == 0.0)
    ratio = abs(p2) / abs(p1)
    assert math.isclose(ratio, REFERENCE_RATIO_COMBO2_OVER_COMBO1, rel_tol=0.01)


def test_write_element_forces_csv_roundtrip(tmp_path):
    model, result, inputs, n_bays = _build()
    rows = element_forces_table(model, result, inputs, el_pattern="EL_X")
    path = tmp_path / "forces.csv"
    write_element_forces_csv(rows, str(path))
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        data_rows = list(reader)
    assert header == ["ITEM", "Frame", "Label", "OutputCase", "Station[m]",
                       "P[KN]", "M3[KN-m]", "V2[KN]", "M2[KN-m]", "V3[KN]"]
    assert len(data_rows) == len(rows)
