"""Fact table builder for P&L (Long & Waterfall), Inventory, and Market Price marts."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

import duckdb

from src.atomic_write import atomic_write_text
from src.dataset_engine import make_numeric_sql_expr
from src.dwh.mart_config import (
    AccountMappingRule,
    DwhConfigError,
    EntityIsolationConfig,
    MartWorkspacePaths,
    get_default_account_mapping,
    normalize_number_format,
)

# Non-account base and descriptive columns in stg_pnl
_PNL_NON_ACCOUNT_COLUMNS: frozenset[str] = frozenset(
    {
        "period",
        "entity_code",
        "customer_code",
        "customer_name",
        "channel",
        "channel_code",
        "channel_name",
        "country",
        "region",
        "product_code",
        "product_name",
        "division",
        "category",
        "sub_category",
        "brand",
        "currency_code",
        "currency",
        "sales_qty",
        "qty",
        "unit",
        "uom",
    }
)

UNMAPPED_LOG_COLUMNS: tuple[str, ...] = (
    "logged_at",
    "period",
    "source_name",
    "dimension_name",
    "raw_code",
    "row_count",
    "impact_amount_local",
    "currency_code",
    "impact_amount_reporting",
    "impact_metric_basis",
)


@dataclass
class UnmappedCodeRecord:
    """Audit record for a raw dimension/account code that mapped to surrogate key -1 (UNKNOWN)."""

    logged_at: str
    period: str
    source_name: str
    dimension_name: str
    raw_code: str
    row_count: int
    impact_amount_local: float
    currency_code: str
    impact_amount_reporting: float
    impact_metric_basis: str = "SIGNED_PNL_AMOUNT"


@dataclass
class FactBuildSummary:
    """Summary of built fact tables, reconciliation checks, and isolated unmapped codes."""

    fact_pnl_long_rows: int
    fact_pnl_rows: int
    fact_inventory_rows: int
    fact_price_rows: int
    unmapped_records: list[UnmappedCodeRecord] = field(default_factory=list)
    reconciliation_passed: bool = True
    reconciliation_detail: str = ""


class FactBuilder:
    """Builds Fact_PnL_Long, Fact_PnL, Fact_Inventory, and Fact_Price from staging and dimensions."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        config: EntityIsolationConfig,
        paths: MartWorkspacePaths,
        mapping_rules: Mapping[str, AccountMappingRule] | None = None,
        *,
        allow_unmapped_accounts: bool = False,
    ) -> None:
        self.con = con
        self.config = config
        self.paths = paths
        self.allow_unmapped_accounts = bool(allow_unmapped_accounts)
        raw_rules = mapping_rules if mapping_rules is not None else get_default_account_mapping()
        self.mapping_rules: dict[str, AccountMappingRule] = {
            str(k).strip().upper(): v for k, v in raw_rules.items()
        }

    def _table_exists(self, table_name: str) -> bool:
        row = self.con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE LOWER(table_name) = LOWER(?)
            """,
            [table_name],
        ).fetchone()
        return bool(row and row[0] > 0)

    def _get_table_columns(self, table_name: str) -> list[str]:
        rows = self.con.execute(f'DESCRIBE "{table_name}"').fetchall()
        return [str(r[0]) for r in rows]

    def _resolve_column(
        self,
        table_name: str,
        candidates: tuple[str, ...],
        *,
        required: bool = True,
    ) -> str | None:
        existing = {col.lower(): col for col in self._get_table_columns(table_name)}
        for candidate in candidates:
            if candidate.lower() in existing:
                return existing[candidate.lower()]
        if required:
            raise DwhConfigError(
                f"Table {table_name!r} is missing required column among {candidates}. "
                f"Found columns: {list(existing.values())}"
            )
        return None

    def _validate_numeric_column(self, table_name: str, col_name: str) -> str:
        """Validate that all non-empty, non-dash values conform to strict format-specific numeric syntax.

        In 'polish'/'eu' mode:
          - Comma (',') is the only decimal separator.
          - Space (' ', '\\xa0', '\\u202f') or dot ('.') may be used as a 3-digit thousands grouping separator.
          - Dot-decimals like '12.34' (where dot is followed by != 3 digits) are rejected so they are never
            silently stripped into '1234'.
        In 'us'/'en' mode:
          - Dot ('.') is the only decimal separator.
          - Comma (',') may be used only as a 3-digit thousands grouping separator.
          - Comma-decimals like '12,34' are rejected so they are never silently stripped into '1234'.
        """
        fmt = normalize_number_format(self.config.number_format)
        parsed_expr = make_numeric_sql_expr(col_name, fmt)
        raw_trim = f'TRIM(CAST("{col_name}" AS VARCHAR))'
        sp = " \xa0\u202f"
        if fmt == "us":
            syntax_valid_sql = (
                f"("
                f"regexp_matches({raw_trim}, '^[+-]?[0-9]+(\\.[0-9]+)?$') "
                f"OR regexp_matches({raw_trim}, '^[+-]?[0-9]{{1,3}}(,[0-9]{{3}})+(\\.[0-9]+)?$')"
                f")"
            )
        else:
            syntax_valid_sql = (
                f"("
                f"regexp_matches({raw_trim}, '^[+-]?[0-9]+(,[0-9]+)?$') "
                f"OR regexp_matches({raw_trim}, '^[+-]?[0-9]{{1,3}}([{sp}][0-9]{{3}})+(,[0-9]+)?$') "
                f"OR regexp_matches({raw_trim}, '^[+-]?[0-9]{{1,3}}(\\.[0-9]{{3}})+(,[0-9]+)?$')"
                f")"
            )
        bad_row = self.con.execute(
            f"""
            SELECT "{col_name}"
            FROM "{table_name}"
            WHERE "{col_name}" IS NOT NULL
              AND {raw_trim} NOT IN ('', '-')
              AND (
                  NOT {syntax_valid_sql}
                  OR {parsed_expr} IS NULL
              )
            LIMIT 1
            """
        ).fetchone()
        if bad_row is not None:
            raise DwhConfigError(
                f"Numeric parsing failed in {table_name}.{col_name} for value {bad_row[0]!r} "
                f"(number_format={self.config.number_format!r})."
            )
        return f"""CAST(
            CASE
                WHEN "{col_name}" IS NULL OR {raw_trim} IN ('', '-') THEN 0.0
                ELSE COALESCE({parsed_expr}, 0.0)
            END AS DOUBLE
        )"""

    def build_fx_rate_table(self) -> int:
        """Build Fact_FxRate in DuckDB and validate all staging (period, currency_code) pairs."""
        self.con.execute(
            """
            CREATE OR REPLACE TABLE Fact_FxRate (
                period VARCHAR,
                currency_code VARCHAR,
                reporting_currency VARCHAR,
                fx_rate DOUBLE
            )
            """
        )

        pairs: set[tuple[str, str]] = set()
        for period_key, rate_map in self.config.fx_rates.items():
            if period_key != "DEFAULT":
                for curr in rate_map:
                    if curr in self.config.allowed_currencies:
                        pairs.add((period_key, curr))

        for stg_table in ("stg_pnl", "stg_inventory", "stg_price"):
            if not self._table_exists(stg_table):
                continue
            period_col = self._resolve_column(stg_table, ("period",))
            curr_col = self._resolve_column(stg_table, ("currency_code", "currency"))
            rows = self.con.execute(
                f"""
                SELECT DISTINCT
                    TRIM(CAST("{period_col}" AS VARCHAR)) AS period,
                    UPPER(TRIM(CAST("{curr_col}" AS VARCHAR))) AS currency_code
                FROM "{stg_table}"
                WHERE "{period_col}" IS NOT NULL
                """
            ).fetchall()
            for period_val, curr_val in rows:
                if not period_val:
                    continue
                self.config.get_fx_rate(period_val, curr_val)
                pairs.add((period_val, curr_val))

        records: list[tuple[str, str, str, float]] = []
        for period_val, curr_val in sorted(pairs):
            rate = self.config.get_fx_rate(period_val, curr_val)
            records.append((period_val, curr_val, self.config.reporting_currency, float(rate)))

        if records:
            self.con.executemany(
                "INSERT INTO Fact_FxRate VALUES (?, ?, ?, ?)",
                records,
            )
        return len(records)

    def _identify_pnl_account_columns(self) -> tuple[list[str], list[str]]:
        """Return (active_account_columns, ignored_subtotal_columns) from stg_pnl."""
        all_cols = self._get_table_columns("stg_pnl")
        subtotal_codes_in_dim: set[str] = set()
        if self._table_exists("Dim_Account"):
            dim_cols = {c.lower() for c in self._get_table_columns("Dim_Account")}
            if "is_subtotal" in dim_cols:
                rows = self.con.execute(
                    "SELECT UPPER(TRIM(account_code)) FROM Dim_Account WHERE is_subtotal = TRUE"
                ).fetchall()
                subtotal_codes_in_dim = {str(r[0]) for r in rows if r[0]}

        active_cols: list[str] = []
        subtotal_cols: list[str] = []

        for col in all_cols:
            col_clean = col.strip()
            col_lower = col_clean.lower()
            col_upper = col_clean.upper()

            if col_clean.startswith("_") or col_lower in _PNL_NON_ACCOUNT_COLUMNS:
                continue

            rule = self.mapping_rules.get(col_upper)
            is_subtotal = bool(
                (rule is not None and rule.is_subtotal)
                or col_upper in subtotal_codes_in_dim
                or (rule is not None and rule.statement_line == "SUBTOTAL_IGNORED")
            )
            if is_subtotal:
                subtotal_cols.append(col)
            else:
                active_cols.append(col)

        if not active_cols:
            raise DwhConfigError("No non-subtotal P&L account columns found in stg_pnl.")

        return active_cols, subtotal_cols

    def build_fact_pnl(
        self,
        *,
        allow_unmapped_accounts: bool | None = None,
    ) -> tuple[int, int, bool, str]:
        """Build Fact_PnL_Long and Fact_PnL with Wide-to-Long reconciliation and unmapped account guard."""
        if not self._table_exists("stg_pnl"):
            raise DwhConfigError("Staging table 'stg_pnl' does not exist.")

        effective_allow_unmapped = (
            self.allow_unmapped_accounts
            if allow_unmapped_accounts is None
            else bool(allow_unmapped_accounts)
        )

        period_col = self._resolve_column("stg_pnl", ("period",))
        entity_col = self._resolve_column("stg_pnl", ("entity_code",))
        customer_col = self._resolve_column("stg_pnl", ("customer_code",))
        product_col = self._resolve_column("stg_pnl", ("product_code",))
        currency_col = self._resolve_column("stg_pnl", ("currency_code", "currency"))
        qty_col = self._resolve_column("stg_pnl", ("sales_qty", "qty"))

        assert period_col and entity_col and customer_col and product_col and currency_col and qty_col

        qty_expr = self._validate_numeric_column("stg_pnl", qty_col)
        active_account_cols, subtotal_cols = self._identify_pnl_account_columns()

        for sub_col in subtotal_cols:
            self._validate_numeric_column("stg_pnl", sub_col)

        account_exprs: dict[str, str] = {}
        for acc_col in active_account_cols:
            account_exprs[acc_col] = self._validate_numeric_column("stg_pnl", acc_col)

        # Stage row-numbered P&L base table for exact source row tracking
        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE _Fact_PnL_Staged_Rows AS
            SELECT
                ROW_NUMBER() OVER () AS _stg_row_id,
                COALESCE(d.date_sk, -1) AS date_sk,
                TRIM(CAST(s."{period_col}" AS VARCHAR)) AS period,
                UPPER(TRIM(CAST(s."{entity_col}" AS VARCHAR))) AS entity_code,
                COALESCE(c.customer_sk, -1) AS customer_sk,
                COALESCE(
                    c.customer_code,
                    NULLIF(TRIM(CAST(s."{customer_col}" AS VARCHAR)), ''),
                    'UNKNOWN'
                ) AS customer_code,
                COALESCE(p.product_sk, -1) AS product_sk,
                COALESCE(
                    p.product_code,
                    NULLIF(UPPER(TRIM(CAST(s."{product_col}" AS VARCHAR))), ''),
                    'UNKNOWN'
                ) AS product_code,
                UPPER(TRIM(CAST(s."{currency_col}" AS VARCHAR))) AS currency_code,
                COALESCE(fx.reporting_currency, '{self.config.reporting_currency}') AS reporting_currency,
                COALESCE(fx.fx_rate, 1.0) AS fx_rate,
                ROUND({qty_expr}, 6) AS sales_qty,
                {", ".join(f'ROUND({account_exprs[c]}, 6) AS "{c}"' for c in active_account_cols)}
            FROM stg_pnl s
            LEFT JOIN Dim_Date d
                ON d.period = TRIM(CAST(s."{period_col}" AS VARCHAR)) AND d.date_sk != -1
            LEFT JOIN Dim_Customer c
                ON UPPER(TRIM(c.customer_code)) = UPPER(TRIM(CAST(s."{customer_col}" AS VARCHAR)))
               AND c.customer_sk != -1
            LEFT JOIN Dim_Product p
                ON UPPER(TRIM(p.product_code)) = UPPER(TRIM(CAST(s."{product_col}" AS VARCHAR)))
               AND p.product_sk != -1
            LEFT JOIN Fact_FxRate fx
                ON fx.period = TRIM(CAST(s."{period_col}" AS VARCHAR))
               AND fx.currency_code = UPPER(TRIM(CAST(s."{currency_col}" AS VARCHAR)))
            """
        )

        union_branches: list[str] = []
        for acc_col in active_account_cols:
            acc_code_literal = acc_col.strip().upper().replace("'", "''")
            union_branches.append(
                f"""
                SELECT
                    _stg_row_id,
                    date_sk,
                    period,
                    entity_code,
                    customer_sk,
                    customer_code,
                    product_sk,
                    product_code,
                    currency_code,
                    reporting_currency,
                    fx_rate,
                    '{acc_code_literal}' AS account_code,
                    "{acc_col}" AS raw_amount_local
                FROM _Fact_PnL_Staged_Rows
                """
            )

        unpivoted_sql = "\nUNION ALL\n".join(union_branches)

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE Fact_PnL_Long AS
            WITH unpivoted AS (
                {unpivoted_sql}
            )
            SELECT
                u.date_sk AS date_sk,
                u.period AS period,
                u.entity_code AS entity_code,
                u.customer_sk AS customer_sk,
                u.customer_code AS customer_code,
                u.product_sk AS product_sk,
                u.product_code AS product_code,
                COALESCE(a.account_sk, -1) AS account_sk,
                u.account_code AS account_code,
                COALESCE(a.statement_line, 'UNKNOWN') AS statement_line,
                COALESCE(a.waterfall_step, 'UNKNOWN') AS waterfall_step,
                COALESCE(a.cost_category, 'UNKNOWN') AS cost_category,
                COALESCE(a.sign_multiplier, 1) AS sign_multiplier,
                u.currency_code AS currency_code,
                u.reporting_currency AS reporting_currency,
                u.fx_rate AS fx_rate,
                ROUND(u.raw_amount_local, 6) AS raw_amount_local,
                ROUND(u.raw_amount_local * COALESCE(a.sign_multiplier, 1), 6) AS signed_amount_local,
                ROUND(u.raw_amount_local * u.fx_rate, 6) AS raw_amount_reporting,
                ROUND(
                    ROUND(u.raw_amount_local * COALESCE(a.sign_multiplier, 1), 6) * u.fx_rate,
                    6
                ) AS signed_amount_reporting
            FROM unpivoted u
            LEFT JOIN Dim_Account a
                ON UPPER(TRIM(a.account_code)) = UPPER(TRIM(u.account_code))
               AND a.account_sk != -1
            """
        )

        # 5. Wide-to-Long Reconciliation check
        wide_sum_expr = " + ".join(f"COALESCE(SUM({account_exprs[c]}), 0.0)" for c in active_account_cols)
        wide_total_row = self.con.execute(f"SELECT ROUND({wide_sum_expr}, 6) FROM stg_pnl").fetchone()
        long_total_row = self.con.execute(
            "SELECT ROUND(COALESCE(SUM(raw_amount_local), 0.0), 6) FROM Fact_PnL_Long"
        ).fetchone()

        wide_total = float(wide_total_row[0] if wide_total_row and wide_total_row[0] is not None else 0.0)
        long_total = float(long_total_row[0] if long_total_row and long_total_row[0] is not None else 0.0)
        diff = abs(wide_total - long_total)
        reconciliation_passed = diff <= 1e-4
        reconciliation_detail = (
            f"Wide-to-Long reconciliation: stg_pnl_sum={wide_total:.6f}, "
            f"fact_pnl_long_sum={long_total:.6f}, diff={diff:.6f}"
        )
        if not reconciliation_passed:
            raise DwhConfigError(f"Wide-to-Long reconciliation failed! {reconciliation_detail}")

        # 6. Build Fact_PnL (Waterfall Summary Fact Table) preserving customer_code and product_code
        self.con.execute(
            """
            CREATE OR REPLACE TABLE Fact_PnL AS
            WITH qty_by_grain AS (
                SELECT
                    date_sk,
                    period,
                    entity_code,
                    customer_sk,
                    customer_code,
                    product_sk,
                    product_code,
                    currency_code,
                    reporting_currency,
                    ROUND(SUM(sales_qty), 6) AS sales_qty
                FROM _Fact_PnL_Staged_Rows
                GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
            ),
            acct_by_grain AS (
                SELECT
                    date_sk,
                    period,
                    entity_code,
                    customer_sk,
                    customer_code,
                    product_sk,
                    product_code,
                    currency_code,
                    reporting_currency,
                    ROUND(SUM(CASE WHEN statement_line = 'GROSS_REVENUE' THEN signed_amount_local ELSE 0.0 END), 6)
                        AS gross_revenue_local,
                    ROUND(SUM(CASE WHEN statement_line = 'SALES_DEDUCTION' THEN signed_amount_local ELSE 0.0 END), 6)
                        AS sales_deduction_local,
                    ROUND(SUM(CASE WHEN statement_line = 'COGS' THEN signed_amount_local ELSE 0.0 END), 6)
                        AS cogs_local,
                    ROUND(SUM(CASE WHEN statement_line = 'LOGISTICS_COST' THEN signed_amount_local ELSE 0.0 END), 6)
                        AS logistics_cost_local,
                    ROUND(SUM(CASE WHEN statement_line = 'PROMOTION_COST' THEN signed_amount_local ELSE 0.0 END), 6)
                        AS promotion_cost_local,
                    ROUND(SUM(CASE WHEN statement_line = 'WARRANTY_COST' THEN signed_amount_local ELSE 0.0 END), 6)
                        AS warranty_cost_local,
                    ROUND(SUM(CASE WHEN statement_line = 'FIXED_SGNA' THEN signed_amount_local ELSE 0.0 END), 6)
                        AS fixed_sgna_cost_local,
                    ROUND(SUM(CASE WHEN statement_line = 'GROSS_REVENUE' THEN signed_amount_reporting ELSE 0.0 END), 6)
                        AS gross_revenue_reporting,
                    ROUND(SUM(CASE WHEN statement_line = 'SALES_DEDUCTION' THEN signed_amount_reporting ELSE 0.0 END), 6)
                        AS sales_deduction_reporting,
                    ROUND(SUM(CASE WHEN statement_line = 'COGS' THEN signed_amount_reporting ELSE 0.0 END), 6)
                        AS cogs_reporting,
                    ROUND(SUM(CASE WHEN statement_line = 'LOGISTICS_COST' THEN signed_amount_reporting ELSE 0.0 END), 6)
                        AS logistics_cost_reporting,
                    ROUND(SUM(CASE WHEN statement_line = 'PROMOTION_COST' THEN signed_amount_reporting ELSE 0.0 END), 6)
                        AS promotion_cost_reporting,
                    ROUND(SUM(CASE WHEN statement_line = 'WARRANTY_COST' THEN signed_amount_reporting ELSE 0.0 END), 6)
                        AS warranty_cost_reporting,
                    ROUND(SUM(CASE WHEN statement_line = 'FIXED_SGNA' THEN signed_amount_reporting ELSE 0.0 END), 6)
                        AS fixed_sgna_cost_reporting
                FROM Fact_PnL_Long
                GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
            ),
            combined AS (
                SELECT
                    a.date_sk,
                    a.period,
                    a.entity_code,
                    a.customer_sk,
                    a.customer_code,
                    a.product_sk,
                    a.product_code,
                    a.currency_code,
                    a.reporting_currency,
                    COALESCE(q.sales_qty, 0.0) AS sales_qty,
                    a.gross_revenue_local,
                    a.sales_deduction_local,
                    ROUND(a.gross_revenue_local + a.sales_deduction_local, 6) AS net_revenue_local,
                    a.cogs_local,
                    ROUND(a.gross_revenue_local + a.sales_deduction_local + a.cogs_local, 6) AS gross_margin_local,
                    a.logistics_cost_local,
                    a.promotion_cost_local,
                    a.warranty_cost_local,
                    ROUND(a.logistics_cost_local + a.promotion_cost_local + a.warranty_cost_local, 6)
                        AS variable_cost_total_local,
                    ROUND(
                        a.gross_revenue_local + a.sales_deduction_local + a.cogs_local
                        + a.logistics_cost_local + a.promotion_cost_local + a.warranty_cost_local,
                        6
                    ) AS contribution_margin_local,
                    a.fixed_sgna_cost_local,
                    ROUND(
                        a.gross_revenue_local + a.sales_deduction_local + a.cogs_local
                        + a.logistics_cost_local + a.promotion_cost_local + a.warranty_cost_local
                        + a.fixed_sgna_cost_local,
                        6
                    ) AS operating_profit_local,
                    a.gross_revenue_reporting,
                    a.sales_deduction_reporting,
                    ROUND(a.gross_revenue_reporting + a.sales_deduction_reporting, 6) AS net_revenue_reporting,
                    a.cogs_reporting,
                    ROUND(a.gross_revenue_reporting + a.sales_deduction_reporting + a.cogs_reporting, 6)
                        AS gross_margin_reporting,
                    a.logistics_cost_reporting,
                    a.promotion_cost_reporting,
                    a.warranty_cost_reporting,
                    ROUND(a.logistics_cost_reporting + a.promotion_cost_reporting + a.warranty_cost_reporting, 6)
                        AS variable_cost_total_reporting,
                    ROUND(
                        a.gross_revenue_reporting + a.sales_deduction_reporting + a.cogs_reporting
                        + a.logistics_cost_reporting + a.promotion_cost_reporting + a.warranty_cost_reporting,
                        6
                    ) AS contribution_margin_reporting,
                    a.fixed_sgna_cost_reporting,
                    ROUND(
                        a.gross_revenue_reporting + a.sales_deduction_reporting + a.cogs_reporting
                        + a.logistics_cost_reporting + a.promotion_cost_reporting + a.warranty_cost_reporting
                        + a.fixed_sgna_cost_reporting,
                        6
                    ) AS operating_profit_reporting
                FROM acct_by_grain a
                LEFT JOIN qty_by_grain q
                    ON a.date_sk = q.date_sk
                   AND a.period = q.period
                   AND a.entity_code = q.entity_code
                   AND a.customer_sk = q.customer_sk
                   AND a.customer_code = q.customer_code
                   AND a.product_sk = q.product_sk
                   AND a.product_code = q.product_code
                   AND a.currency_code = q.currency_code
                   AND a.reporting_currency = q.reporting_currency
            )
            SELECT * FROM combined
            ORDER BY period, entity_code, customer_sk, customer_code, product_sk, product_code, currency_code
            """
        )

        # Check for unmapped P&L account columns with non-zero impact
        unmapped_acct_rows = self.con.execute(
            """
            SELECT account_code, ROUND(SUM(ABS(raw_amount_local)), 6) AS abs_impact
            FROM Fact_PnL_Long
            WHERE account_sk = -1
            GROUP BY account_code
            HAVING SUM(ABS(raw_amount_local)) > 0
            ORDER BY account_code
            """
        ).fetchall()
        if unmapped_acct_rows and not effective_allow_unmapped:
            self.collect_and_log_unmapped_codes()
            details = ", ".join(f"{r[0]} (abs_amount={r[1]:.6f})" for r in unmapped_acct_rows)
            raise DwhConfigError(
                f"Unmapped P&L account column(s) with non-zero amount detected: {details}. "
                "Update account_mapping rules before building P&L waterfall marts."
            )

        # Signed waterfall reconciliation between Fact_PnL_Long (mapped accounts) and Fact_PnL operating_profit
        signed_recon_row = self.con.execute(
            """
            SELECT
                (SELECT ROUND(COALESCE(SUM(signed_amount_local), 0.0), 6) FROM Fact_PnL_Long WHERE account_sk != -1),
                (SELECT ROUND(COALESCE(SUM(operating_profit_local), 0.0), 6) FROM Fact_PnL),
                (SELECT ROUND(COALESCE(SUM(signed_amount_reporting), 0.0), 6) FROM Fact_PnL_Long WHERE account_sk != -1),
                (SELECT ROUND(COALESCE(SUM(operating_profit_reporting), 0.0), 6) FROM Fact_PnL)
            """
        ).fetchone()
        long_signed_local = float(signed_recon_row[0]) if signed_recon_row else 0.0
        pnl_op_local = float(signed_recon_row[1]) if signed_recon_row else 0.0
        long_signed_rep = float(signed_recon_row[2]) if signed_recon_row else 0.0
        pnl_op_rep = float(signed_recon_row[3]) if signed_recon_row else 0.0
        signed_diff_local = abs(long_signed_local - pnl_op_local)
        signed_diff_rep = abs(long_signed_rep - pnl_op_rep)
        if signed_diff_local > 1e-4 or signed_diff_rep > 1e-4:
            raise DwhConfigError(
                f"Signed P&L waterfall reconciliation failed: "
                f"long_signed_local={long_signed_local:.6f} vs pnl_op_local={pnl_op_local:.6f}, "
                f"long_signed_reporting={long_signed_rep:.6f} vs pnl_op_reporting={pnl_op_rep:.6f}"
            )

        long_rows = int(self.con.execute("SELECT COUNT(*) FROM Fact_PnL_Long").fetchone()[0])
        pnl_rows = int(self.con.execute("SELECT COUNT(*) FROM Fact_PnL").fetchone()[0])
        return long_rows, pnl_rows, reconciliation_passed, reconciliation_detail

    def build_fact_inventory(self) -> int:
        """Build Fact_Inventory from stg_inventory and dimension tables."""
        if not self._table_exists("stg_inventory"):
            raise DwhConfigError("Staging table 'stg_inventory' does not exist.")

        period_col = self._resolve_column("stg_inventory", ("period",))
        entity_col = self._resolve_column("stg_inventory", ("entity_code",))
        warehouse_col = self._resolve_column("stg_inventory", ("warehouse_code", "warehouse"))
        product_col = self._resolve_column("stg_inventory", ("product_code",))
        currency_col = self._resolve_column("stg_inventory", ("currency_code", "currency"))

        ending_qty_col = self._resolve_column(
            "stg_inventory", ("ending_qty", "inventory_qty", "qty", "stock_qty")
        )
        ending_val_col = self._resolve_column(
            "stg_inventory",
            (
                "ending_inventory_val_local",
                "ending_inventory_val",
                "inventory_val_local",
                "inventory_val",
                "ending_val",
            ),
        )
        dio_col = self._resolve_column("stg_inventory", ("dio_days", "dio"))
        wh_cost_col = self._resolve_column(
            "stg_inventory",
            (
                "warehouse_logistics_cost_local",
                "warehouse_logistics_cost",
                "logistics_cost_local",
                "logistics_cost",
                "wh_logistics_cost",
            ),
        )

        assert (
            period_col
            and entity_col
            and warehouse_col
            and product_col
            and currency_col
            and ending_qty_col
            and ending_val_col
            and dio_col
            and wh_cost_col
        )

        qty_expr = self._validate_numeric_column("stg_inventory", ending_qty_col)
        val_expr = self._validate_numeric_column("stg_inventory", ending_val_col)
        dio_expr = self._validate_numeric_column("stg_inventory", dio_col)
        cost_expr = self._validate_numeric_column("stg_inventory", wh_cost_col)

        dim_wh_col = self._resolve_column("Dim_Warehouse", ("warehouse_code", "warehouse"))
        assert dim_wh_col

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE _Fact_Inventory_Staged AS
            SELECT
                COALESCE(d.date_sk, -1) AS date_sk,
                TRIM(CAST(s."{period_col}" AS VARCHAR)) AS period,
                UPPER(TRIM(CAST(s."{entity_col}" AS VARCHAR))) AS entity_code,
                COALESCE(w.warehouse_sk, -1) AS warehouse_sk,
                COALESCE(
                    w."{dim_wh_col}",
                    NULLIF(UPPER(TRIM(CAST(s."{warehouse_col}" AS VARCHAR))), ''),
                    'UNKNOWN'
                ) AS warehouse_code,
                COALESCE(p.product_sk, -1) AS product_sk,
                COALESCE(
                    p.product_code,
                    NULLIF(UPPER(TRIM(CAST(s."{product_col}" AS VARCHAR))), ''),
                    'UNKNOWN'
                ) AS product_code,
                UPPER(TRIM(CAST(s."{currency_col}" AS VARCHAR))) AS currency_code,
                COALESCE(fx.reporting_currency, '{self.config.reporting_currency}') AS reporting_currency,
                ROUND({qty_expr}, 6) AS ending_qty,
                ROUND({val_expr}, 6) AS ending_inventory_val_local,
                ROUND({val_expr} * COALESCE(fx.fx_rate, 1.0), 6) AS ending_inventory_val_reporting,
                ROUND({dio_expr}, 6) AS dio_days,
                ROUND({cost_expr}, 6) AS warehouse_logistics_cost_local,
                ROUND({cost_expr} * COALESCE(fx.fx_rate, 1.0), 6) AS warehouse_logistics_cost_reporting
            FROM stg_inventory s
            LEFT JOIN Dim_Date d
                ON d.period = TRIM(CAST(s."{period_col}" AS VARCHAR)) AND d.date_sk != -1
            LEFT JOIN Dim_Warehouse w
                ON UPPER(TRIM(w."{dim_wh_col}")) = UPPER(TRIM(CAST(s."{warehouse_col}" AS VARCHAR)))
               AND w.warehouse_sk != -1
            LEFT JOIN Dim_Product p
                ON UPPER(TRIM(p.product_code)) = UPPER(TRIM(CAST(s."{product_col}" AS VARCHAR)))
               AND p.product_sk != -1
            LEFT JOIN Fact_FxRate fx
                ON fx.period = TRIM(CAST(s."{period_col}" AS VARCHAR))
               AND fx.currency_code = UPPER(TRIM(CAST(s."{currency_col}" AS VARCHAR)))
            """
        )

        self.con.execute(
            """
            CREATE OR REPLACE TABLE Fact_Inventory AS
            SELECT
                date_sk,
                period,
                entity_code,
                warehouse_sk,
                product_sk,
                product_code,
                currency_code,
                reporting_currency,
                ROUND(SUM(ending_qty), 6) AS ending_qty,
                ROUND(SUM(ending_inventory_val_local), 6) AS ending_inventory_val_local,
                ROUND(SUM(ending_inventory_val_reporting), 6) AS ending_inventory_val_reporting,
                ROUND(
                    CASE
                        WHEN SUM(ending_inventory_val_reporting) > 0
                        THEN SUM(dio_days * ending_inventory_val_reporting) / SUM(ending_inventory_val_reporting)
                        ELSE AVG(dio_days)
                    END,
                    6
                ) AS dio_days,
                ROUND(SUM(warehouse_logistics_cost_local), 6) AS warehouse_logistics_cost_local,
                ROUND(SUM(warehouse_logistics_cost_reporting), 6) AS warehouse_logistics_cost_reporting
            FROM _Fact_Inventory_Staged
            GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
            ORDER BY period, entity_code, warehouse_sk, product_sk, product_code, currency_code
            """
        )

        return int(self.con.execute("SELECT COUNT(*) FROM Fact_Inventory").fetchone()[0])

    def build_fact_price(self) -> int:
        """Build Fact_Price from stg_price and dimension tables."""
        if not self._table_exists("stg_price"):
            raise DwhConfigError("Staging table 'stg_price' does not exist.")

        period_col = self._resolve_column("stg_price", ("period",))
        entity_col = self._resolve_column("stg_price", ("entity_code",))
        channel_col = self._resolve_column("stg_price", ("channel_code", "channel", "sales_channel"))
        product_col = self._resolve_column("stg_price", ("product_code",))
        currency_col = self._resolve_column("stg_price", ("currency_code", "currency"))

        our_price_col = self._resolve_column(
            "stg_price", ("our_price_local", "our_price", "company_price")
        )
        comp_price_col = self._resolve_column(
            "stg_price", ("competitor_price_local", "competitor_price", "comp_price")
        )
        bench_price_col = self._resolve_column(
            "stg_price",
            ("market_benchmark_price_local", "market_benchmark_price", "benchmark_price", "market_price"),
        )

        assert (
            period_col
            and entity_col
            and channel_col
            and product_col
            and currency_col
            and our_price_col
            and comp_price_col
            and bench_price_col
        )

        our_expr = self._validate_numeric_column("stg_price", our_price_col)
        comp_expr = self._validate_numeric_column("stg_price", comp_price_col)
        bench_expr = self._validate_numeric_column("stg_price", bench_price_col)

        dim_ch_col = self._resolve_column("Dim_Channel", ("channel_code", "channel", "channel_name"))
        assert dim_ch_col

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE _Fact_Price_Staged AS
            SELECT
                COALESCE(d.date_sk, -1) AS date_sk,
                TRIM(CAST(s."{period_col}" AS VARCHAR)) AS period,
                UPPER(TRIM(CAST(s."{entity_col}" AS VARCHAR))) AS entity_code,
                COALESCE(ch.channel_sk, -1) AS channel_sk,
                COALESCE(
                    ch."{dim_ch_col}",
                    NULLIF(UPPER(TRIM(CAST(s."{channel_col}" AS VARCHAR))), ''),
                    'UNKNOWN'
                ) AS channel_code,
                COALESCE(p.product_sk, -1) AS product_sk,
                COALESCE(
                    p.product_code,
                    NULLIF(UPPER(TRIM(CAST(s."{product_col}" AS VARCHAR))), ''),
                    'UNKNOWN'
                ) AS product_code,
                UPPER(TRIM(CAST(s."{currency_col}" AS VARCHAR))) AS currency_code,
                COALESCE(fx.reporting_currency, '{self.config.reporting_currency}') AS reporting_currency,
                ROUND({our_expr}, 6) AS our_price_local,
                ROUND({our_expr} * COALESCE(fx.fx_rate, 1.0), 6) AS our_price_reporting,
                ROUND({comp_expr}, 6) AS competitor_price_local,
                ROUND({comp_expr} * COALESCE(fx.fx_rate, 1.0), 6) AS competitor_price_reporting,
                ROUND({bench_expr}, 6) AS market_benchmark_price_local,
                ROUND({bench_expr} * COALESCE(fx.fx_rate, 1.0), 6) AS market_benchmark_price_reporting
            FROM stg_price s
            LEFT JOIN Dim_Date d
                ON d.period = TRIM(CAST(s."{period_col}" AS VARCHAR)) AND d.date_sk != -1
            LEFT JOIN Dim_Channel ch
                ON UPPER(TRIM(ch."{dim_ch_col}")) = UPPER(TRIM(CAST(s."{channel_col}" AS VARCHAR)))
               AND ch.channel_sk != -1
            LEFT JOIN Dim_Product p
                ON UPPER(TRIM(p.product_code)) = UPPER(TRIM(CAST(s."{product_col}" AS VARCHAR)))
               AND p.product_sk != -1
            LEFT JOIN Fact_FxRate fx
                ON fx.period = TRIM(CAST(s."{period_col}" AS VARCHAR))
               AND fx.currency_code = UPPER(TRIM(CAST(s."{currency_col}" AS VARCHAR)))
            """
        )

        self.con.execute(
            """
            CREATE OR REPLACE TABLE Fact_Price AS
            SELECT
                date_sk,
                period,
                entity_code,
                channel_sk,
                product_sk,
                product_code,
                currency_code,
                reporting_currency,
                ROUND(AVG(our_price_local), 6) AS our_price_local,
                ROUND(AVG(our_price_reporting), 6) AS our_price_reporting,
                ROUND(AVG(competitor_price_local), 6) AS competitor_price_local,
                ROUND(AVG(competitor_price_reporting), 6) AS competitor_price_reporting,
                ROUND(AVG(market_benchmark_price_local), 6) AS market_benchmark_price_local,
                ROUND(AVG(market_benchmark_price_reporting), 6) AS market_benchmark_price_reporting
            FROM _Fact_Price_Staged
            GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
            ORDER BY period, entity_code, channel_sk, product_sk, product_code, currency_code
            """
        )

        return int(self.con.execute("SELECT COUNT(*) FROM Fact_Price").fetchone()[0])

    def collect_and_log_unmapped_codes(self) -> list[UnmappedCodeRecord]:
        """Collect all unmapped dimension codes (sk = -1) across P&L, Inventory, and Price and write CSV."""
        now_iso = datetime.now(timezone.utc).isoformat()
        records: list[UnmappedCodeRecord] = []

        # 1a. P&L Customer & Product unmapped codes: source row count from _Fact_PnL_Staged_Rows
        # and impact_amount from Fact_PnL net_revenue (with fallback to ABS(operating_profit))
        if self._table_exists("_Fact_PnL_Staged_Rows") and self._table_exists("Fact_PnL"):
            for dim_name, sk_col, code_col in (
                ("Dim_Customer", "customer_sk", "customer_code"),
                ("Dim_Product", "product_sk", "product_code"),
            ):
                rows = self.con.execute(
                    f"""
                    WITH stg_counts AS (
                        SELECT
                            period,
                            COALESCE(NULLIF(TRIM({code_col}), ''), 'UNKNOWN') AS raw_code,
                            currency_code,
                            COUNT(*) AS row_count
                        FROM _Fact_PnL_Staged_Rows
                        WHERE {sk_col} = -1
                        GROUP BY 1, 2, 3
                    ),
                    pnl_impact AS (
                        SELECT
                            period,
                            COALESCE(NULLIF(TRIM({code_col}), ''), 'UNKNOWN') AS raw_code,
                            currency_code,
                            ROUND(
                                CASE
                                    WHEN COALESCE(SUM(net_revenue_local), 0.0) != 0.0
                                    THEN SUM(net_revenue_local)
                                    ELSE COALESCE(SUM(ABS(operating_profit_local)), 0.0)
                                END,
                                6
                            ) AS impact_local,
                            ROUND(
                                CASE
                                    WHEN COALESCE(SUM(net_revenue_reporting), 0.0) != 0.0
                                    THEN SUM(net_revenue_reporting)
                                    ELSE COALESCE(SUM(ABS(operating_profit_reporting)), 0.0)
                                END,
                                6
                            ) AS impact_reporting
                        FROM Fact_PnL
                        WHERE {sk_col} = -1
                        GROUP BY 1, 2, 3
                    )
                    SELECT
                        c.period,
                        c.raw_code,
                        c.currency_code,
                        c.row_count,
                        COALESCE(i.impact_local, 0.0) AS impact_local,
                        COALESCE(i.impact_reporting, 0.0) AS impact_reporting
                    FROM stg_counts c
                    LEFT JOIN pnl_impact i
                        ON c.period = i.period
                       AND c.raw_code = i.raw_code
                       AND c.currency_code = i.currency_code
                    ORDER BY 1, 2, 3
                    """
                ).fetchall()
                for period_val, raw_code, curr_code, cnt, imp_local, imp_rep in rows:
                    records.append(
                        UnmappedCodeRecord(
                            logged_at=now_iso,
                            period=str(period_val),
                            source_name="pnl",
                            dimension_name=dim_name,
                            raw_code=str(raw_code),
                            row_count=int(cnt),
                            impact_amount_local=float(imp_local),
                            currency_code=str(curr_code),
                            impact_amount_reporting=float(imp_rep),
                            impact_metric_basis="NET_REVENUE_OR_ABS_OP_PROFIT",
                        )
                    )
        elif self._table_exists("Fact_PnL_Long"):
            for dim_name, sk_col, code_col in (
                ("Dim_Customer", "customer_sk", "customer_code"),
                ("Dim_Product", "product_sk", "product_code"),
            ):
                rows = self.con.execute(
                    f"""
                    SELECT
                        period,
                        COALESCE(NULLIF(TRIM({code_col}), ''), 'UNKNOWN') AS raw_code,
                        currency_code,
                        COUNT(DISTINCT customer_code || '|' || product_code) AS row_count,
                        ROUND(COALESCE(SUM(ABS(signed_amount_local)), 0.0), 6) AS impact_local,
                        ROUND(COALESCE(SUM(ABS(signed_amount_reporting)), 0.0), 6) AS impact_reporting
                    FROM Fact_PnL_Long
                    WHERE {sk_col} = -1
                    GROUP BY 1, 2, 3
                    ORDER BY 1, 2, 3
                    """
                ).fetchall()
                for period_val, raw_code, curr_code, cnt, imp_local, imp_rep in rows:
                    records.append(
                        UnmappedCodeRecord(
                            logged_at=now_iso,
                            period=str(period_val),
                            source_name="pnl",
                            dimension_name=dim_name,
                            raw_code=str(raw_code),
                            row_count=int(cnt),
                            impact_amount_local=float(imp_local),
                            currency_code=str(curr_code),
                            impact_amount_reporting=float(imp_rep),
                            impact_metric_basis="ABS_SIGNED_PNL_LONG_AMOUNT",
                        )
                    )

        # 1b. P&L Account unmapped codes (account_sk = -1)
        if self._table_exists("Fact_PnL_Long"):
            rows = self.con.execute(
                """
                SELECT
                    period,
                    COALESCE(NULLIF(TRIM(account_code), ''), 'UNKNOWN') AS raw_code,
                    currency_code,
                    COUNT(*) AS row_count,
                    ROUND(COALESCE(SUM(raw_amount_local), 0.0), 6) AS impact_local,
                    ROUND(COALESCE(SUM(raw_amount_reporting), 0.0), 6) AS impact_reporting
                FROM Fact_PnL_Long
                WHERE account_sk = -1
                GROUP BY 1, 2, 3
                ORDER BY 1, 2, 3
                """
            ).fetchall()
            for period_val, raw_code, curr_code, cnt, imp_local, imp_rep in rows:
                records.append(
                    UnmappedCodeRecord(
                        logged_at=now_iso,
                        period=str(period_val),
                        source_name="pnl",
                        dimension_name="Dim_Account",
                        raw_code=str(raw_code),
                        row_count=int(cnt),
                        impact_amount_local=float(imp_local),
                        currency_code=str(curr_code),
                        impact_amount_reporting=float(imp_rep),
                        impact_metric_basis="RAW_ACCOUNT_AMOUNT",
                    )
                )

        # 2. Inventory: Dim_Product (and Dim_Warehouse if unmapped)
        if self._table_exists("_Fact_Inventory_Staged"):
            for dim_name, sk_col, code_col in (
                ("Dim_Product", "product_sk", "product_code"),
                ("Dim_Warehouse", "warehouse_sk", "warehouse_code"),
            ):
                rows = self.con.execute(
                    f"""
                    SELECT
                        period,
                        COALESCE(NULLIF(TRIM({code_col}), ''), 'UNKNOWN') AS raw_code,
                        currency_code,
                        COUNT(*) AS row_count,
                        ROUND(COALESCE(SUM(ending_inventory_val_local), 0.0), 6) AS impact_local,
                        ROUND(COALESCE(SUM(ending_inventory_val_reporting), 0.0), 6) AS impact_reporting
                    FROM _Fact_Inventory_Staged
                    WHERE {sk_col} = -1
                    GROUP BY 1, 2, 3
                    ORDER BY 1, 2, 3
                    """
                ).fetchall()
                for period_val, raw_code, curr_code, cnt, imp_local, imp_rep in rows:
                    records.append(
                        UnmappedCodeRecord(
                            logged_at=now_iso,
                            period=str(period_val),
                            source_name="inventory",
                            dimension_name=dim_name,
                            raw_code=str(raw_code),
                            row_count=int(cnt),
                            impact_amount_local=float(imp_local),
                            currency_code=str(curr_code),
                            impact_amount_reporting=float(imp_rep),
                            impact_metric_basis="ENDING_INVENTORY_VALUE",
                        )
                    )

        # 3. Price: Dim_Product (and Dim_Channel if unmapped) — use AVG unit price (never SUM unit prices)
        if self._table_exists("_Fact_Price_Staged"):
            for dim_name, sk_col, code_col in (
                ("Dim_Product", "product_sk", "product_code"),
                ("Dim_Channel", "channel_sk", "channel_code"),
            ):
                rows = self.con.execute(
                    f"""
                    SELECT
                        period,
                        COALESCE(NULLIF(TRIM({code_col}), ''), 'UNKNOWN') AS raw_code,
                        currency_code,
                        COUNT(*) AS row_count,
                        ROUND(COALESCE(AVG(our_price_local), 0.0), 6) AS impact_local,
                        ROUND(COALESCE(AVG(our_price_reporting), 0.0), 6) AS impact_reporting
                    FROM _Fact_Price_Staged
                    WHERE {sk_col} = -1
                    GROUP BY 1, 2, 3
                    ORDER BY 1, 2, 3
                    """
                ).fetchall()
                for period_val, raw_code, curr_code, cnt, imp_local, imp_rep in rows:
                    records.append(
                        UnmappedCodeRecord(
                            logged_at=now_iso,
                            period=str(period_val),
                            source_name="price",
                            dimension_name=dim_name,
                            raw_code=str(raw_code),
                            row_count=int(cnt),
                            impact_amount_local=float(imp_local),
                            currency_code=str(curr_code),
                            impact_amount_reporting=float(imp_rep),
                            impact_metric_basis="AVG_OUR_UNIT_PRICE_NON_ADDITIVE",
                        )
                    )

        # Write logs/unmapped_codes.csv atomically
        self.paths.ensure_dirs()
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(UNMAPPED_LOG_COLUMNS)
        for rec in records:
            writer.writerow(
                [
                    rec.logged_at,
                    rec.period,
                    rec.source_name,
                    rec.dimension_name,
                    rec.raw_code,
                    rec.row_count,
                    f"{rec.impact_amount_local:.6f}",
                    rec.currency_code,
                    f"{rec.impact_amount_reporting:.6f}",
                    rec.impact_metric_basis,
                ]
            )
        atomic_write_text(self.paths.unmapped_log_path, buf.getvalue())
        return records

    def build_all(self, *, allow_unmapped_accounts: bool | None = None) -> FactBuildSummary:
        """Build FX rate table, all fact tables, reconcile P&L, and log unmapped codes."""
        try:
            self.build_fx_rate_table()
            long_rows, pnl_rows, recon_passed, recon_detail = self.build_fact_pnl(
                allow_unmapped_accounts=allow_unmapped_accounts
            )
            inv_rows = self.build_fact_inventory()
            price_rows = self.build_fact_price()
            unmapped = self.collect_and_log_unmapped_codes()
        finally:
            self.con.execute("DROP TABLE IF EXISTS _Fact_PnL_Staged_Rows")
            self.con.execute("DROP TABLE IF EXISTS _Fact_Inventory_Staged")
            self.con.execute("DROP TABLE IF EXISTS _Fact_Price_Staged")

        return FactBuildSummary(
            fact_pnl_long_rows=long_rows,
            fact_pnl_rows=pnl_rows,
            fact_inventory_rows=inv_rows,
            fact_price_rows=price_rows,
            unmapped_records=unmapped,
            reconciliation_passed=recon_passed,
            reconciliation_detail=recon_detail,
        )

    def build_facts(self, *, allow_unmapped_accounts: bool | None = None) -> FactBuildSummary:
        """Alias for build_all()."""
        return self.build_all(allow_unmapped_accounts=allow_unmapped_accounts)

    def run(self, *, allow_unmapped_accounts: bool | None = None) -> FactBuildSummary:
        """Alias for build_all()."""
        return self.build_all(allow_unmapped_accounts=allow_unmapped_accounts)
