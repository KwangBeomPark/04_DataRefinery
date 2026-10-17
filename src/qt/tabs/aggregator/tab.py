"""PySide6 implementation for '데이터 집계·슬라이서' (Data Aggregator)."""

from __future__ import annotations

import os
from dataclasses import replace
from html import escape
from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QScrollArea,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.aggregator_fields import (
    AggregatorFieldState,
)
from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.data_aggregator import (
    AggregationResult,
    AggregationSpec,
    AggregationNumericError,
    aggregate_dataset,
    inspect_dataset_schema,
    preview_aggregation,
    validate_aggregation_output_path,
    validate_aggregation_output_name,
)
from src.file_reveal import open_containing_folder
from src.i18n import _UI_TEXT
from src.preset_manager import (
    AggregationPreset,
    delete_preset,
    list_presets,
    preset_exists,
    save_preset,
)
from src.qt.dialogs import confirm_dialog, error_dialog, info_dialog, warning_dialog
from src.qt.jobs import create_qt_job_runner
from src.qt.tabs.aggregator.dialogs import (
    FilterRuleDialog,
    FormulaRuleDialog,
    GroupRuleDialog,
)
from src.qt.widgets.card import CardWidget
from src.qt.widgets.file_picker import FilePickerWidget
from src.ui_components import PALETTE


class _FieldSplitter(QSplitter):
    """Prefer compact panes in a scroll area, while expanding in a tall viewport."""

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(self.minimumHeight())
        return size


class _SamplePreviewDialog(QDialog):
    """Modal dialog displaying sampled aggregation results."""

    def __init__(
        self,
        headers: List[str],
        rows: List[List[Any]],
        parent: Optional[QWidget] = None,
        missing_note: str = "",
        language_code: str = "ko",
    ):
        super().__init__(parent)
        t = _UI_TEXT.get(language_code, _UI_TEXT["ko"])
        self.setWindowTitle(t.get("agg_preview_title", "미리보기 · 샘플 집계 결과"))
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        lbl_note = QLabel(t.get("agg_preview_note", "") + missing_note)
        lbl_note.setWordWrap(True)
        lbl_note.setProperty("caption", "true")
        layout.addWidget(lbl_note)

        table = QTableView()
        model = _GenericTableModel(headers, rows, self)
        table.setModel(model)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        btn_close = QPushButton(t.get("agg_preview_close", "닫기"))
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)


