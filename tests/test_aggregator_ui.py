import os
import pathlib
import shutil
import tempfile
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import aggregator_ui
from src.aggregator_dialogs import PreviewDialog
from src.aggregator_fields import DIMENSION, MEASURE, VALUES
from src.data_aggregator import AggregationCancelledError, ColumnGroupRule, DerivedFormulaRule, FilterCondition
from src.data_refinery import DataRefineryApp
from src.ui_dnd import DragPayload

COLUMNS = ["월", "국가", "디비전", "수량", "매출", "비용1", "비용2", "비용3"]
DIMENSIONS = ["월", "국가", "디비전"]
MEASURES = ["수량", "매출", "비용1", "비용2", "비용3"]


class AggregatorTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = DataRefineryApp(self.root)
        self.tab = self.app.aggregator_tab

    def tearDown(self):
        try:
            for after_id in self.root.tk.splitlist(self.root.tk.eval('after info')):
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        except Exception:
            pass
        self.root.destroy()

    def load_schema(self):
        self.tab.state.load_schema(DIMENSIONS, MEASURES, month_column="월", columns=COLUMNS)
        self.tab.refresh_views()

    def _payload(self, view, name):
        """A drag payload for the named row, as the controller would build it."""
        index = view.names().index(name)
        return DragPayload(source=view, item=view.items()[index], index=index)


class TestAggregatorShell(AggregatorTestCase):
    def test_three_tabs_exist_and_switch(self):
        self.assertEqual(len(self.app.notebook.tabs()), 3)

        self.app._select_task_tab(self.app.aggregator_tab)
        self.assertEqual(self.app._selected_task_id(), "aggregator")
        self.assertEqual(self.app.aggregator_tab_button.cget("style"), "TaskTab.Selected.TButton")
        self.assertEqual(self.app.csv_tab_button.cget("style"), "TaskTab.TButton")

        self.app._select_task_tab(self.app.csv_tab)
        self.assertEqual(self.app._selected_task_id(), "csv")
        self.assertEqual(self.app.csv_tab_button.cget("style"), "TaskTab.Selected.TButton")

    def test_language_switch_updates_all_three_tabs(self):
        self.app.language.set("English")
        self.app._apply_language()
        self.assertEqual(self.app.aggregator_tab_button.cget("text"), "Data aggregator")

        self.app.language.set("한국어")
        self.app._apply_language()
        self.assertEqual(self.app.aggregator_tab_button.cget("text"), "데이터 집계·슬라이서")

        self.app.language.set("Polski")
        self.app._apply_language()
        self.assertEqual(self.app.aggregator_tab_button.cget("text"), "Agregacja danych")

    def test_language_switch_translates_aggregator_controls(self):
        self.app.language.set("English")
        self.app._apply_language()
        self.assertEqual(self.tab.btn_run.cget("text"), self.tab._ui("agg_run"))
        self.assertEqual(self.tab.btn_run.cget("text"), "★ Aggregate and save")

        self.app.language.set("한국어")
        self.app._apply_language()
        self.assertEqual(self.tab.btn_run.cget("text"), "★ 데이터 집계 및 저장하기")

    def test_missing_translation_key_falls_back_instead_of_raising(self):
        self.assertEqual(self.app._ui("definitely_not_a_key"), "definitely_not_a_key")

    def test_aggregator_tab_components_initialized(self):
        self.assertIsNotNone(self.tab.view_dimensions)
        self.assertIsNotNone(self.tab.view_measures)
        self.assertIsNotNone(self.tab.view_rows)
        self.assertIsNotNone(self.tab.view_values)
        self.assertIsNotNone(self.tab.view_filters)
        self.assertEqual(self.tab.group_keys, [])


class TestAggregatorTranslations(unittest.TestCase):
    """Guards the 3-language string table; a typo here would crash at runtime."""

    def agg_keys(self, language):
        from src.data_refinery import _UI_TEXT

        return {key for key in _UI_TEXT[language] if key.startswith("agg_")}

    def placeholders(self, template):
        import string

        return {field for _, field, _, _ in string.Formatter().parse(template) if field}

    def test_every_language_defines_the_same_aggregator_keys(self):
        english = self.agg_keys("en")
        self.assertGreater(len(english), 100)
        self.assertEqual(self.agg_keys("ko"), english)
        self.assertEqual(self.agg_keys("pl"), english)

    def test_format_placeholders_match_across_languages(self):
        from src.data_refinery import _UI_TEXT

        for key in self.agg_keys("en"):
            expected = self.placeholders(_UI_TEXT["en"][key])
            for language in ("ko", "pl"):
                self.assertEqual(
                    self.placeholders(_UI_TEXT[language][key]),
                    expected,
                    msg=f"placeholder mismatch for {key!r} in {language!r}",
                )

    def test_column_hints_stay_short_enough_not_to_clip(self):
        """A Treeview cell cannot wrap, so a long hint is cut mid-word.

        The pool and area lists are about half a pane wide; only the filter
        strip spans the full width and may carry a longer sentence.
        """
        from src.data_refinery import _UI_TEXT

        narrow = ("agg_hint_dimensions", "agg_hint_measures", "agg_hint_rows", "agg_hint_values")
        for language in ("en", "ko", "pl"):
            for key in narrow:
                hint = _UI_TEXT[language][key]
                self.assertLessEqual(
                    len(hint), 24, msg=f"{key} in {language!r} is {len(hint)} chars and will clip"
                )

    def test_every_key_the_tab_asks_for_exists(self):
        import re
        from pathlib import Path

        double_quoted = re.compile(r'"(agg_[a-z0-9_]+)"')
        single_quoted = re.compile(r"'(agg_[a-z0-9_]+)'")

        used = set()
        for name in ("src/aggregator_ui.py", "src/aggregator_dialogs.py"):
            text = Path(name).read_text(encoding="utf-8")
            used |= set(double_quoted.findall(text)) | set(single_quoted.findall(text))

        self.assertTrue(used)
        self.assertEqual(used - self.agg_keys("en"), set())
        self.assertEqual(self.agg_keys("en") - used - {"agg_initial_result"}, set())


