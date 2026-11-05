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
    QHBoxLayout,
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
    Dimension,
    ExportDefinition,
    MartEngine,
    MartModel,
    Measure,
    WorkspaceStore,
)
from src.mart.capacity import ResourcePreflight, available_space, collect_capacity
from src.mart.model import model_from_dict

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
        "지원 범위: 차원 1–4, 업무키 2열, 행 기준 8열, 분할 2열, 속성 12개, 셀 125 UTF-8바이트. 64열/16지표까지 100만 행, 128열/64지표까지 10만 행, 최대 36범위. 추정은 보장이 아닙니다.",
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
        "Supported profile: 1–4 dimensions, 2 business-key columns, 8 row keys, 2 partitions, 12 attributes, 125 UTF-8 bytes per cell. Up to 1 million rows for 64 columns/16 measures, or 100,000 for 128 columns/64 measures, and 36 scopes. Estimates are not guarantees.",
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
        "Obsługiwany profil: 1–4 wymiary, 2 kolumny klucza, 8 kluczy wiersza, 2 podziały, 12 atrybutów, 125 bajtów UTF-8 na komórkę. Do 1 mln wierszy dla 64 kolumn/16 miar, albo 100 tys. dla 128 kolumn/64 miar, maks. 36 zakresów. Szacunek nie jest gwarancją.",
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
    ]
)
_TEXT["en"].extend(
    [
        "Save publication settings",
        "Actual relationships",
        "Only additive sums are supported. Inventory balances and ratios require a separate period or measure design.",
        "Storage Capacity",
    ]
)
_TEXT["pl"].extend(
    [
        "Zapisz ustawienia publikacji",
        "Rzeczywiste relacje",
        "Obsługiwane są tylko sumy addytywne. Salda zapasów i wskaźniki wymagają osobnego projektu okresu lub miary.",
        "Pojemność Pamięci",
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
        self.cancel_event = threading.Event()
        self._busy = False
        self.warning = False
        self.budget_value = None
        self.last_revision = None
        self.offset = 0
        self.translations = []
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
        self.create_button = self.button(1, self.create_workspace)
        side.addWidget(self.create_button)
        row.addLayout(side)
        base.addWidget(self.label(0))
        base.addLayout(row)
        self.tabs = QTabWidget()
        base.addWidget(self.tabs)
        self.tab_labels = []
        model_lay = self.group(self.tabs, 3)
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
        for idx, w in [
            (6, self.fact),
            (7, self.period),
            (8, self.row_keys),
            (9, self.partitions),
            (10, self.excluded),
        ]:
            self.formrow(form, idx, w)
        self.dim_list = QListWidget()
        self.dim_list.setMaximumHeight(80)
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
        # Scroll the longer model page on smaller displays.
        page = self.tabs.widget(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.tabs.removeTab(0)
        scroll.setWidget(page)
        self.tabs.insertTab(0, scroll, "")
        load = self.group(self.tabs, 26)
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
        inspect = self.group(self.tabs, 30)
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

        # 4. Deploy & Publish tab (Grouping Purpose CSV, SharePoint, and Capacity)
        deploy = self.group(self.tabs, 35)
        self.deploy_tabs = QTabWidget()
        deploy.addWidget(self.deploy_tabs)

        ex = self.group(self.deploy_tabs, 42)
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
        for widget in (self.period, self.dim_authority, self.metric_col, self.kind):
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
        for index in range(1, self.tabs.count()):
            page = self.tabs.widget(index)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            self.tabs.removeTab(index)
            scroll.setWidget(page)
            self.tabs.insertTab(index, scroll, "")

    def apply_language(self, lang):
        self.language_code = lang if lang in _TEXT else "en"
        if self.structure.columnCount() == 3:
            self.structure.setHorizontalHeaderLabels(self._structure_headers())
        for obj, index in self.translations:
            obj.setText(self.t(index))
        for tabs, index, key in self.tab_labels:
            tabs.setTabText(index, self.t(key))
        if hasattr(self, "wizard_button"):
            self.wizard_button.setText(
                {
                    "ko": "✨ 스타 스키마 설계 마법사 (Wizard)",
                    "en": "✨ Star Schema Modeling Wizard",
                    "pl": "✨ Kreator Modelowania Gwiazdy",
                }.get(self.language_code, "✨ Star Schema Modeling Wizard")
            )
        self.profile_detail.setText(
            {
                "ko": "범위당 10만 행, 차원당 고유 키 1만 개(소형 단일 차원 10만 개), 정밀도 18 이하, 기존 DB 512 MiB 이하. 그룹 열 8개와 등가 필터 2개까지 지원합니다.",
                "en": "Up to 100,000 rows per scope, 10,000 keys per dimension (100,000 for the small single-dimension profile), precision 18 and a 512 MiB existing DB. Up to eight grouping columns and two equality filters.",
                "pl": "Do 100 000 wierszy na zakres, 10 000 kluczy na wymiar (100 000 dla małego profilu), precyzja 18 i istniejąca baza do 512 MiB. Do ośmiu kolumn grupowania i dwóch filtrów równości.",
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
            return
        try:
            path = self.current / "model.json"
            if path.exists():
                self.model = model_from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                self.restore_model()
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
                self.restore_model(aligned_model)
                self.save_model()
                # Rollback UI forms if save failed (e.g. locked DB or validation error)
                if self.model != aligned_model and prev_model is not None:
                    self.restore_model(prev_model)

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
        self.metrics.append(m)
        self._update_lists()

    def remove_metric(self):
        self._reset_declarations()
        i = self.metric_list.currentRow()
        if i >= 0:
            self.metrics.pop(i)
            self._update_lists()

    def _update_lists(self):
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
        self.metric_list.clear()
        self.metric_list.addItems(
            [m.name + " ← " + m.column + " (" + m.unit + ", SUM)" for m in self.metrics]
        )

    def save_model(self):
        self._reset_declarations()
        if not self.current:
            self.status.setText(self.t(83))
            return
        source_delim = getattr(self, "_wizard_source_delim", None) or getattr(self, "_detected_source_delim", None) or (getattr(self.model, "source_delimiter", ",") if self.model else ",")
        source_enc = getattr(self, "_wizard_source_enc", None) or getattr(self, "_detected_source_enc", None) or (getattr(self.model, "source_encoding", "utf-8-sig") if self.model else "utf-8-sig")
        self._wizard_source_delim = None
        self._wizard_source_enc = None
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
            self._scope_fields(model)
            self.status.setText(self.t(69))
        except (ValueError, OSError) as exc:
            self.status.setText(self.t(70) + ": " + str(exc))

    def restore_model(self, m=None):
        if m is None:
            m = self.model
        if m is None:
            return
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
        self.dims = list(m.dimensions)
        self.metrics = list(m.measures)
        self._update_lists()
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
        for col in (model.period_column, *model.partition_columns):
            edit = QLineEdit()
            edit.textChanged.connect(self._reset_declarations)
            self.scope_inputs[col] = edit
            self.scope_form.addRow(col, edit)

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
        self._run(
            lambda _: MartEngine(
                WorkspaceStore(current), ResourcePreflight(cancel=cancel)
            ).load_snapshot(
                model,
                files,
                scope,
                [p.name for p in files],
                complete=True,
                allow_empty=empty,
            ),
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
        self.refresh_candidates(result["revision"])
        self.refresh_capacity()

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
