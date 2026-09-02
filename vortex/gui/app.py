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
from typing import List, Optional

import requests
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
from .viewer3d import Viewer3D
from .legend import ColorLegend
from .theme import apply_dark_theme

SOIL_TYPES = ["A", "B", "C", "D", "E"]
BRACE_ANGLES_DEG = [30, 45, 60, 65, 70, 75]

# =====================================================================
# Recomendaciones IA (Groq) — TODO lo relacionado con Groq vive aquí, en
# este mismo archivo, a propósito: para configurar tu API key basta con
# editar la línea de abajo, sin buscar en otro archivo ni módulo.
#
#   GROQ_API_KEY = "gsk_TU_LLAVE_AQUI"
#
# Consigue una API key gratuita en https://console.groq.com/keys. Este
# archivo (vortex/gui/app.py) es parte del código fuente normal del
# proyecto: si compartes o subes este repositorio a un lugar público,
# recuerda quitar tu key antes (o usar la variable de entorno
# GROQ_API_KEY en su lugar, que tiene prioridad sobre esta constante y
# nunca queda escrita en ningún archivo).
# =====================================================================
GROQ_API_KEY = ""

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# El modelo lo elige el sistema, no el usuario: se prueban estos modelos
# de chat de Groq en orden y se usa el primero que responda. Se dejaron
# de listar los modelos vía la API de Groq (GET /models) porque esa lista
# incluye TODOS los modelos de la cuenta -- también los que no son de
# chat (voz, transcripción, moderación, etc.) -- y el selector podía
# terminar eligiendo uno de esos por error (p.ej. un modelo de
# texto-a-voz), lo cual nunca iba a funcionar para esto. Esta lista fija
# sólo tiene modelos de chat conocidos; si Groq retira uno, el sistema
# simplemente prueba el siguiente sin que el usuario tenga que hacer nada.
CANDIDATE_MODELS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "gemma2-9b-it",
]

SYSTEM_PROMPT = (
    "Eres un ingeniero estructural senior, experto en diseño de estanterías "
    "industriales de acero (racks selectivos) según NSR-10 Título F (perfiles "
    "conformados en frío, AISI S100), NTC 5689:2009 y RMI ANSI MH16.1. "
    "Recibes un resumen numérico de un chequeo estructural generado por el "
    "software Vortex (análisis matricial 3D + verificación miembro por "
    "miembro). Da recomendaciones de ingeniería concretas y priorizadas por "
    "severidad, en español, dirigidas a un calculista que va a firmar la "
    "memoria de cálculo. Sé específico: nombra el elemento, la relación "
    "demanda/capacidad, y una acción concreta (cambiar sección, revisar "
    "arriostramiento, verificar un dato de entrada, etc.). No inventes "
    "valores que no estén en el resumen. Si algo luce como un posible error "
    "de datos de entrada (por ejemplo Aa=0 en una ciudad de amenaza sísmica "
    "alta), dilo explícitamente. Responde en viñetas, máximo ~300 palabras."
)


class AdvisorError(RuntimeError):
    """Error al construir el resumen o al consultar la API de Groq."""


def _resolve_groq_api_key() -> str:
    """Variable de entorno GROQ_API_KEY (si está definida) tiene
    prioridad; si no, la constante GROQ_API_KEY editada arriba en este
    mismo archivo."""
    return os.environ.get("GROQ_API_KEY", "").strip() or GROQ_API_KEY.strip()


