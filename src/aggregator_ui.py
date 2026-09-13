"""Tkinter UI frame component for the Data Aggregator tab.

The screen is a pivot field list: source columns live in the left pools, and the
user moves them into the row-group and values areas on the right by
double-clicking or dragging (filters come from a dialog).  All of the truth
lives in `AggregatorFieldState`; the widgets are only a rendering of it.
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable, Dict, List, Optional

from src.aggregator_dialogs import (
    PreviewDialog,
    ask_column_group,
    ask_constant_column,
    ask_derived_formula,
    ask_filter,
    describe_filter,
)
from src.aggregator_fields import (
    CONSTANT,
    DIMENSION,
    MEASURE,
    ROWS,
    VALUES,
    AggregatorFieldState,
    PoolField,
)
from src.background_jobs import JobCallbacks
from src.data_aggregator import (
    AggregationCancelledError,
    AggregationResult,
    AggregationSpec,
    ColumnGroupRule,
    DerivedFormulaRule,
    FilterCondition,
    aggregate_dataset,
    default_output_path,
    estimate_result_rows,
    format_preview_rows,
    inspect_dataset_schema,
    preview_aggregation,
)
from src.file_reveal import open_containing_folder, open_file
from src.session_memory import recall, remember
from src.preset_manager import (
    AggregationPreset,
    delete_preset,
    export_preset_file,
    import_preset_file,
    list_presets,
    load_preset,
    preset_exists,
    save_preset,
    validate_preset_against_columns,
)
from src.ui_dnd import DragDropController, DragPayload
from src.ui_field_list import FieldListItem, FieldListView, PlaceholderEntry

GROUP_GLYPH = "∑"
FORMULA_GLYPH = "%"
CONSTANT_GLYPH = "✎"
_OUTPUT_FORMATS = ("Excel (.xlsx)", "CSV (.csv)")
_ROW_GAP = 3  # vertical gap between the single-line setup fields
_PREVIEW_ROWS = 2000
# Background job that counts the rows a full run would produce; one per preview dialog.
_ROW_COUNT_JOB = "preview-row-count"
# How each value column is reduced. Sum is the default and carries no marker,
# so only a deliberate choice shows up in the list.
_FUNCTION_KEYS = {
    "sum": "agg_fn_sum",
    "mean": "agg_fn_mean",
    "count": "agg_fn_count",
    "min": "agg_fn_min",
    "max": "agg_fn_max",
}
_FUNCTION_GLYPHS = {"mean": "x̄", "count": "#", "min": "↓", "max": "↑"}
_FORMAT_LABEL_KEYS = {
    "percent": "agg_dlg_format_percent",
    "ratio": "agg_dlg_format_ratio",
    "number": "agg_dlg_format_number",
}


class AggregatorTabFrame(ttk.Frame):
    """Encapsulates the entire Data Aggregator tab UI and background execution."""

    def __init__(self, parent: tk.Widget, app: Any, **kwargs):
        super().__init__(parent, style="App.TFrame", **kwargs)
        self.app = app
        self._schema = None
        self._cancel_event: Optional[threading.Event] = None
        self._is_aggregating = False
        self._row_count_seq = 0

        self.state = AggregatorFieldState()
        self.dnd = DragDropController(self.winfo_toplevel())

        # State variables
        self.filepath_var = tk.StringVar()
        self.preset_name_var = tk.StringVar()
        self.output_format_var = tk.StringVar(value=_OUTPUT_FORMATS[0])
        self.output_dir_var = tk.StringVar()
        self.output_name_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self._last_output_path: Optional[str] = None

        self._translated: List[tuple] = []  # (widget, option, ui_key)
        self._list_hints: Dict[FieldListView, str] = {}
        # The two pane cards and, per card row, the frames whose height both
        # panes must agree on (see `_align_pane_rows`).
        self._pane_cards: List[ttk.LabelFrame] = []
        self._shared_rows: Dict[int, List[tk.Misc]] = {0: [], 3: []}

        self._build_ui()
        self._build_function_menu()
        self._register_drag_and_drop()
        self.refresh_presets_dropdown()
        self.refresh_views()

    # -------------------------------------------------------------
    # Translation helpers
    # -------------------------------------------------------------
    def _ui(self, key: str) -> str:
        return self.app._ui(key)

    def _track(self, widget: tk.Misc, key: str, option: str = "text") -> tk.Misc:
        """Apply a translated string now and remember it for language switches."""
        widget.configure(**{option: self._ui(key)})
        self._translated.append((widget, option, key))
        return widget

    def apply_language(self) -> None:
        """Re-apply every translated string after the user switches language."""
        for widget, option, key in self._translated:
            try:
                if option == "placeholder":
                    widget.set_placeholder(self._ui(key))
                else:
                    widget.configure(**{option: self._ui(key)})
            except tk.TclError:
                pass
        self._translate_preset_menu()
        self._translate_function_menu()
        self.refresh_views()

    # -------------------------------------------------------------
    # Layout
    # -------------------------------------------------------------
    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        self._build_setup_card()
        self._build_panes()
        self._build_bottom_bar()
        self.search_var.trace_add("write", lambda *_: self._refresh_pools())

    def _build_setup_card(self) -> None:
        """Source, destination and presets in one card: everything you set up
        before touching the field lists lives here, in the order you do it."""
        card = ttk.Frame(self, style="Card.TFrame", padding=(12, 8))
        card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        # One grid for all three rows so the labels, fields and buttons line up
        # in columns instead of drifting row by row.
        card.columnconfigure(1, weight=1)

        rows = (
            ("agg_file_label", self.filepath_var, True, "agg_browse", self.browse_file),
            ("agg_output_dir", self.output_dir_var, False, "agg_browse_folder", self.browse_output_dir),
            ("agg_output_name", self.output_name_var, False, None, None),
        )
        entries = []
        for index, (label_key, variable, readonly, button_key, command) in enumerate(rows):
            # Three single-line fields have no reason to be airy; they sit at the
            # same density as the search box and filter strip below them.
            pad_y = (0, 0) if index == 0 else (_ROW_GAP, 0)
            self._track(ttk.Label(card, style="Field.TLabel"), label_key).grid(
                row=index, column=0, sticky="w", padx=(0, 10), pady=pad_y
            )
            entry = ttk.Entry(
                card,
                textvariable=variable,
                state="readonly" if readonly else "normal",
                style="Compact.TEntry",
            )
            entry.grid(row=index, column=1, sticky="ew", pady=pad_y)
            entries.append(entry)
            if button_key:
                self._track(
                    ttk.Button(card, command=command, style="Compact.TButton", width=11), button_key
                ).grid(row=index, column=2, sticky="ew", padx=(8, 0), pady=pad_y)

        self.ent_file, self.ent_out_dir, self.ent_out_name = entries

        # Each "open the result" button sits on the line it acts on: the folder
        # button beside the folder, the file button beside the file name.
        self.btn_open_folder = self._track(
            ttk.Button(
                card, command=self.open_output_folder, style="Compact.TButton", width=11, state="disabled"
            ),
            "agg_open_folder",
        )
        self.btn_open_folder.grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=(_ROW_GAP, 0))

        # The format belongs with the file name whose extension it decides.
        self.combo_out_fmt = ttk.Combobox(
            card,
            textvariable=self.output_format_var,
            values=list(_OUTPUT_FORMATS),
            state="readonly",
            width=11,
            style="Compact.TCombobox",
        )
        self.combo_out_fmt.grid(row=2, column=2, sticky="ew", padx=(8, 0), pady=(_ROW_GAP, 0))

        self.btn_open_file = self._track(
            ttk.Button(
                card, command=self.open_output_file, style="Compact.TButton", width=11, state="disabled"
            ),
            "agg_open_file",
        )
        self.btn_open_file.grid(row=2, column=3, sticky="ew", padx=(6, 0), pady=(_ROW_GAP, 0))

        # Presets are a different job from "where does this run read and write",
        # so they sit apart at the far right.
        self._track(ttk.Label(card, style="Field.TLabel"), "agg_preset_label").grid(
            row=0, column=4, sticky="e", padx=(24, 8)
        )
        self.combo_presets = ttk.Combobox(
            card, textvariable=self.preset_name_var, state="readonly", width=16, style="Compact.TCombobox"
        )
        self.combo_presets.grid(row=0, column=5, sticky="ew")
        self.btn_preset_menu = self._track(
            ttk.Button(card, command=self._show_preset_menu, style="Compact.TButton", width=11),
            "agg_preset_menu",
        )
        self.btn_preset_menu.grid(row=0, column=6, sticky="ew", padx=(8, 0))

        self._preset_menu = tk.Menu(self, tearoff=False)
        self._preset_menu_items = (
            ("agg_preset_load", self.apply_selected_preset),
            ("agg_preset_save", self.save_current_as_preset),
            ("agg_preset_export", self.export_preset),
            ("agg_preset_import", self.import_preset),
            ("agg_preset_delete", self.delete_current_preset),
        )
        for _key, command in self._preset_menu_items:
            self._preset_menu.add_command(command=command)
        self._translate_preset_menu()

    def _build_function_menu(self) -> None:
        """Right-click a value to choose how it is reduced; sum stays the default."""
        self._function_menu = tk.Menu(self, tearoff=False)
        self._function_var = tk.StringVar(value="sum")
        self._function_target: Optional[str] = None
        for name in _FUNCTION_KEYS:
            self._function_menu.add_radiobutton(
                value=name,
                variable=self._function_var,
                command=lambda n=name: self._apply_function_choice(n),
            )
        self._translate_function_menu()

    def _translate_function_menu(self) -> None:
        for index, key in enumerate(_FUNCTION_KEYS.values()):
            self._function_menu.entryconfigure(index, label=self._ui(key))

    def _show_function_menu(self, item: FieldListItem, x_root: int, y_root: int) -> None:
        if self.state.is_derived(item.name):
            return  # a rule column is computed, not aggregated
        self._function_target = item.name
        self._function_var.set(self.state.measure_function(item.name))
        try:
            self._function_menu.tk_popup(x_root, y_root)
        finally:
            self._function_menu.grab_release()

    def _apply_function_choice(self, function: str) -> None:
        name = self._function_target
        if not name:
            return
        self._mutate(lambda: self.state.set_measure_function(name, function))

    def _translate_preset_menu(self) -> None:
        """Menu entries are not widgets, so `_track` cannot reach them."""
        for index, (key, _command) in enumerate(self._preset_menu_items):
            self._preset_menu.entryconfigure(index, label=self._ui(key))

    def _show_preset_menu(self) -> None:
        """Pop the preset actions under their button."""
        self.btn_preset_menu.update_idletasks()
        x = self.btn_preset_menu.winfo_rootx()
        y = self.btn_preset_menu.winfo_rooty() + self.btn_preset_menu.winfo_height()
        try:
            self._preset_menu.tk_popup(x, y)
        finally:
            self._preset_menu.grab_release()

    def _build_panes(self) -> None:
        pane_frame = ttk.Frame(self, style="App.TFrame")
        pane_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.rowconfigure(1, weight=1)
        pane_frame.columnconfigure(0, weight=5)
        pane_frame.columnconfigure(1, weight=6)
        pane_frame.rowconfigure(0, weight=1)

        self._build_source_pane(pane_frame)
        self._build_rules_pane(pane_frame)
        self._align_pane_rows()

    def _align_pane_rows(self) -> None:
        """Reserve the same height for the utility and action rows of both panes.

        The panes put different things in those rows (a search box against a
        caption plus a one-row list; rule buttons against nothing), and the
        pixel heights follow the font and DPI scaling, so each row is sized to
        the tallest content on either side once the widgets exist.  That is
        what keeps the four field lists on one baseline.
        """
        self.update_idletasks()
        for row, frames in self._shared_rows.items():
            height = max(frame.winfo_reqheight() for frame in frames)
            for card in self._pane_cards:
                card.rowconfigure(row, minsize=height)

    def _build_source_pane(self, parent: tk.Widget) -> None:
        card = ttk.LabelFrame(parent, style="Card.TLabelframe", padding=(10, 8))
        self._track(card, "agg_source_card")
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        card.rowconfigure(2, weight=1)
        self._pane_cards.append(card)

        # A file with hundreds of columns is unusable without a filter box.
        search_row = ttk.Frame(card, style="Card.TFrame")
        search_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        search_row.columnconfigure(0, weight=1)
        self._shared_rows[0].append(search_row)
        self.ent_search = PlaceholderEntry(
            search_row, textvariable=self.search_var, placeholder=self._ui("agg_search_placeholder")
        )
        self.ent_search.grid(row=0, column=0, sticky="ew")
        self._translated.append((self.ent_search, "placeholder", "agg_search_placeholder"))
        self.btn_search_clear = self._track(
            ttk.Button(search_row, command=self.clear_search, style="Compact.TButton", width=12),
            "agg_search_clear",
        )
        self.btn_search_clear.grid(row=0, column=1, padx=(6, 0))

        # --- Dimensions column -------------------------------------------------
        self._track(ttk.Label(card, style="Field.TLabel"), "agg_dim_header").grid(
            row=1, column=0, sticky="w", pady=(0, 2)
        )
        self.view_dimensions = FieldListView(
            card,
            on_activate=lambda item: self._place_as_row(item.name),
            on_remove=lambda item: self._remove_constant_column(item.name),
        )
        self.view_dimensions.grid(row=2, column=0, sticky="nsew", padx=(0, 4))
        self._list_hints[self.view_dimensions] = "agg_hint_dimensions"

        # The gap above the buttons is packed inside the frame so that the
        # frame's requested height is the whole row `_align_pane_rows` reads.
        constant_buttons = ttk.Frame(card, style="Card.TFrame")
        constant_buttons.grid(row=3, column=0, sticky="ew", padx=(0, 4))
        self._shared_rows[3].append(constant_buttons)
        self.btn_constant = self._track(
            ttk.Button(constant_buttons, command=self.add_constant_column, style="Compact.TButton"),
            "agg_btn_constant",
        )
        self.btn_constant.pack(side="left", expand=True, fill="x", padx=1, pady=(4, 0))

        # --- Measures column ---------------------------------------------------
        self._track(ttk.Label(card, style="Field.TLabel"), "agg_measure_header").grid(
            row=1, column=1, sticky="w", pady=(0, 2), padx=(4, 0)
        )
        self.view_measures = FieldListView(
            card,
            on_activate=lambda item: self._place_as_value(item.name),
            on_remove=lambda item: self._delete_rule(item.name),
        )
        self.view_measures.grid(row=2, column=1, sticky="nsew", padx=(4, 0))
        self._list_hints[self.view_measures] = "agg_hint_measures"

        # Rule builders sit with the measures they produce.
        rule_buttons = ttk.Frame(card, style="Card.TFrame")
        rule_buttons.grid(row=3, column=1, sticky="ew", padx=(4, 0))
        self._shared_rows[3].append(rule_buttons)
        self.btn_group_rule = self._track(
            ttk.Button(rule_buttons, command=self.add_column_group_rule, style="Compact.TButton"),
            "agg_btn_group_rule",
        )
        self.btn_group_rule.pack(side="left", expand=True, fill="x", padx=1, pady=(4, 0))
        self.btn_formula_rule = self._track(
            ttk.Button(rule_buttons, command=self.add_formula_rule, style="Compact.TButton"),
            "agg_btn_formula_rule",
        )
        self.btn_formula_rule.pack(side="left", expand=True, fill="x", padx=1, pady=(4, 0))

    def _build_rules_pane(self, parent: tk.Widget) -> None:
        card = ttk.LabelFrame(parent, style="Card.TLabelframe", padding=(10, 8))
        self._track(card, "agg_rules_card")
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        card.rowconfigure(2, weight=1)
        self._pane_cards.append(card)

        # --- Filters: a short strip on top, because a filter narrows everything
        # below it. It occupies the same row as the source pane's search box, so
        # the two placement lists line up with the two source lists.
        filter_row = ttk.Frame(card, style="Card.TFrame")
        filter_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._shared_rows[0].append(filter_row)

        # The caption sits beside the list rather than above it, which buys the
        # second row without costing any height.
        filter_row.columnconfigure(1, weight=1)
        self._track(ttk.Label(filter_row, style="Field.TLabel"), "agg_filters_box").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.view_filters = FieldListView(
            filter_row,
            on_remove=lambda item: self._remove_filter(item.name),
            height=2,
        )
        self.view_filters.grid(row=0, column=1, sticky="ew")
        self._list_hints[self.view_filters] = "agg_hint_filters"

        self.btn_add_filter = self._track(
            ttk.Button(filter_row, command=self.add_filter_condition, style="Compact.TButton", width=11),
            "agg_btn_add_filter",
        )
        self.btn_add_filter.grid(row=0, column=2, sticky="n", padx=(8, 0))

        # --- 1. Row groups / 2. Values, mirroring the source pane's two columns
        self._track(ttk.Label(card, style="Field.TLabel"), "agg_rows_box").grid(
            row=1, column=0, sticky="w", pady=(0, 2)
        )
        self.view_rows = FieldListView(
            card,
            on_remove=lambda item: self._unplace(item.name),
            on_activate=lambda item: self._unplace(item.name),
        )
        self.view_rows.grid(row=2, column=0, sticky="nsew", padx=(0, 4))
        self._list_hints[self.view_rows] = "agg_hint_rows"

        self._track(ttk.Label(card, style="Field.TLabel"), "agg_values_box").grid(
            row=1, column=1, sticky="w", pady=(0, 2), padx=(4, 0)
        )
        self.view_values = FieldListView(
            card,
            on_remove=lambda item: self._unplace(item.name),
            on_activate=lambda item: self._unplace(item.name),
            on_context=self._show_function_menu,
        )
        self.view_values.grid(row=2, column=1, sticky="nsew", padx=(4, 0))
        self._list_hints[self.view_values] = "agg_hint_values"

    def _build_bottom_bar(self) -> None:
        bottom_bar = ttk.Frame(self, style="App.TFrame")
        bottom_bar.grid(row=3, column=0, sticky="ew")
        bottom_bar.columnconfigure(0, weight=1)

        # Undo belongs with the configuration it reverses, not with "run it".
        self.btn_undo = self._track(
            ttk.Button(bottom_bar, command=self.undo_last_change, style="Secondary.TButton", state="disabled"),
            "agg_undo",
        )
        self.btn_undo.grid(row=0, column=0, sticky="w")
        self.winfo_toplevel().bind("<Control-z>", self._undo_shortcut, add="+")

        btn_frame = ttk.Frame(bottom_bar, style="App.TFrame")
        btn_frame.grid(row=0, column=1, sticky="e")

        self.btn_preview = self._track(
            ttk.Button(btn_frame, command=self.run_preview, style="Secondary.TButton"), "agg_preview"
        )
        self.btn_preview.pack(side="left", padx=(0, 6))

        self.btn_cancel = self._track(
            ttk.Button(btn_frame, command=self.cancel_aggregation, style="Secondary.TButton", state="disabled"),
            "agg_cancel",
        )
        self.btn_cancel.pack(side="left", padx=(0, 10))

        self.btn_run = self._track(
            ttk.Button(btn_frame, command=self.run_aggregation, style="Primary.TButton"), "agg_run"
        )
        self.btn_run.pack(side="left")

    # -------------------------------------------------------------
    # Output destination
    # -------------------------------------------------------------
    def _output_extension(self) -> str:
        return ".xlsx" if self._output_format() == "xlsx" else ".csv"

    def _output_format(self) -> str:
        return "xlsx" if "excel" in self.output_format_var.get().lower() else "csv"

    def _reset_output_destination(self, source_path: str) -> None:
        """Default the destination to the engine's own choice next to the source."""
        suggestion = default_output_path(source_path, self._output_format())
        self.output_dir_var.set(os.path.dirname(suggestion))
        self.output_name_var.set(os.path.splitext(os.path.basename(suggestion))[0])

    def resolved_output_path(self) -> Optional[str]:
        """The absolute destination, or None while the form is incomplete."""
        folder = self.output_dir_var.get().strip()
        stem = self.output_name_var.get().strip()
        if not folder or not stem:
            return None
        # Strip only an extension we would add ourselves. `os.path.splitext`
        # would turn "2026.01 요약" into "2026".
        for extension in (".xlsx", ".csv"):
            if stem.lower().endswith(extension) and len(stem) > len(extension):
                stem = stem[: -len(extension)]
                break
        return os.path.abspath(os.path.join(folder, f"{stem}{self._output_extension()}"))

    def browse_output_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title=self._ui("agg_msg_choose_folder"),
            initialdir=self.output_dir_var.get().strip() or None,
        )
        if chosen:
            self.output_dir_var.set(os.path.normpath(chosen))

    def open_output_folder(self) -> None:
        if self._last_output_path and not open_containing_folder(self._last_output_path):
            self._warn_open_failed()

    def open_output_file(self) -> None:
        if self._last_output_path and not open_file(self._last_output_path):
            self._warn_open_failed()

    def _warn_open_failed(self) -> None:
        messagebox.showwarning(
            self._ui("agg_msg_open_failed_title"),
            self._ui("agg_msg_open_failed").format(path=self._last_output_path or ""),
        )

    def _set_last_output(self, path: Optional[str]) -> None:
        self._last_output_path = path
        state = "normal" if path and os.path.exists(path) else "disabled"
        self.btn_open_folder.configure(state=state)
        self.btn_open_file.configure(state=state)

    # -------------------------------------------------------------
    # Drag and drop wiring
    # -------------------------------------------------------------
    def _register_drag_and_drop(self) -> None:
        # A pool lights up for a field going back home or for a raw source column
        # being reclassified, which is the deliberate way to use a measure as a
        # row key.  The year, constant and rule fields never leave their pool.
        self.dnd.register(
            self.view_dimensions,
            draggable=True,
            accepts=lambda payload: self.state.can_drop_on_pool(payload.item.name, DIMENSION),
            on_drop=lambda payload, index: self._drop_on_pool(payload, DIMENSION),
        )
        self.dnd.register(
            self.view_measures,
            draggable=True,
            accepts=lambda payload: self.state.can_drop_on_pool(payload.item.name, MEASURE),
            on_drop=lambda payload, index: self._drop_on_pool(payload, MEASURE),
        )
        # An area only lights up for a field it can actually hold: rows take raw
        # dimensions, values take measures and rule columns.
        self.dnd.register(
            self.view_rows,
            draggable=True,
            accepts=lambda payload: self.state.can_place_group_key(payload.item.name),
            on_drop=self._drop_on_rows,
        )
        self.dnd.register(
            self.view_values,
            draggable=True,
            accepts=lambda payload: self.state.can_place_value(payload.item.name),
            on_drop=self._drop_on_values,
        )
        self.winfo_toplevel().bind("<Escape>", lambda _event: self.dnd.cancel(), add="+")

    def _drop_on_pool(self, payload: DragPayload, home: str) -> None:
        name = payload.item.name

        def move() -> bool:
            moved = False
            if payload.source in (self.view_rows, self.view_values):
                moved |= self.state.unplace(name)
            return self.state.reclassify(name, home) or moved

        self._mutate(move)

    def _drop_on_rows(self, payload: DragPayload, index: int) -> None:
        self._mutate(lambda: self._drop_into(payload, index, ROWS))

    def _drop_on_values(self, payload: DragPayload, index: int) -> None:
        self._mutate(lambda: self._drop_into(payload, index, VALUES))

    def _drop_into(self, payload: DragPayload, index: int, area: str) -> bool:
        """Reorder within the area, or move the field in from wherever it was."""
        name = payload.item.name
        view = self.view_rows if area == ROWS else self.view_values
        if payload.source is view:
            return self.state.reorder(area, payload.index, index if index <= payload.index else index - 1)
        self.state.unplace(name)
        place = self.state.place_group_key if area == ROWS else self.state.place_value
        return place(name, index)

    # -------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------
    def refresh_views(self) -> None:
        """Redraw every list from the state model."""
        self._refresh_pools()
        self.view_rows.set_items([self._placed_item(name) for name in self.state.group_keys])
        self.view_values.set_items([self._placed_item(name) for name in self.state.values])
        self.view_filters.set_items(
            [
                FieldListItem(name=str(index), label=describe_filter(condition), hint=describe_filter(condition))
                for index, condition in enumerate(self.state.filters)
            ]
        )
        for view, key in self._list_hints.items():
            view.set_empty_hint(self._ui(key))
        self._refresh_undo_state()

    def _refresh_pools(self) -> None:
        """Redraw the two source lists, applying the search filter."""
        needle = self.search_var.get().strip().casefold()

        def matches(field: PoolField) -> bool:
            return not needle or needle in field.name.casefold()

        self.view_dimensions.set_items(
            [self._pool_item(f, removable=f.kind == CONSTANT) for f in self.state.dimension_pool if matches(f)]
        )
        self.view_measures.set_items(
            [self._pool_item(f, removable=f.is_derived) for f in self.state.measure_pool if matches(f)]
        )

    def clear_search(self) -> None:
        self.search_var.set("")
        self.ent_search.focus_set()

    def _pool_item(self, field: PoolField, removable: bool) -> FieldListItem:
        return FieldListItem(
            name=field.name,
            label=self._field_label(field.name, field.is_month),
            tag=self._field_tag(field.name, field.is_month),
            removable=removable,
            hint=self._rule_description(field.name),
        )

    def _placed_item(self, name: str) -> FieldListItem:
        field = self.state.field(name)
        is_month = bool(field and field.is_month)
        return FieldListItem(
            name=name,
            label=self._field_label(name, is_month),
            tag=self._field_tag(name, is_month),
            hint=self._rule_description(name),
        )

    def _field_label(self, name: str, is_month: bool = False) -> str:
        rule = self.state.rule_for(name)
        if isinstance(rule, ColumnGroupRule):
            return f"{GROUP_GLYPH} {name}"
        if isinstance(rule, DerivedFormulaRule):
            return f"{FORMULA_GLYPH} {name}"
        glyph = _FUNCTION_GLYPHS.get(self.state.measure_function(name))
        if glyph and name in self.state.values:
            return f"{glyph} {name}"
        if self.state.is_constant(name):
            return f"{CONSTANT_GLYPH} {name} = {self.state.constant_columns[name]}"
        if self.state.is_year(name):
            return f"{name} {self._ui('agg_year_tag')}"
        return f"{name} {self._ui('agg_month_tag')}" if is_month else name

    def _field_tag(self, name: str, is_month: bool) -> str:
        if self.state.is_derived(name) or self.state.is_constant(name):
            return "derived"
        if self.state.is_year(name):
            return "month"
        return "month" if is_month else ""

    def _rule_description(self, name: str) -> str:
        """Full definition of a rule column, shown on hover since lists are narrow."""
        if self.state.is_constant(name):
            return self._ui("agg_hint_constant").format(name=name, value=self.state.constant_columns[name])
        if self.state.is_year(name):
            return self._ui("agg_hint_year").format(month=self.state.month_column or "")
        rule = self.state.rule_for(name)
        if isinstance(rule, ColumnGroupRule):
            return f"{rule.new_column} = {' + '.join(rule.source_columns)}"
        if isinstance(rule, DerivedFormulaRule):
            body = f"{rule.new_column} = {rule.numerator_column} ÷ {rule.denominator_column}"
            if rule.multiplier != 1.0:  # a plain "× 1" tells the reader nothing
                body += f" × {rule.multiplier:g}"
            return f"{body}  ({self._format_type_label(rule.format_type)})"
        return ""

    def _format_type_label(self, format_type: str) -> str:
        return self._ui(_FORMAT_LABEL_KEYS.get(format_type, "agg_dlg_format_number"))

    # -------------------------------------------------------------
    # Field placement actions
    # -------------------------------------------------------------
    def _mutate(self, action: Callable[[], bool]) -> bool:
        """Run a configuration change behind a restore point.

        A rejected change (dropping a measure on the row area, say) must not
        leave a step behind, or the next Ctrl+Z would appear to do nothing.
        """
        self.state.snapshot()
        if action():
            self.refresh_views()
            return True
        self.state.undo()  # the snapshot equals the current state, so this just drops it
        return False

    def _place_as_row(self, name: str) -> None:
        self._mutate(lambda: self.state.place_group_key(name))

    def _place_as_value(self, name: str) -> None:
        self._mutate(lambda: self.state.place_value(name))

    def _unplace(self, name: str) -> None:
        self._mutate(lambda: self.state.unplace(name))

    # -------------------------------------------------------------
    # Rule actions
    # -------------------------------------------------------------
    def _require_schema(self) -> bool:
        if self._schema is None:
            messagebox.showinfo(self._ui("agg_msg_notice"), self._ui("agg_msg_select_file_first"))
            return False
        return True

    def add_column_group_rule(self) -> None:
        if not self._require_schema():
            return
        rule = ask_column_group(self, self._ui, self.state)
        if rule and self._mutate(lambda: self.state.add_column_group(rule)):
            self.view_measures.reveal(rule.new_column)

    def add_formula_rule(self) -> None:
        if not self._require_schema():
            return
        rule = ask_derived_formula(self, self._ui, self.state)
        if rule and self._mutate(lambda: self.state.add_derived_formula(rule)):
            self.view_measures.reveal(rule.new_column)

    def add_constant_column(self) -> None:
        """A literal column needs no source file: it supplies its own value."""
        result = ask_constant_column(self, self._ui, self.state)
        if result and self._mutate(lambda: self.state.add_constant_column(*result)):
            self.view_dimensions.reveal(result[0])

    def _remove_constant_column(self, name: str) -> None:
        self._mutate(lambda: self.state.remove_constant_column(name))

    def add_filter_condition(self) -> None:
        if not self._require_schema():
            return
        condition = ask_filter(self, self._ui, self._schema.columns, self._schema.sample_values)
        if condition:
            self._mutate(lambda: self.state.add_filter(condition) or True)

    def _delete_rule(self, name: str) -> None:
        """Remove a rule column, warning first when other rules depend on it."""
        dependents = self.state.dependents_of(name)
        if dependents:
            confirmed = messagebox.askyesno(
                self._ui("agg_msg_cascade_title"),
                self._ui("agg_msg_cascade").format(name=name, dependents=", ".join(dependents)),
            )
            if not confirmed:
                return
        self._mutate(lambda: bool(self.state.remove_derived(name)))

    def _remove_filter(self, row_name: str) -> None:
        try:
            index = int(row_name)
        except ValueError:
            return
        self._mutate(lambda: self.state.remove_filter(index))

    # -------------------------------------------------------------
    # Compatibility accessors (read-only views onto the state model)
    # -------------------------------------------------------------
    @property
    def group_keys(self) -> List[str]:
        return self.state.group_keys

    @property
    def values(self) -> List[str]:
        return self.state.values

    @property
    def filters(self) -> List[FilterCondition]:
        return self.state.filters

    @property
    def column_groups(self) -> List[ColumnGroupRule]:
        return self.state.column_groups

    @property
    def derived_formulas(self) -> List[DerivedFormulaRule]:
        return self.state.derived_formulas

    # -------------------------------------------------------------
    # File selection
    # -------------------------------------------------------------
    def browse_file(self):
        filename = filedialog.askopenfilename(
            title=self._ui("agg_file_dialog_title"),
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not filename:
            return

        self.filepath_var.set(filename)
        # A long path would otherwise show its drive letter and hide the file name.
        self.ent_file.xview_moveto(1.0)
        try:
            self.app.set_progress(0, self._ui("agg_msg_schema_progress"))
            schema = inspect_dataset_schema(filename)
            self._schema = schema
            self.state.load_schema(
                dimensions=schema.dimension_candidates,
                measures=schema.measure_candidates,
                month_column=schema.detected_month_column,
                columns=schema.columns,
            )
            self._reset_output_destination(filename)
            self._set_last_output(None)
            restored = self._restore_remembered_configuration(filename)
            self.refresh_views()
            self.app.set_progress(100, self._ui("agg_msg_schema_done").format(count=len(schema.columns)))
            log_key = "agg_msg_schema_restored" if restored else "agg_msg_schema_log"
            self.app.set_status_log(
                self._ui(log_key).format(name=os.path.basename(filename), count=len(schema.columns))
            )
        except Exception as error:
            messagebox.showerror(
                self._ui("agg_msg_schema_error_title"),
                self._ui("agg_msg_schema_error").format(error=error),
            )

    # -------------------------------------------------------------
    # Per-file memory
    # -------------------------------------------------------------
    def _current_preset(self, name: str = "") -> AggregationPreset:
        """The current screen as a preset, which is also the memory format."""
        column_groups, derived_formulas = self.state.all_rules_with_output()
        return AggregationPreset(
            name=name,
            group_by_keys=list(self.state.group_keys),
            measure_sums=self.state.measure_sums(),
            column_groups=column_groups,
            derived_formulas=derived_formulas,
            filters=list(self.state.filters),
            value_order=list(self.state.values),
            constant_columns=dict(self.state.constant_columns),
            measure_functions=self.state.measure_functions_for_spec(),
            rollup_annual=self.state.uses_year(),
            month_column=self._schema.detected_month_column if self._schema else self.state.month_column,
            output_format=self._output_format(),
        )

    def _apply_preset_to_state(self, preset: AggregationPreset) -> None:
        self.state.apply_configuration(
            group_keys=preset.group_by_keys,
            measure_sums=preset.measure_sums,
            column_groups=preset.column_groups,
            derived_formulas=preset.derived_formulas,
            filters=preset.filters,
            value_order=preset.value_order,
            constant_columns=preset.constant_columns,
            measure_functions=preset.measure_functions,
        )

    def _restore_remembered_configuration(self, file_path: str) -> bool:
        """Bring back how this file was last aggregated. Never blocks the open."""
        try:
            stored = recall(file_path)
            if not stored:
                return False
            preset = AggregationPreset.from_dict({**stored, "name": stored.get("name") or "recent"})
            self._apply_preset_to_state(preset)
            self.output_format_var.set(
                _OUTPUT_FORMATS[0] if preset.output_format.lower() == "xlsx" else _OUTPUT_FORMATS[1]
            )
            return True
        except Exception:
            # A convenience feature must never stop the user opening a file.
            return False

    def _remember_configuration(self, file_path: str) -> None:
        try:
            remember(file_path, self._current_preset().to_dict())
        except Exception:
            pass

    # -------------------------------------------------------------
    # Presets
    # -------------------------------------------------------------
    def refresh_presets_dropdown(self):
        names = [preset.name for preset in list_presets()]
        self.combo_presets["values"] = names
        if names and not self.preset_name_var.get():
            self.preset_name_var.set(names[0])

    def apply_selected_preset(self):
        name = self.preset_name_var.get().strip()
        if not name:
            messagebox.showinfo(self._ui("agg_msg_notice"), self._ui("agg_msg_preset_missing"))
            return

        try:
            preset = load_preset(name)
        except Exception as error:
            messagebox.showerror(
                self._ui("agg_msg_preset_load_error_title"),
                self._ui("agg_msg_preset_load_error").format(error=error),
            )
            return

        if self._schema:
            missing = validate_preset_against_columns(preset, self._schema.columns)
            if missing:
                proceed = messagebox.askyesno(
                    self._ui("agg_msg_preset_mismatch_title"),
                    self._ui("agg_msg_preset_mismatch").format(columns=", ".join(missing)),
                )
                if not proceed:
                    return

        self.output_format_var.set(_OUTPUT_FORMATS[0] if preset.output_format.lower() == "xlsx" else _OUTPUT_FORMATS[1])

        self._apply_preset_to_state(preset)
        self.refresh_views()

        self.app.set_status_log(self._ui("agg_msg_preset_applied").format(name=name))
        if preset.description:
            self.app.set_result_text(
                self._ui("agg_msg_preset_desc").format(name=name, description=preset.description)
            )

    def save_current_as_preset(self):
        name = simpledialog.askstring(
            self._ui("agg_msg_preset_save_title"),
            self._ui("agg_msg_preset_save_prompt"),
            initialvalue=self.preset_name_var.get(),
        )
        if not name or not name.strip():
            return
        name = name.strip()

        if preset_exists(name):
            overwrite = messagebox.askyesno(
                self._ui("agg_msg_preset_overwrite_title"),
                self._ui("agg_msg_preset_overwrite").format(name=name),
            )
            if not overwrite:
                return

        description = simpledialog.askstring(
            self._ui("agg_msg_preset_memo_title"),
            self._ui("agg_msg_preset_memo_prompt"),
            initialvalue="",
        ) or ""

        preset = self._current_preset(name)
        preset.description = description.strip()

        save_preset(preset)
        self.refresh_presets_dropdown()
        self.preset_name_var.set(preset.name)
        messagebox.showinfo(
            self._ui("agg_msg_preset_saved_title"),
            self._ui("agg_msg_preset_saved").format(name=preset.name),
        )

    def export_preset(self):
        name = self.preset_name_var.get().strip()
        if not name:
            messagebox.showinfo(self._ui("agg_msg_notice"), self._ui("agg_msg_preset_missing"))
            return

        destination = filedialog.asksaveasfilename(
            title=self._ui("agg_msg_preset_export_title"),
            defaultextension=".json",
            initialfile=f"{name}.json",
            filetypes=(("JSON preset file", "*.json"),),
        )
        if not destination:
            return

        try:
            export_preset_file(load_preset(name), destination)
            messagebox.showinfo(
                self._ui("agg_msg_preset_export_done_title"),
                self._ui("agg_msg_preset_export_done").format(path=destination),
            )
        except Exception as error:
            messagebox.showerror(self._ui("agg_msg_preset_export_error"), str(error))

    def import_preset(self):
        source = filedialog.askopenfilename(
            title=self._ui("agg_msg_preset_import_title"),
            filetypes=(("JSON preset file", "*.json"), ("All files", "*.*")),
        )
        if not source:
            return

        try:
            imported = import_preset_file(source, save_to_local=True)
            self.refresh_presets_dropdown()
            self.preset_name_var.set(imported.name)
            messagebox.showinfo(
                self._ui("agg_msg_preset_import_done_title"),
                self._ui("agg_msg_preset_import_done").format(name=imported.name),
            )
            self.apply_selected_preset()
        except Exception as error:
            messagebox.showerror(
                self._ui("agg_msg_preset_import_error"),
                self._ui("agg_msg_preset_import_error_body").format(error=error),
            )

    def delete_current_preset(self):
        name = self.preset_name_var.get().strip()
        if not name:
            return
        if messagebox.askyesno(
            self._ui("agg_msg_preset_delete_title"),
            self._ui("agg_msg_preset_delete").format(name=name),
        ):
            delete_preset(name)
            self.preset_name_var.set("")
            self.refresh_presets_dropdown()

    # -------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------
    def cancel_aggregation(self):
        if self._cancel_event and not self._cancel_event.is_set():
            self._cancel_event.set()
            self.btn_cancel.configure(state="disabled")
            self.app.set_status_log(self._ui("agg_msg_cancel_requested"))

    def _build_spec(self) -> Optional[AggregationSpec]:
        file_path = self.filepath_var.get().strip()
        if not file_path or not os.path.exists(file_path):
            messagebox.showerror(self._ui("agg_msg_need_file_title"), self._ui("agg_msg_need_file"))
            return None

        if not self.state.group_keys:
            messagebox.showwarning(self._ui("agg_msg_config_title"), self._ui("agg_msg_need_group_key"))
            return None

        if not self.state.values:
            messagebox.showwarning(self._ui("agg_msg_config_title"), self._ui("agg_msg_need_measure"))
            return None

        number_mode = getattr(self.app, "_number_mode", lambda _: "English")(
            getattr(self.app, "num_format", tk.StringVar()).get()
        )
        column_groups, derived_formulas = self.state.rules_for_spec()

        return AggregationSpec(
            file_path=file_path,
            group_by_keys=list(self.state.group_keys),
            measure_sums=self.state.measure_sums(),
            column_groups=column_groups,
            derived_formulas=derived_formulas,
            filters=list(self.state.filters),
            output_order=list(self.state.values),
            constant_columns=self.state.constant_columns_for_spec(),
            measure_functions=self.state.measure_functions_for_spec(),
            rollup_annual=self.state.uses_year(),
            month_column=self._schema.detected_month_column if self._schema else self.state.month_column,
            delimiter=self._schema.delimiter if self._schema else None,
            encoding=self._schema.encoding if self._schema else None,
            number_mode=number_mode,
            output_format=self._output_format(),
            # None lets the engine fall back to its own name next to the source.
            output_path=self.resolved_output_path(),
        )

    def _confirm_destination(self, spec: AggregationSpec) -> bool:
        """Check the chosen folder is usable and get consent before overwriting."""
        if not spec.output_path:
            return True

        folder = os.path.dirname(spec.output_path)
        if not os.path.isdir(folder):
            messagebox.showerror(
                self._ui("agg_msg_config_title"),
                self._ui("agg_msg_bad_folder").format(folder=folder),
            )
            return False

        if os.path.exists(spec.output_path):
            return messagebox.askyesno(
                self._ui("agg_msg_overwrite_title"),
                self._ui("agg_msg_overwrite").format(name=os.path.basename(spec.output_path)),
            )
        return True

    def undo_last_change(self) -> None:
        """Take back the last configuration change."""
        if self.state.undo():
            self.refresh_views()

    def _undo_shortcut(self, _event: Any = None) -> None:
        """Ctrl+Z is bound on the whole window, so it must check that it is meant for us.

        Without the checks a Ctrl+Z pressed on the CSV tab would silently rewind a
        configuration the user cannot see, and one pressed while typing a file name
        would move a field instead of touching the text.
        """
        if not self.winfo_viewable():
            return
        try:
            focused = self.focus_get()
        except (tk.TclError, KeyError):
            focused = None
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Text)):
            return
        self.undo_last_change()

    def _refresh_undo_state(self) -> None:
        self.btn_undo.configure(state="normal" if self.state.can_undo() else "disabled")

    def run_preview(self):
        spec = self._build_spec()
        if not spec:
            return

        try:
            self.app.set_status_log(self._ui("agg_msg_preview_running"))
            preview_df, _text = preview_aggregation(spec, sample_rows=_PREVIEW_ROWS)
            columns, rows = format_preview_rows(
                preview_df, spec, self._effective_group_keys(spec), rows=_PREVIEW_ROWS
            )
        except Exception as error:
            messagebox.showerror(
                self._ui("agg_msg_preview_error_title"),
                self._ui("agg_msg_preview_error").format(error=error),
            )
            return

        group_keys = set(self._effective_group_keys(spec))
        dialog = PreviewDialog(
            self, self._ui, columns, rows,
            numeric_columns=[c for c in columns if c not in group_keys],
        )
        dialog.set_summary(self._ui("agg_preview_counting"))
        self.app.set_status_log(self._ui("agg_msg_preview_done"))
        self._start_row_count(dialog, spec, len(rows))
        dialog.show()

    def _effective_group_keys(self, spec: AggregationSpec) -> List[str]:
        """The group columns as the engine will emit them, roll-up included."""
        keys = list(spec.group_by_keys)
        if spec.rollup_annual and spec.month_column:
            if spec.month_column in keys:
                keys[keys.index(spec.month_column)] = spec.annual_column_name
            elif spec.annual_column_name not in keys:
                keys.insert(0, spec.annual_column_name)
        return keys

    def _start_row_count(self, dialog: PreviewDialog, spec: AggregationSpec, shown: int) -> None:
        """Tell the user how big the real result will be before they commit to it.

        The count streams only the grouping columns, so it is far cheaper than
        the run itself — but it is still a pass over the whole file, and on a
        large one that is seconds to minutes. On the Tk thread it would freeze
        the dialog with no way to close it, so it runs as a background job and
        closing the dialog cancels it at the next chunk.
        """
        cancel = threading.Event()
        dialog.bind("<Destroy>", lambda _event: cancel.set(), add="+")
        sample_only = self._ui("agg_preview_summary_sample").format(shown=shown)

        def worker(_report_progress):
            return estimate_result_rows(spec, cancel_event=cancel)

        def on_success(result):
            total, exact = result
            key = "agg_preview_summary" if exact else "agg_preview_summary_capped"
            self._set_preview_summary(dialog, self._ui(key).format(shown=shown, rows=f"{total:,}"))

        def on_error(_error):
            if not cancel.is_set():  # a cancelled count has no dialog left to report to
                self._set_preview_summary(dialog, sample_only)

        # One job name per dialog: a count still winding down for a preview that
        # was just closed must not stop the next preview from getting its own.
        self._row_count_seq += 1
        started = self.app.job_runner.start(
            f"{_ROW_COUNT_JOB}-{self._row_count_seq}",
            worker,
            JobCallbacks(
                on_progress=lambda _percent, _message: None,
                on_success=on_success,
                on_error=on_error,
                on_finished=lambda: None,
            ),
        )
        if not started:
            self._set_preview_summary(dialog, sample_only)

    @staticmethod
    def _set_preview_summary(dialog: PreviewDialog, text: str) -> None:
        try:
            if dialog.winfo_exists():
                dialog.set_summary(text)
        except tk.TclError:
            pass  # the user closed the preview while the count was running

    def run_aggregation(self):
        if getattr(self.app, "_csv_processing", False) or getattr(self.app, "_promotion_processing", False):
            messagebox.showinfo(self._ui("agg_msg_busy_title"), self._ui("agg_msg_busy"))
            return

        spec = self._build_spec()
        if not spec:
            return
        if not self._confirm_destination(spec):
            return

        self._set_last_output(None)
        self._cancel_event = threading.Event()
        self._is_aggregating = True
        self.btn_run.configure(state="disabled")
        self.btn_preview.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self._refresh_sibling_tabs()
        self.app.set_progress(0, self._ui("agg_msg_prepare"))

        def worker(report_progress):
            def progress_bridge(current, total, message):
                report_progress(int((current / max(1, total)) * 100), message)

            return aggregate_dataset(spec, progress_callback=progress_bridge, cancel_event=self._cancel_event)

        def on_success(result: AggregationResult):
            self.app.set_progress(100, self._ui("agg_msg_done_progress"))
            self.app.set_status_log(self._ui("agg_msg_saved_log").format(name=os.path.basename(result.out_path)))
            self._set_last_output(result.out_path)
            self._remember_configuration(spec.file_path)

            warning = ""
            if result.coerced_numbers_count > 0:
                warning = self._ui("agg_msg_coerced").format(count=result.coerced_numbers_count)

            self.app.set_result_text(
                self._ui("agg_msg_result_body").format(
                    name=os.path.basename(result.out_path),
                    folder=os.path.dirname(result.out_path),
                    rows=f"{result.final_rows:,}",
                    keys=", ".join(spec.group_by_keys),
                    rollup=self._ui("agg_msg_rollup_on" if spec.rollup_annual else "agg_msg_rollup_off"),
                    columns=len(self.state.values),
                    warning=warning,
                    preview=result.sample_preview_text,
                )
            )

            message = self._ui("agg_msg_complete").format(path=result.out_path)
            if result.coerced_numbers_count > 0:
                message += self._ui("agg_msg_complete_coerced").format(count=result.coerced_numbers_count)
            messagebox.showinfo(self._ui("agg_msg_complete_title"), message)

        def on_error(error):
            if isinstance(error, AggregationCancelledError):
                self.app.set_progress(0, self._ui("agg_msg_cancelled_progress"))
                self.app.set_status_log(self._ui("agg_msg_cancelled_log"))
                self.app.set_result_text(self._ui("agg_msg_cancelled_text"))
            else:
                self.app.set_progress(0, self._ui("agg_msg_error_progress"))
                self.app.set_status_log(self._ui("agg_msg_error_log"))
                messagebox.showerror(
                    self._ui("agg_msg_error_title"), self._ui("agg_msg_error").format(error=error)
                )

        def on_finished():
            self._is_aggregating = False
            self.btn_run.configure(state="normal")
            self.btn_preview.configure(state="normal")
            self.btn_cancel.configure(state="disabled")
            self._refresh_sibling_tabs()

        started = self.app.job_runner.start(
            "dataset-aggregation",
            worker,
            JobCallbacks(
                on_progress=lambda pct, message: self.app.set_progress(pct, message),
                on_success=on_success,
                on_error=on_error,
                on_finished=on_finished,
            ),
        )
        if not started:
            on_finished()

    def _refresh_sibling_tabs(self) -> None:
        for hook in ("_refresh_csv_action_state", "_refresh_promotion_action_state"):
            if hasattr(self.app, hook):
                getattr(self.app, hook)()
