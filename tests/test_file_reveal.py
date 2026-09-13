"""Unit tests for the OS file-manager helpers.

Every OS call is patched, so running the suite never opens a window.
"""

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src import file_reveal


def _completed(returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode)


class FileRevealTestCase(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        # A space in the name: the path must survive as a single argument.
        self.file_path = self.folder / "집계 결과.xlsx"
        self.file_path.write_text("x", encoding="utf-8")
        self.resolved_file = str(self.file_path.resolve())
        self.resolved_folder = str(self.folder.resolve())
        self.missing = str(self.folder / "없는파일.xlsx")


class TestGuards(FileRevealTestCase):
    def test_missing_path_returns_false_without_touching_the_os(self):
        with patch("subprocess.run") as run, patch("os.startfile", create=True) as startfile:
            self.assertFalse(file_reveal.open_containing_folder(self.missing))
            self.assertFalse(file_reveal.open_file(self.missing))

            run.assert_not_called()
            startfile.assert_not_called()

    def test_empty_path_returns_false(self):
        with patch("subprocess.run") as run, patch("os.startfile", create=True) as startfile:
            self.assertFalse(file_reveal.open_containing_folder(""))
            self.assertFalse(file_reveal.open_file(""))

            run.assert_not_called()
            startfile.assert_not_called()


class TestWindows(FileRevealTestCase):
    def setUp(self):
        super().setUp()
        platform = patch.object(file_reveal.sys, "platform", "win32")
        platform.start()
        self.addCleanup(platform.stop)

    def test_folder_command_selects_the_file(self):
        with patch("subprocess.run") as run:
            run.return_value = _completed()
            self.assertTrue(file_reveal.open_containing_folder(str(self.file_path)))

        command = run.call_args.args[0]
        self.assertIsInstance(command, list)  # never a shell string
        self.assertEqual(command, ["explorer", f"/select,{self.resolved_file}"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_explorer_nonzero_exit_code_is_still_success(self):
        with patch("subprocess.run") as run:
            run.return_value = _completed(returncode=1)
            self.assertTrue(file_reveal.open_containing_folder(str(self.file_path)))

    def test_failing_to_start_explorer_returns_false(self):
        with patch("subprocess.run", side_effect=OSError("no explorer")):
            self.assertFalse(file_reveal.open_containing_folder(str(self.file_path)))

    def test_open_file_uses_startfile(self):
        with patch("os.startfile", create=True) as startfile:
            self.assertTrue(file_reveal.open_file(str(self.file_path)))

        startfile.assert_called_once_with(self.resolved_file)

    def test_startfile_error_is_swallowed(self):
        with patch("os.startfile", create=True, side_effect=OSError("no handler")):
            self.assertFalse(file_reveal.open_file(str(self.file_path)))

    def test_a_folder_argument_opens_the_folder_itself(self):
        with patch("os.startfile", create=True) as startfile, patch("subprocess.run") as run:
            self.assertTrue(file_reveal.open_containing_folder(str(self.folder)))

            startfile.assert_called_once_with(self.resolved_folder)
            run.assert_not_called()


class TestMacOs(FileRevealTestCase):
    def setUp(self):
        super().setUp()
        platform = patch.object(file_reveal.sys, "platform", "darwin")
        platform.start()
        self.addCleanup(platform.stop)

    def test_reveal_and_open_commands(self):
        with patch("subprocess.run") as run:
            run.return_value = _completed()
            self.assertTrue(file_reveal.open_containing_folder(str(self.file_path)))
            self.assertTrue(file_reveal.open_file(str(self.file_path)))

        self.assertEqual(run.call_args_list[0].args[0], ["open", "-R", self.resolved_file])
        self.assertEqual(run.call_args_list[1].args[0], ["open", self.resolved_file])


class TestLinux(FileRevealTestCase):
    def setUp(self):
        super().setUp()
        platform = patch.object(file_reveal.sys, "platform", "linux")
        platform.start()
        self.addCleanup(platform.stop)

    def test_reveal_falls_back_to_the_parent_folder(self):
        with patch("subprocess.run") as run:
            run.return_value = _completed()
            self.assertTrue(file_reveal.open_containing_folder(str(self.file_path)))

        self.assertEqual(run.call_args.args[0], ["xdg-open", self.resolved_folder])

    def test_open_file_command(self):
        with patch("subprocess.run") as run:
            run.return_value = _completed()
            self.assertTrue(file_reveal.open_file(str(self.file_path)))

        self.assertEqual(run.call_args.args[0], ["xdg-open", self.resolved_file])

    def test_nonzero_exit_code_is_a_failure_where_it_is_meaningful(self):
        # Unlike explorer.exe, xdg-open reports real failures through its code.
        with patch("subprocess.run") as run:
            run.return_value = _completed(returncode=3)
            self.assertFalse(file_reveal.open_file(str(self.file_path)))

    def test_missing_xdg_open_returns_false(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("xdg-open")):
            self.assertFalse(file_reveal.open_file(str(self.file_path)))


if __name__ == "__main__":
    unittest.main()
