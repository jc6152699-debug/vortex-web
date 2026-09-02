"""
Ventana "Diagramas y especificaciones": reúne en un solo lugar (con
pestañas) el diagrama de cargas de producto, el diagrama de cargas de
sismo (con la tabla NIVEL/FX calculada), los diagramas de momento, fuerza
axial y cortante (estilo SAP2000, sobre la combinación de carga que se
elija), y el reporte de especificaciones ("Member Check") de cada sección
de paral usada en el modelo.

Esta ventana NO recalcula nada: toma el `RackModel` y el `PipelineResult`
ya calculados por "Analizar y verificar" (`vortex.analysis.run_full_check`)
y sólo los dibuja/formatea, igual que el resto de paneles de la GUI
(`_populate_load_diagram_panel`, `_populate_summary_panel`, etc.) — así el
dibujo nunca se desincroniza del cálculo real.
"""
from __future__ import annotations

import io
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..analysis import PipelineInputs, PipelineResult, element_forces_table
from ..geometry.model import RackModel
from ..loads import plot_product_load_diagram
from ..report import (
    plot_seismic_load_diagram,
    plot_frame_force_diagram,
    seismic_levels_table,
    upright_section_report,
)

_QUANTITY_LABELS = {
    "M3": "Diagrama de momentos (M3)",
    "P": "Diagrama de fuerza axial (P)",
    "V2": "Diagrama de fuerza cortante (V2)",
}


def _fig_to_pixmap(fig, dpi: int = 150) -> QtGui.QPixmap:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    import matplotlib.pyplot as plt
    plt.close(fig)
    pixmap = QtGui.QPixmap()
    pixmap.loadFromData(buf.getvalue())
    return pixmap


class _ImageTab(QtWidgets.QWidget):
    """Pestaña genérica: imagen centrada con scroll + botón para guardarla
    como PNG. Las subclases/usuarios sólo deben llamar `set_figure`."""

    def __init__(self, default_filename: str, parent=None):
        super().__init__(parent)
        self._default_filename = default_filename
        self._current_fig = None

        layout = QtWidgets.QVBoxLayout(self)
        self.controls_row = QtWidgets.QHBoxLayout()
        layout.addLayout(self.controls_row)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_image = QtWidgets.QLabel("Generando diagrama…")
        self.lbl_image.setAlignment(QtCore.Qt.AlignCenter)
        self.scroll.setWidget(self.lbl_image)
        layout.addWidget(self.scroll, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_save = QtWidgets.QPushButton("💾  Guardar imagen (.png)")
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    def set_figure(self, fig) -> None:
        self._current_fig = fig
        pixmap = _fig_to_pixmap(fig)
        self.lbl_image.setPixmap(pixmap)
        self.lbl_image.adjustSize()

    def set_error(self, exc: Exception) -> None:
        self.lbl_image.setPixmap(QtGui.QPixmap())
        self.lbl_image.setText(f"No se pudo generar el diagrama:\n{exc}")

    def _on_save(self) -> None:
        if self._current_fig is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar diagrama", self._default_filename, "Imagen PNG (*.png)"
        )
        if not path:
            return
        try:
            self._current_fig.savefig(path, dpi=150)
            QtWidgets.QMessageBox.information(self, "Vortex", f"Diagrama exportado a:\n{path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Vortex", f"Error al exportar:\n{exc}")