def build_results_summary(
    model: RackModel, result: PipelineResult, inputs: PipelineInputs,
    n_worst: int = 10,
) -> str:
    """
    Resumen textual compacto (no exhaustivo) del modelo y de los resultados
    del análisis, pensado para caber en el contexto de un LLM sin exponer
    la tabla completa de elementos.
    """
    rows = sorted(result.member_rows.values(), key=lambda r: -r.ratio)
    n_fail = sum(1 for r in rows if r.ratio > 1.0)
    worst = rows[:n_worst]

    lines = [
        f"Modelo: {len(model.nodes)} nudos, {len(model.members)} elementos.",
        (
            f"Sismo transversal: Aa={inputs.seismic.aa}, Av={inputs.seismic.av}, "
            f"suelo={inputs.seismic.soil_type}, Cs={result.seismic_transversal.cs:.4f}, "
            f"V={result.seismic_transversal.v_base:.2f} kN."
        ),
        (
            f"Sismo longitudinal: Cs={result.seismic_longitudinal.cs:.4f}, "
            f"V={result.seismic_longitudinal.v_base:.2f} kN."
        ),
        (
            f"Carga de producto: {inputs.pl_per_level_kn:.2f} kN/nivel-bahía. "
            f"Carga viva: {inputs.ll_kn_m2:.2f} kN/m²."
        ),
        f"Elementos verificados: {len(rows)}. No cumplen (ratio > 1.0): {n_fail}.",
        f"Los {len(worst)} elementos más críticos (ratio de utilización descendente):",
    ]
    for r in worst:
        lines.append(f"  - {r.label} ({r.kind}), combo {r.combo}: ratio={r.ratio:.2f}, {r.detail}")
    return "\n".join(lines)


def _groq_error_code(resp: "requests.Response") -> str:
    try:
        return str(resp.json().get("error", {}).get("code", ""))
    except (ValueError, AttributeError):
        return ""


