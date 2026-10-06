"""Path convention and migration safety tests; all storage is temporary."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from src import app_paths
from src.update_checker import load_settings


class TestAppPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name)
        self.env = patch.dict("os.environ", {"LOCALAPPDATA": str(self.local)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.target = self.local / "Programs" / "Data Refinery" / "UserSetting"
        self.legacy = self.local / "Data Refinery"
        self.datasets = self.local / "DataRefinery" / "datasets"

    def _legacy_settings(self):
        self.legacy.mkdir(parents=True)
        (self.legacy / "settings.json").write_text('{"update_check_enabled":false}', encoding="utf-8")
        (self.legacy / "recent_configurations.json").write_text('{"version":1,"entries":{}}', encoding="utf-8")
        (self.legacy / "logs").mkdir()
        (self.legacy / "logs" / "errors.log").write_text("error-id", encoding="utf-8")
        (self.legacy / "presets").mkdir()
        (self.legacy / "presets" / "layout.json").write_text("{}", encoding="utf-8")

    def test_paths_are_under_programs_usersetting(self):
        self.assertEqual(app_paths.installation_directory(), self.target.parent)
        self.assertEqual(app_paths.user_settings_directory(), self.target)
        self.assertEqual(app_paths.dataset_storage_directory(), self.target / "datasets")

    def test_migrates_settings_logs_presets_and_real_database_without_changing_sources(self):
        self._legacy_settings()
        db = self.datasets / "sales" / "workspace.duckdb"
        db.parent.mkdir(parents=True)
        with duckdb.connect(str(db)) as connection:
            connection.execute("CREATE TABLE sales AS SELECT 202605 AS period, 123.45 AS amount")
        (self.datasets / "datasets_registry.json").write_text("[]", encoding="utf-8")
        before_settings = app_paths._snapshot(self.legacy)
        before_datasets = app_paths._snapshot(self.datasets)
        self.assertEqual(load_settings(), {"update_check_enabled": False})
        with duckdb.connect(str(self.target / "datasets" / "sales" / "workspace.duckdb"), read_only=True) as connection:
            row = connection.execute("SELECT * FROM sales").fetchone()
            self.assertEqual(row[0], 202605)
            self.assertEqual(float(row[1]), 123.45)
        self.assertEqual(app_paths._snapshot(self.legacy), before_settings)
        self.assertEqual(app_paths._snapshot(self.datasets), before_datasets)
        self.assertEqual((self.target / "logs" / "errors.log").read_text(), "error-id")
        self.assertTrue((self.target / "presets" / "layout.json").exists())

    def test_new_settings_and_new_dataset_tree_win_over_legacy(self):
        self._legacy_settings()
        self.target.mkdir(parents=True)
        (self.target / "settings.json").write_text('{"update_check_enabled":true}', encoding="utf-8")
        (self.target / "datasets").mkdir()
        (self.target / "datasets" / "datasets_registry.json").write_text('["current"]', encoding="utf-8")
        self.datasets.mkdir(parents=True)
        (self.datasets / "datasets_registry.json").write_text('["old"]', encoding="utf-8")
        app_paths.ensure_user_settings_directory()
        self.assertEqual(load_settings(), {"update_check_enabled": True})
        self.assertEqual(json.loads((self.target / "datasets" / "datasets_registry.json").read_text()), ["current"])
        self.assertEqual(len(list(self.target.parent.glob("UserSetting.before-migration-*"))), 1)

    def test_migration_does_not_restore_intentionally_deleted_new_settings(self):
        self._legacy_settings()
        app_paths.ensure_user_settings_directory()
        (self.target / "settings.json").unlink()
        self.assertEqual(load_settings(), {"update_check_enabled": True})
        self.assertFalse((self.target / "settings.json").exists())

    def test_failed_copy_keeps_sources_and_does_not_publish_partial_tree(self):
        self._legacy_settings()
        before = app_paths._snapshot(self.legacy)
        with patch("src.app_paths.shutil.copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                app_paths.ensure_user_settings_directory()
        self.assertEqual(app_paths._snapshot(self.legacy), before)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob(".usersetting-stage-*")), [])
        self.assertEqual(app_paths.ensure_user_settings_directory(), self.target)

    def test_wal_blocks_database_copy(self):
        self._legacy_settings()
        self.datasets.mkdir(parents=True)
        (self.datasets / "workspace.duckdb.wal").write_bytes(b"pending")
        with self.assertRaises(OSError):
            app_paths.ensure_user_settings_directory()
        self.assertFalse(self.target.exists())
        self.assertTrue((self.datasets / "workspace.duckdb.wal").exists())

    def test_changed_source_aborts_publication(self):
        self._legacy_settings()
        merge = app_paths._merge_missing

        def changing_merge(source, target):
            merge(source, target)
            (self.legacy / "settings.json").write_text("{}", encoding="utf-8")

        with patch("src.app_paths._merge_missing", side_effect=changing_merge):
            with self.assertRaises(OSError):
                app_paths.ensure_user_settings_directory()
        self.assertFalse(self.target.exists())

    def test_another_migrations_lock_is_not_deleted(self):
        self.target.parent.mkdir(parents=True)
        lock = self.target.parent / ".usersetting-migration.lock"
        with app_paths._acquire_migration_lock(lock):
            with self.assertRaises(OSError):
                app_paths.ensure_user_settings_directory()
        self.assertTrue(lock.exists())
        self.assertEqual(app_paths.ensure_user_settings_directory(), self.target)
