"""Small reusable Tkinter components used by the application shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence

# A calm, high-contrast palette that remains readable with the clam theme.
# Shared so the app shell and the reusable widgets cannot drift apart.
#
# The screen reads as white cards floating on a cool slate page.  Depth comes
# from the surface/page contrast and hairline borders rather than from bevels,
# which is what keeps a Tk app from looking like Windows 98.
PALETTE = {
    # Surfaces
    "page_bg": "#EEF2F7",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F9FC",  # readonly fields, hover, zebra
    # Ink
    "navy": "#0F2A38",  # card and section titles
    "text": "#233A4A",
    "muted": "#6B8095",
    # Lines
    "border": "#E1E8F0",
    "border_strong": "#C7D3E0",
    # Brand
    "accent": "#0E7490",
    "accent_active": "#0A5A72",
    "accent_soft": "#E4F0F5",
    "accent_tint": "#F2F9FB",
    "selection": "#D6E9F1",
    # Semantics
    "derived": "#A9620A",
    "drop_target": "#0E7490",
    "danger": "#B42318",
    "success": "#15803D",
}


class UpdateMenu:
    """Settings anchored below one header button: language plus update controls.

    Language and updates are set once and then forgotten, so they do not earn
    permanent space next to the task tabs.
    """

    def __init__(
        self,
        root: tk.Misc,
        enabled_variable: tk.BooleanVar,
        on_check: Callable[[], None],
        on_download: Callable[[], None],
        on_preference_changed: Callable[[], None],
        language_variable: Optional[tk.StringVar] = None,
        languages: Sequence[str] = (),
        on_language_changed: Optional[Callable[[], None]] = None,
    ):
        self._menu = tk.Menu(root, tearoff=False)
        self._language_menu: Optional[tk.Menu] = None
        self._language_index: Optional[int] = None

        if language_variable is not None and languages:
            self._language_menu = tk.Menu(self._menu, tearoff=False)
            for name in languages:
                self._language_menu.add_radiobutton(
                    label=name,
                    value=name,
                    variable=language_variable,
                    command=on_language_changed,
                )
            self._menu.add_cascade(menu=self._language_menu)
            self._language_index = self._menu.index("end")
            self._menu.add_separator()

        self._menu.add_command(label="", state="disabled")
        self.status_index = self._menu.index("end")
        self._menu.add_separator()
        self._menu.add_command(command=on_check)
        self.check_index = self._menu.index("end")
        self._menu.add_command(command=on_download, state="disabled")
        self.download_index = self._menu.index("end")
        self._menu.add_separator()
        self._menu.add_checkbutton(variable=enabled_variable, command=on_preference_changed)
        self.enabled_index = self._menu.index("end")

    def set_texts(self, status: str, check: str, download: str, enabled: str) -> None:
        self._menu.entryconfigure(self.status_index, label=status)
        self._menu.entryconfigure(self.check_index, label=check)
        self._menu.entryconfigure(self.download_index, label=download)
        self._menu.entryconfigure(self.enabled_index, label=enabled)

    def set_language_label(self, label: str) -> None:
        if self._language_index is not None:
            self._menu.entryconfigure(self._language_index, label=label)

    def set_status(self, status: str) -> None:
        self._menu.entryconfigure(self.status_index, label=status)

    def set_download_enabled(self, enabled: bool) -> None:
        self._menu.entryconfigure(
            self.download_index,
            state="normal" if enabled else "disabled",
        )

    def show_below(self, button: ttk.Button) -> None:
        button.update_idletasks()
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height()
        try:
            self._menu.tk_popup(x, y)
        finally:
            self._menu.grab_release()
