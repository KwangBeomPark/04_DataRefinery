import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.update_checker import (
    ReleaseInfo,
    check_for_update,
    load_settings,
    should_check,
    version_key,
)


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
        self.assertNotIn("last_update_check", settings)

    def test_network_errors_can_be_reported_without_caching_success(self):
        errors = []
        settings = {"update_check_enabled": True}
        self.assertIsNone(check_for_update(
            "1.6.0",
            settings,
            fetcher=lambda: (_ for _ in ()).throw(OSError("offline")),
            on_error=errors.append,
        ))
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OSError)
        self.assertNotIn("last_update_check", settings)

    def test_migrates_legacy_update_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            local_app_data = Path(directory) / "Local"
            legacy_path = local_app_data / "CSV Modifier" / "settings.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(
                json.dumps({"update_check_enabled": False}), encoding="utf-8"
            )

            with patch.dict("os.environ", {"LOCALAPPDATA": str(local_app_data)}):
                self.assertEqual(load_settings(), {"update_check_enabled": False})
                target_path = local_app_data / "Programs" / "Data Refinery" / "UserSetting" / "settings.json"
                self.assertTrue(target_path.exists())
                self.assertTrue(legacy_path.exists())

    def test_fetch_latest_release_parses_assets(self):
        from src.update_checker import fetch_latest_release

        fake_payload = json.dumps({
            "tag_name": "v2.5.0",
            "html_url": "https://github.com/test/repo/releases/tag/v2.5.0",
            "body": "Test release notes",
            "assets": [
                {
                    "name": "App04_DataRefinery_Setup_v2.5.0.exe",
                    "browser_download_url": "https://github.com/test/repo/releases/download/v2.5.0/App04_DataRefinery_Setup_v2.5.0.exe",
                    "size": 102400,
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": "https://github.com/test/repo/releases/download/v2.5.0/SHA256SUMS.txt",
                    "size": 64,
                }
            ]
        }).encode("utf-8")

        class MockResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return fake_payload

        rel = fetch_latest_release(opener=lambda req, timeout=2: MockResponse())
        self.assertIsNotNone(rel)
        self.assertEqual(rel.version, "2.5.0")
        self.assertEqual(rel.asset_name, "App04_DataRefinery_Setup_v2.5.0.exe")
        self.assertEqual(rel.download_url, "https://github.com/test/repo/releases/download/v2.5.0/App04_DataRefinery_Setup_v2.5.0.exe")
        self.assertEqual(rel.size, 102400)

    def test_download_release_asset_success_and_cancel(self):
        from src.update_checker import download_release_asset

        fake_data = b"Hello, installer bytes!"

        class MockDownloadResponse:
            def __init__(self):
                self.headers = {"Content-Length": str(len(fake_data))}
                self._stream = io.BytesIO(fake_data)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, size):
                return self._stream.read(size)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "installer.exe"
            progress_calls = []

            saved = download_release_asset(
                "https://example.test/download.exe",
                target,
                progress_callback=lambda cur, tot: progress_calls.append((cur, tot)),
                opener=lambda req, timeout=15: MockDownloadResponse(),
                chunk_size=4,
            )
            self.assertEqual(saved, target)
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), fake_data)
            self.assertFalse(target.with_suffix(".exe.part").exists())
            self.assertTrue(len(progress_calls) > 0)
            self.assertEqual(progress_calls[-1], (len(fake_data), len(fake_data)))

            # Test cancellation
            target_cancel = Path(tmpdir) / "installer_cancel.exe"
            with self.assertRaises(InterruptedError):
                download_release_asset(
                    "https://example.test/download.exe",
                    target_cancel,
                    cancel_check=lambda: True,
                    opener=lambda req, timeout=15: MockDownloadResponse(),
                )
            self.assertFalse(target_cancel.exists())
            self.assertFalse(target_cancel.with_suffix(".exe.part").exists())

    def test_download_release_asset_incomplete_socket_error(self):
        from src.update_checker import download_release_asset

        fake_data = b"Truncated data"

        class MockIncompleteResponse:
            def __init__(self):
                self.headers = {"Content-Length": "1000"}
                self._stream = io.BytesIO(fake_data)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return self._stream.read(size)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "installer_incomplete.exe"
            with self.assertRaises(OSError) as ctx:
                download_release_asset(
                    "https://example.test/download.exe",
                    target,
                    opener=lambda req, timeout=15: MockIncompleteResponse(),
                )
            self.assertIn("Incomplete download", str(ctx.exception))
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".exe.part").exists())


if __name__ == "__main__":
    unittest.main()
