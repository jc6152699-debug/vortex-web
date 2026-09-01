import numpy as np
import pytest

from vortex.geometry.model import ConnectionRelease
from vortex.analysis.stiffness import (
    rigid_local_stiffness,
    condense_bending_block,
    local_axes,
    transformation_matrix,
)


def _z_block(k12):
    idx = [1, 5, 7, 11]
    return k12[np.ix_(idx, idx)]


def test_condensation_both_rigid_is_identity():
    k12 = rigid_local_stiffness(E=2e8, G=7.7e7, A=0.001, Iy=1e-6, Iz=2e-6, J=1e-6, L=2.0)
    kz = _z_block(k12)
    cond = condense_bending_block(kz, ConnectionRelease.rigid(), ConnectionRelease.rigid())
    assert cond.is_identity
    assert np.allclose(cond.k_reduced, kz)


def test_condensation_both_pinned_is_zero():
    k12 = rigid_local_stiffness(E=2e8, G=7.7e7, A=0.001, Iy=1e-6, Iz=2e-6, J=1e-6, L=2.0)
    kz = _z_block(k12)
    cond = condense_bending_block(kz, ConnectionRelease.pinned(), ConnectionRelease.pinned())
    assert np.allclose(cond.k_reduced, 0.0, atol=1e-6)


def test_condensation_one_pinned_matches_classic_formula():
    # Viga empotrada-articulada (rigido en 1, articulado en 2): la rigidez
    # clásica de v1 (extremo empotrado) frente a v1 es 3EI/L^3 (fórmula de
    # libro de texto para viga con un extremo articulado).
    E, I, L = 2e8, 1e-6, 2.0
    k12 = rigid_local_stiffness(E=E, G=7.7e7, A=0.001, Iy=1e-6, Iz=I, J=1e-6, L=L)
    kz = _z_block(k12)
    cond = condense_bending_block(kz, ConnectionRelease.rigid(), ConnectionRelease.pinned())
    # orden salida [v1, r1, v2, phi2]; phi2 debe quedar totalmente
    # desacoplado (columna/fila cero) ya que el momento en el articulado es 0
    assert np.allclose(cond.k_reduced[:, 3], 0.0, atol=1e-8)
    assert np.allclose(cond.k_reduced[3, :], 0.0, atol=1e-8)
    assert np.isclose(cond.k_reduced[0, 0], 3 * E * I / L ** 3, rtol=1e-8)


def test_condensation_semirigid_limits_bracket_pinned_and_rigid():
    E, I, L = 2e8, 1e-6, 2.0
    k12 = rigid_local_stiffness(E=E, G=7.7e7, A=0.001, Iy=1e-6, Iz=I, J=1e-6, L=L)
    kz = _z_block(k12)
    k_rigid = condense_bending_block(kz, ConnectionRelease.rigid(), ConnectionRelease.rigid()).k_reduced
    k_pinned = condense_bending_block(kz, ConnectionRelease.rigid(), ConnectionRelease.pinned()).k_reduced

    k_soft = condense_bending_block(
        kz, ConnectionRelease.rigid(), ConnectionRelease.semirigid(1e-3),
    ).k_reduced
    k_stiff = condense_bending_block(
        kz, ConnectionRelease.rigid(), ConnectionRelease.semirigid(1e9),
    ).k_reduced

    assert np.isclose(k_soft[0, 0], k_pinned[0, 0], rtol=1e-2)
    assert np.isclose(k_stiff[0, 0], k_rigid[0, 0], rtol=1e-3)


def test_local_axes_beam_along_x():
    ex, ey, ez = local_axes((0, 0, 0), (2.0, 0, 0), (0, 0, 1))
    assert np.allclose(ex, [1, 0, 0])
    assert np.allclose(ey, [0, 1, 0])
    assert np.allclose(ez, [0, 0, 1])


def test_local_axes_upright_along_z():
    ex, ey, ez = local_axes((0, 0, 0), (0, 0, 3.0), (1, 0, 0))
    assert np.allclose(ex, [0, 0, 1])
    assert np.allclose(ez, [1, 0, 0])


def test_transformation_is_orthogonal():
    ex, ey, ez = local_axes((0, 0, 0), (1.0, 2.0, 3.0), (0, 0, 1))
    T = transformation_matrix(ex, ey, ez)
    assert np.allclose(T @ T.T, np.eye(12), atol=1e-10)
