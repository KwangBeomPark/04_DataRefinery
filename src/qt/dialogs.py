"""Dialog utilities wrapping Qt message boxes and file dialogs.

Decouples UI components from raw static Qt dialog calls for testability.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget


def info_dialog(parent: Optional[QWidget], title: str, message: str) -> None:
    """Show an informational message box."""
    QMessageBox.information(parent, title, message)


def warning_dialog(parent: Optional[QWidget], title: str, message: str) -> None:
    """Show a warning message box."""
    QMessageBox.warning(parent, title, message)


def error_dialog(parent: Optional[QWidget], title: str, message: str) -> None:
    """Show an error message box."""
    QMessageBox.critical(parent, title, message)


def confirm_dialog(parent: Optional[QWidget], title: str, message: str) -> bool:
    """Show a confirmation dialog with Yes/No buttons. Return True if confirmed."""
    reply = QMessageBox.question(
        parent,
        title,
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def choose_file_dialog(
    parent: Optional[QWidget],
    title: str,
    filter_pattern: str = "All Files (*.*)",
) -> str:
    """Show a file selection dialog and return the chosen file path string (empty if cancelled)."""
    file_path, _ = QFileDialog.getOpenFileName(parent, title, "", filter_pattern)
    return file_path or ""


def choose_folder_dialog(parent: Optional[QWidget], title: str) -> str:
    """Show a directory selection dialog and return the chosen folder path string (empty if cancelled)."""
    folder = QFileDialog.getExistingDirectory(parent, title)
    return folder or ""


def save_file_dialog(
    parent: Optional[QWidget],
    title: str,
    default_name: str = "",
    filter_pattern: str = "All Files (*.*)",
) -> str:
    """Show a save file dialog and return the chosen path (empty if cancelled)."""
    file_path, _ = QFileDialog.getSaveFileName(parent, title, default_name, filter_pattern)
    return file_path or ""
