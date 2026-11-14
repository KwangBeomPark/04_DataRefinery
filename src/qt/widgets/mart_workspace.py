"""Guided single-fact mart workspace; all database work runs on the job runner."""

from __future__ import annotations

import csv
import json
import math
import threading
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGraphicsScene,
    QGraphicsView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app_paths import dataset_storage_directory
from src.background_jobs import JobCallbacks
from src.mart import (
    AccountMappingEntry,
    Dimension,
    ExportDefinition,
    MartEngine,
    MartModel,
    MartRecipe,
    Measure,
    WorkspaceStore,
)
from src.mart.capacity import ResourcePreflight, available_space, collect_capacity
from src.mart.engine import inspect_source_scopes
from src.mart.mapping import (
    export_mapping_file,
    import_mapping_file,
    load_mappings_json,
    suggest_mapping_entries,
)
from src.mart.model import model_from_dict
from src.mart.recipes import build_default_recipes, load_recipes

# Text is indexed by stable keys so all labels can be switched without losing form state.
_TEXT = {
    "ko": [
        "작업 공간",
        "새 작업",
        "이름",
        "1. 📋 원본 확인 및 모델 설계",
        "원본 CSV 선택",
        "원본 열 확인",
        "팩트 이름",
        "기간 열",
        "행 기준 열",
        "분할 열",
        "제외 열",
        "차원",
        "차원 이름",
        "업무 키",
        "속성",
        "기준시점 열 (선택)",
        "차원 추가",
        "지표",
        "지표 이름",
        "원본 열",
        "단위",
        "정밀도",
        "소수 자릿수",
        "종류",
        "지표 추가",
        "모델 저장",
        "2. ⚡ 데이터베이스 생성 및 적재",
        "범위 값",
        "선택한 파일이 이 범위의 완전한 구성임을 선언합니다",
        "누적 실행",
        "3. 🔍 DB 생성 결과 및 구조 검수",
        "새로 확인",
        "구조 / PK / FK",
        "표본 (최대 100행)",
        "원본과 DB 대사",
        "4. 📤 배포 및 활용 (목적 CSV · SharePoint · 용량)",
        "예산 B (GiB, 선택)",
        "예산 저장",
        "활성 DB + WAL",
        "전체 보존 자료",
        "CSV / Excel",
        "사용 가능 디스크",
        "목적 CSV",
        "출력 이름",
        "출력 열",
        "그룹 열",
        "필터 열",
        "필터 값",
        "기간 시작 (YYYY-MM 또는 YYYY-MM-DD)",
        "기간 끝",
        "후보 생성",
        "후보 미리보기",
        "검수자",
        "승인",
        "로컬 공개",
        "SharePoint",
        "사이트 주소",
        "고급 설정",
        "승인된 테넌트",
        "승인된 클라이언트 ID",
        "라이브러리",
        "폴더",
        "로그인",
        "취소 요청",
        "파일 이름",
        "승인된 후보 게시",
        "Excel 연결 안내 복사",
        "준비",
        "실행 중: 저장 확정과 취소는 별개입니다.",
        "완료",
        "실패",
        "선택",
        "삭제",
        "파일 목록",
        "완성 선언 및 구성 파일을 확인하세요.",
        "미설정 또는 확인 불가",
        "경고: 예산 80% 이상",
        "지원 범위: 차원 1–16, 업무키 8열, 행 기준 16열, 분할 2열, 속성 64개, 셀 1,000 UTF-8바이트. 최대 512열/512지표, 100만 행, 36범위, YYYYMM 기간 및 고정밀/지수 표기(정밀도 38) 지원. 추정은 보장이 아닙니다.",
        "상태",
        "리비전",
        "행 수",
        "출력은 DB 변경 후 재생성·재승인이 필요합니다.",
        "대상 파일은 최대 250 MiB입니다. 소비자의 Excel 새로고침은 알 수 없습니다.",
        "모델을 저장하고 원본을 선택하세요.",
        "다음",
        "이전",
        "확인된 결과 없음",
        "전체 빈 범위를 의도적으로 선언",
        "사용자 코드",
        "열",
        "형식",
        "제약",
        "범위",
        "원본 합계",
        "DB 합계",
        "일치",
        "모델",
        "취소",
    ],
    "en": [
        "Workspaces",
        "New workspace",
        "Name",
        "Source and model",
        "Choose source CSVs",
        "Inspect columns",
        "Fact name",
        "Period column",
        "Row grain",
        "Partitions",
        "Excluded columns",
        "Dimensions",
        "Dimension name",
        "Business keys",
        "Attributes",
        "Authority column (optional)",
        "Add dimension",
        "Measures",
        "Measure name",
        "Source column",
        "Unit",
        "Precision",
        "Scale",
        "Kind",
        "Add measure",
        "Save model",
        "Load scope",
        "Scope values",
        "I declare these files are the complete scope",
        "Load snapshot",
        "Database inspection",
        "Refresh",
        "Structure / PK / FK",
        "Sample (up to 100 rows)",
        "Source and database reconciliation",
        "4. 📤 Deploy & Publish (CSV · SharePoint · Capacity)",
        "Budget B (GiB, optional)",
        "Save budget",
        "Active DB + WAL",
        "All retained data",
        "CSV / Excel",
        "Available disk",
        "Purpose CSV",
        "Output name",
        "Output columns",
        "Group columns",
        "Filter column",
        "Filter value",
        "Period start (YYYY-MM or YYYY-MM-DD)",
        "Period end",
        "Create candidate",
        "Candidate preview",
        "Reviewer",
        "Approve",
        "Publish locally",
        "SharePoint",
        "Site URL",
        "Advanced settings",
        "Approved tenant",
        "Approved client ID",
        "Library",
        "Folder",
        "Sign in",
        "Request cancellation",
        "File name",
        "Publish approved candidate",
        "Copy Excel connection guide",
        "Ready",
        "Running: cancellation and commit are separate.",
        "Done",
        "Failed",
        "Select",
        "Remove",
        "Files",
        "Check completion declaration and contributing files.",
        "Not set or unknown",
        "Warning: budget at least 80%",
        "Supported profile: 1–16 dimensions, 8 business-key columns, 16 row keys, 2 partitions, 64 attributes, 1,000 UTF-8 bytes per cell. Up to 512 columns/512 measures, 1 million rows, 36 scopes, YYYYMM periods, and high-precision/scientific decimals (precision 38). Estimates are not guarantees.",
        "Status",
        "Revision",
        "Rows",
        "After a database change, recreate and approve the output again.",
        "Maximum SharePoint file: 250 MiB. Consumer Excel refresh is unknown.",
        "Save the model and select sources.",
        "Next",
        "Previous",
        "No verified result",
        "Declare intentionally empty scope",
        "User code",
        "Column",
        "Type",
        "Constraint",
        "Scope",
        "Source total",
        "DB total",
        "Match",
        "Model",
        "Cancel",
    ],
    "pl": [
        "Obszary pracy",
        "Nowy obszar",
        "Nazwa",
        "Źródło i model",
        "Wybierz pliki CSV",
        "Sprawdź kolumny",
        "Nazwa faktu",
        "Kolumna okresu",
        "Klucz wiersza",
        "Podziały",
        "Pominięte kolumny",
        "Wymiary",
        "Nazwa wymiaru",
        "Klucze biznesowe",
        "Atrybuty",
        "Kolumna daty (opcjonalna)",
        "Dodaj wymiar",
        "Miary",
        "Nazwa miary",
        "Kolumna źródła",
        "Jednostka",
        "Precyzja",
        "Skala",
        "Rodzaj",
        "Dodaj miarę",
        "Zapisz model",
        "Załaduj zakres",
        "Wartości zakresu",
        "Potwierdzam kompletność plików zakresu",
        "Załaduj dane",
        "Kontrola bazy",
        "Odśwież",
        "Struktura / PK / FK",
        "Próbka (do 100 wierszy)",
        "Uzgodnienie źródła i bazy",
        "4. 📤 Publikacja i Wdrożenie (CSV · SharePoint · Pamięć)",
        "Budżet B (GiB, opcjonalnie)",
        "Zapisz budżet",
        "Aktywna baza + WAL",
        "Wszystkie dane zachowane",
        "CSV / Excel",
        "Dostępne miejsce",
        "Plik CSV",
        "Nazwa wyniku",
        "Kolumny wyniku",
        "Kolumny grupowania",
        "Kolumna filtra",
        "Wartość filtra",
        "Początek okresu (RRRR-MM lub RRRR-MM-DD)",
        "Koniec okresu",
        "Utwórz wynik",
        "Podgląd wyniku",
        "Osoba sprawdzająca",
        "Zatwierdź",
        "Publikuj lokalnie",
        "SharePoint",
        "Adres witryny",
        "Ustawienia zaawansowane",
        "Zatwierdzony tenant",
        "Zatwierdzony identyfikator klienta",
        "Biblioteka",
        "Folder",
        "Zaloguj",
        "Anuluj operację",
        "Nazwa pliku",
        "Publikuj zatwierdzony wynik",
        "Kopiuj instrukcję Excel",
        "Gotowe",
        "Trwa praca: anulowanie i zapis to osobne etapy.",
        "Zakończono",
        "Błąd",
        "Wybierz",
        "Usuń",
        "Pliki",
        "Sprawdź deklarację kompletności i pliki.",
        "Nie ustawiono lub brak danych",
        "Ostrzeżenie: co najmniej 80% budżetu",
        "Obsługiwany profil: 1–16 wymiarów, 8 kolumn klucza, 16 kluczy wiersza, 2 podziały, 64 atrybuty, 1 000 bajtów UTF-8 na komórkę. Do 512 kolumn/512 miar, 1 mln wierszy, 36 zakresów, okresy RRRRMM oraz notacja naukowa/wysoka precyzja (precyzja 38). Szacunek nie jest gwarancją.",
        "Stan",
        "Wersja",
        "Wiersze",
        "Po zmianie bazy utwórz i zatwierdź wynik ponownie.",
        "Maksymalny plik SharePoint: 250 MiB. Odświeżenie Excel odbiorcy jest nieznane.",
        "Zapisz model i wybierz źródła.",
        "Dalej",
        "Wstecz",
        "Brak potwierdzonego wyniku",
        "Potwierdzam celowo pusty zakres",
        "Kod użytkownika",
        "Kolumna",
        "Typ",
        "Ograniczenie",
        "Zakres",
        "Suma źródła",
        "Suma bazy",
        "Zgodność",
        "Model",
        "Anuluj",
    ],
}


def _atomic_json(path, data):
    from src.atomic_write import atomic_write_json

    atomic_write_json(path, data)


_TEXT["ko"].extend(
    [
        "배포 설정 저장",
        "실제 관계도",
        "가산 지표의 합계만 지원합니다. 재고 잔액과 비율은 별도 기준기간 또는 측정값 설계가 필요합니다.",
        "저장소 용량 모니터링",
        "1. 🗄️ DB 구축 및 적재 (스키마·적재·검수)",
        "2. 🏷️ 계정·지표 매핑 (선택)",
        "3. 📦 데이터 마트·검증·배포",
    ]
)
_TEXT["en"].extend(
    [
        "Save publication settings",
        "Actual relationships",
        "Only additive sums are supported. Inventory balances and ratios require a separate period or measure design.",
        "Storage Capacity",
        "1. 🗄️ Build & Load DB (Schema · Load · Verify)",
        "2. 🏷️ Account Mapping (Optional)",
        "3. 📦 Data Mart & Publish",
    ]
)
_TEXT["pl"].extend(
    [
        "Zapisz ustawienia publikacji",
        "Rzeczywiste relacje",
        "Obsługiwane są tylko sumy addytywne. Salda zapasów i wskaźniki wymagają osobnego projektu okresu lub miary.",
        "Pojemność Pamięci",
        "1. 🗄️ Budowa i Ładowanie Bazy (Model · Dane · Kontrola)",
        "2. 🏷️ Mapowanie Kont (Opcjonalne)",
        "3. 📦 Mart Danych i Publikacja",
    ]
)


class MultiPick(QPushButton):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.options = []
        self.values = []
        self.caption = "Select"
        self.clicked.connect(self.pick)

    def set_options(self, values):
        self.options = list(dict.fromkeys(values))
        self.values = [v for v in self.values if v in self.options]
        self.update_text()

    def set_values(self, values):
        values = list(values)
        changed = values != self.values
        self.values = values
        self.update_text()
        if changed:
            self.changed.emit()

    def update_text(self):
        self.setText(", ".join(self.values) or self.caption)

    def pick(self):
        dialog = QDialog(self)
        layout = QVBoxLayout(dialog)
        items = QListWidget()
        layout.addWidget(items)
        for value in self.options:
            item = QListWidgetItem(value)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if value in self.values
                else Qt.CheckState.Unchecked
            )
            items.addItem(item)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec():
            self.set_values(
                [
                    items.item(i).text()
                    for i in range(items.count())
                    if items.item(i).checkState() == Qt.CheckState.Checked
                ]
            )


