"""Intelligent column role inference and auto-guess engine for Data Refinery marts.

Bridges raw CSV profiling (via dataset_profiler) with the Kimball Star Schema
domain contract (MartModel, Dimension, Measure).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from src.dataset_profiler import DatasetProfileResult, profile_dataset_sample
from src.mart.model import Dimension, MartModel, Measure, validate_column_roles


# Role enumeration constants
ROLE_PERIOD = "period"
ROLE_ROW_KEY = "row_key"
ROLE_PARTITION = "partition"
ROLE_MEASURE = "measure"
ROLE_DIM_KEY = "dim_key"
ROLE_DIM_ATTR = "dim_attr"
ROLE_EXCLUDED = "excluded"
ROLE_UNASSIGNED = "unassigned"

VALID_ROLES = (
    ROLE_PERIOD,
    ROLE_ROW_KEY,
    ROLE_PARTITION,
    ROLE_MEASURE,
    ROLE_DIM_KEY,
    ROLE_DIM_ATTR,
    ROLE_EXCLUDED,
    ROLE_UNASSIGNED,
)

# Standard entity patterns for dimension discovery (Korean, English, Polish)
ENTITY_PATTERNS = [
    {
        "dim_name": "고객/거래처",
        "key_re": re.compile(r"(거래처코드|고객코드|매출처코드|customer_id|customer_code|client_code|partner_code|klient_kod)", re.IGNORECASE),
        "attr_re": re.compile(r"(거래처명|고객명|매출처명|상호|대표자|사업자번호|customer_name|client_name|partner_name|klient_nazwa)", re.IGNORECASE),
    },
    {
        "dim_name": "제품/품목",
        "key_re": re.compile(r"(상품코드|품목코드|제품코드|자재코드|product_id|product_code|item_code|sku|kod_produktu)", re.IGNORECASE),
        "attr_re": re.compile(r"(상품명|품목명|제품명|규격|단위|카테고리|분류|product_name|item_name|category|brand|nazwa_produktu)", re.IGNORECASE),
    },
    {
        "dim_name": "계정/손익과목",
        "key_re": re.compile(r"(계정코드|과목코드|손익코드|항목코드|account_code|account_id|gl_code|kod_konta)", re.IGNORECASE),
        "attr_re": re.compile(r"(계정명|과목명|손익항목|비용항목|account_name|gl_name|account_desc|nazwa_konta)", re.IGNORECASE),
    },
    {
        "dim_name": "조직/부서",
        "key_re": re.compile(r"(부서코드|팀코드|사업부코드|dept_code|team_code|org_code|division_code)", re.IGNORECASE),
        "attr_re": re.compile(r"(부서명|팀명|사업부명|지점명|dept_name|team_name|org_name|division_name)", re.IGNORECASE),
    },
    {
        "dim_name": "채널/매장",
        "key_re": re.compile(r"(매장코드|채널코드|점포코드|store_code|channel_code|shop_code)", re.IGNORECASE),
        "attr_re": re.compile(r"(매장명|채널명|점포명|유통채널|store_name|channel_name|shop_name)", re.IGNORECASE),
    },
]

PARTITION_RE = re.compile(r"(법인|회사코드|entity|company_code|subsidiary|spolka)", re.IGNORECASE)
ROW_KEY_RE = re.compile(r"(전표번호|전표|행번호|라인번호|seq|line_no|slip_no|doc_no|id|uuid)$", re.IGNORECASE)
MEASURE_NAME_RE = re.compile(r"(금액|단가|매출액|매출|이익|원가|수량|실적|예산|비용|잔액|amt|amount|qty|quantity|sales|profit|revenue|cost|price|balance|total)", re.IGNORECASE)


@dataclass
class ColumnAssignment:
    column_name: str
    role: str = ROLE_UNASSIGNED
    dimension_name: str = ""
    measure_kind: str = "value"  # 'value', 'cost', 'quantity'
    measure_unit: str = ""
    measure_precision: int = 18
    measure_scale: int = 2
    confidence: float = 0.5
    reason: str = ""


@dataclass
class ModelGuessResult:
    fact_table: str
    period_column: str
    assignments: List[ColumnAssignment]
    dimensions: List[Dimension]
    measures: List[Measure]
    partition_columns: List[str]
    row_key_columns: List[str]
    excluded_columns: List[str]
    summary_text: str = ""


def guess_model_assignments(
    columns: Sequence[str],
    profile: Optional[DatasetProfileResult] = None,
    csv_path: Optional[Path | str] = None,
    default_fact_name: str = "fact_pnl",
    delimiter: Optional[str] = None,
    encoding: Optional[str] = None,
) -> ModelGuessResult:
    """Analyze columns and suggest an initial Kimball star schema model setup.
    
    If profile is None and csv_path is given, sample profiling is run automatically.
    """
    if profile is None and csv_path is not None:
        try:
            profile = profile_dataset_sample(csv_path, sample_rows=1000, delimiter=delimiter, encoding=encoding)
        except Exception:
            profile = None

    profile_map = {p.name.casefold(): p for p in (profile.columns if profile else [])}

    assignments: List[ColumnAssignment] = []
    claimed_columns: set[str] = set()

    # 1. Period column detection
    period_col = ""
    if profile and profile.suggested_period_column:
        for c in columns:
            if c.casefold() == profile.suggested_period_column.casefold():
                period_col = c
                break

    if not period_col:
        for c in columns:
            col_prof = profile_map.get(c.casefold())
            if col_prof and col_prof.suggested_role == "period":
                period_col = c
                break

    if not period_col:
        for c in columns:
            if re.search(r"(기준년월|년월|기간|일자|period|month|date|yyyymm)", c, re.IGNORECASE):
                period_col = c
                break

    if not period_col and columns:
        period_col = columns[0]

    # Assign period
    for c in columns:
        if c == period_col:
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_PERIOD,
                    confidence=0.9,
                    reason="기준 분석 기간 (ISO Month/Date)",
                )
            )
            claimed_columns.add(c)
            break

    # 2. Partition detection (Entity/Subsidiary)
    for c in columns:
        if c in claimed_columns:
            continue
        if PARTITION_RE.search(c):
            col_prof = profile_map.get(c.casefold())
            unique_cnt = col_prof.unique_count if col_prof else 1
            if unique_cnt <= 10:
                assignments.append(
                    ColumnAssignment(
                        column_name=c,
                        role=ROLE_PARTITION,
                        confidence=0.85,
                        reason="데이터 파티션/법인 격리 기준",
                    )
                )
                claimed_columns.add(c)

    # 3. Measures detection (Additive numeric amounts)
    for c in columns:
        if c in claimed_columns:
            continue
        col_prof = profile_map.get(c.casefold())
        is_numeric = False
        if col_prof and col_prof.inferred_type in ("numeric", "integer"):
            is_numeric = True
        elif MEASURE_NAME_RE.search(c) and not re.search(r"(코드|번호|code|id|no)$", c, re.IGNORECASE):
            is_numeric = True

        if is_numeric and (col_prof is None or col_prof.suggested_role == "numeric"):
            kind = "value"
            if re.search(r"(수량|qty|quantity|count)", c, re.IGNORECASE):
                kind = "quantity"
            elif re.search(r"(원가|비용|cost|expense)", c, re.IGNORECASE):
                kind = "cost"

            scale = 0 if (col_prof and col_prof.inferred_type == "integer") else 2
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_MEASURE,
                    measure_kind=kind,
                    measure_scale=scale,
                    confidence=0.85,
                    reason="가산적 수치 측도 (Measure)",
                )
            )
            claimed_columns.add(c)

    # 4. Standard Entity Dimension matching
    for entity in ENTITY_PATTERNS:
        dim_name = entity["dim_name"]
        key_re = entity["key_re"]
        attr_re = entity["attr_re"]

        # Find key columns
        matched_keys = [c for c in columns if c not in claimed_columns and key_re.search(c)]
        # Find attribute columns
        matched_attrs = [c for c in columns if c not in claimed_columns and attr_re.search(c)]

        if matched_keys or matched_attrs:
            # If no explicit key but attributes exist, take first attr as key if unique
            if not matched_keys and matched_attrs:
                matched_keys.append(matched_attrs.pop(0))

            for k in matched_keys:
                assignments.append(
                    ColumnAssignment(
                        column_name=k,
                        role=ROLE_DIM_KEY,
                        dimension_name=dim_name,
                        confidence=0.8,
                        reason=f"{dim_name} 차원 식별 자연키",
                    )
                )
                claimed_columns.add(k)

            for a in matched_attrs:
                assignments.append(
                    ColumnAssignment(
                        column_name=a,
                        role=ROLE_DIM_ATTR,
                        dimension_name=dim_name,
                        confidence=0.75,
                        reason=f"{dim_name} 차원 상세 속성",
                    )
                )
                claimed_columns.add(a)

    # 5. Generic Dimension discovery by prefix/stem (e.g. prefix_code, "Product Lvl4 Code" -> dim)
    remaining = [c for c in columns if c not in claimed_columns]
    consumed_in_dim: set[str] = set()

    # 5a. Direct prefix match: one column is the exact base/prefix of another
    # e.g., "Bill To Customer" and "Bill To Customer Name"
    for c1 in remaining:
        if c1 in consumed_in_dim:
            continue
        matches = [
            c2 for c2 in remaining
            if c2 != c1 and c2 not in consumed_in_dim and (
                c2.startswith(c1 + " ") or c2.startswith(c1 + "_") or c2.startswith(c1 + "-")
            )
        ]
        if matches:
            dim_title = c1.replace(" ", "_")
            assignments.append(
                ColumnAssignment(
                    column_name=c1,
                    role=ROLE_DIM_KEY,
                    dimension_name=dim_title,
                    confidence=0.8,
                    reason=f"{dim_title} 차원 식별키 (기본 열)",
                )
            )
            claimed_columns.add(c1)
            consumed_in_dim.add(c1)
            for m_col in matches:
                assignments.append(
                    ColumnAssignment(
                        column_name=m_col,
                        role=ROLE_DIM_ATTR,
                        dimension_name=dim_title,
                        confidence=0.75,
                        reason=f"{dim_title} 차원 확장 속성",
                    )
                )
                claimed_columns.add(m_col)
                consumed_in_dim.add(m_col)

    # 5b. Common token prefix match (e.g. "Product Lvl4 Code" & "Product Lvl4 Model Suffix")
    rem_after_5a = [c for c in remaining if c not in consumed_in_dim]
    stem_buckets: Dict[str, List[str]] = {}
    for c in rem_after_5a:
        m = re.match(r"^([a-zA-Z가-힣0-9]+)[_\-](.*)$", c)
        if m:
            pfx = m.group(1)
            stem_buckets.setdefault(pfx, []).append(c)
            continue
        tokens = c.strip().split()
        if len(tokens) >= 2:
            for p_len in range(1, len(tokens)):
                cand_stem = " ".join(tokens[:p_len])
                cand_suffix = " ".join(tokens[p_len:])
                if re.search(r"(code|id|name|suffix|desc|no|type|class|model|코드|명|번호|유형|규격)", cand_suffix, re.IGNORECASE):
                    stem_buckets.setdefault(cand_stem, []).append(c)

    # Prioritize longest stems first
    sorted_stems = sorted(stem_buckets.keys(), key=lambda s: (-len(stem_buckets[s]), -len(s)))
    for pfx in sorted_stems:
        pfx_cols = [c for c in stem_buckets[pfx] if c not in consumed_in_dim]
        if len(pfx_cols) >= 2:
            keys = [c for c in pfx_cols if re.search(r"(code|id|코드|번호)$", c, re.IGNORECASE)]
            attrs = [c for c in pfx_cols if c not in keys]
            if not keys and pfx_cols:
                keys = [pfx_cols[0]]
                attrs = pfx_cols[1:]
            if keys:
                dim_title = pfx.replace(" ", "_")
                for k in keys:
                    assignments.append(
                        ColumnAssignment(
                            column_name=k,
                            role=ROLE_DIM_KEY,
                            dimension_name=dim_title,
                            confidence=0.7,
                            reason=f"{dim_title} 차원 식별키",
                        )
                    )
                    claimed_columns.add(k)
                    consumed_in_dim.add(k)
                for a in attrs:
                    assignments.append(
                        ColumnAssignment(
                            column_name=a,
                            role=ROLE_DIM_ATTR,
                            dimension_name=dim_title,
                            confidence=0.7,
                            reason=f"{dim_title} 차원 속성",
                        )
                    )
                    claimed_columns.add(a)
                    consumed_in_dim.add(a)

    # 6. Row Key detection (slip_no, line_no, uuid, etc.)
    for c in columns:
        if c in claimed_columns:
            continue
        if ROW_KEY_RE.search(c):
            col_prof = profile_map.get(c.casefold())
            if col_prof is None or (col_prof.unique_ratio > 0.8 or col_prof.suggested_role == "key"):
                assignments.append(
                    ColumnAssignment(
                        column_name=c,
                        role=ROLE_ROW_KEY,
                        confidence=0.75,
                        reason="원천 행 고유 식별키 (Row Grain)",
                    )
                )
                claimed_columns.add(c)

    # 7. Remaining columns: check high unique text vs low unique text
    for c in columns:
        if c in claimed_columns:
            continue
        col_prof = profile_map.get(c.casefold())
        if col_prof and col_prof.inferred_type in ("numeric", "integer"):
            # Secondary measure
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_MEASURE,
                    confidence=0.6,
                    reason="수치 열 (기본 측정값)",
                )
            )
            claimed_columns.add(c)
        else:
            # Leave unassigned for explicit user confirmation
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_UNASSIGNED,
                    confidence=0.5,
                    reason="역할 미할당 (차원 속성 또는 제외 지정 필요)",
                )
            )

    # Reorder assignments to match original columns order
    col_order = {c: i for i, c in enumerate(columns)}
    assignments.sort(key=lambda a: col_order.get(a.column_name, 999))

    # Build dimensions, measures, etc.
    dims, measures, partitions, row_keys, excluded = _build_components_from_assignments(assignments)

    total_cols = len(columns)
    assigned_cols = sum(1 for a in assignments if a.role != ROLE_UNASSIGNED)
    summary_text = (
        f"총 {total_cols}개 컬럼 중 {assigned_cols}개 역할 자동 추천 완료 "
        f"({total_cols - assigned_cols}개 확인 필요)"
    )

    return ModelGuessResult(
        fact_table=default_fact_name,
        period_column=period_col,
        assignments=assignments,
        dimensions=dims,
        measures=measures,
        partition_columns=partitions,
        row_key_columns=row_keys,
        excluded_columns=excluded,
        summary_text=summary_text,
    )


def _build_components_from_assignments(
    assignments: Sequence[ColumnAssignment],
) -> Tuple[List[Dimension], List[Measure], List[str], List[str], List[str]]:
    dim_dict: Dict[str, Dict[str, List[str]]] = {}
    measures: List[Measure] = []
    partitions: List[str] = []
    row_keys: List[str] = []
    excluded: List[str] = []

    for a in assignments:
        if a.role == ROLE_PARTITION:
            partitions.append(a.column_name)
        elif a.role == ROLE_ROW_KEY:
            row_keys.append(a.column_name)
        elif a.role == ROLE_EXCLUDED:
            excluded.append(a.column_name)
        elif a.role == ROLE_MEASURE:
            measures.append(
                Measure(
                    name=a.column_name,
                    column=a.column_name,
                    kind=a.measure_kind,
                    precision=a.measure_precision,
                    scale=a.measure_scale,
                    unit=a.measure_unit,
                    aggregation="sum",
                )
            )
        elif a.role == ROLE_DIM_KEY:
            dname = a.dimension_name or "공통차원"
            dim_dict.setdefault(dname, {"keys": [], "attrs": []})["keys"].append(a.column_name)
        elif a.role == ROLE_DIM_ATTR:
            dname = a.dimension_name or "공통차원"
            dim_dict.setdefault(dname, {"keys": [], "attrs": []})["attrs"].append(a.column_name)

    dimensions: List[Dimension] = []
    for dname, parts in dim_dict.items():
        keys = tuple(parts["keys"])
        attrs = tuple(parts["attrs"])
        if keys:  # Dimension must have at least one natural key
            dimensions.append(
                Dimension(
                    name=dname,
                    key_columns=keys,
                    attributes=attrs,
                )
            )
        else:
            # Fix High 5: Attributes of dimensions without keys must be safely excluded, not dropped
            for col in attrs:
                if col not in excluded:
                    excluded.append(col)

    return dimensions, measures, partitions, row_keys, excluded


def assignments_to_model(
    assignments: Sequence[ColumnAssignment],
    model_name: str,
    fact_table: str,
    period_column: str,
    source_delimiter: str = ",",
    source_encoding: str = "utf-8-sig",
) -> MartModel:
    """Construct and validate a MartModel from a finalized assignment list."""
    dimensions, measures, partitions, row_keys, excluded = _build_components_from_assignments(assignments)
    model = MartModel(
        name=model_name,
        fact_table=fact_table,
        dimensions=tuple(dimensions),
        measures=tuple(measures),
        period_column=period_column,
        partition_columns=tuple(partitions),
        row_key_columns=tuple(row_keys),
        excluded_columns=tuple(excluded),
        version="1",
        source_delimiter=source_delimiter,
        source_encoding=source_encoding,
    )
    validate_column_roles(model)
    return model


def model_to_assignments(
    model: MartModel,
    all_columns: Sequence[str],
) -> List[ColumnAssignment]:
    """Reverse-map an existing MartModel back into editable ColumnAssignments."""
    result: List[ColumnAssignment] = []
    claimed = set()

    # Period
    if model.period_column in all_columns:
        result.append(
            ColumnAssignment(
                column_name=model.period_column,
                role=ROLE_PERIOD,
                confidence=1.0,
                reason="저장된 모델 기간 기준",
            )
        )
        claimed.add(model.period_column)

    # Partitions
    for c in model.partition_columns:
        if c in all_columns and c not in claimed:
            result.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_PARTITION,
                    confidence=1.0,
                    reason="저장된 파티션",
                )
            )
            claimed.add(c)

    # Row keys
    for c in model.row_key_columns:
        if c in all_columns and c not in claimed:
            result.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_ROW_KEY,
                    confidence=1.0,
                    reason="저장된 행 고유키",
                )
            )
            claimed.add(c)

    # Measures
    measure_map = {m.column: m for m in model.measures}
    for c, m in measure_map.items():
        if c in all_columns and c not in claimed:
            result.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_MEASURE,
                    measure_kind=m.kind,
                    measure_scale=m.scale,
                    measure_precision=m.precision,
                    measure_unit=m.unit,
                    confidence=1.0,
                    reason="저장된 측정값 (Measure)",
                )
            )
            claimed.add(c)

    # Dimensions
    for dim in model.dimensions:
        for k in dim.key_columns:
            if k in all_columns and k not in claimed:
                result.append(
                    ColumnAssignment(
                        column_name=k,
                        role=ROLE_DIM_KEY,
                        dimension_name=dim.name,
                        confidence=1.0,
                        reason=f"{dim.name} 차원 키",
                    )
                )
                claimed.add(k)
        for a in dim.attributes:
            if a in all_columns and a not in claimed:
                result.append(
                    ColumnAssignment(
                        column_name=a,
                        role=ROLE_DIM_ATTR,
                        dimension_name=dim.name,
                        confidence=1.0,
                        reason=f"{dim.name} 차원 속성",
                    )
                )
                claimed.add(a)

    # Excluded
    for c in model.excluded_columns:
        if c in all_columns and c not in claimed:
            result.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_EXCLUDED,
                    confidence=1.0,
                    reason="저장된 제외 열",
                )
            )
            claimed.add(c)

    # Remaining
    for c in all_columns:
        if c not in claimed:
            result.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_UNASSIGNED,
                    confidence=0.0,
                    reason="미할당 열",
                )
            )

    col_order = {c: i for i, c in enumerate(all_columns)}
    result.sort(key=lambda a: col_order.get(a.column_name, 999))
    return result
