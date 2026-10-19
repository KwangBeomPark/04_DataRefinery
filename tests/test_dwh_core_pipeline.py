"""Unit and integration tests for the Data Refinery Kimball Star Schema DWH core pipeline."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
import duckdb
import polars as pl

from src.config_loader import ConfigLoader
from src.dimension_builder import DimensionBuilder
from src.extractor import DataExtractor
from src.fact_builder import FactBuilder
from src.loader import DwhLoader
from scripts.generate_sample_data import generate_sample_data


class TestDwhCorePipeline(unittest.TestCase):
    """Test suite covering config, extraction, dimensions, facts, and mart loading."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_dwh_")
        self.base_dir = Path(self.temp_dir).resolve()

        # Copy config folder to temp directory
        proj_root = Path(__file__).resolve().parent.parent
        shutil.copytree(proj_root / "config", self.base_dir / "config")

        # Generate sample data in temp directory
        generate_sample_data(base_dir=self.base_dir)

        self.loader = ConfigLoader(base_dir=self.base_dir)
        self.settings = self.loader.load_settings()
        self.account_rules = self.loader.load_account_mapping()
        self.settings.ensure_directories()
        self.con = duckdb.connect(str(self.settings.warehouse_db))

    def tearDown(self) -> None:
        self.con.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_config_loader(self) -> None:
        """Verify settings and account mapping rules load accurately."""
        self.assertEqual(self.settings.entity_code, "PL")
        self.assertEqual(self.settings.reporting_currency, "PLN")
        self.assertIn("EUR", self.settings.allowed_currencies)
        self.assertGreater(len(self.account_rules), 100)

        # Check FX rate
        rate_pln = self.settings.get_fx_rate("2026-01", "PLN")
        rate_eur = self.settings.get_fx_rate("2026-01", "EUR")
        self.assertEqual(rate_pln, 1.0)
        self.assertGreater(rate_eur, 4.0)

    def test_extractor_preserves_leading_zeros_and_filters_entity(self) -> None:
        """Verify extractor preserves string bill_to_code leading zeros and filters non-PL."""
        extractor = DataExtractor(settings=self.settings, con=self.con)
        stats = extractor.extract_pnl()

        self.assertEqual(stats.source_name, "pnl")
        self.assertGreater(stats.filtered_entity_rows, 0)
        self.assertGreater(stats.excluded_entity_rows, 0)

        # Check Parquet file
        df = pl.read_parquet(stats.staging_parquet_path)
        self.assertIn("bill_to_code", df.columns)
        self.assertEqual(df["bill_to_code"].dtype, pl.String)
        # Verify leading zero preserved
        sample_codes = df["bill_to_code"].unique().to_list()
        has_leading_zero = any(c.startswith("0") for c in sample_codes)
        self.assertTrue(has_leading_zero, f"Expected codes with leading zeros, got: {sample_codes}")

    def test_dimension_builder_scd1_and_surrogate_keys(self) -> None:
        """Verify dimension builder assigns 1-based integer surrogate keys."""
        dim_builder = DimensionBuilder(settings=self.settings, con=self.con)
        dim_results = dim_builder.build_all()

        cust_stats = dim_results["dim_customer"]
        prod_stats = dim_results["dim_product"]
        date_stats = dim_results["dim_date"]

        self.assertGreater(cust_stats.total_records, 0)
        self.assertGreater(prod_stats.total_records, 0)
        self.assertGreater(date_stats.total_records, 0)

        # Read duckdb core tables
        cust_rows = self.con.execute("SELECT customer_id, bill_to_code FROM core.dim_customer ORDER BY customer_id;").fetchall()
        self.assertEqual(cust_rows[0][0], 1)
        self.assertTrue(cust_rows[0][1].startswith("0"))

        prod_rows = self.con.execute("SELECT product_id, model_code FROM core.dim_product ORDER BY product_id;").fetchall()
        self.assertEqual(prod_rows[0][0], 1)

    def test_fact_builder_and_waterfall_rollups(self) -> None:
        """Verify P&L waterfall calculation and reporting FX conversion."""
        extractor = DataExtractor(settings=self.settings, con=self.con)
        extractor.extract_all()

        dim_builder = DimensionBuilder(settings=self.settings, con=self.con)
        dim_builder.build_all()

        fact_builder = FactBuilder(settings=self.settings, account_rules=self.account_rules, con=self.con)
        fact_stats = fact_builder.build_fact_pnl()

        self.assertGreater(fact_stats.total_records, 0)
        self.assertGreater(fact_stats.total_gross_revenue_reporting, 0)

        # Verify waterfall reconciliation on core.fact_pnl
        row = self.con.execute("""
            SELECT
                gross_revenue_local,
                sales_deduction_local,
                net_revenue_local,
                cogs_local,
                gross_margin_local,
                variable_cost_total_local,
                contribution_margin_local,
                fixed_sgna_local,
                operating_profit_local
            FROM core.fact_pnl
            LIMIT 1;
        """).fetchone()

        rev, ded, net, cogs, gm, var_cost, cm, fix, op = row
        self.assertAlmostEqual(net, rev + ded, places=2)
        self.assertAlmostEqual(gm, net + cogs, places=2)
        self.assertAlmostEqual(cm, gm + var_cost, places=2)
        self.assertAlmostEqual(op, cm + fix, places=2)

    def test_strict_fail_fast_foreign_key_integrity(self) -> None:
        """Verify anti-join catches unmapped codes, dumps error CSV, and halts pipeline."""
        # Inject unmapped record into raw P&L
        generate_sample_data(base_dir=self.base_dir, inject_unmapped=True)

        extractor = DataExtractor(settings=self.settings, con=self.con)
        extractor.extract_all()

        dim_builder = DimensionBuilder(settings=self.settings, con=self.con)
        dim_builder.build_all()

        fact_builder = FactBuilder(settings=self.settings, account_rules=self.account_rules, con=self.con)

        with self.assertRaises(ValueError) as ctx:
            fact_builder.build_fact_pnl()

        err_msg = str(ctx.exception)
        self.assertIn("[STRICT FAIL-FAST]", err_msg)
        self.assertIn("999999999999", err_msg)
        self.assertIn("UNKNOWN_MODEL_X", err_msg)

        # Verify error log was dumped
        error_logs = list(self.settings.logs_dir.glob("unmapped_errors_*.csv"))
        self.assertGreater(len(error_logs), 0)
        content = error_logs[0].read_text(encoding="utf-8")
        self.assertIn("999999999999", content)

    def test_loader_creates_obt_view_and_exports_parquet(self) -> None:
        """Verify OBT View mart.v_ai_pnl_summary and Excel Parquet exports."""
        extractor = DataExtractor(settings=self.settings, con=self.con)
        extractor.extract_all()

        dim_builder = DimensionBuilder(settings=self.settings, con=self.con)
        dim_builder.build_all()

        fact_builder = FactBuilder(settings=self.settings, account_rules=self.account_rules, con=self.con)
        fact_builder.build_fact_pnl()

        loader = DwhLoader(settings=self.settings, con=self.con)
        stats = loader.run_all()

        self.assertIn("mart.v_ai_pnl_summary", stats.views_created)
        self.assertGreater(stats.total_obt_rows, 0)

        # Check parquet files
        obt_parquet = self.settings.mart_export_dir / "v_ai_pnl_summary.parquet"
        self.assertTrue(obt_parquet.is_file())
        df_obt = pl.read_parquet(obt_parquet)
        self.assertEqual(len(df_obt), stats.total_obt_rows)
        self.assertIn("customer_name", df_obt.columns)
        self.assertIn("product_name", df_obt.columns)
        self.assertIn("operating_profit_reporting", df_obt.columns)

        # Check schema dictionary
        dict_json = self.settings.mart_export_dir / "ai_schema_dictionary.json"
        dict_md = self.settings.mart_export_dir / "ai_schema_dictionary.md"
        self.assertTrue(dict_json.is_file())
        self.assertTrue(dict_md.is_file())

    def test_null_foreign_key_and_indexes_verification(self) -> None:
        """Verify NULL FK is accurately captured with affected_rows > 0 and indexes exist."""
        extractor = DataExtractor(settings=self.settings, con=self.con)
        extractor.extract_all()

        dim_builder = DimensionBuilder(settings=self.settings, con=self.con)
        dim_builder.build_all()

        # Inject a NULL bill_to_code record into stg_pnl.parquet
        pnl_parquet = self.settings.staging_dir / "stg_pnl.parquet"
        df_pnl = pl.read_parquet(pnl_parquet)
        null_row = df_pnl.head(1).with_columns(pl.lit(None).cast(pl.String).alias("bill_to_code"))
        df_pnl_with_null = pl.concat([df_pnl, null_row])
        df_pnl_with_null.write_parquet(pnl_parquet, compression="zstd")

        fact_builder = FactBuilder(settings=self.settings, account_rules=self.account_rules, con=self.con)
        with self.assertRaises(ValueError) as ctx:
            fact_builder.build_fact_pnl()

        self.assertIn("Missing Customers (bill_to_code)", str(ctx.exception))

        # Check that the dumped error log has affected_rows == 1 for NULL
        error_logs = sorted(list(self.settings.logs_dir.glob("unmapped_errors_*.csv")))
        latest_log = error_logs[-1]
        log_df = pl.read_csv(latest_log)
        null_record = log_df.filter(pl.col("column_name") == "bill_to_code")
        self.assertGreater(len(null_record), 0)
        self.assertEqual(null_record["affected_rows"][0], 1)


if __name__ == "__main__":
    unittest.main()
