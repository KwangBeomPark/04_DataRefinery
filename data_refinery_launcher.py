"""One-file bootstrap launcher for the per-user Data Refinery installation."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from tkinter import Tk, Toplevel, ttk, messagebox
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


APPLICATION_NAME = "Data Refinery"
REPOSITORY = "KwangBeomPark/DataRefinery"
INSTALLER_NAME_PATTERN = re.compile(r"App04_DataRefinery_Setup_v(\d+)\.(\d+)\.(\d+)\.exe\Z")
APPLICATION_NAME_PATTERN = re.compile(r"App04_DataRefinery_v(\d+)\.(\d+)\.(\d+)\.exe\Z")
INSTALLER_ARGUMENTS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")
REQUEST_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
INSTALLER_APP_ID = "{2E1A7E3F-8D78-4DB0-9B62-50B12CD4326F}_is1"


class LauncherError(RuntimeError):
    """A user-facing launcher error."""


@dataclass(frozen=True)
class InstallerAsset:
    name: str
    url: str
    size: int


def _local_appdata(local_appdata: Path | None = None) -> Path:
    if local_appdata is not None:
        return Path(local_appdata)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def _version_from_name(path: Path, pattern: re.Pattern[str]) -> tuple[int, int, int] | None:
    match = pattern.fullmatch(path.name)
    return tuple(int(value) for value in match.groups()) if match else None


def _installed_executables(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return [
        path
        for path in directory.glob("App04_DataRefinery_v*.exe")
        if path.is_file() and _version_from_name(path, APPLICATION_NAME_PATTERN) is not None
    ]


def _registry_install_directory() -> Path | None:
    """Read the app's per-user Inno Setup record when Windows has one."""
    try:
        import winreg

        uninstall_key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INSTALLER_APP_ID}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
        return Path(value) if value else None
    except (FileNotFoundError, OSError, ImportError):
        return None


def find_installed_executable(
    local_appdata: Path | None = None,
    include_registry: bool = True,
) -> Path | None:
    """Return the newest valid installed executable, or ``None`` when absent."""
    appdata = _local_appdata(local_appdata)
    directories = [appdata / "Programs" / APPLICATION_NAME]
    if include_registry:
        registry_directory = _registry_install_directory()
        if registry_directory is not None and registry_directory not in directories:
            directories.append(registry_directory)

    candidates = [path for directory in directories for path in _installed_executables(directory)]
    if not candidates:
        return None
    return max(candidates, key=lambda path: _version_from_name(path, APPLICATION_NAME_PATTERN) or (0, 0, 0))


def _is_trusted_installer_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "github.com"
        and parsed.path.startswith(f"/{REPOSITORY}/releases/download/")
    )


def fetch_latest_installer(
    opener: Callable = urlopen,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> InstallerAsset:
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Data-Refinery-Launcher",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise LauncherError("GitHub에서 최신 설치 정보를 가져오지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도하세요.") from error

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise LauncherError("최신 릴리스에 설치 파일 정보가 없습니다.")

    matches = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        size = asset.get("size", 0)
        if INSTALLER_NAME_PATTERN.fullmatch(name) and _is_trusted_installer_url(url) and isinstance(size, int) and size > 0:
            matches.append(InstallerAsset(name=name, url=url, size=size))

    if len(matches) != 1:
        raise LauncherError("공식 Data Refinery 설치 파일을 찾지 못했습니다.")
    return matches[0]


def download_installer(
    asset: InstallerAsset,
    destination: Path,
    opener: Callable = urlopen,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Path:
    request = Request(asset.url, headers={"User-Agent": "Data-Refinery-Launcher"})
    received = 0
    try:
        with opener(request, timeout=timeout) as response, destination.open("wb") as output:
            while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                output.write(chunk)
                received += len(chunk)
    except Exception as error:
        destination.unlink(missing_ok=True)
        raise LauncherError("설치 파일을 내려받지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도하세요.") from error

    if received != asset.size:
        destination.unlink(missing_ok=True)
        raise LauncherError("설치 파일 다운로드가 완전하지 않습니다. 다시 시도하세요.")
    return destination


def install_latest_release(installer: Path) -> None:
    try:
        result = subprocess.run([str(installer), *INSTALLER_ARGUMENTS], check=False)
    except OSError as error:
        raise LauncherError("설치 프로그램을 실행하지 못했습니다.") from error
    if result.returncode != 0:
        raise LauncherError(f"설치 프로그램이 완료되지 않았습니다. (오류 코드: {result.returncode})")


def launch_application(executable: Path) -> None:
    try:
        subprocess.Popen([str(executable)], cwd=str(executable.parent))
    except OSError as error:
        raise LauncherError("설치된 Data Refinery를 실행하지 못했습니다.") from error


class BusyDialog:
    """A small non-modal progress notice while network and setup work runs."""

    def __init__(self, root: Tk, message: str):
        self.window = Toplevel(root)
        self.window.title(APPLICATION_NAME)
        self.window.resizable(False, False)
        self.window.transient(root)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)
        self.label = ttk.Label(self.window, text=message, padding=(24, 16), wraplength=360)
        self.label.grid(row=0, column=0, sticky="ew")
        self.progress = ttk.Progressbar(self.window, mode="indeterminate", length=300)
        self.progress.grid(row=1, column=0, padx=24, pady=(0, 18), sticky="ew")
        self.progress.start(12)
        self.window.update_idletasks()
        self.window.grab_set()

    def set_message(self, message: str) -> None:
        self.label.configure(text=message)
        self.window.update_idletasks()

    def close(self) -> None:
        self.progress.stop()
        self.window.grab_release()
        self.window.destroy()


def run_launcher() -> int:
    root = Tk()
    root.withdraw()
    root.title(APPLICATION_NAME)
    busy: BusyDialog | None = None
    try:
        installed = find_installed_executable()
        if installed is not None:
            launch_application(installed)
            return 0

        should_install = messagebox.askyesno(
            f"{APPLICATION_NAME} 설치",
            f"{APPLICATION_NAME}가 설치되어 있지 않습니다.\n\nGitHub에서 최신 버전을 내려받아 설치하시겠습니까?",
            icon="question",
            parent=root,
        )
        if not should_install:
            return 0

        busy = BusyDialog(root, "최신 설치 정보를 확인하는 중입니다…")
        asset = fetch_latest_installer()
        busy.set_message("Data Refinery 최신 설치 파일을 내려받는 중입니다…")
        with tempfile.TemporaryDirectory(prefix="DataRefinery-") as temp_directory:
            installer = download_installer(asset, Path(temp_directory) / asset.name)
            busy.set_message("다운로드가 끝났습니다. Data Refinery를 설치하는 중입니다…")
            install_latest_release(installer)

        installed = find_installed_executable()
        if installed is None:
            raise LauncherError("설치는 완료됐지만 실행 파일을 찾지 못했습니다.")
        busy.set_message("설치가 완료되었습니다. Data Refinery를 시작하는 중입니다…")
        launch_application(installed)
        return 0
    except LauncherError as error:
        messagebox.showerror(f"{APPLICATION_NAME} 런처", str(error), parent=root)
        return 1
    finally:
        if busy is not None and busy.window.winfo_exists():
            busy.close()
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(run_launcher())
