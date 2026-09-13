"""Unit tests for the per-row remove list widget."""

import tkinter as tk
import unittest

from src.ui_field_list import REMOVE_GLYPH, FieldListItem, FieldListView, PlaceholderEntry


class TestFieldListView(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.removed = []
        self.activated = []
        self.view = FieldListView(
            self.root,
            on_remove=lambda item: self.removed.append(item.name),
            on_activate=lambda item: self.activated.append(item.name),
            empty_hint="비어 있음",
        )

    def tearDown(self):
        self.root.destroy()

    def items(self):
        return [
            FieldListItem(name="매출", label="매출"),
            FieldListItem(name="총비용", label="∑ 총비용", tag="derived", hint="총비용 = 비용1 + 비용2"),
            FieldListItem(name="월", label="월 [월]", removable=False),
        ]

    def test_set_items_renders_every_row(self):
        self.view.set_items(self.items())

        self.assertEqual(self.view.size(), 3)
        self.assertEqual(self.view.names(), ["매출", "총비용", "월"])
        self.assertEqual(len(self.view.tree.get_children()), 3)

    def test_only_removable_rows_show_the_glyph(self):
        self.view.set_items(self.items())
        children = self.view.tree.get_children()

        self.assertEqual(self.view.tree.set(children[0], "remove"), REMOVE_GLYPH)
        self.assertEqual(self.view.tree.set(children[2], "remove"), "")

    def test_tags_reach_the_tree(self):
        self.view.set_items(self.items())
        children = self.view.tree.get_children()

        self.assertIn("derived", self.view.tree.item(children[1], "tags"))

    def test_empty_list_shows_the_hint_and_counts_as_empty(self):
        self.view.set_items([])

        self.assertEqual(self.view.size(), 0)
        self.assertEqual(self.view.names(), [])
        self.assertEqual(len(self.view.tree.get_children()), 1)  # the hint row
        self.assertIsNone(self.view.selected_name())

    def test_set_empty_hint_updates_an_empty_list(self):
        self.view.set_items([])
        self.view.set_empty_hint("Drop fields here")

        hint_row = self.view.tree.get_children()[0]
        self.assertIn("Drop fields here", self.view.tree.item(hint_row, "text"))

    def test_selection_survives_a_rerender(self):
        self.view.set_items(self.items())
        self.view.select_name("총비용")
        self.assertEqual(self.view.selected_name(), "총비용")

        self.view.set_items(self.items())
        self.assertEqual(self.view.selected_name(), "총비용")

    def test_selection_is_dropped_when_the_row_disappears(self):
        self.view.set_items(self.items())
        self.view.select_name("총비용")

        self.view.set_items([FieldListItem(name="매출", label="매출")])

        self.assertIsNone(self.view.selected_name())

    def test_hint_row_is_not_reported_as_a_selection(self):
        self.view.set_items([])
        self.view.tree.selection_set("hint")

        self.assertIsNone(self.view.selected_index())
        self.assertIsNone(self.view.selected_name())

    def test_item_at_index_bounds(self):
        self.view.set_items(self.items())

        self.assertEqual(self.view.item_at_index(0).name, "매출")
        self.assertIsNone(self.view.item_at_index(9))
        self.assertIsNone(self.view.item_at_index(-1))

    def test_drop_active_toggles_the_border_colour(self):
        neutral = self.view.cget("highlightbackground")
        self.view.set_drop_active(True)
        active = self.view.cget("highlightbackground")
        self.view.set_drop_active(False)

        self.assertNotEqual(neutral, active)
        self.assertEqual(self.view.cget("highlightbackground"), neutral)

    def test_a_short_list_keeps_its_height_when_it_needs_a_scrollbar(self):
        """The scrollbar's own height must not stretch a one-row list (the filter strip)."""
        view = FieldListView(self.root, height=1)
        view.grid()
        view.set_items(self.items())
        view._on_scroll("0.0", "1.0")  # everything fits: no scrollbar
        self.root.update_idletasks()
        without_scrollbar = view.winfo_reqheight()

        view._on_scroll("0.0", "0.34")  # three rows in a one-row viewport
        self.root.update_idletasks()

        self.assertEqual(view._scroll_holder.winfo_manager(), "grid")
        self.assertEqual(view.winfo_reqheight(), without_scrollbar)


class TestPlaceholderEntry(unittest.TestCase):
    """The hint must never leak into the bound variable."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.var = tk.StringVar(self.root, value="")
        self.entry = PlaceholderEntry(self.root, textvariable=self.var, placeholder="Type to find…")
        self.entry.pack()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_the_variable_stays_empty_while_the_hint_shows(self):
        self.assertTrue(self.entry.placeholder_visible())
        self.assertEqual(self.var.get(), "")

    def test_typing_hides_the_hint(self):
        self.var.set("매출")
        self.root.update_idletasks()

        self.assertFalse(self.entry.placeholder_visible())
        self.assertEqual(self.var.get(), "매출")

    def test_clearing_brings_the_hint_back(self):
        self.var.set("매출")
        self.var.set("")
        self.root.update_idletasks()

        self.assertTrue(self.entry.placeholder_visible())

    def test_focus_hides_the_hint_even_when_empty(self):
        self.entry.event_generate("<FocusIn>")
        self.assertFalse(self.entry.placeholder_visible())

        self.entry.event_generate("<FocusOut>")
        self.assertTrue(self.entry.placeholder_visible())

    def test_changing_the_placeholder_text_takes_effect(self):
        self.entry.set_placeholder("컬럼 찾기")
        self.assertTrue(self.entry.placeholder_visible())
        self.assertEqual(self.entry._hint.cget("text"), "컬럼 찾기")

    def test_an_empty_placeholder_shows_nothing(self):
        self.entry.set_placeholder("")
        self.assertFalse(self.entry.placeholder_visible())

    def test_destroying_the_entry_detaches_it_from_the_variable(self):
        """The variable belongs to the screen and outlives the field."""
        reported = []
        self.root.report_callback_exception = lambda *args: reported.append(args)

        self.entry.destroy()
        self.var.set("typed after the field was gone")

        self.assertEqual(reported, [])
        self.assertEqual(self.var.trace_info(), [])


if __name__ == "__main__":
    unittest.main()
