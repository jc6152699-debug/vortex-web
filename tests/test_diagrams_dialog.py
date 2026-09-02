"""
Pruebas de la ventana "Diagramas y especificaciones" (`DiagramsDialog`):
verifica que se pueda abrir desde la barra de herramientas de la ventana
principal después de analizar, que arme sus 6 pestañas sin error, que la
tabla NIVEL/FX de sismo se llene con datos reales, y que las gráficas de
momento/axial/cortante y el reporte de especificaciones de parales se
generen correctamente para todas las combinaciones de dirección sísmica
disponibles.
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


def _analyzed_window(qapp):
    from vortex.gui.app import MainWindow
    win = MainWindow()
    win.on_update()
    assert win.pipeline_result is not None
    return win


def test_diagrams_dialog_requires_analysis_first(qapp, monkeypatch):
    from vortex.gui.app import MainWindow
    from PySide6 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    win = MainWindow()
    # Sin construir/analizar: debe advertir, no lanzar excepción ni abrir diálogo.
    win.on_open_diagrams_dialog()


def test_diagrams_dialog_opens_with_six_tabs(qapp):
    win = _analyzed_window(qapp)
    from vortex.gui.diagrams_dialog import DiagramsDialog

    dlg = DiagramsDialog(win.model, win.pipeline_result, win.last_inputs, win)
    assert dlg.tabs.count() == 6
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert any("Cargas de producto" in t for t in titles)
    assert any("Cargas de sismo" in t for t in titles)
    assert any("momentos" in t for t in titles)
    assert any("axial" in t for t in titles)
    assert any("cortante" in t for t in titles)
    assert any("parales" in t for t in titles)


def test_seismic_tab_table_matches_pipeline_result(qapp):
    win = _analyzed_window(qapp)
    from vortex.gui.diagrams_dialog import DiagramsDialog

    dlg = DiagramsDialog(win.model, win.pipeline_result, win.last_inputs, win)
    n_levels = len(win.pipeline_result.seismic_transversal.fx_by_level)
    assert dlg.table_seismic_levels.rowCount() == n_levels

    # Cambiar a longitudinal debe repoblar la tabla con los mismos niveles.
    dlg.cb_seismic_dir.setCurrentIndex(1)
    assert dlg.table_seismic_levels.rowCount() == n_levels
    fx_col2 = dlg.table_seismic_levels.item(0, 2).text()
    expected = list(sorted(win.pipeline_result.seismic_longitudinal.fx_by_level.items()))[0][1]
    assert abs(float(fx_col2) - expected) < 1e-3


def test_force_tabs_render_for_both_seismic_patterns(qapp):
    from PySide6 import QtWidgets
    win = _analyzed_window(qapp)
    from vortex.gui.diagrams_dialog import DiagramsDialog

    dlg = DiagramsDialog(win.model, win.pipeline_result, win.last_inputs, win)
    for i in range(dlg.tabs.count()):
        w = dlg.tabs.widget(i)
        combos = w.findChildren(QtWidgets.QComboBox)
        if len(combos) != 2:
            continue
        cb_pattern, cb_combo = combos
        for pattern_idx in (0, 1):
            cb_pattern.setCurrentIndex(pattern_idx)
            assert cb_combo.count() >= 1
            for combo_idx in range(cb_combo.count()):
                cb_combo.setCurrentIndex(combo_idx)


def test_upright_spec_report_mentions_all_upright_sections(qapp):
    win = _analyzed_window(qapp)
    from vortex.geometry.model import MemberKind
    from vortex.report import upright_section_report

    report = upright_section_report(win.model, win.pipeline_result, win.last_inputs)
    section_names = {m.section.name for m in win.model.members_of_kind(MemberKind.UPRIGHT)}
    for name in section_names:
        assert name in report
    assert "RATIO GOBERNANTE" in report


def test_seismic_load_diagram_and_force_diagrams_return_figures(qapp):
    win = _analyzed_window(qapp)
    from vortex.analysis import element_forces_table
    from vortex.report import plot_seismic_load_diagram, plot_frame_force_diagram

    fig = plot_seismic_load_diagram(win.model, win.pipeline_result.seismic_transversal)
    assert fig is not None

    rows = element_forces_table(win.model, win.pipeline_result, win.last_inputs, el_pattern="EL_X")
    seismic_rows = [r for r in rows if "EL" in r.output_case]
    assert seismic_rows
    for quantity in ("M3", "P", "V2"):
        fig = plot_frame_force_diagram(win.model, seismic_rows, quantity)
        assert fig is not None


def test_toolbar_action_present_and_wired(qapp, monkeypatch):
    """'Diagramas y especificaciones' ahora es un QToolButton con menú
    desplegable (no una QAction de texto suelta) — se verifica el botón y
    que cada ítem del menú abra el diálogo sin lanzar excepciones."""
    win = _analyzed_window(qapp)
    from PySide6 import QtWidgets
    from vortex.gui.diagrams_dialog import DiagramsDialog

    toolbar = win.findChildren(QtWidgets.QToolBar)[0]
    buttons = [
        toolbar.widgetForAction(a) for a in toolbar.actions()
        if isinstance(toolbar.widgetForAction(a), QtWidgets.QToolButton)
    ]
    match = [b for b in buttons if "Diagramas y especificaciones" in b.text()]
    assert len(match) == 1
    menu = match[0].menu()
    assert menu is not None
    menu_actions = [a for a in menu.actions() if a.text()]
    assert len(menu_actions) == 6

    # No debe lanzar excepción al dispararla (ya hay modelo y resultado).
    # `exec()` se reemplaza por un no-op: en la prueba automatizada no hay
    # usuario que cierre el diálogo modal, y aquí sólo interesa verificar
    # que se construye sin errores, no la interacción manual.
    monkeypatch.setattr(DiagramsDialog, "exec", lambda self: None)
    for a in menu_actions:
        a.trigger()
