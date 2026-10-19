"""Master-driven Kimball Dimension Builder with SCD Type 1 policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import duckdb
import polars as pl

from src.config_loader import ConfigError, DwhSettings


@dataclass
class DimensionStats:
    """Summary of built dimension tables."""
    dimension_name: str
    total_records: int
    natural_key: str
    surrogate_key: str
    table_name: str


class DimensionBuilder:
    """Builds conformed dimension tables from official master files (SCD Type 1)."""

    def __init__(self, settings: DwhSettings, con: Optional[duckdb.DuckDBPyConnection] = None) -> None:
        self.settings = settings
        self.con = con
        self.settings.ensure_directories()

    def build_dim_customer(self, master_path: Optional[Path | str] = None) -> DimensionStats:
        """Build core.dim_customer from master_customers.csv."""
        path = Path(master_path).resolve() if master_path else self.settings.masters_dir / "master_customers.csv"
        if not path.is_file():
            raise ConfigError(f"Customer master file not found: {path}")

        df = pl.read_csv(
            path,
            schema_overrides={"bill_to_code": pl.String},
            truncate_ragged_lines=True,
        )
        df = df.rename({c: c.strip() for c in df.columns})

        if "bill_to_code" not in df.columns:
            raise ConfigError(f"Customer master missing required column 'bill_to_code': {path}")

        # Natural key deduplication & sort
        df = (
            df.with_columns(pl.col("bill_to_code").str.strip_chars())
            .filter(pl.col("bill_to_code").is_not_null() & (pl.col("bill_to_code") != ""))
            .unique(subset=["bill_to_code"], keep="first")
            .sort("bill_to_code")
        )

        # 1-based integer surrogate key (Int32)
        df = df.with_columns(
            (pl.int_range(1, len(df) + 1, dtype=pl.Int32)).alias("customer_id")
        )

        # Reorder columns with customer_id first
        cols = ["customer_id", "bill_to_code"] + [c for c in df.columns if c not in ("customer_id", "bill_to_code")]
        df = df.select(cols)

        # Parquet export
        out_parquet = self.settings.staging_dir / "dim_customer.parquet"
        df.write_parquet(out_parquet, compression="zstd")

        # DuckDB SCD Type 1 overwrite
        if self.con is not None:
            self.con.execute("CREATE SCHEMA IF NOT EXISTS core;")
            self.con.execute(
                "CREATE OR REPLACE TABLE core.dim_customer AS SELECT * FROM read_parquet(?);",
                [out_parquet.as_posix()],
            )

        return DimensionStats(
            dimension_name="Customer Dimension",
            total_records=len(df),
            natural_key="bill_to_code",
            surrogate_key="customer_id",
            table_name="core.dim_customer",
        )

    def build_dim_product(self, master_path: Optional[Path | str] = None) -> DimensionStats:
        """Build core.dim_product from master_products.csv."""
        path = Path(master_path).resolve() if master_path else self.settings.masters_dir / "master_products.csv"
        if not path.is_file():
            raise ConfigError(f"Product master file not found: {path}")

        df = pl.read_csv(
            path,
            schema_overrides={"model_code": pl.String},
            truncate_ragged_lines=True,
        )
        df = df.rename({c: c.strip() for c in df.columns})

        if "model_code" not in df.columns:
            raise ConfigError(f"Product master missing required column 'model_code': {path}")

        df = (
            df.with_columns(pl.col("model_code").str.strip_chars())
            .filter(pl.col("model_code").is_not_null() & (pl.col("model_code") != ""))
            .unique(subset=["model_code"], keep="first")
            .sort("model_code")
        )

        # 1-based integer surrogate key (Int32)
        df = df.with_columns(
            (pl.int_range(1, len(df) + 1, dtype=pl.Int32)).alias("product_id")
        )

        cols = ["product_id", "model_code"] + [c for c in df.columns if c not in ("product_id", "model_code")]
        df = df.select(cols)

        out_parquet = self.settings.staging_dir / "dim_product.parquet"
        df.write_parquet(out_parquet, compression="zstd")

        if self.con is not None:
            self.con.execute("CREATE SCHEMA IF NOT EXISTS core;")
            self.con.execute(
                "CREATE OR REPLACE TABLE core.dim_product AS SELECT * FROM read_parquet(?);",
                [out_parquet.as_posix()],
            )

        return DimensionStats(
            dimension_name="Product Dimension",
            total_records=len(df),
            natural_key="model_code",
            surrogate_key="product_id",
            table_name="core.dim_product",
        )

    def build_dim_date(self, periods: Optional[List[str]] = None) -> DimensionStats:
        """Build core.dim_date conformed calendar dimension."""
        if not periods:
            # Default to standard quarters for 2025-2027
            periods = [f"{y}-{m:02d}" for y in range(2025, 2028) for m in range(1, 13)]

        date_records = []
        for p in sorted(list(set(periods))):
            parts = p.split("-")
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 1
            quarter = f"{year}-Q{(month - 1) // 3 + 1}"
            date_id = year * 100 + month
            date_records.append({
                "date_id": date_id,
                "period": p,
                "year": year,
                "quarter": quarter,
                "month": month,
            })

        df = pl.DataFrame(date_records)
        df = df.with_columns(pl.col("date_id").cast(pl.Int32))

        out_parquet = self.settings.staging_dir / "dim_date.parquet"
        df.write_parquet(out_parquet, compression="zstd")

        if self.con is not None:
            self.con.execute("CREATE SCHEMA IF NOT EXISTS core;")
            self.con.execute(
                "CREATE OR REPLACE TABLE core.dim_date AS SELECT * FROM read_parquet(?);",
                [out_parquet.as_posix()],
            )

        return DimensionStats(
            dimension_name="Date Dimension",
            total_records=len(df),
            natural_key="period",
            surrogate_key="date_id",
            table_name="core.dim_date",
        )

    def build_all(self, periods: Optional[List[str]] = None) -> Dict[str, DimensionStats]:
        """Build all dimensions."""
        return {
            "dim_customer": self.build_dim_customer(),
            "dim_product": self.build_dim_product(),
            "dim_date": self.build_dim_date(periods=periods),
        }
