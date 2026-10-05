"""High-speed dataset profiling and column role inference engine for Data Refinery.

Analyzes CSV sample rows (up to 1,000 rows) using DuckDB and Python CSV parsers
to detect encodings, delimiters, column data types, and auto-suggest dataset roles:
- 'period': Primary calendar period (e.g. YYYY-MM)
- 'key': Unique/compound identifier key (e.g. StoreCode, ProductCode)
- 'numeric': Quantitative measure for aggregation (e.g. Sales, Quantity, Profit)
- 'general': Descriptive dimension or attribute
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import duckdb

from src.csv_processing import detect_encoding, detect_encoding_precise
from src.dataset_engine import make_numeric_sql_expr, normalize_period_value


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str  # 'period', 'numeric', 'integer', 'identifier', 'text'
    sample_values: List[str] = field(default_factory=list)
    suggested_role: str = "general"  # 'period', 'key', 'numeric', 'general'
    confidence: float = 0.5
    reason: str = ""
    null_ratio: float = 0.0
    unique_count: int = 0
    unique_ratio: float = 0.0


@dataclass
class DatasetProfileResult:
    file_path: str
    file_name: str
    encoding: str
    delimiter: str
    total_columns: int
    sample_rows_analyzed: int
    columns: List[ColumnProfile]
    suggested_period_column: Optional[str] = None
    suggested_period_format: str = "Auto"
    suggested_key_columns: List[str] = field(default_factory=list)
    suggested_numeric_columns: List[str] = field(default_factory=list)
    detected_number_format: str = "auto"  # '1,234.56' or '1 234,56'


# Regex patterns for role inference
PERIOD_NAME_RE = re.compile(r"(기준년월|년월|기간|일자|period|month|date|yyyymm|year_month)", re.IGNORECASE)
KEY_NAME_RE = re.compile(r"(코드|code|id|번호|no|key|cd|사번|계정|uuid)$", re.IGNORECASE)
NUMERIC_NAME_RE = re.compile(r"(금액|액|가|매출|이익|원가|수량|단가|실적|예산|비용|amt|amount|qty|quantity|sales|profit|revenue|cost|price|total)", re.IGNORECASE)

EU_NUMBER_RE = re.compile(r"^[-+]?\d{1,3}([ .\u00a0\u202f]\d{3})*(,\d+)?$|^[-+]?\d+(,\d+)?$")
US_NUMBER_RE = re.compile(r"^[-+]?\d{1,3}(,\d{3})*(\.\d+)?$|^[-+]?\d+(\.\d+)?$")


def profile_dataset_sample(
    file_path: Path | str,
    sample_rows: int = 1000,
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> DatasetProfileResult:
    """Analyze the first sample_rows of a CSV file and suggest column roles."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # 1. Detect encoding & delimiter
    enc = encoding or detect_encoding_precise(str(path))
    delim = delimiter or ","

    # Read sample lines in Python
    raw_lines: List[str] = []
    try:
        with open(path, "r", encoding=enc, errors="replace") as f:
            for _ in range(sample_rows + 5):
                line = f.readline()
                if not line:
                    break
                raw_lines.append(line)
    except Exception as e:
        raise ValueError(f"Failed to read file sample with encoding '{enc}': {e}")

    if not raw_lines:
        raise ValueError("File is empty (0 lines).")

    # Detect delimiter if not given
    if not delimiter and len(raw_lines) > 0:
        first_line = raw_lines[0]
        comma_cnt = first_line.count(",")
        semicolon_cnt = first_line.count(";")
        tab_cnt = first_line.count("\t")
        if semicolon_cnt > comma_cnt and semicolon_cnt > tab_cnt:
            delim = ";"
        elif tab_cnt > comma_cnt and tab_cnt > semicolon_cnt:
            delim = "\t"
        else:
            delim = ","

    # Parse CSV in Python
    reader = csv.reader(raw_lines, delimiter=delim)
    header_raw = next(reader, None)
    if not header_raw:
        raise ValueError("CSV file has no header row.")

    header = [c.strip().lstrip("\ufeff") for c in header_raw]
    data_rows: List[List[str]] = []
    for row in reader:
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        # Pad or trim row to match header length
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        elif len(row) > len(header):
            row = row[:len(header)]
        data_rows.append([cell.strip() for cell in row])

    actual_sample_cnt = len(data_rows)
    if actual_sample_cnt == 0:
        # Only header exists
        profiles = [
            ColumnProfile(
                name=col,
                inferred_type="text",
                sample_values=[],
                suggested_role="general",
                confidence=0.1,
                reason="헤더만 존재함",
            )
            for col in header
        ]
        return DatasetProfileResult(
            file_path=str(path),
            file_name=path.name,
            encoding=enc,
            delimiter=delim,
            total_columns=len(header),
            sample_rows_analyzed=0,
            columns=profiles,
        )

    # 2. Transpose into column arrays
    col_values: Dict[str, List[str]] = {col: [] for col in header}
    for row in data_rows:
        for idx, col in enumerate(header):
            col_values[col].append(row[idx])

    # 3. Detect global number format across numeric candidates
    eu_votes = 0
    us_votes = 0
    for col, vals in col_values.items():
        for v in vals:
            if not v:
                continue
            if "," in v and ("." not in v or v.rfind(",") > v.rfind(".")):
                # Has comma as potential decimal
                parts = v.split(",")
                if len(parts) == 2 and 1 <= len(parts[1]) <= 4:
                    eu_votes += 1
            if "." in v and ("," not in v or v.rfind(".") > v.rfind(",")):
                parts = v.split(".")
                if len(parts) == 2 and 1 <= len(parts[1]) <= 4:
                    us_votes += 1

    detected_num_fmt = "1 234,56" if eu_votes > us_votes else "1,234.56"

    # 4. Profile each column
    profiles: List[ColumnProfile] = []
    period_candidates: List[Tuple[str, float]] = []
    key_candidates: List[Tuple[str, float]] = []
    numeric_candidates: List[Tuple[str, float]] = []

    for col in header:
        vals = col_values[col]
        non_empty = [v for v in vals if v]
        null_ratio = 1.0 - (len(non_empty) / actual_sample_cnt) if actual_sample_cnt > 0 else 1.0
        unique_vals = set(non_empty)
        unique_cnt = len(unique_vals)
        unique_ratio = unique_cnt / len(non_empty) if non_empty else 0.0

        sample_preview = list(dict.fromkeys(non_empty))[:3]

        # Analyze types
        is_period = False
        is_numeric = False
        is_identifier = False

        # Check Period
        period_match_cnt = sum(1 for v in non_empty if normalize_period_value(v) is not None)
        period_match_ratio = period_match_cnt / len(non_empty) if non_empty else 0.0

        if period_match_ratio >= 0.8:
            is_period = True

        # Check Numeric (using regex according to detected format)
        num_match_cnt = 0
        has_leading_zero = False
        for v in non_empty:
            cleaned = v.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
            if re.match(r"^0\d+", cleaned):
                has_leading_zero = True
            # Check EU or US
            if EU_NUMBER_RE.match(v) or US_NUMBER_RE.match(v):
                num_match_cnt += 1

        num_match_ratio = num_match_cnt / len(non_empty) if non_empty else 0.0
        if num_match_ratio >= 0.85 and not has_leading_zero:
            is_numeric = True

        # Check Identifier / Code (leading zeros, fixed length, or key naming)
        if (has_leading_zero or KEY_NAME_RE.search(col)) and not is_period:
            is_identifier = True

        # Decide Suggested Role & Confidence
        role = "general"
        confidence = 0.5
        reason = ""
        inferred_type = "text"

        if is_period:
            inferred_type = "period"
            if PERIOD_NAME_RE.search(col):
                role = "period"
                confidence = 0.95
                reason = "컬럼명 및 데이터가 날짜/기간 형식(YYYY-MM)과 일치"
            else:
                role = "period"
                confidence = 0.80
                reason = "샘플 데이터의 80% 이상이 기간 형식"
            period_candidates.append((col, confidence))

        elif is_numeric:
            inferred_type = "numeric"
            if KEY_NAME_RE.search(col):
                # ID or code even though numeric
                role = "key"
                confidence = 0.75
                reason = "숫자 형태이나 컬럼명이 코드/ID 형식이므로 식별 키로 분류"
                key_candidates.append((col, confidence))
            elif NUMERIC_NAME_RE.search(col):
                role = "numeric"
                confidence = 0.95
                reason = "수치 데이터 및 컬럼명이 합산 수치 항목과 일치"
                numeric_candidates.append((col, confidence))
            else:
                role = "numeric"
                confidence = 0.80
                reason = "샘플 데이터의 85% 이상이 숫자"
                numeric_candidates.append((col, confidence))

        elif is_identifier:
            inferred_type = "identifier"
            role = "key"
            confidence = 0.85
            reason = "코드/식별자 형식 (고유값 비율 우수)"
            key_candidates.append((col, confidence))

        else:
            inferred_type = "text"
            role = "general"
            confidence = 0.5
            reason = "일반 속성 텍스트"

        profiles.append(
            ColumnProfile(
                name=col,
                inferred_type=inferred_type,
                sample_values=sample_preview,
                suggested_role=role,
                confidence=round(confidence, 2),
                reason=reason,
                null_ratio=round(null_ratio, 3),
                unique_count=unique_cnt,
                unique_ratio=round(unique_ratio, 3),
            )
        )

    # 5. Resolve single primary period column
    suggested_period_col = None
    if period_candidates:
        # Pick the one with highest confidence, prefer name match
        period_candidates.sort(key=lambda item: (PERIOD_NAME_RE.search(item[0]) is not None, item[1]), reverse=True)
        suggested_period_col = period_candidates[0][0]

    # Ensure other period candidates don't stay as 'period' if only 1 is allowed
    for p in profiles:
        if p.name == suggested_period_col:
            p.suggested_role = "period"
        elif p.suggested_role == "period":
            p.suggested_role = "general"
            p.reason = "추가 기간 열 (일반 속성으로 분류됨)"

    suggested_keys = [col for col, conf in key_candidates if col != suggested_period_col]
    suggested_numerics = [col for col, conf in numeric_candidates if col != suggested_period_col]

    return DatasetProfileResult(
        file_path=str(path),
        file_name=path.name,
        encoding=enc,
        delimiter=delim,
        total_columns=len(header),
        sample_rows_analyzed=actual_sample_cnt,
        columns=profiles,
        suggested_period_column=suggested_period_col,
        suggested_period_format="YYYY-MM" if suggested_period_col else "Auto",
        suggested_key_columns=suggested_keys,
        suggested_numeric_columns=suggested_numerics,
        detected_number_format=detected_num_fmt,
    )


