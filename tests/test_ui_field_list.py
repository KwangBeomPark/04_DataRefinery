"""Unit tests for the per-row remove list widget."""

import tkinter as tk
import unittest

from src.ui_components import PALETTE
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

    def test_treeview_can_take_focus(self):
        """The Treeview must be explicitly keyboard focusable."""
        self.assertIn(str(self.view.tree.cget("takefocus")), ("1", "True"))

    def test_focus_in_and_out_toggles_visible_outline(self):
        """FocusIn applies the accent outline, FocusOut restores the neutral border."""
        self.assertEqual(self.view.cget("highlightbackground"), PALETTE["border"])

        self.view.tree.event_generate("<FocusIn>")
        self.assertEqual(self.view.cget("highlightbackground"), PALETTE["accent"])
        self.assertEqual(self.view.cget("highlightcolor"), PALETTE["accent"])

        self.view.tree.event_generate("<FocusOut>")
        self.assertEqual(self.view.cget("highlightbackground"), PALETTE["border"])
        self.assertEqual(self.view.cget("highlightcolor"), PALETTE["border"])

    def test_focus_in_and_out_preserves_drop_target_state(self):
        """Drop target border is restored on FocusOut instead of being clobbered."""
        self.view.set_drop_active(True)
        self.assertEqual(self.view.cget("highlightbackground"), PALETTE["drop_target"])

        # When focused, visible accent outline is displayed
        self.view.tree.event_generate("<FocusIn>")
        self.assertEqual(self.view.cget("highlightbackground"), PALETTE["accent"])

        # On FocusOut, drop-target border is restored
        self.view.tree.event_generate("<FocusOut>")
        self.assertEqual(self.view.cget("highlightbackground"), PALETTE["drop_target"])

        # Disabling drop target restores neutral border
        self.view.set_drop_active(False)
        self.assertEqual(self.view.cget("highlightbackground"), PALETTE["border"])

    def test_focus_outline_with_distinct_palette_colors(self):
        """Verify state transitions when drop_target and accent colors are distinct."""
        from unittest.mock import patch

        with patch.dict(PALETTE, {"drop_target": "#112233", "accent": "#445566", "border": "#778899"}):
            self.view._update_border()
            self.assertEqual(self.view.cget("highlightbackground"), "#778899")

            # Focus in and out toggles outline
            self.view.tree.event_generate("<FocusIn>")
            self.assertEqual(self.view.cget("highlightbackground"), "#445566")
            self.view.tree.event_generate("<FocusOut>")
            self.assertEqual(self.view.cget("highlightbackground"), "#778899")

            # Activate drop target
            self.view.set_drop_active(True)
            self.assertEqual(self.view.cget("highlightbackground"), "#112233")

            # Focus in switches to accent focus outline
            self.view.tree.event_generate("<FocusIn>")
            self.assertEqual(self.view.cget("highlightbackground"), "#445566")

            # Focus out restores drop target border, preserving drop target state
            self.view.tree.event_generate("<FocusOut>")
            self.assertEqual(self.view.cget("highlightbackground"), "#112233")

            # Disabling drop target restores normal border
            self.view.set_drop_active(False)
            self.assertEqual(self.view.cget("highlightbackground"), "#778899")

    def _press_key(self, view: FieldListView, key: str) -> None:
        if not view.winfo_manager():
            view.pack()
        self.root.deiconify()
        self.root.update()
        view.tree.focus_force()
        self.root.update()
        try:
            seq = key if key.startswith("<") else f"<{key}>"
            view.tree.event_generate(seq, when="now")
            self.root.update()
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_return_key_activates_selected_item(self):
        self.view.set_items(self.items())
        self.view.select_name("매출")
        self._press_key(self.view, "Return")
        self.assertEqual(self.activated, ["매출"])

    def test_delete_key_removes_selected_removable_item(self):
        self.view.set_items(self.items())
        self.view.select_name("총비용")
        self._press_key(self.view, "Delete")
        self.assertEqual(self.removed, ["총비용"])

    def test_delete_key_ignores_non_removable_item(self):
        self.view.set_items(self.items())
        self.view.select_name("월")
        dispatched = []
        self.view.tree.bind("<Delete>", lambda _e: dispatched.append(True), add="+")
        self._press_key(self.view, "Delete")
        self.assertEqual(dispatched, [True])
        self.assertEqual(self.removed, [])
        self.assertIn("월", self.view.names())
        self.assertEqual(self.view.selected_name(), "월")

    def test_focus_in_initializes_selection_and_focus_to_first_real_item(self):
        """When receiving focus without selection, focus and selection point to the first real item."""
        self.view.set_items(self.items())
        self.view.tree.selection_set()
        self.view.tree.focus("")
        self.assertIsNone(self.view.selected_index())

        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            self.assertEqual(self.view.selected_index(), 0)
            self.assertEqual(self.view.selected_name(), "매출")
            self.assertEqual(self.view.tree.selection(), ("row0",))
            self.assertEqual(self.view.tree.focus(), "row0")

            # Keyboard navigation can then begin immediately
            self.view.tree.event_generate("<Down>", when="now")
            self.root.update()
            self.assertEqual(self.view.selected_index(), 1)
            self.assertEqual(self.view.selected_name(), "총비용")
            self.assertEqual(self.view.tree.selection(), ("row1",))
            self.assertEqual(self.view.tree.focus(), "row1")
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_focus_in_preserves_existing_selection(self):
        """FocusIn does not clobber a valid existing selection or focus."""
        self.view.set_items(self.items())
        self.view.select_name("총비용")
        self.assertEqual(self.view.selected_index(), 1)

        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            self.assertEqual(self.view.selected_index(), 1)
            self.assertEqual(self.view.selected_name(), "총비용")
            self.assertEqual(self.view.tree.selection(), ("row1",))
            self.assertEqual(self.view.tree.focus(), "row1")

            # Keyboard navigation continues from existing selection
            self.view.tree.event_generate("<Down>", when="now")
            self.root.update()
            self.assertEqual(self.view.selected_index(), 2)
            self.assertEqual(self.view.selected_name(), "월")
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_focus_in_preserves_selection_when_focus_was_unset(self):
        """If selection exists but focus cursor was empty, FocusIn syncs focus to selection."""
        self.view.set_items(self.items())
        self.view.tree.selection_set("row2")
        self.view.tree.focus("")

        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            self.assertEqual(self.view.tree.selection(), ("row2",))
            self.assertEqual(self.view.tree.focus(), "row2")
            self.assertEqual(self.view.selected_name(), "월")
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_focus_in_leaves_empty_or_hint_only_content_unselected(self):
        """FocusIn on an empty or hint-only list does not select or focus the placeholder hint."""
        self.view.set_items([])
        self.assertEqual(len(self.view.tree.get_children()), 1)

        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            self.assertEqual(self.view.tree.selection(), ())
            self.assertEqual(self.view.tree.focus(), "")
            self.assertIsNone(self.view.selected_index())
            self.assertIsNone(self.view.selected_name())
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_focus_in_on_completely_empty_list_does_nothing(self):
        """A list without any items or hint remains completely unselected on focus."""
        view = FieldListView(self.root, empty_hint="")
        view.pack()
        view.set_items([])
        self.root.deiconify()
        self.root.update()
        view.tree.focus_force()
        self.root.update()

        try:
            self.assertEqual(view.tree.selection(), ())
            self.assertEqual(view.tree.focus(), "")
            self.assertIsNone(view.selected_index())
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass
            view.destroy()

    def test_focus_in_cleans_up_accidental_hint_selection(self):
        """If placeholder row was somehow selected, FocusIn clears it on an empty list."""
        self.view.set_items([])
        self.view.tree.selection_set("hint")
        self.view.tree.focus("hint")

        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            self.assertEqual(self.view.tree.selection(), ())
            self.assertEqual(self.view.tree.focus(), "")
            self.assertIsNone(self.view.selected_index())
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_focused_rerender_selects_surviving_row_at_same_index(self):
        """When the current row disappears while focused, select/focus the surviving row at the same index."""
        self.view.set_items(self.items())
        self.view.select_name("총비용")  # index 1
        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            # Remove '총비용'; remaining are '매출' (0) and '월' (now index 1)
            self.view.set_items([
                FieldListItem(name="매출", label="매출"),
                FieldListItem(name="월", label="월 [월]", removable=False),
            ])
            self.root.update()

            self.assertEqual(self.view.selected_index(), 1)
            self.assertEqual(self.view.selected_name(), "월")
            self.assertEqual(self.view.tree.focus(), "row1")
            self.assertEqual(self.view.tree.selection(), ("row1",))
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_focused_rerender_clamps_to_last_row_when_tail_removed(self):
        """When the last row disappears while focused, select/focus the clamped new last row."""
        self.view.set_items(self.items())
        self.view.select_name("월")  # index 2
        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            # Remove '월'; remaining are '매출' (0) and '총비용' (1)
            self.view.set_items([
                FieldListItem(name="매출", label="매출"),
                FieldListItem(name="총비용", label="∑ 총비용"),
            ])
            self.root.update()

            self.assertEqual(self.view.selected_index(), 1)
            self.assertEqual(self.view.selected_name(), "총비용")
            self.assertEqual(self.view.tree.focus(), "row1")
            self.assertEqual(self.view.tree.selection(), ("row1",))
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_focused_rerender_transitions_to_empty_without_selecting_hint(self):
        """When all rows disappear while focused, list transitions to empty and hint is not selected."""
        self.view.set_items([FieldListItem(name="매출", label="매출")])
        self.view.select_name("매출")
        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            self.view.set_items([])
            self.root.update()

            self.assertIsNone(self.view.selected_index())
            self.assertIsNone(self.view.selected_name())
            self.assertEqual(self.view.tree.selection(), ())
            self.assertEqual(self.view.tree.focus(), "")
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass

    def test_unfocused_rerender_drops_selection_without_altering_list(self):
        """An unfocused list whose item disappears drops selection and does not select surviving items."""
        self.view.set_items(self.items())
        self.view.select_name("총비용")
        # Ensure widget is unfocused
        self.root.withdraw()
        self.root.update()

        self.view.set_items([FieldListItem(name="매출", label="매출")])
        self.assertIsNone(self.view.selected_name())
        self.assertIsNone(self.view.selected_index())
        self.assertEqual(self.view.tree.selection(), ())

    def test_keyboard_navigation_continues_after_focused_rerender_without_refocus(self):
        """Down/Return events continue to work seamlessly after rerender removes current row."""
        self.view.set_items(self.items())
        self.view.select_name("매출")  # index 0
        if not self.view.winfo_manager():
            self.view.pack()
        self.root.deiconify()
        self.root.update()
        self.view.tree.focus_force()
        self.root.update()

        try:
            # Simulate activation moving '매출' out
            self.view.set_items([
                FieldListItem(name="총비용", label="∑ 총비용"),
                FieldListItem(name="월", label="월 [월]", removable=False),
            ])
            self.root.update()
            self.assertEqual(self.view.selected_name(), "총비용")

            # Press Down arrow without re-focusing
            self.view.tree.event_generate("<Down>", when="now")
            self.root.update()
            self.assertEqual(self.view.selected_name(), "월")
            self.assertEqual(self.view.selected_index(), 1)

            # Press Return on '월'
            self.view.tree.event_generate("<Return>", when="now")
            self.root.update()
            self.assertEqual(self.activated, ["월"])
        finally:
            try:
                self.root.withdraw()
                self.root.update()
            except tk.TclError:
                pass


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