class TestSetupCard(AggregatorTestCase):
    """Source / folder / file name are three rows of one grid, so the columns line up."""

    def test_the_three_rows_share_one_grid(self):
        entries = (self.tab.ent_file, self.tab.ent_out_dir, self.tab.ent_out_name)

        self.assertEqual({entry.master for entry in entries}, {self.tab.ent_file.master})
        self.assertEqual([int(entry.grid_info()["row"]) for entry in entries], [0, 1, 2])
        self.assertEqual({int(entry.grid_info()["column"]) for entry in entries}, {1})

    def test_open_buttons_sit_on_the_rows_they_act_on(self):
        card = self.tab.ent_file.master
        self.assertIs(self.tab.btn_open_folder.master, card)
        self.assertIs(self.tab.btn_open_file.master, card)
        self.assertEqual(self.tab.btn_open_folder.grid_info()["row"], self.tab.ent_out_dir.grid_info()["row"])
        self.assertEqual(self.tab.btn_open_file.grid_info()["row"], self.tab.ent_out_name.grid_info()["row"])

    def test_preset_actions_are_one_menu_whose_labels_follow_the_language(self):
        menu = self.tab._preset_menu
        self.assertEqual(menu.index("end"), 4)

        for language, load_label in (("English", "Load"), ("한국어", "불러오기"), ("Polski", "Wczytaj")):
            self.app.language.set(language)
            self.app._apply_language()
            self.assertEqual(menu.entrycget(0, "label"), load_label)


class TestPaneAlignment(AggregatorTestCase):
    """Both panes share one row structure so the four lists sit on one baseline."""

    def row_heights(self, row):
        self.root.update_idletasks()
        return [card.grid_bbox(0, row)[3] for card in self.tab._pane_cards]

    def test_utility_and_action_rows_are_the_same_height_in_both_panes(self):
        for row in (0, 3):
            heights = self.row_heights(row)
            self.assertEqual(len(heights), 2)
            self.assertGreater(heights[0], 0)
            self.assertEqual(heights[0], heights[1], msg=f"row {row}: {heights}")

    def test_a_scrolling_filter_strip_does_not_push_the_lists_apart(self):
        before = self.row_heights(0)
        self.tab.state.add_filter(FilterCondition("디비전", "==", "Mobile"))
        self.tab.state.add_filter(FilterCondition("국가", "==", "KR"))
        self.tab.refresh_views()
        self.tab.view_filters._on_scroll("0.0", "0.5")  # what the tree reports once mapped

        self.assertEqual(self.row_heights(0), before)


class TestFieldPlacement(AggregatorTestCase):
    def test_placing_a_dimension_removes_it_from_the_source_list(self):
        self.load_schema()
        self.assertIn("디비전", self.tab.view_dimensions.names())

        self.tab._place_as_row("디비전")

        self.assertNotIn("디비전", self.tab.view_dimensions.names())
        self.assertEqual(self.tab.view_rows.names(), ["디비전"])
        self.assertEqual(self.tab.group_keys, ["디비전"])

    def test_removing_a_placed_field_returns_it_to_the_source_list(self):
        self.load_schema()
        self.tab._place_as_value("매출")
        self.assertNotIn("매출", self.tab.view_measures.names())

        self.tab._unplace("매출")

        self.assertIn("매출", self.tab.view_measures.names())
        self.assertEqual(self.tab.view_values.names(), [])

    def test_source_rows_have_no_remove_button_but_placed_rows_do(self):
        self.load_schema()
        self.tab._place_as_row("국가")

        source_row = self.tab.view_dimensions.items()[0]
        placed_row = self.tab.view_rows.items()[0]
        self.assertFalse(source_row.removable)
        self.assertTrue(placed_row.removable)

    def test_month_column_is_tagged_in_the_source_list(self):
        self.load_schema()
        month_row = next(item for item in self.tab.view_dimensions.items() if item.name == "월")
        self.assertEqual(month_row.tag, "month")
        self.assertIn(self.tab._ui("agg_month_tag"), month_row.label)

    def test_dragging_onto_the_other_pool_reclassifies(self):
        """The ↔ buttons are gone; dropping a field on the other pool is the move."""
        self.load_schema()
        index = self.tab.view_dimensions.names().index("국가")
        payload = DragPayload(
            source=self.tab.view_dimensions, item=self.tab.view_dimensions.items()[index], index=index
        )

        self.tab._drop_on_pool(payload, MEASURE)

        self.assertNotIn("국가", self.tab.view_dimensions.names())
        self.assertIn("국가", self.tab.view_measures.names())


