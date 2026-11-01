"""Unit tests for "Explain, don't block" micro-nudges (ui_state + DWH tab)."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QPushButton

from src.i18n import _UI_TEXT
from src.qt.app import create_or_get_app
from src.qt.tabs.dwh_tab import DwhPipelineTab, _make_unique_backup_path
from src.qt.ui_state import NudgeState, format_nudge, nudge_reason, set_button_enabled_with_nudge

HANGUL_RE = re.compile(r"[가-힣]")

NUDGE_KEYS = (
    "nudge_reason_prefix", "nudge_action_prefix",
    "dwh_dlg_sample_confirm_title", "dwh_dlg_sample_confirm_body",
    "dwh_nudge_missing_header", "dwh_nudge_missing_cust", "dwh_nudge_missing_prod",
    "dwh_nudge_missing_pnl", "dwh_nudge_missing_action",
    "dwh_nudge_mart_not_built", "dwh_nudge_mart_action",
    "dwh_nudge_running", "dwh_nudge_running_action",
    "dwh_nudge_settings_error", "dwh_nudge_settings_action",
    "dwh_nudge_no_error_log", "dwh_nudge_no_error_log_action",
    "dwh_tip_preview_ready", "dwh_tip_copy_mcode_ready", "dwh_tip_copy_path_ready",
    "dwh_tip_error_log_ready", "dwh_tip_download_template", "dwh_tip_gen_sample",
    "dwh_tip_refresh", "dwh_tip_import_cust", "dwh_tip_import_prod", "dwh_tip_import_pnl",
    "dwh_empty_state_title", "dwh_empty_state_body", "dwh_empty_state_safe", "dwh_dlg_file_lock_err",
)


class TestUiStateHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def test_disabled_shows_reason_and_action(self):
        btn = QPushButton()
        set_button_enabled_with_nudge(btn, False, language_code="en", reason="R1", action="A1")
        t = _UI_TEXT["en"]
        self.assertFalse(btn.isEnabled())
        self.assertEqual(
            btn.toolTip(),
            f"{t['nudge_reason_prefix']} R1\n{t['nudge_action_prefix']} A1",
        )
        self.assertEqual(btn.accessibleDescription(), btn.toolTip())
        self.assertEqual(nudge_reason(btn), "R1")

    def test_enabled_restores_ready_tip_and_clears_reason(self):
        btn = QPushButton()
        set_button_enabled_with_nudge(btn, False, reason="R1", action="A1")
        set_button_enabled_with_nudge(btn, True, ready_tip="Ready!", reason="R1")
        self.assertTrue(btn.isEnabled())
        self.assertEqual(btn.toolTip(), "Ready!")
        self.assertEqual(nudge_reason(btn), "")

    def test_nudge_state_snapshot_and_single_line_format(self):
        btn = QPushButton()
        self.assertIsNone(nudge_reason(btn))
        NudgeState(enabled=False, reason="R2").apply(btn, "pl")
        self.assertFalse(btn.isEnabled())
        self.assertEqual(btn.toolTip(), f"{_UI_TEXT['pl']['nudge_reason_prefix']} R2")
        self.assertEqual(format_nudge("", "", "ko"), "")


class TestNudgeI18n(unittest.TestCase):
    def test_all_nudge_keys_exist_in_every_language(self):
        for lang in ("ko", "en", "pl"):
            for key in NUDGE_KEYS:
                self.assertTrue(_UI_TEXT[lang].get(key), f"[{lang}] missing i18n key: {key}")

    def test_no_hangul_in_english_and_polish_nudges(self):
        for lang in ("en", "pl"):
            for key in NUDGE_KEYS:
                self.assertIsNone(HANGUL_RE.search(_UI_TEXT[lang][key]), f"[{lang}] {key} contains Korean")


class TestDwhTabNudges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.settings = SimpleNamespace(
            raw_dir=root,
            masters_dir=root / "masters",
            pnl_dir=root / "pnl",
            inventory_dir=root / "inventory",
            price_dir=root / "price",
            mart_export_dir=root / "export",
            logs_dir=root / "logs",
            entity_code="PL",
            reporting_currency="PLN",
        )
        for d in (
            self.settings.masters_dir,
            self.settings.pnl_dir,
            self.settings.inventory_dir,
            self.settings.price_dir,
            self.settings.mart_export_dir,
        ):
            d.mkdir(parents=True)
        self.settings.ensure_directories = lambda: None

        self.load_settings = mock.patch("src.qt.tabs.dwh_tab.load_settings", return_value=self.settings).start()
        mock.patch("src.qt.tabs.dwh_tab.load_account_mapping", return_value={}).start()
        self.addCleanup(mock.patch.stopall)

        self.tab = DwhPipelineTab(language_code="ko")
        self.addCleanup(self.tab.deleteLater)
        self.addCleanup(self.tab.close)

    def _write(self, path: Path, text: str = "h\nv\n") -> None:
        path.write_text(text, encoding="utf-8")

    def test_run_button_names_exactly_the_missing_sources(self):
        t = _UI_TEXT["ko"]
        self._write(self.settings.masters_dir / "master_customers.csv")
        self.tab._check_source_files()

        tip = self.tab.btn_run_dwh.toolTip()
        self.assertFalse(self.tab.btn_run_dwh.isEnabled())
        self.assertNotIn(t["dwh_nudge_missing_cust"], tip)
        self.assertIn(t["dwh_nudge_missing_prod"], tip)
        self.assertIn(t["dwh_nudge_missing_pnl"], tip)
        self.assertIn(t["dwh_nudge_missing_action"], tip)

        self._write(self.settings.masters_dir / "master_products.csv")
        self._write(self.settings.pnl_dir / "raw_pnl.csv")
        self.tab._check_source_files()
        self.assertTrue(self.tab.btn_run_dwh.isEnabled())
        self.assertEqual(self.tab.btn_run_dwh.toolTip(), t["dwh_build_tooltip_ready"])
        self.assertTrue(self.tab.empty_state_frame.isHidden())

    def test_mart_buttons_nudge_until_mart_is_built(self):
        t = _UI_TEXT["ko"]
        mart_buttons = (self.tab.btn_preview_mart, self.tab.btn_copy_parquet_path, self.tab.btn_copy_mcode)
        for btn in mart_buttons:
            self.assertFalse(btn.isEnabled())
            self.assertIn(t["dwh_nudge_mart_action"], btn.toolTip())
            self.assertEqual(nudge_reason(btn), t["dwh_nudge_mart_not_built"])

        (self.settings.mart_export_dir / "v_ai_pnl_summary.parquet").write_bytes(b"PAR1")
        self.tab._check_source_files()
        for btn in mart_buttons:
            self.assertTrue(btn.isEnabled())
            self.assertEqual(nudge_reason(btn), "")
        self.assertEqual(self.tab.btn_preview_mart.toolTip(), t["dwh_tip_preview_ready"])

    def test_empty_state_shows_safety_reassurance(self):
        self.assertFalse(self.tab.empty_state_frame.isHidden())
        self.assertIn("원본 파일은 절대 수정되지 않습니다", self.tab.lbl_empty_safe.text())

    def test_language_switch_retranslates_nudges(self):
        for lang in ("en", "pl"):
            self.tab.apply_language(lang)
            t = _UI_TEXT[lang]
            self.assertIn(t["dwh_nudge_missing_header"], self.tab.btn_run_dwh.toolTip())
            self.assertEqual(self.tab.lbl_empty_safe.text(), t["dwh_empty_state_safe"])
            for btn in (
                self.tab.btn_run_dwh, self.tab.btn_preview_mart, self.tab.btn_copy_mcode,
                self.tab.btn_copy_parquet_path, self.tab.btn_open_err_log,
            ):
                self.assertIsNone(HANGUL_RE.search(btn.toolTip()), f"[{lang}] {btn.toolTip()!r}")

        self.tab.apply_language("ko")
        self.assertIn(_UI_TEXT["ko"]["dwh_nudge_missing_header"], self.tab.btn_run_dwh.toolTip())

    def test_running_gate_explains_and_restores(self):
        for name in ("master_customers.csv", "master_products.csv"):
            self._write(self.settings.masters_dir / name)
        self._write(self.settings.pnl_dir / "raw_pnl.csv")
        self.tab._check_source_files()
        self.assertTrue(self.tab.btn_run_dwh.isEnabled())

        running = _UI_TEXT["ko"]["dwh_nudge_running"]
        self.tab._set_running(True)
        for btn in (
            self.tab.btn_run_dwh, self.tab.btn_download_template, self.tab.btn_gen_sample,
            self.tab.btn_refresh, self.tab.btn_preview_mart,
            self.tab.btn_import_cust, self.tab.btn_import_prod, self.tab.btn_import_pnl,
        ):
            self.assertFalse(btn.isEnabled())
            self.assertEqual(nudge_reason(btn), running)

        self.tab._set_running(False)
        self.assertTrue(self.tab.btn_run_dwh.isEnabled())
        self.assertTrue(self.tab.btn_refresh.isEnabled())
        self.assertTrue(self.tab.btn_import_cust.isEnabled())
        self.assertEqual(self.tab.btn_import_cust.toolTip(), _UI_TEXT["ko"]["dwh_tip_import_cust"])
        self.assertFalse(self.tab.btn_preview_mart.isEnabled())

    def test_settings_failure_disables_build_with_reason(self):
        self.load_settings.side_effect = RuntimeError("settings.yaml broken")
        self.tab._check_source_files()
        self.assertFalse(self.tab.btn_run_dwh.isEnabled())
        self.assertIn("settings.yaml broken", nudge_reason(self.tab.btn_run_dwh))

    def test_generate_sample_aborts_on_settings_failure(self):
        with (
            mock.patch("src.qt.tabs.dwh_tab.error_dialog") as mock_err,
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog") as mock_confirm,
            mock.patch("src.sample_data.generate_sample_data") as mock_gen,
        ):
            self.load_settings.side_effect = RuntimeError("settings fail")
            self.tab._on_generate_sample()
            mock_err.assert_called_once()
            mock_confirm.assert_not_called()
            mock_gen.assert_not_called()

    def test_generate_sample_prompts_when_sources_exist(self):
        with (
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog") as mock_confirm,
            mock.patch("src.qt.tabs.dwh_tab.info_dialog") as mock_info,
            mock.patch("src.sample_data.generate_sample_data") as mock_gen,
        ):
            # Case 1: No sources exist -> directly generates with explicit directories
            self.tab._on_generate_sample()
            mock_confirm.assert_not_called()
            mock_gen.assert_called_once_with(
                masters_dir=self.settings.masters_dir,
                pnl_dir=self.settings.pnl_dir,
                inv_dir=self.settings.inventory_dir,
                price_dir=self.settings.price_dir,
            )
            mock_info.assert_called_once()

            mock_gen.reset_mock()
            mock_info.reset_mock()
            # Case 2: Only inventory file exists -> prompts confirmation, user declines -> aborted
            self._write(self.settings.inventory_dir / "raw_inventory.csv")
            mock_confirm.return_value = False
            self.tab._on_generate_sample()
            mock_confirm.assert_called_once()
            mock_gen.assert_not_called()
            mock_info.assert_not_called()

            mock_confirm.reset_mock()
            # Case 3: Target master file exists -> prompts confirmation, user accepts -> auto-backup created & proceeds
            (self.settings.inventory_dir / "raw_inventory.csv").unlink()
            original_content = "bill_to_code,customer_name\n123,Original Client\n"
            self._write(self.settings.masters_dir / "master_customers.csv", original_content)
            mock_confirm.return_value = True
            self.tab._on_generate_sample()
            mock_confirm.assert_called_once()
            mock_gen.assert_called_once_with(
                masters_dir=self.settings.masters_dir,
                pnl_dir=self.settings.pnl_dir,
                inv_dir=self.settings.inventory_dir,
                price_dir=self.settings.price_dir,
            )
            mock_info.assert_called_once()

            # Verify automated backup creation preserves original user data
            bak_files = list(self.settings.masters_dir.glob("master_customers.csv.bak_*"))
            self.assertTrue(len(bak_files) >= 1)
            self.assertEqual(bak_files[0].read_text(encoding="utf-8"), original_content)

    def test_generate_sample_backup_failure_aborts_generation(self):
        with (
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog", return_value=True),
            mock.patch("src.qt.tabs.dwh_tab.error_dialog") as mock_err,
            mock.patch("shutil.move", side_effect=PermissionError("File locked in Excel")),
            mock.patch("src.sample_data.generate_sample_data") as mock_gen,
        ):
            self._write(self.settings.masters_dir / "master_customers.csv")
            self.tab._on_generate_sample()
            mock_err.assert_called_once()
            mock_gen.assert_not_called()

    def test_import_pnl_archives_previous_pnl_files_and_sets_active(self):
        with (
            mock.patch("src.qt.tabs.dwh_tab.choose_file_dialog") as mock_choose,
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog", return_value=True),
            mock.patch("src.qt.tabs.dwh_tab.info_dialog"),
        ):
            # Step 1: Simulate existing sample P&L file
            sample_pnl = self.settings.pnl_dir / "raw_pnl_2026_q1.csv"
            self._write(sample_pnl, "period,entity_code\n2026-01,PL\n")

            # Step 2: User imports real P&L file
            real_source = self.settings.raw_dir / "my_user_pnl.csv"
            real_content = "period,entity_code,custom_note\n2026-03,PL,RealUserActuals\n"
            self._write(real_source, real_content)
            mock_choose.return_value = str(real_source)

            self.tab._on_import_file("pnl")

            # Step 3: Verify previous sample P&L was archived to .bak_*
            bak_files = list(self.settings.pnl_dir.glob("raw_pnl_2026_q1.csv.bak_*"))
            self.assertTrue(len(bak_files) >= 1)

            # Step 4: Verify active P&L file exists and contains the real imported data
            active_pnl = self.settings.pnl_dir / "raw_pnl_active.csv"
            self.assertTrue(active_pnl.is_file())
            self.assertEqual(active_pnl.read_text(encoding="utf-8"), real_content)

            # Step 5: Verify Extractor._resolve_source_file strictly picks the active file
            from src.extractor import DataExtractor
            extractor = DataExtractor(settings=self.settings)
            resolved = extractor._resolve_source_file(None, self.settings.pnl_dir, "raw_pnl_*.csv")
            self.assertEqual(resolved, active_pnl)

    def test_running_tooltip_retranslates_on_language_change(self):
        self.tab._set_running(True)
        self.assertTrue(self.tab._is_running)

        # Progress bar at step 2 while running
        self.tab.progress_bar.setValue(2)

        # Switch to English while running
        self.tab.apply_language("en")
        t_en = _UI_TEXT["en"]
        self.assertEqual(nudge_reason(self.tab.btn_download_template), t_en["dwh_nudge_running"])
        self.assertEqual(nudge_reason(self.tab.btn_gen_sample), t_en["dwh_nudge_running"])
        self.assertIsNone(HANGUL_RE.search(self.tab.btn_download_template.toolTip()))
        # Completed steps show English name and checkmark; subsequent steps show circle
        self.assertIn("✔", self.tab.lbl_step1.text())
        self.assertIn(t_en["dwh_step1_name"], self.tab.lbl_step1.text())
        self.assertIn("✔", self.tab.lbl_step2.text())
        self.assertIn(t_en["dwh_step2_name"], self.tab.lbl_step2.text())
        self.assertIn("○", self.tab.lbl_step3.text())
        self.assertIn(t_en["dwh_step3_name"], self.tab.lbl_step3.text())

        # Switch to Polish while running
        self.tab.apply_language("pl")
        t_pl = _UI_TEXT["pl"]
        self.assertEqual(nudge_reason(self.tab.btn_download_template), t_pl["dwh_nudge_running"])
        self.assertIsNone(HANGUL_RE.search(self.tab.btn_download_template.toolTip()))

        # Switch back to Korean
        self.tab.apply_language("ko")
        t_ko = _UI_TEXT["ko"]
        self.assertEqual(nudge_reason(self.tab.btn_download_template), t_ko["dwh_nudge_running"])

        self.tab._set_running(False)

    def test_dwh_pipeline_runs_with_job_runner(self):
        mock_runner = mock.MagicMock()
        mock_runner.start.return_value = True
        self.tab.job_runner = mock_runner

        self.tab._on_run_dwh_pipeline()
        self.assertTrue(self.tab._is_running)
        mock_runner.start.assert_called_once()
        name, worker, callbacks = mock_runner.start.call_args[0]
        self.assertEqual(name, "dwh_pipeline")
        self.assertTrue(callable(worker))
        self.assertTrue(callable(callbacks.on_progress))
        self.assertTrue(callable(callbacks.on_success))
        self.assertTrue(callable(callbacks.on_error))


    def test_import_pnl_when_selected_is_in_pnl_dir(self):
        """Verify importing a file already inside pnl_dir does not delete it before copy."""
        with (
            mock.patch("src.qt.tabs.dwh_tab.choose_file_dialog") as mock_choose,
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog", return_value=True),
            mock.patch("src.qt.tabs.dwh_tab.info_dialog") as mock_info,
            mock.patch("src.qt.tabs.dwh_tab.error_dialog") as mock_err,
        ):
            # User picks raw_pnl_2026_q1.csv located INSIDE the pnl_dir
            source_pnl = self.settings.pnl_dir / "raw_pnl_2026_q1.csv"
            source_content = "period,entity_code,val\n2026-01,PL,100\n"
            self._write(source_pnl, source_content)
            mock_choose.return_value = str(source_pnl)

            self.tab._on_import_file("pnl")

            mock_err.assert_not_called()
            mock_info.assert_called_once()

            # Verify active file exists and has the exact content
            active_pnl = self.settings.pnl_dir / "raw_pnl_active.csv"
            self.assertTrue(active_pnl.is_file())
            self.assertEqual(active_pnl.read_text(encoding="utf-8"), source_content)

            # Verify the original file was archived safely into .bak_*
            bak_files = list(self.settings.pnl_dir.glob("raw_pnl_2026_q1.csv.bak_*"))
            self.assertEqual(len(bak_files), 1)
            self.assertEqual(bak_files[0].read_text(encoding="utf-8"), source_content)

            # Verify no temporary files remain
            tmp_files = list(self.settings.pnl_dir.glob("*.tmp"))
            self.assertEqual(len(tmp_files), 0)

    def test_import_pnl_backup_failure_rollbacks_and_cleans_tmp(self):
        """Verify that if archiving fails during import, state is rolled back cleanly."""
        with (
            mock.patch("src.qt.tabs.dwh_tab.choose_file_dialog") as mock_choose,
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog", return_value=True),
            mock.patch("src.qt.tabs.dwh_tab.error_dialog") as mock_err,
        ):
            active_pnl = self.settings.pnl_dir / "raw_pnl_active.csv"
            self._write(active_pnl, "original_active_content")

            new_source = self.settings.raw_dir / "new_upload.csv"
            self._write(new_source, "new_upload_content")
            mock_choose.return_value = str(new_source)

            with mock.patch("shutil.move", side_effect=PermissionError("Locked by Excel")):
                self.tab._on_import_file("pnl")

            mock_err.assert_called_once()
            # Active file should be restored / preserved
            self.assertTrue(active_pnl.is_file())
            self.assertEqual(active_pnl.read_text(encoding="utf-8"), "original_active_content")

            # Staged temporary files should be cleaned up
            tmp_files = list(self.settings.pnl_dir.glob("*.tmp"))
            self.assertEqual(len(tmp_files), 0)

    def test_generate_sample_backup_partial_failure_rollbacks_all_moved_files(self):
        """Verify partial failure during sample backup restores previously moved files."""
        cust_file = self.settings.masters_dir / "master_customers.csv"
        prod_file = self.settings.masters_dir / "master_products.csv"
        self._write(cust_file, "customer_orig")
        self._write(prod_file, "product_orig")

        import shutil as shutil_mod
        real_move = shutil_mod.move
        call_count = [0]

        def side_effect_move(src, dst):
            call_count[0] += 1
            if call_count[0] == 2:
                raise PermissionError("Locked second file in Excel")
            return real_move(src, dst)

        with (
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog", return_value=True),
            mock.patch("src.qt.tabs.dwh_tab.error_dialog") as mock_err,
            mock.patch("shutil.move", side_effect=side_effect_move),
            mock.patch("src.sample_data.generate_sample_data") as mock_gen,
        ):
            self.tab._on_generate_sample()
            mock_err.assert_called_once()
            mock_gen.assert_not_called()

            # Both files must still exist in their original locations after rollback!
            self.assertTrue(cust_file.is_file(), "First file was not rolled back!")
            self.assertTrue(prod_file.is_file(), "Second file was missing!")
            self.assertEqual(cust_file.read_text(encoding="utf-8"), "customer_orig")
            self.assertEqual(prod_file.read_text(encoding="utf-8"), "product_orig")

    def test_generate_sample_generator_failure_restores_user_originals(self):
        """Verify that if sample generator fails after partially creating files, originals are restored."""
        cust_file = self.settings.masters_dir / "master_customers.csv"
        self._write(cust_file, "customer_user_data")

        def fail_generator(**kwargs):
            # Simulate sample generator creating a partial new customer file before crashing
            cust_file.write_text("partial_sample_data", encoding="utf-8")
            raise RuntimeError("Out of memory during synthetic generation")

        with (
            mock.patch("src.qt.tabs.dwh_tab.confirm_dialog", return_value=True),
            mock.patch("src.qt.tabs.dwh_tab.error_dialog") as mock_err,
            mock.patch("src.sample_data.generate_sample_data", side_effect=fail_generator),
        ):
            self.tab._on_generate_sample()
            mock_err.assert_called_once()
            # Customer file must be restored to original user data, NOT left with partial sample data!
            self.assertTrue(cust_file.is_file())
            self.assertEqual(cust_file.read_text(encoding="utf-8"), "customer_user_data")

    def test_make_unique_backup_path_avoids_timestamp_collision(self):
        """Verify unique backup paths are generated when timestamps collide."""
        target = self.settings.masters_dir / "master_customers.csv"
        self._write(target, "dummy")

        ts = "20261101_120000"
        p1 = _make_unique_backup_path(target, ts)
        self.assertEqual(p1.name, "master_customers.csv.bak_20261101_120000")
        self._write(p1, "bak1")

        p2 = _make_unique_backup_path(target, ts)
        self.assertEqual(p2.name, "master_customers.csv.bak_20261101_120000_1")
        self._write(p2, "bak2")

        p3 = _make_unique_backup_path(target, ts)
        self.assertEqual(p3.name, "master_customers.csv.bak_20261101_120000_2")


if __name__ == "__main__":
    unittest.main()
