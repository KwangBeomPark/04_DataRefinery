import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import diagnostics


class TestDiagnostics(unittest.TestCase):
    def _close_handlers(self):
        for handler in diagnostics._LOGGER.handlers[:]:
            diagnostics._LOGGER.removeHandler(handler)
            handler.close()

    def test_error_log_excludes_exception_message_and_absolute_path(self):
        with tempfile.TemporaryDirectory() as directory:
            try:
                with patch("src.diagnostics.diagnostics_directory", return_value=Path(directory)):
                    try:
                        raise OSError("customer secret at C:/Private/input.csv")
                    except OSError as error:
                        error_id = diagnostics.record_error("csv_processing", error)

                log = (Path(directory) / "errors.log").read_text(encoding="utf-8")
                self.assertIn(error_id, log)
                self.assertIn("operation=csv_processing", log)
                self.assertIn("exception=builtins.OSError", log)
                self.assertNotIn("customer secret", log)
                self.assertNotIn("C:/Private/input.csv", log)
            finally:
                self._close_handlers()
