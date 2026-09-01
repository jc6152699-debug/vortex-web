import math

from vortex.geometry.model import Material, Section, SectionKind
from vortex.analysis.solve import MemberForces
from vortex.design.beam import beam_moment_at, deflection_profile, check_beam, check_deflection
from vortex.design.upright_cfs import (
    check_upright_compression_bending, euler_stress, nominal_flexural_buckling_stress,
)
from vortex.design.connections import check_brace, check_base_plate


def _section(Fy=250_000.0, A=0.001, Iy=2e-6, Iz=1e-6, ry=0.03, rz=0.02, Sy=4e-5, Sz=2e-5):
    mat = Material("T", E=2e8, G=7.7e7, Fy=Fy)
    return Section(
        name="T", kind=SectionKind.GENERIC, material=mat,
        A=A, Iy=Iy, Iz=Iz, J=1e-7, depth=0.1, width=0.06, thickness=0.003,
        Sy=Sy, Sz=Sz, ry=ry, rz=rz,
    )


def test_beam_moment_at_matches_simply_supported_udl():
    L, q = 5.0, 4.0
    mf = MemberForces(
        member_id=1, P_i=0, V2_i=0, V3_i=-10.0, T_i=0, M2_i=0.0, M3_i=0,
        P_j=0, V2_j=0, V3_j=10.0, T_j=0, M2_j=0.0, M3_j=0,
    )
    m = beam_moment_at(mf, w_local_z=-q, L=L, x=L / 2)
    assert math.isclose(abs(m), q * L ** 2 / 8, rel_tol=1e-9)


def test_deflection_profile_matches_simply_supported_formula():
    L, q = 5.0, 4.0
    E, I = 2e8, 8e-6
    EI = E * I
    # extremos simplemente apoyados: v=0, rotaciones internas +-theta iguales
    # y opuestas por simetria; para viga prismatica bajo UDL:
    theta = q * L ** 3 / (24 * EI)
    xs, vs = deflection_profile(L, EI, v1=0.0, theta1=-theta, v2=0.0, theta2=theta, w=-q)
    v_mid_expected = -5 * q * L ** 4 / (384 * EI)
    i_mid = len(xs) // 2
    assert math.isclose(vs[i_mid], v_mid_expected, rel_tol=1e-6)


def test_check_beam_ratio_below_one_for_light_load():
    section = _section(Sy=5e-5)
    mf = MemberForces(
        member_id=1, P_i=0, V2_i=0, V3_i=-1.0, T_i=0, M2_i=0.0, M3_i=0,
        P_j=0, V2_j=0, V3_j=1.0, T_j=0, M2_j=0.0, M3_j=0,
    )
    r = check_beam(section, "test", mf, w_local_z=-0.4, L=2.0)
    assert r.ok
    assert r.ratio_bending < 1.0


def test_upright_pure_axial_short_column_near_yield_capacity():
    section = _section(A=0.001, ry=0.05, rz=0.03)
    # columna corta (KL/r pequeño) -> Fn ~ Fy
    r = check_upright_compression_bending(
        section, "test", P=1.0, M2=0.0, M3=0.0, KLy=0.1, KLz=0.1,
    )
    Fe = euler_stress(section.material.E, 0.1 / 0.03)
    Fn = nominal_flexural_buckling_stress(section.Fy, Fe)
    assert math.isclose(Fn, section.Fy, rel_tol=0.05)
    assert r.ratio_axial < 0.01


def test_upright_slender_column_governed_by_buckling():
    section = _section(A=0.001, ry=0.02, rz=0.02)
    r_stocky = check_upright_compression_bending(
        section, "test", P=5.0, M2=0.0, M3=0.0, KLy=0.5, KLz=0.5,
    )
    r_slender = check_upright_compression_bending(
        section, "test", P=5.0, M2=0.0, M3=0.0, KLy=4.0, KLz=4.0,
    )
    assert r_slender.Pa < r_stocky.Pa


def test_brace_tension_governed_by_yield():
    section = _section(A=0.0005, ry=0.015, rz=0.015, Fy=250_000.0)
    # Ae = A * perforation_ratio (0.85 por defecto, sin Ae_known del fabricante)
    ae = 0.0005 * section.perforation_ratio
    r = check_brace(section, "test", N=ae * 250_000.0 / 1.67 * 0.5, KL=1.0)
    assert r.ok
    assert math.isclose(r.capacity, ae * 250_000.0 / 1.67, rel_tol=1e-9)


def test_brace_compression_reduced_by_slenderness():
    section = _section(A=0.0005, ry=0.008, rz=0.008, Fy=250_000.0)
    r_short = check_brace(section, "test", N=-1.0, KL=0.3)
    r_long = check_brace(section, "test", N=-1.0, KL=3.0)
    assert r_long.capacity < r_short.capacity


def test_base_plate_all_compression_no_anchor_tension():
    r = check_base_plate(
        "test", P=20.0, Mx=0.5, My=0.5, Vx=1.0, Vy=0.5,
        plate_length=0.20, plate_width=0.15,
        anchor_positions=[(-0.07, -0.05), (0.07, -0.05), (-0.07, 0.05), (0.07, 0.05)],
        f_c_concrete_mpa=21.0,
        anchor_capacity_tension_kn=15.0, anchor_capacity_shear_kn=8.0,
    )
    assert r.anchor_tension_max == 0.0
    assert r.ratio_anchor_tension == 0.0


def test_base_plate_uplift_produces_anchor_tension():
    r = check_base_plate(
        "test", P=-5.0, Mx=0.0, My=2.0, Vx=0.5, Vy=0.0,
        plate_length=0.20, plate_width=0.15,
        anchor_positions=[(-0.07, -0.05), (0.07, -0.05), (-0.07, 0.05), (0.07, 0.05)],
        f_c_concrete_mpa=21.0,
        anchor_capacity_tension_kn=15.0, anchor_capacity_shear_kn=8.0,
    )
    assert r.anchor_tension_max > 0.0
