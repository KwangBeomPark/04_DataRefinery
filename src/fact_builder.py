"""Fact table builder with Anti-Join Strict Fail-Fast foreign key verification and account roll-up."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
import duckdb
import polars as pl

from src.config_loader import AccountRule, ConfigError, DwhSettings


@dataclass
class FactBuildStats:
    """Summary of built fact tables and reconciliation results."""
    fact_name: str
    total_records: int
    table_name: str
    total_gross_revenue_reporting: float
    total_operating_profit_reporting: float
    parquet_path: str


class FactBuilder:
    """Validates FK integrity with Anti-Join Strict Fail-Fast and builds Core Fact tables."""

    def __init__(
        self,
        settings: DwhSettings,
        account_rules: Dict[str, AccountRule],
        con: Optional[duckdb.DuckDBPyConnection] = None,
    ) -> None:
        self.settings = settings
        self.account_rules = account_rules
        self.con = con
        self.settings.ensure_directories()

    def build_fact_pnl(
        self,
        stg_pnl_path: Optional[Path | str] = None,
        dim_customer_path: Optional[Path | str] = None,
        dim_product_path: Optional[Path | str] = None,
        dim_date_path: Optional[Path | str] = None,
    ) -> FactBuildStats:
        """Build core.fact_pnl with strict anti-join integrity and 100+ account roll-up."""
        pnl_path = Path(stg_pnl_path) if stg_pnl_path else self.settings.staging_dir / "stg_pnl.parquet"
        cust_path = Path(dim_customer_path) if dim_customer_path else self.settings.staging_dir / "dim_customer.parquet"
        prod_path = Path(dim_product_path) if dim_product_path else self.settings.staging_dir / "dim_product.parquet"
        date_path = Path(dim_date_path) if dim_date_path else self.settings.staging_dir / "dim_date.parquet"

        for p in [pnl_path, cust_path, prod_path, date_path]:
            if not p.is_file():
                raise ConfigError(f"Required staging input not found: {p}")

        df_pnl = pl.read_parquet(pnl_path)
        df_cust = pl.read_parquet(cust_path)
        df_prod = pl.read_parquet(prod_path)
        df_date = pl.read_parquet(date_path)

        # -------------------------------------------------------------
        # 1. Anti-Join Strict Fail-Fast FK Integrity Validation
        # -------------------------------------------------------------
        self._validate_foreign_keys_fail_fast(df_pnl, df_cust, df_prod, df_date)

        # -------------------------------------------------------------
        # 2. Join Conformed Dimensions for Surrogate Keys
        # -------------------------------------------------------------
        df_pnl = df_pnl.join(
            df_cust.select(["bill_to_code", "customer_id"]),
            on="bill_to_code",
            how="inner",
        )
        df_pnl = df_pnl.join(
            df_prod.select(["model_code", "product_id"]),
            on="model_code",
            how="inner",
        )
        df_pnl = df_pnl.join(
            df_date.select(["period", "date_id"]),
            on="period",
            how="inner",
        )

        # -------------------------------------------------------------
        # 3. Currency Conversion (FX Rates)
        # -------------------------------------------------------------
        # High-performance vectorized FX lookup via unique pairs join
        unique_pairs = df_pnl.select(["period", "currency_code"]).unique().to_dicts()
        fx_lookup_records = []
        for r in unique_pairs:
            p, c = r["period"], r["currency_code"]
            fx_lookup_records.append({
                "period": p,
                "currency_code": c,
                "fx_rate": self.settings.get_fx_rate(period=p, currency=c),
            })
        df_fx = pl.DataFrame(
            fx_lookup_records,
            schema={"period": pl.String, "currency_code": pl.String, "fx_rate": pl.Float64},
        )
        df_pnl = df_pnl.join(df_fx, on=["period", "currency_code"], how="left")
        df_pnl = df_pnl.with_columns([
            pl.lit(self.settings.reporting_currency).alias("reporting_currency"),
        ])

        # -------------------------------------------------------------
        # 4. Account Roll-up & Waterfall Metric Calculation
        # -------------------------------------------------------------
        # Group accounts by statement_line, ignoring subtotal columns to prevent double-counting
        active_accounts = [col for col in df_pnl.columns if col in self.account_rules]

        rev_cols = [c for c in active_accounts if self.account_rules[c].statement_line == "GROSS_REVENUE" and not self.account_rules[c].is_subtotal]
        ded_cols = [c for c in active_accounts if self.account_rules[c].statement_line == "SALES_DEDUCTION" and not self.account_rules[c].is_subtotal]
        cog_cols = [c for c in active_accounts if self.account_rules[c].statement_line == "COGS" and not self.account_rules[c].is_subtotal]
        log_cols = [c for c in active_accounts if self.account_rules[c].statement_line == "LOGISTICS_COST" and not self.account_rules[c].is_subtotal]
        prm_cols = [c for c in active_accounts if self.account_rules[c].statement_line == "PROMOTION_COST" and not self.account_rules[c].is_subtotal]
        war_cols = [c for c in active_accounts if self.account_rules[c].statement_line == "WARRANTY_COST" and not self.account_rules[c].is_subtotal]
        fix_cols = [c for c in active_accounts if self.account_rules[c].statement_line == "FIXED_SGNA" and not self.account_rules[c].is_subtotal]

        def sum_cols_or_zero(cols: List[str]) -> pl.Expr:
            if not cols:
                return pl.lit(0.0)
            return pl.sum_horizontal([pl.col(c).fill_null(0.0).cast(pl.Float64) for c in cols])

        df_pnl = df_pnl.with_columns([
            sum_cols_or_zero(rev_cols).round(2).alias("gross_revenue_local"),
            # Deductions, COGS, Logistics, Promotion, Warranty, Fixed are negative impact on profit
            (-sum_cols_or_zero(ded_cols)).round(2).alias("sales_deduction_local"),
            (-sum_cols_or_zero(cog_cols)).round(2).alias("cogs_local"),
            (-sum_cols_or_zero(log_cols)).round(2).alias("logistics_cost_local"),
            (-sum_cols_or_zero(prm_cols)).round(2).alias("promotion_cost_local"),
            (-sum_cols_or_zero(war_cols)).round(2).alias("warranty_cost_local"),
            (-sum_cols_or_zero(fix_cols)).round(2).alias("fixed_sgna_local"),
        ])

        # Waterfall lines
        df_pnl = df_pnl.with_columns([
            (pl.col("gross_revenue_local") + pl.col("sales_deduction_local")).round(2).alias("net_revenue_local"),
        ])
        df_pnl = df_pnl.with_columns([
            (pl.col("net_revenue_local") + pl.col("cogs_local")).round(2).alias("gross_margin_local"),
            (pl.col("logistics_cost_local") + pl.col("promotion_cost_local") + pl.col("warranty_cost_local")).round(2).alias("variable_cost_total_local"),
        ])
        df_pnl = df_pnl.with_columns([
            (pl.col("gross_margin_local") + pl.col("variable_cost_total_local")).round(2).alias("contribution_margin_local"),
        ])
        df_pnl = df_pnl.with_columns([
            (pl.col("contribution_margin_local") + pl.col("fixed_sgna_local")).round(2).alias("operating_profit_local"),
        ])

        waterfall_local_cols = [
            "gross_revenue_local", "sales_deduction_local", "net_revenue_local",
            "cogs_local", "gross_margin_local", "logistics_cost_local",
            "promotion_cost_local", "warranty_cost_local", "variable_cost_total_local",
            "contribution_margin_local", "fixed_sgna_local", "operating_profit_local"
        ]

        # Reporting currency metrics (Base items converted with FX rate, then strictly reconciled)
        df_pnl = df_pnl.with_columns([
            (pl.col("gross_revenue_local") * pl.col("fx_rate")).round(2).alias("gross_revenue_reporting"),
            (pl.col("sales_deduction_local") * pl.col("fx_rate")).round(2).alias("sales_deduction_reporting"),
            (pl.col("cogs_local") * pl.col("fx_rate")).round(2).alias("cogs_reporting"),
            (pl.col("logistics_cost_local") * pl.col("fx_rate")).round(2).alias("logistics_cost_reporting"),
            (pl.col("promotion_cost_local") * pl.col("fx_rate")).round(2).alias("promotion_cost_reporting"),
            (pl.col("warranty_cost_local") * pl.col("fx_rate")).round(2).alias("warranty_cost_reporting"),
            (pl.col("fixed_sgna_local") * pl.col("fx_rate")).round(2).alias("fixed_sgna_reporting"),
        ])
        df_pnl = df_pnl.with_columns([
            (pl.col("gross_revenue_reporting") + pl.col("sales_deduction_reporting")).round(2).alias("net_revenue_reporting"),
            (pl.col("logistics_cost_reporting") + pl.col("promotion_cost_reporting") + pl.col("warranty_cost_reporting")).round(2).alias("variable_cost_total_reporting"),
        ])
        df_pnl = df_pnl.with_columns([
            (pl.col("net_revenue_reporting") + pl.col("cogs_reporting")).round(2).alias("gross_margin_reporting"),
        ])
        df_pnl = df_pnl.with_columns([
            (pl.col("gross_margin_reporting") + pl.col("variable_cost_total_reporting")).round(2).alias("contribution_margin_reporting"),
        ])
        df_pnl = df_pnl.with_columns([
            (pl.col("contribution_margin_reporting") + pl.col("fixed_sgna_reporting")).round(2).alias("operating_profit_reporting"),
        ])

        # -------------------------------------------------------------
        # 5. Assemble Final Fact Table Schema
        # -------------------------------------------------------------
        df_pnl = df_pnl.with_columns([
            (pl.int_range(1, len(df_pnl) + 1, dtype=pl.Int64)).alias("fact_pnl_id"),
            pl.col("sales_qty").cast(pl.Int64),
        ])

        fact_cols = [
            "fact_pnl_id",
            "date_id",
            "customer_id",
            "product_id",
            "period",
            "entity_code",
            "bill_to_code",
            "model_code",
            "currency_code",
            "reporting_currency",
            "fx_rate",
            "sales_qty",
        ] + waterfall_local_cols + [c.replace("_local", "_reporting") for c in waterfall_local_cols]

        df_fact = df_pnl.select(fact_cols)

        # Export Core Fact Parquet
        out_parquet = self.settings.warehouse_dir / "fact_pnl.parquet"
        df_fact.write_parquet(out_parquet, compression="zstd")

        # DuckDB load via zero-copy direct parquet read
        if self.con is not None:
            self.con.execute("CREATE SCHEMA IF NOT EXISTS core;")
            self.con.execute(
                "CREATE OR REPLACE TABLE core.fact_pnl AS SELECT * FROM read_parquet(?);",
                [out_parquet.as_posix()],
            )

        total_rev = float(df_fact["gross_revenue_reporting"].sum() or 0.0)
        total_op = float(df_fact["operating_profit_reporting"].sum() or 0.0)

        return FactBuildStats(
            fact_name="Core P&L Fact",
            total_records=len(df_fact),
            table_name="core.fact_pnl",
            total_gross_revenue_reporting=total_rev,
            total_operating_profit_reporting=total_op,
            parquet_path=str(out_parquet),
        )

    def _validate_foreign_keys_fail_fast(
        self,
        df_pnl: pl.DataFrame,
        df_cust: pl.DataFrame,
        df_prod: pl.DataFrame,
        df_date: pl.DataFrame,
    ) -> None:
        """Strict Fail-Fast validation using vectorized Anti-Joins with NULL resilience."""
        cust_missing_df = (
            df_pnl.join(df_cust.select(["bill_to_code"]), on="bill_to_code", how="anti")
            .group_by("bill_to_code")
            .len()
            .sort("len", descending=True)
        )
        prod_missing_df = (
            df_pnl.join(df_prod.select(["model_code"]), on="model_code", how="anti")
            .group_by("model_code")
            .len()
            .sort("len", descending=True)
        )
        date_missing_df = (
            df_pnl.join(df_date.select(["period"]), on="period", how="anti")
            .group_by("period")
            .len()
            .sort("len", descending=True)
        )

        has_missing = len(cust_missing_df) > 0 or len(prod_missing_df) > 0 or len(date_missing_df) > 0

        if has_missing:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            suffix = uuid.uuid4().hex[:6]
            error_log_path = self.settings.logs_dir / f"unmapped_errors_{timestamp}_{suffix}.csv"
            error_log_path.parent.mkdir(parents=True, exist_ok=True)

            error_records: List[Dict[str, Any]] = []
            for r in cust_missing_df.to_dicts():
                error_records.append({
                    "timestamp": timestamp,
                    "dimension": "dim_customer",
                    "column_name": "bill_to_code",
                    "unmapped_code": str(r["bill_to_code"]),
                    "affected_rows": r["len"],
                })

            for r in prod_missing_df.to_dicts():
                error_records.append({
                    "timestamp": timestamp,
                    "dimension": "dim_product",
                    "column_name": "model_code",
                    "unmapped_code": str(r["model_code"]),
                    "affected_rows": r["len"],
                })

            for r in date_missing_df.to_dicts():
                error_records.append({
                    "timestamp": timestamp,
                    "dimension": "dim_date",
                    "column_name": "period",
                    "unmapped_code": str(r["period"]),
                    "affected_rows": r["len"],
                })

            with open(error_log_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["timestamp", "dimension", "column_name", "unmapped_code", "affected_rows"]
                )
                writer.writeheader()
                writer.writerows(error_records)

            # Build detailed error message
            msg_parts = [
                "[STRICT FAIL-FAST] Foreign key integrity violation detected! Found unmapped master codes:",
            ]
            if len(cust_missing_df) > 0:
                missing_codes = [str(r["bill_to_code"]) for r in cust_missing_df.head(5).to_dicts()]
                msg_parts.append(
                    f" - Missing Customers (bill_to_code): {len(cust_missing_df)} unique codes missing from master_customers.csv. Sample: {missing_codes}"
                )
            if len(prod_missing_df) > 0:
                missing_codes = [str(r["model_code"]) for r in prod_missing_df.head(5).to_dicts()]
                msg_parts.append(
                    f" - Missing Products (model_code): {len(prod_missing_df)} unique codes missing from master_products.csv. Sample: {missing_codes}"
                )
            if len(date_missing_df) > 0:
                missing_codes = [str(r["period"]) for r in date_missing_df.head(5).to_dicts()]
                msg_parts.append(
                    f" - Missing Periods: {len(date_missing_df)} unique periods missing from dim_date. Sample: {missing_codes}"
                )
            msg_parts.append(f"Full unmapped error details dumped to: '{error_log_path}'.")
            msg_parts.append("Please update the official master files before re-running the pipeline.")

            raise ValueError("\n".join(msg_parts))
