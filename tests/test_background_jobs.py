import queue
import threading
import time
import unittest

from src.background_jobs import BackgroundJobRunner, JobCallbacks


class TestBackgroundJobRunner(unittest.TestCase):
    def _drain_until_finished(self, scheduled, finished):
        deadline = time.monotonic() + 2
        while not finished.is_set() and time.monotonic() < deadline:
            try:
                callback = scheduled.get(timeout=0.05)
            except queue.Empty:
                continue
            callback()
        self.assertTrue(finished.is_set(), "background job did not finish")

    def test_dispatches_progress_and_success_through_the_scheduler(self):
        scheduled = queue.Queue()
        finished = threading.Event()
        events = []
        ui_thread_id = threading.get_ident()

        runner = BackgroundJobRunner(
            lambda _delay, callback: scheduled.put(callback),
            poll_interval_ms=1,
        )

        def worker(report):
            events.append(("worker-thread", threading.get_ident()))
            report(25, "working")
            return 42

        started = runner.start(
            "example",
            worker,
            JobCallbacks(
                on_progress=lambda percent, detail: events.append((detail, percent)),
                on_success=lambda result: events.append(("success", result)),
                on_error=lambda error: events.append(("error", str(error))),
                on_finished=finished.set,
            ),
        )
        self.assertTrue(started)
        self.assertFalse(
            runner.start(
                "example",
                worker,
                JobCallbacks(
                    on_progress=lambda *_: None,
                    on_success=lambda *_: None,
                    on_error=lambda *_: None,
                    on_finished=lambda: None,
                ),
            )
        )

        self._drain_until_finished(scheduled, finished)

        worker_thread_id = next(value for kind, value in events if kind == "worker-thread")
        self.assertNotEqual(worker_thread_id, ui_thread_id)
        self.assertIn(("working", 25), events)
        self.assertIn(("success", 42), events)
        self.assertFalse(runner.is_running("example"))

    def test_dispatches_worker_errors_and_always_finishes(self):
        scheduled = queue.Queue()
        finished = threading.Event()
        errors = []
        runner = BackgroundJobRunner(lambda _delay, callback: scheduled.put(callback), 1)

        def worker(_report):
            raise ValueError("broken")

        runner.start(
            "failing",
            worker,
            JobCallbacks(
                on_progress=lambda *_: None,
                on_success=lambda *_: None,
                on_error=lambda error: errors.append(str(error)),
                on_finished=finished.set,
            ),
        )
        self._drain_until_finished(scheduled, finished)

        self.assertEqual(errors, ["broken"])
        self.assertFalse(runner.is_running("failing"))
