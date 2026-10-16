"""Exercise Windows runner path differences and native check failure propagation."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows quality gate")
class TestWindowsQualityGate(unittest.TestCase):
    def test_push_hook_does_not_inherit_foreign_powershell_modules(self):
        with tempfile.TemporaryDirectory(prefix="DataRefinery-hook-environment-") as folder:
            fake_python = Path(folder) / "python.cmd"
            trace = Path(folder) / "module-path.txt"
            fake_python.write_text(
                '@echo off\necho %PSModulePath%>"%CHECK_TRACE%"\nexit /b 23\n', encoding="ascii",
            )
            environment = os.environ.copy()
            environment["PATH"] = folder + os.pathsep + environment["PATH"]
            environment["CHECK_TRACE"] = str(trace)
            poisoned_path = "C:\\deliberately-foreign-ci-modules"
            environment["PSModulePath"] = poisoned_path
            result = subprocess.run(
                ["git", "-c", "core.hooksPath=.githooks", "hook", "run", "pre-push"],
                cwd=ROOT, env=environment, capture_output=True, text=True, errors="replace", timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Checks require Python 3.13 x64", result.stdout + result.stderr)
            module_path = trace.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(poisoned_path.casefold(), module_path.casefold())
            self.assertIn("windowspowershell", module_path.casefold())

    def test_aggregation_safety_with_checkout_and_temp_on_different_drives(self):
        # SUBST creates a disposable drive without touching existing mounts.
        # The child runs only this focused suite, so discovery cannot recurse here.
        subst = shutil.which("subst.exe")
        self.assertIsNotNone(subst, "Windows SUBST is required for cross-drive coverage")
        with tempfile.TemporaryDirectory(prefix="DataRefinery-cross-drive-") as folder:
            drive = None
            for letter in "ZYXWVUTSRQPONMLKJIH":
                candidate = f"{letter}:"
                if candidate.casefold() == Path(tempfile.gettempdir()).drive.casefold():
                    continue
                mapped = subprocess.run([subst, candidate, folder], capture_output=True, timeout=15)
                if mapped.returncode == 0:
                    drive = candidate
                    break
            self.assertIsNotNone(drive, "No free drive for the cross-drive regression check")
            try:
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(ROOT)
                probe = subprocess.run(
                    [sys.executable, "-c", "import os,tempfile; print(os.path.splitdrive(os.getcwd())[0]); print(os.path.splitdrive(tempfile.gettempdir())[0])"],
                    cwd=drive + "\\", env=environment, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(probe.returncode, 0, probe.stderr)
                checkout_drive, temporary_drive = probe.stdout.strip().splitlines()
                self.assertEqual(checkout_drive.casefold(), drive.casefold())
                self.assertNotEqual(checkout_drive.casefold(), temporary_drive.casefold())
                result = subprocess.run(
                    [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
                     "-p", "test_aggregation_safety.py", "-q"],
                    cwd=drive + "\\", env=environment, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=90,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                unmapped = subprocess.run([subst, drive, "/D"], capture_output=True, timeout=15)
                self.assertEqual(unmapped.returncode, 0, "Could not remove the test drive mapping")

    def test_quality_gate_stops_at_each_native_failure(self):
        shell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        with tempfile.TemporaryDirectory(prefix="DataRefinery-check-fault-") as folder:
            fake_python = Path(folder) / "python.cmd"
            trace = Path(folder) / "calls.txt"
            for phase, expected_error in (
                ("version", "Checks require Python 3.13 x64"),
                ("pip", "Dependency consistency check failed"),
                ("ruff", "Python lint failed"),
                ("unittest", "Application tests failed"),
            ):
                with self.subTest(phase=phase):
                    trace.write_text("", encoding="ascii")
                    fake_python.write_text(
                        '@echo off\necho call>>"%CHECK_TRACE%"\n'
                        'if "%1"=="-c" goto version\n'
                        f'if "%2"=="{phase}" exit /b 23\nexit /b 0\n'
                        f':version\nexit /b {23 if phase == "version" else 0}\n', encoding="ascii",
                    )
                    environment = os.environ.copy()
                    environment.pop("PSModulePath", None)
                    environment["CHECK_TRACE"] = str(trace)
                    result = subprocess.run(
                        [str(shell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                         str(ROOT / "scripts/check.ps1"), "-Python", str(fake_python)],
                        env=environment, capture_output=True, text=True, errors="replace", timeout=30,
                    )
                    self.assertNotEqual(result.returncode, 0, "A failed check was reported as success")
                    self.assertIn(expected_error, result.stdout + result.stderr)
                    self.assertNotIn("Quality checks passed", result.stdout)
                    calls = trace.read_text(encoding="ascii").splitlines()
                    self.assertEqual(len(calls), ("version", "pip", "ruff", "unittest").index(phase) + 1)


if __name__ == "__main__":
    unittest.main()
