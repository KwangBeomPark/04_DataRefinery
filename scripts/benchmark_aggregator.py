"""Benchmark script for data aggregator engine with a synthetic dataset.

This benchmark evaluates synthetic dataset generation, schema inspection, and
streaming chunk-based aggregation without modifying or creating persistent files
in the workspace or sample_data directory. All inputs and outputs are isolated
within a TemporaryDirectory and cleaned up automatically on success or failure.

Usage from repository root:
    python scripts/benchmark_aggregator.py
    python scripts/benchmark_aggregator.py --rows 1000
    python scripts/benchmark_aggregator.py --rows 1000 --seed 42
    python -m scripts.benchmark_aggregator
"""

import argparse
import os
import sys
import tempfile
import time
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd

# Ensure repository root is on sys.path when invoked directly from any working directory
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.data_aggregator import (  # noqa: E402
    AggregationSpec,
    ColumnGroupRule,
    DerivedFormulaRule,
    FilterCondition,
    aggregate_dataset,
    inspect_dataset_schema,
)


def run_benchmark(
    n_rows: int = 500_000,
    seed: int = 42,
    chunksize: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute aggregation benchmark with synthetic financial ledger data.

    Parameters:
        n_rows: Number of synthetic rows to generate (default: 500,000).
        seed: Random seed for local NumPy generator (default: 42).
        chunksize: Streaming chunk size for aggregation (default: 100,000).

    Returns:
        Dictionary containing benchmark timings, schema details, result row counts,
        and cleaned-up status/paths.
    """
    print(f"Generating synthetic dataset with {n_rows:,} rows...")
    t0 = time.time()

    months = [f"2026{m:02d}" for m in range(1, 13)]
    divisions = ["TV", "Mobile", "Appliances", "Audio"]
    customers = [f"Customer_{i}" for i in range(1, 25)]
    models = [f"Model_{i}" for i in range(1, 100)]

    # Use a local NumPy random generator to avoid mutating global random state
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "월": rng.choice(months, size=n_rows),
        "디비전": rng.choice(divisions, size=n_rows),
        "거래선": rng.choice(customers, size=n_rows),
        "모델": rng.choice(models, size=n_rows),
        "매출": rng.integers(100, 10000, size=n_rows),
        "비용1": rng.integers(10, 1000, size=n_rows),
        "비용2": rng.integers(10, 500, size=n_rows),
        "비용3": rng.integers(5, 200, size=n_rows),
        "영업이익": rng.integers(-200, 2000, size=n_rows),
    })

    effective_chunksize = chunksize if chunksize is not None else 100_000

    # Execute entirely inside TemporaryDirectory with automatic cleanup on success or failure
    with tempfile.TemporaryDirectory(prefix="benchmark_aggregator_") as tmp_dir:
        benchmark_csv = os.path.join(tmp_dir, "benchmark_ledger.csv")
        output_xlsx = os.path.join(tmp_dir, "benchmark_summary.xlsx")

        df.to_csv(benchmark_csv, index=False, encoding="utf-8-sig")
        gen_elapsed = time.time() - t0
        file_size_mb = os.path.getsize(benchmark_csv) / (1024 * 1024)
        print(f"Generated {benchmark_csv} ({file_size_mb:.1f} MB) in {gen_elapsed:.2f}s")

        # Benchmark Schema Inspection
        t_inspect = time.time()
        schema = inspect_dataset_schema(benchmark_csv)
        inspect_elapsed = time.time() - t_inspect
        print(f"Schema inspection took: {inspect_elapsed:.3f}s")
        print(f"Detected month: {schema.detected_month_column}")
        print(f"Dimensions: {schema.dimension_candidates}")
        print(f"Measures: {schema.measure_candidates}")

        # Benchmark Aggregation (Annual rollup, column grouping, ratio formula, excel export)
        spec = AggregationSpec(
            file_path=benchmark_csv,
            output_path=output_xlsx,
            group_by_keys=["디비전", "모델"],
            measure_sums=["매출", "영업이익"],
            column_groups=[
                ColumnGroupRule(new_column="직접비", source_columns=["비용1", "비용2"]),
            ],
            derived_formulas=[
                DerivedFormulaRule(
                    new_column="이익율(%)",
                    numerator_column="영업이익",
                    denominator_column="매출",
                ),
                DerivedFormulaRule(
                    new_column="직접비율(%)",
                    numerator_column="직접비",
                    denominator_column="매출",
                ),
            ],
            filters=[FilterCondition(column="디비전", operator="in", value=["TV", "Mobile"])],
            rollup_annual=True,
            month_column="월",
            output_format="xlsx",
            chunksize=effective_chunksize,
        )

        t_agg = time.time()
        out_path = aggregate_dataset(spec)
        agg_elapsed = time.time() - t_agg
        out_size_kb = os.path.getsize(str(out_path)) / 1024

        print(f"Aggregation completed in: {agg_elapsed:.2f}s!")
        print(f"Output saved to: {out_path} ({out_size_kb:.1f} KB)")

        # Read back and print summary
        res_df = pd.read_excel(str(out_path))
        result_row_count = len(res_df)
        print(f"Result row count: {result_row_count:,} rows")
        print("Sample rows:")
        print(res_df.head(5))

        # Capture results before exiting context manager; explicitly label paths as cleaned up
        results = {
            "n_rows": n_rows,
            "gen_elapsed": gen_elapsed,
            "inspect_elapsed": inspect_elapsed,
            "agg_elapsed": agg_elapsed,
            "result_rows": result_row_count,
            "schema": schema,
            "cleaned_up": True,
            "cleaned_up_output_path": output_xlsx,
            "cleaned_up_temp_dir": tmp_dir,
        }

    # At this point, the context manager has exited and tmp_dir has been cleaned up
    print("Cleaned up benchmark files.")
    return results


def parse_args(args: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for benchmark invocation."""
    parser = argparse.ArgumentParser(
        description="Benchmark script for data aggregator engine with a synthetic dataset."
    )
    parser.add_argument(
        "--rows",
        "-n",
        type=int,
        default=500_000,
        help="Number of synthetic rows to generate (default: 500,000).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for local NumPy generator (default: 42).",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=None,
        help="Chunksize for streaming aggregation (default: 100,000).",
    )
    return parser.parse_args(args)


if __name__ == "__main__":
    cli_args = parse_args(sys.argv[1:])
    run_benchmark(
        n_rows=cli_args.rows,
        seed=cli_args.seed,
        chunksize=cli_args.chunksize,
    )
