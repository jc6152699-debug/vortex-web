"""
Validación de las fórmulas de NSR-10 Título F, Capítulo F.4 (ancho
efectivo y cortante) agregadas a `design.upright_cfs`, contra los casos
límite explícitos del propio reglamento (texto extraído de
NSR10TituloF.docx) y contra valores de referencia conocidos de AISI S100.
"""
import math

from vortex.geometry.model import Material, Section, SectionKind
from vortex.design.upright_cfs import (
    plate_reduction_factor,
    effective_area_at_stress,
    shear_capacity,
    check_upright_compression_bending,
)
from vortex.sections.catalog import default_catalog, lipped_channel_upright


def test_plate_reduction_factor_full_width_below_slenderness_limit():
    # lambda <= 0.673 -> rho = 1 (b = w), NSR-10 F.4.2.2-1
    E = 2.0e8
    # esfuerzo muy bajo -> Fcr >> f -> lambda pequeño
    rho = plate_reduction_factor(w=0.05, t=0.003, f=1000.0, E=E, k=4.0)
    assert rho == 1.0


def test_plate_reduction_factor_reduced_above_slenderness_limit():
    E = 2.0e8
    # w/t grande y f alto -> lambda > 0.673 -> reducción
    rho = plate_reduction_factor(w=0.20, t=0.001, f=200_000.0, E=E, k=4.0)
    assert 0.0 < rho < 1.0


def test_plate_reduction_factor_matches_hand_calc():
    # Caso de mano: w=0.07m, t=0.0025m, f=Fy=345000 kPa, k=4 (alma), E=2e8 kPa
    E, mu, k = 2.0e8, 0.3, 4.0
    w, t, f = 0.07, 0.0025, 345_000.0
    Fcr = k * math.pi ** 2 * E / (12 * (1 - mu ** 2) * (w / t) ** 2)
    lam = math.sqrt(f / Fcr)
    expected_rho = 1.0 if lam <= 0.673 else min((1 - 0.22 / lam) / lam, 1.0)
    rho = plate_reduction_factor(w, t, f, E, k)
    assert math.isclose(rho, expected_rho, rel_tol=1e-9)


def test_effective_area_never_exceeds_gross_area():
    cat = default_catalog()
    paral = cat["PARAL 122x2.5mm"]
    Ae = effective_area_at_stress(paral, f=paral.Fy)
    assert 0 < Ae <= paral.A * 1.0001


def test_effective_area_increases_at_lower_stress():
    # A un esfuerzo f menor (columna esbelta, Fn<Fy), el ancho efectivo
    # nunca debe ser menor que a un esfuerzo mayor (Fy).
    cat = default_catalog()
    paral = cat["PARAL 122x2.5mm"]
    Ae_low_stress = effective_area_at_stress(paral, f=paral.Fy * 0.3)
    Ae_high_stress = effective_area_at_stress(paral, f=paral.Fy)
    assert Ae_low_stress >= Ae_high_stress - 1e-12


def test_effective_area_falls_back_without_segments():
    mat = Material("T", E=2e8, G=7.7e7, Fy=250_000.0)
    sec = Section(
        name="T", kind=SectionKind.CFS_UPRIGHT, material=mat,
        A=0.001, Iy=1e-6, Iz=1e-6, J=1e-7, depth=0.1, width=0.05, thickness=0.002,
        perforation_ratio=0.9,
    )
    Ae = effective_area_at_stress(sec, f=sec.Fy)
    assert math.isclose(Ae, 0.001 * 0.9, rel_tol=1e-9)


def test_shear_capacity_stocky_web_uses_060fy():
    # Alma robusta (h/t pequeño): Fv=0.60Fy, Va=Aw*Fv/1.60 (NSR-10 F.4.3.3-44)
    mat = Material("T", E=2e8, G=7.7e7, Fy=250_000.0)
    sec = Section(
        name="T", kind=SectionKind.BEAM_BOX, material=mat,
        A=0.001, Iy=1e-6, Iz=1e-6, J=1e-7, depth=0.05, width=0.03, thickness=0.005,
        effective_width_segments=[
            {"w": 0.05, "t": 0.005, "k": 4.0, "is_web": True},
            {"w": 0.05, "t": 0.005, "k": 4.0, "is_web": True},
        ],
    )
    Va = shear_capacity(sec, sec.Fy)
    Aw_total = 2 * 0.05 * 0.005
    expected = Aw_total * 0.60 * 250_000.0 / 1.60
    assert math.isclose(Va, expected, rel_tol=1e-9)


def test_shear_capacity_slender_web_reduced_below_stocky_value():
    mat = Material("T", E=2e8, G=7.7e7, Fy=250_000.0)
    sec_slender = Section(
        name="T", kind=SectionKind.BEAM_BOX, material=mat,
        A=0.001, Iy=1e-6, Iz=1e-6, J=1e-7, depth=0.5, width=0.03, thickness=0.0008,
        effective_width_segments=[
            {"w": 0.5, "t": 0.0008, "k": 4.0, "is_web": True},
        ],
    )
    Va_slender = shear_capacity(sec_slender, sec_slender.Fy)
    Aw = 0.5 * 0.0008
    Va_stocky_equivalent = Aw * 0.60 * 250_000.0 / 1.60
    assert Va_slender < Va_stocky_equivalent


def test_upright_check_reports_shear_and_component_checks():
    cat = default_catalog()
    paral = cat["PARAL 122x2.5mm"]
    r = check_upright_compression_bending(
        paral, "test", P=10.0, M2=0.2, M3=0.1, V2=0.5, V3=0.5, KLy=1.7, KLz=1.0,
    )
    assert r.Va > 0
    checks = r.component_checks
    assert set(checks) == {"P", "M2", "M3", "V2", "V3"}
    assert all(isinstance(v, bool) for v in checks.values())
