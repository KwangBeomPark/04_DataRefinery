"""DuckDB multi-schema manager, One Big Table (OBT) Mart View builder, and Parquet exporter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional
import duckdb

from src.config_loader import DwhSettings


@dataclass
class LoaderStats:
    """Summary of created mart views and exported Parquet files."""
    warehouse_db_path: str
    views_created: List[str]
    exported_parquet_files: Dict[str, str]
    total_obt_rows: int


class DwhLoader:
    """Manages DuckDB schemas (staging, core, mart), views, and Excel Power Query Parquet exports."""

    def __init__(self, settings: DwhSettings, con: Optional[duckdb.DuckDBPyConnection] = None) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self.db_path = self.settings.warehouse_db
        self.con = con or duckdb.connect(str(self.db_path))

    def setup_schemas(self) -> None:
        """Create staging, core, and mart schemas in the DuckDB database."""
        self.con.execute("CREATE SCHEMA IF NOT EXISTS staging;")
        self.con.execute("CREATE SCHEMA IF NOT EXISTS core;")
        self.con.execute("CREATE SCHEMA IF NOT EXISTS mart;")

    def create_indexes(self) -> None:
        """Create surrogate key indexes to accelerate OBT joins and ad-hoc BI queries."""
        self.con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_cust_pk ON core.dim_customer(customer_id);")
        self.con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_prod_pk ON core.dim_product(product_id);")
        self.con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_date_pk ON core.dim_date(date_id);")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_fact_cust_fk ON core.fact_pnl(customer_id);")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_fact_prod_fk ON core.fact_pnl(product_id);")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_fact_date_fk ON core.fact_pnl(date_id);")

    def create_mart_views(self) -> List[str]:
        """Create denormalized One Big Table (OBT) View mart.v_ai_pnl_summary."""
        self.setup_schemas()

        view_sql = """
        CREATE OR REPLACE VIEW mart.v_ai_pnl_summary AS
        SELECT
            f.fact_pnl_id,
            d.date_id,
            d.period,
            d.year,
            d.quarter,
            d.month,
            c.customer_id,
            c.bill_to_code,
            c.customer_name,
            c.channel_group,
            c.country_code,
            c.city,
            p.product_id,
            p.model_code,
            p.product_name,
            p.division,
            p.category,
            f.entity_code,
            f.currency_code,
            f.reporting_currency,
            f.fx_rate,
            f.sales_qty,
            -- Waterfall Metrics (Reporting Currency)
            f.gross_revenue_reporting,
            f.sales_deduction_reporting,
            f.net_revenue_reporting,
            f.cogs_reporting,
            f.gross_margin_reporting,
            f.logistics_cost_reporting,
            f.promotion_cost_reporting,
            f.warranty_cost_reporting,
            f.variable_cost_total_reporting,
            f.contribution_margin_reporting,
            f.fixed_sgna_reporting,
            f.operating_profit_reporting,
            -- Waterfall Metrics (Local Currency)
            f.gross_revenue_local,
            f.sales_deduction_local,
            f.net_revenue_local,
            f.cogs_local,
            f.gross_margin_local,
            f.logistics_cost_local,
            f.promotion_cost_local,
            f.warranty_cost_local,
            f.variable_cost_total_local,
            f.contribution_margin_local,
            f.fixed_sgna_local,
            f.operating_profit_local
        FROM core.fact_pnl f
        JOIN core.dim_customer c ON f.customer_id = c.customer_id
        JOIN core.dim_product p ON f.product_id = p.product_id
        JOIN core.dim_date d ON f.date_id = d.date_id;
        """
        self.con.execute(view_sql)
        return ["mart.v_ai_pnl_summary"]

    def export_parquet_for_power_query(self) -> Dict[str, str]:
        """Export core tables and final mart views to Parquet for Excel Power Query."""
        self.settings.mart_export_dir.mkdir(parents=True, exist_ok=True)
        exported: Dict[str, str] = {}

        targets = [
            ("mart.v_ai_pnl_summary", "v_ai_pnl_summary.parquet"),
            ("core.dim_customer", "dim_customer.parquet"),
            ("core.dim_product", "dim_product.parquet"),
            ("core.dim_date", "dim_date.parquet"),
            ("core.fact_pnl", "fact_pnl.parquet"),
        ]

        for source_rel, filename in targets:
            dest = self.settings.mart_export_dir / filename
            # Use as_posix() to prevent Windows backslash escaping errors
            self.con.execute(f"COPY (SELECT * FROM {source_rel}) TO '{dest.as_posix()}' (FORMAT PARQUET, COMPRESSION 'ZSTD');")
            exported[source_rel] = str(dest)

        # Generate AI metadata schema dictionary
        self._write_schema_dictionary()

        return exported

    def _write_schema_dictionary(self) -> None:
        """Write AI schema dictionary in JSON and Markdown for LLM prompt grounding."""
        info = self.con.execute("DESCRIBE SELECT * FROM mart.v_ai_pnl_summary;").fetchall()
        columns_meta = []
        for row in info:
            col_name, col_type, nullable = row[0], row[1], row[2]
            additivity = "ADDITIVE"
            if (
                col_name.endswith("_id")
                or col_type.upper().startswith("VARCHAR")
                or col_type.upper().startswith("TEXT")
                or col_name in ("period", "year", "quarter", "month", "entity_code", "currency_code", "reporting_currency", "fx_rate")
            ):
                additivity = "NON_ADDITIVE_DIMENSION"

            columns_meta.append({
                "name": col_name,
                "type": col_type,
                "nullable": nullable,
                "additivity": additivity,
            })

        dict_path = self.settings.mart_export_dir / "ai_schema_dictionary.json"
        with open(dict_path, "w", encoding="utf-8") as f:
            json.dump({"view": "mart.v_ai_pnl_summary", "columns": columns_meta}, f, indent=2, ensure_ascii=False)

        md_path = self.settings.mart_export_dir / "ai_schema_dictionary.md"
        md_lines = [
            "# AI & BI Schema Dictionary: `mart.v_ai_pnl_summary`",
            "",
            "Kimball Star Schema based One Big Table (OBT) flat mart view for AI (Text-to-SQL) and Excel Power Query.",
            "",
            "| Column Name | Data Type | Additivity Rule | Description |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for col in columns_meta:
            md_lines.append(f"| `{col['name']}` | `{col['type']}` | `{col['additivity']}` | Field from conformed dim/fact |")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")

    def run_all(self) -> LoaderStats:
        """Execute complete loading: create schemas, build OBT view, export Parquet."""
        self.create_indexes()
        views = self.create_mart_views()
        exports = self.export_parquet_for_power_query()
        obt_rows = self.con.execute("SELECT count(*) FROM mart.v_ai_pnl_summary;").fetchone()[0]

        return LoaderStats(
            warehouse_db_path=str(self.db_path),
            views_created=views,
            exported_parquet_files=exports,
            total_obt_rows=obt_rows,
        )
