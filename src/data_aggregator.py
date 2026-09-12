"""High-performance dataset aggregation and slicing engine with no Tkinter dependency."""

from __future__ import annotations

import csv
from datetime import datetime
import decimal
import math
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd
import numpy as np

from src.csv_processing import (
    _EXCEL_MAX_DATA_ROWS,
    ParsedNumber,
    detect_delimiter,
    detect_encoding,
    parse_number,
)

_MONTH_COL_PATTERNS = re.compile(r"(월|month|yyyymm|yearmonth|기간|period|ym)", re.IGNORECASE)
_DIMENSION_COL_PATTERNS = re.compile(r"(코드|code|id|명|name|구분|유형|type|군|group|level|거래선|선|customer|account|업체|모델|model|디비전|사업부|division|제품|품목|category|지역|region|국가|country)", re.IGNORECASE)
_MEASURE_COL_PATTERNS = re.compile(r"(매출|액|금액|수량|qty|이익|손익|원가|cost|비용|마진|margin|단가|price|재료비|변동비|고정비|판관비|차감|리베이트|rebate|profit|cogs)", re.IGNORECASE)


class AggregatorError(Exception):
    """Base exception for dataset aggregator errors."""


class ColumnNotFoundError(AggregatorError):
    """Raised when a required column is not found in the dataset."""


class EmptyResultError(AggregatorError):
    """Raised when filtering or aggregation produces no rows."""


class AggregationCancelledError(AggregatorError):
    """Raised when the aggregation job is cancelled by the user."""


@dataclass(frozen=True)
class FilterCondition:
    column: str
    operator: str  # "==", "!=", "in", "not in", "contains"
    value: Any


@dataclass(frozen=True)
class ColumnGroupRule:
    new_column: str
    source_columns: List[str]


@dataclass(frozen=True)
class DerivedFormulaRule:
    new_column: str
    numerator_column: str
    denominator_column: str
    multiplier: float = 100.0
    format_type: str = "percent"  # "percent", "ratio", "currency", "number"


@dataclass(frozen=True)
class AggregationSpec:
    file_path: str
    group_by_keys: List[str]
    measure_sums: List[str]
    column_groups: List[ColumnGroupRule] = field(default_factory=list)
    derived_formulas: List[DerivedFormulaRule] = field(default_factory=list)
    filters: List[FilterCondition] = field(default_factory=list)
    rollup_annual: bool = False
    month_column: Optional[str] = None
    annual_column_name: str = "연도"
    delimiter: Optional[str] = None
    encoding: Optional[str] = None
    number_mode: str = "English"  # "English" or "Polish"
    output_format: str = "xlsx"  # "xlsx" or "csv"
    output_path: Optional[str] = None
    chunksize: int = 50_000


@dataclass(frozen=True)
class DatasetSchema:
    file_path: str
    columns: List[str]
    dimension_candidates: List[str]
    measure_candidates: List[str]
    detected_month_column: Optional[str]
    sample_values: Dict[str, List[str]]
    total_estimated_rows: Optional[int] = None
    delimiter: str = ","
    encoding: str = "utf-8"


@dataclass(frozen=True)
class AggregationResult:
    out_path: str
    final_rows: int
    coerced_numbers_count: int = 0
    sample_preview_text: str = ""

    def __fspath__(self) -> str:
        return self.out_path

    def __str__(self) -> str:
        return self.out_path

    def endswith(self, suffix: str, *args) -> bool:
        return self.out_path.endswith(suffix, *args)


def _temporary_output_path(out_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(out_path))
    name, ext = os.path.splitext(os.path.basename(out_path))
    return os.path.join(directory, f".{name}.{uuid.uuid4().hex}.tmp{ext}")


def detect_file_encoding(file_path: str) -> str:
    """Detect encoding using the verified detector from csv_processing."""
    try:
        return detect_encoding(file_path)
    except Exception:
        return "utf-8"


