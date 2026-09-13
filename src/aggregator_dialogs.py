"""Modal dialogs for building aggregator rules (column groups, formulas, filters)."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from src.aggregator_fields import AggregatorFieldState
from src.data_aggregator import ColumnGroupRule, DerivedFormulaRule, FilterCondition
from src.ui_components import PALETTE

FILTER_OPERATORS = ("==", "!=", "contains", "in", "not in")
_MULTI_VALUE_OPERATORS = ("in", "not in")

# Display formats offered for a derived column, in the order they are listed.
# The label is translated; the type at the same index is what gets stored.
_FORMAT_TYPES = ("percent", "ratio", "number")
_FORMAT_LABEL_KEYS = ("agg_dlg_format_percent", "agg_dlg_format_ratio", "agg_dlg_format_number")

Translator = Callable[[str], str]


class _RuleDialog(tk.Toplevel):
    """Shared modal shell: title, body, and an Add / Cancel button row."""

    def __init__(self, parent: tk.Misc, ui: Translator, title: str, geometry: str):
        super().__init__(parent)
        self.title(title)
        self.geometry(geometry)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.configure(background=PALETTE["surface"])
        self.result = None
        self._ui = ui

        self.body = ttk.Frame(self, style="Dialog.TFrame", padding=(14, 12))
        self.body.pack(fill="both", expand=True)

        buttons = ttk.Frame(self, style="Dialog.TFrame", padding=(14, 0, 14, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=ui("agg_dlg_add"), command=self._on_ok, style="Primary.TButton").pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text=ui("agg_dlg_cancel"), command=self.destroy, style="Secondary.TButton").pack(side="right")

        self.bind("<Escape>", lambda _event: self.destroy())

    def _on_ok(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def show(self):
        self.grab_set()
        self.wait_window()
        return self.result

    def _warn(self, message_key: str) -> None:
        messagebox.showwarning(self._ui("agg_dlg_check_title"), self._ui(message_key), parent=self)


class _ColumnGroupDialog(_RuleDialog):
    def __init__(self, parent: tk.Misc, ui: Translator, state: AggregatorFieldState):
        super().__init__(parent, ui, ui("agg_dlg_group_title"), "400x360")
        self._state = state

        ttk.Label(self.body, text=ui("agg_dlg_group_name"), style="Field.TLabel").pack(anchor="w")
        self._name_var = tk.StringVar(value=ui("agg_dlg_group_default_name"))
        entry = ttk.Entry(self.body, textvariable=self._name_var)
        entry.pack(fill="x", pady=(4, 10))
        entry.focus_set()

        ttk.Label(self.body, text=ui("agg_dlg_group_sources"), style="Field.TLabel").pack(anchor="w")
        self._listbox = tk.Listbox(self.body, selectmode="multiple", height=9, exportselection=False)
        self._listbox.pack(fill="both", expand=True, pady=(4, 0))
        for column in state.raw_measure_names():
            self._listbox.insert(tk.END, column)

    def _on_ok(self) -> None:
        name = self._name_var.get().strip()
        sources = [self._listbox.get(i) for i in self._listbox.curselection()]
        if not name or not sources:
            self._warn("agg_dlg_group_incomplete")
            return
        if self._state.is_known(name):
            self._warn("agg_dlg_name_taken")
            return
        self.result = ColumnGroupRule(new_column=name, source_columns=sources)
        self.destroy()


class _FormulaDialog(_RuleDialog):
    def __init__(self, parent: tk.Misc, ui: Translator, state: AggregatorFieldState):
        super().__init__(parent, ui, ui("agg_dlg_formula_title"), "400x300")
        self._state = state
        candidates = state.formula_candidate_names()

        ttk.Label(self.body, text=ui("agg_dlg_formula_name"), style="Field.TLabel").pack(anchor="w")
        self._name_var = tk.StringVar(value=ui("agg_dlg_formula_default_name"))
        entry = ttk.Entry(self.body, textvariable=self._name_var)
        entry.pack(fill="x", pady=(4, 10))
        entry.focus_set()

        grid = ttk.Frame(self.body, style="Dialog.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text=ui("agg_dlg_formula_numerator"), style="Field.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        self._numerator_var = tk.StringVar()
        ttk.Combobox(grid, textvariable=self._numerator_var, values=candidates, state="readonly").grid(row=0, column=1, sticky="ew", pady=4, padx=(8, 0))

        ttk.Label(grid, text=ui("agg_dlg_formula_denominator"), style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        self._denominator_var = tk.StringVar()
        ttk.Combobox(grid, textvariable=self._denominator_var, values=candidates, state="readonly").grid(row=1, column=1, sticky="ew", pady=4, padx=(8, 0))

        # A percentage is stored as a ratio and displayed by the spreadsheet's own
        # percent format, so the user picks a display format rather than a
        # multiplier — "× 100" was the thing that produced 2746.76% cells.
        ttk.Label(grid, text=ui("agg_dlg_formula_format"), style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self._format_labels = [ui(key) for key in _FORMAT_LABEL_KEYS]
        self._format_var = tk.StringVar(value=self._format_labels[0])
        format_combo = ttk.Combobox(grid, textvariable=self._format_var, values=self._format_labels, state="readonly")
        format_combo.grid(row=2, column=1, sticky="ew", pady=4, padx=(8, 0))
        format_combo.bind("<<ComboboxSelected>>", self._on_format_changed)

        self._multiplier_label = ttk.Label(grid, text=ui("agg_dlg_formula_multiplier"), style="Field.TLabel")
        self._multiplier_label.grid(row=3, column=0, sticky="w", pady=4)
        self._multiplier_var = tk.StringVar(value="1")
        self._multiplier_entry = ttk.Entry(grid, textvariable=self._multiplier_var)
        self._multiplier_entry.grid(row=3, column=1, sticky="ew", pady=4, padx=(8, 0))

        ttk.Label(self.body, text=ui("agg_dlg_formula_note"), style="Help.TLabel", wraplength=380).pack(anchor="w", pady=(10, 0))
        self._on_format_changed()

    def _selected_format_type(self) -> str:
        try:
            return _FORMAT_TYPES[self._format_labels.index(self._format_var.get())]
        except ValueError:
            return _FORMAT_TYPES[0]

    def _on_format_changed(self, _event=None) -> None:
        """A scale factor only means something for a plain number column."""
        if self._selected_format_type() == "number":
            self._multiplier_label.grid()
            self._multiplier_entry.grid()
        else:
            self._multiplier_label.grid_remove()
            self._multiplier_entry.grid_remove()

    def _on_ok(self) -> None:
        name = self._name_var.get().strip()
        numerator = self._numerator_var.get()
        denominator = self._denominator_var.get()
        if not name or not numerator or not denominator:
            self._warn("agg_dlg_formula_incomplete")
            return
        if self._state.is_known(name):
            self._warn("agg_dlg_name_taken")
            return

        format_type = self._selected_format_type()
        if format_type == "number":
            try:
                multiplier = float(self._multiplier_var.get())
            except ValueError:
                multiplier = 1.0
        else:
            multiplier = 1.0  # percent and ratio are stored raw and formatted by Excel

        self.result = DerivedFormulaRule(
            new_column=name,
            numerator_column=numerator,
            denominator_column=denominator,
            multiplier=multiplier,
            format_type=format_type,
        )
        self.destroy()


class _FilterDialog(_RuleDialog):
    def __init__(
        self,
        parent: tk.Misc,
        ui: Translator,
        columns: Sequence[str],
        sample_values: Dict[str, List[str]],
    ):
        super().__init__(parent, ui, ui("agg_dlg_filter_title"), "420x270")
        self._sample_values = sample_values
        column_list = list(columns)

        grid = ttk.Frame(self.body, style="Dialog.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text=ui("agg_dlg_filter_column"), style="Field.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        self._column_var = tk.StringVar(value=column_list[0] if column_list else "")
        column_combo = ttk.Combobox(grid, textvariable=self._column_var, values=column_list, state="readonly")
        column_combo.grid(row=0, column=1, sticky="ew", pady=6, padx=(8, 0))
        column_combo.bind("<<ComboboxSelected>>", self._on_column_changed)

        ttk.Label(grid, text=ui("agg_dlg_filter_operator"), style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        self._operator_var = tk.StringVar(value="==")
        ttk.Combobox(grid, textvariable=self._operator_var, values=list(FILTER_OPERATORS), state="readonly").grid(row=1, column=1, sticky="ew", pady=6, padx=(8, 0))

        ttk.Label(grid, text=ui("agg_dlg_filter_value"), style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=6)
        self._value_var = tk.StringVar()
        self._value_combo = ttk.Combobox(grid, textvariable=self._value_var, values=self._samples_for(self._column_var.get()))
        self._value_combo.grid(row=2, column=1, sticky="ew", pady=6, padx=(8, 0))
        self._value_combo.focus_set()

        ttk.Label(self.body, text=ui("agg_dlg_filter_note"), style="Help.TLabel", wraplength=380).pack(anchor="w", pady=(8, 0))

    def _samples_for(self, column: str) -> List[str]:
        return [str(v) for v in self._sample_values.get(column, []) if str(v).strip()]

    def _on_column_changed(self, _event=None) -> None:
        self._value_combo["values"] = self._samples_for(self._column_var.get())

    def _on_ok(self) -> None:
        column = self._column_var.get().strip()
        operator = self._operator_var.get().strip()
        raw_value = self._value_var.get().strip()

        if not column or not operator:
            self._warn("agg_dlg_filter_incomplete")
            return
        if not raw_value:
            self._warn("agg_dlg_filter_value_required")
            return

        if operator in _MULTI_VALUE_OPERATORS:
            parsed = [part.strip() for part in raw_value.split(",") if part.strip()]
            if not parsed:
                self._warn("agg_dlg_filter_value_required")
                return
            value = parsed
        else:
            value = raw_value

        self.result = FilterCondition(column=column, operator=operator, value=value)
        self.destroy()


class _ConstantColumnDialog(_RuleDialog):
    """Adds a literal column, e.g. a header YYYY carrying 2026 on every row."""

    def __init__(self, parent: tk.Misc, ui: Translator, state: AggregatorFieldState):
        super().__init__(parent, ui, ui("agg_dlg_constant_title"), "400x230")
        self._state = state

        grid = ttk.Frame(self.body, style="Dialog.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text=ui("agg_dlg_constant_name"), style="Field.TLabel").grid(row=0, column=0, sticky="w", pady=6)
        self._name_var = tk.StringVar(value=ui("agg_dlg_constant_default_name"))
        name_entry = ttk.Entry(grid, textvariable=self._name_var)
        name_entry.grid(row=0, column=1, sticky="ew", pady=6, padx=(8, 0))
        name_entry.focus_set()

        ttk.Label(grid, text=ui("agg_dlg_constant_value"), style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=6)
        self._value_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self._value_var).grid(row=1, column=1, sticky="ew", pady=6, padx=(8, 0))

        ttk.Label(self.body, text=ui("agg_dlg_constant_note"), style="Help.TLabel", wraplength=360).pack(
            anchor="w", pady=(10, 0)
        )

    def _on_ok(self) -> None:
        name = self._name_var.get().strip()
        value = self._value_var.get().strip()
        if not name or not value:
            self._warn("agg_dlg_constant_incomplete")
            return
        if self._state.is_known(name):
            self._warn("agg_dlg_name_taken")
            return
        self.result = (name, value)
        self.destroy()


class PreviewDialog(tk.Toplevel):
    """Shows the sampled result as a real table instead of fixed-width text.

    A wide result is unreadable as monospaced text: columns drift, long numbers
    wrap, and nothing can be resized. A Treeview gives real columns the user can
    widen, and keeps the cell strings identical to what the file will contain.
    """

    def __init__(
        self,
        parent: tk.Misc,
        ui: Translator,
        columns: Sequence[str],
        rows: Sequence[Sequence[str]],
        numeric_columns: Sequence[str] = (),
    ):
        super().__init__(parent)
        self.title(ui("agg_preview_title"))
        self.geometry("820x420")
        self.transient(parent.winfo_toplevel())
        self.configure(background=PALETTE["surface"])
        self._ui = ui

        body = ttk.Frame(self, style="Dialog.TFrame", padding=(14, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.summary = ttk.Label(body, style="Muted.TLabel", anchor="w")
        self.summary.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        numeric = set(numeric_columns)
        self.table = ttk.Treeview(body, columns=list(columns), show="headings", style="FieldList.Treeview")
        for name in columns:
            self.table.heading(name, text=name)
            width = max(90, min(240, 9 * (len(name) + 6)))
            self.table.column(name, width=width, anchor="e" if name in numeric else "w", stretch=False)
        for values in rows:
            self.table.insert("", "end", values=list(values))
        self.table.grid(row=1, column=0, sticky="nsew")

        vertical = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(body, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        buttons = ttk.Frame(self, style="Dialog.TFrame", padding=(14, 0, 14, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=ui("agg_preview_close"), command=self.destroy, style="Secondary.TButton").pack(
            side="right"
        )

        self.bind("<Escape>", lambda _event: self.destroy())

    def set_summary(self, text: str) -> None:
        self.summary.configure(text=text)

    def show(self) -> None:
        self.grab_set()
        self.wait_window()


def ask_constant_column(parent: tk.Misc, ui: Translator, state: AggregatorFieldState) -> Optional[Tuple[str, str]]:
    """Ask for a literal column; returns (name, value) or None."""
    return _ConstantColumnDialog(parent, ui, state).show()


def ask_column_group(parent: tk.Misc, ui: Translator, state: AggregatorFieldState) -> Optional[ColumnGroupRule]:
    return _ColumnGroupDialog(parent, ui, state).show()


def ask_derived_formula(parent: tk.Misc, ui: Translator, state: AggregatorFieldState) -> Optional[DerivedFormulaRule]:
    return _FormulaDialog(parent, ui, state).show()


def ask_filter(
    parent: tk.Misc,
    ui: Translator,
    columns: Sequence[str],
    sample_values: Dict[str, List[str]],
) -> Optional[FilterCondition]:
    return _FilterDialog(parent, ui, columns, sample_values).show()


def describe_filter(condition: FilterCondition) -> str:
    """Human-readable one-liner for a filter row."""
    if isinstance(condition.value, (list, tuple)):
        value = ", ".join(str(v) for v in condition.value)
    else:
        value = str(condition.value)
    return f"{condition.column} {condition.operator} {value}"
