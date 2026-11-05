"""Unit and regression tests for MartModelWizardDialog and mart workspace integration."""

import os
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.mart.auto_guess import (
    ROLE_DIM_ATTR,
    ROLE_DIM_KEY,
    ROLE_EXCLUDED,
    ROLE_MEASURE,
    ROLE_PERIOD,
    assignments_to_model,
)
from src.qt.widgets.mart_model_wizard import (
    DataViewerPopupDialog,
    MartModelWizardDialog,
)

os.environ["QT_QPA_PLATFORM"] = "offscreen"
app = QApplication.instance() or QApplication([])


class TestMartModelWizard(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.temp_dir.name) / "test_pnl.csv"
        csv_content = (
            "기준년월,법인코드,전표번호,거래처코드,거래처명,상품코드,상품명,매출액,매출원가,비고\n"
            "2026-01,PL,SLIP001,CUST01,고객사A,PROD01,제품A,1000000,700000,정상\n"
            "2026-01,PL,SLIP002,CUST02,고객사B,PROD02,제품B,2500000,1800000,정상\n"
            "2026-02,PL,SLIP003,CUST01,고객사A,PROD01,제품A,1200000,800000,할인\n"
        )
        self.csv_path.write_text(csv_content, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wizard_dialog_initialization_and_stepping(self):
        dlg = MartModelWizardDialog(
            parent=None,
            csv_path=self.csv_path,
            initial_model=None,
            language_code="ko",
        )
        self.assertEqual(len(dlg.headers), 10)
        self.assertEqual(len(dlg.sample_rows), 3)

        # Check left table dimensions
        self.assertEqual(dlg.data_table.columnCount(), 10)
        self.assertEqual(dlg.data_table.rowCount(), 3)

        # Step 1
        self.assertEqual(dlg.stack.currentIndex(), 0)

        # Step 2: Dimensions Partitioning
        dlg._on_next_step()
        self.assertEqual(dlg.stack.currentIndex(), 1)

        # Step 3: Fact & Measures Configuration (Edit fact name)
        dlg._on_next_step()
        self.assertEqual(dlg.stack.currentIndex(), 2)
        dlg.txt_fact_name.setText("fact_custom_sales")
        self.assertEqual(dlg.fact_table_name, "fact_custom_sales")

        # Step 4: Review
        dlg._on_next_step()
        self.assertEqual(dlg.stack.currentIndex(), 3)
        self.assertEqual(dlg.fact_table_name, "fact_custom_sales")

        # Exclude remaining unassigned
        for a in dlg.assignments:
            if a.role == "unassigned":
                a.role = ROLE_EXCLUDED

        dlg._on_finish()
        model = dlg.get_model()
        self.assertIsNotNone(model)
        self.assertEqual(model.fact_table, "fact_custom_sales")
        self.assertEqual(model.period_column, "기준년월")
        self.assertTrue(len(model.measures) >= 2)
        self.assertTrue(len(model.dimensions) >= 2)
        dlg.close()

    def test_multi_key_dimension_selection_preserves_keys(self):
        """Regression test for High 2: Selecting dimension must not demote multi-keys."""
        dlg = MartModelWizardDialog(
            parent=None,
            csv_path=self.csv_path,
            language_code="ko",
        )
        # Create a custom dimension with 2 natural keys
        for a in dlg.assignments:
            if a.column_name in ("거래처코드", "전표번호"):
                a.role = ROLE_DIM_KEY
                a.dimension_name = "복합차원"
            elif a.column_name == "거래처명":
                a.role = ROLE_DIM_ATTR
                a.dimension_name = "복합차원"

        dlg._refresh_step3_view()

        # Find and select '복합차원'
        for i in range(dlg.dim_list_widget.count()):
            if dlg.dim_list_widget.item(i).text() == "복합차원":
                dlg.dim_list_widget.setCurrentRow(i)
                break

        # Check keys of '복합차원' are still preserved
        dim_keys = [a.column_name for a in dlg.assignments if a.dimension_name == "복합차원" and a.role == ROLE_DIM_KEY]
        self.assertIn("거래처코드", dim_keys)
        self.assertIn("전표번호", dim_keys)
        dlg.close()

    def test_orphaned_attributes_without_keys_are_not_silently_dropped(self):
        """Regression test for High 5: Dimensions without natural keys must exclude attributes, not drop."""
        from src.mart.auto_guess import ColumnAssignment
        test_assignments = [
            ColumnAssignment(column_name="period", role=ROLE_PERIOD),
            ColumnAssignment(column_name="attr_only", role=ROLE_DIM_ATTR, dimension_name="키없는차원"),
            ColumnAssignment(column_name="amount", role=ROLE_MEASURE),
        ]
        model = assignments_to_model(test_assignments, "test_fact", "test_fact", "period")
        # 'attr_only' should be safely placed in excluded_columns, not disappeared
        self.assertIn("attr_only", model.excluded_columns)

    def test_data_viewer_popup_dialog(self):
        headers = ["Col1", "Col2_Very_Long_Remarks_Column_That_Exceeds_Normal_Width"]
        rows = [["Val1", "A" * 500], ["Val3", "B" * 500]]
        popup = DataViewerPopupDialog(None, headers, rows, language_code="ko")
        self.assertEqual(popup.layout().count(), 3)
        # Verify columns are clamped under 340px max width
        table = popup.findChild(type(popup).mro()[0].__dict__.get("table", None) or type(popup.layout().itemAt(1).widget()))
        self.assertLessEqual(table.columnWidth(1), 340)
        self.assertGreaterEqual(table.columnWidth(0), 140)
        popup.close()

    def test_step1_banner_dynamic_text(self):
        # 1. P&L CSV (contains 매출액, etc.)
        dlg_pnl = MartModelWizardDialog(None, self.csv_path, language_code="ko")
        self.assertIn("데릭 P&L", dlg_pnl.lbl_step1_banner.text())
        dlg_pnl.close()

        # 2. Generic CSV without P&L hints
        generic_csv = Path(self.temp_dir.name) / "generic.csv"
        generic_csv.write_text("dt,id,score,category\n2026-01-01,1,95,A\n", encoding="utf-8")
        dlg_gen = MartModelWizardDialog(None, generic_csv, language_code="ko")
        dlg_gen._apply_generic_auto_guess()
        self.assertIn("원본 데이터 분석 완료", dlg_gen.lbl_step1_banner.text())
        dlg_gen.close()

    def test_dimension_name_collision_and_empty_guard(self):
        dlg = MartModelWizardDialog(None, self.csv_path, language_code="ko")
        # Add a dimension
        dlg._on_add_dimension()
        initial_count = dlg.dim_list_widget.count()
        # Verify next add creates a unique name
        dlg._on_add_dimension()
        self.assertEqual(dlg.dim_list_widget.count(), initial_count + 1)
        name1 = dlg.dim_list_widget.item(initial_count - 1).text()
        name2 = dlg.dim_list_widget.item(initial_count).text()
        self.assertNotEqual(name1, name2)

        # Attempt to rename to an existing name must be blocked
        dlg.dim_list_widget.setCurrentRow(initial_count)
        dlg._on_dim_name_edited(name1)
        self.assertEqual(dlg.dim_list_widget.item(initial_count).text(), name2)

        # Attempt to rename to empty string must be blocked
        dlg._on_dim_name_edited("   ")
        self.assertEqual(dlg.dim_list_widget.item(initial_count).text(), name2)
        dlg.close()

    def test_tab_delimited_sniffing_and_manual_switch(self):
        """Test tab-delimited file sniffing and dynamic single-column warning banner."""
        tab_path = Path(self.temp_dir.name) / "tab_data.tsv"
        tab_content = "ColA\tColB\tColC\n1\t2\t3\n4\t5\t6\n"
        tab_path.write_text(tab_content, encoding="utf-8")

        # 1. Automatic tab detection
        dlg = MartModelWizardDialog(None, tab_path, language_code="ko")
        self.assertEqual(dlg.detected_delimiter, "\t")
        self.assertEqual(len(dlg.headers), 3)
        self.assertTrue(dlg.warn_single_column_banner.isHidden())

        # 2. Switch to comma manually -> single column warning must appear
        idx_comma = dlg.cmb_delimiter.findData(",")
        dlg.cmb_delimiter.setCurrentIndex(idx_comma)
        self.assertEqual(len(dlg.headers), 1)
        self.assertFalse(dlg.warn_single_column_banner.isHidden())

        # 3. Switch back to tab -> normal columns restored, warning hidden
        idx_tab = dlg.cmb_delimiter.findData("\t")
        dlg.cmb_delimiter.setCurrentIndex(idx_tab)
        self.assertEqual(len(dlg.headers), 3)
        self.assertTrue(dlg.warn_single_column_banner.isHidden())
        dlg.close()

    def test_step2_interactive_attr_checkbox_toggle(self):
        """Test Step 2 interactive checkbox assignment for dimension attributes."""
        from PySide6.QtCore import Qt

        dlg = MartModelWizardDialog(None, self.csv_path, language_code="ko")
        dlg.stack.setCurrentIndex(1)  # Step 2: Dimensions

        # Ensure a dimension is selected
        if dlg.dim_list_widget.count() == 0:
            dlg._on_add_dimension()
        dlg.dim_list_widget.setCurrentRow(0)
        curr_dim = dlg.dim_list_widget.item(0).text()

        # Find an attribute item in checklist
        target_item = None
        for i in range(dlg.dim_attrs_checklist.count()):
            it = dlg.dim_attrs_checklist.item(i)
            if it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                target_item = it
                break

        self.assertIsNotNone(target_item)
        target_col = target_item.data(Qt.ItemDataRole.UserRole)

        # Toggle to checked
        target_item.setCheckState(Qt.CheckState.Checked)
        dlg._on_dim_attr_item_changed(target_item)

        asgn = next((a for a in dlg.assignments if a.column_name == target_col), None)
        self.assertIsNotNone(asgn)
        self.assertEqual(asgn.role, ROLE_DIM_ATTR)
        self.assertEqual(asgn.dimension_name, curr_dim)

        # Toggle to unchecked
        target_item.setCheckState(Qt.CheckState.Unchecked)
        dlg._on_dim_attr_item_changed(target_item)
        self.assertEqual(asgn.role, "unassigned")
        self.assertEqual(asgn.dimension_name, "")
        dlg.close()

    def test_step3_add_and_delete_measure(self):
        """Test Step 3 adding and removing measures interactively."""
        dlg = MartModelWizardDialog(None, self.csv_path, language_code="ko")
        dlg.stack.setCurrentIndex(2)  # Step 3: Fact & Measures

        # Pick a non-period column to unassign and test
        target_asgn = next(a for a in dlg.assignments if a.column_name != dlg.period_column_name)
        target_asgn.role = "unassigned"
        target_col = target_asgn.column_name

        # Add measure
        initial_meas_count = dlg.measure_table.rowCount()
        dlg._on_add_measure(target_column=target_col)
        self.assertEqual(dlg.measure_table.rowCount(), initial_meas_count + 1)
        self.assertEqual(target_asgn.role, ROLE_MEASURE)

        # Delete measure
        # Select the measure row corresponding to target_col
        for r in range(dlg.measure_table.rowCount()):
            if dlg.measure_table.item(r, 0).text() == target_col:
                dlg.measure_table.setCurrentCell(r, 0)
                break
        dlg._on_del_measure()
        self.assertEqual(dlg.measure_table.rowCount(), initial_meas_count)
        self.assertEqual(target_asgn.role, "unassigned")
        dlg.close()


    def test_tab_delimited_end_to_end_load(self):
        """End-to-end regression test: Wizard -> Model -> MartEngine DuckDB load on tab TSV."""
        from src.mart.engine import MartEngine
        from src.mart.storage import WorkspaceStore

        tab_path = Path(self.temp_dir.name) / "e2e_pnl.tsv"
        tab_content = (
            "Period\tSlipNo\tCustCode\tCustName\tRevenue\n"
            "2026-01\tSLIP01\tC01\tAcme\t100000\n"
            "2026-01\tSLIP02\tC02\tBeta\t200000\n"
        )
        tab_path.write_text(tab_content, encoding="utf-8")

        dlg = MartModelWizardDialog(None, tab_path, language_code="ko")
        self.assertEqual(dlg.detected_delimiter, "\t")
        dlg._apply_generic_auto_guess()

        # Step 4: Finish and get model
        for a in dlg.assignments:
            if a.column_name == "Period":
                a.role = ROLE_PERIOD
            elif a.column_name == "SlipNo":
                a.role = "row_key"
            elif a.column_name == "CustCode":
                a.role = ROLE_DIM_KEY
                a.dimension_name = "Customer"
            elif a.column_name == "CustName":
                a.role = ROLE_DIM_ATTR
                a.dimension_name = "Customer"
            elif a.column_name == "Revenue":
                a.role = ROLE_MEASURE

        dlg._on_finish()
        model = dlg.get_model()
        self.assertIsNotNone(model)
        self.assertEqual(model.source_delimiter, "\t")

        # Load into workspace engine
        ws_root = Path(self.temp_dir.name) / "test_workspace"
        store = WorkspaceStore(ws_root)
        store.create()
        engine = MartEngine(store, preflight=lambda *_: {"allowed": True})

        scope = {"Period": "2026-01"}
        rev = engine.load_snapshot(model, [tab_path], scope, [tab_path.name], complete=True)
        self.assertGreaterEqual(rev["revision"], 1)

        # Verify DuckDB content
        import duckdb
        db = duckdb.connect(str(store.database), read_only=True)
        count = db.execute("SELECT COUNT(*) FROM fact_" + model.fact_table).fetchone()[0]
        self.assertEqual(count, 2)
        db.close()
        dlg.close()

    def test_backward_compatibility_reloading_legacy_database(self):
        """Regression test for C1: Re-loading a workspace whose mart_meta.model lacks source_* keys."""
        import json

        from src.mart.engine import MartEngine
        from src.mart.model import Dimension, MartModel, Measure
        from src.mart.storage import WorkspaceStore

        ws_root = Path(self.temp_dir.name) / "legacy_workspace"
        store = WorkspaceStore(ws_root)
        store.create()
        engine = MartEngine(store, preflight=lambda *_: {"allowed": True})

        csv_path = Path(self.temp_dir.name) / "legacy.csv"
        csv_path.write_text("Period,Id,DimKey,Val\n2026-01,1,K1,10\n", encoding="utf-8")

        # 1. First initial load
        model = MartModel(
            name="legacy_model",
            fact_table="sales",
            dimensions=(Dimension(name="Dim", key_columns=("DimKey",)),),
            measures=(Measure(name="Val", column="Val"),),
            period_column="Period",
            row_key_columns=("Id",),
            version="1",
        )
        scope = {"Period": "2026-01"}
        engine.load_snapshot(model, [csv_path], scope, [csv_path.name], complete=True)

        # 2. Simulate legacy DB by stripping source_* keys from mart_meta.model in DuckDB
        import duckdb
        db = duckdb.connect(str(store.database))
        raw_model_json = db.execute("SELECT value FROM mart_meta WHERE key='model'").fetchone()[0]
        model_dict = json.loads(raw_model_json)
        model_dict.pop("source_delimiter", None)
        model_dict.pop("source_encoding", None)
        legacy_json = json.dumps(model_dict, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute("UPDATE mart_meta SET value=? WHERE key='model'", [legacy_json])
        db.close()

        # 3. Re-load with new model instance containing source_delimiter=',' and source_encoding='utf-8-sig'
        # Must NOT raise 'Model changes require a new workspace'!
        rev2 = engine.load_snapshot(model, [csv_path], scope, [csv_path.name], complete=True)
        self.assertGreaterEqual(rev2["revision"], 1)


    def test_step2_attribute_quick_search_and_filter(self):
        """Verify Step 2 attribute quick search box and show-assigned-only filter toggle."""
        dlg = MartModelWizardDialog(None, self.csv_path, language_code="ko")
        dlg.stack.setCurrentIndex(1)  # Step 2: Dimensions
        dlg.dim_list_widget.setCurrentRow(0)

        # 1. Type search query matching one specific column
        dlg.txt_search_attrs.setText("상품명")
        visible_items = [
            dlg.dim_attrs_checklist.item(i)
            for i in range(dlg.dim_attrs_checklist.count())
            if not dlg.dim_attrs_checklist.item(i).isHidden()
        ]
        self.assertEqual(len(visible_items), 1)
        self.assertEqual(visible_items[0].data(Qt.ItemDataRole.UserRole), "상품명")

        # 2. Clear search and toggle show_assigned_only
        dlg.txt_search_attrs.clear()
        dlg.chk_show_assigned_only.setChecked(True)
        assigned_visible = [
            dlg.dim_attrs_checklist.item(i)
            for i in range(dlg.dim_attrs_checklist.count())
            if not dlg.dim_attrs_checklist.item(i).isHidden()
        ]
        for it in assigned_visible:
            self.assertEqual(it.checkState(), Qt.CheckState.Checked)

        dlg.close()

    def test_step3_measure_editing_reflected_in_model(self):
        """Verify editing kind, unit, and scale in measure_table updates assignment & model (Codex P1)."""
        dlg = MartModelWizardDialog(None, self.csv_path, language_code="ko")
        dlg.stack.setCurrentIndex(2)  # Step 3: Fact & Measures

        # Find first measure row
        self.assertGreater(dlg.measure_table.rowCount(), 0)
        meas_col = dlg.measure_table.item(0, 0).text()

        # Edit kind to 'cost', unit to 'KRW', scale to '4'
        dlg.measure_table.item(0, 1).setText("cost")
        dlg.measure_table.item(0, 2).setText("KRW")
        dlg.measure_table.item(0, 3).setText("4")

        # Trigger item edits
        dlg._on_measure_item_edited(dlg.measure_table.item(0, 1))
        dlg._on_measure_item_edited(dlg.measure_table.item(0, 2))
        dlg._on_measure_item_edited(dlg.measure_table.item(0, 3))

        asgn = next(a for a in dlg.assignments if a.column_name == meas_col)
        self.assertEqual(asgn.measure_kind, "cost")
        self.assertEqual(asgn.measure_unit, "KRW")
        self.assertEqual(asgn.measure_scale, 4)

        # Ensure all other columns assigned or excluded
        for a in dlg.assignments:
            if a.role == "unassigned":
                a.role = ROLE_EXCLUDED

        dlg._on_finish()
        model = dlg.get_model()
        self.assertIsNotNone(model)
        target_m = next(m for m in model.measures if m.column == meas_col)
        self.assertEqual(target_m.kind, "cost")
        self.assertEqual(target_m.unit, "KRW")
        self.assertEqual(target_m.scale, 4)
        dlg.close()

    def test_manual_tsv_fallback_preserves_delimiter_without_wizard(self):
        """Verify manual form flow preserves detected delimiter into save_model without wizard (Opus N1 & Codex P1)."""
        import tempfile

        from src.qt.widgets.mart_workspace import MartWorkspace

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws_root = root / "manual_ws"
            tab_path = root / "data.tsv"
            tab_path.write_text("Period\tId\tDimKey\tAmt\n2026-01\t1\tK1\t100\n", encoding="utf-8")

            class ImmediateRunner:
                def start(self, name, worker, callbacks):
                    try:
                        callbacks.on_success(worker(lambda *args: callbacks.on_progress(*args)))
                    except (ValueError, OSError, RuntimeError) as exc:
                        callbacks.on_error(exc)
                    callbacks.on_finished()
                    return True

            widget = MartWorkspace("ko", ImmediateRunner(), root=root / "spaces")
            widget.current = ws_root
            widget.files = (tab_path,)
            widget.inspect_source()

            self.assertEqual(getattr(widget, "_detected_source_delim", None), "\t")

            # Setup minimal model fields
            widget.fact.setText("sales")
            widget.period.addItem("Period")
            widget.period.setCurrentText("Period")
            widget.row_keys.set_values(["Id"])
            widget.excluded.set_values([])
            from src.mart.model import Dimension, Measure
            widget.dims = [Dimension("d", ("DimKey",))]
            widget.metrics = [Measure("Amt", "Amt")]

            widget.save_model()
            self.assertIsNotNone(widget.model)
            self.assertEqual(widget.model.source_delimiter, "\t")

if __name__ == "__main__":
    unittest.main()

