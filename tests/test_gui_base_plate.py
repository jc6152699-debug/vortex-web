"""
Verifica el panel opcional "Placa base / anclajes" en la GUI: por defecto
desactivado (no se inventa una geometría de anclaje que el usuario no
tiene), y cuando se activa, sus valores llegan a `PipelineInputs.
base_plate` y producen filas de resultado -- antes esto no existía en
absoluto (ver tests/test_base_plate.py para el motor).
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


def test_base_plate_fields_disabled_by_default(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    assert not win.chk_base_plate.isChecked()
    assert not win.sp_bp_length.isEnabled()
    win.on_update()
    assert win.last_inputs.base_plate is None
    assert win.pipeline_result.base_plate_rows == []


def test_checking_base_plate_enables_fields_and_flows_into_analysis(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.chk_base_plate.setChecked(True)
    assert win.sp_bp_length.isEnabled()

    win.sp_bp_anchor_tension.setValue(20.0)
    win.sp_bp_anchor_shear.setValue(12.0)
    win.on_update()

    assert win.last_inputs.base_plate is not None
    assert win.last_inputs.base_plate.anchor_capacity_tension_kn == 20.0
    assert len(win.pipeline_result.base_plate_rows) > 0
