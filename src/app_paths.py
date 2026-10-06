"""PL Suite storage convention and one-time, non-destructive legacy migration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

APPLICATION_NAME = "Data Refinery"
_MIGRATION_MARKER = ".storage-v1.json"


def local_appdata_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def installation_directory() -> Path:
    return local_appdata_directory() / "Programs" / APPLICATION_NAME


def user_settings_directory() -> Path:
    return installation_directory() / "UserSetting"


def legacy_settings_directories() -> tuple[Path, ...]:
    base = local_appdata_directory()
    return (base / APPLICATION_NAME, base / "CSV Modifier")


def _legacy_dataset_directories() -> tuple[Path, ...]:
    if os.environ.get("LOCALAPPDATA"):
        return (local_appdata_directory() / "DataRefinery" / "datasets",)
    if os.environ.get("APPDATA"):
        return (Path(os.environ["APPDATA"]) / "DataRefinery" / "datasets",)
    return (Path.home() / ".datarefinery" / "datasets",)


def _snapshot(directory: Path) -> dict[str, str]:
    """Reject links and detect content changes before publishing a migrated tree."""
    result = {}
    for path in (directory, *sorted(directory.rglob("*"))):
        if path.is_symlink() or path.is_junction():
            raise OSError("설정 이관 대상에 연결된 폴더/파일이 있습니다.")
        if path == directory:
            continue
        relative = path.relative_to(directory).as_posix()
        if path.is_dir():
            result[relative + "/"] = "directory"
            continue
        if path.name.endswith(".wal"):
            raise OSError("작업 DB가 사용 중이거나 복구가 필요합니다. 기존 앱을 종료한 뒤 다시 실행하세요.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result[relative] = digest.hexdigest()
    return result


def _merge_missing(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.iterdir()):
        destination = target / path.name
        if path.is_dir():
            if destination.is_file():
                raise OSError("이관 대상에서 파일/폴더 이름이 충돌합니다.")
            _merge_missing(path, destination)
        elif not destination.exists():
            shutil.copy2(path, destination)


def _acquire_migration_lock(path: Path) -> BinaryIO:
    """Use an OS lock released on process exit, so a crash cannot leave a stale lock."""
    stream = path.open("a+b")
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise


def ensure_user_settings_directory() -> Path:
    """Copy legacy data once; retain originals and prefer existing new settings.

    A staged complete tree is published by rename. DB read locks, hashes, and
    a migration marker prevent partial copies, active writers, and resurrection
    of old settings after users intentionally delete their new configuration.
    """
    target = user_settings_directory()
    if target.is_symlink() or target.is_junction():
        raise OSError("UserSetting 경로가 연결된 폴더입니다. 실제 저장 폴더를 확인하세요.")
    if (target / _MIGRATION_MARKER).is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / ".usersetting-migration.lock"
    try:
        lock = _acquire_migration_lock(lock_path)
    except OSError as error:
        raise OSError("다른 앱에서 설정을 이관 중입니다. 해당 작업을 종료한 뒤 다시 실행하세요.") from error
    stage: Path | None = None
    backup: Path | None = None
    try:
        with lock, ExitStack() as stack:
            # Another process may have completed immediately before the lock.
            if (target / _MIGRATION_MARKER).is_file():
                return target
            sources = [path for path in legacy_settings_directories() if path.is_dir()]
            dataset_source = next((path for path in _legacy_dataset_directories() if path.is_dir()), None)
            roots = sources + ([target] if target.exists() else [])
            if dataset_source is not None:
                roots.append(dataset_source)
            before = {root: _snapshot(root) for root in roots}
            # Hold read locks while copying legacy DBs, preventing another
            # process from opening these same files for writes during migration.
            for root in roots:
                for path in sorted(root.rglob("*.duckdb")):
                    import duckdb

                    connection = duckdb.connect(str(path), read_only=True)
                    stack.callback(connection.close)
            stage = Path(tempfile.mkdtemp(prefix=".usersetting-stage-", dir=target.parent))
            expected = dict(before.get(target, {}))
            if target.exists():
                _merge_missing(target, stage)
            for source in sources:
                _merge_missing(source, stage)
                for relative, digest in before[source].items():
                    expected.setdefault(relative, digest)
            # Never merge stale legacy databases into a new dataset workspace.
            if dataset_source is not None and not (stage / "datasets").exists():
                shutil.copytree(dataset_source, stage / "datasets")
                expected["datasets/"] = "directory"
                expected.update({"datasets/" + relative: digest for relative, digest in before[dataset_source].items()})
            if any(_snapshot(root) != snapshot for root, snapshot in before.items()):
                raise OSError("이관 중 기존 자료가 변경되었습니다. 기존 앱을 종료한 뒤 다시 실행하세요.")
            if _snapshot(stage) != expected:
                raise OSError("이관 사본 검증에 실패했습니다. 기존 자료는 보존되어 있습니다.")
            (stage / _MIGRATION_MARKER).write_text(json.dumps({"storage_version": 1}), encoding="utf-8")
            if target.exists():
                # Preserve even an existing UserSetting tree as a backup.
                backup = target.with_name("UserSetting.before-migration-" + stage.name.rsplit("-", 1)[-1])
                target.rename(backup)
            try:
                stage.rename(target)
            except OSError:
                if backup is not None:
                    backup.rename(target)
                raise
            stage = None
            return target
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
        # Keep the tiny lock file: removing it after unlocking could race with
        # another process holding a lock on that same file.


def dataset_storage_directory() -> Path:
    directory = ensure_user_settings_directory() / "datasets"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
