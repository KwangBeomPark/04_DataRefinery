"""High-performance dataset aggregation and slicing engine with no Tkinter dependency."""

from __future__ import annotations

import csv
from datetime import datetime
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
    detect_delimiter,
    detect_encoding,
)

_MONTH_COL_PATTERNS = re.compile(r"(월|month|yyyymm|yearmonth|기간|period|ym)", re.IGNORECASE)
_DIMENSION_COL_PATTERNS = re.compile(r"(코드|code|id|명|name|구분|유형|type|군|group|level|거래선|선|customer|account|업체|모델|model|디비전|사업부|division|제품|품목|category|지역|region|국가|country)", re.IGNORECASE)
_MEASURE_COL_PATTERNS = re.compile(r"(매출|액|금액|수량|qty|이익|손익|원가|cost|비용|마진|margin|단가|price|재료비|변동비|고정비|판관비|차감|리베이트|rebate|profit|cogs)", re.IGNORECASE)

_AGGREGATED_SHEET_NAME = "Aggregated"
_PERCENT_NUMBER_FORMAT = "0.00%"
_RATIO_NUMBER_FORMAT = "0.000"
_MEASURE_NUMBER_FORMAT = "#,##0.##"
_COUNT_NUMBER_FORMAT = "#,##0"

#: Reductions a value column may use. Anything else falls back to "sum".
AGGREGATION_FUNCTIONS = ("sum", "mean", "count", "min", "max")
_DEFAULT_AGGREGATION_FUNCTION = "sum"

# Scratch columns the chunked fold carries alongside the real measures. The prefix keeps
# them out of the way of any source column name and they never reach the output frame.
_ACCUMULATOR_PREFIX = "__dr_agg__"


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
    output: bool = True  # False = computed as an intermediate value but kept out of the result


@dataclass(frozen=True)
class DerivedFormulaRule:
    """A value column computed as (numerator / denominator) * multiplier after grouping."""

    new_column: str
    numerator_column: str
    denominator_column: str
    multiplier: float = 1.0  # uniform scale factor; a percent column keeps the plain ratio
    format_type: str = "percent"  # "percent", "ratio", "number" -- display format only
    output: bool = True  # False = computed as an intermediate value but kept out of the result


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
    output_order: Optional[List[str]] = None  # explicit value-column order; None = declaration order
    constant_columns: Dict[str, str] = field(default_factory=dict)  # column name -> literal value
    measure_functions: Dict[str, str] = field(default_factory=dict)  # column -> function; absent means "sum"


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


def _output_value_columns(
    spec: "AggregationSpec",
    available: Sequence[str],
    already_used: Sequence[str],
) -> List[str]:
    """Return the value columns to emit, honouring rule output flags and spec.output_order."""
    available_set = set(available)
    taken = set(already_used)

    value_cols: List[str] = []

    def _add(name: str) -> None:
        if name in available_set and name not in taken and name not in value_cols:
            value_cols.append(name)

    for measure in spec.measure_sums:
        _add(measure)
    for grp in spec.column_groups:
        if grp.output:
            _add(grp.new_column)
    for form in spec.derived_formulas:
        if form.output:
            _add(form.new_column)

    if spec.output_order:
        requested = [c for c in spec.output_order if c in value_cols]
        value_cols = requested + [c for c in value_cols if c not in requested]

    return value_cols


def _measure_function(spec: "AggregationSpec", column: str) -> str:
    """Reduction chosen for a value column; an unknown or empty name falls back to "sum"."""
    raw = spec.measure_functions.get(column)
    if raw is None:
        return _DEFAULT_AGGREGATION_FUNCTION
    name = str(raw).strip().lower()
    return name if name in AGGREGATION_FUNCTIONS else _DEFAULT_AGGREGATION_FUNCTION


def _count_measure_columns(spec: "AggregationSpec") -> Set[str]:
    """Value columns reduced with "count": whole numbers, so they get a whole-number format."""
    return {col for col in spec.measure_sums if _measure_function(spec, col) == "count"}


def _read_header(file_path: str, delimiter: str, encoding: str) -> List[str]:
    """Read only the header row, dropping a UTF-8 BOM from the first column name."""
    with open(file_path, "r", encoding=encoding, errors="replace") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        header = next(reader, None)
    if not header:
        raise AggregatorError("File is empty or has no header.")
    if header[0].startswith("\ufeff"):
        header[0] = header[0].lstrip("\ufeff")
    return header


