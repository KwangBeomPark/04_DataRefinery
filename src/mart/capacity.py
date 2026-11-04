"""File-length and caller-available-space observations for mart operations."""
import ctypes
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Measurement:
    value: int | None
    target: str
    source: str
    observed_at: datetime
    status: str = "ok"
    stale: bool = False


def file_size(path, *, absent_zero=False):
    now = datetime.now(timezone.utc)
    try:
        size = Path(path).stat().st_size
        return Measurement(size, str(path), "stat", now)
    except FileNotFoundError:
        return Measurement(0 if absent_zero else None, str(path), "stat", now,
                           "absent" if absent_zero else "unknown")
    except OSError:
        return Measurement(None, str(path), "stat", now, "unknown")


def available_space(path):
    """Return space available to this caller, including OS-enforced quotas."""
    p = Path(path).resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    if p.is_file():
        p = p.parent
    now = datetime.now(timezone.utc)
    try:
        if os.name == "nt":
            free = ctypes.c_ulonglong()
            fn = ctypes.windll.kernel32.GetDiskFreeSpaceExW
            if not fn(str(p), ctypes.byref(free), None, None):
                raise OSError("GetDiskFreeSpaceExW failed")
            value = free.value
            source = "GetDiskFreeSpaceExW caller available"
        else:
            value = shutil.disk_usage(p).free
            source = "disk_usage caller available"
        return Measurement(value, str(p), source, now)
    except OSError:
        return Measurement(None, str(p), "available space", now, "unknown")