class TestRuleColumns(AggregatorTestCase):
    def test_column_group_appears_at_the_bottom_of_the_measure_list(self):
        self.load_schema()

        self.tab.state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2", "비용3"]))
        self.tab.refresh_views()

        names = self.tab.view_measures.names()
        self.assertEqual(names[-1], "총비용")

        row = self.tab.view_measures.items()[-1]
        self.assertEqual(row.label, "∑ 총비용")
        self.assertEqual(row.tag, "derived")
        self.assertTrue(row.removable)
        self.assertEqual(row.hint, "총비용 = 비용1 + 비용2 + 비용3")

    def test_formula_column_shows_its_definition_on_hover(self):
        self.load_schema()
        self.tab.state.add_derived_formula(DerivedFormulaRule("이익율", "매출", "수량"))
        self.tab.refresh_views()

        row = self.tab.view_measures.items()[-1]
        self.assertEqual(row.label, "% 이익율")
        # No meaningless "× 1", and the display format is named instead.
        self.assertEqual(row.hint, f"이익율 = 매출 ÷ 수량  ({self.tab._ui('agg_dlg_format_percent')})")

    def test_a_real_multiplier_still_shows_in_the_hint(self):
        self.load_schema()
        self.tab.state.add_derived_formula(
            DerivedFormulaRule("배수", "매출", "수량", multiplier=1000.0, format_type="number")
        )
        self.tab.refresh_views()

        self.assertIn("× 1000", self.tab.view_measures.items()[-1].hint)

    def test_deleting_a_rule_without_dependents_needs_no_confirmation(self):
        from unittest.mock import patch

        self.load_schema()
        self.tab.state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        self.tab.refresh_views()

        with patch("src.aggregator_ui.messagebox.askyesno") as mock_ask:
            self.tab._delete_rule("총비용")

        mock_ask.assert_not_called()
        self.assertNotIn("총비용", self.tab.view_measures.names())

    def test_deleting_a_rule_with_dependents_asks_first(self):
        from unittest.mock import patch

        self.load_schema()
        self.tab.state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        self.tab.state.add_derived_formula(DerivedFormulaRule("비용율", "총비용", "매출"))
        self.tab.refresh_views()

        with patch("src.aggregator_ui.messagebox.askyesno", return_value=False) as mock_ask:
            self.tab._delete_rule("총비용")

        mock_ask.assert_called_once()
        self.assertIn("총비용", self.tab.view_measures.names())


class TestDragAndDrop(AggregatorTestCase):
    def test_drop_from_pool_onto_rows_places_the_field(self):
        self.load_schema()
        payload = self._payload(self.tab.view_dimensions, "디비전")

        self.tab._drop_on_rows(payload, 0)

        self.assertEqual(self.tab.view_rows.names(), ["디비전"])
        self.assertNotIn("디비전", self.tab.view_dimensions.names())

    def test_drop_within_values_reorders(self):
        self.load_schema()
        self.tab._place_as_value("수량")
        self.tab._place_as_value("매출")
        payload = self._payload(self.tab.view_values, "매출")

        self.tab._drop_on_values(payload, 0)

        self.assertEqual(self.tab.view_values.names(), ["매출", "수량"])

    def test_drop_back_onto_a_pool_unplaces_the_field(self):
        self.load_schema()
        self.tab._place_as_value("매출")
        payload = self._payload(self.tab.view_values, "매출")

        self.tab._drop_on_pool(payload, MEASURE)

        self.assertEqual(self.tab.view_values.names(), [])
        self.assertIn("매출", self.tab.view_measures.names())

    def test_a_measure_cannot_be_dropped_into_row_groups(self):
        self.load_schema()
        payload = self._payload(self.tab.view_measures, "매출")

        self.assertFalse(self.tab.state.can_place_group_key("매출"))
        self.tab._drop_on_rows(payload, 0)

        self.assertEqual(self.tab.view_rows.names(), [])
        self.assertIn("매출", self.tab.view_measures.names())

    def test_a_dimension_cannot_be_dropped_into_values(self):
        self.load_schema()
        payload = self._payload(self.tab.view_dimensions, "디비전")

        self.assertFalse(self.tab.state.can_place_value("디비전"))
        self.tab._drop_on_values(payload, 0)

        self.assertEqual(self.tab.view_values.names(), [])
        self.assertIn("디비전", self.tab.view_dimensions.names())

    def test_reclassifying_is_the_way_to_use_a_measure_as_a_row_key(self):
        self.load_schema()
        payload = self._payload(self.tab.view_measures, "수량")
        self.tab._drop_on_pool(payload, DIMENSION)

        self.assertTrue(self.tab.state.can_place_group_key("수량"))
        self.tab._place_as_row("수량")
        self.assertEqual(self.tab.view_rows.names(), ["수량"])

    def test_derived_columns_are_rejected_as_row_groups(self):
        self.load_schema()
        self.tab.state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        self.tab.refresh_views()
        payload = self._payload(self.tab.view_measures, "총비용")

        self.tab._drop_on_rows(payload, 0)

        self.assertEqual(self.tab.view_rows.names(), [])

    def test_the_year_and_a_constant_cannot_be_dragged_into_the_measure_pool(self):
        self.load_schema()
        self.tab.state.add_constant_column("YYYY", "2026")
        self.tab.refresh_views()
        measures = next(r for r in self.tab.dnd._registrations if r.view is self.tab.view_measures)

        for name in ("연도", "YYYY"):
            payload = self._payload(self.tab.view_dimensions, name)
            self.assertFalse(measures.accepts(payload), name)
            self.tab._drop_on_pool(payload, MEASURE)
            self.assertIn(name, self.tab.view_dimensions.names())
            self.assertNotIn(name, self.tab.view_measures.names())


