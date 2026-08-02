import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.update_checker import ReleaseInfo, check_for_update, load_settings, should_check, version_key


class TestUpdateChecker(unittest.TestCase):
    def test_parses_stable_three_part_versions_only(self):
        self.assertEqual(version_key("v1.6.0"), (1, 6, 0))
        self.assertEqual(version_key("1.6.0"), (1, 6, 0))
        self.assertIsNone(version_key("1.6"))
        self.assertIsNone(version_key("v1.6.0-beta"))

    def test_respects_the_24_hour_cache(self):
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        settings = {"update_check_enabled": True, "last_update_check": (now - timedelta(hours=23)).isoformat()}
        self.assertFalse(should_check(settings, now))
        settings["last_update_check"] = (now - timedelta(hours=24, seconds=1)).isoformat()
        self.assertTrue(should_check(settings, now))

    def test_returns_only_a_newer_release(self):
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        settings = {"update_check_enabled": True}
        newer = check_for_update(
            "1.6.0",
            settings,
            now=now,
            fetcher=lambda: ReleaseInfo("1.6.1", "https://example.test/release", "notes"),
        )
        self.assertEqual(newer.version, "1.6.1")

        settings = {"update_check_enabled": True}
        self.assertIsNone(check_for_update(
            "1.6.0",
            settings,
            now=now,
            fetcher=lambda: ReleaseInfo("1.6.0", "https://example.test/release", "notes"),
        ))

    def test_network_errors_are_silent(self):
        settings = {"update_check_enabled": True}
        self.assertIsNone(check_for_update(
            "1.6.0",
            settings,
            fetcher=lambda: (_ for _ in ()).throw(OSError("offline")),
        ))

    def test_migrates_legacy_update_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            local_app_data = Path(directory) / "Local"
            legacy_path = local_app_data / "CSV Modifier" / "settings.json"
            target_path = local_app_data / "Data Refinery" / "settings.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(
                json.dumps({"update_check_enabled": False}), encoding="utf-8"
            )

            with patch("src.update_checker.settings_path", return_value=target_path):
                self.assertEqual(load_settings(), {"update_check_enabled": False})
