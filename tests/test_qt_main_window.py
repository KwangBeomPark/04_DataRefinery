"""Unit tests for PySide6 MainWindow."""

import os
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from src.qt.app import create_or_get_app
from src.qt.main_window import MainWindow, __version__


class TestQtMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def test_main_window_creation_and_tabs(self):
        window = MainWindow()
        self.assertEqual(window.tab_widget.count(), 4)
        self.assertIn(__version__, window.windowTitle())

        # Test language switching to English
        window.apply_language("en")
        self.assertEqual(window.language_code, "en")
        self.assertEqual(window.tab_widget.tabText(0), "CSV repair")

        # Test language switching to Polish
        window.apply_language("pl")
        self.assertEqual(window.language_code, "pl")
        self.assertEqual(window.tab_widget.tabText(0), "Naprawa CSV")

        # Test language switching back to Korean
        window.apply_language("ko")
        self.assertEqual(window.language_code, "ko")
        self.assertEqual(window.tab_widget.tabText(0), "CSV 구조 복구")


if __name__ == "__main__":
    unittest.main()