class TestFiltersAndPresets(AggregatorTestCase):
    def test_filter_management_add_and_remove(self):
        self.load_schema()
        self.tab.state.add_filter(FilterCondition(column="디비전", operator="==", value="패션"))
        self.tab.refresh_views()

        self.assertEqual(self.tab.view_filters.size(), 1)
        self.assertEqual(self.tab.view_filters.items()[0].label, "디비전 == 패션")

        self.tab._remove_filter("0")

        self.assertEqual(len(self.tab.filters), 0)
        self.assertEqual(self.tab.view_filters.size(), 0)

    def test_apply_selected_preset_with_filters(self):
        from unittest.mock import patch
        from src.preset_manager import AggregationPreset

        self.load_schema()
        self.tab.preset_name_var.set("필터포함프리셋")

        preset = AggregationPreset(
            name="필터포함프리셋",
            description="테스트용",
            group_by_keys=["디비전"],
            measure_sums=["매출"],
            column_groups=[],
            derived_formulas=[],
            filters=[
                FilterCondition(column="디비전", operator="in", value=["식품", "생활"]),
                FilterCondition(column="국가", operator="==", value="KR"),
            ],
            rollup_annual=True,
            month_column="월",
            output_format="xlsx",
        )

        with patch("src.aggregator_ui.load_preset", return_value=preset):
            self.tab.apply_selected_preset()

        self.assertEqual(len(self.tab.filters), 2)
        self.assertEqual(self.tab.view_filters.size(), 2)
        self.assertEqual(self.tab.view_filters.items()[0].label, "디비전 in 식품, 생활")
        self.assertEqual(self.tab.view_filters.items()[1].label, "국가 == KR")
        self.assertEqual(self.tab.view_rows.names(), ["디비전"])
        self.assertEqual(self.tab.view_values.names(), ["매출"])

    def test_save_current_as_preset_overwrite_prompt(self):
        from unittest.mock import patch

        self.load_schema()
        self.tab._place_as_row("디비전")
        self.tab._place_as_value("매출")

        with patch("src.aggregator_ui.simpledialog.askstring", side_effect=["테스트프리셋", "설명"]), \
             patch("src.aggregator_ui.preset_exists", return_value=True), \
             patch("src.aggregator_ui.messagebox.askyesno", return_value=False) as mock_ask, \
             patch("src.aggregator_ui.messagebox.showinfo"):
            self.tab.save_current_as_preset()

        mock_ask.assert_called_once()


class TestColumnSearch(AggregatorTestCase):
    def test_search_filters_both_source_lists(self):
        self.load_schema()
        self.tab.search_var.set("비용")

        self.assertEqual(self.tab.view_measures.names(), ["비용1", "비용2", "비용3"])
        self.assertEqual(self.tab.view_dimensions.names(), [])

    def test_search_is_case_insensitive(self):
        self.tab.state.load_schema(["Region"], ["Sales"], columns=["Region", "Sales"])
        self.tab.refresh_views()
        self.tab.search_var.set("reg")

        self.assertEqual(self.tab.view_dimensions.names(), ["Region"])

    def test_search_never_hides_a_placed_field(self):
        self.load_schema()
        self.tab._place_as_row("디비전")
        self.tab._place_as_value("매출")

        self.tab.search_var.set("비용")

        self.assertEqual(self.tab.view_rows.names(), ["디비전"])
        self.assertEqual(self.tab.view_values.names(), ["매출"])

    def test_clearing_the_search_restores_every_column(self):
        self.load_schema()
        self.tab.search_var.set("없는것")
        self.assertEqual(self.tab.view_measures.names(), [])

        self.tab.clear_search()

        self.assertEqual(self.tab.view_measures.names(), MEASURES)


class TestConstantColumnUI(AggregatorTestCase):
    def test_adding_a_literal_shows_its_value_in_the_label(self):
        self.load_schema()
        self.tab.state.add_constant_column("YYYY", "2026")
        self.tab.refresh_views()

        row = next(i for i in self.tab.view_dimensions.items() if i.name == "YYYY")
        self.assertEqual(row.label, "✎ YYYY = 2026")
        self.assertTrue(row.removable)
        self.assertIn("2026", row.hint)

    def test_removing_a_literal_from_the_pool(self):
        self.load_schema()
        self.tab.state.add_constant_column("YYYY", "2026")
        self.tab.refresh_views()

        self.tab._remove_constant_column("YYYY")

        self.assertNotIn("YYYY", self.tab.view_dimensions.names())

    def test_a_source_column_has_no_remove_button(self):
        self.load_schema()
        row = next(i for i in self.tab.view_dimensions.items() if i.name == "디비전")
        self.assertFalse(row.removable)