class _GenericTableModel(QAbstractTableModel):
    def __init__(self, headers: List[str], rows: List[List[Any]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._headers = headers
        self._rows = rows

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            val = self._rows[index.row()][index.column()]
            return str(val) if val is not None else ""
        return None


class AggregatorTab(QWidget):
    """Large data aggregation and slice engine."""

    def __init__(
        self,
        language_code: str = "ko",
        job_runner: Optional[BackgroundJobRunner] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.language_code = language_code
        self.job_runner = job_runner or create_qt_job_runner()
        self.state = AggregatorFieldState()

        self._last_result: Optional[Any] = None
        self._last_spec: Optional[AggregationSpec] = None
        self._is_processing = False

        self._init_ui()
        self.apply_language(self.language_code)

    def _ui(self, key: str) -> str:
        return _UI_TEXT.get(self.language_code, _UI_TEXT["ko"]).get(key, key)

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        main_layout = QVBoxLayout(content)
        self._scroll.setWidget(content)
        outer.addWidget(self._scroll)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Step Guide Banner
        self.lbl_step_guide = QLabel()
        self.lbl_step_guide.setWordWrap(True)
        self.lbl_step_guide.setStyleSheet(
            f"background-color: {PALETTE['surface_alt']}; border: 1px solid {PALETTE['border_strong']}; "
            f"border-radius: 6px; padding: 10px 12px; color: {PALETTE['navy']}; font-size: 9.5pt;"
        )
        main_layout.addWidget(self.lbl_step_guide)

        # 1. Top Bar: Source file, Encoding, Presets
        self.top_card = CardWidget("1. 원본 CSV 파일 및 숫자 표기 선택")
        top_lay = QHBoxLayout()
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(10)

        self.lbl_source = QLabel("소스:")
        top_lay.addWidget(self.lbl_source)
        self.picker_source = FilePickerWidget(
            mode="file",
            filter_pattern="CSV Files (*.csv);;All Files (*.*)",
            placeholder="대용량 CSV 파일을 선택하세요",
        )
        self.picker_source.pathChanged.connect(self._on_source_file_changed)
        top_lay.addWidget(self.picker_source, stretch=1)

        self.lbl_encoding = QLabel("인코딩:")
        top_lay.addWidget(self.lbl_encoding)
        self.combo_encoding = QComboBox()
        self.combo_encoding.addItems(["auto", "utf-8", "cp949", "cp1250", "iso-8859-2"])
        top_lay.addWidget(self.combo_encoding)

        # Preset Menu Button
        self.btn_preset = QPushButton("프리셋 ▾")
        self.menu_preset = QMenu(self)
        self.act_preset_load = self.menu_preset.addAction("불러오기...", self._on_load_preset)
        self.act_preset_save = self.menu_preset.addAction("저장...", self._on_save_preset)
        self.menu_preset.addSeparator()
        self.act_preset_export = self.menu_preset.addAction("내보내기...", self._on_export_preset)
        self.act_preset_import = self.menu_preset.addAction("가져오기...", self._on_import_preset)
        self.act_preset_delete = self.menu_preset.addAction("삭제...", self._on_delete_preset)
        self.btn_preset.setMenu(self.menu_preset)
        top_lay.addWidget(self.btn_preset)

        self.btn_undo = QPushButton("↶ 되돌리기")
        self.btn_undo.clicked.connect(self._on_undo)
        top_lay.addWidget(self.btn_undo)

        self.top_card.content_layout.addLayout(top_lay)
        numeric_lay = QHBoxLayout()
        self.lbl_number_mode = QLabel()
        numeric_lay.addWidget(self.lbl_number_mode)
        self.combo_number_mode = QComboBox()
        self.combo_number_mode.addItem("", "English")
        self.combo_number_mode.addItem("", "Polish")
        numeric_lay.addWidget(self.combo_number_mode)
        self.lbl_numeric_policy = QLabel()
        self.lbl_numeric_policy.setWordWrap(True)
        self.lbl_numeric_policy.setProperty("caption", "true")
        numeric_lay.addWidget(self.lbl_numeric_policy, stretch=1)
        self.top_card.content_layout.addLayout(numeric_lay)
        main_layout.addWidget(self.top_card)

        # 2. Main Middle Splitter: Left (Source Columns) | Right (Rules & Pivot Areas)
        splitter = _FieldSplitter(Qt.Orientation.Horizontal)
        splitter.setMinimumHeight(300)

        # Left: Source Columns Explorer
        self.card_sources = CardWidget("2-A. 원본 컬럼 탐색기 (더블클릭하여 오른쪽에 추가)")
        src_lay = QVBoxLayout()
        src_lay.setSpacing(6)

        # Search box
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("컬럼 이름을 입력해 찾기…")
        self.edit_search.textChanged.connect(self._filter_source_columns)
        src_lay.addWidget(self.edit_search)

        self.lbl_dims_header = QLabel("📁 차원/키 (더블클릭: 행 그룹)")
        src_lay.addWidget(self.lbl_dims_header)
        self.list_dims = QListWidget()
        self.list_dims.setMinimumHeight(45)
        self.list_dims.itemDoubleClicked.connect(self._on_dim_double_clicked)
        src_lay.addWidget(self.list_dims, stretch=1)

        self.lbl_measures_header = QLabel("📊 수치/값 (더블클릭: 값)")
        src_lay.addWidget(self.lbl_measures_header)
        self.list_measures = QListWidget()
        self.list_measures.setMinimumHeight(45)
        self.list_measures.itemDoubleClicked.connect(self._on_measure_double_clicked)
        src_lay.addWidget(self.list_measures, stretch=1)

        self.card_sources.content_layout.addLayout(src_lay)
        splitter.addWidget(self.card_sources)

        # Right: Aggregation Rules & Pivot Target Areas
        self.card_rules = CardWidget("2-B. 집계 규칙 설정 (더블클릭하여 제거)")
        rules_lay = QVBoxLayout()
        rules_lay.setSpacing(8)

        # Rule action buttons
        rule_btns = QHBoxLayout()
        rule_btns.setSpacing(6)
        self.btn_grp = QPushButton("∑ 묶기")
        self.btn_grp.clicked.connect(self._on_add_group_rule)
        rule_btns.addWidget(self.btn_grp)

        self.btn_formula = QPushButton("% 비율식")
        self.btn_formula.clicked.connect(self._on_add_formula_rule)
        rule_btns.addWidget(self.btn_formula)

        self.btn_filter = QPushButton("+ 필터")
        self.btn_filter.clicked.connect(self._on_add_filter_rule)
        rule_btns.addWidget(self.btn_filter)
        for button in (self.btn_grp, self.btn_formula, self.btn_filter):
            button.setMinimumHeight(button.sizeHint().height())
        rule_btns.addStretch()
        rules_lay.addLayout(rule_btns)

        # Area 1: Rows
        self.lbl_rows_area = QLabel("1. 행 그룹 (Rows):")
        rules_lay.addWidget(self.lbl_rows_area)
        self.list_rows = QListWidget()
        self.list_rows.setMinimumHeight(45)
        self.list_rows.itemDoubleClicked.connect(self._on_row_double_clicked)
        rules_lay.addWidget(self.list_rows, stretch=1)

        # Area 2: Values
        self.lbl_values_area = QLabel("2. 값 (Values - 더블클릭 시 제거):")
        rules_lay.addWidget(self.lbl_values_area)
        self.list_values = QListWidget()
        self.list_values.setMinimumHeight(45)
        self.list_values.itemDoubleClicked.connect(self._on_value_double_clicked)
        rules_lay.addWidget(self.list_values, stretch=1)

        # Area 3: Filters
        self.lbl_filters_area = QLabel("조건 필터 (더블클릭 시 제거):")
        rules_lay.addWidget(self.lbl_filters_area)
        self.list_filters = QListWidget()
        self.list_filters.setMinimumHeight(45)
        self.list_filters.itemDoubleClicked.connect(self._on_filter_double_clicked)
        rules_lay.addWidget(self.list_filters, stretch=1)

        self.card_rules.content_layout.addLayout(rules_lay)
        splitter.addWidget(self.card_rules)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter, stretch=1)

        # 3. Bottom Execution Card
        self.bot_card = CardWidget("3. 저장 경로 설정 및 집계 실행")
        form_bot = QFormLayout()
        form_bot.setSpacing(8)

        self.lbl_save_folder = QLabel("저장 폴더:")
        self.picker_output = FilePickerWidget(
            mode="folder",
            placeholder="저장할 폴더를 선택하세요 (비워두면 원본과 같은 폴더)",
        )
        form_bot.addRow(self.lbl_save_folder, self.picker_output)

        self.lbl_save_name = QLabel("저장 파일명:")
        self.edit_output_name = QLineEdit("aggregated_output.csv")
        form_bot.addRow(self.lbl_save_name, self.edit_output_name)

        self.check_rollup = QCheckBox("연간 롤업(Annual Roll-up) 적용 (기준년월 컬럼 필요)")
        form_bot.addRow("", self.check_rollup)

        self.bot_card.content_layout.addLayout(form_bot)

        # Actions
        act_box = QHBoxLayout()
        act_box.setSpacing(8)
        self.btn_preview = QPushButton("🔍 미리보기 (샘플)")
        self.btn_preview.clicked.connect(self._on_preview_clicked)
        act_box.addWidget(self.btn_preview)

        self.btn_run = QPushButton("★ 데이터 집계 및 저장하기")
        self.btn_run.setProperty("primary", "true")
        self.btn_run.clicked.connect(self._on_run_clicked)
        act_box.addWidget(self.btn_run)

        self.btn_open_folder = QPushButton("📂 폴더 열기")
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        self.btn_open_folder.setVisible(False)
        act_box.addWidget(self.btn_open_folder)

        self.btn_open_file = QPushButton("📄 파일 열기")
        self.btn_open_file.clicked.connect(self._on_open_file)
        self.btn_open_file.setVisible(False)
        act_box.addWidget(self.btn_open_file)

        act_box.addStretch()
        self.bot_card.content_layout.addLayout(act_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.bot_card.content_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("대용량 CSV 파일을 선택하여 컬럼을 분석하세요.")
        self.lbl_status.setWordWrap(True)
        self.bot_card.content_layout.addWidget(self.lbl_status)

        main_layout.addWidget(self.bot_card)

        self._update_step_gating()

    def _update_step_gating(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        src_path = self.picker_source.get_path()
        has_file = bool(src_path and os.path.isfile(src_path))
        has_rules = bool(self.state.group_keys and self.state.values)

        self.card_sources.setEnabled(has_file)
        self.card_rules.setEnabled(has_file)
        self.picker_output.setEnabled(has_file)
        self.edit_output_name.setEnabled(has_file)
        self.check_rollup.setEnabled(has_file)
        self.btn_preview.setEnabled(has_file and has_rules and not self._is_processing)
        if not self._is_processing:
            self.btn_run.setEnabled(has_file and has_rules)

        if not has_file:
            self.lbl_step_guide.setText(t.get("agg_step_guide_1", ""))
        elif not has_rules:
            self.lbl_step_guide.setText(t.get("agg_step_guide_2", ""))
        else:
            self.lbl_step_guide.setText(t.get("agg_step_guide_3", ""))

    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        t = _UI_TEXT.get(lang_code, _UI_TEXT["ko"])

        self.top_card.set_title(t.get("agg_step1_card", "1. 원본 CSV 파일 및 숫자 표기 선택"))
        self.lbl_source.setText(t.get("agg_file_label", "소스") + ":")
        self.picker_source.set_placeholder_text(t.get("agg_source_placeholder", ""))
        self.picker_source.set_browse_text(t.get("agg_browse", "찾아보기…"))
        self.picker_source.set_dialog_title(t.get("agg_file_dialog_title", "파일 선택"))

        self.lbl_encoding.setText(t.get("agg_encoding_label", "인코딩:"))
        self.combo_encoding.setItemText(0, f"auto ({t.get('agg_encoding_auto', '자동')})")

        self.btn_preset.setText(t["agg_preset_menu"])
        self.act_preset_load.setText(t.get("agg_preset_load", "불러오기") + "...")
        self.act_preset_save.setText(t.get("agg_preset_save", "저장") + "...")
        self.act_preset_export.setText(t.get("agg_preset_export", "내보내기") + "...")
        self.act_preset_import.setText(t.get("agg_preset_import", "가져오기") + "...")
        self.act_preset_delete.setText(t.get("agg_preset_delete", "삭제") + "...")

        self.btn_undo.setText(t["agg_undo"])
        self.lbl_number_mode.setText(t["agg_number_mode"])
        self.combo_number_mode.setItemText(0, t["agg_number_english"])
        self.combo_number_mode.setItemText(1, t["agg_number_polish"])
        self.lbl_numeric_policy.setText(t["agg_numeric_policy"])

        self.card_sources.set_title(t.get("agg_step2_left_card", t.get("agg_source_card", "").strip()))
        self.edit_search.setPlaceholderText(t["agg_search_placeholder"])
        self.lbl_dims_header.setText(t.get("agg_dim_header", "📁 차원/키"))
        self.lbl_measures_header.setText(t.get("agg_measure_header", "📊 수치/값"))

        self.card_rules.set_title(t.get("agg_step2_right_card", t.get("agg_rules_card", "").strip()))
        self.btn_grp.setText(t.get("agg_btn_group_rule", "∑ 묶기"))
        self.btn_formula.setText(t.get("agg_btn_formula_rule", "% 비율식"))
        self.btn_filter.setText(t.get("agg_btn_add_filter", "+ 필터"))
        self.lbl_rows_area.setText(t.get("agg_rows_label", t.get("agg_rows_box", "1. 행 그룹")))
        self.lbl_values_area.setText(t.get("agg_values_label", t.get("agg_values_box", "2. 값")))
        self.lbl_filters_area.setText(t.get("agg_filters_label", t.get("agg_filters_box", "필터")))

        self.bot_card.set_title(t.get("agg_step3_card", "3. 저장 경로 설정 및 집계 실행"))
        self.lbl_save_folder.setText(t.get("agg_save_folder_label", t.get("agg_output_dir", "폴더") + ":"))
        self.picker_output.set_placeholder_text(t.get("agg_save_folder_placeholder", ""))
        self.picker_output.set_browse_text(t.get("agg_browse_folder", "찾아보기…"))
        self.picker_output.set_dialog_title(t.get("agg_msg_choose_folder", "폴더 선택"))
        self.lbl_save_name.setText(t.get("agg_save_name_label", t.get("agg_output_name", "파일명") + ":"))
        self.check_rollup.setText(t.get("agg_rollup_check", "연간 롤업 적용"))

        self.btn_preview.setText(t["agg_preview"])
        self.btn_run.setText(t["agg_run"])
        self.btn_open_folder.setText(t.get("agg_open_folder", "📂 폴더 열기"))
        self.btn_open_file.setText(t.get("agg_open_file", "📄 파일 열기"))

        if not self._last_result:
            src_path = self.picker_source.get_path()
            if src_path and os.path.isfile(src_path):
                total_cols = len(self.state.dimension_pool) + len(self.state.measure_pool) + len(self.state.group_keys) + len(self.state.values)
                self.lbl_status.setText(t.get("agg_msg_schema_done", "").format(count=total_cols))
            else:
                self.lbl_status.setText(t["agg_initial_result"])
        else:
            self._render_result(self._last_result, self._last_spec)

        self._update_step_gating()

    # -------------------------------------------------------------------------
    # Schema Loading
    # -------------------------------------------------------------------------
    def _on_source_file_changed(self, file_path: str) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        if not file_path or not os.path.isfile(file_path):
            self.state.reset()
            self._sync_ui_lists()
            return

        enc = self.combo_encoding.currentText().split()[0]
        try:
            schema = inspect_dataset_schema(file_path, encoding=enc if enc != "auto" else None)
            self._detected_delimiter = schema.delimiter
            self.state.load_schema(
                dimensions=schema.dimension_candidates,
                measures=schema.measure_candidates,
                month_column=schema.detected_month_column,
                columns=schema.columns,
            )
            # Default output folder to same folder as source
            self.picker_output.set_path(str(Path(file_path).parent.resolve()))
            stem = Path(file_path).stem
            self.edit_output_name.setText(f"{stem}_aggregated.csv")
            self._sync_ui_lists()
            self.lbl_status.setText(t.get("agg_msg_schema_done", "컬럼 감지 완료 ({count}개 열)").format(count=len(schema.columns)))
        except Exception as e:
            error_dialog(
                self,
                t.get("agg_msg_schema_error_title", "파일 분석 오류"),
                t.get("agg_msg_schema_error", "{error}").format(error=e),
            )
            self._update_step_gating()

    def _sync_ui_lists(self) -> None:
        query = self.edit_search.text().strip().lower()

        # 1. Dimensions pool
        self.list_dims.clear()
        for f in self.state.dimension_pool:
            if not query or query in f.name.lower():
                self.list_dims.addItem(QListWidgetItem(f.name))

        # 2. Measures pool
        self.list_measures.clear()
        for f in self.state.measure_pool:
            if not query or query in f.name.lower():
                tag = f" [{f.kind}]" if f.is_derived else ""
                self.list_measures.addItem(QListWidgetItem(f.name + tag))

        # 3. Rows area
        self.list_rows.clear()
        for k in self.state.group_keys:
            self.list_rows.addItem(QListWidgetItem(k))

        # 4. Values area
        self.list_values.clear()
        for v in self.state.values:
            fn = self.state.measure_functions.get(v, "sum")
            self.list_values.addItem(QListWidgetItem(f"{v} ({fn})"))

        # 5. Filters area
        self.list_filters.clear()
        for flt in self.state.filters:
            self.list_filters.addItem(QListWidgetItem(f"{flt.column} {flt.operator} {flt.value}"))

        self._update_step_gating()

    def _filter_source_columns(self) -> None:
        self._sync_ui_lists()

    # -------------------------------------------------------------------------
    # Drag/Double-Click Placement
    # -------------------------------------------------------------------------
    def _on_dim_double_clicked(self, item: QListWidgetItem) -> None:
        name = item.text()
        self.state.snapshot()
        self.state.place_group_key(name)
        self._sync_ui_lists()

    def _on_measure_double_clicked(self, item: QListWidgetItem) -> None:
        name = item.text().split(" [")[0]
        self.state.snapshot()
        self.state.place_value(name)
        self._sync_ui_lists()

    def _on_row_double_clicked(self, item: QListWidgetItem) -> None:
        name = item.text()
        self.state.snapshot()
        self.state.unplace(name)
        self._sync_ui_lists()

    def _on_value_double_clicked(self, item: QListWidgetItem) -> None:
        name = item.text().split(" (")[0]
        self.state.snapshot()
        self.state.unplace(name)
        self._sync_ui_lists()

    def _on_filter_double_clicked(self, item: QListWidgetItem) -> None:
        idx = self.list_filters.row(item)
        if 0 <= idx < len(self.state.filters):
            self.state.snapshot()
            del self.state.filters[idx]
            self._sync_ui_lists()

    def _on_undo(self) -> None:
        if self.state.undo():
            self._sync_ui_lists()

    # -------------------------------------------------------------------------
    # Rules
    # -------------------------------------------------------------------------
    def _on_add_group_rule(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        measures = [f.name for f in self.state.measure_pool] + list(self.state.values)
        if not measures:
            warning_dialog(self, t.get("agg_msg_notice", "알림"), t.get("agg_msg_select_file_first", ""))
            return

        existing = [f.name for f in self.state.dimension_pool + self.state.measure_pool] + self.state.group_keys + self.state.values
        dlg = GroupRuleDialog(
            available_measures=sorted(list(set(measures))),
            existing_names=existing,
            parent=self,
            language_code=self.language_code,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.rule:
            self.state.snapshot()
            self.state.add_column_group(dlg.rule)
            self._sync_ui_lists()

    def _on_add_formula_rule(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        measures = [f.name for f in self.state.measure_pool] + list(self.state.values)
        if not measures:
            warning_dialog(self, t.get("agg_msg_notice", "알림"), t.get("agg_msg_select_file_first", ""))
            return

        existing = [f.name for f in self.state.dimension_pool + self.state.measure_pool] + self.state.group_keys + self.state.values
        dlg = FormulaRuleDialog(
            available_measures=sorted(list(set(measures))),
            existing_names=existing,
            parent=self,
            language_code=self.language_code,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.rule:
            self.state.snapshot()
            self.state.add_derived_formula(dlg.rule)
            self._sync_ui_lists()

    def _on_add_filter_rule(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        all_cols = [f.name for f in self.state.dimension_pool + self.state.measure_pool] + self.state.group_keys + self.state.values
        if not all_cols:
            warning_dialog(self, t.get("agg_msg_notice", "알림"), t.get("agg_msg_select_file_first", ""))
            return

        dlg = FilterRuleDialog(
            available_columns=sorted(list(set(all_cols))),
            parent=self,
            language_code=self.language_code,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.condition:
            self.state.snapshot()
            self.state.filters.append(dlg.condition)
            self._sync_ui_lists()

    # -------------------------------------------------------------------------
    # Presets
    # -------------------------------------------------------------------------
    def _on_load_preset(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        presets = list_presets()
        if not presets:
            info_dialog(self, t.get("agg_msg_notice", "알림"), t.get("agg_msg_preset_missing", ""))
            return

        names = [p.name for p in presets]
        chosen_name, ok = QInputDialog.getItem(
            self,
            t.get("agg_preset_load", "불러오기"),
            t.get("agg_preset_label", "프리셋:"),
            names,
            0,
            False,
        )
        if not ok or not chosen_name:
            return

        preset = next((p for p in presets if p.name == chosen_name), None)
        if not preset:
            return

        self.state.apply_configuration(
            group_keys=preset.group_by_keys,
            measure_sums=preset.measure_sums,
            column_groups=preset.column_groups,
            derived_formulas=preset.derived_formulas,
            filters=preset.filters,
            value_order=preset.value_order,
            constant_columns=preset.constant_columns,
            measure_functions=preset.measure_functions,
        )
        self.check_rollup.setChecked(preset.rollup_annual)
        if preset.number_mode is not None:
            self.combo_number_mode.setCurrentIndex(self.combo_number_mode.findData(preset.number_mode))
        self._sync_ui_lists()
        info_dialog(self, t.get("agg_msg_notice", "알림"), t.get("agg_msg_preset_applied", "").format(name=preset.name))

    def _on_save_preset(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        name, ok = QInputDialog.getText(
            self,
            t.get("agg_msg_preset_save_title", "프리셋 저장"),
            t.get("agg_msg_preset_save_prompt", ""),
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        if preset_exists(name):
            if not confirm_dialog(
                self,
                t.get("agg_msg_preset_overwrite_title", "프리셋 덮어쓰기 확인"),
                t.get("agg_msg_preset_overwrite", "").format(name=name),
            ):
                return

        preset = AggregationPreset(
            name=name,
            group_by_keys=list(self.state.group_keys),
            measure_sums=list(self.state.values),
            column_groups=list(self.state.column_groups),
            derived_formulas=list(self.state.derived_formulas),
            filters=list(self.state.filters),
            rollup_annual=self.check_rollup.isChecked(),
            month_column=self.state.month_column,
            annual_column_name=self.state.annual_column_name,
            constant_columns=dict(self.state.constant_columns),
            measure_functions=dict(self.state.measure_functions),
            number_mode=self.combo_number_mode.currentData(),
        )
        save_preset(preset)
        info_dialog(
            self,
            t.get("agg_msg_preset_saved_title", "저장 완료"),
            t.get("agg_msg_preset_saved", "").format(name=name),
        )

    def _on_export_preset(self) -> None:
        pass

    def _on_import_preset(self) -> None:
        pass

    def _on_delete_preset(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        presets = list_presets()
        if not presets:
            info_dialog(self, t.get("agg_msg_preset_delete_title", "프리셋 삭제"), t.get("agg_msg_preset_missing", ""))
            return

        names = [p.name for p in presets]
        chosen_name, ok = QInputDialog.getItem(
            self,
            t.get("agg_msg_preset_delete_title", "프리셋 삭제"),
            t.get("agg_preset_label", "프리셋:"),
            names,
            0,
            False,
        )
        if not ok or not chosen_name:
            return

        if confirm_dialog(
            self,
            t.get("agg_msg_preset_delete_title", "프리셋 삭제"),
            t.get("agg_msg_preset_delete", "").format(name=chosen_name),
        ):
            delete_preset(chosen_name)
            info_dialog(self, t.get("agg_msg_preset_delete_title", "프리셋 삭제"), chosen_name)

    # -------------------------------------------------------------------------
    # Execution & Preview
    # -------------------------------------------------------------------------
    def _build_spec(self) -> Optional[AggregationSpec]:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        src_path = self.picker_source.get_path()
        if not src_path or not os.path.isfile(src_path):
            error_dialog(self, t.get("agg_msg_need_file_title", "파일 선택 필요"), t.get("agg_msg_need_file", ""))
            return None

        if not self.state.group_keys:
            error_dialog(self, t.get("agg_msg_config_title", "설정 확인"), t.get("agg_msg_need_group_key", ""))
            return None

        if not self.state.values:
            error_dialog(self, t.get("agg_msg_config_title", "설정 확인"), t.get("agg_msg_need_measure", ""))
            return None

        out_dir = self.picker_output.get_path() or str(Path(src_path).parent.resolve())
        out_name = self.edit_output_name.text().strip() or "aggregated.csv"
        out_path = str(Path(out_dir) / out_name)
        try:
            validate_aggregation_output_name(out_name)
            validate_aggregation_output_path(src_path, out_path)
        except Exception as error:
            error_dialog(self, t.get("agg_msg_config_title", "설정 확인"), str(error))
            return None
        ext = Path(out_path).suffix.lower().lstrip(".")
        out_format = "xlsx" if ext == "xlsx" else "csv"

        enc = self.combo_encoding.currentText().split()[0]

        return AggregationSpec(
            file_path=src_path,
            group_by_keys=list(self.state.group_keys),
            measure_sums=list(self.state.values),
            column_groups=list(self.state.column_groups),
            derived_formulas=list(self.state.derived_formulas),
            filters=list(self.state.filters),
            rollup_annual=self.check_rollup.isChecked(),
            month_column=self.state.month_column,
            annual_column_name=self.state.annual_column_name,
            delimiter=getattr(self, "_detected_delimiter", None),
            encoding=enc if enc != "auto" else None,
            number_mode=self.combo_number_mode.currentData(),
            strict_numeric=True,
            allow_output_overwrite=False,
            output_path=out_path,
            output_format=out_format,
            constant_columns=dict(self.state.constant_columns),
            measure_functions=dict(self.state.measure_functions),
        )

    def _on_preview_clicked(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        spec = self._build_spec()
        if not spec:
            return

        try:
            df, _ = preview_aggregation(spec, sample_rows=2000)
            headers = list(df.columns)
            rows = df.values.tolist()
            missing = df.attrs.get("missing_numbers_by_column", {})
            note = ("\n" + ", ".join(f"{col}: {count:,}" for col, count in missing.items())) if missing else ""
            dlg = _SamplePreviewDialog(headers, rows, parent=self, missing_note=note, language_code=self.language_code)
            dlg.exec()
        except Exception as e:
            error_dialog(self, t.get("agg_msg_preview_error_title", "미리보기 오류"), self._aggregation_error_text(e))

    def _aggregation_error_text(self, error: Exception) -> str:
        if isinstance(error, AggregationNumericError):
            return self._ui("agg_numeric_error").format(
                column=error.column, notation=error.number_mode, count=error.count,
                examples=", ".join(error.examples),
            )
        return str(error)

    def _on_run_clicked(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        spec = self._build_spec()
        if not spec:
            return

        if os.path.lexists(spec.output_path):
            if not confirm_dialog(
                self,
                t.get("agg_msg_overwrite_title", "결과 파일 덮어쓰기"),
                t.get("agg_msg_overwrite", "{name}").format(name=Path(spec.output_path).name),
            ):
                return
            spec = replace(spec, allow_output_overwrite=True)

        self._is_processing = True
        self.btn_run.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.btn_open_folder.setVisible(False)
        self.btn_open_file.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)
        self.lbl_status.setText(t.get("agg_msg_prepare", "집계 준비 중..."))

        def worker(report):
            def on_agg_progress(current, total, msg):
                pct = int((current / total) * 100) if total else 0
                report(pct, msg)
            return aggregate_dataset(spec, progress_callback=on_agg_progress)

        def on_success(result: AggregationResult):
            self._is_processing = False
            self.progress_bar.setVisible(False)
            self._last_result = result
            self._last_spec = spec
            self.btn_open_folder.setVisible(True)
            self.btn_open_file.setVisible(True)
            self._render_result(result, spec)
            self._update_step_gating()

        def on_error(err: Exception):
            self._is_processing = False
            self.progress_bar.setVisible(False)
            self.lbl_status.setText(f"<font color='{PALETTE['danger']}'>{escape(self._aggregation_error_text(err))}</font>")
            error_dialog(self, t.get("agg_msg_error_title", "집계 오류"), self._aggregation_error_text(err))
            self._update_step_gating()

        self.job_runner.start(
            "aggregate_dataset",
            worker,
            JobCallbacks(
                on_progress=lambda pct, msg: (self.progress_bar.setValue(pct), self.lbl_status.setText(msg)),
                on_success=on_success,
                on_error=on_error,
                on_finished=lambda: None,
            ),
        )

    def _render_result(self, result: Any, spec: Optional[AggregationSpec] = None) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        if isinstance(result, AggregationResult):
            out_path = result.out_path
            rows = result.final_rows
            coerced = result.coerced_numbers_count
        elif isinstance(result, dict):
            out_path = result.get("output_path", "")
            rows = result.get("rows", 0)
            coerced = result.get("coerced_numeric_count", 0)
        else:
            out_path = str(result)
            rows = 0
            coerced = 0

        keys = spec.group_by_keys if spec else list(self.state.group_keys)

        lines = [
            f"<font color='{PALETTE['success']}'><b>✓ {t.get('agg_msg_done_status', '데이터 집계 완료!')} ({rows:,})</b></font>",
            f"• <b>{t.get('agg_output_name', 'File')}:</b> {Path(out_path).name}",
            f"• <b>{t.get('agg_output_dir', 'Folder')}:</b> {Path(out_path).parent}",
            f"• <b>{t.get('agg_rows_box', 'Group By')}:</b> {', '.join(keys)}",
        ]
        if coerced > 0:
            lines.append(f"• <font color='{PALETTE['warning']}'>{t.get('agg_msg_coerced', '').strip().format(count=coerced)}</font>")

        self.lbl_status.setText("<br>".join(lines))

    def _get_last_out_path(self) -> Optional[str]:
        if not self._last_result:
            return None
        if isinstance(self._last_result, AggregationResult):
            return self._last_result.out_path
        if isinstance(self._last_result, dict):
            return self._last_result.get("output_path")
        return str(self._last_result)

    def _on_open_folder(self) -> None:
        p = self._get_last_out_path()
        if p:
            open_containing_folder(p)

    def _on_open_file(self) -> None:
        p = self._get_last_out_path()
        if p and os.path.exists(p):
            os.startfile(p)

