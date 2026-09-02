"""
Aplicación de escritorio Vortex: interfaz visual para modelar, analizar y
verificar estanterías industriales de acero según NTC 5689, con un flujo
de trabajo similar al de Autodesk Inventor (modelado paramétrico, barra de
herramientas superior, panel de propiedades) y SAP2000 (análisis matricial
+ resultados coloreados por elemento).
"""
from __future__ import annotations

import datetime
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
    element_forces_table, write_element_forces_csv, BasePlateInputs,
)
from ..report import ProjectInfo, ReportData, generate_memoria
from ..units import kgf_to_kn
from ..ai import advisor
from ..ai.advisor import AdvisorError, build_results_summary, get_recommendations_auto
from .viewer3d import Viewer3D
from .legend import ColorLegend
from .theme import apply_dark_theme
from .diagrams_dialog import DiagramsDialog

SOIL_TYPES = ["A", "B", "C", "D", "E"]
BRACE_ANGLES_DEG = [30, 45, 60, 65, 70, 75]

# =====================================================================
# Recomendaciones IA (Groq) — la configuración de la API key, el resumen
# de resultados y la llamada HTTP viven en `vortex.ai.advisor` (un único
# lugar, reutilizable también fuera de la GUI); antes ese mismo código
# estaba DUPLICADO aquí, con el riesgo de que las dos copias se
# desincronizaran (p.ej. una recibía el manejo de HTTP 429/
# model_decommissioned y la otra no). La GUI sólo reexporta lo que usa
# y le agrega la selección automática de modelo (`get_recommendations_auto`,
# ya definida en `advisor`) y el hilo de Qt para no bloquear la interfaz.
#
# La API key NUNCA se pide ni se muestra en un campo de la interfaz
# gráfica ni se guarda en una constante de este archivo (fuente
# versionada): ver `vortex.ai.advisor.load_local_api_key` — variable de
# entorno GROQ_API_KEY, o vortex/ai/local_config.py, o .groq_api_key en
# la raíz del proyecto (ambos archivos gitignored). Consigue una API key
# gratuita en https://console.groq.com/keys.
# =====================================================================
CANDIDATE_MODELS = advisor.AVAILABLE_MODELS
SYSTEM_PROMPT = advisor.SYSTEM_PROMPT


