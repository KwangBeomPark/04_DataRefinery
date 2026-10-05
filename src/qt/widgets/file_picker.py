"""File and folder picker widget with Browse button and reactive signals."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from src.qt.dialogs import choose_file_dialog, choose_folder_dialog


class FilePickerWidget(QWidget):
    """A composite line edit + browse button for selecting files or folders."""

    pathChanged = Signal(str)

    def __init__(
        self,
        mode: str = "file",  # "file" or "folder"
        filter_pattern: str = "All Files (*.*)",
        placeholder: str = "",
        browse_text: str = "찾아보기…",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._mode = mode
        self._filter_pattern = filter_pattern

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._line_edit = QLineEdit()
        self._line_edit.setPlaceholderText(placeholder)
        self._line_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._line_edit, stretch=1)

        self._browse_btn = QPushButton(browse_text)
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        layout.addWidget(self._browse_btn)

    def _on_text_changed(self, text: str) -> None:
        self.pathChanged.emit(text.strip())

    def _on_browse_clicked(self) -> None:
        if self._mode == "folder":
            chosen = choose_folder_dialog(self, "폴더 선택")
        else:
            chosen = choose_file_dialog(self, "파일 선택", self._filter_pattern)

        if chosen:
            self._line_edit.setText(chosen)

    def get_path(self) -> str:
        return self._line_edit.text().strip()

    def set_path(self, path: str) -> None:
        self._line_edit.setText(path)

    def set_read_only(self, read_only: bool) -> None:
        self._line_edit.setReadOnly(read_only)
