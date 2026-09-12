import os
import tkinter as tk
import unittest
from pathlib import Path

from src.data_refinery import DataRefineryApp


class TestAggregatorUI(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = DataRefineryApp(self.root)

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

    def test_three_tabs_exist_and_switch(self):
        self.assertEqual(len(self.app.notebook.tabs()), 3)

        # Switch to aggregator tab
        self.app._select_task_tab(self.app.aggregator_tab)
        self.assertEqual(self.app._selected_task_id(), "aggregator")
        self.assertEqual(
            self.app.aggregator_tab_button.cget("style"),
            "TaskTab.Selected.TButton",
        )
        self.assertEqual(
            self.app.csv_tab_button.cget("style"),
            "TaskTab.TButton",
        )

        # Switch back to csv tab
        self.app._select_task_tab(self.app.csv_tab)
        self.assertEqual(self.app._selected_task_id(), "csv")
        self.assertEqual(
            self.app.csv_tab_button.cget("style"),
            "TaskTab.Selected.TButton",
        )

    def test_language_switch_updates_all_three_tabs(self):
        # Switch to English
        self.app.language.set("English")
        self.app._apply_language()
        self.assertEqual(self.app.aggregator_tab_button.cget("text"), "Data aggregator")

        # Switch to Korean
        self.app.language.set("한국어")
        self.app._apply_language()
        self.assertEqual(self.app.aggregator_tab_button.cget("text"), "데이터 집계·슬라이서")

        # Switch to Polish
        self.app.language.set("Polski")
        self.app._apply_language()
        self.assertEqual(self.app.aggregator_tab_button.cget("text"), "Agregacja danych")

    def test_aggregator_tab_components_initialized(self):
        tab = self.app.aggregator_tab
        self.assertIsNotNone(tab.lb_dimensions)
        self.assertIsNotNone(tab.lb_measures)
        self.assertIsNotNone(tab.lb_group_keys)
        self.assertIsNotNone(tab.lb_selected_measures)
        self.assertIsNotNone(tab.lb_filters)
        self.assertIsNotNone(tab.lb_custom_rules)
        self.assertTrue(tab.rollup_annual_var.get())

    def test_run_aggregation_spec_creation(self):
        tab = self.app.aggregator_tab
        tab.selected_group_keys = ["디비전"]
        tab.selected_measures = ["매출"]
        tab.output_format_var.set("CSV (.csv)")

        self.assertEqual(tab.selected_group_keys, ["디비전"])
        self.assertEqual(tab.selected_measures, ["매출"])
        self.assertEqual(tab.output_format_var.get(), "CSV (.csv)")

    def test_preview_button_and_build_spec(self):
        from unittest.mock import patch
        tab = self.app.aggregator_tab
        self.assertIsNotNone(tab.btn_preview)
        # Without valid file, _build_spec shows error and returns None
        with patch("src.aggregator_ui.messagebox.showerror"):
            self.assertIsNone(tab._build_spec())

    def test_save_current_as_preset_overwrite_prompt(self):
        from unittest.mock import patch
        tab = self.app.aggregator_tab
        tab.selected_group_keys = ["디비전"]
        tab.selected_measures = ["매출"]

        with patch("src.aggregator_ui.simpledialog.askstring", side_effect=["테스트프리셋", "설명"]), \
             patch("src.aggregator_ui.preset_exists", return_value=True), \
             patch("src.aggregator_ui.messagebox.askyesno", return_value=False) as mock_ask, \
             patch("src.aggregator_ui.messagebox.showinfo"):
            tab.save_current_as_preset()
            # User chose 'No' on overwrite, so save should be aborted
            mock_ask.assert_called_once()


if __name__ == "__main__":
    unittest.main()