class MartWorkspace(QWidget):
    def __init__(self, language_code, job_runner, parent=None, root=None):
        super().__init__(parent)
        self.runner = job_runner
        self.root = (
            Path(root) if root else dataset_storage_directory() / "mart_workspaces"
        )
        self.current = None
        self.language_code = language_code
        self.files = []
        self.columns = []
        self.dims = []
        self.metrics = []
        self.model = None
        self.candidate = None
        self.publisher = None
        self.destination_settings = {}
        self._saved_mappings_cache = []
        self.cancel_event = threading.Event()
        self._busy = False
        self.warning = False
        self.budget_value = None
        self.last_revision = None
        self.offset = 0
        self.translations = []
        self._autofilled_scope = {}
        self._build()
        self.apply_language(language_code)
        self.refresh_list()

    def t(self, index):
        return _TEXT.get(self.language_code, _TEXT["en"])[index]

    def label(self, index):
        obj = QLabel()
        obj.setWordWrap(True)
        self.translations.append((obj, index))
        return obj

    def button(self, index, callback):
        obj = QPushButton()
        obj.clicked.connect(callback)
        self.translations.append((obj, index))
        return obj

    def group(self, parent, index):
        page = QWidget()
        lay = QVBoxLayout(page)
        parent.addTab(page, "")
        self.tab_labels.append((parent, parent.count() - 1, index))
        return lay

    def group_detached(self, index):
        page = QWidget(self)
        page.setVisible(False)
        lay = QVBoxLayout(page)
        return lay

    def formrow(self, form, index, widget):
        form.addRow(self.label(index), widget)

    def _build(self):
        outer = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.settings_warning = QLabel()
        self.settings_warning.setWordWrap(True)
        outer.addWidget(self.settings_warning)
        self.edit_area = QWidget()
        outer.addWidget(self.edit_area)
        base = QVBoxLayout(self.edit_area)
        row = QHBoxLayout()
        self.workspaces = QListWidget()
        self.workspaces.setMaximumHeight(85)
        self.workspaces.currentTextChanged.connect(self._select)
        row.addWidget(self.workspaces)
        side = QVBoxLayout()
        self.name = QLineEdit()
        side.addWidget(self.name)
        ws_btns = QHBoxLayout()
        self.create_button = self.button(1, self.create_workspace)
        self.clone_button = QPushButton("📋 설계도 복제 (새 별도 DB)")
        self.clone_button.clicked.connect(self.clone_workspace_blueprint)
        ws_btns.addWidget(self.create_button)
        ws_btns.addWidget(self.clone_button)
        side.addLayout(ws_btns)
        row.addLayout(side)
        base.addWidget(self.label(0))
        base.addLayout(row)
        self.workflow_guide_banner = QLabel()
        self.workflow_guide_banner.setWordWrap(True)
        self.workflow_guide_banner.setStyleSheet(
            "background-color: #f0f7ff; border: 1px solid #bfdbfe; border-radius: 4px; padding: 6px 10px; color: #1e3a8a;"
        )
        base.addWidget(self.workflow_guide_banner)
        self.tabs = QTabWidget()
        base.addWidget(self.tabs)
        self.tab_labels = []

        # Stage 1: DB Build, Load & Verify (db_tabs inside Stage 1)
        stage1_page = QWidget()
        stage1_lay = QVBoxLayout(stage1_page)
        self.tabs.addTab(stage1_page, "")
        self.tab_labels.append((self.tabs, 0, 102))
        self.db_tabs = QTabWidget()
        stage1_lay.addWidget(self.db_tabs)

        model_lay = self.group(self.db_tabs, 3)
        source_row = QHBoxLayout()
        self.source_button = self.button(4, self.choose_source)
        self.sample_button = self.button(5, self.inspect_source)
        self.wizard_button = QPushButton("✨ 스타 스키마 설계 마법사 (Wizard)")
        self.wizard_button.setStyleSheet(
            "font-weight: bold; background-color: #2563eb; color: white; padding: 4px 14px; border-radius: 4px;"
        )
        self.wizard_button.clicked.connect(self.open_model_wizard)
        source_row.addWidget(self.source_button)
        source_row.addWidget(self.sample_button)
        source_row.addWidget(self.wizard_button)
        model_lay.addLayout(source_row)
        self.source = QLineEdit()
        self.source.setReadOnly(True)
        model_lay.addWidget(self.source)
        self.source_preview = QTableWidget()
        self.source_preview.setMaximumHeight(130)
        model_lay.addWidget(self.source_preview)
        form = QFormLayout()
        model_lay.addLayout(form)
        self.fact = QLineEdit()
        self.period = QComboBox()
        self.row_keys = MultiPick()
        self.partitions = MultiPick()
        self.excluded = MultiPick()
        self.duplicate_policy_label = QLabel("중복 행 처리")
        self.duplicate_policy = QComboBox()
        self.duplicate_policy.addItem("중복 행 발견 시 차단 (기본)", "reject")
        self.duplicate_policy.addItem("동일 기준 수치 자동 합산 (Group & Sum)", "sum")
        for idx, w in [
            (6, self.fact),
            (7, self.period),
            (8, self.row_keys),
            (9, self.partitions),
            (10, self.excluded),
        ]:
            self.formrow(form, idx, w)
        form.addRow(self.duplicate_policy_label, self.duplicate_policy)
        self.dim_list = QListWidget()
        self.dim_list.setMaximumHeight(80)
        self.dim_list.currentRowChanged.connect(self._on_dimension_selected)
        model_lay.addWidget(self.label(11))
        model_lay.addWidget(self.dim_list)
        form = QFormLayout()
        model_lay.addLayout(form)
        self.dim_name = QLineEdit()
        self.dim_keys = MultiPick()
        self.dim_attrs = MultiPick()
        self.dim_authority = QComboBox()
        for idx, w in [
            (12, self.dim_name),
            (13, self.dim_keys),
            (14, self.dim_attrs),
            (15, self.dim_authority),
        ]:
            self.formrow(form, idx, w)
        row = QHBoxLayout()
        row.addWidget(self.button(16, self.add_dimension))
        row.addWidget(self.button(72, self.remove_dimension))
        model_lay.addLayout(row)
        self.metric_list = QListWidget()
        self.metric_list.setMaximumHeight(80)
        self.metric_list.currentRowChanged.connect(self._on_metric_selected)
        model_lay.addWidget(self.label(17))
        model_lay.addWidget(self.label(100))
        model_lay.addWidget(self.metric_list)
        form = QFormLayout()
        model_lay.addLayout(form)
        self.metric_name = QLineEdit()
        self.metric_col = QComboBox()
        self.unit = QLineEdit()
        self.precision = QSpinBox()
        self.precision.setRange(1, 38)
        self.precision.setValue(18)
        self.scale = QSpinBox()
        self.scale.setRange(0, 38)
        self.scale.setValue(2)
        self.kind = QComboBox()
        self.kind.addItems(["value", "cost", "quantity"])
        for idx, w in [
            (18, self.metric_name),
            (19, self.metric_col),
            (20, self.unit),
            (21, self.precision),
            (22, self.scale),
            (23, self.kind),
        ]:
            self.formrow(form, idx, w)
        row = QHBoxLayout()
        row.addWidget(self.button(24, self.add_metric))
        row.addWidget(self.button(72, self.remove_metric))
        row.addWidget(self.button(25, self.save_model))
        model_lay.addLayout(row)
        load = self.group(self.db_tabs, 26)
        self.load_guide_label = QLabel()
        self.load_guide_label.setWordWrap(True)
        self.load_guide_label.setStyleSheet(
            "background-color: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 6px 10px;"
        )
        load.addWidget(self.load_guide_label)
        self.scope_form = QFormLayout()
        load.addLayout(self.scope_form)
        self.scope_inputs = {}
        self.files_label = QLabel()
        self.files_label.setWordWrap(True)
        load.addWidget(self.label(73))
        load.addWidget(self.files_label)
        self.complete = QCheckBox()
        self.translations.append((self.complete, 28))
        load.addWidget(self.complete)
        self.empty = QCheckBox()
        self.translations.append((self.empty, 87))
        load.addWidget(self.empty)
        self.load_button = self.button(29, self.load_snapshot)
        load.addWidget(self.load_button)
        self.load_result = QLabel()
        load.addWidget(self.load_result)
        load.addWidget(self.label(83))
        inspect = self.group(self.db_tabs, 30)
        inspect.addWidget(self.button(31, self.inspect_database))
        self.home_status = QLabel()
        inspect.addWidget(self.home_status)
        self.home = QTableWidget()
        inspect.addWidget(self.home)
        inspect.addWidget(self.label(32))
        inspect.addWidget(self.label(99))
        self.relation_scene = QGraphicsScene(self)
        self.relation_view = QGraphicsView(self.relation_scene)
        self.relation_view.setMinimumHeight(190)
        inspect.addWidget(self.relation_view)
        self.structure = QTableWidget()
        inspect.addWidget(self.structure)
        inspect.addWidget(self.label(33))
        self.sample = QTableWidget()
        inspect.addWidget(self.sample)
        row = QHBoxLayout()
        self.previous_button = self.button(85, self.prev_page)
        self.next_button = self.button(84, self.next_page)
        row.addWidget(self.previous_button)
        row.addWidget(self.next_button)
        inspect.addLayout(row)
        inspect.addWidget(self.label(34))
        self.reconciliation = QTableWidget()
        inspect.addWidget(self.reconciliation)

        # Stage 2: Account & Measure Mapping Studio (Optional)
        mapping_lay = self.group(self.tabs, 103)
        self.mapping_optional_banner = QLabel()
        self.mapping_optional_banner.setWordWrap(True)
        self.mapping_optional_banner.setStyleSheet(
            "background-color: #fffbeb; border: 1px solid #fde68a; border-radius: 4px; padding: 6px 10px; color: #92400e;"
        )
        mapping_lay.addWidget(self.mapping_optional_banner)
        self.mapping_status_label = QLabel()
        self.mapping_status_label.setWordWrap(True)
        mapping_lay.addWidget(self.mapping_status_label)
        map_btns = QHBoxLayout()
        self.mapping_suggest_button = QPushButton("✨ 키워드 자동 분류")
        self.mapping_suggest_button.clicked.connect(self.auto_suggest_mapping)
        self.mapping_save_button = QPushButton("💾 DB에 매핑 저장")
        self.mapping_save_button.clicked.connect(self.save_mapping_from_ui)
        self.mapping_export_button = QPushButton("📤 Excel/CSV 내보내기")
        self.mapping_export_button.clicked.connect(lambda: self.export_mapping_dialog())
        self.mapping_import_button = QPushButton("📥 Excel/CSV 가져오기")
        self.mapping_import_button.clicked.connect(lambda: self.import_mapping_dialog())
        self.mapping_copy_ws_button = QPushButton("🔗 다른 워크스페이스 매핑 가져오기")
        self.mapping_copy_ws_button.clicked.connect(lambda: self.import_mapping_from_other_workspace())
        for btn in (
            self.mapping_suggest_button,
            self.mapping_save_button,
            self.mapping_export_button,
            self.mapping_import_button,
            self.mapping_copy_ws_button,
        ):
            map_btns.addWidget(btn)
        mapping_lay.addLayout(map_btns)

        bulk_row = QHBoxLayout()
        self.bulk_type_combo = QComboBox()
        self.bulk_type_combo.addItem("유형 변경 안함", "")
        self.bulk_type_combo.addItem("amount (세부금액)", "amount")
        self.bulk_type_combo.addItem("non_monetary (비금액/수량)", "non_monetary")
        self.bulk_type_combo.addItem("subtotal_excluded (원본소계 제외)", "subtotal_excluded")
        self.bulk_major_edit = QLineEdit()
        self.bulk_major_edit.setPlaceholderText("대분류 일괄 입력")
        self.bulk_medium_edit = QLineEdit()
        self.bulk_medium_edit.setPlaceholderText("중분류 일괄 입력")
        self.bulk_sign_combo = QComboBox()
        self.bulk_sign_combo.addItem("부호 변경 안함", None)
        self.bulk_sign_combo.addItem("+1", 1)
        self.bulk_sign_combo.addItem("-1", -1)
        self.bulk_apply_button = QPushButton("선택 행 일괄 적용")
        self.bulk_apply_button.clicked.connect(self.apply_bulk_mapping)
        for w in (
            self.bulk_type_combo,
            self.bulk_major_edit,
            self.bulk_medium_edit,
            self.bulk_sign_combo,
            self.bulk_apply_button,
        ):
            bulk_row.addWidget(w)
        mapping_lay.addLayout(bulk_row)

        self.mapping_table = QTableWidget()
        self.mapping_table.setMinimumHeight(280)
        self.mapping_table.setColumnCount(9)
        self.mapping_table.setHorizontalHeaderLabels(self._mapping_headers())
        mapping_lay.addWidget(self.mapping_table)

        # Stage 3: Data Mart Recipe, Verification & Deployment
        deploy = self.group(self.tabs, 104)
        self.deploy_tabs = QTabWidget()
        deploy.addWidget(self.deploy_tabs)

        ex = self.group(self.deploy_tabs, 42)
        self.recipe_box = QGroupBox("데이터 마트 레시피 설계 · 실행 · 대사 검증")
        recipe_lay = QVBoxLayout(self.recipe_box)
        preset_row = QHBoxLayout()
        self.preset_wide_rollup_btn = QPushButton("📊 프리셋: 대분류 손익 요약 (Wide)")
        self.preset_wide_rollup_btn.clicked.connect(lambda: self.apply_recipe_preset("wide_rollup"))
        self.preset_long_detail_btn = QPushButton("🔎 프리셋: 원가 상세 계정전개 (Long, 0원 제외)")
        self.preset_long_detail_btn.clicked.connect(lambda: self.apply_recipe_preset("long_detail"))
        self.preset_wide_raw_btn = QPushButton("📄 프리셋: 원본 컬럼 직접 선택")
        self.preset_wide_raw_btn.clicked.connect(lambda: self.apply_recipe_preset("wide_raw"))
        for btn in (
            self.preset_wide_rollup_btn,
            self.preset_long_detail_btn,
            self.preset_wide_raw_btn,
        ):
            preset_row.addWidget(btn)
        recipe_lay.addLayout(preset_row)

        r_form = QFormLayout()
        self.recipe_combo = QComboBox()
        self.recipe_combo.currentIndexChanged.connect(self._on_recipe_selected)
        self.recipe_mode_combo = QComboBox()
        self.recipe_mode_combo.addItem("원본 컬럼 직접 추출 (wide_raw)", "wide_raw")
        self.recipe_mode_combo.addItem("대분류·중분류 요약 (wide_rollup)", "wide_rollup")
        self.recipe_mode_combo.addItem("계정 상세 펼침 (long_detail)", "long_detail")
        self.rollup_level_combo = QComboBox()
        self.rollup_level_combo.addItem("대분류별 (major)", "major")
        self.rollup_level_combo.addItem("중분류별 (medium)", "medium")
        self.major_filter_edit = QLineEdit()
        self.major_filter_edit.setPlaceholderText("예: 매출원가, 판관비 (비우면 전체 금액 계정)")
        self.unmapped_policy_combo = QComboBox()
        self.unmapped_policy_combo.addItem("미매핑 계정 있으면 차단 (권장)", "fail")
        self.unmapped_policy_combo.addItem("미분류 버킷으로 포함", "other_bucket")
        self.recipe_saved_label = QLabel("저장된 레시피")
        self.recipe_mode_label = QLabel("마트 출력 형태")
        self.rollup_level_label = QLabel("요약 집계 단계")
        self.major_filter_label = QLabel("대상 대분류 필터")
        self.unmapped_policy_label = QLabel("미매핑 처리 정책")
        r_form.addRow(self.recipe_saved_label, self.recipe_combo)
        r_form.addRow(self.recipe_mode_label, self.recipe_mode_combo)
        r_form.addRow(self.rollup_level_label, self.rollup_level_combo)
        r_form.addRow(self.major_filter_label, self.major_filter_edit)
        r_form.addRow(self.unmapped_policy_label, self.unmapped_policy_combo)
        recipe_lay.addLayout(r_form)

        r_checks = QHBoxLayout()
        self.exclude_zero_check = QCheckBox("0원 행 제외 (Long 상세)")
        self.exclude_zero_check.setChecked(True)
        self.apply_sign_check = QCheckBox("매핑 부호(+1/-1) 반영")
        r_checks.addWidget(self.exclude_zero_check)
        r_checks.addWidget(self.apply_sign_check)
        recipe_lay.addLayout(r_checks)

        r_actions = QHBoxLayout()
        self.save_recipe_button = QPushButton("💾 레시피 저장")
        self.save_recipe_button.clicked.connect(self.save_recipe_from_ui)
        self.run_recipe_button = QPushButton("🚀 레시피 마트 생성 & 대사 검증")
        self.run_recipe_button.clicked.connect(self.run_recipe_export)
        self.run_all_recipes_button = QPushButton("⚡ 저장된 레시피 전체 일괄 생성")
        self.run_all_recipes_button.clicked.connect(self.run_all_saved_recipes)
        for btn in (
            self.save_recipe_button,
            self.run_recipe_button,
            self.run_all_recipes_button,
        ):
            r_actions.addWidget(btn)
        recipe_lay.addLayout(r_actions)
        ex.addWidget(self.recipe_box)

        self.candidates = QComboBox()
        self.candidates.currentIndexChanged.connect(self.select_candidate)
        ex.addWidget(self.candidates)
        form = QFormLayout()
        ex.addLayout(form)
        self.export_name = QLineEdit()
        self.export_cols = MultiPick()
        self.group_cols = MultiPick()
        self.filter_col = QComboBox()
        self.filter_value = QLineEdit()
        self.filter_col2 = QComboBox()
        self.filter_value2 = QLineEdit()
        self.start = QLineEdit()
        self.end = QLineEdit()
        self.reviewer = QLineEdit()
        for idx, w in [
            (43, self.export_name),
            (44, self.export_cols),
            (45, self.group_cols),
            (46, self.filter_col),
            (47, self.filter_value),
            (48, self.start),
            (49, self.end),
        ]:
            self.formrow(form, idx, w)
        self.formrow(form, 46, self.filter_col2)
        self.formrow(form, 47, self.filter_value2)
        ex.addWidget(self.button(50, self.create_export))
        ex.addWidget(self.label(51))
        self.export_preview = QTableWidget()
        ex.addWidget(self.export_preview)
        self.recipe_reconcile_summary = QLabel()
        self.recipe_reconcile_summary.setWordWrap(True)
        ex.addWidget(self.recipe_reconcile_summary)
        self.recipe_reconcile_table = QTableWidget()
        self.recipe_reconcile_table.setMaximumHeight(170)
        ex.addWidget(self.recipe_reconcile_table)
        self.formrow(form, 52, self.reviewer)
        row = QHBoxLayout()
        row.addWidget(self.button(53, self.approve))
        row.addWidget(self.button(54, self.publish_local))
        ex.addLayout(row)
        ex.addWidget(self.label(81))

        sp = self.group(self.deploy_tabs, 55)
        form = QFormLayout()
        sp.addLayout(form)
        self.site = QLineEdit()
        self.tenant = QLineEdit()
        self.client = QLineEdit()
        self.library = QLineEdit()
        self.folder = QLineEdit()
        self.filename = QLineEdit()
        self.formrow(form, 56, self.site)
        advanced_button = QPushButton()
        advanced_button.setCheckable(True)
        self.translations.append((advanced_button, 57))
        sp.addWidget(advanced_button)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced.setVisible(False)
        advanced_button.toggled.connect(advanced.setVisible)
        sp.addWidget(advanced)
        for idx, w in [
            (58, self.tenant),
            (59, self.client),
            (60, self.library),
            (61, self.folder),
        ]:
            self.formrow(advanced_form, idx, w)
        filename_form = QFormLayout()
        self.formrow(filename_form, 64, self.filename)
        sp.addLayout(filename_form)
        row = QHBoxLayout()
        row.addWidget(self.button(62, self.login))
        row.addWidget(self.button(98, self.save_profile))
        row.addWidget(self.button(65, self.publish_remote))
        row.addWidget(self.button(66, self.copy_excel))
        sp.addLayout(row)
        sp.addWidget(self.label(82))
        self.login_info = QLabel()
        self.login_info.setWordWrap(True)
        sp.addWidget(self.login_info)

        cap = self.group(self.deploy_tabs, 101)
        form = QFormLayout()
        cap.addLayout(form)
        self.budget = QLineEdit()
        self.formrow(form, 36, self.budget)
        cap.addWidget(self.button(37, self.save_budget))
        self.cap_labels = []
        for idx in (38, 39, 40, 41):
            row = QHBoxLayout()
            row.addWidget(self.label(idx))
            value = QLabel()
            row.addWidget(value)
            cap.addLayout(row)
            self.cap_labels.append(value)
        cap.addWidget(self.label(77))
        self.profile_detail = QLabel()
        self.profile_detail.setWordWrap(True)
        cap.addWidget(self.profile_detail)
        cap.addStretch()
        self.cancel = self.button(63, self.cancel_event.set)
        outer.addWidget(self.cancel)
        self.cancel.setEnabled(False)
        for widget in (self.fact, self.dim_name, self.metric_name, self.unit):
            widget.textChanged.connect(self._reset_declarations)
        for widget in (self.period, self.duplicate_policy, self.dim_authority, self.metric_col, self.kind):
            widget.currentIndexChanged.connect(self._reset_declarations)
        for widget in (self.precision, self.scale):
            widget.valueChanged.connect(self._reset_declarations)
        for widget in (
            self.row_keys,
            self.partitions,
            self.excluded,
            self.dim_keys,
            self.dim_attrs,
        ):
            widget.changed.connect(self._reset_declarations)
        def wrap_tab_scroll(tab_widget, index):
            page = tab_widget.widget(index)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            tab_widget.removeTab(index)
            scroll.setWidget(page)
            tab_widget.insertTab(index, scroll, "")

        for index in range(self.db_tabs.count()):
            wrap_tab_scroll(self.db_tabs, index)
        for index in range(1, self.tabs.count()):
            wrap_tab_scroll(self.tabs, index)

    def apply_language(self, lang):
        self.language_code = lang if lang in _TEXT else "en"
        if self.structure.columnCount() == 3:
            self.structure.setHorizontalHeaderLabels(self._structure_headers())
        if hasattr(self, "mapping_table") and self.mapping_table.columnCount() == 9:
            self.mapping_table.setHorizontalHeaderLabels(self._mapping_headers())
        if hasattr(self, "recipe_reconcile_table"):
            self.recipe_reconcile_table.setHorizontalHeaderLabels(self._reconcile_headers())
        for obj, index in self.translations:
            obj.setText(self.t(index))
        for tabs, index, key in self.tab_labels:
            tabs.setTabText(index, self.t(key))
        lang = self.language_code
        if hasattr(self, "clone_button"):
            self.clone_button.setText(
                {
                    "ko": "📋 설계도 복제 (새 별도 DB)",
                    "en": "📋 Clone Blueprint (New DB)",
                    "pl": "📋 Klonuj Szablon (Nowa Baza)",
                }[lang]
            )
        if hasattr(self, "load_guide_label"):
            self.load_guide_label.setText(
                {
                    "ko": "💡 같은 양식의 다음 월 데이터를 기존 DB에 누적하려면 새 월 파일만 선택해 적재하세요. 별도 DB로 분리하려면 상단 '설계도 복제'를 사용하세요.",
                    "en": "💡 To append a new month to the current DB, select the new file(s) below. To create a separate DB with the same layout, use 'Clone Blueprint' above.",
                    "pl": "💡 Aby dopisać kolejny miesiąc do bieżącej bazy, wskaż nowe pliki poniżej. Aby utworzyć osobną bazę z tym samym układem, użyj 'Klonuj Szablon'.",
                }[lang]
            )
        if hasattr(self, "mapping_optional_banner"):
            self.mapping_optional_banner.setText(
                {
                    "ko": "ℹ️ [선택 단계] 원본 컬럼 그대로 추출하려면 3단계로 바로 이동하세요. 대분류 손익 요약이나 0원 제외 원가 상세 마트가 필요할 때 여기서 계정을 한 번 분류해 두면 DB에 영구 저장됩니다.",
                    "en": "ℹ️ [Optional] Skip to Step 3 for raw-column exports. Configure classifications here once to enable category rollups and zero-filtered cost-detail marts.",
                    "pl": "ℹ️ [Opcjonalne] Przejdź do kroku 3 dla surowego eksportu. Tutaj jednorazowo sklasyfikujesz konta dla podsumowań i szczegółów kosztów bez zer.",
                }[lang]
            )
        if hasattr(self, "mapping_suggest_button"):
            self.mapping_suggest_button.setText(
                {
                    "ko": "✨ 키워드 자동 분류",
                    "en": "✨ Auto-Classify by Keywords",
                    "pl": "✨ Auto-klasyfikacja",
                }[lang]
            )
            self.mapping_save_button.setText(
                {
                    "ko": "💾 DB에 매핑 저장",
                    "en": "💾 Save Mapping to DB",
                    "pl": "💾 Zapisz mapowanie do DB",
                }[lang]
            )
            self.mapping_export_button.setText(
                {
                    "ko": "📤 Excel/CSV 내보내기",
                    "en": "📤 Export Excel/CSV",
                    "pl": "📤 Eksportuj Excel/CSV",
                }[lang]
            )
            self.mapping_import_button.setText(
                {
                    "ko": "📥 Excel/CSV 가져오기",
                    "en": "📥 Import Excel/CSV",
                    "pl": "📥 Importuj Excel/CSV",
                }[lang]
            )
            self.mapping_copy_ws_button.setText(
                {
                    "ko": "🔗 다른 워크스페이스 매핑 가져오기",
                    "en": "🔗 Copy Mapping from Workspace",
                    "pl": "🔗 Kopiuj z innego obszaru",
                }[lang]
            )
            self.bulk_major_edit.setPlaceholderText(
                {
                    "ko": "대분류 일괄 입력",
                    "en": "Bulk major category",
                    "pl": "Kategoria główna (grupowo)",
                }[lang]
            )
            self.bulk_medium_edit.setPlaceholderText(
                {
                    "ko": "중분류 일괄 입력",
                    "en": "Bulk medium category",
                    "pl": "Podkategoria (grupowo)",
                }[lang]
            )
            self.bulk_apply_button.setText(
                {
                    "ko": "선택 행 일괄 적용",
                    "en": "Apply to Selected Rows",
                    "pl": "Zastosuj do zaznaczonych",
                }[lang]
            )
            bulk_types = {
                "ko": (
                    "유형 변경 안함",
                    "amount (세부금액)",
                    "non_monetary (비금액/수량)",
                    "subtotal_excluded (원본소계 제외)",
                ),
                "en": (
                    "Keep measure type",
                    "amount (Monetary measure)",
                    "non_monetary (Quantity / non-monetary)",
                    "subtotal_excluded (Exclude subtotal)",
                ),
                "pl": (
                    "Bez zmiany typu",
                    "amount (Kwota szczegółowa)",
                    "non_monetary (Ilość / niematerialne)",
                    "subtotal_excluded (Wyklucz sumę częściową)",
                ),
            }[lang]
            self.bulk_type_combo.blockSignals(True)
            for idx, txt in enumerate(bulk_types):
                if idx < self.bulk_type_combo.count():
                    self.bulk_type_combo.setItemText(idx, txt)
            self.bulk_type_combo.blockSignals(False)
            self.bulk_sign_combo.blockSignals(True)
            if self.bulk_sign_combo.count() > 0:
                self.bulk_sign_combo.setItemText(
                    0,
                    {
                        "ko": "부호 변경 안함",
                        "en": "Keep sign",
                        "pl": "Bez zmiany znaku",
                    }[lang],
                )
            self.bulk_sign_combo.blockSignals(False)
            self._update_mapping_status_label()
        if hasattr(self, "recipe_box"):
            self.recipe_box.setTitle(
                {
                    "ko": "데이터 마트 레시피 설계 · 실행 · 대사 검증",
                    "en": "Data Mart Recipe Design, Execution & Reconciliation",
                    "pl": "Projektowanie, Uruchamianie i Uzgadnianie Martów",
                }[lang]
            )
            self.preset_wide_rollup_btn.setText(
                {
                    "ko": "📊 프리셋: 대분류 손익 요약 (Wide)",
                    "en": "📊 Preset: Major Rollup Summary (Wide)",
                    "pl": "📊 Szablon: Podsumowanie wg kategorii (Wide)",
                }[lang]
            )
            self.preset_long_detail_btn.setText(
                {
                    "ko": "🔎 프리셋: 원가 상세 계정전개 (Long, 0원 제외)",
                    "en": "🔎 Preset: Cost Account Detail (Long, No Zero)",
                    "pl": "🔎 Szablon: Szczegóły kont (Long, bez zer)",
                }[lang]
            )
            self.preset_wide_raw_btn.setText(
                {
                    "ko": "📄 프리셋: 원본 컬럼 직접 선택",
                    "en": "📄 Preset: Raw Columns Select",
                    "pl": "📄 Szablon: Wybór kolumn źródłowych",
                }[lang]
            )
            self.recipe_saved_label.setText(
                {"ko": "저장된 레시피", "en": "Saved recipe", "pl": "Zapisany przepis"}[lang]
            )
            self.recipe_mode_label.setText(
                {"ko": "마트 출력 형태", "en": "Mart output mode", "pl": "Tryb wyjścia"}[lang]
            )
            self.rollup_level_label.setText(
                {"ko": "요약 집계 단계", "en": "Rollup level", "pl": "Poziom agregacji"}[lang]
            )
            self.major_filter_label.setText(
                {"ko": "대상 대분류 필터", "en": "Major category filter", "pl": "Filtr kategorii głównej"}[lang]
            )
            self.unmapped_policy_label.setText(
                {"ko": "미매핑 처리 정책", "en": "Unmapped measure policy", "pl": "Polityka niemapowanych"}[lang]
            )
            self.major_filter_edit.setPlaceholderText(
                {
                    "ko": "예: 매출원가, 판관비 (비우면 전체 금액 계정)",
                    "en": "e.g. COGS, SG&A (blank = all amount measures)",
                    "pl": "np. COGS, SG&A (puste = wszystkie kwoty)",
                }[lang]
            )
            self.recipe_mode_combo.blockSignals(True)
            for idx, txt in enumerate(
                {
                    "ko": (
                        "원본 컬럼 직접 추출 (wide_raw)",
                        "대분류·중분류 요약 (wide_rollup)",
                        "계정 상세 펼침 (long_detail)",
                    ),
                    "en": (
                        "Raw Columns Export (wide_raw)",
                        "Category Rollup Summary (wide_rollup)",
                        "Account Detail Unpivot (long_detail)",
                    ),
                    "pl": (
                        "Surowy eksport kolumn (wide_raw)",
                        "Podsumowanie kategorii (wide_rollup)",
                        "Szczegóły kont (long_detail)",
                    ),
                }[lang]
            ):
                if idx < self.recipe_mode_combo.count():
                    self.recipe_mode_combo.setItemText(idx, txt)
            self.recipe_mode_combo.blockSignals(False)
            self.rollup_level_combo.blockSignals(True)
            for idx, txt in enumerate(
                {
                    "ko": ("대분류별 (major)", "중분류별 (medium)"),
                    "en": ("By Major Category (major)", "By Medium Category (medium)"),
                    "pl": ("Wg kategorii głównej (major)", "Wg podkategorii (medium)"),
                }[lang]
            ):
                if idx < self.rollup_level_combo.count():
                    self.rollup_level_combo.setItemText(idx, txt)
            self.rollup_level_combo.blockSignals(False)
            self.unmapped_policy_combo.blockSignals(True)
            for idx, txt in enumerate(
                {
                    "ko": ("미매핑 계정 있으면 차단 (권장)", "미분류 버킷으로 포함"),
                    "en": ("Block if unmapped measures exist (Recommended)", "Include in Unclassified bucket"),
                    "pl": ("Blokuj przy niemapowanych kontach (Zalecane)", "Dołącz jako niesklasyfikowane"),
                }[lang]
            ):
                if idx < self.unmapped_policy_combo.count():
                    self.unmapped_policy_combo.setItemText(idx, txt)
            self.unmapped_policy_combo.blockSignals(False)
            self.recipe_combo.blockSignals(True)
            if self.recipe_combo.count() > 0:
                self.recipe_combo.setItemText(0, self._recipe_placeholder())
            self.recipe_combo.blockSignals(False)
            self.exclude_zero_check.setText(
                {
                    "ko": "0원 행 제외 (Long 상세)",
                    "en": "Exclude zero rows (Long detail)",
                    "pl": "Wyklucz wiersze zerowe (Long)",
                }[lang]
            )
            self.apply_sign_check.setText(
                {
                    "ko": "매핑 부호(+1/-1) 반영",
                    "en": "Apply mapping sign (+1/-1)",
                    "pl": "Zastosuj znak mapowania (+1/-1)",
                }[lang]
            )
            self.save_recipe_button.setText(
                {"ko": "💾 레시피 저장", "en": "💾 Save Recipe", "pl": "💾 Zapisz przepis"}[lang]
            )
            self.run_recipe_button.setText(
                {
                    "ko": "🚀 레시피 마트 생성 & 대사 검증",
                    "en": "🚀 Build Mart & Reconcile",
                    "pl": "🚀 Generuj mart i uzgodnij",
                }[lang]
            )
            self.run_all_recipes_button.setText(
                {
                    "ko": "⚡ 저장된 레시피 전체 일괄 생성",
                    "en": "⚡ Run All Saved Recipes",
                    "pl": "⚡ Uruchom wszystkie przepisy",
                }[lang]
            )
        self._update_workflow_guide()
        if hasattr(self, "wizard_button"):
            self.wizard_button.setText(
                {
                    "ko": "✨ 스타 스키마 설계 마법사 (Wizard)",
                    "en": "✨ Star Schema Modeling Wizard",
                    "pl": "✨ Kreator Modelowania Gwiazdy",
                }.get(self.language_code, "✨ Star Schema Modeling Wizard")
            )
        if hasattr(self, "duplicate_policy_label") and hasattr(self, "duplicate_policy"):
            self.duplicate_policy_label.setText(
                {
                    "ko": "중복 행 처리",
                    "en": "Duplicate grain policy",
                    "pl": "Polityka duplikatów",
                }[self.language_code]
            )
            reject_txt, sum_txt = {
                "ko": ("중복 행 발견 시 차단 (기본)", "동일 기준 수치 자동 합산 (Group & Sum)"),
                "en": ("Reject on duplicate grain (Default)", "Auto Group & Sum duplicate rows"),
                "pl": ("Odrzuć duplikaty (Domyślnie)", "Automatycznie sumuj duplikaty (Group & Sum)"),
            }[self.language_code]
            self.duplicate_policy.blockSignals(True)
            self.duplicate_policy.setItemText(0, reject_txt)
            self.duplicate_policy.setItemText(1, sum_txt)
            self.duplicate_policy.blockSignals(False)
        self.profile_detail.setText(
            {
                "ko": "범위당 최대 100만 행, 최대 512열/512지표, 16개 차원(차원당 고유 키 10만 개), YYYYMM 기간 및 고정밀/지수 표기(정밀도 38 이하), 기존 DB 512 MiB 이하. 그룹 열 16개와 등가 필터 8개까지 지원합니다.",
                "en": "Up to 1,000,000 rows per scope, up to 512 columns/512 measures, 16 dimensions (100,000 unique keys per dimension), YYYYMM periods and lossless scientific/high-precision decimals (precision up to 38), and a 512 MiB existing DB. Up to 16 grouping columns and 8 equality filters.",
                "pl": "Do 1 000 000 wierszy na zakres, do 512 kolumn/512 miar, 16 wymiarów (100 000 unikalnych kluczy na wymiar), okresy RRRRMM i bezstratna notacja naukowa/wysoka precyzja (do 38) oraz istniejąca baza do 512 MiB. Do 16 kolumn grupowania i 8 filtrów równości.",
            }[self.language_code]
        )
        for picker in (
            self.row_keys,
            self.partitions,
            self.excluded,
            self.dim_keys,
            self.dim_attrs,
            self.export_cols,
            self.group_cols,
        ):
            picker.caption = self.t(71)
            picker.update_text()
        self.name.setPlaceholderText(self.t(2))
        self.status.setText(self.t(67))
        if self.current and self.last_revision is not None:
            self.refresh_candidates(self.last_revision)

    def refresh_list(self):
        current = self.current.name if self.current else None
        self.workspaces.blockSignals(True)
        self.workspaces.clear()
        if self.root.exists():
            self.workspaces.addItems(
                sorted(
                    p.name
                    for p in self.root.iterdir()
                    if p.is_dir() and (p / "workspace.json").is_file()
                )
            )
        if current:
            for i in range(self.workspaces.count()):
                if self.workspaces.item(i).text() == current:
                    self.workspaces.setCurrentRow(i)
                    break
        self.workspaces.blockSignals(False)

    def _select(self, name):
        self._reset_declarations()
        self.settings_warning.clear()
        self.current = self.root / name if name else None
        self.model = None
        self.candidate = None
        self.dims = []
        self.metrics = []
        self.files = []
        self.source.clear()
        self.files_label.clear()
        self.budget.clear()
        self.warning = False
        self.budget_value = None
        self.last_revision = None
        self.publisher = None
        self.destination_settings = {}
        self._saved_mappings_cache = []
        if hasattr(self, "mapping_table"):
            self.mapping_table.setRowCount(0)
        if hasattr(self, "mapping_status_label"):
            self.mapping_status_label.clear()
        if hasattr(self, "recipe_combo"):
            self.recipe_combo.blockSignals(True)
            self.recipe_combo.clear()
            self.recipe_combo.blockSignals(False)
        if hasattr(self, "recipe_reconcile_summary"):
            self.recipe_reconcile_summary.clear()
        if hasattr(self, "recipe_reconcile_table"):
            self.recipe_reconcile_table.setRowCount(0)
        for field in (
            self.site,
            self.tenant,
            self.client,
            self.library,
            self.folder,
            self.filename,
        ):
            field.clear()
        if not self.current:
            self._update_workflow_guide()
            return
        try:
            path = self.current / "model.json"
            if path.exists():
                self.model = model_from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                self.restore_model()
            self.refresh_mapping_table()
            self._populate_recipe_combo()
            prefs = self.current / "ui.json"
            if prefs.exists():
                try:
                    settings = self._validate_ui_settings(
                        json.loads(prefs.read_text(encoding="utf-8"))
                    )
                except (OSError, ValueError, TypeError, OverflowError):
                    self._settings_save_warning()
                    settings = {}
                self.budget_value = settings.get("budget_gib") or None
                self.warning = bool(settings.get("warning_active", False))
                self.budget.setText(str(self.budget_value or ""))
                profile = settings.get("sharepoint", {})
                for key, field in (
                    ("site", self.site),
                    ("tenant", self.tenant),
                    ("client", self.client),
                    ("library", self.library),
                    ("folder", self.folder),
                    ("filename", self.filename),
                ):
                    field.setText(str(profile.get(key, "")))
                self.destination_settings = settings.get("destinations", {})
            self.refresh_capacity()
            self._update_workflow_guide()
            if (self.current / "mart.duckdb").exists():
                self.inspect_database()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))

    def create_workspace(self):
        name = self.name.text().strip()
        if (
            not name
            or name in (".", "..")
            or Path(name).name != name
            or any(c in name for c in '<>:"/\\|?*')
        ):
            self.status.setText(self.t(2))
            return
        try:
            WorkspaceStore(self.root / name).create()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
            return
        self.current = self.root / name
        self.refresh_list()
        self._select(name)

    def _save_ui(self, **changes):
        path = self.current / "ui.json"
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self._validate_ui_settings(settings)
        settings.update(changes)
        self._validate_ui_settings(settings)
        _atomic_json(path, settings)

    @staticmethod
    def _validate_ui_settings(settings):
        if not isinstance(settings, dict):
            raise ValueError("Invalid workspace settings")
        budget = settings.get("budget_gib")
        if budget is not None and budget != "":
            if isinstance(budget, bool) or not isinstance(budget, (int, float)):
                raise ValueError("Invalid workspace budget")
            try:
                valid_budget = math.isfinite(budget) and budget > 0
            except OverflowError as exc:
                raise ValueError("Invalid workspace budget") from exc
            if not valid_budget:
                raise ValueError("Invalid workspace budget")
        if "warning_active" in settings and not isinstance(
            settings["warning_active"], bool
        ):
            raise ValueError("Invalid capacity warning")

        def profile(value):
            if not isinstance(value, dict):
                raise ValueError("Invalid publication profile")
            if any(
                not isinstance(value[key], str)
                for key in ("site", "tenant", "client", "library", "folder", "filename")
                if key in value
            ):
                raise ValueError("Invalid publication profile field")

        profile(settings.get("sharepoint", {}))
        destinations = settings.get("destinations", {})
        if not isinstance(destinations, dict):
            raise ValueError("Invalid publication destinations")
        for key, value in destinations.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise ValueError("Invalid publication destination")
            if any(
                not isinstance(value[field], str)
                for field in ("filename", "local_path")
                if field in value
            ):
                raise ValueError("Invalid publication destination field")
            if "sharepoint" in value:
                profile(value["sharepoint"])
        return settings

    def _settings_save_warning(self):
        self.settings_warning.setText(
            {
                "ko": "편의 설정을 저장하지 못했습니다. 작업 결과는 위 상태에서 확인하세요.",
                "en": "Convenience settings could not be saved. Check the operation status above.",
                "pl": "Nie zapisano ustawień pomocniczych. Sprawdź powyższy stan operacji.",
            }[self.language_code]
        )

    def save_profile(self):
        if not self.current:
            self.status.setText(self.t(83))
            return
        profile = {
            key: field.text()
            for key, field in (
                ("site", self.site),
                ("tenant", self.tenant),
                ("client", self.client),
                ("library", self.library),
                ("folder", self.folder),
                ("filename", self.filename),
            )
        }
        try:
            self._validate_ui_settings({"destinations": self.destination_settings})
            if self.candidate:
                self.destination_settings.setdefault(self.candidate, {}).update(
                    {"filename": self.filename.text(), "sharepoint": profile}
                )
            self._save_ui(sharepoint=profile, destinations=self.destination_settings)
            self.status.setText(self.t(69))
        except (OSError, ValueError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))

    def choose_source(self):
        files, _ = QFileDialog.getOpenFileNames(self, self.t(4), "", "CSV (*.csv)")
        if files:
            self.set_files(files)

    def set_files(self, files):
        self._reset_declarations()
        self.files = [Path(p) for p in files]
        self.source.setText("; ".join(p.name for p in self.files))
        self.files_label.setText("\n".join(p.name for p in self.files))
        self.inspect_source()
        self._autofill_scope_inputs()

    def open_model_wizard(self):
        target_path = None
        if self.files:
            target_path = self.files[0]
        elif self.source.text():
            parts = [p.strip() for p in self.source.text().split(";") if p.strip()]
            if parts and Path(parts[0]).exists():
                target_path = Path(parts[0])

        if not target_path or not target_path.exists():
            self.choose_source()
            if self.files:
                target_path = self.files[0]
            elif self.source.text():
                parts = [p.strip() for p in self.source.text().split(";") if p.strip()]
                if parts and Path(parts[0]).exists():
                    target_path = Path(parts[0])

        if not target_path or not target_path.exists():
            return

        from PySide6.QtWidgets import QDialog
        from src.qt.widgets.mart_model_wizard import MartModelWizardDialog

        from PySide6.QtCore import Qt
        dlg = MartModelWizardDialog(
            parent=self,
            csv_path=target_path,
            initial_model=self.model,
            language_code=self.language_code,
        )
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_model = dlg.get_model()
            if new_model:
                from dataclasses import replace
                aligned_model = replace(
                    new_model,
                    name=self.current.name if self.current else new_model.name,
                    version="1",
                )
                prev_model = self.model
                self._wizard_source_delim = getattr(aligned_model, "source_delimiter", ",")
                self._wizard_source_enc = getattr(aligned_model, "source_encoding", "utf-8-sig")
                self._wizard_dup_policy = getattr(aligned_model, "duplicate_grain_policy", "reject")
                self.restore_model(aligned_model)
                self.save_model()
                # Rollback UI forms if save failed (e.g. locked DB or validation error)
                if self.model != aligned_model and prev_model is not None:
                    self.restore_model(prev_model)

    _launch_wizard = open_model_wizard

    def _run(self, work, done, progress=None, *, cancelable=False, on_finished=None):
        if self._busy:
            return
        self._busy = True
        self.cancel_event.clear()
        self.edit_area.setEnabled(False)
        self.cancel.setEnabled(cancelable)
        self.status.setText(self.t(68))

        def success(result):
            self.status.setText(self.t(69))
            done(result)

        def error(exc):
            self.status.setText(self.t(70) + ": " + str(exc))

        def finished():
            self._busy = False
            self.edit_area.setEnabled(True)
            self.cancel.setEnabled(False)
            if on_finished:
                on_finished()

        if not self.runner.start(
            "mart_workspace",
            work,
            JobCallbacks(progress or (lambda p, d: None), success, error, finished),
        ):
            finished()
            self.status.setText(self.t(70))

    def inspect_source(self):
        files = (
            tuple(self.files)
            if self.files
            else ((Path(self.source.text()),) if self.source.text() else ())
        )
        if not files:
            self.status.setText(self.t(83))
            return

        def read(_):
            result = []
            headers = None
            delim = getattr(self.model, "source_delimiter", None) if self.model else None
            enc = getattr(self.model, "source_encoding", None) if self.model else None
            if not delim or not enc:
                from src.csv_processing import sniff_source
                auto_enc, auto_delim = sniff_source(files[0])
                enc = enc or auto_enc
                delim = delim or auto_delim
            self._detected_source_delim = delim
            self._detected_source_enc = enc
            for path in files:
                with path.open(encoding=enc, errors="replace", newline="") as f:
                    reader = csv.reader(f, delimiter=delim)
                    h = next(reader)
                    if len(h) != len(set(h)) or not all(h):
                        raise ValueError("Invalid CSV header")
                    if headers is not None and h != headers:
                        raise ValueError("Source headers differ")
                    headers = h
                    if not result:
                        result = [h] + [row for _, row in zip(range(10), reader)]
            return result

        def done(rows):
            self.columns = rows[0]
            self._set_columns(self.columns)
            self._table(self.source_preview, self.columns, rows[1:])
            self._autofill_scope_inputs()

        self._run(read, done)

    def _set_columns(self, cols):
        for picker in (
            self.row_keys,
            self.partitions,
            self.excluded,
            self.dim_keys,
            self.dim_attrs,
            self.export_cols,
            self.group_cols,
        ):
            picker.set_options(cols)
        for combo, blank in (
            (self.period, False),
            (self.metric_col, False),
            (self.dim_authority, True),
            (self.filter_col, True),
            (self.filter_col2, True),
        ):
            old = combo.currentText()
            combo.clear()
            combo.addItems(([""] if blank else []) + list(cols))
            combo.setCurrentText(old)

    def _on_dimension_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.dims):
            return
        d = self.dims[row]
        for w in (self.dim_name, self.dim_authority):
            w.blockSignals(True)
        self.dim_name.setText(d.name)
        self.dim_keys.blockSignals(True)
        self.dim_keys.set_values(d.key_columns)
        self.dim_keys.blockSignals(False)
        self.dim_attrs.blockSignals(True)
        self.dim_attrs.set_values(d.attributes)
        self.dim_attrs.blockSignals(False)
        self.dim_authority.setCurrentText(d.authority_column or "")
        for w in (self.dim_name, self.dim_authority):
            w.blockSignals(False)

    def _on_metric_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.metrics):
            return
        m = self.metrics[row]
        for w in (self.metric_name, self.metric_col, self.unit, self.precision, self.scale, self.kind):
            w.blockSignals(True)
        self.metric_name.setText(m.name)
        self.metric_col.setCurrentText(m.column)
        self.unit.setText(m.unit)
        self.precision.setValue(m.precision)
        self.scale.setValue(m.scale)
        self.kind.setCurrentText(m.kind)
        for w in (self.metric_name, self.metric_col, self.unit, self.precision, self.scale, self.kind):
            w.blockSignals(False)

    def add_dimension(self):
        self._reset_declarations()
        d = Dimension(
            self.dim_name.text().strip(),
            tuple(self.dim_keys.values),
            tuple(self.dim_attrs.values),
            self.dim_authority.currentText() or None,
        )
        if not d.name or not d.key_columns:
            self.status.setText(self.t(74))
            return
        for idx, existing in enumerate(self.dims):
            if existing.name == d.name:
                self.dims[idx] = d
                self._update_lists()
                return
        self.dims.append(d)
        self._update_lists()

    def remove_dimension(self):
        self._reset_declarations()
        i = self.dim_list.currentRow()
        if i >= 0:
            self.dims.pop(i)
            self._update_lists()

    def add_metric(self):
        self._reset_declarations()
        m = Measure(
            self.metric_name.text().strip(),
            self.metric_col.currentText(),
            self.precision.value(),
            self.scale.value(),
            self.unit.text(),
            "sum",
            self.kind.currentText(),
        )
        if not m.name or not m.column:
            self.status.setText(self.t(74))
            return
        for idx, existing in enumerate(self.metrics):
            if existing.name == m.name or existing.column == m.column:
                self.metrics[idx] = m
                self._update_lists()
                return
        self.metrics.append(m)
        self._update_lists()

    def remove_metric(self):
        self._reset_declarations()
        i = self.metric_list.currentRow()
        if i >= 0:
            self.metrics.pop(i)
            self._update_lists()

    def _update_lists(self):
        self.dim_list.blockSignals(True)
        self.dim_list.clear()
        self.dim_list.addItems(
            [
                d.name
                + ": "
                + ", ".join(d.key_columns)
                + " → "
                + ", ".join(d.attributes)
                for d in self.dims
            ]
        )
        self.dim_list.blockSignals(False)
        self.metric_list.blockSignals(True)
        self.metric_list.clear()
        self.metric_list.addItems(
            [
                f"{m.name} ← {m.column} ({m.unit}, SUM, DEC({m.precision},{m.scale}))"
                for m in self.metrics
            ]
        )
        self.metric_list.blockSignals(False)

    def save_model(self):
        self._reset_declarations()
        if not self.current:
            self.status.setText(self.t(83))
            return
        source_delim = (
            getattr(self, "_wizard_source_delim", None)
            or getattr(self, "_restored_source_delim", None)
            or getattr(self, "_detected_source_delim", None)
            or (getattr(self.model, "source_delimiter", None) if self.model else None)
            or ","
        )
        source_enc = (
            getattr(self, "_wizard_source_enc", None)
            or getattr(self, "_restored_source_enc", None)
            or getattr(self, "_detected_source_enc", None)
            or (getattr(self.model, "source_encoding", None) if self.model else None)
            or "utf-8-sig"
        )
        dup_policy = (
            getattr(self, "_wizard_dup_policy", None)
            or (self.duplicate_policy.currentData() if hasattr(self, "duplicate_policy") else None)
            or (getattr(self.model, "duplicate_grain_policy", None) if self.model else None)
            or "reject"
        )
        self._wizard_source_delim = None
        self._wizard_source_enc = None
        self._wizard_dup_policy = None
        model_ver = getattr(self.model, "version", "1") if self.model else "1"
        try:
            model = MartModel(
                self.current.name,
                self.fact.text().strip(),
                tuple(self.dims),
                tuple(self.metrics),
                self.period.currentText(),
                tuple(self.partitions.values),
                tuple(self.row_keys.values),
                tuple(self.excluded.values),
                version=model_ver,
                source_delimiter=source_delim,
                source_encoding=source_enc,
                duplicate_grain_policy=dup_policy if dup_policy in ("reject", "sum") else "reject",
            )
            required = MartEngine(WorkspaceStore(self.current))._validate_model(model)
            if set(self.columns) != required:
                raise ValueError(
                    "Every CSV column needs a model role or explicit exclusion"
                )
            if (
                (self.current / "mart.duckdb").exists()
                and self.model is not None
                and model != self.model
            ):
                raise ValueError(
                    "An existing database requires its original model; create another workspace for model changes"
                )
            _atomic_json(self.current / "model.json", asdict(model))
            self.model = model
            self._restored_source_delim = model.source_delimiter
            self._restored_source_enc = model.source_encoding
            self._scope_fields(model)
            self.refresh_mapping_table()
            self._populate_recipe_combo()
            self._update_workflow_guide()
            self.status.setText(self.t(69))
        except (ValueError, OSError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))

    def restore_model(self, m=None):
        if m is None:
            m = self.model
        if m is None:
            return
        self._restored_source_delim = getattr(m, "source_delimiter", ",")
        self._restored_source_enc = getattr(m, "source_encoding", "utf-8-sig")
        cols = list(
            dict.fromkeys(
                [
                    m.period_column,
                    *m.partition_columns,
                    *m.row_key_columns,
                    *m.excluded_columns,
                    *[
                        x
                        for d in m.dimensions
                        for x in (
                            *d.key_columns,
                            *d.attributes,
                            *((d.authority_column,) if d.authority_column else ()),
                        )
                    ],
                    *[x.column for x in m.measures],
                ]
            )
        )
        self.columns = cols
        self._set_columns(cols)
        self.fact.setText(m.fact_table)
        self.period.setCurrentText(m.period_column)
        self.row_keys.set_values(m.row_key_columns)
        self.partitions.set_values(m.partition_columns)
        self.excluded.set_values(m.excluded_columns)
        if hasattr(self, "duplicate_policy"):
            policy = getattr(m, "duplicate_grain_policy", "reject")
            idx = self.duplicate_policy.findData(policy)
            if idx >= 0:
                self.duplicate_policy.blockSignals(True)
                self.duplicate_policy.setCurrentIndex(idx)
                self.duplicate_policy.blockSignals(False)
        self.dims = list(m.dimensions)
        self.metrics = list(m.measures)
        self._update_lists()
        if self.metrics:
            self._on_metric_selected(0)
        self._scope_fields(m)

    def _scope_fields(self, model=None):
        if model is None:
            model = self.model
        if model is None:
            return
        while self.scope_form.count():
            item = self.scope_form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.scope_inputs = {}
        self._autofilled_scope = {}
        for col in (model.period_column, *model.partition_columns):
            edit = QLineEdit()
            edit.textChanged.connect(self._reset_declarations)
            self.scope_inputs[col] = edit
            self.scope_form.addRow(col, edit)
        self._autofill_scope_inputs(model)

    def _resolve_source_files(self) -> tuple[Path, ...]:
        if self.files:
            return tuple(Path(p) for p in self.files if Path(p).exists())
        if self.source.text():
            parts = [Path(p.strip()) for p in self.source.text().split(";") if p.strip()]
            return tuple(p for p in parts if p.exists())
        return ()

    def _autofill_scope_inputs(self, model=None):
        if model is None:
            model = self.model
        if model is None or not self.scope_inputs:
            return
        files = self._resolve_source_files()
        if not files:
            return
        detected_scope = None
        try:
            scopes = inspect_source_scopes(model, files)
            if scopes:
                detected_scope = scopes[0]
        except Exception:
            detected_scope = None
        if not detected_scope:
            try:
                delim = getattr(model, "source_delimiter", ",") or ","
                enc = getattr(model, "source_encoding", "utf-8-sig") or "utf-8-sig"
                with files[0].open(encoding=enc, errors="replace", newline="") as f:
                    reader = csv.reader(f, delimiter=delim)
                    headers = [c.strip().lstrip("\ufeff") for c in next(reader, [])]
                    for row in reader:
                        if any(cell.strip() for cell in row):
                            row_map = {
                                headers[i]: row[i].strip()
                                for i in range(min(len(headers), len(row)))
                            }
                            detected_scope = {
                                col: row_map.get(col, "")
                                for col in self.scope_inputs
                                if row_map.get(col, "")
                            }
                            break
            except Exception:
                detected_scope = None
        if detected_scope:
            autofilled = getattr(self, "_autofilled_scope", {})
            for col, edit in self.scope_inputs.items():
                val = str(detected_scope.get(col, "")).strip()
                curr = edit.text().strip()
                if val and (not curr or curr == autofilled.get(col)):
                    edit.blockSignals(True)
                    edit.setText(val)
                    edit.blockSignals(False)
                    autofilled[col] = val
            self._autofilled_scope = autofilled

    def load_snapshot(self):
        if (
            not self.current
            or not self.model
            or not self.files
            or not self.complete.isChecked()
        ):
            self.status.setText(self.t(74))
            self._reset_declarations()
            return
        model = self.model
        files = tuple(self.files)
        scope = {k: w.text().strip() for k, w in self.scope_inputs.items()}
        current = self.current
        empty = self.empty.isChecked()
        cancel = self.cancel_event

        def work(_):
            engine = MartEngine(
                WorkspaceStore(current), ResourcePreflight(cancel=cancel)
            )
            if not empty:
                try:
                    detected_scopes = inspect_source_scopes(model, files)
                except Exception:
                    detected_scopes = ()
                if len(detected_scopes) > 1:
                    return engine.load_all_scopes(
                        model,
                        files,
                        [p.name for p in files],
                        complete=True,
                        allow_empty=empty,
                    )
                if len(detected_scopes) == 1 and any(not v for v in scope.values()):
                    for k, v in detected_scopes[0].items():
                        if not scope.get(k):
                            scope[k] = v
            return engine.load_snapshot(
                model,
                files,
                scope,
                [p.name for p in files],
                complete=True,
                allow_empty=empty,
            )

        self._run(
            work,
            lambda r: self._loaded(r),
            cancelable=True,
            on_finished=self._reset_declarations,
        )

    def _reset_declarations(self, *_args):
        self.complete.setChecked(False)
        self.empty.setChecked(False)

    def _loaded(self, result):
        self.candidate = None
        self._reset_declarations()
        self.load_result.setText(
            self.t(79)
            + " "
            + str(result["revision"])
            + " — "
            + self.t(80)
            + " "
            + str(result.get("rows", 0))
        )
        self.status.setText(self.t(69))
        self.refresh_mapping_table()
        self._populate_recipe_combo()
        self.refresh_candidates(result["revision"])
        self.refresh_capacity()
        self._update_workflow_guide()

    @staticmethod
    def _table(table, headers, rows):
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels([str(h) for h in headers])
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                table.setItem(
                    i, j, QTableWidgetItem("" if value is None else str(value))
                )
        table.resizeColumnsToContents()

    def inspect_database(self):
        if not self.current:
            return
        current = self.current
        offset = self.offset

        def work(_):
            e = MartEngine(WorkspaceStore(current))
            return e.catalog(), e.sample(limit=100, offset=offset), e.reconcile()

        def done(result):
            catalog, sample, recon = result
            self.home_status.setText(
                self.t(79) + ": " + str(catalog["revision"]) + " — " + self.t(81)
            )
            self.refresh_candidates(catalog["revision"])
            self._update_workflow_guide(catalog.get("scopes"))
            self._table(
                self.home,
                [self.t(92), self.t(80)],
                [
                    (
                        ", ".join(
                            str(k) + " = " + str(v) for k, v in s["scope"].items()
                        ),
                        s["rows"],
                    )
                    for s in catalog["scopes"]
                ],
            )
            self._table(
                self.structure,
                self._structure_headers(),
                [(a, b, c) for a, b, c in catalog["columns"]]
                + [
                    (
                        a,
                        typ,
                        ", ".join(cols or [])
                        + " → "
                        + str(ref or "")
                        + " "
                        + ", ".join(refcols or []),
                    )
                    for a, typ, cols, ref, refcols in catalog["constraints"]
                ],
            )
            self.draw_relations(catalog)
            rows = sample["rows"]
            cols = list(rows[0]) if rows else []
            self._table(self.sample, cols, [[r.get(c) for c in cols] for r in rows])
            rr = []
            for scope in recon["scopes"]:
                label = ", ".join(
                    str(k) + " = " + str(v) for k, v in scope["scope"].items()
                )
                rr.append(
                    (
                        label,
                        self.t(80),
                        scope["expected"]["rows"],
                        scope["actual"]["rows"],
                        scope["expected"]["rows"] == scope["actual"]["rows"],
                    )
                )
                for key, value in scope["expected"]["sums"].items():
                    rr.append(
                        (
                            label,
                            key,
                            value,
                            scope["actual"]["sums"].get(key),
                            value == scope["actual"]["sums"].get(key),
                        )
                    )
            self._table(
                self.reconciliation,
                [self.t(92), self.t(89), self.t(93), self.t(94), self.t(95)],
                rr,
            )
            self.refresh_capacity()

        self._run(work, done)

    def _structure_headers(self):
        return {
            "ko": ["테이블", "열 / 제약", "형식 / 참조"],
            "en": ["Table", "Column / constraint", "Type / reference"],
            "pl": ["Tabela", "Kolumna / ograniczenie", "Typ / odwołanie"],
        }[self.language_code]

    def draw_relations(self, catalog):
        """Draw only catalog-backed foreign keys; flag absent model relationships."""
        scene = self.relation_scene
        scene.clear()
        model = catalog.get("model") or {}
        fact = "fact_" + model.get("fact_table", "")
        scene.addRect(260, 10, 220, 48)
        scene.addText(fact).setPos(268, 21)
        constraints = catalog.get("constraints", [])
        actual = {
            (table, tuple(columns or ()), target, tuple(target_columns or ()))
            for table, kind, columns, target, target_columns in constraints
            if kind == "FOREIGN KEY"
        }
        primary = {
            (table, tuple(columns or ()))
            for table, kind, columns, _target, _target_columns in constraints
            if kind == "PRIMARY KEY"
        }
        for index, dimension in enumerate(model.get("dimensions", [])):
            name = dimension["name"]
            target = "dim_" + name
            present = (fact, ("dim_" + name + "_sk",), target, ("sk",)) in actual and (
                target,
                ("sk",),
            ) in primary
            x = index * 180
            pen = QPen(QColor("#24704a" if present else "#b42318"), 2)
            scene.addRect(x, 120, 170, 48, pen)
            scene.addText(target).setPos(x + 5, 126)
            scene.addText("FK ✓" if present else "FK ?").setPos(x + 5, 145)
            scene.addLine(370, 58, x + 85, 120, pen)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-10, -10, 10, 10))

    def next_page(self):
        self.offset += 100
        self.inspect_database()

    def prev_page(self):
        self.offset = max(0, self.offset - 100)
        self.inspect_database()

    def refresh_candidates(self, revision):
        self.last_revision = revision
        selected = self.candidate
        self.candidates.blockSignals(True)
        self.candidates.clear()
        self.candidates.addItem(self.t(86), None)
        folder = self.current / "exports"
        if folder.exists():
            for path in sorted(folder.glob("*/manifest.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    candidate = data["candidate"]
                    stale = data["revision"] != revision
                    states = {
                        "ko": (
                            "이전 리비전",
                            "승인 기록 있음 (게시 전 재검증)",
                            "미리보기",
                        ),
                        "en": (
                            "STALE",
                            "APPROVAL SAVED (VERIFY ON PUBLISH)",
                            "PREVIEW",
                        ),
                        "pl": (
                            "NIEAKTUALNY",
                            "ZAPISANO ZATWIERDZENIE (SPRAWDŹ PRZED PUBLIKACJĄ)",
                            "PODGLĄD",
                        ),
                    }[self.language_code]
                    state = (
                        states[0]
                        if stale
                        else states[1]
                        if (path.parent / "approval.json").exists()
                        else states[2]
                    )
                    self.candidates.addItem(
                        data["definition"]["name"]
                        + " — "
                        + state
                        + " — "
                        + candidate[:8],
                        candidate,
                    )
                    if candidate == selected:
                        self.candidates.setCurrentIndex(self.candidates.count() - 1)
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        self.candidates.blockSignals(False)

    def select_candidate(self, index):
        self.candidate = self.candidates.itemData(index)
        if not self.candidate or not self.current:
            return
        target = self.destination_settings.get(self.candidate, {})
        profile = target.get("sharepoint", {})
        if profile:
            for key, field in (
                ("site", self.site),
                ("tenant", self.tenant),
                ("client", self.client),
                ("library", self.library),
                ("folder", self.folder),
            ):
                field.setText(str(profile.get(key, "")))
        if target.get("filename"):
            self.filename.setText(target["filename"])
        current = self.current
        candidate = self.candidate

        def work(_):
            manifest = MartEngine(WorkspaceStore(current)).inspect_export(candidate)
            with (current / "exports" / candidate / "data.csv").open(
                encoding="utf-8-sig", newline=""
            ) as source:
                reader = csv.reader(source)
                return manifest, next(reader), [r for _, r in zip(range(100), reader)]

        def done(result):
            definition = result[0]["definition"]
            self.export_name.setText(definition["name"])
            self.export_cols.set_values(definition["columns"])
            self.group_cols.set_values(definition.get("group_by", []))
            filters = definition.get("filters", [])
            for index, (combo, value) in enumerate(
                (
                    (self.filter_col, self.filter_value),
                    (self.filter_col2, self.filter_value2),
                )
            ):
                column, text = filters[index] if index < len(filters) else ("", "")
                combo.setCurrentText(column)
                value.setText(text)
            self.start.setText(definition.get("period_start") or "")
            self.end.setText(definition.get("period_end") or "")
            self._table(self.export_preview, result[1], result[2])
            self._display_recipe_reconciliation(result[0])

        self._run(work, done)

    def save_budget(self):
        if not self.current:
            return
        try:
            value = float(self.budget.text()) if self.budget.text().strip() else None
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("Budget must be positive")
            self.budget_value = value
            self._save_ui(budget_gib=value or "", warning_active=self.warning)
            self.refresh_capacity()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def refresh_capacity(self):
        if not self.current:
            return

        def files_in(path):
            return [p for p in path.rglob("*") if p.is_file()] if path.exists() else []

        db = self.current / "mart.duckdb"
        try:
            self._validate_ui_settings({"budget_gib": self.budget_value})
            budget = float(self.budget_value) * 1024**3 if self.budget_value else None
            if budget is not None and (not math.isfinite(budget) or budget <= 0):
                budget = None
                self._settings_save_warning()
        except (TypeError, ValueError, OverflowError):
            budget = None
            self._settings_save_warning()
        # The first two groups are independently measured because exports are also retained.
        retained = files_in(self.current)
        exports = files_in(self.current / "exports")
        info = collect_capacity(
            [db], budget, self.warning, groups={"retained": retained}
        )
        output_info = collect_capacity([], groups={"exports": exports})
        if info["warning"] is not None and info["warning"] != self.warning:
            self.warning = info["warning"]
            try:
                self._save_ui(
                    budget_gib=self.budget_value or "", warning_active=self.warning
                )
            except (OSError, ValueError):
                self._settings_save_warning()
        space = available_space(self.current)

        def fmt(v):
            return self.t(75) if v is None else f"{v / 1024**3:.3f} GiB"

        ratio = "" if info["ratio"] is None else f" ({info['ratio'] * 100:.1f}%)"
        self.cap_labels[0].setText(
            (fmt(info["known_bytes"]) if info["complete"] else self.t(75))
            + ratio
            + (" — " + self.t(76) if self.warning else "")
        )
        self.cap_labels[1].setText(
            fmt(info["groups"]["retained"]["known_bytes"])
            if info["groups"]["retained"]["complete"]
            else self.t(75)
        )
        self.cap_labels[2].setText(
            fmt(output_info["groups"]["exports"]["known_bytes"])
            if output_info["groups"]["exports"]["complete"]
            else self.t(75)
        )
        self.cap_labels[3].setText(fmt(space.value))

    def create_export(self):
        if not self.current or not self.model:
            self.status.setText(self.t(83))
            return
        filters = tuple(
            (combo.currentText(), value.text())
            for combo, value in (
                (self.filter_col, self.filter_value),
                (self.filter_col2, self.filter_value2),
            )
            if combo.currentText()
        )
        definition = ExportDefinition(
            self.export_name.text().strip(),
            tuple(self.export_cols.values),
            group_by=tuple(self.group_cols.values),
            filters=filters,
            period_start=self.start.text().strip() or None,
            period_end=self.end.text().strip() or None,
        )
        current = self.current

        def work(_):
            result = MartEngine(
                WorkspaceStore(current), ResourcePreflight(cancel=self.cancel_event)
            ).create_export(definition)
            with (current / "exports" / result["candidate"] / "data.csv").open(
                encoding="utf-8-sig", newline=""
            ) as f:
                reader = csv.reader(f)
                return result, next(reader), [r for _, r in zip(range(100), reader)]

        def done(result):
            self.candidate = result[0]["candidate"]
            self._table(self.export_preview, result[1], result[2])
            self._display_recipe_reconciliation(result[0])
            self.refresh_candidates(result[0]["revision"])
            self.refresh_capacity()

        self._run(work, done, cancelable=True)

    def approve(self):
        if not self.candidate:
            self.status.setText(self.t(83))
            return
        current = self.current
        candidate = self.candidate
        reviewer = self.reviewer.text()
        self._run(
            lambda _: MartEngine(WorkspaceStore(current)).approve_export(
                candidate, reviewer
            ),
            lambda r: self.refresh_candidates(r["revision"]),
        )

    def publish_local(self):
        if not self.candidate:
            self.status.setText(self.t(83))
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t(54), "", "CSV (*.csv)")
        if path:
            current = self.current
            candidate = self.candidate

            def done(_result):
                try:
                    if not isinstance(self.destination_settings, dict):
                        raise ValueError("Invalid publication destinations")
                    destination = self.destination_settings.setdefault(candidate, {})
                    if not isinstance(destination, dict):
                        raise ValueError("Invalid publication destination")
                    destination["local_path"] = path
                    self._save_ui(destinations=self.destination_settings)
                except (OSError, ValueError):
                    self._settings_save_warning()

            self._run(
                lambda _: MartEngine(
                    WorkspaceStore(current), ResourcePreflight(cancel=self.cancel_event)
                ).publish_local(candidate, path),
                done,
                cancelable=True,
            )

    def login(self):
        from src.mart.publication import SharePointProfile, SharePointPublisher

        try:
            publisher = SharePointPublisher(
                SharePointProfile(
                    self.site.text(),
                    self.tenant.text(),
                    self.client.text(),
                    self.library.text(),
                    self.folder.text(),
                )
            )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.publisher = publisher

        def done(result):
            self.login_info.setText(result)
            if result != "authenticated":
                self.status.setText(self.t(70) + ": " + result)

        self._run(
            lambda report: publisher.login_device(
                lambda info: report(0, " | ".join(info.values())),
                cancel=self.cancel_event,
            ),
            done,
            lambda p, d: self.login_info.setText(d),
            cancelable=True,
        )

    def publish_remote(self):
        if (
            not self.current
            or not self.candidate
            or not self.publisher
            or not self.publisher.token
        ):
            self.status.setText(self.t(83))
            return
        profile = self.publisher.profile
        if (
            profile.site_url,
            profile.tenant,
            profile.client_id,
            profile.library,
            profile.folder,
        ) != (
            self.site.text(),
            self.tenant.text(),
            self.client.text(),
            self.library.text(),
            self.folder.text(),
        ):
            self.status.setText(
                {
                    "ko": "대상 설정이 바뀌었습니다. 다시 로그인하세요.",
                    "en": "The target settings changed. Sign in again.",
                    "pl": "Ustawienia celu uległy zmianie. Zaloguj się ponownie.",
                }[self.language_code]
            )
            return
        current = self.current
        candidate = self.candidate
        publisher = self.publisher
        filename = self.filename.text()

        def done(result):
            self.status.setText(result.status)
            self.login_info.setText(
                result.status
                + " — "
                + result.detail
                + "; previous: "
                + result.previous_remote
                + "; consumer refresh: "
                + result.consumer_refresh
            )

        self._run(
            lambda _: MartEngine(WorkspaceStore(current)).publish_sharepoint(
                candidate, publisher, filename
            ),
            done,
        )

    def copy_excel(self):
        from PySide6.QtWidgets import QApplication
        from src.mart.excel import sharepoint_m_code

        if (
            not self.current
            or not self.candidate
            or not self.publisher
            or not self.publisher.token
        ):
            self.status.setText(self.t(83))
            return
        profile = self.publisher.profile
        if (
            profile.site_url,
            profile.tenant,
            profile.client_id,
            profile.library,
            profile.folder,
        ) != (
            self.site.text(),
            self.tenant.text(),
            self.client.text(),
            self.library.text(),
            self.folder.text(),
        ):
            self.status.setText(self.t(70))
            return
        site = self.site.text()
        filename = self.filename.text()
        publisher = self.publisher
        candidate = self.candidate
        current = self.current
        saved = self.destination_settings.get(candidate, {})
        expected_profile = {
            "site": site,
            "tenant": self.tenant.text(),
            "client": self.client.text(),
            "library": self.library.text(),
            "folder": self.folder.text(),
            "filename": filename,
        }
        if (
            saved.get("sharepoint") != expected_profile
            or saved.get("filename") != filename
        ):
            self.status.setText(self.t(70))
            return

        def work(_):
            def build(manifest, model):
                if manifest["definition"].get("format", "wide") != "wide":
                    raise ValueError("Excel guidance supports wide CSV only")
                columns = manifest["definition"]["columns"]
                numeric = [
                    m["column"] for m in model["measures"] if m["column"] in columns
                ]
                target = publisher.resolve()
                return sharepoint_m_code(
                    site, target["folder_url"], filename, columns, numeric
                )

            return MartEngine(WorkspaceStore(current)).with_approved_export(
                candidate, build
            )

        def done(code):
            guide = {
                "ko": "제작자가 Excel에서 데이터 > 데이터 가져오기 > 빈 쿼리 > 고급 편집기를 열고 아래 코드만 붙여넣으세요. 회사 계정으로 로그인하고 데이터 모델로 로드한 뒤 통합 문서를 저장해 배포하세요. 받는 사람은 각자 회사 계정으로 로그인해 모두 새로 고침을 사용합니다. 이 안내는 연결된 통합 문서가 아닙니다.\n\n",
                "en": "The author opens Data > Get Data > Blank Query > Advanced Editor in Excel and pastes only the code below. Sign in, load to the data model, then save and share the workbook. Each recipient signs in with their own account and uses Refresh All. This guide is not a connected workbook.\n\n",
                "pl": "Autor wybiera w Excel Dane > Pobierz dane > Puste zapytanie > Edytor zaawansowany i wkleja tylko poniższy kod. Po zalogowaniu ładuje dane do modelu oraz zapisuje i udostępnia skoroszyt. Odbiorcy logują się własnymi kontami i wybierają Odśwież wszystko. Ta instrukcja nie jest połączonym skoroszytem.\n\n",
            }[self.language_code]
            QApplication.clipboard().setText(guide + code)
            self.status.setText(self.t(69))

        self._run(work, done)

    # ------------------------------------------------------------------
    # Stage 1 / 2 / 3 Workflow Guide, Blueprint Clone, Mapping & Recipes
    # ------------------------------------------------------------------
    def _mapping_headers(self):
        return {
            "ko": ["원천 컬럼", "대상 유형", "계정 코드", "표시명", "대분류", "중분류", "소분류", "부호", "정렬순서"],
            "en": ["Source Column", "Type", "Code", "Display Name", "Major Category", "Medium Category", "Minor Category", "Sign", "Order"],
            "pl": ["Kolumna źródła", "Typ", "Kod", "Nazwa", "Kategoria gł.", "Podkategoria", "Szczegół", "Znak", "Kolejność"],
        }.get(self.language_code, ["Source Column", "Type", "Code", "Display Name", "Major Category", "Medium Category", "Minor Category", "Sign", "Order"])

    def _reconcile_headers(self):
        return {
            "ko": ["구분/대분류", "원천 DB 합계", "생성 마트 합계", "차이(Diff)", "일치 여부"],
            "en": ["Category", "Source DB Sum", "Exported Mart Sum", "Diff", "Match"],
            "pl": ["Kategoria", "Suma źródłowa", "Suma eksportu", "Różnica", "Zgodność"],
        }.get(self.language_code, ["Category", "Source DB Sum", "Exported Mart Sum", "Diff", "Match"])

    def _update_workflow_guide(self, scopes=None):
        if not hasattr(self, "workflow_guide_banner"):
            return
        lang = self.language_code
        if not self.current:
            self.workflow_guide_banner.setText(
                {
                    "ko": "💡 시작 안내: 왼쪽에서 작업 공간을 선택하거나 이름을 입력해 '새 작업'으로 시작하세요.",
                    "en": "💡 Guide: Select an existing workspace or enter a name to create a new one.",
                    "pl": "💡 Wskazówka: Wybierz obszar roboczy lub utwórz nowy powyżej.",
                }.get(
                    lang,
                    "💡 Guide: Select an existing workspace or enter a name to create a new one.",
                )
            )
            return
        ws_name = self.current.name
        has_db = (self.current / "mart.duckdb").exists()
        saved_maps = load_mappings_json(self.current)
        saved_recipes = load_recipes(self.current) if (self.current / "recipes.json").exists() else []
        if not self.model:
            msg = {
                "ko": f"📂 [{ws_name}] 1-1단계에서 원본 CSV를 선택하고 '✨ 스타 스키마 설계 마법사'로 차원·팩트를 설계하세요.",
                "en": f"📂 [{ws_name}] Pick source file(s) in Step 1-1 and launch '✨ Star Schema Modeling Wizard' to configure dimensions & facts.",
                "pl": f"📂 [{ws_name}] Wybierz pliki w Kroku 1-1 i uruchom kreator modelu gwiazdy, aby zdefiniować wymiary i fakty.",
            }.get(lang, f"📂 [{ws_name}] Configure dimensions & facts in Step 1-1.")
        elif not has_db:
            msg = {
                "ko": (
                    f"🧩 [{ws_name}] 설계도 준비 완료 (지표 {len(self.model.measures)}개, 저장된 매핑 {len(saved_maps)}개, 레시피 {len(saved_recipes)}개). "
                    "1-2단계에서 첫 원본 파일을 적재하면 DuckDB가 생성됩니다."
                ),
                "en": (
                    f"🧩 [{ws_name}] Blueprint ready ({len(self.model.measures)} measures, {len(saved_maps)} mappings, {len(saved_recipes)} recipes). "
                    "Load your first source snapshot in Step 1-2 to create DuckDB."
                ),
                "pl": (
                    f"🧩 [{ws_name}] Szablon gotowy ({len(self.model.measures)} miar, {len(saved_maps)} mapowań, {len(saved_recipes)} przepisów). "
                    "Załaduj pierwszy plik w Kroku 1-2, aby utworzyć DuckDB."
                ),
            }.get(lang, f"🧩 [{ws_name}] Blueprint ready.")
        else:
            scope_txt = (
                {
                    "ko": f"적재 범위 {len(scopes)}개",
                    "en": f"{len(scopes)} scope(s) loaded",
                    "pl": f"Załadowano zakresów: {len(scopes)}",
                }[lang]
                if scopes is not None
                else {
                    "ko": "DB 적재 완료",
                    "en": "DB loaded",
                    "pl": "Baza załadowana",
                }[lang]
            )
            msg = {
                "ko": (
                    f"✅ [{ws_name}] {scope_txt} · 매핑 {len(saved_maps)}개 · 레시피 {len(saved_recipes)}개 | "
                    "다음 월 누적: [1-2. 스냅샷 적재] / 별도 DB 분리: [상단 📋 설계도 복제] / 마트 산출: [2단계(선택) → 3단계]"
                ),
                "en": (
                    f"✅ [{ws_name}] {scope_txt} · {len(saved_maps)} mappings · {len(saved_recipes)} recipes | "
                    "Append month: [1-2. Load] / Separate DB: [Clone Blueprint] / Build marts: [Step 2 (Optional) -> Step 3]"
                ),
                "pl": (
                    f"✅ [{ws_name}] {scope_txt} · {len(saved_maps)} mapowań · {len(saved_recipes)} przepisów | "
                    "Kolejny miesiąc: [1-2. Ładowanie] / Osobna baza: [Klonuj Szablon] / Marty: [Krok 2 -> Krok 3]"
                ),
            }.get(lang, f"✅ [{ws_name}] {scope_txt}")
        self.workflow_guide_banner.setText(msg)

    def clone_workspace_blueprint(self, new_name=None):
        if not isinstance(new_name, str):
            new_name = None
        if not self.current:
            self.status.setText(self.t(83))
            return None
        target_name = (new_name or self.name.text()).strip()
        if not target_name or target_name == self.current.name:
            entered, ok = QInputDialog.getText(
                self,
                "설계도 복제 (새 별도 DB)",
                f"'{self.current.name}'의 스키마·매핑·레시피만 복제할 새 작업 공간 이름:",
                text=f"{self.current.name}_copy",
            )
            if not ok:
                return None
            target_name = entered.strip()
        if (
            not target_name
            or target_name in (".", "..")
            or Path(target_name).name != target_name
            or any(c in target_name for c in '<>:"/\\|?*')
        ):
            self.status.setText(self.t(2))
            return None
        try:
            res = MartEngine(WorkspaceStore(self.current)).clone_blueprint(
                self.root / target_name
            )
        except (ValueError, OSError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))
            return None
        self.current = self.root / target_name
        self.refresh_list()
        self._select(target_name)
        self.status.setText(
            f"{self.t(69)}: '{target_name}' ({', '.join(res.get('copied', []))})"
        )
        return res

    def _current_mapping_entries(self):
        if hasattr(self, "mapping_table") and self.mapping_table.rowCount() > 0:
            return self._entries_from_mapping_table()
        if self.current:
            saved = load_mappings_json(self.current)
            if saved:
                return saved
        if self.model:
            return suggest_mapping_entries(self.model)
        return []

    def refresh_mapping_table(self):
        if not hasattr(self, "mapping_table"):
            return
        saved = load_mappings_json(self.current) if self.current else []
        self._saved_mappings_cache = list(saved)
        if not self.model:
            self._populate_mapping_table(saved, saved=bool(saved))
            return
        if not saved:
            draft = suggest_mapping_entries(self.model)
            self._populate_mapping_table(draft, saved=False)
            return
        by_col = {e.source_column: e for e in saved}
        by_col_fallback = {m.name: m.column for m in self.model.measures}
        aligned = []
        missing_any = False
        for m in self.model.measures:
            entry = by_col.get(m.column) or by_col.get(m.name)
            if entry is None:
                missing_any = True
                aligned.append(AccountMappingEntry(source_column=m.column, display_name=m.column))
            else:
                src_col = by_col_fallback.get(entry.source_column, entry.source_column)
                if src_col != entry.source_column:
                    entry = AccountMappingEntry(
                        source_column=src_col,
                        measure_type=entry.measure_type,
                        account_code=entry.account_code,
                        display_name=entry.display_name,
                        major_category=entry.major_category,
                        medium_category=entry.medium_category,
                        minor_category=entry.minor_category,
                        sign=entry.sign,
                        sort_order=entry.sort_order,
                        updated_at=entry.updated_at,
                    )
                aligned.append(entry)
        self._populate_mapping_table(aligned, saved=not missing_any)

    def _populate_mapping_table(self, entries, *, saved=True):
        from PySide6.QtGui import QBrush

        self.mapping_table.blockSignals(True)
        self.mapping_table.setColumnCount(9)
        self.mapping_table.setHorizontalHeaderLabels(self._mapping_headers())
        self.mapping_table.setRowCount(len(entries))

        unmapped_count = 0
        amt_count = 0
        non_mon_count = 0
        sub_count = 0

        for r, entry in enumerate(entries):
            is_unmapped = entry.measure_type == "amount" and not entry.major_category.strip()
            if is_unmapped:
                unmapped_count += 1
            if entry.measure_type == "amount":
                amt_count += 1
            elif entry.measure_type == "non_monetary":
                non_mon_count += 1
            elif entry.measure_type == "subtotal_excluded":
                sub_count += 1

            col_item = QTableWidgetItem(entry.source_column)
            col_item.setFlags(col_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.mapping_table.setItem(r, 0, col_item)

            type_combo = QComboBox()
            type_combo.addItem("amount", "amount")
            type_combo.addItem("non_monetary", "non_monetary")
            type_combo.addItem("subtotal_excluded", "subtotal_excluded")
            t_idx = type_combo.findData(entry.measure_type)
            if t_idx >= 0:
                type_combo.setCurrentIndex(t_idx)
            self.mapping_table.setCellWidget(r, 1, type_combo)

            vals = [
                (2, entry.account_code),
                (3, entry.display_name),
                (4, entry.major_category),
                (5, entry.medium_category),
                (6, entry.minor_category),
                (8, str(entry.sort_order if entry.sort_order else r + 1)),
            ]
            for c_idx, text in vals:
                it = QTableWidgetItem(text)
                if is_unmapped:
                    it.setBackground(QBrush(QColor("#fef3c7")))
                self.mapping_table.setItem(r, c_idx, it)

            sign_combo = QComboBox()
            sign_combo.addItem("+1", 1)
            sign_combo.addItem("-1", -1)
            s_idx = sign_combo.findData(int(entry.sign) if entry.sign in (1, -1) else 1)
            if s_idx >= 0:
                sign_combo.setCurrentIndex(s_idx)
            self.mapping_table.setCellWidget(r, 7, sign_combo)

            if is_unmapped:
                col_item.setBackground(QBrush(QColor("#fef3c7")))

        self.mapping_table.blockSignals(False)
        self.mapping_table.resizeColumnsToContents()
        self._last_mapping_stats = (len(entries), amt_count, non_mon_count, sub_count, unmapped_count, saved)
        self._update_mapping_status_label()

    def _update_mapping_status_label(self):
        if not hasattr(self, "mapping_status_label"):
            return
        stats = getattr(self, "_last_mapping_stats", None)
        if not stats:
            self.mapping_status_label.clear()
            return
        total, amt_count, non_mon_count, sub_count, unmapped_count, saved = stats
        lang = self.language_code
        save_state = (
            {"ko": "DB 저장됨", "en": "Saved to DB", "pl": "Zapisano w DB"}[lang]
            if saved
            else {"ko": "초안/수정본 (💾 DB에 매핑 저장 필요)", "en": "Draft/Modified (Save to DB required)", "pl": "Szkic/Zmieniono (Wymaga zapisu)"}[lang]
        )
        msg = {
            "ko": f"총 {total}개 지표 | 금액(amount): {amt_count} · 비금액(수량): {non_mon_count} · 원본소계(제외): {sub_count} · 미분류: {unmapped_count} | 상태: {save_state}",
            "en": f"{total} measure(s) | amount: {amt_count} · non_monetary: {non_mon_count} · subtotal_excluded: {sub_count} · unmapped: {unmapped_count} | Status: {save_state}",
            "pl": f"Miary: {total} | amount: {amt_count} · non_monetary: {non_mon_count} · subtotal_excluded: {sub_count} · niemapowane: {unmapped_count} | Stan: {save_state}",
        }.get(lang, f"{total} measure(s) | Status: {save_state}")
        self.mapping_status_label.setText(msg)

    def _entries_from_mapping_table(self):
        entries = []
        for r in range(self.mapping_table.rowCount()):
            col_item = self.mapping_table.item(r, 0)
            source_column = col_item.text().strip() if col_item else ""
            if not source_column:
                continue
            type_w = self.mapping_table.cellWidget(r, 1)
            measure_type = (
                type_w.currentData()
                if isinstance(type_w, QComboBox)
                else (self.mapping_table.item(r, 1).text().strip() if self.mapping_table.item(r, 1) else "amount")
            )
            sign_w = self.mapping_table.cellWidget(r, 7)
            sign_val = (
                int(sign_w.currentData())
                if isinstance(sign_w, QComboBox)
                else int(self.mapping_table.item(r, 7).text().strip() if self.mapping_table.item(r, 7) else 1)
            )
            order_text = self.mapping_table.item(r, 8).text().strip() if self.mapping_table.item(r, 8) else ""
            try:
                sort_order = int(order_text) if order_text else r + 1
            except ValueError as exc:
                raise ValueError(f"Row {r + 1}: 정렬순서는 정수여야 합니다 ('{order_text}')") from exc
            entries.append(
                AccountMappingEntry(
                    source_column=source_column,
                    measure_type=str(measure_type),
                    account_code=(self.mapping_table.item(r, 2).text().strip() if self.mapping_table.item(r, 2) else ""),
                    display_name=(self.mapping_table.item(r, 3).text().strip() if self.mapping_table.item(r, 3) else source_column),
                    major_category=(self.mapping_table.item(r, 4).text().strip() if self.mapping_table.item(r, 4) else ""),
                    medium_category=(self.mapping_table.item(r, 5).text().strip() if self.mapping_table.item(r, 5) else ""),
                    minor_category=(self.mapping_table.item(r, 6).text().strip() if self.mapping_table.item(r, 6) else ""),
                    sign=sign_val,
                    sort_order=sort_order,
                )
            )
        return entries

    def auto_suggest_mapping(self):
        if not self.model:
            self.status.setText(self.t(83))
            return
        existing = self._entries_from_mapping_table() if self.mapping_table.rowCount() else ()
        suggested = suggest_mapping_entries(self.model, existing=existing)
        self._populate_mapping_table(suggested, saved=False)
        self.status.setText(f"{self.t(69)}: 키워드 자동 분류 적용 (저장 버튼을 눌러 확정하세요)")

    def apply_bulk_mapping(self):
        rows = sorted({idx.row() for idx in self.mapping_table.selectedIndexes()})
        if not rows:
            self.status.setText("일괄 적용할 행을 표에서 먼저 선택하세요.")
            return
        target_type = self.bulk_type_combo.currentData()
        major_txt = self.bulk_major_edit.text().strip()
        medium_txt = self.bulk_medium_edit.text().strip()
        sign_val = self.bulk_sign_combo.currentData()
        for r in rows:
            if target_type:
                type_w = self.mapping_table.cellWidget(r, 1)
                if isinstance(type_w, QComboBox):
                    idx = type_w.findData(target_type)
                    if idx >= 0:
                        type_w.setCurrentIndex(idx)
            if major_txt:
                self.mapping_table.setItem(r, 4, QTableWidgetItem(major_txt))
            if medium_txt:
                self.mapping_table.setItem(r, 5, QTableWidgetItem(medium_txt))
            if sign_val is not None:
                sign_w = self.mapping_table.cellWidget(r, 7)
                if isinstance(sign_w, QComboBox):
                    idx = sign_w.findData(sign_val)
                    if idx >= 0:
                        sign_w.setCurrentIndex(idx)
        entries = self._entries_from_mapping_table()
        self._populate_mapping_table(entries, saved=False)

    def save_mapping_from_ui(self):
        if not self.current:
            self.status.setText(self.t(83))
            return
        try:
            entries = self._entries_from_mapping_table()
        except ValueError as exc:
            self.status.setText(self.t(70) + ": " + str(exc))
            return
        current = self.current

        def work(_):
            return MartEngine(WorkspaceStore(current)).save_account_mapping(entries)

        def done(res):
            self._saved_mappings_cache = list(entries)
            self._populate_mapping_table(entries, saved=True)
            self._populate_recipe_combo()
            self._update_workflow_guide()
            unmapped = res.get("unmapped") or []
            if unmapped:
                self.status.setText(
                    f"매핑 저장 완료 ({res['saved']}건). 주의: 미분류 지표 {len(unmapped)}건 남음."
                )
            else:
                self.status.setText(f"{self.t(69)}: 매핑 {res['saved']}건 DB 저장 완료")

        self._run(work, done)

    def export_mapping_dialog(self, path=None):
        if not isinstance(path, (str, Path)):
            path = None
        if path is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self, "계정 매핑 내보내기", "account_mapping.xlsx", "Excel (*.xlsx);;CSV (*.csv)"
            )
            if not chosen:
                return None
            path = chosen
        try:
            entries = self._entries_from_mapping_table()
            out = export_mapping_file(path, entries)
            self.status.setText(f"{self.t(69)}: {Path(out).name}")
            return out
        except (ValueError, OSError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))
            return None

    def import_mapping_dialog(self, path=None):
        if not isinstance(path, (str, Path)):
            path = None
        if path is None:
            chosen, _ = QFileDialog.getOpenFileName(
                self, "계정 매핑 가져오기", "", "Mapping Files (*.xlsx *.csv)"
            )
            if not chosen:
                return None
            path = chosen
        try:
            imported = import_mapping_file(path)
            merged = self._align_imported_mappings(imported)
            self._populate_mapping_table(merged, saved=False)
            self.status.setText(
                f"{self.t(69)}: 매핑 {len(imported)}건 불러옴 (💾 DB에 매핑 저장 버튼을 누르세요)"
            )
            return merged
        except (ValueError, OSError, FileNotFoundError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))
            return None

    def _align_imported_mappings(self, imported):
        if not self.model:
            return imported
        by_col = {e.source_column: e for e in imported}
        current_by_col = {
            e.source_column: e for e in (self._entries_from_mapping_table() if self.mapping_table.rowCount() else [])
        }
        aligned = []
        for idx, m in enumerate(self.model.measures, start=1):
            entry = by_col.get(m.column) or by_col.get(m.name)
            if entry is not None:
                aligned.append(
                    AccountMappingEntry(
                        source_column=m.column,
                        measure_type=entry.measure_type,
                        account_code=entry.account_code,
                        display_name=entry.display_name or m.column,
                        major_category=entry.major_category,
                        medium_category=entry.medium_category,
                        minor_category=entry.minor_category,
                        sign=entry.sign,
                        sort_order=entry.sort_order or idx,
                        updated_at=entry.updated_at,
                    )
                )
            elif m.column in current_by_col:
                aligned.append(current_by_col[m.column])
            else:
                aligned.append(AccountMappingEntry(source_column=m.column, display_name=m.column, sort_order=idx))
        return aligned

    def import_mapping_from_other_workspace(self, source_ws_name=None):
        if not isinstance(source_ws_name, str):
            source_ws_name = None
        if not self.current or not self.root.exists():
            self.status.setText(self.t(83))
            return None
        candidates = sorted(
            p.name
            for p in self.root.iterdir()
            if p.is_dir()
            and p.name != self.current.name
            and ((p / "mappings.json").is_file() or (p / "mart.duckdb").is_file())
        )
        if not candidates:
            self.status.setText("매핑을 가져올 수 있는 다른 작업 공간이 없습니다.")
            return None
        if source_ws_name is None:
            picked, ok = QInputDialog.getItem(
                self, "다른 작업 공간 매핑 가져오기", "원본 작업 공간:", candidates, 0, False
            )
            if not ok or not picked:
                return None
            source_ws_name = picked
        try:
            src_root = self.root / source_ws_name
            entries = load_mappings_json(src_root)
            if not entries and (src_root / "mart.duckdb").is_file():
                entries = MartEngine(WorkspaceStore(src_root)).load_account_mapping()
            merged = self._align_imported_mappings(entries)
            self._populate_mapping_table(merged, saved=False)
            self.status.setText(f"{self.t(69)}: '{source_ws_name}' 매핑 불러옴 (저장 필요)")
            return merged
        except (ValueError, OSError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))
            return None

    def _recipe_placeholder(self):
        return {
            "ko": "-- 새 레시피 / 프리셋 선택 --",
            "en": "-- Select Saved Recipe / Preset --",
            "pl": "-- Wybierz przepis / szablon --",
        }.get(self.language_code, "-- Select Saved Recipe / Preset --")

    def _populate_recipe_combo(self, select_name=None):
        if not hasattr(self, "recipe_combo") or not self.current:
            return
        recipes = load_recipes(self.current) if (self.current / "recipes.json").is_file() else []
        self.recipe_combo.blockSignals(True)
        self.recipe_combo.clear()
        self.recipe_combo.addItem(self._recipe_placeholder(), None)
        for r in recipes:
            self.recipe_combo.addItem(f"{r.name} [{r.mode}]", r)
        if select_name:
            for idx in range(self.recipe_combo.count()):
                item = self.recipe_combo.itemData(idx)
                if isinstance(item, MartRecipe) and item.name == select_name:
                    self.recipe_combo.setCurrentIndex(idx)
                    break
        self.recipe_combo.blockSignals(False)

    def _apply_recipe_to_form(self, recipe: MartRecipe):
        self.export_name.setText(recipe.name)
        m_idx = self.recipe_mode_combo.findData(recipe.mode)
        if m_idx >= 0:
            self.recipe_mode_combo.setCurrentIndex(m_idx)
        r_idx = self.rollup_level_combo.findData(recipe.rollup_level)
        if r_idx >= 0:
            self.rollup_level_combo.setCurrentIndex(r_idx)
        p_idx = self.unmapped_policy_combo.findData(recipe.unmapped_policy)
        if p_idx >= 0:
            self.unmapped_policy_combo.setCurrentIndex(p_idx)
        self.major_filter_edit.setText(", ".join(recipe.major_categories))
        self.exclude_zero_check.setChecked(bool(recipe.exclude_zero))
        self.apply_sign_check.setChecked(bool(recipe.apply_sign))
        self.group_cols.set_values(list(recipe.group_by))
        if recipe.mode == "wide_raw" and recipe.columns:
            self.export_cols.set_values(list(recipe.columns))
        elif recipe.mode == "wide_rollup" and recipe.include_non_monetary:
            self.export_cols.set_values(list(recipe.include_non_monetary))
        self.start.setText(recipe.period_start or "")
        self.end.setText(recipe.period_end or "")

    def _on_recipe_selected(self, index):
        data = self.recipe_combo.itemData(index)
        if isinstance(data, MartRecipe):
            self._apply_recipe_to_form(data)

    def apply_recipe_preset(self, mode):
        if not self.model:
            self.status.setText(self.t(83))
            return None
        presets = build_default_recipes(self.model, self._current_mapping_entries())
        target = next((r for r in presets if r.mode == mode), None)
        if target:
            self._apply_recipe_to_form(target)
            self.status.setText(f"프리셋 적용됨: {target.name}")
        return target

    def _recipe_from_ui(self):
        mode = self.recipe_mode_combo.currentData() or "wide_raw"
        name = self.export_name.text().strip() or f"mart_{mode}"
        filters = tuple(
            (combo.currentText(), value.text())
            for combo, value in (
                (self.filter_col, self.filter_value),
                (self.filter_col2, self.filter_value2),
            )
            if combo.currentText()
        )
        majors = tuple(x.strip() for x in self.major_filter_edit.text().split(",") if x.strip())
        non_mon_picked = ()
        if mode == "wide_rollup" and self.model:
            measure_cols = {m.column for m in self.model.measures}
            mapping_by_col = {e.source_column: e for e in self._current_mapping_entries()}
            non_mon_picked = tuple(
                c
                for c in self.export_cols.values
                if c in measure_cols
                and mapping_by_col.get(c)
                and mapping_by_col[c].measure_type == "non_monetary"
            )
        return MartRecipe(
            name=name,
            mode=mode,
            group_by=tuple(self.group_cols.values),
            columns=tuple(self.export_cols.values) if mode == "wide_raw" else (),
            major_categories=majors,
            exclude_zero=self.exclude_zero_check.isChecked(),
            apply_sign=self.apply_sign_check.isChecked(),
            include_non_monetary=non_mon_picked,
            rollup_level=self.rollup_level_combo.currentData() or "major",
            unmapped_policy=self.unmapped_policy_combo.currentData() or "fail",
            filters=filters,
            period_start=self.start.text().strip() or None,
            period_end=self.end.text().strip() or None,
        )

    def save_recipe_from_ui(self):
        if not self.current:
            self.status.setText(self.t(83))
            return None
        try:
            recipe = self._recipe_from_ui()
            existing = load_recipes(self.current) if (self.current / "recipes.json").is_file() else []
            updated = [r for r in existing if r.name != recipe.name] + [recipe]
            MartEngine(WorkspaceStore(self.current)).save_recipes(updated)
            self._populate_recipe_combo(select_name=recipe.name)
            self._update_workflow_guide()
            self.status.setText(f"{self.t(69)}: 레시피 '{recipe.name}' 저장됨")
            return recipe
        except (ValueError, OSError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))
            return None

    def run_recipe_export(self):
        if not self.current or not self.model:
            self.status.setText(self.t(83))
            return
        try:
            recipe = self._recipe_from_ui()
        except ValueError as exc:
            self.status.setText(self.t(70) + ": " + str(exc))
            return
        current = self.current

        def work(_):
            manifest = MartEngine(
                WorkspaceStore(current), ResourcePreflight(cancel=self.cancel_event)
            ).create_recipe_export(recipe)
            with (current / "exports" / manifest["candidate"] / "data.csv").open(
                encoding="utf-8-sig", newline=""
            ) as f:
                reader = csv.reader(f)
                return manifest, next(reader), [r for _, r in zip(range(100), reader)]

        def done(result):
            manifest, headers, rows = result
            self.candidate = manifest["candidate"]
            self._table(self.export_preview, headers, rows)
            self._display_recipe_reconciliation(manifest)
            self.refresh_candidates(manifest["revision"])
            self.refresh_capacity()

        self._run(work, done, cancelable=True)

    def run_all_saved_recipes(self):
        if not self.current or not self.model:
            self.status.setText(self.t(83))
            return
        recipes = load_recipes(self.current) if (self.current / "recipes.json").is_file() else []
        if not recipes:
            recipes = build_default_recipes(self.model, self._current_mapping_entries())
        if not recipes:
            self.status.setText("실행할 레시피가 없습니다.")
            return
        current = self.current

        def work(_):
            engine = MartEngine(
                WorkspaceStore(current), ResourcePreflight(cancel=self.cancel_event)
            )
            manifests = [engine.create_recipe_export(r) for r in recipes]
            last = manifests[-1]
            with (current / "exports" / last["candidate"] / "data.csv").open(
                encoding="utf-8-sig", newline=""
            ) as f:
                reader = csv.reader(f)
                return manifests, next(reader), [r for _, r in zip(range(100), reader)]

        def done(result):
            manifests, headers, rows = result
            last = manifests[-1]
            self.candidate = last["candidate"]
            self._table(self.export_preview, headers, rows)
            self._display_recipe_reconciliation(last)
            self.refresh_candidates(last["revision"])
            self.refresh_capacity()
            self.status.setText(f"{self.t(69)}: 총 {len(manifests)}개 레시피 마트 일괄 생성 완료")

        self._run(work, done, cancelable=True)

    def _display_recipe_reconciliation(self, manifest):
        if not hasattr(self, "recipe_reconcile_table"):
            return
        recon = manifest.get("recipe_reconciliation")
        if not recon:
            self.recipe_reconcile_summary.clear()
            self.recipe_reconcile_table.setRowCount(0)
            return
        status_txt = "✅ 원천 DB와 마트 합계 완전 일치 (오차 0)" if recon.get("matches") else "❌ 합계 불일치 확인 필요"
        extra_notes = []
        if recon.get("filtered_zero_rows"):
            extra_notes.append(f"0원 제외 행: {recon['filtered_zero_rows']}건")
        if recon.get("excluded_subtotals"):
            extra_notes.append(f"제외된 원본소계: {len(recon['excluded_subtotals'])}열")
        if recon.get("excluded_non_monetary"):
            extra_notes.append(f"제외된 비금액(수량 등): {len(recon['excluded_non_monetary'])}열")
        note_str = (" | " + " · ".join(extra_notes)) if extra_notes else ""
        self.recipe_reconcile_summary.setText(
            f"{status_txt} — 원천 합계: {recon.get('total_source_amount', '0')} / "
            f"마트 합계: {recon.get('total_exported_amount', '0')} (차이: {recon.get('diff', '0')}){note_str}"
        )
        rows = [
            (
                item["category"],
                item["source_amount"],
                item["exported_amount"],
                item["diff"],
                "일치" if item["matches"] else "불일치",
            )
            for item in recon.get("by_category", [])
        ]
        self._table(self.recipe_reconcile_table, self._reconcile_headers(), rows)
