"""Unit tests for PySide6 PromotionTab."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from openpyxl import Workbook

from src.qt.app import create_or_get_app
from src.qt.tabs.promotion_tab import PromotionTab


class TestQtPromotionTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.wb_path = Path(self.temp_dir.name) / "promo.xlsx"

        # Create valid promotion workbook
        wb = Workbook()
        ws1 = wb.active
        ws1.title = "Promotion_Master"
        ws1.append(["promotion_id", "promotion_name", "channel", "promotion_type", "notes"])
        ws1.append(["PROMO_1", "봄맞이 세일", "대리점", "현금", "비고"])

        ws2 = wb.create_sheet(title="Support_Rules")
        ws2.append(["support_rule_id", "promotion_id", "model_code", "start_date", "end_date", "support_per_unit", "currency"])
        ws2.append(["RULE_1", "PROMO_1", "TV_OLED_65", "2026-04-01", "2026-04-05", 50000, "KRW"])
        wb.save(self.wb_path)

    def test_promotion_load_and_preview(self):
        tab = PromotionTab(language_code="ko")
        tab.picker_file.set_path(str(self.wb_path))

        # Check data loaded and preview populated
        self.assertIsNotNone(tab._promotion_data)
        self.assertEqual(len(tab._promotion_data.support_rules), 1)
        self.assertEqual(tab.model_preview.rowCount(), 5)  # 5 daily rows (4/1 ~ 4/5)

    def test_apply_language_switches_labels(self):
        tab = PromotionTab(language_code="ko")
        self.assertIn("일별", tab.btn_process.text())

        tab.apply_language("en")
        self.assertIn("Create", tab.btn_process.text())

        tab.apply_language("pl")
        self.assertIn("Utwórz", tab.btn_process.text())


if __name__ == "__main__":
    unittest.main()
