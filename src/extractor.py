"""Polars-based high-speed data extractor and staging layer builder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import duckdb
import polars as pl

from src.config_loader import ConfigError, DwhSettings


@dataclass
class ExtractionStats:
    """Statistics for an extracted staging source."""
    source_name: str
    source_file: str
    total_raw_rows: int
    filtered_entity_rows: int
    excluded_entity_rows: int
    excluded_currency_rows: int
    staging_parquet_path: str
    staging_table: str


class DataExtractor:
    """Extracts, filters, and standardizes raw CSVs into staging Parquet and DuckDB."""

    def __init__(self, settings: DwhSettings, con: Optional[duckdb.DuckDBPyConnection] = None) -> None:
        self.settings = settings
        self.con = con
        self.settings.ensure_directories()

    def extract_pnl(self, csv_path: Optional[Path | str] = None) -> ExtractionStats:
        """Extract raw P&L, filter for PL entity and allowed currencies, stage to Parquet & DuckDB."""
        path = self._resolve_source_file(csv_path, self.settings.pnl_dir, "raw_pnl_*.csv")
        return self._process_source(
            source_name="pnl",
            csv_path=path,
            table_name="stg_pnl",
            code_columns=["bill_to_code", "model_code"],
        )

    def extract_inventory(self, csv_path: Optional[Path | str] = None) -> ExtractionStats:
        """Extract raw inventory data."""
        path = self._resolve_source_file(csv_path, self.settings.inventory_dir, "*.csv")
        return self._process_source(
            source_name="inventory",
            csv_path=path,
            table_name="stg_inventory",
            code_columns=["warehouse_code", "model_code"],
        )

    def extract_market_price(self, csv_path: Optional[Path | str] = None) -> ExtractionStats:
        """Extract raw market pricing data."""
        path = self._resolve_source_file(csv_path, self.settings.price_dir, "*.csv")
        return self._process_source(
            source_name="market_price",
            csv_path=path,
            table_name="stg_market_price",
            code_columns=["channel_code", "model_code"],
        )

    def extract_all(self) -> Dict[str, ExtractionStats]:
        """Run extraction for all available raw sources."""
        results: Dict[str, ExtractionStats] = {}
        if any(self.settings.pnl_dir.glob("*.csv")):
            results["pnl"] = self.extract_pnl()
        if any(self.settings.inventory_dir.glob("*.csv")):
            results["inventory"] = self.extract_inventory()
        if any(self.settings.price_dir.glob("*.csv")):
            results["market_price"] = self.extract_market_price()
        return results

    def _resolve_source_file(self, explicit_path: Optional[Path | str], directory: Path, pattern: str) -> Path:
        if explicit_path:
            p = Path(explicit_path).resolve()
            if not p.is_file():
                raise ConfigError(f"Raw source file not found: {p}")
            return p

        matches = sorted(list(directory.glob(pattern)))
        if not matches:
            matches = sorted(list(directory.glob("*.csv")))
        if not matches:
            raise ConfigError(f"No source files matching '{pattern}' found in {directory}")
        return matches[-1]

    def _process_source(
        self,
        source_name: str,
        csv_path: Path,
        table_name: str,
        code_columns: List[str],
    ) -> ExtractionStats:
        # Schema override to ensure natural codes keep leading zeros (e.g. 000100200301)
        schema_overrides = {col: pl.String for col in code_columns}
        schema_overrides["period"] = pl.String
        schema_overrides["entity_code"] = pl.String
        schema_overrides["currency_code"] = pl.String

        try:
            df = pl.read_csv(
                csv_path,
                schema_overrides=schema_overrides,
                ignore_errors=False,
                truncate_ragged_lines=True,
            )
        except Exception as e:
            raise ConfigError(f"Failed to read CSV '{csv_path}': {e}") from e

        # Normalize column names: stripped
        df = df.rename({col: col.strip() for col in df.columns})

        # Mandatory schema validation (Fail-Fast before staging)
        required_cols = {"period", "entity_code", "currency_code"}
        if code_columns:
            required_cols.update(code_columns)
        missing_cols = required_cols - set(df.columns)
        if missing_cols:
            raise ConfigError(
                f"Source file '{csv_path}' missing mandatory columns: {sorted(list(missing_cols))}"
            )

        total_raw_rows = len(df)
        if total_raw_rows == 0:
            raise ConfigError(f"Source file '{csv_path}' contains zero rows.")

        # Normalize period (YYYY-MM)
        if "period" in df.columns:
            df = df.with_columns(
                pl.col("period")
                .str.strip_chars()
                .str.replace_all(r"[./]", "-")
                .alias("period")
            )

        # Entity filtering (Zero copy memory optimization)
        if "entity_code" in df.columns:
            df = df.with_columns(pl.col("entity_code").str.strip_chars().str.to_uppercase())
            pre_filter_len = len(df)
            df = df.filter(pl.col("entity_code") == self.settings.entity_code)
            excluded_entity_rows = pre_filter_len - len(df)
        else:
            excluded_entity_rows = 0

        # Currency filtering
        if "currency_code" in df.columns:
            df = df.with_columns(pl.col("currency_code").str.strip_chars().str.to_uppercase())
            invalid_curr_df = df.filter(~pl.col("currency_code").is_in(self.settings.allowed_currencies))
            excluded_currency_rows = len(invalid_curr_df)
            if excluded_currency_rows > 0:
                invalid_currs = invalid_curr_df["currency_code"].unique().to_list()
                raise ConfigError(
                    f"Found {excluded_currency_rows} rows with unsupported currencies {invalid_currs} "
                    f"for entity '{self.settings.entity_code}'. Allowed: {self.settings.allowed_currencies}"
                )
        else:
            excluded_currency_rows = 0

        filtered_entity_rows = len(df)
        if filtered_entity_rows == 0:
            raise ConfigError(
                f"No matching records found for entity '{self.settings.entity_code}' in '{csv_path}'"
            )

        # Export Staging Parquet
        out_parquet = self.settings.staging_dir / f"{table_name}.parquet"
        df.write_parquet(out_parquet, compression="zstd")

        # Load into DuckDB staging schema if connection is provided
        if self.con is not None:
            self.con.execute("CREATE SCHEMA IF NOT EXISTS staging;")
            self.con.execute(
                f"CREATE OR REPLACE TABLE staging.{table_name} AS SELECT * FROM read_parquet(?);",
                [out_parquet.as_posix()],
            )

        return ExtractionStats(
            source_name=source_name,
            source_file=str(csv_path),
            total_raw_rows=total_raw_rows,
            filtered_entity_rows=filtered_entity_rows,
            excluded_entity_rows=excluded_entity_rows,
            excluded_currency_rows=excluded_currency_rows,
            staging_parquet_path=str(out_parquet),
            staging_table=f"staging.{table_name}",
        )
