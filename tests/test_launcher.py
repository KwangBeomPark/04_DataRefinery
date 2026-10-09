import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.data_refinery_launcher import (
    APPLICATION_NAME_PATTERN,
    INSTALLER_NAME_PATTERN,
    InstallerAsset,
    LauncherError,
    TRUSTED_SIGNER_THUMBPRINT,
    _version_from_name,
    download_installer,
    fetch_latest_installer,
    find_installed_executable,
    verify_installer_signature,
)


class _Response:
    def __init__(self, payload):
        self._stream = io.BytesIO(payload)

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class TestLauncher(unittest.TestCase):
    def test_finds_the_latest_valid_installed_application(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory) / "Programs" / "Data Refinery"
            install_dir.mkdir(parents=True)
            (install_dir / "App04_DataRefinery_v1.5.0.exe").touch()
            latest = install_dir / "App04_DataRefinery_v1.6.0.exe"
            latest.touch()
            (install_dir / "DataRefinery.exe").touch()

            found = find_installed_executable(Path(directory), include_registry=False)

        self.assertEqual(found, latest)

    def test_release_lookup_accepts_only_the_official_setup_asset(self):
        asset_name = "App04_DataRefinery_Setup_v1.6.0.exe"
        payload = {
            "tag_name": "v1.6.0",
            "assets": [
                {
                    "name": asset_name,
                    "browser_download_url": "https://github.com/KwangBeomPark/04_DataRefinery/releases/download/v1.6.0/" + asset_name,
                    "size": 123,
                },
                {
                    "name": "source.zip",
                    "browser_download_url": "https://github.com/KwangBeomPark/04_DataRefinery/archive/v1.6.0.zip",
                    "size": 456,
                },
            ]
        }

        installer = fetch_latest_installer(lambda request, timeout: _Response(json.dumps(payload).encode("utf-8")))

        self.assertEqual(installer.name, asset_name)
        self.assertEqual(installer.size, 123)

    def test_release_lookup_accepts_previous_hyphenated_name(self):
        name = "App04_DataRefinery-Setup_v2.0.1.exe"
        payload = {"tag_name": "v2.0.1", "assets": [{
            "name": name, "size": 123,
            "browser_download_url": "https://github.com/KwangBeomPark/04_DataRefinery/releases/download/v2.0.1/" + name,
        }]}
        asset = fetch_latest_installer(lambda request, timeout: _Response(json.dumps(payload).encode("utf-8")))
        self.assertEqual(asset.name, name)

    def test_release_lookup_rejects_an_untrusted_download_location(self):
        payload = {
            "tag_name": "v1.6.0",
            "assets": [
                {
                    "name": "App04_DataRefinery_Setup_v1.6.0.exe",
                    "browser_download_url": "https://example.invalid/setup.exe",
                    "size": 123,
                }
            ]
        }

        with self.assertRaises(LauncherError):
            fetch_latest_installer(lambda request, timeout: _Response(json.dumps(payload).encode("utf-8")))

    def test_release_lookup_rejects_a_mismatched_tag(self):
        name = "App04_DataRefinery_Setup_v1.6.0.exe"
        payload = {
            "tag_name": "v1.6.1",
            "assets": [{
                "name": name,
                "browser_download_url": f"https://github.com/KwangBeomPark/04_DataRefinery/releases/download/v1.6.1/{name}",
                "size": 123,
            }],
        }
        with self.assertRaises(LauncherError):
            fetch_latest_installer(lambda request, timeout: _Response(json.dumps(payload).encode("utf-8")))

    def test_download_checks_the_expected_file_size(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "setup.exe"
            asset = InstallerAsset(
                name="App04_DataRefinery_Setup_v1.6.0.exe",
                url="https://github.com/KwangBeomPark/04_DataRefinery/releases/download/v1.6.0/App04_DataRefinery_Setup_v1.6.0.exe",
                size=3,
            )
            downloaded = download_installer(asset, destination, lambda request, timeout: _Response(b"abc"))

            self.assertEqual(downloaded.read_bytes(), b"abc")

    def test_filename_patterns_require_a_three_part_version(self):
        self.assertEqual(
            _version_from_name(Path("App04_DataRefinery_v1.6.0.exe"), APPLICATION_NAME_PATTERN),
            (1, 6, 0),
        )
        self.assertIsNone(_version_from_name(Path("App04_DataRefinery_v1.6.exe"), APPLICATION_NAME_PATTERN))
        self.assertTrue(INSTALLER_NAME_PATTERN.fullmatch("App04_DataRefinery_Setup_v1.6.0.exe"))
        self.assertTrue(INSTALLER_NAME_PATTERN.fullmatch("App04_DataRefinery-Setup_v1.6.0.exe"))
        self.assertFalse(INSTALLER_NAME_PATTERN.fullmatch("DataRefinery-Setup.v1.6.0.exe"))

    def test_signature_check_requires_valid_pinned_signer(self):
        installer = Path("C:/Temp/setup.exe")
        trusted = json.dumps({"status": "Valid", "thumbprint": TRUSTED_SIGNER_THUMBPRINT})
        with patch("src.data_refinery_launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0, trusted)):
            verify_installer_signature(installer)

        untrusted = json.dumps({"status": "Valid", "thumbprint": "0" * 40})
        with patch("src.data_refinery_launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0, untrusted)):
            with self.assertRaises(LauncherError):
                verify_installer_signature(installer)

        with patch("src.data_refinery_launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{"status":"NotSigned"}')):
            with self.assertRaises(LauncherError):
                verify_installer_signature(installer)


if __name__ == "__main__":
    unittest.main()
