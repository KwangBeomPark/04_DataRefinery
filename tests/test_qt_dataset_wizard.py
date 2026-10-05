"""Unit tests for Qt DatasetWizardDialog and related models."""

import csv
import os
import tempfile
import unittest
from pathlib import Path

# Enforce offscreen Qt rendering for headless testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.dataset_config import DatasetDefinition
from src.qt.app import create_or_get_app
from src.qt.tabs.dataset.models import ColumnRoleTableModel, DatasetFileTableModel
from src.qt.tabs.dataset.wizard import DatasetWizardDialog


class TestQtDatasetWizard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.input_dir = Path(self.temp_dir.name) / "input"
        self.input_dir.mkdir()

        # Create sample files
        self.f1 = self.input_dir / "sales_2026_01.csv"
        with open(self.f1, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["period", "country", "model_code", "sales_qty", "revenue", "note"])
            for i in range(100):
                writer.writerow(["2026-01", "KR", f"MD_{i}", str(10 + i), str(100.5 * i), "test"])

        self.f2 = self.input_dir / "sales_2026_02.csv"
        with open(self.f2, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["period", "country", "model_code", "sales_qty", "revenue", "note"])
            for i in range(50):
                writer.writerow(["2026-02", "PL", f"MD_{i}", str(5 + i), str(50.0 * i), "test"])

        self.f_ignore = self.input_dir / "backup_old.csv"
        with open(self.f_ignore, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["period", "country", "model_code", "sales_qty", "revenue", "note"])
            writer.writerow(["2025-12", "KR", "MD_1", "1", "10", "old"])

    def test_file_table_model(self):
        wizard = DatasetWizardDialog()
        wizard._picker_input.set_path(str(self.input_dir))
        wizard._trigger_rescan()

        model = wizard._file_model
        self.assertEqual(model.rowCount(), 3)

        # Apply exclude keyword "backup"
        wizard._chip_exclude.add_chip("backup")
        self.assertEqual(model.rowCount(), 2)
        included_files = model.get_included_files()
        self.assertEqual(len(included_files), 2)
        self.assertTrue(all("backup" not in f.file_name for f in included_files))

    def test_column_role_assignment_and_auto_guess(self):
        wizard = DatasetWizardDialog()
        wizard._edit_name.setText("Test TV Sales")
        wizard._picker_input.set_path(str(self.input_dir))
        wizard._chip_exclude.add_chip("backup")
        wizard._trigger_rescan()

        # Move to step 2
        wizard._switch_to_step(1)

        col_model = wizard._col_model
        self.assertEqual(col_model.rowCount(), 6)

        # Check Auto-Guess result
        summary = col_model.get_role_summary()
        self.assertEqual(summary["period"], "period")
        self.assertIn("model_code", summary["keys"])
        self.assertIn("sales_qty", summary["numerics"])
        self.assertIn("revenue", summary["numerics"])

        # Manually assign country (row 1) as key to test compound key assignment
        col_model.set_role_for_row(1, "key")
        summary2 = col_model.get_role_summary()
        self.assertIn("country", summary2["keys"])
        self.assertIn("model_code", summary2["keys"])

        # Check diagnostics
        wizard._update_column_diagnostics()
        self.assertTrue(wizard._badge_key_uniqueness.text().startswith("✓"))

    def test_wizard_save_creates_dataset_definition(self):
        wizard = DatasetWizardDialog()
        wizard._edit_name.setText("Master Dataset")
        wizard._picker_input.set_path(str(self.input_dir))
        wizard._chip_include.add_chip("sales")
        wizard._trigger_rescan()

        # Step 1 -> Step 2 -> Step 3
        wizard._switch_to_step(1)
        # Assign country as key as well
        wizard._col_model.set_role_for_row(1, "key")
        wizard._switch_to_step(2)

        # Trigger save
        wizard._on_save_clicked()
        res = wizard.get_dataset()
        self.assertIsNotNone(res)
        self.assertEqual(res.name, "Master Dataset")
        self.assertEqual(res.period_column, "period")
        self.assertIn("country", res.key_columns)
        self.assertIn("model_code", res.key_columns)
        self.assertIn("sales_qty", res.numeric_columns)
        self.assertEqual(res.include_keywords, ["sales"])


if __name__ == "__main__":
    unittest.main()
