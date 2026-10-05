"""Dataset Creation & Edit Wizard: 3-step interactive UI.

Step 1: Folder, keyword chip filters, candidate file checklist with header badges.
Step 2: Discovered column table with Auto-Guess, shortcut keys, and compound key uniqueness preview.
Step 3: Summary and advanced options (encoding, delimiter, number format, policies).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from PySide6.QtCore import QEvent, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.dataset_config import DatasetDefinition, DatasetRegistry, get_default_storage_dir
from src.dataset_engine import DatasetEngine, ScannedFile
from src.dataset_profiler import (
    ColumnProfile,
    check_sample_key_uniqueness,
    profile_dataset_sample,
)
from src.qt.dialogs import error_dialog, info_dialog, warning_dialog
from src.qt.tabs.dataset.delegates import RoleDelegate
from src.qt.tabs.dataset.models import ColumnRoleTableModel, DatasetFileTableModel
from src.qt.widgets.badge import StatusBadge
from src.qt.widgets.card import CardWidget
from src.qt.widgets.chip_editor import ChipEditorWidget
from src.qt.widgets.file_picker import FilePickerWidget
from src.qt.widgets.stepper import StepperWidget
from src.ui_components import PALETTE


class DatasetWizardDialog(QDialog):
    """Modern 3-step wizard dialog for registering or modifying a DatasetDefinition."""

    def __init__(
        self,
        dataset: Optional[DatasetDefinition] = None,
        registry: Optional[DatasetRegistry] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._initial_dataset = dataset
        self._registry = registry or DatasetRegistry(get_default_storage_dir())
        self._is_edit = dataset is not None

        title = f"데이터셋 수정: {dataset.name}" if self._is_edit else "새 데이터셋 등록 마법사"
        self.setWindowTitle(title)
        self.resize(960, 720)
        self.setMinimumSize(850, 620)

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # 1. Stepper indicator
        self._stepper = StepperWidget(
            ["파일 선택 및 필터", "컬럼 자동인식 및 역할 지정", "설정 확인 및 저장"]
        )
        self._stepper.stepClicked.connect(self._on_stepper_step_clicked)
        main_layout.addWidget(self._stepper)

        # 2. Stacked pages
        self._stack = QStackedWidget()
        self._page1 = self._build_page1_files()
        self._page2 = self._build_page2_columns()
        self._page3 = self._build_page3_review()

        self._stack.addWidget(self._page1)
        self._stack.addWidget(self._page2)
        self._stack.addWidget(self._page3)
        main_layout.addWidget(self._stack, stretch=1)

        # 3. Bottom navigation buttons
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 0, 0, 0)
        btn_bar.setSpacing(8)

        self._btn_cancel = QPushButton("취소")
        self._btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(self._btn_cancel)

        btn_bar.addStretch()

        self._btn_prev = QPushButton("◀ 이전 단계")
        self._btn_prev.clicked.connect(self._on_prev_clicked)
        self._btn_prev.setEnabled(False)
        btn_bar.addWidget(self._btn_prev)

        self._btn_next = QPushButton("다음 단계 ▶")
        self._btn_next.setProperty("primary", "true")
        self._btn_next.clicked.connect(self._on_next_clicked)
        btn_bar.addWidget(self._btn_next)

        self._btn_save = QPushButton("✓ 데이터셋 저장")
        self._btn_save.setProperty("primary", "true")
        self._btn_save.clicked.connect(self._on_save_clicked)
        self._btn_save.setVisible(False)
        btn_bar.addWidget(self._btn_save)

        main_layout.addLayout(btn_bar)

        # Profiling cached state
        self._current_profiles: List[ColumnProfile] = []
        self._sample_file_path: Optional[Path] = None

        # Load existing dataset if in edit mode
        if self._initial_dataset:
            self._load_dataset_fields(self._initial_dataset)

    # -------------------------------------------------------------------------
    # Page 1: File Selection & Keyword Filters
    # -------------------------------------------------------------------------
    def _build_page1_files(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Folder settings card
        card_dir = CardWidget("1. 데이터셋 경로 및 기본 정보")
        form_dir = QFormLayout()
        form_dir.setSpacing(8)

        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText("예: TV 매출 실적 (구분하기 쉬운 이름을 입력하세요)")
        form_dir.addRow("데이터셋 이름 *", self._edit_name)

        self._picker_input = FilePickerWidget(mode="folder", placeholder="CSV 파일들이 위치한 입력 폴더를 선택하세요")
        self._picker_input.pathChanged.connect(self._on_input_folder_changed)
        form_dir.addRow("입력 폴더 *", self._picker_input)

        self._picker_public = FilePickerWidget(mode="folder", placeholder="정제/배포된 결과물이 저장될 폴더 (미지정 시 기본 폴더)")
        form_dir.addRow("배포 폴더", self._picker_public)

        self._edit_pattern = QLineEdit("*.csv")
        self._edit_pattern.setPlaceholderText("기본값: *.csv")
        self._edit_pattern.textChanged.connect(self._trigger_rescan)
        form_dir.addRow("파일 확장자/패턴", self._edit_pattern)

        card_dir.content_layout.addLayout(form_dir)
        layout.addWidget(card_dir)

        # Filter settings card
        card_filter = CardWidget("2. 파일 필터링 규칙 (키워드/체크 선택)")
        grid_filter = QGridLayout()
        grid_filter.setSpacing(8)

        # Include keywords
        grid_filter.addWidget(QLabel("포함할 키워드:"), 0, 0)
        self._chip_include = ChipEditorWidget(placeholder="파일명에 포함될 단어 입력 후 Enter (예: sales, monthly)")
        self._chip_include.chipsChanged.connect(self._trigger_rescan)
        grid_filter.addWidget(self._chip_include, 0, 1)

        # Keyword match mode (OR vs AND)
        mode_box = QWidget()
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        self._radio_or = QRadioButton("하나라도 포함 (OR)")
        self._radio_and = QRadioButton("모두 포함 (AND)")
        self._radio_or.setChecked(True)
        self._radio_or.toggled.connect(self._trigger_rescan)
        mode_layout.addWidget(self._radio_or)
        mode_layout.addWidget(self._radio_and)
        mode_layout.addStretch()
        grid_filter.addWidget(mode_box, 0, 2)

        # Exclude keywords
        grid_filter.addWidget(QLabel("제외할 키워드:"), 1, 0)
        self._chip_exclude = ChipEditorWidget(placeholder="파일명에 포함되면 제외할 단어 입력 후 Enter (예: temp, test, old)")
        self._chip_exclude.chipsChanged.connect(self._trigger_rescan)
        grid_filter.addWidget(self._chip_exclude, 1, 1, 1, 2)

        # Recursive check
        self._check_recursive = QCheckBox("하위 폴더의 파일도 모두 포함하여 탐색")
        self._check_recursive.toggled.connect(self._trigger_rescan)
        grid_filter.addWidget(self._check_recursive, 2, 1, 1, 2)

        card_filter.content_layout.addLayout(grid_filter)
        layout.addWidget(card_filter)

        # File Candidate Table card
        card_table = CardWidget("3. 대상 파일 선택 (체크 해제된 파일은 처리에서 제외됩니다)")
        tbl_actions = QHBoxLayout()
        self._lbl_file_count = QLabel("발견된 파일: 0개 (선택됨: 0개)")
        self._lbl_file_count.setProperty("subheading", "true")
        tbl_actions.addWidget(self._lbl_file_count)
        tbl_actions.addStretch()

        btn_select_all = QPushButton("전체 선택")
        btn_select_all.clicked.connect(lambda: self._file_model.select_all(True))
        tbl_actions.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("전체 해제")
        btn_deselect_all.clicked.connect(lambda: self._file_model.select_all(False))
        tbl_actions.addWidget(btn_deselect_all)

        btn_rescan = QPushButton("🔄 다시 검색")
        btn_rescan.clicked.connect(self._trigger_rescan)
        tbl_actions.addWidget(btn_rescan)
        card_table.content_layout.addLayout(tbl_actions)

        # File table view
        self._file_table = QTableView()
        self._file_model = DatasetFileTableModel(self)
        self._file_model.selectionChanged.connect(self._update_file_count_label)
        self._file_table.setModel(self._file_model)
        self._file_table.setAlternatingRowColors(True)
        self._file_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._file_table.horizontalHeader().setStretchLastSection(True)
        self._file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._file_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._file_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        card_table.content_layout.addWidget(self._file_table)
        layout.addWidget(card_table, stretch=1)

        return page

    # -------------------------------------------------------------------------
    # Page 2: Discovered Columns & Role Assignment
    # -------------------------------------------------------------------------
    def _build_page2_columns(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Top toolbar
        toolbar_card = CardWidget()
        tb_layout = QHBoxLayout()
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(10)

        btn_auto_guess = QPushButton("✨ 역할 자동 추천 (Auto-Guess)")
        btn_auto_guess.setProperty("primary", "true")
        btn_auto_guess.setToolTip("헤더 이름과 샘플 데이터 1,000행을 분석하여 최적의 기간, 식별키, 수치 역할을 자동 지정합니다.")
        btn_auto_guess.clicked.connect(self._on_auto_guess_clicked)
        tb_layout.addWidget(btn_auto_guess)

        lbl_shortcuts = QLabel("단축키: 행 선택 후 [P: 기간]  [K: 식별키]  [N: 수치]  [I: 제외]")
        lbl_shortcuts.setProperty("muted", "true")
        tb_layout.addWidget(lbl_shortcuts)

        tb_layout.addStretch()

        # Batch actions
        btn_set_key = QPushButton("선택 ➔ 식별키")
        btn_set_key.clicked.connect(lambda: self._set_selected_roles("key"))
        tb_layout.addWidget(btn_set_key)

        btn_set_num = QPushButton("선택 ➔ 수치")
        btn_set_num.clicked.connect(lambda: self._set_selected_roles("numeric"))
        tb_layout.addWidget(btn_set_num)

        btn_set_ign = QPushButton("선택 ➔ 제외")
        btn_set_ign.clicked.connect(lambda: self._set_selected_roles("ignore"))
        tb_layout.addWidget(btn_set_ign)

        toolbar_card.content_layout.addLayout(tb_layout)
        layout.addWidget(toolbar_card)

        # Splitter: Table on left, Real-time Validation on right
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Table
        left_card = CardWidget("인식된 컬럼 목록 (드롭다운으로 역할 변경)")
        self._col_table = QTableView()
        self._col_model = ColumnRoleTableModel(self)
        self._col_model.rolesChanged.connect(self._update_column_diagnostics)
        self._col_table.setModel(self._col_model)
        self._col_table.setAlternatingRowColors(True)
        self._col_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._col_table.setItemDelegateForColumn(3, RoleDelegate(self))
        self._col_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._col_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._col_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._col_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        # Key press handler for P/K/N/I shortcuts
        self._col_table.installEventFilter(self)

        left_card.content_layout.addWidget(self._col_table)
        splitter.addWidget(left_card)

        # Right: Validation & Summary Panel
        right_card = CardWidget("역할 요약 및 실시간 무결성 진단")
        right_layout = QVBoxLayout()
        right_layout.setSpacing(12)

        # Period info
        grp_period = QGroupBox("기준 기간 (필수 1개)")
        lay_p = QVBoxLayout(grp_period)
        self._lbl_period_status = QLabel("지정되지 않음 (미선택 시 진행 불가)")
        self._lbl_period_status.setStyleSheet(f"color: {PALETTE['danger']}; font-weight: bold;")
        lay_p.addWidget(self._lbl_period_status)

        form_p = QFormLayout()
        self._combo_period_format = QComboBox()
        self._combo_period_format.addItems(["YYYY-MM (월별 · 권장)", "YYYYMM (6자리)", "YYYY-WW (주별)", "RAW (원형 유지)"])
        form_p.addRow("기간 형식:", self._combo_period_format)
        lay_p.addLayout(form_p)
        right_layout.addWidget(grp_period)

        # Compound Key info
        grp_key = QGroupBox("복합 식별 키 (다중 선택 가능)")
        lay_k = QVBoxLayout(grp_key)
        self._lbl_key_status = QLabel("선택된 키 없음 (단순 누적 모드)")
        self._lbl_key_status.setProperty("subheading", "true")
        lay_k.addWidget(self._lbl_key_status)

        self._badge_key_uniqueness = StatusBadge("샘플 고유성 검사 대기", variant="neutral")
        lay_k.addWidget(self._badge_key_uniqueness)
        lbl_k_help = QLabel("※ 2개 이상의 키를 지정하면 복합 기본키(Composite Key)로 결합되어 기간 내 중복을 방지합니다.")
        lbl_k_help.setWordWrap(True)
        lbl_k_help.setProperty("caption", "true")
        lay_k.addWidget(lbl_k_help)
        right_layout.addWidget(grp_key)

        # Numeric measures info
        grp_num = QGroupBox("수치 합산 항목 (측도)")
        lay_n = QVBoxLayout(grp_num)
        self._lbl_num_status = QLabel("수치 컬럼: 0개 선택됨")
        lay_n.addWidget(self._lbl_num_status)
        right_layout.addWidget(grp_num)

        right_layout.addStretch()
        right_card.content_layout.addLayout(right_layout)
        splitter.addWidget(right_card)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        return page

    # -------------------------------------------------------------------------
    # Page 3: Review & Advanced Settings
    # -------------------------------------------------------------------------
    def _build_page3_review(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Summary card
        card_sum = CardWidget("데이터셋 구성 최종 확인")
        self._lbl_final_summary = QLabel()
        self._lbl_final_summary.setStyleSheet("font-size: 9.5pt; line-height: 1.5;")
        self._lbl_final_summary.setWordWrap(True)
        card_sum.content_layout.addWidget(self._lbl_final_summary)
        layout.addWidget(card_sum)

        # Advanced options card
        card_adv = CardWidget("고급 적재 및 호환성 옵션")
        form_adv = QFormLayout()
        form_adv.setSpacing(10)

        # Encoding
        self._combo_encoding = QComboBox()
        self._combo_encoding.addItems(["auto (자동 감지 · 권장)", "utf-8", "cp949 (한국어 레거시)", "cp1250 (중앙유럽/폴란드/체코)"])
        form_adv.addRow("파일 인코딩:", self._combo_encoding)

        # Delimiter
        self._combo_delim = QComboBox()
        self._combo_delim.addItems([", (쉼표)", "; (세미콜론)", "\\t (탭)"])
        form_adv.addRow("구분 기호:", self._combo_delim)

        # Number format
        self._combo_number_format = QComboBox()
        self._combo_number_format.addItems([
            "auto (자동 감지 · 권장)",
            "polish (폴란드/유럽식 · 1 234,56 쉼표 소수점)",
            "english (영어식 · 1,234.56 점 소수점)",
        ])
        form_adv.addRow("숫자 표기 방식:", self._combo_number_format)

        # Same period policy
        self._combo_period_policy = QComboBox()
        self._combo_period_policy.addItems([
            "overwrite (해당 기간 기존 데이터 전체 교체 · 권장)",
            "merge (중복 키 기준 병합/갱신)",
        ])
        form_adv.addRow("동일 기간 적재 정책:", self._combo_period_policy)

        # Header policy
        self._combo_header_policy = QComboBox()
        self._combo_header_policy.addItems([
            "block_missing_or_extra (필수열 누락 차단 · 권장)",
            "warn_only (경고 후 이름 기준 적재)",
        ])
        form_adv.addRow("헤더 검사 정책:", self._combo_header_policy)

        card_adv.content_layout.addLayout(form_adv)
        layout.addWidget(card_adv, stretch=1)

        return page

    # -------------------------------------------------------------------------
    # Event Filter for Keyboard Shortcuts (P, K, N, I)
    # -------------------------------------------------------------------------
    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if watched == self._col_table and event.type() == QEvent.Type.KeyPress:
            key_event: QKeyEvent = event  # type: ignore
            key = key_event.key()
            if key == Qt.Key.Key_P:
                self._set_selected_roles("period")
                return True
            elif key == Qt.Key.Key_K:
                self._set_selected_roles("key")
                return True
            elif key == Qt.Key.Key_N:
                self._set_selected_roles("numeric")
                return True
            elif key in (Qt.Key.Key_I, Qt.Key.Key_X, Qt.Key.Key_Delete):
                self._set_selected_roles("ignore")
                return True
        return super().eventFilter(watched, event)

    def _set_selected_roles(self, role_code: str) -> None:
        selection = self._col_table.selectionModel().selectedRows()
        if not selection:
            return
        for idx in selection:
            self._col_model.set_role_for_row(idx.row(), role_code)

    # -------------------------------------------------------------------------
    # Stepper and Navigation
    # -------------------------------------------------------------------------
    def _on_stepper_step_clicked(self, step_idx: int) -> None:
        current = self._stack.currentIndex()
        if step_idx < current:
            self._switch_to_step(step_idx)
        elif step_idx > current:
            # Validate every intervening step before moving forward
            for s in range(current, step_idx):
                if not self._validate_step(s):
                    return
            self._switch_to_step(step_idx)

    def _on_prev_clicked(self) -> None:
        current = self._stack.currentIndex()
        if current > 0:
            self._switch_to_step(current - 1)

    def _on_next_clicked(self) -> None:
        current = self._stack.currentIndex()
        if self._validate_step(current):
            self._switch_to_step(current + 1)

    def _switch_to_step(self, step_idx: int) -> None:
        self._stack.setCurrentIndex(step_idx)
        self._stepper.set_current_step(step_idx)
        self._btn_prev.setEnabled(step_idx > 0)
        is_last = (step_idx == 2)
        self._btn_next.setVisible(not is_last)
        self._btn_save.setVisible(is_last)

        if step_idx == 1:
            self._prepare_page2_columns()
        elif step_idx == 2:
            self._prepare_page3_review()

    def _validate_step(self, step: int) -> bool:
        if step == 0:
            name = self._edit_name.text().strip()
            if not name:
                error_dialog(self, "입력 확인", "데이터셋 이름을 입력해 주세요.")
                self._edit_name.setFocus()
                return False

            inp = self._picker_input.get_path()
            if not inp or not Path(inp).is_dir():
                error_dialog(self, "입력 확인", "유효한 입력 폴더를 선택해 주세요.")
                return False

            inc_files = self._file_model.get_included_files()
            if not inc_files:
                error_dialog(self, "입력 확인", "선택된 대상 파일이 없습니다. 최소 1개 이상의 파일을 선택해야 합니다.")
                return False
            return True

        elif step == 1:
            summary = self._col_model.get_role_summary()
            if not summary["period"]:
                error_dialog(self, "역할 지정 확인", "기준 기간(Period) 컬럼이 지정되지 않았습니다.\n기간 역할을 수행할 컬럼을 반드시 1개 지정해 주세요.")
                return False
            return True

        return True

    # -------------------------------------------------------------------------
    # Scanning and Profiling Logic
    # -------------------------------------------------------------------------
    def _on_input_folder_changed(self, folder_path: str) -> None:
        if not self._edit_name.text().strip() and folder_path:
            p = Path(folder_path)
            self._edit_name.setText(p.name)
        self._trigger_rescan()

    def _trigger_rescan(self) -> None:
        input_dir_str = self._picker_input.get_path()
        if not input_dir_str or not Path(input_dir_str).is_dir():
            self._file_model.set_files([])
            return

        # Build dummy definition for scan_input_folder
        dummy = DatasetDefinition.create_new(
            name=self._edit_name.text().strip() or "Temp",
            input_folder=input_dir_str,
            publish_folder=self._picker_public.get_path() or input_dir_str,
            file_pattern=self._edit_pattern.text().strip() or "*.csv",
            include_keywords=self._chip_include.get_chips(),
            exclude_keywords=self._chip_exclude.get_chips(),
            keyword_mode="and" if self._radio_and.isChecked() else "or",
            include_subfolders=self._check_recursive.isChecked(),
        )
        engine = DatasetEngine(dummy, self._registry)
        scan = engine.scan_input_folder()
        self._file_model.set_files(scan.files)

    def _update_file_count_label(self) -> None:
        total = self._file_model.rowCount()
        included = len(self._file_model.get_included_files())
        self._lbl_file_count.setText(f"발견된 파일: {total:,}개 (선택됨: {included:,}개)")

    def _prepare_page2_columns(self) -> None:
        inc_files = self._file_model.get_included_files()
        if not inc_files:
            return

        # Pick first valid file as representative sample
        target_path = Path(inc_files[0].file_path)
        if self._sample_file_path == target_path and self._current_profiles:
            # Already profiled this exact file
            return

        self._sample_file_path = target_path
        delim = self._get_selected_delimiter() if self._is_edit else None

        try:
            profile_res = profile_dataset_sample(target_path, delimiter=delim, sample_rows=1000)
            profiles = profile_res.columns
            self._current_profiles = profiles

            # Auto-reflect detected delimiter into combo box
            if profile_res.delimiter == ";":
                self._combo_delim.setCurrentIndex(1)
            elif profile_res.delimiter == "\t":
                self._combo_delim.setCurrentIndex(2)
            else:
                self._combo_delim.setCurrentIndex(0)

            init_period = self._initial_dataset.period_column if self._initial_dataset else None
            init_keys = self._initial_dataset.key_columns if self._initial_dataset else None
            init_nums = self._initial_dataset.numeric_columns if self._initial_dataset else None

            self._col_model.set_profiles(
                profiles,
                initial_period=init_period,
                initial_keys=init_keys,
                initial_numerics=init_nums,
            )
        except Exception as e:
            error_dialog(self, "파일 분석 실패", f"샘플 파일 분석 중 오류가 발생했습니다:\n{e}")

    def _on_auto_guess_clicked(self) -> None:
        self._col_model.apply_auto_guess()
        info_dialog(self, "자동 추천 완료", "헤더 이름과 1,000행 샘플 데이터를 분석하여 기간, 키, 수치 역할을 지정했습니다.")

    def _update_column_diagnostics(self) -> None:
        summary = self._col_model.get_role_summary()

        # 1. Period status
        period = summary["period"]
        if period:
            self._lbl_period_status.setText(f"✓ '{period}' (지정됨)")
            self._lbl_period_status.setStyleSheet(f"color: {PALETTE['success']}; font-weight: bold;")
        else:
            self._lbl_period_status.setText("지정되지 않음 (미선택 시 진행 불가)")
            self._lbl_period_status.setStyleSheet(f"color: {PALETTE['danger']}; font-weight: bold;")

        # 2. Key status & compound uniqueness check
        keys = summary["keys"]
        if keys:
            key_str = ", ".join(keys)
            self._lbl_key_status.setText(f"선택된 키 ({len(keys)}개): {key_str}")

            if self._sample_file_path and self._sample_file_path.exists() and period:
                delim = self._get_selected_delimiter()
                dup_cnt, total_sample = check_sample_key_uniqueness(
                    self._sample_file_path, period, keys, delimiter=delim
                )
                if dup_cnt == 0:
                    self._badge_key_uniqueness.set_variant(
                        "success", f"✓ 샘플 {total_sample:,}행에서 중복 없음 (유니크)"
                    )
                else:
                    self._badge_key_uniqueness.set_variant(
                        "warning",
                        f"⚠️ 샘플에서 중복 키 발견 ({dup_cnt:,}개 중복 그룹) - 식별키 추가 권장"
                    )
        else:
            self._lbl_key_status.setText("선택된 키 없음 (단순 누적 모드)")
            self._badge_key_uniqueness.set_variant("neutral", "단순 기간 누적 (중복 키 검사 없음)")

        # 3. Numeric status
        nums = summary["numerics"]
        self._lbl_num_status.setText(f"수치 컬럼 ({len(nums)}개): {', '.join(nums) if nums else '없음'}")

    def _prepare_page3_review(self) -> None:
        summary = self._col_model.get_role_summary()
        inc_files = self._file_model.get_included_files()
        exc_names = self._file_model.get_excluded_file_names()

        lines = [
            f"<b>데이터셋 이름:</b> {self._edit_name.text().strip()}",
            f"<b>입력 폴더:</b> {self._picker_input.get_path()}",
            f"<b>배포 폴더:</b> {self._picker_public.get_path() or '(기본 배포 폴더)'}",
            f"<b>대상 파일:</b> 총 {len(inc_files):,}개 선택됨 (수동 제외: {len(exc_names):,}개)",
            f"<b>기준 기간 컬럼:</b> <font color='{PALETTE['accent']}'><b>{summary['period']}</b></font>",
            f"<b>식별 키 컬럼:</b> {', '.join(summary['keys']) if summary['keys'] else '(미지정)'}",
            f"<b>수치 집계 컬럼:</b> {', '.join(summary['numerics']) if summary['numerics'] else '(미지정)'}",
            f"<b>제외 컬럼:</b> {len(summary['ignores'])}개",
        ]
        self._lbl_final_summary.setText("<br>".join(lines))

    # -------------------------------------------------------------------------
    # Saving Dataset
    # -------------------------------------------------------------------------
    def _on_save_clicked(self) -> None:
        summary = self._col_model.get_role_summary()

        delim = self._get_selected_delimiter()
        enc = self._combo_encoding.currentText().split()[0]
        num_fmt = self._combo_number_format.currentText().split()[0]
        period_policy = self._combo_period_policy.currentText().split()[0]
        header_policy = self._combo_header_policy.currentText().split()[0]

        # Extract baseline header from profiles
        baseline = [p.name for p in self._current_profiles]

        # Build or update DatasetDefinition
        if self._is_edit and self._initial_dataset:
            ds = self._initial_dataset
            ds.name = self._edit_name.text().strip()
            ds.input_folder = self._picker_input.get_path()
            ds.publish_folder = self._picker_public.get_path() or self._picker_input.get_path()
            ds.file_pattern = self._edit_pattern.text().strip() or "*.csv"
            ds.delimiter = delim
            ds.encoding = enc
            ds.period_column = summary["period"]
            ds.key_columns = summary["keys"]
            ds.numeric_columns = summary["numerics"]
            ds.include_keywords = self._chip_include.get_chips()
            ds.exclude_keywords = self._chip_exclude.get_chips()
            ds.keyword_mode = "and" if self._radio_and.isChecked() else "or"
            ds.include_subfolders = self._check_recursive.isChecked()
            ds.excluded_files = self._file_model.get_excluded_file_names()
            ds.baseline_columns = baseline
            ds.number_format = num_fmt
            self._result_dataset = ds
        else:
            self._result_dataset = DatasetDefinition.create_new(
                name=self._edit_name.text().strip(),
                input_folder=self._picker_input.get_path(),
                publish_folder=self._picker_public.get_path() or self._picker_input.get_path(),
                file_pattern=self._edit_pattern.text().strip() or "*.csv",
                delimiter=delim,
                encoding=enc,
                period_column=summary["period"],
                key_columns=summary["keys"],
                numeric_columns=summary["numerics"],
                include_keywords=self._chip_include.get_chips(),
                exclude_keywords=self._chip_exclude.get_chips(),
                keyword_mode="and" if self._radio_and.isChecked() else "or",
                include_subfolders=self._check_recursive.isChecked(),
                excluded_files=self._file_model.get_excluded_file_names(),
                baseline_columns=baseline,
                number_format=num_fmt,
            )
        self.accept()

    def get_dataset(self) -> Optional[DatasetDefinition]:
        return getattr(self, "_result_dataset", None)

    def _get_selected_delimiter(self) -> str:
        txt = self._combo_delim.currentText()
        if ";" in txt:
            return ";"
        elif "\\t" in txt:
            return "\t"
        return ","

    def _load_dataset_fields(self, ds: DatasetDefinition) -> None:
        self._edit_name.setText(ds.name)
        self._picker_input.set_path(ds.input_folder)
        self._picker_public.set_path(ds.publish_folder)
        self._edit_pattern.setText(ds.file_pattern or "*.csv")
        self._chip_include.set_chips(getattr(ds, "include_keywords", None) or [])
        self._chip_exclude.set_chips(getattr(ds, "exclude_keywords", None) or [])

        if getattr(ds, "keyword_mode", "or") == "and":
            self._radio_and.setChecked(True)
        else:
            self._radio_or.setChecked(True)

        self._check_recursive.setChecked(getattr(ds, "include_subfolders", False))

        # Restore delimiter
        delim = getattr(ds, "delimiter", ",")
        if delim == ";":
            self._combo_delim.setCurrentIndex(1)
        elif delim == "\t":
            self._combo_delim.setCurrentIndex(2)
        else:
            self._combo_delim.setCurrentIndex(0)

        # Restore encoding
        enc = getattr(ds, "encoding", "utf-8")
        for i in range(self._combo_encoding.count()):
            if enc.lower() in self._combo_encoding.itemText(i).lower():
                self._combo_encoding.setCurrentIndex(i)
                break

        # Restore number format
        num_fmt = getattr(ds, "number_format", "polish")
        for i in range(self._combo_number_format.count()):
            if num_fmt.lower() in self._combo_number_format.itemText(i).lower():
                self._combo_number_format.setCurrentIndex(i)
                break

        self._trigger_rescan()

        # Restore excluded files in file model
        excluded = getattr(ds, "excluded_files", []) or []
        if excluded:
            self._file_model.set_excluded_files(set(excluded))
