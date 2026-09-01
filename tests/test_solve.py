"""
Validación del motor de análisis matricial 3D contra soluciones clásicas
de resistencia de materiales (viga en voladizo, viga simplemente
apoyada), como verificación independiente de todas las convenciones de
signo del elemento y de la recuperación de fuerzas internas.
"""
import math

import numpy as np
import pytest

from vortex.geometry.model import (
    ConnectionRelease, Material, Member, MemberKind, Node, RackModel, Section, SectionKind,
)
from vortex.analysis.solve import analyze, NodalLoad, MemberLoad


def _make_section(E=2.0e8, G=7.7e7, A=0.005, Iy=8e-6, Iz=8e-6, J=1e-6) -> Section:
    mat = Material("TEST", E=E, G=G, Fy=250_000.0)
    return Section(
        name="TEST", kind=SectionKind.GENERIC, material=mat,
        A=A, Iy=Iy, Iz=Iz, J=J, depth=0.1, width=0.1, thickness=0.005,
    )


def _cantilever_model(L=3.0, section=None) -> RackModel:
    section = section or _make_section()
    model = RackModel()
    model.add_node(Node(1, 0, 0, 0, restraints=(True,) * 6))
    model.add_node(Node(2, L, 0, 0, restraints=(False,) * 6))
    model.add_member(Member(
        id=1, node_i=1, node_j=2, section=section, kind=MemberKind.BEAM,
        z_axis_ref=(0.0, 0.0, 1.0),
    ))
    return model


def test_cantilever_tip_point_load_vertical():
    L = 3.0
    P = 10.0  # kN, hacia -Z (gravedad)
    section = _make_section()
    model = _cantilever_model(L, section)
    result = analyze(model, [NodalLoad(node_id=2, fz=-P)])

    E, Iy = section.material.E, section.Iy
    expected_defl = -P * L ** 3 / (3 * E * Iy)
    expected_rot = -P * L ** 2 / (2 * E * Iy)  # rotación local ry en el extremo libre

    uz_tip = result.displacements[2][2]
    assert math.isclose(uz_tip, expected_defl, rel_tol=1e-9)

    mf = result.member_forces[1]
    # Momento y cortante en el empotramiento: M = P*L, V = P (magnitudes clásicas)
    assert math.isclose(abs(mf.M2_i), P * L, rel_tol=1e-9)
    assert math.isclose(abs(mf.V3_i), P, rel_tol=1e-9)
    # En el extremo libre no hay momento ni cortante remanente más allá de
    # la reacción interna trasladada (cortante constante a lo largo del
    # tramo sin carga distribuida)
    assert math.isclose(abs(mf.V3_j), P, rel_tol=1e-9)
    assert math.isclose(abs(mf.M2_j), 0.0, abs_tol=1e-8)

    # Equilibrio global: reacción vertical en el empotramiento = P
    assert math.isclose(result.reactions[1][2], P, rel_tol=1e-9)


def test_cantilever_tip_point_load_horizontal_inplane():
    """Misma verificación pero en el plano x-y (usa el bloque Iz / v,rz)."""
    L = 2.5
    P = 6.0
    section = _make_section()
    model = _cantilever_model(L, section)
    result = analyze(model, [NodalLoad(node_id=2, fy=P)])

    E, Iz = section.material.E, section.Iz
    expected_defl = P * L ** 3 / (3 * E * Iz)
    uy_tip = result.displacements[2][1]
    assert math.isclose(uy_tip, expected_defl, rel_tol=1e-9)

    mf = result.member_forces[1]
    assert math.isclose(abs(mf.M3_i), P * L, rel_tol=1e-9)
    assert math.isclose(abs(mf.V2_i), P, rel_tol=1e-9)


def test_cantilever_axial_load():
    L = 4.0
    P = 50.0
    section = _make_section()
    model = _cantilever_model(L, section)
    result = analyze(model, [NodalLoad(node_id=2, fx=-P)])  # compresión

    E, A = section.material.E, section.A
    expected_shortening = -P * L / (E * A)
    assert math.isclose(result.displacements[2][0], expected_shortening, rel_tol=1e-9)
    mf = result.member_forces[1]
    assert math.isclose(mf.P_j, -P, rel_tol=1e-9)  # compresión = negativo (convención)


def test_simply_supported_udl_deflection_and_shear():
    L = 5.0
    q = 4.0  # kN/m hacia -Z
    section = _make_section()
    model = RackModel()
    model.add_node(Node(1, 0, 0, 0, restraints=(True, True, True, True, False, False)))
    model.add_node(Node(2, L, 0, 0, restraints=(False, True, True, True, False, False)))
    model.add_member(Member(
        id=1, node_i=1, node_j=2, section=section, kind=MemberKind.BEAM,
        z_axis_ref=(0.0, 0.0, 1.0),
    ))
    result = analyze(model, [], [MemberLoad(member_id=1, wz=-q)])
    mf = result.member_forces[1]
    assert math.isclose(abs(mf.V3_i), q * L / 2, rel_tol=1e-8)
    assert math.isclose(abs(mf.V3_j), q * L / 2, rel_tol=1e-8)
    assert math.isclose(mf.M2_i, 0.0, abs_tol=1e-6)
    assert math.isclose(mf.M2_j, 0.0, abs_tol=1e-6)

    # Momento a una estación x por equilibrio del tramo libre desde el
    # nodo i, con la convención de signos de MemberForces (extremo i
    # invertido, ver `solve.analyze`): M(x) = M2_i - V3_i*x + qz_local*x^2/2.
    # Se verifica que M(0)=M2_i, M(L)=M2_j (ya comprobado arriba ~0) y que
    # el máximo a mitad de luz coincide con qL^2/8 (viga simplemente
    # apoyada bajo carga uniforme).
    qz_local = -q  # wz aplicado = -q; el eje local z coincide con el global Z
    x = L / 2
    m_mid = mf.M2_i - mf.V3_i * x + qz_local * x ** 2 / 2
    assert math.isclose(abs(m_mid), q * L ** 2 / 8, rel_tol=1e-6)


def test_equilibrium_reactions_balance_applied_load_cantilever_with_udl():
    L = 3.2
    q = 2.5
    section = _make_section()
    model = _cantilever_model(L, section)
    result = analyze(model, [], [MemberLoad(member_id=1, wz=-q)])
    total_reaction_z = result.reactions[1][2]
    assert math.isclose(total_reaction_z, q * L, rel_tol=1e-9)
    total_reaction_moment = result.reactions[1][4]
    assert math.isclose(abs(total_reaction_moment), q * L ** 2 / 2, rel_tol=1e-9)
