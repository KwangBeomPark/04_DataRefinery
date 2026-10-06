"""Application bootstrap and Qt runtime initialization."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from src.qt.theme import apply_light_palette, get_application_stylesheet


def get_asset_path(relative_path: str) -> str:
    """Find bundled assets in dev or PyInstaller environments."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return os.path.join(bundle_root, "assets", relative_path)
    return str(Path(__file__).resolve().parents[2] / "assets" / relative_path)


def create_or_get_app() -> QApplication:
    """Initialize or return the single QApplication instance with HiDPI and theme configured."""
    app = QApplication.instance()
    if app is None:
        # High DPI configuration
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)
        app.setApplicationName("Data Refinery")
        app.setOrganizationName("DataRefinery")

        # Set app font
        font = QFont("Segoe UI", 10)
        app.setFont(font)

        # Set app icon if available
        icon_path = get_asset_path("icons/icon.ico")
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))

    # Apply light palette and theme
    apply_light_palette(app)
    app.setStyleSheet(get_application_stylesheet())
    return app
