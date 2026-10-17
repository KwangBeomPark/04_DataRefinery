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
        expected_widgets = [window.tab_aggregator, window.tab_dataset, window.tab_promotion, window.tab_csv]
        self.assertEqual([window.tab_widget.widget(i) for i in range(4)], expected_widgets)
        self.assertIs(window.tab_widget.currentWidget(), window.tab_aggregator)

        # Test language switching to English
        window.apply_language("en")
        self.assertEqual(window.language_code, "en")
        self.assertEqual(
            [window.tab_widget.tabText(i) for i in range(4)],
            ["Data aggregator", "Dataset publisher", "Promotion template", "CSV repair"],
        )

        # Test language switching to Polish
        window.apply_language("pl")
        self.assertEqual(window.language_code, "pl")
        self.assertEqual(
            [window.tab_widget.tabText(i) for i in range(4)],
            ["Agregacja danych", "Publikacja zestawów danych", "Szablon promocji", "Naprawa CSV"],
        )

        # Test language switching back to Korean
        window.apply_language("ko")
        self.assertEqual(window.language_code, "ko")
        self.assertEqual(
            [window.tab_widget.tabText(i) for i in range(4)],
            ["데이터 집계·슬라이서", "데이터셋 배포", "프로모션 템플릿", "CSV 구조 복구"],
        )
        self.assertEqual([window.tab_widget.widget(i) for i in range(4)], expected_widgets)
        # Moving between tasks and changing language must retain task state and selection.
        window.tab_aggregator.edit_output_name.setText("tv_summary.csv")
        window.tab_widget.setCurrentWidget(window.tab_dataset)
        window.apply_language("en")
        self.assertIs(window.tab_widget.currentWidget(), window.tab_dataset)
        window.tab_widget.setCurrentWidget(window.tab_aggregator)
        self.assertEqual(window.tab_aggregator.edit_output_name.text(), "tv_summary.csv")
        window.close()

    def test_cli_version_flag(self):
        import io
        import sys
        from unittest.mock import patch
        from src.data_refinery import main

        stdout_buf = io.StringIO()
        with patch.object(sys, "argv", ["data_refinery", "--version"]):
            with patch("sys.stdout", stdout_buf):
                with self.assertRaises(SystemExit) as cm:
                    main()
                self.assertEqual(cm.exception.code, 0)
                self.assertIn(__version__, stdout_buf.getvalue())

    def test_full_localization_and_progressive_step_gating(self):
        import re
        from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QPushButton
        from src.qt.widgets.card import CardWidget

        hangul_re = re.compile(r"[가-힣]")
        window = MainWindow()

        # 1. Verify initial progressive step-gating (before selecting files)
        self.assertFalse(window.tab_csv.card_import.isEnabled())
        self.assertFalse(window.tab_csv.card_output.isEnabled())
        self.assertFalse(window.tab_csv.btn_process.isEnabled())

        self.assertFalse(window.tab_promotion.card_output.isEnabled())
        self.assertFalse(window.tab_promotion.btn_process.isEnabled())

        self.assertFalse(window.tab_aggregator.card_sources.isEnabled())
        self.assertFalse(window.tab_aggregator.card_rules.isEnabled())
        self.assertFalse(window.tab_aggregator.btn_preview.isEnabled())
        self.assertFalse(window.tab_aggregator.btn_run.isEnabled())

        # 2. Verify zero Hangul characters remain in English and Polish UI labels/buttons/placeholders
        for lang in ("en", "pl"):
            window.apply_language(lang)
            for action in window.menu_settings.actions():
                if action.text():
                    self.assertIsNone(hangul_re.search(action.text()), f"Menu action has Hangul in {lang}: {action.text()}")
            for action in window.tab_aggregator.menu_preset.actions():
                if action.text():
                    self.assertIsNone(hangul_re.search(action.text()), f"Preset action has Hangul in {lang}: {action.text()}")

            for tab_name, tab in (
                ("csv", window.tab_csv),
                ("promo", window.tab_promotion),
                ("agg", window.tab_aggregator),
                ("ds", window.tab_dataset),
            ):
                widgets = []
                for cls in (QLabel, QPushButton, QCheckBox, QLineEdit, CardWidget):
                    widgets.extend(tab.findChildren(cls))
                for w in widgets:
                    if isinstance(w, CardWidget):
                        txt = w._title_label.text() if hasattr(w, "_title_label") and w._title_label else ""
                    elif isinstance(w, QLineEdit):
                        txt = w.placeholderText()
                    else:
                        txt = w.text()
                    self.assertIsNone(
                        hangul_re.search(txt),
                        f"[{lang}/{tab_name}] Widget {type(w).__name__} still contains Korean: {txt!r}",
                    )

        window.close()


if __name__ == "__main__":
    unittest.main()

