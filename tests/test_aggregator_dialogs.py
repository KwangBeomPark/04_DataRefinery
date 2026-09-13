"""Unit tests for the aggregator rule dialogs' validation and results."""

import tkinter as tk
import unittest
from unittest.mock import patch

from src.aggregator_dialogs import (
    PreviewDialog,
    _ColumnGroupDialog,
    _FilterDialog,
    _FormulaDialog,
    describe_filter,
)
from src.aggregator_fields import AggregatorFieldState
from src.data_aggregator import ColumnGroupRule, FilterCondition
from src.data_refinery import DataRefineryApp

COLUMNS = ["월", "디비전", "수량", "매출", "비용1", "비용2"]


class DialogTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = DataRefineryApp(self.root)  # registers the ttk styles the dialogs use
        self.state = AggregatorFieldState()
        self.state.load_schema(["월", "디비전"], ["수량", "매출", "비용1", "비용2"],
                               month_column="월", columns=COLUMNS)

    def tearDown(self):
        try:
            for after_id in self.root.tk.splitlist(self.root.tk.eval('after info')):
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        except Exception:
            pass
        self.root.destroy()

    def ui(self, key):
        return self.app._ui(key)


class TestColumnGroupDialog(DialogTestCase):
    def build(self):
        return _ColumnGroupDialog(self.app.aggregator_tab, self.ui, self.state)

    def test_lists_every_source_measure(self):
        dialog = self.build()
        listed = [dialog._listbox.get(i) for i in range(dialog._listbox.size())]
        self.assertEqual(listed, ["수량", "매출", "비용1", "비용2"])

    def test_valid_input_produces_a_rule(self):
        dialog = self.build()
        dialog._name_var.set("총비용")
        dialog._listbox.selection_set(2, 3)

        dialog._on_ok()

        self.assertEqual(dialog.result, ColumnGroupRule("총비용", ["비용1", "비용2"]))

    def test_missing_sources_are_rejected(self):
        dialog = self.build()
        dialog._name_var.set("총비용")

        with patch("src.aggregator_dialogs.messagebox.showwarning") as warn:
            dialog._on_ok()

        warn.assert_called_once()
        self.assertIsNone(dialog.result)

    def test_duplicate_name_is_rejected(self):
        self.state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        dialog = self.build()
        dialog._name_var.set("총비용")
        dialog._listbox.selection_set(3)

        with patch("src.aggregator_dialogs.messagebox.showwarning") as warn:
            dialog._on_ok()

        warn.assert_called_once()
        self.assertIsNone(dialog.result)

    def test_an_existing_source_column_name_is_rejected(self):
        dialog = self.build()
        dialog._name_var.set("매출")
        dialog._listbox.selection_set(2)

        with patch("src.aggregator_dialogs.messagebox.showwarning"):
            dialog._on_ok()

        self.assertIsNone(dialog.result)


