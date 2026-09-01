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
        self._model: Optional[RackModel] = None

    def clear_model(self) -> None:
        for item in self._member_items.values():
            self.removeItem(item)
        self._member_items.clear()

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

    def color_by_kind(self) -> None:
        if self._model is None:
            return
        for mid, item in self._member_items.items():
            member = self._model.members[mid]
            item.setData(color=KIND_COLOR.get(member.kind, (1, 1, 1, 1)))