def _validate_constant_columns(spec: "AggregationSpec", header: Sequence[str]) -> None:
    """Reject constant columns that would shadow a source column or the annual rollup column."""
    header_set = set(header)
    clashing = [name for name in spec.constant_columns if name in header_set]
    if clashing:
        raise AggregatorError(
            "Constant columns already exist in the dataset and would shadow real data: "
            f"{', '.join(clashing)}"
        )
    # The annual rollup writes annual_column_name itself, so a constant of the same name would
    # be overwritten by the YYYYMM slice instead of keeping its literal value.
    if spec.rollup_annual and spec.month_column and spec.annual_column_name in spec.constant_columns:
        raise AggregatorError(
            f"Constant column '{spec.annual_column_name}' collides with the annual rollup column. "
            "Rename the constant column or turn the annual rollup off."
        )


def _group_keys_with_constants(spec: "AggregationSpec", group_keys: Sequence[str]) -> List[str]:
    """Group keys plus any constant column that group_by_keys did not already position."""
    keys = list(group_keys)
    for name in spec.constant_columns:
        if name not in keys:
            keys.append(name)
    return keys


def _apply_constant_columns(frame: pd.DataFrame, constants: Dict[str, str]) -> None:
    """Stamp each literal onto every row as text so a code like '007' keeps its leading zero."""
    for name, value in constants.items():
        frame[name] = str(value)


def _temporary_output_path(out_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(out_path))
    name, ext = os.path.splitext(os.path.basename(out_path))
    return os.path.join(directory, f".{name}.{uuid.uuid4().hex}.tmp{ext}")


def _column_number_formats(
    spec: "AggregationSpec",
    output_columns: Sequence[str],
    group_keys: Sequence[str],
) -> Dict[str, str]:
    """Map each output column to its Excel number format (absent = leave as General)."""
    derived_by_name = {form.new_column: form for form in spec.derived_formulas}
    # A constant column is a label too, so it stays General even if it slipped out of group_keys.
    label_columns = set(group_keys) | set(spec.constant_columns)
    count_columns = _count_measure_columns(spec)

    formats: Dict[str, str] = {}
    for col in output_columns:
        if col in label_columns:
            continue  # group keys are labels, not numbers
        form = derived_by_name.get(col)
        if form is None:
            # A count is a row tally, so it never shows a decimal part.
            formats[col] = _COUNT_NUMBER_FORMAT if col in count_columns else _MEASURE_NUMBER_FORMAT
            continue
        if form.format_type == "percent":
            # Excel scales by 100 on display, so the stored ratio 0.2747 reads as 27.47%.
            formats[col] = _PERCENT_NUMBER_FORMAT
        elif form.format_type == "ratio":
            formats[col] = _RATIO_NUMBER_FORMAT
        # "number" (and any unknown type) keeps the General format
    return formats


def _apply_excel_number_formats(
    worksheet: Any,
    output_columns: Sequence[str],
    formats: Dict[str, str],
) -> None:
    """Stamp number formats on the data cells; row 1 holds the header and stays untouched."""
    for position, col in enumerate(output_columns, start=1):
        number_format = formats.get(col)
        if not number_format:
            continue
        for row in worksheet.iter_rows(min_row=2, min_col=position, max_col=position):
            for cell in row:
                cell.number_format = number_format


