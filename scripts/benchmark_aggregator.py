"""Benchmark script for data aggregator engine with a 100MB+ dataset."""

import os
import sys
import time
import pandas as pd
import numpy as np

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.data_aggregator import (
    AggregationSpec,
    ColumnGroupRule,
    DerivedFormulaRule,
    FilterCondition,
    aggregate_dataset,
    inspect_dataset_schema,
)

def run_benchmark():
    benchmark_csv = "sample_data/benchmark_ledger.csv"
    os.makedirs("sample_data", exist_ok=True)

    n_rows = 500_000
    print(f"Generating synthetic dataset with {n_rows:,} rows...")
    t0 = time.time()

    months = [f"2026{m:02d}" for m in range(1, 13)]
    divisions = ["TV", "Mobile", "Appliances", "Audio"]
    customers = [f"Customer_{i}" for i in range(1, 25)]
    models = [f"Model_{i}" for i in range(1, 100)]

    np.random.seed(42)
    df = pd.DataFrame({
        "월": np.random.choice(months, size=n_rows),
        "디비전": np.random.choice(divisions, size=n_rows),
        "거래선": np.random.choice(customers, size=n_rows),
        "모델": np.random.choice(models, size=n_rows),
        "매출": np.random.randint(100, 10000, size=n_rows),
        "비용1": np.random.randint(10, 1000, size=n_rows),
        "비용2": np.random.randint(10, 500, size=n_rows),
        "비용3": np.random.randint(5, 200, size=n_rows),
        "영업이익": np.random.randint(-200, 2000, size=n_rows),
    })

    df.to_csv(benchmark_csv, index=False, encoding="utf-8-sig")
    file_size_mb = os.path.getsize(benchmark_csv) / (1024 * 1024)
    print(f"Generated {benchmark_csv} ({file_size_mb:.1f} MB) in {time.time() - t0:.2f}s")

    # Benchmark Schema Inspection
    t_inspect = time.time()
    schema = inspect_dataset_schema(benchmark_csv)
    print(f"Schema inspection took: {time.time() - t_inspect:.3f}s")
    print(f"Detected month: {schema.detected_month_column}")
    print(f"Dimensions: {schema.dimension_candidates}")
    print(f"Measures: {schema.measure_candidates}")

    # Benchmark Aggregation (Annual rollup, column grouping, ratio formula, excel export)
    spec = AggregationSpec(
        file_path=benchmark_csv,
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
        chunksize=100_000,
    )

    t_agg = time.time()
    out_path = aggregate_dataset(spec)
    agg_elapsed = time.time() - t_agg
    out_size_kb = os.path.getsize(out_path) / 1024

    print(f"Aggregation completed in: {agg_elapsed:.2f}s!")
    print(f"Output saved to: {out_path} ({out_size_kb:.1f} KB)")

    # Read back and print summary
    res_df = pd.read_excel(out_path)
    print(f"Result row count: {len(res_df):,} rows")
    print("Sample rows:")
    print(res_df.head(5))

    # Clean up benchmark file
    if os.path.exists(benchmark_csv):
        os.remove(benchmark_csv)
    if os.path.exists(out_path):
        os.remove(out_path)
    print("Cleaned up benchmark files.")

if __name__ == "__main__":
    run_benchmark()
