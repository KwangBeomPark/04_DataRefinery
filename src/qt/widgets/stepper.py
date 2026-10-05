"""Stepper widget indicating steps in multi-step wizards."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from src.ui_components import PALETTE


class StepperWidget(QFrame):
    """Horizontal step indicator (e.g. ① File selection -> ② Column roles -> ③ Review & Save)."""

    stepClicked = Signal(int)

    def __init__(self, steps: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._steps = steps
        self._current_step = 0

        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {PALETTE['surface']};
                border-bottom: 1px solid {PALETTE['border']};
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        self._labels: List[QLabel] = []

        for idx, title in enumerate(steps):
            lbl = QLabel(f"{idx + 1}. {title}")
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = lambda e, i=idx: self._on_step_clicked(i)
            self._labels.append(lbl)
            layout.addWidget(lbl)

            if idx < len(steps) - 1:
                arrow = QLabel("➔")
                arrow.setStyleSheet(f"color: {PALETTE['muted']}; font-weight: bold;")
                layout.addWidget(arrow)

        layout.addStretch()
        self._update_styles()

    def _on_step_clicked(self, step_idx: int) -> None:
        self.stepClicked.emit(step_idx)

    def set_current_step(self, step_idx: int) -> None:
        self._current_step = max(0, min(step_idx, len(self._steps) - 1))
        self._update_styles()

    def _update_styles(self) -> None:
        for idx, lbl in enumerate(self._labels):
            if idx == self._current_step:
                lbl.setStyleSheet(
                    f"""
                    QLabel {{
                        color: {PALETTE['accent']};
                        font-weight: 700;
                        font-size: 10pt;
                        border-bottom: 2px solid {PALETTE['accent']};
                        padding-bottom: 4px;
                    }}
                    """
                )
            elif idx < self._current_step:
                lbl.setStyleSheet(
                    f"""
                    QLabel {{
                        color: {PALETTE['navy']};
                        font-weight: 600;
                        font-size: 9.5pt;
                    }}
                    """
                )
            else:
                lbl.setStyleSheet(
                    f"""
                    QLabel {{
                        color: {PALETTE['muted']};
                        font-size: 9.5pt;
                    }}
                    """
                )
