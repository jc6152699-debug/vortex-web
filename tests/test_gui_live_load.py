"""
Verifica que la carga viva (LL) en la GUI sea un único campo manual (kN/m²,
por defecto 0), sin un selector de "origen" con ocupaciones preestablecidas
(NSR-10 Título B): ese selector se retiró porque mezclaba, en una misma
casilla, el concepto de LL de estanterías (numeral 2.1 NTC 5689, casi
siempre 0 salvo plataforma transitable) con cargas vivas de edificaciones
que no aplican a la mayoría de los proyectos, y confundía más de lo que
ayudaba.
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


def test_ll_field_defaults_to_zero_and_is_always_editable(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    assert win.sp_ll.value() == 0.0
    assert win.sp_ll.isEnabled()
    assert not hasattr(win, "cb_ll_preset")


def test_manual_ll_value_flows_into_the_analysis(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.sp_ll.setValue(6.0)
    win.on_update()
    assert win.last_inputs.ll_kn_m2 == 6.0
    assert win.pipeline_result.ll_total_kn > 0.0
