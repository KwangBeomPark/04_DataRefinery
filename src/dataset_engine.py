"""DuckDB-powered engine for dataset accumulation, replacement, validation, and safe publication.

All heavy processing runs in DuckDB with transactional safety.
Data is read from CSVs, accumulated/replaced by period, strictly validated,
and safely published to shared folders as clean CSVs for Excel Power Query consumption.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import threading
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import duckdb

from src.csv_processing import detect_encoding, detect_encoding_precise
from src.dataset_config import DatasetDefinition, DatasetRegistry


class DatasetError(Exception):
    """Base exception for dataset engine errors."""
    pass


class DatasetValidationError(DatasetError):
    """Validation or schema mismatch error."""
    pass


class DatasetPublishLockError(DatasetError):
    """File lock error when replacing target in shared folder."""
    pass


@dataclass
class ScannedFile:
    file_path: str
    file_name: str
    file_size: int
    mtime: float
    file_hash: str
    period: str  # Primary period or comma-separated periods
    periods: List[str] = field(default_factory=list)  # All distinct periods contained in file
    row_count_estimate: int = 0
    status: str = "pending"  # 'new_period', 'modified_period', 'unchanged', 'conflict', 'empty'
    status_detail: str = ""
    encoding: str = "utf-8"
    header_status: str = "ok"  # 'ok', 'order_mismatch', 'missing_columns', 'extra_columns', 'empty'
    header_detail: str = ""


@dataclass
class ScanSummary:
    files: List[ScannedFile]
    new_periods: List[str]
    modified_periods: List[str]
    unchanged_files: List[str]
    conflicts: List[str]
    has_changes: bool


@dataclass
class PeriodMetric:
    period: str
    row_count: int
    measures: Dict[str, float]


@dataclass
class InspectionResult:
    dataset_id: str
    timestamp: str
    approval_token: str
    before_period_min: Optional[str]
    before_period_max: Optional[str]
    before_row_count: int
    after_period_min: Optional[str]
    after_period_max: Optional[str]
    after_row_count: int
    added_row_count: int
    replaced_row_count: int
    all_periods: List[str]
    period_metrics: List[PeriodMetric]
    numeric_totals_before: Dict[str, float]
    numeric_totals_after: Dict[str, float]
    numeric_deltas: Dict[str, float]
    null_period_count: int
    duplicate_key_count: int
    schema_columns: List[str]
    warnings: List[str]
    errors: List[str]
    is_valid: bool


def make_numeric_sql_expr(clean_col: str, number_format: str = "auto") -> str:
    """Return SQL expression to parse a numeric column safely into DECIMAL(38,6).
    
    Handles US/standard ('1,234.56') and European/Polish ('1 234,56' or '1234,56') formats
    without 100x magnification bugs.
    """
    x = f'TRIM(CAST("{clean_col}" AS VARCHAR))'
    fmt = (number_format or "auto").lower()
    # Use direct Python unicode characters for spaces (U+00A0 and U+202F)
    sp = " .\xa0\u202f"
    eu_convert = f"REPLACE(regexp_replace({x}, '[{sp}]', '', 'g'), ',', '.')"
    us_convert = f"REPLACE({x}, ',', '')"

    if fmt in ("1 234,56", "1.234,56", "polish", "eu", "european"):
        body = eu_convert
    elif fmt in ("1,234.56", "us", "standard"):
        body = us_convert
    else:  # auto
        # Disambiguate comma:
        # 1. Obvious EU: contains thousands space/dot before comma (e.g. '1 234,56', '1.234,56')
        # 2. Obvious EU decimal without thousands: single comma followed by 1, 2, or 4+ digits (e.g. '1234,5', '1234,56', '1234,5678')
        # 3. Otherwise (including comma thousands like '10,000' or '1,234,567'): treat as US thousands!
        body = f"""CASE 
            WHEN regexp_matches({x}, '^[+-]?[0-9]{{1,3}}([{sp}][0-9]{{3}})+(,[0-9]+)$') THEN {eu_convert}
            WHEN regexp_matches({x}, '^[+-]?[0-9]+,[0-9]{{1,2}}$') THEN {eu_convert}
            WHEN regexp_matches({x}, '^[+-]?[0-9]+,[0-9]{{4,}}$') THEN {eu_convert}
            ELSE {us_convert}
        END"""
    return f"TRY_CAST({body} AS DECIMAL(38,6))"


def calculate_file_hash(path: Path | str, chunk_size: int = 65536) -> str:
    """Compute SHA-256 fingerprint for a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def normalize_period_value(val: Any) -> Optional[str]:
    """Normalize period values like '202401', '2024-01', '2024.1', '2024년 1월' to 'YYYY-MM'."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None

    # Matches YYYY-MM, YYYY.MM, YYYY/MM, YYYY_MM with 1 or 2 digit month (e.g. 2024-1, 2024-01)
    m1 = re.match(r"^(\d{4})[-./_](\d{1,2})$", s)
    if m1:
        year, month = m1.group(1), int(m1.group(2))
        if 1 <= month <= 12:
            return f"{year}-{month:02d}"

    # Matches YYYYMM (e.g. 202401)
    m2 = re.match(r"^(\d{4})(\d{2})$", s)
    if m2:
        year, month = m2.group(1), int(m2.group(2))
        if 1 <= month <= 12:
            return f"{year}-{month:02d}"

    # Matches YYYYMMDD (e.g. 20240115 -> 2024-01)
    m2_daily = re.match(r"^(\d{4})(\d{2})\d{2}$", s)
    if m2_daily:
        year, month = m2_daily.group(1), int(m2_daily.group(2))
        if 1 <= month <= 12:
            return f"{year}-{month:02d}"

    # Matches YY년 MM월 or YYYY년 MM월 or YYYY년 M월
    m3 = re.search(r"(\d{4}|\d{2})[년./\s]+(\d{1,2})[월]?", s)
    if m3:
        year_str, month = m3.group(1), int(m3.group(2))
        year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
        if 1 <= month <= 12:
            return f"{year}-{month:02d}"

    return None


def extract_period_from_filename(filename: str) -> Optional[str]:
    """Attempt to extract period token (YYYY-MM or YYYYMM) from filename."""
    name = Path(filename).stem

    # Matches 2024-01 or 2024_01 or 2024.01
    m = re.search(r"(\b|[^0-9])(20\d{2})[-._](\d{2})(\b|[^0-9])", name)
    if m:
        month = int(m.group(3))
        if 1 <= month <= 12:
            return f"{m.group(2)}-{month:02d}"

    # Matches 202401
    m2 = re.search(r"(\b|[^0-9])(20\d{2})(\d{2})(\b|[^0-9])", name)
    if m2:
        month = int(m2.group(3))
        if 1 <= month <= 12:
            return f"{m2.group(2)}-{month:02d}"

    return None


class DatasetEngine:
    """DuckDB workspace manager for a specific dataset."""

    def __init__(self, dataset: DatasetDefinition, registry: DatasetRegistry):
        self.dataset = dataset
        self.registry = registry
        self.db_path = registry.get_dataset_db_path(dataset.id)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        # Connect to DuckDB persistent database
        return duckdb.connect(str(self.db_path))

    def _init_db(self) -> None:
        """Initialize metadata tables in DuckDB workspace."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_history (
                    file_path VARCHAR,
                    file_name VARCHAR,
                    file_hash VARCHAR PRIMARY KEY,
                    file_size BIGINT,
                    mtime DOUBLE,
                    period VARCHAR,
                    row_count BIGINT,
                    ingested_at VARCHAR
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS publish_log (
                    version VARCHAR,
                    published_at VARCHAR,
                    row_count BIGINT,
                    period_min VARCHAR,
                    period_max VARCHAR,
                    csv_path VARCHAR
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inspection_snapshots (
                    token VARCHAR PRIMARY KEY,
                    created_at VARCHAR,
                    snapshot_json VARCHAR
                );
            """)
        finally:
            conn.close()

    def get_existing_periods(self) -> List[str]:
        """Return distinct periods currently stored in dataset_records."""
        conn = self._get_connection()
        try:
            # Check if dataset_records table exists
            table_check = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dataset_records'"
            ).fetchone()[0]
            if table_check == 0:
                return []

            period_col = self._clean_ident(self.dataset.period_column)
            res = conn.execute(
                f'SELECT DISTINCT "{period_col}" FROM dataset_records WHERE "{period_col}" IS NOT NULL ORDER BY "{period_col}"'
            ).fetchall()
            return [str(r[0]) for r in res if r[0] is not None]
        except Exception:
            return []
        finally:
            conn.close()

    def get_ingested_files_map(self) -> Dict[str, Dict[str, Any]]:
        """Return dict of {file_hash: row} for already ingested files."""
        conn = self._get_connection()
        try:
            res = conn.execute("SELECT file_hash, file_name, period, ingested_at FROM ingestion_history").fetchall()
            return {
                r[0]: {"file_hash": r[0], "file_name": r[1], "period": r[2], "ingested_at": r[3]}
                for r in res
            }
        finally:
            conn.close()

    def _extract_periods_from_file(self, path: Path) -> List[str]:
        """Extract all distinct normalized periods contained in a CSV file."""
        if not path.exists() or path.stat().st_size == 0:
            return []

        period_col = self.dataset.period_column.strip()
        delim = (self.dataset.delimiter or ",").replace("'", "''")

        try:
            temp_conn = duckdb.connect(":memory:")
            try:
                clean_pcol = self._clean_ident(period_col)
                with self._utf8_csv_path(path) as csv_path:
                    safe_path = csv_path.resolve().as_posix().replace("'", "''")
                    query = f"""
                        SELECT DISTINCT CAST("{clean_pcol}" AS VARCHAR)
                        FROM read_csv('{safe_path}', header=true, delim='{delim}', all_varchar=true)
                        WHERE "{clean_pcol}" IS NOT NULL
                    """
                    rows = temp_conn.execute(query).fetchall()
                norm_periods = set()
                for r in rows:
                    if r[0] is not None:
                        raw_v = str(r[0]).strip()
                        val = normalize_period_value(raw_v)
                        norm_periods.add(val if val else raw_v)
                if norm_periods:
                    return sorted(list(norm_periods))
            finally:
                temp_conn.close()
        except Exception:
            pass

        # Fallback to python csv reader if in-memory duckdb read encounters schema issues
        return self._sample_periods_from_csv(path)

    def _sample_periods_from_csv(self, path: Path) -> List[str]:
        """Fallback period extraction using csv reader."""
        try:
            encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
            for enc in encodings:
                try:
                    with open(path, "r", encoding=enc, errors="replace") as f:
                        reader = csv.reader(f, delimiter=self.dataset.delimiter or ",")
                        header = next(reader, None)
                        if not header:
                            continue
                        period_col = self.dataset.period_column.strip().lower()
                        target_idx = -1
                        for idx, col in enumerate(header):
                            if col.strip().lower() == period_col:
                                target_idx = idx
                                break
                        if target_idx != -1:
                            found_periods = set()
                            for _ in range(500):  # scan up to 500 rows for periods
                                row = next(reader, None)
                                if row is None:
                                    break
                                if len(row) > target_idx:
                                    raw_v = str(row[target_idx]).strip()
                                    norm = normalize_period_value(raw_v)
                                    found_periods.add(norm if norm else raw_v)
                            if found_periods:
                                return sorted(list(found_periods))
                except Exception:
                    continue
        except Exception:
            pass
        return []

    def scan_input_folder(self) -> ScanSummary:
        """Inspect input folder and classify each file as new, modified, unchanged, or conflict."""
        input_dir = Path(self.dataset.input_folder)
        if not input_dir.exists() or not input_dir.is_dir():
            return ScanSummary(
                files=[],
                new_periods=[],
                modified_periods=[],
                unchanged_files=[],
                conflicts=[],
                has_changes=False,
            )

        pattern = self.dataset.file_pattern or "*.csv"
        if getattr(self.dataset, "include_subfolders", False):
            raw_matching = list(input_dir.rglob(pattern))
        else:
            raw_matching = list(input_dir.glob(pattern))
        raw_matching = sorted([p for p in raw_matching if p.is_file()], key=lambda p: p.name)

        # Keyword filtering
        inc_kw = [k.strip().lower() for k in (getattr(self.dataset, "include_keywords", None) or []) if k.strip()]
        exc_kw = [k.strip().lower() for k in (getattr(self.dataset, "exclude_keywords", None) or []) if k.strip()]
        exc_files = set(getattr(self.dataset, "excluded_files", None) or [])
        kw_mode = (getattr(self.dataset, "keyword_mode", "or") or "or").lower()

        matching_files: List[Path] = []
        for p in raw_matching:
            if p.name in exc_files:
                continue
            name_lower = p.name.lower()
            if exc_kw and any(k in name_lower for k in exc_kw):
                continue
            if inc_kw:
                if kw_mode == "and":
                    if not all(k in name_lower for k in inc_kw):
                        continue
                else:  # "or"
                    if not any(k in name_lower for k in inc_kw):
                        continue
            matching_files.append(p)

        existing_periods = set(self.get_existing_periods())
        ingested_map = self.get_ingested_files_map()

        scanned: List[ScannedFile] = []
        period_to_files: Dict[str, List[Path]] = {}
        baseline = getattr(self.dataset, "baseline_columns", None) or []

        for p in matching_files:
            stat = p.stat()
            if stat.st_size == 0:
                scanned.append(
                    ScannedFile(
                        file_path=str(p.resolve()),
                        file_name=p.name,
                        file_size=0,
                        mtime=stat.st_mtime,
                        file_hash="",
                        period="EMPTY",
                        periods=[],
                        row_count_estimate=0,
                        status="conflict",
                        status_detail="0바이트 빈 파일입니다 (처리 제외).",
                        encoding="unknown",
                        header_status="empty",
                        header_detail="0바이트 빈 파일",
                    )
                )
                continue

            fhash = calculate_file_hash(p)

            # Determine encoding
            file_enc = detect_encoding(str(p))

            # Check header
            header_status = "ok"
            header_detail = "정상"
            header_cols: List[str] = []
            try:
                with open(p, "r", encoding=file_enc, errors="replace") as f:
                    reader = csv.reader(f, delimiter=self.dataset.delimiter or ",")
                    first_row = next(reader, None)
                    if first_row:
                        header_cols = [c.strip().lstrip("\ufeff") for c in first_row if c.strip()]
            except Exception:
                pass

            if baseline and header_cols:
                # Required columns: period, keys, numerics
                required = set()
                if self.dataset.period_column:
                    required.add(self.dataset.period_column)
                required.update(self.dataset.key_columns)
                required.update(self.dataset.numeric_columns)

                missing_req = required - set(header_cols)
                if missing_req:
                    header_status = "missing_columns"
                    header_detail = f"필수 열 누락: {', '.join(sorted(missing_req))}"
                elif header_cols != baseline:
                    header_status = "order_mismatch"
                    header_detail = "순서 다름 (이름 기준 정렬)"
                else:
                    header_status = "ok"
                    header_detail = f"✓ {len(header_cols)}열 일치"

            # Determine all periods in file
            file_periods = self._extract_periods_from_file(p)
            if not file_periods:
                fn_period = extract_period_from_filename(p.name)
                if fn_period:
                    file_periods = [fn_period]

            period_str = ", ".join(file_periods) if file_periods else "UNKNOWN"
            for per in (file_periods or ["UNKNOWN"]):
                period_to_files.setdefault(per, []).append(p)

            # Estimate row count quickly
            est_rows = max(0, int(stat.st_size / 150))

            init_status = "pending"
            init_detail = ""
            if header_status == "missing_columns":
                init_status = "conflict"
                init_detail = f"필수 컬럼이 누락되어 적재할 수 없습니다 ({header_detail})."

            scanned.append(
                ScannedFile(
                    file_path=str(p.resolve()),
                    file_name=p.name,
                    file_size=stat.st_size,
                    mtime=stat.st_mtime,
                    file_hash=fhash,
                    period=period_str,
                    periods=file_periods,
                    row_count_estimate=est_rows,
                    status=init_status,
                    status_detail=init_detail,
                    encoding=file_enc,
                    header_status=header_status,
                    header_detail=header_detail,
                )
            )

        new_periods: Set[str] = set()
        modified_periods: Set[str] = set()
        unchanged_files: List[str] = []
        conflicts: List[str] = []

        # Analyze status
        for sf in scanned:
            if sf.status == "conflict":
                conflicts.append(sf.file_name)
                continue

            # Check conflict mode
            has_conflict = False
            for per in sf.periods:
                if len(period_to_files.get(per, [])) > 1 and self.dataset.merge_mode == "error":
                    sf.status = "conflict"
                    sf.status_detail = f"동일 기간({per})에 여러 파일이 존재합니다 (충돌 방지 모드)."
                    conflicts.append(sf.file_name)
                    has_conflict = True
                    break
            if has_conflict:
                continue

            if sf.file_hash in ingested_map:
                sf.status = "unchanged"
                sf.status_detail = "이미 동일한 내용으로 누적된 파일입니다 (변경 없음)."
                unchanged_files.append(sf.file_name)
            else:
                # Check which periods in this file are modified vs new
                file_has_modified = any(per in existing_periods for per in sf.periods)
                for per in sf.periods:
                    if per in existing_periods:
                        modified_periods.add(per)
                    else:
                        new_periods.add(per)

                if file_has_modified:
                    sf.status = "modified_period"
                    sf.status_detail = f"포함된 기간({sf.period}) 중 기존 데이터가 새 내용으로 교체됩니다."
                else:
                    sf.status = "new_period"
                    sf.status_detail = f"신규 기간({sf.period})이 누적 테이블에 추가됩니다."

        has_changes = bool(new_periods or modified_periods)

        return ScanSummary(
            files=scanned,
            new_periods=sorted(list(new_periods)),
            modified_periods=sorted(list(modified_periods)),
            unchanged_files=unchanged_files,
            conflicts=conflicts,
            has_changes=has_changes,
        )

    def _clean_ident(self, name: str) -> str:
        """Escape double quotes for safe SQL identifier quoting."""
        return name.replace('"', '""')

    def accumulate_and_inspect(
        self,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> InspectionResult:
        """
        Execute period-based accumulation and replacement in DuckDB with transactional safety.
        Returns detailed InspectionResult.
        Invalidates any previous inspection approval.
        """
        def report(pct: int, msg: str):
            if progress_callback:
                progress_callback(pct, msg)

        report(5, "입력 파일 스캔 및 변경사항 확인 중...")
        scan = self.scan_input_folder()
        if scan.conflicts:
            raise DatasetValidationError(f"동일 기간 파일 충돌이 감지되었습니다: {', '.join(scan.conflicts)}")

        if not scan.files:
            raise DatasetError("입력 폴더에 처리할 CSV 파일이 없습니다.")

        conn = self._get_connection()
        try:
            # 1. Snapshot BEFORE metrics
            report(15, "반영 전 기존 데이터 상태 집계 중...")
            before_metrics = self._query_summary_metrics(conn)

            table_exists = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = 'ingestion_history'"
            ).fetchone()[0] > 0
            history_rows = (
                conn.execute("SELECT file_name, file_hash, period FROM ingestion_history").fetchall()
                if table_exists
                else []
            )

            # Gather initial target periods from changed files
            changed_files = [f for f in scan.files if f.status in ("new_period", "modified_period")]
            initial_target_periods: Set[str] = set()
            for sf in changed_files:
                for per in sf.periods:
                    initial_target_periods.add(per)

            # If a modified file previously contributed to older periods (period changed/shrunk),
            # include those previous periods in target_periods so orphan records in previous periods are cleared!
            for sf in changed_files:
                for h_name, h_hash, h_per_str in history_rows:
                    if sf.file_name == h_name:
                        for p in (h_per_str or "").split(","):
                            p_norm = p.strip()
                            if p_norm and p_norm != "UNKNOWN":
                                initial_target_periods.add(p_norm)

            # Fixed-point expansion: reinforce all files (including unchanged) that share target periods
            # to guarantee that replacing a period never silently drops records from other files in that period!
            target_periods = set(initial_target_periods)
            files_to_process: List[ScannedFile] = []

            if target_periods:
                while True:
                    added_any = False
                    for sf in scan.files:
                        if sf.status == "conflict":
                            continue
                        if any(p in target_periods for p in sf.periods):
                            if sf not in files_to_process:
                                files_to_process.append(sf)
                                added_any = True
                            for p in sf.periods:
                                if p not in target_periods:
                                    target_periods.add(p)
                                    added_any = True
                    if not added_any:
                        break

            # BLOCK HALF-PERIOD REPLACEMENTS:
            # If a target period previously had MULTIPLE contributing files, ensure that removing
            # one of them while modifying another doesn't cause a silent partial loss!
            if history_rows and target_periods:
                period_to_hist_files: Dict[str, List[Tuple[str, str]]] = {}
                for h_name, h_hash, h_per_str in history_rows:
                    for p in (h_per_str or "").split(","):
                        p_norm = p.strip()
                        if p_norm and p_norm in target_periods:
                            period_to_hist_files.setdefault(p_norm, []).append((h_name, h_hash))

                for per, hist_files in period_to_hist_files.items():
                    if len(hist_files) >= 2:
                        current_files_for_per = [sf for sf in scan.files if per in sf.periods]
                        missing_files = []
                        for h_name, h_hash in hist_files:
                            present = any(sf.file_name == h_name or sf.file_hash == h_hash for sf in scan.files)
                            if not present:
                                missing_files.append(h_name)
                        if missing_files and current_files_for_per:
                            raise DatasetValidationError(
                                f"수정/갱신하려는 기간({per})에 과거 기여했던 파일 '{', '.join(missing_files)}'이 현재 입력 폴더에 없습니다.\n"
                                f"해당 기간의 일부 파일만 교체하면 기존 데이터가 영구 누락(반쪽 교체)되므로 작업을 안전하게 중단합니다.\n"
                                f"기존 파일을 입력 폴더에 함께 유지한 후 다시 실행해 주세요."
                            )

            # Verify input file fingerprints before loading to detect modifications during scan/run
            for sf in files_to_process:
                p = Path(sf.file_path)
                if not p.exists():
                    raise DatasetValidationError(f"입력 파일 '{sf.file_name}'을(를) 찾을 수 없습니다. 파일이 이동되거나 삭제되었는지 확인하세요.")
                stat = p.stat()
                if stat.st_mtime != sf.mtime or stat.st_size != sf.file_size:
                    raise DatasetValidationError(f"스캔 이후 입력 파일 '{sf.file_name}'이(가) 외부에서 수정되었습니다. 다시 스캔 후 진행하세요.")

            # 2. Begin Transaction
            conn.execute("BEGIN TRANSACTION;")

            # If there are periods to replace/ingest
            if target_periods:
                records_exist = conn.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dataset_records'"
                ).fetchone()[0] > 0

                period_col = self._clean_ident(self.dataset.period_column)

                # 2.1 Delete existing data for all target periods upfront (atomic replacement of target periods)
                if records_exist and target_periods:
                    placeholders = ", ".join(["?"] * len(target_periods))
                    p_list = sorted(list(target_periods))
                    conn.execute(f'DELETE FROM dataset_records WHERE "{period_col}" IN ({placeholders});', p_list)
                    conn.execute(f'DELETE FROM ingestion_history WHERE period IN ({placeholders});', p_list)

                # 2.2 Process and ingest files one by one with safe column matching
                total_files = len(files_to_process)
                for f_idx, sf in enumerate(files_to_process, start=1):
                    if cancel_event and cancel_event.is_set():
                        conn.execute("ROLLBACK;")
                        raise DatasetError("작업이 사용자에 의해 취소되었습니다. 모든 변경사항이 롤백되었습니다.")

                    pct = 20 + int((f_idx / total_files) * 50) if total_files > 0 else 50
                    report(pct, f"파일 처리 및 적재 중: {sf.file_name} ({f_idx}/{total_files})...")

                    # Load this file into staging_temp
                    file_row_cnt = self._load_file_into_staging(conn, sf)

                    # Ensure dataset_records exists with correct schema
                    table_exists = conn.execute(
                        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dataset_records'"
                    ).fetchone()[0] > 0

                    if not table_exists:
                        conn.execute("CREATE TABLE dataset_records AS SELECT * FROM staging_temp WHERE 1=0;")

                    # Align schema and handle new columns
                    staging_cols = [
                        r[0]
                        for r in conn.execute(
                            "SELECT column_name FROM information_schema.columns WHERE table_name = 'staging_temp' ORDER BY ordinal_position"
                        ).fetchall()
                    ]
                    records_cols = [
                        r[0]
                        for r in conn.execute(
                            "SELECT column_name FROM information_schema.columns WHERE table_name = 'dataset_records' ORDER BY ordinal_position"
                        ).fetchall()
                    ]
                    records_set = set(records_cols)

                    for sc in staging_cols:
                        if sc not in records_set:
                            clean_sc = self._clean_ident(sc)
                            conn.execute(f'ALTER TABLE dataset_records ADD COLUMN "{clean_sc}" VARCHAR;')
                            records_cols.append(sc)
                            records_set.add(sc)

                    common_cols = [c for c in staging_cols if c in records_set]
                    cols_clause = ", ".join([f'"{self._clean_ident(c)}"' for c in common_cols])

                    conn.execute(f"INSERT INTO dataset_records ({cols_clause}) SELECT {cols_clause} FROM staging_temp;")

                    # Record to ingestion_history
                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO ingestion_history
                        (file_path, file_name, file_hash, file_size, mtime, period, row_count, ingested_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [sf.file_path, sf.file_name, sf.file_hash, sf.file_size, sf.mtime, sf.period, file_row_cnt, now_iso],
                    )

                    conn.execute("DROP TABLE IF EXISTS staging_temp;")

            # Final cancellation check before committing
            if cancel_event and cancel_event.is_set():
                conn.execute("ROLLBACK;")
                raise DatasetError("작업이 사용자에 의해 취소되었습니다. 모든 변경사항이 롤백되었습니다.")

            # 3. Snapshot AFTER metrics & validations INSIDE transaction
            report(75, "반영 후 데이터 무결성 및 수치 검수 계산 중...")
            after_metrics = self._query_summary_metrics(conn)

            # Build Inspection Result
            token = str(uuid.uuid4())
            result = self._build_inspection_result(conn, before_metrics, after_metrics, token)

            # Store snapshot in DuckDB with config fingerprint (inside transaction!)
            snap_payload = asdict(result)
            snap_payload["config_fingerprint"] = self.dataset.get_fingerprint()
            conn.execute(
                "INSERT INTO inspection_snapshots (token, created_at, snapshot_json) VALUES (?, ?, ?)",
                [token, datetime.now(timezone.utc).isoformat(), json.dumps(snap_payload, ensure_ascii=False)],
            )

            # 4. Commit transaction
            report(90, "데이터베이스 트랜잭션 커밋 중...")
            conn.execute("COMMIT;")

            # 5. Invalidate dataset approval state in registry AFTER successful commit
            self.dataset.inspection_approved = False
            self.dataset.approval_token = token
            self.dataset.last_inspected_at = datetime.now(timezone.utc).isoformat()
            self.registry.save_dataset(self.dataset)

            report(100, "누적 및 검수 계산이 완료되었습니다.")
            return result

        except Exception as e:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise e
        finally:
            conn.close()

    @contextmanager
    def _utf8_csv_path(self, path: Path):
        """Decode legacy encodings (CP949, EUC-KR, CP1250, ISO-8859-2, etc.) to temporary UTF-8 CSV."""
        configured = (self.dataset.encoding or "auto").strip().lower()
        if configured == "auto":
            detected = detect_encoding_precise(str(path)).lower()
        else:
            detected = configured

        # If already UTF-8 or ASCII, yield directly without conversion
        if detected in ("utf-8", "utf-8-sig", "ascii", "utf8"):
            yield path
            return

        descriptor, filename = tempfile.mkstemp(suffix=".csv", dir=self.db_path.parent)
        os.close(descriptor)
        converted = Path(filename)
        try:
            with path.open("r", encoding=detected, errors="replace", newline="") as source:
                with converted.open("w", encoding="utf-8", newline="") as target:
                    shutil.copyfileobj(source, target, length=65536)
            yield converted
        finally:
            converted.unlink(missing_ok=True)

    def _load_file_into_staging(self, conn: duckdb.DuckDBPyConnection, sf: ScannedFile) -> int:
        """Load a CSV file into staging_temp table in DuckDB with proper types and return row count."""
        conn.execute("DROP TABLE IF EXISTS staging_temp;")

        # Prepare explicit type overrides
        # Ensure key columns, identifier columns, and period columns are strictly VARCHAR
        types_dict: Dict[str, str] = {}
        for col, col_type in self.dataset.column_types.items():
            types_dict[col] = col_type

        # Force identifiers and period column to VARCHAR if not overridden
        for k in self.dataset.key_columns:
            if k not in types_dict:
                types_dict[k] = "VARCHAR"
        if self.dataset.period_column not in types_dict:
            types_dict[self.dataset.period_column] = "VARCHAR"

        # Build column types parameter for read_csv
        types_clause = ""
        if types_dict:
            pairs = [
                "'{}': '{}'".format(col.replace("'", "''"), t.replace("'", "''"))
                for col, t in types_dict.items()
            ]
            types_clause = f", types={{{', '.join(pairs)}}}"

        delim = (self.dataset.delimiter or ",").replace("'", "''")
        delim_clause = f", delim='{delim}'"

        with self._utf8_csv_path(Path(sf.file_path)) as csv_path:
            safe_path = csv_path.as_posix().replace("'", "''")
            read_sql = f"SELECT * FROM read_csv('{safe_path}', header=true, all_varchar=true{delim_clause}{types_clause})"
            conn.execute(f"CREATE TEMP TABLE staging_temp AS {read_sql};")
        if calculate_file_hash(sf.file_path) != sf.file_hash:
            raise DatasetValidationError("적재 도중 입력 파일이 변경되었습니다. 다시 검수하세요.")

        # Standardize period column in staging_temp
        period_col = self._clean_ident(self.dataset.period_column)
        has_period_col = conn.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'staging_temp' AND column_name = ?",
            [self.dataset.period_column],
        ).fetchone()[0] > 0

        if has_period_col:
            # 1) YYYY[-./_]MM or YYYY[-./_]M
            conn.execute(f"""
                UPDATE staging_temp
                SET "{period_col}" = regexp_replace("{period_col}", '^([0-9]{{4}})[-./_]?([0-9]{{1,2}})$', '\\1-\\2')
                WHERE regexp_matches(CAST("{period_col}" AS VARCHAR), '^[0-9]{{4}}[-./_]?[0-9]{{1,2}}$');
            """)
            # 2) Single digit month YYYY-M -> YYYY-0M
            conn.execute(f"""
                UPDATE staging_temp
                SET "{period_col}" = regexp_replace("{period_col}", '^([0-9]{{4}})-([0-9])$', '\\1-0\\2')
                WHERE regexp_matches(CAST("{period_col}" AS VARCHAR), '^[0-9]{{4}}-[0-9]$');
            """)
            # 3) Korean format: YYYY년 M월 or YYYY년 MM월
            conn.execute(f"""
                UPDATE staging_temp
                SET "{period_col}" = regexp_replace(
                    regexp_replace("{period_col}", '^([0-9]{{4}})[년./\\s]+([0-9]{{1,2}})[월]?$', '\\1-\\2'),
                    '^([0-9]{{4}})-([0-9])$', '\\1-0\\2'
                )
                WHERE regexp_matches(CAST("{period_col}" AS VARCHAR), '^[0-9]{{4}}[년./\\s]+[0-9]{{1,2}}[월]?$');
            """)

        cnt = conn.execute("SELECT count(*) FROM staging_temp").fetchone()[0]
        return int(cnt)

    def _query_summary_metrics(self, conn: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
        """Compute row count, min/max period, and totals for numeric columns."""
        table_exists = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dataset_records'"
        ).fetchone()[0] > 0

        if not table_exists:
            return {
                "exists": False,
                "row_count": 0,
                "period_min": None,
                "period_max": None,
                "periods": [],
                "numeric_totals": {},
            }

        period_col = self._clean_ident(self.dataset.period_column)
        row_count = conn.execute("SELECT count(*) FROM dataset_records").fetchone()[0]

        periods_res = conn.execute(
            f'SELECT DISTINCT "{period_col}" FROM dataset_records WHERE "{period_col}" IS NOT NULL ORDER BY "{period_col}"'
        ).fetchall()
        periods = [str(r[0]) for r in periods_res]
        p_min = periods[0] if periods else None
        p_max = periods[-1] if periods else None

        # Numeric column totals: handle commas and European decimal formats safely
        totals: Dict[str, float] = {}
        for num_col in self.dataset.numeric_columns:
            clean_num = self._clean_ident(num_col)
            try:
                cast_expr = make_numeric_sql_expr(clean_num, self.dataset.number_format)
                val = conn.execute(
                    f'SELECT COALESCE(SUM({cast_expr}), 0) FROM dataset_records'
                ).fetchone()[0]
                totals[num_col] = float(val) if val is not None else 0.0
            except Exception:
                totals[num_col] = 0.0

        return {
            "exists": True,
            "row_count": row_count,
            "period_min": p_min,
            "period_max": p_max,
            "periods": periods,
            "numeric_totals": totals,
        }

    def _build_inspection_result(
        self,
        conn: duckdb.DuckDBPyConnection,
        before: Dict[str, Any],
        after: Dict[str, Any],
        token: str,
    ) -> InspectionResult:
        """Construct detailed InspectionResult comparing before and after states."""
        table_exists = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'dataset_records'"
        ).fetchone()[0] > 0
        if not table_exists:
            return InspectionResult(
                approval_token=token,
                before_row_count=before.get("row_count", 0),
                after_row_count=0,
                before_periods=before.get("periods", []),
                after_periods=[],
                per_period_metrics=[],
                numeric_deltas={},
                warnings=["데이터셋 테이블(dataset_records)이 생성되지 않았습니다."],
                errors=["적재된 유효한 데이터가 없습니다."],
                is_valid=False,
            )

        period_col = self._clean_ident(self.dataset.period_column)

        # Check null periods
        null_periods = conn.execute(
            f'SELECT count(*) FROM dataset_records WHERE "{period_col}" IS NULL OR trim(CAST("{period_col}" AS VARCHAR)) = \'\''
        ).fetchone()[0]

        # Check duplicate keys if key_columns specified
        # Compound key check: evaluate duplicate keys within each period
        dup_keys = 0
        if self.dataset.key_columns:
            keys = [k for k in self.dataset.key_columns if k.lower() != self.dataset.period_column.lower()]
            key_parts = [f'"{period_col}"'] + [f'"{self._clean_ident(k)}"' for k in keys]
            key_expr = ", ".join(key_parts)
            try:
                dup_res = conn.execute(
                    f"SELECT count(*) FROM (SELECT {key_expr}, count(*) FROM dataset_records GROUP BY {key_expr} HAVING count(*) > 1)"
                ).fetchone()[0]
                dup_keys = int(dup_res)
            except Exception:
                dup_keys = 0

        # Calculate per-period metrics
        period_metrics: List[PeriodMetric] = []
        for p in after["periods"]:
            p_rows = conn.execute(
                f'SELECT count(*) FROM dataset_records WHERE "{period_col}" = ?', [p]
            ).fetchone()[0]
            p_measures: Dict[str, float] = {}
            for num_col in self.dataset.numeric_columns:
                clean_num = self._clean_ident(num_col)
                try:
                    cast_expr = make_numeric_sql_expr(clean_num, self.dataset.number_format)
                    val = conn.execute(
                        f'SELECT COALESCE(SUM({cast_expr}), 0) FROM dataset_records WHERE "{period_col}" = ?',
                        [p],
                    ).fetchone()[0]
                    p_measures[num_col] = float(val) if val is not None else 0.0
                except Exception:
                    p_measures[num_col] = 0.0
            period_metrics.append(PeriodMetric(period=p, row_count=p_rows, measures=p_measures))

        # Numeric deltas
        deltas: Dict[str, float] = {}
        for num_col in self.dataset.numeric_columns:
            after_val = after["numeric_totals"].get(num_col, 0.0)
            before_val = before["numeric_totals"].get(num_col, 0.0)
            deltas[num_col] = round(after_val - before_val, 4)

        # Schema columns
        col_names = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'dataset_records' ORDER BY ordinal_position"
            ).fetchall()
        ]

        warnings: List[str] = []
        errors: List[str] = []

        # 1. Period column validation
        if self.dataset.period_column not in col_names:
            errors.append(f"기준 기간 컬럼 '{self.dataset.period_column}'이(가) 데이터셋 테이블에 존재하지 않습니다.")
        else:
            if null_periods > 0:
                errors.append(f"기간 컬럼('{self.dataset.period_column}')에 빈 값(NULL/공백)인 행이 {null_periods:,}개 있습니다.")
            try:
                # Require a canonical calendar month, including a valid month range.
                malformed_cnt = conn.execute(
                    f'SELECT count(*) FROM dataset_records WHERE "{period_col}" IS NOT NULL '
                    f'AND TRIM(CAST("{period_col}" AS VARCHAR)) != \'\' '
                    f'AND NOT REGEXP_FULL_MATCH(CAST("{period_col}" AS VARCHAR), \'[0-9]{{4}}-(0[1-9]|1[0-2])\')'
                ).fetchone()[0]
                if malformed_cnt > 0:
                    errors.append(f"기간 컬럼('{self.dataset.period_column}')에 잘못된 기간 형식 행이 {malformed_cnt:,}개 있습니다.")
            except Exception as exc:
                raise DatasetValidationError("기간 형식을 검증할 수 없습니다.") from exc

        # 2. Key column validation
        for k in self.dataset.key_columns:
            if k not in col_names:
                errors.append(f"식별 키 컬럼 '{k}'이(가) 데이터셋 테이블에 존재하지 않습니다.")
            else:
                clean_k = self._clean_ident(k)
                try:
                    null_k_cnt = conn.execute(
                        f'SELECT count(*) FROM dataset_records WHERE "{clean_k}" IS NULL OR TRIM(CAST("{clean_k}" AS VARCHAR)) = \'\''
                    ).fetchone()[0]
                    if null_k_cnt > 0:
                        errors.append(f"식별 키 컬럼('{k}')에 빈 값(NULL/공백)인 행이 {null_k_cnt:,}개 있습니다.")
                except Exception:
                    pass

        # 3. Numeric column validations
        for num_col in self.dataset.numeric_columns:
            if num_col not in col_names:
                errors.append(f"지정된 수치 컬럼 '{num_col}'이(가) 데이터셋 테이블에 존재하지 않습니다.")
                continue

            clean_num = self._clean_ident(num_col)
            try:
                # Detect values that fail TRY_CAST (e.g. non-numeric text)
                cast_expr = make_numeric_sql_expr(clean_num, self.dataset.number_format)
                bad_cnt = conn.execute(
                    f'SELECT count(*) FROM dataset_records WHERE "{clean_num}" IS NOT NULL '
                    f'AND TRIM(CAST("{clean_num}" AS VARCHAR)) != \'\' '
                    f'AND ({cast_expr}) IS NULL'
                ).fetchone()[0]
                if bad_cnt > 0:
                    errors.append(
                        f"수치 컬럼 '{num_col}'에 숫자로 변환할 수 없는 잘못된 값(문자열 등)이 {bad_cnt:,}건 포함되어 있습니다."
                    )
            except Exception:
                pass

            # Detect if column is entirely null in non-empty dataset
            if after["row_count"] > 0:
                try:
                    cast_expr = make_numeric_sql_expr(clean_num, self.dataset.number_format)
                    valid_num_cnt = conn.execute(
                        f'SELECT count(*) FROM dataset_records WHERE "{clean_num}" IS NOT NULL '
                        f'AND TRIM(CAST("{clean_num}" AS VARCHAR)) != \'\' '
                        f'AND ({cast_expr}) IS NOT NULL'
                    ).fetchone()[0]
                    if valid_num_cnt == 0:
                        errors.append(f"수치 컬럼 '{num_col}'에 유효한 숫자 데이터가 전혀 없습니다 (전체 NULL 또는 빈값).")
                except Exception:
                    pass

        # Duplicate keys: error or warning depending on merge_mode
        if dup_keys > 0:
            if self.dataset.merge_mode == "error":
                errors.append(f"지정된 식별 키 기준 중복 행이 {dup_keys:,}건 발견되었습니다 (중복 불허 모드).")
            else:
                warnings.append(f"지정된 식별 키 기준 중복 행이 {dup_keys:,}건 발견되었습니다.")

        if after["row_count"] == 0:
            errors.append("최종 데이터셋에 데이터 행이 전혀 없습니다.")

        added_rows = max(0, after["row_count"] - before["row_count"])
        replaced_rows = before["row_count"] if before["row_count"] > 0 and added_rows == 0 else 0

        is_valid = (len(errors) == 0) and (after["row_count"] > 0)

        return InspectionResult(
            dataset_id=self.dataset.id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            approval_token=token,
            before_period_min=before["period_min"],
            before_period_max=before["period_max"],
            before_row_count=before["row_count"],
            after_period_min=after["period_min"],
            after_period_max=after["period_max"],
            after_row_count=after["row_count"],
            added_row_count=added_rows,
            replaced_row_count=replaced_rows,
            all_periods=after["periods"],
            period_metrics=period_metrics,
            numeric_totals_before=before["numeric_totals"],
            numeric_totals_after=after["numeric_totals"],
            numeric_deltas=deltas,
            null_period_count=null_periods,
            duplicate_key_count=dup_keys,
            schema_columns=col_names,
            warnings=warnings,
            errors=errors,
            is_valid=is_valid,
        )

    def approve_inspection(self, token: str) -> None:
        """Mark inspection as approved for the given token with strict verification."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT snapshot_json FROM inspection_snapshots WHERE token = ?",
                [token],
            ).fetchone()
            if not row:
                raise DatasetValidationError("유효한 검수 스냅샷을 찾을 수 없습니다. 다시 누적 및 검수를 진행하세요.")

            snap = json.loads(row[0])

            # 1. Error check
            if not snap.get("is_valid", False) or snap.get("errors"):
                err_list = snap.get("errors", [])
                raise DatasetValidationError(
                    "검수 결과에 미해결 오류가 있어 승인할 수 없습니다:\n" + "\n".join(f"- {e}" for e in err_list)
                )

            # 2. Config fingerprint check
            snap_fp = snap.get("config_fingerprint")
            current_fp = self.dataset.get_fingerprint()
            if snap_fp and snap_fp != current_fp:
                raise DatasetValidationError(
                    "검수 이후 데이터셋의 구조나 설정(컬럼, 키, 구분자 등)이 변경되었습니다.\n"
                    "변경된 설정으로 다시 누적 및 검수를 진행한 후 승인해 주세요."
                )

            # 3. DB current metrics check against snapshot
            curr_metrics = self._query_summary_metrics(conn)
            if (
                curr_metrics["row_count"] != snap.get("after_row_count")
                or curr_metrics["period_min"] != snap.get("after_period_min")
                or curr_metrics["period_max"] != snap.get("after_period_max")
            ):
                raise DatasetValidationError("검수 이후 데이터베이스 내용이 변경되었습니다. 다시 검수를 실행하세요.")

            for num_col, s_tot in snap.get("numeric_totals_after", {}).items():
                c_tot = curr_metrics["numeric_totals"].get(num_col, 0.0)
                if abs(c_tot - s_tot) > 0.0001:
                    raise DatasetValidationError(
                        f"검수 이후 수치 컬럼('{num_col}')의 합계가 변경되었습니다. 다시 검수를 실행하세요."
                    )

            self.dataset.inspection_approved = True
            self.dataset.approval_token = token
            self.dataset.last_inspected_at = datetime.now(timezone.utc).isoformat()
            self.registry.save_dataset(self.dataset)
        finally:
            conn.close()

    def get_latest_inspection(self) -> Optional[InspectionResult]:
        """Fetch the most recent inspection snapshot."""
        conn = self._get_connection()
        try:
            res = conn.execute(
                "SELECT snapshot_json FROM inspection_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not res:
                return None
            data = json.loads(res[0])
            # Reconstruct dataclass safely
            data.pop("config_fingerprint", None)
            known_fields = set(InspectionResult.__dataclass_fields__.keys())
            filtered_data = {k: v for k, v in data.items() if k in known_fields}
            period_metrics = [PeriodMetric(**pm) for pm in filtered_data.get("period_metrics", [])]
            filtered_data["period_metrics"] = period_metrics
            return InspectionResult(**filtered_data)
        except Exception:
            return None
        finally:
            conn.close()

    def publish_dataset(
        self,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Safely publish the accumulated dataset to the shared publish folder.
        Guarantees atomic replacement, backup of previous published version,
        and UTF-8-SIG CSV formatting for direct Excel compatibility.
        """
        def report(pct: int, msg: str):
            if progress_callback:
                progress_callback(pct, msg)

        # 1. Verification of approval and strict snapshot binding with current DB state
        if not self.dataset.inspection_approved or not self.dataset.approval_token:
            raise DatasetValidationError("검수가 승인되지 않은 상태에서는 배포할 수 없습니다. '결과 확인 및 검수 승인'을 먼저 진행하세요.")

        conn = self._get_connection()
        try:
            snap_res = conn.execute(
                "SELECT snapshot_json FROM inspection_snapshots WHERE token = ?",
                [self.dataset.approval_token],
            ).fetchone()
            if not snap_res:
                self.dataset.inspection_approved = False
                self.registry.save_dataset(self.dataset)
                raise DatasetValidationError("승인된 검수 스냅샷을 찾을 수 없습니다. 다시 누적 및 검수를 진행하세요.")

            snap_data = json.loads(snap_res[0])
            current_metrics = self._query_summary_metrics(conn)

            # Strict state validation: row count, periods, and numeric totals must match snapshot exactly
            if (
                current_metrics["row_count"] != snap_data.get("after_row_count")
                or current_metrics["period_min"] != snap_data.get("after_period_min")
                or current_metrics["period_max"] != snap_data.get("after_period_max")
            ):
                self.dataset.inspection_approved = False
                self.registry.save_dataset(self.dataset)
                raise DatasetValidationError(
                    "검수 승인 이후 데이터베이스 내용이 변경되었습니다 (행수 또는 기간 불일치).\n"
                    "데이터 무결성을 보호하기 위해 배포가 차단되었습니다. 다시 검수를 실행하고 승인해 주세요."
                )

            # Check numeric totals match snapshot
            snap_totals = snap_data.get("numeric_totals_after", {})
            for num_col, snap_val in snap_totals.items():
                curr_val = current_metrics["numeric_totals"].get(num_col, 0.0)
                if abs(curr_val - snap_val) > 0.0001:
                    self.dataset.inspection_approved = False
                    self.registry.save_dataset(self.dataset)
                    raise DatasetValidationError(
                        f"검수 승인 이후 수치 컬럼('{num_col}')의 합계가 변경되었습니다.\n"
                        f"(승인 시점: {snap_val:,.2f} vs 현재 DB: {curr_val:,.2f})\n"
                        "다시 검수를 실행하고 승인해 주세요."
                    )
        finally:
            conn.close()

        publish_dir = Path(self.dataset.publish_folder)
        if not publish_dir.exists():
            try:
                publish_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise DatasetError(f"배포 대상 폴더를 생성할 수 없습니다: {publish_dir}\n{e}")

        report(10, "배포 대상 폴더 및 권한 점검 중...")

        current_dir = publish_dir / "current"
        versions_dir = publish_dir / "versions"
        backup_dir = publish_dir / "_backup"
        staging_dir = publish_dir / "_staging"

        current_dir.mkdir(parents=True, exist_ok=True)
        versions_dir.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        report(25, "배포용 CSV 파일 생성 중 (DuckDB 내보내기)...")

        # Determine version number
        manifest_file = publish_dir / "manifest.json"
        prev_version_num = 0
        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    mdata = json.load(f)
                    prev_version_num = mdata.get("version_number", 0)
            except Exception:
                pass

        new_version_num = prev_version_num + 1
        now_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        version_str = f"v{new_version_num:03d}"
        clean_name = re.sub(r"[^\w\-_]", "_", self.dataset.name) or "dataset"

        temp_csv = staging_dir / f"{clean_name}_{version_str}_{uuid.uuid4().hex[:6]}.tmp"

        # Export from DuckDB to temp_csv
        conn = self._get_connection()
        try:
            duck_export_path = temp_csv.as_posix().replace("'", "''")
            period_col = self._clean_ident(self.dataset.period_column)
            order_clause = f'ORDER BY "{period_col}"'

            conn.execute(f"""
                COPY (
                    SELECT * FROM dataset_records {order_clause}
                ) TO '{duck_export_path}' (HEADER, DELIMITER ',');
            """)

            row_count = conn.execute("SELECT count(*) FROM dataset_records").fetchone()[0]
            periods_res = conn.execute(
                f'SELECT min("{period_col}"), max("{period_col}") FROM dataset_records'
            ).fetchone()
            p_min = str(periods_res[0]) if periods_res[0] is not None else ""
            p_max = str(periods_res[1]) if periods_res[1] is not None else ""
        finally:
            conn.close()

        report(60, "Excel 호환 UTF-8 BOM 인코딩 확인 및 무결성 검증 중...")
        self._ensure_utf8_bom(temp_csv)

        report(75, "안전한 버전 보관 및 현재 버전(current) 원자적 교체 중...")

        target_current_csv = self.dataset.published_csv_path()
        current_csv_name = target_current_csv.name
        backup_csv = backup_dir / f"{clean_name}_prev.csv"
        version_csv_name = f"{clean_name}_{version_str}_{now_ts}.csv"
        version_csv_path = versions_dir / version_csv_name

        version_created: Optional[Path] = None
        current_existed = target_current_csv.exists()
        manifest_existed = manifest_file.exists()
        current_replaced = False
        manifest_replaced = False
        previous_published_at = self.dataset.last_published_at

        try:
            # 0. Back up existing manifest.json if present
            backup_manifest = backup_dir / "manifest_prev.json"
            if manifest_file.exists():
                if backup_manifest.exists():
                    backup_manifest.unlink()
                shutil.copy2(manifest_file, backup_manifest)

            # 1. Back up existing current file if present
            if target_current_csv.exists():
                if backup_csv.exists():
                    backup_csv.unlink()
                shutil.copy2(target_current_csv, backup_csv)

            # 2. Safely replace current file FIRST.
            # If target_current_csv is locked by Excel, PermissionError will raise here
            # BEFORE any version file is created in versions/ directory!
            temp_csv.replace(target_current_csv)
            current_replaced = True

            # 3. Create permanent version copy only after successful current replacement
            shutil.copy2(target_current_csv, version_csv_path)
            version_created = version_csv_path

            # 4. Write manifest.json atomically
            report(90, "배포 매니페스트(manifest.json) 및 이력 업데이트 중...")
            manifest_payload = {
                "dataset_id": self.dataset.id,
                "dataset_name": self.dataset.name,
                "version": version_str,
                "version_number": new_version_num,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "published_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "current_csv_file": f"current/{current_csv_name}",
                "current_csv_absolute": str(target_current_csv.resolve()),
                "version_csv_file": f"versions/{version_csv_name}",
                "row_count": row_count,
                "period_column": self.dataset.period_column,
                "period_min": p_min,
                "period_max": p_max,
                "columns": self.dataset.numeric_columns,
            }

            tmp_manifest = publish_dir / "manifest.json.tmp"
            with open(tmp_manifest, "w", encoding="utf-8") as f:
                json.dump(manifest_payload, f, ensure_ascii=False, indent=2)
            tmp_manifest.replace(manifest_file)
            manifest_replaced = True

            # 5. Record publish log in DuckDB
            conn = self._get_connection()
            try:
                conn.execute(
                    "INSERT INTO publish_log (version, published_at, row_count, period_min, period_max, csv_path) VALUES (?, ?, ?, ?, ?, ?)",
                    [version_str, datetime.now(timezone.utc).isoformat(), row_count, p_min, p_max, str(target_current_csv)],
                )
            finally:
                conn.close()

            # Update dataset definition
            self.dataset.last_published_at = datetime.now(timezone.utc).isoformat()
            self.registry.save_dataset(self.dataset)

            # Clean staging dir
            shutil.rmtree(staging_dir, ignore_errors=True)

            report(100, f"배포가 성공적으로 완료되었습니다! ({version_str})")
            return manifest_payload

        except Exception as pe:
            # Atomic rollback on any failure: restore previous published file and manifest
            if temp_csv.exists():
                temp_csv.unlink(missing_ok=True)
            if version_created and version_created.exists():
                version_created.unlink(missing_ok=True)
            if current_replaced:
                if current_existed:
                    restore = staging_dir / f"restore_{uuid.uuid4().hex}.tmp"
                    shutil.copy2(backup_csv, restore)
                    restore.replace(target_current_csv)
                else:
                    target_current_csv.unlink(missing_ok=True)
            if manifest_replaced:
                if manifest_existed:
                    restore_manifest = publish_dir / f"restore_{uuid.uuid4().hex}.tmp"
                    shutil.copy2(backup_manifest, restore_manifest)
                    restore_manifest.replace(manifest_file)
                else:
                    manifest_file.unlink(missing_ok=True)
            self.dataset.last_published_at = previous_published_at
            tmp_m = publish_dir / "manifest.json.tmp"
            if tmp_m.exists():
                tmp_m.unlink(missing_ok=True)

            if isinstance(pe, PermissionError):
                raise DatasetPublishLockError(
                    f"공유 폴더의 파일이 Excel 등 다른 프로그램에서 사용 중(잠김 상태)입니다.\n\n"
                    f"대상 파일: {target_current_csv}\n\n"
                    f"Excel을 닫거나 파일을 닫은 후 다시 배포해 주세요."
                ) from pe
            raise pe

    def _ensure_utf8_bom(self, file_path: Path) -> None:
        """Prepend UTF-8 BOM if not present using chunked streaming to avoid high memory usage."""
        with open(file_path, "rb") as f:
            header = f.read(3)
        if header != b"\xef\xbb\xbf":
            temp_bom_file = file_path.with_name(f"{file_path.name}.bom.tmp")
            with open(file_path, "rb") as src, open(temp_bom_file, "wb") as dst:
                dst.write(b"\xef\xbb\xbf")
                shutil.copyfileobj(src, dst, length=64 * 1024)
            temp_bom_file.replace(file_path)
