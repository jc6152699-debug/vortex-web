"""
Valida los totales de carga expuestos en `PipelineResult` (dl_total_kn,
dl_per_level_kn, pl_total_kn, ll_total_kn) usados por el panel "Cargas y
sismo" de la GUI — deben coincidir con una cuenta directa a partir del
modelo y de las entradas, sin recalcular nada por fuera del motor ya
validado. También valida que la carga viva (LL) -- antes aceptada por la
GUI pero nunca usada en ningún patrón de carga -- ahora sí se traduce en
fuerzas reales sobre vigas y parales, y en el peso sísmico Ws.
"""
import pytest

from vortex.geometry import RackParameters, build_selective_rack
from vortex.geometry.model import MemberKind
from vortex.sections.catalog import default_catalog
from vortex.loads.dead_live import dead_load_uprights
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.units import kgf_to_kn


def _build(ll_kn_m2: float = 0.0):
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
        pl_per_level_kn=kgf_to_kn(2400.0), ll_kn_m2=ll_kn_m2,
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


def test_pl_and_ll_grand_totals_multiply_by_n_levels():
    model, result, inputs = _build(ll_kn_m2=1.5)
    assert result.pl_grand_total_kn == result.pl_total_kn * model.n_levels
    assert result.ll_grand_total_kn == result.ll_total_kn * model.n_levels


def test_ll_total_matches_area_load_times_plan_area():
    model, result, inputs = _build(ll_kn_m2=1.5)
    expected = inputs.ll_kn_m2 * model.bay_length * model.frame_depth * model.n_bays
    assert result.ll_total_kn == expected


def test_ll_zero_gives_same_results_as_before_the_fix():
    """Con LL=0 (el valor por defecto en toda la GUI hasta ahora) el
    comportamiento debe ser idéntico al de antes de conectar el patrón LL."""
    _, result_zero, _ = _build(ll_kn_m2=0.0)
    assert result_zero.ll_total_kn == 0.0
    assert result_zero.ll_grand_total_kn == 0.0
    for mf in result_zero.patterns["LL"].member_forces.values():
        assert mf.P_i == 0.0 and mf.M2_i == 0.0 and mf.V3_i == 0.0


def test_ll_increases_beam_moment_and_upright_axial_load():
    """La carga viva (LL) estaba en la GUI ("Carga viva (LL) kN/m²") pero
    nunca se traducía en ningún patrón de carga -- el bug que reportó el
    usuario. Verifica que ahora sí aumenta la demanda real."""
    model, result_zero, _ = _build(ll_kn_m2=0.0)
    _, result_ll, _ = _build(ll_kn_m2=2.0)

    beam = next(iter(model.members_of_kind(MemberKind.BEAM)))
    m_zero = result_zero.member_rows[beam.id].raw_force
    m_ll = result_ll.member_rows[beam.id].raw_force
    assert m_ll > m_zero, "el momento de diseño de la viga debería subir al agregar LL"

    upright = next(iter(model.members_of_kind(MemberKind.UPRIGHT)))
    p_zero = result_zero.member_rows[upright.id].raw_force
    p_ll = result_ll.member_rows[upright.id].raw_force
    assert p_ll > p_zero, "la carga axial del paral debería subir al agregar LL"


def test_ll_increases_seismic_weight_ws():
    _, result_zero, _ = _build(ll_kn_m2=0.0)
    _, result_ll, _ = _build(ll_kn_m2=2.0)
    assert result_ll.seismic_transversal.ws > result_zero.seismic_transversal.ws


def test_seismic_ws_uses_whole_structure_totals_not_one_level():
    """
    Ws/V (numeral 1.1.3, 'cortante sísmico DE BASE') deben construirse con
    el peso de TODA la estantería (dl_total, y pl/ll de TODOS los
    niveles) -- antes usaban dl_per_level y el pl/ll de un solo nivel,
    subestimando Ws (y por tanto V y las fuerzas sísmicas aplicadas al
    modelo) en un factor ~n_levels. La prueba de consistencia es
    dimensional: la suma de los pesos por nivel que se usan para repartir
    V verticalmente (`fx_by_level` es proporcional a ese peso) debe
    corresponder al mismo total que produjo V -- si Ws_total fuera en
    realidad el peso de UN solo nivel, sería exactamente 1/n_levels de la
    suma de los pesos por nivel.
    """
    from vortex.loads import seismic as sm

    model, result, inputs = _build()
    n_levels = model.n_levels

    dl_total = result.dl_total_kn
    pl_all_levels = result.pl_total_kn * n_levels
    ll_all_levels = result.ll_total_kn * n_levels

    expected_ws = sm.seismic_weight(pl=pl_all_levels, dl=dl_total, ll=ll_all_levels, plrf=1.0)
    assert result.seismic_transversal.ws == pytest.approx(expected_ws, rel=1e-9)

    # La suma de los pesos por nivel (usados para reparto vertical, ver
    # `vortex.loads.seismic.vertical_distribution`) debe coincidir con Ws
    # (dentro de la aproximación de que DL por nivel es uniforme): con
    # n_levels niveles de peso igual, la razón debe ser ~n_levels, NUNCA 1.
    per_level_ws = sm.seismic_weight(
        pl=result.pl_total_kn, dl=result.dl_per_level_kn, ll=result.ll_total_kn, plrf=1.0,
    )
    assert result.seismic_transversal.ws == pytest.approx(per_level_ws * n_levels, rel=1e-6)
