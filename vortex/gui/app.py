"""
Aplicación de escritorio Vortex: interfaz visual para modelar, analizar y
verificar estanterías industriales de acero según NTC 5689, con un flujo
de trabajo similar al de Autodesk Inventor (modelado paramétrico) y
SAP2000 (análisis matricial + resultados coloreados por elemento).
"""
from __future__ import annotations

import datetime
import os
import sys
import traceback
from typing import Optional

from PySide6 import QtCore, QtWidgets

from ..geometry import RackParameters, build_selective_rack
from ..geometry.model import RackModel
from ..sections.catalog import default_catalog
from ..loads.seismic import AA_AV_BY_CITY
from ..analysis import PipelineInputs, PipelineResult, SeismicInputs, run_full_check
from ..report import ProjectInfo, ReportData, generate_memoria
from ..units import kgf_to_kn
from .viewer3d import Viewer3D

SOIL_TYPES = ["A", "B", "C", "D", "E"]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vortex — Cálculo y modelado de estanterías industriales (NTC 5689)")
        self.resize(1400, 900)

        self.catalog = default_catalog()
        self.model: Optional[RackModel] = None
        self.pipeline_result: Optional[PipelineResult] = None

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)

        self.form_panel = self._build_form_panel()
        main_layout.addWidget(self.form_panel, 0)

        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.viewer = Viewer3D()
        right.addWidget(self.viewer)

        self.results_table = QtWidgets.QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(
            ["Elemento", "Tipo", "Combinación crítica", "Detalle", "Ratio"]
        )
        self.results_table.horizontalHeader().setStretchLastSection(False)
        self.results_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.Stretch
        )
        right.addWidget(self.results_table)
        right.setSizes([650, 250])
        main_layout.addWidget(right, 1)

        self.status = self.statusBar()
        self.status.showMessage("Defina la geometría y presione \"Construir modelo\".")

    def _build_form_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(360)
        layout = QtWidgets.QVBoxLayout(panel)

        geo_box = QtWidgets.QGroupBox("Geometría")
        geo_form = QtWidgets.QFormLayout(geo_box)
        self.sp_bays = QtWidgets.QSpinBox(); self.sp_bays.setRange(1, 50); self.sp_bays.setValue(4)
        self.sp_bay_length = QtWidgets.QDoubleSpinBox(); self.sp_bay_length.setRange(0.5, 6.0)
        self.sp_bay_length.setValue(2.44); self.sp_bay_length.setSuffix(" m")
        self.sp_depth = QtWidgets.QDoubleSpinBox(); self.sp_depth.setRange(0.3, 3.0)
        self.sp_depth.setValue(1.06); self.sp_depth.setSuffix(" m")
        self.sp_n_levels = QtWidgets.QSpinBox(); self.sp_n_levels.setRange(1, 20); self.sp_n_levels.setValue(6)
        self.sp_h_first = QtWidgets.QDoubleSpinBox(); self.sp_h_first.setRange(0.3, 4.0)
        self.sp_h_first.setValue(1.20); self.sp_h_first.setSuffix(" m")
        self.sp_h_rest = QtWidgets.QDoubleSpinBox(); self.sp_h_rest.setRange(0.3, 4.0)
        self.sp_h_rest.setValue(1.80); self.sp_h_rest.setSuffix(" m")
        self.cb_base = QtWidgets.QComboBox(); self.cb_base.addItems(["pinned", "fixed"])
        geo_form.addRow("N° bahías", self.sp_bays)
        geo_form.addRow("Longitud de viga", self.sp_bay_length)
        geo_form.addRow("Profundidad de marco", self.sp_depth)
        geo_form.addRow("N° niveles de carga", self.sp_n_levels)
        geo_form.addRow("Altura piso→nivel 1", self.sp_h_first)
        geo_form.addRow("Altura entre niveles", self.sp_h_rest)
        geo_form.addRow("Base", self.cb_base)
        layout.addWidget(geo_box)

        sec_box = QtWidgets.QGroupBox("Secciones")
        sec_form = QtWidgets.QFormLayout(sec_box)
        self.cb_upright = QtWidgets.QComboBox()
        self.cb_beam = QtWidgets.QComboBox()
        self.cb_brace = QtWidgets.QComboBox()
        for name, sec in self.catalog.items():
            if "PARAL" in name:
                self.cb_upright.addItem(name)
            elif "VIGA" in name:
                self.cb_beam.addItem(name)
            elif "DIAGONAL" in name:
                self.cb_brace.addItem(name)
        sec_form.addRow("Paral", self.cb_upright)
        sec_form.addRow("Viga", self.cb_beam)
        sec_form.addRow("Diagonal", self.cb_brace)
        layout.addWidget(sec_box)

        load_box = QtWidgets.QGroupBox("Cargas")
        load_form = QtWidgets.QFormLayout(load_box)
        self.sp_pl = QtWidgets.QDoubleSpinBox(); self.sp_pl.setRange(0, 100000)
        self.sp_pl.setValue(2400.0); self.sp_pl.setSuffix(" kgf / nivel-bahía")
        self.sp_ll = QtWidgets.QDoubleSpinBox(); self.sp_ll.setRange(0, 50)
        self.sp_ll.setValue(0.0); self.sp_ll.setSuffix(" kN/m²")
        load_form.addRow("Carga de producto (PL)", self.sp_pl)
        load_form.addRow("Carga viva (LL)", self.sp_ll)
        layout.addWidget(load_box)

        seis_box = QtWidgets.QGroupBox("Sismo — NTC 5689 numeral 2.7")
        seis_form = QtWidgets.QFormLayout(seis_box)
        self.cb_city = QtWidgets.QComboBox()
        self.cb_city.addItems(sorted(AA_AV_BY_CITY.keys()))
        self.cb_city.setCurrentText("Medellín")
        self.cb_city.currentTextChanged.connect(self._on_city_changed)
        self.sp_aa = QtWidgets.QDoubleSpinBox(); self.sp_aa.setRange(0, 0.6); self.sp_aa.setDecimals(3)
        self.sp_av = QtWidgets.QDoubleSpinBox(); self.sp_av.setRange(0, 0.6); self.sp_av.setDecimals(3)
        self.cb_soil = QtWidgets.QComboBox(); self.cb_soil.addItems(SOIL_TYPES); self.cb_soil.setCurrentText("D")
        seis_form.addRow("Ciudad (NSR-10)", self.cb_city)
        seis_form.addRow("Aa", self.sp_aa)
        seis_form.addRow("Av", self.sp_av)
        seis_form.addRow("Tipo de perfil de suelo", self.cb_soil)
        layout.addWidget(seis_box)
        self._on_city_changed(self.cb_city.currentText())

        btn_build = QtWidgets.QPushButton("1. Construir modelo")
        btn_build.clicked.connect(self.on_build_model)
        btn_analyze = QtWidgets.QPushButton("2. Analizar y verificar")
        btn_analyze.clicked.connect(self.on_analyze)
        btn_export = QtWidgets.QPushButton("3. Exportar memoria de cálculo (.docx)")
        btn_export.clicked.connect(self.on_export)
        layout.addWidget(btn_build)
        layout.addWidget(btn_analyze)
        layout.addWidget(btn_export)
        layout.addStretch(1)

        disclaimer = QtWidgets.QLabel(
            "Este software es una herramienta de apoyo al cálculo. Todo "
            "diseño debe ser revisado y firmado por un ingeniero calculista "
            "responsable antes de su uso para fabricación o construcción."
        )
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(disclaimer)

        return panel

    def _on_city_changed(self, city: str) -> None:
        data = AA_AV_BY_CITY.get(city)
        if data:
            self.sp_aa.setValue(float(data["Aa"]))
            self.sp_av.setValue(float(data["Av"]))

    # ------------------------------------------------------------------
    def _current_params(self) -> RackParameters:
        n_levels = self.sp_n_levels.value()
        heights = [self.sp_h_first.value()] + [self.sp_h_rest.value()] * (n_levels - 1)
        return RackParameters(
            n_bays=self.sp_bays.value(),
            bay_length=self.sp_bay_length.value(),
            frame_depth=self.sp_depth.value(),
            level_heights=heights,
            upright_section=self.catalog[self.cb_upright.currentText()],
            beam_section=self.catalog[self.cb_beam.currentText()],
            brace_section=self.catalog[self.cb_brace.currentText()],
            base_fixity=self.cb_base.currentText(),
        )

    def on_build_model(self) -> None:
        try:
            params = self._current_params()
            self.model = build_selective_rack(params)
            self.viewer.show_model(self.model)
            self.results_table.setRowCount(0)
            self.status.showMessage(
                f"Modelo construido: {len(self.model.nodes)} nudos, "
                f"{len(self.model.members)} elementos. Presione \"Analizar\"."
            )
        except Exception as exc:
            self._show_error("Error al construir el modelo", exc)

    def on_analyze(self) -> None:
        if self.model is None:
            QtWidgets.QMessageBox.warning(self, "Vortex", "Primero construya el modelo.")
            return
        try:
            inputs = PipelineInputs(
                pl_per_level_kn=kgf_to_kn(self.sp_pl.value()),
                ll_kn_m2=self.sp_ll.value(),
                seismic=SeismicInputs(
                    soil_type=self.cb_soil.currentText(),
                    aa=self.sp_aa.value(), av=self.sp_av.value(),
                ),
            )
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            self.pipeline_result = run_full_check(self.model, inputs)
            QtWidgets.QApplication.restoreOverrideCursor()

            ratios = {mid: row.ratio for mid, row in self.pipeline_result.member_rows.items()}
            self.viewer.color_by_ratio(ratios)
            self._populate_results_table()
            n_fail = sum(1 for r in self.pipeline_result.member_rows.values() if r.ratio > 1.0)
            self.status.showMessage(
                f"Análisis completo. Ratio máximo: {self.pipeline_result.max_ratio():.2f}. "
                f"{n_fail} elemento(s) no cumplen."
            )
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._show_error("Error durante el análisis", exc)

    def _populate_results_table(self) -> None:
        rows = sorted(self.pipeline_result.member_rows.values(), key=lambda r: -r.ratio)
        self.results_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [row.label, row.kind, row.combo, row.detail, f"{row.ratio:.2f}"]
            for j, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(v)
                if j == 4:
                    if row.ratio > 1.0:
                        item.setBackground(QtCore.Qt.red)
                    elif row.ratio > 0.9:
                        item.setBackground(QtCore.Qt.yellow)
                self.results_table.setItem(i, j, item)
        self.results_table.resizeColumnsToContents()

    def on_export(self) -> None:
        if self.model is None or self.pipeline_result is None:
            QtWidgets.QMessageBox.warning(
                self, "Vortex", "Primero construya el modelo y ejecute el análisis."
            )
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar memoria de cálculo", "memoria_calculo.docx", "Word (*.docx)"
        )
        if not path:
            return
        try:
            project = ProjectInfo(
                titulo="ESTANTERÍA INDUSTRIAL DE ACERO",
                ciudad=self.cb_city.currentText(),
                fecha=datetime.date.today().strftime("%Y-%m-%d"),
                ingeniero="[Nombre del ingeniero calculista]",
                especialidad="Esp. Estructuras", matricula="[M.P.]",
            )
            design_rows = [
                {
                    "elemento": r.label, "tipo": r.kind, "combo": r.combo,
                    "demanda_capacidad": r.detail, "ratio": r.ratio,
                }
                for r in self.pipeline_result.member_rows.values()
            ]
            data = ReportData(
                project=project, model=self.model,
                dl_note="DL = Peso propio de la estructura (calculado automáticamente).",
                ll_kn_m2=self.sp_ll.value(),
                pl_per_level_kn=kgf_to_kn(self.sp_pl.value()),
                seismic_transversal=self.pipeline_result.seismic_transversal,
                seismic_longitudinal=self.pipeline_result.seismic_longitudinal,
                material_names=[self.catalog[self.cb_upright.currentText()].material.name],
                upright_section=self.catalog[self.cb_upright.currentText()],
                beam_section=self.catalog[self.cb_beam.currentText()],
                brace_section=self.catalog[self.cb_brace.currentText()],
                method_name="LRFD", combos=self.pipeline_result.combos,
                design_rows=design_rows,
            )
            generate_memoria(data, path)
            self.status.showMessage(f"Memoria de cálculo exportada: {path}")
            QtWidgets.QMessageBox.information(self, "Vortex", f"Memoria exportada a:\n{path}")
        except Exception as exc:
            self._show_error("Error al exportar la memoria", exc)

    def _show_error(self, title: str, exc: Exception) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setText(str(exc))
        box.setDetailedText(detail)
        box.exec()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
