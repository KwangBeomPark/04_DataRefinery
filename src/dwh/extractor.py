"""High-speed DuckDB CSV extractor with entity isolation, period normalization, and currency validation."""

from __future__ import annotations

import csv
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb

from src.csv_processing import detect_delimiter, detect_encoding_precise
from src.dataset_engine import (
    calculate_file_hash,
    make_numeric_sql_expr,
    normalize_period_value,
)
from src.dwh.mart_config import (
    AccountMappingRule,
    DwhConfigError,
    EntityIsolationConfig,
    MartWorkspacePaths,
    expand_period_filter,
)

__all__ = [
    "AccountMappingRule",
    "DwhExtractionError",
    "EntityExtractor",
    "ExtractionStats",
    "make_numeric_sql_expr",
    "normalize_period_value",
]

_SAFE_SOURCE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_CORE_FILTER_COLUMNS: tuple[str, ...] = ("entity_code", "period", "currency_code")


class DwhExtractionError(DwhConfigError, ValueError):
    """Raised when source CSV validation, required columns, or currency isolation checks fail."""


@dataclass
class ExtractionStats:
    """Summary metrics and artifact locations produced by extracting a raw CSV source."""

    source_name: str
    source_path: str
    file_hash: str
    encoding: str
    delimiter: str
    total_raw_rows: int
    filtered_rows: int
    excluded_entity_rows: int
    excluded_period_rows: int
    invalid_currency_rows: int
    staging_table: str
    staging_parquet_path: str


def _quote_ident(identifier: str) -> str:
    """Safely double-quote a SQL identifier for DuckDB."""
    escaped = str(identifier).replace('"', '""')
    return f'"{escaped}"'


def _quote_literal(value: str) -> str:
    """Safely single-quote a SQL string literal for DuckDB."""
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _sanitize_source_name(source_name: str) -> str:
    """Validate and normalize a logical source name ('pnl', 'inventory', 'price')."""
    clean = str(source_name).strip().lower()
    if not clean or not _SAFE_SOURCE_NAME_RE.match(clean):
        raise DwhExtractionError(f"Invalid source_name: {source_name!r}")
    return clean


def _build_norm_period_sql(period_ident: str = '"period"') -> str:
    """Build DuckDB SQL expression that normalizes raw period strings to 'YYYY-MM'."""
    trimmed = f"TRIM(COALESCE(CAST({period_ident} AS VARCHAR), ''))"
    return f"""CASE
        WHEN regexp_matches({trimmed}, '^[0-9]{{4}}[-./_][0-9]{{1,2}}$') THEN
            CASE
                WHEN TRY_CAST(regexp_extract({trimmed}, '^([0-9]{{4}})[-./_]([0-9]{{1,2}})$', 2) AS INTEGER) BETWEEN 1 AND 12
                THEN regexp_extract({trimmed}, '^([0-9]{{4}})[-./_]([0-9]{{1,2}})$', 1)
                     || '-' ||
                     LPAD(regexp_extract({trimmed}, '^([0-9]{{4}})[-./_]([0-9]{{1,2}})$', 2), 2, '0')
                ELSE {trimmed}
            END
        WHEN regexp_matches({trimmed}, '^[0-9]{{6}}$') THEN
            CASE
                WHEN TRY_CAST(SUBSTR({trimmed}, 5, 2) AS INTEGER) BETWEEN 1 AND 12
                THEN SUBSTR({trimmed}, 1, 4) || '-' || SUBSTR({trimmed}, 5, 2)
                ELSE {trimmed}
            END
        WHEN regexp_matches({trimmed}, '^[0-9]{{8}}$') THEN
            CASE
                WHEN TRY_CAST(SUBSTR({trimmed}, 5, 2) AS INTEGER) BETWEEN 1 AND 12
                THEN SUBSTR({trimmed}, 1, 4) || '-' || SUBSTR({trimmed}, 5, 2)
                ELSE {trimmed}
            END
        WHEN regexp_matches({trimmed}, '^([0-9]{{4}}|[0-9]{{2}})[년./\\s]+([0-9]{{1,2}})[월]?$') THEN
            CASE
                WHEN TRY_CAST(regexp_extract({trimmed}, '^([0-9]{{4}}|[0-9]{{2}})[년./\\s]+([0-9]{{1,2}})[월]?$', 2) AS INTEGER) BETWEEN 1 AND 12
                THEN (
                    CASE
                        WHEN LENGTH(regexp_extract({trimmed}, '^([0-9]{{4}}|[0-9]{{2}})[년./\\s]+([0-9]{{1,2}})[월]?$', 1)) = 2
                        THEN '20' || regexp_extract({trimmed}, '^([0-9]{{4}}|[0-9]{{2}})[년./\\s]+([0-9]{{1,2}})[월]?$', 1)
                        ELSE regexp_extract({trimmed}, '^([0-9]{{4}}|[0-9]{{2}})[년./\\s]+([0-9]{{1,2}})[월]?$', 1)
                    END
                ) || '-' || LPAD(regexp_extract({trimmed}, '^([0-9]{{4}}|[0-9]{{2}})[년./\\s]+([0-9]{{1,2}})[월]?$', 2), 2, '0')
                ELSE {trimmed}
            END
        ELSE {trimmed}
    END"""


