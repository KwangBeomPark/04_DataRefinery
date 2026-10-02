"""Regression cases found by the final review of dataset publication."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import duckdb

from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.dataset_engine import DatasetEngine, DatasetValidationError


class DatasetPublicationRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        self.input.mkdir()
        self.registry = DatasetRegistry(self.root / "storage")
        self.dataset = DatasetDefinition.create_new(
            "TV / 상세", str(self.input), str(self.root / "public"),
            period_column="period", numeric_columns=["sales"],
        )
        self.registry.save_dataset(self.dataset)
        self.engine = DatasetEngine(self.dataset, self.registry)

    def write(self, period="2026-04", encoding="utf-8-sig", filename="data.csv"):
        path = self.input / filename
        with path.open("w", encoding=encoding, newline="") as stream:
            csv.writer(stream).writerows([
                ["period", "model", "sales"], [period, "000123", "10.50"]
            ])
        return path

    def test_invalid_calendar_month_cannot_be_approved(self):
        self.write("2026-13")
        result = self.engine.accumulate_and_inspect()
        self.assertFalse(result.is_valid)
        with self.assertRaises(DatasetValidationError):
            self.engine.approve_inspection(result.approval_token)

    def test_unconfigured_identifier_keeps_leading_zeros(self):
        self.write()
        self.engine.accumulate_and_inspect()
        with duckdb.connect(str(self.engine.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT model FROM dataset_records").fetchone()[0], "000123")

    def test_korean_legacy_encoding_without_extension_download(self):
        self.dataset.encoding = "cp949"
        with (self.input / "korean.csv").open("w", encoding="cp949", newline="") as stream:
            csv.writer(stream).writerows([
                ["period", "model", "sales"], ["2026-04", "냉장고001", "10.50"]
            ])
        result = self.engine.accumulate_and_inspect()
        self.assertTrue(result.is_valid)
        self.assertEqual(result.after_row_count, 1)

    def test_apostrophe_in_filename_and_publish_folder(self):
        self.dataset.publish_folder = str(self.root / "O'Connor")
        self.registry.save_dataset(self.dataset)
        self.write(filename="O'Connor.csv")
        result = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(result.approval_token)
        published = self.engine.publish_dataset()
        self.assertTrue(Path(published["current_csv_absolute"]).exists())

    def test_rename_keeps_existing_excel_connection_path(self):
        self.write()
        result = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(result.approval_token)
        before = self.engine.publish_dataset()["current_csv_absolute"]
        self.dataset.name = "바뀐 이름"
        self.registry.save_dataset(self.dataset)
        result = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(result.approval_token)
        after = self.engine.publish_dataset()["current_csv_absolute"]
        self.assertEqual(before, after)

    def test_failed_first_publication_leaves_no_public_data(self):
        self.write()
        result = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(result.approval_token)
        with mock.patch.object(self.registry, "save_dataset", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.engine.publish_dataset()
        self.assertFalse(self.dataset.published_csv_path().exists())
        self.assertFalse((Path(self.dataset.publish_folder) / "manifest.json").exists())
        self.assertIsNone(self.dataset.last_published_at)
