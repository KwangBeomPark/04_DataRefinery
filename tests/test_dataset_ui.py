"""Tests for DatasetPublisherTabFrame UI and DataRefinery integration."""

import tempfile
import tkinter as tk
from pathlib import Path
import unittest
import time
import threading
from unittest import mock

from src.background_jobs import BackgroundJobRunner

from src.dataset_config import DatasetDefinition, DatasetRegistry
from src.dataset_ui import DatasetPublisherTabFrame


class MockApp:
    def __init__(self, root):
        self.root = root
        self.job_runner = None


class TestDatasetUI(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = Path(self.temp_dir.name) / "storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.registry = DatasetRegistry(storage_dir=self.storage_dir)

        # Create sample datasets
        self.ds1 = DatasetDefinition.create_new(
            name="Trade_Profit",
            input_folder=str(Path(self.temp_dir.name) / "in1"),
            publish_folder=str(Path(self.temp_dir.name) / "pub1"),
            period_column="기준년월",
            numeric_columns=["매출액", "영업이익"],
        )
        self.ds2 = DatasetDefinition.create_new(
            name="TV_Detail",
            input_folder=str(Path(self.temp_dir.name) / "in2"),
            publish_folder=str(Path(self.temp_dir.name) / "pub2"),
            period_column="매출월",
            numeric_columns=["수량", "금액"],
        )
        self.registry.save_dataset(self.ds1)
        self.registry.save_dataset(self.ds2)

        self.mock_app = MockApp(self.root)
        with mock.patch("src.dataset_ui.DatasetRegistry", return_value=self.registry):
            self.frame = DatasetPublisherTabFrame(self.root, self.mock_app)
        # Inject test registry
        self.frame.registry = self.registry
        self.frame._refresh_dataset_list()
        self.root.update_idletasks()

    def tearDown(self):
        self.frame.destroy()
        self.root.destroy()
        self.temp_dir.cleanup()

    def test_ui_initialization_and_dataset_selection(self):
        """Verify datasets appear in treeview and selection updates labels."""
        tree = self.frame.tree_ds
        items = tree.get_children()
        self.assertEqual(len(items), 2)

        # Select first dataset
        self.frame._select_dataset(self.ds1)
        self.assertIn("Trade_Profit", self.frame.lbl_ds_title.cget("text"))
        self.assertEqual(self.frame.lbl_in_path.cget("text"), self.ds1.input_folder)
        self.assertEqual(self.frame.lbl_pub_path.cget("text"), self.ds1.publish_folder)

        # Since unapproved, publish button must be disabled
        self.assertEqual(str(self.frame.btn_publish["state"]), "disabled")

    def test_approval_enables_publish_button(self):
        """When dataset is approved, publish button must be active; when unapproved, disabled."""
        self.ds1.inspection_approved = True
        self.frame._select_dataset(self.ds1)

        self.assertEqual(str(self.frame.btn_publish["state"]), "normal")
        self.assertIn("배포 가능", self.frame.lbl_approval_badge.cget("text"))

        # Invalidate approval
        self.ds1.inspection_approved = False
        self.frame._select_dataset(self.ds1)
        self.assertEqual(str(self.frame.btn_publish["state"]), "disabled")

    def test_busy_state_locks_interactive_controls(self):
        """Verify _set_busy(True) locks dataset list and action buttons, and False restores them."""
        self.frame._select_dataset(self.ds1)
        self.assertEqual(str(self.frame.btn_add_ds["state"]), "normal")
        self.assertEqual(str(self.frame.btn_edit_ds["state"]), "normal")
        self.assertEqual(str(self.frame.btn_del_ds["state"]), "normal")

        # Set busy
        self.frame._set_busy(True)
        self.assertEqual(str(self.frame.btn_add_ds["state"]), "disabled")
        self.assertEqual(str(self.frame.btn_edit_ds["state"]), "disabled")
        self.assertEqual(str(self.frame.btn_del_ds["state"]), "disabled")
        self.assertEqual(str(self.frame.btn_accumulate["state"]), "disabled")
        self.assertEqual(str(self.frame.btn_scan["state"]), "disabled")

        # Release busy
        self.frame._set_busy(False)
        self.assertEqual(str(self.frame.btn_add_ds["state"]), "normal")
        self.assertEqual(str(self.frame.btn_edit_ds["state"]), "normal")
        self.assertEqual(str(self.frame.btn_del_ds["state"]), "normal")
        self.assertEqual(str(self.frame.btn_accumulate["state"]), "normal")
        self.assertEqual(str(self.frame.btn_scan["state"]), "normal")

    def wait_for_job(self, name):
        deadline = time.monotonic() + 5
        while self.mock_app.job_runner.is_running(name) and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.mock_app.job_runner.is_running(name))

    def test_scan_uses_real_application_runner(self):
        self.mock_app.job_runner = BackgroundJobRunner(self.root.after)
        self.frame._select_dataset(self.ds1)
        self.frame._on_scan_files()
        self.wait_for_job("dataset_scan")
        self.assertIsNotNone(self.frame.last_scan)
        self.assertIn("스캔 완료", self.frame.lbl_status.cget("text"))

    def test_template_generation_runs_off_ui_thread(self):
        self.mock_app.job_runner = BackgroundJobRunner(self.root.after)
        self.frame._select_dataset(self.ds1)
        worker_threads = []
        def generate(*args):
            worker_threads.append(threading.get_ident())
            return True, "생성 완료"
        with mock.patch("src.dataset_ui.filedialog.asksaveasfilename", return_value=str(Path(self.temp_dir.name) / "analysis.xlsx")), \
             mock.patch("src.dataset_ui.messagebox.askyesno", return_value=True), \
             mock.patch("src.dataset_ui.messagebox.showinfo"), \
             mock.patch("src.dataset_ui.create_excel_template_workbook", side_effect=generate):
            self.frame._on_create_template_click()
            self.wait_for_job("dataset_template")
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], threading.get_ident())


if __name__ == "__main__":
    unittest.main()
