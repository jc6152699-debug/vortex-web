"""
Valida los totales de carga expuestos en `PipelineResult` (dl_total_kn,
dl_per_level_kn, pl_total_kn) usados por el panel "Cargas y sismo" de la
GUI — deben coincidir con una cuenta directa a partir del modelo y de las
entradas, sin recalcular nada por fuera del motor ya validado.
"""
from vortex.geometry import RackParameters, build_selective_rack
from vortex.sections.catalog import default_catalog
from vortex.loads.dead_live import dead_load_uprights
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.units import kgf_to_kn


def _build():
    catalog = default_catalog()
    params = RackParameters(
        n_bays=3, bay_length=2.44, frame_depth=1.06, level_heights=[1.20, 1.80, 1.80],
        upright_section=catalog["PARAL 122x2.5mm"],
        beam_section=catalog["VIGA CAJA 160x60x1.5mm"],
        brace_section=catalog["DIAGONAL TUBULAR 30x30x2.0mm"],
        base_fixity="pinned",
    )
    model = build_selective_rack(params)
    inputs = PipelineInputs(
        pl_per_level_kn=kgf_to_kn(2400.0), ll_kn_m2=0.0,
        seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20),
    )
    result = run_full_check(model, inputs)
    return model, result, inputs


def test_pl_total_matches_bays_times_levels():
    model, result, inputs = _build()
    expected = inputs.pl_per_level_kn * model.n_bays
    assert result.pl_total_kn == expected


def test_dl_total_matches_direct_sum_of_member_self_weight():
    model, result, inputs = _build()
    dl_by_member = dead_load_uprights(model)
    assert result.dl_total_kn == sum(dl_by_member.values())
    assert result.dl_per_level_kn == result.dl_total_kn / model.n_levels


def test_seismic_results_expose_fx_for_every_level():
    model, result, inputs = _build()
    assert len(result.seismic_transversal.fx_by_level) == model.n_levels
    assert len(result.seismic_longitudinal.fx_by_level) == model.n_levels
