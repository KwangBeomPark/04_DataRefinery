"""Tests for dataset configuration and registry management."""

import tempfile
import unittest
from pathlib import Path

from src.dataset_config import DatasetDefinition, DatasetRegistry


class TestDatasetConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = Path(self.temp_dir.name)
        self.registry = DatasetRegistry(storage_dir=self.storage_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_and_save_dataset(self):
        ds = DatasetDefinition.create_new(
            name="거래선 손익",
            input_folder=str(self.storage_dir / "input"),
            publish_folder=str(self.storage_dir / "publish"),
            period_column="매출월",
            period_format="YYYY-MM",
            numeric_columns=["매출액", "영업이익"],
            key_columns=["거래선코드", "상품코드"],
            column_types={"거래선코드": "VARCHAR", "상품코드": "VARCHAR"},
        )

        self.registry.save_dataset(ds)

        # Verify saved list
        all_ds = self.registry.list_datasets()
        self.assertEqual(len(all_ds), 1)
        loaded = all_ds[0]
        self.assertEqual(loaded.name, "거래선 손익")
        self.assertEqual(loaded.period_column, "매출월")
        self.assertEqual(loaded.numeric_columns, ["매출액", "영업이익"])
        self.assertEqual(loaded.key_columns, ["거래선코드", "상품코드"])
        self.assertFalse(loaded.inspection_approved)

    def test_update_existing_dataset(self):
        ds = DatasetDefinition.create_new(
            name="TV 상세",
            input_folder=str(self.storage_dir / "in_tv"),
            publish_folder=str(self.storage_dir / "pub_tv"),
        )
        self.registry.save_dataset(ds)

        # Modify and re-save
        ds.numeric_columns = ["수량", "금액"]
        ds.inspection_approved = True
        self.registry.save_dataset(ds)

        retrieved = self.registry.get_dataset(ds.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.numeric_columns, ["수량", "금액"])
        self.assertTrue(retrieved.inspection_approved)

    def test_delete_dataset(self):
        ds1 = DatasetDefinition.create_new(name="DS1", input_folder="in1", publish_folder="pub1")
        ds2 = DatasetDefinition.create_new(name="DS2", input_folder="in2", publish_folder="pub2")
        self.registry.save_dataset(ds1)
        self.registry.save_dataset(ds2)

        self.assertEqual(len(self.registry.list_datasets()), 2)
        deleted = self.registry.delete_dataset(ds1.id)
        self.assertTrue(deleted)
        self.assertEqual(len(self.registry.list_datasets()), 1)
        self.assertIsNone(self.registry.get_dataset(ds1.id))
        self.assertIsNotNone(self.registry.get_dataset(ds2.id))

    def test_duplicate_publish_folder_blocked(self):
        pub_path = str(self.storage_dir / "common_pub")
        ds1 = DatasetDefinition.create_new(name="DS1", input_folder="in1", publish_folder=pub_path)
        ds2 = DatasetDefinition.create_new(name="DS2", input_folder="in2", publish_folder=pub_path)
        self.registry.save_dataset(ds1)

        with self.assertRaises(ValueError):
            self.registry.save_dataset(ds2)

    def test_fingerprint_changes_on_config_modification(self):
        ds = DatasetDefinition.create_new(
            name="DS",
            input_folder="in",
            publish_folder="pub",
            numeric_columns=["매출"],
        )
        fp1 = ds.get_fingerprint()
        ds.numeric_columns = ["매출", "이익"]
        fp2 = ds.get_fingerprint()
        self.assertNotEqual(fp1, fp2)


if __name__ == "__main__":
    unittest.main()