class TestYearField(AggregatorTestCase):
    def test_the_year_is_a_field_not_a_checkbox(self):
        self.load_schema()

        self.assertFalse(hasattr(self.tab, "chk_rollup"))
        row = next(i for i in self.tab.view_dimensions.items() if i.name == "연도")
        self.assertEqual(row.label, f"연도 {self.tab._ui('agg_year_tag')}")
        self.assertIn("월", row.hint)

    def test_placing_the_year_turns_roll_up_on(self):
        self.load_schema()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("월,디비전,매출\n202601,Mobile,10\n")
            path = handle.name
        try:
            self.tab.filepath_var.set(path)
            self.tab._place_as_value("매출")

            self.tab._place_as_row("디비전")
            self.assertFalse(self.tab._build_spec().rollup_annual)

            self.tab._place_as_row("연도")
            spec = self.tab._build_spec()
            self.assertTrue(spec.rollup_annual)
            self.assertIn("연도", spec.group_by_keys)
        finally:
            os.unlink(path)


class TestUndoWiring(AggregatorTestCase):
    """Every configuration change must be one Ctrl+Z away from being undone."""

    def test_undo_button_follows_whether_there_is_anything_to_undo(self):
        self.load_schema()
        self.assertEqual(str(self.tab.btn_undo.cget("state")), "disabled")

        self.tab._place_as_row("디비전")
        self.assertEqual(str(self.tab.btn_undo.cget("state")), "normal")

        self.tab.undo_last_change()
        self.assertEqual(str(self.tab.btn_undo.cget("state")), "disabled")

    def test_undo_restores_a_removed_field(self):
        self.load_schema()
        self.tab._place_as_value("매출")
        self.tab._unplace("매출")
        self.assertEqual(self.tab.view_values.names(), [])

        self.tab.undo_last_change()

        self.assertEqual(self.tab.view_values.names(), ["매출"])

    def test_a_rejected_change_leaves_no_dead_undo_step(self):
        """Dropping a measure on the row area does nothing, so Ctrl+Z afterwards
        must not silently consume a step that changes nothing."""
        self.load_schema()
        payload = self._payload(self.tab.view_measures, "매출")

        self.tab._drop_on_rows(payload, 0)

        self.assertEqual(self.tab.view_rows.names(), [])
        self.assertFalse(self.tab.state.can_undo())

    def test_a_drag_move_undoes_as_one_step(self):
        self.load_schema()
        self.tab._place_as_value("수량")
        self.tab._place_as_value("매출")
        payload = self._payload(self.tab.view_values, "매출")

        self.tab._drop_on_values(payload, 0)
        self.assertEqual(self.tab.view_values.names(), ["매출", "수량"])

        self.tab.undo_last_change()

        self.assertEqual(self.tab.view_values.names(), ["수량", "매출"])

    def test_undo_brings_back_a_deleted_rule(self):
        from unittest.mock import patch

        self.load_schema()
        self.tab.state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        self.tab.refresh_views()
        with patch("src.aggregator_ui.messagebox.askyesno", return_value=True):
            self.tab._delete_rule("총비용")
        self.assertNotIn("총비용", self.tab.view_measures.names())

        self.tab.undo_last_change()

        self.assertIn("총비용", self.tab.view_measures.names())

    def test_ctrl_z_only_acts_while_the_tab_is_on_screen(self):
        """The shortcut is bound on the window, so on another tab it must do nothing."""
        self.load_schema()
        self.tab._place_as_row("디비전")

        with patch.object(self.tab, "winfo_viewable", return_value=False):
            self.tab._undo_shortcut()
        self.assertEqual(self.tab.view_rows.names(), ["디비전"])

        with patch.object(self.tab, "winfo_viewable", return_value=True):
            self.tab._undo_shortcut()
        self.assertEqual(self.tab.view_rows.names(), [])

    def test_ctrl_z_inside_a_text_field_leaves_the_fields_alone(self):
        self.load_schema()
        self.tab._place_as_row("디비전")

        with patch.object(self.tab, "winfo_viewable", return_value=True), patch.object(
            self.tab, "focus_get", return_value=self.tab.ent_out_name
        ):
            self.tab._undo_shortcut()

        self.assertEqual(self.tab.view_rows.names(), ["디비전"])


