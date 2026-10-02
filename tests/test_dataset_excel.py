"""Tests for dataset Excel template and Power Query M code generation."""

import tempfile
import unittest
import os
import time
from pathlib import Path

import openpyxl

from src.dataset_config import DatasetDefinition
from src.dataset_excel import (
    create_excel_template_workbook,
    generate_powerquery_m_code,
    get_powerquery_guide_text,
)


class TestDatasetExcel(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

        self.dataset = DatasetDefinition.create_new(
            name="Trade_Profit",
            input_folder=str(self.base_dir / "input"),
            publish_folder=str(self.base_dir / "publish"),
            period_column="기준년월",
            period_format="YYYY-MM",
            numeric_columns=["매출액", "영업이익"],
            key_columns=["거래선코드", "상품코드"],
            column_types={
                "거래선코드": "VARCHAR",
                "상품코드": "VARCHAR",
                "기준년월": "VARCHAR",
            },
        )
        self.csv_path = self.base_dir / "publish" / "current" / "Trade_Profit.csv"
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.write_text(
            "거래선코드,상품코드,기준년월,매출액,영업이익,비고\n"
            "00123,P001,2026-01,1000,100,기본\n"
            "00456,P002,2026-01,2000,200,기본\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generate_powerquery_m_code(self):
        m_code = generate_powerquery_m_code(
            self.csv_path,
            self.dataset,
            columns=["거래선코드", "상품코드", "기준년월", "매출액", "영업이익", "비고"],
        )

        # M code assertions
        self.assertIn("Csv.Document", m_code)
        self.assertIn("Encoding=65001", m_code)  # UTF-8
        self.assertIn('{"거래선코드", type text}', m_code)  # text preservation for leading zero
        self.assertIn('{"상품코드", type text}', m_code)
        self.assertIn('{"기준년월", type text}', m_code)
        self.assertIn('{"매출액", type number}', m_code)
        self.assertIn('{"영업이익", type number}', m_code)
        self.assertIn('{"비고", type text}', m_code)
        self.assertIn(self.csv_path.resolve().as_posix(), m_code)

    def test_get_powerquery_guide_text(self):
        guide = get_powerquery_guide_text(self.csv_path, self.dataset)
        self.assertIn("Power Query", guide)
        self.assertIn(self.dataset.name, guide)
        self.assertIn("고급 편집기", guide)
        self.assertIn("Csv.Document", guide)

    def test_create_excel_template_workbook(self):
        """Verify workbook template creation (native COM when available or guide fallback)."""
        template_path = self.base_dir / "output_template.xlsx"
        is_native, msg = create_excel_template_workbook(
            template_path,
            self.csv_path,
            self.dataset,
            columns=["거래선코드", "상품코드", "기준년월", "매출액", "영업이익"],
        )

        self.assertTrue(template_path.exists())
        self.assertTrue(len(msg) > 0)
        # Verify valid zip/xlsx archive
        wb = openpyxl.load_workbook(template_path)
        sheet_names = wb.sheetnames
        self.assertTrue(len(sheet_names) >= 1)
        wb.close()


    def test_parameterized_types_mapped_to_number(self):
        """Ensure types like DECIMAL(18,2) or NUMERIC(10,0) map to 'type number' in M code."""
        ds = DatasetDefinition.create_new(
            name="TypesTest",
            input_folder="in",
            publish_folder="pub",
            column_types={
                "금액": "DECIMAL(18,2)",
                "수량": "NUMERIC(10,0)",
                "코드": "VARCHAR",
            },
            key_columns=["코드"],
        )
        m_code = generate_powerquery_m_code("test.csv", ds)
        self.assertIn('{"금액", type number}', m_code)
        self.assertIn('{"수량", type number}', m_code)
        self.assertIn('{"코드", type text}', m_code)
        self.assertIn('"en-US"', m_code)

    @unittest.skipUnless(os.environ.get("DATAREFINERY_EXCEL_TESTS") == "1", "실제 Excel 통합 검증은 명시적으로 실행합니다.")
    def test_native_com_model_pivot_end_to_end(self):
        """End-to-end test: COM template with Data Model & PivotTable, copy to personal folder, refresh on new data."""
        import sys
        import csv
        import shutil
        import threading
        if sys.platform != "win32":
            return

        # Prepare shared CSV
        pub_csv = self.base_dir / "publish" / "current" / "Trade_Profit.csv"
        pub_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(pub_csv, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows([
                ["기준년월", "거래선코드", "상품코드", "매출액", "영업이익"],
                ["2026-01", "C001", "P001", "100", "10"],
            ])

        # Test running template creation from a BACKGROUND THREAD (with CoInitialize)
        template_path = self.base_dir / "output_template.xlsx"
        thread_error = []

        def bg_worker():
            try:
                is_native, msg = create_excel_template_workbook(
                    template_path,
                    pub_csv,
                    self.dataset,
                    columns=["기준년월", "거래선코드", "상품코드", "매출액", "영업이익"],
                )
                self.assertTrue(is_native)
            except Exception as e:
                thread_error.append(e)

        th = threading.Thread(target=bg_worker)
        th.start()
        th.join()

        if thread_error:
            raise thread_error[0]

        self.assertTrue(template_path.exists())

        # Copy workbook to a separate "personal folder"
        personal_dir = self.base_dir / "personal_user_folder"
        personal_dir.mkdir(parents=True, exist_ok=True)
        personal_wb_path = personal_dir / "my_analysis.xlsx"
        shutil.copy2(template_path, personal_wb_path)

        # Append new month to shared CSV
        with open(pub_csv, "a", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows([
                ["2026-02", "C001", "P001", "250", "25"],
            ])

        # Open copied personal workbook via isolated Excel COM and RefreshAll
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        excel = None
        wb = None
        def retry(call):
            deadline = time.monotonic() + 30
            while True:
                try:
                    return call()
                except Exception:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
        try:
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            wb = excel.Workbooks.Open(str(personal_wb_path.resolve()))

            # Verify Queries, Connections, and PivotTables
            self.assertEqual(wb.Queries.Count, 1)
            self.assertTrue(wb.Connections.Count >= 1)

            # Refresh data
            wb.RefreshAll()
            excel.CalculateUntilAsyncQueriesDone()
            self.assertEqual(retry(lambda: wb.Model.ModelTables.Item(1).RecordCount), 2)
            pivots = [
                wb.Sheets.Item(i).PivotTables().Item(1)
                for i in range(1, wb.Sheets.Count + 1)
                if wb.Sheets.Item(i).PivotTables().Count
            ]
            self.assertEqual(len(pivots), 1)
            pivot = pivots[0]
            self.assertEqual(pivot.RowFields.Count, 1)
            self.assertEqual(pivot.DataFields.Count, 2)
            self.assertIn("2026-02", str(pivot.TableRange2.Value))
            self.assertIn(350.0, pivot.TableRange2.Value[-1])

            # Save and close
            wb.Save()
            wb.Close(SaveChanges=False)
            wb = None
            excel.Quit()
            excel = None
        finally:
            if wb is not None:
                retry(lambda: wb.Close(SaveChanges=False))
            if excel is not None:
                retry(lambda: excel.Quit())
            pythoncom.CoUninitialize()


if __name__ == "__main__":
    unittest.main()
