import math

from vortex.geometry import (
    RackParameters, build_selective_rack, MemberKind,
    brace_levels_per_panel_for_angle, brace_levels_per_panel_for_count,
    resulting_brace_angle_deg, brace_panel_count,
)
from vortex.sections.catalog import default_catalog
from vortex.analysis.solve import analyze, MemberLoad


def _params(**overrides):
    cat = default_catalog()
    defaults = dict(
        n_bays=2, bay_length=2.44, frame_depth=1.06,
        level_heights=[1.2, 1.8, 1.8, 1.8, 1.8, 1.8],
        upright_section=cat["PARAL 122x2.5mm"],
        beam_section=cat["VIGA 130x60x2.0mm"],
        brace_section=cat["RIOSTRA 25x40x10x1.5mm"],
    )
    defaults.update(overrides)
    return RackParameters(**defaults)


def test_angle_70_matches_steep_default_for_typical_proportions():
    p = _params()
    n = brace_levels_per_panel_for_angle(70.0, p.frame_depth, p.level_heights)
    assert n == 1  # ángulo empinado -> una diagonal por nivel (comportamiento previo)


def test_shallower_angle_groups_more_levels_per_panel():
    p = _params()
    n_steep = brace_levels_per_panel_for_angle(75.0, p.frame_depth, p.level_heights)
    n_shallow = brace_levels_per_panel_for_angle(15.0, p.frame_depth, p.level_heights)
    assert n_shallow >= n_steep


def test_panel_count_roundtrip_with_levels_per_panel():
    n_levels = 6
    lpp = brace_levels_per_panel_for_count(panel_count=3, n_levels=n_levels)
    count = brace_panel_count(n_levels, lpp)
    assert count in (2, 3, 4)  # aproximación entera, cercana al objetivo


def test_resulting_angle_is_between_0_and_90():
    p = _params()
    for lpp in (1, 2, 3):
        angle = resulting_brace_angle_deg(p.frame_depth, p.level_heights, lpp)
        assert 0.0 < angle < 90.0


def test_model_with_two_levels_per_panel_has_fewer_diagonals():
    p1 = _params(brace_levels_per_panel=1)
    p2 = _params(brace_levels_per_panel=2)
    m1 = build_selective_rack(p1)
    m2 = build_selective_rack(p2)
    d1 = [m for m in m1.members_of_kind(MemberKind.BRACE) if "DIAGONAL" in m.label]
    d2 = [m for m in m2.members_of_kind(MemberKind.BRACE) if "DIAGONAL" in m.label]
    assert len(d2) < len(d1)


def test_model_with_sparser_bracing_still_stable_under_analysis():
    p = _params(brace_levels_per_panel=2, n_bays=1)
    model = build_selective_rack(p)
    dl_loads = [
        MemberLoad(member_id=m.id, wz=-1.0) for m in model.members.values()
    ]
    # No debe lanzar LinAlgError de matriz singular (mecanismo)
    result = analyze(model, [], dl_loads)
    assert len(result.member_forces) == len(model.members)


def test_diagonal_boundaries_span_full_height_no_gaps():
    p = _params(brace_levels_per_panel=4)
    model = build_selective_rack(p)
    diag = sorted(
        [m for m in model.members_of_kind(MemberKind.BRACE) if "DIAGONAL" in m.label and m.frame_index == 0],
        key=lambda m: model.nodes[m.node_i].z,
    )
    # el primer nudo del primer panel está en el piso, el último nudo del
    # último panel está en el nivel superior (cobertura completa, sin huecos)
    z0 = min(model.nodes[m.node_i].z for m in diag)
    z1 = max(model.nodes[m.node_j].z for m in diag)
    assert math.isclose(z0, 0.0, abs_tol=1e-9)
    assert math.isclose(z1, model.level_elevations[-1], abs_tol=1e-9)
