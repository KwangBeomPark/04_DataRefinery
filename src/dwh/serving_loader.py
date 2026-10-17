"""Serving layer exporter, Multipass (Drill-Across) AI Mart builder, and end-to-end DWH pipeline orchestrator."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from src.atomic_write import atomic_write_text
from src.dwh.dimension_builder import (
    DIM_ACCOUNT,
    DIM_CHANNEL,
    DIM_CUSTOMER,
    DIM_DATE,
    DIM_PRODUCT,
    DIM_WAREHOUSE,
    DimensionBuilder,
    DimensionBuildStats,
)
from src.dwh.extractor import EntityExtractor, ExtractionStats
from src.dwh.fact_builder import FactBuilder, FactBuildSummary, UnmappedCodeRecord
from src.dwh.mart_config import (
    AccountMappingRule,
    DwhConfigError,
    EntityIsolationConfig,
    MartWorkspacePaths,
    compute_mart_fingerprint,
    get_default_account_mapping,
    load_account_mapping,
    load_fx_rates,
)

AI_MART_VIEW_NAME = "v_ai_mart_pnl_inventory_price"

DIMENSION_TABLES: tuple[str, ...] = (
    DIM_DATE,
    DIM_CUSTOMER,
    DIM_PRODUCT,
    DIM_ACCOUNT,
    DIM_WAREHOUSE,
    DIM_CHANNEL,
)

FACT_TABLES: tuple[str, ...] = (
    "Fact_PnL_Long",
    "Fact_PnL",
    "Fact_Inventory",
    "Fact_Price",
)


@dataclass
class PipelineRunResult:
    """End-to-end execution result of the DWH pipeline."""

    entity_code: str
    period_spec: str
    reporting_currency: str
    mart_fingerprint: str
    workspace_root: str
    warehouse_db_path: str
    extraction_stats: dict[str, ExtractionStats]
    dimension_stats: DimensionBuildStats
    fact_summary: FactBuildSummary
    serving_validation: dict[str, Any]
    exported_artifacts: dict[str, str]
    audit_log_path: str
    started_at: str
    completed_at: str
    is_demo: bool = False

    @property
    def unmapped_records(self) -> list[UnmappedCodeRecord]:
        return self.fact_summary.unmapped_records

    @property
    def reconciliation_passed(self) -> bool:
        return bool(
            self.fact_summary.reconciliation_passed
            and self.serving_validation.get("fanout_check_passed", False)
            and self.serving_validation.get("grain_unique", False)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_code": self.entity_code,
            "is_demo": bool(self.is_demo),
            "period_spec": self.period_spec,
            "reporting_currency": self.reporting_currency,
            "mart_fingerprint": self.mart_fingerprint,
            "workspace_root": self.workspace_root,
            "warehouse_db_path": self.warehouse_db_path,
            "extraction_stats": {k: asdict(v) for k, v in self.extraction_stats.items()},
            "dimension_stats": self.dimension_stats.as_dict(),
            "fact_summary": {
                "fact_pnl_long_rows": self.fact_summary.fact_pnl_long_rows,
                "fact_pnl_rows": self.fact_summary.fact_pnl_rows,
                "fact_inventory_rows": self.fact_summary.fact_inventory_rows,
                "fact_price_rows": self.fact_summary.fact_price_rows,
                "reconciliation_passed": self.fact_summary.reconciliation_passed,
                "reconciliation_detail": self.fact_summary.reconciliation_detail,
                "unmapped_count": len(self.fact_summary.unmapped_records),
                "unmapped_records": [asdict(r) for r in self.fact_summary.unmapped_records],
            },
            "serving_validation": self.serving_validation,
            "exported_artifacts": self.exported_artifacts,
            "audit_log_path": self.audit_log_path,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class ServingLoader:
    """Creates Multipass (Drill-Across) AI Mart views, validates zero fan-out, and exports BI/AI artifacts."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        config: EntityIsolationConfig,
        paths: MartWorkspacePaths,
        mapping_rules: Mapping[str, AccountMappingRule] | None = None,
    ) -> None:
        self.con = con
        self.config = config
        self.paths = paths
        self.mapping_rules: dict[str, AccountMappingRule] = dict(
            mapping_rules if mapping_rules is not None else get_default_account_mapping()
        )

    def _relation_exists(self, name: str) -> bool:
        row = self.con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE LOWER(table_name) = LOWER(?)
            """,
            [name],
        ).fetchone()
        return bool(row and int(row[0]) > 0)

    def _get_relation_columns(self, name: str) -> set[str]:
        rows = self.con.execute(f'DESCRIBE "{name}"').fetchall()
        return {str(r[0]).lower() for r in rows}

    def _product_code_expr(self, alias: str, table_name: str, dim_prod_alias: str = "dp") -> str:
        cols = self._get_relation_columns(table_name)
        if "product_code" in cols:
            return f"COALESCE(NULLIF(TRIM(CAST({alias}.product_code AS VARCHAR)), ''), {dim_prod_alias}.product_code, 'UNKNOWN')"
        return f"COALESCE({dim_prod_alias}.product_code, 'UNKNOWN')"

    def _customer_count_expr(self, alias: str, table_name: str) -> str:
        cols = self._get_relation_columns(table_name)
        if "customer_code" in cols:
            return (
                f"CAST(COUNT(DISTINCT CASE WHEN {alias}.customer_sk = -1 "
                f"THEN COALESCE(NULLIF(TRIM(CAST({alias}.customer_code AS VARCHAR)), ''), '-1') "
                f"ELSE CAST({alias}.customer_sk AS VARCHAR) END) AS INTEGER)"
            )
        return f"CAST(COUNT(DISTINCT {alias}.customer_sk) AS INTEGER)"

    def create_ai_mart_views(self) -> dict[str, Any]:
        """Create `v_ai_mart_pnl_inventory_price` using Multipass (Drill-Across) CTE pre-aggregation.

        Prevents Fan-out / Chasm Traps by aggregating Fact_PnL (customer grain), Fact_Inventory (warehouse grain),
        and Fact_Price (channel grain) separately to
        `(date_sk, period, entity_code, product_sk, product_code, reporting_currency)`
        before joining via FULL OUTER JOIN. Preserves distinct unmapped `product_code` values even when `product_sk = -1`.
        """
        for required_table in ("Fact_PnL", "Fact_Inventory", "Fact_Price", DIM_DATE, DIM_PRODUCT):
            if not self._relation_exists(required_table):
                raise DwhConfigError(
                    f"Cannot create AI mart view {AI_MART_VIEW_NAME!r}: required table {required_table!r} does not exist."
                )

        pnl_prod_code_expr = self._product_code_expr("f", "Fact_PnL", "dp")
        inv_prod_code_expr = self._product_code_expr("f", "Fact_Inventory", "dp")
        price_prod_code_expr = self._product_code_expr("f", "Fact_Price", "dp")
        cust_cnt_expr = self._customer_count_expr("f", "Fact_PnL")

        self.con.execute(
            f"""
            CREATE OR REPLACE VIEW {AI_MART_VIEW_NAME} AS
            WITH pnl_by_product AS (
                SELECT
                    f.date_sk AS date_sk,
                    f.period AS period,
                    f.entity_code AS entity_code,
                    f.product_sk AS product_sk,
                    {pnl_prod_code_expr} AS product_code,
                    f.reporting_currency AS reporting_currency,
                    ROUND(SUM(f.sales_qty), 6) AS sales_qty,
                    ROUND(SUM(f.gross_revenue_reporting), 6) AS gross_revenue_reporting,
                    ROUND(SUM(f.sales_deduction_reporting), 6) AS sales_deduction_reporting,
                    ROUND(SUM(f.net_revenue_reporting), 6) AS net_revenue_reporting,
                    ROUND(SUM(f.cogs_reporting), 6) AS cogs_reporting,
                    ROUND(SUM(f.gross_margin_reporting), 6) AS gross_margin_reporting,
                    ROUND(SUM(f.logistics_cost_reporting), 6) AS logistics_cost_reporting,
                    ROUND(SUM(f.promotion_cost_reporting), 6) AS promotion_cost_reporting,
                    ROUND(SUM(f.warranty_cost_reporting), 6) AS warranty_cost_reporting,
                    ROUND(SUM(f.variable_cost_total_reporting), 6) AS variable_cost_total_reporting,
                    ROUND(SUM(f.contribution_margin_reporting), 6) AS contribution_margin_reporting,
                    ROUND(SUM(f.fixed_sgna_cost_reporting), 6) AS fixed_sgna_cost_reporting,
                    ROUND(SUM(f.operating_profit_reporting), 6) AS operating_profit_reporting,
                    {cust_cnt_expr} AS active_customer_count
                FROM Fact_PnL f
                LEFT JOIN {DIM_PRODUCT} dp
                    ON dp.product_sk = f.product_sk
                GROUP BY 1, 2, 3, 4, 5, 6
            ),
            inv_by_product AS (
                SELECT
                    f.date_sk AS date_sk,
                    f.period AS period,
                    f.entity_code AS entity_code,
                    f.product_sk AS product_sk,
                    {inv_prod_code_expr} AS product_code,
                    f.reporting_currency AS reporting_currency,
                    ROUND(SUM(f.ending_qty), 6) AS ending_qty,
                    ROUND(SUM(f.ending_inventory_val_reporting), 6) AS ending_inventory_val_reporting,
                    ROUND(SUM(f.warehouse_logistics_cost_reporting), 6) AS warehouse_logistics_cost_reporting,
                    ROUND(
                        CASE
                            WHEN SUM(f.ending_inventory_val_reporting) > 0
                            THEN SUM(f.dio_days * f.ending_inventory_val_reporting) / SUM(f.ending_inventory_val_reporting)
                            ELSE AVG(f.dio_days)
                        END,
                        6
                    ) AS dio_days,
                    CAST(COUNT(DISTINCT f.warehouse_sk) AS INTEGER) AS warehouse_count
                FROM Fact_Inventory f
                LEFT JOIN {DIM_PRODUCT} dp
                    ON dp.product_sk = f.product_sk
                GROUP BY 1, 2, 3, 4, 5, 6
            ),
            price_by_product AS (
                SELECT
                    f.date_sk AS date_sk,
                    f.period AS period,
                    f.entity_code AS entity_code,
                    f.product_sk AS product_sk,
                    {price_prod_code_expr} AS product_code,
                    f.reporting_currency AS reporting_currency,
                    ROUND(AVG(f.our_price_reporting), 6) AS avg_our_price_reporting,
                    ROUND(MIN(f.our_price_reporting), 6) AS min_our_price_reporting,
                    ROUND(MAX(f.our_price_reporting), 6) AS max_our_price_reporting,
                    ROUND(AVG(f.competitor_price_reporting), 6) AS avg_competitor_price_reporting,
                    ROUND(MIN(f.competitor_price_reporting), 6) AS min_competitor_price_reporting,
                    ROUND(MAX(f.competitor_price_reporting), 6) AS max_competitor_price_reporting,
                    ROUND(AVG(f.market_benchmark_price_reporting), 6) AS avg_market_benchmark_price_reporting,
                    ROUND(MIN(f.market_benchmark_price_reporting), 6) AS min_market_benchmark_price_reporting,
                    ROUND(MAX(f.market_benchmark_price_reporting), 6) AS max_market_benchmark_price_reporting,
                    CAST(COUNT(DISTINCT f.channel_sk) AS INTEGER) AS channel_count
                FROM Fact_Price f
                LEFT JOIN {DIM_PRODUCT} dp
                    ON dp.product_sk = f.product_sk
                GROUP BY 1, 2, 3, 4, 5, 6
            ),
            joined_marts AS (
                SELECT
                    date_sk,
                    period,
                    entity_code,
                    product_sk,
                    product_code,
                    reporting_currency,
                    COALESCE(pnl.sales_qty, 0.0) AS sales_qty,
                    COALESCE(pnl.gross_revenue_reporting, 0.0) AS gross_revenue_reporting,
                    COALESCE(pnl.sales_deduction_reporting, 0.0) AS sales_deduction_reporting,
                    COALESCE(pnl.net_revenue_reporting, 0.0) AS net_revenue_reporting,
                    COALESCE(pnl.cogs_reporting, 0.0) AS cogs_reporting,
                    COALESCE(pnl.gross_margin_reporting, 0.0) AS gross_margin_reporting,
                    COALESCE(pnl.logistics_cost_reporting, 0.0) AS logistics_cost_reporting,
                    COALESCE(pnl.promotion_cost_reporting, 0.0) AS promotion_cost_reporting,
                    COALESCE(pnl.warranty_cost_reporting, 0.0) AS warranty_cost_reporting,
                    COALESCE(pnl.variable_cost_total_reporting, 0.0) AS variable_cost_total_reporting,
                    COALESCE(pnl.contribution_margin_reporting, 0.0) AS contribution_margin_reporting,
                    COALESCE(pnl.fixed_sgna_cost_reporting, 0.0) AS fixed_sgna_cost_reporting,
                    COALESCE(pnl.operating_profit_reporting, 0.0) AS operating_profit_reporting,
                    COALESCE(pnl.active_customer_count, 0) AS active_customer_count,
                    COALESCE(inv.ending_qty, 0.0) AS ending_qty,
                    COALESCE(inv.ending_inventory_val_reporting, 0.0) AS ending_inventory_val_reporting,
                    COALESCE(inv.warehouse_logistics_cost_reporting, 0.0) AS warehouse_logistics_cost_reporting,
                    COALESCE(inv.dio_days, 0.0) AS dio_days,
                    COALESCE(inv.warehouse_count, 0) AS warehouse_count,
                    price.avg_our_price_reporting,
                    price.min_our_price_reporting,
                    price.max_our_price_reporting,
                    price.avg_competitor_price_reporting,
                    price.min_competitor_price_reporting,
                    price.max_competitor_price_reporting,
                    price.avg_market_benchmark_price_reporting,
                    price.min_market_benchmark_price_reporting,
                    price.max_market_benchmark_price_reporting,
                    COALESCE(price.channel_count, 0) AS channel_count
                FROM pnl_by_product pnl
                FULL OUTER JOIN inv_by_product inv
                    USING (date_sk, period, entity_code, product_sk, product_code, reporting_currency)
                FULL OUTER JOIN price_by_product price
                    USING (date_sk, period, entity_code, product_sk, product_code, reporting_currency)
            )
            SELECT
                jm.date_sk,
                jm.period,
                COALESCE(d.year, -1) AS year,
                COALESCE(d.quarter, 'UNKNOWN') AS quarter,
                COALESCE(d.month, -1) AS month,
                jm.entity_code,
                jm.product_sk,
                jm.product_code,
                COALESCE(p.product_name, 'UNKNOWN') AS product_name,
                COALESCE(p.division, 'UNKNOWN') AS division,
                COALESCE(p.category, 'UNKNOWN') AS category,
                jm.reporting_currency,
                jm.sales_qty,
                jm.gross_revenue_reporting,
                jm.sales_deduction_reporting,
                jm.net_revenue_reporting,
                jm.cogs_reporting,
                jm.gross_margin_reporting,
                jm.logistics_cost_reporting,
                jm.promotion_cost_reporting,
                jm.warranty_cost_reporting,
                jm.variable_cost_total_reporting,
                jm.contribution_margin_reporting,
                jm.fixed_sgna_cost_reporting,
                jm.operating_profit_reporting,
                jm.active_customer_count,
                jm.ending_qty,
                jm.ending_inventory_val_reporting,
                jm.warehouse_logistics_cost_reporting,
                jm.dio_days,
                jm.warehouse_count,
                jm.avg_our_price_reporting,
                jm.min_our_price_reporting,
                jm.max_our_price_reporting,
                jm.avg_competitor_price_reporting,
                jm.min_competitor_price_reporting,
                jm.max_competitor_price_reporting,
                jm.avg_market_benchmark_price_reporting,
                jm.min_market_benchmark_price_reporting,
                jm.max_market_benchmark_price_reporting,
                jm.channel_count
            FROM joined_marts jm
            LEFT JOIN {DIM_DATE} d
                ON d.date_sk = jm.date_sk
            LEFT JOIN {DIM_PRODUCT} p
                ON p.product_sk = jm.product_sk
            ORDER BY jm.period, jm.entity_code, jm.product_sk, jm.product_code;
            """
        )

        return self.validate_serving_views()

    def validate_serving_views(self) -> dict[str, Any]:
        """Validate grain uniqueness and zero fan-out across all P&L, Inventory, and Price metrics on `v_ai_mart_pnl_inventory_price`."""
        if not self._relation_exists(AI_MART_VIEW_NAME):
            raise DwhConfigError(f"Serving view {AI_MART_VIEW_NAME!r} does not exist.")

        grain_row = self.con.execute(
            f"""
            SELECT
                (SELECT COUNT(*) FROM {AI_MART_VIEW_NAME}) AS total_rows,
                (
                    SELECT COUNT(*)
                    FROM (
                        SELECT DISTINCT
                            date_sk,
                            period,
                            entity_code,
                            product_sk,
                            product_code,
                            reporting_currency
                        FROM {AI_MART_VIEW_NAME}
                    )
                ) AS distinct_grain_rows;
            """
        ).fetchone()
        total_rows = int(grain_row[0]) if grain_row else 0
        distinct_grain_rows = int(grain_row[1]) if grain_row else 0
        duplicate_grain_rows = total_rows - distinct_grain_rows
        grain_unique = duplicate_grain_rows == 0

        if not grain_unique:
            raise DwhConfigError(
                f"Duplicate grain keys detected in {AI_MART_VIEW_NAME}: "
                f"total_rows={total_rows}, distinct_grain_rows={distinct_grain_rows}"
            )

        pnl_additive_cols: tuple[str, ...] = (
            "sales_qty",
            "gross_revenue_reporting",
            "sales_deduction_reporting",
            "net_revenue_reporting",
            "cogs_reporting",
            "gross_margin_reporting",
            "logistics_cost_reporting",
            "promotion_cost_reporting",
            "warranty_cost_reporting",
            "variable_cost_total_reporting",
            "contribution_margin_reporting",
            "fixed_sgna_cost_reporting",
            "operating_profit_reporting",
        )
        inv_additive_cols: tuple[str, ...] = (
            "ending_qty",
            "ending_inventory_val_reporting",
            "warehouse_logistics_cost_reporting",
        )

        # 1. Total-level reconciliation for all additive P&L and Inventory columns
        pnl_sum_select = ", ".join(f"COALESCE(SUM({c}), 0.0)" for c in pnl_additive_cols)
        view_pnl_totals = self.con.execute(
            f"SELECT {pnl_sum_select} FROM {AI_MART_VIEW_NAME};"
        ).fetchone()
        fact_pnl_totals = self.con.execute(
            f"SELECT {pnl_sum_select} FROM Fact_PnL;"
        ).fetchone()

        inv_sum_select = ", ".join(f"COALESCE(SUM({c}), 0.0)" for c in inv_additive_cols)
        view_inv_totals = self.con.execute(
            f"SELECT {inv_sum_select} FROM {AI_MART_VIEW_NAME};"
        ).fetchone()
        fact_inv_totals = self.con.execute(
            f"SELECT {inv_sum_select} FROM Fact_Inventory;"
        ).fetchone()

        pnl_total_diffs: dict[str, float] = {}
        for idx, col in enumerate(pnl_additive_cols):
            v_val = float(view_pnl_totals[idx]) if view_pnl_totals else 0.0
            f_val = float(fact_pnl_totals[idx]) if fact_pnl_totals else 0.0
            pnl_total_diffs[col] = abs(v_val - f_val)

        inv_total_diffs: dict[str, float] = {}
        for idx, col in enumerate(inv_additive_cols):
            v_val = float(view_inv_totals[idx]) if view_inv_totals else 0.0
            f_val = float(fact_inv_totals[idx]) if fact_inv_totals else 0.0
            inv_total_diffs[col] = abs(v_val - f_val)

        view_net_rev = float(view_pnl_totals[3]) if view_pnl_totals else 0.0
        fact_net_rev = float(fact_pnl_totals[3]) if fact_pnl_totals else 0.0
        view_op_profit = float(view_pnl_totals[12]) if view_pnl_totals else 0.0
        fact_op_profit = float(fact_pnl_totals[12]) if fact_pnl_totals else 0.0
        view_inv_val = float(view_inv_totals[1]) if view_inv_totals else 0.0
        fact_inv_val = float(fact_inv_totals[1]) if fact_inv_totals else 0.0

        net_rev_diff = pnl_total_diffs["net_revenue_reporting"]
        op_profit_diff = pnl_total_diffs["operating_profit_reporting"]
        inv_val_diff = inv_total_diffs["ending_inventory_val_reporting"]

        # 2. Grain-level reconciliation across P&L, Inventory, and Price
        pnl_prod_code_expr = self._product_code_expr("f", "Fact_PnL", "dp")
        inv_prod_code_expr = self._product_code_expr("f", "Fact_Inventory", "dp")
        price_prod_code_expr = self._product_code_expr("f", "Fact_Price", "dp")
        cust_cnt_expr = self._customer_count_expr("f", "Fact_PnL")

        pnl_grp_select = ", ".join(f"ROUND(SUM(f.{c}), 6) AS {c}" for c in pnl_additive_cols)
        pnl_diff_select = ", ".join(
            f"COALESCE(MAX(ABS(COALESCE(f_grp.{c}, 0.0) - COALESCE(v_grp.{c}, 0.0))), 0.0)"
            for c in [*pnl_additive_cols, "active_customer_count"]
        )
        max_pnl_group_diff_row = self.con.execute(
            f"""
            WITH f_grp AS (
                SELECT
                    f.date_sk,
                    f.period,
                    f.entity_code,
                    f.product_sk,
                    {pnl_prod_code_expr} AS product_code,
                    f.reporting_currency,
                    {pnl_grp_select},
                    CAST({cust_cnt_expr} AS DOUBLE) AS active_customer_count
                FROM Fact_PnL f
                LEFT JOIN {DIM_PRODUCT} dp ON dp.product_sk = f.product_sk
                GROUP BY 1, 2, 3, 4, 5, 6
            ),
            v_grp AS (
                SELECT
                    date_sk,
                    period,
                    entity_code,
                    product_sk,
                    product_code,
                    reporting_currency,
                    {", ".join(pnl_additive_cols)},
                    CAST(active_customer_count AS DOUBLE) AS active_customer_count
                FROM {AI_MART_VIEW_NAME}
            )
            SELECT {pnl_diff_select}
            FROM f_grp
            FULL OUTER JOIN v_grp
                USING (date_sk, period, entity_code, product_sk, product_code, reporting_currency);
            """
        ).fetchone()

        max_inv_group_diff_row = self.con.execute(
            f"""
            WITH f_grp AS (
                SELECT
                    f.date_sk,
                    f.period,
                    f.entity_code,
                    f.product_sk,
                    {inv_prod_code_expr} AS product_code,
                    f.reporting_currency,
                    ROUND(SUM(f.ending_qty), 6) AS ending_qty,
                    ROUND(SUM(f.ending_inventory_val_reporting), 6) AS ending_inventory_val_reporting,
                    ROUND(SUM(f.warehouse_logistics_cost_reporting), 6) AS warehouse_logistics_cost_reporting,
                    ROUND(
                        CASE
                            WHEN SUM(f.ending_inventory_val_reporting) > 0
                            THEN SUM(f.dio_days * f.ending_inventory_val_reporting) / SUM(f.ending_inventory_val_reporting)
                            ELSE AVG(f.dio_days)
                        END,
                        6
                    ) AS dio_days,
                    CAST(COUNT(DISTINCT f.warehouse_sk) AS DOUBLE) AS warehouse_count
                FROM Fact_Inventory f
                LEFT JOIN {DIM_PRODUCT} dp ON dp.product_sk = f.product_sk
                GROUP BY 1, 2, 3, 4, 5, 6
            ),
            v_grp AS (
                SELECT
                    date_sk,
                    period,
                    entity_code,
                    product_sk,
                    product_code,
                    reporting_currency,
                    ending_qty,
                    ending_inventory_val_reporting,
                    warehouse_logistics_cost_reporting,
                    dio_days,
                    CAST(warehouse_count AS DOUBLE) AS warehouse_count
                FROM {AI_MART_VIEW_NAME}
            )
            SELECT
                COALESCE(MAX(ABS(COALESCE(f_grp.ending_qty, 0.0) - COALESCE(v_grp.ending_qty, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.ending_inventory_val_reporting, 0.0) - COALESCE(v_grp.ending_inventory_val_reporting, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.warehouse_logistics_cost_reporting, 0.0) - COALESCE(v_grp.warehouse_logistics_cost_reporting, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.dio_days, 0.0) - COALESCE(v_grp.dio_days, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.warehouse_count, 0.0) - COALESCE(v_grp.warehouse_count, 0.0))), 0.0)
            FROM f_grp
            FULL OUTER JOIN v_grp
                USING (date_sk, period, entity_code, product_sk, product_code, reporting_currency);
            """
        ).fetchone()

        max_price_group_diff_row = self.con.execute(
            f"""
            WITH f_grp AS (
                SELECT
                    f.date_sk,
                    f.period,
                    f.entity_code,
                    f.product_sk,
                    {price_prod_code_expr} AS product_code,
                    f.reporting_currency,
                    ROUND(AVG(f.our_price_reporting), 6) AS avg_our,
                    ROUND(MIN(f.our_price_reporting), 6) AS min_our,
                    ROUND(MAX(f.our_price_reporting), 6) AS max_our,
                    ROUND(AVG(f.competitor_price_reporting), 6) AS avg_comp,
                    ROUND(MIN(f.competitor_price_reporting), 6) AS min_comp,
                    ROUND(MAX(f.competitor_price_reporting), 6) AS max_comp,
                    ROUND(AVG(f.market_benchmark_price_reporting), 6) AS avg_bench,
                    ROUND(MIN(f.market_benchmark_price_reporting), 6) AS min_bench,
                    ROUND(MAX(f.market_benchmark_price_reporting), 6) AS max_bench,
                    CAST(COUNT(DISTINCT f.channel_sk) AS DOUBLE) AS channel_count
                FROM Fact_Price f
                LEFT JOIN {DIM_PRODUCT} dp ON dp.product_sk = f.product_sk
                GROUP BY 1, 2, 3, 4, 5, 6
            ),
            v_grp AS (
                SELECT
                    date_sk,
                    period,
                    entity_code,
                    product_sk,
                    product_code,
                    reporting_currency,
                    avg_our_price_reporting AS avg_our,
                    min_our_price_reporting AS min_our,
                    max_our_price_reporting AS max_our,
                    avg_competitor_price_reporting AS avg_comp,
                    min_competitor_price_reporting AS min_comp,
                    max_competitor_price_reporting AS max_comp,
                    avg_market_benchmark_price_reporting AS avg_bench,
                    min_market_benchmark_price_reporting AS min_bench,
                    max_market_benchmark_price_reporting AS max_bench,
                    CAST(channel_count AS DOUBLE) AS channel_count
                FROM {AI_MART_VIEW_NAME}
            )
            SELECT
                COALESCE(MAX(ABS(COALESCE(f_grp.avg_our, 0.0) - COALESCE(v_grp.avg_our, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.min_our, 0.0) - COALESCE(v_grp.min_our, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.max_our, 0.0) - COALESCE(v_grp.max_our, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.avg_comp, 0.0) - COALESCE(v_grp.avg_comp, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.min_comp, 0.0) - COALESCE(v_grp.min_comp, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.max_comp, 0.0) - COALESCE(v_grp.max_comp, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.avg_bench, 0.0) - COALESCE(v_grp.avg_bench, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.min_bench, 0.0) - COALESCE(v_grp.min_bench, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.max_bench, 0.0) - COALESCE(v_grp.max_bench, 0.0))), 0.0),
                COALESCE(MAX(ABS(COALESCE(f_grp.channel_count, 0.0) - COALESCE(v_grp.channel_count, 0.0))), 0.0)
            FROM f_grp
            FULL OUTER JOIN v_grp
                USING (date_sk, period, entity_code, product_sk, product_code, reporting_currency);
            """
        ).fetchone()

        pnl_grain_diffs = [
            float(x) for x in (max_pnl_group_diff_row or (0.0,) * (len(pnl_additive_cols) + 1))
        ]
        inv_grain_diffs = [float(x) for x in (max_inv_group_diff_row or (0.0,) * 5)]
        price_grain_diffs = [float(x) for x in (max_price_group_diff_row or (0.0,) * 10)]

        max_prod_net_rev_diff = pnl_grain_diffs[3]
        max_prod_op_profit_diff = pnl_grain_diffs[12]
        max_prod_inv_val_diff = inv_grain_diffs[1]
        max_all_metric_diff = max(
            *pnl_total_diffs.values(),
            *inv_total_diffs.values(),
            *pnl_grain_diffs,
            *inv_grain_diffs,
            *price_grain_diffs,
        )

        fanout_check_passed = max_all_metric_diff <= 1e-4
        if not fanout_check_passed:
            raise DwhConfigError(
                f"Fan-out / reconciliation check failed on {AI_MART_VIEW_NAME}! "
                f"max_all_metric_diff={max_all_metric_diff:.6f}, "
                f"net_revenue diff={net_rev_diff:.6f} (max_product_diff={max_prod_net_rev_diff:.6f}), "
                f"operating_profit diff={op_profit_diff:.6f} (max_product_diff={max_prod_op_profit_diff:.6f}), "
                f"inventory_val diff={inv_val_diff:.6f} (max_product_diff={max_prod_inv_val_diff:.6f})"
            )

        return {
            "view_name": AI_MART_VIEW_NAME,
            "total_rows": total_rows,
            "distinct_grain_rows": distinct_grain_rows,
            "duplicate_grain_rows": duplicate_grain_rows,
            "grain_unique": grain_unique,
            "fanout_check_passed": fanout_check_passed,
            "view_net_revenue_reporting": round(view_net_rev, 6),
            "fact_net_revenue_reporting": round(fact_net_rev, 6),
            "net_revenue_diff": round(net_rev_diff, 6),
            "view_operating_profit_reporting": round(view_op_profit, 6),
            "fact_operating_profit_reporting": round(fact_op_profit, 6),
            "operating_profit_diff": round(op_profit_diff, 6),
            "view_ending_inventory_val_reporting": round(view_inv_val, 6),
            "fact_ending_inventory_val_reporting": round(fact_inv_val, 6),
            "inventory_val_diff": round(inv_val_diff, 6),
            "max_product_net_revenue_diff": round(max_prod_net_rev_diff, 6),
            "max_product_operating_profit_diff": round(max_prod_op_profit_diff, 6),
            "max_product_inventory_val_diff": round(max_prod_inv_val_diff, 6),
            "max_all_metric_diff": round(max_all_metric_diff, 6),
        }

    def _build_ai_schema_dictionary(self) -> dict[str, Any]:
        """Build structured AI schema dictionary with explicit grain and additivity rules."""
        fingerprint = compute_mart_fingerprint(self.config, self.mapping_rules)
        return {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entity_code": self.config.entity_code,
            "reporting_currency": self.config.reporting_currency,
            "allowed_currencies": list(self.config.allowed_currencies),
            "mart_fingerprint": fingerprint,
            "additivity_rules": {
                "ADDITIVE": {
                    "rule": "Safe to SUM across all dimensions including time (period), customer, product, and account.",
                    "columns": [
                        "sales_qty",
                        "gross_revenue_local",
                        "gross_revenue_reporting",
                        "sales_deduction_local",
                        "sales_deduction_reporting",
                        "net_revenue_local",
                        "net_revenue_reporting",
                        "cogs_local",
                        "cogs_reporting",
                        "gross_margin_local",
                        "gross_margin_reporting",
                        "logistics_cost_local",
                        "logistics_cost_reporting",
                        "promotion_cost_local",
                        "promotion_cost_reporting",
                        "warranty_cost_local",
                        "warranty_cost_reporting",
                        "variable_cost_total_local",
                        "variable_cost_total_reporting",
                        "contribution_margin_local",
                        "contribution_margin_reporting",
                        "fixed_sgna_cost_local",
                        "fixed_sgna_cost_reporting",
                        "operating_profit_local",
                        "operating_profit_reporting",
                        "warehouse_logistics_cost_local",
                        "warehouse_logistics_cost_reporting",
                    ],
                },
                "SEMI_ADDITIVE_SNAPSHOT": {
                    "rule": (
                        "SEMI_ADDITIVE_SNAPSHOT (시간축 SUM 금지): Safe to SUM across products or warehouses "
                        "within a single period, but NEVER SUM across multiple periods (months/quarters/years). "
                        "Use latest period snapshot (LAST_VALUE) or average across periods."
                    ),
                    "columns": [
                        "ending_qty",
                        "ending_inventory_val_local",
                        "ending_inventory_val_reporting",
                    ],
                },
                "NON_ADDITIVE": {
                    "rule": (
                        "NON_ADDITIVE (단순 SUM 금지, 분자/분모 재계산 필수): Ratios, unit prices, and DIO days "
                        "must NEVER be summed. Always recompute from numerator/denominator totals or use weighted averages."
                    ),
                    "columns": [
                        "dio_days",
                        "our_price_local",
                        "our_price_reporting",
                        "competitor_price_local",
                        "competitor_price_reporting",
                        "market_benchmark_price_local",
                        "market_benchmark_price_reporting",
                        "avg_our_price_reporting",
                        "min_our_price_reporting",
                        "max_our_price_reporting",
                        "avg_competitor_price_reporting",
                        "min_competitor_price_reporting",
                        "max_competitor_price_reporting",
                        "avg_market_benchmark_price_reporting",
                        "min_market_benchmark_price_reporting",
                        "max_market_benchmark_price_reporting",
                        "active_customer_count",
                        "warehouse_count",
                        "channel_count",
                    ],
                },
            },
            "tables": {
                "Fact_PnL_Long": {
                    "type": "FACT_TABLE",
                    "grain": "(date_sk, period, entity_code, customer_sk, customer_code, product_sk, product_code, account_sk, currency_code, reporting_currency)",
                    "description": "Account-level long-format P&L fact table excluding source subtotals to prevent double counting.",
                },
                "Fact_PnL": {
                    "type": "FACT_TABLE",
                    "grain": "(date_sk, period, entity_code, customer_sk, customer_code, product_sk, product_code, currency_code, reporting_currency)",
                    "description": "Customer x Product monthly P&L waterfall summary fact table in local and reporting currencies.",
                },
                "Fact_Inventory": {
                    "type": "FACT_TABLE",
                    "grain": "(date_sk, period, entity_code, warehouse_sk, product_sk, product_code, currency_code, reporting_currency)",
                    "description": "Warehouse x Product monthly ending inventory snapshot and logistics cost fact table.",
                },
                "Fact_Price": {
                    "type": "FACT_TABLE",
                    "grain": "(date_sk, period, entity_code, channel_sk, product_sk, product_code, currency_code, reporting_currency)",
                    "description": "Channel x Product monthly pricesensing benchmark fact table.",
                },
                AI_MART_VIEW_NAME: {
                    "type": "AI_SERVING_VIEW",
                    "grain": "(date_sk, period, entity_code, product_sk, product_code, reporting_currency)",
                    "description": (
                        "Fan-out free Multipass (Drill-Across) integrated AI mart combining P&L, Inventory, and "
                        "Market Price pre-aggregated at the Product x Period grain in reporting currency."
                    ),
                },
            },
        }

    def _render_ai_schema_markdown(self, dictionary: Mapping[str, Any]) -> str:
        """Render human- and LLM-friendly Markdown documentation for the serving schema."""
        lines = [
            f"# AI & BI Serving Mart Schema Dictionary ({dictionary['entity_code']})",
            "",
            f"- **Entity Code**: `{dictionary['entity_code']}`",
            f"- **Reporting Currency**: `{dictionary['reporting_currency']}`",
            f"- **Allowed Currencies**: `{', '.join(dictionary['allowed_currencies'])}`",
            f"- **Mart Fingerprint**: `{dictionary['mart_fingerprint']}`",
            "",
            "## 1. Critical Additivity Rules (필수 집계 규칙)",
            "",
            "### ADDITIVE (완전 가산 지표)",
            f"- **원칙**: {dictionary['additivity_rules']['ADDITIVE']['rule']}",
            f"- **대상 컬럼**: {', '.join(f'`{c}`' for c in dictionary['additivity_rules']['ADDITIVE']['columns'])}",
            "",
            "### SEMI_ADDITIVE_SNAPSHOT (시간축 SUM 금지)",
            f"- **원칙**: {dictionary['additivity_rules']['SEMI_ADDITIVE_SNAPSHOT']['rule']}",
            f"- **대상 컬럼**: {', '.join(f'`{c}`' for c in dictionary['additivity_rules']['SEMI_ADDITIVE_SNAPSHOT']['columns'])}",
            "",
            "### NON_ADDITIVE (단순 SUM 금지, 분자/분모 재계산 필수)",
            f"- **원칙**: {dictionary['additivity_rules']['NON_ADDITIVE']['rule']}",
            f"- **대상 컬럼**: {', '.join(f'`{c}`' for c in dictionary['additivity_rules']['NON_ADDITIVE']['columns'])}",
            "",
            "## 2. Tables & Serving Views",
            "",
        ]
        for table_name, meta in dictionary["tables"].items():
            lines.extend(
                [
                    f"### `{table_name}` ({meta['type']})",
                    f"- **Grain**: `{meta['grain']}`",
                    f"- **Description**: {meta['description']}",
                    "",
                ]
            )
        return "\n".join(lines)

    def _stage_copy_to_temp_file(self, source_sql: str, target_path: Path, format_options: str) -> Path:
        """Export a DuckDB relation or query to a temporary sibling file without replacing target_path yet."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.parent / f".{target_path.name}.tmp.{uuid.uuid4().hex}{target_path.suffix}"
        tmp_posix = tmp_path.resolve().as_posix().replace("'", "''")
        self.con.execute(f"COPY {source_sql} TO '{tmp_posix}' ({format_options});")
        return tmp_path

    def export_serving_artifacts(self) -> dict[str, str]:
        """Export Dimension/Fact Parquet & CSV files, AI Mart View artifacts, and AI Schema Dictionary as an atomic bundle."""
        self.paths.ensure_dirs()

        if not self._relation_exists(AI_MART_VIEW_NAME):
            self.create_ai_mart_views()
        else:
            self.validate_serving_views()

        staged_pairs: list[tuple[str, Path, Path]] = []
        try:
            # 1. Stage all Dim_* and Fact_* tables to temporary files first
            for table_name in (*DIMENSION_TABLES, *FACT_TABLES):
                if not self._relation_exists(table_name):
                    continue
                wh_parquet = self.paths.warehouse_dir / f"{table_name}.parquet"
                srv_csv = self.paths.serving_dir / f"{table_name}.csv"

                tmp_wh = self._stage_copy_to_temp_file(f'"{table_name}"', wh_parquet, "FORMAT PARQUET")
                staged_pairs.append((f"warehouse_{table_name}_parquet", tmp_wh, wh_parquet))

                tmp_srv = self._stage_copy_to_temp_file(
                    f'"{table_name}"', srv_csv, "FORMAT CSV, HEADER TRUE"
                )
                staged_pairs.append((f"serving_{table_name}_csv", tmp_srv, srv_csv))

            # 2. Stage v_ai_mart_pnl_inventory_price CSV and Parquet
            view_csv = self.paths.serving_dir / f"{AI_MART_VIEW_NAME}.csv"
            view_parquet = self.paths.serving_dir / f"{AI_MART_VIEW_NAME}.parquet"

            tmp_view_csv = self._stage_copy_to_temp_file(
                f"(SELECT * FROM {AI_MART_VIEW_NAME})", view_csv, "FORMAT CSV, HEADER TRUE"
            )
            staged_pairs.append((f"serving_{AI_MART_VIEW_NAME}_csv", tmp_view_csv, view_csv))

            tmp_view_pq = self._stage_copy_to_temp_file(
                f"(SELECT * FROM {AI_MART_VIEW_NAME})", view_parquet, "FORMAT PARQUET"
            )
            staged_pairs.append((f"serving_{AI_MART_VIEW_NAME}_parquet", tmp_view_pq, view_parquet))

            # 3. Stage ai_schema_dictionary.json and ai_schema_dictionary.md
            schema_dict = self._build_ai_schema_dictionary()
            schema_md = self._render_ai_schema_markdown(schema_dict)

            dict_json_path = self.paths.serving_dir / "ai_schema_dictionary.json"
            dict_md_path = self.paths.serving_dir / "ai_schema_dictionary.md"

            tmp_json = dict_json_path.parent / f".{dict_json_path.name}.tmp.{uuid.uuid4().hex}.json"
            tmp_md = dict_md_path.parent / f".{dict_md_path.name}.tmp.{uuid.uuid4().hex}.md"

            atomic_write_text(tmp_json, json.dumps(schema_dict, ensure_ascii=False, indent=2))
            staged_pairs.append(("ai_schema_dictionary_json", tmp_json, dict_json_path))

            atomic_write_text(tmp_md, schema_md)
            staged_pairs.append(("ai_schema_dictionary_md", tmp_md, dict_md_path))

            # 4. Promote all staged files atomically with backup/rollback
            return _commit_staged_file_pairs(staged_pairs, self.paths.root_dir)
        finally:
            for _, tmp_file, _ in staged_pairs:
                tmp_file.unlink(missing_ok=True)


