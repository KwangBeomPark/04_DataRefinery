"""Privacy-conscious local error records for support requests."""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.update_checker import application_data_directory


_LOCK = threading.Lock()
_LOGGER = logging.getLogger("data_refinery.diagnostics")
_LOGGER.propagate = False


def diagnostics_directory() -> Path:
    return application_data_directory() / "logs"


def record_error(operation: str, error: Exception) -> str:
    """Return a support ID and log only exception type and code locations.

    Exception messages and absolute paths can contain customer data, so neither
    is written to the diagnostic log.
    """
    error_id = uuid.uuid4().hex[:10].upper()
    frames = traceback.extract_tb(error.__traceback__)
    code_locations = " > ".join(
        f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
        for frame in frames[-8:]
    ) or "unknown"
    try:
        with _LOCK:
            if not _LOGGER.handlers:
                directory = diagnostics_directory()
                directory.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    directory / "errors.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
                )
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                _LOGGER.addHandler(handler)
            _LOGGER.error(
                "id=%s operation=%s exception=%s.%s frames=%s",
                error_id,
                operation,
                type(error).__module__,
                type(error).__name__,
                code_locations,
            )
    except OSError:
        # Logging cannot turn a recoverable application error into another one.
        pass
    return error_id
