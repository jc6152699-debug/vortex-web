"""
Valida `loads.distribution.build_load_distribution`: que sea la MISMA
fuente que usa `analysis.pipeline` (no un cálculo aparte) y que la
cuadrícula bahía x nivel (`beam_grid`) reproduzca el reparto esperado de
carga de producto por viga.
"""
import math

from vortex.geometry import RackParameters, build_selective_rack
from vortex.sections.catalog import default_catalog
from vortex.loads.distribution import build_load_distribution
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check


def _build(n_bays=8, n_levels=6, pl_per_level_kn=24.0):
    catalog = default_catalog()
    params = RackParameters(
        n_bays=n_bays, bay_length=2.44, frame_depth=1.06,
        level_heights=[1.20] + [1.80] * (n_levels - 1),
        upright_section=catalog["PARAL 122x2.5mm"],
        beam_section=catalog["VIGA CAJA 160x60x1.5mm"],
        brace_section=catalog["DIAGONAL TUBULAR 30x30x2.0mm"],
        base_fixity="pinned",
    )
    model = build_selective_rack(params)
    inputs = PipelineInputs(
        pl_per_level_kn=pl_per_level_kn, ll_kn_m2=0.0,
        seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20),
    )
    return model, inputs


def test_beam_grid_matches_pl_per_level_over_bay_length():
    # PL=24 kN/nivel-bahía, bay_length=2.44 m -> w_pl = (24/2)/2.44 = 4.918
    # kN/m (el mismo valor de referencia del diagrama de cargas de
    # producto); la cuadrícula (`beam_grid`) reporta el total por viga
    # (w_pl + peso propio de la viga), así que se compara contra
    # `w_pl_beam_kn_m` con una tolerancia que sólo cubre ese peso propio.
    model, inputs = _build()
    dist = build_load_distribution(model, inputs.pl_per_level_kn, inputs.ll_kn_m2)
    grid = dist.beam_grid()
    max_dl_beam = max(row.w_dl_kn_m for row in dist.beam_rows)

    assert set(grid.keys()) == set(range(1, model.n_levels + 1))
    for level, bays in grid.items():
        assert set(bays.keys()) == set(range(model.n_bays))
        for w in bays.values():
            assert 0.0 <= w - dist.w_pl_beam_kn_m <= max_dl_beam + 1e-9
    assert math.isclose(dist.w_pl_beam_kn_m, 4.918032786885246, rel_tol=1e-9)
    assert all(math.isclose(row.w_pl_kn_m, dist.w_pl_beam_kn_m, rel_tol=1e-9) for row in dist.beam_rows)


def test_pipeline_result_exposes_same_distribution_object():
    model, inputs = _build()
    result = run_full_check(model, inputs)
    assert result.load_distribution is not None
    assert math.isclose(result.load_distribution.pl_total_kn, result.pl_total_kn)
    assert math.isclose(result.load_distribution.dl_total_kn, result.dl_total_kn)


def test_beam_rows_cover_every_beam_member():
    from vortex.geometry.model import MemberKind
    model, inputs = _build(n_bays=4, n_levels=3)
    dist = build_load_distribution(model, inputs.pl_per_level_kn, inputs.ll_kn_m2)
    beam_ids = {m.id for m in model.members_of_kind(MemberKind.BEAM)}
    assert {row.member_id for row in dist.beam_rows} == beam_ids