def _commit_staged_file_pairs(
    staged_pairs: list[tuple[str, Path, Path]],
    backup_parent_dir: Path,
) -> dict[str, str]:
    """Promote all `(key, tmp_path, target_path)` pairs atomically, rolling back only modified files on failure."""
    backup_dir = Path(tempfile.mkdtemp(prefix=".export_bak_", dir=str(backup_parent_dir)))
    backup_map: dict[Path, Path | None] = {}
    touched_targets: list[Path] = []
    exported: dict[str, str] = {}
    succeeded = False
    try:
        # 1. Back up existing targets before modifying anything
        for idx, (_, _, target_path) in enumerate(staged_pairs):
            if target_path.exists():
                bak_file = backup_dir / f"bak_{idx}_{target_path.name}"
                shutil.copy2(target_path, bak_file)
                backup_map[target_path] = bak_file
            else:
                backup_map[target_path] = None

        # 2. Promote staged files into target locations, recording only touched targets
        for key, tmp_path, target_path in staged_pairs:
            touched_targets.append(target_path)
            os.replace(tmp_path, target_path)
            exported[key] = str(target_path.resolve())

        succeeded = True
        return exported
    finally:
        if not succeeded:
            for target_path in reversed(touched_targets):
                bak_file = backup_map.get(target_path)
                if bak_file is not None and bak_file.exists():
                    shutil.copy2(bak_file, target_path)
                else:
                    target_path.unlink(missing_ok=True)
        shutil.rmtree(backup_dir, ignore_errors=True)


