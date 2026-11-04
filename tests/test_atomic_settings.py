"""Settings failure checks use only disposable paths and isolated AppData."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.atomic_write import atomic_write_json
from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.update_checker import load_settings, save_settings
from src import session_memory


class AtomicSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ,
            {
                "LOCALAPPDATA": str(self.root / "local"),
                "APPDATA": str(self.root / "roaming"),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_replace_failure_preserves_settings_and_other_temporary_file(self):
        target = self.root / "settings.json"
        target.write_bytes(b'{"update_check_enabled":false}')
        other = self.root / "settings.json.tmp"
        other.write_bytes(b"other writer")
        with patch(
            "src.atomic_write.os.replace", side_effect=PermissionError("locked")
        ):
            with self.assertRaises(PermissionError):
                save_settings({"update_check_enabled": True}, target)
        self.assertEqual(target.read_bytes(), b'{"update_check_enabled":false}')
        self.assertEqual(other.read_bytes(), b"other writer")
        self.assertEqual(list(self.root.glob(".settings.json.*.tmp")), [])

    def test_flush_failure_preserves_original(self):
        target = self.root / "settings.json"
        target.write_bytes(b"original")
        with patch("src.atomic_write.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                atomic_write_json(target, {"new": 1})
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows replacement errors")
    def test_transient_windows_replace_errors_retry_same_closed_sibling(self):
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                target = self.root / ("settings-" + str(code) + ".json")
                target.write_text("old", encoding="utf-8")
                original = os.replace
                calls = []
                error = PermissionError(13, "injected")
                error.winerror = code

                def replace(source, destination):
                    calls.append(source)
                    if len(calls) == 1:
                        raise error
                    return original(source, destination)

                with (
                    patch("src.atomic_write.os.replace", side_effect=replace),
                    patch("src.atomic_write.time.sleep") as wait,
                ):
                    atomic_write_json(target, {"ok": True})
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0], calls[1])
                wait.assert_called_once_with(0.05)
                self.assertEqual(
                    json.loads(target.read_text(encoding="utf-8")), {"ok": True}
                )

    @unittest.skipUnless(os.name == "nt", "Windows replacement errors")
    def test_permanent_windows_replace_failure_preserves_original(self):
        target = self.root / "settings.json"
        target.write_text("old", encoding="utf-8")
        error = PermissionError(13, "injected")
        error.winerror = 5
        with (
            patch("src.atomic_write.os.replace", side_effect=error) as replace,
            patch("src.atomic_write.time.sleep") as wait,
        ):
            with self.assertRaises(PermissionError) as caught:
                atomic_write_json(target, {"new": 1})
        self.assertIs(caught.exception, error)
        self.assertEqual(replace.call_count, 4)
        self.assertEqual(wait.call_count, 3)
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.root.glob(".settings.json.*.tmp")), [])

    def test_fsync_failure_does_not_retry_replace(self):
        target = self.root / "settings.json"
        target.write_text("old", encoding="utf-8")
        with (
            patch("src.atomic_write.os.fsync", side_effect=OSError("failure")) as fsync,
            patch("src.atomic_write.os.replace") as replace,
            patch("src.atomic_write.time.sleep") as wait,
        ):
            with self.assertRaises(OSError):
                atomic_write_json(target, {"new": 1})
        fsync.assert_called_once()
        replace.assert_not_called()
        wait.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "old")

    def test_unique_temporary_paths_and_explicit_false_setting(self):
        target = self.root / "settings.json"
        paths = []
        replace = os.replace

        def record(source, destination):
            paths.append(source)
            return replace(source, destination)

        with patch("src.atomic_write.os.replace", side_effect=record):
            save_settings({"update_check_enabled": False}, target)
            save_settings({"update_check_enabled": False, "custom": 25}, target)
        self.assertEqual(len(set(paths)), 2)
        self.assertEqual(
            load_settings(target), {"update_check_enabled": False, "custom": 25}
        )

    def test_registry_save_and_delete_failure_preserve_registry_and_workspace(self):
        registry = DatasetRegistry(self.root / "datasets")
        dataset = DatasetDefinition.create_new("Example", "input", "publish")
        registry.save_dataset(dataset)
        original = registry._registry_file.read_bytes()
        database = registry.get_dataset_db_path(dataset.id)
        database.write_bytes(b"workspace fixture")
        dataset.name = "Changed"
        with patch(
            "src.atomic_write.os.replace", side_effect=PermissionError("locked")
        ):
            with self.assertRaises(PermissionError):
                registry.save_dataset(dataset)
            with self.assertRaises(PermissionError):
                registry.delete_dataset(dataset.id)
        self.assertEqual(registry._registry_file.read_bytes(), original)
        self.assertEqual(database.read_bytes(), b"workspace fixture")
        self.assertEqual(json.loads(original)[0]["name"], "Example")

    def test_unreadable_registry_cannot_be_overwritten_or_delete_workspace(self):
        registry = DatasetRegistry(self.root / "datasets")
        dataset = DatasetDefinition.create_new("Example", "input", "publish")
        registry.save_dataset(dataset)
        database = registry.get_dataset_db_path(dataset.id)
        database.write_bytes(b"workspace fixture")
        for invalid in (b"invalid json", b"{}"):
            registry._registry_file.write_bytes(invalid)
            for operation in (
                lambda: registry.save_dataset(dataset),
                lambda: registry.delete_dataset(dataset.id),
            ):
                with self.assertRaises(ValueError):
                    operation()
                self.assertEqual(registry._registry_file.read_bytes(), invalid)
                self.assertEqual(database.read_bytes(), b"workspace fixture")
        with patch("builtins.open", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                registry.save_dataset(dataset)
            with self.assertRaises(PermissionError):
                registry.delete_dataset(dataset.id)

    def test_settings_read_fallback_cannot_erase_corrupt_original(self):
        target = self.root / "settings.json"
        for invalid in (b"invalid json", b"[]", b"\xffinvalid utf8"):
            target.write_bytes(invalid)
            settings = load_settings(target)
            with self.assertRaises(OSError):
                save_settings(settings, target)
            self.assertEqual(target.read_bytes(), invalid)
            self.assertEqual(list(self.root.glob(".settings.json.*.tmp")), [])
        target.write_bytes(b'{"update_check_enabled":false}')
        with patch.object(Path, "read_text", side_effect=PermissionError("locked")):
            settings = load_settings(target)
            with self.assertRaises(OSError):
                save_settings(settings, target)
        self.assertEqual(target.read_bytes(), b'{"update_check_enabled":false}')

    def test_recent_recovery_preserves_verified_original_and_backup_failure_blocks_save(
        self,
    ):
        target = self.root / "recent_configurations.json"
        original = b"broken recent configuration"
        target.write_bytes(original)
        with patch("src.session_memory._store_path", return_value=target):
            with patch(
                "src.session_memory.os.fsync", side_effect=OSError("backup unavailable")
            ):
                session_memory.remember("input.csv", {"custom": 25})
            self.assertEqual(target.read_bytes(), original)
            session_memory.remember("input.csv", {"custom": 25})
            self.assertEqual(session_memory.recall("input.csv"), {"custom": 25})
        backups = list(self.root.glob("recent_configurations.json.*.bak"))
        self.assertTrue(backups)
        self.assertTrue(all(backup.read_bytes() == original for backup in backups))


if __name__ == "__main__":
    unittest.main()
