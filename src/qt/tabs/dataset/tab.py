"""PySide6 Tab Implementation for '데이터셋 배포' (Dataset Publisher).

Integrates DatasetRegistry, DatasetEngine, DatasetWizardDialog, and BackgroundJobRunner.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
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
    DatasetValidationError,
    InspectionResult,
    ScanSummary,
)
from src.dataset_excel import create_excel_template_workbook
from src.file_reveal import open_containing_folder
from src.qt.dialogs import confirm_dialog, error_dialog, info_dialog, warning_dialog
from src.qt.jobs import create_qt_job_runner
from src.qt.tabs.dataset.wizard import DatasetWizardDialog
from src.qt.widgets.badge import StatusBadge
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
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section]
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
                return self._status_map.get(ds.id, "준비됨")
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
        self.refresh_dataset_list()
        self.apply_language(self.language_code)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

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
        btn_add = QPushButton("✨ 새 데이터셋 등록")
        btn_add.setProperty("primary", "true")
        btn_add.clicked.connect(self._on_add_dataset)
        hdr_actions.addWidget(btn_add)

        btn_edit = QPushButton("마법사 수정")
        btn_edit.clicked.connect(self._on_edit_dataset)
        hdr_actions.addWidget(btn_edit)

        btn_del = QPushButton("삭제")
        btn_del.setProperty("danger", "true")
        btn_del.clicked.connect(self._on_delete_dataset)
        hdr_actions.addWidget(btn_del)
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
        self._card_overview = CardWidget("1. 데이터셋 개요 및 경로")
        self._lbl_ds_title = QLabel("등록된 데이터셋을 선택하세요.")
        self._lbl_ds_title.setProperty("subheading", "true")
        self._card_overview.content_layout.addWidget(self._lbl_ds_title)

        self._lbl_ds_paths = QLabel()
        self._lbl_ds_paths.setWordWrap(True)
        self._card_overview.content_layout.addWidget(self._lbl_ds_paths)
        self._ws_layout.addWidget(self._card_overview)

        # Card 2: Scan & Changes
        self._card_scan = CardWidget("2. 입력 파일 스캔 및 변경 감지")
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
        self._btn_accumulate = QPushButton("★ 기간 누적 및 검수 실행")
        self._btn_accumulate.setProperty("primary", "true")
        self._btn_accumulate.clicked.connect(self._run_accumulation)
        inspect_top.addWidget(self._btn_accumulate)

        self._btn_approve = QPushButton("✓ 검수 승인 및 Excel 배포")
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
        self._card_pub = CardWidget("4. 배포 관리 및 Excel 연결")
        pub_top = QHBoxLayout()
        self._btn_open_folder = QPushButton("📂 배포 폴더 열기")
        self._btn_open_folder.clicked.connect(self._on_open_publish_folder)
        pub_top.addWidget(self._btn_open_folder)

        self._btn_open_csv = QPushButton("📄 배포 CSV 열기")
        self._btn_open_csv.clicked.connect(self._on_open_published_csv)
        pub_top.addWidget(self._btn_open_csv)

        self._btn_export_excel = QPushButton("📊 Excel 템플릿 생성")
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
        main_layout.addWidget(splitter)

    # -------------------------------------------------------------------------
    # Dataset Management
    # -------------------------------------------------------------------------
    def refresh_dataset_list(self, select_id: Optional[str] = None) -> None:
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
                    periods_map[ds.id] = "(데이터 없음)"
            except Exception:
                periods_map[ds.id] = "-"

            status_map[ds.id] = "배포됨" if ds.last_published_at else "준비됨"

        self._list_model.set_datasets(datasets, periods_map, status_map)

        if datasets:
            target_row = 0
            if select_id:
                for idx, ds in enumerate(datasets):
                    if ds.id == select_id:
                        target_row = idx
                        break
            self._list_table.selectRow(target_row)

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
        self._update_workspace()
        self._run_scan()

    def _clear_workspace(self) -> None:
        self.current_dataset = None
        self.current_engine = None
        self._lbl_ds_title.setText("등록된 데이터셋을 선택하세요.")
        self._lbl_ds_paths.setText("")
        self._lbl_scan_summary.setText("")
        self._lbl_inspect_summary.setText("아직 누적/검수가 실행되지 않았습니다.")
        self._btn_approve.setEnabled(False)

    def _update_workspace(self) -> None:
        if not self.current_dataset or not self.current_engine:
            return
        ds = self.current_dataset

        self._lbl_ds_title.setText(f"선택된 데이터셋: <b>{ds.name}</b>")
        lines = [
            f"• <b>입력 폴더:</b> {ds.input_folder}",
            f"• <b>배포 폴더:</b> {ds.publish_folder}",
            f"• <b>기준 기간:</b> {ds.period_column} ({ds.period_format})",
            f"• <b>식별 키:</b> {', '.join(ds.key_columns) if ds.key_columns else '(미지정)'}",
            f"• <b>수치 컬럼:</b> {', '.join(ds.numeric_columns) if ds.numeric_columns else '(미지정)'}",
        ]
        self._lbl_ds_paths.setText("<br>".join(lines))

        # Pub info
        pub_csv = ds.published_csv_path()
        if pub_csv.exists():
            stat = pub_csv.stat()
            self._lbl_pub_info.setText(
                f"✓ 배포 파일 존재: <b>{pub_csv.name}</b> ({stat.st_size / 1024:.1f} KB)<br>"
                f"위치: {pub_csv}"
            )
        else:
            self._lbl_pub_info.setText("아직 배포된 파일이 없습니다. [검수 승인 및 Excel 배포]를 실행하세요.")

    def _on_add_dataset(self) -> None:
        dlg = DatasetWizardDialog(registry=self.registry, parent=self)
        if dlg.exec() == DatasetWizardDialog.DialogCode.Accepted:
            new_ds = dlg.get_dataset()
            if new_ds:
                self.registry.save_dataset(new_ds)
                self.refresh_dataset_list(select_id=new_ds.id)
                info_dialog(self, "등록 완료", f"데이터셋 '{new_ds.name}'이(가) 성공적으로 등록되었습니다.")

    def _on_edit_dataset(self) -> None:
        if not self.current_dataset:
            warning_dialog(self, "선택 필요", "수정할 데이터셋을 먼저 선택해 주세요.")
            return

        dlg = DatasetWizardDialog(dataset=self.current_dataset, registry=self.registry, parent=self)
        if dlg.exec() == DatasetWizardDialog.DialogCode.Accepted:
            updated_ds = dlg.get_dataset()
            if updated_ds:
                self.registry.save_dataset(updated_ds)
                self.refresh_dataset_list(select_id=updated_ds.id)
                info_dialog(self, "수정 완료", f"데이터셋 '{updated_ds.name}' 설정이 갱신되었습니다.")

    def _on_delete_dataset(self) -> None:
        if not self.current_dataset:
            warning_dialog(self, "선택 필요", "삭제할 데이터셋을 먼저 선택해 주세요.")
            return

        name = self.current_dataset.name
        if confirm_dialog(self, "데이터셋 삭제", f"정말로 데이터셋 '{name}' 및 로컬 축적 데이터를 모두 삭제하시겠습니까?"):
            self.registry.delete_dataset(self.current_dataset.id)
            self.current_dataset = None
            self.refresh_dataset_list()
            info_dialog(self, "삭제 완료", f"데이터셋 '{name}'이(가) 삭제되었습니다.")

    # -------------------------------------------------------------------------
    # Scan Operation
    # -------------------------------------------------------------------------
    def _run_scan(self) -> None:
        if not self.current_engine:
            return

        self._lbl_scan_status.setText("스캔 중...")
        try:
            scan = self.current_engine.scan_input_folder()
            self.last_scan = scan

            new_cnt = len(scan.new_periods)
            mod_cnt = len(scan.modified_periods)
            unchanged_cnt = len(scan.unchanged_files)
            conflict_cnt = len(scan.conflicts)

            status_str = f"스캔 완료: 총 {len(scan.files)}개 파일"
            self._lbl_scan_status.setText(status_str)

            lines = [
                f"• <b>새로운 기간:</b> {new_cnt}개 ({', '.join(scan.new_periods) if new_cnt else '없음'})",
                f"• <b>수정/갱신 기간:</b> {mod_cnt}개 ({', '.join(scan.modified_periods) if mod_cnt else '없음'})",
                f"• <b>변경 없는 파일:</b> {unchanged_cnt}개",
            ]
            if conflict_cnt:
                lines.append(f"• <font color='{PALETTE['danger']}'><b>충돌/에러 파일:</b> {conflict_cnt}개</font>")

            self._lbl_scan_summary.setText("<br>".join(lines))
        except Exception as e:
            self._lbl_scan_status.setText("스캔 실패")
            self._lbl_scan_summary.setText(f"<font color='{PALETTE['danger']}'>스캔 오류: {e}</font>")

    # -------------------------------------------------------------------------
    # Accumulation & Inspection
    # -------------------------------------------------------------------------
    def _run_accumulation(self) -> None:
        if not self.current_engine:
            return

        self._btn_accumulate.setEnabled(False)
        self._btn_approve.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(10)
        self._lbl_inspect_summary.setText("기간 누적 및 수치 검수 진행 중...")

        engine = self.current_engine

        def worker(report):
            return engine.accumulate_and_inspect(progress_callback=report)

        def on_success(result: InspectionResult):
            self.last_inspection = result
            self._btn_accumulate.setEnabled(True)
            self._progress_bar.setVisible(False)

            if result.is_valid:
                self._btn_approve.setEnabled(True)
                periods = result.all_periods or []
                period_range = f" ({periods[0]} ~ {periods[-1]})" if periods else ""
                lines = [
                    f"<font color='{PALETTE['success']}'><b>✓ 검수 완료: 모든 무결성 및 수치 검증 통과</b></font>",
                    f"• <b>반영 후 총 행 수:</b> {result.after_row_count:,}행 (이전 대비: {result.after_row_count - result.before_row_count:+,}행)",
                    f"• <b>기간 수:</b> {len(periods)}개{period_range}",
                ]
                if result.numeric_deltas:
                    lines.append("• <b>수치 합계 변동:</b>")
                    for k, v in result.numeric_deltas.items():
                        lines.append(f"  - {k}: {v:+,.2f}")
                self._lbl_inspect_summary.setText("<br>".join(lines))
            else:
                self._btn_approve.setEnabled(False)
                lines = [
                    f"<font color='{PALETTE['danger']}'><b>⚠️ 검수 실패: 데이터에 오류가 발견되어 배포가 차단되었습니다.</b></font>",
                ]
                for err in result.errors:
                    lines.append(f"• <font color='{PALETTE['danger']}'>{err}</font>")
                self._lbl_inspect_summary.setText("<br>".join(lines))

        def on_error(err: Exception):
            self._btn_accumulate.setEnabled(True)
            self._btn_approve.setEnabled(False)
            self._progress_bar.setVisible(False)
            self._lbl_inspect_summary.setText(f"<font color='{PALETTE['danger']}'>누적 실패: {err}</font>")
            error_dialog(self, "누적 오류", f"기간 누적 중 오류가 발생했습니다:\n{err}")

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

    # -------------------------------------------------------------------------
    # Publish Operation
    # -------------------------------------------------------------------------
    def _run_publish(self) -> None:
        if not self.current_engine or not self.last_inspection:
            return

        token = self.last_inspection.approval_token
        try:
            self.current_engine.approve_inspection(token)
            manifest = self.current_engine.publish_dataset()
            self.registry.save_dataset(self.current_dataset)
            self._update_workspace()
            self._btn_approve.setEnabled(False)
            pub_csv = self.current_dataset.published_csv_path()
            version_str = manifest.get("version", "")
            info_dialog(
                self,
                "배포 성공",
                f"데이터셋이 성공적으로 배포되었습니다! (버전: {version_str})\n\n저장 경로:\n{pub_csv}",
            )
        except DatasetPublishLockError as lock_err:
            warning_dialog(
                self,
                "배포 파일 잠김",
                f"배포 대상 파일이 다른 프로그램(예: Excel)에서 열려 있어 갱신하지 못했습니다.\n"
                f"파일을 닫은 후 다시 시도해 주세요:\n{lock_err}",
            )
        except Exception as e:
            error_dialog(self, "배포 실패", f"배포 중 오류가 발생했습니다:\n{e}")

    # -------------------------------------------------------------------------
    # Open / Export Helpers
    # -------------------------------------------------------------------------
    def _on_open_publish_folder(self) -> None:
        if self.current_dataset:
            pub_dir = Path(self.current_dataset.publish_folder)
            if pub_dir.exists():
                open_containing_folder(str(pub_dir))
            else:
                warning_dialog(self, "폴더 없음", f"배포 폴더가 아직 생성되지 않았습니다:\n{pub_dir}")

    def _on_open_published_csv(self) -> None:
        if self.current_dataset:
            csv_path = self.current_dataset.published_csv_path()
            if csv_path.exists():
                os.startfile(str(csv_path))
            else:
                warning_dialog(self, "파일 없음", "아직 배포된 CSV 파일이 없습니다.")

    def _on_export_excel_template(self) -> None:
        if not self.current_dataset:
            return
        csv_path = self.current_dataset.published_csv_path()
        if not csv_path.exists():
            warning_dialog(self, "배포 필요", "Excel 템플릿을 생성하려면 먼저 데이터셋을 배포해야 합니다.")
            return

        out_xlsx = csv_path.parent / f"{self.current_dataset.published_filename.replace('.csv', '')}_Template.xlsx"
        try:
            create_excel_template_workbook(out_xlsx, csv_path, self.current_dataset)
            info_dialog(self, "Excel 템플릿 생성 완료", f"Excel 템플릿이 성공적으로 생성되었습니다:\n{out_xlsx}")
            open_containing_folder(str(out_xlsx))
        except Exception as e:
            error_dialog(self, "템플릿 생성 실패", f"Excel 템플릿 생성 중 오류가 발생했습니다:\n{e}")

    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        # Tab-level dynamic UI strings can be refreshed here if needed
