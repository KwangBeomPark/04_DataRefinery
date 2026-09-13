"""Unit tests for the drag-and-drop controller's press/motion/release plumbing."""

import tkinter as tk
import unittest
from tkinter import ttk
from types import SimpleNamespace

from src.ui_dnd import DRAG_THRESHOLD_PX, DragDropController
from src.ui_field_list import FieldListItem


class FakeView:
    """Stands in for a FieldListView: real drag widget, scripted hit testing."""

    def __init__(self, parent, items):
        self._widget = ttk.Frame(parent)
        self._items = list(items)
        self.drop_active = False
        self.drop_index = 0

    def drag_widget(self):
        return self._widget

    def index_at(self, y):
        return 0 if self._items else None

    def item_at_index(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def drop_index_at(self, y):
        return self.drop_index

    def set_drop_active(self, active):
        self.drop_active = active


def event(x_root=0, y_root=0, y=0):
    return SimpleNamespace(x_root=x_root, y_root=y_root, x=0, y=y)


class TestDragDropController(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.controller = DragDropController(self.root)
        self.source = FakeView(self.root, [FieldListItem(name="매출", label="매출")])
        self.target = FakeView(self.root, [])
        self.drops = []

        self.controller.register(self.source, draggable=True)
        self.controller.register(
            self.target,
            accepts=lambda payload: True,
            on_drop=lambda payload, index: self.drops.append((payload.item.name, index)),
        )
        self.root.winfo_containing = lambda x, y: self.target

    def tearDown(self):
        self.controller.cancel()
        self.root.destroy()

    def press(self):
        self.controller._on_press(event(100, 100, 5), self.source)

    def test_small_movement_is_a_click_not_a_drag(self):
        self.press()
        self.controller._on_motion(event(100 + DRAG_THRESHOLD_PX - 1, 100))

        self.controller._on_release(event(100, 100))
        self.assertEqual(self.drops, [])

    def test_drag_past_the_threshold_drops_on_the_target(self):
        self.press()
        self.target.drop_index = 2
        self.controller._on_motion(event(100 + DRAG_THRESHOLD_PX + 5, 140))

        self.assertTrue(self.target.drop_active)
        self.controller._on_release(event(200, 140))

        self.assertEqual(self.drops, [("매출", 2)])
        self.assertFalse(self.target.drop_active)

    def test_target_that_rejects_the_payload_receives_no_drop(self):
        self.controller._registrations[1].accepts = lambda payload: False
        self.press()
        self.controller._on_motion(event(200, 140))

        self.controller._on_release(event(200, 140))

        self.assertEqual(self.drops, [])
        self.assertFalse(self.target.drop_active)

    def test_release_without_a_press_is_ignored(self):
        self.controller._on_release(event(200, 140))
        self.assertEqual(self.drops, [])

    def test_press_on_empty_space_starts_nothing(self):
        empty = FakeView(self.root, [])
        self.controller.register(empty, draggable=True)

        self.controller._on_press(event(10, 10, 500), empty)
        self.controller._on_motion(event(200, 200))
        self.controller._on_release(event(200, 200))

        self.assertEqual(self.drops, [])

    def test_cancel_clears_an_in_flight_drag(self):
        self.press()
        self.controller._on_motion(event(200, 140))
        self.assertIsNotNone(self.controller._ghost)

        self.controller.cancel()

        self.assertIsNone(self.controller._ghost)
        self.assertFalse(self.target.drop_active)
        self.controller._on_release(event(200, 140))
        self.assertEqual(self.drops, [])

    def test_drop_outside_any_target_is_harmless(self):
        self.root.winfo_containing = lambda x, y: None
        self.press()
        self.controller._on_motion(event(200, 140))

        self.controller._on_release(event(900, 900))

        self.assertEqual(self.drops, [])
        self.assertIsNone(self.controller._ghost)


if __name__ == "__main__":
    unittest.main()
