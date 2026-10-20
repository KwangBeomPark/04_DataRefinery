"""PySide6 MainWindow for Data Refinery."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

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
from src.qt.tabs.data_tools_tab import DataToolsTab
from src.qt.tabs.dataset.tab import DatasetPublisherTab
from src.qt.tabs.dwh_tab import DwhPipelineTab
from src.qt.theme import PALETTE
from src.update_checker import load_settings
from src.version import __version__


class MainWindow(QMainWindow):
    """Main desktop application window for Data Refinery."""

    @staticmethod
    def _resource_path(relative_path: str) -> str:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root is not None:
            return os.path.join(bundle_root, "assets", relative_path)
        return str(Path(__file__).resolve().parents[2] / "assets" / relative_path)

    def __init__(self, parent: QWidget | None = None):
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

        # Update and application information menu.
        self.btn_settings = QPushButton("업데이트 / 정보 ▾")
        self.menu_settings = QMenu(self)
        self.act_check_update = self.menu_settings.addAction("업데이트 확인...", self._on_check_update)
        self.act_visit_repo = self.menu_settings.addAction("GitHub 저장소 방문...", self._on_visit_repo)
        self.menu_settings.addSeparator()
        self.act_about = self.menu_settings.addAction("정보 (About)...", self._on_about)
        self.btn_settings.setMenu(self.menu_settings)
        top_layout.addWidget(self.btn_settings)

        main_layout.addWidget(top_bar)

        # 2. Main Tab Widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)

        # Instantiate 4 tabs in core lifecycle order:
        # 1. Core DWH & Mart (Default Home) -> 2. Team Share & Publisher -> 3. Fast Pivot Slicer -> 4. Data Cleaning Tools
        self.tab_dwh = DwhPipelineTab(language_code=self.language_code, job_runner=self.job_runner)
        self.tab_dataset = DatasetPublisherTab(language_code=self.language_code, job_runner=self.job_runner)
        self.tab_aggregator = AggregatorTab(language_code=self.language_code, job_runner=self.job_runner)
        self.tab_tools = DataToolsTab(language_code=self.language_code, job_runner=self.job_runner)

        # Backward-compatibility handles
        self.tab_csv = self.tab_tools.tab_csv
        self.tab_promotion = self.tab_tools.tab_promotion

        self.tab_widget.addTab(self.tab_dwh, "1. 🏛️ 손익 마트 생성 (DWH)")
        self.tab_widget.addTab(self.tab_dataset, "2. 🚀 팀 공유 및 배포 (Publisher)")
        self.tab_widget.addTab(self.tab_aggregator, "3. 📊 초고속 피벗 집계 (Slicer)")
        self.tab_widget.addTab(self.tab_tools, "4. 🛠️ 데이터 정제 도구 (Data Prep)")
        self.tab_widget.setCurrentIndex(0)

        main_layout.addWidget(self.tab_widget, stretch=1)

        # 3. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.lbl_status_msg = QLabel("준비됨")
        self.status_bar.addWidget(self.lbl_status_msg, stretch=1)
        self.lbl_version = QLabel(f"v{__version__}")
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
        self.btn_settings.setText(t["update_info_menu"])
        self.act_check_update.setText(t.get("menu_check_update", "업데이트 확인..."))
        self.act_visit_repo.setText(t.get("menu_visit_repo", "GitHub 저장소 방문..."))
        self.act_about.setText(t.get("menu_about", "정보 (About)..."))
        self.lbl_status_msg.setText(t.get("status_ready", "준비됨"))

        # task_options identifies features in logical order:
        task_opts = t.get("task_options", ())
        if len(task_opts) >= 4:
            for tab, task_index in (
                (self.tab_dwh, 0),
                (self.tab_dataset, 1),
                (self.tab_aggregator, 2),
                (self.tab_tools, 3),
            ):
                idx = self.tab_widget.indexOf(tab)
                if idx >= 0:
                    self.tab_widget.setTabText(idx, task_opts[task_index])

        # Propagate to all tabs
        self.tab_dwh.apply_language(lang_code)
        self.tab_dataset.apply_language(lang_code)
        self.tab_aggregator.apply_language(lang_code)
        self.tab_tools.apply_language(lang_code)

    def _on_check_update(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        from src.qt.dialogs import error_dialog, info_dialog
        from src.qt.widgets.update_dialog import UpdateDialog
        from src.update_checker import fetch_latest_release, version_key
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rel = fetch_latest_release(timeout=5)
            QGuiApplication.restoreOverrideCursor()
            if rel:
                latest = version_key(rel.version)
                current = version_key(__version__)
                if latest and current and latest > current:
                    dlg = UpdateDialog(
                        release_info=rel,
                        current_version=__version__,
                        parent=self,
                        language_code=self.language_code,
                    )
                    dlg.exec()
                    return
            info_dialog(
                self,
                t.get("dlg_update_title", "업데이트 확인"),
                t.get("dlg_update_latest", "").format(current=__version__),
            )
        except Exception as e:
            QGuiApplication.restoreOverrideCursor()
            error_dialog(
                self,
                t.get("dlg_update_err_title", "업데이트 확인 실패"),
                t.get("dlg_update_err_body", "").format(error=e),
            )

    def _on_visit_repo(self) -> None:
        webbrowser.open("https://github.com/KwangBeomPark/04_DataRefinery")

    def _on_about(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        info_dialog(
            self,
            t.get("dlg_about_title", "Data Refinery 정보"),
            t.get("dlg_about_body", "").format(version=__version__),
        )

