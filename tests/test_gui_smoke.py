"""
Prueba de humo de la interfaz gráfica: construye la ventana principal,
ejecuta el flujo completo (construir modelo -> analizar -> exportar
memoria) sin interacción real de usuario, en modo "offscreen" (sin
requerir un display). No verifica el renderizado visual, sólo que la
aplicación no falle y que el pipeline complete correctamente conectado a
los widgets reales.
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


def test_main_window_build_analyze_export(qapp, tmp_path):
    from vortex.gui.app import MainWindow
    from vortex.report import ProjectInfo, ReportData, generate_memoria

    win = MainWindow()
    win.on_build_model()
    assert win.model is not None
    assert len(win.model.nodes) > 0
    assert len(win.model.members) > 0

    win.on_analyze()
    assert win.pipeline_result is not None
    # Parales, vigas Y diagonales/travesaños (BRACE): antes las diagonales
    # se modelaban para la rigidez del análisis pero nunca se verificaban
    # (código muerto en `design.connections.check_brace`) — ahora también
    # tienen su propia fila de resultado.
    assert len(win.pipeline_result.member_rows) == len(
        [m for m in win.model.members.values() if m.kind.name in ("UPRIGHT", "BEAM", "BRACE")]
    )
    assert win.results_table.rowCount() == len(win.pipeline_result.member_rows)

    design_rows = [
        {"elemento": r.label, "tipo": r.kind, "combo": r.combo,
         "demanda_capacidad": r.detail, "ratio": r.ratio}
        for r in win.pipeline_result.member_rows.values()
    ]
    project = ProjectInfo(
        titulo="TEST", ciudad="Medellín", fecha="2026-09-01",
        ingeniero="X", especialidad="Y", matricula="Z",
    )
    data = ReportData(
        project=project, model=win.model, dl_note="n", ll_kn_m2=0, pl_per_level_kn=10,
        seismic_transversal=win.pipeline_result.seismic_transversal,
        seismic_longitudinal=win.pipeline_result.seismic_longitudinal,
        material_names=["A572"],
        upright_section=win.catalog[win.cb_upright.currentText()],
        beam_section=win.catalog[win.cb_beam.currentText()],
        brace_section=win.catalog[win.cb_brace.currentText()],
        method_name="LRFD", combos=win.pipeline_result.combos, design_rows=design_rows,
    )
    out = tmp_path / "memoria.docx"
    generate_memoria(data, str(out))
    assert out.exists() and out.stat().st_size > 0


def test_on_update_rebuilds_and_reanalyzes_in_one_step(qapp):
    """"Actualizar" existe porque "Analizar" solo, sin reconstruir,
    reutiliza el último modelo — si el usuario cambia geometría/secciones
    y solo presiona "Analizar", el cambio no se refleja. Verifica que
    "Actualizar" sí reconstruye con los valores actuales."""
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.on_update()
    assert win.model is not None
    assert win.pipeline_result is not None
    n_bays_before = win.model.n_bays

    win.sp_bays.setValue(n_bays_before + 1)
    win.on_update()
    assert win.model.n_bays == n_bays_before + 1
    assert win.pipeline_result is not None


def test_on_clear_resets_model_but_keeps_form_values(qapp):
    from vortex.gui.app import MainWindow

    win = MainWindow()
    win.on_update()
    assert win.model is not None
    bays_value = win.sp_bays.value()

    win.on_clear()
    assert win.model is None
    assert win.pipeline_result is None
    assert win.results_table.rowCount() == 0
    assert win.sp_bays.value() == bays_value  # el formulario no se toca
