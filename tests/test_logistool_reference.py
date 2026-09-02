"""
Pruebas de regresión contra el proyecto de referencia REAL (memoria de
cálculo LOGISTOOL: estantería selectiva 9.50m x 6 niveles x 2400kg/nivel,
Medellín, perfil de suelo D) — verifican que:

1. Los valores por defecto que carga la GUI al abrir (formulario) SON
   exactamente los del proyecto real (n_bays=5, n_niveles=5, viga real,
   factor EL sin relajar, PLpromedio/PLmáxima=0.76), no una geometría de
   ejemplo distinta.
2. Con esos valores por defecto, el motor de análisis reproduce de forma
   cercana (documentada, no exacta bit a bit — ver notas en
   `design/upright_cfs.py` sobre las limitaciones conocidas) los
   resultados reales de la tabla "RESISTENCIA PARAL 120 2.5mm MODELO CFS"
   de esa memoria: P=84.329 kN en el paral interior de la base (H=1.20m).

Esto existe para que un futuro cambio en los valores por defecto de
`_build_form_panel` (o en el motor) no vuelva a desviar silenciosamente
la carga de ejemplo del proyecto real que la valida.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pyside6 = pytest.importorskip("PySide6")

# Rango real de la tabla "RESISTENCIA PARAL 120 2.5mm MODELO CFS" de la
# memoria de cálculo LOGISTOOL (paral interior, H=1.20m -> P=84.329 kN).
REAL_BASE_INTERIOR_P_KN = 84.329
REAL_TOLERANCE = 0.10  # 10%: el modelo 3D completo de Vortex difiere del
# método envolvente simplificado del anexo (ver docstring de
# `design.upright_cfs`); ~1-3% es lo típico observado, 10% es margen de
# sobra para no volverse una prueba frágil ante ajustes menores.


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


def test_default_form_matches_logistool_reference_geometry(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    assert win.sp_bays.value() == 5, "n_bays por defecto debe ser 5 (6 parales), no 4"
    assert win.sp_n_levels.value() == 5, "n_niveles por defecto debe ser 5, no 6"
    assert win.sp_h_first.value() == pytest.approx(1.20)
    assert win.sp_h_rest.value() == pytest.approx(1.80)
    assert win.sp_bay_length.value() == pytest.approx(2.44)
    assert win.sp_depth.value() == pytest.approx(1.06)
    assert win.cb_upright.currentText() == "PARAL 122x2.5mm"
    assert win.cb_beam.currentText() == "VIGA CAJA 160x60x1.5mm", (
        "la viga por defecto debe ser la sección REAL del proyecto "
        "(160x60x1.5mm), no la primera de la lista (100x50x2.0mm)"
    )
    assert win.sp_pl.value() == pytest.approx(2400.0)
    assert win.cb_city.currentText() == "Medellín"
    assert win.cb_soil.currentText() == "D"
    assert not win.chk_el_relaxed.isChecked(), (
        "el proyecto real usa la combinación literal 1.2DL+1.5EL+0.85PL "
        "(EL sin relajar) — el checkbox de relajación debe iniciar "
        "DESMARCADO"
    )
    assert win.sp_pl_promedio_ratio.value() == pytest.approx(0.76)


def test_default_example_reproduces_real_base_paral_axial_force(qapp):
    """Construye el modelo y analiza EXACTAMENTE con los valores por
    defecto de la GUI (sin tocar el formulario) y verifica que el paral
    interior de la base quede dentro de un margen razonable del valor
    real documentado (84.329 kN)."""
    from vortex.gui.app import MainWindow
    from vortex.geometry.model import MemberKind

    win = MainWindow()
    win.on_update()
    assert win.model is not None and win.pipeline_result is not None

    model = win.model
    result = win.pipeline_result

    base_interior = [
        m for m in model.members_of_kind(MemberKind.UPRIGHT)
        if m.level_index == 0 and m.side == "frente"
        and 0 < m.frame_index < model.n_bays  # interior, no en el borde
    ]
    assert base_interior, "no se encontraron parales interiores en la base"

    ps = [result.member_rows[m.id].raw_force for m in base_interior]
    p_avg = sum(ps) / len(ps)

    rel_error = abs(p_avg - REAL_BASE_INTERIOR_P_KN) / REAL_BASE_INTERIOR_P_KN
    assert rel_error < REAL_TOLERANCE, (
        f"P calculado ({p_avg:.2f} kN) se desvía {rel_error:.1%} del valor "
        f"real de la memoria LOGISTOOL ({REAL_BASE_INTERIOR_P_KN} kN); "
        f"revisar geometría/secciones/combinaciones por defecto."
    )


def test_seismic_longitudinal_base_shear_matches_reference_order_of_magnitude(qapp):
    """El cortante sísmico basal longitudinal (dirección en el plano de
    los marcos mostrados en el anexo 'CARGAS DE SISMO') calculado con los
    valores por defecto debe quedar cerca del real (~5.55 kN, suma de las
    fuerzas por nivel de la tabla de Cargas de sismo del anexo)."""
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.on_update()
    v_real = 5.55
    v_calc = win.pipeline_result.seismic_longitudinal.v_base
    rel_error = abs(v_calc - v_real) / v_real
    assert rel_error < 0.25, (
        f"V_base longitudinal calculado ({v_calc:.2f} kN) se desvía "
        f"{rel_error:.1%} del real (~{v_real} kN)"
    )
