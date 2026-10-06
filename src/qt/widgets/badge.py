"""Status badges for clear visual state indicators."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget


_BADGE_STYLES = {
    "ok": {
        "bg": "#ECFDF5",
        "fg": "#065F46",
        "border": "#A7F3D0",
    },
    "success": {
        "bg": "#ECFDF5",
        "fg": "#065F46",
        "border": "#A7F3D0",
    },
    "warning": {
        "bg": "#FFFBEB",
        "fg": "#92400E",
        "border": "#FDE68A",
    },
    "error": {
        "bg": "#FEF2F2",
        "fg": "#991B1B",
        "border": "#FECACA",
    },
    "danger": {
        "bg": "#FEF2F2",
        "fg": "#991B1B",
        "border": "#FECACA",
    },
    "info": {
        "bg": "#F0F9FF",
        "fg": "#075985",
        "border": "#BAE6FD",
    },
    "accent": {
        "bg": "#E4F0F5",
        "fg": "#0E7490",
        "border": "#B9DFEC",
    },
    "neutral": {
        "bg": "#F1F5F9",
        "fg": "#475569",
        "border": "#CBD5E1",
    },
    "new": {
        "bg": "#EFF6FF",
        "fg": "#1D4ED8",
        "border": "#BFDBFE",
    },
    "modified": {
        "bg": "#FFF7ED",
        "fg": "#C2410C",
        "border": "#FFEDD5",
    },
}


class StatusBadge(QLabel):
    """A pill badge showing a state with contextual coloring."""

    def __init__(
        self,
        text: str = "",
        variant: str = "neutral",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_variant(variant, text)

    def set_variant(self, variant: str, text: Optional[str] = None) -> None:
        if text is not None:
            self.setText(text)
        style = _BADGE_STYLES.get(variant, _BADGE_STYLES["neutral"])
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {style["bg"]};
                color: {style["fg"]};
                border: 1px solid {style["border"]};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 8pt;
                font-weight: 600;
            }}
            """
        )
