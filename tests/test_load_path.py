"""
Valida contra el modelo completo (motor de análisis matricial 3D, no un
cálculo manual simplificado) la trayectoria de carga de PL descrita en la
observación técnica externa: bahía -> viga (frente/fondo) -> reacciones de
apoyo -> paral, con la diferencia clave entre un paral EXTREMO (una sola
bahía adyacente) y un paral INTERIOR (dos bahías adyacentes).

`beam_udl_from_product_load` (`vortex/loads/dead_live.py`) es la única
función que aplica PL sobre el modelo (usada por
`analysis.pipeline.run_full_check`); estos tests comprueban que, a partir
de ella, el reparto que emerge del pórtico espacial 3D coincide con el
reparto manual paso a paso (bahía/2 -> viga, viga/2 -> apoyo, paral
interior = 2x paral extremo) sin necesidad de un caso especial por tipo de
paral en el motor.
"""
import math

from vortex.geometry import RackParameters, build_selective_rack
from vortex.geometry.model import MemberKind
from vortex.sections.catalog import default_catalog
from vortex.loads.dead_live import beam_udl_from_product_load
from vortex.analysis import PipelineInputs, SeismicInputs, run_full_check
from vortex.units import kgf_to_kn


def _build(n_bays: int = 4):
    catalog = default_catalog()
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
        seismic=SeismicInputs(soil_type="D", aa=0.15, av=0.20),
    )
    result = run_full_check(model, inputs)
    return model, result, inputs, n_bays


def _base_upright(model, frame_index, side="frente"):
    return next(
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.frame_index == frame_index and m.level_index == 0 and m.side == side
    )


def test_beam_udl_from_product_load_splits_bay_load_in_half():
    # Paso 1 de la observación: la estiba se apoya sobre las dos filas de
    # vigas (frente + fondo) de la bahía -> cada viga recibe PL/2.
    pl_per_level_kn = 24.0
    bay_length = 2.44
    w = beam_udl_from_product_load(pl_per_level_kn, bay_length)
    assert math.isclose(w * bay_length, pl_per_level_kn / 2.0, rel_tol=1e-9)


def test_beam_reactions_split_equally_between_both_ends():
    # Paso 2: cada viga reparte su carga a partes iguales entre sus dos
    # apoyos (parales), por simetría bajo carga uniformemente distribuida.
    model, result, inputs, n_bays = _build()
    beam = next(
        m for m in model.members_of_kind(MemberKind.BEAM)
        if m.bay_index == n_bays // 2 and m.level_index == 3 and m.side == "frente"
    )
    mf = result.patterns["PL"].member_forces[beam.id]
    assert math.isclose(abs(mf.V3_i), abs(mf.V3_j), rel_tol=0.01)


def test_interior_upright_carries_double_the_extremo_upright_under_pl():
    # Núcleo de la observación: bajo PL puro, un paral interior (conectado
    # a dos bahías) recibe ~2x la carga axial de un paral extremo
    # (conectado a una sola bahía) en el mismo nivel. Esto no se
    # fuerza con un caso especial en el motor: emerge de la conectividad
    # del pórtico espacial 3D continuo.
    model, result, inputs, n_bays = _build()
    extremo = _base_upright(model, frame_index=0)
    interior = _base_upright(model, frame_index=n_bays // 2)

    p_ext = abs(result.patterns["PL"].member_forces[extremo.id].P_i)
    p_int = abs(result.patterns["PL"].member_forces[interior.id].P_i)

    assert p_ext > 0.0
    assert math.isclose(p_int / p_ext, 2.0, rel_tol=0.02)


def test_pl_total_and_grand_total_scale_with_bay_and_level_count():
    # El propio motor ya expone estos totales (usados por el panel "Cargas
    # y sismo" de la GUI); se valida aquí junto al resto de la trayectoria
    # de carga para dejar en un solo lugar la cobertura de la observación.
    model, result, inputs, n_bays = _build()
    assert result.pl_total_kn == inputs.pl_per_level_kn * n_bays
    assert result.pl_grand_total_kn == result.pl_total_kn * model.n_levels
