"""
Pruebas de la barra de herramientas tipo software CAD/ingeniería
profesional: "Más herramientas", "Vista" y "Diagramas y especificaciones"
son menús desplegables (QToolButton + QMenu) agrupados a la izquierda, no
ventanas aparte ni acciones de texto sueltas; y el panel de
"Recomendaciones IA" es un panel acoplable (QDockWidget) siempre visible
junto al visor 3D, no una pestaña escondida.
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


def _toolbar_buttons(win):
    from PySide6 import QtWidgets
    toolbar = win.findChildren(QtWidgets.QToolBar)[0]
    buttons = []
    for action in toolbar.actions():
        w = toolbar.widgetForAction(action)
        if isinstance(w, QtWidgets.QToolButton):
            buttons.append(w)
    return buttons


def test_toolbar_starts_with_tools_dropdown_button(qapp):
    from vortex.gui.app import MainWindow
    from PySide6 import QtWidgets

    win = MainWindow()
    buttons = _toolbar_buttons(win)
    assert buttons, "no se encontró ningún QToolButton en la barra"

    # El primer botón desplegable de la barra debe ser "Más herramientas".
    first_widget = buttons[0]
    assert isinstance(first_widget, QtWidgets.QToolButton)
    assert "Más herramientas" in first_widget.text()
    assert first_widget.menu() is not None
    assert first_widget is win.btn_tools


def test_view_dropdown_is_second_and_next_to_tools(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    buttons = _toolbar_buttons(win)
    labels = [b.text() for b in buttons]
    assert any("Más herramientas" in l for l in labels)
    assert any("Vista" in l for l in labels)
    assert any("Diagramas y especificaciones" in l for l in labels)
    # "Vista" debe quedar justo después de "Más herramientas" (mismo grupo
    # de la izquierda), no al final de la barra.
    idx_tools = next(i for i, l in enumerate(labels) if "Más herramientas" in l)
    idx_view = next(i for i, l in enumerate(labels) if "Vista" in l)
    assert idx_view == idx_tools + 1


def test_tools_dropdown_menu_has_expected_actions(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    menu = win.btn_tools.menu()
    labels = [a.text() for a in menu.actions() if a.text()]
    assert any("Construir modelo" in l for l in labels)
    assert any("Analizar y verificar" in l for l in labels)
    assert any("Memoria de cálculo" in l for l in labels)
    assert any("Fuerzas por elemento" in l for l in labels)
    assert any("Recomendaciones IA" in l for l in labels)
    # El export de "Diagrama de cargas (.png)" se retiró de este menú (ya
    # está disponible en la pestaña "Cargas de producto" del desplegable
    # "Diagramas y especificaciones").
    assert not any("Diagrama de cargas" in l for l in labels)


def test_tools_dropdown_build_action_runs_handler(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    assert win.model is None
    menu = win.btn_tools.menu()
    act_build = next(a for a in menu.actions() if "Construir modelo" in a.text())
    act_build.trigger()
    assert win.model is not None


def test_view_dropdown_has_force_lines_and_fit_view_controls(qapp):
    from vortex.gui.app import MainWindow
    from PySide6 import QtWidgets

    win = MainWindow()
    menu = win.btn_view.menu()
    assert menu is not None
    actions = menu.actions()
    assert len(actions) == 1
    panel = actions[0].defaultWidget()
    assert panel is not None

    # Los controles existen y son los mismos objetos que usa el resto de
    # la clase (no una copia aparte).
    assert isinstance(win.chk_show_diagram, QtWidgets.QCheckBox)
    assert isinstance(win.cb_diagram_pattern, QtWidgets.QComboBox)
    assert isinstance(win.cb_diagram_component, QtWidgets.QComboBox)
    assert isinstance(win.sp_diagram_scale, QtWidgets.QDoubleSpinBox)
    assert isinstance(win.sp_view_zoom, QtWidgets.QDoubleSpinBox)
    assert win.chk_show_diagram.parentWidget() is panel or win.chk_show_diagram in panel.findChildren(QtWidgets.QCheckBox)


def test_toolbar_remaining_actions_after_dropdown(qapp):
    from vortex.gui.app import MainWindow
    from PySide6 import QtWidgets

    win = MainWindow()
    toolbars = win.findChildren(QtWidgets.QToolBar)
    text_action_labels = [a.text() for a in toolbars[0].actions() if a.text()]
    button_labels = [b.text() for b in _toolbar_buttons(win)]

    assert any("Actualizar" in l for l in text_action_labels)
    assert any("Borrar" in l for l in text_action_labels)
    assert any("Diagramas y especificaciones" in l for l in button_labels)
    # Ya no deben quedar como acciones de texto sueltas en la barra (ahora
    # viven dentro de los menús desplegables).
    assert not any("Construir modelo" in l for l in text_action_labels)
    assert not any("Analizar y verificar" in l for l in text_action_labels)
    assert not any("Memoria de cálculo" in l for l in text_action_labels)
    assert not any("Fuerzas por elemento" in l for l in text_action_labels)
    assert not any("Diagrama de cargas (.png)" in l for l in text_action_labels)


def test_load_diagram_tab_removed_from_main_tabs(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.on_update()
    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert not any("Diagrama de cargas" in t for t in titles)
    assert win.load_diagram_panel is not None


def test_ai_panel_is_a_persistent_dock_not_a_tab(qapp):
    from vortex.gui.app import MainWindow
    from PySide6 import QtWidgets

    win = MainWindow()
    win.on_update()

    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert not any("Recomendaciones IA" in t for t in titles)

    assert isinstance(win.ai_dock, QtWidgets.QDockWidget)
    assert win.ai_dock.widget() is win.ai_panel
    # Visible por defecto (no escondido detrás de una pestaña). Se usa
    # isHidden() en vez de isVisible(): en la prueba automatizada la
    # ventana principal nunca se muestra (offscreen), así que isVisible()
    # sería False para CUALQUIER widget sin importar su estado real;
    # isHidden() sí refleja si algo lo ocultó explícitamente (o no).
    assert not win.ai_dock.isHidden()


def test_ai_dock_toggles_visibility_from_menu_action(qapp):
    """No se ejecuta `on_update()` a propósito: `_on_ai_toolbar_clicked`
    dispara una consulta real a la IA (red) cuando ya hay un análisis
    corrido, y esta prueba sólo quiere verificar el toggle de
    visibilidad del panel, sin depender de la red."""
    from vortex.gui.app import MainWindow

    win = MainWindow()
    assert win.pipeline_result is None
    assert not win.ai_dock.isHidden()

    win._on_ai_toolbar_clicked()  # oculta (estaba visible)
    assert win.ai_dock.isHidden()

    win._on_ai_toolbar_clicked()  # vuelve a mostrar (sin análisis -> no llama a la IA)
    assert not win.ai_dock.isHidden()


def test_ai_groq_config_section_collapsed_by_default(qapp):
    """La sección 'Configuración (Groq)' debe existir pero arrancar
    colapsada (no visible) — ya no es un bloque de texto siempre a la
    vista dentro del panel de IA."""
    from vortex.gui.app import MainWindow
    from PySide6 import QtWidgets

    win = MainWindow()
    headers = [
        b for b in win.ai_panel.findChildren(QtWidgets.QToolButton)
        if "Configuración" in b.text()
    ]
    assert len(headers) == 1
    header = headers[0]
    assert header.isCheckable()
    assert not header.isChecked()


def test_candidate_models_no_longer_include_decommissioned_ones(qapp):
    from vortex.gui import app as app_module

    decommissioned = {
        "llama-3.1-8b-instant", "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile", "gemma2-9b-it",
    }
    assert not (decommissioned & set(app_module.CANDIDATE_MODELS))
    assert len(app_module.CANDIDATE_MODELS) > 0
