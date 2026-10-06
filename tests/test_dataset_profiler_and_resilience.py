"""Unit tests for dataset profiler, European number format parsing, encoding resilience, and schema validation."""

import tempfile
from pathlib import Path
import unittest
import duckdb

from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.dataset_engine import DatasetEngine, make_numeric_sql_expr
from src.dataset_profiler import profile_dataset_sample, check_sample_key_uniqueness


class TestDatasetProfilerAndResilience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_make_numeric_sql_expr_european_and_us(self):
        """Verify that make_numeric_sql_expr parses Polish/EU and US formats without 100x errors."""
        conn = duckdb.connect(":memory:")
        try:
            conn.execute("CREATE TABLE test_nums (raw_us VARCHAR, raw_eu VARCHAR, raw_eu_tight VARCHAR);")
            conn.execute("INSERT INTO test_nums VALUES ('1,234.56', '1 234,56', '1234,56');")

            # US format
            sql_us = make_numeric_sql_expr("raw_us", "1,234.56")
            val_us = conn.execute(f"SELECT {sql_us} FROM test_nums").fetchone()[0]
            self.assertEqual(float(val_us), 1234.56)

            # EU format with space
            sql_eu = make_numeric_sql_expr("raw_eu", "1 234,56")
            val_eu = conn.execute(f"SELECT {sql_eu} FROM test_nums").fetchone()[0]
            self.assertEqual(float(val_eu), 1234.56)

            # EU format without space (e.g. '1234,56' - previous code parsed this as 123456!)
            sql_tight = make_numeric_sql_expr("raw_eu_tight", "1 234,56")
            val_tight = conn.execute(f"SELECT {sql_tight} FROM test_nums").fetchone()[0]
            self.assertEqual(float(val_tight), 1234.56)

            # Auto format
            sql_auto_eu = make_numeric_sql_expr("raw_eu_tight", "auto")
            val_auto_eu = conn.execute(f"SELECT {sql_auto_eu} FROM test_nums").fetchone()[0]
            self.assertEqual(float(val_auto_eu), 1234.56)
        finally:
            conn.close()

    def test_profiler_auto_guess_roles(self):
        """Verify that profiler correctly detects period, keys, numerics, and codes with leading zeros."""
        csv_file = self.temp_path / "sales_sample.csv"
        content = (
            '기준년월,거래선코드,거래선명,지점코드,매출액,수량,영업이익\n'
            '2026-09,C001,Krakow Shop,0101,"1 250,50",10,"320,00"\n'
            '2026-09,C002,Warsaw Store,0102,"980,00",8,"210,50"\n'
            '2026-08,C001,Krakow Shop,0101,"1 100,00",9,"280,00"\n'
        )
        csv_file.write_text(content, encoding="utf-8")

        result = profile_dataset_sample(csv_file)
        self.assertEqual(result.total_columns, 7)
        self.assertEqual(result.suggested_period_column, "기준년월")
        self.assertEqual(result.detected_number_format, "1 234,56")

        # Period column
        p_col = next(c for c in result.columns if c.name == "기준년월")
        self.assertEqual(p_col.suggested_role, "period")

        # Key columns
        self.assertIn("거래선코드", result.suggested_key_columns)
        self.assertIn("지점코드", result.suggested_key_columns)  # has code name and 0101

        # Numeric columns
        self.assertIn("매출액", result.suggested_numeric_columns)
        self.assertIn("수량", result.suggested_numeric_columns)
        self.assertIn("영업이익", result.suggested_numeric_columns)

        # General text
        name_col = next(c for c in result.columns if c.name == "거래선명")
        self.assertEqual(name_col.suggested_role, "general")

    def test_check_sample_key_uniqueness(self):
        """Test compound key duplicate detection within sample."""
        csv_file = self.temp_path / "key_test.csv"
        # Duplicate in period 2026-09 for key C001+P1
        content = (
            "기준년월,거래선코드,상품코드,매출액\n"
            "2026-09,C001,P1,100\n"
            "2026-09,C001,P1,200\n"
            "2026-09,C002,P1,300\n"
            "2026-08,C001,P1,100\n"
        )
        csv_file.write_text(content, encoding="utf-8")

        dup_cnt, total_cnt = check_sample_key_uniqueness(
            csv_file,
            period_col="기준년월",
            key_cols=["거래선코드", "상품코드"],
        )
        self.assertEqual(dup_cnt, 1)  # 1 duplicate group in 2026-09
        self.assertEqual(total_cnt, 4)

    def test_scan_keyword_filtering_and_header_check(self):
        """Test keyword filtering and header schema validation during scan_input_folder."""
        in_dir = self.temp_path / "inputs"
        pub_dir = self.temp_path / "publish"
        in_dir.mkdir()
        pub_dir.mkdir()

        # Create files
        f1 = in_dir / "sales_PL_202609.csv"
        f1.write_text("기준년월,거래선코드,매출액\n2026-09,C1,100\n", encoding="utf-8")

        f2 = in_dir / "sales_PL_202608.csv"
        # Order mismatch (매출액 before 거래선코드)
        f2.write_text("기준년월,매출액,거래선코드\n2026-08,200,C2\n", encoding="utf-8")

        f3 = in_dir / "sales_backup_202609.csv"
        f3.write_text("기준년월,거래선코드,매출액\n2026-09,C1,100\n", encoding="utf-8")

        f4 = in_dir / "sales_PL_missing.csv"
        # Missing required 매출액
        f4.write_text("기준년월,거래선코드,기타\n2026-07,C3,X\n", encoding="utf-8")

        registry = DatasetRegistry(self.temp_path / "registry")
        ds = DatasetDefinition.create_new(
            name="TestSet",
            input_folder=str(in_dir),
            publish_folder=str(pub_dir),
            period_column="기준년월",
            key_columns=["거래선코드"],
            numeric_columns=["매출액"],
            include_keywords=["PL"],
            exclude_keywords=["backup"],
            baseline_columns=["기준년월", "거래선코드", "매출액"],
        )
        registry.save_dataset(ds)
        engine = DatasetEngine(ds, registry)

        scan = engine.scan_input_folder()

        # f3 (backup) should be filtered out
        scanned_names = [f.file_name for f in scan.files]
        self.assertNotIn("sales_backup_202609.csv", scanned_names)
        self.assertIn("sales_PL_202609.csv", scanned_names)
        self.assertIn("sales_PL_202608.csv", scanned_names)
        self.assertIn("sales_PL_missing.csv", scanned_names)

        # Header check on f1: ok
        sf1 = next(f for f in scan.files if f.file_name == "sales_PL_202609.csv")
        self.assertEqual(sf1.header_status, "ok")

        # Header check on f2: order_mismatch
        sf2 = next(f for f in scan.files if f.file_name == "sales_PL_202608.csv")
        self.assertEqual(sf2.header_status, "order_mismatch")

        # Header check on f4: missing_columns and conflict status
        sf4 = next(f for f in scan.files if f.file_name == "sales_PL_missing.csv")
        self.assertEqual(sf4.header_status, "missing_columns")
        self.assertEqual(sf4.status, "conflict")


if __name__ == "__main__":
    unittest.main()
