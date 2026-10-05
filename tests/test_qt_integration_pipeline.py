"""End-to-End integration tests executing real jobs across all Qt tabs."""

import csv
import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.qt.app import create_or_get_app
from src.qt.tabs.aggregator.tab import AggregatorTab
from src.qt.tabs.csv_tab import CsvRepairTab
from src.qt.tabs.dataset.tab import DatasetPublisherTab
from src.qt.tabs.promotion_tab import PromotionTab


class TestQtIntegrationPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_dir = Path(self.temp_dir.name)

        # 1. Create Sample CSV
        self.csv_file = self.base_dir / "sales_202601.csv"
        with open(self.csv_file, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["period", "store_code", "product_code", "qty", "sales"])
            for i in range(10):
                w.writerow(["2026-01", f"S_{i % 2}", f"P_{i % 3}", "10", "1000"])

    def test_aggregator_tab_real_execution(self):
        """Test Tab 3 real aggregation execution and output file generation."""
        tab = AggregatorTab(language_code="ko")
        tab.picker_source.set_path(str(self.csv_file))

        tab.state.place_group_key("period")
        tab.state.place_group_key("store_code")
        tab.state.place_value("qty")
        tab.state.place_value("sales")

        out_csv = self.base_dir / "aggregated_test.csv"
        tab.picker_output.set_path(str(self.base_dir))
        tab.edit_output_name.setText("aggregated_test.csv")

        # Directly build spec and trigger sync execution logic
        spec = tab._build_spec()
        self.assertIsNotNone(spec)
        self.assertEqual(spec.group_by_keys, ["period", "store_code"])

        # Run worker directly to verify without thread timing
        from src.data_aggregator import aggregate_dataset
        result = aggregate_dataset(spec)
        self.assertTrue(os.path.exists(result.out_path))
        self.assertGreater(result.final_rows, 0)

        # Render result in tab
        tab._render_result(result, spec)
        self.assertIn("데이터 집계 완료", tab.lbl_status.text())

    def test_dataset_publisher_tab_real_scan(self):
        """Test Tab 4 scanning and dataset registration integration."""
        registry_dir = self.base_dir / "registry"
        registry = DatasetRegistry(storage_dir=registry_dir)

        ds = DatasetDefinition.create_new(
            name="Test Sales",
            input_folder=str(self.base_dir),
            publish_folder=str(self.base_dir / "publish"),
            period_column="period",
            key_columns=["store_code", "product_code"],
            numeric_columns=["qty", "sales"],
            file_pattern="*.csv",
        )
        registry.save_dataset(ds)

        tab = DatasetPublisherTab(registry=registry, language_code="ko")
        tab.refresh_dataset_list()
        self.assertEqual(tab._list_model.rowCount(), 1)

        # Select dataset and verify engine init
        tab._select_dataset(ds)
        self.assertIsNotNone(tab.current_engine)


if __name__ == "__main__":
    unittest.main()
