"""Tag/chip editor widget for keyword filters."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

from src.ui_components import PALETTE


class _Chip(QFrame):
    """An individual removable keyword chip."""

    removed = Signal(str)

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._text = text

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(4)

        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {PALETTE['navy']}; font-weight: 500; font-size: 8.5pt;")
        layout.addWidget(lbl)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(16, 16)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {PALETTE['muted']};
                font-size: 8pt;
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover {{
                color: {PALETTE['danger']};
            }}
            """
        )
        btn_close.clicked.connect(lambda: self.removed.emit(self._text))
        layout.addWidget(btn_close)

        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {PALETTE['surface_alt']};
                border: 1px solid {PALETTE['border_strong']};
                border-radius: 4px;
            }}
            """
        )


class ChipEditorWidget(QWidget):
    """An interactive chip editor that converts entered tokens into chips."""

    chipsChanged = Signal(list)

    def __init__(
        self,
        placeholder: str = "키워드 입력 후 Enter (쉼표 구분 가능)",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._chips: List[str] = []

        main_layout = QVBoxLayout(self) if False else QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        # Container for chips
        self._chips_container = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_container)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(4)

        # Input line edit
        self._input = QLineEdit()
        self._input.setPlaceholderText(placeholder)
        self._input.returnPressed.connect(self._add_from_input)
        self._input.textChanged.connect(self._check_delimiter)

        main_layout.addWidget(self._chips_container)
        main_layout.addWidget(self._input, stretch=1)

    def _check_delimiter(self, text: str) -> None:
        if "," in text:
            parts = text.split(",")
            for part in parts[:-1]:
                self.add_chip(part)
            self._input.setText(parts[-1])

    def _add_from_input(self) -> None:
        text = self._input.text().strip()
        if text:
            self.add_chip(text)
            self._input.clear()

    def add_chip(self, text: str) -> None:
        norm = text.strip()
        if not norm or norm in self._chips:
            return
        self._chips.append(norm)

        chip = _Chip(norm)
        chip.removed.connect(self.remove_chip)
        self._chips_layout.addWidget(chip)
        self.chipsChanged.emit(list(self._chips))

    def remove_chip(self, text: str) -> None:
        if text in self._chips:
            self._chips.remove(text)
            # Rebuild chips layout
            while self._chips_layout.count() > 0:
                item = self._chips_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            for c in self._chips:
                chip = _Chip(c)
                chip.removed.connect(self.remove_chip)
                self._chips_layout.addWidget(chip)
            self.chipsChanged.emit(list(self._chips))

    def get_chips(self) -> List[str]:
        return list(self._chips)

    def set_chips(self, chips: List[str]) -> None:
        self.clear()
        for c in chips:
            self.add_chip(c)

    def clear(self) -> None:
        self._chips.clear()
        while self._chips_layout.count() > 0:
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._input.clear()
        self.chipsChanged.emit([])
