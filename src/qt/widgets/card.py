"""Card container widget for clean, structured desktop surfaces."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)



class CardWidget(QFrame):
    """A floating card with white background, subtle border, and optional header."""

    def __init__(
        self,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setProperty("card", "true")

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(16, 14, 16, 14)
        self._main_layout.setSpacing(12)

        self._title_label: Optional[QLabel] = None
        self._subtitle_label: Optional[QLabel] = None
        self._header_layout: Optional[QHBoxLayout] = None

        if title or subtitle:
            self._header_layout = QHBoxLayout()
            self._header_layout.setContentsMargins(0, 0, 0, 0)
            self._header_layout.setSpacing(8)

            text_layout = QVBoxLayout()
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(2)

            if title:
                self._title_label = QLabel(title)
                self._title_label.setProperty("subheading", "true")
                text_layout.addWidget(self._title_label)

            if subtitle:
                self._subtitle_label = QLabel(subtitle)
                self._subtitle_label.setProperty("muted", "true")
                text_layout.addWidget(self._subtitle_label)

            self._header_layout.addLayout(text_layout)
            self._header_layout.addStretch()
            self._main_layout.addLayout(self._header_layout)

        # Body container
        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(10)
        self._main_layout.addWidget(self._content_widget)

    @property
    def content_layout(self) -> QVBoxLayout:
        """Access the layout where inner widgets should be added."""
        return self._content_layout

    def add_header_action(self, widget: QWidget) -> None:
        """Add an action widget (e.g. button) to the right side of the card header."""
        if self._header_layout is not None:
            self._header_layout.addWidget(widget)

    def set_title(self, title: str) -> None:
        if self._title_label:
            self._title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        if self._subtitle_label:
            self._subtitle_label.setText(subtitle)