def _resolve_source_file(
    explicit_path: Path | str | None,
    base_dir: Path,
    candidates: tuple[str, ...],
    *,
    required: bool = True,
) -> Path | None:
    if explicit_path is not None:
        resolved = Path(explicit_path)
        if not resolved.exists():
            raise DwhConfigError(f"Specified input file does not exist: {resolved}")
        return resolved

    for name in candidates:
        candidate_path = base_dir / name
        if candidate_path.exists() and candidate_path.is_file():
            return candidate_path

    # Glob fallback
    if base_dir.exists() and base_dir.is_dir():
        all_files = sorted(p for p in base_dir.iterdir() if p.is_file())
        for token in candidates:
            stem_token = Path(token).stem.lower()
            for f in all_files:
                if stem_token in f.name.lower():
                    return f

    if required:
        raise DwhConfigError(
            f"Could not locate required source file among {candidates} in directory {base_dir}"
        )
    return None


def _resolve_and_validate_workspace_paths(
    config: EntityIsolationConfig,
    effective_workspace: Path | str | None,
    *,
    is_demo: bool,
) -> MartWorkspacePaths:
    """Resolve MartWorkspacePaths while preventing demo runs from targeting production or cross-entity folders."""
    entity_lower = config.entity_code.lower()
    prod_folder = f"dwh_{entity_lower}"
    sample_folder = f"dwh_{entity_lower}_sample"

    if effective_workspace is None:
        if is_demo:
            return MartWorkspacePaths.from_entity(f"{config.entity_code}_sample")
        return MartWorkspacePaths.from_entity(config.entity_code)

    ws_path = Path(effective_workspace)
    for part in ws_path.parts:
        part_lower = part.lower()
        if part_lower.startswith("dwh_"):
            suffix = part_lower[4:]
            if suffix and suffix != entity_lower and not suffix.startswith(f"{entity_lower}_"):
                raise DwhConfigError(
                    f"Workspace entity mismatch: {effective_workspace!r} belongs to entity {suffix.upper()!r}, "
                    f"which conflicts with configured entity_code={config.entity_code!r}."
                )

    # Check if ws_path itself is already an initialized workspace root
    is_direct_ws_root = (
        (ws_path / "warehouse" / "warehouse.duckdb").exists()
        or (ws_path / "logs" / "pipeline_audit.json").exists()
    )

    # Check existing dwh_* subdirectories when ws_path is a parent directory
    if ws_path.exists() and ws_path.is_dir() and not is_direct_ws_root:
        for child in ws_path.iterdir():
            if not child.is_dir():
                continue
            child_lower = child.name.lower()
            if child_lower.startswith("dwh_"):
                suffix = child_lower[4:]
                if suffix and suffix != entity_lower and not suffix.startswith(f"{entity_lower}_"):
                    raise DwhConfigError(
                        f"Workspace entity mismatch: {str(ws_path)!r} already contains {child.name!r}, "
                        f"cannot run entity_code={config.entity_code!r}."
                    )
        if is_demo and (ws_path / prod_folder).exists():
            raise DwhConfigError(
                f"Workspace mode mismatch: {str(ws_path)!r} already contains production workspace {prod_folder!r}."
            )
        if not is_demo and (ws_path / sample_folder).exists():
            raise DwhConfigError(
                f"Workspace mode mismatch: {str(ws_path)!r} already contains demo sample workspace {sample_folder!r}."
            )

    if is_demo:
        if ws_path.name.lower() == prod_folder:
            raise DwhConfigError(
                f"Demo mode cannot target production workspace directory {str(ws_path)!r} ({prod_folder!r}). "
                f"Use an isolated sample workspace such as {sample_folder!r}."
            )
        if ws_path.name.lower().startswith("dwh_") or is_direct_ws_root:
            return MartWorkspacePaths(root_dir=ws_path)
        return MartWorkspacePaths.from_entity(f"{config.entity_code}_sample", base_dir=ws_path)

    if ws_path.name.lower() == sample_folder:
        raise DwhConfigError(
            f"Production run cannot target sample workspace directory {str(ws_path)!r} ({sample_folder!r})."
        )
    if ws_path.name.lower().startswith("dwh_") or is_direct_ws_root:
        return MartWorkspacePaths(root_dir=ws_path)
    return MartWorkspacePaths.from_entity(config.entity_code, base_dir=ws_path)


