"""Comprehensive test suite for DatasetEngine.

Validates:
- New period accumulation
- Modified period replacement with preservation of other periods
- Duplicate prevention on unchanged file re-runs
- Schema preservation and leading zero retention for identifiers
- Transaction rollback on cancellation or error
- Invalidation of previous approval when new accumulation occurs
- Block publishing without valid inspection approval
- Safe atomic publishing to shared folder (UTF-8 BOM, manifest, versions)
- File lock error handling and backup preservation
"""

from __future__ import annotations

import csv
import tempfile
import threading
import unittest
from pathlib import Path

from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.dataset_engine import (
    DatasetEngine,
    DatasetError,
    DatasetPublishLockError,
    DatasetValidationError,
)


def write_test_csv(file_path: Path, rows: list[list[str]], encoding: str = "utf-8-sig"):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", newline="", encoding=encoding) as f:
        writer = csv.writer(f)
        for r in rows:
            writer.writerow(r)


class TestDatasetEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

        self.storage_dir = self.base_dir / "storage"
        self.input_dir = self.base_dir / "input"
        self.publish_dir = self.base_dir / "publish"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.publish_dir.mkdir(parents=True, exist_ok=True)

        self.registry = DatasetRegistry(storage_dir=self.storage_dir)

        self.dataset = DatasetDefinition.create_new(
            name="Trade_Profit",
            input_folder=str(self.input_dir),
            publish_folder=str(self.publish_dir),
            period_column="기준년월",
            period_format="YYYY-MM",
            numeric_columns=["매출액", "영업이익"],
            key_columns=["거래선코드", "상품코드"],
            column_types={
                "거래선코드": "VARCHAR",
                "상품코드": "VARCHAR",
                "기준년월": "VARCHAR",
            },
        )
        self.registry.save_dataset(self.dataset)
        self.engine = DatasetEngine(self.dataset, self.registry)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_period_accumulation(self):
        """Verify adding new monthly periods accumulates correctly."""
        # 1. First period: 2024-01 (2 rows)
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1000", "200"],
                ["00456", "P002", "2024-01", "2000", "400"],
            ],
        )

        res1 = self.engine.accumulate_and_inspect()
        self.assertEqual(res1.after_row_count, 2)
        self.assertEqual(res1.all_periods, ["2024-01"])
        self.assertEqual(res1.numeric_totals_after["매출액"], 3000.0)
        self.assertEqual(res1.numeric_totals_after["영업이익"], 600.0)
        self.assertTrue(res1.is_valid)

        # 2. Second period: 2024-02 (2 rows)
        file_02 = self.input_dir / "sales_202402.csv"
        write_test_csv(
            file_02,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-02", "1500", "300"],
                ["00789", "P003", "2024-02", "2500", "500"],
            ],
        )

        # Scan should detect 2024-02 as new period, 2024-01 as unchanged
        scan = self.engine.scan_input_folder()
        self.assertEqual(scan.new_periods, ["2024-02"])
        self.assertEqual(scan.unchanged_files, [file_01.name])

        res2 = self.engine.accumulate_and_inspect()
        self.assertEqual(res2.after_row_count, 4)
        self.assertEqual(res2.all_periods, ["2024-01", "2024-02"])
        self.assertEqual(res2.numeric_totals_after["매출액"], 7000.0)
        self.assertEqual(res2.numeric_totals_after["영업이익"], 1400.0)
        self.assertEqual(res2.added_row_count, 2)

    def test_modified_period_replaces_only_target_period(self):
        """Verify modified period file replaces only that period and preserves other periods."""
        # Setup 2024-01 and 2024-02
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1000", "200"],
                ["00456", "P002", "2024-01", "2000", "400"],
            ],
        )
        file_02 = self.input_dir / "sales_202402.csv"
        write_test_csv(
            file_02,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-02", "1500", "300"],
            ],
        )
        self.engine.accumulate_and_inspect()

        # Now replace 2024-02 file with 3 rows and updated numbers
        write_test_csv(
            file_02,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-02", "5000", "1000"],
                ["00456", "P002", "2024-02", "3000", "600"],
                ["00999", "P009", "2024-02", "2000", "400"],
            ],
        )

        scan = self.engine.scan_input_folder()
        self.assertEqual(scan.modified_periods, ["2024-02"])
        self.assertEqual(scan.unchanged_files, [file_01.name])

        res = self.engine.accumulate_and_inspect()

        # Total rows should be: 2024-01 (2 rows) + 2024-02 (3 rows) = 5 rows
        self.assertEqual(res.after_row_count, 5)
        self.assertEqual(res.all_periods, ["2024-01", "2024-02"])

        # Check that 2024-01 rows are completely intact
        pm_01 = next(pm for pm in res.period_metrics if pm.period == "2024-01")
        self.assertEqual(pm_01.row_count, 2)
        self.assertEqual(pm_01.measures["매출액"], 3000.0)

        # Check 2024-02 rows are updated
        pm_02 = next(pm for pm in res.period_metrics if pm.period == "2024-02")
        self.assertEqual(pm_02.row_count, 3)
        self.assertEqual(pm_02.measures["매출액"], 10000.0)
        self.assertEqual(pm_02.measures["영업이익"], 2000.0)

        # Total 매출액 = 3000 + 10000 = 13000
        self.assertEqual(res.numeric_totals_after["매출액"], 13000.0)

    def test_unchanged_file_rerun_does_not_duplicate(self):
        """Re-running with unchanged files skips processing and produces no duplicates."""
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1000", "200"],
            ],
        )
        res1 = self.engine.accumulate_and_inspect()
        self.assertEqual(res1.after_row_count, 1)

        # Re-run immediately
        scan = self.engine.scan_input_folder()
        self.assertEqual(scan.unchanged_files, [file_01.name])
        self.assertFalse(scan.has_changes)

        res2 = self.engine.accumulate_and_inspect()
        self.assertEqual(res2.after_row_count, 1)
        self.assertEqual(res2.numeric_totals_after["매출액"], 1000.0)

    def test_leading_zero_preservation(self):
        """Ensure codes with leading zeros (e.g. '00123') are preserved as strings and not truncated to numbers."""
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00001", "0099", "2024-01", "100", "10"],
            ],
        )
        self.engine.accumulate_and_inspect()

        conn = self.engine._get_connection()
        row = conn.execute("SELECT 거래선코드, 상품코드 FROM dataset_records LIMIT 1").fetchone()
        conn.close()

        self.assertEqual(row[0], "00001")
        self.assertEqual(row[1], "0099")

    def test_transaction_rollback_on_cancel(self):
        """Verify transaction rollback when user cancels operation."""
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1000", "200"],
            ],
        )
        self.engine.accumulate_and_inspect()

        # Add second file but trigger cancel
        file_02 = self.input_dir / "sales_202402.csv"
        write_test_csv(
            file_02,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P002", "2024-02", "5000", "1000"],
            ],
        )

        cancel_ev = threading.Event()
        cancel_ev.set()  # Cancel immediately

        with self.assertRaises(DatasetError):
            self.engine.accumulate_and_inspect(cancel_event=cancel_ev)

        # Existing 2024-01 data must remain completely intact
        existing = self.engine.get_existing_periods()
        self.assertEqual(existing, ["2024-01"])

        conn = self.engine._get_connection()
        cnt = conn.execute("SELECT count(*) FROM dataset_records").fetchone()[0]
        conn.close()
        self.assertEqual(cnt, 1)

    def test_approval_invalidation_and_publishing_gate(self):
        """Test that adding new data invalidates approval, and unapproved publishing is blocked."""
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1000", "200"],
            ],
        )
        res1 = self.engine.accumulate_and_inspect()

        # Initially, inspection is NOT approved
        self.assertFalse(self.dataset.inspection_approved)

        # Trying to publish should fail with DatasetValidationError
        with self.assertRaises(DatasetValidationError):
            self.engine.publish_dataset()

        # Approve inspection
        self.engine.approve_inspection(res1.approval_token)
        self.assertTrue(self.dataset.inspection_approved)

        # Publish should now succeed
        manifest = self.engine.publish_dataset()
        self.assertEqual(manifest["version"], "v001")
        self.assertEqual(manifest["row_count"], 1)

        # Now add new data (2024-02)
        file_02 = self.input_dir / "sales_202402.csv"
        write_test_csv(
            file_02,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00456", "P002", "2024-02", "2000", "400"],
            ],
        )
        res2 = self.engine.accumulate_and_inspect()

        # Approval must be VOIDED!
        self.assertFalse(self.dataset.inspection_approved)
        self.assertNotEqual(res2.approval_token, res1.approval_token)

        # Publishing again without approval must fail!
        with self.assertRaises(DatasetValidationError):
            self.engine.publish_dataset()

    def test_safe_publishing_structure_and_utf8_bom(self):
        """Verify published CSV has UTF-8 BOM, manifest is updated, and versions are kept."""
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1000", "200"],
            ],
        )
        res = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(res.approval_token)

        self.engine.publish_dataset()
        current_csv = self.publish_dir / "current" / "Trade_Profit.csv"
        versions_dir = self.publish_dir / "versions"
        manifest_file = self.publish_dir / "manifest.json"

        self.assertTrue(current_csv.exists())
        self.assertTrue(manifest_file.exists())
        self.assertEqual(len(list(versions_dir.glob("*.csv"))), 1)

        # Check BOM in current_csv
        with open(current_csv, "rb") as f:
            bom = f.read(3)
        self.assertEqual(bom, b"\xef\xbb\xbf")

    def test_file_lock_error_preserves_backup(self):
        """Simulate file lock on current CSV and verify backup preservation and clear error."""
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1000", "200"],
            ],
        )
        res = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(res.approval_token)
        self.engine.publish_dataset()

        current_csv = self.publish_dir / "current" / "Trade_Profit.csv"
        self.assertTrue(current_csv.exists())

        # Lock the current_csv file by opening with exclusive write mode
        with open(current_csv, "r+b"):
            # Try publishing again while file is held open
            # Windows raises PermissionError when attempting to replace locked file
            # In our code, this should be caught and raise DatasetPublishLockError
            try:
                # We need approval again for second publish
                res2 = self.engine.accumulate_and_inspect()
                self.engine.approve_inspection(res2.approval_token)
                self.engine.publish_dataset()
            except (DatasetPublishLockError, PermissionError) as expected:
                self.assertIn("사용 중", str(expected))

        # Check that target current_csv is still present and valid
        self.assertTrue(current_csv.exists())

    def test_multi_month_csv_accumulation_replaces_all_target_periods(self):
        """Verify annual/multi-month CSV accurately replaces all target periods and preserves others."""
        # 1. Prior data: 2023-12 (1 row), 2024-01 (1 row), 2024-02 (1 row)
        write_test_csv(
            self.input_dir / "sales_202312.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["C001", "P001", "2023-12", "1000", "100"]],
        )
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["C001", "P001", "2024-01", "500", "50"]],
        )
        write_test_csv(
            self.input_dir / "sales_202402.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["C001", "P001", "2024-02", "800", "80"]],
        )
        res1 = self.engine.accumulate_and_inspect()
        self.assertEqual(res1.after_row_count, 3)
        self.assertEqual(sorted(res1.all_periods), ["2023-12", "2024-01", "2024-02"])

        # 2. Drop annual file: contains 2024-01 (revised: 1500), 2024-02 (revised: 2000), 2024-03 (new: 3000)
        (self.input_dir / "sales_202401.csv").unlink()
        (self.input_dir / "sales_202402.csv").unlink()
        write_test_csv(
            self.input_dir / "sales_annual_2024.csv",
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["C001", "P001", "2024-01", "1500", "150"],
                ["C001", "P001", "2024-02", "2000", "200"],
                ["C001", "P001", "2024-03", "3000", "300"],
            ],
        )

        scan = self.engine.scan_input_folder()
        self.assertEqual(scan.modified_periods, ["2024-01", "2024-02"])
        self.assertEqual(scan.new_periods, ["2024-03"])

        res2 = self.engine.accumulate_and_inspect()
        self.assertEqual(res2.after_row_count, 4)
        self.assertEqual(sorted(res2.all_periods), ["2023-12", "2024-01", "2024-02", "2024-03"])

        # Check DuckDB rows for 2024-02 to guarantee no duplication
        conn = self.engine._get_connection()
        p02_rows = conn.execute("SELECT count(*), sum(TRY_CAST(매출액 AS DOUBLE)) FROM dataset_records WHERE 기준년월 = '2024-02'").fetchall()[0]
        conn.close()
        self.assertEqual(p02_rows[0], 1)
        self.assertEqual(p02_rows[1], 2000.0)
        self.assertEqual(res2.numeric_totals_after["매출액"], 7500.0)

    def test_formatted_numbers_with_commas_summed_correctly(self):
        """Ensure currency/numeric strings with commas (e.g. '1,234,567') sum correctly."""
        write_test_csv(
            self.input_dir / "sales_commas.csv",
            [
                ["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
                ["00123", "P001", "2024-01", "1,234,567", "10,000"],
                ["00456", "P002", "2024-01", "2,500,000", "20,000"],
            ],
        )
        res = self.engine.accumulate_and_inspect()
        self.assertEqual(res.numeric_totals_after["매출액"], 3734567.0)
        self.assertEqual(res.numeric_totals_after["영업이익"], 30000.0)

    def test_approval_token_binding_blocks_tampered_db_publishing(self):
        """Test that altering DuckDB after approval voids publishing."""
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["00123", "P001", "2024-01", "1000", "200"]],
        )
        res = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(res.approval_token)

        # Alter DB after approval
        conn = self.engine._get_connection()
        conn.execute("INSERT INTO dataset_records VALUES ('00999', 'P999', '2024-01', '999999', '99999')")
        conn.close()

        with self.assertRaises(DatasetValidationError):
            self.engine.publish_dataset()

    def test_orphan_version_prevented_on_publish_lock(self):
        """Verify no orphan version file is left behind if publish fails on current file lock."""
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["00123", "P001", "2024-01", "1000", "200"]],
        )
        res1 = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(res1.approval_token)
        self.engine.publish_dataset()

        versions_dir = self.publish_dir / "versions"
        v_count_before = len(list(versions_dir.glob("*.csv")))
        self.assertEqual(v_count_before, 1)

        # Ingest new data
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["00123", "P001", "2024-01", "2000", "400"]],
        )
        res2 = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(res2.approval_token)

        current_csv = self.publish_dir / "current" / "Trade_Profit.csv"
        with open(current_csv, "r+b"):
            try:
                self.engine.publish_dataset()
            except (DatasetPublishLockError, PermissionError):
                pass

        v_count_after = len(list(versions_dir.glob("*.csv")))
        self.assertEqual(v_count_after, v_count_before)

    def test_same_period_multiple_files_partial_update_preserves_other_file(self):
        """Verify that updating one file in a period does not silently wipe records from another file in that same period."""
        part_a = self.input_dir / "part_a_202404.csv"
        part_b = self.input_dir / "part_b_202404.csv"

        write_test_csv(
            part_a,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-04", "10", "1"]],
        )
        write_test_csv(
            part_b,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["002", "P02", "2024-04", "20", "2"]],
        )

        res1 = self.engine.accumulate_and_inspect()
        self.assertEqual(res1.after_row_count, 2)
        self.assertEqual(res1.numeric_totals_after["매출액"], 30.0)

        # Update only part_a (part_b remains unchanged)
        write_test_csv(
            part_a,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-04", "15", "1"]],
        )

        res2 = self.engine.accumulate_and_inspect()
        # Must still have 2 rows (part_a: 15 + part_b: 20 = 35)
        self.assertEqual(res2.after_row_count, 2)
        self.assertEqual(res2.numeric_totals_after["매출액"], 35.0)

    def test_latest_inspection_snapshot_restores_cleanly(self):
        """Ensure get_latest_inspection correctly deserializes JSON snapshot without error."""
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["00123", "P001", "2024-01", "1000", "200"]],
        )
        res = self.engine.accumulate_and_inspect()
        self.assertIsNotNone(res)

        latest = self.engine.get_latest_inspection()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.approval_token, res.approval_token)
        self.assertEqual(latest.after_row_count, 1)
        self.assertEqual(latest.numeric_totals_after["매출액"], 1000.0)

    def test_archive_old_input_preserves_old_period_data(self):
        """Archiving (removing) older month CSV from input folder and adding new month must NOT delete older accumulated period."""
        file_01 = self.input_dir / "sales_202401.csv"
        write_test_csv(
            file_01,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-01", "100", "10"]],
        )
        res1 = self.engine.accumulate_and_inspect()
        self.assertEqual(res1.all_periods, ["2024-01"])
        self.assertEqual(res1.after_row_count, 1)

        # Move/delete 2024-01 file (user archived it)
        file_01.unlink()

        # Add 2024-02 file
        file_02 = self.input_dir / "sales_202402.csv"
        write_test_csv(
            file_02,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["002", "P02", "2024-02", "200", "20"]],
        )

        res2 = self.engine.accumulate_and_inspect()
        # 2024-01 must remain intact and 2024-02 added! Total 2 rows.
        self.assertEqual(res2.all_periods, ["2024-01", "2024-02"])
        self.assertEqual(res2.after_row_count, 2)
        self.assertEqual(res2.numeric_totals_after["매출액"], 300.0)

    def test_missing_file_for_target_period_blocks_half_replacement(self):
        """If modifying a period that had multiple files, removing one file must block with clear error to avoid half replacement."""
        part_a = self.input_dir / "part_a_202404.csv"
        part_b = self.input_dir / "part_b_202404.csv"

        write_test_csv(
            part_a,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-04", "10", "1"]],
        )
        write_test_csv(
            part_b,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["002", "P02", "2024-04", "20", "2"]],
        )
        res1 = self.engine.accumulate_and_inspect()
        self.assertEqual(res1.after_row_count, 2)

        # User accidentally deletes part_b and updates part_a
        part_b.unlink()
        write_test_csv(
            part_a,
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-04", "15", "1"]],
        )

        # Must raise DatasetValidationError blocking half replacement
        with self.assertRaises(DatasetValidationError) as ctx:
            self.engine.accumulate_and_inspect()
        self.assertIn("과거 기여했던 파일", str(ctx.exception))

    def test_malformed_numeric_string_fails_inspection_and_blocks_approval(self):
        """Invalid numeric values must mark inspection as invalid and block approval."""
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-01", "오류값", "10"]],
        )
        res = self.engine.accumulate_and_inspect()
        self.assertFalse(res.is_valid)
        self.assertTrue(any("숫자로 변환할 수 없는" in e for e in res.errors))

        # Approval must be rejected
        with self.assertRaises(DatasetValidationError):
            self.engine.approve_inspection(res.approval_token)

    def test_stale_approval_voided_on_config_change(self):
        """Changing dataset configuration after inspection voids approval."""
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-01", "100", "10"]],
        )
        res = self.engine.accumulate_and_inspect()
        self.assertTrue(res.is_valid)

        # Alter dataset config
        self.dataset.numeric_columns = ["매출액", "영업이익", "추가열"]

        with self.assertRaises(DatasetValidationError) as ctx:
            self.engine.approve_inspection(res.approval_token)
        self.assertIn("설정", str(ctx.exception))
        self.assertIn("변경되었습니다", str(ctx.exception))

    def test_publish_failure_injection_restores_previous_current_csv_and_manifest(self):
        """Simulate failure during manifest/log writing after current CSV replacement, verify full restoration."""
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-01", "100", "10"]],
        )
        res1 = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(res1.approval_token)
        self.engine.publish_dataset()

        current_csv = self.publish_dir / "current" / "Trade_Profit.csv"
        manifest_file = self.publish_dir / "manifest.json"
        self.assertTrue(current_csv.exists())
        self.assertTrue(manifest_file.exists())
        v1_csv_content = current_csv.read_bytes()
        v1_manifest_content = manifest_file.read_bytes()

        # Update to v2
        write_test_csv(
            self.input_dir / "sales_202401.csv",
            [["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"], ["001", "P01", "2024-01", "200", "20"]],
        )
        res2 = self.engine.accumulate_and_inspect()
        self.engine.approve_inspection(res2.approval_token)

        # Inject failure during registry save in publish_dataset
        original_save = self.registry.save_dataset

        def faulty_save(ds):
            raise IOError("Simulated disk error during save_dataset in publish")

        self.registry.save_dataset = faulty_save
        try:
            with self.assertRaises(IOError):
                self.engine.publish_dataset()
        finally:
            self.registry.save_dataset = original_save

        # Must have restored v1 current CSV and v1 manifest!
        self.assertEqual(current_csv.read_bytes(), v1_csv_content)
        self.assertEqual(manifest_file.read_bytes(), v1_manifest_content)


if __name__ == "__main__":
    unittest.main()
