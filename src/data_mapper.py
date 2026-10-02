"""High-performance rule-based dataset category mapping engine with no Tkinter dependency."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.csv_processing import (
    _EXCEL_MAX_DATA_ROWS,
    detect_delimiter,
    detect_encoding,
)

# Supported condition operators
OP_EQUALS = "=="
OP_NOT_EQUALS = "!="
OP_CONTAINS = "contains"
OP_NOT_CONTAINS = "not_contains"
OP_STARTSWITH = "startswith"
OP_ENDSWITH = "endswith"
OP_GREATER = ">"
OP_GREATER_EQUAL = ">="
OP_LESS = "<"
OP_LESS_EQUAL = "<="
OP_IN = "in"
OP_NOT_IN = "not_in"
OP_IS_NULL = "is_null"
OP_IS_NOT_NULL = "is_not_null"
OP_REGEX = "regex"

SUPPORTED_OPERATORS = (
    OP_EQUALS,
    OP_NOT_EQUALS,
    OP_CONTAINS,
    OP_NOT_CONTAINS,
    OP_STARTSWITH,
    OP_ENDSWITH,
    OP_GREATER,
    OP_GREATER_EQUAL,
    OP_LESS,
    OP_LESS_EQUAL,
    OP_IN,
    OP_NOT_IN,
    OP_IS_NULL,
    OP_IS_NOT_NULL,
    OP_REGEX,
)


class MapperError(Exception):
    """Base exception for data mapper errors."""


class MapperColumnNotFoundError(MapperError):
    """Raised when a condition refers to a column not found in the dataset."""


class MapperInvalidRuleError(MapperError):
    """Raised when a mapping rule or condition definition is malformed."""


class MapperEmptyDatasetError(MapperError):
    """Raised when the input file has no readable data rows."""


class MapperCancelledError(MapperError):
    """Raised when a mapping background job is cancelled by the user."""


@dataclass(frozen=True)
class RuleCondition:
    """A single conditional test applied to a specific column."""

    column: str
    operator: str
    value: Any = None
    case_sensitive: bool = False

    def __post_init__(self):
        if not self.column or not str(self.column).strip():
            raise MapperInvalidRuleError("컬럼명이 지정되지 않았습니다.")
        if self.operator not in SUPPORTED_OPERATORS:
            raise MapperInvalidRuleError(f"지원하지 않는 연산자입니다: {self.operator}")


@dataclass
class MappingRule:
    """A business mapping rule assigning a target category when conditions match."""

    rule_id: str
    name: str
    target_value: str
    conditions: list[RuleCondition] = field(default_factory=list)
    combine_operator: str = "AND"  # "AND" or "OR"
    enabled: bool = True

    def __post_init__(self):
        if not self.rule_id:
            self.rule_id = str(uuid.uuid4())[:8]
        if not self.target_value:
            raise MapperInvalidRuleError("할당할 카테고리/라벨 값이 비어 있습니다.")
        if self.combine_operator not in ("AND", "OR"):
            raise MapperInvalidRuleError(f"지원하지 않는 조건 결합 연산자입니다: {self.combine_operator}")


@dataclass(frozen=True)
class RuleMatchStat:
    """Execution statistics for an individual mapping rule."""

    rule_id: str
    rule_name: str
    target_value: str
    matched_count: int
    matched_percent: float


@dataclass(frozen=True)
class MappingResult:
    """Result summary of a completed mapping execution."""

    output_path: str
    total_rows: int
    mapped_rows: int
    unmapped_rows: int
    rule_stats: list[RuleMatchStat]
    unmapped_top_values: dict[str, list[tuple[Any, int]]] = field(default_factory=dict)

    @property
    def mapped_percent(self) -> float:
        return (self.mapped_rows / self.total_rows * 100.0) if self.total_rows > 0 else 0.0

    @property
    def unmapped_percent(self) -> float:
        return (self.unmapped_rows / self.total_rows * 100.0) if self.total_rows > 0 else 0.0


@dataclass(frozen=True)
class MappingSpec:
    """Full declarative specification of a data mapping job."""

    file_path: str
    target_column: str
    rules: list[MappingRule]
    default_value: str = "미분류"
    delimiter: str | None = None
    encoding: str | None = None
    output_format: str = "csv"  # "csv" or "xlsx"
    output_path: str | None = None
    export_unmapped_only: bool = False
    chunksize: int = 50_000

    def __post_init__(self):
        if not self.file_path or not os.path.isfile(self.file_path):
            raise MapperError(f"입력 파일을 찾을 수 없습니다: {self.file_path}")
        if not self.target_column or not str(self.target_column).strip():
            raise MapperInvalidRuleError("결과 카테고리 컬럼명이 지정되지 않았습니다.")


def default_mapping_output_path(source_path: str, output_format: str, is_unmapped_only: bool = False) -> str:
    """Returns the default atomic destination beside the source file with a timestamp."""
    p = Path(source_path) if isinstance(source_path, Path) else Path(str(source_path))
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M")
    suffix = f"_mapped_{timestamp}" if not is_unmapped_only else f"_unmapped_{timestamp}"
    ext = f".{output_format.lower().lstrip('.')}"
    return str(p.parent / f"{p.stem}{suffix}{ext}")


def _staging_path(final_path: str) -> str:
    """Generates an atomic temporary staging path in the target directory."""
    dirname, basename = os.path.split(final_path)
    return os.path.join(dirname, f".dr_tmp_{uuid.uuid4().hex}_{basename}")


def evaluate_condition(series: pd.Series, condition: RuleCondition) -> pd.Series:
    """Evaluates a single RuleCondition against a pandas Series using fast vectorization."""
    op = condition.operator
    val = condition.value

    # Null checks
    if op == OP_IS_NULL:
        return series.isna() | (series.astype(str).str.strip() == "")
    if op == OP_IS_NOT_NULL:
        return series.notna() & (series.astype(str).str.strip() != "")

    # Equality & inequality
    if op == OP_EQUALS:
        if condition.case_sensitive:
            return series.astype(str) == str(val)
        return series.astype(str).str.casefold() == str(val).casefold()

    if op == OP_NOT_EQUALS:
        if condition.case_sensitive:
            return series.astype(str) != str(val)
        return series.astype(str).str.casefold() != str(val).casefold()

    # Substring checks
    if op == OP_CONTAINS:
        return series.astype(str).str.contains(
            re.escape(str(val)),
            case=condition.case_sensitive,
            na=False,
            regex=True,
        )

    if op == OP_NOT_CONTAINS:
        return ~series.astype(str).str.contains(
            re.escape(str(val)),
            case=condition.case_sensitive,
            na=False,
            regex=True,
        )

    if op == OP_STARTSWITH:
        if condition.case_sensitive:
            return series.astype(str).str.startswith(str(val), na=False)
        return series.astype(str).str.casefold().str.startswith(str(val).casefold(), na=False)

    if op == OP_ENDSWITH:
        if condition.case_sensitive:
            return series.astype(str).str.endswith(str(val), na=False)
        return series.astype(str).str.casefold().str.endswith(str(val).casefold(), na=False)

    # In / Not In (comma or list support)
    if op in (OP_IN, OP_NOT_IN):
        if isinstance(val, (list, tuple, set)):
            val_items = [str(x) for x in val]
        else:
            val_items = [x.strip() for x in str(val).split(",") if x.strip()]

        if not condition.case_sensitive:
            lookup_set = {x.casefold() for x in val_items}
            mask = series.astype(str).str.casefold().isin(lookup_set)
        else:
            lookup_set = set(val_items)
            mask = series.astype(str).isin(lookup_set)

        return mask if op == OP_IN else ~mask

    # Numeric comparisons
    if op in (OP_GREATER, OP_GREATER_EQUAL, OP_LESS, OP_LESS_EQUAL):
        try:
            num_val = float(val)
        except (ValueError, TypeError):
            raise MapperInvalidRuleError(f"수치 비교를 위한 유효한 숫자가 아닙니다: {val}")

        num_series = pd.to_numeric(series, errors="coerce")
        if op == OP_GREATER:
            return num_series > num_val
        if op == OP_GREATER_EQUAL:
            return num_series >= num_val
        if op == OP_LESS:
            return num_series < num_val
        if op == OP_LESS_EQUAL:
            return num_series <= num_val

    # Regex
    if op == OP_REGEX:
        flags = 0 if condition.case_sensitive else re.IGNORECASE
        return series.astype(str).str.contains(str(val), flags=flags, na=False, regex=True)

    raise MapperInvalidRuleError(f"알 수 없는 연산자입니다: {op}")


def evaluate_rule(df: pd.DataFrame, rule: MappingRule) -> pd.Series:
    """Evaluates a single MappingRule containing multiple conditions combined via AND/OR."""
    if not rule.enabled or not rule.conditions:
        return pd.Series(False, index=df.index)

    combined_mask: pd.Series | None = None

    for cond in rule.conditions:
        if cond.column not in df.columns:
            raise MapperColumnNotFoundError(f"데이터셋에 '{cond.column}' 컬럼이 존재하지 않습니다.")

        cond_mask = evaluate_condition(df[cond.column], cond)

        if combined_mask is None:
            combined_mask = cond_mask
        else:
            if rule.combine_operator == "AND":
                combined_mask = combined_mask & cond_mask
            else:
                combined_mask = combined_mask | cond_mask

    return combined_mask if combined_mask is not None else pd.Series(False, index=df.index)


def apply_mapping_to_dataframe(
    df: pd.DataFrame,
    rules: Sequence[MappingRule],
    target_column: str,
    default_value: str = "미분류",
) -> tuple[pd.DataFrame, list[dict[str, Any]], pd.Series]:
    """Applies a sequence of mapping rules (Waterfall / Cascade) to a pandas DataFrame in-place.

    Returns:
        (df_with_target, rule_counts, unassigned_mask)
    """
    unassigned_mask = pd.Series(True, index=df.index)
    target_series = pd.Series(default_value, index=df.index, dtype=object)

    stats: list[dict[str, Any]] = []

    for rule in rules:
        if not rule.enabled or not rule.conditions:
            continue

        # Evaluate only against yet unassigned rows
        unassigned_subset = df[unassigned_mask]
        if unassigned_subset.empty:
            stats.append({
                "rule_id": rule.rule_id,
                "rule_name": rule.name,
                "target_value": rule.target_value,
                "matched_count": 0,
            })
            continue

        raw_match = evaluate_rule(unassigned_subset, rule)
        matched_indices = unassigned_subset.index[raw_match]
        matched_count = len(matched_indices)

        if matched_count > 0:
            target_series.loc[matched_indices] = rule.target_value
            unassigned_mask.loc[matched_indices] = False

        stats.append({
            "rule_id": rule.rule_id,
            "rule_name": rule.name,
            "target_value": rule.target_value,
            "matched_count": matched_count,
        })

    df[target_column] = target_series
    return df, stats, unassigned_mask


def analyze_unmapped_values(
    df: pd.DataFrame,
    unmapped_mask: pd.Series,
    candidate_columns: Sequence[str] | None = None,
    top_n: int = 5,
) -> dict[str, list[tuple[Any, int]]]:
    """Inspects frequent distinct values among unmapped rows to recommend new mapping rules."""
    unmapped_df = df[unmapped_mask]
    if unmapped_df.empty:
        return {}

    cols = candidate_columns if candidate_columns else list(unmapped_df.columns[:10])
    results: dict[str, list[tuple[Any, int]]] = {}

    for c in cols:
        if c in unmapped_df.columns:
            vc = unmapped_df[c].dropna().astype(str).value_counts().head(top_n)
            results[c] = list(vc.items())

    return results


def preview_mapping(
    spec: MappingSpec,
    preview_rows: int = 2000,
) -> tuple[pd.DataFrame, list[RuleMatchStat], dict[str, list[tuple[Any, int]]]]:
    """Generates a fast sample preview of the mapping result without scanning the entire file."""
    enc = spec.encoding or detect_encoding(spec.file_path)
    delim = spec.delimiter or detect_delimiter(spec.file_path)

    # Read preview rows
    sample_df = pd.read_csv(
        spec.file_path,
        sep=delim,
        encoding=enc,
        nrows=preview_rows,
        low_memory=False,
    )

    if sample_df.empty:
        raise MapperEmptyDatasetError("미리보기를 생성할 데이터가 비어 있습니다.")

    mapped_df, raw_stats, unmapped_mask = apply_mapping_to_dataframe(
        sample_df.copy(),
        spec.rules,
        spec.target_column,
        spec.default_value,
    )

    total_sample = len(sample_df)
    rule_stats = [
        RuleMatchStat(
            rule_id=s["rule_id"],
            rule_name=s["rule_name"],
            target_value=s["target_value"],
            matched_count=s["matched_count"],
            matched_percent=(s["matched_count"] / total_sample * 100.0) if total_sample > 0 else 0.0,
        )
        for s in raw_stats
    ]

    unmapped_top = analyze_unmapped_values(mapped_df, unmapped_mask, top_n=5)
    return mapped_df, rule_stats, unmapped_top


def execute_mapping(
    spec: MappingSpec,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> MappingResult:
    """Executes full dataset mapping using chunked streaming for constant low-memory footprint.

    Guarantees atomic file commit and safe rollback on cancellation or error.
    """
    final_output_path = spec.output_path or default_mapping_output_path(
        spec.file_path, spec.output_format, spec.export_unmapped_only
    )
    staging_file = _staging_path(final_output_path)

    enc = spec.encoding or detect_encoding(spec.file_path)
    delim = spec.delimiter or detect_delimiter(spec.file_path)

    rule_accum_counts: dict[str, int] = {r.rule_id: 0 for r in spec.rules}
    total_processed_rows = 0
    total_unmapped_rows = 0

    # Unmapped frequency aggregator for the entire file
    unmapped_candidate_cols = [c for r in spec.rules for c in [cond.column for cond in r.conditions]]
    unmapped_distinct_tracker: dict[str, dict[str, int]] = {c: {} for c in set(unmapped_candidate_cols)}

    is_excel = spec.output_format.lower() in ("xlsx", "excel")

    try:
        reader = pd.read_csv(
            spec.file_path,
            sep=delim,
            encoding=enc,
            chunksize=spec.chunksize,
            low_memory=False,
        )

        first_chunk = True
        accumulated_excel_dfs: list[pd.DataFrame] = []

        for chunk in reader:
            if cancel_event and cancel_event.is_set():
                raise MapperCancelledError("사용자에 의해 매핑 작업이 취소되었습니다.")

            mapped_chunk, stats, unmapped_mask = apply_mapping_to_dataframe(
                chunk,
                spec.rules,
                spec.target_column,
                spec.default_value,
            )

            # Accumulate rule stats
            for s in stats:
                rule_accum_counts[s["rule_id"]] += s["matched_count"]

            chunk_len = len(chunk)
            chunk_unmapped_count = int(unmapped_mask.sum())
            total_processed_rows += chunk_len
            total_unmapped_rows += chunk_unmapped_count

            # Track unmapped values for diagnosis
            if chunk_unmapped_count > 0:
                unmapped_sub = mapped_chunk[unmapped_mask]
                for col, target_dict in unmapped_distinct_tracker.items():
                    if col in unmapped_sub.columns:
                        vc = unmapped_sub[col].dropna().astype(str).value_counts().head(20)
                        for val, cnt in vc.items():
                            target_dict[val] = target_dict.get(val, 0) + int(cnt)

            # Filter if unmapped export only
            export_chunk = mapped_chunk[unmapped_mask] if spec.export_unmapped_only else mapped_chunk

            if is_excel:
                # Accumulate for excel (enforcing _EXCEL_MAX_DATA_ROWS)
                accumulated_excel_dfs.append(export_chunk)
                current_acc_rows = sum(len(d) for d in accumulated_excel_dfs)
                if current_acc_rows > _EXCEL_MAX_DATA_ROWS:
                    raise MapperError(
                        f"엑셀 형식 출력은 최대 {_EXCEL_MAX_DATA_ROWS:,}행까지만 지원합니다. "
                        f"현재 행 수가 초과되었으므로 CSV 형식을 선택해 주세요."
                    )
            else:
                # Stream write to CSV
                export_chunk.to_csv(
                    staging_file,
                    mode="w" if first_chunk else "a",
                    index=False,
                    header=first_chunk,
                    encoding="utf-8-sig",
                )

            first_chunk = False
            if progress_callback:
                progress_callback(total_processed_rows, total_unmapped_rows)

        if total_processed_rows == 0:
            raise MapperEmptyDatasetError("처리할 데이터가 비어 있습니다.")

        # Finalize Excel if applicable
        if is_excel:
            if accumulated_excel_dfs:
                final_excel_df = pd.concat(accumulated_excel_dfs, ignore_index=True)
            else:
                final_excel_df = pd.DataFrame()

            final_excel_df.to_excel(
                staging_file,
                sheet_name="Mapped_Data",
                index=False,
                engine="openpyxl",
            )

        # Atomic commit
        os.replace(staging_file, final_output_path)

    except Exception:
        # Clean up staging file on failure or cancel
        if os.path.exists(staging_file):
            try:
                os.remove(staging_file)
            except OSError:
                pass
        raise

    # Package stats
    final_stats: list[RuleMatchStat] = []
    rule_lookup = {r.rule_id: r for r in spec.rules}

    for r_id, count in rule_accum_counts.items():
        r = rule_lookup.get(r_id)
        if r:
            final_stats.append(
                RuleMatchStat(
                    rule_id=r_id,
                    rule_name=r.name,
                    target_value=r.target_value,
                    matched_count=count,
                    matched_percent=(count / total_processed_rows * 100.0) if total_processed_rows > 0 else 0.0,
                )
            )

    # Format top unmapped values
    unmapped_top_summary: dict[str, list[tuple[Any, int]]] = {}
    for col, val_counts in unmapped_distinct_tracker.items():
        sorted_pairs = sorted(val_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        if sorted_pairs:
            unmapped_top_summary[col] = sorted_pairs

    return MappingResult(
        output_path=final_output_path,
        total_rows=total_processed_rows,
        mapped_rows=total_processed_rows - total_unmapped_rows,
        unmapped_rows=total_unmapped_rows,
        rule_stats=final_stats,
        unmapped_top_values=unmapped_top_summary,
    )


def rules_to_dict_list(rules: Sequence[MappingRule]) -> list[dict[str, Any]]:
    """Serializes a list of MappingRule instances to a list of primitive dictionaries."""
    data = []
    for r in rules:
        rule_dict = {
            "rule_id": r.rule_id,
            "name": r.name,
            "target_value": r.target_value,
            "combine_operator": r.combine_operator,
            "enabled": r.enabled,
            "conditions": [
                {
                    "column": c.column,
                    "operator": c.operator,
                    "value": c.value,
                    "case_sensitive": c.case_sensitive,
                }
                for c in r.conditions
            ],
        }
        data.append(rule_dict)
    return data


def dict_list_to_rules(data: Sequence[dict[str, Any]]) -> list[MappingRule]:
    """Deserializes a list of primitive dictionaries into MappingRule instances."""
    rules = []
    for item in data:
        conds = [
            RuleCondition(
                column=c.get("column", ""),
                operator=c.get("operator", OP_EQUALS),
                value=c.get("value"),
                case_sensitive=bool(c.get("case_sensitive", False)),
            )
            for c in item.get("conditions", [])
        ]
        rules.append(
            MappingRule(
                rule_id=item.get("rule_id", ""),
                name=item.get("name", "Unnamed Rule"),
                target_value=item.get("target_value", ""),
                conditions=conds,
                combine_operator=item.get("combine_operator", "AND"),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return rules


def save_ruleset_file(
    file_path: str,
    rules: Sequence[MappingRule],
    target_column: str,
    default_value: str = "미분류",
) -> None:
    """Saves a mapping ruleset configuration to a JSON file atomically."""
    payload = {
        "version": "1.0",
        "saved_at": datetime.now().astimezone().isoformat(),
        "target_column": target_column,
        "default_value": default_value,
        "rules": rules_to_dict_list(rules),
    }

    staging = _staging_path(file_path)
    with open(staging, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    os.replace(staging, file_path)


def load_ruleset_file(file_path: str) -> tuple[list[MappingRule], str, str]:
    """Loads a mapping ruleset from a JSON file.

    Returns:
        (rules, target_column, default_value)
    """
    if not os.path.isfile(file_path):
        raise MapperError(f"규칙 파일이 존재하지 않습니다: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    target_column = payload.get("target_column", "Category")
    default_value = payload.get("default_value", "미분류")
    raw_rules = payload.get("rules", [])
    rules = dict_list_to_rules(raw_rules)

    return rules, target_column, default_value


def validate_rules_against_columns(
    rules: Sequence[MappingRule],
    available_columns: Sequence[str],
) -> list[str]:
    """Validates rules against active dataset columns and returns actionable warning messages."""
    warnings: list[str] = []
    col_set = set(available_columns)

    if not rules:
        warnings.append("등록된 매핑 규칙이 없습니다.")
        return warnings

    seen_target_values = set()
    for idx, rule in enumerate(rules, 1):
        if not rule.enabled:
            continue

        if not rule.conditions:
            warnings.append(f"규칙 #{idx} '{rule.name}': 조건이 비어 있습니다.")
            continue

        for cond in rule.conditions:
            if cond.column not in col_set:
                warnings.append(
                    f"규칙 #{idx} '{rule.name}': 데이터셋에 없는 컬럼 '{cond.column}'을(를) 참조합니다."
                )

        seen_target_values.add(rule.target_value)

    return warnings