def _validate_existing_workspace_identity(
    paths: MartWorkspacePaths,
    config: EntityIsolationConfig,
    *,
    is_demo: bool,
) -> None:
    """Verify that any pre-existing audit log or DuckDB warehouse matches `entity_code` and `is_demo` mode."""
    expected_entity = config.entity_code.strip().upper()

    if paths.audit_log_path.exists():
        try:
            audit_payload = json.loads(paths.audit_log_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DwhConfigError(
                f"Existing pipeline_audit.json at {paths.audit_log_path} is unreadable: {exc}"
            ) from exc
        if isinstance(audit_payload, Mapping):
            existing_entity = str(audit_payload.get("entity_code", "")).strip().upper()
            if existing_entity and existing_entity != expected_entity:
                raise DwhConfigError(
                    f"Workspace at {paths.root_dir} belongs to entity {existing_entity!r}, "
                    f"cannot run entity {expected_entity!r}."
                )
            if "is_demo" in audit_payload and bool(audit_payload["is_demo"]) != bool(is_demo):
                raise DwhConfigError(
                    f"Workspace mode mismatch at {paths.root_dir}: existing is_demo={bool(audit_payload['is_demo'])}, "
                    f"requested is_demo={bool(is_demo)}."
                )
            if is_demo and "is_demo" not in audit_payload:
                raise DwhConfigError(
                    f"Demo run cannot overwrite existing non-demo workspace at {paths.root_dir}."
                )

    if paths.warehouse_db_path.exists():
        con = duckdb.connect(str(paths.warehouse_db_path), read_only=True)
        try:
            tables = {
                str(r[0]).lower(): str(r[0])
                for r in con.execute("SELECT table_name FROM information_schema.tables;").fetchall()
            }
            meta_entity: str | None = None
            meta_is_demo: bool | None = None
            if "_mart_metadata" in tables:
                meta_rows = dict(
                    con.execute("SELECT key, value FROM _mart_metadata;").fetchall()
                )
                if meta_rows.get("entity_code"):
                    meta_entity = str(meta_rows["entity_code"]).strip().upper()
                if meta_rows.get("is_demo") is not None:
                    meta_is_demo = str(meta_rows["is_demo"]).strip().lower() == "true"

            if meta_entity and meta_entity != expected_entity:
                raise DwhConfigError(
                    f"Existing warehouse.duckdb at {paths.warehouse_db_path} belongs to entity {meta_entity!r}, "
                    f"cannot run entity {expected_entity!r}."
                )
            if meta_is_demo is not None and meta_is_demo != bool(is_demo):
                raise DwhConfigError(
                    f"Existing warehouse.duckdb mode mismatch at {paths.warehouse_db_path}: "
                    f"existing is_demo={meta_is_demo}, requested is_demo={bool(is_demo)}."
                )

            has_existing_facts = False
            for fact_tbl in ("fact_pnl", "fact_inventory", "fact_price"):
                if fact_tbl in tables:
                    actual_tbl = tables[fact_tbl]
                    has_existing_facts = True
                    cols = {
                        str(r[0]).lower()
                        for r in con.execute(f'DESCRIBE "{actual_tbl}";').fetchall()
                    }
                    if "entity_code" in cols:
                        entities = [
                            str(r[0]).strip().upper()
                            for r in con.execute(
                                f'SELECT DISTINCT entity_code FROM "{actual_tbl}" WHERE entity_code IS NOT NULL;'
                            ).fetchall()
                        ]
                        for ent in entities:
                            if ent and ent != expected_entity:
                                raise DwhConfigError(
                                    f"Existing {actual_tbl} in {paths.warehouse_db_path} contains entity {ent!r}, "
                                    f"cannot run entity {expected_entity!r}."
                                )

            if is_demo and has_existing_facts and meta_is_demo is not True:
                raise DwhConfigError(
                    f"Demo run cannot overwrite existing production warehouse.duckdb at {paths.warehouse_db_path}."
                )
        finally:
            con.close()


def _promote_staged_workspace(staged_paths: MartWorkspacePaths, live_paths: MartWorkspacePaths) -> None:
    """Atomically promote all files from `staged_paths` into `live_paths` with full backup and rollback on error."""
    live_paths.ensure_dirs()
    backup_root = Path(tempfile.mkdtemp(prefix=".ws_bak_", dir=str(live_paths.root_dir)))
    subdirs = ("staging", "warehouse", "serving", "logs")
    backup_map: dict[Path, Path] = {}
    touched_paths: list[Path] = []
    succeeded = False
    try:
        # 1. Back up all existing files in live_paths before touching any live file
        for sub in subdirs:
            src_dir = getattr(live_paths, f"{sub}_dir")
            bak_dir = backup_root / sub
            bak_dir.mkdir(parents=True, exist_ok=True)
            for item in src_dir.iterdir():
                if item.is_file():
                    bak_file = bak_dir / item.name
                    shutil.copy2(item, bak_file)
                    backup_map[item] = bak_file

        # 2. Promote staged files into live_paths (recording only files modified during promotion)
        for sub in subdirs:
            stage_dir = getattr(staged_paths, f"{sub}_dir")
            live_dir = getattr(live_paths, f"{sub}_dir")
            staged_files = [p for p in stage_dir.iterdir() if p.is_file()]
            staged_names = {p.name for p in staged_files}
            for staged_file in staged_files:
                target_live = live_dir / staged_file.name
                touched_paths.append(target_live)
                os.replace(staged_file, target_live)
            for existing_file in list(live_dir.iterdir()):
                if existing_file.is_file() and existing_file.name not in staged_names:
                    touched_paths.append(existing_file)
                    existing_file.unlink(missing_ok=True)

        succeeded = True
    finally:
        if not succeeded:
            for target_live in reversed(touched_paths):
                bak_file = backup_map.get(target_live)
                if bak_file is not None and bak_file.exists():
                    shutil.copy2(bak_file, target_live)
                else:
                    target_live.unlink(missing_ok=True)
        shutil.rmtree(backup_root, ignore_errors=True)


def run_dwh_pipeline(
    input_dir: Path | str | None = None,
    entity_code: str = "PL",
    period_spec: str | None = "ALL",
    workspace_dir: Path | str | None = None,
    *,
    source_dir: Path | str | None = None,
    base_dir: Path | str | None = None,
    pnl_csv: Path | str | None = None,
    inventory_csv: Path | str | None = None,
    price_csv: Path | str | None = None,
    customer_master_csv: Path | str | None = None,
    product_master_csv: Path | str | None = None,
    account_mapping_path: Path | str | None = None,
    fx_rates_path: Path | str | None = None,
    config: EntityIsolationConfig | None = None,
    mapping_rules: Mapping[str, AccountMappingRule] | None = None,
    allow_unmapped_accounts: bool = False,
    is_demo: bool = False,
) -> PipelineRunResult:
    """Execute the end-to-end DWH pipeline atomically as a single unit: Extract -> Dimension -> Fact -> Serving & Audit."""
    started_at = datetime.now(timezone.utc).isoformat()

    effective_input = input_dir if input_dir is not None else source_dir
    effective_workspace = workspace_dir if workspace_dir is not None else base_dir
    has_explicit_files = any(
        p is not None
        for p in (pnl_csv, inventory_csv, price_csv, customer_master_csv, product_master_csv)
    )

    if is_demo and (effective_input is not None or has_explicit_files):
        raise DwhConfigError(
            "Cannot combine is_demo=True (--demo) with explicit input_dir or source file paths."
        )

    effective_is_demo = bool(is_demo or (effective_input is None and not has_explicit_files))
    default_sample_dir = Path(__file__).resolve().parents[2] / "sample_data" / "dwh_pl_sample"
    base_input_dir = Path(effective_input) if effective_input is not None else default_sample_dir

    # Resolve FX rates & EntityIsolationConfig without leaking demo FX rates into production runs
    if config is not None:
        effective_config = config
    else:
        resolved_fx_file = _resolve_source_file(
            fx_rates_path,
            base_input_dir,
            ("fx_rates.csv", "fx_rates.json", "master_fx_rates.csv"),
            required=False,
        )
        if resolved_fx_file is not None:
            loaded_fx = load_fx_rates(resolved_fx_file)
            effective_config = EntityIsolationConfig(
                entity_code=entity_code,
                fx_rates=loaded_fx,
                allow_default_fx_fallback=False,
            )
        elif effective_is_demo:
            effective_config = EntityIsolationConfig.for_demo(entity_code=entity_code)
        else:
            effective_config = EntityIsolationConfig(entity_code=entity_code)

    paths = _resolve_and_validate_workspace_paths(
        effective_config,
        effective_workspace,
        is_demo=effective_is_demo,
    )
    paths.ensure_dirs()
    _validate_existing_workspace_identity(paths, effective_config, is_demo=effective_is_demo)

    # Resolve account mapping rules
    if mapping_rules is not None:
        effective_rules = dict(mapping_rules)
    else:
        resolved_map_file = _resolve_source_file(
            account_mapping_path,
            base_input_dir,
            ("account_mapping.csv", "account_mapping.json", "master_account_mapping.csv"),
            required=False,
        )
        if resolved_map_file is not None:
            effective_rules = load_account_mapping(resolved_map_file)
        else:
            effective_rules = get_default_account_mapping()

    resolved_pnl = _resolve_source_file(
        pnl_csv,
        base_input_dir,
        ("raw_erp_pnl.csv", "erp_pnl.csv", "pnl.csv"),
        required=True,
    )
    resolved_inv = _resolve_source_file(
        inventory_csv,
        base_input_dir,
        ("raw_scm_inventory.csv", "scm_inventory.csv", "inventory.csv"),
        required=True,
    )
    resolved_price = _resolve_source_file(
        price_csv,
        base_input_dir,
        ("raw_market_price.csv", "market_price.csv", "price.csv"),
        required=True,
    )
    resolved_cust = _resolve_source_file(
        customer_master_csv,
        base_input_dir,
        ("master_customer.csv", "customer_master.csv", "customers.csv"),
        required=True,
    )
    resolved_prod = _resolve_source_file(
        product_master_csv,
        base_input_dir,
        ("master_product.csv", "product_master.csv", "products.csv"),
        required=True,
    )

    assert resolved_pnl and resolved_inv and resolved_price and resolved_cust and resolved_prod

    # Execute the entire run inside a staging bundle directory so partial exports never mix with live workspace
    stage_root = Path(tempfile.mkdtemp(prefix=".run_stage_", dir=str(paths.root_dir)))
    staged_paths = MartWorkspacePaths(root_dir=stage_root)
    staged_paths.ensure_dirs()

    con: duckdb.DuckDBPyConnection | None = None
    try:
        if paths.warehouse_db_path.exists():
            shutil.copy2(paths.warehouse_db_path, staged_paths.warehouse_db_path)

        con = duckdb.connect(str(staged_paths.warehouse_db_path))
        extractor = EntityExtractor(con=con, config=effective_config, paths=staged_paths)
        pnl_stats_raw = extractor.extract_source(
            "pnl",
            resolved_pnl,
            period_spec=period_spec,
            required_columns=("customer_code", "product_code", "sales_qty"),
        )
        inv_stats_raw = extractor.extract_source(
            "inventory",
            resolved_inv,
            period_spec=period_spec,
            required_columns=("warehouse_code", "product_code"),
        )
        price_stats_raw = extractor.extract_source(
            "price",
            resolved_price,
            period_spec=period_spec,
            required_columns=("channel_code", "product_code"),
        )

        # Remap staging_parquet_path in stats to the final live workspace path
        extraction_stats = {
            "pnl": ExtractionStats(
                **{
                    **asdict(pnl_stats_raw),
                    "staging_parquet_path": str(
                        (paths.staging_dir / Path(pnl_stats_raw.staging_parquet_path).name).resolve()
                    ),
                }
            ),
            "inventory": ExtractionStats(
                **{
                    **asdict(inv_stats_raw),
                    "staging_parquet_path": str(
                        (paths.staging_dir / Path(inv_stats_raw.staging_parquet_path).name).resolve()
                    ),
                }
            ),
            "price": ExtractionStats(
                **{
                    **asdict(price_stats_raw),
                    "staging_parquet_path": str(
                        (paths.staging_dir / Path(price_stats_raw.staging_parquet_path).name).resolve()
                    ),
                }
            ),
        }

        dim_builder = DimensionBuilder(con=con, paths=staged_paths)
        dim_stats = dim_builder.build_all_dimensions(
            customer_master=resolved_cust,
            product_master=resolved_prod,
            account_mapping=effective_rules,
            period_spec=period_spec,
        )

        fact_builder = FactBuilder(
            con=con,
            config=effective_config,
            paths=staged_paths,
            mapping_rules=effective_rules,
            allow_unmapped_accounts=allow_unmapped_accounts,
        )
        fact_summary = fact_builder.build_all(
            allow_unmapped_accounts=allow_unmapped_accounts
        )

        serving_loader = ServingLoader(
            con=con,
            config=effective_config,
            paths=staged_paths,
            mapping_rules=effective_rules,
        )
        serving_validation = serving_loader.create_ai_mart_views()
        staged_exported = serving_loader.export_serving_artifacts()

        completed_at = datetime.now(timezone.utc).isoformat()
        fingerprint = compute_mart_fingerprint(effective_config, effective_rules)

        # Persist workspace identity metadata inside warehouse.duckdb
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS _mart_metadata (
                key VARCHAR PRIMARY KEY,
                value VARCHAR NOT NULL
            );
            """
        )
        con.executemany(
            "INSERT OR REPLACE INTO _mart_metadata (key, value) VALUES (?, ?);",
            [
                ("entity_code", effective_config.entity_code),
                ("is_demo", "true" if effective_is_demo else "false"),
                ("reporting_currency", effective_config.reporting_currency),
                ("mart_fingerprint", fingerprint),
                ("updated_at", completed_at),
            ],
        )

        con.close()
        con = None

        # Map exported artifact paths from staged_paths to final live paths
        exported_artifacts: dict[str, str] = {}
        for key, staged_file_str in staged_exported.items():
            staged_file = Path(staged_file_str)
            rel_to_stage = staged_file.relative_to(staged_paths.root_dir.resolve())
            exported_artifacts[key] = str((paths.root_dir.resolve() / rel_to_stage).resolve())

        result = PipelineRunResult(
            entity_code=effective_config.entity_code,
            period_spec=str(period_spec or "ALL"),
            reporting_currency=effective_config.reporting_currency,
            mart_fingerprint=fingerprint,
            workspace_root=str(paths.root_dir.resolve()),
            warehouse_db_path=str(paths.warehouse_db_path.resolve()),
            extraction_stats=extraction_stats,
            dimension_stats=dim_stats,
            fact_summary=fact_summary,
            serving_validation=serving_validation,
            exported_artifacts=exported_artifacts,
            audit_log_path=str(paths.audit_log_path.resolve()),
            started_at=started_at,
            completed_at=completed_at,
            is_demo=effective_is_demo,
        )

        atomic_write_text(
            staged_paths.audit_log_path,
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        )

        # Atomically promote the complete staged workspace bundle into live `paths`
        _promote_staged_workspace(staged_paths, paths)
        return result
    finally:
        if con is not None:
            import contextlib

            with contextlib.suppress(duckdb.Error, OSError):
                con.close()
        shutil.rmtree(stage_root, ignore_errors=True)