def _format_preview_cell(value: Any, number_format: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number_format == _PERCENT_NUMBER_FORMAT:
        return f"{number * 100:,.2f}%"
    if number_format == _RATIO_NUMBER_FORMAT:
        return f"{number:,.3f}"
    if number_format == _COUNT_NUMBER_FORMAT:
        return f"{number:,.0f}"
    if number != number:  # NaN
        return str(value)
    return f"{number:,.0f}" if float(number).is_integer() else f"{number:,.2f}"


def _preview_display_frame(
    frame: pd.DataFrame,
    spec: "AggregationSpec",
    group_keys: Sequence[str],
    rows: int,
) -> pd.DataFrame:
    """The head of `frame` with every formatted column replaced by its display string.

    This is the single place the number-format layer is applied to screen output, so the
    preview text and the preview table can never drift apart.
    """
    shown = frame.head(rows).copy()
    formats = _column_number_formats(spec, list(shown.columns), group_keys)
    for column, number_format in formats.items():
        if column in shown.columns:
            shown[column] = shown[column].map(lambda v, fmt=number_format: _format_preview_cell(v, fmt))
    return shown


def _pandas_cell_strings(series: pd.Series) -> List[str]:
    """Render a still-numeric column the way `to_string()` renders it inside the text preview."""
    if series.empty:
        return []
    rendered = series.to_frame().to_string(index=False, header=False)
    return [line.strip() for line in rendered.splitlines()]


def format_preview_rows(
    frame: pd.DataFrame,
    spec: "AggregationSpec",
    group_keys: Sequence[str],
    rows: int = 10,
) -> Tuple[List[str], List[List[str]]]:
    """Column names and per-cell display strings, formatted exactly as the file will be."""
    shown = _preview_display_frame(frame, spec, group_keys, rows)
    headers = [str(column) for column in shown.columns]

    columns_text: List[List[str]] = []
    for column in shown.columns:
        series = shown[column]
        if pd.api.types.is_numeric_dtype(series):
            # No Excel number format applies (a "number" formula, say), so the text preview
            # leaves pandas to render it. Borrow that rendering for the cells too.
            columns_text.append(_pandas_cell_strings(series))
        else:
            columns_text.append(["" if value is None else str(value) for value in series])

    body = [list(cells) for cells in zip(*columns_text)] if columns_text else []
    return headers, body


def format_preview_text(
    frame: pd.DataFrame,
    spec: "AggregationSpec",
    group_keys: Sequence[str],
    rows: int = 10,
) -> str:
    """Render rows the way the saved spreadsheet will display them.

    The file stores a percentage as a ratio and lets Excel's number format do the
    scaling, so a plain `to_string()` would show 0.2747 where the workbook shows
    27.47%. Preview text goes through the same format map as the cells do.
    """
    return _preview_display_frame(frame, spec, group_keys, rows).to_string(index=False)


def default_output_path(file_path: str, output_format: str) -> str:
    """Destination used when AggregationSpec.output_path is not set."""
    source_path = os.path.abspath(file_path)
    dir_name = os.path.dirname(source_path)
    base_name, _ = os.path.splitext(os.path.basename(source_path))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = ".xlsx" if output_format.lower() == "xlsx" else ".csv"
    return os.path.join(dir_name, f"{base_name}_aggregated_{ts}{ext}")


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


def _coerce_numeric_series(series: pd.Series, number_mode: str) -> Tuple[pd.Series, int]:
    """Parse raw text numbers to float64 respecting locale, leaving unusable cells as NaN.

    Keeping the NaN is what lets `count`, `min` and `max` skip blank and unparseable
    cells; the sum path fills them with 0.0 afterwards.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float), 0

    s = series.astype(str).str.strip()
    if number_mode == "Polish":
        s_clean = s.str.replace(" ", "", regex=False).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        s_clean = s.str.replace(",", "", regex=False)

    numeric_vals = pd.to_numeric(s_clean, errors="coerce")
    coerced = (numeric_vals.isna()) & (s != "") & (s != "-") & (s != "nan")
    coerced_count = int(coerced.sum())

    return numeric_vals, coerced_count


@dataclass(frozen=True)
class _Accumulator:
    """One scratch column of the chunked fold, with its two-stage reduction."""

    name: str
    source: str
    prepare: str  # "total" (NaN -> 0), "value" (NaN kept), "present" (1 where the cell holds a number)
    chunk_agg: str
    combine: str


@dataclass(frozen=True)
class _MeasurePlan:
    """How every value column is reduced per chunk and how the partial frames combine."""

    sum_columns: List[str]  # reduced in place under their own name: sum per chunk, then sum
    accumulators: List[_Accumulator]
    direct_columns: Dict[str, str]  # value column -> accumulator already holding the answer
    mean_columns: Dict[str, Tuple[str, str]]  # value column -> (total accumulator, count accumulator)

    @property
    def is_plain_sum(self) -> bool:
        """True when every column is a plain sum, i.e. the pre-`measure_functions` behaviour."""
        return not self.accumulators

    @property
    def source_columns(self) -> List[str]:
        """Source columns to number-clean, in a stable order and without repeats."""
        ordered = list(self.sum_columns)
        for acc in self.accumulators:
            if acc.source not in ordered:
                ordered.append(acc.source)
        return ordered

    def accumulators_for(self, source: str) -> List[_Accumulator]:
        return [acc for acc in self.accumulators if acc.source == source]

    def aggregation_map(self, stage: str) -> Dict[str, str]:
        """Column -> pandas function for the chunk stage or for the combine stage."""
        mapping: Dict[str, str] = {col: "sum" for col in self.sum_columns}
        for acc in self.accumulators:
            mapping[acc.name] = acc.chunk_agg if stage == "chunk" else acc.combine
        return mapping


def _build_measure_plan(
    spec: "AggregationSpec",
    measure_columns: Sequence[str],
    rule_columns: Set[str],
) -> _MeasurePlan:
    """Split the measure columns into plain sums and per-function accumulators.

    A column-group source is always summed even when the same column is a value column
    with another function, so both the group total and the chosen reduction stay correct.
    """
    group_sources = {src for grp in spec.column_groups for src in grp.source_columns}

    sum_columns: List[str] = []
    accumulators: List[_Accumulator] = []
    direct_columns: Dict[str, str] = {}
    mean_columns: Dict[str, Tuple[str, str]] = {}

    def _name(kind: str, column: str) -> str:
        return f"{_ACCUMULATOR_PREFIX}{kind}__{column}"

    functions: Dict[str, str] = {}
    for column in dict.fromkeys(spec.measure_sums):
        if column in rule_columns:
            continue  # a rule builds its own column after grouping; it has no raw values to reduce
        functions[column] = _measure_function(spec, column)

    for column in measure_columns:
        function = functions.get(column, _DEFAULT_AGGREGATION_FUNCTION)
        if function == _DEFAULT_AGGREGATION_FUNCTION or column in group_sources:
            # A group source is summed twice over: the plain sum feeds the column group,
            # the accumulator feeds the value column's own function.
            sum_columns.append(column)

    for column, function in functions.items():
        if function == _DEFAULT_AGGREGATION_FUNCTION:
            continue
        if function == "mean":
            total = _name("sum", column)
            counted = _name("count", column)
            accumulators.append(_Accumulator(total, column, "total", "sum", "sum"))
            accumulators.append(_Accumulator(counted, column, "present", "sum", "sum"))
            mean_columns[column] = (total, counted)
        elif function == "count":
            counted = _name("count", column)
            accumulators.append(_Accumulator(counted, column, "present", "sum", "sum"))
            direct_columns[column] = counted
        else:  # "min" / "max" use the same function on both stages
            name = _name(function, column)
            accumulators.append(_Accumulator(name, column, "value", function, function))
            direct_columns[column] = name

    return _MeasurePlan(
        sum_columns=sum_columns,
        accumulators=accumulators,
        direct_columns=direct_columns,
        mean_columns=mean_columns,
    )


def _accumulator_values(numeric: pd.Series, prepare: str) -> pd.Series:
    """Turn a parsed measure column into the values one accumulator folds."""
    if prepare == "total":
        return numeric.fillna(0.0)
    if prepare == "present":
        return numeric.notna().astype("float64")
    return numeric  # "value": NaN marks a cell that min/max must skip


def _prepare_chunk_measures(
    frame: pd.DataFrame,
    plan: _MeasurePlan,
    number_mode: str,
) -> int:
    """Number-clean each source column once, lay down the scratch columns, return coerced count."""
    sum_set = set(plan.sum_columns)
    coerced_total = 0
    for column in plan.source_columns:
        if column not in frame.columns:
            continue
        numeric, coerced = _coerce_numeric_series(frame[column], number_mode)
        coerced_total += coerced
        for acc in plan.accumulators_for(column):
            frame[acc.name] = _accumulator_values(numeric, acc.prepare)
        if column in sum_set:
            frame[column] = numeric.fillna(0.0)
    return coerced_total


def _fold_group(
    frame: pd.DataFrame,
    group_keys: Sequence[str],
    plan: _MeasurePlan,
    stage: str,
) -> pd.DataFrame:
    """Group one chunk (stage "chunk") or a concatenation of partial frames (stage "combine")."""
    grouped = frame.groupby(list(group_keys), as_index=False)
    if plan.is_plain_sum:
        # Untouched legacy path: same call, same column order, same bytes as before.
        return grouped[plan.sum_columns].sum()
    return grouped.agg(plan.aggregation_map(stage))


def _materialise_measures(frame: pd.DataFrame, plan: _MeasurePlan) -> None:
    """Write each non-sum value column from its accumulators, then drop the scratch columns.

    Runs after the column-group rules so those still see the plain sums, and before the
    derived formulas so a formula may use a mean or a count as an operand.
    """
    if plan.is_plain_sum:
        return

    for column, accumulator in plan.direct_columns.items():
        if accumulator in frame.columns:
            # A group with no usable cell has no min/max; the engine never emits NaN.
            frame[column] = frame[accumulator].fillna(0.0)

    for column, (total_col, count_col) in plan.mean_columns.items():
        if total_col not in frame.columns or count_col not in frame.columns:
            continue
        totals = frame[total_col].to_numpy(dtype=float)
        counts = frame[count_col].to_numpy(dtype=float)
        # Divide the summed totals by the summed counts exactly once, at the very end:
        # a mean of chunk means would be wrong as soon as the chunks differ in size.
        safe_counts = np.where(counts > 0.0, counts, 1.0)
        frame[column] = np.where(counts > 0.0, totals / safe_counts, 0.0)

    scratch = [acc.name for acc in plan.accumulators if acc.name in frame.columns]
    if scratch:
        frame.drop(columns=scratch, inplace=True)


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

    # Columns the engine produces itself are never read from the file: literals
    # are stamped on, and the annual column is derived from the month column.
    constant_names = set(spec.constant_columns)
    needed_cols -= constant_names
    if spec.rollup_annual and spec.month_column:
        needed_cols.discard(spec.annual_column_name)

    # Validate against header
    header = _read_header(file_path, delimiter, encoding)
    _validate_constant_columns(spec, header)

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

    # group_by_keys fixes where a constant column sits; anything left out is appended.
    effective_group_keys = _group_keys_with_constants(spec, effective_group_keys)

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

    if constant_names:
        # A constant is a label: it is never number-cleaned and never summed.
        all_measure_cols = [c for c in all_measure_cols if c not in constant_names]

    rule_columns = created_columns | {form.new_column for form in spec.derived_formulas}
    plan = _build_measure_plan(spec, all_measure_cols, rule_columns)

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

            # Materialise constants before grouping; a constant never changes the granularity.
            if spec.constant_columns:
                _apply_constant_columns(chunk_df, spec.constant_columns)

            # Handle annual rollup: slice YYYYMM -> YYYY safely as string
            if spec.rollup_annual and spec.month_column and spec.month_column in chunk_df.columns:
                # Slicing first 4 chars
                chunk_df[annual_col] = chunk_df[spec.month_column].astype(str).str.strip().str.slice(0, 4)

            # Convert measure columns to numeric, lay down the per-function scratch
            # columns, and track coerced errors
            total_coerced += _prepare_chunk_measures(chunk_df, plan, spec.number_mode)

            # Reduce this chunk with each column's per-chunk function
            grouped_chunk = _fold_group(chunk_df, effective_group_keys, plan, "chunk")
            chunk_accumulators.append(grouped_chunk)

            # Periodic memory fold: Merge every 10 chunks to prevent memory buildup
            if len(chunk_accumulators) >= 10:
                fold_df = pd.concat(chunk_accumulators, ignore_index=True)
                chunk_accumulators = [_fold_group(fold_df, effective_group_keys, plan, "combine")]

    if not chunk_accumulators:
        raise EmptyResultError("No data matched the filter conditions or the dataset was empty.")

    # 4. Final aggregation across all chunks
    if progress_callback:
        progress_callback(total_chunks, total_chunks, "Merging group aggregations...")

    merged_df = pd.concat(chunk_accumulators, ignore_index=True)
    final_df = _fold_group(merged_df, effective_group_keys, plan, "combine")

    # 5. Apply Column Group rules (Summing multiple columns into one)
    # The sources are always the plain sums, whatever function the same column uses
    # as a value column -- the per-function value is written in step 5b, after this.
    for grp in spec.column_groups:
        srcs = [c for c in grp.source_columns if c in final_df.columns]
        if srcs:
            final_df[grp.new_column] = final_df[srcs].sum(axis=1)
        else:
            final_df[grp.new_column] = 0.0

    # 5b. Write mean / count / min / max value columns from their accumulators
    _materialise_measures(final_df, plan)

    # 6. Apply Derived Formulas (e.g. Profit Rate = Profit / Sales, stored as a ratio)
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
    # Order: effective_group_keys + value columns (spec.output_order, else declaration order)
    final_output_cols: List[str] = list(effective_group_keys)
    value_cols = _output_value_columns(spec, final_df.columns, final_output_cols)
    final_output_cols.extend(value_cols)

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
        out_path = default_output_path(file_path, spec.output_format)

    # 9. Atomic save
    tmp_path = _temporary_output_path(out_path)
    if progress_callback:
        progress_callback(total_chunks, total_chunks, f"Saving result to {os.path.basename(out_path)}...")

    try:
        if spec.output_format.lower() == "xlsx":
            with pd.ExcelWriter(tmp_path, engine="openpyxl") as writer:
                final_df.to_excel(writer, index=False, sheet_name=_AGGREGATED_SHEET_NAME)
                _apply_excel_number_formats(
                    writer.book[_AGGREGATED_SHEET_NAME],
                    final_output_cols,
                    _column_number_formats(spec, final_output_cols, effective_group_keys),
                )
        else:
            # CSV has no formatting layer, so it carries the raw values: a percent
            # column lands as 0.2747, the same number the .xlsx cell stores.
            final_df.to_csv(tmp_path, index=False, encoding="utf-8-sig")

        # Atomic replace directly
        os.replace(tmp_path, out_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    preview_text = format_preview_text(final_df, spec, effective_group_keys, rows=10)
    return AggregationResult(
        out_path=out_path,
        final_rows=len(final_df),
        coerced_numbers_count=total_coerced,
        sample_preview_text=preview_text,
    )


def estimate_result_rows(
    spec: AggregationSpec,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    max_distinct: int = 2_000_000,
) -> Tuple[int, bool]:
    """Rows the result will have, by streaming only the grouping columns.

    Returns (count, exact). `exact` is False when max_distinct was reached and
    counting stopped.

    Only the group-by keys, the filter columns and -- under an annual roll-up -- the
    month column are read; no measure is touched and no value is number-cleaned, which
    is what makes this far cheaper than the real run. Constant columns are the same on
    every row, so they change no count and are neither read nor part of the key.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise AggregationCancelledError("Row estimation was cancelled before starting.")

    file_path = os.path.abspath(spec.file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Source file not found: {file_path}")

    encoding = spec.encoding or detect_file_encoding(file_path)
    delimiter = spec.delimiter or detect_delimiter(file_path) or ","

    header = _read_header(file_path, delimiter, encoding)
    _validate_constant_columns(spec, header)

    annual_col = spec.annual_column_name
    rolls_up = bool(spec.rollup_annual and spec.month_column)
    constant_names = set(spec.constant_columns)

    # Mirror aggregate_dataset's key resolution so the two agree row for row.
    effective_group_keys = list(spec.group_by_keys)
    if rolls_up:
        if spec.month_column in effective_group_keys:
            effective_group_keys[effective_group_keys.index(spec.month_column)] = annual_col
        elif annual_col not in effective_group_keys:
            effective_group_keys.insert(0, annual_col)
    effective_group_keys = _group_keys_with_constants(spec, effective_group_keys)

    distinct_keys = [key for key in effective_group_keys if key not in constant_names]
    needed_cols: Set[str] = {key for key in distinct_keys if not (rolls_up and key == annual_col)}
    needed_cols.update(cond.column for cond in spec.filters)
    if rolls_up:
        needed_cols.add(spec.month_column)
    needed_cols -= constant_names

    header_set = set(header)
    missing_cols = [c for c in needed_cols if c not in header_set]
    if missing_cols:
        raise ColumnNotFoundError(f"Columns not found in dataset: {', '.join(missing_cols)}")

    if not needed_cols:
        # Nothing read from the file can split the result: every row folds into one group.
        probe = pd.read_csv(
            file_path,
            sep=delimiter,
            encoding=encoding,
            usecols=[header[0]],
            nrows=1,
            dtype=str,
            keep_default_na=False,
        )
        return (1 if len(probe) else 0), True

    seen: Set[Tuple[str, ...]] = set()
    matched_any = False
    file_size = os.path.getsize(file_path)
    total_chunks = max(1, file_size // (spec.chunksize * 100))  # rough estimation
    chunk_idx = 0

    with pd.read_csv(
        file_path,
        sep=delimiter,
        encoding=encoding,
        usecols=list(needed_cols),
        chunksize=spec.chunksize,
        dtype=str,
        keep_default_na=False,
    ) as reader:
        for chunk_df in reader:
            if cancel_event is not None and cancel_event.is_set():
                raise AggregationCancelledError("Row estimation was cancelled by user.")

            chunk_idx += 1
            if progress_callback:
                clamped_progress = min(chunk_idx, total_chunks)
                progress_callback(clamped_progress, total_chunks, f"Counting chunk {chunk_idx}...")

            if spec.filters:
                chunk_df = _apply_filters(chunk_df, spec.filters)
                if chunk_df.empty:
                    continue

            if rolls_up and spec.month_column in chunk_df.columns:
                chunk_df[annual_col] = chunk_df[spec.month_column].astype(str).str.strip().str.slice(0, 4)

            matched_any = True
            present_keys = [key for key in distinct_keys if key in chunk_df.columns]
            if not present_keys:
                continue  # only constants group the result, so the count stays at one

            for key in chunk_df[present_keys].drop_duplicates().itertuples(index=False, name=None):
                seen.add(key)
                if len(seen) >= max_distinct:
                    return len(seen), False

    if not distinct_keys:
        return (1 if matched_any else 0), True
    return len(seen), True


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

    constant_names = set(spec.constant_columns)
    if constant_names:
        # Constants are stamped on, not read, so keep them out of usecols entirely.
        _validate_constant_columns(spec, _read_header(file_path, delimiter, encoding))
        needed_cols -= constant_names
    if spec.rollup_annual and spec.month_column:
        # The annual column is derived from the month column, never read.
        needed_cols.discard(spec.annual_column_name)

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

    if spec.constant_columns:
        _apply_constant_columns(sample_df, spec.constant_columns)

    effective_group_keys = list(spec.group_by_keys)
    annual_col = spec.annual_column_name
    if spec.rollup_annual and spec.month_column and spec.month_column in sample_df.columns:
        sample_df[annual_col] = sample_df[spec.month_column].astype(str).str.strip().str.slice(0, 4)
        if spec.month_column in effective_group_keys:
            idx = effective_group_keys.index(spec.month_column)
            effective_group_keys[idx] = annual_col
        elif annual_col not in effective_group_keys:
            effective_group_keys.insert(0, annual_col)

    effective_group_keys = _group_keys_with_constants(spec, effective_group_keys)

    all_measure_cols = list(dict.fromkeys(spec.measure_sums))
    for grp in spec.column_groups:
        for sc in grp.source_columns:
            if sc not in all_measure_cols:
                all_measure_cols.append(sc)

    if constant_names:
        all_measure_cols = [c for c in all_measure_cols if c not in constant_names]

    rule_columns = created_columns | {form.new_column for form in spec.derived_formulas}
    plan = _build_measure_plan(spec, all_measure_cols, rule_columns)

    _prepare_chunk_measures(sample_df, plan, spec.number_mode)

    grouped = _fold_group(sample_df, effective_group_keys, plan, "chunk")

    for grp in spec.column_groups:
        srcs = [c for c in grp.source_columns if c in grouped.columns]
        grouped[grp.new_column] = grouped[srcs].sum(axis=1) if srcs else 0.0

    # The sample is read in one piece, so the chunk stage is also the combine stage.
    _materialise_measures(grouped, plan)

    for form in spec.derived_formulas:
        num_col, den_col = form.numerator_column, form.denominator_column
        num_vals = grouped[num_col] if num_col in grouped.columns else pd.Series(0.0, index=grouped.index)
        den_vals = grouped[den_col] if den_col in grouped.columns else pd.Series(0.0, index=grouped.index)
        with np.errstate(divide="ignore", invalid="ignore"):
            calc = np.where(den_vals != 0.0, (num_vals / den_vals) * form.multiplier, 0.0)
            grouped[form.new_column] = np.nan_to_num(calc, nan=0.0, posinf=0.0, neginf=0.0)

    final_cols = list(effective_group_keys)
    final_cols.extend(_output_value_columns(spec, grouped.columns, final_cols))

    res_df = grouped[final_cols].head(15)
    return res_df, format_preview_text(res_df, spec, effective_group_keys, rows=15)
