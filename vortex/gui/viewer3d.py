"""
Visor 3D interactivo de la estantería (estilo Inventor/SAP2000): los
elementos se dibujan como segmentos de línea coloreados por tipo (parales,
vigas, diagonales) o, después de analizar, por relación demanda/capacidad
(verde=holgado, amarillo=ajustado, rojo=no cumple).
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6 import QtCore

from ..geometry.model import MemberKind, RackModel
from ..analysis.solve import AnalysisResult

DIAGRAM_COLOR = (1.0, 0.55, 0.0, 1.0)   # naranja, estilo diagramas SAP2000
FORCE_COMPONENTS = ["P", "M2", "M3", "V2", "V3"]

KIND_COLOR = {
    MemberKind.UPRIGHT: (0.2, 0.4, 0.9, 1.0),
    MemberKind.BEAM: (0.9, 0.5, 0.1, 1.0),
    MemberKind.BRACE: (0.6, 0.6, 0.6, 1.0),
    MemberKind.BASE: (0.3, 0.3, 0.3, 1.0),
}


def ratio_to_color(ratio: float) -> tuple:
    if ratio <= 0.7:
        t = ratio / 0.7
        return (0.1 + 0.1 * t, 0.7, 0.15, 1.0)
    if ratio <= 1.0:
        t = (ratio - 0.7) / 0.3
        return (0.2 + 0.8 * t, 0.75 - 0.35 * t, 0.15 * (1 - t), 1.0)
    over = min((ratio - 1.0) / 1.0, 1.0)
    return (0.9, 0.15 * (1 - over), 0.15 * (1 - over), 1.0)


def heat_color(t: float) -> tuple:
    """Escala de calor azul->amarillo->rojo para un valor normalizado
    t en [0,1] (usada para colorear por magnitud de fuerza/esfuerzo, en
    vez de por relación demanda/capacidad)."""
    t = min(max(t, 0.0), 1.0)
    if t <= 0.5:
        u = t / 0.5
        return (0.15 + 0.75 * u, 0.35 + 0.5 * u, 0.85 - 0.75 * u, 1.0)
    u = (t - 0.5) / 0.5
    return (0.9, 0.85 - 0.70 * u, 0.1 * (1 - u), 1.0)


class Viewer3D(gl.GLViewWidget):
    memberClicked = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCameraPosition(distance=15, elevation=20, azimuth=45)
        self._grid = gl.GLGridItem()
        self._grid.setSize(20, 20)
        self._grid.setSpacing(1, 1)
        self.addItem(self._grid)
        self._line_item: Optional[gl.GLLinePlotItem] = None
        self._member_items: Dict[int, gl.GLLinePlotItem] = {}
        self._diagram_items: list = []
        self._model: Optional[RackModel] = None

    def clear_model(self) -> None:
        for item in self._member_items.values():
            self.removeItem(item)
        self._member_items.clear()
        self.clear_force_diagram()

    def show_model(self, model: RackModel) -> None:
        self.clear_model()
        self._model = model
        for mid, member in model.members.items():
            ni, nj = model.nodes[member.node_i], model.nodes[member.node_j]
            pts = np.array([[ni.x, ni.y, ni.z], [nj.x, nj.y, nj.z]])
            color = KIND_COLOR.get(member.kind, (1, 1, 1, 1))
            width = 4.0 if member.kind == MemberKind.UPRIGHT else 2.5
            item = gl.GLLinePlotItem(pos=pts, color=color, width=width, antialias=True)
            self.addItem(item)
            self._member_items[mid] = item

        (x0, y0, z0), (x1, y1, z1) = model.bounding_box()
        cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
        span = max(x1 - x0, y1 - y0, z1 - z0, 1.0)
        self.setCameraPosition(distance=span * 1.6)
        self.opts["center"] = pg.Vector(cx, cy, cz)

    def color_by_ratio(self, ratios: Dict[int, float]) -> None:
        for mid, item in self._member_items.items():
            if mid in ratios:
                item.setData(color=ratio_to_color(ratios[mid]))

    def color_by_heat(self, values: Dict[int, float]) -> None:
        """Colorea por magnitud relativa (0=mínimo azul, 1=máximo rojo),
        normalizando `values` (p.ej. fuerza axial o momento) a [0,1] por
        separado dentro de cada tipo de elemento (parales entre sí, vigas
        entre sí), para que ambos grupos usen su propio rango de "calor"."""
        if self._model is None or not values:
            return
        by_kind: Dict[MemberKind, list] = {}
        for mid, v in values.items():
            member = self._model.members.get(mid)
            if member is None:
                continue
            by_kind.setdefault(member.kind, []).append((mid, v))
        for kind, items in by_kind.items():
            vals = [v for _, v in items]
            vmin, vmax = min(vals), max(vals)
            span = (vmax - vmin) or 1.0
            for mid, v in items:
                t = (v - vmin) / span
                item = self._member_items.get(mid)
                if item is not None:
                    item.setData(color=heat_color(t))

    def clear_force_diagram(self) -> None:
        for item in self._diagram_items:
            self.removeItem(item)
        self._diagram_items.clear()

    def show_force_diagram(
        self, model: RackModel, analysis: AnalysisResult, component: str,
        kinds: Optional[set] = None, scale: float = 1.0,
    ) -> None:
        """
        Dibuja el diagrama de la fuerza `component` (P, M2, M3, V2 o V3)
        de cada elemento como una línea offset (estilo SAP2000/Inventor),
        perpendicular al eje del elemento, con el valor en cada extremo
        interpolado linealmente entre los dos extremos analizados (para
        vigas con carga uniforme, esta es una aproximación lineal del
        verdadero diagrama parabólico de momento — el cálculo de diseño
        usa la fórmula exacta por estación, ver `design.beam`; sólo la
        visualización se simplifica aquí a una línea recta extremo-extremo).
        """
        self.clear_force_diagram()
        if component not in FORCE_COMPONENTS:
            raise ValueError(f"Componente de fuerza desconocido: {component}")

        end_attr_i = {"P": "P_i", "M2": "M2_i", "M3": "M3_i", "V2": "V2_i", "V3": "V3_i"}[component]
        end_attr_j = {"P": "P_j", "M2": "M2_j", "M3": "M3_j", "V2": "V2_j", "V3": "V3_j"}[component]

        values = {}
        for mid, mf in analysis.member_forces.items():
            member = model.members.get(mid)
            if member is None or (kinds is not None and member.kind not in kinds):
                continue
            values[mid] = (getattr(mf, end_attr_i), getattr(mf, end_attr_j))
        if not values:
            return
        max_abs = max(max(abs(a), abs(b)) for a, b in values.values()) or 1.0

        (x0, y0, z0), (x1, y1, z1) = model.bounding_box()
        span = max(x1 - x0, y1 - y0, z1 - z0, 1.0)
        max_offset = 0.12 * span * scale

        offset_axis = "ez" if component in ("M2", "P", "V2") else "ey"
        for mid, (vi, vj) in values.items():
            member = model.members[mid]
            geom = analysis.member_geometry.get(mid)
            if geom is None:
                continue
            ni, nj = model.nodes[member.node_i], model.nodes[member.node_j]
            p_i = np.array([ni.x, ni.y, ni.z])
            p_j = np.array([nj.x, nj.y, nj.z])
            axis_vec = getattr(geom, offset_axis)
            off_i = p_i + axis_vec * (vi / max_abs) * max_offset
            off_j = p_j + axis_vec * (vj / max_abs) * max_offset
            pts = np.array([p_i, off_i, off_j, p_j])
            item = gl.GLLinePlotItem(pos=pts, color=DIAGRAM_COLOR, width=2.0, antialias=True)
            self.addItem(item)
            self._diagram_items.append(item)

    def color_by_kind(self) -> None:
        if self._model is None:
            return
        for mid, item in self._member_items.items():
            member = self._model.members[mid]
            item.setData(color=KIND_COLOR.get(member.kind, (1, 1, 1, 1)))
