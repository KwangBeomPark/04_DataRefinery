"""Small reusable Tkinter components used by the application shell."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class UpdateMenu:
    """Compact update controls anchored below a header button."""

    STATUS_INDEX = 0
    CHECK_INDEX = 2
    DOWNLOAD_INDEX = 3
    ENABLED_INDEX = 5

    def __init__(
        self,
        root: tk.Misc,
        enabled_variable: tk.BooleanVar,
        on_check: Callable[[], None],
        on_download: Callable[[], None],
        on_preference_changed: Callable[[], None],
    ):
        self._menu = tk.Menu(root, tearoff=False)
        self._menu.add_command(label="", state="disabled")
        self._menu.add_separator()
        self._menu.add_command(command=on_check)
        self._menu.add_command(command=on_download, state="disabled")
        self._menu.add_separator()
        self._menu.add_checkbutton(
            variable=enabled_variable,
            command=on_preference_changed,
        )

    def set_texts(self, status: str, check: str, download: str, enabled: str) -> None:
        self._menu.entryconfigure(self.STATUS_INDEX, label=status)
        self._menu.entryconfigure(self.CHECK_INDEX, label=check)
        self._menu.entryconfigure(self.DOWNLOAD_INDEX, label=download)
        self._menu.entryconfigure(self.ENABLED_INDEX, label=enabled)

    def set_status(self, status: str) -> None:
        self._menu.entryconfigure(self.STATUS_INDEX, label=status)

    def set_download_enabled(self, enabled: bool) -> None:
        self._menu.entryconfigure(
            self.DOWNLOAD_INDEX,
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