class TestAggregationFunctionPicker(AggregatorTestCase):
    def test_sum_is_the_default_and_carries_no_marker(self):
        self.load_schema()
        self.tab._place_as_value("매출")

        row = self.tab.view_values.items()[0]
        self.assertEqual(self.tab.state.measure_function("매출"), "sum")
        self.assertEqual(row.label, "매출")

    def test_choosing_a_function_shows_in_the_label_and_reaches_the_spec(self):
        self.load_schema()
        self.tab._place_as_value("매출")
        self.tab._place_as_row("디비전")
        self.tab._function_target = "매출"

        self.tab._apply_function_choice("mean")

        self.assertEqual(self.tab.view_values.items()[0].label, "x̄ 매출")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("디비전,매출\nMobile,10\n")
            path = handle.name
        try:
            self.tab.filepath_var.set(path)
            self.assertEqual(self.tab._build_spec().measure_functions, {"매출": "mean"})
        finally:
            os.unlink(path)

    def test_going_back_to_sum_clears_the_marker_and_the_spec_entry(self):
        self.load_schema()
        self.tab._place_as_value("매출")
        self.tab._function_target = "매출"
        self.tab._apply_function_choice("count")

        self.tab._apply_function_choice("sum")

        self.assertEqual(self.tab.view_values.items()[0].label, "매출")
        self.assertEqual(self.tab.state.measure_functions_for_spec(), {})

    def test_choosing_a_function_is_undoable(self):
        self.load_schema()
        self.tab._place_as_value("매출")
        self.tab._function_target = "매출"
        self.tab._apply_function_choice("max")

        self.tab.undo_last_change()

        self.assertEqual(self.tab.state.measure_function("매출"), "sum")

    def test_picking_the_current_function_again_leaves_no_dead_undo_step(self):
        self.load_schema()
        self.tab._place_as_value("매출")
        self.tab._function_target = "매출"
        self.tab._apply_function_choice("max")
        self.tab._apply_function_choice("max")  # the menu shows it ticked; picked once more

        self.tab.undo_last_change()

        # One Ctrl+Z undoes the real choice, not a step that changed nothing.
        self.assertEqual(self.tab.state.measure_function("매출"), "sum")
        self.assertEqual(self.tab.view_values.names(), ["매출"])

    def test_a_rule_column_has_no_function_menu(self):
        from unittest.mock import patch

        self.load_schema()
        self.tab.state.add_column_group(ColumnGroupRule("총비용", ["비용1"]))
        self.tab.refresh_views()
        row = next(i for i in self.tab.view_measures.items() if i.name == "총비용")

        with patch.object(self.tab._function_menu, "tk_popup") as popup:
            self.tab._show_function_menu(row, 0, 0)

        popup.assert_not_called()

    def test_the_menu_offers_exactly_the_supported_functions(self):
        from src.aggregator_fields import MEASURE_FUNCTIONS

        menu = self.tab._function_menu
        offered = [menu.entrycget(index, "value") for index in range(menu.index("end") + 1)]
        self.assertEqual(offered, list(MEASURE_FUNCTIONS))


class TestOutputDestination(AggregatorTestCase):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.mkdtemp()
        self.source = os.path.join(self.folder, "ledger.csv")
        with open(self.source, "w", encoding="utf-8") as handle:
            handle.write("디비전,매출\nMobile,10\n")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)
        super().tearDown()

    def test_default_destination_is_the_source_folder(self):
        self.tab._reset_output_destination(self.source)

        self.assertEqual(self.tab.output_dir_var.get(), self.folder)
        self.assertTrue(self.tab.output_name_var.get().startswith("ledger_aggregated_"))
        self.assertTrue(self.tab.resolved_output_path().endswith(".xlsx"))
        self.assertEqual(os.path.dirname(self.tab.resolved_output_path()), self.folder)

    def test_extension_follows_the_chosen_format(self):
        self.tab._reset_output_destination(self.source)
        stem = self.tab.output_name_var.get()

        self.tab.output_format_var.set("CSV (.csv)")

        self.assertEqual(self.tab.output_name_var.get(), stem)  # the name is untouched
        self.assertTrue(self.tab.resolved_output_path().endswith(".csv"))

    def test_user_supplied_name_and_folder_win(self):
        self.tab.output_dir_var.set(self.folder)
        self.tab.output_name_var.set("월간_요약")

        self.assertEqual(
            self.tab.resolved_output_path(),
            os.path.abspath(os.path.join(self.folder, "월간_요약.xlsx")),
        )

    def test_a_typed_extension_is_not_doubled(self):
        self.tab.output_dir_var.set(self.folder)
        self.tab.output_name_var.set("월간_요약.xlsx")

        self.assertEqual(
            self.tab.resolved_output_path(),
            os.path.abspath(os.path.join(self.folder, "월간_요약.xlsx")),
        )

    def test_a_dot_in_the_name_is_not_mistaken_for_an_extension(self):
        self.tab.output_dir_var.set(self.folder)
        self.tab.output_name_var.set("2026.01 요약")

        self.assertEqual(
            self.tab.resolved_output_path(),
            os.path.abspath(os.path.join(self.folder, "2026.01 요약.xlsx")),
        )

    def test_a_foreign_extension_is_kept_as_part_of_the_name(self):
        self.tab.output_dir_var.set(self.folder)
        self.tab.output_name_var.set("요약.v2")

        self.assertEqual(
            self.tab.resolved_output_path(),
            os.path.abspath(os.path.join(self.folder, "요약.v2.xlsx")),
        )

    def test_incomplete_form_falls_back_to_the_engine_default(self):
        self.tab.output_dir_var.set("")
        self.tab.output_name_var.set("")

        self.assertIsNone(self.tab.resolved_output_path())

    def test_the_two_fields_and_the_format_fully_describe_the_destination(self):
        """The separate hint line is gone; folder + name + format must suffice."""
        self.tab.output_dir_var.set(self.folder)
        self.tab.output_name_var.set("요약")

        self.assertEqual(
            self.tab.resolved_output_path(),
            os.path.abspath(os.path.join(self.folder, "요약.xlsx")),
        )

    def test_open_buttons_stay_disabled_until_a_file_exists(self):
        self.assertEqual(str(self.tab.btn_open_folder.cget("state")), "disabled")

        self.tab._set_last_output(self.source)
        self.assertEqual(str(self.tab.btn_open_folder.cget("state")), "normal")
        self.assertEqual(str(self.tab.btn_open_file.cget("state")), "normal")

        self.tab._set_last_output(os.path.join(self.folder, "gone.xlsx"))
        self.assertEqual(str(self.tab.btn_open_folder.cget("state")), "disabled")

    def test_open_buttons_report_a_failure_instead_of_crashing(self):
        from unittest.mock import patch

        self.tab._set_last_output(self.source)
        with patch("src.aggregator_ui.open_containing_folder", return_value=False), \
             patch("src.aggregator_ui.messagebox.showwarning") as warn:
            self.tab.open_output_folder()

        warn.assert_called_once()

    def test_existing_file_asks_before_overwriting(self):
        from unittest.mock import patch

        destination = os.path.join(self.folder, "요약.xlsx")
        open(destination, "w", encoding="utf-8").close()
        self.tab.output_dir_var.set(self.folder)
        self.tab.output_name_var.set("요약")
        spec = SimpleNamespace(output_path=destination)

        with patch("src.aggregator_ui.messagebox.askyesno", return_value=False) as ask:
            self.assertFalse(self.tab._confirm_destination(spec))
        ask.assert_called_once()

    def test_missing_folder_is_refused(self):
        from unittest.mock import patch

        spec = SimpleNamespace(output_path=os.path.join(self.folder, "nope", "x.xlsx"))
        with patch("src.aggregator_ui.messagebox.showerror") as error:
            self.assertFalse(self.tab._confirm_destination(spec))
        error.assert_called_once()

    def test_new_file_in_a_real_folder_needs_no_confirmation(self):
        from unittest.mock import patch

        spec = SimpleNamespace(output_path=os.path.join(self.folder, "새파일.xlsx"))
        with patch("src.aggregator_ui.messagebox.askyesno") as ask:
            self.assertTrue(self.tab._confirm_destination(spec))
        ask.assert_not_called()


