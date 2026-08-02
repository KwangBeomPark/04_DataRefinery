"""Small, dependency-free GitHub release checker used after the UI is visible."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen


DEFAULT_REPOSITORY = "KwangBeomPark/DataRefinery"
CHECK_INTERVAL = timedelta(hours=24)
REQUEST_TIMEOUT_SECONDS = 2
APPLICATION_NAME = "Data Refinery"
LEGACY_APPLICATION_NAMES = ("CSV Modifier",)


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str
    notes: str


def application_data_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), "AppData", "Local")
    return Path(base) / APPLICATION_NAME


def settings_path() -> Path:
    return application_data_directory() / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    target = path or settings_path()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if path is None:
            base = target.parent.parent
            for legacy_name in LEGACY_APPLICATION_NAMES:
                legacy_path = base / legacy_name / "settings.json"
                try:
                    return json.loads(legacy_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        return {"update_check_enabled": True}


def save_settings(settings: dict, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def version_key(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if match is None:
        return None
    return tuple(int(piece) for piece in match.groups())


def should_check(settings: dict, now: datetime) -> bool:
    if not settings.get("update_check_enabled", True):
        return False
    value = settings.get("last_update_check")
    if not value:
        return True
    try:
        previous = datetime.fromisoformat(value)
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    return now - previous >= CHECK_INTERVAL


def fetch_latest_release(repository: str = DEFAULT_REPOSITORY, timeout: int = REQUEST_TIMEOUT_SECONDS, opener: Callable = urlopen) -> ReleaseInfo | None:
    request = Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Data-Refinery-Update-Checker",
        },
    )
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = str(payload.get("tag_name", "")).lstrip("v")
    url = str(payload.get("html_url", ""))
    if version_key(version) is None or not url:
        return None
    return ReleaseInfo(version=version, url=url, notes=str(payload.get("body", "")))


def check_for_update(current_version: str, settings: dict, now: datetime | None = None, fetcher: Callable = fetch_latest_release) -> ReleaseInfo | None:
    """Return a newer release or None. Network failures deliberately stay silent."""
    now = now or datetime.now(timezone.utc)
    if not should_check(settings, now):
        return None
    settings["last_update_check"] = now.isoformat()
    try:
        release = fetcher()
    except Exception:
        return None
    if release is None:
        return None
    current = version_key(current_version)
    latest = version_key(release.version)
    return release if current is not None and latest is not None and latest > current else None
