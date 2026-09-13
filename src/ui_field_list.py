"""A compact list widget whose rows carry their own remove button.

`tk.Listbox` cannot host child widgets, so a per-row "remove" affordance is
built on `ttk.Treeview` instead: the tree column holds the label and a narrow
second column holds the glyph.  One widget renders every row, so a 500-column
source file costs the same as a 5-column one.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable, List, Optional, Sequence

from src.ui_components import PALETTE

REMOVE_GLYPH = "✕"  # ✕
_REMOVE_COLUMN = "#1"


class PlaceholderEntry(ttk.Entry):
    """An Entry that shows grey guidance text while it is empty and unfocused.

    Tk has no placeholder support. The hint is a label laid over the field rather
    than text written into the variable, so `textvariable` always holds exactly
    what the user typed — including when something sets it programmatically.
    """

    def __init__(self, parent: tk.Misc, textvariable: tk.StringVar, placeholder: str = "", **kwargs):
        super().__init__(parent, textvariable=textvariable, **kwargs)
        self._variable = textvariable
        self._focused = False
        self._hint = tk.Label(
            self,
            text=placeholder,
            background=PALETTE["surface"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        )
        self._hint.bind("<Button-1>", lambda _event: self.focus_set())
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        # The variable belongs to the screen and outlives this field, so the
        # trace is detached on destroy; otherwise every later write would call
        # into a dead label and report an exception.
        self._trace: Optional[str] = textvariable.trace_add("write", lambda *_: self._sync_hint())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._sync_hint()

    def set_placeholder(self, placeholder: str) -> None:
        """Change the hint text, e.g. after a language switch."""
        self._hint.configure(text=placeholder)
        self._sync_hint()

    def placeholder_visible(self) -> bool:
        """True while the hint is drawn over the field."""
        return bool(self._hint.winfo_manager())

    def _on_focus_in(self, _event: tk.Event) -> None:
        self._focused = True
        self._sync_hint()

    def _on_focus_out(self, _event: tk.Event) -> None:
        self._focused = False
        self._sync_hint()

    def _on_destroy(self, _event: tk.Event) -> None:
        if self._trace is None:
            return
        try:
            self._variable.trace_remove("write", self._trace)
        except tk.TclError:
            pass  # the interpreter itself is going away
        self._trace = None

    def _sync_hint(self) -> None:
        if not self._hint.winfo_exists():
            return
        if self._hint.cget("text") and not self._variable.get() and not self._focused:
            self._hint.place(x=10, rely=0.5, anchor="w")
        else:
            self._hint.place_forget()


@dataclass(frozen=True)
class FieldListItem:
    """One row: `name` is the real column name, `label` is what the user sees."""

    name: str
    label: str
    tag: str = ""
    removable: bool = True
    hint: str = ""  # shown on hover; used for rule definitions that do not fit


class FieldListView(tk.Frame):
    """Scrollable rows with an optional per-row remove button and drag hooks."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_remove: Optional[Callable[[FieldListItem], None]] = None,
        on_activate: Optional[Callable[[FieldListItem], None]] = None,
        on_context: Optional[Callable[[FieldListItem, int, int], None]] = None,
        height: int = 6,
        show_remove: bool = True,
        empty_hint: str = "",
    ):
        super().__init__(
            parent,
            background=PALETTE["surface"],
            highlightthickness=2,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["border"],
            bd=0,
        )
        self._on_remove = on_remove
        self._on_activate = on_activate
        self._on_context = on_context
        self._show_remove = show_remove
        self._empty_hint = empty_hint
        self._items: List[FieldListItem] = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            self,
            columns=("remove",),
            show="tree",
            selectmode="browse",
            height=height,
            style="FieldList.Treeview",
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.column("#0", anchor="w", stretch=True, minwidth=60, width=120)
        self.tree.column(
            "remove",
            anchor="center",
            stretch=False,
            width=26 if show_remove else 0,
            minwidth=0,
        )

        # A bare ttk scrollbar asks for more height than one row (two arrows
        # plus a thumb) and would stretch a short list.  Parking it in a holder
        # that does not propagate its size keeps the list exactly `height` rows
        # tall whether or not the scrollbar is showing.
        self._scroll_holder = tk.Frame(self, background=PALETTE["surface"], height=1)
        self._scroll = ttk.Scrollbar(self._scroll_holder, orient="vertical", command=self.tree.yview)
        self._scroll_holder.configure(width=self._scroll.winfo_reqwidth())
        self._scroll_holder.pack_propagate(False)  # the scrollbar inside is packed
        self._scroll.pack(fill="both", expand=True)
        self._scroll_holder.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=self._on_scroll)

        self.tree.tag_configure("derived", foreground=PALETTE["derived"])
        self.tree.tag_configure("month", foreground=PALETTE["accent"])
        self.tree.tag_configure("hint", foreground=PALETTE["muted"])

        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<Double-Button-1>", self._on_double_click, add="+")
        self.tree.bind("<Return>", self._on_return, add="+")
        self.tree.bind("<Delete>", self._on_delete, add="+")
        self.tree.bind("<BackSpace>", self._on_delete, add="+")
        self.tree.bind("<Button-3>", self._on_context_click, add="+")
        self.tree.bind("<Motion>", self._on_hover, add="+")
        self.tree.bind("<Leave>", lambda _e: self._hide_tip(), add="+")
        self.tree.bind("<ButtonPress-1>", lambda _e: self._hide_tip(), add="+")
        self.tree.bind("<Destroy>", lambda _e: self._hide_tip(), add="+")

        self._tip: Optional[tk.Toplevel] = None
        self._tip_after: Optional[str] = None
        self._tip_index: Optional[int] = None
        self._draggable = False
        self._cursor = ""

        self._render()

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    def set_items(self, items: Sequence[FieldListItem]) -> None:
        keep = self.selected_name()
        self._items = list(items)
        self._render()
        if keep:
            self.select_name(keep)

    def set_empty_hint(self, hint: str) -> None:
        """Update the placeholder shown when there is nothing in the list."""
        if hint == self._empty_hint:
            return
        self._empty_hint = hint
        if not self._items:
            self._render()

    def items(self) -> List[FieldListItem]:
        return list(self._items)

    def names(self) -> List[str]:
        return [item.name for item in self._items]

    def size(self) -> int:
        return len(self._items)

    def item_at_index(self, index: int) -> Optional[FieldListItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self._items:
            if self._empty_hint:
                self.tree.insert("", "end", iid="hint", text=f"  {self._empty_hint}", tags=("hint",))
            return
        for index, item in enumerate(self._items):
            glyph = REMOVE_GLYPH if (self._show_remove and item.removable) else ""
            self.tree.insert(
                "",
                "end",
                iid=self._iid(index),
                text=f" {item.label}",
                values=(glyph,),
                tags=(item.tag,) if item.tag else (),
            )

    @staticmethod
    def _iid(index: int) -> str:
        return f"row{index}"

    @staticmethod
    def _index_of_iid(iid: str) -> Optional[int]:
        if not iid.startswith("row"):
            return None
        try:
            return int(iid[3:])
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def selected_index(self) -> Optional[int]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._index_of_iid(selection[0])

    def selected_name(self) -> Optional[str]:
        index = self.selected_index()
        return self._items[index].name if index is not None and index < len(self._items) else None

    def select_index(self, index: int) -> None:
        if 0 <= index < len(self._items):
            iid = self._iid(index)
            self.tree.selection_set(iid)
            self.tree.focus(iid)

    def select_name(self, name: str) -> None:
        for index, item in enumerate(self._items):
            if item.name == name:
                self.select_index(index)
                return

    def reveal(self, name: str) -> None:
        """Select a row and scroll it into view, so a new entry is not missed."""
        for index, item in enumerate(self._items):
            if item.name == name:
                self.select_index(index)
                self.tree.see(self._iid(index))
                return

    # ------------------------------------------------------------------
    # Hit testing (used by the drag controller)
    # ------------------------------------------------------------------
    def index_at(self, y: int) -> Optional[int]:
        """Index of the row under a y offset relative to this widget's tree."""
        iid = self.tree.identify_row(y)
        return self._index_of_iid(iid) if iid else None

    def drop_index_at(self, y: int) -> int:
        """Insertion point for a drop at a y offset, splitting rows at their midpoint."""
        index = self.index_at(y)
        if index is None:
            return len(self._items)
        bbox = self.tree.bbox(self._iid(index))
        if bbox and y > bbox[1] + bbox[3] / 2:
            return index + 1
        return index

    def item_at(self, y: int) -> Optional[FieldListItem]:
        index = self.index_at(y)
        return self.item_at_index(index) if index is not None else None

    def drag_widget(self) -> ttk.Treeview:
        return self.tree

    def set_draggable(self, draggable: bool) -> None:
        """Rows of a drag source get a hand cursor, which is the only hint Tk
        can give that an item can be picked up and carried somewhere."""
        self._draggable = draggable

    def set_drop_active(self, active: bool) -> None:
        colour = PALETTE["drop_target"] if active else PALETTE["border"]
        self.configure(highlightbackground=colour, highlightcolor=colour)

    # ------------------------------------------------------------------
    # Hover hint (rule definitions and truncated names)
    # ------------------------------------------------------------------
    def _on_hover(self, event: tk.Event) -> None:
        index = self.index_at(event.y)
        if self._draggable:
            # <Motion> fires continuously, so only touch the widget on a change.
            cursor = "hand2" if index is not None else ""
            if cursor != self._cursor:
                self._cursor = cursor
                self.tree.configure(cursor=cursor)
        if index == self._tip_index:
            return
        self._hide_tip()
        self._tip_index = index
        item = self.item_at_index(index) if index is not None else None
        if item is None or not item.hint:
            return
        self._tip_after = self.after(
            500, lambda text=item.hint, x=event.x_root, y=event.y_root: self._show_tip(text, x, y)
        )

    def _show_tip(self, text: str, x_root: int, y_root: int) -> None:
        self._tip_after = None
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            tip,
            text=text,
            background=PALETTE["navy"],
            foreground="#FFFFFF",
            font=("Segoe UI", 8),
            padx=6,
            pady=3,
            justify="left",
        ).pack()
        tip.geometry(f"+{x_root + 14}+{y_root + 18}")
        self._tip = tip

    def _hide_tip(self) -> None:
        self._tip_index = None
        if self._tip_after is not None:
            try:
                self.after_cancel(self._tip_after)
            except (tk.TclError, ValueError):
                pass
            self._tip_after = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _on_scroll(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scroll_holder.grid_remove()
        else:
            self._scroll_holder.grid()
        self._scroll.set(first, last)

    def _on_click(self, event: tk.Event):
        if not self._show_remove or self._on_remove is None:
            return None
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != _REMOVE_COLUMN:
            return None
        index = self.index_at(event.y)
        if index is None or not self._items[index].removable:
            return None
        # "break" stops the selection change *and* the drag controller's press
        # handler, so clicking ✕ never starts a drag.
        self._hide_tip()
        self._on_remove(self._items[index])
        return "break"

    def _on_double_click(self, event: tk.Event):
        if self._on_activate is None:
            return None
        index = self.index_at(event.y)
        if index is None:
            return None
        self._on_activate(self._items[index])
        return "break"

    def _on_return(self, event: tk.Event):
        index = self.selected_index()
        if index is not None and self._on_activate is not None:
            self._on_activate(self._items[index])
            return "break"
        return None

    def _on_context_click(self, event: tk.Event):
        """Right-click acts on the row under the cursor, selecting it first so the
        user can see which item the menu belongs to."""
        if self._on_context is None:
            return None
        index = self.index_at(event.y)
        if index is None:
            return None
        self._hide_tip()
        self.select_index(index)
        self._on_context(self._items[index], event.x_root, event.y_root)
        return "break"

    def _on_delete(self, event: tk.Event):
        """Delete/Backspace does what the row's ✕ does, for keyboard users."""
        index = self.selected_index()
        if index is None or self._on_remove is None:
            return None
        item = self._items[index]
        if not item.removable:
            return None
        self._hide_tip()
        self._on_remove(item)
        return "break"