class TestPerFileMemory(AggregatorTestCase):
    """The tab remembers how each source file was last aggregated."""

    def setUp(self):
        super().setUp()
        from unittest.mock import patch

        from src import session_memory

        self.store_dir = tempfile.mkdtemp()
        # _store_path() hands back a Path, and the store calls .parent on it.
        store_file = pathlib.Path(self.store_dir) / "recent.json"
        self._patcher = patch.object(session_memory, "_store_path", lambda: store_file)
        self._patcher.start()

        self.source = os.path.join(self.store_dir, "ledger.csv")
        with open(self.source, "w", encoding="utf-8") as handle:
            handle.write("월,디비전,매출\n202601,Mobile,10\n")

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.store_dir, ignore_errors=True)
        super().tearDown()

    def configure_and_remember(self):
        self.load_schema()
        self.tab.filepath_var.set(self.source)
        self.tab._place_as_row("디비전")
        self.tab._place_as_value("매출")
        self.tab.state.set_measure_function("매출", "mean")
        self.tab.state.add_constant_column("YYYY", "2026")
        self.tab._place_as_row("YYYY")
        self.tab.state.add_filter(FilterCondition("디비전", "==", "Mobile"))
        self.tab._remember_configuration(self.source)

    def test_configuration_survives_a_reload(self):
        self.configure_and_remember()

        # A fresh screen, as if the file were opened again later.
        self.tab.state.load_schema(DIMENSIONS, MEASURES, month_column="월", columns=COLUMNS)
        self.tab.refresh_views()
        self.assertEqual(self.tab.view_rows.names(), [])

        self.assertTrue(self.tab._restore_remembered_configuration(self.source))
        self.tab.refresh_views()

        # Order is preserved: 디비전 was placed before the literal.
        self.assertEqual(self.tab.view_rows.names(), ["디비전", "YYYY"])
        self.assertEqual(self.tab.view_values.names(), ["매출"])
        self.assertEqual(self.tab.state.measure_function("매출"), "mean")
        self.assertEqual(self.tab.state.constant_columns, {"YYYY": "2026"})
        self.assertEqual(len(self.tab.filters), 1)

    def test_an_unseen_file_restores_nothing(self):
        self.load_schema()
        other = os.path.join(self.store_dir, "never_used.csv")

        self.assertFalse(self.tab._restore_remembered_configuration(other))

    def test_a_broken_store_never_blocks_opening_a_file(self):
        self.configure_and_remember()
        from src import session_memory

        with open(session_memory._store_path(), "w", encoding="utf-8") as handle:
            handle.write("{not json")

        self.assertFalse(self.tab._restore_remembered_configuration(self.source))

    def test_remembering_is_best_effort_and_swallows_failures(self):
        from unittest.mock import patch

        self.load_schema()
        with patch("src.aggregator_ui.remember", side_effect=OSError("disk full")):
            self.tab._remember_configuration(self.source)  # must not raise


