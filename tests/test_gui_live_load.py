"""
Verifica el cálculo automático de la carga viva (LL) en la GUI a partir
de la ocupación elegida (valores de referencia NSR-10 Título B, Tabla
B.4.2.1-1) — antes había que escribir el valor de LL a mano sin ninguna
ayuda; ahora un desplegable de ocupación lo autocompleta, igual que Aa/Av
se autocompletan al elegir la ciudad.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pyside6 = pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


def test_default_preset_is_zero_and_field_disabled(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    assert win.cb_ll_preset.currentText() == "Sin plataforma de trabajo (LL = 0)"
    assert win.sp_ll.value() == 0.0
    assert not win.sp_ll.isEnabled()


def test_choosing_a_preset_fills_and_locks_the_field(qapp):
    from vortex.gui.app import MainWindow
    from vortex.loads.dead_live import LIVE_LOAD_PRESETS_KN_M2

    win = MainWindow()
    win.cb_ll_preset.setCurrentText("Bodega — almacenamiento pesado")
    assert win.sp_ll.value() == LIVE_LOAD_PRESETS_KN_M2["Bodega — almacenamiento pesado"]
    assert not win.sp_ll.isEnabled()


def test_manual_option_unlocks_the_field(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.cb_ll_preset.setCurrentText("Bodega — almacenamiento liviano")
    win.cb_ll_preset.setCurrentText("Manual (ingresar valor)")
    assert win.sp_ll.isEnabled()


def test_selected_ll_flows_into_the_analysis(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.cb_ll_preset.setCurrentText("Bodega — almacenamiento liviano")
    win.on_update()
    assert win.last_inputs.ll_kn_m2 == 6.0
    assert win.pipeline_result.ll_total_kn > 0.0
