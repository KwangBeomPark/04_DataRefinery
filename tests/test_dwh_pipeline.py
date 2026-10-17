"""End-to-end and unit tests for the DWH Star Schema & AI Mart pipeline (`src/dwh/`)."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from src.dwh import (
    AI_MART_VIEW_NAME,
    DIM_ACCOUNT,
    DIM_CUSTOMER,
    DIM_PRODUCT,
    UNKNOWN_SK,
    AccountMappingRule,
    DimensionBuilder,
    DwhConfigError,
    EntityExtractor,
    EntityIsolationConfig,
    FactBuilder,
    MartWorkspacePaths,
    compute_mart_fingerprint,
    expand_period_filter,
    get_default_account_mapping,
    load_account_mapping,
    load_fx_rates,
    run_dwh_pipeline,
    save_account_mapping_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "sample_data" / "dwh_pl_sample"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_sample_dir(dest_dir: Path, *, exclude: set[str] | None = None) -> None:
    exclude_names = exclude or set()
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src_file in SAMPLE_DIR.iterdir():
        if src_file.is_file() and src_file.name not in exclude_names:
            (dest_dir / src_file.name).write_bytes(src_file.read_bytes())


class TestMartConfig(unittest.TestCase):
    """Tests for period expansion, FX rate resolution, account mapping, and fingerprinting."""

    def test_expand_period_filter(self) -> None:
        self.assertIsNone(expand_period_filter(None))
        self.assertIsNone(expand_period_filter(""))
        self.assertIsNone(expand_period_filter("ALL"))
        self.assertEqual(expand_period_filter("2026-Q1"), ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(expand_period_filter("2026Q4"), ["2026-10", "2026-11", "2026-12"])
        self.assertEqual(expand_period_filter("2026-02"), ["2026-02"])
        self.assertEqual(expand_period_filter("202603"), ["2026-03"])
        self.assertEqual(len(expand_period_filter("2026") or []), 12)

        with self.assertRaises(DwhConfigError):
            expand_period_filter("2026-Q5")
        with self.assertRaises(DwhConfigError):
            expand_period_filter("2026-13")

    def test_fx_rates_strict_production_vs_demo_and_currency_validation(self) -> None:
        # Production config has NO hardcoded sample FX rates for foreign currencies
        prod_cfg = EntityIsolationConfig(entity_code="PL")
        self.assertAlmostEqual(prod_cfg.get_fx_rate("2026-01", "PLN"), 1.0)
        with self.assertRaisesRegex(DwhConfigError, "No FX rate configured"):
            prod_cfg.get_fx_rate("2026-01", "EUR")

        # Production config rejects DEFAULT fallback unless allow_default_fx_fallback=True
        prod_with_default = EntityIsolationConfig(
            entity_code="PL",
            fx_rates={"2026-01": {"PLN": 1.0, "EUR": 4.25}, "DEFAULT": {"PLN": 1.0, "EUR": 4.30}},
            allow_default_fx_fallback=False,
        )
        self.assertAlmostEqual(prod_with_default.get_fx_rate("2026-01", "EUR"), 4.25)
        with self.assertRaisesRegex(DwhConfigError, "No FX rate configured"):
            prod_with_default.get_fx_rate("2026-09", "EUR")

        # Demo config explicitly enables DEMO_SAMPLE_FX_RATES
        demo_cfg = EntityIsolationConfig.for_demo(entity_code="PL")
        self.assertAlmostEqual(demo_cfg.get_fx_rate("2026-01", "PLN"), 1.0)
        self.assertAlmostEqual(demo_cfg.get_fx_rate("2026-01", "EUR"), 4.30)

        # Loading fx_rates.csv works and validates rates
        loaded_fx = load_fx_rates(SAMPLE_DIR / "fx_rates.csv")
        self.assertAlmostEqual(loaded_fx["2026-02"]["EUR"], 4.30)

        with self.assertRaises(DwhConfigError):
            demo_cfg.get_fx_rate("2026-01", "USD")

        # Self-currency rate when reporting_currency="EUR" uses default factory must be 1.0
        eur_cfg = EntityIsolationConfig(entity_code="PL", reporting_currency="EUR")
        self.assertAlmostEqual(eur_cfg.get_fx_rate("2026-01", "EUR"), 1.0)

        # Explicit non-1.0 rate for reporting_currency must be rejected
        with self.assertRaises(DwhConfigError):
            EntityIsolationConfig(
                entity_code="PL",
                reporting_currency="EUR",
                fx_rates={"2026-01": {"PLN": 1.0, "EUR": 4.30}},
            )

        # Invalid entity_code with path traversal characters must be rejected
        with self.assertRaises(DwhConfigError):
            EntityIsolationConfig(entity_code="../PL")

    def test_account_mapping_csv_and_json_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            rules = load_account_mapping(SAMPLE_DIR / "account_mapping.csv")
            self.assertIn("REV_GROSS", rules)
            self.assertEqual(rules["REV_GROSS"].sign_multiplier, 1)
            self.assertEqual(rules["DED_REBATE"].sign_multiplier, -1)
            self.assertTrue(rules["CALC_GROSS_PROFIT"].is_subtotal)

            json_path = tmp_path / "mapping.json"
            save_account_mapping_json(json_path, rules)
            loaded_json = load_account_mapping(json_path)
            self.assertEqual(loaded_json, rules)

            fp1 = compute_mart_fingerprint(EntityIsolationConfig.for_demo(), rules)
            fp2 = compute_mart_fingerprint(EntityIsolationConfig.for_demo(), loaded_json)
            self.assertEqual(fp1, fp2)


class TestExtractorAndDimensions(unittest.TestCase):
    """Tests for fast entity extraction, leading-zero preservation, and persistent surrogate keys."""

    def test_extractor_filters_entity_period_and_preserves_leading_zeros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = MartWorkspacePaths.from_entity("PL", base_dir=tmp_path)
            paths.ensure_dirs()
            cfg = EntityIsolationConfig.for_demo(entity_code="PL")

            con = duckdb.connect(str(paths.warehouse_db_path))
            try:
                extractor = EntityExtractor(con=con, config=cfg, paths=paths)
                stats = extractor.extract_source(
                    "pnl",
                    SAMPLE_DIR / "raw_erp_pnl.csv",
                    period_spec="2026-Q1",
                    required_columns=("customer_code", "product_code", "sales_qty"),
                )
                self.assertGreaterEqual(stats.excluded_entity_rows, 1)  # DE row excluded
                self.assertGreaterEqual(stats.excluded_period_rows, 1)  # 2026-04 row excluded
                self.assertEqual(stats.invalid_currency_rows, 0)
                self.assertEqual(stats.filtered_rows, 5)
                self.assertTrue(Path(stats.staging_parquet_path).is_file())

                cust_codes = [
                    r[0]
                    for r in con.execute(
                        "SELECT DISTINCT customer_code FROM stg_pnl ORDER BY customer_code"
                    ).fetchall()
                ]
                self.assertIn("000100200301", cust_codes)
                self.assertTrue(all(len(c) == 12 and c.startswith("000") for c in cust_codes))
            finally:
                con.close()

    def test_extractor_rejects_disallowed_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad_csv = tmp_path / "bad_currency.csv"
            bad_csv.write_text(
                "period,entity_code,customer_code,product_code,currency_code,sales_qty\n"
                "2026-01,PL,000100200301,OLED65C6P,USD,10\n",
                encoding="utf-8",
            )
            paths = MartWorkspacePaths.from_entity("PL", base_dir=tmp_path)
            paths.ensure_dirs()
            con = duckdb.connect(str(paths.warehouse_db_path))
            try:
                extractor = EntityExtractor(
                    con=con, config=EntityIsolationConfig(entity_code="PL"), paths=paths
                )
                with self.assertRaisesRegex(DwhConfigError, "currency"):
                    extractor.extract_source("pnl", bad_csv, period_spec="2026-Q1")
            finally:
                con.close()

    def test_persistent_surrogate_keys_never_reassigned_and_removed_accounts_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = MartWorkspacePaths.from_entity("PL", base_dir=tmp_path)
            paths.ensure_dirs()
            con = duckdb.connect(str(paths.warehouse_db_path))
            try:
                builder = DimensionBuilder(con=con, paths=paths)
                full_rules = get_default_account_mapping()
                builder.build_all_dimensions(
                    customer_master=SAMPLE_DIR / "master_customers.csv",
                    product_master=SAMPLE_DIR / "master_products.csv",
                    account_mapping=full_rules,
                    period_spec="2026-Q1",
                )

                initial_map = dict(
                    con.execute(
                        f"SELECT product_code, product_sk FROM {DIM_PRODUCT} ORDER BY product_sk"
                    ).fetchall()
                )
                self.assertEqual(initial_map["UNKNOWN"], UNKNOWN_SK)
                self.assertGreater(initial_map["OLED65C6P"], 0)

                # Add a new product that sorts alphabetically BEFORE OLED65C6P ("AAA_NEW_MODEL")
                expanded_products = tmp_path / "master_products_v2.csv"
                expanded_products.write_text(
                    "product_code,product_name,division,category\n"
                    "AAA_NEW_MODEL,Alpha TV,TV,LED\n"
                    "OLED65C6P,OLED TV 65 C6,TV,OLED\n"
                    "QNED55B6P,QNED TV 55 B6,TV,QNED\n"
                    "WM9000PL,Eco Washer 9kg,HA,Washing Machine\n",
                    encoding="utf-8",
                )

                builder.build_dim_product(expanded_products)
                updated_map = dict(
                    con.execute(
                        f"SELECT product_code, product_sk FROM {DIM_PRODUCT} ORDER BY product_sk"
                    ).fetchall()
                )

                # Existing surrogate keys MUST remain identical!
                self.assertEqual(updated_map["OLED65C6P"], initial_map["OLED65C6P"])
                self.assertEqual(updated_map["QNED55B6P"], initial_map["QNED55B6P"])
                self.assertEqual(updated_map["WM9000PL"], initial_map["WM9000PL"])
                self.assertEqual(updated_map["AAA_NEW_MODEL"], max(initial_map.values()) + 1)

                # Verify removing an account rule removes it from active Dim_Account while preserving registry SK
                subset_rules = {k: v for k, v in full_rules.items() if k != "COST_FIXED_LABOR"}
                builder.build_dim_account(subset_rules)
                active_accounts = {
                    r[0] for r in con.execute(f"SELECT account_code FROM {DIM_ACCOUNT}").fetchall()
                }
                self.assertNotIn("COST_FIXED_LABOR", active_accounts)
            finally:
                con.close()

    def test_duplicate_and_case_colliding_master_keys_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dup_prod = tmp_path / "dup_products.csv"
            dup_prod.write_text(
                "product_code,product_name,division,category\n"
                "OLED65C6P,First Name,TV,OLED\n"
                "oled65c6p,Lowercase Collision,TV,OLED\n",
                encoding="utf-8",
            )
            con = duckdb.connect(":memory:")
            try:
                builder = DimensionBuilder(con=con)
                with self.assertRaisesRegex(DwhConfigError, "Duplicate"):
                    builder.build_dim_product(dup_prod)
            finally:
                con.close()

    def test_pre_existing_lowercase_db_keys_normalized_and_collisions_rejected(self) -> None:
        con = duckdb.connect(":memory:")
        try:
            # Seed legacy lowercase keys in dim_key_registry and _master_product
            con.execute(
                """
                CREATE TABLE dim_key_registry (
                    dimension_name VARCHAR NOT NULL,
                    natural_key VARCHAR NOT NULL,
                    surrogate_key INTEGER NOT NULL,
                    created_at VARCHAR NOT NULL,
                    PRIMARY KEY (dimension_name, natural_key)
                )
                """
            )
            con.execute(
                """
                INSERT INTO dim_key_registry VALUES
                    ('Dim_Product', 'UNKNOWN', -1, '2026-01-01T00:00:00Z'),
                    ('Dim_Product', 'oled65c6p', 7, '2026-01-01T00:00:00Z')
                """
            )
            con.execute(
                """
                CREATE TABLE _master_product (
                    product_code VARCHAR PRIMARY KEY,
                    product_name VARCHAR NOT NULL,
                    division VARCHAR NOT NULL,
                    category VARCHAR NOT NULL
                )
                """
            )
            con.execute(
                "INSERT INTO _master_product VALUES ('oled65c6p', 'Legacy Lowercase OLED', 'TV', 'OLED')"
            )

            # Re-running DimensionBuilder with uppercase OLED65C6P must normalize in place
            # and preserve surrogate_key = 7 without duplicate rows!
            builder = DimensionBuilder(con=con)
            builder.build_dim_product(SAMPLE_DIR / "master_products.csv")

            rows = con.execute(
                f"SELECT product_code, product_sk FROM {DIM_PRODUCT} WHERE UPPER(product_code) = 'OLED65C6P'"
            ).fetchall()
            self.assertEqual(rows, [("OLED65C6P", 7)])

            # Now inject a conflicting lowercase + uppercase pair into dim_key_registry and verify rejection
            con.execute(
                "INSERT INTO dim_key_registry VALUES ('Dim_Product', 'qned55b6p', 99, '2026-01-01T00:00:00Z')"
            )
            with self.assertRaisesRegex(DwhConfigError, "Case-insensitive"):
                DimensionBuilder(con=con)
        finally:
            con.close()


class TestEndToEndDwhPipeline(unittest.TestCase):
    """End-to-end tests for FactBuilder, -1 Unknown isolation, Multipass AI View, and CLI."""

    def test_end_to_end_pipeline_exact_totals_and_zero_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = run_dwh_pipeline(
                input_dir=SAMPLE_DIR,
                entity_code="PL",
                period_spec="2026-Q1",
                workspace_dir=tmp_path,
            )

            self.assertEqual(result.entity_code, "PL")
            self.assertEqual(result.period_spec, "2026-Q1")
            self.assertEqual(result.reporting_currency, "PLN")
            self.assertTrue(result.reconciliation_passed)
            self.assertTrue(result.serving_validation["grain_unique"])
            self.assertTrue(result.serving_validation["fanout_check_passed"])

            # Verify exact independent Decimal expectations from Codex Sol 6.1 review:
            # P&L filtered rows = 5, Long rows = 45 (5 rows x 9 non-subtotal accounts)
            # Net Revenue = 471,200 PLN, Operating Profit = 63,950 PLN, Ending Inventory = 260,150 PLN
            self.assertEqual(result.fact_summary.fact_pnl_rows, 5)
            self.assertEqual(result.fact_summary.fact_pnl_long_rows, 45)
            self.assertAlmostEqual(
                result.serving_validation["fact_net_revenue_reporting"], 471200.0, places=2
            )
            self.assertAlmostEqual(
                result.serving_validation["view_net_revenue_reporting"], 471200.0, places=2
            )
            self.assertAlmostEqual(
                result.serving_validation["fact_operating_profit_reporting"], 63950.0, places=2
            )
            self.assertAlmostEqual(
                result.serving_validation["view_operating_profit_reporting"], 63950.0, places=2
            )
            self.assertAlmostEqual(
                result.serving_validation["fact_ending_inventory_val_reporting"], 260150.0, places=2
            )
            self.assertAlmostEqual(
                result.serving_validation["view_ending_inventory_val_reporting"], 260150.0, places=2
            )

            # Verify unmapped product code NEW_TV_2026X was isolated to -1 and logged with source row_count=1
            unmapped_map = {r.raw_code: r for r in result.unmapped_records}
            self.assertIn("NEW_TV_2026X", unmapped_map)
            self.assertEqual(unmapped_map["NEW_TV_2026X"].row_count, 1)
            self.assertEqual(
                unmapped_map["NEW_TV_2026X"].impact_metric_basis,
                "NET_REVENUE_OR_ABS_OP_PROFIT",
            )

            # Verify exported serving files and AI Schema Dictionary
            self.assertTrue(
                Path(result.exported_artifacts[f"serving_{AI_MART_VIEW_NAME}_csv"]).is_file()
            )
            self.assertTrue(
                Path(result.exported_artifacts[f"serving_{AI_MART_VIEW_NAME}_parquet"]).is_file()
            )
            schema_json_path = Path(result.exported_artifacts["ai_schema_dictionary_json"])
            schema_md_path = Path(result.exported_artifacts["ai_schema_dictionary_md"])
            self.assertTrue(schema_json_path.is_file())
            self.assertTrue(schema_md_path.is_file())

            # Verify leading zeros preserved in exported Dim_Customer.csv
            dim_cust_csv = Path(result.exported_artifacts[f"serving_{DIM_CUSTOMER}_csv"])
            with dim_cust_csv.open("r", encoding="utf-8") as f:
                cust_rows = list(csv.DictReader(f))
            cust_codes = {r["customer_code"] for r in cust_rows}
            self.assertIn("000100200301", cust_codes)

    def test_negative_revenue_with_minus_one_sign_multiplier_reconciles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            custom_dir = tmp_path / "neg_rev_input"
            _copy_sample_dir(custom_dir)

            # Create a P&L CSV where REV_GROSS is stored as negative (-100000) and sign_multiplier is -1
            pnl_csv = custom_dir / "raw_erp_pnl.csv"
            pnl_csv.write_text(
                "period,entity_code,customer_code,product_code,currency_code,sales_qty,"
                "REV_GROSS,DED_REBATE,COGS_MAT,CALC_GROSS_PROFIT,COST_LOG_FREIGHT,COST_LOG_WH,"
                "COST_PROMO_COOP,COST_WARRANTY,COST_FIXED_RENT,COST_FIXED_LABOR\n"
                "2026-01,PL,000100200301,OLED65C6P,PLN,10,"
                "-100000,5000,60000,-35000,2000,1000,3000,1000,4000,6000\n",
                encoding="utf-8",
            )
            rules = dict(get_default_account_mapping())
            rev_rule = rules["REV_GROSS"]
            rules["REV_GROSS"] = AccountMappingRule(
                account_code=rev_rule.account_code,
                account_name=rev_rule.account_name,
                statement_line=rev_rule.statement_line,
                waterfall_step=rev_rule.waterfall_step,
                cost_category=rev_rule.cost_category,
                sign_multiplier=-1,
                is_subtotal=False,
            )

            result = run_dwh_pipeline(
                input_dir=custom_dir,
                entity_code="PL",
                period_spec="2026-01",
                workspace_dir=tmp_path / "ws",
                mapping_rules=rules,
            )
            self.assertTrue(result.reconciliation_passed)
            # Signed gross revenue = -100,000 * (-1) = +100,000; Net revenue = 100,000 - 5,000 = 95,000
            self.assertAlmostEqual(
                result.serving_validation["fact_net_revenue_reporting"], 95000.0, places=2
            )
            # Operating profit = 100,000 - (5,000 + 60,000 + 2,000 + 1,000 + 3,000 + 1,000 + 4,000 + 6,000) = 18,000
            self.assertAlmostEqual(
                result.serving_validation["fact_operating_profit_reporting"], 18000.0, places=2
            )

    def test_format_specific_numeric_syntax_rejects_wrong_decimal_separator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # 1) Polish mode must reject dot-decimal "12.34"
            bad_polish_dir = tmp_path / "bad_polish"
            _copy_sample_dir(bad_polish_dir)
            inv_path = bad_polish_dir / "raw_inventory.csv"
            inv_lines = inv_path.read_text(encoding="utf-8").strip().splitlines()
            row1 = inv_lines[1].split(",")
            row1[6] = "12.34"  # dot-decimal in polish mode!
            inv_lines[1] = ",".join(row1)
            inv_path.write_text("\n".join(inv_lines) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(DwhConfigError, "Numeric parsing failed"):
                run_dwh_pipeline(
                    input_dir=bad_polish_dir,
                    entity_code="PL",
                    period_spec="2026-Q1",
                    workspace_dir=tmp_path / "ws_polish",
                )

            # 2) US mode and all US aliases ('us', 'en', 'standard', '1,234.56') must reject comma-decimal "12,34"
            con = duckdb.connect(":memory:")
            try:
                con.execute("CREATE TABLE stg_test (val VARCHAR)")
                con.execute("INSERT INTO stg_test VALUES ('12,34')")
                for alias in ("us", "en", "standard", "1,234.56"):
                    us_cfg = EntityIsolationConfig(
                        entity_code="PL",
                        number_format=alias,
                        fx_rates={"2026-01": {"PLN": 1.0, "EUR": 4.30}},
                    )
                    self.assertEqual(us_cfg.number_format, "us")
                    fb = FactBuilder(
                        con=con,
                        config=us_cfg,
                        paths=MartWorkspacePaths.from_entity("PL", base_dir=tmp_path / "ws_us"),
                        mapping_rules=get_default_account_mapping(),
                    )
                    with self.assertRaisesRegex(DwhConfigError, "Numeric parsing failed"):
                        fb._validate_numeric_column("stg_test", "val")

                # 3) Polish aliases ('polish', 'eu', 'european', '1 234,56', '1.234,56') must reject dot-decimal "12.34"
                con.execute("CREATE TABLE stg_test_pl (val VARCHAR)")
                con.execute("INSERT INTO stg_test_pl VALUES ('12.34')")
                for pl_alias in ("polish", "eu", "european", "1 234,56", "1.234,56"):
                    pl_cfg = EntityIsolationConfig(
                        entity_code="PL",
                        number_format=pl_alias,
                        fx_rates={"2026-01": {"PLN": 1.0, "EUR": 4.30}},
                    )
                    self.assertEqual(pl_cfg.number_format, "polish")
                    fb_pl = FactBuilder(
                        con=con,
                        config=pl_cfg,
                        paths=MartWorkspacePaths.from_entity("PL", base_dir=tmp_path / "ws_pl"),
                        mapping_rules=get_default_account_mapping(),
                    )
                    with self.assertRaisesRegex(DwhConfigError, "Numeric parsing failed"):
                        fb_pl._validate_numeric_column("stg_test_pl", "val")
            finally:
                con.close()

    def test_production_run_without_fx_rates_rejects_foreign_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            no_fx_dir = tmp_path / "no_fx_input"
            # Copy sample files EXCEPT fx_rates.csv
            _copy_sample_dir(no_fx_dir, exclude={"fx_rates.csv"})

            with self.assertRaisesRegex(DwhConfigError, "FX rate"):
                run_dwh_pipeline(
                    input_dir=no_fx_dir,
                    entity_code="PL",
                    period_spec="2026-Q1",
                    workspace_dir=tmp_path / "ws_no_fx",
                    is_demo=False,
                )

    def test_failed_run_and_late_promotion_failure_preserve_all_workspace_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ws_dir = tmp_path / "ws"
            first = run_dwh_pipeline(
                input_dir=SAMPLE_DIR,
                entity_code="PL",
                period_spec="2026-Q1",
                workspace_dir=ws_dir,
            )
            ws_root = Path(first.workspace_root)
            tracked_paths = [
                Path(first.warehouse_db_path),
                Path(first.exported_artifacts["serving_Fact_PnL_csv"]),
                Path(first.exported_artifacts["warehouse_Fact_PnL_parquet"]),
                Path(first.extraction_stats["pnl"].staging_parquet_path),
                ws_root / "logs" / "unmapped_codes.csv",
                Path(first.audit_log_path),
            ]
            hashes_before = {p: _sha256(p) for p in tracked_paths}

            # Case A: Mid-run validation failure (malformed number in inventory)
            bad_dir = tmp_path / "bad_inv_input"
            _copy_sample_dir(bad_dir)
            inv_path = bad_dir / "raw_inventory.csv"
            inv_lines = inv_path.read_text(encoding="utf-8").strip().splitlines()
            row1 = inv_lines[1].split(",")
            row1[6] = "12..34"
            inv_lines[1] = ",".join(row1)
            inv_path.write_text("\n".join(inv_lines) + "\n", encoding="utf-8")

            with self.assertRaises(DwhConfigError):
                run_dwh_pipeline(
                    input_dir=bad_dir,
                    entity_code="PL",
                    period_spec="2026-Q1",
                    workspace_dir=ws_dir,
                )

            for p, h in hashes_before.items():
                self.assertEqual(_sha256(p), h, f"Artifact {p} changed after mid-run failure")

            # Case B: Failure during backup phase of _promote_staged_workspace (shutil.copy2 fails on 3rd backup file)
            real_copy2 = __import__("shutil").copy2
            copy2_calls = 0

            def _fail_mid_backup(src: str | Path, dst: str | Path, *args: object, **kwargs: object) -> object:
                nonlocal copy2_calls
                if ".ws_bak_" in str(dst):
                    copy2_calls += 1
                    if copy2_calls == 3:
                        raise OSError("Simulated failure during backup copy before promotion")
                return real_copy2(src, dst, *args, **kwargs)

            with (
                patch("src.dwh.serving_loader.shutil.copy2", side_effect=_fail_mid_backup),
                self.assertRaises(OSError),
            ):
                run_dwh_pipeline(
                    input_dir=SAMPLE_DIR,
                    entity_code="PL",
                    period_spec="2026-01",
                    workspace_dir=ws_dir,
                )

            for p, h in hashes_before.items():
                self.assertTrue(p.exists(), f"Artifact {p} was deleted after backup failure")
                self.assertEqual(_sha256(p), h, f"Artifact {p} changed after backup failure")

            # Case C: Late failure during final workspace bundle promotion in _promote_staged_workspace
            # Modify period_spec to "2026-01" so new run produces different staging/warehouse/serving files,
            # then force os.replace to fail midway ONLY when promoting into live ws_root (ignoring .run_stage_ internal replaces)!
            real_replace = __import__("os").replace
            live_replace_calls = 0

            def _fail_mid_promotion(src: str | Path, dst: str | Path) -> None:
                nonlocal live_replace_calls
                if ".run_stage_" not in str(dst):
                    live_replace_calls += 1
                    if live_replace_calls == 6:
                        raise OSError("Simulated late failure midway through _promote_staged_workspace")
                real_replace(src, dst)

            with (
                patch("src.dwh.serving_loader.os.replace", side_effect=_fail_mid_promotion),
                self.assertRaises(OSError),
            ):
                run_dwh_pipeline(
                    input_dir=SAMPLE_DIR,
                    entity_code="PL",
                    period_spec="2026-01",
                    workspace_dir=ws_dir,
                )

            # Confirm 5 live files were already replaced before the 6th failed, and all were rolled back!
            self.assertEqual(live_replace_calls, 6)
            for p, h in hashes_before.items():
                self.assertEqual(_sha256(p), h, f"Artifact {p} was not rolled back after late failure")

    def test_demo_and_entity_workspace_isolation_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # 1) --demo cannot target a production workspace name like dwh_pl
            prod_named_ws = tmp_path / "dwh_pl"
            with self.assertRaisesRegex(DwhConfigError, "cannot target production workspace"):
                run_dwh_pipeline(
                    entity_code="PL",
                    period_spec="2026-Q1",
                    workspace_dir=prod_named_ws,
                    is_demo=True,
                )

            # 2) Build a production PL workspace, then verify DE entity or demo run cannot overwrite it
            pl_ws = tmp_path / "custom_pl_ws"
            run_dwh_pipeline(
                input_dir=SAMPLE_DIR,
                entity_code="PL",
                period_spec="2026-Q1",
                workspace_dir=pl_ws,
                is_demo=False,
            )
            with self.assertRaisesRegex(DwhConfigError, "Workspace mode mismatch"):
                run_dwh_pipeline(
                    entity_code="PL",
                    period_spec="2026-Q1",
                    workspace_dir=pl_ws,
                    is_demo=True,
                )
            with self.assertRaisesRegex(DwhConfigError, "Workspace entity mismatch"):
                run_dwh_pipeline(
                    input_dir=SAMPLE_DIR,
                    entity_code="DE",
                    period_spec="2026-Q1",
                    workspace_dir=pl_ws,
                    is_demo=False,
                )

    def test_multiple_unmapped_products_stay_distinct_in_ai_mart_and_price_uses_avg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            custom_dir = tmp_path / "custom_input"
            _copy_sample_dir(custom_dir)

            # Add 2 distinct unmapped customers for the same product OLED65C6P in 2026-02 in raw_erp_pnl.csv
            pnl_path = custom_dir / "raw_erp_pnl.csv"
            with pnl_path.open("a", encoding="utf-8", newline="") as f:
                f.write(
                    "2026-02,PL,000999888771,OLED65C6P,PLN,5,10000,500,6000,3500,200,100,300,100,400,600\n"
                )
                f.write(
                    "2026-02,PL,000999888772,OLED65C6P,PLN,5,10000,500,6000,3500,200,100,300,100,400,600\n"
                )

            # Add an unmapped product NEW_INV_ONLY_99 to raw_inventory.csv in 2026-02
            inv_path = custom_dir / "raw_inventory.csv"
            with inv_path.open("a", encoding="utf-8", newline="") as f:
                f.write("2026-02,PL,WAW_DC1,Warsaw Central DC,NEW_INV_ONLY_99,PLN,25,10000,30,500\n")

            # Add 2 channels of an unmapped product NEW_PRICE_MODEL_77 to raw_market_price.csv (prices 1000 and 1200 -> AVG 1100)
            price_path = custom_dir / "raw_market_price.csv"
            with price_path.open("a", encoding="utf-8", newline="") as f:
                f.write("2026-02,PL,CH_RETAIL_ME,MediaExpert,NEW_PRICE_MODEL_77,PLN,1000,1050,1020\n")
                f.write("2026-02,PL,CH_RETAIL_RTV,RTV Euro AGD,NEW_PRICE_MODEL_77,PLN,1200,1250,1220\n")

            result = run_dwh_pipeline(
                input_dir=custom_dir,
                entity_code="PL",
                period_spec="2026-Q1",
                workspace_dir=tmp_path / "ws",
            )
            self.assertTrue(result.serving_validation["fanout_check_passed"])
            view_csv = Path(result.exported_artifacts[f"serving_{AI_MART_VIEW_NAME}_csv"])
            with view_csv.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            oled_2026_02 = [
                r for r in rows if r["period"] == "2026-02" and r["product_code"] == "OLED65C6P"
            ]
            self.assertEqual(len(oled_2026_02), 1)
            # 2 unmapped customers (000999888771, 000999888772) must both be counted as distinct customers!
            self.assertGreaterEqual(int(oled_2026_02[0]["active_customer_count"]), 2)

            unknown_rows_2026_02 = [
                r for r in rows if r["period"] == "2026-02" and int(r["product_sk"]) == -1
            ]
            unknown_codes = {r["product_code"] for r in unknown_rows_2026_02}
            self.assertEqual(
                unknown_codes,
                {"NEW_TV_2026X", "NEW_INV_ONLY_99", "NEW_PRICE_MODEL_77"},
            )

            price_unmapped = [
                r
                for r in result.unmapped_records
                if r.source_name == "price" and r.raw_code == "NEW_PRICE_MODEL_77"
            ]
            self.assertEqual(len(price_unmapped), 1)
            self.assertEqual(price_unmapped[0].row_count, 2)
            self.assertEqual(
                price_unmapped[0].impact_metric_basis,
                "AVG_OUR_UNIT_PRICE_NON_ADDITIVE",
            )
            self.assertAlmostEqual(price_unmapped[0].impact_amount_reporting, 1100.0, places=2)

    def test_unmapped_nonzero_account_column_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            incomplete_rules = {
                k: v for k, v in get_default_account_mapping().items() if k != "COST_FIXED_LABOR"
            }
            with self.assertRaisesRegex(DwhConfigError, "COST_FIXED_LABOR"):
                run_dwh_pipeline(
                    input_dir=SAMPLE_DIR,
                    entity_code="PL",
                    period_spec="2026-Q1",
                    workspace_dir=tmp_path / "ws",
                    mapping_rules=incomplete_rules,
                )

    def test_cli_mart_run_subcommand_and_mutual_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ws_dir = tmp_path / "cli_ws"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.data_refinery",
                    "mart-run",
                    "--entity",
                    "PL",
                    "--period",
                    "2026-Q1",
                    "--input-dir",
                    str(SAMPLE_DIR),
                    "--workspace-dir",
                    str(ws_dir),
                    "--json",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"STDERR: {proc.stderr}\nSTDOUT: {proc.stdout}")
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["entity_code"], "PL")
            self.assertEqual(payload["period_spec"], "2026-Q1")
            self.assertTrue(payload["fact_summary"]["reconciliation_passed"])
            self.assertTrue(payload["serving_validation"]["fanout_check_passed"])

            # Verify --demo and --input-dir are mutually exclusive in CLI
            both_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.data_refinery",
                    "mart-run",
                    "--demo",
                    "--input-dir",
                    str(SAMPLE_DIR),
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(both_proc.returncode, 0)
            self.assertIn("mutually exclusive", both_proc.stderr)


if __name__ == "__main__":
    unittest.main()
