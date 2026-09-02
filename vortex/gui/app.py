"""
Aplicación de escritorio Vortex: interfaz visual para modelar, analizar y
verificar estanterías industriales de acero según NTC 5689, con un flujo
de trabajo similar al de Autodesk Inventor (modelado paramétrico, barra de
herramientas superior, panel de propiedades) y SAP2000 (análisis matricial
+ resultados coloreados por elemento).
"""
from __future__ import annotations

import datetime
import os
import sys
import traceback
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..geometry import (
    RackParameters, build_selective_rack,
    brace_levels_per_panel_for_angle, brace_levels_per_panel_for_count,
    resulting_brace_angle_deg, brace_panel_count,
)
from ..geometry.model import RackModel, SectionKind
from ..sections.catalog import default_catalog
from ..loads.seismic import AA_AV_BY_CITY
from ..analysis import (
    PipelineInputs, PipelineResult, SeismicInputs, run_full_check,
    element_forces_table, write_element_forces_csv,
)
from ..report import ProjectInfo, ReportData, generate_memoria
from ..units import kgf_to_kn
from ..ai import AdvisorError, DEFAULT_MODEL, AVAILABLE_MODELS, build_results_summary, get_recommendations
from .viewer3d import Viewer3D
from .legend import ColorLegend
from .theme import apply_dark_theme

SOIL_TYPES = ["A", "B", "C", "D", "E"]
BRACE_ANGLES_DEG = [30, 45, 60, 65, 70, 75]


