"""
Tema visual de Vortex: paleta oscura de aspecto profesional/CAD (similar a
Autodesk Inventor) aplicada mediante QPalette + hoja de estilos (QSS), para
que la interfaz se vea ordenada y "de categoría" para un calculista, sin
depender de archivos de íconos externos (se usan símbolos Unicode).
"""
from __future__ import annotations

from PySide6 import QtGui
from PySide6.QtCore import Qt

BG_WINDOW = "#20242a"
BG_PANEL = "#262b32"
BG_INPUT = "#2f353d"
BG_INPUT_HOVER = "#374049"
BORDER = "#3a4048"
BORDER_LIGHT = "#454c56"
ACCENT = "#2f8fef"
ACCENT_HOVER = "#4aa3ff"
ACCENT_PRESSED = "#1f74cc"
TEXT = "#e4e8ed"
TEXT_DIM = "#96a1ad"
OK_GREEN = "#3ecf6e"
WARN_YELLOW = "#e8c547"
FAIL_RED = "#e5534b"


def apply_dark_theme(app) -> None:
    app.setStyle("Fusion")

    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.Window, QtGui.QColor(BG_WINDOW))
    palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(BG_INPUT))
    palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(BG_PANEL))
    palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(BG_PANEL))
    palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor(BG_INPUT))
    palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(TEXT))
    palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor(FAIL_RED))
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(ACCENT))
    palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor(TEXT_DIM))
    palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor(TEXT_DIM))
    palette.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor(TEXT_DIM))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)


STYLESHEET = f"""
QWidget {{
    background-color: {BG_WINDOW};
    color: {TEXT};
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 12px;
}}

QMainWindow::separator {{
    background: {BORDER};
    width: 3px;
    height: 3px;
}}

QToolBar {{
    background-color: {BG_PANEL};
    border-bottom: 1px solid {BORDER};
    padding: 4px;
    spacing: 4px;
}}
QToolBar QToolButton {{
    background-color: transparent;
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 6px 10px;
    font-weight: 600;
}}
QToolBar QToolButton:hover {{
    background-color: {BG_INPUT_HOVER};
    border: 1px solid {BORDER_LIGHT};
}}
QToolBar QToolButton:pressed {{
    background-color: {ACCENT_PRESSED};
}}
QToolBar QToolButton:checked {{
    background-color: {ACCENT};
    color: white;
}}
QToolBar::separator {{
    background: {BORDER};
    width: 1px;
    margin: 4px 6px;
}}

QStatusBar {{
    background-color: {BG_PANEL};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
}}

QScrollArea {{
    border: none;
}}

QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
    color: {ACCENT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {ACCENT};
}}

QLabel {{
    background: transparent;
    color: {TEXT};
}}

QPushButton {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 5px;
    padding: 7px 12px;
    color: {TEXT};
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {BG_INPUT_HOVER};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT_PRESSED};
    color: white;
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {BORDER};
}}
QPushButton#primaryAction {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: white;
}}
QPushButton#primaryAction:hover {{
    background-color: {ACCENT_HOVER};
}}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_LIGHT};
    border-radius: 4px;
    padding: 4px 6px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_LIGHT};
    selection-background-color: {ACCENT};
    color: {TEXT};
}}

QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER_LIGHT};
    border-radius: 3px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    top: -1px;
}}
QTabBar::tab {{
    background: {BG_PANEL};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 6px 14px;
    margin-right: 2px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background: {BG_INPUT};
    color: {ACCENT};
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}

QTableWidget {{
    background-color: {BG_INPUT};
    alternate-background-color: {BG_PANEL};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {ACCENT};
}}
QHeaderView::section {{
    background-color: {BG_PANEL};
    color: {TEXT};
    border: none;
    border-bottom: 2px solid {ACCENT};
    padding: 6px;
    font-weight: 700;
}}

QTextEdit, QTextBrowser {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: {TEXT};
}}

QSplitter::handle {{
    background: {BORDER};
}}

QScrollBar:vertical {{
    background: {BG_WINDOW};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_LIGHT};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QToolTip {{
    background-color: {BG_PANEL};
    color: {TEXT};
    border: 1px solid {ACCENT};
    padding: 4px;
}}
"""
