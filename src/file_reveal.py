"""Hand a finished file to the operating system's file manager or default app.

The aggregator offers "폴더 열기" and "파일 열기" after a successful run, so every
entry point here is a UI button: a missing file, a locked path or a desktop
without a file manager must end as a `False` return, never as an exception that
takes the app down.  No Tkinter and no third-party dependency is used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def open_containing_folder(path: str) -> bool:
    """Open the file's folder in the OS file manager, selecting the file when possible.

    Returns True when the file manager could be started, False when the path
    does not exist or the OS call failed.
    """
    target = _resolved(path)
    if target is None:
        return False
    if target.is_dir():
        return _open_with_default_app(target)  # the folder itself is what the user wants to see
    if sys.platform.startswith("win"):
        # explorer.exe reports a non-zero exit code even when it opened the
        # window as asked, so its exit status is deliberately not checked; only
        # a failure to start the process counts as failure.
        return _run(["explorer", f"/select,{target}"], trust_exit_code=False)
    if sys.platform == "darwin":
        return _run(["open", "-R", str(target)], trust_exit_code=True)
    # Linux file managers have no portable "select this file" flag.
    return _run(["xdg-open", str(target.parent)], trust_exit_code=True)


def open_file(path: str) -> bool:
    """Open a file with the OS default application.

    Returns True when the application could be started, False when the path
    does not exist or the OS call failed.
    """
    target = _resolved(path)
    if target is None:
        return False
    return _open_with_default_app(target)


def _open_with_default_app(target: Path) -> bool:
    if sys.platform.startswith("win"):
        try:
            os.startfile(str(target))  # Windows-only API; the branch guards it.
        except OSError:
            return False
        return True
    if sys.platform == "darwin":
        return _run(["open", str(target)], trust_exit_code=True)
    return _run(["xdg-open", str(target)], trust_exit_code=True)


def _resolved(path: str) -> Optional[Path]:
    """Return an existing absolute target, or None when the path is unusable.

    Resolving before any command is built is also the argument-injection guard:
    only a real file or directory ever reaches a command line, and it always
    travels as a single list element.
    """
    if not path:
        return None
    try:
        return Path(path).expanduser().resolve(strict=True)
    except (OSError, ValueError):
        return None


def _run(command: List[str], *, trust_exit_code: bool) -> bool:
    """Start a file-manager command without a shell, reporting success as bool."""
    try:
        completed = subprocess.run(command, check=False)  # list + no shell: never interpreted
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return not trust_exit_code or completed.returncode == 0