class _AdvisorWorker(QtCore.QObject):
    """Ejecuta la llamada (bloqueante, por red) a la API de Groq en un
    hilo aparte para no congelar la interfaz."""

    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, summary: str, api_key: str, model: str):
        super().__init__()
        self._summary = summary
        self._api_key = api_key
        self._model = model

    def run(self) -> None:
        try:
            text = get_recommendations(self._summary, self._api_key, self._model)
            self.finished.emit(text)
        except AdvisorError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # defensivo: nunca dejar el hilo morir en silencio
            self.failed.emit(f"Error inesperado consultando la IA: {exc}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vortex — Cálculo y modelado de estanterías industriales (NTC 5689)")
        self.resize(1500, 940)

        self.catalog = default_catalog()
        self.model: Optional[RackModel] = None
        self.pipeline_result: Optional[PipelineResult] = None
        self.last_inputs: Optional[PipelineInputs] = None
        self._ai_thread: Optional[QtCore.QThread] = None
        self._ai_worker: Optional[_AdvisorWorker] = None

        self._build_toolbar()
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        toolbar = QtWidgets.QToolBar("Principal")
        toolbar.setMovable(False)
        toolbar.setIconSize(QtCore.QSize(1, 1))  # sin íconos gráficos, solo texto/emoji
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)

        act_build = QtGui.QAction("🧱  1. Construir modelo", self)
        act_build.triggered.connect(self.on_build_model)
        toolbar.addAction(act_build)

        act_analyze = QtGui.QAction("📊  2. Analizar y verificar", self)
        act_analyze.triggered.connect(self.on_analyze)
        toolbar.addAction(act_analyze)

        toolbar.addSeparator()

        act_export = QtGui.QAction("📄  Memoria de cálculo (.docx)", self)
        act_export.triggered.connect(self.on_export)
        toolbar.addAction(act_export)

        act_export_forces = QtGui.QAction("📈  Fuerzas por elemento (.csv)", self)
        act_export_forces.triggered.connect(self.on_export_element_forces)
        toolbar.addAction(act_export_forces)

        toolbar.addSeparator()

        act_ai = QtGui.QAction("🤖  Recomendaciones IA", self)
        act_ai.triggered.connect(self._on_ai_toolbar_clicked)
        toolbar.addAction(act_ai)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        self.form_panel = self._build_form_panel()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_panel)
        scroll.setFixedWidth(392)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        main_layout.addWidget(scroll, 0)

        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        viewer_container = QtWidgets.QWidget()
        viewer_layout = QtWidgets.QVBoxLayout(viewer_container)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        self.viewer = Viewer3D()
        viewer_layout.addWidget(self.viewer, 1)

        legend_row = QtWidgets.QHBoxLayout()
        self.cb_color_by = QtWidgets.QComboBox()
        self.cb_color_by.addItems([
            "Colorear por: ratio de utilización",
            "Colorear por: concentración de esfuerzos (fuerza relativa)",
        ])
        self.cb_color_by.currentIndexChanged.connect(self._on_color_by_changed)
        self.cb_color_by.setEnabled(False)
        legend_row.addWidget(self.cb_color_by)
        self.legend = ColorLegend()
        legend_row.addWidget(self.legend, 1)
        viewer_layout.addLayout(legend_row)

        diagram_row = QtWidgets.QHBoxLayout()
        self.chk_show_diagram = QtWidgets.QCheckBox("Líneas de fuerzas")
        self.chk_show_diagram.setEnabled(False)
        self.chk_show_diagram.toggled.connect(self._on_diagram_changed)
        diagram_row.addWidget(self.chk_show_diagram)
        self.cb_diagram_pattern = QtWidgets.QComboBox()
        self.cb_diagram_pattern.addItems(["DL", "PL", "EL_X", "EL_Y"])
        self.cb_diagram_pattern.currentIndexChanged.connect(self._on_diagram_changed)
        diagram_row.addWidget(self.cb_diagram_pattern)
        self.cb_diagram_component = QtWidgets.QComboBox()
        self.cb_diagram_component.addItems(["P", "M2", "M3", "V2", "V3"])
        self.cb_diagram_component.setCurrentText("M2")
        self.cb_diagram_component.currentIndexChanged.connect(self._on_diagram_changed)
        diagram_row.addWidget(self.cb_diagram_component)
        self.sp_diagram_scale = QtWidgets.QDoubleSpinBox()
        self.sp_diagram_scale.setRange(0.1, 10.0)
        self.sp_diagram_scale.setSingleStep(0.1)
        self.sp_diagram_scale.setValue(1.0)
        self.sp_diagram_scale.valueChanged.connect(self._on_diagram_changed)
        diagram_row.addWidget(QtWidgets.QLabel("Escala"))
        diagram_row.addWidget(self.sp_diagram_scale)
        diagram_row.addStretch(1)
        viewer_layout.addLayout(diagram_row)

        right.addWidget(viewer_container)

        self.tabs = QtWidgets.QTabWidget()

        self.results_table = QtWidgets.QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(
            ["Elemento", "Tipo", "Combinación crítica", "Detalle", "Ratio"]
        )
        self.results_table.setAlternatingRowColors(True)
        self.results_table.horizontalHeader().setStretchLastSection(False)
        self.results_table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.Stretch
        )
        self.tabs.addTab(self.results_table, "📋 Resultados")

        self.summary_panel = self._build_summary_panel()
        self.tabs.addTab(self.summary_panel, "📊 Cargas y sismo")

        self.ai_panel = self._build_ai_panel()
        self.tabs.addTab(self.ai_panel, "🤖 Recomendaciones IA")

        right.addWidget(self.tabs)
        right.setSizes([650, 250])
        main_layout.addWidget(right, 1)

        self.status = self.statusBar()
        self.status.showMessage("Defina la geometría y presione \"Construir modelo\".")

    def _build_summary_panel(self) -> QtWidgets.QWidget:
        """
        Resumen de cargas y sismo, estilo la hoja "1.Datos_Entrada" /
        "2.Cargas_Sismo" de la memoria de cálculo en Excel de referencia:
        carga de producto y peso propio (total y por nivel), coeficientes
        sísmicos Ca/Cv/Cs y cortante basal V por dirección, y la
        distribución vertical de fuerzas Fx por nivel. Se calcula con el
        mismo motor ya validado (`vortex.loads.seismic`); este panel sólo
        hace visibles esos resultados intermedios, no repite el cálculo.
        """
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        loads_box = QtWidgets.QGroupBox("⬇ Cargas (calculado)")
        loads_form = QtWidgets.QFormLayout(loads_box)
        self.lbl_sum_pl_level = QtWidgets.QLabel("—")
        self.lbl_sum_pl_total = QtWidgets.QLabel("—")
        self.lbl_sum_dl_level = QtWidgets.QLabel("—")
        self.lbl_sum_dl_total = QtWidgets.QLabel("—")
        loads_form.addRow("Carga de producto (PL) por nivel-bahía", self.lbl_sum_pl_level)
        loads_form.addRow("Carga de producto (PL) total", self.lbl_sum_pl_total)
        loads_form.addRow("Peso propio (DL) tributario por nivel", self.lbl_sum_dl_level)
        loads_form.addRow("Peso propio (DL) total (modelo 3D real)", self.lbl_sum_dl_total)
        layout.addWidget(loads_box)

        seis_row = QtWidgets.QHBoxLayout()
        self.seis_trans_box, self._seis_trans_labels = self._build_seismic_summary_box(
            "〰 Sismo transversal (marcos, R=4)"
        )
        self.seis_long_box, self._seis_long_labels = self._build_seismic_summary_box(
            "〰 Sismo longitudinal (vigas, R=6)"
        )
        seis_row.addWidget(self.seis_trans_box)
        seis_row.addWidget(self.seis_long_box)
        layout.addLayout(seis_row)

        dist_box = QtWidgets.QGroupBox("📶 Distribución de fuerzas horizontales por nivel")
        dist_layout = QtWidgets.QVBoxLayout(dist_box)
        self.table_fx = QtWidgets.QTableWidget(0, 4)
        self.table_fx.setHorizontalHeaderLabels(
            ["Nivel", "Altura desde piso (m)", "Fx transversal (kN)", "Fx longitudinal (kN)"]
        )
        self.table_fx.horizontalHeader().setStretchLastSection(True)
        dist_layout.addWidget(self.table_fx)
        layout.addWidget(dist_box, 1)

        placeholder = QtWidgets.QLabel(
            "Ejecute \"Analizar y verificar\" para calcular este resumen."
        )
        placeholder.setStyleSheet("color: #96a1ad;")
        layout.addWidget(placeholder)
        self._lbl_summary_placeholder = placeholder

        return panel

    def _build_seismic_summary_box(self, title: str):
        box = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(box)
        labels = {}
        for key, caption in (
            ("ca", "Ca"), ("cv", "Cv"), ("r", "R"), ("ip", "Ip"),
            ("plrf", "PLRF"), ("ws", "Ws (peso sísmico efectivo)"),
            ("cs", "Cs"), ("v", "V (cortante basal)"),
        ):
            lbl = QtWidgets.QLabel("—")
            form.addRow(caption, lbl)
            labels[key] = lbl
        return box, labels

    def _populate_summary_panel(self) -> None:
        result = self.pipeline_result
        model = self.model
        if result is None or model is None:
            return
        self._lbl_summary_placeholder.setVisible(False)

        self.lbl_sum_pl_level.setText(f"{self.sp_pl.value():.2f} kgf  ({kgf_to_kn(self.sp_pl.value()):.3f} kN)")
        self.lbl_sum_pl_total.setText(f"{result.pl_total_kn:.2f} kN")
        self.lbl_sum_dl_level.setText(f"{result.dl_per_level_kn:.3f} kN")
        self.lbl_sum_dl_total.setText(f"{result.dl_total_kn:.3f} kN")

        for seis, labels in (
            (result.seismic_transversal, self._seis_trans_labels),
            (result.seismic_longitudinal, self._seis_long_labels),
        ):
            labels["ca"].setText(f"{seis.ca:.4f}")
            labels["cv"].setText(f"{seis.cv:.4f}")
            labels["r"].setText(f"{seis.r:.2f}")
            labels["ip"].setText(f"{seis.ip:.2f}")
            labels["plrf"].setText(f"{seis.plrf:.3f}")
            labels["ws"].setText(f"{seis.ws:.2f} kN")
            labels["cs"].setText(f"{seis.cs:.5f}")
            labels["v"].setText(f"{seis.v_base:.2f} kN")

        levels = sorted(result.seismic_transversal.fx_by_level.keys())
        self.table_fx.setRowCount(len(levels))
        for i, lv in enumerate(levels):
            elevation = model.level_elevations[lv]
            fx_t = result.seismic_transversal.fx_by_level.get(lv, 0.0)
            fx_l = result.seismic_longitudinal.fx_by_level.get(lv, 0.0)
            vals = [str(lv), f"{elevation:.2f}", f"{fx_t:.3f}", f"{fx_l:.3f}"]
            for j, v in enumerate(vals):
                self.table_fx.setItem(i, j, QtWidgets.QTableWidgetItem(v))
        self.table_fx.resizeColumnsToContents()

    def _build_ai_panel(self) -> QtWidgets.QWidget:
        """
        Panel de recomendaciones de IA: envía un resumen numérico del
        chequeo estructural (parales/vigas más críticos, parámetros
        sísmicos, elementos que no cumplen) a un modelo LLM vía la API de
        Groq (https://console.groq.com), y muestra su respuesta como
        apoyo de lectura rápida — no reemplaza el criterio del calculista
        ni las verificaciones normativas ya hechas por Vortex.
        """
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        cfg_box = QtWidgets.QGroupBox("Configuración (Groq)")
        cfg_form = QtWidgets.QFormLayout(cfg_box)
        self.ed_groq_key = QtWidgets.QLineEdit()
        self.ed_groq_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_groq_key.setPlaceholderText("gsk_...")
        self.ed_groq_key.setText(os.environ.get("GROQ_API_KEY", ""))
        self.cb_ai_model = QtWidgets.QComboBox()
        self.cb_ai_model.setEditable(True)
        self.cb_ai_model.addItems(AVAILABLE_MODELS)
        self.cb_ai_model.setCurrentText(DEFAULT_MODEL)
        cfg_form.addRow("API key", self.ed_groq_key)
        cfg_form.addRow("Modelo", self.cb_ai_model)
        hint = QtWidgets.QLabel(
            "Obtén una API key gratuita en console.groq.com/keys. Requiere "
            "conexión a internet; la clave sólo se usa localmente para "
            "llamar a la API de Groq, nunca se guarda en la memoria de cálculo."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #96a1ad; font-size: 10px;")
        cfg_form.addRow(hint)
        layout.addWidget(cfg_box)

        self.btn_ai_recommend = QtWidgets.QPushButton("🤖  Analizar resultados con IA")
        self.btn_ai_recommend.setObjectName("primaryAction")
        self.btn_ai_recommend.clicked.connect(self.on_ai_recommend)
        layout.addWidget(self.btn_ai_recommend)

        self.txt_ai_output = QtWidgets.QTextBrowser()
        self.txt_ai_output.setPlaceholderText(
            "Ejecute \"Analizar y verificar\" y luego presione "
            "\"Analizar resultados con IA\" para obtener recomendaciones "
            "de ingeniería sobre los elementos más críticos."
        )
        layout.addWidget(self.txt_ai_output, 1)
        return panel

    def _build_form_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        geo_box = QtWidgets.QGroupBox("📐 Geometría")
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
        for sp in (self.sp_depth, self.sp_n_levels, self.sp_h_first, self.sp_h_rest):
            sp.valueChanged.connect(self._update_brace_preview)

        sec_box = QtWidgets.QGroupBox("🔩 Secciones")
        sec_form = QtWidgets.QFormLayout(sec_box)
        self.cb_upright = QtWidgets.QComboBox()
        self.cb_beam = QtWidgets.QComboBox()
        self.cb_brace = QtWidgets.QComboBox()
        for name, sec in self.catalog.items():
            if sec.kind in (SectionKind.CFS_UPRIGHT, SectionKind.HR_UPRIGHT):
                self.cb_upright.addItem(name)
            elif sec.kind == SectionKind.BEAM_BOX:
                self.cb_beam.addItem(name)
            elif sec.kind == SectionKind.BRACE_ANGLE:
                self.cb_brace.addItem(name)
        sec_form.addRow("Paral", self.cb_upright)
        sec_form.addRow("Viga", self.cb_beam)
        sec_form.addRow("Diagonal", self.cb_brace)
        layout.addWidget(sec_box)

        brace_box = QtWidgets.QGroupBox("╱ Arriostramiento del marco (riostras)")
        brace_form = QtWidgets.QFormLayout(brace_box)
        self.cb_brace_angle = QtWidgets.QComboBox()
        self.cb_brace_angle.addItem("Automático (según cantidad)", None)
        for a in BRACE_ANGLES_DEG:
            self.cb_brace_angle.addItem(f"{a}°", float(a))
        self.cb_brace_angle.setCurrentIndex(BRACE_ANGLES_DEG.index(70) + 1)  # 70° por defecto
        self.sp_brace_count = QtWidgets.QSpinBox()
        self.sp_brace_count.setRange(1, 200)
        self.lbl_brace_info = QtWidgets.QLabel("—")
        self.lbl_brace_info.setStyleSheet("color: #96a1ad; font-size: 10px;")
        self.lbl_brace_info.setWordWrap(True)
        brace_form.addRow("Ángulo objetivo", self.cb_brace_angle)
        brace_form.addRow("Cantidad de diagonales", self.sp_brace_count)
        brace_form.addRow(self.lbl_brace_info)
        layout.addWidget(brace_box)

        self._brace_source = "angle"  # "angle" | "count" — cuál control mandó por última vez
        self.cb_brace_angle.currentIndexChanged.connect(self._on_brace_angle_changed)
        self.sp_brace_count.valueChanged.connect(self._on_brace_count_changed)

        load_box = QtWidgets.QGroupBox("⬇ Cargas")
        load_form = QtWidgets.QFormLayout(load_box)
        self.sp_pl = QtWidgets.QDoubleSpinBox(); self.sp_pl.setRange(0, 100000)
        self.sp_pl.setValue(2400.0); self.sp_pl.setSuffix(" kgf / nivel-bahía")
        self.sp_ll = QtWidgets.QDoubleSpinBox(); self.sp_ll.setRange(0, 50)
        self.sp_ll.setValue(0.0); self.sp_ll.setSuffix(" kN/m²")
        load_form.addRow("Carga de producto (PL)", self.sp_pl)
        load_form.addRow("Carga viva (LL)", self.sp_ll)
        layout.addWidget(load_box)

        seis_box = QtWidgets.QGroupBox("〰 Sismo — NTC 5689 numeral 2.7")
        seis_form = QtWidgets.QFormLayout(seis_box)
        self.cb_city = QtWidgets.QComboBox()
        self.cb_city.addItems(sorted(AA_AV_BY_CITY.keys()))
        self.cb_city.setCurrentText("Medellín")
        self.cb_city.currentTextChanged.connect(self._on_city_changed)
        self.sp_aa = QtWidgets.QDoubleSpinBox(); self.sp_aa.setRange(0, 0.6); self.sp_aa.setDecimals(3)
        self.sp_av = QtWidgets.QDoubleSpinBox(); self.sp_av.setRange(0, 0.6); self.sp_av.setDecimals(3)
        self.cb_soil = QtWidgets.QComboBox(); self.cb_soil.addItems(SOIL_TYPES); self.cb_soil.setCurrentText("D")
        self.chk_el_relaxed = QtWidgets.QCheckBox("Factor EL=1.0 (relajación NTC 5689 num. 2.2)")
        self.chk_el_relaxed.setChecked(True)
        self.chk_el_relaxed.setToolTip(
            "Desmarcar para usar EL=1.5 sin relajar (combinación literal "
            "1.2DL+1.5EL+0.85PL), como en el proyecto de referencia."
        )
        seis_form.addRow("Ciudad (NSR-10)", self.cb_city)
        seis_form.addRow("Aa", self.sp_aa)
        seis_form.addRow("Av", self.sp_av)
        seis_form.addRow("Tipo de perfil de suelo", self.cb_soil)
        seis_form.addRow(self.chk_el_relaxed)
        layout.addWidget(seis_box)
        self._on_city_changed(self.cb_city.currentText())

        layout.addStretch(1)

        disclaimer = QtWidgets.QLabel(
            "Este software es una herramienta de apoyo al cálculo. Todo "
            "diseño debe ser revisado y firmado por un ingeniero calculista "
            "responsable antes de su uso para fabricación o construcción."
        )
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet("color: #96a1ad; font-size: 10px;")
        layout.addWidget(disclaimer)

        self._update_brace_preview()
        return panel

    def _on_city_changed(self, city: str) -> None:
        data = AA_AV_BY_CITY.get(city)
        if data:
            self.sp_aa.setValue(float(data["Aa"]))
            self.sp_av.setValue(float(data["Av"]))

    def _current_level_heights(self) -> list:
        n_levels = self.sp_n_levels.value()
        return [self.sp_h_first.value()] + [self.sp_h_rest.value()] * (n_levels - 1)

    def _on_brace_angle_changed(self) -> None:
        self._brace_source = "angle"
        self._update_brace_preview()

    def _on_brace_count_changed(self) -> None:
        self._brace_source = "count"
        self._update_brace_preview()

    def _brace_levels_per_panel(self) -> int:
        heights = self._current_level_heights()
        depth = self.sp_depth.value()
        angle = self.cb_brace_angle.currentData()
        if self._brace_source == "angle" and angle is not None:
            return brace_levels_per_panel_for_angle(angle, depth, heights)
        return brace_levels_per_panel_for_count(self.sp_brace_count.value(), len(heights))

    def _update_brace_preview(self) -> None:
        heights = self._current_level_heights()
        depth = self.sp_depth.value()
        lpp = self._brace_levels_per_panel()
        n_panels = brace_panel_count(len(heights), lpp)
        real_angle = resulting_brace_angle_deg(depth, heights, lpp)

        self.sp_brace_count.blockSignals(True)
        self.sp_brace_count.setMaximum(max(1, len(heights)))
        self.sp_brace_count.setValue(n_panels)
        self.sp_brace_count.blockSignals(False)

        text = (
            f"{n_panels} diagonal(es) por marco · {lpp} nivel(es) por panel · "
            f"ángulo real ≈ {real_angle:.0f}°"
        )
        target_angle = self.cb_brace_angle.currentData()
        if self._brace_source == "angle" and target_angle is not None and abs(real_angle - target_angle) > 10:
            text += (
                f"\n⚠ el ángulo objetivo ({target_angle:.0f}°) no es alcanzable con la "
                f"altura de nivel actual sin subdividir el paral (no soportado); se "
                f"usa el panel más cercano posible."
            )
        self.lbl_brace_info.setText(text)

    # ------------------------------------------------------------------
    def _current_params(self) -> RackParameters:
        heights = self._current_level_heights()
        return RackParameters(
            n_bays=self.sp_bays.value(),
            bay_length=self.sp_bay_length.value(),
            frame_depth=self.sp_depth.value(),
            level_heights=heights,
            upright_section=self.catalog[self.cb_upright.currentText()],
            beam_section=self.catalog[self.cb_beam.currentText()],
            brace_section=self.catalog[self.cb_brace.currentText()],
            base_fixity=self.cb_base.currentText(),
            brace_levels_per_panel=self._brace_levels_per_panel(),
        )

    def on_build_model(self) -> None:
        try:
            params = self._current_params()
            self.model = build_selective_rack(params)
            self.pipeline_result = None
            self.viewer.show_model(self.model)
            self.cb_color_by.setEnabled(False)
            self.chk_show_diagram.setChecked(False)
            self.chk_show_diagram.setEnabled(False)
            self.results_table.setRowCount(0)
            self.table_fx.setRowCount(0)
            self._lbl_summary_placeholder.setVisible(True)
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
                apply_el_factor_10=self.chk_el_relaxed.isChecked(),
            )
            self.last_inputs = inputs
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            self.pipeline_result = run_full_check(self.model, inputs)
            QtWidgets.QApplication.restoreOverrideCursor()

            self.cb_color_by.setEnabled(True)
            self.chk_show_diagram.setEnabled(True)
            self._apply_coloring()
            self._on_diagram_changed()
            self._populate_results_table()
            self._populate_summary_panel()
            self.tabs.setCurrentWidget(self.results_table)
            n_fail = sum(1 for r in self.pipeline_result.member_rows.values() if r.ratio > 1.0)
            self.status.showMessage(
                f"Análisis completo. Ratio máximo: {self.pipeline_result.max_ratio():.2f}. "
                f"{n_fail} elemento(s) no cumplen."
            )
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._show_error("Error durante el análisis", exc)

    def _on_color_by_changed(self) -> None:
        if self.pipeline_result is not None:
            self._apply_coloring()

    def _on_diagram_changed(self) -> None:
        if self.pipeline_result is None:
            return
        if not self.chk_show_diagram.isChecked():
            self.viewer.clear_force_diagram()
            return
        pattern = self.cb_diagram_pattern.currentText()
        component = self.cb_diagram_component.currentText()
        analysis = self.pipeline_result.patterns.get(pattern)
        if analysis is None:
            return
        self.viewer.show_force_diagram(
            self.model, analysis, component, scale=self.sp_diagram_scale.value(),
        )

    def _apply_coloring(self) -> None:
        if self.pipeline_result is None:
            return
        if self.cb_color_by.currentIndex() == 1:
            forces = {mid: row.raw_force for mid, row in self.pipeline_result.member_rows.items()}
            self.viewer.color_by_heat(forces)
            self.legend.set_heat_scale("parales: P (kN) · vigas: M (kN·m)")
        else:
            ratios = {mid: row.ratio for mid, row in self.pipeline_result.member_rows.items()}
            self.viewer.color_by_ratio(ratios)
            self.legend.set_ratio_scale()

    def _populate_results_table(self) -> None:
        rows = sorted(self.pipeline_result.member_rows.values(), key=lambda r: -r.ratio)
        self.results_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            vals = [row.label, row.kind, row.combo, row.detail, f"{row.ratio:.2f}"]
            for j, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(v)
                if j == 4:
                    if row.ratio > 1.0:
                        item.setBackground(QtGui.QColor("#5c2622"))
                        item.setForeground(QtGui.QColor("#ff8a80"))
                    elif row.ratio > 0.9:
                        item.setBackground(QtGui.QColor("#5c4d1a"))
                        item.setForeground(QtGui.QColor("#ffe08a"))
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
                member_rows_detail=list(self.pipeline_result.member_rows.values()),
            )
            generate_memoria(data, path)
            self.status.showMessage(f"Memoria de cálculo exportada: {path}")
            QtWidgets.QMessageBox.information(self, "Vortex", f"Memoria exportada a:\n{path}")
        except Exception as exc:
            self._show_error("Error al exportar la memoria", exc)

    def on_export_element_forces(self) -> None:
        if self.model is None or self.pipeline_result is None or self.last_inputs is None:
            QtWidgets.QMessageBox.warning(
                self, "Vortex", "Primero construya el modelo y ejecute el análisis."
            )
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar tabla de fuerzas por elemento", "element_forces.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            rows = element_forces_table(self.model, self.pipeline_result, self.last_inputs, el_pattern="EL_X")
            write_element_forces_csv(rows, path)
            self.status.showMessage(f"Tabla de fuerzas exportada: {path}")
            QtWidgets.QMessageBox.information(
                self, "Vortex",
                f"Tabla de fuerzas por elemento exportada a:\n{path}\n\n"
                f"{len(rows)} filas (Frame, OutputCase, P, M3, V2, M2, V3, por estación) "
                f"listas para comparar contra una tabla 'Element Forces - Frames' de SAP2000."
            )
        except Exception as exc:
            self._show_error("Error al exportar la tabla de fuerzas", exc)

    # -------------------------- IA (Groq) ------------------------------
    def _on_ai_toolbar_clicked(self) -> None:
        self.tabs.setCurrentWidget(self.ai_panel)
        if self.pipeline_result is not None:
            self.on_ai_recommend()

    def on_ai_recommend(self) -> None:
        if self.model is None or self.pipeline_result is None or self.last_inputs is None:
            QtWidgets.QMessageBox.warning(
                self, "Vortex", "Primero construya el modelo y ejecute el análisis."
            )
            return
        if self._ai_thread is not None and self._ai_thread.isRunning():
            return  # ya hay una consulta en curso

        api_key = self.ed_groq_key.text().strip() or os.environ.get("GROQ_API_KEY", "")
        model = self.cb_ai_model.currentText().strip() or DEFAULT_MODEL
        summary = build_results_summary(self.model, self.pipeline_result, self.last_inputs)

        self.txt_ai_output.setPlainText("Consultando IA (Groq)...")
        self.btn_ai_recommend.setEnabled(False)

        self._ai_thread = QtCore.QThread(self)
        self._ai_worker = _AdvisorWorker(summary, api_key, model)
        self._ai_worker.moveToThread(self._ai_thread)
        self._ai_thread.started.connect(self._ai_worker.run)
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_worker.failed.connect(self._on_ai_failed)
        self._ai_worker.finished.connect(self._ai_thread.quit)
        self._ai_worker.failed.connect(self._ai_thread.quit)
        self._ai_thread.finished.connect(self._ai_thread.deleteLater)
        self._ai_thread.start()

    def _on_ai_finished(self, text: str) -> None:
        self.txt_ai_output.setPlainText(text)
        self.btn_ai_recommend.setEnabled(True)
        self.status.showMessage("Recomendaciones de IA recibidas.")

    def _on_ai_failed(self, msg: str) -> None:
        self.txt_ai_output.setPlainText(f"⚠ {msg}")
        self.btn_ai_recommend.setEnabled(True)
        self.status.showMessage("La consulta a la IA falló. Ver detalle en el panel.")

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
    apply_dark_theme(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