def get_recommendations(
    summary: str, api_key: str, model: str, timeout: float = 30.0,
) -> str:
    """Envía `summary` a la API de Groq (Chat Completions) y devuelve el
    texto de la respuesta. Lanza `AdvisorError` con un mensaje claro ante
    cualquier falla (sin API key, red, HTTP, formato de respuesta)."""
    if not api_key:
        raise AdvisorError(
            "No se configuró una API key de Groq. Consigue una gratis en "
            "https://console.groq.com/keys y pégala en la constante "
            "GROQ_API_KEY al inicio de vortex/gui/app.py (o defínela como "
            "variable de entorno GROQ_API_KEY)."
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": summary},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise AdvisorError(f"Error de red al contactar Groq: {exc}") from exc

    if resp.status_code == 401:
        raise AdvisorError("API key de Groq inválida o expirada (HTTP 401).")
    if resp.status_code == 429:
        raise AdvisorError(
            "Límite de tasa de la API de Groq alcanzado (HTTP 429). Intente de nuevo en unos segundos."
        )
    if resp.status_code == 404:
        code = _groq_error_code(resp)
        if code == "model_not_found":
            raise AdvisorError(f"model_not_found: el modelo '{model}' no está disponible.")
        raise AdvisorError(f"Groq respondió con error HTTP 404: {resp.text[:500]}")
    if resp.status_code >= 400:
        raise AdvisorError(f"Groq respondió con error HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise AdvisorError(f"Respuesta inesperada de Groq: {exc}") from exc


def get_recommendations_auto(
    summary: str, api_key: str, models: List[str] = CANDIDATE_MODELS, timeout: float = 30.0,
) -> str:
    """
    Igual que `get_recommendations`, pero el modelo lo elige el sistema:
    prueba cada modelo de `models` en orden y devuelve la respuesta del
    primero que funcione — el usuario nunca tiene que elegir ni ver una
    lista de modelos. Sólo si NINGUNO de los modelos candidatos responde
    (por ejemplo, todos retirados de Groq) se lanza `AdvisorError` con el
    detalle del último intento.
    """
    if not api_key:
        raise AdvisorError(
            "No se configuró una API key de Groq. Consigue una gratis en "
            "https://console.groq.com/keys y pégala en la constante "
            "GROQ_API_KEY al inicio de vortex/gui/app.py (o defínela como "
            "variable de entorno GROQ_API_KEY)."
        )
    last_error: Optional[AdvisorError] = None
    for model in models:
        try:
            return get_recommendations(summary, api_key, model, timeout=timeout)
        except AdvisorError as exc:
            last_error = exc
            continue
    raise AdvisorError(
        f"Ningún modelo de la lista interna de Vortex (CANDIDATE_MODELS en "
        f"vortex/gui/app.py) respondió. Último error: {last_error}"
    )


class _AdvisorWorker(QtCore.QObject):
    """Ejecuta la llamada (bloqueante, por red) a la API de Groq en un
    hilo aparte para no congelar la interfaz. El modelo lo elige el
    sistema (ver `get_recommendations_auto`/`CANDIDATE_MODELS`) — nunca
    se le pide al usuario que elija uno."""

    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, summary: str, api_key: str):
        super().__init__()
        self._summary = summary
        self._api_key = api_key

    def run(self) -> None:
        try:
            text = get_recommendations_auto(self._summary, self._api_key)
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

        act_update = QtGui.QAction("🔄  Actualizar", self)
        act_update.setToolTip(
            "Reconstruye el modelo con los valores actuales del formulario y "
            "vuelve a analizar, en un solo clic — use esto después de cambiar "
            "cualquier dato (geometría, secciones, cargas, sismo); \"Analizar\" "
            "por sí solo NO reconstruye el modelo, así que un cambio de "
            "sección u otra geometría no se reflejaría en los resultados."
        )
        act_update.triggered.connect(self.on_update)
        toolbar.addAction(act_update)

        act_clear = QtGui.QAction("🗑  Borrar", self)
        act_clear.setToolTip(
            "Limpia el modelo, los resultados, el visor 3D y las "
            "recomendaciones de IA (los valores del formulario NO se "
            "borran) para empezar de nuevo."
        )
        act_clear.triggered.connect(self.on_clear)
        toolbar.addAction(act_clear)

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
        self.cb_diagram_pattern.addItems(["DL", "PL", "LL", "EL_X", "EL_Y"])
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

        viewer_container.setMinimumHeight(160)
        right.addWidget(viewer_container)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setMinimumHeight(140)

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
        right.setStretchFactor(0, 3)   # el visor 3D se lleva la mayor parte del espacio extra
        right.setStretchFactor(1, 1)
        right.setSizes([760, 200])
        main_layout.addWidget(right, 1)

        self.status = self.statusBar()
        self.status.showMessage("Defina la geometría y presione \"Construir modelo\".")

    def _build_summary_panel(self) -> QtWidgets.QWidget:
        """
        Resumen de cargas y sismo, estilo la hoja "1.Datos_Entrada" /
        "2.Cargas_Sismo" de la memoria de cálculo en Excel de referencia:
        carga de producto, carga viva y peso propio (por nivel y total de
        todo el rack), coeficientes sísmicos Ca/Cv/Cs y cortante basal V
        por dirección, y la distribución vertical de fuerzas Fx por
        nivel. Se calcula con el mismo motor ya validado
        (`vortex.loads.seismic`); este panel sólo hace visibles esos
        resultados intermedios, no repite el cálculo.

        Todo el contenido va dentro de un QScrollArea propio: así su
        altura mínima no obliga al splitter principal a robarle espacio
        vertical al visor 3D (antes esta pestaña, sin scroll, forzaba una
        altura mínima grande y dejaba el visor apretado).
        """
        outer = QtWidgets.QScrollArea()
        outer.setWidgetResizable(True)
        outer.setFrameShape(QtWidgets.QFrame.NoFrame)

        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        loads_box = QtWidgets.QGroupBox("⬇ Cargas (calculado)")
        loads_form = QtWidgets.QFormLayout(loads_box)
        self.lbl_sum_pl = QtWidgets.QLabel("—")
        self.lbl_sum_ll = QtWidgets.QLabel("—")
        self.lbl_sum_dl = QtWidgets.QLabel("—")
        loads_form.addRow("Producto (PL)", self.lbl_sum_pl)
        loads_form.addRow("Viva (LL)", self.lbl_sum_ll)
        loads_form.addRow("Peso propio (DL)", self.lbl_sum_dl)
        layout.addWidget(loads_box)

        seis_row = QtWidgets.QHBoxLayout()
        self.seis_trans_box, self._seis_trans_labels = self._build_seismic_summary_box(
            "〰 Transversal (marcos, R=4)"
        )
        self.seis_long_box, self._seis_long_labels = self._build_seismic_summary_box(
            "〰 Longitudinal (vigas, R=6)"
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
        self.table_fx.setMinimumHeight(120)
        dist_layout.addWidget(self.table_fx)
        layout.addWidget(dist_box, 1)

        placeholder = QtWidgets.QLabel(
            "Ejecute \"Analizar y verificar\" para calcular este resumen."
        )
        placeholder.setStyleSheet("color: #96a1ad;")
        layout.addWidget(placeholder)
        self._lbl_summary_placeholder = placeholder

        outer.setWidget(panel)
        return outer

    def _build_seismic_summary_box(self, title: str):
        """Grid de 2 columnas (en vez de una lista vertical de 8 filas)
        para que las dos cajas sísmicas (transversal/longitudinal) quepan
        una junto a otra sin ocupar tanta altura."""
        box = QtWidgets.QGroupBox(title)
        grid = QtWidgets.QGridLayout(box)
        grid.setHorizontalSpacing(14)
        labels = {}
        pairs = (
            ("ca", "Ca"), ("cv", "Cv"),
            ("r", "R"), ("ip", "Ip"),
            ("plrf", "PLRF"), ("cs", "Cs"),
            ("ws", "Ws"), ("v", "V"),
        )
        for i, (key, caption) in enumerate(pairs):
            row, col = divmod(i, 2)
            lbl = QtWidgets.QLabel("—")
            grid.addWidget(QtWidgets.QLabel(f"{caption}:"), row, col * 2)
            grid.addWidget(lbl, row, col * 2 + 1)
            labels[key] = lbl
        return box, labels

    def _populate_summary_panel(self) -> None:
        result = self.pipeline_result
        model = self.model
        if result is None or model is None:
            return
        self._lbl_summary_placeholder.setVisible(False)

        self.lbl_sum_pl.setText(
            f"{self.sp_pl.value():.2f} kgf/nivel-bahía → {result.pl_total_kn:.2f} kN/nivel · "
            f"{result.pl_grand_total_kn:.2f} kN total rack"
        )
        self.lbl_sum_ll.setText(
            f"{self.sp_ll.value():.2f} kN/m² → {result.ll_total_kn:.2f} kN/nivel · "
            f"{result.ll_grand_total_kn:.2f} kN total rack"
        )
        self.lbl_sum_dl.setText(
            f"{result.dl_per_level_kn:.3f} kN/nivel (tributario) · "
            f"{result.dl_total_kn:.3f} kN total (modelo 3D real)"
        )

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

        Igual que en `_build_summary_panel`: el contenido va dentro de un
        QScrollArea propio, para que si la pestaña queda con poca altura
        disponible el panel se vuelva desplazable en vez de comprimirse
        (con Qt aplastando la caja de configuración y el texto de estado
        de la API key hasta hacerlos ilegibles).
        """
        outer = QtWidgets.QScrollArea()
        outer.setWidgetResizable(True)
        outer.setFrameShape(QtWidgets.QFrame.NoFrame)

        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        cfg_box = QtWidgets.QGroupBox("Configuración (Groq)")
        cfg_form = QtWidgets.QFormLayout(cfg_box)

        # El modelo lo elige el sistema (CANDIDATE_MODELS, arriba en este
        # archivo): no hay selector ni lista para el usuario. Si un modelo
        # deja de estar disponible, Vortex prueba el siguiente de la lista
        # sin intervención del usuario — ver get_recommendations_auto().
        model_note = QtWidgets.QLabel(
            "El modelo de IA lo elige el sistema automáticamente "
            "(vortex/gui/app.py, CANDIDATE_MODELS) — no hay nada que "
            "configurar aquí."
        )
        model_note.setWordWrap(True)
        model_note.setStyleSheet("color: #96a1ad; font-size: 10px;")
        cfg_form.addRow(model_note)

        # La API key NO se pide ni se muestra en la interfaz: se configura
        # directamente en la constante GROQ_API_KEY al inicio de este mismo
        # archivo (vortex/gui/app.py), o en la variable de entorno
        # GROQ_API_KEY (tiene prioridad).
        key_status = QtWidgets.QLabel(self._groq_key_status_text())
        key_status.setWordWrap(True)
        key_status.setStyleSheet("color: #96a1ad; font-size: 10px;")
        self._lbl_groq_key_status = key_status
        cfg_form.addRow(key_status)
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
        self.txt_ai_output.setMinimumHeight(280)
        layout.addWidget(self.txt_ai_output, 1)

        outer.setWidget(panel)
        return outer

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
        load_form.addRow("Carga de producto (PL)", self.sp_pl)

        self.sp_ll = QtWidgets.QDoubleSpinBox(); self.sp_ll.setRange(0, 50)
        self.sp_ll.setValue(0.0); self.sp_ll.setSuffix(" kN/m²")
        load_form.addRow("Carga viva (LL)", self.sp_ll)

        ll_hint = QtWidgets.QLabel(
            "LL = carga viva distinta a la de estibas/producto (numeral "
            "2.1 NTC 5689); sólo aplica si la estantería tiene una "
            "plataforma o entrepiso transitable — la mayoría de "
            "estanterías selectivas sin entrepiso NO tienen esta carga "
            "(LL=0). Si aplica, use el valor de la norma de cargas de "
            "edificaciones vigente para la ocupación real del proyecto "
            "(en Colombia, NSR-10 Título B)."
        )
        ll_hint.setWordWrap(True)
        ll_hint.setStyleSheet("color: #96a1ad; font-size: 10px;")
        load_form.addRow(ll_hint)
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

    def on_update(self) -> None:
        """Reconstruye el modelo con los valores actuales del formulario y
        vuelve a analizar, en un solo paso. "Analizar y verificar" por sí
        solo reutiliza el último modelo construido — si se cambió una
        sección, la geometría, el arriostramiento, etc. sin volver a
        presionar "Construir modelo", los resultados no reflejan ese
        cambio; este botón evita ese olvido."""
        self.on_build_model()
        if self.model is not None:
            self.on_analyze()

    def on_clear(self) -> None:
        """Limpia el modelo, los resultados, el visor 3D y el panel de IA
        — NO toca los valores ya diligenciados en el formulario, para no
        perder lo que el usuario ya configuró."""
        self.model = None
        self.pipeline_result = None
        self.last_inputs = None

        self.viewer.clear_model()
        self.viewer.clear_force_diagram()
        self.cb_color_by.setEnabled(False)
        self.chk_show_diagram.setChecked(False)
        self.chk_show_diagram.setEnabled(False)

        self.results_table.setRowCount(0)
        self.table_fx.setRowCount(0)
        self._lbl_summary_placeholder.setVisible(True)
        self.txt_ai_output.clear()

        self.status.showMessage(
            "Modelo y resultados limpiados. Los valores del formulario se "
            "conservan — presione \"Construir modelo\" para empezar de nuevo."
        )

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
                ai_analysis=self.txt_ai_output.toPlainText(),
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

        api_key = _resolve_groq_api_key()
        summary = build_results_summary(self.model, self.pipeline_result, self.last_inputs)

        self.txt_ai_output.setPlainText("Consultando IA (Groq)...")
        self.btn_ai_recommend.setEnabled(False)

        self._ai_thread = QtCore.QThread(self)
        self._ai_worker = _AdvisorWorker(summary, api_key)
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

    def _groq_key_status_text(self) -> str:
        configured = bool(_resolve_groq_api_key())
        if configured:
            return "✓ API key de Groq configurada (GROQ_API_KEY en vortex/gui/app.py)."
        return (
            "Sin API key configurada. Edite la constante GROQ_API_KEY al inicio "
            "de vortex/gui/app.py y pegue ahí su API key de "
            "https://console.groq.com/keys, o defina la variable de entorno "
            "GROQ_API_KEY."
        )

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
