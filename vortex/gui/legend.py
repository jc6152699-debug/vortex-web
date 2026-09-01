"""Leyenda de escala de colores para los resultados del visor 3D."""
from __future__ import annotations

from PySide6 import QtWidgets, QtCore

from .viewer3d import ratio_to_color


def _css_rgba(color: tuple) -> str:
    r, g, b, a = color
    return f"rgba({int(r * 255)},{int(g * 255)},{int(b * 255)},{a})"


class ColorLegend(QtWidgets.QWidget):
    """Barra de degradado con etiquetas, estilo la leyenda de esfuerzos de
    SAP2000/Inventor: verde = holgado, amarillo = ajustado, rojo = no
    cumple (para el modo "ratio"), o una escala de calor relativa
    (para el modo "fuerza")."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.title = QtWidgets.QLabel("Escala de colores")
        self.title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.title)

        self.bar = QtWidgets.QLabel()
        self.bar.setFixedHeight(18)
        layout.addWidget(self.bar)

        labels_row = QtWidgets.QHBoxLayout()
        self.lbl_min = QtWidgets.QLabel()
        self.lbl_mid = QtWidgets.QLabel()
        self.lbl_max = QtWidgets.QLabel()
        self.lbl_mid.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_max.setAlignment(QtCore.Qt.AlignRight)
        for lbl in (self.lbl_min, self.lbl_mid, self.lbl_max):
            lbl.setStyleSheet("font-size: 10px; color: #555;")
        labels_row.addWidget(self.lbl_min)
        labels_row.addWidget(self.lbl_mid, 1)
        labels_row.addWidget(self.lbl_max)
        layout.addLayout(labels_row)

        self.set_ratio_scale()

    def set_ratio_scale(self) -> None:
        stops = [(0.0, ratio_to_color(0.0)), (0.35, ratio_to_color(0.35)),
                  (0.7, ratio_to_color(0.7)), (0.85, ratio_to_color(0.85)),
                  (1.0, ratio_to_color(1.0)), (2.0, ratio_to_color(2.0))]
        positions = [0.0, 0.175, 0.35, 0.425, 0.5, 1.0]
        css_stops = ", ".join(
            f"stop:{pos:.3f} {_css_rgba(c)}" for pos, (_, c) in zip(positions, stops)
        )
        self.bar.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0, {css_stops}); "
            f"border: 1px solid #999; border-radius: 2px;"
        )
        self.title.setText("Escala de colores — relación demanda/capacidad")
        self.lbl_min.setText("0.0 (holgado)")
        self.lbl_mid.setText("0.7–1.0 (ajustado)")
        self.lbl_max.setText("≥ 1.0 (NO CUMPLE)")

    def set_heat_scale(self, unit_label: str = "") -> None:
        stops = [(0.0, (0.15, 0.35, 0.85, 1.0)), (0.5, (0.9, 0.85, 0.1, 1.0)),
                  (1.0, (0.9, 0.15, 0.15, 1.0))]
        css_stops = ", ".join(f"stop:{pos:.2f} {_css_rgba(c)}" for pos, c in stops)
        self.bar.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0, {css_stops}); "
            f"border: 1px solid #999; border-radius: 2px;"
        )
        self.title.setText("Escala de colores — concentración de esfuerzos (relativa)")
        self.lbl_min.setText("Mínimo")
        self.lbl_mid.setText(unit_label or "")
        self.lbl_max.setText("Máximo")
