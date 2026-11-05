"""Guided mart flow tests use isolated synthetic data."""

import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from src.qt.app import create_or_get_app
from src.qt.widgets.mart_workspace import MartWorkspace
from src.mart import Dimension, MartModel, Measure
from src.mart.capacity import ResourcePreflight
from tests.test_mart_resources import free


class ImmediateRunner:
    def start(self, name, worker, callbacks):
        try:
            callbacks.on_success(worker(lambda *args: callbacks.on_progress(*args)))
        except Exception as exc:
            callbacks.on_error(exc)
        callbacks.on_finished()
        return True


class MartWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_or_get_app()

    def test_workspace_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            widget = MartWorkspace("en", ImmediateRunner(), root=root / "workspaces")
            widget.name.setText("../outside")
            widget.create_workspace()
            self.assertFalse((root / "outside").exists())
            widget.name.setText("sales")
            widget.create_workspace()
            source = root / "input.csv"

            def write(period, amount):
                with source.open("w", encoding="utf-8", newline="") as out:
                    w = csv.writer(out)
                    w.writerow(["period", "id", "customer", "name", "amount"])
                    w.writerow([period, "1", "001", "A", amount])

            write("2026-01", "1.25")
            widget.set_files([source])
            self.assertEqual(widget.source_preview.rowCount(), 1)
            widget.fact.setText("sales")
            widget.period.setCurrentText("period")
            widget.row_keys.set_values(["id"])
            widget.dims = [Dimension("customer", ("customer",), ("name",))]
            widget.metrics = [Measure("amount", "amount", 18, 2, "USD", kind="cost")]
            if os.name == "nt":
                original_replace = os.replace
                error = PermissionError(13, "injected")
                error.winerror = 32
                calls = []

                def replace_once(source, destination):
                    calls.append(1)
                    if len(calls) == 1:
                        raise error
                    return original_replace(source, destination)

                with (
                    patch("src.atomic_write.os.replace", side_effect=replace_once),
                    patch("src.atomic_write.time.sleep"),
                ):
                    widget.save_model()
                self.assertEqual(len(calls), 2)
            else:
                widget.save_model()
            self.assertTrue((widget.current / "model.json").exists())

            def guard(**kwargs):
                return ResourcePreflight(
                    reserve_bytes=0, space_probe=lambda _: free(10 * 1024**3), **kwargs
                )

            with patch(
                "src.qt.widgets.mart_workspace.ResourcePreflight", side_effect=guard
            ):
                for period, amount in [
                    ("2026-01", "1.25"),
                    ("2026-02", "2.25"),
                    ("2026-01", "3.25"),
                ]:
                    write(period, amount)
                    widget.scope_inputs["period"].setText(period)
                    widget.complete.setChecked(True)
                    widget.load_snapshot()
                    self.assertEqual(widget.status.text(), widget.t(69))
                    self.assertFalse(widget.complete.isChecked())
                    self.assertFalse(widget.empty.isChecked())
                widget.inspect_database()
                self.assertEqual(widget.home.rowCount(), 2)
                self.assertEqual(
                    widget.structure.horizontalHeaderItem(0).text(), "Table"
                )
                widget.apply_language("ko")
                self.assertEqual(
                    widget.structure.horizontalHeaderItem(0).text(), "테이블"
                )
                widget.apply_language("en")
                self.assertTrue(
                    any(
                        getattr(item, "toPlainText", lambda: "")() == "FK ✓"
                        for item in widget.relation_scene.items()
                    )
                )
                self.assertTrue(
                    any(
                        "FOREIGN KEY" in widget.structure.item(i, 1).text()
                        for i in range(widget.structure.rowCount())
                    )
                )
                self.assertEqual(widget.reconciliation.rowCount(), 4)
                widget.export_name.setText("customer")
                widget.export_cols.set_values(["customer", "amount"])
                widget.group_cols.set_values(["customer"])
                widget.create_export()
                self.assertIsNotNone(widget.candidate)
                widget.reviewer.setText("reviewer")
                widget.approve()
                self.assertTrue(
                    (
                        widget.current / "exports" / widget.candidate / "approval.json"
                    ).exists()
                )
                output = root / "output.csv"
                with patch(
                    "src.qt.widgets.mart_workspace.QFileDialog.getSaveFileName",
                    return_value=(str(output), ""),
                ):
                    widget.publish_local()
                self.assertTrue(output.exists())
                self.assertIn("5.50", output.read_text(encoding="utf-8-sig"))
                widget.site.setText("https://example.invalid/sites/demo")
                widget.tenant.setText("tenant")
                widget.client.setText("client")
                widget.library.setText("Documents")
                widget.folder.setText("folder")
                widget.filename.setText("sales.csv")
                candidate = widget.candidate
                widget.save_profile()
                from src.mart.publication import SharePointProfile

                class FakePublisher:
                    token = "fixture"
                    profile = SharePointProfile(
                        "https://example.invalid/sites/demo",
                        "tenant",
                        "client",
                        "Documents",
                        "folder",
                    )

                    def __init__(self):
                        self.calls = 0

                    def resolve(self):
                        self.calls += 1
                        return {
                            "folder_url": "https://example.invalid/sites/demo/Shared%20Documents/folder"
                        }

                publisher = FakePublisher()
                widget.publisher = publisher
                widget.export_cols.set_values(["customer"])
                widget.copy_excel()
                self.assertIn('{"amount", type number}', self.app.clipboard().text())
                self.assertEqual(publisher.calls, 1)
                approval_path = widget.current / "exports" / candidate / "approval.json"
                held = approval_path.with_suffix(".held")
                approval_path.rename(held)
                widget.copy_excel()
                self.assertEqual(publisher.calls, 1)
                held.rename(approval_path)
                from src.mart import ExportDefinition, MartEngine, WorkspaceStore

                engine = MartEngine(
                    WorkspaceStore(widget.current),
                    preflight=lambda *_: {"allowed": True},
                )
                long_id = engine.create_export(
                    ExportDefinition(
                        "long", ("customer",), format="long", cost_measures=("amount",)
                    )
                )["candidate"]
                engine.approve_export(long_id, "reviewer")
                widget.candidate = long_id
                widget.save_profile()
                widget.copy_excel()
                self.assertIn("wide CSV only", widget.status.text())
                self.assertEqual(publisher.calls, 1)
            widget._select("sales")
            self.assertEqual(widget.model.period_column, "period")
            self.assertEqual(widget.site.text(), "https://example.invalid/sites/demo")
            self.assertEqual(
                widget.destination_settings[candidate]["filename"], "sales.csv"
            )
            self.assertNotIn(
                "token",
                (widget.current / "ui.json").read_text(encoding="utf-8").lower(),
            )
            widget.apply_language("pl")
            self.assertEqual(widget.load_button.text(), widget.t(29))
            widget.apply_language("en")
            self.assertEqual(widget.load_button.text(), "Load snapshot")
            self.assertEqual(widget.sample.rowCount(), 2)
            widget.next_page()
            self.assertEqual(widget.offset, 100)
            self.assertEqual(widget.sample.rowCount(), 0)
            widget.prev_page()
            self.assertEqual(widget.offset, 0)
            self.assertEqual(widget.sample.rowCount(), 2)

    def test_bounded_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.csv"
            path.write_text(
                "code,value\n" + "".join(f"{i},{i}\n" for i in range(20)),
                encoding="utf-8",
            )
            widget = MartWorkspace("en", ImmediateRunner(), root=Path(temp) / "spaces")
            widget.set_files([path])
            self.assertEqual(widget.source_preview.rowCount(), 10)

    def test_bad_settings_are_preserved_and_do_not_block_load(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            widget = MartWorkspace("en", ImmediateRunner(), root=root / "spaces")
            widget.name.setText("sales")
            widget.create_workspace()
            source = root / "input.csv"
            source.write_text(
                "period,id,customer,name,amount\n2026-01,1,001,A,1.00\n",
                encoding="utf-8",
            )
            widget.set_files([source])
            widget.fact.setText("sales")
            widget.period.setCurrentText("period")
            widget.row_keys.set_values(["id"])
            widget.dims = [Dimension("customer", ("customer",), ("name",))]
            widget.metrics = [Measure("amount", "amount", 18, 2, "USD")]
            widget.save_model()

            def guard(**kwargs):
                return ResourcePreflight(
                    reserve_bytes=0, space_probe=lambda _: free(10 * 1024**3), **kwargs
                )

            cases = [
                b"[]",
                b'{"budget_gib":[1]}',
                b'{"budget_gib":NaN}',
                b'{"budget_gib":true}',
                b'{"sharepoint":[]}',
                b'{"sharepoint":{"site":[]}}',
                b'{"destinations":[]}',
                b'{"destinations":{"x":[]}}',
                b'{"destinations":{"x":{"sharepoint":[]}}}',
            ]
            with patch(
                "src.qt.widgets.mart_workspace.ResourcePreflight", side_effect=guard
            ):
                for index, invalid in enumerate(cases):
                    with self.subTest(settings=index):
                        path = widget.current / "ui.json"
                        path.write_bytes(invalid)
                        widget._select("sales")
                        self.assertTrue(widget.settings_warning.text())
                        self.assertEqual(widget.destination_settings, {})
                        widget.set_files([source])
                        widget.scope_inputs["period"].setText("2026-01")
                        source.write_text(
                            f"period,id,customer,name,amount\n2026-01,1,001,A,{index + 1}.00\n",
                            encoding="utf-8",
                        )
                        widget.budget_value = [1]
                        widget.complete.setChecked(True)
                        widget.load_snapshot()
                        self.assertIn(str(index + 1), widget.load_result.text())
                        self.assertTrue(widget.settings_warning.text())
                        self.assertEqual(path.read_bytes(), invalid)
                        with self.assertRaises(ValueError):
                            widget._save_ui(budget_gib=1)

    def test_empty_declaration_is_cleared_and_cannot_delete_existing_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            widget = MartWorkspace("en", ImmediateRunner(), root=root / "spaces")
            widget.name.setText("sales")
            widget.create_workspace()
            source = root / "source.csv"
            source.write_text(
                "period,id,customer,name,amount\n2026-01,1,001,A,1.25\n",
                encoding="utf-8",
            )
            widget.set_files([source])
            widget.fact.setText("sales")
            widget.period.setCurrentText("period")
            widget.row_keys.set_values(["id"])
            widget.dims = [Dimension("customer", ("customer",), ("name",))]
            widget.metrics = [Measure("amount", "amount", 18, 2, "USD")]
            widget.save_model()

            def guard(**kwargs):
                return ResourcePreflight(
                    reserve_bytes=0, space_probe=lambda _: free(10 * 1024**3), **kwargs
                )

            with patch(
                "src.qt.widgets.mart_workspace.ResourcePreflight", side_effect=guard
            ):
                widget.scope_inputs["period"].setText("2026-01")
                widget.complete.setChecked(True)
                widget.load_snapshot()
                source.write_text("period,id,customer,name,amount\n", encoding="utf-8")
                widget.set_files([source])
                widget.scope_inputs["period"].setText("2026-02")
                widget.complete.setChecked(True)
                widget.empty.setChecked(True)
                widget.budget_value = 0.000000001
                widget.warning = False
                settings = widget.current / "ui.json"
                settings.write_bytes(b"{bad-json")
                widget.load_snapshot()
                self.assertIn("2", widget.load_result.text())
                self.assertIn("could not be saved", widget.settings_warning.text())
                self.assertEqual(settings.read_bytes(), b"{bad-json")
                settings.write_bytes(b"[]")
                with self.assertRaises(ValueError):
                    widget._save_ui(budget_gib=1)
                self.assertEqual(settings.read_bytes(), b"[]")
                self.assertFalse(widget.empty.isChecked())
                self.assertFalse(widget.complete.isChecked())
                widget.scope_inputs["period"].setText("2026-01")
                widget.complete.setChecked(True)
                widget.load_snapshot()
                self.assertIn("Empty snapshot", widget.status.text())
                self.assertFalse(widget.complete.isChecked())
                self.assertFalse(widget.empty.isChecked())
                from src.mart import MartEngine, WorkspaceStore

                engine = MartEngine(WorkspaceStore(widget.current))
                self.assertEqual(engine.catalog()["revision"], 2)
                self.assertEqual(engine.sample()["rows"][0]["amount"], "1.25")
                widget.complete.setChecked(True)
                widget.empty.setChecked(True)
                widget.scope_inputs["period"].setText("2026-03")
                self.assertFalse(widget.complete.isChecked())
                self.assertFalse(widget.empty.isChecked())
            for language in ("ko", "en", "pl"):
                widget.apply_language(language)
                self.assertEqual(widget.next_button.text(), widget.t(84))
                self.assertEqual(widget.previous_button.text(), widget.t(85))
                widget.candidate = None
                widget.approve()
                self.assertEqual(widget.status.text(), widget.t(83))


    def test_save_model_failure_does_not_mutate_memory_model(self):
        """Regression test for Critical: Failed model save must not corrupt in-memory self.model."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            widget = MartWorkspace("en", ImmediateRunner(), root=root / "spaces")
            widget.name.setText("sales_locked")
            widget.create_workspace()

            source = root / "input.csv"
            source.write_text("period,id,cust,amt\n2026-01,1,A,100\n", encoding="utf-8")
            widget.set_files([source])
            widget.fact.setText("fact_sales")
            widget.period.setCurrentText("period")
            widget.row_keys.set_values(["id"])
            widget.dims = [Dimension("Customer", ("cust",), ())]
            widget.metrics = [Measure("amt", "amt", kind="value")]
            widget.save_model()

            initial_model = widget.model
            self.assertIsNotNone(initial_model)

            # Create dummy mart.duckdb to simulate existing database
            (widget.current / "mart.duckdb").write_text("dummy", encoding="utf-8")

            # Attempt to save a modified model which should fail
            widget.dims = [Dimension("NewDim", ("cust",), ())]
            widget.save_model()

            # Save should fail and self.model must remain initial_model
            self.assertIn("An existing database requires its original model", widget.status.text())
            self.assertEqual(widget.model, initial_model)


    def test_open_model_wizard_success_on_new_workspace(self):
        """Verify open_model_wizard successfully configures a brand-new workspace without AttributeError."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            widget = MartWorkspace("en", ImmediateRunner(), root=root / "spaces")
            widget.name.setText("brand_new_space")
            widget.create_workspace()
            self.assertIsNone(widget.model)

            source = root / "input.csv"
            source.write_text("period,id,cust,amt\n2026-01,1,A,100\n", encoding="utf-8")
            widget.set_files([source])

            mock_model = MartModel(
                name="brand_new_space",
                fact_table="fact_sales",
                dimensions=(Dimension("Customer", ("cust",), ()),),
                measures=(Measure("amt", "amt", kind="value"),),
                period_column="period",
                row_key_columns=("id",),
                version="1",
            )

            from unittest.mock import patch
            from PySide6.QtWidgets import QDialog
            with patch("src.qt.widgets.mart_model_wizard.MartModelWizardDialog.exec", return_value=QDialog.DialogCode.Accepted), \
                 patch("src.qt.widgets.mart_model_wizard.MartModelWizardDialog.get_model", return_value=mock_model):
                widget.open_model_wizard()

            self.assertIsNotNone(widget.model)
            self.assertEqual(widget.model.period_column, "period")
            self.assertTrue((widget.current / "model.json").exists())
            self.assertIn("period", widget.scope_inputs)

    def test_open_model_wizard_rollback_on_locked_database_error(self):
        """Verify open_model_wizard rolls back UI and preserves original self.model if locked DB forbids change."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            widget = MartWorkspace("en", ImmediateRunner(), root=root / "spaces")
            widget.name.setText("locked_wizard_space")
            widget.create_workspace()

            source = root / "input.csv"
            source.write_text("period,id,cust,amt\n2026-01,1,A,100\n", encoding="utf-8")
            widget.set_files([source])
            widget.fact.setText("fact_sales")
            widget.period.setCurrentText("period")
            widget.row_keys.set_values(["id"])
            widget.dims = [Dimension("Customer", ("cust",), ())]
            widget.metrics = [Measure("amt", "amt", kind="value")]
            widget.save_model()

            initial_model = widget.model
            # Create dummy mart.duckdb to simulate locked DB
            (widget.current / "mart.duckdb").write_text("locked", encoding="utf-8")

            conflicting_model = MartModel(
                name="locked_wizard_space",
                fact_table="fact_sales",
                dimensions=(Dimension("ConflictingDim", ("cust",), ()),),
                measures=(Measure("amt", "amt", kind="value"),),
                period_column="period",
                row_key_columns=("id",),
                version="1",
            )

            from unittest.mock import patch
            from PySide6.QtWidgets import QDialog
            with patch("src.qt.widgets.mart_model_wizard.MartModelWizardDialog.exec", return_value=QDialog.DialogCode.Accepted), \
                 patch("src.qt.widgets.mart_model_wizard.MartModelWizardDialog.get_model", return_value=conflicting_model):
                widget.open_model_wizard()

            # Error status and self.model preserved
            self.assertIn("An existing database requires its original model", widget.status.text())
            self.assertEqual(widget.model, initial_model)
            # UI forms must be rolled back to initial_model dimensions
            self.assertEqual([d.name for d in widget.dims], ["Customer"])

    def test_workspace_tabs_and_deployment_subtabs(self):
        """Verify the 4-step main workflow tabs and deploy sub-tabs structure."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            widget = MartWorkspace("ko", ImmediateRunner(), root=root / "spaces")

            # 1. Main tabs count and titles
            self.assertEqual(widget.tabs.count(), 4)
            self.assertIn("1. 📋", widget.tabs.tabText(0))
            self.assertIn("2. ⚡", widget.tabs.tabText(1))
            self.assertIn("3. 🔍", widget.tabs.tabText(2))
            self.assertIn("4. 📤", widget.tabs.tabText(3))

            # 2. Deploy sub-tabs count and titles
            self.assertTrue(hasattr(widget, "deploy_tabs"))
            self.assertEqual(widget.deploy_tabs.count(), 3)
            self.assertEqual(widget.deploy_tabs.tabText(0), "목적 CSV")
            self.assertEqual(widget.deploy_tabs.tabText(1), "SharePoint")
            self.assertEqual(widget.deploy_tabs.tabText(2), "저장소 용량 모니터링")

            # 3. Widgets integrity
            self.assertIsNotNone(widget.candidates)
            self.assertIsNotNone(widget.budget)
            self.assertIsNotNone(widget.site)
            self.assertIsNotNone(widget.export_name)


if __name__ == "__main__":
    unittest.main()
