"""PySide6 Tab Implementation for '데이터셋 배포' (Dataset Publisher).

Integrates DatasetRegistry, DatasetEngine, DatasetWizardDialog, and BackgroundJobRunner.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.dataset_config import DatasetDefinition, DatasetRegistry, get_default_storage_dir
from src.dataset_engine import (
    DatasetEngine,
    DatasetPublishLockError,
    InspectionResult,
    ScanSummary,
)
from src.dataset_excel import create_excel_template_workbook
from src.file_reveal import open_containing_folder
from src.i18n import _UI_TEXT
from src.qt.dialogs import confirm_dialog, error_dialog, info_dialog, warning_dialog
from src.qt.jobs import create_qt_job_runner
from src.qt.tabs.dataset.wizard import DatasetWizardDialog
from src.qt.widgets.card import CardWidget
from src.ui_components import PALETTE


class _DatasetListModel(QAbstractTableModel):
    """Model for the left dataset list table."""

    COLUMNS = ["데이터셋명", "포함 기간", "상태", "마지막 배포"]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._datasets: List[DatasetDefinition] = []
        self._periods_map: Dict[str, str] = {}
        self._status_map: Dict[str, str] = {}
        self._headers: List[str] = list(self.COLUMNS)

    def set_headers(self, headers: Any) -> None:
        self._headers = list(headers)
        if self._headers:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self._headers) - 1)

    def set_datasets(
        self,
        datasets: List[DatasetDefinition],
        periods_map: Dict[str, str],
        status_map: Dict[str, str],
    ) -> None:
        self.beginResetModel()
        self._datasets = list(datasets)
        self._periods_map = dict(periods_map)
        self._status_map = dict(status_map)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._datasets)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self._headers):
                return self._headers[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._datasets)):
            return None

        ds = self._datasets[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return ds.name
            elif col == 1:
                return self._periods_map.get(ds.id, "-")
            elif col == 2:
                return self._status_map.get(ds.id, "Ready")
            elif col == 3:
                return ds.last_published_at[:16].replace("T", " ") if ds.last_published_at else "-"

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (1, 2, 3):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        return None

    def get_dataset(self, row: int) -> Optional[DatasetDefinition]:
        if 0 <= row < len(self._datasets):
            return self._datasets[row]
        return None


class DatasetPublisherTab(QWidget):
    """Main tab widget for Dataset Publishing."""

    def __init__(
        self,
        registry: Optional[DatasetRegistry] = None,
        job_runner: Optional[BackgroundJobRunner] = None,
        language_code: str = "ko",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.language_code = language_code
        self.registry = registry or DatasetRegistry(get_default_storage_dir())
        self.job_runner = job_runner or create_qt_job_runner()

        self.current_dataset: Optional[DatasetDefinition] = None
        self.current_engine: Optional[DatasetEngine] = None
        self.last_scan: Optional[ScanSummary] = None
        self.last_inspection: Optional[InspectionResult] = None
        self._cancel_event: Optional[threading.Event] = None

        self._init_ui()
        self.apply_language(self.language_code)
        self.refresh_dataset_list()

    def _t(self) -> Dict[str, Any]:
        return _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
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

        # Splitter: Left (Dataset list) | Right (Workspace)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Panel
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        # Left Actions
        hdr_actions = QHBoxLayout()
        hdr_actions.setSpacing(6)
        self.btn_add = QPushButton("✨ 새 데이터셋 등록")
        self.btn_add.setProperty("primary", "true")
        self.btn_add.clicked.connect(self._on_add_dataset)
        hdr_actions.addWidget(self.btn_add)

        self.btn_edit = QPushButton("⚙ 설정 수정")
        self.btn_edit.clicked.connect(self._on_edit_dataset)
        hdr_actions.addWidget(self.btn_edit)

        self.btn_del = QPushButton("삭제")
        self.btn_del.setProperty("danger", "true")
        self.btn_del.clicked.connect(self._on_delete_dataset)
        hdr_actions.addWidget(self.btn_del)
        left_layout.addLayout(hdr_actions)

        # Left Table
        self._list_table = QTableView()
        self._list_model = _DatasetListModel(self)
        self._list_table.setModel(self._list_model)
        self._list_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list_table.setAlternatingRowColors(True)
        self._list_table.horizontalHeader().setStretchLastSection(True)
        self._list_table.selectionModel().selectionChanged.connect(self._on_dataset_selected)
        left_layout.addWidget(self._list_table)
        splitter.addWidget(left_widget)

        # Right Panel: Scrollable Workspace
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._ws_container = QWidget()
        self._ws_layout = QVBoxLayout(self._ws_container)
        self._ws_layout.setContentsMargins(8, 0, 0, 0)
        self._ws_layout.setSpacing(12)

        # Card 1: Overview
        self._card_overview = CardWidget("1. 선택된 데이터셋 개요 및 경로")
        self._lbl_ds_title = QLabel("등록된 데이터셋을 선택하세요.")
        self._lbl_ds_title.setProperty("subheading", "true")
        self._card_overview.content_layout.addWidget(self._lbl_ds_title)

        self._lbl_ds_paths = QLabel()
        self._lbl_ds_paths.setWordWrap(True)
        self._card_overview.content_layout.addWidget(self._lbl_ds_paths)
        self._ws_layout.addWidget(self._card_overview)

        # Card 2: Scan & Changes
        self._card_scan = CardWidget("2. 입력 폴더 스캔 및 변경 감지")
        scan_top = QHBoxLayout()
        self._lbl_scan_status = QLabel("스캔 대기 중")
        self._lbl_scan_status.setProperty("subheading", "true")
        scan_top.addWidget(self._lbl_scan_status)
        scan_top.addStretch()

        self._btn_scan = QPushButton("🔄 입력 폴더 다시 스캔")
        self._btn_scan.clicked.connect(self._run_scan)
        scan_top.addWidget(self._btn_scan)
        self._card_scan.content_layout.addLayout(scan_top)

        self._lbl_scan_summary = QLabel()
        self._lbl_scan_summary.setWordWrap(True)
        self._card_scan.content_layout.addWidget(self._lbl_scan_summary)
        self._ws_layout.addWidget(self._card_scan)

        # Card 3: Accumulate & Inspect
        self._card_inspect = CardWidget("3. 기간 누적 / 교체 및 수치 검수")
        inspect_top = QHBoxLayout()
        self._btn_accumulate = QPushButton("★ 1단계: 기간 누적 및 검수 실행")
        self._btn_accumulate.setProperty("primary", "true")
        self._btn_accumulate.clicked.connect(self._run_accumulation)
        inspect_top.addWidget(self._btn_accumulate)

        self._btn_approve = QPushButton("✓ 2단계: 검수 승인 및 최종 배포")
        self._btn_approve.setProperty("primary", "true")
        self._btn_approve.setEnabled(False)
        self._btn_approve.clicked.connect(self._run_publish)
        inspect_top.addWidget(self._btn_approve)
        inspect_top.addStretch()
        self._card_inspect.content_layout.addLayout(inspect_top)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._card_inspect.content_layout.addWidget(self._progress_bar)

        self._lbl_inspect_summary = QLabel("아직 누적/검수가 실행되지 않았습니다.")
        self._lbl_inspect_summary.setWordWrap(True)
        self._card_inspect.content_layout.addWidget(self._lbl_inspect_summary)
        self._ws_layout.addWidget(self._card_inspect)

        # Card 4: Publication & Excel
        self._card_pub = CardWidget("4. 배포 완료 파일 관리 및 Excel 연결")
        pub_top = QHBoxLayout()
        self._btn_open_folder = QPushButton("📂 배포 폴더 열기")
        self._btn_open_folder.clicked.connect(self._on_open_publish_folder)
        pub_top.addWidget(self._btn_open_folder)

        self._btn_open_csv = QPushButton("📄 배포 CSV 열기")
        self._btn_open_csv.clicked.connect(self._on_open_published_csv)
        pub_top.addWidget(self._btn_open_csv)

        self._btn_export_excel = QPushButton("📊 Excel 피벗 템플릿 생성")
        self._btn_export_excel.clicked.connect(self._on_export_excel_template)
        pub_top.addWidget(self._btn_export_excel)
        pub_top.addStretch()
        self._card_pub.content_layout.addLayout(pub_top)

        self._lbl_pub_info = QLabel()
        self._lbl_pub_info.setWordWrap(True)
        self._card_pub.content_layout.addWidget(self._lbl_pub_info)
        self._ws_layout.addWidget(self._card_pub)

        self._ws_layout.addStretch()
        scroll.setWidget(self._ws_container)
        splitter.addWidget(scroll)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter, stretch=1)

    def _update_step_gating(self) -> None:
        t = self._t()
        has_ds = self.current_dataset is not None
        has_valid_inspection = bool(has_ds and self.last_inspection and self.last_inspection.is_valid)
        has_pub_csv = bool(has_ds and self.current_dataset and self.current_dataset.published_csv_path().exists())

        self.btn_edit.setEnabled(has_ds)
        self.btn_del.setEnabled(has_ds)
        self._card_scan.setEnabled(has_ds)
        self._btn_scan.setEnabled(has_ds)
        self._card_inspect.setEnabled(has_ds)
        self._btn_accumulate.setEnabled(has_ds and not self._progress_bar.isVisible())
        self._btn_approve.setEnabled(has_valid_inspection and not self._progress_bar.isVisible())
        self._card_pub.setEnabled(has_ds)
        self._btn_open_folder.setEnabled(has_ds)
        self._btn_open_csv.setEnabled(has_pub_csv)
        self._btn_export_excel.setEnabled(has_pub_csv)

        if not has_ds:
            self.lbl_step_guide.setText(t.get("ds_step_guide_none", ""))
        elif has_valid_inspection:
            self.lbl_step_guide.setText(t.get("ds_step_guide_inspected", ""))
        else:
            self.lbl_step_guide.setText(t.get("ds_step_guide_selected", ""))

    # -------------------------------------------------------------------------
    # Dataset Management
    # -------------------------------------------------------------------------
    def refresh_dataset_list(self, select_id: Optional[str] = None) -> None:
        t = self._t()
        datasets = self.registry.list_datasets()
        periods_map: Dict[str, str] = {}
        status_map: Dict[str, str] = {}

        for ds in datasets:
            try:
                eng = DatasetEngine(ds, self.registry)
                pers = eng.get_existing_periods()
                if pers:
                    periods_map[ds.id] = f"{pers[0]} ~ {pers[-1]}" if len(pers) > 1 else pers[0]
                else:
                    periods_map[ds.id] = t.get("ds_no_data", "(데이터 없음)")
            except Exception:
                periods_map[ds.id] = "-"

            status_map[ds.id] = (
                t.get("ds_status_published", "배포됨")
                if ds.last_published_at
                else t.get("ds_status_ready", "준비됨")
            )

        self._list_model.set_datasets(datasets, periods_map, status_map)

        if datasets:
            target_row = 0
            if select_id:
                for idx, ds in enumerate(datasets):
                    if ds.id == select_id:
                        target_row = idx
                        break
            self._list_table.selectRow(target_row)
        else:
            self._clear_workspace()

    def _on_dataset_selected(self) -> None:
        indexes = self._list_table.selectionModel().selectedRows()
        if not indexes:
            self._clear_workspace()
            return

        row = indexes[0].row()
        ds = self._list_model.get_dataset(row)
        if not ds:
            self._clear_workspace()
            return
        self._select_dataset(ds)

    def _select_dataset(self, ds: DatasetDefinition) -> None:
        self.current_dataset = ds
        self.current_engine = DatasetEngine(ds, self.registry)
        self.last_inspection = None
        self._update_workspace()
        self._run_scan()
        self._update_step_gating()

    def _clear_workspace(self) -> None:
        t = self._t()
        self.current_dataset = None
        self.current_engine = None
        self.last_scan = None
        self.last_inspection = None
        self._lbl_ds_title.setText(t.get("ds_select_prompt", ""))
        self._lbl_ds_paths.setText("")
        self._lbl_scan_status.setText(t.get("ds_scan_waiting", ""))
        self._lbl_scan_summary.setText("")
        self._lbl_inspect_summary.setText(t.get("ds_inspect_waiting", ""))
        self._lbl_pub_info.setText(t.get("ds_pub_missing", ""))
        self._btn_approve.setEnabled(False)
        self._update_step_gating()

    def _update_workspace(self) -> None:
        t = self._t()
        if not self.current_dataset or not self.current_engine:
            self._clear_workspace()
            return
        ds = self.current_dataset
        unassigned = t.get("ds_unassigned", "(미지정)")

        self._lbl_ds_title.setText(t.get("ds_selected_title", "{name}").format(name=ds.name))
        lines = [
            t.get("ds_info_input", "").format(path=ds.input_folder),
            t.get("ds_info_publish", "").format(path=ds.publish_folder),
            t.get("ds_info_period", "").format(col=ds.period_column, fmt=ds.period_format),
            t.get("ds_info_keys", "").format(keys=", ".join(ds.key_columns) if ds.key_columns else unassigned),
            t.get("ds_info_nums", "").format(nums=", ".join(ds.numeric_columns) if ds.numeric_columns else unassigned),
        ]
        self._lbl_ds_paths.setText("<br>".join(lines))

        # Pub info
        pub_csv = ds.published_csv_path()
        if pub_csv.exists():
            stat = pub_csv.stat()
            self._lbl_pub_info.setText(
                t.get("ds_pub_exists", "").format(
                    name=pub_csv.name,
                    size=stat.st_size / 1024,
                    path=pub_csv,
                )
            )
        else:
            self._lbl_pub_info.setText(t.get("ds_pub_missing", ""))

    def _on_add_dataset(self) -> None:
        t = self._t()
        dlg = DatasetWizardDialog(registry=self.registry, parent=self, language_code=self.language_code)
        if dlg.exec() == DatasetWizardDialog.DialogCode.Accepted:
            new_ds = dlg.get_dataset()
            if new_ds:
                self.registry.save_dataset(new_ds)
                self.refresh_dataset_list(select_id=new_ds.id)
                info_dialog(
                    self,
                    t.get("ds_dlg_reg_title", "등록 완료"),
                    t.get("ds_dlg_reg_body", "").format(name=new_ds.name),
                )

    def _on_edit_dataset(self) -> None:
        t = self._t()
        if not self.current_dataset:
            warning_dialog(self, t.get("ds_dlg_sel_title", ""), t.get("ds_dlg_sel_edit", ""))
            return

        dlg = DatasetWizardDialog(
            dataset=self.current_dataset,
            registry=self.registry,
            parent=self,
            language_code=self.language_code,
        )
        if dlg.exec() == DatasetWizardDialog.DialogCode.Accepted:
            updated_ds = dlg.get_dataset()
            if updated_ds:
                self.registry.save_dataset(updated_ds)
                self.refresh_dataset_list(select_id=updated_ds.id)
                info_dialog(
                    self,
                    t.get("ds_dlg_edit_title", "수정 완료"),
                    t.get("ds_dlg_edit_body", "").format(name=updated_ds.name),
                )

    def _on_delete_dataset(self) -> None:
        t = self._t()
        if not self.current_dataset:
            warning_dialog(self, t.get("ds_dlg_sel_title", ""), t.get("ds_dlg_sel_del", ""))
            return

        name = self.current_dataset.name
        if confirm_dialog(
            self,
            t.get("ds_dlg_del_title", "데이터셋 삭제"),
            t.get("ds_dlg_del_confirm", "").format(name=name),
        ):
            self.registry.delete_dataset(self.current_dataset.id)
            self.current_dataset = None
            self.refresh_dataset_list()
            info_dialog(
                self,
                t.get("ds_dlg_del_done", "삭제 완료"),
                t.get("ds_dlg_del_body", "").format(name=name),
            )

    # -------------------------------------------------------------------------
    # Scan Operation
    # -------------------------------------------------------------------------
    def _run_scan(self) -> None:
        t = self._t()
        if not self.current_engine:
            return

        self._lbl_scan_status.setText(t.get("ds_scanning", "스캔 중..."))
        try:
            scan = self.current_engine.scan_input_folder()
            self.last_scan = scan
            self._render_scan_summary(scan)
        except Exception as e:
            self._lbl_scan_status.setText(t.get("ds_scan_failed", "스캔 실패"))
            self._lbl_scan_summary.setText(
                f"<font color='{PALETTE['danger']}'>{t.get('ds_scan_error', '{error}').format(error=e)}</font>"
            )

    def _render_scan_summary(self, scan: ScanSummary) -> None:
        t = self._t()
        new_cnt = len(scan.new_periods)
        mod_cnt = len(scan.modified_periods)
        unchanged_cnt = len(scan.unchanged_files)
        conflict_cnt = len(scan.conflicts)
        none_str = t.get("ds_scan_none", "없음")

        self._lbl_scan_status.setText(t.get("ds_scan_done", "").format(count=len(scan.files)))

        lines = [
            t.get("ds_scan_new", "").format(count=new_cnt, items=", ".join(scan.new_periods) if new_cnt else none_str),
            t.get("ds_scan_mod", "").format(count=mod_cnt, items=", ".join(scan.modified_periods) if mod_cnt else none_str),
            t.get("ds_scan_unchanged", "").format(count=unchanged_cnt),
        ]
        if conflict_cnt:
            lines.append(t.get("ds_scan_conflict", "").format(color=PALETTE["danger"], count=conflict_cnt))

        self._lbl_scan_summary.setText("<br>".join(lines))

    # -------------------------------------------------------------------------
    # Accumulation & Inspection
    # -------------------------------------------------------------------------
    def _run_accumulation(self) -> None:
        t = self._t()
        if not self.current_engine or not self.current_dataset:
            return

        self._btn_accumulate.setEnabled(False)
        self._btn_approve.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(10)
        self._lbl_inspect_summary.setText(t.get("ds_inspect_running", ""))

        engine = self.current_engine

        def worker(report):
            return engine.accumulate_and_inspect(progress_callback=report)

        def on_success(result: InspectionResult):
            self.last_inspection = result
            self._progress_bar.setVisible(False)
            self._render_inspection_summary(result)
            self._update_step_gating()

        def on_error(err: Exception):
            t_err = self._t()
            self._progress_bar.setVisible(False)
            self._lbl_inspect_summary.setText(
                f"<font color='{PALETTE['danger']}'>{t_err.get('ds_inspect_err', '{error}').format(error=err)}</font>"
            )
            self._update_step_gating()
            error_dialog(
                self,
                t_err.get("ds_dlg_acc_err_title", "누적 오류"),
                t_err.get("ds_dlg_acc_err_body", "{error}").format(error=err),
            )

        self.job_runner.start(
            f"accumulate_{self.current_dataset.id}",
            worker,
            JobCallbacks(
                on_progress=lambda pct, msg: self._progress_bar.setValue(pct),
                on_success=on_success,
                on_error=on_error,
                on_finished=lambda: None,
            ),
        )

    def _render_inspection_summary(self, result: InspectionResult) -> None:
        t = self._t()
        if result.is_valid:
            periods = result.all_periods or []
            period_range = f" ({periods[0]} ~ {periods[-1]})" if periods else ""
            lines = [
                f"<font color='{PALETTE['success']}'><b>{t.get('ds_inspect_ok', '')}</b></font>",
                t.get("ds_inspect_rows", "").format(
                    after=result.after_row_count,
                    delta=result.after_row_count - result.before_row_count,
                ),
                t.get("ds_inspect_periods", "").format(count=len(periods), range_str=period_range),
            ]
            if result.numeric_deltas:
                lines.append(t.get("ds_inspect_deltas", ""))
                for k, v in result.numeric_deltas.items():
                    lines.append(f"  - {k}: {v:+,.2f}")
            self._lbl_inspect_summary.setText("<br>".join(lines))
        else:
            lines = [
                f"<font color='{PALETTE['danger']}'><b>{t.get('ds_inspect_fail', '')}</b></font>",
            ]
            for err in result.errors:
                lines.append(f"• <font color='{PALETTE['danger']}'>{err}</font>")
            self._lbl_inspect_summary.setText("<br>".join(lines))

    # -------------------------------------------------------------------------
    # Publish Operation
    # -------------------------------------------------------------------------
    def _run_publish(self) -> None:
        t = self._t()
        if not self.current_engine or not self.last_inspection or not self.current_dataset:
            return

        token = self.last_inspection.approval_token
        try:
            self.current_engine.approve_inspection(token)
            manifest = self.current_engine.publish_dataset()
            self.registry.save_dataset(self.current_dataset)
            self.last_inspection = None
            self.refresh_dataset_list(select_id=self.current_dataset.id)
            pub_csv = self.current_dataset.published_csv_path()
            version_str = manifest.get("version", "")
            info_dialog(
                self,
                t.get("ds_dlg_pub_ok_title", "배포 성공"),
                t.get("ds_dlg_pub_ok_body", "").format(version=version_str, path=pub_csv),
            )
        except DatasetPublishLockError as lock_err:
            warning_dialog(
                self,
                t.get("ds_dlg_pub_lock_title", "배포 파일 잠김"),
                t.get("ds_dlg_pub_lock_body", "").format(error=lock_err),
            )
        except Exception as e:
            error_dialog(
                self,
                t.get("ds_dlg_pub_err_title", "배포 실패"),
                t.get("ds_dlg_pub_err_body", "").format(error=e),
            )

    # -------------------------------------------------------------------------
    # Open / Export Helpers
    # -------------------------------------------------------------------------
    def _on_open_publish_folder(self) -> None:
        t = self._t()
        if self.current_dataset:
            pub_dir = Path(self.current_dataset.publish_folder)
            if pub_dir.exists():
                open_containing_folder(str(pub_dir))
            else:
                warning_dialog(
                    self,
                    t.get("ds_dlg_no_folder_title", ""),
                    t.get("ds_dlg_no_folder_body", "").format(path=pub_dir),
                )

    def _on_open_published_csv(self) -> None:
        t = self._t()
        if self.current_dataset:
            csv_path = self.current_dataset.published_csv_path()
            if csv_path.exists():
                os.startfile(str(csv_path))
            else:
                warning_dialog(self, t.get("ds_dlg_no_csv_title", ""), t.get("ds_dlg_no_csv_body", ""))

    def _on_export_excel_template(self) -> None:
        t = self._t()
        if not self.current_dataset:
            return
        csv_path = self.current_dataset.published_csv_path()
        if not csv_path.exists():
            warning_dialog(self, t.get("ds_dlg_need_pub_title", ""), t.get("ds_dlg_need_pub_body", ""))
            return

        out_xlsx = csv_path.parent / f"{self.current_dataset.published_filename.replace('.csv', '')}_Template.xlsx"
        try:
            create_excel_template_workbook(out_xlsx, csv_path, self.current_dataset)
            info_dialog(
                self,
                t.get("ds_dlg_excel_ok_title", ""),
                t.get("ds_dlg_excel_ok_body", "").format(path=out_xlsx),
            )
            open_containing_folder(str(out_xlsx))
        except Exception as e:
            error_dialog(
                self,
                t.get("ds_dlg_excel_err_title", ""),
                t.get("ds_dlg_excel_err_body", "").format(error=e),
            )

    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        t = self._t()

        self.btn_add.setText(t.get("ds_btn_add", "✨ 새 데이터셋 등록"))
        self.btn_edit.setText(t.get("ds_btn_edit", "⚙ 설정 수정"))
        self.btn_del.setText(t.get("ds_btn_del", "삭제"))
        self._list_model.set_headers(t.get("ds_list_cols", _DatasetListModel.COLUMNS))

        self._card_overview.set_title(t.get("ds_card_1", "1. 선택된 데이터셋 개요 및 경로"))
        self._card_scan.set_title(t.get("ds_card_2", "2. 입력 폴더 스캔 및 변경 감지"))
        self._btn_scan.setText(t.get("ds_btn_rescan", "🔄 입력 폴더 다시 스캔"))
        self._card_inspect.set_title(t.get("ds_card_3", "3. 기간 누적 / 교체 및 수치 검수"))
        self._btn_accumulate.setText(t.get("ds_btn_accumulate", "★ 1단계: 기간 누적 및 검수 실행"))
        self._btn_approve.setText(t.get("ds_btn_approve", "✓ 2단계: 검수 승인 및 최종 배포"))
        self._card_pub.set_title(t.get("ds_card_4", "4. 배포 완료 파일 관리 및 Excel 연결"))
        self._btn_open_folder.setText(t.get("ds_btn_open_folder", "📂 배포 폴더 열기"))
        self._btn_open_csv.setText(t.get("ds_btn_open_csv", "📄 배포 CSV 열기"))
        self._btn_export_excel.setText(t.get("ds_btn_export_excel", "📊 Excel 피벗 템플릿 생성"))

        current_id = self.current_dataset.id if self.current_dataset else None
        saved_inspection = self.last_inspection
        self.refresh_dataset_list(select_id=current_id)
        if saved_inspection and self.current_dataset:
            self.last_inspection = saved_inspection
            self._render_inspection_summary(saved_inspection)
        elif self.current_dataset:
            self._lbl_inspect_summary.setText(t.get("ds_inspect_waiting", ""))
        self._update_step_gating()

