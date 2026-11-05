"""Interactive star schema modeling wizard dialog for Data Refinery DWH marts.

Provides a spacious 1200x820 dual-pane interface:
- Left pane: Large raw data grid (up to 1,000 sample rows) with interactive role header badges
- Right pane: 4-step guided wizard (Profile & Auto-Guess -> Fact & Measures -> Dimension Partitioning -> Star Schema Review)
- Detachable full-screen data viewer popup
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.csv_processing import read_file_rows
from src.mart.auto_guess import (
    ROLE_DIM_ATTR,
    ROLE_DIM_KEY,
    ROLE_EXCLUDED,
    ROLE_MEASURE,
    ROLE_PARTITION,
    ROLE_PERIOD,
    ROLE_ROW_KEY,
    ROLE_UNASSIGNED,
    ColumnAssignment,
    ModelGuessResult,
    assignments_to_model,
    guess_model_assignments,
    model_to_assignments,
)
from src.mart.model import MartModel
from src.qt.theme import PALETTE


WIZARD_TEXT = {
    "ko": {
        "title": "스타 스키마 데이터셋 설계 마법사",
        "step1_title": "1. 📋 원본 확인 및 구분자 점검",
        "step2_title": "2. 🏷️ 차원(Dimension) 분리 및 묶기",
        "step3_title": "3. 📊 팩트(Fact) 및 지표 설정",
        "step4_title": "4. 🔍 관계 검토 및 완성",
        "delimiter_label": "파일 구분 기호:",
        "encoding_label": "인코딩:",
        "delimiter_comma": "쉼표 (,)",
        "delimiter_tab": "탭 (\\t)",
        "delimiter_semicolon": "세미콜론 (;)",
        "delimiter_pipe": "파이프 (|)",
        "warn_single_column": "⚠️ 구분자가 맞지 않아 전체 데이터가 단 1개의 열로 뭉개졌습니다! 위의 [파일 구분 기호]를 탭(\\t)이나 세미콜론(;)으로 변경해 주세요.",
        "btn_expand_data": "🔍 데이터 별도 창으로 크게 보기",
        "btn_auto_guess_pnl": "📊 데릭 P&L 손익 프리셋 자동 추천",
        "btn_auto_guess_generic": "⚡ 스마트 Auto-Guess 실행",
        "btn_add_measure": "+ 새 지표 추가",
        "btn_del_measure": "- 선택 지표 삭제",
        "btn_back": "◀ 이전",
        "btn_next": "다음 ▶",
        "btn_finish": "✔ 모델 적용 및 저장",
        "btn_cancel": "취소",
        "fact_table_label": "팩트 테이블명:",
        "period_label": "기준 분석 기간 (Period):",
        "row_key_label": "원천 행 고유키 (Row Grain):",
        "partition_label": "파티션/법인 격리 기준:",
        "add_dim": "+ 새 차원 추가",
        "del_dim": "- 선택 차원 삭제",
        "dim_name_label": "차원 테이블명:",
        "dim_keys_label": "식별 자연키 (Key Column):",
        "dim_attrs_label": "소속 속성 열들 (체크박스로 선택):",
        "unassigned_alert": "⚠️ 아직 역할이 지정되지 않은 열이 있습니다. 아래에서 확인하세요:",
        "star_schema_desc": "💡 이 모델이 저장되면 DWH에 팩트 1개와 차원 N개의 테이블이 자동 생성됩니다.",
        "success_applied": "스타 스키마 모델이 성공적으로 설계되었습니다.",
        "err_no_period": "기준 기간 컬럼을 반드시 지정해야 합니다.",
        "err_no_fact": "팩트 테이블 이름을 입력해야 합니다.",
        "err_single_column": "열이 1개뿐입니다. 구분 기호를 확인해 여러 열로 정상 분리해 주세요.",
        "err_unassigned": "모든 열에 역할을 부여하거나 '제외(Excluded)'로 지정해야 합니다.",
        "validation_passed": "✔ 모든 무결성 검증 통과 (컬럼 역할 충돌 없음)",
        "badge_period": "[기간]",
        "badge_key": "[식별키]",
        "badge_dim": "[차원]",
        "badge_measure": "[지표]",
        "badge_excluded": "[제외]",
        "badge_unassigned": "[미지정]",
        "step1_banner_pnl": "✨ 데릭 P&L 손익 데이터셋 구조가 자동으로 분석되었습니다!<br>• 왼쪽 표의 열 머리글에 붙은 색상 배지(🔵기간, 🟢식별키, 🟡차원, 🟣지표)를 확인해 보세요.<br>• <b>복잡한 설정 없이, 우측 하단의 [다음 ▶] 버튼</b>을 눌러 차원 분리 단계로 이동하세요.",
        "step1_banner_generic": "⚡ 원본 데이터 분석 완료! 추천 스타 스키마 구조가 적용되었습니다.<br>• 왼쪽 표의 열 머리글에 붙은 색상 배지를 확인해 보세요.<br>• <b>기본 추천 구조를 확인하신 뒤, 우측 하단의 [다음 ▶] 버튼</b>을 눌러 차원 분리 단계로 이동하세요.",
        "step1_banner_existing": "📋 기존에 저장된 스타 스키마 모델 설정을 불러왔습니다.<br>• 현재 설정을 검토하시고, 수정을 원치 않으시면 [다음 ▶]을 눌러 각 단계를 확인하세요.",
        "step2_banner": "💡 <b>1단계: 차원(Dimension) 분리</b> — 전체 열에서 고객, 제품, 조직 등 분석 기준이 될 차원 열들을 묶어 빼내는 단계입니다.<br>• 차원을 선택하고, 오른쪽 체크박스에서 <b>포함할 속성 열들을 마우스로 체크</b>하세요.<br>• <b>식별 자연키(Key)</b>는 팩트 테이블에 외래키(FK)로 남아 연결됩니다.",
        "step3_banner": "💡 <b>2단계: 팩트(Fact) 및 지표 설정</b> — 차원으로 빠져나간 열을 제외하고, 거래의 핵심 숫자(매출액, 비용, 수량 등)를 팩트 테이블의 지표(Measures)로 설정합니다.<br>• [+ 새 지표 추가] 버튼으로 원하는 열을 자유롭게 추가하고 집계 방식을 설정하세요.",
        "step3_banner_unassigned": "⚠️ 아직 역할이 지정되지 않은 열(🔴)이 있습니다.<br>• 해당 열을 차원에 속성으로 추가하거나 제외(Excluded) 처리 후 [다음 ▶]을 누르세요.",
        "popup_title": "Data Refinery - 원본 데이터 전용 뷰어 (Full Data Viewer)",
        "popup_summary": "전체 {cols}개 열 / 상위 {rows:,}개 샘플 행 탐색",
        "btn_close": "닫기 (Close)",
        "col_header_name": "컬럼명",
        "col_header_role": "제안 역할",
        "col_header_reason": "근거 / 비고",
        "col_header_kind": "종류 (Kind)",
        "col_header_unit": "단위 (Unit)",
        "col_header_scale": "소수점 (Scale)",
        "col_header_target": "배치 대상 테이블",
        "validation_warn": "검증 경고",
    },
    "en": {
        "title": "Star Schema Dataset Modeling Wizard",
        "step1_title": "1. 📋 Source & Delimiter Check",
        "step2_title": "2. 🏷️ Dimension Partitioning",
        "step3_title": "3. 📊 Fact & Measures Configuration",
        "step4_title": "4. 🔍 Relationship Review",
        "delimiter_label": "File Delimiter:",
        "encoding_label": "Encoding:",
        "delimiter_comma": "Comma (,)",
        "delimiter_tab": "Tab (\\t)",
        "delimiter_semicolon": "Semicolon (;)",
        "delimiter_pipe": "Pipe (|)",
        "warn_single_column": "⚠️ Delimiter mismatch! All data parsed as 1 single column. Change delimiter above to Tab (\\t) or Semicolon (;).",
        "btn_expand_data": "🔍 View Data in Large Window",
        "btn_auto_guess_pnl": "📊 Apply Derek P&L Preset",
        "btn_auto_guess_generic": "⚡ Run Smart Auto-Guess",
        "btn_add_measure": "+ Add Measure",
        "btn_del_measure": "- Remove Measure",
        "btn_back": "◀ Back",
        "btn_next": "Next ▶",
        "btn_finish": "✔ Apply & Save Model",
        "btn_cancel": "Cancel",
        "fact_table_label": "Fact Table Name:",
        "period_label": "Analysis Period Column:",
        "row_key_label": "Row Grain Key:",
        "partition_label": "Partition / Entity Column:",
        "add_dim": "+ Add Dimension",
        "del_dim": "- Remove Dimension",
        "dim_name_label": "Dimension Name:",
        "dim_keys_label": "Natural Keys:",
        "dim_attrs_label": "Attributes (Check to include):",
        "unassigned_alert": "⚠️ Unassigned columns detected. Please review below:",
        "star_schema_desc": "💡 Saving this model creates 1 fact table and N dimension tables in DWH.",
        "success_applied": "Star schema model designed successfully.",
        "err_no_period": "Period column must be specified.",
        "err_no_fact": "Fact table name cannot be empty.",
        "err_single_column": "Only 1 column parsed. Please check delimiter to separate columns.",
        "err_unassigned": "All columns must have a role assigned or be marked as Excluded.",
        "validation_passed": "✔ All integrity checks passed (No role collisions)",
        "badge_period": "[Period]",
        "badge_key": "[Key]",
        "badge_dim": "[Dim]",
        "badge_measure": "[Measure]",
        "badge_excluded": "[Excluded]",
        "badge_unassigned": "[Unassigned]",
        "step1_banner_pnl": "✨ Derek P&L structure automatically analyzed!<br>• Review colored badges in column headers (🔵Period, 🟢Key, 🟡Dim, 🟣Measure).<br>• <b>Click [Next ▶] below</b> to proceed to dimension partitioning.",
        "step1_banner_generic": "⚡ Data analyzed! Recommended star schema structure applied.<br>• Review colored badges in column headers.<br>• <b>Click [Next ▶] below</b> to proceed to dimension partitioning.",
        "step1_banner_existing": "📋 Loaded existing star schema model configuration.<br>• Review settings and click [Next ▶] to inspect each step.",
        "step2_banner": "💡 <b>Step 1: Dimension Partitioning</b> — Extract entities (Customer, Product, Entity) as dimensions from full columns.<br>• Select a dimension, then <b>check attributes</b> on the right checklist.<br>• Natural key stays in Fact table as Foreign Key (FK).",
        "step3_banner": "💡 <b>Step 2: Fact & Measures</b> — Remaining non-dimension columns are configured as central Fact table and numeric measures (Sales, Cost, Qty).<br>• Click [+ Add Measure] to add or configure numeric metrics.",
        "step3_banner_unassigned": "⚠️ Some columns remain unassigned (🔴).<br>• Assign them as dimension attributes or mark as Excluded before proceeding.",
        "popup_title": "Data Refinery - Raw Data Viewer",
        "popup_summary": "Total {cols} columns / Top {rows:,} sample rows preview",
        "btn_close": "Close",
        "col_header_name": "Column Name",
        "col_header_role": "Suggested Role",
        "col_header_reason": "Reason / Notes",
        "col_header_kind": "Kind",
        "col_header_unit": "Unit",
        "col_header_scale": "Scale",
        "col_header_target": "Target Table",
        "validation_warn": "Validation Warning",
    },
    "pl": {
        "title": "Kreator Modelowania Hurtowni Gwiazdy",
        "step1_title": "1. 📋 Sprawdzenie Źródła i Separatora",
        "step2_title": "2. 🏷️ Wydzielenie Wymiarów",
        "step3_title": "3. 📊 Tabela Faktów i Miar",
        "step4_title": "4. 🔍 Przegląd Relacji i Podsumowanie",
        "delimiter_label": "Separator Kolumn:",
        "encoding_label": "Kodowanie:",
        "delimiter_comma": "Przecinek (,)",
        "delimiter_tab": "Tabulator (\\t)",
        "delimiter_semicolon": "Średnik (;)",
        "delimiter_pipe": "Pionowa kreska (|)",
        "warn_single_column": "⚠️ Błąd separatora! Wszystkie dane w 1 kolumnie. Zmień separator na Tabulator (\\t) lub Średnik (;).",
        "btn_expand_data": "🔍 Otwórz w Dużym Oknie",
        "btn_auto_guess_pnl": "📊 Zastosuj Szablon Derek P&L",
        "btn_auto_guess_generic": "⚡ Uruchom Auto-Rekomendację",
        "btn_add_measure": "+ Dodaj Miarę",
        "btn_del_measure": "- Usuń Miarę",
        "btn_back": "◀ Wstecz",
        "btn_next": "Dalej ▶",
        "btn_finish": "✔ Zastosuj i Zapisz Model",
        "btn_cancel": "Anuluj",
        "fact_table_label": "Nazwa Tabeli Faktów:",
        "period_label": "Kolumna Okresu:",
        "row_key_label": "Klucz Wiersza:",
        "partition_label": "Kolumna Partycji/Podmiotu:",
        "add_dim": "+ Dodaj Wymiar",
        "del_dim": "- Usuń Wymiar",
        "dim_name_label": "Nazwa Wymiaru:",
        "dim_keys_label": "Klucze Naturalne:",
        "dim_attrs_label": "Atrybuty (Zaznacz pola wyboru):",
        "unassigned_alert": "⚠️ Wykryto nieprzypisane kolumny. Sprawdź poniżej:",
        "star_schema_desc": "💡 Zapisanie modelu utworzy 1 tabelę faktów i N tabel wymiarów.",
        "success_applied": "Model schematu gwiazdy został pomyślnie zaprojektowany.",
        "err_no_period": "Należy określić kolumnę okresu.",
        "err_no_fact": "Nazwa tabeli faktów nie może być pusta.",
        "err_single_column": "Rozpoznano tylko 1 kolumnę. Sprawdź separator plików.",
        "err_unassigned": "Wszystkie kolumny muszą mieć przypisaną rolę lub być wykluczone.",
        "validation_passed": "✔ Wszystkie testy integralności zaliczone",
        "badge_period": "[Okres]",
        "badge_key": "[Klucz]",
        "badge_dim": "[Wymiar]",
        "badge_measure": "[Miara]",
        "badge_excluded": "[Wykluczone]",
        "badge_unassigned": "[Brak]",
        "step1_banner_pnl": "✨ Struktura Derek P&L przeanalizowana automatycznie!<br>• Sprawdź kolorowe etykiety w nagłówkach kolumn.<br>• <b>Kliknij [Dalej ▶] poniżej</b>, aby przejść do wydzielania wymiarów.",
        "step1_banner_generic": "⚡ Dane przeanalizowane! Zastosowano rekomendowany schemat gwiazdy.<br>• Sprawdź nagłówki kolumn i <b>kliknij [Dalej ▶] poniżej</b>.",
        "step1_banner_existing": "📋 Załadowano istniejącą konfigurację schematu gwiazdy.<br>• Przejrzyj ustawienia i kliknij [Dalej ▶].",
        "step2_banner": "💡 <b>Krok 1: Wydzielenie Wymiarów</b> — Wydziel osie analizy (Klient, Produkt, Podmiot) ze wszystkich kolumn.<br>• Wybierz wymiar i <b>zaznacz atrybuty</b> na liście po prawej stronie.<br>• Klucz naturalny pozostaje w tabeli faktów jako klucz obcy (FK).",
        "step3_banner": "💡 <b>Krok 2: Tabela Faktów i Miar</b> — Pozostałe kolumny numeryczne tworzą centralną tabelę faktów i miary (Sprzedaż, Koszt, Ilość).<br>• Kliknij [+ Dodaj Miarę], aby skonfigurować wskaźniki.",
        "step3_banner_unassigned": "⚠️ Niektóre kolumny pozostają nieprzypisane (🔴).<br>• Przypisz je do wymiarów lub oznacz jako wykluczone przed przejściem dalej.",
        "popup_title": "Data Refinery - Przeglądarka Danych Źródłowych",
        "popup_summary": "Łącznie {cols} kolumn / Próbka {rows:,} wierszy",
        "btn_close": "Zamknij",
        "col_header_name": "Nazwa Kolumny",
        "col_header_role": "Rola",
        "col_header_reason": "Powód / Uwagi",
        "col_header_kind": "Typ",
        "col_header_unit": "Jednostka",
        "col_header_scale": "Miejsca po przecinku",
        "col_header_target": "Tabela Docelowa",
        "validation_warn": "Ostrzeżenie Walidacji",
    },
}


def fit_table_columns(table: QTableWidget, lo: int = 130, hi: int = 340, pad: int = 24) -> None:
    """Auto-fit table column widths within comfortable [lo, hi] bounds with padding."""
    table.resizeColumnsToContents()
    for c in range(table.columnCount()):
        curr = table.columnWidth(c)
        table.setColumnWidth(c, max(lo, min(curr + pad, hi)))


class DataViewerPopupDialog(QDialog):
    """Full-size standalone data viewer popup dialog."""

    def __init__(
        self,
        parent: Optional[QWidget],
        headers: List[str],
        rows: List[List[str]],
        language_code: str = "ko",
    ):
        super().__init__(parent)
        t = WIZARD_TEXT.get(language_code, WIZARD_TEXT["ko"])
        self.setWindowTitle(t["popup_title"])
        self.resize(1300, 850)
        layout = QVBoxLayout(self)

        summary_text = t["popup_summary"].format(cols=len(headers), rows=len(rows))
        info_lbl = QLabel(summary_text)
        info_lbl.setStyleSheet(f"font-weight: bold; color: {PALETTE['navy']};")
        layout.addWidget(info_lbl)

        table = QTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setMinimumSectionSize(140)
        table.setAlternatingRowColors(True)

        for r_idx, row in enumerate(rows):
            for c_idx, val in enumerate(row):
                table.setItem(r_idx, c_idx, QTableWidgetItem(str(val)))

        fit_table_columns(table, lo=140, hi=340)
        layout.addWidget(table)
        btn_close = QPushButton(t["btn_close"])
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)


class MartModelWizardDialog(QDialog):
    """1200x820 dual-pane Kimball Star Schema modeling wizard."""

    def __init__(
        self,
        parent: Optional[QWidget],
        csv_path: Path | str,
        initial_model: Optional[MartModel] = None,
        language_code: str = "ko",
    ):
        super().__init__(parent)
        self.csv_path = Path(csv_path)
        self.initial_model = initial_model
        self.language_code = language_code if language_code in WIZARD_TEXT else "ko"
        self.t = WIZARD_TEXT[self.language_code]

        self.headers: List[str] = []
        self.sample_rows: List[List[str]] = []
        self.assignments: List[ColumnAssignment] = []
        self.extra_dimensions: List[str] = []
        self.detected_encoding: str = "utf-8"
        self.detected_delimiter: str = ","
        self.fact_table_name: str = "fact_pnl"
        self.period_column_name: str = ""
        self.final_model: Optional[MartModel] = None

        if self.initial_model:
            self.detected_delimiter = getattr(self.initial_model, "source_delimiter", ",") or ","
            self.detected_encoding = getattr(self.initial_model, "source_encoding", "utf-8-sig") or "utf-8-sig"
            self._load_csv_data(encoding=self.detected_encoding, delimiter=self.detected_delimiter)
        else:
            self._load_csv_data()
        self._init_window()
        self._init_ui()
        self._init_default_assignments()
        self._refresh_all_views()

    def _load_csv_data(self, encoding: Optional[str] = None, delimiter: Optional[str] = None) -> None:
        """Load up to 1,000 sample rows and headers using safe encoding & delimiter sniffing."""
        self.headers = []
        self.sample_rows = []
        if not self.csv_path.exists():
            return

        from src.csv_processing import sniff_source

        if not encoding or not delimiter:
            auto_enc, auto_delim = sniff_source(self.csv_path)
            self.detected_encoding = encoding or auto_enc
            self.detected_delimiter = delimiter or auto_delim
        else:
            self.detected_encoding = encoding
            self.detected_delimiter = delimiter

        try:
            with open(self.csv_path, "r", encoding=self.detected_encoding, errors="replace", newline="") as f:
                reader = csv.reader(f, delimiter=self.detected_delimiter)
                header_raw = next(reader, [])
                self.headers = [c.strip().lstrip("\ufeff") for c in header_raw if c.strip()]
                self.sample_rows = [[c.strip() for c in row] for _, row in zip(range(1000), reader)]
        except Exception:
            self.headers = []
            self.sample_rows = []
            rows, _ = read_file_rows(str(self.csv_path), delimiter=self.detected_delimiter, max_rows=1000)
            if rows:
                self.headers = [str(c).strip().lstrip("\ufeff") for c in rows[0] if str(c).strip()]
                self.sample_rows = [[str(c).strip() for c in r] for r in rows[1:]]

    def _init_window(self) -> None:
        self.setWindowTitle(f"💎 Data Refinery - {self.t['title']}")
        self.resize(1260, 850)
        self.setMinimumSize(1020, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # 1. Top Stepper Header
        top_box = QFrame()
        top_box.setStyleSheet("background-color: #f8fafc; border-radius: 6px; padding: 6px;")
        top_lay = QHBoxLayout(top_box)
        top_lay.setContentsMargins(8, 4, 8, 4)

        self.step_labels = [
            QLabel(self.t["step1_title"]),
            QLabel("➔"),
            QLabel(self.t["step2_title"]),
            QLabel("➔"),
            QLabel(self.t["step3_title"]),
            QLabel("➔"),
            QLabel(self.t["step4_title"]),
        ]
        for lbl in self.step_labels:
            lbl.setFont(QFont("Segoe UI", 10))
            top_lay.addWidget(lbl)
        top_lay.addStretch(1)

        self.btn_expand = QPushButton(self.t["btn_expand_data"])
        self.btn_expand.setStyleSheet("font-weight: bold; padding: 5px 12px;")
        self.btn_expand.clicked.connect(self._on_expand_data_viewer)
        top_lay.addWidget(self.btn_expand)

        main_layout.addWidget(top_box)

        # 2. Main Central Splitter (Left: 58% Data Grid, Right: 42% Wizard Steps)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # --- Left Pane: Large Data Grid ---
        left_pane = QWidget()
        left_lay = QVBoxLayout(left_pane)
        left_lay.setContentsMargins(0, 0, 0, 0)

        grid_toolbar = QHBoxLayout()
        self.lbl_grid_info = QLabel()
        self.lbl_grid_info.setStyleSheet("font-weight: bold; color: #334155;")
        grid_toolbar.addWidget(self.lbl_grid_info)
        grid_toolbar.addStretch(1)
        left_lay.addLayout(grid_toolbar)

        self.data_table = QTableWidget()
        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.data_table.horizontalHeader().setMinimumSectionSize(130)
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectColumns)
        self.data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._populate_preview_table()
        left_lay.addWidget(self.data_table)
        self.splitter.addWidget(left_pane)

        # --- Right Pane: Wizard Step Cards ---
        right_pane = QWidget()
        right_lay = QVBoxLayout(right_pane)
        right_lay.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()
        self._init_step1_card()
        self._init_step2_card()
        self._init_step3_card()
        self._init_step4_card()

        right_lay.addWidget(self.stack)
        self.splitter.addWidget(right_pane)

        self.splitter.setStretchFactor(0, 6)
        self.splitter.setStretchFactor(1, 4)
        main_layout.addWidget(self.splitter, stretch=1)

        # 3. Bottom Action Buttons
        bot_box = QHBoxLayout()
        self.btn_cancel = QPushButton(self.t["btn_cancel"])
        self.btn_cancel.clicked.connect(self.reject)
        bot_box.addWidget(self.btn_cancel)

        bot_box.addStretch(1)

        self.btn_back = QPushButton(self.t["btn_back"])
        self.btn_back.clicked.connect(self._on_prev_step)
        bot_box.addWidget(self.btn_back)

        self.btn_next = QPushButton(self.t["btn_next"])
        self.btn_next.setStyleSheet("font-weight: bold; background-color: #2563eb; color: white; padding: 6px 18px;")
        self.btn_next.clicked.connect(self._on_next_step)
        bot_box.addWidget(self.btn_next)

        self.btn_finish = QPushButton(self.t["btn_finish"])
        self.btn_finish.setStyleSheet("font-weight: bold; background-color: #16a34a; color: white; padding: 6px 20px;")
        self.btn_finish.clicked.connect(self._on_finish)
        bot_box.addWidget(self.btn_finish)

        main_layout.addLayout(bot_box)
        self._update_stepper_ui()

    def _populate_preview_table(self) -> None:
        """Fill the left sample grid and set initial comfortable column widths."""
        self.lbl_grid_info.setText(f"📄 원본 미리보기: {len(self.headers)}개 열 / {len(self.sample_rows):,}개 행")
        self.data_table.setRowCount(len(self.sample_rows))
        self.data_table.setColumnCount(len(self.headers))
        self.data_table.setHorizontalHeaderLabels(self.headers)
        for r_idx, row in enumerate(self.sample_rows):
            for c_idx, val in enumerate(row):
                if c_idx < len(self.headers):
                    self.data_table.setItem(r_idx, c_idx, QTableWidgetItem(str(val)))
        fit_table_columns(self.data_table, lo=130, hi=340)

    def _init_step1_card(self) -> None:
        """Step 1: Source & Delimiter Check + Smart Auto-Guess Preset."""
        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setSpacing(8)

        title = QLabel(f"<h3>{self.t['step1_title']}</h3>")
        lay.addWidget(title)

        # Delimiter & Encoding selector toolbar
        fmt_frame = QFrame()
        fmt_frame.setStyleSheet("background-color: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 5px; padding: 4px;")
        fmt_lay = QHBoxLayout(fmt_frame)
        fmt_lay.setContentsMargins(6, 4, 6, 4)

        fmt_lay.addWidget(QLabel(self.t["delimiter_label"]))
        self.cmb_delimiter = QComboBox()
        self.cmb_delimiter.addItem(self.t["delimiter_comma"], ",")
        self.cmb_delimiter.addItem(self.t["delimiter_tab"], "\t")
        self.cmb_delimiter.addItem(self.t["delimiter_semicolon"], ";")
        self.cmb_delimiter.addItem(self.t["delimiter_pipe"], "|")
        idx_delim = self.cmb_delimiter.findData(self.detected_delimiter)
        if idx_delim >= 0:
            self.cmb_delimiter.setCurrentIndex(idx_delim)
        self.cmb_delimiter.currentIndexChanged.connect(self._on_format_changed)
        fmt_lay.addWidget(self.cmb_delimiter)

        fmt_lay.addWidget(QLabel(self.t["encoding_label"]))
        self.cmb_encoding = QComboBox()
        for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp949", "euc-kr", "windows-1250", "cp1250", "cp1252", "latin-1"):
            self.cmb_encoding.addItem(enc)
        try:
            import codecs
            canonical_enc = codecs.lookup(self.detected_encoding).name
        except Exception:
            canonical_enc = self.detected_encoding.lower()
        idx_enc = self.cmb_encoding.findText(canonical_enc)
        if idx_enc < 0:
            self.cmb_encoding.addItem(canonical_enc)
            idx_enc = self.cmb_encoding.findText(canonical_enc)
        if idx_enc >= 0:
            self.cmb_encoding.setCurrentIndex(idx_enc)
        self.cmb_encoding.currentIndexChanged.connect(self._on_format_changed)
        fmt_lay.addWidget(self.cmb_encoding)

        lay.addWidget(fmt_frame)

        # Single column error banner
        self.warn_single_column_banner = QLabel(
            f"<div style='background-color: #fee2e2; border: 1px solid #f87171; border-radius: 6px; padding: 10px;'>"
            f"<b style='color: #b91c1c; font-size: 12px;'>{self.t['warn_single_column']}</b>"
            f"</div>"
        )
        self.warn_single_column_banner.setWordWrap(True)
        self.warn_single_column_banner.setVisible(len(self.headers) <= 1)
        lay.addWidget(self.warn_single_column_banner)

        # Guidance banner
        self.lbl_step1_banner = QLabel()
        self.lbl_step1_banner.setWordWrap(True)
        lay.addWidget(self.lbl_step1_banner)

        # Presets row
        preset_row = QHBoxLayout()
        self.btn_pnl_preset = QPushButton(self.t["btn_auto_guess_pnl"])
        self.btn_pnl_preset.setStyleSheet("font-weight: bold; padding: 8px; background-color: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; border-radius: 4px;")
        self.btn_pnl_preset.clicked.connect(self._apply_pnl_auto_guess)
        preset_row.addWidget(self.btn_pnl_preset)

        self.btn_generic_guess = QPushButton(self.t["btn_auto_guess_generic"])
        self.btn_generic_guess.clicked.connect(self._apply_generic_auto_guess)
        preset_row.addWidget(self.btn_generic_guess)
        lay.addLayout(preset_row)

        self.lbl_guess_summary = QLabel()
        self.lbl_guess_summary.setWordWrap(True)
        self.lbl_guess_summary.setStyleSheet("background-color: #f1f5f9; padding: 8px; border-radius: 4px; font-weight: bold;")
        lay.addWidget(self.lbl_guess_summary)

        # Mini summary table
        self.step1_table = QTableWidget(len(self.headers), 3)
        self.step1_table.setHorizontalHeaderLabels([
            self.t["col_header_name"],
            self.t["col_header_role"],
            self.t["col_header_reason"],
        ])
        self.step1_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.step1_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.step1_table)

        self.stack.addWidget(card)

    def _init_step2_card(self) -> None:
        """Step 2: Dimension Partitioning & Interactive Checkbox Attribute Grouping."""
        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setSpacing(8)

        title = QLabel(f"<h3>{self.t['step2_title']}</h3>")
        lay.addWidget(title)

        self.lbl_step2_banner = QLabel(
            f"<div style='background-color: #faf5ff; border: 1px solid #e9d5ff; border-radius: 6px; padding: 10px;'>"
            f"<span style='color: #581c87; font-size: 11px; line-height: 1.5;'>{self.t['step2_banner']}</span>"
            f"</div>"
        )
        self.lbl_step2_banner.setWordWrap(True)
        lay.addWidget(self.lbl_step2_banner)

        # Dimension list management buttons
        dim_btn_row = QHBoxLayout()
        self.btn_add_dim = QPushButton(self.t["add_dim"])
        self.btn_add_dim.clicked.connect(self._on_add_dimension)
        self.btn_del_dim = QPushButton(self.t["del_dim"])
        self.btn_del_dim.clicked.connect(self._on_del_dimension)
        dim_btn_row.addWidget(self.btn_add_dim)
        dim_btn_row.addWidget(self.btn_del_dim)
        lay.addLayout(dim_btn_row)

        self.dim_list_widget = QListWidget()
        self.dim_list_widget.currentRowChanged.connect(self._on_dimension_selected)
        self.dim_list_widget.setMaximumHeight(90)
        lay.addWidget(self.dim_list_widget)

        # Dimension editor box
        self.dim_edit_box = QFrame()
        self.dim_edit_box.setStyleSheet("background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px;")
        dim_form = QVBoxLayout(self.dim_edit_box)
        dim_form.setSpacing(6)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(self.t["dim_name_label"]))
        self.txt_dim_name = QLineEdit()
        self.txt_dim_name.textChanged.connect(self._on_dim_name_edited)
        name_row.addWidget(self.txt_dim_name)
        dim_form.addLayout(name_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel(self.t["dim_keys_label"]))
        self.cmb_dim_key = QComboBox()
        self.cmb_dim_key.currentTextChanged.connect(self._on_dim_key_edited)
        key_row.addWidget(self.cmb_dim_key)
        dim_form.addLayout(key_row)

        dim_form.addWidget(QLabel(f"<b>{self.t['dim_attrs_label']}</b>"))
        self.dim_attrs_checklist = QListWidget()
        self.dim_attrs_checklist.itemChanged.connect(self._on_dim_attr_item_changed)
        dim_form.addWidget(self.dim_attrs_checklist)

        lay.addWidget(self.dim_edit_box)

        self.lbl_unassigned_warn = QLabel()
        self.lbl_unassigned_warn.setWordWrap(True)
        self.lbl_unassigned_warn.setStyleSheet("color: #b91c1c; font-weight: bold;")
        lay.addWidget(self.lbl_unassigned_warn)

        self.stack.addWidget(card)

    def _init_step3_card(self) -> None:
        """Step 3: Fact Table & Additive Measures Configuration."""
        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setSpacing(8)

        title = QLabel(f"<h3>{self.t['step3_title']}</h3>")
        lay.addWidget(title)

        self.lbl_step3_banner = QLabel(
            f"<div style='background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 10px;'>"
            f"<span style='color: #14532d; font-size: 11px; line-height: 1.5;'>{self.t['step3_banner']}</span>"
            f"</div>"
        )
        self.lbl_step3_banner.setWordWrap(True)
        lay.addWidget(self.lbl_step3_banner)

        # Fact table name
        fact_box = QHBoxLayout()
        fact_box.addWidget(QLabel(self.t["fact_table_label"]))
        self.txt_fact_name = QLineEdit(self.fact_table_name)
        self.txt_fact_name.textChanged.connect(self._on_fact_name_edited)
        fact_box.addWidget(self.txt_fact_name)
        lay.addLayout(fact_box)

        # Period column
        period_box = QHBoxLayout()
        period_box.addWidget(QLabel(self.t["period_label"]))
        self.cmb_period = QComboBox()
        self.cmb_period.currentTextChanged.connect(self._on_period_changed)
        period_box.addWidget(self.cmb_period)
        lay.addLayout(period_box)

        # Row grain key selector (H2)
        row_key_box = QHBoxLayout()
        row_key_box.addWidget(QLabel(self.t["row_key_label"]))
        self.cmb_row_key = QComboBox()
        self.cmb_row_key.currentTextChanged.connect(self._on_row_key_changed)
        row_key_box.addWidget(self.cmb_row_key)
        lay.addLayout(row_key_box)

        # Partition column selector (H2)
        partition_box = QHBoxLayout()
        partition_box.addWidget(QLabel(self.t["partition_label"]))
        self.cmb_partition = QComboBox()
        self.cmb_partition.currentTextChanged.connect(self._on_partition_changed)
        partition_box.addWidget(self.cmb_partition)
        lay.addLayout(partition_box)

        # Measures list table toolbar
        meas_toolbar = QHBoxLayout()
        meas_toolbar.addWidget(QLabel("<b>가산 수치 지표 (Additive Measures):</b>"))
        meas_toolbar.addStretch(1)
        self.btn_add_measure = QPushButton(self.t["btn_add_measure"])
        self.btn_add_measure.setStyleSheet("font-weight: bold; background-color: #f0fdf4; border: 1px solid #86efac; color: #166534; padding: 4px 10px;")
        self.btn_add_measure.clicked.connect(self._on_add_measure)
        meas_toolbar.addWidget(self.btn_add_measure)

        self.btn_del_measure = QPushButton(self.t["btn_del_measure"])
        self.btn_del_measure.setStyleSheet("color: #b91c1c; padding: 4px 10px;")
        self.btn_del_measure.clicked.connect(self._on_del_measure)
        meas_toolbar.addWidget(self.btn_del_measure)
        lay.addLayout(meas_toolbar)

        self.measure_table = QTableWidget(0, 4)
        self.measure_table.setHorizontalHeaderLabels([
            self.t["col_header_name"],
            self.t["col_header_kind"],
            self.t["col_header_unit"],
            self.t["col_header_scale"],
        ])
        self.measure_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.measure_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.measure_table.itemChanged.connect(self._on_measure_item_edited)
        lay.addWidget(self.measure_table)

        self.stack.addWidget(card)

    def _init_step4_card(self) -> None:
        """Step 4: Star Schema Diagram & Final Integrity Check."""
        card = QWidget()
        lay = QVBoxLayout(card)
        lay.setSpacing(8)

        title = QLabel(f"<h3>{self.t['step4_title']}</h3>")
        lay.addWidget(title)

        desc = QLabel(self.t["star_schema_desc"])
        desc.setWordWrap(True)
        desc.setStyleSheet("background-color: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; padding: 6px; border-radius: 4px;")
        lay.addWidget(desc)

        # Star schema scene & view
        self.schema_scene = QGraphicsScene()
        self.schema_view = QGraphicsView(self.schema_scene)
        self.schema_view.setMaximumHeight(200)
        lay.addWidget(self.schema_view)

        # Mapping table
        lay.addWidget(QLabel("<b>최종 컬럼 매핑 현황:</b>"))
        self.mapping_table = QTableWidget(0, 4)
        self.mapping_table.setHorizontalHeaderLabels(["컬럼명", "최종 역할", "대상 테이블", "비고"])
        self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.mapping_table)

        self.lbl_validation_status = QLabel()
        self.lbl_validation_status.setWordWrap(True)
        lay.addWidget(self.lbl_validation_status)

        self.stack.addWidget(card)

    def _init_default_assignments(self) -> None:
        """Run initial auto-guess or load from initial_model."""
        if self.initial_model:
            self.assignments = model_to_assignments(self.initial_model, self.headers)
            self.fact_table_name = self.initial_model.fact_table
            self.period_column_name = self.initial_model.period_column
        else:
            self._apply_pnl_auto_guess()

    def _apply_pnl_auto_guess(self) -> None:
        res: ModelGuessResult = guess_model_assignments(
            columns=self.headers,
            csv_path=self.csv_path,
            default_fact_name="fact_pnl",
            delimiter=self.detected_delimiter,
            encoding=self.detected_encoding,
        )
        self.assignments = res.assignments
        self.fact_table_name = res.fact_table
        self.period_column_name = res.period_column
        self.lbl_guess_summary.setText(f"✔ {res.summary_text}")
        fit_table_columns(self.data_table, lo=130, hi=340)
        self._refresh_all_views()

    def _apply_generic_auto_guess(self) -> None:
        res: ModelGuessResult = guess_model_assignments(
            columns=self.headers,
            csv_path=self.csv_path,
            default_fact_name="fact_general",
            delimiter=self.detected_delimiter,
            encoding=self.detected_encoding,
        )
        self.assignments = res.assignments
        self.fact_table_name = res.fact_table
        self.period_column_name = res.period_column
        self.lbl_guess_summary.setText(f"✔ {res.summary_text}")
        fit_table_columns(self.data_table, lo=130, hi=340)
        self._refresh_all_views()

    def _refresh_all_views(self) -> None:
        """Synchronize header badges, step tables, and schema diagram."""
        self._update_header_badges()
        self._refresh_step1_view()
        self._refresh_step2_view()
        self._refresh_step3_view()
        self._refresh_step4_view()

    def _update_header_badges(self) -> None:
        """Update column headers in the large data grid with colored badges and tooltips."""
        role_map = {a.column_name: a for a in self.assignments}
        badge_labels = []

        for col in self.headers:
            asgn = role_map.get(col)
            role = asgn.role if asgn else ROLE_UNASSIGNED
            if role == ROLE_PERIOD:
                badge = f"🔵{self.t['badge_period']}\n{col}"
            elif role in (ROLE_ROW_KEY, ROLE_PARTITION):
                badge = f"🟢{self.t['badge_key']}\n{col}"
            elif role in (ROLE_DIM_KEY, ROLE_DIM_ATTR):
                badge = f"🟡{self.t['badge_dim']}:{asgn.dimension_name}\n{col}"
            elif role == ROLE_MEASURE:
                badge = f"🟣{self.t['badge_measure']}\n{col}"
            elif role == ROLE_EXCLUDED:
                badge = f"⚪{self.t['badge_excluded']}\n{col}"
            else:
                badge = f"🔴{self.t['badge_unassigned']}\n{col}"
            badge_labels.append(badge)

        self.data_table.setHorizontalHeaderLabels(badge_labels)
        for c in range(self.data_table.columnCount()):
            item = self.data_table.horizontalHeaderItem(c)
            if item and c < len(self.headers):
                role_tag = badge_labels[c].split("\n")[0]
                item.setToolTip(f"{self.headers[c]} ({role_tag})")

    def _on_format_changed(self) -> None:
        """Handle delimiter or encoding change from Step 1 combos."""
        delim = self.cmb_delimiter.currentData() or ","
        enc = self.cmb_encoding.currentText() or "utf-8"
        self._load_csv_data(encoding=enc, delimiter=delim)
        self.warn_single_column_banner.setVisible(len(self.headers) <= 1)
        self._populate_preview_table()
        self._init_default_assignments()
        self._refresh_all_views()

    def _refresh_step1_view(self) -> None:
        pnl_keywords = ("ACCOUNT", "REVENUE", "매출", "손익", "계정", "금액", "수익")
        is_pnl = any(any(k in c.upper() for k in pnl_keywords) for c in self.headers)
        if self.initial_model:
            banner_html = self.t["step1_banner_existing"]
            bg, border, color = "#eff6ff", "#bfdbfe", "#1e40af"
        elif is_pnl:
            banner_html = self.t["step1_banner_pnl"]
            bg, border, color = "#eff6ff", "#bfdbfe", "#1e40af"
        else:
            banner_html = self.t["step1_banner_generic"]
            bg, border, color = "#f0f9ff", "#bae6fd", "#0369a1"

        self.lbl_step1_banner.setText(
            f"<div style='background-color: {bg}; border: 1px solid {border}; border-radius: 6px; padding: 10px;'>"
            f"<span style='color: {color}; font-size: 11px; line-height: 1.5;'>{banner_html}</span>"
            f"</div>"
        )

        self.step1_table.setRowCount(len(self.assignments))
        for r_idx, a in enumerate(self.assignments):
            self.step1_table.setItem(r_idx, 0, QTableWidgetItem(a.column_name))
            role_desc = f"{a.role} ({a.dimension_name})" if a.dimension_name else a.role
            self.step1_table.setItem(r_idx, 1, QTableWidgetItem(role_desc))
            self.step1_table.setItem(r_idx, 2, QTableWidgetItem(a.reason))

    def _refresh_step2_view(self) -> None:
        """Step 2 View: Dimension Partitioning and Attribute Assignment."""
        dims_dict: Dict[str, Dict[str, List[str]]] = {}
        for dname in self.extra_dimensions:
            dims_dict[dname] = {"keys": [], "attrs": []}
        for a in self.assignments:
            if a.dimension_name:
                dims_dict.setdefault(a.dimension_name, {"keys": [], "attrs": []})
                if a.role == ROLE_DIM_KEY:
                    dims_dict[a.dimension_name]["keys"].append(a.column_name)
                elif a.role == ROLE_DIM_ATTR:
                    dims_dict[a.dimension_name]["attrs"].append(a.column_name)

        curr_row = self.dim_list_widget.currentRow()
        self.dim_list_widget.blockSignals(True)
        self.dim_list_widget.clear()
        for dname in dims_dict.keys():
            self.dim_list_widget.addItem(QListWidgetItem(dname))
        self.dim_list_widget.blockSignals(False)

        if dims_dict:
            target_row = max(0, min(curr_row, len(dims_dict) - 1))
            self.dim_list_widget.setCurrentRow(target_row)
            self._on_dimension_selected(target_row)
        else:
            self.txt_dim_name.clear()
            self.cmb_dim_key.clear()
            self.dim_attrs_checklist.clear()

        unassigned = [a.column_name for a in self.assignments if a.role == ROLE_UNASSIGNED]
        if unassigned:
            self.lbl_unassigned_warn.setText(
                f"⚠️ 미할당 열 {len(unassigned)}개: {', '.join(unassigned[:5])}..."
            )
        else:
            self.lbl_unassigned_warn.setText("✔ 모든 열에 역할이 할당되었습니다.")

    def _refresh_step3_view(self) -> None:
        """Step 3 View: Fact Table & Measures Configuration."""
        self.txt_fact_name.setText(self.fact_table_name)
        self.cmb_period.blockSignals(True)
        self.cmb_period.clear()
        for col in self.headers:
            self.cmb_period.addItem(col)
        if self.period_column_name in self.headers:
            self.cmb_period.setCurrentText(self.period_column_name)
        self.cmb_period.blockSignals(False)

        # Row Key Selector
        curr_row_key = next((a.column_name for a in self.assignments if a.role == ROLE_ROW_KEY), "")
        self.cmb_row_key.blockSignals(True)
        self.cmb_row_key.clear()
        self.cmb_row_key.addItem("-- 원천 행 식별키 선택 --", "")
        for col in self.headers:
            if col != self.period_column_name:
                self.cmb_row_key.addItem(col, col)
        if curr_row_key:
            idx = self.cmb_row_key.findData(curr_row_key)
            if idx >= 0:
                self.cmb_row_key.setCurrentIndex(idx)
        self.cmb_row_key.blockSignals(False)

        # Partition Selector
        curr_partition = next((a.column_name for a in self.assignments if a.role == ROLE_PARTITION), "")
        self.cmb_partition.blockSignals(True)
        self.cmb_partition.clear()
        self.cmb_partition.addItem("(선택 안 함 - 단일 법인/파티션 없음)", "")
        for col in self.headers:
            if col != self.period_column_name and col != curr_row_key:
                self.cmb_partition.addItem(col, col)
        if curr_partition:
            idx = self.cmb_partition.findData(curr_partition)
            if idx >= 0:
                self.cmb_partition.setCurrentIndex(idx)
        self.cmb_partition.blockSignals(False)

        measures = [a for a in self.assignments if a.role == ROLE_MEASURE]
        self.measure_table.blockSignals(True)
        self.measure_table.setRowCount(len(measures))
        for r_idx, m in enumerate(measures):
            item_col = QTableWidgetItem(m.column_name)
            item_col.setFlags(item_col.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.measure_table.setItem(r_idx, 0, item_col)
            self.measure_table.setItem(r_idx, 1, QTableWidgetItem(m.measure_kind))
            self.measure_table.setItem(r_idx, 2, QTableWidgetItem(m.measure_unit or "-"))
            self.measure_table.setItem(r_idx, 3, QTableWidgetItem(str(m.measure_scale)))
        self.measure_table.blockSignals(False)

    def _refresh_step4_view(self) -> None:
        """Render star schema diagram and mapping review table."""
        # 1. Diagram Scene
        self.schema_scene.clear()
        fact_title = f"Fact: {self.fact_table_name}"
        self.schema_scene.addRect(220, 10, 180, 42, QPen(QColor(PALETTE["navy"]), 2))
        self.schema_scene.addText(fact_title).setPos(225, 20)

        dims_dict: Dict[str, Dict[str, List[str]]] = {}
        for a in self.assignments:
            if a.dimension_name:
                dims_dict.setdefault(a.dimension_name, {"keys": [], "attrs": []})
                if a.role == ROLE_DIM_KEY:
                    dims_dict[a.dimension_name]["keys"].append(a.column_name)

        for index, (dname, parts) in enumerate(dims_dict.items()):
            x = index * 160 + 20
            self.schema_scene.addRect(x, 110, 140, 42, QPen(QColor("#2563eb"), 2))
            dim_text = self.schema_scene.addText(f"Dim: {dname}")
            dim_text.setPos(x + 5, 120)
            # Connecting line from fact to dim
            self.schema_scene.addLine(310, 52, x + 70, 110, QPen(QColor("#94a3b8"), 1))

        self.schema_scene.setSceneRect(self.schema_scene.itemsBoundingRect().adjusted(-10, -10, 10, 10))

        # 2. Mapping Table
        self.mapping_table.setHorizontalHeaderLabels([
            self.t["col_header_name"],
            self.t["col_header_role"],
            self.t["col_header_target"],
            self.t["col_header_reason"],
        ])
        self.mapping_table.setRowCount(len(self.assignments))
        for r_idx, a in enumerate(self.assignments):
            self.mapping_table.setItem(r_idx, 0, QTableWidgetItem(a.column_name))
            self.mapping_table.setItem(r_idx, 1, QTableWidgetItem(a.role))
            target = a.dimension_name if a.dimension_name else (self.fact_table_name if a.role == ROLE_MEASURE else "-")
            self.mapping_table.setItem(r_idx, 2, QTableWidgetItem(target))
            self.mapping_table.setItem(r_idx, 3, QTableWidgetItem(a.reason))

        # 3. Model validation (strictly using MartEngine._validate_model)
        try:
            temp_model = assignments_to_model(
                self.assignments,
                model_name=self.fact_table_name,
                fact_table=self.fact_table_name,
                period_column=self.period_column_name,
                source_delimiter=self.detected_delimiter,
                source_encoding=self.detected_encoding,
            )
            from src.mart.engine import validate_mart_model
            validate_mart_model(temp_model)
            self.lbl_validation_status.setText(f"<font color='#16a34a'><b>{self.t['validation_passed']}</b></font>")
            self.final_model = temp_model
        except Exception as e:
            self.lbl_validation_status.setText(f"<font color='#dc2626'><b>{self.t['validation_warn']}: {e}</b></font>")
            self.final_model = None

    def _on_expand_data_viewer(self) -> None:
        """Launch the full-screen standalone data viewer popup."""
        popup = DataViewerPopupDialog(self, self.headers, self.sample_rows, language_code=self.language_code)
        popup.exec()

    def _on_fact_name_edited(self, text: str) -> None:
        trimmed = text.strip()
        self.fact_table_name = trimmed

    def _on_period_changed(self, text: str) -> None:
        self.period_column_name = text
        for a in self.assignments:
            if a.column_name == text:
                a.role = ROLE_PERIOD
                a.dimension_name = ""  # Clean ghost dimension
            elif a.role == ROLE_PERIOD:
                a.role = ROLE_EXCLUDED
        self._update_header_badges()

    def _on_row_key_changed(self, text: str) -> None:
        col = self.cmb_row_key.currentData() or ""
        for a in self.assignments:
            if a.column_name == col and col:
                a.role = ROLE_ROW_KEY
                a.dimension_name = ""
                a.reason = "원천 행 고유 식별키"
            elif a.role == ROLE_ROW_KEY and a.column_name != col:
                a.role = ROLE_UNASSIGNED
                a.reason = "역할 미할당"
        self._update_header_badges()

    def _on_partition_changed(self, text: str) -> None:
        col = self.cmb_partition.currentData() or ""
        for a in self.assignments:
            if a.column_name == col and col:
                a.role = ROLE_PARTITION
                a.dimension_name = ""
                a.reason = "데이터 파티션/법인 격리 기준"
            elif a.role == ROLE_PARTITION and a.column_name != col:
                a.role = ROLE_UNASSIGNED
                a.reason = "역할 미할당"
        self._update_header_badges()

    def _on_dimension_selected(self, row: int) -> None:
        item = self.dim_list_widget.item(row)
        if not item:
            return
        dname = item.text()
        self.txt_dim_name.blockSignals(True)
        self.txt_dim_name.setText(dname)
        self.txt_dim_name.blockSignals(False)

        key_cols = [a.column_name for a in self.assignments if a.dimension_name == dname and a.role == ROLE_DIM_KEY]

        # Populate dimension key selector
        self.cmb_dim_key.blockSignals(True)
        self.cmb_dim_key.clear()
        for col in self.headers:
            if col != self.period_column_name:
                self.cmb_dim_key.addItem(col)
        if key_cols:
            self.cmb_dim_key.setCurrentText(key_cols[0])
        self.cmb_dim_key.blockSignals(False)

        # Populate dimension attributes checkable list
        self.dim_attrs_checklist.blockSignals(True)
        self.dim_attrs_checklist.clear()
        curr_key = key_cols[0] if key_cols else ""
        for col in self.headers:
            if col == self.period_column_name:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, col)
            asgn = next((a for a in self.assignments if a.column_name == col), None)

            if col == curr_key:
                item.setText(f"{col} (식별키)")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            elif asgn and asgn.dimension_name == dname and asgn.role == ROLE_DIM_ATTR:
                item.setText(f"{col} (속성)")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Checked)
            elif asgn and asgn.dimension_name and asgn.dimension_name != dname:
                item.setText(f"{col} [소속: {asgn.dimension_name}]")
                item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setText(col)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)

            self.dim_attrs_checklist.addItem(item)
        self.dim_attrs_checklist.blockSignals(False)

    def _on_dim_attr_item_changed(self, item: QListWidgetItem) -> None:
        """Handle user checking or unchecking an attribute column for the current dimension."""
        col = item.data(Qt.ItemDataRole.UserRole)
        if not col:
            return
        curr_item = self.dim_list_widget.currentItem()
        if not curr_item:
            return
        dname = curr_item.text().strip()
        is_checked = (item.checkState() == Qt.CheckState.Checked)

        for a in self.assignments:
            if a.column_name == col:
                if is_checked:
                    a.role = ROLE_DIM_ATTR
                    a.dimension_name = dname
                    a.reason = f"{dname} 차원 속성"
                else:
                    if a.dimension_name == dname and a.role == ROLE_DIM_ATTR:
                        a.role = ROLE_UNASSIGNED
                        a.dimension_name = ""
                        a.reason = "미할당"
                break

        self._update_header_badges()
        unassigned = [a.column_name for a in self.assignments if a.role == ROLE_UNASSIGNED]
        if unassigned:
            self.lbl_unassigned_warn.setText(
                f"⚠️ 미할당 열 {len(unassigned)}개: {', '.join(unassigned[:5])}..."
            )
        else:
            self.lbl_unassigned_warn.setText("✔ 모든 열에 역할이 할당되었습니다.")

    def _on_measure_item_edited(self, item: QTableWidgetItem) -> None:
        row = item.row()
        col_item = self.measure_table.item(row, 0)
        if not col_item:
            return
        col_name = col_item.text()
        asgn = next((a for a in self.assignments if a.column_name == col_name and a.role == ROLE_MEASURE), None)
        if not asgn:
            return

        col_idx = item.column()
        if col_idx == 1:
            kind = item.text().strip().lower()
            if kind in ("value", "cost", "quantity"):
                asgn.measure_kind = kind
        elif col_idx == 2:
            asgn.measure_unit = item.text().strip()
        elif col_idx == 3:
            try:
                scale = int(item.text().strip())
                if 0 <= scale <= 18:
                    asgn.measure_scale = scale
            except ValueError:
                pass

    def _on_add_measure(self, target_column: Optional[str] = None) -> None:
        """Add a column as a new fact additive measure via user selection or direct column name."""
        candidates = [
            a for a in self.assignments
            if a.role in (ROLE_UNASSIGNED, ROLE_EXCLUDED)
            and a.column_name != self.period_column_name
        ]
        if not candidates:
            QMessageBox.information(self, "지표 추가", "지표로 추가할 수 있는 미할당/제외 열이 없습니다.")
            return

        if target_column:
            chosen_col = target_column
        else:
            from PySide6.QtWidgets import QInputDialog
            candidate_names = [a.column_name for a in candidates]
            chosen_col, ok = QInputDialog.getItem(
                self,
                "가산 수치 지표 추가",
                "팩트 테이블에 추가할 지표 열을 선택하세요:",
                candidate_names,
                0,
                False,
            )
            if not ok or not chosen_col:
                return

        target = next((a for a in candidates if a.column_name == chosen_col), None)
        if target:
            target.role = ROLE_MEASURE
            target.dimension_name = ""
            target.measure_kind = "value"
            target.reason = "사용자 추가 지표"
            self._refresh_step3_view()
            self._update_header_badges()

    def _on_del_measure(self) -> None:
        """Remove the selected measure from fact table."""
        curr_row = self.measure_table.currentRow()
        if curr_row < 0:
            return
        item = self.measure_table.item(curr_row, 0)
        if not item:
            return
        col_name = item.text()
        for a in self.assignments:
            if a.column_name == col_name and a.role == ROLE_MEASURE:
                a.role = ROLE_UNASSIGNED
                a.reason = "지표 해제"
                break
        self._refresh_step3_view()
        self._update_header_badges()

    def _on_dim_name_edited(self, new_name: str) -> None:
        trimmed = new_name.strip()
        curr_item = self.dim_list_widget.currentItem()
        if not curr_item or not trimmed:
            return
        old_name = curr_item.text()
        if trimmed == old_name:
            return
        # Guard against collision with other dimension names
        other_names = {
            self.dim_list_widget.item(i).text()
            for i in range(self.dim_list_widget.count())
            if self.dim_list_widget.item(i) is not curr_item
        }
        if trimmed in other_names:
            return
        curr_item.setText(trimmed)
        if old_name in self.extra_dimensions:
            self.extra_dimensions[self.extra_dimensions.index(old_name)] = trimmed
        for a in self.assignments:
            if a.dimension_name == old_name:
                a.dimension_name = trimmed
        self._update_header_badges()

    def _on_dim_key_edited(self, new_key: str) -> None:
        curr_item = self.dim_list_widget.currentItem()
        if not curr_item or not new_key:
            return
        dname = curr_item.text()
        curr_keys = [a for a in self.assignments if a.dimension_name == dname and a.role == ROLE_DIM_KEY]
        if new_key in {k.column_name for k in curr_keys}:
            return
        if curr_keys and curr_keys[0].column_name != new_key:
            curr_keys[0].role = ROLE_DIM_ATTR
        for a in self.assignments:
            if a.column_name == new_key:
                a.role = ROLE_DIM_KEY
                a.dimension_name = dname
        self._update_header_badges()

    def _on_add_dimension(self) -> None:
        existing_names = {self.dim_list_widget.item(i).text() for i in range(self.dim_list_widget.count())}
        idx = self.dim_list_widget.count() + 1
        dname = f"차원_{idx}"
        while dname in existing_names:
            idx += 1
            dname = f"차원_{idx}"
        self.extra_dimensions.append(dname)
        # Pick the first unassigned column as an initial key for this new dimension
        unassigned = [a for a in self.assignments if a.role == ROLE_UNASSIGNED]
        if unassigned:
            unassigned[0].role = ROLE_DIM_KEY
            unassigned[0].dimension_name = dname
            unassigned[0].reason = f"{dname} 차원 키"
        self._refresh_all_views()
        # Find and select the newly added dimension
        for i in range(self.dim_list_widget.count()):
            if self.dim_list_widget.item(i).text() == dname:
                self.dim_list_widget.setCurrentRow(i)
                break

    def _on_del_dimension(self) -> None:
        curr_row = self.dim_list_widget.currentRow()
        if curr_row < 0:
            return
        item = self.dim_list_widget.takeItem(curr_row)
        dname = item.text()
        if dname in self.extra_dimensions:
            self.extra_dimensions.remove(dname)
        for a in self.assignments:
            if a.dimension_name == dname:
                a.dimension_name = ""
                a.role = ROLE_UNASSIGNED
        self._refresh_all_views()

    def _on_prev_step(self) -> None:
        curr = self.stack.currentIndex()
        if curr > 0:
            self.stack.setCurrentIndex(curr - 1)
            self._update_stepper_ui()

    def _on_next_step(self) -> None:
        curr = self.stack.currentIndex()
        if curr < self.stack.count() - 1:
            self.stack.setCurrentIndex(curr + 1)
            self._update_stepper_ui()
            self._refresh_all_views()

    def _update_stepper_ui(self) -> None:
        curr = self.stack.currentIndex()
        self.btn_back.setEnabled(curr > 0)
        self.btn_next.setVisible(curr < self.stack.count() - 1)
        self.btn_finish.setVisible(curr == self.stack.count() - 1)

        # Highlight current step label
        for idx, lbl in enumerate([self.step_labels[0], self.step_labels[2], self.step_labels[4], self.step_labels[6]]):
            if idx == curr:
                lbl.setStyleSheet("color: #2563eb; font-weight: bold; text-decoration: underline;")
            else:
                lbl.setStyleSheet("color: #64748b; font-weight: normal;")

    def _on_finish(self) -> None:
        """Validate and finalize star schema model construction."""
        if not self.period_column_name:
            QMessageBox.warning(self, "입력 오류", self.t["err_no_period"])
            return

        unassigned = [a.column_name for a in self.assignments if a.role == ROLE_UNASSIGNED]
        if unassigned:
            reply = QMessageBox.question(
                self,
                "미할당 열 발견",
                f"다음 {len(unassigned)}개 열이 미할당 상태입니다:\n{', '.join(unassigned[:8])}\n\n이 열들을 모두 '제외(Excluded)'로 지정하고 완료하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                for a in self.assignments:
                    if a.role == ROLE_UNASSIGNED:
                        a.role = ROLE_EXCLUDED
            else:
                return

        try:
            candidate_model = assignments_to_model(
                self.assignments,
                model_name=self.txt_fact_name.text().strip() or "fact_pnl",
                fact_table=self.txt_fact_name.text().strip() or "fact_pnl",
                period_column=self.period_column_name,
                source_delimiter=self.detected_delimiter,
                source_encoding=self.detected_encoding,
            )
            from src.mart.engine import validate_mart_model
            validate_mart_model(candidate_model)
            self.final_model = candidate_model
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "모델 무결성 오류", f"모델 저장에 실패했습니다:\n{e}")

    def get_model(self) -> Optional[MartModel]:
        return self.final_model