def _resolve_groq_api_key() -> str:
    return advisor.load_local_api_key()


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

        # "Más herramientas" va PRIMERO (a la izquierda) y como menú
        # desplegable — patrón estándar de software CAD/ingeniería
        # profesional (Inventor, SolidWorks, ANSYS): las acciones menos
        # frecuentes (construir, analizar, exportar) quedan agrupadas bajo
        # un único botón con flecha, en vez de una ventana aparte o de
        # llenar la barra de íconos sueltos.
        self.btn_tools = QtWidgets.QToolButton()
        self.btn_tools.setText("🗂  Más herramientas ▾")
        self.btn_tools.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.btn_tools.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        menu_tools = QtWidgets.QMenu(self.btn_tools)

        act_build = menu_tools.addAction("🧱  Construir modelo")
        act_build.setToolTip("Arma la geometría 3D a partir del formulario.")
        act_build.triggered.connect(self.on_build_model)

        act_analyze = menu_tools.addAction("📊  Analizar y verificar")
        act_analyze.setToolTip("Corre el análisis matricial y la verificación normativa.")
        act_analyze.triggered.connect(self.on_analyze)

        menu_tools.addSeparator()

        act_export = menu_tools.addAction("📄  Memoria de cálculo (.docx)")
        act_export.triggered.connect(self.on_export)

        act_export_forces = menu_tools.addAction("📈  Fuerzas por elemento (.csv)")
        act_export_forces.triggered.connect(self.on_export_element_forces)

        menu_tools.addSeparator()

        # El panel de recomendaciones de IA ahora es un panel acoplable
        # (dock) siempre visible junto al visor 3D, no una pestaña que se
        # pueda perder de vista — `toggleViewAction()` es el mecanismo
        # nativo de Qt para mostrar/ocultar ese panel (equivalente al menú
        # "Ver" de un programa de escritorio profesional).
        act_toggle_ai = menu_tools.addAction(
            "🤖  Mostrar/ocultar panel de Recomendaciones IA"
        )
        act_toggle_ai.triggered.connect(self._on_ai_toolbar_clicked)

        # Los controles de "Vista" (líneas de fuerzas del visor 3D y
        # encuadre/zoom) viven DENTRO de "Más herramientas" — a pedido
        # explícito del usuario ya no tienen su propio botón desplegable
        # en la barra. Al ser un QWidgetAction dentro del mismo QMenu, el
        # usuario interactúa con los controles reales sin que el menú se
        # cierre en cada clic.
        menu_tools.addSeparator()
        view_panel = self._build_view_menu_panel()
        act_view_panel = QtWidgets.QWidgetAction(menu_tools)
        act_view_panel.setDefaultWidget(view_panel)
        menu_tools.addAction(act_view_panel)

        self.btn_tools.setMenu(menu_tools)
        toolbar.addWidget(self.btn_tools)

        toolbar.addSeparator()

        act_update = QtGui.QAction("🔄  Actualizar", self)
        act_update.setToolTip(
            "Reconstruye el modelo con los valores actuales del formulario y "
            "vuelve a analizar, en un solo clic — use esto después de cambiar "
            "cualquier dato (geometría, secciones, cargas, sismo)."
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

        act_diagrams = QtWidgets.QToolButton()
        act_diagrams.setText("📐  Diagramas y especificaciones ▾")
        act_diagrams.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        act_diagrams.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        act_diagrams.setToolTip(
            "Diagrama de cargas de producto, diagrama de cargas de sismo "
            "(calculado, con tabla NIVEL/FX), diagramas de momento, fuerza "
            "axial y cortante (estilo SAP2000), y especificaciones de cada "
            "sección de paral."
        )
        menu_diagrams = QtWidgets.QMenu(act_diagrams)
        diagram_tabs = [
            ("🖼  Cargas de producto", 0),
            ("🌍  Cargas de sismo", 1),
            ("📐  Diagrama de momentos", 2),
            ("📊  Diagrama de fuerza axial", 3),
            ("✂️  Diagrama de cortante", 4),
            ("📋  Especificaciones de parales", 5),
        ]
        for label, tab_index in diagram_tabs:
            act = menu_diagrams.addAction(label)
            act.triggered.connect(
                lambda checked=False, i=tab_index: self.on_open_diagrams_dialog(i)
            )
        act_diagrams.setMenu(menu_diagrams)
        toolbar.addWidget(act_diagrams)

    def _build_view_menu_panel(self) -> QtWidgets.QWidget:
        """
        Panel embebido dentro del desplegable "🗂 Más herramientas" de la
        barra de herramientas: control de "Encuadre" (zoom/ajuste de la
        vista al tamaño del estante). El control creado aquí
        (`self.sp_view_zoom`) es el MISMO objeto que usa el resto de la
        clase — sólo cambia dónde vive visualmente.

        (La opción "Líneas de fuerzas" — overlay de P/M2/M3/V2/V3 sobre el
        visor 3D — se retiró a pedido explícito del usuario; los
        diagramas de momento/axial/cortante siguen disponibles en el
        desplegable "📐 Diagramas y especificaciones".)
        """
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        lbl_frame = QtWidgets.QLabel("Encuadre del estante (zoom del visor 3D)")
        lbl_frame.setStyleSheet("font-weight: 600;")
        layout.addWidget(lbl_frame)

        row4 = QtWidgets.QHBoxLayout()
        row4.addWidget(QtWidgets.QLabel("Tamaño"))
        self.sp_view_zoom = QtWidgets.QDoubleSpinBox()
        self.sp_view_zoom.setRange(0.3, 5.0)
        self.sp_view_zoom.setSingleStep(0.1)
        self.sp_view_zoom.setValue(1.6)
        self.sp_view_zoom.setToolTip(
            "Distancia de la cámara = mayor dimensión del estante x este "
            "factor. Valores menores acercan la vista (estante se ve más "
            "grande); valores mayores la alejan (estante se ve más pequeño)."
        )
        self.sp_view_zoom.valueChanged.connect(self._on_view_zoom_changed)
        row4.addWidget(self.sp_view_zoom, 1)
        layout.addLayout(row4)

        btn_fit = QtWidgets.QPushButton("🔍  Ajustar encuadre")
        btn_fit.setToolTip("Vuelve a centrar y ajustar la cámara al tamaño actual del estante.")
        btn_fit.clicked.connect(lambda: self.viewer.fit_view(self.sp_view_zoom.value()))
        layout.addWidget(btn_fit)

        return panel

    def _on_view_zoom_changed(self, value: float) -> None:
        self.viewer.fit_view(value)

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

        # NOTA: el control de encuadre del visor 3D ya NO vive aquí debajo
        # del visor — se movió dentro del menú desplegable "🗂 Más
        # herramientas" de la barra de herramientas (ver `_build_toolbar`),
        # para no ocupar espacio permanente con un control de uso
        # ocasional ni un botón de barra aparte. Se sigue creando en
        # `_build_view_menu_panel` y funciona exactamente igual.

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
        self.ai_dock = QtWidgets.QDockWidget("🤖 Recomendaciones IA", self)
        self.ai_dock.setObjectName("aiDock")
        self.ai_dock.setWidget(self.ai_panel)
        self.ai_dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
            | QtWidgets.QDockWidget.DockWidgetClosable
        )
        self.ai_dock.setMinimumWidth(320)
        # Panel acoplable SIEMPRE visible junto al visor 3D (no una pestaña
        # que haya que recordar abrir) — igual que el panel de propiedades
        # de un programa CAD/ingeniería profesional (Inventor, SolidWorks,
        # ANSYS): la respuesta de la IA queda a la vista de inmediato en
        # vez de escondida detrás de una pestaña inferior.
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.ai_dock)

        self.load_diagram_panel = self._build_load_diagram_panel()
        # NOTA: este panel ya NO se agrega como pestaña visible (quedaba
        # duplicado con la pestaña "🖼 Cargas de producto" de la ventana
        # "Diagramas y especificaciones"). Se sigue construyendo y
        # actualizando internamente (on_build_model/on_analyze/on_clear lo
        # usan) por si algún otro panel llega a necesitarlo, pero no ocupa
        # espacio en la barra de pestañas principal.

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

    def _build_load_diagram_panel(self) -> QtWidgets.QWidget:
        """
        Pestaña que muestra el diagrama de cargas de producto (bahía x
        nivel, w en kN/m por viga — mismo dibujo que exporta el botón
        "Diagrama de cargas (.png)" de la barra de herramientas) como
        imagen DENTRO de la interfaz, no sólo como archivo para guardar.
        Se regenera cada vez que corre el análisis (`on_analyze`), a
        partir de `pipeline_result.load_distribution` — el mismo reparto
        de carga usado por el motor de análisis (`loads.distribution`).
        """
        outer = QtWidgets.QScrollArea()
        outer.setWidgetResizable(True)
        outer.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.setAlignment(QtCore.Qt.AlignCenter)

        self.lbl_load_diagram = QtWidgets.QLabel(
            "Construya el modelo y presione \"Analizar y verificar\" para "
            "ver aquí el diagrama de cargas de producto."
        )
        self.lbl_load_diagram.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_load_diagram.setWordWrap(True)
        self.lbl_load_diagram.setStyleSheet("color: #96a1ad; padding: 24px;")
        outer.setWidget(self.lbl_load_diagram)
        return outer

    def _populate_load_diagram_panel(self) -> None:
        """Regenera el diagrama de cargas de producto y lo muestra en la
        pestaña "🖼 Diagrama de cargas" como imagen (sin pasar por
        disco: se renderiza a un buffer PNG en memoria)."""
        if self.model is None or self.pipeline_result is None:
            return
        dist = self.pipeline_result.load_distribution
        if dist is None:
            return
        try:
            import io
            from ..loads import plot_product_load_diagram
            fig = plot_product_load_diagram(self.model, dist)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150)
            import matplotlib.pyplot as plt
            plt.close(fig)
            pixmap = QtGui.QPixmap()
            pixmap.loadFromData(buf.getvalue())
            self.lbl_load_diagram.setPixmap(pixmap)
            self.lbl_load_diagram.setText("")
            self.lbl_load_diagram.adjustSize()
        except Exception as exc:
            self.lbl_load_diagram.setPixmap(QtGui.QPixmap())
            self.lbl_load_diagram.setText(f"No se pudo generar el diagrama de cargas:\n{exc}")

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

        # "Configuración (Groq)" ahora es una sección desplegable/colapsable
        # (colapsada por defecto) en vez de un bloque de texto siempre
        # visible — así el panel de IA se ve limpio de entrada y el texto
        # de configuración (detalles internos como el nombre del archivo
        # de código o la variable de entorno) sólo aparece si el usuario
        # decide abrirlo.
        cfg_header = QtWidgets.QToolButton()
        cfg_header.setText("▸  Configuración (Groq)")
        cfg_header.setCheckable(True)
        cfg_header.setChecked(False)
        cfg_header.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        cfg_header.setStyleSheet(
            "QToolButton { border: none; font-weight: 600; text-align: left; }"
        )
        layout.addWidget(cfg_header)

        cfg_content = QtWidgets.QWidget()
        cfg_form = QtWidgets.QFormLayout(cfg_content)
        cfg_content.setVisible(False)

        # El modelo lo elige el sistema (CANDIDATE_MODELS, arriba en este
        # archivo): no hay selector ni lista para el usuario. Si un modelo
        # deja de estar disponible, Vortex prueba el siguiente de la lista
        # sin intervención del usuario — ver get_recommendations_auto().
        model_note = QtWidgets.QLabel(
            "El modelo de IA lo elige el sistema automáticamente "
            "(vortex/ai/advisor.py, AVAILABLE_MODELS) — no hay nada que "
            "configurar aquí."
        )
        model_note.setWordWrap(True)
        model_note.setStyleSheet("color: #96a1ad; font-size: 10px;")
        cfg_form.addRow(model_note)

        # La API key NO se pide ni se muestra en la interfaz: se configura
        # en vortex/ai/local_config.py, en .groq_api_key (raíz del
        # proyecto, ambos gitignored), o en la variable de entorno
        # GROQ_API_KEY (máxima prioridad) — ver
        # `vortex.ai.advisor.load_local_api_key`.
        key_status = QtWidgets.QLabel(self._groq_key_status_text())
        key_status.setWordWrap(True)
        key_status.setStyleSheet("color: #96a1ad; font-size: 10px;")
        self._lbl_groq_key_status = key_status
        cfg_form.addRow(key_status)
        layout.addWidget(cfg_content)

        def _toggle_cfg(checked: bool) -> None:
            cfg_content.setVisible(checked)
            cfg_header.setText(("▾  " if checked else "▸  ") + "Configuración (Groq)")

        cfg_header.toggled.connect(_toggle_cfg)

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
        self.sp_bays = QtWidgets.QSpinBox(); self.sp_bays.setRange(1, 50); self.sp_bays.setValue(5)
        self.sp_bay_length = QtWidgets.QDoubleSpinBox(); self.sp_bay_length.setRange(0.5, 6.0)
        self.sp_bay_length.setValue(2.44); self.sp_bay_length.setSuffix(" m")
        self.sp_depth = QtWidgets.QDoubleSpinBox(); self.sp_depth.setRange(0.3, 3.0)
        self.sp_depth.setValue(1.06); self.sp_depth.setSuffix(" m")
        self.sp_n_levels = QtWidgets.QSpinBox(); self.sp_n_levels.setRange(1, 20); self.sp_n_levels.setValue(5)
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
        # Valores por defecto = proyecto de referencia real (memoria de
        # cálculo LOGISTOOL, estantería 9.50m x 6 niveles x 2400kg/nivel,
        # Medellín): "VIGA CAJA 160x60x1.5mm" es la sección real de ese
        # proyecto (ver comentario en sections/catalog.py) — NO la de caja
        # 100x50x2.0mm que quedaba seleccionada por ser la primera de la
        # lista. Si esta sección no está en el catálogo por algún motivo
        # (p.ej. catálogo editado), `setCurrentText` simplemente no hace
        # nada y queda la selección por defecto de Qt (primer ítem).
        self.cb_beam.setCurrentText("VIGA CAJA 160x60x1.5mm")
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

        self.lbl_pl_kn = QtWidgets.QLabel()
        self.lbl_pl_kn.setStyleSheet("color: #96a1ad; font-size: 10px;")
        load_form.addRow("", self.lbl_pl_kn)
        self.sp_pl.valueChanged.connect(self._on_pl_changed)
        self._on_pl_changed(self.sp_pl.value())

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
        self.sp_pl_promedio_ratio = QtWidgets.QDoubleSpinBox()
        self.sp_pl_promedio_ratio.setRange(0.01, 1.0)
        self.sp_pl_promedio_ratio.setDecimals(2)
        self.sp_pl_promedio_ratio.setSingleStep(0.01)
        # 0.76 = PLpromedio/PLmaxima del proyecto de referencia real
        # (memoria de cálculo LOGISTOOL) — NTC 5689 numeral 2.7.2 exige
        # este dato para la dirección longitudinal (no arriostrada); antes
        # no había ningún control para editarlo y el motor usaba 1.0
        # (PLRF=1.0, sismo longitudinal sobrestimado) sin que el usuario
        # lo supiera.
        self.sp_pl_promedio_ratio.setValue(0.76)
        self.sp_pl_promedio_ratio.setToolTip(
            "PLpromedio / PLmáxima (NTC 5689 numeral 2.7.2): reduce el peso "
            "sísmico efectivo en la dirección longitudinal (no arriostrada, "
            "vigas) cuando no todas las bahías están cargadas al máximo "
            "simultáneamente. 1.0 = todas las bahías siempre a carga máxima "
            "(conservador). No aplica a la dirección transversal (PLRF=1.0 "
            "fijo ahí, ver numeral 2.7.2)."
        )
        self.chk_el_relaxed = QtWidgets.QCheckBox("Factor EL=1.0 (relajación NTC 5689 num. 2.2)")
        self.chk_el_relaxed.setChecked(False)
        self.chk_el_relaxed.setToolTip(
            "Desmarcar para usar EL=1.5 sin relajar (combinación literal "
            "1.2DL+1.5EL+0.85PL), como en el proyecto de referencia."
        )
        seis_form.addRow("Ciudad (NSR-10)", self.cb_city)
        seis_form.addRow("Aa", self.sp_aa)
        seis_form.addRow("Av", self.sp_av)
        seis_form.addRow("Tipo de perfil de suelo", self.cb_soil)
        seis_form.addRow("PLpromedio/PLmáxima (long.)", self.sp_pl_promedio_ratio)
        seis_form.addRow(self.chk_el_relaxed)
        layout.addWidget(seis_box)
        self._on_city_changed(self.cb_city.currentText())

        bp_box = QtWidgets.QGroupBox("⚓ Placa base / anclajes (opcional)")
        bp_form = QtWidgets.QFormLayout(bp_box)
        self.chk_base_plate = QtWidgets.QCheckBox("Verificar placa base y anclajes")
        self.chk_base_plate.setChecked(False)
        self.chk_base_plate.toggled.connect(self._on_base_plate_toggled)
        bp_form.addRow(self.chk_base_plate)

        self.sp_bp_length = QtWidgets.QDoubleSpinBox()
        self.sp_bp_length.setRange(0.05, 1.0); self.sp_bp_length.setSingleStep(0.01)
        self.sp_bp_length.setValue(0.15); self.sp_bp_length.setSuffix(" m")
        bp_form.addRow("Largo de placa (X)", self.sp_bp_length)

        self.sp_bp_width = QtWidgets.QDoubleSpinBox()
        self.sp_bp_width.setRange(0.05, 1.0); self.sp_bp_width.setSingleStep(0.01)
        self.sp_bp_width.setValue(0.15); self.sp_bp_width.setSuffix(" m")
        bp_form.addRow("Ancho de placa (Y)", self.sp_bp_width)

        self.sp_bp_spacing_x = QtWidgets.QDoubleSpinBox()
        self.sp_bp_spacing_x.setRange(0.02, 0.9); self.sp_bp_spacing_x.setSingleStep(0.01)
        self.sp_bp_spacing_x.setValue(0.10); self.sp_bp_spacing_x.setSuffix(" m")
        bp_form.addRow("Separación anclajes (X)", self.sp_bp_spacing_x)

        self.sp_bp_spacing_y = QtWidgets.QDoubleSpinBox()
        self.sp_bp_spacing_y.setRange(0.02, 0.9); self.sp_bp_spacing_y.setSingleStep(0.01)
        self.sp_bp_spacing_y.setValue(0.10); self.sp_bp_spacing_y.setSuffix(" m")
        bp_form.addRow("Separación anclajes (Y)", self.sp_bp_spacing_y)

        self.sp_bp_fc = QtWidgets.QDoubleSpinBox()
        self.sp_bp_fc.setRange(10.0, 50.0); self.sp_bp_fc.setValue(21.0); self.sp_bp_fc.setSuffix(" MPa")
        bp_form.addRow("f'c del concreto", self.sp_bp_fc)

        self.sp_bp_anchor_tension = QtWidgets.QDoubleSpinBox()
        self.sp_bp_anchor_tension.setRange(0.0, 500.0); self.sp_bp_anchor_tension.setSuffix(" kN")
        bp_form.addRow("Cap. tracción por anclaje", self.sp_bp_anchor_tension)

        self.sp_bp_anchor_shear = QtWidgets.QDoubleSpinBox()
        self.sp_bp_anchor_shear.setRange(0.0, 500.0); self.sp_bp_anchor_shear.setSuffix(" kN")
        bp_form.addRow("Cap. cortante por anclaje", self.sp_bp_anchor_shear)

        bp_hint = QtWidgets.QLabel(
            "Patrón de 4 anclajes (uno por esquina). Las capacidades de "
            "tracción y cortante por anclaje deben venir del informe de "
            "evaluación técnica (ICC-ES u homólogo) del anclaje real del "
            "proyecto — este chequeo NO recalcula la capacidad al concreto "
            "(ACI 318 cap. 17), sólo la demanda."
        )
        bp_hint.setWordWrap(True)
        bp_hint.setStyleSheet("color: #96a1ad; font-size: 10px;")
        bp_form.addRow(bp_hint)
        layout.addWidget(bp_box)
        self._on_base_plate_toggled(self.chk_base_plate.isChecked())

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

    def _on_base_plate_toggled(self, checked: bool) -> None:
        for w in (
            self.sp_bp_length, self.sp_bp_width, self.sp_bp_spacing_x, self.sp_bp_spacing_y,
            self.sp_bp_fc, self.sp_bp_anchor_tension, self.sp_bp_anchor_shear,
        ):
            w.setEnabled(checked)

    def _current_base_plate_inputs(self) -> Optional[BasePlateInputs]:
        if not self.chk_base_plate.isChecked():
            return None
        return BasePlateInputs(
            plate_length=self.sp_bp_length.value(), plate_width=self.sp_bp_width.value(),
            anchor_spacing_x=self.sp_bp_spacing_x.value(), anchor_spacing_y=self.sp_bp_spacing_y.value(),
            f_c_concrete_mpa=self.sp_bp_fc.value(),
            anchor_capacity_tension_kn=self.sp_bp_anchor_tension.value(),
            anchor_capacity_shear_kn=self.sp_bp_anchor_shear.value(),
        )

    def _on_city_changed(self, city: str) -> None:
        data = AA_AV_BY_CITY.get(city)
        if data:
            self.sp_aa.setValue(float(data["Aa"]))
            self.sp_av.setValue(float(data["Av"]))

    def _on_pl_changed(self, value: float) -> None:
        """Muestra en kN el valor de carga de producto (PL) que el
        usuario diligencia en kgf — mismo campo, misma unidad que usa
        internamente el motor de análisis (`PipelineInputs.pl_per_level_kn`,
        vía `kgf_to_kn`)."""
        self.lbl_pl_kn.setText(f"= {kgf_to_kn(value):.2f} kN / nivel-bahía")

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
            self.results_table.setRowCount(0)
            self.table_fx.setRowCount(0)
            self._lbl_summary_placeholder.setVisible(True)
            self.lbl_load_diagram.setPixmap(QtGui.QPixmap())
            self.lbl_load_diagram.setText(
                "Presione \"Analizar y verificar\" para ver aquí el diagrama "
                "de cargas de producto."
            )
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
                    pl_promedio_ratio=self.sp_pl_promedio_ratio.value(),
                ),
                apply_el_factor_10=self.chk_el_relaxed.isChecked(),
                base_plate=self._current_base_plate_inputs(),
            )
            self.last_inputs = inputs
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            self.pipeline_result = run_full_check(self.model, inputs)
            QtWidgets.QApplication.restoreOverrideCursor()

            self.cb_color_by.setEnabled(True)
            self._apply_coloring()
            self._populate_results_table()
            self._populate_summary_panel()
            self._populate_load_diagram_panel()
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
        self.cb_color_by.setEnabled(False)

        self.results_table.setRowCount(0)
        self.table_fx.setRowCount(0)
        self._lbl_summary_placeholder.setVisible(True)
        self.txt_ai_output.clear()
        self.lbl_load_diagram.setPixmap(QtGui.QPixmap())
        self.lbl_load_diagram.setText(
            "Construya el modelo y presione \"Analizar y verificar\" para "
            "ver aquí el diagrama de cargas de producto."
        )

        self.status.showMessage(
            "Modelo y resultados limpiados. Los valores del formulario se "
            "conservan — presione \"Construir modelo\" para empezar de nuevo."
        )

    def _on_color_by_changed(self) -> None:
        if self.pipeline_result is not None:
            self._apply_coloring()

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
                base_plate_rows=self.pipeline_result.base_plate_rows,
            )
            generate_memoria(data, path)
            self.status.showMessage(f"Memoria de cálculo exportada: {path}")
            QtWidgets.QMessageBox.information(self, "Vortex", f"Memoria exportada a:\n{path}")
        except Exception as exc:
            self._show_error("Error al exportar la memoria", exc)

    def on_export_load_diagram(self) -> None:
        if self.model is None or self.pipeline_result is None:
            QtWidgets.QMessageBox.warning(
                self, "Vortex", "Primero construya el modelo y ejecute el análisis."
            )
            return
        dist = self.pipeline_result.load_distribution
        if dist is None:
            QtWidgets.QMessageBox.warning(
                self, "Vortex", "El análisis no expone la distribución de cargas."
            )
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar diagrama de cargas", "cargas_producto.png", "Imagen PNG (*.png)"
        )
        if not path:
            return
        try:
            from ..loads import plot_product_load_diagram
            plot_product_load_diagram(self.model, dist, path=path)
            self.status.showMessage(f"Diagrama de cargas exportado: {path}")
            QtWidgets.QMessageBox.information(self, "Vortex", f"Diagrama exportado a:\n{path}")
        except Exception as exc:
            self._show_error("Error al exportar el diagrama de cargas", exc)

    def on_open_diagrams_dialog(self, initial_tab: int = 0) -> None:
        """Abre la ventana de diagramas (cargas, sismo, momento, axial,
        cortante) y especificaciones de parales, a partir del último
        análisis corrido ('Analizar y verificar'). `initial_tab` permite
        abrir directamente en la pestaña elegida desde el desplegable de
        la barra de herramientas."""
        if self.model is None or self.pipeline_result is None or self.last_inputs is None:
            QtWidgets.QMessageBox.warning(
                self, "Vortex", "Primero construya el modelo y ejecute \"Analizar y verificar\"."
            )
            return
        try:
            dialog = DiagramsDialog(
                self.model, self.pipeline_result, self.last_inputs, self, initial_tab=initial_tab,
            )
            dialog.exec()
        except Exception as exc:
            self._show_error("Error al abrir la ventana de diagramas", exc)

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
        """Muestra/oculta el panel acoplable de Recomendaciones IA (si
        estaba oculto lo muestra y lo trae al frente; si ya estaba visible
        lo oculta) y, si hay un análisis corrido, dispara la consulta a la
        IA de una vez. Se usa `isHidden()` (no `isVisible()`) para decidir
        la dirección del toggle: `isVisible()` también depende de si la
        ventana principal está mostrada, mientras que `isHidden()` refleja
        únicamente si este panel en particular fue ocultado explícitamente."""
        if not self.ai_dock.isHidden():
            self.ai_dock.hide()
            return
        self.ai_dock.show()
        self.ai_dock.raise_()
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
            return "✓ API key de Groq configurada."
        return (
            "Sin API key configurada. Copie vortex/ai/local_config.example.py "
            "a vortex/ai/local_config.py y pegue ahí su API key de "
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
