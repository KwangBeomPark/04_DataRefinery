"""Unit tests for DWH UI/UX enhancements and Excel template generator."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import openpyxl

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from src.dwh_template import create_dwh_excel_template
from src.qt.app import create_or_get_app
from src.qt.tabs.dwh_tab import DwhPipelineTab
from src.qt.widgets.dwh_guide_dialog import DwhGuideDialog
from src.qt.widgets.dwh_preview_dialog import DwhPreviewDialog


class TestDwhUxEnhancements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def test_create_dwh_excel_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "test_template.xlsx"
            res = create_dwh_excel_template(out_path)
            self.assertTrue(res.is_file())

            wb = openpyxl.load_workbook(str(res))
            self.assertEqual(wb.sheetnames, ["master_customers", "master_products", "raw_pnl"])

            ws_cust = wb["master_customers"]
            headers_cust = [c.value for c in ws_cust[1]]
            self.assertEqual(headers_cust, ["bill_to_code", "customer_name", "channel_group", "country_code", "city"])
            self.assertGreaterEqual(ws_cust.max_row, 4)

            ws_prod = wb["master_products"]
            headers_prod = [c.value for c in ws_prod[1]]
            self.assertEqual(headers_prod, ["model_code", "product_name", "division", "category"])
            self.assertGreaterEqual(ws_prod.max_row, 4)

            ws_pnl = wb["raw_pnl"]
            headers_pnl = [c.value for c in ws_pnl[1]]
            self.assertIn("period", headers_pnl)
            self.assertIn("sales_qty", headers_pnl)
            self.assertIn("REV_001", headers_pnl)
            self.assertGreaterEqual(ws_pnl.max_row, 3)

    def test_dwh_guide_dialog(self):
        dlg = DwhGuideDialog(language_code="ko")
        self.assertEqual(dlg.tabs.count(), 4)
        self.assertIn("5대", dlg.tabs.tabText(0))

        dlg.apply_language("en")
        self.assertIn("5 Golden Rules", dlg.tabs.tabText(0))
        self.assertIn("Troubleshooting", dlg.tabs.tabText(3))

        dlg.apply_language("pl")
        self.assertIn("5 Zasad Sukcesu", dlg.tabs.tabText(0))
        dlg.close()

    def test_dwh_tab_new_controls(self):
        tab = DwhPipelineTab(language_code="ko")
        self.assertTrue(hasattr(tab, "btn_open_guide"))
        self.assertTrue(hasattr(tab, "btn_download_template"))
        self.assertTrue(hasattr(tab, "btn_copy_mcode"))
        self.assertTrue(hasattr(tab, "btn_open_masters"))
        self.assertTrue(hasattr(tab, "btn_preview_mart"))
        self.assertTrue(hasattr(tab, "btn_import_cust"))
        self.assertTrue(hasattr(tab, "btn_import_prod"))
        self.assertTrue(hasattr(tab, "btn_import_pnl"))

        # Test language switching
        tab.apply_language("en")
        self.assertEqual(tab.btn_open_guide.text(), "📖 Beginner's Guide")
        self.assertEqual(tab.btn_download_template.text(), "📥 Download Excel Template")
        self.assertEqual(tab.btn_copy_mcode.text(), "⚡ Copy Power Query M-Code")
        self.assertEqual(tab.btn_open_masters.text(), "📝 Open Masters Folder to Add Code")
        self.assertEqual(tab.btn_preview_mart.text(), "📊 Instant Mart Preview")
        self.assertEqual(tab.btn_import_cust.text(), "📂 Import")

        tab.apply_language("pl")
        self.assertEqual(tab.btn_open_guide.text(), "📖 Poradnik dla Początkujących")
        self.assertEqual(tab.btn_download_template.text(), "📥 Pobierz Szablon Excel")
        self.assertEqual(tab.btn_copy_mcode.text(), "⚡ Kopiuj M-Code Power Query")
        self.assertEqual(tab.btn_open_masters.text(), "📝 Otwórz folder kartotek, aby dodać kod")
        self.assertEqual(tab.btn_preview_mart.text(), "📊 Szybki Podgląd Danych Mart")
        self.assertEqual(tab.btn_import_cust.text(), "📂 Importuj")

        tab.apply_language("ko")
        self.assertEqual(tab.btn_open_guide.text(), "📖 초보자 실전 매뉴얼")
        self.assertEqual(tab.btn_download_template.text(), "📥 표준 엑셀 서식 다운로드")
        self.assertEqual(tab.btn_copy_mcode.text(), "⚡ Power Query M-코드 복사")
        self.assertEqual(tab.btn_open_masters.text(), "📝 마스터 폴더 열기 (코드 추가)")
        self.assertEqual(tab.btn_preview_mart.text(), "📊 마트 데이터 즉시 미리보기")
        self.assertEqual(tab.btn_import_cust.text(), "📂 가져오기")
        tab.close()

    def test_dwh_preview_dialog(self):
        import duckdb
        with tempfile.TemporaryDirectory() as tmpdir:
            pq_path = Path(tmpdir) / "sample_mart.parquet"
            # Create 60 sample rows to verify dynamic limit behavior
            con = duckdb.connect()
            con.execute(f"""
                COPY (
                    SELECT '2026-01' AS period,
                           'DEALER' AS channel_group,
                           'Cust ' || (i::VARCHAR) AS customer_name,
                           'MOD01' AS model_code,
                           'Prod A' AS product_name,
                           (100 + i) AS sales_qty,
                           (1000.0 + i) AS gross_revenue_reporting,
                           600.0 AS cogs_reporting,
                           400.0 AS gross_margin_reporting,
                           150.0 AS operating_profit_reporting
                    FROM range(60) t(i)
                ) TO '{pq_path.as_posix()}' (FORMAT PARQUET)
            """)
            con.close()

            dlg = DwhPreviewDialog(parquet_path=pq_path, language_code="ko")
            self.assertEqual(dlg.table.rowCount(), 50)
            self.assertEqual(dlg.table.columnCount(), 10)
            self.assertIn("Cust 0", dlg.table.item(0, 2).text())
            self.assertTrue(dlg.table.isSortingEnabled())
            self.assertEqual(dlg.combo_limit.count(), 3)
            self.assertEqual(dlg.combo_limit.currentText(), "50")

            # Test search filtering
            dlg.edit_filter.setText("Cust 999")
            self.assertTrue(all(dlg.table.isRowHidden(r) for r in range(dlg.table.rowCount())))
            dlg.edit_filter.setText("Cust 5")
            # Should match Cust 5, Cust 50..59 (11 matches within top 50 rows)
            visible_count = sum(1 for r in range(dlg.table.rowCount()) if not dlg.table.isRowHidden(r))
            self.assertGreaterEqual(visible_count, 1)

            # Test limit change to 100 while filter active
            dlg.combo_limit.setCurrentText("100")
            self.assertEqual(dlg.table.rowCount(), 60)
            # Filter Cust 5 should still be active and re-applied
            visible_after_limit = sum(1 for r in range(dlg.table.rowCount()) if not dlg.table.isRowHidden(r))
            self.assertGreaterEqual(visible_after_limit, visible_count)

            # Clear filter
            dlg.edit_filter.clear()
            self.assertTrue(all(not dlg.table.isRowHidden(r) for r in range(dlg.table.rowCount())))

            dlg.apply_language("en")
            self.assertIn("Preview", dlg.windowTitle())
            self.assertIn("Top 50", dlg.windowTitle())
            self.assertIn("Search", dlg.edit_filter.placeholderText())

            dlg.apply_language("pl")
            self.assertIn("Podgląd", dlg.windowTitle())
            self.assertIn("Wyszukaj", dlg.edit_filter.placeholderText())
            dlg.close()


if __name__ == "__main__":
    unittest.main()