class TestPreviewRowCount(AggregatorTestCase):
    """The result-row count is a pass over the whole file, so it runs off the Tk thread."""

    def setUp(self):
        super().setUp()
        self.load_schema()
        self.temp_dir = tempfile.TemporaryDirectory()
        path = os.path.join(self.temp_dir.name, "preview.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("월,디비전,매출\n202601,TV,10\n202601,Mobile,20\n202602,TV,30\n202603,Audio,5\n")
        self.tab.filepath_var.set(path)
        self.tab._place_as_row("디비전")
        self.tab._place_as_value("매출")

    def tearDown(self):
        self._pump()  # never leave a counting thread behind
        self.temp_dir.cleanup()
        super().tearDown()

    def _open_preview(self) -> PreviewDialog:
        with patch.object(PreviewDialog, "show", lambda _dialog: None):  # no modal wait in a test
            self.tab.run_preview()
        return next(child for child in self.tab.winfo_children() if isinstance(child, PreviewDialog))

    def _job_name(self) -> str:
        return f"{aggregator_ui._ROW_COUNT_JOB}-{self.tab._row_count_seq}"

    def _pump(self, timeout: float = 5.0) -> None:
        """Deliver the job's events the way the Tk loop would, until the count is over."""
        deadline = time.monotonic() + timeout
        while self.app.job_runner.is_running(self._job_name()) and time.monotonic() < deadline:
            self.app.job_runner.poll()
            time.sleep(0.01)
        self.app.job_runner.poll()

    def test_the_count_runs_as_a_job_and_lands_on_the_dialog(self):
        dialog = self._open_preview()
        try:
            # run_preview came back with the count still pending: the UI thread was never blocked.
            self.assertEqual(dialog.summary.cget("text"), self.tab._ui("agg_preview_counting"))
            self.assertTrue(self.app.job_runner.is_running(self._job_name()))

            self._pump()

            self.assertEqual(
                dialog.summary.cget("text"),
                self.tab._ui("agg_preview_summary").format(shown=3, rows="3"),
            )
        finally:
            dialog.destroy()

    def test_a_capped_count_is_reported_as_a_lower_bound(self):
        with patch("src.aggregator_ui.estimate_result_rows", return_value=(2_000_000, False)):
            dialog = self._open_preview()
            try:
                self._pump()
                self.assertEqual(
                    dialog.summary.cget("text"),
                    self.tab._ui("agg_preview_summary_capped").format(shown=3, rows="2,000,000"),
                )
            finally:
                dialog.destroy()

    def test_closing_the_dialog_cancels_the_count(self):
        seen = {}

        def slow_estimate(spec, cancel_event=None, **_kwargs):
            seen["event"] = cancel_event
            cancel_event.wait(5)
            raise AggregationCancelledError("cancelled")

        with patch("src.aggregator_ui.estimate_result_rows", slow_estimate):
            dialog = self._open_preview()
            dialog.destroy()
            self._pump()

        self.assertTrue(seen["event"].is_set())
        self.assertFalse(self.app.job_runner.is_running(self._job_name()))

    def test_a_failed_count_falls_back_to_the_sample_only_summary(self):
        with patch("src.aggregator_ui.estimate_result_rows", side_effect=OSError("unreadable")):
            dialog = self._open_preview()
            try:
                self._pump()
                self.assertEqual(
                    dialog.summary.cget("text"),
                    self.tab._ui("agg_preview_summary_sample").format(shown=3),
                )
            finally:
                dialog.destroy()


class TestSpecBuilding(AggregatorTestCase):
    def test_build_spec_without_a_file_shows_an_error(self):
        from unittest.mock import patch

        with patch("src.aggregator_ui.messagebox.showerror"):
            self.assertIsNone(self.tab._build_spec())

    def test_build_spec_carries_placement_and_order(self):
        self.load_schema()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("월,디비전,매출,비용1,비용2\n202401,Mobile,10,1,2\n")
            path = handle.name

        try:
            self.tab.filepath_var.set(path)
            self.tab._place_as_row("디비전")
            self.tab.state.add_column_group(ColumnGroupRule("총비용", ["비용1", "비용2"]))
            self.tab._place_as_value("총비용")
            self.tab._place_as_value("매출")
            self.tab.state.reorder(VALUES, 1, 0)  # 매출 first, 총비용 second
            self.tab.refresh_views()

            spec = self.tab._build_spec()

            self.assertIsNotNone(spec)
            self.assertEqual(spec.group_by_keys, ["디비전"])
            self.assertEqual(spec.measure_sums, ["매출"])
            self.assertEqual(spec.output_order, ["매출", "총비용"])
            self.assertEqual([g.new_column for g in spec.column_groups], ["총비용"])
            self.assertTrue(spec.column_groups[0].output)
        finally:
            os.unlink(path)

    def test_build_spec_requires_a_value(self):
        from unittest.mock import patch

        self.load_schema()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("디비전,매출\nMobile,10\n")
            path = handle.name

        try:
            self.tab.filepath_var.set(path)
            self.tab._place_as_row("디비전")
            with patch("src.aggregator_ui.messagebox.showwarning") as mock_warn:
                self.assertIsNone(self.tab._build_spec())
            mock_warn.assert_called_once()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
