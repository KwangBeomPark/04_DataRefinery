"""PySide6 MainWindow for Data Refinery v2.0.0."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.background_jobs import BackgroundJobRunner
from src.i18n import _LANGUAGE_CODES, _UI_TEXT
from src.qt.dialogs import info_dialog
from src.qt.jobs import create_qt_job_runner
from src.qt.tabs.aggregator.tab import AggregatorTab
from src.qt.tabs.csv_tab import CsvRepairTab
from src.qt.tabs.dataset.tab import DatasetPublisherTab
from src.qt.tabs.promotion_tab import PromotionTab
from src.qt.theme import PALETTE
from src.update_checker import check_for_update, load_settings, save_settings
from src.version import __version__


class MainWindow(QMainWindow):
    """Main desktop application window for Data Refinery."""

    @staticmethod
    def _resource_path(relative_path: str) -> str:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root is not None:
            return os.path.join(bundle_root, "assets", relative_path)
        return str(Path(__file__).resolve().parents[2] / "assets" / relative_path)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.language_code = "en"
        self.job_runner: BackgroundJobRunner = create_qt_job_runner()
        self._update_settings = load_settings()

        self._init_window()
        self._init_ui()
        self.apply_language("ko")  # Default to Korean per user rule

    def _init_window(self) -> None:
        self.setWindowTitle(f"Data Refinery v{__version__}")
        self.resize(980, 880)
        self.setMinimumSize(820, 720)

        icon_path = self._resource_path("icons/icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _init_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # 1. Top Bar: Title & Subtitle on Left, Language & Settings on Right
        top_bar = QWidget()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(12)

        # Title & Subtitle
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.lbl_app_title = QLabel("Data Refinery")
        self.lbl_app_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {PALETTE['navy']};")
        self.lbl_app_subtitle = QLabel("데이터 전처리 · 집계 · 배포 솔루션")
        self.lbl_app_subtitle.setProperty("caption", "true")
        title_box.addWidget(self.lbl_app_title)
        title_box.addWidget(self.lbl_app_subtitle)
        top_layout.addLayout(title_box, stretch=1)

        # Language Selector
        top_layout.addWidget(QLabel("🌐"))
        self.combo_language = QComboBox()
        self._lang_display_to_code = {}
        for display_name, code in _LANGUAGE_CODES.items():
            self._lang_display_to_code[display_name] = code
            self.combo_language.addItem(display_name)
        # Select Korean initially
        ko_idx = self.combo_language.findText("한국어")
        if ko_idx >= 0:
            self.combo_language.setCurrentIndex(ko_idx)
        self.combo_language.currentIndexChanged.connect(self._on_language_changed)
        top_layout.addWidget(self.combo_language)

        # Settings / Update Button
        self.btn_settings = QPushButton("설정 / 업데이트 ▾")
        self.menu_settings = QMenu(self)
        self.menu_settings.addAction("업데이트 확인...", self._on_check_update)
        self.menu_settings.addAction("GitHub 저장소 방문...", self._on_visit_repo)
        self.menu_settings.addSeparator()
        self.menu_settings.addAction("정보 (About)...", self._on_about)
        self.btn_settings.setMenu(self.menu_settings)
        top_layout.addWidget(self.btn_settings)

        main_layout.addWidget(top_bar)

        # 2. Main Tab Widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)

        # Instantiate 4 tabs
        self.tab_csv = CsvRepairTab(language_code=self.language_code, job_runner=self.job_runner)
        self.tab_promotion = PromotionTab(language_code=self.language_code, job_runner=self.job_runner)
        self.tab_aggregator = AggregatorTab(language_code=self.language_code, job_runner=self.job_runner)
        self.tab_dataset = DatasetPublisherTab(language_code=self.language_code, job_runner=self.job_runner)

        self.tab_widget.addTab(self.tab_csv, "CSV 복구/분할")
        self.tab_widget.addTab(self.tab_promotion, "프로모션 템플릿")
        self.tab_widget.addTab(self.tab_aggregator, "데이터 집계·슬라이서")
        self.tab_widget.addTab(self.tab_dataset, "데이터셋 배포")

        main_layout.addWidget(self.tab_widget, stretch=1)

        # 3. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.lbl_status_msg = QLabel("준비됨")
        self.status_bar.addWidget(self.lbl_status_msg, stretch=1)
        self.lbl_version = QLabel(f"v{__version__} (PySide6)")
        self.status_bar.addPermanentWidget(self.lbl_version)

    def _on_language_changed(self, index: int) -> None:
        display_name = self.combo_language.currentText()
        code = self._lang_display_to_code.get(display_name, "ko")
        self.apply_language(code)

    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        t = _UI_TEXT.get(lang_code, _UI_TEXT["ko"])

        self.setWindowTitle(f"Data Refinery v{__version__}")
        self.lbl_app_subtitle.setText(t.get("header_subtitle", "데이터 전처리 · 집계 · 배포 솔루션"))
        self.btn_settings.setText(t.get("update_menu", "설정 / 업데이트 ▾"))

        # Update Tab Titles from task_options
        task_opts = t.get("task_options", ("CSV 구조 복구", "프로모션 템플릿", "데이터 집계·슬라이서", "데이터셋 배포"))
        if len(task_opts) >= 4:
            self.tab_widget.setTabText(0, task_opts[0])
            self.tab_widget.setTabText(1, task_opts[1])
            self.tab_widget.setTabText(2, task_opts[2])
            self.tab_widget.setTabText(3, task_opts[3])

        # Propagate to all tabs
        self.tab_csv.apply_language(lang_code)
        self.tab_promotion.apply_language(lang_code)
        self.tab_aggregator.apply_language(lang_code)
        self.tab_dataset.apply_language(lang_code)

    def _on_check_update(self) -> None:
        from src.qt.dialogs import confirm_dialog, error_dialog, info_dialog
        from src.update_checker import fetch_latest_release, version_key
        try:
            rel = fetch_latest_release(timeout=4)
            if rel:
                latest = version_key(rel.version)
                current = version_key(__version__)
                if latest and current and latest > current:
                    if confirm_dialog(
                        self,
                        "새 업데이트 발견",
                        f"새로운 버전 <b>v{rel.version}</b>이(가) 출시되었습니다!<br>"
                        f"현재 버전: v{__version__}<br><br>"
                        "지금 다운로드 페이지를 여시겠습니까?",
                    ):
                        webbrowser.open(rel.url)
                    return
            info_dialog(self, "업데이트 확인", f"현재 최신 버전(v{__version__})을 사용 중입니다.")
        except Exception as e:
            error_dialog(self, "업데이트 확인 실패", f"서버와 통신할 수 없습니다:\n{e}")

    def _on_visit_repo(self) -> None:
        webbrowser.open("https://github.com/KwangBeomPark/04_DataRefinery")

    def _on_about(self) -> None:
        info_dialog(
            self,
            "Data Refinery 정보",
            f"<b>Data Refinery v{__version__}</b><br><br>"
            "대용량 CSV/Excel 데이터 정제, 집계, 프로모션 전처리 및 데이터셋 배포 도구<br>"
            "PySide6 (Qt for Python) 기반 최신 GUI 탑재<br><br>"
            "© 2026 KwangBeom Park. All rights reserved.",
        )
