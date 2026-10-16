"""Unit tests for PySide6 AggregatorTab."""

import csv
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from src.qt.app import create_or_get_app
from src.qt.tabs.aggregator.tab import AggregatorTab
from src.data_aggregator import AggregationNumericError
from src.preset_manager import load_preset, save_preset


class TestQtAggregatorTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.csv_path = Path(self.temp_dir.name) / "sales.csv"

        with open(self.csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["country", "model", "qty", "sales"])
            for i in range(20):
                w.writerow(["KR", f"M_{i % 3}", "5", "500"])

    def test_schema_load_and_field_placement(self):
        tab = AggregatorTab(language_code="ko")
        tab.picker_source.set_path(str(self.csv_path))

        # Check pools populated
        self.assertGreater(tab.list_dims.count(), 0)
        self.assertGreater(tab.list_measures.count(), 0)

        # Place first dimension into rows
        item = tab.list_dims.item(0)
        dim_name = item.text()
        tab._on_dim_double_clicked(item)
        self.assertIn(dim_name, tab.state.group_keys)
        self.assertEqual(tab.list_rows.count(), 1)

        # Place first measure into values
        m_item = tab.list_measures.item(0)
        m_name = m_item.text()
        tab._on_measure_double_clicked(m_item)
        self.assertIn(m_name, tab.state.values)
        self.assertEqual(tab.list_values.count(), 1)

        # Test Undo
        tab._on_undo()
        self.assertEqual(tab.list_values.count(), 0)

    def test_build_spec_and_preview(self):
        tab = AggregatorTab(language_code="ko")
        tab.picker_source.set_path(str(self.csv_path))

        # Setup group keys and measures
        tab.state.place_group_key("country")
        tab.state.place_group_key("model")
        tab.state.place_value("qty")
        tab.state.place_value("sales")

        spec = tab._build_spec()
        self.assertIsNotNone(spec)
        self.assertEqual(spec.group_by_keys, ["country", "model"])
        self.assertEqual(spec.measure_sums, ["qty", "sales"])

    def configured_tab(self):
        tab = AggregatorTab(language_code="ko")
        tab.picker_source.set_path(str(self.csv_path))
        tab.state.place_group_key("country")
        tab.state.place_value("sales")
        return tab

    def test_notation_preset_survives_language_changes_and_disk_roundtrip(self):
        tab = self.configured_tab()
        tab.combo_number_mode.setCurrentIndex(1)
        for language in ("pl", "en", "ko"):
            tab.apply_language(language)
            self.assertEqual(tab.combo_number_mode.currentData(), "Polish")
            self.assertEqual(tab._build_spec().number_mode, "Polish")
        with patch("src.qt.tabs.aggregator.tab.QInputDialog.getText", return_value=("sample", True)), \
             patch("src.qt.tabs.aggregator.tab.preset_exists", return_value=False), \
             patch("src.qt.tabs.aggregator.tab.save_preset", side_effect=lambda p: save_preset(p, Path(self.temp_dir.name))), \
             patch("src.qt.tabs.aggregator.tab.info_dialog"):
            tab._on_save_preset()
        loaded = load_preset("sample", Path(self.temp_dir.name))
        tab.combo_number_mode.setCurrentIndex(0)
        with patch("src.qt.tabs.aggregator.tab.list_presets", return_value=[loaded]), \
             patch("src.qt.tabs.aggregator.tab.QInputDialog.getItem", return_value=("sample", True)), \
             patch("src.qt.tabs.aggregator.tab.info_dialog"):
            tab._on_load_preset()
        self.assertEqual(tab._build_spec().number_mode, "Polish")

    def test_source_destination_is_blocked_and_existing_result_requires_confirmation(self):
        tab = self.configured_tab()
        original = self.csv_path.read_bytes()
        tab.edit_output_name.setText(self.csv_path.name)
        with patch("src.qt.tabs.aggregator.tab.error_dialog") as error:
            self.assertIsNone(tab._build_spec())
            error.assert_called_once()
        self.assertEqual(self.csv_path.read_bytes(), original)
        output = self.csv_path.parent / "result.csv"
        output.write_bytes(b"existing result")
        tab.edit_output_name.setText(output.name)
        with patch("src.qt.tabs.aggregator.tab.confirm_dialog", return_value=False), \
             patch.object(tab.job_runner, "start") as start:
            tab._on_run_clicked()
            start.assert_not_called()
        self.assertEqual(output.read_bytes(), b"existing result")

    def test_numeric_diagnostics_follow_language(self):
        tab = self.configured_tab()
        error = AggregationNumericError("sales", "English", 2, ["'1.234,56'"])
        for language, phrase in (("en", "invalid numbers"), ("ko", "맞지 않는 값"), ("pl", "błędnych liczb")):
            tab.apply_language(language)
            text = tab._aggregation_error_text(error)
            self.assertIn(phrase, text)
            self.assertIn("sales", text)
            self.assertIn("1.234,56", text)

    def test_old_preset_preserves_chosen_notation_and_small_window_scrolls_to_run(self):
        from src.preset_manager import AggregationPreset

        tab = self.configured_tab()
        tab.combo_number_mode.setCurrentIndex(1)
        with patch("src.qt.tabs.aggregator.tab.list_presets", return_value=[AggregationPreset(name="old")]), \
             patch("src.qt.tabs.aggregator.tab.QInputDialog.getItem", return_value=("old", True)), \
             patch("src.qt.tabs.aggregator.tab.info_dialog"):
            tab._on_load_preset()
        self.assertEqual(tab.combo_number_mode.currentData(), "Polish")
        tab.resize(780, 520)
        tab.show()
        self.app.processEvents()
        tab._scroll.ensureWidgetVisible(tab.btn_run)
        self.app.processEvents()
        self.assertGreater(tab._scroll.verticalScrollBar().maximum(), 0)
        position = tab.btn_run.mapTo(tab._scroll.viewport(), tab.btn_run.rect().center())
        self.assertTrue(tab._scroll.viewport().rect().contains(position))
        tab.close()


if __name__ == "__main__":
    unittest.main()
