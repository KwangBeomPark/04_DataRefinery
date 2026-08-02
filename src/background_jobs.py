"""Reusable background-job coordination for Tkinter-facing application code."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


ResultT = TypeVar("ResultT")
ProgressCallback = Callable[[int, str], None]
Worker = Callable[[ProgressCallback], ResultT]
Scheduler = Callable[[int, Callable[[], None]], object]


@dataclass(frozen=True)
class JobCallbacks(Generic[ResultT]):
    on_progress: ProgressCallback
    on_success: Callable[[ResultT], None]
    on_error: Callable[[Exception], None]
    on_finished: Callable[[], None]


class BackgroundJobRunner:
    """Run named work off the UI thread and dispatch its events on the UI thread."""

    def __init__(self, schedule: Scheduler, poll_interval_ms: int = 40):
        self._schedule = schedule
        self._poll_interval_ms = poll_interval_ms
        self._events: queue.Queue[tuple[str, str, object]] = queue.Queue()
        self._callbacks: dict[str, JobCallbacks] = {}
        self._poll_scheduled = False

    def is_running(self, name: str) -> bool:
        return name in self._callbacks

    def start(self, name: str, worker: Worker, callbacks: JobCallbacks) -> bool:
        if self.is_running(name):
            return False
        self._callbacks[name] = callbacks

        def report(percent: int, detail: str) -> None:
            self._events.put((name, "progress", (percent, detail)))

        def run() -> None:
            try:
                result = worker(report)
            except Exception as error:
                self._events.put((name, "error", error))
            else:
                self._events.put((name, "success", result))

        threading.Thread(target=run, name=name, daemon=True).start()
        self._request_poll(0)
        return True

    def _request_poll(self, delay_ms: int) -> None:
        if self._poll_scheduled:
            return
        self._poll_scheduled = True
        self._schedule(delay_ms, self.poll)

    def poll(self) -> None:
        self._poll_scheduled = False
        while True:
            try:
                name, kind, payload = self._events.get_nowait()
            except queue.Empty:
                break

            callbacks = self._callbacks.get(name)
            if callbacks is None:
                continue
            if kind == "progress":
                percent, detail = payload
                callbacks.on_progress(percent, detail)
                continue

            try:
                if kind == "success":
                    callbacks.on_success(payload)
                else:
                    callbacks.on_error(payload)
            finally:
                self._callbacks.pop(name, None)
                callbacks.on_finished()

        if self._callbacks:
            self._request_poll(self._poll_interval_ms)
