"""Interactive in-app update dialog with direct installer download and guided reinstallation."""

from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.i18n import _UI_TEXT
from src.qt.dialogs import confirm_dialog, error_dialog
from src.ui_components import PALETTE
from src.update_checker import (
    ReleaseInfo,
    download_release_asset,
    get_update_download_directory,
)


class UpdateDownloadWorker(QThread):
    """Background worker for downloading the release installer without UI freezes."""

    progress = Signal(int, int)  # (downloaded_bytes, total_bytes)
    finished = Signal(str)       # target_path
    failed = Signal(str)         # error_message

    def __init__(self, download_url: str, target_path: Path) -> None:
        super().__init__()
        self.download_url = download_url
        self.target_path = target_path
        self._is_cancelled = False

    def run(self) -> None:
        try:
            download_release_asset(
                self.download_url,
                self.target_path,
                progress_callback=self._on_progress,
                cancel_check=lambda: self._is_cancelled,
            )
            if not self._is_cancelled:
                self.finished.emit(str(self.target_path))
        except Exception as exc:
            if not self._is_cancelled:
                self.failed.emit(str(exc))

    def cancel(self) -> None:
        self._is_cancelled = True

    def _on_progress(self, downloaded: int, total: int) -> None:
        self.progress.emit(downloaded, total)


