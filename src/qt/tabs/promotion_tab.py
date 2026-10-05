"""PySide6 implementation for Tab 2: '프로모션 템플릿' (Promotion Template)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.file_reveal import open_containing_folder
from src.i18n import _UI_TEXT
from src.promotion_normalizer import (
    EXCEL_MAX_DATA_ROWS,
    PromotionTemplateData,
    export_normalized,
    load_template,
    preview_daily_rows,
)
from src.qt.app import get_asset_path
from src.qt.dialogs import error_dialog, info_dialog, save_file_dialog, warning_dialog
from src.qt.jobs import create_qt_job_runner
from src.qt.widgets.card import CardWidget
from src.qt.widgets.file_picker import FilePickerWidget
from src.ui_components import PALETTE


class _PromotionPreviewModel(QAbstractTableModel):
    """Table model for previewing daily generated promotion rows."""

    COLUMNS = ["적용 일자", "모델 코드", "프로모션 ID", "대당 지원금", "통화"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None

        r = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return r.get("applied_date", "")
            elif col == 1:
                return r.get("model_code", "")
            elif col == 2:
                return r.get("promotion_id", "")
            elif col == 3:
                val = r.get("support_per_unit", "")
                try:
                    return f"{float(val):,}"
                except Exception:
                    return str(val)
            elif col == 4:
                return r.get("currency", "")

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 4):
                return int(Qt.AlignmentFlag.AlignCenter)
            elif col == 3:
                return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        return None


class PromotionTab(QWidget):
    """Tab 2: Promotion template expansion and normalization."""

    def __init__(
        self,
        language_code: str = "ko",
        job_runner: Optional[BackgroundJobRunner] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.language_code = language_code
        self.job_runner = job_runner or create_qt_job_runner()

        self._promotion_data: Optional[PromotionTemplateData] = None
        self._last_result: Optional[Any] = None
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

        # 1. Template Selection Card
        self.card_file = CardWidget()
        top_bar = QHBoxLayout()
        self.btn_download = QPushButton("템플릿 받기…")
        self.btn_download.clicked.connect(self._on_download_template)
        top_bar.addWidget(self.btn_download)
        top_bar.addStretch()
        self.card_file.content_layout.addLayout(top_bar)

        form_file = QFormLayout()
        form_file.setSpacing(8)

        self.picker_file = FilePickerWidget(
            mode="file",
            filter_pattern="Excel Template (*.xlsx);;All Files (*.*)",
            placeholder="작성한 프로모션 템플릿(.xlsx)을 선택하세요",
        )
        self.picker_file.pathChanged.connect(self._on_file_selected)
        form_file.addRow("템플릿 파일:", self.picker_file)

        self.lbl_file_info = QLabel()
        self.lbl_file_info.setProperty("muted", "true")
        form_file.addRow("", self.lbl_file_info)

        self.card_file.content_layout.addLayout(form_file)
        layout.addWidget(self.card_file)

        # 2. Template Validation & Preview Card
        self.card_preview = CardWidget("템플릿 검증 및 일별 지원금 미리보기")
        self.lbl_validation = QLabel("템플릿을 선택하면 규칙 검증 및 미리보기가 생성됩니다.")
        self.lbl_validation.setProperty("subheading", "true")
        self.card_preview.content_layout.addWidget(self.lbl_validation)

        self.table_preview = QTableView()
        self.model_preview = _PromotionPreviewModel(self)
        self.table_preview.setModel(self.model_preview)
        self.table_preview.setAlternatingRowColors(True)
        self.table_preview.horizontalHeader().setStretchLastSection(True)
        self.table_preview.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_preview.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table_preview.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_preview.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_preview.setFixedHeight(180)
        self.card_preview.content_layout.addWidget(self.table_preview)
        layout.addWidget(self.card_preview)

        # 3. Output Settings Card
        self.card_output = CardWidget()
        form_output = QFormLayout()
        form_output.setSpacing(8)

        self.combo_out_format = QComboBox()
        self.combo_out_format.currentIndexChanged.connect(self._on_out_format_changed)
        form_output.addRow("일별 지원금 형식:", self.combo_out_format)

        self.lbl_out_help = QLabel()
        self.lbl_out_help.setProperty("caption", "true")
        form_output.addRow("", self.lbl_out_help)

        self.lbl_out_hint = QLabel()
        self.lbl_out_hint.setProperty("muted", "true")
        form_output.addRow("", self.lbl_out_hint)

        self.card_output.content_layout.addLayout(form_output)
        layout.addWidget(self.card_output)

        # Actions & Progress
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

        # 4. Result Summary Card
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

        self.card_file.set_title(t["promo_file_section"])
        self.btn_download.setText(t["promo_download"])
        self.lbl_file_info.setText(t["promo_file_info"])

        self.card_output.set_title(t["promo_output_section"])
        curr_out_idx = self.combo_out_format.currentIndex()
        self.combo_out_format.blockSignals(True)
        self.combo_out_format.clear()
        self.combo_out_format.addItems(t["promo_output_options"])
        self.combo_out_format.setCurrentIndex(max(0, curr_out_idx))
        self.combo_out_format.blockSignals(False)

        self.lbl_out_help.setText(t["promo_output_help"])
        self._on_out_format_changed()

        self.btn_process.setText(t["promo_process"])
        self.card_result.set_title(t["promo_result_title"])

        if not self._last_result:
            self.lbl_status.setText(t["promo_initial_result"])
            self.lbl_result_summary.setText(t["promo_initial_result"])

    def _on_out_format_changed(self) -> None:
        is_excel = self.combo_out_format.currentIndex() == 1
        ext = ".xlsx" if is_excel else ".csv"
        self.lbl_out_hint.setText(f"저장 파일 이름: promotion_daily_support_YYYYMMDD_HHMM{ext}")

    def _on_download_template(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        dest = save_file_dialog(
            self,
            title=t["promo_download"],
            default_name="promotion_template.xlsx",
            filter_pattern="Excel workbook (*.xlsx)",
        )
        if not dest:
            return

        template_src = get_asset_path("templates/promotion_template.xlsx")
        try:
            if os.path.exists(template_src):
                shutil.copyfile(template_src, dest)
            else:
                # If bundle asset not found, create empty fallback workbook
                from openpyxl import Workbook
                wb = Workbook()
                ws1 = wb.active
                ws1.title = "Promotion_Master"
                ws1.append(["promotion_id", "promotion_name", "start_date", "end_date"])
                ws2 = wb.create_sheet(title="Support_Rules")
                ws2.append(["promotion_id", "model_code", "start_date", "end_date", "support_per_unit", "currency"])
                wb.save(dest)

            info_dialog(self, "다운로드 완료", t["promo_template_saved"].format(name=os.path.basename(dest)))
        except Exception as e:
            error_dialog(self, t["promo_file_section"], str(e))

    def _on_file_selected(self, path_str: str) -> None:
        if not path_str or not os.path.isfile(path_str):
            self._promotion_data = None
            self.model_preview.set_rows([])
            self.lbl_validation.setText("템플릿을 선택하면 규칙 검증 및 미리보기가 생성됩니다.")
            return

        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        try:
            data, issues = load_template(path_str)
        except Exception as err:
            self._promotion_data = None
            self.model_preview.set_rows([])
            self.lbl_validation.setText(f"<font color='{PALETTE['danger']}'>템플릿 로드 실패: {err}</font>")
            return

        if issues:
            self._promotion_data = None
            self.model_preview.set_rows([])
            shown = [f"• {issue.display()}" for issue in issues[:4]]
            if len(issues) > len(shown):
                shown.append(t["promo_issue_more"].format(count=len(issues) - len(shown)))
            msg = f"<font color='{PALETTE['danger']}'><b>{t['promo_invalid'].format(count=len(issues))}</b></font><br>" + "<br>".join(shown)
            self.lbl_validation.setText(msg)
            return

        self._promotion_data = data
        preview_rows = preview_daily_rows(data, limit=20)
        self.model_preview.set_rows(preview_rows)

        summary_msg = [
            f"<font color='{PALETTE['success']}'><b>✓ {t['promo_valid'].format(rules=len(data.support_rules), rows=f'{data.estimated_daily_rows:,}')}</b></font>",
            f"<font color='{PALETTE['muted']}'>{t['promo_overlap'].format(count=data.overlapping_rule_pairs)}</font>",
        ]
        self.lbl_validation.setText("<br>".join(summary_msg))

    def _run_process(self) -> None:
        path_str = self.picker_file.get_path()
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])

        if not path_str or not os.path.isfile(path_str):
            error_dialog(self, t["promo_select_title"], t["promo_select_message"])
            return

        if not self._promotion_data:
            self._on_file_selected(path_str)
            if not self._promotion_data:
                return

        data = self._promotion_data
        is_excel = self.combo_out_format.currentIndex() == 1
        daily_format = "Excel (.xlsx)" if is_excel else "CSV"

        if daily_format == "Excel (.xlsx)" and data.estimated_daily_rows > EXCEL_MAX_DATA_ROWS:
            error_dialog(self, t["promo_result_title"], t["promo_excel_limit"].format(limit=EXCEL_MAX_DATA_ROWS))
            return

        self._is_processing = True
        self.btn_process.setEnabled(False)
        self.btn_open_folder.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(15)
        self.lbl_status.setText(t["promo_saving"])

        def worker(report):
            report(15, "saving")
            return export_normalized(
                data,
                path_str,
                daily_format=daily_format,
                progress=report,
            )

        def on_success(result):
            self._is_processing = False
            self.btn_process.setEnabled(True)
            self.progress_bar.setVisible(False)
            self._last_result = result
            self.btn_open_folder.setVisible(True)
            self._render_result_summary(result)

        def on_error(err: Exception):
            self._is_processing = False
            self.btn_process.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.lbl_status.setText(t["promo_result_title"])
            error_dialog(self, t["promo_result_title"], str(err))

        self.job_runner.start(
            "promotion_export",
            worker,
            JobCallbacks(
                on_progress=lambda pct, msg: self.progress_bar.setValue(pct),
                on_success=on_success,
                on_error=on_error,
                on_finished=lambda: None,
            ),
        )

    def _render_result_summary(self, result: Any) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])

        daily_rows = getattr(result, "daily_rows", 0)
        daily_rows_str = f"{int(daily_rows):,}"
        master_path = getattr(result, "master_path", "")
        rules_path = getattr(result, "rules_path", "")
        daily_path = getattr(result, "daily_path", "")
        overlap = getattr(result, "overlapping_rule_pairs", 0)

        output_dir = str(Path(daily_path).parent.resolve()) if daily_path else ""

        done_msg = t["promo_done"].format(rows=daily_rows_str)
        self.lbl_status.setText(done_msg)

        lines = [
            f"<b>{done_msg}</b>",
            f"<font color='{PALETTE['muted']}'>{t['promo_summary_location'].format(path=output_dir)}</font>",
            "",
            f"<b>{t['promo_summary_title']}</b>",
            t["promo_master_file"].format(name=Path(master_path).name),
            t["promo_rules_file"].format(name=Path(rules_path).name),
            t["promo_daily_file"].format(name=Path(daily_path).name),
            t["promo_overlap"].format(count=overlap),
        ]
        self.lbl_result_summary.setText("<br>".join(lines))

    def _on_open_folder(self) -> None:
        if self._last_result:
            daily_path = getattr(self._last_result, "daily_path", "")
            if daily_path:
                open_containing_folder(daily_path)
