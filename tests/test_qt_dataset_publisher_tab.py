"""Unit tests for PySide6 DatasetPublisherTab."""

import csv
import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"


from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.qt.app import create_or_get_app
from src.qt.tabs.dataset.tab import DatasetPublisherTab


class TestQtDatasetPublisherTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.storage = self.root / "storage"
        self.registry = DatasetRegistry(self.storage)

        self.input_dir = self.root / "input"
        self.input_dir.mkdir()
        self.pub_dir = self.root / "public"
        self.pub_dir.mkdir()

        # Write sample file
        f1 = self.input_dir / "data_2026_01.csv"
        with open(f1, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["period", "product_code", "sales"])
            w.writerow(["2026-01", "P001", "100.5"])
            w.writerow(["2026-01", "P002", "200.0"])

        self.ds = DatasetDefinition.create_new(
            name="Sample Sales",
            input_folder=str(self.input_dir),
            publish_folder=str(self.pub_dir),
            period_column="period",
            key_columns=["product_code"],
            numeric_columns=["sales"],
        )
        self.registry.save_dataset(self.ds)

    def test_tab_loads_and_selects_dataset(self):
        tab = DatasetPublisherTab(registry=self.registry)
        self.assertEqual(tab._list_model.rowCount(), 1)
        self.assertIsNotNone(tab.current_dataset)
        self.assertEqual(tab.current_dataset.name, "Sample Sales")

        # Check scan executed on selection
        self.assertIsNotNone(tab.last_scan)
        self.assertEqual(len(tab.last_scan.files), 1)
        self.assertIn("2026-01", tab.last_scan.new_periods)

    def test_accumulation_and_publish_flow(self):
        tab = DatasetPublisherTab(registry=self.registry)
        # Direct engine accumulation check
        res = tab.current_engine.accumulate_and_inspect()
        self.assertTrue(res.is_valid)
        self.assertEqual(res.after_row_count, 2)

        # Approve and publish
        tab.current_engine.approve_inspection(res.approval_token)
        tab.current_engine.publish_dataset()
        pub_csv = tab.current_dataset.published_csv_path()
        self.assertTrue(pub_csv.exists())
        self.assertIn("P001", pub_csv.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
