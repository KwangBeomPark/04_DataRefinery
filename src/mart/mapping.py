"""Account and measure mapping persistence, auto-classification, and Excel/CSV round-trip."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from src.atomic_write import atomic_write_json

VALID_MEASURE_TYPES = ("amount", "non_monetary", "subtotal_excluded")
VALID_SIGNS = (1, -1)

MAPPING_FILE_HEADERS = [
    "source_column",
    "measure_type",
    "account_code",
    "display_name",
    "major_category",
    "medium_category",
    "minor_category",
    "sign",
    "sort_order",
]

_NON_MONETARY_RE = re.compile(
    r"(수량|\bqty\b|quantity|단가|unit\s*price|환율|\brate\b|비율|비중|인원|headcount|%)",
    re.IGNORECASE,
)
_SUBTOTAL_RE = re.compile(
    r"(합계|소계|총계|총액|\bsubtotal\b|\btotal\b"
    r"|.+?(?:원가|판관비|재료비|노무비|경비|판매비|관리비|제조비|가공비|매출|수익|비용)계$"
    r"|영업이익|공헌이익|매출총이익|매출이익|당기순이익|세전이익|경상이익|영업손익"
    r"|operating\s*profit|gross\s*profit|contribution\s*margin|net\s*income|\bebitda\b|\bebit\b)",
    re.IGNORECASE,
)
_VARIABLE_COST_RE = re.compile(
    r"(변동|재료비|원재료|부재료|외주|물류|운반|포장|소모성|variable|material|freight|packaging)",
    re.IGNORECASE,
)
_FIXED_COST_RE = re.compile(
    r"(고정|감가상각|노무|임차|수선|소모품|보험|세금|공과|제조경비|fixed|depreciation|labor|rent)",
    re.IGNORECASE,
)
_SGA_RE = re.compile(
    r"(판관비|판매비|관리비|급여|복리후생|여비|광고|선전|접대|통신|수수료|연구|개발비|\bsga\b|admin|selling)",
    re.IGNORECASE,
)
_GENERIC_COST_RE = re.compile(r"(원가|비용|\bcogs\b|\bcost\b|\bexpense\b)", re.IGNORECASE)
_REVENUE_RE = re.compile(r"(매출|수익|revenue|sales)", re.IGNORECASE)
_BRACKET_CODE_RE = re.compile(r"^\s*(.+?)\s*\[([^\[\]]+)\]\s*$")


@dataclass(frozen=True)
class AccountMappingEntry:
    source_column: str
    measure_type: str = "amount"
    account_code: str = ""
    display_name: str = ""
    major_category: str = ""
    medium_category: str = ""
    minor_category: str = ""
    sign: int = 1
    sort_order: int = 0
    updated_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_account_code_and_name(source_column: str) -> tuple[str, str]:
    """Extract optional bracketed account code like '원재료비[510001]' -> ('510001', '원재료비')."""
    text = str(source_column or "").strip()
    match = _BRACKET_CODE_RE.match(text)
    if match:
        return match.group(2).strip(), match.group(1).strip()
    return "", text


def entry_from_dict(data: dict) -> AccountMappingEntry:
    """Parse and validate a dict into an AccountMappingEntry."""
    if not isinstance(data, dict):
        raise ValueError("Mapping entry must be a dict")
    source_column = str(data.get("source_column", "") or "").strip()
    if not source_column:
        raise ValueError("source_column is required")
    measure_type = str(data.get("measure_type", "amount") or "amount").strip()
    if measure_type not in VALID_MEASURE_TYPES:
        raise ValueError(
            f"Invalid measure_type '{measure_type}' for '{source_column}'. "
            f"Expected one of {VALID_MEASURE_TYPES}"
        )
    raw_sign = data.get("sign", 1)
    if isinstance(raw_sign, bool):
        raise ValueError(f"Invalid sign '{raw_sign}' for '{source_column}'")
    try:
        sign = int(raw_sign)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid sign '{raw_sign}' for '{source_column}'") from exc
    if sign not in VALID_SIGNS:
        raise ValueError(f"Invalid sign '{sign}' for '{source_column}'. Expected 1 or -1")

    raw_order = data.get("sort_order", 0)
    if isinstance(raw_order, bool):
        raise ValueError(f"Invalid sort_order '{raw_order}' for '{source_column}'")
    try:
        sort_order = int(raw_order if raw_order not in ("", None) else 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid sort_order '{raw_order}' for '{source_column}'") from exc

    return AccountMappingEntry(
        source_column=source_column,
        measure_type=measure_type,
        account_code=str(data.get("account_code", "") or "").strip(),
        display_name=str(data.get("display_name", "") or "").strip(),
        major_category=str(data.get("major_category", "") or "").strip(),
        medium_category=str(data.get("medium_category", "") or "").strip(),
        minor_category=str(data.get("minor_category", "") or "").strip(),
        sign=sign,
        sort_order=sort_order,
        updated_at=str(data.get("updated_at", "") or "").strip(),
    )


def _model_measure_items(model) -> list[tuple[str, str]]:
    """Return list of (name, column) tuples from a MartModel or dict."""
    if model is None:
        return []
    raw_measures = model["measures"] if isinstance(model, dict) else model.measures
    items = []
    for measure in raw_measures:
        if isinstance(measure, dict):
            items.append((str(measure["name"]), str(measure["column"])))
        else:
            items.append((str(measure.name), str(measure.column)))
    return items


def validate_mapping_entries(
    entries: Sequence[AccountMappingEntry],
    model=None,
) -> tuple[AccountMappingEntry, ...]:
    """Validate mapping entries for uniqueness, valid enums, and optional model compatibility."""
    seen: set[str] = set()
    validated: list[AccountMappingEntry] = []
    allowed_keys: set[str] | None = None
    col_by_alias: dict[str, str] = {}
    if model is not None:
        allowed_keys = set()
        for name, column in _model_measure_items(model):
            allowed_keys.add(column)
            allowed_keys.add(name)
            col_by_alias[column] = column
            col_by_alias.setdefault(name, column)

    for raw in entries:
        entry = raw if isinstance(raw, AccountMappingEntry) else entry_from_dict(raw)
        if not entry.source_column or not entry.source_column.strip():
            raise ValueError("Mapping source_column cannot be empty")
        if entry.measure_type not in VALID_MEASURE_TYPES:
            raise ValueError(
                f"Invalid measure_type '{entry.measure_type}' for '{entry.source_column}'"
            )
        if isinstance(entry.sign, bool) or entry.sign not in VALID_SIGNS:
            raise ValueError(f"Invalid sign '{entry.sign}' for '{entry.source_column}'")
        if allowed_keys is not None and entry.source_column not in allowed_keys:
            raise ValueError(f"Unknown measure column in mapping: '{entry.source_column}'")
        canonical_col = col_by_alias.get(entry.source_column, entry.source_column)
        if canonical_col in seen or entry.source_column in seen:
            raise ValueError(f"Duplicate source_column in mapping: '{entry.source_column}'")
        seen.add(canonical_col)
        seen.add(entry.source_column)
        if canonical_col != entry.source_column:
            entry = AccountMappingEntry(
                source_column=canonical_col,
                measure_type=entry.measure_type,
                account_code=entry.account_code,
                display_name=entry.display_name,
                major_category=entry.major_category,
                medium_category=entry.medium_category,
                minor_category=entry.minor_category,
                sign=entry.sign,
                sort_order=entry.sort_order,
                updated_at=entry.updated_at,
            )
        validated.append(entry)
    return tuple(validated)


def detect_unmapped_measures(model, entries: Sequence[AccountMappingEntry]) -> list[str]:
    """Return measure columns missing from mapping or missing a major_category for 'amount'."""
    entry_map: dict[str, AccountMappingEntry] = {}
    for entry in entries:
        entry_map[entry.source_column] = entry

    unmapped: list[str] = []
    for name, column in _model_measure_items(model):
        entry = entry_map.get(column) or entry_map.get(name)
        if entry is None:
            unmapped.append(column)
        elif entry.measure_type == "amount" and not entry.major_category.strip():
            unmapped.append(column)
    return unmapped


def _suggest_single(source_column: str, kind_hint: str, sort_order: int, timestamp: str) -> AccountMappingEntry:
    code, base_name = extract_account_code_and_name(source_column)
    if kind_hint == "quantity" or _NON_MONETARY_RE.search(base_name) or _NON_MONETARY_RE.search(source_column):
        return AccountMappingEntry(
            source_column=source_column,
            measure_type="non_monetary",
            account_code=code,
            display_name=base_name,
            major_category="수량/비금액",
            medium_category="비금액지표",
            minor_category="",
            sign=1,
            sort_order=sort_order,
            updated_at=timestamp,
        )
    if _SUBTOTAL_RE.search(base_name) or _SUBTOTAL_RE.search(source_column):
        return AccountMappingEntry(
            source_column=source_column,
            measure_type="subtotal_excluded",
            account_code=code,
            display_name=base_name,
            major_category="원본소계(제외)",
            medium_category="원본집계/손익(제외)",
            minor_category="",
            sign=1,
            sort_order=sort_order,
            updated_at=timestamp,
        )

    if _VARIABLE_COST_RE.search(base_name):
        major, medium, sign = "매출원가", "변동비", -1
    elif _FIXED_COST_RE.search(base_name):
        major, medium, sign = "매출원가", "고정비", -1
    elif _SGA_RE.search(base_name):
        major, medium, sign = "판관비", "판관비", -1
    elif _GENERIC_COST_RE.search(base_name):
        major, medium, sign = "매출원가", "미분류(변/고정 확인)", -1
    elif _REVENUE_RE.search(base_name):
        major, medium, sign = "매출", "매출", 1
    else:
        major, medium, sign = "기타원가/비용", "미분류(변/고정 확인)", 1

    return AccountMappingEntry(
        source_column=source_column,
        measure_type="amount",
        account_code=code,
        display_name=base_name,
        major_category=major,
        medium_category=medium,
        minor_category="",
        sign=sign,
        sort_order=sort_order,
        updated_at=timestamp,
    )


def suggest_mapping_entries(
    model,
    existing: Iterable[AccountMappingEntry] = (),
) -> list[AccountMappingEntry]:
    """Generate mapping suggestions for all model measures, preserving configured existing entries."""
    existing_by_key = {entry.source_column: entry for entry in existing}
    timestamp = _now_iso()
    raw_measures = model["measures"] if isinstance(model, dict) else model.measures
    result: list[AccountMappingEntry] = []
    for idx, measure in enumerate(raw_measures, start=1):
        if isinstance(measure, dict):
            col = str(measure["column"])
            name = str(measure["name"])
            kind = str(measure.get("kind", "value"))
        else:
            col = str(measure.column)
            name = str(measure.name)
            kind = str(getattr(measure, "kind", "value"))
        prev = existing_by_key.get(col) or existing_by_key.get(name)
        if prev is not None and (prev.major_category.strip() or prev.measure_type != "amount"):
            # Preserve configured entry, normalizing source_column to measure column name.
            result.append(
                AccountMappingEntry(
                    source_column=col,
                    measure_type=prev.measure_type,
                    account_code=prev.account_code,
                    display_name=prev.display_name or col,
                    major_category=prev.major_category,
                    medium_category=prev.medium_category,
                    minor_category=prev.minor_category,
                    sign=prev.sign,
                    sort_order=prev.sort_order or idx,
                    updated_at=prev.updated_at or timestamp,
                )
            )
        else:
            result.append(_suggest_single(col, kind, idx, timestamp))
    return result


def save_mappings_json(workspace_root: Path, entries: Sequence[AccountMappingEntry]) -> None:
    """Persist account mapping entries atomically into workspace/mappings.json."""
    payload = [asdict(entry) for entry in entries]
    atomic_write_json(Path(workspace_root) / "mappings.json", {"version": 1, "entries": payload})


def load_mappings_json(workspace_root: Path) -> list[AccountMappingEntry]:
    """Load account mapping entries from workspace/mappings.json if it exists."""
    path = Path(workspace_root) / "mappings.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("entries", [])
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Invalid mappings.json format")
    return [entry_from_dict(item) for item in items]


def export_mapping_file(path: str | Path, entries: Sequence[AccountMappingEntry]) -> Path:
    """Export mapping entries to an .xlsx workbook (with guide sheet) or .csv file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = target.suffix.lower()

    if suffix == ".csv":
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(MAPPING_FILE_HEADERS)
            for entry in entries:
                writer.writerow(
                    [
                        entry.source_column,
                        entry.measure_type,
                        entry.account_code,
                        entry.display_name,
                        entry.major_category,
                        entry.medium_category,
                        entry.minor_category,
                        entry.sign,
                        entry.sort_order,
                    ]
                )
        return target

    if suffix != ".xlsx":
        raise ValueError(f"Unsupported mapping export format '{target.suffix}'. Use .xlsx or .csv")

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "AccountMapping"
    ws.append(MAPPING_FILE_HEADERS)
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font

    for entry in entries:
        ws.append(
            [
                entry.source_column,
                entry.measure_type,
                entry.account_code,
                entry.display_name,
                entry.major_category,
                entry.medium_category,
                entry.minor_category,
                int(entry.sign),
                int(entry.sort_order),
            ]
        )

    ws.freeze_panes = "A2"
    widths = [34, 20, 14, 26, 18, 22, 20, 8, 10]
    for idx, width in enumerate(widths, start=1):
        col_letter = ws.cell(row=1, column=idx).column_letter
        ws.column_dimensions[col_letter].width = width

    guide = wb.create_sheet("Guide_안내")
    guide.append(["항목 (Column)", "허용 값 / 설명"])
    guide.append(["source_column", "원천 CSV/DB의 수치 컬럼명 (절대 수정하지 마세요)"])
    guide.append(
        [
            "measure_type",
            "amount (세부 금액 계정) | non_monetary (수량·단가·비율 - 금액 합계 제외) | subtotal_excluded (원본 합계·소계·이익 - 이중집계 방지용 제외)",
        ]
    )
    guide.append(["account_code", "계정코드 (예: 510001)"])
    guide.append(["display_name", "출력 표시용 계정명"])
    guide.append(["major_category", "대분류 (예: 매출, 매출원가, 판관비, 기타원가/비용)"])
    guide.append(["medium_category", "중분류 (예: 변동비, 고정비, 판관비 등)"])
    guide.append(["minor_category", "소분류 (선택 입력)"])
    guide.append(["sign", "1 (양수 유지) 또는 -1 (손익 요약/부호 반영 시 차감)"])
    guide.append(["sort_order", "출력 정렬 순서 (정수)"])
    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 96

    wb.save(target)
    return target


