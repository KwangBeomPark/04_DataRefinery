"""PySide6 implementation for Tab 3: '데이터 집계·슬라이서' (Data Aggregator)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.aggregator_fields import (
    DIMENSION,
    MEASURE,
    ROWS,
    VALUES,
    AggregatorFieldState,
    PoolField,
)
from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.data_aggregator import (
    AGGREGATION_FUNCTIONS,
    AggregationResult,
    AggregationSpec,
    ColumnGroupRule,
    DerivedFormulaRule,
    FilterCondition,
    aggregate_dataset,
    inspect_dataset_schema,
    preview_aggregation,
)
from src.file_reveal import open_containing_folder
from src.i18n import _UI_TEXT
from src.preset_manager import (
    AggregationPreset,
    delete_preset,
    export_preset_file,
    import_preset_file,
    list_presets,
    load_preset,
    preset_exists,
    save_preset,
)
from src.qt.dialogs import confirm_dialog, error_dialog, info_dialog, save_file_dialog, warning_dialog
from src.qt.jobs import create_qt_job_runner
from src.qt.tabs.aggregator.dialogs import (
    FilterRuleDialog,
    FormulaRuleDialog,
    GroupRuleDialog,
)
from src.qt.widgets.card import CardWidget
from src.qt.widgets.file_picker import FilePickerWidget
from src.ui_components import PALETTE


class _SamplePreviewDialog(QDialog):
    """Modal dialog displaying sampled aggregation results."""

    def __init__(self, headers: List[str], rows: List[List[Any]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("미리보기 · 샘플 집계 결과")
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        lbl_note = QLabel("※ 수치 값은 원본의 샘플을 기준으로 계산되었으며 전체 저장 결과와 다를 수 있습니다.")
        lbl_note.setProperty("caption", "true")
        layout.addWidget(lbl_note)

        table = QTableView()
        model = _GenericTableModel(headers, rows, self)
        table.setModel(model)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)

        btn_close = QPushButton("닫기")
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
    """Tab 3: Large data aggregation and slice engine."""

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

        self._last_result: Optional[Dict[str, Any]] = None
        self._is_processing = False

        self._init_ui()
        self.apply_language(self.language_code)

    def _ui(self, key: str) -> str:
        return _UI_TEXT.get(self.language_code, _UI_TEXT["ko"]).get(key, key)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # 1. Top Bar: Source file, Encoding, Presets
        top_card = CardWidget()
        top_lay = QHBoxLayout()
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(10)

        top_lay.addWidget(QLabel("소스:"))
        self.picker_source = FilePickerWidget(
            mode="file",
            filter_pattern="CSV Files (*.csv);;All Files (*.*)",
            placeholder="대용량 CSV 파일을 선택하세요",
        )
        self.picker_source.pathChanged.connect(self._on_source_file_changed)
        top_lay.addWidget(self.picker_source, stretch=1)

        top_lay.addWidget(QLabel("인코딩:"))
        self.combo_encoding = QComboBox()
        self.combo_encoding.addItems(["auto (자동 감지)", "utf-8", "cp949", "cp1250", "iso-8859-2"])
        top_lay.addWidget(self.combo_encoding)

        # Preset Menu Button
        self.btn_preset = QPushButton("프리셋 ▾")
        self.menu_preset = QMenu(self)
        self.menu_preset.addAction("불러오기...", self._on_load_preset)
        self.menu_preset.addAction("저장...", self._on_save_preset)
        self.menu_preset.addSeparator()
        self.menu_preset.addAction("내보내기...", self._on_export_preset)
        self.menu_preset.addAction("가져오기...", self._on_import_preset)
        self.menu_preset.addAction("삭제...", self._on_delete_preset)
        self.btn_preset.setMenu(self.menu_preset)
        top_lay.addWidget(self.btn_preset)

        self.btn_undo = QPushButton("↶ 되돌리기")
        self.btn_undo.clicked.connect(self._on_undo)
        top_lay.addWidget(self.btn_undo)

        top_card.content_layout.addLayout(top_lay)
        main_layout.addWidget(top_card)

        # 2. Main Middle Splitter: Left (Source Columns) | Right (Rules & Pivot Areas)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Source Columns Explorer
        card_sources = CardWidget("원본 컬럼 탐색기 (더블 클릭하여 추가)")
        src_lay = QVBoxLayout()
        src_lay.setSpacing(6)

        # Search box
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("컬럼 이름을 입력해 찾기…")
        self.edit_search.textChanged.connect(self._filter_source_columns)
        src_lay.addWidget(self.edit_search)

        src_lay.addWidget(QLabel("📁 차원/키 (더블클릭: 행 그룹)"))
        self.list_dims = QListWidget()
        self.list_dims.itemDoubleClicked.connect(self._on_dim_double_clicked)
        src_lay.addWidget(self.list_dims, stretch=1)

        src_lay.addWidget(QLabel("📊 수치/값 (더블클릭: 값)"))
        self.list_measures = QListWidget()
        self.list_measures.itemDoubleClicked.connect(self._on_measure_double_clicked)
        src_lay.addWidget(self.list_measures, stretch=1)

        card_sources.content_layout.addLayout(src_lay)
        splitter.addWidget(card_sources)

        # Right: Aggregation Rules & Pivot Target Areas
        card_rules = CardWidget("집계 규칙 설정")
        rules_lay = QVBoxLayout()
        rules_lay.setSpacing(8)

        # Rule action buttons
        rule_btns = QHBoxLayout()
        rule_btns.setSpacing(6)
        btn_grp = QPushButton("∑ 묶기")
        btn_grp.clicked.connect(self._on_add_group_rule)
        rule_btns.addWidget(btn_grp)

        btn_formula = QPushButton("% 비율식")
        btn_formula.clicked.connect(self._on_add_formula_rule)
        rule_btns.addWidget(btn_formula)

        btn_filter = QPushButton("+ 필터")
        btn_filter.clicked.connect(self._on_add_filter_rule)
        rule_btns.addWidget(btn_filter)
        rule_btns.addStretch()
        rules_lay.addLayout(rule_btns)

        # Area 1: Rows
        rules_lay.addWidget(QLabel("1. 행 그룹 (Rows):"))
        self.list_rows = QListWidget()
        self.list_rows.itemDoubleClicked.connect(self._on_row_double_clicked)
        rules_lay.addWidget(self.list_rows, stretch=1)

        # Area 2: Values
        rules_lay.addWidget(QLabel("2. 값 (Values - 더블클릭 시 제거):"))
        self.list_values = QListWidget()
        self.list_values.itemDoubleClicked.connect(self._on_value_double_clicked)
        rules_lay.addWidget(self.list_values, stretch=1)

        # Area 3: Filters
        rules_lay.addWidget(QLabel("조건 필터 (더블클릭 시 제거):"))
        self.list_filters = QListWidget()
        self.list_filters.itemDoubleClicked.connect(self._on_filter_double_clicked)
        rules_lay.addWidget(self.list_filters, stretch=1)

        card_rules.content_layout.addLayout(rules_lay)
        splitter.addWidget(card_rules)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter, stretch=1)

        # 3. Bottom Execution Card
        bot_card = CardWidget("저장 및 실행")
        form_bot = QFormLayout()
        form_bot.setSpacing(8)

        self.picker_output = FilePickerWidget(
            mode="folder",
            placeholder="저장할 폴더를 선택하세요 (비워두면 원본과 같은 폴더)",
        )
        form_bot.addRow("저장 폴더:", self.picker_output)

        self.edit_output_name = QLineEdit("aggregated_output.csv")
        form_bot.addRow("저장 파일명:", self.edit_output_name)

        self.check_rollup = QCheckBox("연간 롤업(Annual Roll-up) 적용 (기준년월 컬럼 필요)")
        form_bot.addRow("", self.check_rollup)

        bot_card.content_layout.addLayout(form_bot)

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
        bot_card.content_layout.addLayout(act_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        bot_card.content_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("대용량 CSV 파일을 선택하여 컬럼을 분석하세요.")
        self.lbl_status.setWordWrap(True)
        bot_card.content_layout.addWidget(self.lbl_status)

        main_layout.addWidget(bot_card)

    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        t = _UI_TEXT.get(lang_code, _UI_TEXT["ko"])

        self.btn_undo.setText(t["agg_undo"])
        self.btn_preset.setText(t["agg_preset_menu"])
        self.btn_preview.setText(t["agg_preview"])
        self.btn_run.setText(t["agg_run"])
        self.edit_search.setPlaceholderText(t["agg_search_placeholder"])

        if not self._last_result:
            self.lbl_status.setText(t["agg_initial_result"])

    # -------------------------------------------------------------------------
    # Schema Loading
    # -------------------------------------------------------------------------
    def _on_source_file_changed(self, file_path: str) -> None:
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
            self.lbl_status.setText(f"컬럼 감지 완료 (총 {len(schema.columns)}개 열)")
        except Exception as e:
            error_dialog(self, "파일 분석 오류", f"파일 컬럼 구조를 분석하지 못했습니다:\n{e}")

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
        measures = [f.name for f in self.state.measure_pool] + list(self.state.values)
        if not measures:
            warning_dialog(self, "수치 컬럼 없음", "합산할 수치 컬럼이 없습니다.")
            return

        existing = [f.name for f in self.state.dimension_pool + self.state.measure_pool] + self.state.group_keys + self.state.values
        dlg = GroupRuleDialog(available_measures=sorted(list(set(measures))), existing_names=existing, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.rule:
            self.state.snapshot()
            self.state.add_column_group(dlg.rule)
            self._sync_ui_lists()

    def _on_add_formula_rule(self) -> None:
        measures = [f.name for f in self.state.measure_pool] + list(self.state.values)
        if not measures:
            warning_dialog(self, "수치 컬럼 없음", "수식에 사용할 수치 컬럼이 없습니다.")
            return

        existing = [f.name for f in self.state.dimension_pool + self.state.measure_pool] + self.state.group_keys + self.state.values
        dlg = FormulaRuleDialog(available_measures=sorted(list(set(measures))), existing_names=existing, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.rule:
            self.state.snapshot()
            self.state.add_derived_formula(dlg.rule)
            self._sync_ui_lists()

    def _on_add_filter_rule(self) -> None:
        all_cols = [f.name for f in self.state.dimension_pool + self.state.measure_pool] + self.state.group_keys + self.state.values
        if not all_cols:
            warning_dialog(self, "컬럼 없음", "필터를 적용할 컬럼이 없습니다.")
            return

        dlg = FilterRuleDialog(available_columns=sorted(list(set(all_cols))), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.condition:
            self.state.snapshot()
            self.state.filters.append(dlg.condition)
            self._sync_ui_lists()

    # -------------------------------------------------------------------------
    # Presets
    # -------------------------------------------------------------------------
    def _on_load_preset(self) -> None:
        presets = list_presets()
        if not presets:
            info_dialog(self, "프리셋", "저장된 프리셋이 없습니다.")
            return

        names = [p.name for p in presets]
        chosen_name, ok = QInputDialog.getItem(self, "프리셋 불러오기", "불러올 프리셋을 선택하세요:", names, 0, False)
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
        self._sync_ui_lists()
        info_dialog(self, "프리셋 적용", f"프리셋 '{preset.name}'이(가) 적용되었습니다.")

    def _on_save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "프리셋 저장", "저장할 프리셋 이름을 입력하세요:")
        if not ok or not name.strip():
            return
        name = name.strip()

        if preset_exists(name):
            if not confirm_dialog(self, "프리셋 덮어쓰기", f"프리셋 '{name}'이(가) 이미 존재합니다. 덮어쓰시겠습니까?"):
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
        )
        save_preset(preset)
        info_dialog(self, "프리셋 저장", f"프리셋 '{name}'이(가) 성공적으로 저장되었습니다.")

    def _on_export_preset(self) -> None:
        pass

    def _on_import_preset(self) -> None:
        pass

    def _on_delete_preset(self) -> None:
        presets = list_presets()
        if not presets:
            info_dialog(self, "프리셋 삭제", "삭제할 프리셋이 없습니다.")
            return

        names = [p.name for p in presets]
        chosen_name, ok = QInputDialog.getItem(self, "프리셋 삭제", "삭제할 프리셋을 선택하세요:", names, 0, False)
        if not ok or not chosen_name:
            return

        if confirm_dialog(self, "프리셋 삭제", f"정말로 프리셋 '{chosen_name}'을(를) 삭제하시겠습니까?"):
            delete_preset(chosen_name)
            info_dialog(self, "프리셋 삭제", f"프리셋 '{chosen_name}'이(가) 삭제되었습니다.")

    # -------------------------------------------------------------------------
    # Execution & Preview
    # -------------------------------------------------------------------------
    def _build_spec(self) -> Optional[AggregationSpec]:
        src_path = self.picker_source.get_path()
        if not src_path or not os.path.isfile(src_path):
            error_dialog(self, "파일 선택 필요", "처리할 원본 CSV 파일을 선택해 주세요.")
            return None

        if not self.state.group_keys:
            error_dialog(self, "설정 확인", "최소 하나 이상의 행 그룹(Group By 키)을 선택해 주세요.")
            return None

        if not self.state.values:
            error_dialog(self, "설정 확인", "집계할 값을 최소 하나 이상 '2. 값' 영역에 올려 주세요.")
            return None

        out_dir = self.picker_output.get_path() or str(Path(src_path).parent.resolve())
        out_name = self.edit_output_name.text().strip() or "aggregated.csv"
        out_path = str(Path(out_dir) / out_name)
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
            number_mode=getattr(self, "_number_mode", "English"),
            output_path=out_path,
            output_format=out_format,
            constant_columns=dict(self.state.constant_columns),
            measure_functions=dict(self.state.measure_functions),
        )

    def _on_preview_clicked(self) -> None:
        spec = self._build_spec()
        if not spec:
            return

        try:
            df, _ = preview_aggregation(spec, sample_rows=2000)
            headers = list(df.columns)
            rows = df.values.tolist()
            dlg = _SamplePreviewDialog(headers, rows, parent=self)
            dlg.exec()
        except Exception as e:
            error_dialog(self, "미리보기 오류", f"미리보기를 생성하지 못했습니다:\n{e}")

    def _on_run_clicked(self) -> None:
        spec = self._build_spec()
        if not spec:
            return

        self._is_processing = True
        self.btn_run.setEnabled(False)
        self.btn_open_folder.setVisible(False)
        self.btn_open_file.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)
        self.lbl_status.setText("집계 준비 중...")

        def worker(report):
            def on_agg_progress(current, total, msg):
                pct = int((current / total) * 100) if total else 0
                report(pct, msg)
            return aggregate_dataset(spec, progress_callback=on_agg_progress)

        def on_success(result: AggregationResult):
            self._is_processing = False
            self.btn_run.setEnabled(True)
            self.progress_bar.setVisible(False)
            self._last_result = result
            self.btn_open_folder.setVisible(True)
            self.btn_open_file.setVisible(True)
            self._render_result(result, spec)

        def on_error(err: Exception):
            self._is_processing = False
            self.btn_run.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.lbl_status.setText(f"<font color='{PALETTE['danger']}'>집계 오류: {err}</font>")
            error_dialog(self, "집계 오류", f"집계 중 오류가 발생했습니다:\n{err}")

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
            f"<font color='{PALETTE['success']}'><b>✓ 데이터 집계 완료! ({rows:,}행 저장)</b></font>",
            f"• <b>저장 파일:</b> {Path(out_path).name}",
            f"• <b>위치:</b> {Path(out_path).parent}",
            f"• <b>집계 기준:</b> {', '.join(keys)}",
        ]
        if coerced > 0:
            lines.append(f"• <font color='{PALETTE['warning']}'>결측치/비정상 수치 {coerced:,}건이 0으로 치환되었습니다.</font>")

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