def inspect_dataset_schema(
    file_path: str,
    delimiter: Optional[str] = None,
    encoding: Optional[str] = None,
    sample_rows: int = 1000,
) -> DatasetSchema:
    """Fast sampling scan to identify columns, dimension/measure candidates, and sample values."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    enc = encoding or detect_file_encoding(file_path)
    delim = delimiter or detect_delimiter(file_path) or ","

    # Read sample rows using pandas with nrows
    df_sample = pd.read_csv(
        file_path,
        sep=delim,
        encoding=enc,
        nrows=sample_rows,
        dtype=str,
        keep_default_na=False,
    )

    columns = list(df_sample.columns)
    dimension_candidates: List[str] = []
    measure_candidates: List[str] = []
    detected_month: Optional[str] = None
    sample_values: Dict[str, List[str]] = {}

    for col in columns:
        vals = [v.strip() for v in df_sample[col] if v.strip()]
        unique_vals = list(dict.fromkeys(vals))[:20]
        sample_values[col] = unique_vals

        # Check for Month pattern (e.g. YYYYMM like 202601 or column name matching)
        is_month_by_name = bool(_MONTH_COL_PATTERNS.search(col))
        is_month_by_val = False
        if vals:
            first_val = vals[0]
            if len(first_val) == 6 and first_val.isdigit() and (first_val.startswith("20") or first_val.startswith("19")):
                month_num = int(first_val[4:6])
                if 1 <= month_num <= 12:
                    is_month_by_val = True

        if is_month_by_name or is_month_by_val:
            if detected_month is None:
                detected_month = col
            dimension_candidates.append(col)
            continue

        # Check if numeric (measure)
        is_numeric = False
        numeric_count = 0
        test_slice = vals[:50]
        for v in test_slice:
            clean = v.replace(",", "").replace(" ", "")
            try:
                float(clean)
                numeric_count += 1
            except ValueError:
                pass

        if test_slice and (numeric_count / len(test_slice) > 0.8):
            is_numeric = True

        # Decision based on column name and values
        if is_numeric and not _DIMENSION_COL_PATTERNS.search(col):
            measure_candidates.append(col)
        elif _MEASURE_COL_PATTERNS.search(col):
            measure_candidates.append(col)
        else:
            dimension_candidates.append(col)

    # Estimate total rows roughly from file size if possible
    file_size = os.path.getsize(file_path)
    sample_bytes = sum(len(",".join(row).encode(enc, errors="ignore")) for row in df_sample.values[:100])
    avg_row_size = max(1, sample_bytes // max(1, min(100, len(df_sample))))
    estimated_rows = file_size // avg_row_size if avg_row_size > 0 else None

    return DatasetSchema(
        file_path=file_path,
        columns=columns,
        dimension_candidates=dimension_candidates,
        measure_candidates=measure_candidates,
        detected_month_column=detected_month,
        sample_values=sample_values,
        total_estimated_rows=estimated_rows,
        delimiter=delim,
        encoding=enc,
    )


def _apply_filters(df: pd.DataFrame, filters: Sequence[FilterCondition]) -> pd.DataFrame:
    """Filter rows in dataframe safely with whitespace stripping."""
    for cond in filters:
        col = cond.column
        if col not in df.columns:
            continue
        op = cond.operator
        val = cond.value
        series = df[col].astype(str).str.strip()

        if op == "==":
            df = df[series == str(val).strip()]
        elif op == "!=":
            df = df[series != str(val).strip()]
        elif op in ("in", "IN"):
            if isinstance(val, (list, set, tuple)):
                str_set = {str(item).strip() for item in val}
                df = df[series.isin(str_set)]
            else:
                df = df[series == str(val).strip()]
        elif op in ("not in", "NOT IN"):
            if isinstance(val, (list, set, tuple)):
                str_set = {str(item).strip() for item in val}
                df = df[~series.isin(str_set)]
            else:
                df = df[series != str(val).strip()]
        elif op == "contains":
            df = df[series.str.contains(str(val).strip(), na=False, regex=False)]
    return df


def _clean_numeric_series(series: pd.Series, number_mode: str) -> Tuple[pd.Series, int]:
    """Convert raw text numbers to float64 safely respecting locale, returning (series, coerced_count)."""
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0.0).astype(float), 0

    s = series.astype(str).str.strip()
    if number_mode == "Polish":
        s_clean = s.str.replace(" ", "", regex=False).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        s_clean = s.str.replace(",", "", regex=False)

    numeric_vals = pd.to_numeric(s_clean, errors="coerce")
    coerced = (numeric_vals.isna()) & (s != "") & (s != "-") & (s != "nan")
    coerced_count = int(coerced.sum())

    return numeric_vals.fillna(0.0), coerced_count


def aggregate_dataset(
    spec: AggregationSpec,
    progress_callback: Optional[Callable[[int, int | None, str | None], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """
    Execute streaming chunk-based aggregation according to AggregationSpec.
    Returns the absolute path to the generated output file.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise AggregationCancelledError("Aggregation was cancelled before starting.")

    file_path = os.path.abspath(spec.file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Source file not found: {file_path}")

    encoding = spec.encoding or detect_file_encoding(file_path)
    delimiter = spec.delimiter or detect_delimiter(file_path) or ","

    # 1. Determine all required columns for usecols
    needed_cols: Set[str] = set()
    needed_cols.update(spec.group_by_keys)
    needed_cols.update(spec.measure_sums)

    for cond in spec.filters:
        needed_cols.add(cond.column)

    if spec.rollup_annual and spec.month_column:
        needed_cols.add(spec.month_column)

    for grp in spec.column_groups:
        needed_cols.update(grp.source_columns)

    created_columns = {grp.new_column for grp in spec.column_groups}
    for form in spec.derived_formulas:
        if form.numerator_column not in created_columns:
            needed_cols.add(form.numerator_column)
        if form.denominator_column not in created_columns:
            needed_cols.add(form.denominator_column)

    # Validate against header
    with open(file_path, "r", encoding=encoding, errors="replace") as f:
        reader = csv.reader(f, delimiter=delimiter)
        header = next(reader, None)
        if not header:
            raise AggregatorError("File is empty or has no header.")
        if header and header[0].startswith("\ufeff"):
            header[0] = header[0].lstrip("\ufeff")
    
    header_set = set(header)
    missing_cols = [c for c in needed_cols if c not in header_set]
    if missing_cols:
        raise ColumnNotFoundError(f"Columns not found in dataset: {', '.join(missing_cols)}")

    # 2. Setup group keys and measure keys
    effective_group_keys = list(spec.group_by_keys)
    annual_col = spec.annual_column_name
    if spec.rollup_annual and spec.month_column:
        # If month_column was in group_by_keys, replace it with annual_column_name
        if spec.month_column in effective_group_keys:
            idx = effective_group_keys.index(spec.month_column)
            effective_group_keys[idx] = annual_col
        elif annual_col not in effective_group_keys:
            effective_group_keys.insert(0, annual_col)

    # Collect all numeric columns to accumulate
    all_measure_cols: List[str] = list(dict.fromkeys(spec.measure_sums))
    # Also add individual source columns from column_groups if not already in
    for grp in spec.column_groups:
        for sc in grp.source_columns:
            if sc not in all_measure_cols:
                all_measure_cols.append(sc)

    # Ensure formulas' numerator and denominator are also accumulated
    for form in spec.derived_formulas:
        if form.numerator_column not in all_measure_cols and form.numerator_column not in [grp.new_column for grp in spec.column_groups]:
            all_measure_cols.append(form.numerator_column)
        if form.denominator_column not in all_measure_cols and form.denominator_column not in [grp.new_column for grp in spec.column_groups]:
            all_measure_cols.append(form.denominator_column)

    file_size = os.path.getsize(file_path)
    processed_bytes = 0
    total_coerced = 0
    chunk_accumulators: List[pd.DataFrame] = []

    # 3. Read chunks with usecols
    # Convert usecols to list
    usecols_list = list(needed_cols)

    total_chunks = max(1, file_size // (spec.chunksize * 100))  # rough estimation
    chunk_idx = 0

    with pd.read_csv(
        file_path,
        sep=delimiter,
        encoding=encoding,
        usecols=usecols_list,
        chunksize=spec.chunksize,
        dtype=str,
        keep_default_na=False,
    ) as reader:
        for chunk_df in reader:
            if cancel_event is not None and cancel_event.is_set():
                raise AggregationCancelledError("Aggregation was cancelled by user.")

            chunk_idx += 1
            if progress_callback:
                clamped_progress = min(chunk_idx, total_chunks)
                progress_callback(clamped_progress, total_chunks, f"Processing chunk {chunk_idx}...")

            # Apply filters
            if spec.filters:
                chunk_df = _apply_filters(chunk_df, spec.filters)
                if chunk_df.empty:
                    continue

            # Handle annual rollup: slice YYYYMM -> YYYY safely as string
            if spec.rollup_annual and spec.month_column and spec.month_column in chunk_df.columns:
                # Slicing first 4 chars
                chunk_df[annual_col] = chunk_df[spec.month_column].astype(str).str.strip().str.slice(0, 4)

            # Convert measure columns to numeric and track coerced errors
            for mcol in all_measure_cols:
                if mcol in chunk_df.columns:
                    cleaned_s, coerced_cnt = _clean_numeric_series(chunk_df[mcol], spec.number_mode)
                    chunk_df[mcol] = cleaned_s
                    total_coerced += coerced_cnt

            # GroupBy and Sum within this chunk
            grouped_chunk = chunk_df.groupby(effective_group_keys, as_index=False)[all_measure_cols].sum()
            chunk_accumulators.append(grouped_chunk)

            # Periodic memory fold: Merge every 10 chunks to prevent memory buildup
            if len(chunk_accumulators) >= 10:
                fold_df = pd.concat(chunk_accumulators, ignore_index=True)
                folded = fold_df.groupby(effective_group_keys, as_index=False)[all_measure_cols].sum()
                chunk_accumulators = [folded]

    if not chunk_accumulators:
        raise EmptyResultError("No data matched the filter conditions or the dataset was empty.")

    # 4. Final aggregation across all chunks
    if progress_callback:
        progress_callback(total_chunks, total_chunks, "Merging group aggregations...")

    merged_df = pd.concat(chunk_accumulators, ignore_index=True)
    final_df = merged_df.groupby(effective_group_keys, as_index=False)[all_measure_cols].sum()

    # 5. Apply Column Group rules (Summing multiple columns into one)
    for grp in spec.column_groups:
        srcs = [c for c in grp.source_columns if c in final_df.columns]
        if srcs:
            final_df[grp.new_column] = final_df[srcs].sum(axis=1)
        else:
            final_df[grp.new_column] = 0.0

    # 6. Apply Derived Formulas (e.g. Profit Rate = Profit / Sales * 100)
    # Strictly calculated AFTER group summation!
    for form in spec.derived_formulas:
        num_col = form.numerator_column
        den_col = form.denominator_column
        
        num_vals = final_df[num_col] if num_col in final_df.columns else pd.Series(0.0, index=final_df.index)
        den_vals = final_df[den_col] if den_col in final_df.columns else pd.Series(0.0, index=final_df.index)

        # Safe division: where denominator is 0 or NaN, set to 0.0
        with np.errstate(divide="ignore", invalid="ignore"):
            calc = (num_vals / den_vals) * form.multiplier
            calc = np.where(den_vals != 0.0, calc, 0.0)
            final_df[form.new_column] = np.nan_to_num(calc, nan=0.0, posinf=0.0, neginf=0.0)

    # 7. Select and order output columns
    # Order: effective_group_keys + measure_sums + column_groups.new_column + derived_formulas.new_column
    final_output_cols: List[str] = list(effective_group_keys)

    for m in spec.measure_sums:
        if m in final_df.columns and m not in final_output_cols:
            final_output_cols.append(m)

    for grp in spec.column_groups:
        if grp.new_column in final_df.columns and grp.new_column not in final_output_cols:
            final_output_cols.append(grp.new_column)

    for form in spec.derived_formulas:
        if form.new_column in final_df.columns and form.new_column not in final_output_cols:
            final_output_cols.append(form.new_column)

    final_df = final_df[final_output_cols]

    # Check Excel row limits
    if spec.output_format.lower() == "xlsx" and len(final_df) > _EXCEL_MAX_DATA_ROWS:
        raise AggregatorError(
            f"Result has {len(final_df):,} rows, exceeding Excel's limit of {_EXCEL_MAX_DATA_ROWS:,}. "
            f"Please choose CSV format."
        )

    # 8. Determine destination path
    if spec.output_path:
        out_path = os.path.abspath(spec.output_path)
    else:
        dir_name = os.path.dirname(file_path)
        base_name, _ = os.path.splitext(os.path.basename(file_path))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = ".xlsx" if spec.output_format.lower() == "xlsx" else ".csv"
        out_path = os.path.join(dir_name, f"{base_name}_aggregated_{ts}{ext}")

    # 9. Atomic save
    tmp_path = _temporary_output_path(out_path)
    if progress_callback:
        progress_callback(total_chunks, total_chunks, f"Saving result to {os.path.basename(out_path)}...")

    try:
        if spec.output_format.lower() == "xlsx":
            with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
                final_df.to_excel(writer, index=False, sheet_name="Aggregated")
        else:
            final_df.to_csv(tmp_path, index=False, encoding="utf-8-sig")

        # Atomic replace directly
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    preview_text = final_df.head(10).to_string(index=False)
    return AggregationResult(
        out_path=out_path,
        final_rows=len(final_df),
        coerced_numbers_count=total_coerced,
        sample_preview_text=preview_text,
    )


def preview_aggregation(spec: AggregationSpec, sample_rows: int = 2000) -> Tuple[pd.DataFrame, str]:
    """Execute quick aggregation on the top N sample rows for instant UI preview."""
    file_path = os.path.abspath(spec.file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Source file not found: {file_path}")

    encoding = spec.encoding or detect_file_encoding(file_path)
    delimiter = spec.delimiter or detect_delimiter(file_path) or ","

    # Determine needed columns
    needed_cols: Set[str] = set(spec.group_by_keys)
    needed_cols.update(spec.measure_sums)
    for cond in spec.filters:
        needed_cols.add(cond.column)
    if spec.rollup_annual and spec.month_column:
        needed_cols.add(spec.month_column)
    for grp in spec.column_groups:
        needed_cols.update(grp.source_columns)
    created_columns = {grp.new_column for grp in spec.column_groups}
    for form in spec.derived_formulas:
        if form.numerator_column not in created_columns:
            needed_cols.add(form.numerator_column)
        if form.denominator_column not in created_columns:
            needed_cols.add(form.denominator_column)

    sample_df = pd.read_csv(
        file_path,
        sep=delimiter,
        encoding=encoding,
        usecols=list(needed_cols),
        nrows=sample_rows,
        dtype=str,
        keep_default_na=False,
    )

    if spec.filters:
        sample_df = _apply_filters(sample_df, spec.filters)
        if sample_df.empty:
            raise EmptyResultError("미리보기 샘플 내에서 필터 조건에 일치하는 행이 없습니다.")

    effective_group_keys = list(spec.group_by_keys)
    annual_col = spec.annual_column_name
    if spec.rollup_annual and spec.month_column and spec.month_column in sample_df.columns:
        sample_df[annual_col] = sample_df[spec.month_column].astype(str).str.strip().str.slice(0, 4)
        if spec.month_column in effective_group_keys:
            idx = effective_group_keys.index(spec.month_column)
            effective_group_keys[idx] = annual_col
        elif annual_col not in effective_group_keys:
            effective_group_keys.insert(0, annual_col)

    all_measure_cols = list(dict.fromkeys(spec.measure_sums))
    for grp in spec.column_groups:
        for sc in grp.source_columns:
            if sc not in all_measure_cols:
                all_measure_cols.append(sc)

    for mcol in all_measure_cols:
        if mcol in sample_df.columns:
            s_clean, _ = _clean_numeric_series(sample_df[mcol], spec.number_mode)
            sample_df[mcol] = s_clean

    grouped = sample_df.groupby(effective_group_keys, as_index=False)[all_measure_cols].sum()

    for grp in spec.column_groups:
        srcs = [c for c in grp.source_columns if c in grouped.columns]
        grouped[grp.new_column] = grouped[srcs].sum(axis=1) if srcs else 0.0

    for form in spec.derived_formulas:
        num_col, den_col = form.numerator_column, form.denominator_column
        num_vals = grouped[num_col] if num_col in grouped.columns else pd.Series(0.0, index=grouped.index)
        den_vals = grouped[den_col] if den_col in grouped.columns else pd.Series(0.0, index=grouped.index)
        with np.errstate(divide="ignore", invalid="ignore"):
            calc = np.where(den_vals != 0.0, (num_vals / den_vals) * form.multiplier, 0.0)
            grouped[form.new_column] = np.nan_to_num(calc, nan=0.0, posinf=0.0, neginf=0.0)

    final_cols = list(effective_group_keys)
    for m in spec.measure_sums:
        if m in grouped.columns and m not in final_cols:
            final_cols.append(m)
    for grp in spec.column_groups:
        if grp.new_column in grouped.columns and grp.new_column not in final_cols:
            final_cols.append(grp.new_column)
    for form in spec.derived_formulas:
        if form.new_column in grouped.columns and form.new_column not in final_cols:
            final_cols.append(form.new_column)

    res_df = grouped[final_cols].head(15)
    return res_df, res_df.to_string(index=False)
