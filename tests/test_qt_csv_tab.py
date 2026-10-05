"""Unit tests for PySide6 CsvRepairTab."""

import csv
import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from src.qt.app import create_or_get_app
from src.qt.tabs.csv_tab import CsvRepairTab


class TestQtCsvRepairTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.input_file = Path(self.temp_dir.name) / "test_data.csv"
        with open(self.input_file, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["col1", "col2", "col3"])
            for i in range(10):
                w.writerow([f"A_{i}", "1,234.56", "2026-04-01"])

    def test_file_selection_and_auto_detect(self):
        tab = CsvRepairTab(language_code="ko")
        tab.picker_file.set_path(str(self.input_file))
        self.assertEqual(tab.edit_delim.text(), ",")
        self.assertEqual(tab.edit_cols.text(), "3")

    def test_apply_language_switches_labels(self):
        tab = CsvRepairTab(language_code="ko")
        self.assertIn("정리", tab.btn_process.text())

        tab.apply_language("en")
        self.assertIn("Clean", tab.btn_process.text())

        tab.apply_language("pl")
        self.assertIn("Uporządkuj", tab.btn_process.text())


if __name__ == "__main__":
    unittest.main()
