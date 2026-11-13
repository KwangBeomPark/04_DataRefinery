"""Intelligent column role inference and auto-guess engine for Data Refinery marts.

Bridges raw CSV profiling (via dataset_profiler) with the Kimball Star Schema
domain contract (MartModel, Dimension, Measure).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

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
        "key_re": re.compile(
            r"(부서코드|팀코드|사업부코드|본부코드|조직코드|dept_code|team_code|org_code|division_code|^division$|^dept$|^department$|^사업부$|^부서$)",
            re.IGNORECASE,
        ),
        "attr_re": re.compile(
            r"(부서명|팀명|사업부명|본부명|조직명|지점명|dept_name|team_name|org_name|division_name|subsidiary\(r&r\))",
            re.IGNORECASE,
        ),
    },
    {
        "dim_name": "채널/매장",
        "key_re": re.compile(r"(매장코드|채널코드|점포코드|store_code|channel_code|shop_code)", re.IGNORECASE),
        "attr_re": re.compile(r"(매장명|채널명|점포명|유통채널|store_name|channel_name|shop_name)", re.IGNORECASE),
    },
]

PARTITION_RE = re.compile(r"(법인|회사코드|entity|company_code|subsidiary|spolka)", re.IGNORECASE)
ROW_KEY_RE = re.compile(r"(전표번호|전표|행번호|라인번호|seq|line_no|slip_no|doc_no|id|uuid)$", re.IGNORECASE)
PERIOD_RE = re.compile(r"(기준년월|년월|기간|일자|연도|분기|월|period|month|date|yyyymm|year|quarter)", re.IGNORECASE)
KEY_SUFFIX_RE = re.compile(
    r"(?:^|[\s_\-])(코드|번호|사번|식별자|code|id|no|key|cd|uuid)$|(코드|번호)$",
    re.IGNORECASE,
)
ATTR_SUFFIX_RE = re.compile(
    r"(?:^|[\s_\-])(명|이름|성명|상호|내역|설명|비고|유형|구분|분류|규격|단위|지역|국가|통화|"
    r"name|desc|description|type|class|category|group|unit|currency|curr|suffix|model|lvl\d*|level\d*)$|"
    r"(명|이름|내역|설명|비고|유형|구분)$",
    re.IGNORECASE,
)
MEASURE_NAME_RE = re.compile(
    r"(?:금액|단가|매출액|매출|이익|원가|수량|실적|예산|비용|잔액|손익|손실|세금|할인|충당|상각|마진|판관비|노무비|경비)|"
    r"(?<![a-zA-Z])(?:amt|amount|qty|quantity|sales|profit|revenue|cost|costs|price|balance|total|"
    r"exp|expense|expenses|inc|income|loss|cogs|opex|ebit|ebitda|gp|gross|net|tax|taxes|depr|amort|"
    r"alloc|variance|diff|difference|adjustment|margin|discount|d/c|trade\s*term|charge|material|"
    r"o/h|overhead|royalty|refund|waste|valuation|gap|sg&a|selling|admin|r&d|svc|labor|"
    r"commission|advertising|promotion|bad\s*debt)(?![a-zA-Z])",
    re.IGNORECASE,
)


def infer_decimal_precision_scale(values: Sequence[str]) -> tuple[int, int]:
    """Infer lossless (precision, scale) from sample numeric strings including scientific notation."""
    max_i = 0
    max_r = 0
    for raw in values:
        if not raw:
            continue
        s = str(raw).strip().replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
        if not s:
            continue
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            if re.match(r"^[-+]?\d{1,3}(,\d{3})+$", s):
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        try:
            d = Decimal(s)
            if not d.is_finite():
                continue
            norm = d.normalize()
            if norm.is_zero():
                continue
            t = norm.as_tuple()
            r_digits = max(0, -t.exponent)
            i_digits = max(0, len(t.digits) + t.exponent)
            max_i = max(max_i, i_digits)
            max_r = max(max_r, r_digits)
        except (InvalidOperation, ValueError):
            continue

    if max_r <= 2:
        scale = 2
    else:
        scale = min(max(max_r + 2, 6), 18)

    if max_i + 2 + scale <= 18:
        precision = 18
    else:
        precision = 38
        if max_r > 2:
            scale = min(max(max_r + 2, 6), max(2, 38 - max_i))
        else:
            scale = min(scale, max(0, 38 - max_i))

    return precision, scale


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
    assignments: list[ColumnAssignment]
    dimensions: list[Dimension]
    measures: list[Measure]
    partition_columns: list[str]
    row_key_columns: list[str]
    excluded_columns: list[str]
    summary_text: str = ""


def guess_model_assignments(
    columns: Sequence[str],
    profile: DatasetProfileResult | None = None,
    csv_path: Path | str | None = None,
    default_fact_name: str = "fact_pnl",
    delimiter: str | None = None,
    encoding: str | None = None,
) -> ModelGuessResult:
    """Analyze columns and suggest an initial Kimball star schema model setup.
    
    If profile is None and csv_path is given, sample profiling is run automatically.
    """
    if profile is None and csv_path is not None:
        try:
            profile = profile_dataset_sample(csv_path, sample_rows=1000, delimiter=delimiter, encoding=encoding)
        except (OSError, ValueError):
            profile = None

    profile_map = {p.name.casefold(): p for p in (profile.columns if profile else [])}

    assignments: list[ColumnAssignment] = []
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
        if PARTITION_RE.search(c) and not re.search(r"\(.*\)", c):
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
                break

    # 3. Measures detection (Additive numeric amounts)
    for c in columns:
        if c in claimed_columns:
            continue
        col_prof = profile_map.get(c.casefold())
        has_non_measure_pattern = bool(
            KEY_SUFFIX_RE.search(c) or ATTR_SUFFIX_RE.search(c) or PERIOD_RE.search(c)
        )
        is_profile_numeric = bool(
            col_prof
            and col_prof.inferred_type in ("numeric", "integer")
            and col_prof.suggested_role == "numeric"
        )
        is_empty_sample = bool(
            col_prof is None or not col_prof.sample_values or col_prof.null_ratio >= 1.0
        )
        is_name_measure = bool(MEASURE_NAME_RE.search(c) and not has_non_measure_pattern)

        if is_profile_numeric or (is_empty_sample and is_name_measure):
            kind = "value"
            if re.search(r"(수량|qty|quantity|count)", c, re.IGNORECASE):
                kind = "quantity"
            elif re.search(r"(원가|비용|경비|판관비|노무비|cost|expense|exp|cogs|opex)", c, re.IGNORECASE):
                kind = "cost"

            if col_prof and col_prof.inferred_type == "integer":
                prec, scale = 18, 0
            else:
                prec, scale = infer_decimal_precision_scale(col_prof.sample_values if col_prof else ())
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_MEASURE,
                    measure_kind=kind,
                    measure_precision=prec,
                    measure_scale=scale,
                    confidence=0.85,
                    reason="가산적 수치 측도 (Measure)",
                )
            )
            claimed_columns.add(c)

    # 3b. Wide-table empty columns inside the numeric measure band
    measure_indices = [
        i
        for i, c in enumerate(columns)
        if c in claimed_columns and any(a.column_name == c and a.role == ROLE_MEASURE for a in assignments)
    ]
    if len(measure_indices) >= 10:
        first_meas_idx = min(measure_indices)
        for idx in range(first_meas_idx, len(columns)):
            c = columns[idx]
            if c in claimed_columns:
                continue
            col_prof = profile_map.get(c.casefold())
            is_empty_sample = bool(
                col_prof is not None and (not col_prof.sample_values or col_prof.null_ratio >= 1.0)
            )
            has_non_measure_pattern = bool(
                KEY_SUFFIX_RE.search(c)
                or ATTR_SUFFIX_RE.search(c)
                or PERIOD_RE.search(c)
                or ROW_KEY_RE.search(c)
            )
            if is_empty_sample and not has_non_measure_pattern:
                kind = (
                    "cost"
                    if re.search(r"(원가|비용|경비|판관비|노무비|cost|expense|exp|cogs|opex)", c, re.IGNORECASE)
                    else "value"
                )
                assignments.append(
                    ColumnAssignment(
                        column_name=c,
                        role=ROLE_MEASURE,
                        measure_kind=kind,
                        measure_precision=18,
                        measure_scale=2,
                        confidence=0.75,
                        reason="가산적 수치 측도 (Wide 수치 구간 내 빈 열)",
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
    # e.g., "Bill To Customer" and "Bill To Customer Name", or "Product Lvl4" and "Product Lvl4 Code"
    for c1 in sorted(remaining, key=len):
        if c1 in consumed_in_dim:
            continue
        matches = [
            c2 for c2 in remaining
            if c2 != c1 and c2 not in consumed_in_dim and c2.startswith((c1 + " ", c1 + "_", c1 + "-", c1 + "("))
        ]
        if matches:
            dim_title = c1.replace(" ", "_")
            group = [col for col in remaining if col == c1 or col in matches]
            explicit_keys = [col for col in group if KEY_SUFFIX_RE.search(col)]
            if explicit_keys:
                keys = explicit_keys
                attrs = [col for col in group if col not in explicit_keys]
            else:
                keys = [c1]
                attrs = matches

            for k_col in keys:
                assignments.append(
                    ColumnAssignment(
                        column_name=k_col,
                        role=ROLE_DIM_KEY,
                        dimension_name=dim_title,
                        confidence=0.85,
                        reason=f"{dim_title} 차원 식별 자연키",
                    )
                )
                claimed_columns.add(k_col)
                consumed_in_dim.add(k_col)
            for m_col in attrs:
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
    stem_buckets: dict[str, list[str]] = {}
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
            keys = [c for c in pfx_cols if KEY_SUFFIX_RE.search(c)]
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
                            confidence=0.75,
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

    # 5c. Standalone product/model/currency/code identifiers
    for c in [col for col in remaining if col not in consumed_in_dim]:
        if re.search(r"^(model|model[\s_\-]*suffix|model[\s_\-]*code|품번|모델|모델코드)$", c, re.IGNORECASE):
            prod_dim = next(
                (
                    a.dimension_name
                    for a in assignments
                    if a.dimension_name and re.search(r"(product|item|제품|품목|상품)", a.dimension_name, re.IGNORECASE)
                ),
                "제품/품목",
            )
            existing_keys = [a for a in assignments if a.dimension_name == prod_dim and a.role == ROLE_DIM_KEY]
            role = ROLE_DIM_KEY if len(existing_keys) < 2 else ROLE_DIM_ATTR
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=role,
                    dimension_name=prod_dim,
                    confidence=0.75,
                    reason=f"{prod_dim} 차원 복합 식별키" if role == ROLE_DIM_KEY else f"{prod_dim} 차원 속성",
                )
            )
            claimed_columns.add(c)
            consumed_in_dim.add(c)
        elif re.search(r"^(currency|curr|currency[\s_\-]*code|curr[\s_\-]*code|통화|통화코드)$", c, re.IGNORECASE):
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_DIM_KEY,
                    dimension_name="통화",
                    confidence=0.75,
                    reason="통화 차원 식별키",
                )
            )
            claimed_columns.add(c)
            consumed_in_dim.add(c)

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
            prec, scale = (
                (18, 0)
                if col_prof.inferred_type == "integer"
                else infer_decimal_precision_scale(col_prof.sample_values)
            )
            # Secondary measure
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_MEASURE,
                    measure_precision=prec,
                    measure_scale=scale,
                    confidence=0.6,
                    reason="수치 열 (기본 측정값)",
                )
            )
            claimed_columns.add(c)
        elif KEY_SUFFIX_RE.search(c):
            dim_title = (
                re.sub(
                    r"[\s_\-]*(코드|번호|사번|식별자|code|id|no|key|cd|uuid)$",
                    "",
                    c,
                    flags=re.IGNORECASE,
                )
                .strip()
                .replace(" ", "_")
                or c.replace(" ", "_")
            )
            assignments.append(
                ColumnAssignment(
                    column_name=c,
                    role=ROLE_DIM_KEY,
                    dimension_name=dim_title,
                    confidence=0.7,
                    reason=f"{dim_title} 차원 식별키",
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
) -> tuple[list[Dimension], list[Measure], list[str], list[str], list[str]]:
    dim_dict: dict[str, dict[str, list[str]]] = {}
    measures: list[Measure] = []
    partitions: list[str] = []
    row_keys: list[str] = []
    excluded: list[str] = []

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

    dimensions: list[Dimension] = []
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
    row_key_columns: Sequence[str] | None = None,
    duplicate_grain_policy: str = "reject",
) -> MartModel:
    """Construct and validate a MartModel from a finalized assignment list.

    If explicit row_key_columns are provided, they take precedence.
    Otherwise, assigned row keys are used. If still empty, automatically
    derives a surrogate composite grain from the period column and all dimension keys.
    """
    dimensions, measures, partitions, assigned_row_keys, _excluded = _build_components_from_assignments(assignments)

    if row_key_columns is not None and len(row_key_columns) > 0:
        effective_row_keys = tuple(row_key_columns)
    elif assigned_row_keys:
        effective_row_keys = tuple(assigned_row_keys[:8])
    else:
        # Auto-derive surrogate grain from period + dimension keys (capped at 8)
        auto_keys = []
        if period_column and period_column not in auto_keys:
            auto_keys.append(period_column)
        for d in dimensions:
            for k in d.key_columns:
                if k not in auto_keys:
                    auto_keys.append(k)
        effective_row_keys = tuple(auto_keys[:8])

    if len(effective_row_keys) > 8:
        raise ValueError(f"Row key columns cannot exceed 8 (configured: {len(effective_row_keys)})")
    if not effective_row_keys:
        raise ValueError("Row key columns cannot be empty")

    active_cols = {period_column, *partitions, *effective_row_keys, *(m.column for m in measures)}
    for d in dimensions:
        active_cols.update(d.key_columns)
        active_cols.update(d.attributes)
    clean_excluded = [a.column_name for a in assignments if a.column_name not in active_cols]

    model = MartModel(
        name=model_name,
        fact_table=fact_table,
        dimensions=tuple(dimensions),
        measures=tuple(measures),
        period_column=period_column,
        partition_columns=tuple(partitions),
        row_key_columns=effective_row_keys,
        excluded_columns=tuple(clean_excluded),
        version="1",
        source_delimiter=source_delimiter,
        source_encoding=source_encoding,
        duplicate_grain_policy=duplicate_grain_policy,
    )
    validate_column_roles(model)
    return model


def model_to_assignments(
    model: MartModel,
    all_columns: Sequence[str],
) -> list[ColumnAssignment]:
    """Reverse-map an existing MartModel back into editable ColumnAssignments."""
    result: list[ColumnAssignment] = []
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

    # Dimensions (Processed before row keys so shared dimension keys retain their dimension role)
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

    # Standalone Row keys (only for columns not already claimed by period/partition/dimension)
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
