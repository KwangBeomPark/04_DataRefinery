"""Unit tests for PySide6 AggregatorTab."""

import csv
import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from src.qt.app import create_or_get_app
from src.qt.tabs.aggregator.tab import AggregatorTab


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


if __name__ == "__main__":
    unittest.main()
