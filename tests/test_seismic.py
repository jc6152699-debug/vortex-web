"""
Validación del módulo de sismo contra la norma NTC 5689 (Tablas 1 y 2, texto
literal) y contra los valores numéricos reales del anexo
CARGAS_DE_SISMO.xlsx (proyecto de referencia, ciudad Medellín, perfil D).
"""
import math

from vortex.loads import seismic as sm


def test_tabla1_ca_breakpoints_match_norma():
    assert sm.coef_ca("A", 0.10) == 0.08
    assert sm.coef_ca("D", 0.10) == 0.16
    assert sm.coef_ca("D", 0.20) == 0.28
    assert sm.coef_ca("E", 0.40) == 0.44


def test_tabla2_cv_breakpoints_match_norma():
    assert sm.coef_cv("D", 0.20) == 0.40
    assert sm.coef_cv("C", 0.30) == 0.45
    assert sm.coef_cv("E", 0.10) == 0.35


def test_interpolacion_lineal_intermedia():
    # D entre Aa=0.10 (Ca=0.16) y Aa=0.20 (Ca=0.28): en Aa=0.15 -> 0.22
    assert math.isclose(sm.coef_ca("D", 0.15), 0.22, rel_tol=1e-9)


def test_regla_aa_menor_005():
    assert sm.coef_ca("D", 0.03) == 0.03
    assert sm.coef_cv("B", 0.02) == 0.02


def test_r_factor_por_direccion():
    assert sm.response_reduction_factor(sm.SeismicDirection.TRANSVERSAL, 9.5) == 4.0
    assert sm.response_reduction_factor(sm.SeismicDirection.LONGITUDINAL, 9.5) == 6.0
    # estanterías <= 2.44 m: R=4 en ambas direcciones
    assert sm.response_reduction_factor(sm.SeismicDirection.LONGITUDINAL, 2.0) == 4.0


def test_cv_medellin_perfil_d_coincide_con_hoja_de_calculo():
    # Medellín: Aa=0.15, Av=0.20 ; perfil D -> Cv(D,0.20)=0.40 (valor de
    # tabla exacto, verificado contra CARGAS_DE_SISMO.xlsx hoja MODELO,
    # donde Cs se calculó con Cv=0.40 para T=1.2377s, R=4 y R=6:
    #   Cs_marcos = 1.2*0.40/(4*1.2377^(2/3)) ~ 0.10413
    #   Cs_vigas  = 1.2*0.40/(6*1.2377^(2/3)) ~ 0.06942
    cv = sm.coef_cv("D", 0.20)
    assert cv == 0.40
    t = 1.2370467550415007
    cs_marcos = sm.cs_method1(cv, r=4.0, period_s=t)
    cs_vigas = sm.cs_method1(cv, r=6.0, period_s=t)
    assert math.isclose(cs_marcos, 0.104133509, rel_tol=1e-4)
    assert math.isclose(cs_vigas, 0.069422339, rel_tol=1e-4)
    assert math.isclose(cs_marcos / cs_vigas, 1.5, rel_tol=1e-9)


def test_ws_formula():
    # Ws = 0.67*PLRF*PL + DL + 0.25*LL
    ws = sm.seismic_weight(pl=24.0, dl=0.72, ll=0.0, plrf=1.0)
    assert math.isclose(ws, 0.67 * 24.0 + 0.72, rel_tol=1e-12)


def test_distribucion_vertical_coincide_con_hoja_de_calculo():
    # 6 niveles, separación 1.9 m, 24 kN por nivel (CARGAS_DE_SISMO.xlsx,
    # hoja "CARGAS "). Fracciones esperadas (columna "Sumatoria" del
    # anexo): 0.047619, 0.095238, 0.142857, 0.190476, 0.238095, 0.285714
    levels = [
        sm.LevelWeight(i, elevation_m=1.9 * i, weight_kn=24.0) for i in range(1, 7)
    ]
    fx = sm.vertical_distribution(1.0, levels)  # V=1 -> fx = fracciones
    expected = [0.047619047619, 0.095238095238, 0.142857142857,
                0.190476190476, 0.238095238095, 0.285714285714]
    for i, exp in zip(range(1, 7), expected):
        assert math.isclose(fx[i], exp, rel_tol=1e-9)
    assert math.isclose(sum(fx.values()), 1.0, rel_tol=1e-9)


def test_plrf_transversal_es_uno():
    assert sm.pallet_load_reduction_factor(sm.SeismicDirection.TRANSVERSAL) == 1.0


def test_plrf_longitudinal_requiere_promedio_maxima():
    v = sm.pallet_load_reduction_factor(
        sm.SeismicDirection.LONGITUDINAL, pl_promedio=76.0, pl_maxima=100.0,
    )
    assert math.isclose(v, 0.76, rel_tol=1e-9)
