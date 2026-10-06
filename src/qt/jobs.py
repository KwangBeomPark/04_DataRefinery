"""Qt adapter for BackgroundJobRunner."""

from __future__ import annotations

from PySide6.QtCore import QTimer

from src.background_jobs import BackgroundJobRunner


def create_qt_job_runner(poll_interval_ms: int = 40) -> BackgroundJobRunner:
    """Create a BackgroundJobRunner backed by PySide6 QTimer.singleShot."""
    return BackgroundJobRunner(
        schedule=lambda delay_ms, fn: QTimer.singleShot(delay_ms, fn),
        poll_interval_ms=poll_interval_ms,
    )