def available_memory():
    """Observe currently available physical memory; this is not a reservation."""
    now = datetime.now(timezone.utc)
    try:
        if os.name == 'nt':
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                            ('total_physical', ctypes.c_ulonglong), ('available_physical', ctypes.c_ulonglong),
                            ('total_pagefile', ctypes.c_ulonglong), ('available_pagefile', ctypes.c_ulonglong),
                            ('total_virtual', ctypes.c_ulonglong), ('available_virtual', ctypes.c_ulonglong),
                            ('available_extended_virtual', ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                raise OSError('GlobalMemoryStatusEx failed')
            value = status.available_physical
        else:
            value = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        return Measurement(value, 'physical memory', 'OS available', now)
    except (OSError, ValueError):
        return Measurement(None, 'physical memory', 'OS available', now, 'unknown')


def collect_capacity(db_paths, budget_bytes=None, warning_active=False, *,
                     groups=None, probe=file_size):
    if budget_bytes is not None and budget_bytes <= 0:
        raise ValueError("budget must be positive")
    db = [probe(p) for p in db_paths]
    wal = [probe(str(p) + ".wal", absent_zero=True) for p in db_paths]
    all_active = db + wal
    complete = bool(db) and all(m.value is not None and not m.stale for m in all_active)
    known = sum(m.value for m in all_active if m.value is not None)
    ratio = known / budget_bytes if complete and budget_bytes else None
    warning = None if ratio is None else (ratio >= .75 if warning_active else ratio >= .80)
    panels = {}
    seen = set()
    for name, paths in (groups or {}).items():
        measurements = []
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                measurements.append(probe(path))
        panels[name] = {"items": measurements,
                        "known_bytes": sum(m.value for m in measurements if m.value is not None),
                        "complete": all(m.value is not None and not m.stale for m in measurements)}
    return {"db": db, "wal": wal, "known_bytes": known, "complete": complete,
            "budget_bytes": budget_bytes, "ratio": ratio, "warning": warning, "groups": panels}


def evaluate_preflight(requirements, *, estimate_supported, reserve_bytes=2 * 1024**3,
                       space_probe=available_space, volume_probe=None, active_complete=True):
    """Sum incremental byte estimates once per volume; unknown inputs block writes."""
    if reserve_bytes < 0:
        raise ValueError("negative reserve")
    if not estimate_supported or not active_complete or not requirements:
        return {"allowed": False, "reason": "unverified estimate or active files", "volumes": {}}
    if volume_probe is None:
        def volume_probe(path):
            p = Path(path).resolve()
            while not p.exists() and p != p.parent:
                p = p.parent
            if p.is_file():
                p = p.parent
            if os.name == "nt":
                # Volume serial identifies mount points as well as drive letters.
                volume_path = ctypes.create_unicode_buffer(32768)
                serial = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetVolumePathNameW(str(p), volume_path, len(volume_path)):
                    raise OSError("unknown volume")
                if not ctypes.windll.kernel32.GetVolumeInformationW(volume_path, None, 0, ctypes.byref(serial), None, None, None, 0):
                    raise OSError("unknown volume")
                return (volume_path.value.casefold(), serial.value)
            return p.stat().st_dev
    volumes = {}
    try:
        for path, needed in requirements:
            if needed is None or needed < 0:
                raise ValueError("unknown estimate")
            key = volume_probe(path)
            if key not in volumes:
                volumes[key] = {"required": 0, "available": space_probe(path)}
            volumes[key]["required"] += needed
    except (OSError, ValueError):
        return {"allowed": False, "reason": "unknown volume or estimate", "volumes": volumes}
    allowed = all(v["available"].value is not None and v["available"].status == "ok" and not v["available"].stale and
                  v["required"] + reserve_bytes <= v["available"].value for v in volumes.values())
    return {"allowed": allowed, "reason": "ok" if allowed else "insufficient or unknown space", "volumes": volumes}

@dataclass(frozen=True)
class SupportedProfile:
    """Low-confidence profile, not a resource guarantee.

    Synthetic Windows / Python 3.13.14 / DuckDB 1.5.6 run: 100,000 rows,
    five columns, one dimension, DECIMAL(18,2), max cell 125 UTF-8 bytes.
    Input 16,466,702 bytes, CSV 16,466,705 bytes; sampled DB peak
    42,491,904, WAL 1,867, other files 65,867,568; process peak working
    set 303,431,680 bytes. Sampling every 50 ms can miss short peaks.
    One run does not establish bounds for other value distributions.

    A second synthetic run used four dimensions (one composite key), 12
    attributes, 16 DECIMAL(18,2) measures and 64 columns. Ten 100,000-row,
    152,589,293-byte inputs reached one million rows; each dimension had at
    most 1,000 distinct keys, and cells were at most 36 UTF-8 bytes. A
    grouped two-measure export with a dimension filter, publication, and
    backup succeeded. Peaks sampled every 250 ms: DB 164,114,432 bytes,
    WAL 1,867, temp 0, workspace 1,690,027,639; peak process working set
    895,553,536 bytes. Afterwards 26 one-row periods were added and the
    grouped export/backup repeated. A separately restored one-million-row
    copy was then checked with this preflight: its raw wide output was
    285,389,011 bytes, two-key grouping and period/equals filters worked,
    and an existing 100,000-row scope was corrected to 99,974 rows before
    adding 26 one-row periods. Reconciliation passed at 36 periods and
    one million rows. This is not a 36-full-period test.

    A third synthetic run used four two-column dimension keys, 10,000
    members per dimension, 12 attributes, 64 DECIMAL(18,2) measures,
    128 source columns, an eight-column row key, two partition columns,
    and 125-byte UTF-8 cells. One 100,000-row input was 192,212,063 bytes.
    Observed every 250 ms: DB peak 29,372,416, WAL 1,867, temp 87,883,776,
    workspace 309,470,214 and peak working set 1,035,313,152 bytes. Raw
    output of 100,000 rows and grouped, filtered and unfiltered outputs
    completed. The larger 64-measure/128-column shape was not tested at
    one million rows; it is restricted to 100,000 accumulated rows.
    These studies are best-effort evidence, not guaranteed peak bounds.
    """
    rows: int = 100_000
    columns: int = 128
    cell_bytes: int = 125
    db_bytes: int = 512 * 1024**2


class ResourceCancelled(Exception):
    """User cancelled before the operation completed."""


class ResourceLimitError(OSError):
    """Free space fell below the operational reserve, or became unknown."""


class ResourcePreflight:
    """Best-effort capacity estimate for a restricted, measured profile."""
    accepts_context = True
    memory_limit = '512MB'
    temp_limit = '2GB'

    def __init__(self, *, reserve_bytes=2 * 1024**3, space_probe=available_space, volume_probe=None, memory_probe=available_memory, cancel=None):
        self.profile = SupportedProfile()
        self.reserve = reserve_bytes
        self.space_probe = space_probe
        self.volume_probe = volume_probe
        self.memory_probe = memory_probe
        self.cancel = cancel

    def _check_cancel(self):
        if self.cancel is not None and self.cancel.is_set():
            raise ResourceCancelled('사용자가 작업을 취소했습니다')

    def _check_memory(self, complex_shape):
        observed = self.memory_probe()
        minimum = (2 * 1024**3) if complex_shape else (768 * 1024**2)
        if observed.value is None or observed.status != 'ok' or observed.stale or observed.value < minimum:
            raise ValueError('현재 사용 가능한 메모리가 부족하거나 확인되지 않습니다')
        return observed

    @staticmethod
    def _blocked(reason):
        return {'allowed': False, 'reason': reason, 'confidence': 'low', 'volumes': {}}

    def _shape(self, model):
        from dataclasses import asdict, is_dataclass
        if is_dataclass(model):
            model = asdict(model)
        if not isinstance(model, dict):
            raise TypeError('모델을 확인할 수 없습니다')
        dims, measures = model.get('dimensions', ()), model.get('measures', ())
        if not measures or any(m.get('precision', 0) > 18 for m in measures):
            raise ValueError('측정된 지표 정밀도는 18 이하입니다')
        if not 1 <= len(model.get('row_key_columns', ())) <= 8 or len(model.get('partition_columns', ())) > 2:
            raise ValueError('측정 범위는 행 키 최대 8개, 분할 열 최대 2개입니다')
        fields = {model['period_column'], *model.get('partition_columns', ()), *model.get('row_key_columns', ()), *model.get('excluded_columns', ())}
        for dim in dims:
            fields.update(dim['key_columns'])
            fields.update(dim.get('attributes', ()))
            if dim.get('authority_column'):
                fields.add(dim['authority_column'])
        fields.update(m['column'] for m in measures)
        small = (len(dims) == 1 and len(measures) == 1 and len(fields) <= 5 and
                 len(dims[0].get('key_columns', ())) == 1 and len(dims[0].get('attributes', ())) <= 1 and
                 not model.get('excluded_columns'))
        complex_shape = (1 <= len(dims) <= 4 and len(measures) <= 64 and len(fields) <= 128 and
                         all(1 <= len(d.get('key_columns', ())) <= 2 for d in dims) and
                         all(len(d.get('attributes', ())) <= 3 for d in dims) and
                         sum(len(d.get('attributes', ())) for d in dims) <= 12)
        if not small and not complex_shape:
            raise ValueError('측정 범위는 차원 최대 4개, 지표 최대 64개, 원본 최대 128열입니다')
        return fields, model, not small

    def _inspect(self, files, fields, model, complex_shape):
        import csv

        from .engine import _hash_file
        rows = size = width = 0
        widths = None
        hashes = []
        members = [set() for _ in model['dimensions']]
        cell_limit = self.profile.cell_bytes
        for path in files:
            p = Path(path)
            before = p.stat()
            digest = _hash_file(p)
            size += before.st_size
            with p.open(encoding='utf-8-sig', newline='') as source:
                reader = csv.reader(source, strict=True)
                header = next(reader, None)
                if not header or len(header) != len(set(header)) or set(header) != fields:
                    raise ValueError('원본 헤더가 모델과 다릅니다')
                if any(len(name.encode('utf-8')) > 125 for name in header):
                    raise ValueError('원본 열 이름이 측정 범위를 초과합니다')
                indexes = [[header.index(key) for key in d['key_columns']] for d in model['dimensions']]
                if widths is None:
                    widths = {name: 0 for name in header}
                for row in reader:
                    if rows % 1000 == 0:
                        self._check_cancel()
                    rows += 1
                    if len(row) != len(header):
                        raise ValueError('원본 행의 열 수가 다릅니다')
                    lengths = [len(v.encode('utf-8')) for v in row]
                    width = max(width, *lengths)
                    for name, length in zip(header, lengths):
                        widths[name] = max(widths[name], length)
                    if rows > self.profile.rows or width > cell_limit:
                        raise ValueError(f'측정 범위 초과: 최대 100,000행, 셀 {cell_limit}바이트입니다. 논리 범위별로 나누거나 열 값을 줄여 주세요')
                    for index, key in enumerate(indexes):
                        members[index].add(tuple(row[i] for i in key))
                        if len(members[index]) > (10_000 if complex_shape else 100_000):
                            raise ValueError('측정 범위를 벗어난 차원 고유 키입니다: 차원별 최대 10,000개')
            after = p.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest != _hash_file(p):
                raise ValueError('검사 중 원본이 변경되었습니다')
            hashes.append(digest)
        return rows, size, widths or {}, hashes, members

    def __call__(self, operation, store, size, **context):
        import csv

        import duckdb
        try:
            if size < 0:
                raise ValueError('크기를 확인할 수 없습니다')
            dbsize = file_size(store.database, absent_zero=True)
            wal = file_size(str(store.database) + '.wal', absent_zero=True)
            if dbsize.value is None or wal.value is None:
                raise ValueError('DB 또는 WAL 크기를 확인할 수 없습니다')
            if dbsize.value > self.profile.db_bytes:
                raise ValueError('DB가 측정된 프로필을 초과합니다')
            destination = context.get('destination', store.root)
            if operation == 'load':
                fields, model, complex_shape = self._shape(context.get('model'))
                memory = self._check_memory(complex_shape)
                rows, observed, widths, hashes, members = self._inspect(context.get('files', ()), fields, model, complex_shape)
                if not context.get('files') or observed != size:
                    raise ValueError('원본 크기를 확인할 수 없습니다')
                if dbsize.value:
                    from dataclasses import asdict, is_dataclass
                    model = context['model']
                    model = asdict(model) if is_dataclass(model) else model
                    db = duckdb.connect(str(store.database), read_only=True)
                    try:
                        saved = db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()
                        if saved:
                            import json
                            if json.loads(saved[0]) != json.loads(json.dumps(model)):
                                raise ValueError('기존 모델이 다릅니다')
                            from .engine import _ident
                            current = db.execute('SELECT COUNT(*) FROM ' + _ident('fact_' + model['fact_table'])).fetchone()[0]
                            scope_text = json.dumps(context.get('scope'), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                            previous = db.execute('SELECT row_count FROM mart_scopes WHERE scope=?', [scope_text]).fetchone()
                            scopes = db.execute('SELECT COUNT(*) FROM mart_scopes').fetchone()[0]
                            if scopes >= 36 and not previous:
                                raise ValueError('측정 범위인 36개 기간을 초과합니다')
                            row_limit = 100_000 if len(fields) > 64 or len(model['measures']) > 16 else 1_000_000
                            if current - (previous[0] if previous else 0) + rows > row_limit:
                                raise ValueError(f'누적 {row_limit:,}행을 초과합니다. 작업 공간을 나누어 주세요')
                            key_limit = 10_000 if complex_shape else 100_000
                            for index, dim in enumerate(model['dimensions']):
                                count = db.execute('SELECT COUNT(*) FROM mart_dimensions WHERE dimension=?', [dim['name']]).fetchone()[0]
                                if count > key_limit:
                                    raise ValueError(f'기존 차원 키가 측정 범위인 {key_limit:,}개를 초과합니다')
                                existing = db.execute('SELECT natural_key FROM mart_dimensions WHERE dimension=?', [dim['name']]).fetchall()
                                old = {tuple(json.loads(item[0])) for item in existing}
                                if len(old | members[index]) > key_limit:
                                    raise ValueError(f'기존과 신규 차원 키 합계가 차원당 {key_limit:,}개를 초과합니다')
                    finally:
                        db.close()
                typed = rows * (64 + sum(2 * width + 32 for width in widths.values()))
                requirements = [(store.root, 512 * 1024**2 + 8 * typed + 3 * size + 4 * dbsize.value + 2 * wal.value)]
            elif operation == 'export':
                fields, model, complex_shape = self._shape(context.get('model'))
                memory = self._check_memory(complex_shape)
                definition, db = context.get('definition'), context.get('db')
                if db is None or definition is None or definition.format != 'wide':
                    raise ValueError('측정 범위는 wide CSV 출력입니다')
                if len(definition.group_by) > 8 or len(definition.filters) > 2 or len(definition.columns) > 128:
                    raise ValueError('측정 범위는 그룹 키 최대 8개, 필터 최대 2개, 출력 최대 128열입니다')
                if any(len(str(value).encode('utf-8')) > self.profile.cell_bytes for _, value in definition.filters):
                    raise ValueError('필터 값이 측정 범위인 125바이트를 초과합니다')
                scratch = (2 * 1024**3 if definition.group_by else 512 * 1024**2) + 2 * dbsize.value
                scan_space = evaluate_preflight([(store.root, scratch)],
                                                estimate_supported=True, reserve_bytes=self.reserve,
                                                space_probe=self.space_probe, volume_probe=self.volume_probe)
                if not scan_space['allowed']:
                    return self._blocked('출력 크기 검사에 필요한 여유 공간이 부족하거나 불명입니다')
                from .engine import MartEngine, _ident
                total = db.execute('SELECT COUNT(*) FROM ' + _ident('fact_' + context['model']['fact_table'])).fetchone()[0]
                row_limit = 100_000 if len(fields) > 64 or len(model['measures']) > 16 else 1_000_000
                if total > row_limit:
                    raise ValueError('기존 팩트가 측정된 행 수를 초과합니다')
                cursor = MartEngine._export_query(db, context['model'], definition, context.get('scope'))
                rows = 0
                output = sum(2 * len(c.encode('utf-8')) + 3 for c in definition.columns) + 5
                while batch := cursor.fetchmany(1000):
                    self.check_remaining(store.root)
                    for row in batch:
                        rows += 1
                        if rows > row_limit:
                            raise ValueError('출력 행이 측정된 범위를 초과합니다. 범위별로 나눠 주세요')
                        if any(len(str(v if v is not None else '').encode('utf-8')) > self.profile.cell_bytes for v in row):
                            raise ValueError('출력 셀이 측정된 최대 크기를 초과합니다')
                        output += sum(2 * len(str(v if v is not None else '').encode('utf-8')) + 3 for v in row) + 2
                requirements = [(store.root, scratch), (destination, output + 4096)]
            elif operation in ('publish', 'backup', 'restore'):
                if operation == 'restore' and size > self.profile.db_bytes:
                    raise ValueError('복원본이 측정된 DB 크기 범위를 초과합니다')
                required = 2 * (size + (wal.value if operation == 'backup' else 0)) + 1024**2
                if operation == 'restore':
                    required = 4 * size + 512 * 1024**2
                requirements = [(destination, required)]
                if operation == 'backup':
                    requirements.append((store.root, 2 * dbsize.value + 128 * 1024**2))
            else:
                raise ValueError('지원하지 않는 작업입니다')
            result = evaluate_preflight(requirements, estimate_supported=True, reserve_bytes=self.reserve,
                                        space_probe=self.space_probe, volume_probe=self.volume_probe)
            result.update(confidence='low', estimate='실측 기반 추정이며 공간 보장은 아닙니다')
            if operation == 'load':
                result['input_hashes'] = hashes
            if operation in ('load', 'export'):
                result['memory'] = memory
            return result
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, csv.Error, duckdb.Error) as exc:
            return self._blocked(str(exc))

    def check_remaining(self, path):
        self._check_cancel()
        measurement = self.space_probe(path)
        if measurement.value is None or measurement.status != 'ok' or measurement.stale or measurement.value < self.reserve:
            raise ResourceLimitError('사용 가능 공간이 부족하거나 확인할 수 없어 작업을 중단했습니다')
