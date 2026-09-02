"""
Verificación de placa base / anclajes (`design.connections.check_base_plate`)
conectada al pipeline. Antes esta función ya existía pero nada la llamaba:
el software nunca calculaba ni reportaba la demanda en placa base/anclajes
de un paral, aunque el disclaimer de la memoria dijera "debe verificarse".

`BasePlateInputs` es opcional a propósito: si el usuario no tiene los
datos reales del anclaje (capacidad de tracción/cortante del informe
ICC-ES del fabricante), el chequeo se omite en vez de inventar un valor
-- mismo criterio que el resto del proyecto (Cw/xo/ro, rigidez de
conexión km, etc.).
"""
import pytest

from vortex.geometry import RackParameters, build_selective_rack
from vortex.geometry.model import MemberKind
from vortex.sections.catalog import default_catalog
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.analysis.pipeline import BasePlateInputs
from vortex.units import kgf_to_kn


def _build(base_plate=None, base_fixity="pinned"):
    catalog = default_catalog()
    params = RackParameters(
        n_bays=2, bay_length=2.44, frame_depth=1.06, level_heights=[1.20, 1.80, 1.80],
        upright_section=catalog["PARAL 122x2.5mm"],
        beam_section=catalog["VIGA CAJA 160x60x1.5mm"],
        brace_section=catalog["DIAGONAL TUBULAR 30x30x2.0mm"],
        base_fixity=base_fixity,
    )
    model = build_selective_rack(params)
    inputs = PipelineInputs(
        pl_per_level_kn=kgf_to_kn(2400.0), ll_kn_m2=0.0,
        seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20),
        base_plate=base_plate,
    )
    result = run_full_check(model, inputs)
    return model, result, inputs


def _typical_base_plate() -> BasePlateInputs:
    return BasePlateInputs(
        plate_length=0.15, plate_width=0.15,
        anchor_spacing_x=0.10, anchor_spacing_y=0.10,
        f_c_concrete_mpa=21.0,
        anchor_capacity_tension_kn=15.0,
        anchor_capacity_shear_kn=10.0,
    )


def test_base_plate_check_skipped_when_not_configured():
    _, result, _ = _build(base_plate=None)
    assert result.base_plate_rows == []


def test_base_plate_anchor_positions_are_a_rectangular_4_bolt_pattern():
    bp = _typical_base_plate()
    positions = bp.anchor_positions()
    assert len(positions) == 4
    xs = sorted({round(x, 6) for x, y in positions})
    ys = sorted({round(y, 6) for x, y in positions})
    assert xs == [-0.05, 0.05]
    assert ys == [-0.05, 0.05]


def test_base_plate_check_produces_one_row_per_ground_level_upright():
    model, result, _ = _build(base_plate=_typical_base_plate())
    n_ground_uprights = len([
        m for m in model.members_of_kind(MemberKind.UPRIGHT) if m.level_index == 0
    ])
    assert n_ground_uprights > 0
    assert len(result.base_plate_rows) == n_ground_uprights
    for row in result.base_plate_rows:
        assert row.ratio >= 0.0
        assert row.result.P > 0.0, "bajo carga gravitacional, P (compresión) debe ser > 0"


def test_base_plate_reaction_p_matches_member_axial_force_at_base():
    """Sanity check del signo/magnitud: bajo sólo DL, la compresión que
    la placa base debe resistir (P, de la reacción Fz) debe ser del mismo
    orden que la fuerza axial en el propio tramo de paral apoyado en el
    piso (P_i, bajo la misma carga DL)."""
    model, result, _ = _build(base_plate=_typical_base_plate(), base_fixity="fixed")
    base_upright = next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT) if m.level_index == 0
    )
    node_id = base_upright.node_i
    reaction_p = result.patterns["DL"].reactions[node_id][2]
    member_p = abs(result.patterns["DL"].member_forces[base_upright.id].P_i)
    assert reaction_p > 0.0
    assert reaction_p == pytest.approx(member_p, rel=0.20)


def test_base_plate_ratio_increases_with_more_product_load():
    catalog = default_catalog()
    params = RackParameters(
        n_bays=2, bay_length=2.44, frame_depth=1.06, level_heights=[1.20, 1.80, 1.80],
        upright_section=catalog["PARAL 122x2.5mm"],
        beam_section=catalog["VIGA CAJA 160x60x1.5mm"],
        brace_section=catalog["DIAGONAL TUBULAR 30x30x2.0mm"],
        base_fixity="pinned",
    )
    model = build_selective_rack(params)

    def _ratio(pl_kgf):
        inputs = PipelineInputs(
            pl_per_level_kn=kgf_to_kn(pl_kgf), ll_kn_m2=0.0,
            seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20),
            base_plate=_typical_base_plate(),
        )
        result = run_full_check(model, inputs)
        return max(row.ratio for row in result.base_plate_rows)

    assert _ratio(6000.0) > _ratio(1000.0)