class DiagramsDialog(QtWidgets.QDialog):
    """Ventana con pestañas: Cargas de producto, Cargas de sismo,
    Momento/Axial/Cortante y Especificaciones de parales."""

    def __init__(
        self,
        model: RackModel,
        result: PipelineResult,
        inputs: PipelineInputs,
        parent=None,
        initial_tab: int = 0,
    ):
        super().__init__(parent)
        self.model = model
        self.result = result
        self.inputs = inputs
        self._force_rows_cache = {}  # el_pattern -> List[ElementForceRow]

        self.setWindowTitle("Vortex — Diagramas de cargas, sismo y especificaciones")
        self.resize(1200, 860)

        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)

        close_row = QtWidgets.QHBoxLayout()
        close_row.addStretch(1)
        btn_close = QtWidgets.QPushButton("Cerrar")
        btn_close.clicked.connect(self.accept)
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)

        self._build_load_tab()
        self._build_seismic_tab()
        self._build_force_tab("M3")
        self._build_force_tab("P")
        self._build_force_tab("V2")
        self._build_spec_tab()

        if 0 <= initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(initial_tab)

    # ------------------------------------------------------------------
    # Cargas de producto (mismo diagrama que la pestaña principal / botón
    # "Diagrama de cargas (.png)" de la barra de herramientas).
    # ------------------------------------------------------------------
    def _build_load_tab(self) -> None:
        tab = _ImageTab("diagrama_cargas_producto.png")
        self.tabs.addTab(tab, "🖼 Cargas de producto")
        try:
            dist = self.result.load_distribution
            if dist is None:
                raise ValueError("El resultado del análisis no trae reparto de cargas.")
            fig = plot_product_load_diagram(self.model, dist)
            tab.set_figure(fig)
        except Exception as exc:
            tab.set_error(exc)

    # ------------------------------------------------------------------
    # Cargas de sismo: selector de dirección + diagrama + tabla NIVEL/FX.
    # ------------------------------------------------------------------
    def _build_seismic_tab(self) -> None:
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "🌍 Cargas de sismo")
        layout = QtWidgets.QVBoxLayout(tab)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Dirección:"))
        self.cb_seismic_dir = QtWidgets.QComboBox()
        self.cb_seismic_dir.addItems([
            "Transversal (marcos, R=4)", "Longitudinal (vigas, R=6)",
        ])
        self.cb_seismic_dir.currentIndexChanged.connect(self._populate_seismic_tab)
        controls.addWidget(self.cb_seismic_dir)
        controls.addStretch(1)
        layout.addLayout(controls)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self._seismic_image_tab = _ImageTab("diagrama_cargas_sismo.png")
        splitter.addWidget(self._seismic_image_tab)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.addWidget(QtWidgets.QLabel("Fuerza sísmica horizontal Fx por nivel "
                                                  "(NTC 5689 numeral 2.7.3):"))
        self.table_seismic_levels = QtWidgets.QTableWidget(0, 3)
        self.table_seismic_levels.setHorizontalHeaderLabels(
            ["NIVEL", "Altura desde piso [m]", "FX [kN]"]
        )
        self.table_seismic_levels.horizontalHeader().setStretchLastSection(True)
        right_layout.addWidget(self.table_seismic_levels, 1)

        self.lbl_seismic_params = QtWidgets.QLabel("—")
        self.lbl_seismic_params.setWordWrap(True)
        self.lbl_seismic_params.setStyleSheet("color: #96a1ad; font-size: 11px;")
        right_layout.addWidget(self.lbl_seismic_params)
        splitter.addWidget(right)
        splitter.setSizes([760, 400])

        layout.addWidget(splitter, 1)
        self._populate_seismic_tab()

    def _current_seismic_result(self):
        if self.cb_seismic_dir.currentIndex() == 0:
            return self.result.seismic_transversal, "Transversal (marcos, R=4)"
        return self.result.seismic_longitudinal, "Longitudinal (vigas, R=6)"

    def _populate_seismic_tab(self) -> None:
        seis, direction_label = self._current_seismic_result()
        try:
            fig = plot_seismic_load_diagram(self.model, seis, direction_label=direction_label)
            self._seismic_image_tab.set_figure(fig)
        except Exception as exc:
            self._seismic_image_tab.set_error(exc)

        rows = seismic_levels_table(seis, self.model)
        self.table_seismic_levels.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [
                str(row["nivel"]),
                f"{row['elevacion_m']:.2f}" if row["elevacion_m"] is not None else "—",
                f"{row['fx_kn']:.3f}",
            ]
            for j, v in enumerate(vals):
                self.table_seismic_levels.setItem(i, j, QtWidgets.QTableWidgetItem(v))
        self.table_seismic_levels.resizeColumnsToContents()

        self.lbl_seismic_params.setText(
            f"Ca={seis.ca:.4f}  Cv={seis.cv:.4f}  R={seis.r:.2f}  Ip={seis.ip:.2f}  "
            f"PLRF={seis.plrf:.3f}  Ws={seis.ws:.2f} kN  Cs={seis.cs:.5f}  "
            f"V (cortante basal) = {seis.v_base:.3f} kN"
        )

    # ------------------------------------------------------------------
    # Momento / Axial / Cortante — un método genérico para las 3 pestañas.
    # ------------------------------------------------------------------
    def _get_force_rows(self, el_pattern: str):
        if el_pattern not in self._force_rows_cache:
            self._force_rows_cache[el_pattern] = element_forces_table(
                self.model, self.result, self.inputs, el_pattern=el_pattern,
            )
        return self._force_rows_cache[el_pattern]

    def _build_force_tab(self, quantity: str) -> None:
        tab = QtWidgets.QWidget()
        icon = {"M3": "📐", "P": "📊", "V2": "✂️"}.get(quantity, "📈")
        self.tabs.addTab(tab, f"{icon} {_QUANTITY_LABELS[quantity]}")
        layout = QtWidgets.QVBoxLayout(tab)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("Sismo aplicado en dirección:"))
        cb_pattern = QtWidgets.QComboBox()
        cb_pattern.addItem("EL_X (longitudinal, flexión en el plano del marco)", "EL_X")
        cb_pattern.addItem("EL_Y (transversal)", "EL_Y")
        controls.addWidget(cb_pattern)

        controls.addWidget(QtWidgets.QLabel("Combinación:"))
        cb_combo = QtWidgets.QComboBox()
        controls.addWidget(cb_combo)
        controls.addStretch(1)
        layout.addLayout(controls)

        image_tab = _ImageTab(f"diagrama_{quantity.lower()}.png")
        layout.addWidget(image_tab, 1)

        def refresh_combo_list():
            cb_combo.blockSignals(True)
            cb_combo.clear()
            el_pattern = cb_pattern.currentData()
            rows = self._get_force_rows(el_pattern)
            labels = []
            for r in rows:
                if r.output_case not in labels:
                    labels.append(r.output_case)
            for label in labels:
                cb_combo.addItem(label)
            # Por defecto: la combinación sísmica (contiene "EL"), igual
            # que el anexo de referencia (1.2DL+1.5EL+0.85PL).
            default_idx = next((i for i, l in enumerate(labels) if "EL" in l), 0)
            cb_combo.setCurrentIndex(default_idx)
            cb_combo.blockSignals(False)

        def render():
            el_pattern = cb_pattern.currentData()
            combo_label = cb_combo.currentText()
            rows = [r for r in self._get_force_rows(el_pattern) if r.output_case == combo_label]
            try:
                fig = plot_frame_force_diagram(
                    self.model, rows, quantity, combo_label=combo_label,
                )
                image_tab.set_figure(fig)
            except Exception as exc:
                image_tab.set_error(exc)

        def on_pattern_changed():
            refresh_combo_list()
            render()

        cb_pattern.currentIndexChanged.connect(on_pattern_changed)
        cb_combo.currentIndexChanged.connect(render)

        refresh_combo_list()
        render()

    # ------------------------------------------------------------------
    # Especificaciones de secciones de parales ("Member Check").
    # ------------------------------------------------------------------
    def _build_spec_tab(self) -> None:
        tab = QtWidgets.QWidget()
        self.tabs.addTab(tab, "📋 Especificaciones de parales")
        layout = QtWidgets.QVBoxLayout(tab)

        self.txt_spec = QtWidgets.QTextBrowser()
        self.txt_spec.setFont(QtGui.QFont("Courier New", 10))
        self.txt_spec.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        try:
            report = upright_section_report(self.model, self.result, self.inputs)
        except Exception as exc:
            report = f"No se pudo generar el reporte de especificaciones:\n{exc}"
        self.txt_spec.setPlainText(report)
        layout.addWidget(self.txt_spec, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_save = QtWidgets.QPushButton("💾  Guardar reporte (.txt)")
        btn_save.clicked.connect(lambda: self._save_spec_report(report))
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    def _save_spec_report(self, report: str) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar especificaciones de parales",
            "especificaciones_parales.txt", "Texto (*.txt)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            QtWidgets.QMessageBox.information(self, "Vortex", f"Reporte exportado a:\n{path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Vortex", f"Error al exportar:\n{exc}")