def import_mapping_file(path: str | Path) -> list[AccountMappingEntry]:
    """Import mapping entries from an .xlsx workbook or .csv file with row-level error messages."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Mapping file not found: {source}")
    suffix = source.suffix.lower()
    timestamp = _now_iso()
    rows: list[tuple[int, dict]] = []

    if suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "source_column" not in reader.fieldnames:
                raise ValueError("Mapping CSV must contain a 'source_column' header")
            for idx, record in enumerate(reader, start=2):
                rows.append((idx, dict(record)))
    elif suffix == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(source, data_only=True, read_only=True)
        try:
            sheet = wb["AccountMapping"] if "AccountMapping" in wb.sheetnames else wb.active
            iterator = sheet.iter_rows(values_only=True)
            try:
                raw_headers = next(iterator)
            except StopIteration as exc:
                raise ValueError("Mapping Excel sheet is empty") from exc
            headers = [str(h or "").strip() for h in raw_headers]
            if "source_column" not in headers:
                raise ValueError("Mapping Excel must contain a 'source_column' header")
            for idx, values in enumerate(iterator, start=2):
                if values is None or all(v is None or str(v).strip() == "" for v in values):
                    continue
                record = {
                    headers[i]: values[i] if i < len(values) else ""
                    for i in range(len(headers))
                    if headers[i]
                }
                rows.append((idx, record))
        finally:
            wb.close()
    else:
        raise ValueError(f"Unsupported mapping import format '{source.suffix}'. Use .xlsx or .csv")

    parsed: list[AccountMappingEntry] = []
    seen: set[str] = set()
    for row_num, record in rows:
        record_with_ts = dict(record)
        if not record_with_ts.get("updated_at"):
            record_with_ts["updated_at"] = timestamp
        try:
            entry = entry_from_dict(record_with_ts)
        except ValueError as exc:
            raise ValueError(f"Row {row_num}: {exc}") from exc
        if entry.source_column in seen:
            raise ValueError(f"Row {row_num}: Duplicate source_column '{entry.source_column}'")
        seen.add(entry.source_column)
        parsed.append(entry)
    return parsed
