"""Drag-and-drop between field lists, implemented with plain Tkinter.

Tk has no cross-widget drag protocol, and pulling in `tkinterdnd2` would add a
native binary to the PyInstaller bundle for what amounts to three mouse
bindings.  Instead the press/motion/release triple is tracked manually, a small
borderless `Toplevel` follows the cursor as a drag ghost, and the drop target is
resolved with `winfo_containing`.

The ghost is drawn offset from the pointer so it never sits under the cursor and
shadows that hit test.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from src.ui_components import PALETTE
from src.ui_field_list import FieldListItem, FieldListView

DRAG_THRESHOLD_PX = 5
_GHOST_OFFSET = (16, 12)


@dataclass(frozen=True)
class DragPayload:
    source: FieldListView
    item: FieldListItem
    index: int


@dataclass
class _Registration:
    view: FieldListView
    draggable: bool
    accepts: Optional[Callable[[DragPayload], bool]]
    on_drop: Optional[Callable[[DragPayload, int], None]]


class DragDropController:
    """Wires a set of `FieldListView`s together as drag sources and drop targets."""

    def __init__(self, root: tk.Misc):
        self._root = root
        self._registrations: List[_Registration] = []
        self._press: Optional[tuple] = None
        self._payload: Optional[DragPayload] = None
        self._ghost: Optional[tk.Toplevel] = None
        self._hover: Optional[_Registration] = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        view: FieldListView,
        *,
        draggable: bool = False,
        accepts: Optional[Callable[[DragPayload], bool]] = None,
        on_drop: Optional[Callable[[DragPayload, int], None]] = None,
    ) -> None:
        registration = _Registration(view=view, draggable=draggable, accepts=accepts, on_drop=on_drop)
        self._registrations.append(registration)

        if draggable:
            if hasattr(view, "set_draggable"):
                view.set_draggable(True)
            widget = view.drag_widget()
            # add="+" keeps the view's own ✕ handler, which returns "break" and
            # therefore suppresses the press below when ✕ is what was clicked.
            widget.bind("<ButtonPress-1>", lambda e, v=view: self._on_press(e, v), add="+")
            widget.bind("<B1-Motion>", self._on_motion, add="+")
            widget.bind("<ButtonRelease-1>", self._on_release, add="+")

    # ------------------------------------------------------------------
    # Drag lifecycle
    # ------------------------------------------------------------------
    def _on_press(self, event: tk.Event, view: FieldListView) -> None:
        index = view.index_at(event.y)
        if index is None:
            self._press = None
            return
        self._press = (event.x_root, event.y_root, view, index)

    def _on_motion(self, event: tk.Event) -> None:
        if self._press is None:
            return

        if self._payload is None:
            start_x, start_y, view, index = self._press
            if max(abs(event.x_root - start_x), abs(event.y_root - start_y)) < DRAG_THRESHOLD_PX:
                return  # still a click, not a drag — leaves double-click intact
            item = view.item_at_index(index)
            if item is None:
                self._press = None
                return
            self._payload = DragPayload(source=view, item=item, index=index)
            self._show_ghost(item.label)

        self._move_ghost(event.x_root, event.y_root)
        self._update_hover(event.x_root, event.y_root)

    def _on_release(self, event: tk.Event) -> None:
        payload = self._payload
        target = self._hover
        self._press = None
        self._payload = None
        self._clear_hover()
        self._hide_ghost()

        if payload is None or target is None or target.on_drop is None:
            return

        tree = target.view.drag_widget()
        drop_y = event.y_root - tree.winfo_rooty()
        target.on_drop(payload, target.view.drop_index_at(drop_y))

    def cancel(self) -> None:
        """Abort an in-flight drag without dropping (e.g. on Escape)."""
        self._press = None
        self._payload = None
        self._clear_hover()
        self._hide_ghost()

    # ------------------------------------------------------------------
    # Hover feedback
    # ------------------------------------------------------------------
    def _update_hover(self, x_root: int, y_root: int) -> None:
        target = self._target_at(x_root, y_root)
        if target is self._hover:
            return
        self._clear_hover()
        if target is not None:
            target.view.set_drop_active(True)
            self._hover = target

    def _clear_hover(self) -> None:
        if self._hover is not None:
            self._hover.view.set_drop_active(False)
            self._hover = None

    def _target_at(self, x_root: int, y_root: int) -> Optional[_Registration]:
        try:
            widget: Any = self._root.winfo_containing(x_root, y_root)
        except (KeyError, tk.TclError):
            return None

        while widget is not None:
            for registration in self._registrations:
                if registration.view is widget and registration.on_drop is not None:
                    if self._payload is None:
                        return None
                    if registration.accepts is not None and not registration.accepts(self._payload):
                        return None
                    return registration
            widget = getattr(widget, "master", None)
        return None

    # ------------------------------------------------------------------
    # Drag ghost
    # ------------------------------------------------------------------
    def _show_ghost(self, label: str) -> None:
        self._hide_ghost()
        ghost = tk.Toplevel(self._root)
        ghost.overrideredirect(True)
        try:
            ghost.attributes("-topmost", True)
            ghost.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        tk.Label(
            ghost,
            text=f" {label} ",
            background=PALETTE["accent"],
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 9),
            padx=6,
            pady=3,
        ).pack()
        self._ghost = ghost

    def _move_ghost(self, x_root: int, y_root: int) -> None:
        if self._ghost is not None:
            self._ghost.geometry(f"+{x_root + _GHOST_OFFSET[0]}+{y_root + _GHOST_OFFSET[1]}")

    def _hide_ghost(self) -> None:
        if self._ghost is not None:
            try:
                self._ghost.destroy()
            except tk.TclError:
                pass
            self._ghost = None