class TestFormulaDialog(DialogTestCase):
    def build(self):
        return _FormulaDialog(self.app.aggregator_tab, self.ui, self.state)

    def test_earlier_rule_columns_are_offered_as_operands(self):
        self.state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
        dialog = self.build()

        self.assertIn("총비용", self.state.formula_candidate_names())
        dialog._numerator_var.set("총비용")
        dialog._denominator_var.set("매출")
        dialog._name_var.set("비용율")
        dialog._on_ok()

        self.assertEqual(dialog.result.numerator_column, "총비용")
        self.assertEqual(dialog.result.multiplier, 1.0)

    def test_percent_is_stored_as_a_ratio_not_times_100(self):
        """The ×100 that produced 2746.76% cells must not come back."""
        dialog = self.build()
        dialog._name_var.set("이익율")
        dialog._numerator_var.set("매출")
        dialog._denominator_var.set("수량")

        dialog._on_ok()

        self.assertEqual(dialog.result.format_type, "percent")
        self.assertEqual(dialog.result.multiplier, 1.0)

    def test_multiplier_applies_only_to_the_plain_number_format(self):
        dialog = self.build()
        dialog._name_var.set("배수")
        dialog._numerator_var.set("매출")
        dialog._denominator_var.set("수량")
        dialog._format_var.set(self.ui("agg_dlg_format_number"))
        dialog._multiplier_var.set("1000")

        dialog._on_ok()

        self.assertEqual(dialog.result.format_type, "number")
        self.assertEqual(dialog.result.multiplier, 1000.0)

    def test_ratio_format_ignores_the_multiplier_box(self):
        dialog = self.build()
        dialog._name_var.set("비율")
        dialog._numerator_var.set("매출")
        dialog._denominator_var.set("수량")
        dialog._format_var.set(self.ui("agg_dlg_format_ratio"))
        dialog._multiplier_var.set("1000")

        dialog._on_ok()

        self.assertEqual(dialog.result.format_type, "ratio")
        self.assertEqual(dialog.result.multiplier, 1.0)

    def test_non_numeric_multiplier_falls_back_to_one(self):
        dialog = self.build()
        dialog._name_var.set("이익율")
        dialog._numerator_var.set("매출")
        dialog._denominator_var.set("수량")
        dialog._format_var.set(self.ui("agg_dlg_format_number"))
        dialog._multiplier_var.set("삼백")

        dialog._on_ok()

        self.assertEqual(dialog.result.multiplier, 1.0)

    def test_multiplier_box_is_hidden_unless_the_format_needs_it(self):
        dialog = self.build()
        self.assertEqual(dialog._multiplier_entry.winfo_manager(), "")

        dialog._format_var.set(self.ui("agg_dlg_format_number"))
        dialog._on_format_changed()
        self.assertEqual(dialog._multiplier_entry.winfo_manager(), "grid")

        dialog._format_var.set(self.ui("agg_dlg_format_percent"))
        dialog._on_format_changed()
        self.assertEqual(dialog._multiplier_entry.winfo_manager(), "")

    def test_missing_denominator_is_rejected(self):
        dialog = self.build()
        dialog._name_var.set("이익율")
        dialog._numerator_var.set("매출")

        with patch("src.aggregator_dialogs.messagebox.showwarning") as warn:
            dialog._on_ok()

        warn.assert_called_once()
        self.assertIsNone(dialog.result)


class TestFilterDialog(DialogTestCase):
    def build(self, samples=None):
        return _FilterDialog(self.app.aggregator_tab, self.ui, COLUMNS, samples or {})

    def test_single_value_condition(self):
        dialog = self.build()
        dialog._column_var.set("디비전")
        dialog._operator_var.set("==")
        dialog._value_var.set("Mobile")

        dialog._on_ok()

        self.assertEqual(dialog.result, FilterCondition("디비전", "==", "Mobile"))

    def test_in_operator_splits_on_commas(self):
        dialog = self.build()
        dialog._column_var.set("디비전")
        dialog._operator_var.set("in")
        dialog._value_var.set(" 식품 , 생활 ,, ")

        dialog._on_ok()

        self.assertEqual(dialog.result.value, ["식품", "생활"])

    def test_empty_value_is_rejected(self):
        dialog = self.build()
        dialog._column_var.set("디비전")
        dialog._value_var.set("   ")

        with patch("src.aggregator_dialogs.messagebox.showwarning") as warn:
            dialog._on_ok()

        warn.assert_called_once()
        self.assertIsNone(dialog.result)

    def test_sample_values_follow_the_selected_column(self):
        dialog = self.build({"디비전": ["Mobile", "TV"], "매출": ["100"]})
        dialog._column_var.set("디비전")
        dialog._on_column_changed()

        self.assertEqual(list(dialog._value_combo["values"]), ["Mobile", "TV"])


class TestPreviewDialog(DialogTestCase):
    def test_renders_the_cells_as_given_and_right_aligns_numbers(self):
        dialog = PreviewDialog(
            self.root, self.ui, ["디비전", "매출"], [["TV", "1,000"], ["Mobile", "2,500"]], numeric_columns=["매출"]
        )
        try:
            rows = [
                tuple(str(value) for value in dialog.table.item(item, "values"))
                for item in dialog.table.get_children()
            ]
            self.assertEqual(rows, [("TV", "1,000"), ("Mobile", "2,500")])
            self.assertEqual(str(dialog.table.column("매출", "anchor")), "e")
            self.assertEqual(str(dialog.table.column("디비전", "anchor")), "w")

            dialog.set_summary("42 rows")
            self.assertEqual(dialog.summary.cget("text"), "42 rows")
        finally:
            dialog.destroy()


class TestDescribeFilter(unittest.TestCase):
    def test_scalar_and_list_values(self):
        self.assertEqual(describe_filter(FilterCondition("국가", "==", "KR")), "국가 == KR")
        self.assertEqual(
            describe_filter(FilterCondition("디비전", "in", ["식품", "생활"])),
            "디비전 in 식품, 생활",
        )


if __name__ == "__main__":
    unittest.main()
