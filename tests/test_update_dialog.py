import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.data_refinery_launcher import LauncherError
from src.qt.widgets.update_dialog import (
    INSTALLER_UPGRADE_ARGS,
    UpdateDialog,
    UpdateDownloadWorker,
)
from src.update_checker import ReleaseInfo

app = QApplication.instance() or QApplication([])


class TestUpdateDialog(unittest.TestCase):
    def setUp(self):
        self.sample_rel = ReleaseInfo(
            version="2.6.0",
            url="https://github.com/test/repo/releases/tag/v2.6.0",
            notes="### Changes in v2.6.0\n- New feature A\n- Fix B",
            asset_name="App04_DataRefinery_Setup_v2.6.0.exe",
            download_url="https://github.com/test/repo/releases/download/v2.6.0/App04_DataRefinery_Setup_v2.6.0.exe",
            size=104857600,  # 100 MB
        )

    def test_dialog_initialization(self):
        dlg = UpdateDialog(self.sample_rel, current_version="2.5.0", language_code="ko")
        self.assertIn("v2.6.0", dlg.lbl_title.text())
        self.assertIn("100.0 MB", dlg.lbl_version_info.text())
        self.assertTrue(dlg.chk_auto_install.isChecked())
        self.assertFalse(dlg.progress_bar.isVisible())
        self.assertTrue(dlg.btn_action.isEnabled())
        dlg.close()

    def test_i18n_zero_residual_korean(self):
        korean_pattern = re.compile(r"[\uac00-\ud7a3]")

        for lang in ("en", "pl"):
            dlg = UpdateDialog(self.sample_rel, current_version="2.5.0", language_code=lang)
            texts = [
                dlg.windowTitle(),
                dlg.lbl_title.text(),
                dlg.lbl_notes_title.text(),
                dlg.chk_auto_install.text(),
                dlg.btn_browser.text(),
                dlg.btn_open_folder.text(),
                dlg.btn_close.text(),
                dlg.btn_cancel.text(),
                dlg.btn_action.text(),
            ]
            for text in texts:
                self.assertFalse(
                    bool(korean_pattern.search(text)),
                    f"Found residual Korean in language '{lang}': '{text}'",
                )
            dlg.close()

    def test_cached_installer_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cached_dir = Path(tmpdir)
            cached_file = cached_dir / self.sample_rel.asset_name
            # Write exactly matching size
            cached_file.write_bytes(b"0" * 104857600)

            with patch("src.qt.widgets.update_dialog.get_update_download_directory", return_value=cached_dir):
                dlg = UpdateDialog(self.sample_rel, current_version="2.5.0", language_code="ko")
                self.assertIsNotNone(dlg.installer_path)
                self.assertEqual(dlg.progress_bar.value(), 100)
                self.assertFalse(dlg.btn_open_folder.isHidden())
                self.assertIn("지금 설치하기", dlg.btn_action.text())
                dlg.close()

    def test_worker_signals_and_cancellation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "test_worker_installer.exe"
            worker = UpdateDownloadWorker("https://fake.url/download.exe", target)

            # Test cancel flag
            worker.cancel()
            self.assertTrue(worker._is_cancelled)

    @patch("webbrowser.open")
    def test_open_browser_fallback(self, mock_web_open):
        dlg = UpdateDialog(self.sample_rel, current_version="2.5.0", language_code="ko")
        dlg._on_open_browser()
        mock_web_open.assert_called_once_with(self.sample_rel.url)
        dlg.close()

    @patch("src.qt.widgets.update_dialog.verify_installer_signature")
    @patch("subprocess.Popen")
    @patch.object(QApplication, "quit")
    def test_execute_installer_launches_and_quits(self, mock_quit, mock_popen, mock_verify):
        with tempfile.TemporaryDirectory() as tmpdir:
            installer = Path(tmpdir) / "setup.exe"
            installer.write_bytes(b"dummy")

            dlg = UpdateDialog(self.sample_rel, current_version="2.5.0", language_code="ko")
            dlg.chk_auto_install.setChecked(True)
            dlg._execute_installer(installer)

            mock_verify.assert_called_once_with(installer)
            mock_popen.assert_called_once_with([str(installer), *INSTALLER_UPGRADE_ARGS], shell=False)
            self.assertEqual(dlg.result(), 1)
            mock_quit.assert_called_once()
            dlg.close()

    @patch("src.qt.widgets.update_dialog.error_dialog")
    @patch("src.qt.widgets.update_dialog.verify_installer_signature", side_effect=LauncherError("invalid signature"))
    @patch("subprocess.Popen")
    @patch.object(QApplication, "quit")
    def test_execute_installer_blocks_invalid_signature(self, mock_quit, mock_popen, mock_verify, mock_err):
        with tempfile.TemporaryDirectory() as tmpdir:
            installer = Path(tmpdir) / "setup.exe"
            installer.write_bytes(b"untrusted")

            dlg = UpdateDialog(self.sample_rel, current_version="2.5.0", language_code="ko")
            dlg.installer_path = installer
            dlg.chk_auto_install.setChecked(True)
            dlg._execute_installer(installer)

            mock_verify.assert_called_once_with(installer)
            self.assertFalse(installer.exists())
            self.assertIsNone(dlg.installer_path)
            mock_err.assert_called_once()
            mock_popen.assert_not_called()
            mock_quit.assert_not_called()
            dlg.close()

    def test_worker_blocks_invalid_signature_on_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "setup.exe"

            def fake_download(_url, dest, **_kwargs):
                Path(dest).write_bytes(b"untrusted")
                return Path(dest)

            worker = UpdateDownloadWorker("https://fake.url/setup.exe", target)
            finished_paths = []
            failed_msgs = []
            worker.finished.connect(finished_paths.append)
            worker.failed.connect(failed_msgs.append)

            with (
                patch("src.qt.widgets.update_dialog.download_release_asset", side_effect=fake_download),
                patch("src.qt.widgets.update_dialog.verify_installer_signature", side_effect=LauncherError("bad thumbprint")),
            ):
                worker.run()

            self.assertFalse(target.exists())
            self.assertEqual(finished_paths, [])
            self.assertEqual(failed_msgs, ["bad thumbprint"])


if __name__ == "__main__":
    unittest.main()