def _detect_csv_delimiter(file_path: str, encoding: str) -> str:
    """Detect CSV delimiter safely using src.csv_processing.detect_delimiter with encoding fallback."""
    detected: str | None = None
    try:
        detected = detect_delimiter(file_path)
    except TypeError:
        # Defensive support in case a test monkeypatches detect_delimiter(path, encoding)
        detected = detect_delimiter(file_path, encoding)  # type: ignore[arg-type]
    except (OSError, csv.Error, UnicodeError, ValueError):
        detected = None

    if detected and len(detected) == 1:
        return detected

    # Fallback inspection using the detected encoding directly
    try:
        for candidate in (";", ",", "\t", "|"):
            with open(file_path, "r", encoding=encoding, errors="replace", newline="") as handle:
                reader = csv.reader(handle, delimiter=candidate)
                first_row = next(reader, None)
                if first_row and len(first_row) > 1:
                    return candidate
    except OSError:
        pass
    return ","


@contextmanager
def _utf8_readable_csv(csv_path: Path, encoding: str, scratch_dir: Path) -> Iterator[Path]:
    """Yield a UTF-8 compatible CSV path for DuckDB read_csv, transcoding legacy codepages if needed."""
    norm_enc = (encoding or "utf-8").strip().lower()
    if norm_enc in ("utf-8", "utf-8-sig", "utf8", "ascii"):
        yield csv_path
        return

    scratch_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="dwh_utf8_", suffix=".csv", dir=str(scratch_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with (
            csv_path.open("r", encoding=encoding, errors="replace", newline="") as src_file,
            tmp_path.open("w", encoding="utf-8", newline="") as dst_file,
        ):
            shutil.copyfileobj(src_file, dst_file, length=65536)
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


class EntityExtractor:
    """Extracts and isolates entity-specific records from raw ERP CSV files using DuckDB."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        config: EntityIsolationConfig,
        paths: MartWorkspacePaths,
    ) -> None:
        self.con = con
        self.config = config
        self.paths = paths

    def extract_source(
        self,
        source_name: str,
        csv_path: Path | str,
        period_spec: str | None = None,
        required_columns: Sequence[str] = (),
    ) -> ExtractionStats:
        """Extract a raw CSV into a staging DuckDB table and Parquet file with strict entity/currency isolation.

        Steps:
        1. Verify file existence and compute SHA-256 hash.
        2. Auto-detect encoding and delimiter.
        3. Load into `raw_<source_name>` with `all_varchar=True` to preserve leading zeros (`000100200301`).
        4. Validate that all `required_columns` (and core filter columns) exist.
        5. Normalize `period` into `_norm_period` (`YYYY-MM`).
        6. Count `total_raw_rows`, `excluded_entity_rows`, `excluded_period_rows`, and `invalid_currency_rows`.
        7. Raise `DwhConfigError` if any entity+period matching row has a disallowed currency (`invalid_currency_rows > 0`).
        8. Persist clean filtered records into `stg_<source_name>` and export to `paths.staging_dir / stg_<source_name>.parquet`.
        """
        clean_source = _sanitize_source_name(source_name)
        source_file = Path(csv_path)
        if not source_file.exists() or not source_file.is_file():
            raise DwhExtractionError(f"Source CSV file not found for {clean_source!r}: {source_file}")
        if source_file.stat().st_size == 0:
            raise DwhExtractionError(f"Source CSV file is empty for {clean_source!r}: {source_file}")

        self.paths.ensure_dirs()

        file_hash = calculate_file_hash(source_file)
        encoding = detect_encoding_precise(str(source_file))
        delimiter = _detect_csv_delimiter(str(source_file), encoding)

        raw_table = f"raw_{clean_source}"
        eval_view = f"_eval_{clean_source}"
        staging_table = f"stg_{clean_source}"
        parquet_path = self.paths.staging_dir / f"{staging_table}.parquet"

        with _utf8_readable_csv(source_file, encoding, self.paths.staging_dir) as readable_csv:
            safe_csv_path = readable_csv.resolve().as_posix()
            self.con.execute(f"DROP VIEW IF EXISTS {_quote_ident(raw_table)};")
            self.con.execute(f"DROP TABLE IF EXISTS {_quote_ident(raw_table)};")
            self.con.execute(
                f"""
                CREATE TEMP TABLE {_quote_ident(raw_table)} AS
                SELECT *
                FROM read_csv(
                    {_quote_literal(safe_csv_path)},
                    delim={_quote_literal(delimiter)},
                    header=True,
                    all_varchar=True
                );
                """
            )

        # Inspect columns of raw_<source_name>
        col_rows = self.con.execute(f"DESCRIBE {_quote_ident(raw_table)};").fetchall()
        raw_columns: list[str] = [str(row[0]) for row in col_rows]

        # Normalize BOM or whitespace in column names if present
        rename_map: dict[str, str] = {}
        cleaned_columns: list[str] = []
        for col in raw_columns:
            clean_col = col.lstrip("\ufeff").strip()
            if clean_col != col:
                rename_map[col] = clean_col
            cleaned_columns.append(clean_col)

        for old_col, new_col in rename_map.items():
            self.con.execute(
                f"ALTER TABLE {_quote_ident(raw_table)} "
                f"RENAME COLUMN {_quote_ident(old_col)} TO {_quote_ident(new_col)};"
            )

        available_cols = set(cleaned_columns)
        must_have = list(dict.fromkeys([*_CORE_FILTER_COLUMNS, *(str(c).strip() for c in required_columns if str(c).strip())]))
        missing_cols = [col for col in must_have if col not in available_cols]
        if missing_cols:
            raise DwhExtractionError(
                f"Source {clean_source!r} ({source_file.name}) is missing required columns: {missing_cols}. "
                f"Available columns: {cleaned_columns}"
            )

        expanded_periods = expand_period_filter(period_spec)
        norm_period_expr = _build_norm_period_sql(_quote_ident("period"))
        entity_expr = f"TRIM(UPPER(COALESCE(CAST({_quote_ident('entity_code')} AS VARCHAR), '')))"
        currency_expr = f"TRIM(UPPER(COALESCE(CAST({_quote_ident('currency_code')} AS VARCHAR), '')))"
        target_entity_lit = _quote_literal(self.config.entity_code.upper())

        allowed_currencies_sql = ", ".join(
            _quote_literal(curr.upper()) for curr in self.config.allowed_currencies
        )
        currency_match_sql = f"({currency_expr} IN ({allowed_currencies_sql}))"
        entity_match_sql = f"({entity_expr} = {target_entity_lit})"

        if expanded_periods is not None:
            allowed_periods_sql = ", ".join(_quote_literal(p) for p in expanded_periods)
            period_match_sql = f"(_norm_period IN ({allowed_periods_sql}))"
        else:
            period_match_sql = "TRUE"

        self.con.execute(f"DROP VIEW IF EXISTS {_quote_ident(eval_view)};")
        self.con.execute(
            f"""
            CREATE TEMP VIEW {_quote_ident(eval_view)} AS
            SELECT
                *,
                {norm_period_expr} AS _norm_period
            FROM {_quote_ident(raw_table)};
            """
        )

        counts_row = self.con.execute(
            f"""
            SELECT
                COUNT(*) AS total_raw_rows,
                COALESCE(SUM(CASE WHEN NOT {entity_match_sql} THEN 1 ELSE 0 END), 0) AS excluded_entity_rows,
                COALESCE(SUM(CASE WHEN {entity_match_sql} AND NOT {period_match_sql} THEN 1 ELSE 0 END), 0) AS excluded_period_rows,
                COALESCE(SUM(CASE WHEN {entity_match_sql} AND {period_match_sql} AND NOT {currency_match_sql} THEN 1 ELSE 0 END), 0) AS invalid_currency_rows,
                COALESCE(SUM(CASE WHEN {entity_match_sql} AND {period_match_sql} AND {currency_match_sql} THEN 1 ELSE 0 END), 0) AS filtered_rows
            FROM {_quote_ident(eval_view)};
            """
        ).fetchone()

        total_raw_rows = int(counts_row[0]) if counts_row else 0
        excluded_entity_rows = int(counts_row[1]) if counts_row else 0
        excluded_period_rows = int(counts_row[2]) if counts_row else 0
        invalid_currency_rows = int(counts_row[3]) if counts_row else 0
        filtered_rows = int(counts_row[4]) if counts_row else 0

        if invalid_currency_rows > 0:
            bad_curr_rows = self.con.execute(
                f"""
                SELECT DISTINCT {currency_expr} AS bad_currency
                FROM {_quote_ident(eval_view)}
                WHERE {entity_match_sql} AND {period_match_sql} AND NOT {currency_match_sql}
                ORDER BY bad_currency;
                """
            ).fetchall()
            bad_currencies = [str(r[0]) for r in bad_curr_rows]
            raise DwhExtractionError(
                f"Currency contamination blocked in source {clean_source!r}: found {invalid_currency_rows} row(s) "
                f"for entity {self.config.entity_code!r} with disallowed currency {bad_currencies}. "
                f"Allowed currencies: {self.config.allowed_currencies}"
            )

        # Check for invalid/unparseable normalized periods in matching rows
        invalid_period_rows = self.con.execute(
            f"""
            SELECT DISTINCT _norm_period
            FROM {_quote_ident(eval_view)}
            WHERE {entity_match_sql}
              AND {period_match_sql}
              AND {currency_match_sql}
              AND NOT regexp_matches(_norm_period, '^[0-9]{{4}}-(0[1-9]|1[0-2])$');
            """
        ).fetchall()
        if invalid_period_rows:
            bad_periods = [str(r[0]) for r in invalid_period_rows]
            raise DwhExtractionError(
                f"Invalid period value(s) {bad_periods} in source {clean_source!r} ({source_file.name}); "
                "expected normalizable YYYY-MM format."
            )

        # Build projection replacing "period" with _norm_period and trimming all VARCHAR columns
        select_exprs: list[str] = []
        for col in cleaned_columns:
            col_ident = _quote_ident(col)
            if col == "period":
                select_exprs.append(f'_norm_period AS {col_ident}')
            elif col in ("entity_code", "currency_code"):
                select_exprs.append(f"UPPER(TRIM(CAST({col_ident} AS VARCHAR))) AS {col_ident}")
            else:
                select_exprs.append(
                    f" CASE WHEN {col_ident} IS NULL THEN NULL ELSE TRIM(CAST({col_ident} AS VARCHAR)) END AS {col_ident}"
                )

        projection_sql = ",\n                    ".join(select_exprs)

        self.con.execute(f"DROP TABLE IF EXISTS {_quote_ident(staging_table)};")
        self.con.execute(
            f"""
            CREATE TABLE {_quote_ident(staging_table)} AS
            SELECT
                {projection_sql}
            FROM {_quote_ident(eval_view)}
            WHERE {entity_match_sql}
              AND {period_match_sql}
              AND {currency_match_sql};
            """
        )

        self.con.execute(f"DROP VIEW IF EXISTS {_quote_ident(eval_view)};")

        safe_parquet_path = parquet_path.resolve().as_posix()
        self.con.execute(
            f"COPY {_quote_ident(staging_table)} TO {_quote_literal(safe_parquet_path)} (FORMAT PARQUET);"
        )

        return ExtractionStats(
            source_name=clean_source,
            source_path=str(source_file.resolve()),
            file_hash=file_hash,
            encoding=encoding,
            delimiter=delimiter,
            total_raw_rows=total_raw_rows,
            filtered_rows=filtered_rows,
            excluded_entity_rows=excluded_entity_rows,
            excluded_period_rows=excluded_period_rows,
            invalid_currency_rows=invalid_currency_rows,
            staging_table=staging_table,
            staging_parquet_path=str(parquet_path.resolve()),
        )
