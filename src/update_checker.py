"""Small, dependency-free GitHub release checker used after the UI is visible."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from src.app_paths import ensure_user_settings_directory

DEFAULT_REPOSITORY = "KwangBeomPark/04_DataRefinery"
CHECK_INTERVAL = timedelta(hours=24)
REQUEST_TIMEOUT_SECONDS = 2
APPLICATION_NAME = "Data Refinery"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    url: str
    notes: str
    asset_name: str | None = None
    download_url: str | None = None
    size: int = 0


def application_data_directory() -> Path:
    return ensure_user_settings_directory()


def settings_path() -> Path:
    return application_data_directory() / "settings.json"


def get_update_download_directory() -> Path:
    target = application_data_directory() / "updates"
    target.mkdir(parents=True, exist_ok=True)
    return target


def load_settings(path: Path | None = None) -> dict:
    target = path or settings_path()
    try:
        settings = json.loads(target.read_text(encoding="utf-8"))
        return settings if isinstance(settings, dict) else {"update_check_enabled": True}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"update_check_enabled": True}


def save_settings(settings: dict, path: Path | None = None) -> None:
    from src.atomic_write import atomic_write_json

    target = path or settings_path()
    # A read fallback is for display only, never permission to erase bad content.
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("Cannot validate existing Data Refinery settings; the original was retained.") from exc
    else:
        if not isinstance(existing, dict):
            raise OSError("Existing Data Refinery settings are not an object; the original was retained.")
    atomic_write_json(target, settings)


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


def fetch_latest_release(
    repository: str = DEFAULT_REPOSITORY,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    opener: Callable = urlopen,
) -> ReleaseInfo | None:
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

    # Search for Windows setup executable asset
    asset_name = None
    download_url = None
    size = 0
    assets = payload.get("assets", [])
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name", ""))
            if name.endswith(".exe") and ("setup" in name.lower() or "datarefinery" in name.lower()):
                asset_name = name
                download_url = str(asset.get("browser_download_url", ""))
                size = int(asset.get("size", 0))
                break
        if not download_url:
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name", ""))
                if name.endswith(".exe"):
                    asset_name = name
                    download_url = str(asset.get("browser_download_url", ""))
                    size = int(asset.get("size", 0))
                    break

    return ReleaseInfo(
        version=version,
        url=url,
        notes=str(payload.get("body", "")),
        asset_name=asset_name,
        download_url=download_url,
        size=size,
    )


def download_release_asset(
    download_url: str,
    target_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    chunk_size: int = 65536,
    timeout: int = 15,
    opener: Callable = urlopen,
) -> Path:
    """Download a release asset with progress updates and atomic replacement."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = target_path.with_suffix(target_path.suffix + ".part")

    req = Request(
        download_url,
        headers={
            "User-Agent": "Data-Refinery-Update-Checker",
            "Accept": "application/octet-stream",
        },
    )
    try:
        with opener(req, timeout=timeout) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            with open(part_path, "wb") as f:
                while True:
                    if cancel_check and cancel_check():
                        raise InterruptedError("Download cancelled by user.")
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)
            if total_size > 0 and downloaded < total_size:
                raise OSError(f"Incomplete download: received {downloaded} of {total_size} bytes.")
        if target_path.exists():
            target_path.unlink()
        part_path.replace(target_path)
        return target_path
    except Exception:
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass
        raise


def check_for_update(
    current_version: str,
    settings: dict,
    now: datetime | None = None,
    fetcher: Callable = fetch_latest_release,
    on_error: Callable[[Exception], None] | None = None,
) -> ReleaseInfo | None:
    """Return a newer release or None. Network failures deliberately stay silent."""
    now = now or datetime.now(timezone.utc)
    if not should_check(settings, now):
        return None
    try:
        release = fetcher()
    except Exception as error:
        if on_error is not None:
            on_error(error)
        return None
    if release is None:
        if on_error is not None:
            on_error(ValueError("Release response was invalid."))
        return None
    settings["last_update_check"] = now.isoformat()
    current = version_key(current_version)
    latest = version_key(release.version)
    return release if current is not None and latest is not None and latest > current else None
