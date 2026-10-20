"""Beginner Guide Dialog for DWH & Data Mart."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.file_reveal import open_containing_folder
from src.i18n import _UI_TEXT
from src.ui_components import PALETTE


class DwhGuideDialog(QDialog):
    """Interactive modal dialog displaying the DWH Beginner Guide."""

    def __init__(self, parent: Optional[QWidget] = None, language_code: str = "ko") -> None:
        super().__init__(parent)
        self.language_code = language_code
        self.resize(760, 560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._init_ui()
        self.apply_language(self.language_code)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Title
        self.lbl_title = QLabel()
        self.lbl_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {PALETTE['navy']};")
        layout.addWidget(self.lbl_title)

        # Tabs
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        # Tab 1: Core Rules
        self.browser_rules = QTextBrowser()
        self.browser_rules.setOpenExternalLinks(True)
        self.tabs.addTab(self.browser_rules, "")

        # Tab 2: File Specifications
        self.browser_specs = QTextBrowser()
        self.browser_specs.setOpenExternalLinks(True)
        self.tabs.addTab(self.browser_specs, "")

        # Tab 3: Excel Connection
        self.browser_excel = QTextBrowser()
        self.browser_excel.setOpenExternalLinks(True)
        self.tabs.addTab(self.browser_excel, "")

        # Tab 4: Troubleshooting
        self.browser_trouble = QTextBrowser()
        self.browser_trouble.setOpenExternalLinks(True)
        self.tabs.addTab(self.browser_trouble, "")

        # Bottom Bar
        bottom_row = QHBoxLayout()
        self.btn_open_md = QPushButton()
        self.btn_open_md.clicked.connect(self._on_open_markdown)
        bottom_row.addWidget(self.btn_open_md)
        bottom_row.addStretch()

        self.btn_close = QPushButton()
        self.btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(self.btn_close)

        layout.addLayout(bottom_row)

    def apply_language(self, lang: str) -> None:
        self.language_code = lang
        t = _UI_TEXT.get(lang, _UI_TEXT["ko"])

        self.setWindowTitle(t.get("dwh_dlg_guide_title", "DWH & 손익 마트 초보자 실전 매뉴얼"))
        self.lbl_title.setText(f"🏛️ {t.get('dwh_dlg_guide_title', 'DWH & 손익 마트 초보자 실전 매뉴얼')}")
        self.btn_open_md.setText(t.get("dwh_dlg_guide_open_md", "📂 전체 매뉴얼 마크다운 문서 열기"))
        self.btn_close.setText(t.get("close", "Close" if lang != "ko" else "닫기"))

        if lang == "en":
            self.tabs.setTabText(0, "1. 5 Golden Rules")
            self.tabs.setTabText(1, "2. File Specs")
            self.tabs.setTabText(2, "3. Excel M-Code")
            self.tabs.setTabText(3, "4. Troubleshooting")

            self.browser_rules.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #1E3A8A;">💡 5 Golden Rules for DWH Beginners</h3>
            <ol>
                <li><b>Save as CSV UTF-8</b>: Always choose <code>CSV UTF-8 (Comma delimited) (*.csv)</code> when exporting from Excel. Legacy ANSI/CP949 corrupts non-ASCII text.</li>
                <li><b>Preserve Leading Zeros</b>: Customer codes like <code>000100200301</code> must not be truncated to <code>100200301</code>. Keep column format as Text.</li>
                <li><b>Standardize Period</b>: Period must strictly use <code>YYYY-MM</code> (e.g. <code>2026-01</code>). Do not mix <code>2026.01</code> or <code>202601</code>.</li>
                <li><b>Master Pre-Registration</b>: Any customer or product in transaction files must exist in master CSVs first. Missing codes trigger strict Fail-Fast rollback.</li>
                <li><b>Close Excel Before Building</b>: Close open Excel windows on CSV/Parquet files to prevent <code>Permission Denied</code> file lock errors.</li>
            </ol>
            </div>
            """)

            self.browser_specs.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #1E3A8A;">📋 Required Source File Specifications</h3>
            <p>Place CSV files in <code>data/01_raw/</code> subfolders:</p>
            <ul>
                <li><b>Customer Master</b> (<code>data/01_raw/masters/master_customers.csv</code>):<br>
                Columns: <code>bill_to_code, customer_name, channel_group, country_code, city</code></li>
                <li><b>Product Master</b> (<code>data/01_raw/masters/master_products.csv</code>):<br>
                Columns: <code>model_code, product_name, division, category</code></li>
                <li><b>P&L Transactions</b> (<code>data/01_raw/pnl/*.csv</code>):<br>
                Columns: <code>period, entity_code, bill_to_code, model_code, currency_code, sales_qty</code> + account columns (<code>REV_*, COG_*, LOG_*, PRM_*</code>)</li>
            </ul>
            </div>
            """)

            self.browser_excel.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #1E3A8A;">⚡ 1-Second Power Query M-Code Connection</h3>
            <p>Connect your Excel workbook without manual file browsing:</p>
            <ol>
                <li>Click <b>[⚡ Copy Power Query M-Code]</b> in Card 3.</li>
                <li>In Excel: <b>Data ➔ Get Data ➔ From Other Sources ➔ Blank Query</b>.</li>
                <li>Click <b>Advanced Editor</b> on the ribbon.</li>
                <li>Replace existing text with the copied M-Code and click <b>Done</b>.</li>
                <li>Click <b>Close & Load To... ➔ PivotTable Report</b>.</li>
            </ol>
            </div>
            """)

            self.browser_trouble.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #991B1B;">🚨 Troubleshooting Common Errors</h3>
            <h4>Q1. Foreign Key Integrity Violation (Fail-Fast)</h4>
            <p><b>Cause</b>: Transaction files contain customer or product codes not present in masters.<br>
            <b>Fix</b>: Click <code>[Open Error Report (CSV)]</code>, inspect missing codes, add them to <code>master_customers.csv</code> or <code>master_products.csv</code>, and rebuild.</p>
            <h4>Q2. Permission Denied</h4>
            <p><b>Cause</b>: Excel currently has the Parquet or CSV file open.<br>
            <b>Fix</b>: Close all Excel windows and click Build again.</p>
            </div>
            """)

        elif lang == "pl":
            self.tabs.setTabText(0, "1. 5 Zasad Sukcesu")
            self.tabs.setTabText(1, "2. Specyfikacja")
            self.tabs.setTabText(2, "3. Połączenie Excel")
            self.tabs.setTabText(3, "4. Rozwiązywanie problemów")

            self.browser_rules.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #1E3A8A;">💡 5 Zasad Sukcesu dla Początkujących</h3>
            <ol>
                <li><b>Zapisz jako CSV UTF-8</b>: Zawsze wybieraj <code>CSV UTF-8 (rozdzielany przecinkami) (*.csv)</code>. Standardowy format ANSI niszczy polskie znaki diakrytyczne.</li>
                <li><b>Zachowaj wiodące zera</b>: Kody takie jak <code>000100200301</code> nie mogą zostać obcięte do <code>100200301</code>.</li>
                <li><b>Format okresu</b>: Okres musi mieć dokładnie format <code>YYYY-MM</code> (np. <code>2026-01</code>).</li>
                <li><b>Wcześniejsza rejestracja kartotek</b>: Każdy klient i produkt w transakcjach musi najpierw istnieć w kartotece.</li>
                <li><b>Zamknij Excel przed budową</b>: Zamknij otwarte pliki CSV i Parquet w programie Excel przed uruchomieniem potoku.</li>
            </ol>
            </div>
            """)

            self.browser_specs.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #1E3A8A;">📋 Wymagane Specyfikacje Plików Źródłowych</h3>
            <p>Umieść pliki CSV w podfolderach <code>data/01_raw/</code>:</p>
            <ul>
                <li><b>Kartoteka klientów</b> (<code>data/01_raw/masters/master_customers.csv</code>):<br>
                Kolumny: <code>bill_to_code, customer_name, channel_group, country_code, city</code></li>
                <li><b>Kartoteka produktów</b> (<code>data/01_raw/masters/master_products.csv</code>):<br>
                Kolumny: <code>model_code, product_name, division, category</code></li>
                <li><b>Transakcje P&L</b> (<code>data/01_raw/pnl/*.csv</code>):<br>
                Kolumny: <code>period, entity_code, bill_to_code, model_code, currency_code, sales_qty</code> + konta (<code>REV_*, COG_*, LOG_*</code>)</li>
            </ul>
            </div>
            """)

            self.browser_excel.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #1E3A8A;">⚡ 1-Sekundowe Połączenie Power Query M-Code</h3>
            <ol>
                <li>Kliknij <b>[⚡ Kopiuj M-Code Power Query]</b> w Karcie 3.</li>
                <li>W programie Excel: <b>Dane ➔ Pobierz dane ➔ Z innych źródeł ➔ Puste zapytanie</b>.</li>
                <li>Kliknij <b>Edytor zaawansowany</b>.</li>
                <li>Wklej skopiowany kod i kliknij <b>Gotowe</b>.</li>
                <li>Wybierz <b>Zamknij i załaduj do... ➔ Raport w formie tabeli przestawnej</b>.</li>
            </ol>
            </div>
            """)

            self.browser_trouble.setHtml("""
            <div style="font-family: Segoe UI, sans-serif; line-height: 1.5; color: #1E293B;">
            <h3 style="color: #991B1B;">🚨 Rozwiązywanie Typowych Problemów</h3>
            <h4>P1. Naruszenie integralności kluczy obcych (Fail-Fast)</h4>
            <p><b>Przyczyna</b>: Transakcje zawierają kody niewprowadzone do kartotek.<br>
            <b>Rozwiązanie</b>: Kliknij <code>[Otwórz raport błędów (CSV)]</code>, dodaj brakujące kody do kartotek i zbuduj ponownie.</p>
            </div>
            """)

        else:
            # Korean (default)
            self.tabs.setTabText(0, "1. 초보자 5대 수칙")
            self.tabs.setTabText(1, "2. 파일 준비 규격")
            self.tabs.setTabText(2, "3. 엑셀 파워쿼리 연동")
            self.tabs.setTabText(3, "4. 에러 발생 해결법")

            self.browser_rules.setHtml("""
            <div style="font-family: Malgun Gothic, Segoe UI, sans-serif; line-height: 1.6; color: #1E293B;">
            <h3 style="color: #1E3A8A;">💡 초보자가 가장 많이 하는 실수 5가지와 방지 팁</h3>
            <ol>
                <li><b>엑셀에서 CSV UTF-8로 저장</b>: 엑셀에서 저장 시 반드시 <code>CSV UTF-8 (쉼표로 분리)(*.csv)</code>를 선택하세요. 일반 ANSI로 저장하면 한글이 물음표(???)로 깨집니다.</li>
                <li><b>코드 앞자리 '0' 보존</b>: 거래처코드 <code>000100</code>이 <code>100</code>으로 바뀌지 않도록 텍스트 서식을 유지하세요.</li>
                <li><b>기간(Period) 포맷 통일</b>: 반드시 <code>YYYY-MM</code> (예: <code>2026-01</code>) 형식으로 통일하세요.</li>
                <li><b>마스터 선행 등록 원칙 (가장 중요)</b>: 실적 파일에 신규 거래처나 신제품이 처음 등장했다면, 마스터 파일에 해당 코드를 먼저 한 줄 추가한 뒤 빌드해야 에러가 나지 않습니다.</li>
                <li><b>엑셀 창 닫고 빌드하기</b>: 엑셀에서 CSV나 Parquet 파일을 열어둔 채 빌드하면 <code>Permission Denied</code> 권한 에러가 발생합니다.</li>
            </ol>
            </div>
            """)

            self.browser_specs.setHtml("""
            <div style="font-family: Malgun Gothic, Segoe UI, sans-serif; line-height: 1.6; color: #1E293B;">
            <h3 style="color: #1E3A8A;">📋 필수 원천 파일 3종 규격</h3>
            <p>프로그램 내 [폴더 열기] 버튼을 눌러 나타나는 해당 폴더에 넣어주세요:</p>
            <ul>
                <li><b>고객 마스터</b> (<code>data/01_raw/masters/master_customers.csv</code>):<br>
                컬럼: <code>bill_to_code, customer_name, channel_group, country_code, city</code></li>
                <li><b>제품 마스터</b> (<code>data/01_raw/masters/master_products.csv</code>):<br>
                컬럼: <code>model_code, product_name, division, category</code></li>
                <li><b>P&L 실적 원천</b> (<code>data/01_raw/pnl/*.csv</code>):<br>
                컬럼: <code>period, entity_code, bill_to_code, model_code, currency_code, sales_qty</code> + 계정 컬럼들 (<code>REV_*, COG_*, LOG_*, PRM_*</code>)</li>
            </ul>
            </div>
            """)

            self.browser_excel.setHtml("""
            <div style="font-family: Malgun Gothic, Segoe UI, sans-serif; line-height: 1.6; color: #1E293B;">
            <h3 style="color: #1E3A8A;">⚡ 엑셀 파워쿼리 1초 직결법 (M-Code)</h3>
            <ol>
                <li>카드 3에서 <b>[⚡ Power Query M-코드 복사]</b> 버튼을 누릅니다.</li>
                <li>엑셀을 켜고: <b>데이터 ➔ 데이터 가져오기 ➔ 기타 원본에서 ➔ 빈 쿼리</b>를 클릭합니다.</li>
                <li>상단 리본 메뉴의 <b>고급 편집기</b>를 누르고 기존 내용을 모두 지운 뒤 붙여넣습니다.</li>
                <li><b>[완료]</b>를 누른 후 <b>[닫기 및 다음으로 로드... ➔ 피벗 테이블 보고서]</b>를 선택하면 끝!</li>
            </ol>
            </div>
            """)

            self.browser_trouble.setHtml("""
            <div style="font-family: Malgun Gothic, Segoe UI, sans-serif; line-height: 1.6; color: #1E293B;">
            <h3 style="color: #991B1B;">🚨 에러 발생 시 자가 해결 매뉴얼</h3>
            <h4>1. "외래키 무결성 위반 (미등록 마스터 코드 발견)"</h4>
            <p><b>원인</b>: 실적 파일에는 적혀 있는데 마스터 파일에는 빠진 고객/제품 코드가 있습니다.<br>
            <b>해결책</b>: <code>[📄 오류 리포트 열기 (CSV)]</code>를 눌러 빠진 코드를 확인하고, <code>[📝 마스터 폴더 열기]</code>를 눌러 마스터 파일 맨 아래 줄에 추가한 후 다시 빌드하세요.</p>
            <h4>2. "Permission Denied: ... v_ai_pnl_summary.parquet"</h4>
            <p><b>원인</b>: 엑셀에서 해당 파일을 이미 열고 있는 상태입니다.<br>
            <b>해결책</b>: 엑셀 창을 모두 닫고 다시 빌드 버튼을 누르세요.</p>
            </div>
            """)

    def _on_open_markdown(self) -> None:
        import sys
        target_name = f"DWH_BEGINNER_GUIDE_{self.language_code}.md"
        fallback_name = "DWH_BEGINNER_GUIDE.md"

        candidates = [
            Path(f"docs/{target_name}"),
            Path(f"docs/{fallback_name}"),
            Path(__file__).resolve().parent.parent.parent / "docs" / target_name,
            Path(__file__).resolve().parent.parent.parent / "docs" / fallback_name,
        ]
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "docs" / target_name)
            candidates.append(Path(sys._MEIPASS) / "docs" / fallback_name)
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).parent / "docs" / target_name)
            candidates.append(Path(sys.executable).parent / "docs" / fallback_name)

        for c in candidates:
            if c.is_file():
                try:
                    os.startfile(str(c))
                    return
                except Exception:
                    open_containing_folder(str(c))
                    return