def check_sample_key_uniqueness(
    file_path: Path | str,
    period_col: str,
    key_cols: Sequence[str],
    sample_rows: int = 1000,
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
) -> Tuple[int, int]:
    """Check compound key duplicate count within sample rows.
    
    Returns (duplicate_group_count, total_sample_rows_analyzed).
    Analyzes up to sample_rows using resilient Python CSV parsing.
    """
    path = Path(file_path)
    if not path.exists() or not key_cols:
        return 0, 0

    enc = encoding or detect_encoding_precise(str(path))
    delim = delimiter or ","

    try:
        raw_lines: List[str] = []
        with open(path, "r", encoding=enc, errors="replace") as f:
            for _ in range(sample_rows + 5):
                line = f.readline()
                if not line:
                    break
                raw_lines.append(line)

        if not raw_lines:
            return 0, 0

        # Auto-detect delimiter if not explicitly provided
        if not delimiter and len(raw_lines) > 0:
            first_line = raw_lines[0]
            semicolon_cnt = first_line.count(";")
            comma_cnt = first_line.count(",")
            tab_cnt = first_line.count("\t")
            if semicolon_cnt > comma_cnt and semicolon_cnt > tab_cnt:
                delim = ";"
            elif tab_cnt > comma_cnt and tab_cnt > semicolon_cnt:
                delim = "\t"

        reader = csv.reader(raw_lines, delimiter=delim)
        header_raw = next(reader, None)
        if not header_raw:
            return 0, 0

        header = [c.strip().lstrip("\ufeff") for c in header_raw]
        if period_col not in header:
            return 0, 0

        period_idx = header.index(period_col)
        valid_key_indices = [header.index(k) for k in key_cols if k in header and k != period_col]
        if not valid_key_indices:
            return 0, 0

        from collections import Counter
        key_counter: Counter[Tuple[str, ...]] = Counter()
        total_rows = 0

        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            total_rows += 1
            if total_rows > sample_rows:
                total_rows = sample_rows
                break

            p_val = row[period_idx].strip() if period_idx < len(row) else ""
            if not p_val:
                continue

            k_vals = tuple(row[idx].strip() if idx < len(row) else "" for idx in valid_key_indices)
            compound_key = (p_val,) + k_vals
            key_counter[compound_key] += 1

        dup_cnt = sum(1 for cnt in key_counter.values() if cnt > 1)
        return int(dup_cnt), int(total_rows)
    except Exception:
        return 0, 0

