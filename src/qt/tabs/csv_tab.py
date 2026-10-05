"""PySide6 implementation for Tab 1: 'CSV 구조 복구' (CSV Repair & Cleaning)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.csv_processing import (
    CsvColumnOverflowError,
    CsvNoDataError,
    CsvNoTableError,
    CsvProcessingOptions,
    detect_delimiter as detect_csv_delimiter,
    is_excel as is_excel_file,
    normalize_delimiter,
    process_csv_file,
    read_file_rows,
)
from src.file_reveal import open_containing_folder
from src.i18n import _UI_TEXT
from src.qt.dialogs import error_dialog, info_dialog, warning_dialog
from src.qt.jobs import create_qt_job_runner
from src.qt.widgets.card import CardWidget
from src.qt.widgets.file_picker import FilePickerWidget
from src.ui_components import PALETTE


class CsvRepairTab(QWidget):
    """Tab 1: CSV structure recovery and cleaning."""

    def __init__(
        self,
        language_code: str = "ko",
        job_runner: Optional[BackgroundJobRunner] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.language_code = language_code
        self.job_runner = job_runner or create_qt_job_runner()
        self._last_result: Optional[Dict[str, Any]] = None
        self._is_processing = False

        self._init_ui()
        self.apply_language(self.language_code)

    def _ui(self, key: str) -> str:
        return _UI_TEXT.get(self.language_code, _UI_TEXT["ko"]).get(key, key)

    def _init_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        # 1. Source File Card
        self.card_file = CardWidget()
        form_file = QFormLayout()
        form_file.setSpacing(8)

        self.picker_file = FilePickerWidget(
            mode="file",
            filter_pattern="CSV, TXT, Excel (*.csv *.txt *.xlsx *.xlsm);;All Files (*.*)",
            placeholder="정리할 CSV, TXT, 또는 Excel 파일을 선택하세요",
        )
        self.picker_file.pathChanged.connect(self._on_file_selected)
        form_file.addRow("파일 경로:", self.picker_file)

        self.lbl_file_info = QLabel()
        self.lbl_file_info.setProperty("muted", "true")
        form_file.addRow("", self.lbl_file_info)

        self.card_file.content_layout.addLayout(form_file)
        layout.addWidget(self.card_file)

        # 2. Import Settings Card
        self.card_import = CardWidget()
        form_import = QFormLayout()
        form_import.setSpacing(10)

        # Delimiter
        self.edit_delim = QLineEdit(",")
        self.edit_delim.setFixedWidth(80)
        self.edit_delim.textChanged.connect(self._on_delim_changed)
        form_import.addRow("파일 구분 기호:", self.edit_delim)

        self.lbl_delim_help = QLabel()
        self.lbl_delim_help.setProperty("caption", "true")
        form_import.addRow("", self.lbl_delim_help)

        # Number format
        self.combo_num_format = QComboBox()
        self.combo_num_format.currentIndexChanged.connect(self._on_num_format_changed)
        form_import.addRow("숫자 표기 방식:", self.combo_num_format)

        self.lbl_num_help = QLabel()
        self.lbl_num_help.setProperty("caption", "true")
        form_import.addRow("", self.lbl_num_help)

        # Column count
        self.edit_cols = QLineEdit("0")
        self.edit_cols.setFixedWidth(80)
        form_import.addRow("표의 열 개수:", self.edit_cols)

        self.lbl_cols_help = QLabel()
        self.lbl_cols_help.setProperty("caption", "true")
        form_import.addRow("", self.lbl_cols_help)

        self.card_import.content_layout.addLayout(form_import)
        layout.addWidget(self.card_import)

        # 3. Output Settings Card
        self.card_output = CardWidget()
        form_output = QFormLayout()
        form_output.setSpacing(8)

        self.combo_out_format = QComboBox()
        self.combo_out_format.currentIndexChanged.connect(self._on_out_format_changed)
        form_output.addRow("저장 파일 형식:", self.combo_out_format)

        self.lbl_out_help = QLabel()
        self.lbl_out_help.setProperty("caption", "true")
        form_output.addRow("", self.lbl_out_help)

        self.lbl_out_hint = QLabel()
        self.lbl_out_hint.setProperty("muted", "true")
        form_output.addRow("", self.lbl_out_hint)

        self.card_output.content_layout.addLayout(form_output)
        layout.addWidget(self.card_output)

        # Action Button & Progress
        act_box = QHBoxLayout()
        self.btn_process = QPushButton()
        self.btn_process.setProperty("primary", "true")
        self.btn_process.clicked.connect(self._run_process)
        act_box.addWidget(self.btn_process)

        self.btn_open_folder = QPushButton("📂 결과 폴더 열기")
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        self.btn_open_folder.setVisible(False)
        act_box.addWidget(self.btn_open_folder)
        act_box.addStretch()
        layout.addLayout(act_box)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel()
        self.lbl_status.setProperty("subheading", "true")
        layout.addWidget(self.lbl_status)

        # 4. Result Card
        self.card_result = CardWidget()
        self.lbl_result_summary = QLabel()
        self.lbl_result_summary.setWordWrap(True)
        self.card_result.content_layout.addWidget(self.lbl_result_summary)
        layout.addWidget(self.card_result)

        layout.addStretch()
        scroll.setWidget(container)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.addWidget(scroll)

    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        t = _UI_TEXT.get(lang_code, _UI_TEXT["ko"])

        self.card_file.set_title(t["section_file"])
        self.lbl_file_info.setText(t["file_info"])

        self.card_import.set_title(t["section_import"])
        self.lbl_delim_help.setText(t["delimiter_help"])
        self.lbl_cols_help.setText(t["columns_help"])

        # Number format options
        curr_idx = self.combo_num_format.currentIndex()
        self.combo_num_format.blockSignals(True)
        self.combo_num_format.clear()
        self.combo_num_format.addItems(t["number_options"])
        self.combo_num_format.setCurrentIndex(max(0, curr_idx))
        self.combo_num_format.blockSignals(False)
        self._on_num_format_changed()

        # Output options
        self.card_output.set_title(t["section_output"])
        curr_out_idx = self.combo_out_format.currentIndex()
        self.combo_out_format.blockSignals(True)
        self.combo_out_format.clear()
        self.combo_out_format.addItems(t["output_options"])
        self.combo_out_format.setCurrentIndex(max(0, curr_out_idx))
        self.combo_out_format.blockSignals(False)
        self._on_out_format_changed()

        self.btn_process.setText(t["process"])
        self.card_result.set_title(t["result_title"])

        if not self._last_result:
            self.lbl_status.setText(t["ready"])
            self.lbl_result_summary.setText(t["initial_result"])
        else:
            self._render_result_summary(self._last_result)

    def _on_num_format_changed(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        is_polish = self.combo_num_format.currentIndex() == 1
        self.lbl_num_help.setText(t["number_help_polish"] if is_polish else t["number_help_english"])

    def _on_out_format_changed(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        is_excel = self.combo_out_format.currentIndex() == 1
        self.lbl_out_help.setText(t["output_help_excel"] if is_excel else t["output_help_csv"])
        ext = ".xlsx" if is_excel else ".csv"
        exp_name = f"processed_output_YYYYMMDD_HHMM{ext}"
        path = self.picker_file.get_path()
        if path:
            self.lbl_out_hint.setText(t["output_hint_with_file"].format(name=exp_name))
        else:
            self.lbl_out_hint.setText(t["output_hint"].format(name=exp_name))

    def _on_delim_changed(self) -> None:
        self._auto_detect_columns()

    def _on_file_selected(self, path_str: str) -> None:
        self._on_out_format_changed()
        if not path_str or not os.path.isfile(path_str):
            return

        # Auto-detect delimiter
        try:
            detected_delim = detect_csv_delimiter(path_str)
            if detected_delim:
                self.edit_delim.setText("\\t" if detected_delim == "\t" else detected_delim)
        except Exception:
            pass

        self._auto_detect_columns()

    def _auto_detect_columns(self) -> None:
        path_str = self.picker_file.get_path()
        if not path_str or not os.path.isfile(path_str):
            return

        delim = self.edit_delim.text()
        delim = "\t" if delim == "\\t" else delim
        if not delim:
            delim = ","

        try:
            first_rows, enc = read_file_rows(path_str, delimiter=delim, max_rows=50)
            if first_rows:
                # Find most common row length
                lens = [len(r) for r in first_rows if r]
                if lens:
                    mode_len = max(set(lens), key=lens.count)
                    self.edit_cols.setText(str(mode_len))
        except Exception:
            pass

    def _run_process(self) -> None:
        path_str = self.picker_file.get_path()
        if not path_str or not os.path.isfile(path_str):
            error_dialog(self, self._ui("select_file_title"), self._ui("select_file_message"))
            return

        delim = self.edit_delim.text()
        delim = "\t" if delim == "\\t" else delim
        if len(delim) != 1:
            error_dialog(self, self._ui("delimiter_title"), self._ui("delimiter_message"))
            return

        try:
            col_cnt = int(self.edit_cols.text().strip())
            if col_cnt <= 0:
                raise ValueError()
        except ValueError:
            error_dialog(self, self._ui("columns_title"), self._ui("columns_positive"))
            return

        is_polish = self.combo_num_format.currentIndex() == 1
        num_mode = "Polish" if is_polish else "English"
        is_excel = self.combo_out_format.currentIndex() == 1
        out_fmt = "Excel (.xlsx)" if is_excel else "CSV"

        options = CsvProcessingOptions(
            delimiter=delim,
            expected_columns=col_cnt,
            number_mode=num_mode,
            output_format=out_fmt,
        )

        self._is_processing = True
        self.btn_process.setEnabled(False)
        self.btn_open_folder.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(10)
        self.lbl_status.setText(self._ui("reading"))

        def worker(report):
            return process_csv_file(path_str, options, report_progress=report)

        def on_success(result):
            self._is_processing = False
            self.btn_process.setEnabled(True)
            self.progress_bar.setVisible(False)
            self._last_result = result
            self.btn_open_folder.setVisible(True)
            self._render_result_summary(result)
            self.lbl_status.setText(self._ui("done"))

        def on_error(err: Exception):
            self._is_processing = False
            self.btn_process.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.lbl_status.setText(self._ui("error_title"))
            error_dialog(self, self._ui("error_title"), self._ui("error_message").format(error=str(err)))

        self.job_runner.start(
            "csv_repair",
            worker,
            JobCallbacks(
                on_progress=lambda pct, msg: (self.progress_bar.setValue(pct), self.lbl_status.setText(msg)),
                on_success=on_success,
                on_error=on_error,
                on_finished=lambda: None,
            ),
        )

    def _render_result_summary(self, result: Dict[str, Any]) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        out_path = result.get("out_path", "")
        lines = [
            f"<b>{t['summary_saved'].format(name=os.path.basename(out_path))}</b>",
            f"<font color='{PALETTE['muted']}'>{t['summary_location'].format(path=os.path.dirname(out_path))}</font>",
            "",
            f"<b>{t['summary_title']}</b>",
            t["summary_rows"].format(rows=f"{result.get('data_rows', 0):,}", columns=result.get("total_columns", 0)),
        ]
        if result.get("garbage_rows", 0) > 0:
            lines.append(t["summary_garbage"].format(count=result.get("garbage_rows", 0)))
        lines.append(
            t["summary_values"].format(
                numbers=f"{result.get('converted_numbers', 0):,}",
                dates=f"{result.get('converted_dates', 0):,}",
            )
        )
        if result.get("flattened_newlines", 0) > 0:
            lines.append(t["summary_flattened"].format(count=f"{result.get('flattened_newlines', 0):,}"))
        if result.get("repaired_rows", 0) > 0:
            lines.append(t["summary_repaired"].format(count=f"{result.get('repaired_rows', 0):,}"))
        if result.get("large_integers_as_text", 0) > 0:
            lines.append(t["summary_large"].format(count=f"{result.get('large_integers_as_text', 0):,}"))
        if result.get("encoding"):
            lines.append(t["summary_encoding"].format(encoding=result.get("encoding")))

        self.lbl_result_summary.setText("<br>".join(lines))

    def _on_open_folder(self) -> None:
        if self._last_result and self._last_result.get("out_path"):
            open_containing_folder(self._last_result["out_path"])
