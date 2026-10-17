"""Kimball Bus Architecture Conformed Dimension Builder with persistent Surrogate Key Registry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from src.csv_processing import detect_encoding_precise
from src.dataset_engine import normalize_period_value
from src.dwh.extractor import (
    _detect_csv_delimiter,
    _quote_ident,
    _quote_literal,
    _utf8_readable_csv,
)
from src.dwh.mart_config import (
    AccountMappingRule,
    DwhConfigError,
    MartWorkspacePaths,
    expand_period_filter,
    get_default_account_mapping,
    load_account_mapping,
)

__all__ = [
    "DIM_ACCOUNT",
    "DIM_CHANNEL",
    "DIM_CUSTOMER",
    "DIM_DATE",
    "DIM_PRODUCT",
    "DIM_WAREHOUSE",
    "UNKNOWN_KEY",
    "UNKNOWN_SK",
    "DimensionBuildStats",
    "DimensionBuilder",
]

UNKNOWN_KEY = "UNKNOWN"
UNKNOWN_SK = -1

DIM_DATE = "Dim_Date"
DIM_CUSTOMER = "Dim_Customer"
DIM_PRODUCT = "Dim_Product"
DIM_ACCOUNT = "Dim_Account"
DIM_WAREHOUSE = "Dim_Warehouse"
DIM_CHANNEL = "Dim_Channel"


@dataclass
class DimensionBuildStats:
    """Row count summary for all conformed dimensions in the warehouse."""

    dim_date_rows: int
    dim_customer_rows: int
    dim_product_rows: int
    dim_account_rows: int
    dim_warehouse_rows: int
    dim_channel_rows: int

    def as_dict(self) -> dict[str, int]:
        return {
            DIM_DATE: self.dim_date_rows,
            DIM_CUSTOMER: self.dim_customer_rows,
            DIM_PRODUCT: self.dim_product_rows,
            DIM_ACCOUNT: self.dim_account_rows,
            DIM_WAREHOUSE: self.dim_warehouse_rows,
            DIM_CHANNEL: self.dim_channel_rows,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DimensionBuilder:
    """Builds and maintains conformed Kimball dimensions with append-only persistent surrogate keys.

    Surrogate Key Rules:
    - `dim_key_registry` persists `(dimension_name, natural_key) -> surrogate_key` across runs.
    - Every dimension always includes `natural_key = 'UNKNOWN'` mapped to `surrogate_key = -1`.
    - Existing natural keys in `dim_key_registry` never have their surrogate keys reassigned.
    - Only newly discovered natural keys receive `MAX(surrogate_key) + ROW_NUMBER() OVER (ORDER BY natural_key)`.
    - Natural keys across all entity dimensions are normalized with `UPPER(TRIM(...))` (preserving leading zeros).
    - `Dim_Customer` and `Dim_Product` only allocate positive surrogate keys from explicit master sources,
      never from unmapped staging codes (which must resolve to `-1` in `FactBuilder`).
    - Auxiliary dimensions (`Dim_Warehouse`, `Dim_Channel`) allocate persistent positive surrogate keys
      from distinct staging codes (`stg_inventory`, `stg_price`) and/or explicit seeds.
    """

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        paths: MartWorkspacePaths | None = None,
    ) -> None:
        self.con = con
        self.paths = paths
        self.ensure_registry_table()

    def ensure_registry_table(self) -> None:
        """Create the persistent surrogate key registry and internal master backing tables if absent."""
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS dim_key_registry (
                dimension_name VARCHAR NOT NULL,
                natural_key VARCHAR NOT NULL,
                surrogate_key INTEGER NOT NULL,
                created_at VARCHAR NOT NULL,
                PRIMARY KEY (dimension_name, natural_key)
            );
            """
        )
        self.con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_key_registry_sk
            ON dim_key_registry (dimension_name, surrogate_key);
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS _master_customer (
                customer_code VARCHAR PRIMARY KEY,
                customer_name VARCHAR NOT NULL,
                channel_group VARCHAR NOT NULL,
                country_code VARCHAR NOT NULL
            );
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS _master_product (
                product_code VARCHAR PRIMARY KEY,
                product_name VARCHAR NOT NULL,
                division VARCHAR NOT NULL,
                category VARCHAR NOT NULL
            );
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS _master_account (
                account_code VARCHAR PRIMARY KEY,
                account_name VARCHAR NOT NULL,
                statement_line VARCHAR NOT NULL,
                waterfall_step VARCHAR NOT NULL,
                cost_category VARCHAR NOT NULL,
                sign_multiplier INTEGER NOT NULL,
                is_subtotal BOOLEAN NOT NULL
            );
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS _master_warehouse (
                warehouse_code VARCHAR PRIMARY KEY,
                warehouse_name VARCHAR NOT NULL
            );
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS _master_channel (
                channel_code VARCHAR PRIMARY KEY,
                channel_name VARCHAR NOT NULL
            );
            """
        )
        self._normalize_and_validate_existing_state()

    def _normalize_and_validate_existing_state(self) -> None:
        """Detect case-insensitive collisions in existing registry/master tables and normalize keys to UPPER(TRIM)."""
        reg_collisions = self.con.execute(
            """
            SELECT dimension_name, UPPER(TRIM(natural_key)) AS norm_key, COUNT(*) AS cnt
            FROM dim_key_registry
            GROUP BY dimension_name, norm_key
            HAVING COUNT(*) > 1
            ORDER BY dimension_name, norm_key;
            """
        ).fetchall()
        if reg_collisions:
            details = [f"{r[0]}:{r[1]} (count={r[2]})" for r in reg_collisions]
            raise DwhConfigError(
                f"Case-insensitive natural_key collision detected in existing dim_key_registry: {details}"
            )
        self.con.execute(
            """
            UPDATE dim_key_registry
            SET natural_key = UPPER(TRIM(natural_key))
            WHERE natural_key != UPPER(TRIM(natural_key));
            """
        )

        for table_name, key_col in (
            ("_master_customer", "customer_code"),
            ("_master_product", "product_code"),
            ("_master_account", "account_code"),
            ("_master_warehouse", "warehouse_code"),
            ("_master_channel", "channel_code"),
        ):
            collisions = self.con.execute(
                f"""
                SELECT UPPER(TRIM({key_col})) AS norm_key, COUNT(*) AS cnt
                FROM {table_name}
                GROUP BY norm_key
                HAVING COUNT(*) > 1
                ORDER BY norm_key;
                """
            ).fetchall()
            if collisions:
                details = [f"{r[0]} (count={r[1]})" for r in collisions]
                raise DwhConfigError(
                    f"Case-insensitive key collision detected in existing {table_name}: {details}"
                )
            self.con.execute(
                f"""
                UPDATE {table_name}
                SET {key_col} = UPPER(TRIM({key_col}))
                WHERE {key_col} != UPPER(TRIM({key_col}));
                """
            )

    def _assert_dimension_uniqueness(self, dim_table: str, sk_col: str, nk_col: str) -> int:
        """Verify that `dim_table` has unique surrogate keys and unique normalized natural keys."""
        row = self.con.execute(
            f"""
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT {sk_col}) AS distinct_sk,
                COUNT(DISTINCT UPPER(TRIM(CAST({nk_col} AS VARCHAR)))) AS distinct_nk
            FROM {dim_table};
            """
        ).fetchone()
        total_rows = int(row[0]) if row else 0
        distinct_sk = int(row[1]) if row else 0
        distinct_nk = int(row[2]) if row else 0
        if total_rows != distinct_sk or total_rows != distinct_nk:
            raise DwhConfigError(
                f"Dimension uniqueness violation in {dim_table}: "
                f"total_rows={total_rows}, distinct_{sk_col}={distinct_sk}, distinct_{nk_col}={distinct_nk}"
            )
        return total_rows

    def _ensure_unknown_key(self, dimension_name: str) -> None:
        """Guarantee that natural_key='UNKNOWN' -> surrogate_key=-1 exists for `dimension_name`."""
        now_iso = _utc_now_iso()
        self.con.execute(
            """
            INSERT INTO dim_key_registry (dimension_name, natural_key, surrogate_key, created_at)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM dim_key_registry
                WHERE dimension_name = ? AND UPPER(TRIM(natural_key)) = ?
            );
            """,
            [dimension_name, UNKNOWN_KEY, UNKNOWN_SK, now_iso, dimension_name, UNKNOWN_KEY],
        )

    def register_natural_keys(
        self,
        dimension_name: str,
        natural_keys: Iterable[str],
    ) -> dict[str, int]:
        """Register new natural keys for a dimension without modifying existing surrogate keys.

        All natural keys are normalized with `UPPER(TRIM(...))` (preserving leading zeros).
        Uses DuckDB SQL `GREATEST(COALESCE(MAX(surrogate_key), 0), 0) + ROW_NUMBER() OVER (ORDER BY natural_key)`
        in an append-only insert.
        """
        self.ensure_registry_table()
        self._ensure_unknown_key(dimension_name)

        clean_keys = sorted(
            {
                str(k).strip().upper()
                for k in natural_keys
                if k is not None and str(k).strip() and str(k).strip().upper() != UNKNOWN_KEY
            }
        )
        if clean_keys:
            now_iso = _utc_now_iso()
            self.con.execute("DROP TABLE IF EXISTS _tmp_incoming_keys;")
            self.con.execute("CREATE TEMP TABLE _tmp_incoming_keys (natural_key VARCHAR);")
            self.con.executemany(
                "INSERT INTO _tmp_incoming_keys (natural_key) VALUES (?);",
                [[k] for k in clean_keys],
            )
            self.con.execute(
                """
                INSERT INTO dim_key_registry (dimension_name, natural_key, surrogate_key, created_at)
                WITH max_sk AS (
                    SELECT GREATEST(COALESCE(MAX(surrogate_key), 0), 0) AS base_sk
                    FROM dim_key_registry
                    WHERE dimension_name = ?
                ),
                unregistered AS (
                    SELECT DISTINCT UPPER(TRIM(src.natural_key)) AS natural_key
                    FROM _tmp_incoming_keys src
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM dim_key_registry reg
                        WHERE reg.dimension_name = ?
                          AND UPPER(TRIM(reg.natural_key)) = UPPER(TRIM(src.natural_key))
                    )
                )
                SELECT
                    ? AS dimension_name,
                    u.natural_key,
                    CAST(m.base_sk + ROW_NUMBER() OVER (ORDER BY u.natural_key) AS INTEGER) AS surrogate_key,
                    ? AS created_at
                FROM unregistered u
                CROSS JOIN max_sk m
                ORDER BY u.natural_key;
                """,
                [dimension_name, dimension_name, dimension_name, now_iso],
            )
            self.con.execute("DROP TABLE IF EXISTS _tmp_incoming_keys;")

        return self.get_key_map(dimension_name)

    def get_key_map(self, dimension_name: str) -> dict[str, int]:
        """Return `{natural_key: surrogate_key}` mapping for `dimension_name`."""
        rows = self.con.execute(
            """
            SELECT UPPER(TRIM(natural_key)), surrogate_key
            FROM dim_key_registry
            WHERE dimension_name = ?
            ORDER BY surrogate_key;
            """,
            [dimension_name],
        ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def _table_exists(self, table_name: str) -> bool:
        """Check whether a table or view exists in the current DuckDB connection."""
        row = self.con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE LOWER(table_name) = LOWER(?);
            """,
            [table_name],
        ).fetchone()
        return bool(row and int(row[0]) > 0)

    def _load_csv_into_temp_table(self, csv_path: Path, temp_table: str) -> list[str]:
        """Load a master CSV file into a DuckDB temporary table with `all_varchar=True`."""
        if not csv_path.exists() or not csv_path.is_file():
            raise DwhConfigError(f"Master CSV file not found: {csv_path}")
        if csv_path.stat().st_size == 0:
            raise DwhConfigError(f"Master CSV file is empty: {csv_path}")

        encoding = detect_encoding_precise(str(csv_path))
        delimiter = _detect_csv_delimiter(str(csv_path), encoding)
        scratch_dir = self.paths.staging_dir if self.paths is not None else csv_path.parent

        with _utf8_readable_csv(csv_path, encoding, scratch_dir) as readable_csv:
            safe_path = readable_csv.resolve().as_posix()
            self.con.execute(f"DROP TABLE IF EXISTS {_quote_ident(temp_table)};")
            self.con.execute(
                f"""
                CREATE TEMP TABLE {_quote_ident(temp_table)} AS
                SELECT *
                FROM read_csv(
                    {_quote_literal(safe_path)},
                    delim={_quote_literal(delimiter)},
                    header=True,
                    all_varchar=True,
                    quote='"',
                    escape='"',
                    strict_mode=false
                );
                """
            )

        col_rows = self.con.execute(f"DESCRIBE {_quote_ident(temp_table)};").fetchall()
        raw_cols = [str(r[0]) for r in col_rows]
        cleaned_cols: list[str] = []
        for col in raw_cols:
            clean_col = col.lstrip("\ufeff").strip()
            if clean_col != col:
                self.con.execute(
                    f"ALTER TABLE {_quote_ident(temp_table)} "
                    f"RENAME COLUMN {_quote_ident(col)} TO {_quote_ident(clean_col)};"
                )
            cleaned_cols.append(clean_col)
        return cleaned_cols

    def build_dim_date(
        self,
        periods: Iterable[str] | str | None = None,
        period_spec: str | None = None,
    ) -> int:
        """Build `Dim_Date` from explicit periods, `period_spec`, and any staging table periods.

        Schema:
        - `date_sk INTEGER` (`202601` format integer key, Unknown is `-1`)
        - `period VARCHAR` (`"2026-01"`, Unknown is `"UNKNOWN"`)
        - `year INTEGER` (`2026`, Unknown is `-1`)
        - `quarter VARCHAR` (`"2026-Q1"`, Unknown is `"UNKNOWN"`)
        - `month INTEGER` (`1`~`12`, Unknown is `-1`)
        """
        self.ensure_registry_table()
        self._ensure_unknown_key(DIM_DATE)

        collected_periods: set[str] = set()

        # 1. Explicit periods argument (can be a period_spec string or an iterable of period strings)
        if isinstance(periods, str):
            expanded = expand_period_filter(periods)
            if expanded:
                collected_periods.update(expanded)
            else:
                norm = normalize_period_value(periods)
                if norm:
                    collected_periods.add(norm)
        elif periods is not None:
            for item in periods:
                if item is None:
                    continue
                raw_str = str(item).strip()
                if not raw_str or raw_str.upper() == UNKNOWN_KEY:
                    continue
                norm = normalize_period_value(raw_str)
                if norm is None:
                    expanded = expand_period_filter(raw_str)
                    if expanded:
                        collected_periods.update(expanded)
                    else:
                        raise DwhConfigError(f"Invalid period value for Dim_Date: {item!r}")
                else:
                    collected_periods.add(norm)

        # 2. Optional period_spec argument
        if period_spec:
            expanded_spec = expand_period_filter(period_spec)
            if expanded_spec:
                collected_periods.update(expanded_spec)

        # 3. Discover periods present in staging tables (`stg_pnl`, `stg_inventory`, `stg_price`)
        for stg_table in ("stg_pnl", "stg_inventory", "stg_price"):
            if self._table_exists(stg_table):
                cols = [
                    str(r[0])
                    for r in self.con.execute(f"DESCRIBE {_quote_ident(stg_table)};").fetchall()
                ]
                if "period" in cols:
                    rows = self.con.execute(
                        f'SELECT DISTINCT "period" FROM {_quote_ident(stg_table)} WHERE "period" IS NOT NULL;'
                    ).fetchall()
                    for (raw_p,) in rows:
                        norm_p = normalize_period_value(raw_p)
                        if norm_p:
                            collected_periods.add(norm_p)

        # 4. Include any periods already registered in dim_key_registry for Dim_Date
        existing_rows = self.con.execute(
            """
            SELECT natural_key
            FROM dim_key_registry
            WHERE dimension_name = ? AND UPPER(TRIM(natural_key)) != ?;
            """,
            [DIM_DATE, UNKNOWN_KEY],
        ).fetchall()
        for (existing_p,) in existing_rows:
            norm_p = normalize_period_value(existing_p)
            if norm_p:
                collected_periods.add(norm_p)

        now_iso = _utc_now_iso()
        for norm_period in sorted(collected_periods):
            year = int(norm_period[:4])
            month = int(norm_period[5:7])
            date_sk = year * 100 + month
            self.con.execute(
                """
                INSERT INTO dim_key_registry (dimension_name, natural_key, surrogate_key, created_at)
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM dim_key_registry
                    WHERE dimension_name = ? AND UPPER(TRIM(natural_key)) = ?
                );
                """,
                [DIM_DATE, norm_period, date_sk, now_iso, DIM_DATE, norm_period],
            )

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE {DIM_DATE} AS
            SELECT
                CAST({UNKNOWN_SK} AS INTEGER) AS date_sk,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS period,
                CAST(-1 AS INTEGER) AS year,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS quarter,
                CAST(-1 AS INTEGER) AS month
            UNION ALL
            SELECT
                CAST(reg.surrogate_key AS INTEGER) AS date_sk,
                CAST(reg.natural_key AS VARCHAR) AS period,
                CAST(SUBSTR(reg.natural_key, 1, 4) AS INTEGER) AS year,
                CAST(
                    SUBSTR(reg.natural_key, 1, 4)
                    || '-Q'
                    || CAST(((CAST(SUBSTR(reg.natural_key, 6, 2) AS INTEGER) - 1) // 3) + 1 AS VARCHAR)
                    AS VARCHAR
                ) AS quarter,
                CAST(SUBSTR(reg.natural_key, 6, 2) AS INTEGER) AS month
            FROM dim_key_registry reg
            WHERE reg.dimension_name = {_quote_literal(DIM_DATE)}
              AND UPPER(TRIM(reg.natural_key)) != {_quote_literal(UNKNOWN_KEY)}
            ORDER BY date_sk;
            """
        )

        return self._assert_dimension_uniqueness(DIM_DATE, "date_sk", "period")

    def build_dim_customer(
        self,
        master_source: Path | str | Sequence[Mapping[str, Any]] | None = None,
        *,
        csv_path: Path | str | None = None,
    ) -> int:
        """Build `Dim_Customer` strictly from master customer data with persistent surrogate keys.

        Raises `DwhConfigError` if case-insensitive duplicate or empty `customer_code` values exist in the master input.
        Normalizes `customer_code` with `UPPER(TRIM(...))` while preserving leading zeros (e.g. `'000100200301'`).
        """
        self.ensure_registry_table()
        self._ensure_unknown_key(DIM_CUSTOMER)

        source = csv_path if csv_path is not None else master_source
        temp_raw = "_tmp_raw_customer_master"
        self.con.execute(f"DROP TABLE IF EXISTS {temp_raw};")

        has_incoming = False
        if isinstance(source, (str, Path)):
            path_obj = Path(source)
            if path_obj.exists() and path_obj.is_file():
                cols = self._load_csv_into_temp_table(path_obj, temp_raw)
                if "customer_code" not in cols:
                    raise DwhConfigError(
                        f"Customer master CSV missing required 'customer_code' column: {path_obj}"
                    )
                for optional_col in ("customer_name", "channel_group", "country_code"):
                    if optional_col not in cols:
                        self.con.execute(
                            f"ALTER TABLE {temp_raw} ADD COLUMN {_quote_ident(optional_col)} VARCHAR DEFAULT 'UNKNOWN';"
                        )
                has_incoming = True
            elif isinstance(source, str) and self._table_exists(source):
                cols = [
                    str(r[0])
                    for r in self.con.execute(f"DESCRIBE {_quote_ident(source)};").fetchall()
                ]
                if "customer_code" not in cols:
                    raise DwhConfigError(
                        f"Customer master table {source!r} missing required 'customer_code' column."
                    )
                self.con.execute(
                    f"CREATE TEMP TABLE {temp_raw} AS SELECT * FROM {_quote_ident(source)};"
                )
                for optional_col in ("customer_name", "channel_group", "country_code"):
                    if optional_col not in cols:
                        self.con.execute(
                            f"ALTER TABLE {temp_raw} ADD COLUMN {_quote_ident(optional_col)} VARCHAR DEFAULT 'UNKNOWN';"
                        )
                has_incoming = True
            else:
                raise DwhConfigError(f"Customer master CSV file not found: {source}")
        elif source is not None:
            records = list(source)
            self.con.execute(
                f"""
                CREATE TEMP TABLE {temp_raw} (
                    customer_code VARCHAR,
                    customer_name VARCHAR,
                    channel_group VARCHAR,
                    country_code VARCHAR
                );
                """
            )
            if records:
                rows_to_insert = []
                for idx, item in enumerate(records, start=1):
                    if not isinstance(item, Mapping):
                        raise DwhConfigError(f"Invalid customer master row at index {idx}: {item!r}")
                    if "customer_code" not in item:
                        raise DwhConfigError(
                            f"Customer master record at index {idx} missing 'customer_code'."
                        )
                    rows_to_insert.append(
                        [
                            "" if item.get("customer_code") is None else str(item.get("customer_code")),
                            "" if item.get("customer_name") is None else str(item.get("customer_name")),
                            "" if item.get("channel_group") is None else str(item.get("channel_group")),
                            "" if item.get("country_code") is None else str(item.get("country_code")),
                        ]
                    )
                self.con.executemany(
                    f"INSERT INTO {temp_raw} VALUES (?, ?, ?, ?);",
                    rows_to_insert,
                )
            has_incoming = True

        if has_incoming:
            # 1. Check for empty customer_code
            empty_cnt = self.con.execute(
                f"""
                SELECT COUNT(*)
                FROM {temp_raw}
                WHERE customer_code IS NULL OR TRIM(CAST(customer_code AS VARCHAR)) = '';
                """
            ).fetchone()[0]
            if int(empty_cnt) > 0:
                raise DwhConfigError(
                    f"Customer master contains {empty_cnt} row(s) with empty/null customer_code."
                )

            # 2. Check for case-insensitive duplicate customer_code in master source
            dup_rows = self.con.execute(
                f"""
                SELECT UPPER(TRIM(CAST(customer_code AS VARCHAR))) AS code, COUNT(*) AS cnt
                FROM {temp_raw}
                GROUP BY code
                HAVING COUNT(*) > 1
                ORDER BY code;
                """
            ).fetchall()
            if dup_rows:
                dup_codes = [str(r[0]) for r in dup_rows]
                raise DwhConfigError(
                    f"Duplicate customer_code detected in customer master: {dup_codes}"
                )

            # 3. Upsert into persistent _master_customer with UPPER(TRIM(...)) normalized key
            self.con.execute(
                f"""
                INSERT OR REPLACE INTO _master_customer (
                    customer_code,
                    customer_name,
                    channel_group,
                    country_code
                )
                SELECT
                    UPPER(TRIM(CAST(customer_code AS VARCHAR))) AS customer_code,
                    COALESCE(NULLIF(TRIM(CAST(customer_name AS VARCHAR)), ''), 'UNKNOWN') AS customer_name,
                    COALESCE(NULLIF(TRIM(CAST(channel_group AS VARCHAR)), ''), 'UNKNOWN') AS channel_group,
                    COALESCE(NULLIF(TRIM(CAST(country_code AS VARCHAR)), ''), 'UNKNOWN') AS country_code
                FROM {temp_raw}
                WHERE UPPER(TRIM(CAST(customer_code AS VARCHAR))) != {_quote_literal(UNKNOWN_KEY)};
                """
            )
            self.con.execute(f"DROP TABLE IF EXISTS {temp_raw};")

        # Register all master customer codes in dim_key_registry (append-only!)
        master_codes = [
            str(r[0])
            for r in self.con.execute(
                "SELECT UPPER(TRIM(customer_code)) FROM _master_customer ORDER BY UPPER(TRIM(customer_code));"
            ).fetchall()
        ]
        self.register_natural_keys(DIM_CUSTOMER, master_codes)

        # Materialize Dim_Customer
        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE {DIM_CUSTOMER} AS
            SELECT
                CAST({UNKNOWN_SK} AS INTEGER) AS customer_sk,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS customer_code,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS customer_name,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS channel_group,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS country_code
            UNION ALL
            SELECT
                CAST(reg.surrogate_key AS INTEGER) AS customer_sk,
                CAST(UPPER(TRIM(m.customer_code)) AS VARCHAR) AS customer_code,
                CAST(m.customer_name AS VARCHAR) AS customer_name,
                CAST(m.channel_group AS VARCHAR) AS channel_group,
                CAST(m.country_code AS VARCHAR) AS country_code
            FROM _master_customer m
            INNER JOIN dim_key_registry reg
                ON reg.dimension_name = {_quote_literal(DIM_CUSTOMER)}
               AND UPPER(TRIM(reg.natural_key)) = UPPER(TRIM(m.customer_code))
            ORDER BY customer_sk;
            """
        )

        return self._assert_dimension_uniqueness(DIM_CUSTOMER, "customer_sk", "customer_code")

    def build_dim_product(
        self,
        master_source: Path | str | Sequence[Mapping[str, Any]] | None = None,
        *,
        csv_path: Path | str | None = None,
    ) -> int:
        """Build `Dim_Product` strictly from master product data with persistent surrogate keys.

        Raises `DwhConfigError` if case-insensitive duplicate or empty `product_code` values exist in the master input.
        Normalizes `product_code` with `UPPER(TRIM(...))` while preserving leading zeros.
        """
        self.ensure_registry_table()
        self._ensure_unknown_key(DIM_PRODUCT)

        source = csv_path if csv_path is not None else master_source
        temp_raw = "_tmp_raw_product_master"
        self.con.execute(f"DROP TABLE IF EXISTS {temp_raw};")

        has_incoming = False
        if isinstance(source, (str, Path)):
            path_obj = Path(source)
            if path_obj.exists() and path_obj.is_file():
                cols = self._load_csv_into_temp_table(path_obj, temp_raw)
                if "product_code" not in cols:
                    raise DwhConfigError(
                        f"Product master CSV missing required 'product_code' column: {path_obj}"
                    )
                for optional_col in ("product_name", "division", "category"):
                    if optional_col not in cols:
                        self.con.execute(
                            f"ALTER TABLE {temp_raw} ADD COLUMN {_quote_ident(optional_col)} VARCHAR DEFAULT 'UNKNOWN';"
                        )
                has_incoming = True
            elif isinstance(source, str) and self._table_exists(source):
                cols = [
                    str(r[0])
                    for r in self.con.execute(f"DESCRIBE {_quote_ident(source)};").fetchall()
                ]
                if "product_code" not in cols:
                    raise DwhConfigError(
                        f"Product master table {source!r} missing required 'product_code' column."
                    )
                self.con.execute(
                    f"CREATE TEMP TABLE {temp_raw} AS SELECT * FROM {_quote_ident(source)};"
                )
                for optional_col in ("product_name", "division", "category"):
                    if optional_col not in cols:
                        self.con.execute(
                            f"ALTER TABLE {temp_raw} ADD COLUMN {_quote_ident(optional_col)} VARCHAR DEFAULT 'UNKNOWN';"
                        )
                has_incoming = True
            else:
                raise DwhConfigError(f"Product master CSV file not found: {source}")
        elif source is not None:
            records = list(source)
            self.con.execute(
                f"""
                CREATE TEMP TABLE {temp_raw} (
                    product_code VARCHAR,
                    product_name VARCHAR,
                    division VARCHAR,
                    category VARCHAR
                );
                """
            )
            if records:
                rows_to_insert = []
                for idx, item in enumerate(records, start=1):
                    if not isinstance(item, Mapping):
                        raise DwhConfigError(f"Invalid product master row at index {idx}: {item!r}")
                    if "product_code" not in item:
                        raise DwhConfigError(
                            f"Product master record at index {idx} missing 'product_code'."
                        )
                    rows_to_insert.append(
                        [
                            "" if item.get("product_code") is None else str(item.get("product_code")),
                            "" if item.get("product_name") is None else str(item.get("product_name")),
                            "" if item.get("division") is None else str(item.get("division")),
                            "" if item.get("category") is None else str(item.get("category")),
                        ]
                    )
                self.con.executemany(
                    f"INSERT INTO {temp_raw} VALUES (?, ?, ?, ?);",
                    rows_to_insert,
                )
            has_incoming = True

        if has_incoming:
            # 1. Check for empty product_code
            empty_cnt = self.con.execute(
                f"""
                SELECT COUNT(*)
                FROM {temp_raw}
                WHERE product_code IS NULL OR TRIM(CAST(product_code AS VARCHAR)) = '';
                """
            ).fetchone()[0]
            if int(empty_cnt) > 0:
                raise DwhConfigError(
                    f"Product master contains {empty_cnt} row(s) with empty/null product_code."
                )

            # 2. Check for case-insensitive duplicate product_code in master source
            dup_rows = self.con.execute(
                f"""
                SELECT UPPER(TRIM(CAST(product_code AS VARCHAR))) AS code, COUNT(*) AS cnt
                FROM {temp_raw}
                GROUP BY code
                HAVING COUNT(*) > 1
                ORDER BY code;
                """
            ).fetchall()
            if dup_rows:
                dup_codes = [str(r[0]) for r in dup_rows]
                raise DwhConfigError(
                    f"Duplicate product_code detected in product master: {dup_codes}"
                )

            # 3. Upsert into persistent _master_product with UPPER(TRIM(...)) normalized key
            self.con.execute(
                f"""
                INSERT OR REPLACE INTO _master_product (
                    product_code,
                    product_name,
                    division,
                    category
                )
                SELECT
                    UPPER(TRIM(CAST(product_code AS VARCHAR))) AS product_code,
                    COALESCE(NULLIF(TRIM(CAST(product_name AS VARCHAR)), ''), 'UNKNOWN') AS product_name,
                    COALESCE(NULLIF(TRIM(CAST(division AS VARCHAR)), ''), 'UNKNOWN') AS division,
                    COALESCE(NULLIF(TRIM(CAST(category AS VARCHAR)), ''), 'UNKNOWN') AS category
                FROM {temp_raw}
                WHERE UPPER(TRIM(CAST(product_code AS VARCHAR))) != {_quote_literal(UNKNOWN_KEY)};
                """
            )
            self.con.execute(f"DROP TABLE IF EXISTS {temp_raw};")

        master_codes = [
            str(r[0])
            for r in self.con.execute(
                "SELECT UPPER(TRIM(product_code)) FROM _master_product ORDER BY UPPER(TRIM(product_code));"
            ).fetchall()
        ]
        self.register_natural_keys(DIM_PRODUCT, master_codes)

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE {DIM_PRODUCT} AS
            SELECT
                CAST({UNKNOWN_SK} AS INTEGER) AS product_sk,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS product_code,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS product_name,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS division,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS category
            UNION ALL
            SELECT
                CAST(reg.surrogate_key AS INTEGER) AS product_sk,
                CAST(UPPER(TRIM(m.product_code)) AS VARCHAR) AS product_code,
                CAST(m.product_name AS VARCHAR) AS product_name,
                CAST(m.division AS VARCHAR) AS division,
                CAST(m.category AS VARCHAR) AS category
            FROM _master_product m
            INNER JOIN dim_key_registry reg
                ON reg.dimension_name = {_quote_literal(DIM_PRODUCT)}
               AND UPPER(TRIM(reg.natural_key)) = UPPER(TRIM(m.product_code))
            ORDER BY product_sk;
            """
        )

        return self._assert_dimension_uniqueness(DIM_PRODUCT, "product_sk", "product_code")

    def build_dim_account(
        self,
        mapping_rules: Mapping[str, AccountMappingRule] | Iterable[AccountMappingRule] | Path | str | None = None,
    ) -> int:
        """Build `Dim_Account` from `AccountMappingRule` definitions with persistent surrogate keys.

        Preserves historical `(Dim_Account, account_code) -> account_sk` entries in `dim_key_registry`,
        while replacing `_master_account` and `Dim_Account` with only the active `mapping_rules`
        (plus the `UNKNOWN` `-1` row) so deleted mapping rules do not linger in `Dim_Account`.

        Schema:
        - `account_sk INTEGER` (`-1` and positive integers)
        - `account_code VARCHAR`
        - `account_name VARCHAR`
        - `statement_line VARCHAR`
        - `waterfall_step VARCHAR`
        - `cost_category VARCHAR`
        - `sign_multiplier INTEGER`
        - `is_subtotal BOOLEAN`
        """
        self.ensure_registry_table()
        self._ensure_unknown_key(DIM_ACCOUNT)

        rules_list: list[AccountMappingRule] = []
        replace_active_set = False

        if mapping_rules is None:
            existing_cnt = self.con.execute("SELECT COUNT(*) FROM _master_account;").fetchone()[0]
            if int(existing_cnt) == 0:
                rules_list = list(get_default_account_mapping().values())
                replace_active_set = True
        elif isinstance(mapping_rules, (str, Path)):
            loaded = load_account_mapping(mapping_rules)
            rules_list = list(loaded.values())
            replace_active_set = True
        elif isinstance(mapping_rules, Mapping):
            rules_list = list(mapping_rules.values())
            replace_active_set = True
        else:
            rules_list = list(mapping_rules)
            replace_active_set = True

        if replace_active_set:
            seen_codes: set[str] = set()
            normalized_rows: list[list[Any]] = []
            for rule in rules_list:
                if not isinstance(rule, AccountMappingRule):
                    raise DwhConfigError(f"Expected AccountMappingRule, got {rule!r}")
                norm_code = rule.account_code.strip().upper()
                if not norm_code or norm_code == UNKNOWN_KEY:
                    raise DwhConfigError(f"Invalid account_code in mapping_rules: {rule.account_code!r}")
                if norm_code in seen_codes:
                    raise DwhConfigError(
                        f"Duplicate account_code in mapping_rules: {rule.account_code!r}"
                    )
                seen_codes.add(norm_code)
                normalized_rows.append(
                    [
                        norm_code,
                        rule.account_name,
                        rule.statement_line,
                        rule.waterfall_step,
                        rule.cost_category,
                        int(rule.sign_multiplier),
                        bool(rule.is_subtotal),
                    ]
                )

            # Replace _master_account with only the currently active rules
            self.con.execute("DELETE FROM _master_account;")
            if normalized_rows:
                self.con.executemany(
                    """
                    INSERT INTO _master_account (
                        account_code,
                        account_name,
                        statement_line,
                        waterfall_step,
                        cost_category,
                        sign_multiplier,
                        is_subtotal
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    normalized_rows,
                )

        account_codes = [
            str(r[0])
            for r in self.con.execute(
                "SELECT UPPER(TRIM(account_code)) FROM _master_account ORDER BY UPPER(TRIM(account_code));"
            ).fetchall()
        ]
        self.register_natural_keys(DIM_ACCOUNT, account_codes)

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE {DIM_ACCOUNT} AS
            SELECT
                CAST({UNKNOWN_SK} AS INTEGER) AS account_sk,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS account_code,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS account_name,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS statement_line,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS waterfall_step,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS cost_category,
                CAST(1 AS INTEGER) AS sign_multiplier,
                CAST(FALSE AS BOOLEAN) AS is_subtotal
            UNION ALL
            SELECT
                CAST(reg.surrogate_key AS INTEGER) AS account_sk,
                CAST(UPPER(TRIM(m.account_code)) AS VARCHAR) AS account_code,
                CAST(m.account_name AS VARCHAR) AS account_name,
                CAST(m.statement_line AS VARCHAR) AS statement_line,
                CAST(m.waterfall_step AS VARCHAR) AS waterfall_step,
                CAST(m.cost_category AS VARCHAR) AS cost_category,
                CAST(m.sign_multiplier AS INTEGER) AS sign_multiplier,
                CAST(m.is_subtotal AS BOOLEAN) AS is_subtotal
            FROM _master_account m
            INNER JOIN dim_key_registry reg
                ON reg.dimension_name = {_quote_literal(DIM_ACCOUNT)}
               AND UPPER(TRIM(reg.natural_key)) = UPPER(TRIM(m.account_code))
            ORDER BY account_sk;
            """
        )

        return self._assert_dimension_uniqueness(DIM_ACCOUNT, "account_sk", "account_code")

    def build_dim_warehouse(
        self,
        codes_or_source: Path | str | Iterable[str | Mapping[str, Any]] | None = None,
        *,
        staging_table: str = "stg_inventory",
    ) -> int:
        """Build `Dim_Warehouse` from `stg_inventory` and/or explicit warehouse codes with persistent keys.

        Schema:
        - `warehouse_sk INTEGER`
        - `warehouse_code VARCHAR`
        - `warehouse_name VARCHAR`
        """
        self.ensure_registry_table()
        self._ensure_unknown_key(DIM_WAREHOUSE)

        discovered: dict[str, str] = {}

        # 1. Discover from staging table (default: stg_inventory) if it exists
        if staging_table and self._table_exists(staging_table):
            cols = [
                str(r[0])
                for r in self.con.execute(f"DESCRIBE {_quote_ident(staging_table)};").fetchall()
            ]
            if "warehouse_code" in cols:
                if "warehouse_name" in cols:
                    rows = self.con.execute(
                        f"""
                        SELECT
                            UPPER(TRIM(CAST("warehouse_code" AS VARCHAR))) AS code,
                            MAX(NULLIF(TRIM(CAST("warehouse_name" AS VARCHAR)), '')) AS name
                        FROM {_quote_ident(staging_table)}
                        WHERE "warehouse_code" IS NOT NULL
                          AND TRIM(CAST("warehouse_code" AS VARCHAR)) != ''
                          AND UPPER(TRIM(CAST("warehouse_code" AS VARCHAR))) != {_quote_literal(UNKNOWN_KEY)}
                        GROUP BY code
                        ORDER BY code;
                        """
                    ).fetchall()
                    for code, name in rows:
                        clean_code = str(code).strip().upper()
                        discovered[clean_code] = str(name).strip() if name else clean_code
                else:
                    rows = self.con.execute(
                        f"""
                        SELECT DISTINCT UPPER(TRIM(CAST("warehouse_code" AS VARCHAR))) AS code
                        FROM {_quote_ident(staging_table)}
                        WHERE "warehouse_code" IS NOT NULL
                          AND TRIM(CAST("warehouse_code" AS VARCHAR)) != ''
                          AND UPPER(TRIM(CAST("warehouse_code" AS VARCHAR))) != {_quote_literal(UNKNOWN_KEY)}
                        ORDER BY code;
                        """
                    ).fetchall()
                    for (code,) in rows:
                        clean_code = str(code).strip().upper()
                        discovered[clean_code] = clean_code

        # 2. Optional explicit codes / records / CSV
        if isinstance(codes_or_source, (str, Path)):
            path_obj = Path(codes_or_source)
            if path_obj.exists() and path_obj.is_file():
                tmp_wh = "_tmp_raw_warehouse"
                cols = self._load_csv_into_temp_table(path_obj, tmp_wh)
                if "warehouse_code" not in cols:
                    raise DwhConfigError(
                        f"Warehouse CSV missing required 'warehouse_code' column: {path_obj}"
                    )
                dup_rows = self.con.execute(
                    f"""
                    SELECT UPPER(TRIM(CAST("warehouse_code" AS VARCHAR))) AS code, COUNT(*) AS cnt
                    FROM {tmp_wh}
                    WHERE "warehouse_code" IS NOT NULL AND TRIM(CAST("warehouse_code" AS VARCHAR)) != ''
                    GROUP BY code
                    HAVING COUNT(*) > 1
                    ORDER BY code;
                    """
                ).fetchall()
                if dup_rows:
                    dup_codes = [str(r[0]) for r in dup_rows]
                    raise DwhConfigError(
                        f"Duplicate warehouse_code detected in warehouse master: {dup_codes}"
                    )
                name_expr = (
                    'TRIM(CAST("warehouse_name" AS VARCHAR))'
                    if "warehouse_name" in cols
                    else 'UPPER(TRIM(CAST("warehouse_code" AS VARCHAR)))'
                )
                rows = self.con.execute(
                    f'SELECT UPPER(TRIM(CAST("warehouse_code" AS VARCHAR))), {name_expr} FROM {tmp_wh};'
                ).fetchall()
                for code, name in rows:
                    if code and str(code).strip() and str(code).strip().upper() != UNKNOWN_KEY:
                        c = str(code).strip().upper()
                        discovered[c] = str(name).strip() if name and str(name).strip() else c
                self.con.execute(f"DROP TABLE IF EXISTS {tmp_wh};")
            elif isinstance(codes_or_source, str) and self._table_exists(codes_or_source):
                cols = [
                    str(r[0])
                    for r in self.con.execute(f"DESCRIBE {_quote_ident(codes_or_source)};").fetchall()
                ]
                if "warehouse_code" in cols:
                    name_expr = (
                        'MAX(NULLIF(TRIM(CAST("warehouse_name" AS VARCHAR)), \'\'))'
                        if "warehouse_name" in cols
                        else 'UPPER(TRIM(CAST("warehouse_code" AS VARCHAR)))'
                    )
                    rows = self.con.execute(
                        f"""
                        SELECT UPPER(TRIM(CAST("warehouse_code" AS VARCHAR))) AS code, {name_expr}
                        FROM {_quote_ident(codes_or_source)}
                        GROUP BY code;
                        """
                    ).fetchall()
                    for code, name in rows:
                        if code and str(code).strip() and str(code).strip().upper() != UNKNOWN_KEY:
                            c = str(code).strip().upper()
                            discovered[c] = str(name).strip() if name and str(name).strip() else c
        elif codes_or_source is not None:
            seen_explicit_wh: set[str] = set()
            for item in codes_or_source:
                if isinstance(item, Mapping):
                    raw_code = str(item.get("warehouse_code", "")).strip().upper()
                    raw_name = str(item.get("warehouse_name", "")).strip() or raw_code
                else:
                    raw_code = str(item).strip().upper()
                    raw_name = raw_code
                if raw_code and raw_code != UNKNOWN_KEY:
                    if raw_code in seen_explicit_wh:
                        raise DwhConfigError(
                            f"Duplicate warehouse_code detected in warehouse master: {raw_code!r}"
                        )
                    seen_explicit_wh.add(raw_code)
                    discovered[raw_code] = raw_name

        if discovered:
            self.con.executemany(
                """
                INSERT OR REPLACE INTO _master_warehouse (warehouse_code, warehouse_name)
                VALUES (?, ?);
                """,
                [[code, name] for code, name in sorted(discovered.items())],
            )

        all_wh_codes = [
            str(r[0])
            for r in self.con.execute(
                "SELECT UPPER(TRIM(warehouse_code)) FROM _master_warehouse ORDER BY UPPER(TRIM(warehouse_code));"
            ).fetchall()
        ]
        self.register_natural_keys(DIM_WAREHOUSE, all_wh_codes)

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE {DIM_WAREHOUSE} AS
            SELECT
                CAST({UNKNOWN_SK} AS INTEGER) AS warehouse_sk,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS warehouse_code,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS warehouse_name
            UNION ALL
            SELECT
                CAST(reg.surrogate_key AS INTEGER) AS warehouse_sk,
                CAST(UPPER(TRIM(m.warehouse_code)) AS VARCHAR) AS warehouse_code,
                CAST(m.warehouse_name AS VARCHAR) AS warehouse_name
            FROM _master_warehouse m
            INNER JOIN dim_key_registry reg
                ON reg.dimension_name = {_quote_literal(DIM_WAREHOUSE)}
               AND UPPER(TRIM(reg.natural_key)) = UPPER(TRIM(m.warehouse_code))
            ORDER BY warehouse_sk;
            """
        )

        return self._assert_dimension_uniqueness(DIM_WAREHOUSE, "warehouse_sk", "warehouse_code")

    def build_dim_channel(
        self,
        codes_or_source: Path | str | Iterable[str | Mapping[str, Any]] | None = None,
        *,
        staging_table: str = "stg_price",
    ) -> int:
        """Build `Dim_Channel` from `stg_price` and/or explicit channel codes with persistent keys.

        Schema:
        - `channel_sk INTEGER`
        - `channel_code VARCHAR`
        - `channel_name VARCHAR`
        """
        self.ensure_registry_table()
        self._ensure_unknown_key(DIM_CHANNEL)

        discovered: dict[str, str] = {}

        # 1. Discover from staging table (default: stg_price) if it exists
        if staging_table and self._table_exists(staging_table):
            cols = [
                str(r[0])
                for r in self.con.execute(f"DESCRIBE {_quote_ident(staging_table)};").fetchall()
            ]
            if "channel_code" in cols:
                if "channel_name" in cols:
                    rows = self.con.execute(
                        f"""
                        SELECT
                            UPPER(TRIM(CAST("channel_code" AS VARCHAR))) AS code,
                            MAX(NULLIF(TRIM(CAST("channel_name" AS VARCHAR)), '')) AS name
                        FROM {_quote_ident(staging_table)}
                        WHERE "channel_code" IS NOT NULL
                          AND TRIM(CAST("channel_code" AS VARCHAR)) != ''
                          AND UPPER(TRIM(CAST("channel_code" AS VARCHAR))) != {_quote_literal(UNKNOWN_KEY)}
                        GROUP BY code
                        ORDER BY code;
                        """
                    ).fetchall()
                    for code, name in rows:
                        clean_code = str(code).strip().upper()
                        discovered[clean_code] = str(name).strip() if name else clean_code
                else:
                    rows = self.con.execute(
                        f"""
                        SELECT DISTINCT UPPER(TRIM(CAST("channel_code" AS VARCHAR))) AS code
                        FROM {_quote_ident(staging_table)}
                        WHERE "channel_code" IS NOT NULL
                          AND TRIM(CAST("channel_code" AS VARCHAR)) != ''
                          AND UPPER(TRIM(CAST("channel_code" AS VARCHAR))) != {_quote_literal(UNKNOWN_KEY)}
                        ORDER BY code;
                        """
                    ).fetchall()
                    for (code,) in rows:
                        clean_code = str(code).strip().upper()
                        discovered[clean_code] = clean_code

        # 2. Optional explicit codes / records / CSV
        if isinstance(codes_or_source, (str, Path)):
            path_obj = Path(codes_or_source)
            if path_obj.exists() and path_obj.is_file():
                tmp_ch = "_tmp_raw_channel"
                cols = self._load_csv_into_temp_table(path_obj, tmp_ch)
                if "channel_code" not in cols:
                    raise DwhConfigError(
                        f"Channel CSV missing required 'channel_code' column: {path_obj}"
                    )
                dup_rows = self.con.execute(
                    f"""
                    SELECT UPPER(TRIM(CAST("channel_code" AS VARCHAR))) AS code, COUNT(*) AS cnt
                    FROM {tmp_ch}
                    WHERE "channel_code" IS NOT NULL AND TRIM(CAST("channel_code" AS VARCHAR)) != ''
                    GROUP BY code
                    HAVING COUNT(*) > 1
                    ORDER BY code;
                    """
                ).fetchall()
                if dup_rows:
                    dup_codes = [str(r[0]) for r in dup_rows]
                    raise DwhConfigError(
                        f"Duplicate channel_code detected in channel master: {dup_codes}"
                    )
                name_expr = (
                    'TRIM(CAST("channel_name" AS VARCHAR))'
                    if "channel_name" in cols
                    else 'UPPER(TRIM(CAST("channel_code" AS VARCHAR)))'
                )
                rows = self.con.execute(
                    f'SELECT UPPER(TRIM(CAST("channel_code" AS VARCHAR))), {name_expr} FROM {tmp_ch};'
                ).fetchall()
                for code, name in rows:
                    if code and str(code).strip() and str(code).strip().upper() != UNKNOWN_KEY:
                        c = str(code).strip().upper()
                        discovered[c] = str(name).strip() if name and str(name).strip() else c
                self.con.execute(f"DROP TABLE IF EXISTS {tmp_ch};")
            elif isinstance(codes_or_source, str) and self._table_exists(codes_or_source):
                cols = [
                    str(r[0])
                    for r in self.con.execute(f"DESCRIBE {_quote_ident(codes_or_source)};").fetchall()
                ]
                if "channel_code" in cols:
                    name_expr = (
                        'MAX(NULLIF(TRIM(CAST("channel_name" AS VARCHAR)), \'\'))'
                        if "channel_name" in cols
                        else 'UPPER(TRIM(CAST("channel_code" AS VARCHAR)))'
                    )
                    rows = self.con.execute(
                        f"""
                        SELECT UPPER(TRIM(CAST("channel_code" AS VARCHAR))) AS code, {name_expr}
                        FROM {_quote_ident(codes_or_source)}
                        GROUP BY code;
                        """
                    ).fetchall()
                    for code, name in rows:
                        if code and str(code).strip() and str(code).strip().upper() != UNKNOWN_KEY:
                            c = str(code).strip().upper()
                            discovered[c] = str(name).strip() if name and str(name).strip() else c
        elif codes_or_source is not None:
            seen_explicit_ch: set[str] = set()
            for item in codes_or_source:
                if isinstance(item, Mapping):
                    raw_code = str(item.get("channel_code", "")).strip().upper()
                    raw_name = str(item.get("channel_name", "")).strip() or raw_code
                else:
                    raw_code = str(item).strip().upper()
                    raw_name = raw_code
                if raw_code and raw_code != UNKNOWN_KEY:
                    if raw_code in seen_explicit_ch:
                        raise DwhConfigError(
                            f"Duplicate channel_code detected in channel master: {raw_code!r}"
                        )
                    seen_explicit_ch.add(raw_code)
                    discovered[raw_code] = raw_name

        if discovered:
            self.con.executemany(
                """
                INSERT OR REPLACE INTO _master_channel (channel_code, channel_name)
                VALUES (?, ?);
                """,
                [[code, name] for code, name in sorted(discovered.items())],
            )

        all_ch_codes = [
            str(r[0])
            for r in self.con.execute(
                "SELECT UPPER(TRIM(channel_code)) FROM _master_channel ORDER BY UPPER(TRIM(channel_code));"
            ).fetchall()
        ]
        self.register_natural_keys(DIM_CHANNEL, all_ch_codes)

        self.con.execute(
            f"""
            CREATE OR REPLACE TABLE {DIM_CHANNEL} AS
            SELECT
                CAST({UNKNOWN_SK} AS INTEGER) AS channel_sk,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS channel_code,
                CAST({_quote_literal(UNKNOWN_KEY)} AS VARCHAR) AS channel_name
            UNION ALL
            SELECT
                CAST(reg.surrogate_key AS INTEGER) AS channel_sk,
                CAST(UPPER(TRIM(m.channel_code)) AS VARCHAR) AS channel_code,
                CAST(m.channel_name AS VARCHAR) AS channel_name
            FROM _master_channel m
            INNER JOIN dim_key_registry reg
                ON reg.dimension_name = {_quote_literal(DIM_CHANNEL)}
               AND UPPER(TRIM(reg.natural_key)) = UPPER(TRIM(m.channel_code))
            ORDER BY channel_sk;
            """
        )

        return self._assert_dimension_uniqueness(DIM_CHANNEL, "channel_sk", "channel_code")

    def build_all_dimensions(
        self,
        *,
        customer_master: Path | str | Sequence[Mapping[str, Any]] | None = None,
        product_master: Path | str | Sequence[Mapping[str, Any]] | None = None,
        account_mapping: Mapping[str, AccountMappingRule] | Iterable[AccountMappingRule] | Path | str | None = None,
        periods: Iterable[str] | str | None = None,
        period_spec: str | None = None,
        warehouse_source: Path | str | Iterable[str | Mapping[str, Any]] | None = None,
        channel_source: Path | str | Iterable[str | Mapping[str, Any]] | None = None,
    ) -> DimensionBuildStats:
        """Build all six conformed Kimball dimensions in a single orchestrated call."""
        dim_date_rows = self.build_dim_date(periods=periods, period_spec=period_spec)
        dim_customer_rows = self.build_dim_customer(master_source=customer_master)
        dim_product_rows = self.build_dim_product(master_source=product_master)
        dim_account_rows = self.build_dim_account(mapping_rules=account_mapping)
        dim_warehouse_rows = self.build_dim_warehouse(codes_or_source=warehouse_source)
        dim_channel_rows = self.build_dim_channel(codes_or_source=channel_source)

        return DimensionBuildStats(
            dim_date_rows=dim_date_rows,
            dim_customer_rows=dim_customer_rows,
            dim_product_rows=dim_product_rows,
            dim_account_rows=dim_account_rows,
            dim_warehouse_rows=dim_warehouse_rows,
            dim_channel_rows=dim_channel_rows,
        )
