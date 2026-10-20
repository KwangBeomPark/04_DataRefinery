"""PySide6 implementation for 'DWH & 손익 마트' (Kimball Star Schema DWH & AI Mart Pipeline)."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import shutil
import time
from typing import Optional

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.background_jobs import BackgroundJobRunner, JobCallbacks
from src.config_loader import load_account_mapping, load_settings
from src.dimension_builder import DimensionBuilder
from src.dwh_template import create_dwh_excel_template
from src.extractor import DataExtractor
from src.fact_builder import FactBuilder
from src.file_reveal import open_containing_folder
from src.i18n import _UI_TEXT
from src.loader import DwhLoader
from src.qt.dialogs import (
    choose_file_dialog,
    confirm_dialog,
    error_dialog,
    info_dialog,
    save_file_dialog,
)
from src.qt.widgets.card import CardWidget
from src.qt.widgets.dwh_guide_dialog import DwhGuideDialog
from src.qt.widgets.dwh_preview_dialog import DwhPreviewDialog
from src.ui_components import PALETTE


class DwhPipelineTab(QWidget):
    """Interactive GUI tab for Kimball Star Schema DWH & AI Mart Pipeline."""

    def __init__(
        self,
        language_code: str = "ko",
        job_runner: Optional[BackgroundJobRunner] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.language_code = language_code
        self.job_runner = job_runner
        self._last_error_log: Optional[Path] = None
        self._last_parquet_path: Optional[Path] = None

        self._init_ui()
        self.apply_language(self.language_code)
        self._check_source_files()

    def _init_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 12)
        outer_layout.setSpacing(10)

        # 0. Order of Operations Guide Banner & Beginner Manual Button
        banner_container = QHBoxLayout()
        banner_container.setSpacing(8)

        self.banner_guide = QLabel()
        self.banner_guide.setStyleSheet(
            f"background-color: #EBF3FC; color: {PALETTE['navy']}; "
            f"border: 1px solid #BDD7EE; border-radius: 6px; padding: 10px; font-weight: bold;"
        )
        self.banner_guide.setWordWrap(True)
        banner_container.addWidget(self.banner_guide, stretch=1)

        self.btn_open_guide = QPushButton()
        self.btn_open_guide.setStyleSheet(
            "QPushButton { background-color: #1E3A8A; color: white; font-weight: bold; padding: 8px 14px; border-radius: 6px; border: none; }"
            "QPushButton:hover { background-color: #2563EB; }"
        )
        self.btn_open_guide.clicked.connect(self._on_open_guide)
        banner_container.addWidget(self.btn_open_guide)

        outer_layout.addLayout(banner_container)

        # Scroll area for compact displays
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_widget = QWidget()
        scroll_layout = QVBoxLayout(content_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)

        # -------------------------------------------------------------
        # Card 1: Source Masters & P&L File Verification (Step 1)
        # -------------------------------------------------------------
        self.card_sources = CardWidget()
        c1_layout = self.card_sources.content_layout
        c1_layout.setSpacing(8)

        # Status rows with smart file importers
        self.lbl_cust_status = QLabel()
        self.btn_import_cust = QPushButton()
        self.btn_import_cust.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.btn_import_cust.clicked.connect(lambda: self._on_import_file("customer"))
        row_cust = QHBoxLayout()
        row_cust.addWidget(self.lbl_cust_status, stretch=1)
        row_cust.addWidget(self.btn_import_cust)
        c1_layout.addLayout(row_cust)

        self.lbl_prod_status = QLabel()
        self.btn_import_prod = QPushButton()
        self.btn_import_prod.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.btn_import_prod.clicked.connect(lambda: self._on_import_file("product"))
        row_prod = QHBoxLayout()
        row_prod.addWidget(self.lbl_prod_status, stretch=1)
        row_prod.addWidget(self.btn_import_prod)
        c1_layout.addLayout(row_prod)

        self.lbl_pnl_status = QLabel()
        self.btn_import_pnl = QPushButton()
        self.btn_import_pnl.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self.btn_import_pnl.clicked.connect(lambda: self._on_import_file("pnl"))
        row_pnl = QHBoxLayout()
        row_pnl.addWidget(self.lbl_pnl_status, stretch=1)
        row_pnl.addWidget(self.btn_import_pnl)
        c1_layout.addLayout(row_pnl)

        self.lbl_config_summary = QLabel()
        self.lbl_config_summary.setStyleSheet("color: #555555; font-size: 11px;")
        c1_layout.addWidget(self.lbl_config_summary)

        # Buttons row
        btn_row = QHBoxLayout()
        self.btn_download_template = QPushButton()
        self.btn_download_template.clicked.connect(self._on_download_template)
        btn_row.addWidget(self.btn_download_template)

        self.btn_gen_sample = QPushButton()
        self.btn_gen_sample.setStyleSheet("font-weight: bold;")
        self.btn_gen_sample.clicked.connect(self._on_generate_sample)
        btn_row.addWidget(self.btn_gen_sample)

        self.btn_refresh = QPushButton()
        self.btn_refresh.clicked.connect(self._check_source_files)
        btn_row.addWidget(self.btn_refresh)

        self.btn_open_source_dir = QPushButton()
        self.btn_open_source_dir.clicked.connect(self._on_open_source_folder)
        btn_row.addWidget(self.btn_open_source_dir)
        btn_row.addStretch()

        c1_layout.addLayout(btn_row)
        scroll_layout.addWidget(self.card_sources)

        # -------------------------------------------------------------
        # Card 2: DWH Pipeline Execution & Fail-Fast Verification (Step 2)
        # -------------------------------------------------------------
        self.card_pipeline = CardWidget()
        c2_layout = self.card_pipeline.content_layout
        c2_layout.setSpacing(10)

        # Build Button
        self.btn_run_dwh = QPushButton()
        self.btn_run_dwh.setStyleSheet(
            f"QPushButton {{ background-color: {PALETTE['accent']}; color: white; font-size: 13px; font-weight: bold; padding: 10px; border-radius: 6px; border: none; }}"
            f"QPushButton:hover {{ background-color: #1D4ED8; }}"
            f"QPushButton:disabled {{ background-color: #CBD5E1; color: #94A3B8; }}"
        )
        self.btn_run_dwh.clicked.connect(self._on_run_dwh_pipeline)
        c2_layout.addWidget(self.btn_run_dwh)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 5)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        c2_layout.addWidget(self.progress_bar)

        # 5 Steps Stepper Checklist
        self.stepper_box = QVBoxLayout()
        self.stepper_box.setSpacing(4)
        self.lbl_step1 = QLabel()
        self.lbl_step2 = QLabel()
        self.lbl_step3 = QLabel()
        self.lbl_step4 = QLabel()
        self.lbl_step5 = QLabel()

        for lbl in (self.lbl_step1, self.lbl_step2, self.lbl_step3, self.lbl_step4, self.lbl_step5):
            lbl.setStyleSheet("color: #666666; font-size: 11px;")
            self.stepper_box.addWidget(lbl)

        c2_layout.addLayout(self.stepper_box)

        # Error notification card (hidden by default)
        self.error_frame = QFrame()
        self.error_frame.setStyleSheet(
            "background-color: #FDF2F2; border: 1px solid #F8B4B4; border-radius: 6px; padding: 8px;"
        )
        err_box = QVBoxLayout(self.error_frame)
        self.lbl_err_title = QLabel()
        self.lbl_err_title.setStyleSheet("color: #9B1C1C; font-weight: bold;")
        self.lbl_err_msg = QLabel()
        self.lbl_err_msg.setWordWrap(True)
        self.lbl_err_msg.setStyleSheet("color: #771D1D; font-size: 11px;")
        err_box.addWidget(self.lbl_err_title)
        err_box.addWidget(self.lbl_err_msg)

        err_btn_row = QHBoxLayout()
        self.btn_open_masters = QPushButton()
        self.btn_open_masters.clicked.connect(self._on_open_masters_folder)
        self.btn_open_err_log = QPushButton()
        self.btn_open_err_log.clicked.connect(self._on_open_error_log)
        err_btn_row.addWidget(self.btn_open_masters)
        err_btn_row.addWidget(self.btn_open_err_log)
        err_btn_row.addStretch()
        err_box.addLayout(err_btn_row)

        self.error_frame.setVisible(False)
        c2_layout.addWidget(self.error_frame)

        scroll_layout.addWidget(self.card_pipeline)

        # -------------------------------------------------------------
        # Card 3: Excel Power Query Serving & Results (Step 3)
        # -------------------------------------------------------------
        self.card_excel = CardWidget()
        c3_layout = self.card_excel.content_layout
        c3_layout.setSpacing(10)

        self.lbl_parquet_ready = QLabel()
        self.lbl_parquet_ready.setStyleSheet("font-weight: bold; color: #333333;")
        c3_layout.addWidget(self.lbl_parquet_ready)

        c3_btn_row = QHBoxLayout()
        self.btn_preview_mart = QPushButton()
        self.btn_preview_mart.setStyleSheet(
            "QPushButton { background-color: #047857; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #059669; }"
            "QPushButton:disabled { background-color: #9CA3AF; color: #E5E7EB; }"
        )
        self.btn_preview_mart.clicked.connect(self._on_preview_mart)
        self.btn_copy_mcode = QPushButton()
        self.btn_copy_mcode.setStyleSheet("font-weight: bold;")
        self.btn_copy_mcode.clicked.connect(self._on_copy_mcode)
        self.btn_copy_parquet_path = QPushButton()
        self.btn_copy_parquet_path.clicked.connect(self._on_copy_parquet_path)
        self.btn_open_export_folder = QPushButton()
        self.btn_open_export_folder.clicked.connect(self._on_open_export_folder)

        c3_btn_row.addWidget(self.btn_preview_mart)
        c3_btn_row.addWidget(self.btn_copy_mcode)
        c3_btn_row.addWidget(self.btn_copy_parquet_path)
        c3_btn_row.addWidget(self.btn_open_export_folder)
        c3_btn_row.addStretch()
        c3_layout.addLayout(c3_btn_row)

        # Excel Connection Guide Frame
        self.guide_frame = QFrame()
        self.guide_frame.setStyleSheet(
            "background-color: #F8F9FA; border: 1px dashed #CED4DA; border-radius: 6px; padding: 10px;"
        )
        guide_layout = QVBoxLayout(self.guide_frame)
        self.lbl_guide_title = QLabel()
        self.lbl_guide_title.setStyleSheet("font-weight: bold; color: #1E3A8A;")
        self.lbl_guide_text = QLabel()
        self.lbl_guide_text.setWordWrap(True)
        self.lbl_guide_text.setStyleSheet("color: #4B5563; font-size: 11px; line-height: 1.4;")
        guide_layout.addWidget(self.lbl_guide_title)
        guide_layout.addWidget(self.lbl_guide_text)
        c3_layout.addWidget(self.guide_frame)

        scroll_layout.addWidget(self.card_excel)

        scroll.setWidget(content_widget)
        outer_layout.addWidget(scroll, stretch=1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._check_source_files()

    # -----------------------------------------------------------------
    # Source Detection & Step-Gating
    # -----------------------------------------------------------------
    def _check_source_files(self) -> None:
        """Inspect presence of master and P&L raw files and apply step-gating."""
        try:
            settings = load_settings()
            rules = load_account_mapping()
        except Exception:
            return

        cust_path = settings.masters_dir / "master_customers.csv"
        prod_path = settings.masters_dir / "master_products.csv"
        pnl_files = list(settings.pnl_dir.glob("*.csv"))

        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])

        def safe_count_lines(path: Path) -> int:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return sum(1 for _ in f) - 1
            except Exception:
                return 0

        # Customer master status
        if cust_path.is_file():
            cust_lines = safe_count_lines(cust_path)
            detail = t.get("dwh_cust_count", "{name} ({count:,} customers)").format(name=cust_path.name, count=cust_lines)
            self.lbl_cust_status.setText(f"✔ {t.get('dwh_card1_cust', '고객 마스터:')} {detail}")
            self.lbl_cust_status.setStyleSheet(
                "background-color: #ECFDF5; color: #065F46; border: 1px solid #A7F3D0; border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
            has_cust = True
        else:
            missing_msg = t.get("dwh_cust_missing", "master_customers.csv missing")
            self.lbl_cust_status.setText(f"⚠ {t.get('dwh_card1_cust', '고객 마스터:')} {missing_msg}")
            self.lbl_cust_status.setStyleSheet(
                "background-color: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
            has_cust = False

        # Product master status
        if prod_path.is_file():
            prod_lines = safe_count_lines(prod_path)
            detail = t.get("dwh_prod_count", "{name} ({count:,} products)").format(name=prod_path.name, count=prod_lines)
            self.lbl_prod_status.setText(f"✔ {t.get('dwh_card1_prod', '제품 마스터:')} {detail}")
            self.lbl_prod_status.setStyleSheet(
                "background-color: #ECFDF5; color: #065F46; border: 1px solid #A7F3D0; border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
            has_prod = True
        else:
            missing_msg = t.get("dwh_prod_missing", "master_products.csv missing")
            self.lbl_prod_status.setText(f"⚠ {t.get('dwh_card1_prod', '제품 마스터:')} {missing_msg}")
            self.lbl_prod_status.setStyleSheet(
                "background-color: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
            has_prod = False

        # P&L files status
        if pnl_files:
            total_pnl_lines = sum(safe_count_lines(p) for p in pnl_files)
            detail = t.get("dwh_pnl_count", "{files} file(s), {rows:,} total rows").format(files=len(pnl_files), rows=total_pnl_lines)
            self.lbl_pnl_status.setText(f"✔ {t.get('dwh_card1_pnl', 'P&L 실적 원천:')} {detail}")
            self.lbl_pnl_status.setStyleSheet(
                "background-color: #ECFDF5; color: #065F46; border: 1px solid #A7F3D0; border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
            has_pnl = True
        else:
            missing_msg = t.get("dwh_pnl_missing", "No CSV files found in data/01_raw/pnl/")
            self.lbl_pnl_status.setText(f"⚠ {t.get('dwh_card1_pnl', 'P&L 실적 원천:')} {missing_msg}")
            self.lbl_pnl_status.setStyleSheet(
                "background-color: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; border-radius: 4px; padding: 4px 8px; font-weight: bold;"
            )
            has_pnl = False

        # Config summary
        cfg_tmpl = t.get("dwh_card1_config", "법인/통화: {entity} ({currency}) · 계정 롤업 규칙: {accounts}개 로드됨")
        self.lbl_config_summary.setText(
            cfg_tmpl.format(
                entity=settings.entity_code,
                currency=settings.reporting_currency,
                accounts=len(rules),
            )
        )

        # Step-Gating: Enable build button ONLY if all sources exist
        is_ready = has_cust and has_prod and has_pnl
        self.btn_run_dwh.setEnabled(is_ready)
        if is_ready:
            self.btn_run_dwh.setToolTip(t.get("dwh_build_tooltip_ready", ""))
        else:
            self.btn_run_dwh.setToolTip(t.get("dwh_build_tooltip_disabled", "Please ensure customer, product masters, and P&L source files exist."))

        # Check existing Parquet file
        parquet_file = settings.mart_export_dir / "v_ai_pnl_summary.parquet"
        if parquet_file.is_file():
            self._last_parquet_path = parquet_file
            size_kb = parquet_file.stat().st_size / 1024
            self.lbl_parquet_ready.setText(
                f"✔ {parquet_file.name} ({size_kb:.1f} KB)"
            )
            self.lbl_parquet_ready.setStyleSheet("color: #047857; font-weight: bold;")
            self.btn_preview_mart.setEnabled(True)
            self.btn_copy_parquet_path.setEnabled(True)
            self.btn_copy_mcode.setEnabled(True)
        else:
            if not self._last_parquet_path:
                self.lbl_parquet_ready.setText(t.get("dwh_card3_initial", ""))
                self.lbl_parquet_ready.setStyleSheet("color: #333333;")
                self.btn_preview_mart.setEnabled(False)
                self.btn_copy_parquet_path.setEnabled(False)
                self.btn_copy_mcode.setEnabled(False)

    def _on_import_file(self, target_type: str) -> None:
        """Import external CSV file into canonical DWH raw folders."""
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        settings = load_settings()

        if target_type == "customer":
            dest = settings.masters_dir / "master_customers.csv"
            title = t.get("dwh_card1_cust", "고객 마스터").rstrip(":")
        elif target_type == "product":
            dest = settings.masters_dir / "master_products.csv"
            title = t.get("dwh_card1_prod", "제품 마스터").rstrip(":")
        else:
            dest = settings.pnl_dir / "raw_pnl.csv"
            title = t.get("dwh_card1_pnl", "P&L 실적 원천").rstrip(":")

        selected = choose_file_dialog(
            self,
            f"{t.get('dwh_btn_import_file', '가져오기')} - {title}",
            filter_pattern="CSV Files (*.csv);;All Files (*.*)",
        )
        if not selected:
            return

        if dest.is_file():
            prompt = t.get(
                "dwh_import_overwrite_prompt",
                "해당 마스터/실적 파일이 이미 존재합니다:\n{path}\n\n새로운 파일로 덮어쓰시겠습니까?",
            ).format(path=dest)
            if not confirm_dialog(self, t.get("dwh_btn_import_file", "가져오기"), prompt):
                return

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(selected, dest)
            self._check_source_files()
            info_dialog(
                self,
                t.get("dwh_btn_import_file", "가져오기"),
                t.get("dwh_import_success", "파일이 성공적으로 복사 및 등록되었습니다:\n{path}").format(path=dest),
            )
        except Exception as e:
            error_dialog(self, t.get("dwh_btn_import_file", "가져오기"), str(e))

    def _on_preview_mart(self) -> None:
        """Open quick preview dialog for exported Parquet mart."""
        if not self._last_parquet_path or not self._last_parquet_path.is_file():
            return
        dlg = DwhPreviewDialog(parquet_path=self._last_parquet_path, parent=self, language_code=self.language_code)
        dlg.exec()

    def _on_open_guide(self) -> None:
        """Open the interactive beginner guide dialog."""
        dlg = DwhGuideDialog(self, language_code=self.language_code)
        dlg.exec()

    def _on_download_template(self) -> None:
        """Download styled blank Excel template and prompt to open immediately."""
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        save_path = save_file_dialog(
            self,
            t.get("dwh_dlg_template_title", "표준 엑셀 서식 다운로드"),
            default_name="DataRefinery_DWH_Template.xlsx",
            filter_pattern="Excel Workbook (*.xlsx)",
        )
        if save_path:
            try:
                create_dwh_excel_template(save_path)
                if confirm_dialog(
                    self,
                    t.get("dwh_dlg_template_title", "표준 엑셀 서식 다운로드"),
                    t.get(
                        "dwh_dlg_template_open_prompt",
                        "표준 엑셀 서식이 저장되었습니다:\n{path}\n\n지금 Excel에서 바로 여시겠습니까?",
                    ).format(path=save_path),
                ):
                    try:
                        os.startfile(save_path)
                    except Exception:
                        open_containing_folder(save_path)
            except Exception as e:
                error_dialog(self, t.get("dwh_dlg_template_title", "표준 엑셀 서식 다운로드"), str(e))

    def _on_generate_sample(self) -> None:
        """Generate synthetic master and P&L files for testing."""
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        try:
            from scripts.generate_sample_data import generate_sample_data
            generate_sample_data()
            self._check_source_files()
            info_dialog(
                self,
                t.get("dwh_dlg_sample_success_title", "샘플 데이터 생성 완료"),
                t.get("dwh_dlg_sample_success_msg", "테스트용 공식 마스터 2종과 100+ 컬럼 P&L 실적 데이터가 생성되었습니다."),
            )
        except Exception as e:
            error_dialog(
                self,
                t.get("dwh_dlg_sample_fail_title", "샘플 생성 실패"),
                str(e),
            )

    def _on_open_source_folder(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        try:
            settings = load_settings()
            open_containing_folder(str(settings.raw_dir))
        except Exception as e:
            error_dialog(self, t.get("dwh_dlg_open_folder_fail", "폴더 열기 실패"), str(e))

    def _on_open_masters_folder(self) -> None:
        """Open masters folder so user can immediately append missing codes."""
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        try:
            settings = load_settings()
            open_containing_folder(str(settings.masters_dir))
        except Exception as e:
            error_dialog(self, t.get("dwh_dlg_open_folder_fail", "폴더 열기 실패"), str(e))

    def _on_open_export_folder(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        try:
            settings = load_settings()
            open_containing_folder(str(settings.mart_export_dir))
        except Exception as e:
            error_dialog(self, t.get("dwh_dlg_open_folder_fail", "폴더 열기 실패"), str(e))

    def _on_copy_parquet_path(self) -> None:
        if self._last_parquet_path and self._last_parquet_path.is_file():
            QGuiApplication.clipboard().setText(str(self._last_parquet_path))
            t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
            info_dialog(
                self,
                t.get("dwh_dlg_clipboard_title", "클립보드 복사"),
                t.get("dwh_path_copied", "클립보드에 경로가 복사되었습니다!"),
            )

    def _on_copy_mcode(self) -> None:
        """Copy Excel Power Query M-Code snippet to clipboard."""
        if self._last_parquet_path and self._last_parquet_path.is_file():
            escaped_path = str(self._last_parquet_path.resolve()).replace("\\", "\\\\")
            m_code = (
                f'let\n'
                f'    Source = Parquet.Document(File.Contents("{escaped_path}")),\n'
                f'    #"Navigated" = Source\n'
                f'in\n'
                f'    #"Navigated"'
            )
            QGuiApplication.clipboard().setText(m_code)
            t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
            info_dialog(
                self,
                t.get("dwh_dlg_clipboard_title", "클립보드 복사"),
                t.get("dwh_mcode_copied", "Power Query M-코드가 클립보드에 복사되었습니다!"),
            )

    def _on_open_error_log(self) -> None:
        if self._last_error_log and self._last_error_log.is_file():
            open_containing_folder(str(self._last_error_log))
        else:
            t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
            info_dialog(
                self,
                t.get("dwh_dlg_build_fail_title", "오류 리포트"),
                "No CSV error report file found in logs.",
            )

    # -----------------------------------------------------------------
    # Execution (Background Worker)
    # -----------------------------------------------------------------
    def _on_run_dwh_pipeline(self) -> None:
        """Trigger complete DWH pipeline execution in background thread."""
        self.btn_run_dwh.setEnabled(False)
        self.btn_download_template.setEnabled(False)
        self.btn_gen_sample.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.btn_preview_mart.setEnabled(False)
        self.error_frame.setVisible(False)
        self.progress_bar.setValue(0)
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])

        def worker_task(callbacks: JobCallbacks) -> dict:
            import duckdb
            start_time = time.time()
            settings = load_settings()
            rules = load_account_mapping()
            con = duckdb.connect(str(settings.warehouse_db))

            try:
                con.execute("BEGIN TRANSACTION;")

                # Step 1: Staging
                callbacks.on_progress(1, 5, t.get("dwh_step1_name", "1. 법인 격리 및 Staging 추출"))
                extractor = DataExtractor(settings=settings, con=con)
                extractor.extract_all()

                # Step 2: Dimensions
                callbacks.on_progress(2, 5, t.get("dwh_step2_name", "2. 마스터 Conformed 차원 빌드"))
                dim_builder = DimensionBuilder(settings=settings, con=con)
                dim_builder.build_all()

                # Step 3 & 4: Fact Builder (Anti-join & Roll-up)
                callbacks.on_progress(3, 5, t.get("dwh_step3_name", "3. Anti-Join 외래키 무결성 검증"))
                callbacks.on_progress(4, 5, t.get("dwh_step4_name", "4. 114개 계정 롤업 및 손익 계산"))
                fact_builder = FactBuilder(settings=settings, account_rules=rules, con=con)
                fact_stats = fact_builder.build_fact_pnl()

                # Step 5: Loader & Parquet Export
                callbacks.on_progress(5, 5, t.get("dwh_step5_name", "5. AI OBT 뷰 및 Parquet 발행"))
                loader = DwhLoader(settings=settings, con=con)
                loader_stats = loader.run_all()

                con.execute("COMMIT;")
                elapsed = time.time() - start_time

                return {
                    "success": True,
                    "elapsed": elapsed,
                    "fact_stats": fact_stats,
                    "loader_stats": loader_stats,
                    "settings": settings,
                }
            except Exception as e:
                try:
                    con.execute("ROLLBACK;")
                except Exception:
                    pass
                raise e
            finally:
                con.close()

        def on_progress(current: int, total: int, status: str) -> None:
            self.progress_bar.setValue(current)
            if current >= 1:
                self.lbl_step1.setText(f"✔ {t.get('dwh_step1_name', '1. Staging')}")
                self.lbl_step1.setStyleSheet("color: #0E7490; font-weight: bold;")
            if current >= 2:
                self.lbl_step2.setText(f"✔ {t.get('dwh_step2_name', '2. Dimensions')}")
                self.lbl_step2.setStyleSheet("color: #0E7490; font-weight: bold;")
            if current >= 3:
                self.lbl_step3.setText(f"✔ {t.get('dwh_step3_name', '3. Integrity')}")
                self.lbl_step3.setStyleSheet("color: #0E7490; font-weight: bold;")
            if current >= 4:
                self.lbl_step4.setText(f"✔ {t.get('dwh_step4_name', '4. Fact P&L')}")
                self.lbl_step4.setStyleSheet("color: #0E7490; font-weight: bold;")
            if current >= 5:
                self.lbl_step5.setText(f"✔ {t.get('dwh_step5_name', '5. OBT View & Parquet')}")
                self.lbl_step5.setStyleSheet("color: #0E7490; font-weight: bold;")

        def on_success(result: dict) -> None:
            self.btn_run_dwh.setEnabled(True)
            self.btn_download_template.setEnabled(True)
            self.btn_gen_sample.setEnabled(True)
            self.btn_refresh.setEnabled(True)
            settings = result["settings"]
            loader_stats = result["loader_stats"]
            fact_stats = result["fact_stats"]
            elapsed = result["elapsed"]

            parquet_file = settings.mart_export_dir / "v_ai_pnl_summary.parquet"
            self._last_parquet_path = parquet_file
            size_kb = parquet_file.stat().st_size / 1024 if parquet_file.exists() else 0

            self.lbl_parquet_ready.setText(
                t.get("dwh_parquet_ready", "✔ {name} ({rows:,} rows, {size})").format(
                    name=parquet_file.name,
                    rows=loader_stats.total_obt_rows,
                    size=f"{size_kb:.1f} KB",
                ) + f" - {elapsed:.2f}s"
            )
            self.lbl_parquet_ready.setStyleSheet("color: #047857; font-weight: bold;")
            self.btn_preview_mart.setEnabled(True)
            self.btn_copy_parquet_path.setEnabled(True)
            self.btn_copy_mcode.setEnabled(True)

            # Update step 4 text with actual numbers
            self.lbl_step4.setText(
                t.get("dwh_step4_summary", "✔ 4. Revenue: {rev:,.0f} {cur} | OP: {op:,.0f} {cur}").format(
                    rev=fact_stats.total_gross_revenue_reporting,
                    op=fact_stats.total_operating_profit_reporting,
                    cur=settings.reporting_currency,
                )
            )
            info_dialog(
                self,
                t.get("dwh_dlg_build_success_title", "DWH 빌드 완료"),
                t.get("dwh_dlg_build_success_msg", "").format(
                    elapsed=elapsed,
                    rows=loader_stats.total_obt_rows,
                    name=parquet_file.name,
                ),
            )

        def on_error(err: Exception) -> None:
            self.btn_run_dwh.setEnabled(True)
            self.btn_download_template.setEnabled(True)
            self.btn_gen_sample.setEnabled(True)
            self.btn_refresh.setEnabled(True)
            err_str = str(err)

            # Find latest dumped error CSV if fail-fast occurred and extract unmapped code hints
            unmapped_hint = ""
            try:
                settings = load_settings()
                err_files = sorted(list(settings.logs_dir.glob("unmapped_errors_*.csv")))
                if err_files:
                    self._last_error_log = err_files[-1]
                    missing_codes = []
                    with open(self._last_error_log, "r", encoding="utf-8", errors="replace") as ef:
                        reader = csv.DictReader(ef)
                        for r in reader:
                            code = r.get("unmapped_code") or ""
                            dim = r.get("dimension") or ""
                            if code:
                                missing_codes.append(f"{dim}: {code}" if dim else code)
                            if len(missing_codes) >= 3:
                                break
                    if missing_codes:
                        hint_codes = ", ".join(missing_codes)
                        prefix = "Missing codes" if self.language_code == "en" else ("Niezarejestrowane kody" if self.language_code == "pl" else "누락 코드")
                        unmapped_hint = f"\n\n💡 {prefix}: [{hint_codes}]"
            except Exception:
                pass

            self.lbl_err_msg.setText(err_str + unmapped_hint)
            self.error_frame.setVisible(True)
            self.btn_open_err_log.setEnabled(bool(self._last_error_log))
            self.btn_open_masters.setEnabled(True)

            error_dialog(
                self,
                t.get("dwh_dlg_build_fail_title", "파이프라인 실행 중단 (Fail-Fast)"),
                t.get("dwh_dlg_build_fail_msg", "데이터 무결성 검증 실패:\n\n{error}").format(error=err_str),
            )

        if self.job_runner:
            self.job_runner.run_job(worker_task, on_success, on_error, on_progress)
        else:
            # Synchronous fallback
            cb = JobCallbacks(on_progress=on_progress)
            try:
                res = worker_task(cb)
                on_success(res)
            except Exception as e:
                on_error(e)

    # -----------------------------------------------------------------
    # Language Localization
    # -----------------------------------------------------------------
    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        t = _UI_TEXT.get(lang_code, _UI_TEXT["ko"])

        self.banner_guide.setText(t.get("dwh_guide_step1", "💡 진행 순서: [1단계] 마스터 및 P&L 원천 확인 ➔ [2단계] DWH 빌드 ➔ [3단계] Excel Power Query 연동"))
        self.btn_open_guide.setText(t.get("dwh_btn_guide", "📖 초보자 실전 매뉴얼"))

        self.card_sources.set_title(t.get("dwh_card1_title", "1. 원천 데이터 및 설정 확인"))
        self.btn_import_cust.setText(t.get("dwh_btn_import_file", "📂 가져오기"))
        self.btn_import_prod.setText(t.get("dwh_btn_import_file", "📂 가져오기"))
        self.btn_import_pnl.setText(t.get("dwh_btn_import_file", "📂 가져오기"))
        self.btn_download_template.setText(t.get("dwh_btn_template", "📥 표준 엑셀 서식 다운로드"))
        self.btn_gen_sample.setText(t.get("dwh_btn_sample", "✨ 샘플 원천 데이터 자동 생성"))
        self.btn_refresh.setText(t.get("dwh_btn_refresh", "새로고침"))
        self.btn_open_source_dir.setText(t.get("dwh_btn_open_file", "폴더 열기"))

        self.card_pipeline.set_title(t.get("dwh_card2_title", "2. DWH 빌드 및 무결성 검증 (Kimball Star Schema)"))
        self.btn_run_dwh.setText(t.get("dwh_btn_build", "★ 1-Click DWH 파이프라인 전체 빌드"))
        self.lbl_step1.setText(f"○ {t.get('dwh_step1_name', '1. Staging')}")
        self.lbl_step2.setText(f"○ {t.get('dwh_step2_name', '2. Dimensions')}")
        self.lbl_step3.setText(f"○ {t.get('dwh_step3_name', '3. Integrity')}")
        self.lbl_step4.setText(f"○ {t.get('dwh_step4_name', '4. Fact P&L')}")
        self.lbl_step5.setText(f"○ {t.get('dwh_step5_name', '5. OBT View & Parquet')}")

        self.lbl_err_title.setText(t.get("dwh_err_fk_title", "외래키 무결성 위반 (미등록 마스터 코드 발견)"))
        self.btn_open_masters.setText(t.get("dwh_btn_open_masters", "📝 마스터 폴더 열기 (코드 추가)"))
        self.btn_open_err_log.setText(t.get("dwh_btn_open_error_log", "📄 오류 리포트 열기 (CSV)"))

        self.card_excel.set_title(t.get("dwh_card3_title", "3. Excel Power Query 연동 및 결과"))
        if not self._last_parquet_path:
            self.lbl_parquet_ready.setText(t.get("dwh_card3_initial", ""))
        self.btn_preview_mart.setText(t.get("dwh_btn_preview_mart", "📊 마트 데이터 즉시 미리보기"))
        self.btn_copy_mcode.setText(t.get("dwh_btn_copy_mcode", "⚡ Power Query M-코드 복사"))
        self.btn_copy_parquet_path.setText(t.get("dwh_btn_copy_path", "📋 Parquet 경로 복사"))
        self.btn_open_export_folder.setText(t.get("dwh_btn_open_folder", "📂 배포 폴더 열기"))

        self.lbl_guide_title.setText(t.get("dwh_excel_guide_title", "📌 Microsoft Excel 연결 방법 (1분 완성):"))
        self.lbl_guide_text.setText(t.get("dwh_excel_guide_text", ""))

        self._check_source_files()
