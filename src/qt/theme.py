"""Qt theme and stylesheet definition for Data Refinery v2.0 (PySide6).

Derived from the battle-tested PALETTE in src.ui_components to maintain visual
continuity while elevating the UI to modern Windows desktop standards.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from src.ui_components import PALETTE

# Global font configuration
FONT_FAMILY = "Segoe UI, Malgun Gothic, sans-serif"
FONT_SIZE_BASE = 10
FONT_SIZE_SM = 9
FONT_SIZE_XS = 8
FONT_SIZE_LG = 11
FONT_SIZE_XL = 13


def apply_light_palette(app: QApplication) -> None:
    """Enforce clean light palette regardless of Windows dark mode setting."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(PALETTE["page_bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(PALETTE["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(PALETTE["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PALETTE["surface_alt"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(PALETTE["surface"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(PALETTE["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(PALETTE["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(PALETTE["surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(PALETTE["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Link, QColor(PALETTE["accent"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(PALETTE["selection"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(PALETTE["navy"]))
    app.setPalette(palette)


def get_application_stylesheet() -> str:
    """Return the comprehensive QSS stylesheet for the application."""
    p = PALETTE
    return f"""
    * {{
        font-family: {FONT_FAMILY};
        font-size: {FONT_SIZE_BASE}pt;
        color: {p["text"]};
    }}

    QMainWindow, QDialog {{
        background-color: {p["page_bg"]};
    }}

    /* Card Frame */
    QFrame[card="true"] {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 8px;
    }}

    QFrame[card_header="true"] {{
        background-color: {p["surface_alt"]};
        border-bottom: 1px solid {p["border"]};
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }}

    /* Standard Label */
    QLabel {{
        background: transparent;
    }}
    QLabel[heading="true"] {{
        color: {p["navy"]};
        font-weight: 600;
        font-size: {FONT_SIZE_LG}pt;
    }}
    QLabel[subheading="true"] {{
        color: {p["navy"]};
        font-weight: 600;
        font-size: {FONT_SIZE_BASE}pt;
    }}
    QLabel[muted="true"] {{
        color: {p["muted"]};
        font-size: {FONT_SIZE_SM}pt;
    }}
    QLabel[caption="true"] {{
        color: {p["muted"]};
        font-size: {FONT_SIZE_XS}pt;
    }}

    /* LineEdit / Text Inputs */
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 6px;
        padding: 6px 10px;
        selection-background-color: {p["selection"]};
        selection-color: {p["navy"]};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {p["accent"]};
        background-color: {p["surface"]};
    }}
    QLineEdit:read-only {{
        background-color: {p["surface_alt"]};
        color: {p["muted"]};
    }}

    /* Buttons */
    QPushButton {{
        background-color: {p["surface"]};
        border: 1px solid {p["border_strong"]};
        border-radius: 6px;
        padding: 6px 14px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {p["surface_alt"]};
        border-color: {p["accent"]};
    }}
    QPushButton:pressed {{
        background-color: {p["accent_soft"]};
    }}
    QPushButton:disabled {{
        background-color: {p["surface_alt"]};
        border-color: {p["border"]};
        color: {p["muted"]};
    }}

    /* Primary / Accent Button */
    QPushButton[primary="true"] {{
        background-color: {p["accent"]};
        border: 1px solid {p["accent"]};
        color: #FFFFFF;
        font-weight: 600;
    }}
    QPushButton[primary="true"]:hover {{
        background-color: {p["accent_active"]};
        border-color: {p["accent_active"]};
        color: #FFFFFF;
    }}
    QPushButton[primary="true"]:pressed {{
        background-color: #08485B;
        border-color: #08485B;
        color: #FFFFFF;
    }}
    QPushButton[primary="true"]:disabled {{
        background-color: #A2CAD6;
        border-color: #A2CAD6;
        color: #FFFFFF;
    }}

    /* Danger Button */
    QPushButton[danger="true"] {{
        background-color: {p["surface"]};
        border: 1px solid {p["danger"]};
        color: {p["danger"]};
        font-weight: 500;
    }}
    QPushButton[danger="true"]:hover {{
        background-color: #FEE4E2;
    }}

    /* ComboBox */
    QComboBox {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 6px;
        padding: 5px 10px;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border-color: {p["accent"]};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border-left: none;
    }}
    QComboBox QAbstractItemView {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        selection-background-color: {p["accent_soft"]};
        selection-color: {p["navy"]};
        padding: 4px;
    }}

    /* Tables & Trees */
    QTableView, QTreeView, QListView {{
        background-color: {p["surface"]};
        border: 1px solid {p["border"]};
        border-radius: 6px;
        gridline-color: {p["border"]};
        selection-background-color: {p["selection"]};
        selection-color: {p["navy"]};
        alternate-background-color: {p["surface_alt"]};
    }}
    QHeaderView::section {{
        background-color: {p["surface_alt"]};
        color: {p["navy"]};
        font-weight: 600;
        font-size: {FONT_SIZE_SM}pt;
        border: none;
        border-bottom: 1px solid {p["border_strong"]};
        border-right: 1px solid {p["border"]};
        padding: 6px 8px;
    }}
    QHeaderView::section:last {{
        border-right: none;
    }}

    /* Tab Widget (Pill style) */
    QTabWidget::pane {{
        border: none;
        background: transparent;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {p["muted"]};
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 8px 16px;
        margin-right: 6px;
        font-weight: 500;
    }}
    QTabBar::tab:hover {{
        background-color: {p["surface"]};
        color: {p["text"]};
    }}
    QTabBar::tab:selected {{
        background-color: {p["surface"]};
        border: 1px solid {p["border_strong"]};
        color: {p["accent"]};
        font-weight: 600;
    }}

    /* Progress Bar */
    QProgressBar {{
        background-color: {p["border"]};
        border: none;
        border-radius: 4px;
        height: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {p["accent"]};
        border-radius: 4px;
    }}

    /* Checkbox & Radio */
    QCheckBox, QRadioButton {{
        spacing: 6px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {p["border_strong"]};
        border-radius: 3px;
        background-color: {p["surface"]};
    }}
    QRadioButton::indicator {{
        border-radius: 8px;
    }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border-color: {p["accent"]};
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background-color: {p["accent"]};
        border-color: {p["accent"]};
    }}

    /* Scrollbars */
    QScrollBar:vertical {{
        background: {p["surface_alt"]};
        width: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {p["border_strong"]};
        min-height: 20px;
        border-radius: 5px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p["muted"]};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: {p["surface_alt"]};
        height: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p["border_strong"]};
        min-width: 20px;
        border-radius: 5px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {p["muted"]};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    """