class UpdateDialog(QDialog):
    """In-app modal dialog for one-click downloading and guided installer execution."""

    def __init__(
        self,
        release_info: ReleaseInfo,
        current_version: str,
        parent: QWidget | None = None,
        language_code: str = "ko",
    ) -> None:
        super().__init__(parent)
        self.release_info = release_info
        self.current_version = current_version
        self.language_code = language_code
        self.worker: UpdateDownloadWorker | None = None
        self.installer_path: Path | None = None

        self.resize(680, 520)
        self.setMinimumSize(560, 440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        # Target download file
        asset_name = self.release_info.asset_name or f"App04_DataRefinery_Setup_v{self.release_info.version}.exe"
        self.target_file = get_update_download_directory() / asset_name

        self._init_ui()
        self._check_cached_file()
        self.apply_language(self.language_code)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 1. Header Banner & Version Info
        self.lbl_title = QLabel()
        self.lbl_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {PALETTE['navy']};")
        layout.addWidget(self.lbl_title)

        self.lbl_version_info = QLabel()
        self.lbl_version_info.setStyleSheet("color: #475569; font-size: 12px;")
        layout.addWidget(self.lbl_version_info)

        # 2. Release Notes
        self.lbl_notes_title = QLabel()
        self.lbl_notes_title.setStyleSheet("font-weight: bold; color: #1E293B; margin-top: 4px;")
        layout.addWidget(self.lbl_notes_title)

        self.txt_notes = QTextBrowser()
        self.txt_notes.setOpenExternalLinks(True)
        self.txt_notes.setStyleSheet(
            f"background-color: {PALETTE['surface_alt']}; border: 1px solid {PALETTE['border']}; "
            "border-radius: 6px; padding: 10px; font-size: 12px; line-height: 1.4;"
        )
        if self.release_info.notes:
            self.txt_notes.setMarkdown(self.release_info.notes)
        layout.addWidget(self.txt_notes, stretch=1)

        # 3. Progress Bar & Status Message
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ border: 1px solid {PALETTE['border']}; border-radius: 4px; text-align: center; font-size: 11px; }} "
            f"QProgressBar::chunk {{ background-color: {PALETTE['accent']}; border-radius: 3px; }}"
        )
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("color: #334155; font-size: 12px;")
        layout.addWidget(self.lbl_status)

        # 4. Auto-install Checkbox
        self.chk_auto_install = QCheckBox()
        self.chk_auto_install.setChecked(True)
        self.chk_auto_install.setStyleSheet("font-size: 12px; color: #1E293B;")
        layout.addWidget(self.chk_auto_install)

        # 5. Action Buttons (Footer)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_browser = QPushButton()
        self.btn_browser.clicked.connect(self._on_open_browser)
        btn_layout.addWidget(self.btn_browser)

        self.btn_open_folder = QPushButton()
        self.btn_open_folder.clicked.connect(self._on_open_folder)
        self.btn_open_folder.setVisible(False)
        btn_layout.addWidget(self.btn_open_folder)

        btn_layout.addStretch()

        self.btn_cancel = QPushButton()
        self.btn_cancel.clicked.connect(self._on_cancel_download)
        self.btn_cancel.setVisible(False)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_close = QPushButton()
        self.btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_close)

        self.btn_action = QPushButton()
        self.btn_action.setStyleSheet(
            f"QPushButton {{ background-color: {PALETTE['accent']}; color: white; "
            "font-size: 13px; font-weight: bold; padding: 8px 18px; border-radius: 6px; border: none; }} "
            "QPushButton:hover { background-color: #1D4ED8; } "
            "QPushButton:disabled { background-color: #94A3B8; color: #E2E8F0; }"
        )
        self.btn_action.clicked.connect(self._on_action_clicked)
        btn_layout.addWidget(self.btn_action)

        layout.addLayout(btn_layout)

    def _check_cached_file(self) -> None:
        """Check if installer was already downloaded and matches the size."""
        if self.target_file.exists():
            file_size = self.target_file.stat().st_size
            if self.release_info.size > 0 and file_size == self.release_info.size:
                self.installer_path = self.target_file
                self.progress_bar.setVisible(True)
                self.progress_bar.setValue(100)
                self.btn_open_folder.setVisible(True)

    def apply_language(self, lang_code: str) -> None:
        self.language_code = lang_code
        t = _UI_TEXT.get(lang_code, _UI_TEXT["ko"])

        self.setWindowTitle(t.get("update_dlg_title", "Data Refinery 업데이트"))
        self.lbl_title.setText(t.get("update_new_version_banner", "새로운 버전 <b>v{latest}</b>이(가) 준비되었습니다!").format(latest=self.release_info.version))

        size_mb = f"{self.release_info.size / (1024 * 1024):.1f} MB" if self.release_info.size > 0 else "100+ MB"
        self.lbl_version_info.setText(
            t.get("update_current_version_info", "현재: v{current} · 최신: v{latest} · 크기: {size}").format(
                current=self.current_version,
                latest=self.release_info.version,
                size=size_mb,
            )
        )
        self.lbl_notes_title.setText(t.get("update_notes_title", "이번 버전 변경 사항 (릴리즈 노트):"))
        self.chk_auto_install.setText(t.get("update_chk_auto_install", "다운로드 완료 후 즉시 설치 프로그램 실행 (권장)"))
        self.btn_browser.setText(t.get("update_btn_open_browser", "🌐 웹 브라우저로 열기"))
        self.btn_open_folder.setText(t.get("update_btn_open_folder", "📂 다운로드 폴더 열기"))
        self.btn_close.setText(t.get("update_btn_close", "닫기"))
        self.btn_cancel.setText(t.get("update_btn_cancel", "다운로드 취소"))

        if self.installer_path:
            self.lbl_status.setText(t.get("update_already_cached", "✓ 이미 최신 설치 파일이 준비되어 있습니다. 즉시 설치할 수 있습니다."))
            self.btn_action.setText(t.get("update_btn_install_now", "🚀 지금 설치하기 (앱 자동 종료)"))
        else:
            self.lbl_status.setText(t.get("update_click_to_start", "클릭 한 번으로 최신 설치 파일을 받고 자동으로 업그레이드합니다."))
            self.btn_action.setText(t.get("update_btn_download_install", "⚡ 원클릭 업데이트 (다운로드 및 설치)"))

    def _on_action_clicked(self) -> None:
        if self.installer_path and self.installer_path.exists():
            self._execute_installer(self.installer_path)
            return

        if not self.release_info.download_url:
            self._on_open_browser()
            return

        self._start_download()

    def _start_download(self) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_action.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self.lbl_status.setText(t.get("update_download_starting", "다운로드를 시작하는 중..."))

        self.worker = UpdateDownloadWorker(self.release_info.download_url, self.target_file)
        self.worker.progress.connect(self._on_download_progress)
        self.worker.finished.connect(self._on_download_finished)
        self.worker.failed.connect(self._on_download_failed)
        self.worker.start()

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        pct = int(downloaded / total * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)

        dl_mb = f"{downloaded / (1024 * 1024):.1f} MB"
        tot_mb = f"{total / (1024 * 1024):.1f} MB" if total > 0 else "?? MB"
        self.lbl_status.setText(
            t.get("update_status_downloading", "다운로드 중... {downloaded} / {total} ({percent}%)").format(
                downloaded=dl_mb,
                total=tot_mb,
                percent=pct,
            )
        )

    def _on_download_finished(self, saved_path_str: str) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        self.installer_path = Path(saved_path_str)
        self.progress_bar.setValue(100)
        self.btn_cancel.setVisible(False)
        self.btn_open_folder.setVisible(True)
        self.btn_action.setEnabled(True)
        self.btn_action.setText(t.get("update_btn_install_now", "🚀 지금 설치하기 (앱 자동 종료)"))
        self.lbl_status.setText(t.get("update_status_complete", "✓ 다운로드가 완료되었습니다. 설치를 진행합니다."))

        # If auto-install is checked, launch installer immediately
        if self.chk_auto_install.isChecked():
            self._execute_installer(self.installer_path)

    def _on_download_failed(self, error_message: str) -> None:
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        self.btn_cancel.setVisible(False)
        self.btn_action.setEnabled(True)
        self.btn_action.setText(t.get("update_btn_retry", "다시 시도"))
        self.lbl_status.setText(t.get("update_err_download", "업데이트 다운로드 실패:\n{error}").format(error=error_message))
        error_dialog(self, t.get("update_dlg_title", "Data Refinery 업데이트"), str(error_message))

    def _on_cancel_download(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        self.btn_cancel.setVisible(False)
        self.btn_action.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(t.get("update_cancelled", "다운로드가 취소되었습니다."))
        self.btn_action.setText(t.get("update_btn_download_install", "⚡ 원클릭 업데이트 (다운로드 및 설치)"))

    def _execute_installer(self, path: Path) -> None:
        """Launch Inno Setup installer and close current application to allow clean upgrade."""
        t = _UI_TEXT.get(self.language_code, _UI_TEXT["ko"])
        if not self.chk_auto_install.isChecked() and not confirm_dialog(
            self,
            t.get("update_install_prompt_title", "설치 프로그램 실행"),
            t.get("update_install_prompt_body", "설치 프로그램을 시작합니다.\n새 버전으로 교체하기 위해 현재 실행 중인 Data Refinery가 자동으로 종료됩니다.\n\n계속하시겠습니까?"),
        ):
            return

        try:
            # Spawn installer detached
            subprocess.Popen([str(path)], shell=False)
            # Unblock and close modal dialog loop, allowing clean exit
            self.accept()
            QApplication.instance().quit()
        except Exception as exc:
            error_dialog(self, t.get("update_dlg_title", "Data Refinery 업데이트"), str(exc))

    def _on_open_folder(self) -> None:
        """Open the directory where the installer was downloaded."""
        if self.installer_path and self.installer_path.parent.exists():
            folder = str(self.installer_path.parent)
            try:
                os.startfile(folder)
            except Exception:
                webbrowser.open(folder)

    def _on_open_browser(self) -> None:
        """Fallback to opening GitHub release web page."""
        webbrowser.open(self.release_info.url)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1500)
        super().closeEvent(event)
