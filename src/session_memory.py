"""Remember the configuration a source file was last aggregated with.

Picking a file the user already worked on should bring back the layout they
left, so every successful run records its configuration here keyed by the
source path.  The store is a pure convenience: it must never make the app fail,
so a missing, empty or damaged file simply behaves as "nothing remembered".
The configuration itself is an opaque JSON object supplied by the caller — this
module deliberately knows nothing about presets or aggregation specs and must
stay free of Tkinter and of `preset_manager` / `data_aggregator` imports.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.update_checker import application_data_directory

MEMORY_SCHEMA_VERSION = 1
MEMORY_FILENAME = "recent_configurations.json"
MAX_REMEMBERED_FILES = 50


def _store_path() -> Path:
    """Return the JSON file holding every remembered configuration.

    Tests monkeypatch this helper so they never touch the user's AppData.
    """
    return application_data_directory() / MEMORY_FILENAME


def _entry_key(file_path: str) -> str:
    """Normalise a source path so case and separator variants share one entry."""
    return os.path.normcase(os.path.abspath(str(file_path)))


def _empty_store() -> Dict[str, Any]:
    return {"version": MEMORY_SCHEMA_VERSION, "entries": {}}


def _atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text content atomically using a temporary file and os.replace."""
    temp_path = file_path.with_name(f"{file_path.name}.tmp_{os.getpid()}")
    try:
        temp_path.write_text(content, encoding=encoding)
        os.replace(temp_path, file_path)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise


def _load_store() -> Dict[str, Any]:
    """Read the store, degrading to an empty one on any read or parse failure."""
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()

    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
        return _empty_store()

    # A half-written or hand-edited file can hold entries of the wrong shape;
    # dropping just those keeps the rest of the store usable.
    entries: Dict[str, Any] = {}
    for key, entry in raw["entries"].items():
        if isinstance(entry, dict) and isinstance(entry.get("configuration"), dict):
            entries[str(key)] = entry
    return {"version": MEMORY_SCHEMA_VERSION, "entries": entries}


def _save_store(store: Dict[str, Any]) -> None:
    """Persist the store atomically, staying silent when the disk refuses."""
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, json.dumps(store, ensure_ascii=False, indent=2))
    except OSError:
        # A store that cannot be written is a lost convenience, never a failed run.
        pass


def _evict_overflow(entries: Dict[str, Any]) -> None:
    """Trim the store back to the cap, dropping least recently updated entries."""
    overflow = len(entries) - MAX_REMEMBERED_FILES
    if overflow <= 0:
        return
    # The sort is stable and `remember()` re-inserts a refreshed entry at the end,
    # so a burst of writes sharing one clock tick still evicts the oldest first.
    ordered: List[str] = [
        key
        for key, entry in sorted(entries.items(), key=lambda item: str(item[1].get("updated_at", "")))
    ]
    for key in ordered[:overflow]:
        entries.pop(key, None)


def remember(file_path: str, configuration: Dict[str, Any]) -> None:
    """Record the configuration last used for this source file.

    Raises ValueError when the configuration is not a JSON-serialisable dict;
    the stored file is left untouched in that case.
    """
    if not isinstance(configuration, dict):
        raise ValueError("configuration must be a dict.")
    try:
        # Serialising up front both validates the caller's value and detaches the
        # stored copy, so later mutation of their dict cannot reach the store.
        payload = json.loads(json.dumps(configuration, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"configuration must be JSON-serialisable: {exc}") from exc

    store = _load_store()
    entries = store["entries"]
    key = _entry_key(file_path)
    entries.pop(key, None)  # re-insert at the end so dict order tracks recency
    entries[key] = {
        "file_path": str(file_path),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "configuration": payload,
    }
    _evict_overflow(entries)
    _save_store(store)


def recall(file_path: str) -> Optional[Dict[str, Any]]:
    """Return the configuration last used for this source file, if any."""
    entry = _load_store()["entries"].get(_entry_key(file_path))
    if not isinstance(entry, dict):
        return None
    configuration = entry.get("configuration")
    return configuration if isinstance(configuration, dict) else None


def forget(file_path: str) -> bool:
    """Drop the entry for one file. True if there was one."""
    store = _load_store()
    if store["entries"].pop(_entry_key(file_path), None) is None:
        return False
    _save_store(store)
    return True


def clear() -> None:
    """Drop every entry."""
    _save_store(_empty_store())


def prune_missing() -> int:
    """Drop entries whose source file is gone and return how many were dropped."""
    store = _load_store()
    entries = store["entries"]
    # The key is always an absolute path, so existence does not depend on the cwd.
    vanished = [key for key in entries if not os.path.exists(key)]
    for key in vanished:
        entries.pop(key, None)
    if vanished:
        _save_store(store)
    return len(vanished)
