import tkinter as tk
import unittest

from src.ui_components import UpdateMenu


class TestUpdateMenu(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_update_actions_are_kept_in_one_compact_menu(self):
        enabled = tk.BooleanVar(self.root, value=True)
        component = UpdateMenu(
            self.root,
            enabled,
            on_check=lambda: None,
            on_download=lambda: None,
            on_preference_changed=lambda: None,
        )
        component.set_texts(
            status="Up to date",
            check="Check now",
            download="Download update",
            enabled="Check automatically",
        )
        component.set_download_enabled(True)

        menu = component._menu
        self.assertEqual(menu.index("end"), 5)
        self.assertEqual(menu.entrycget(UpdateMenu.STATUS_INDEX, "label"), "Up to date")
        self.assertEqual(menu.entrycget(UpdateMenu.CHECK_INDEX, "label"), "Check now")
        self.assertEqual(menu.entrycget(UpdateMenu.DOWNLOAD_INDEX, "state"), "normal")
        self.assertEqual(
            menu.entrycget(UpdateMenu.ENABLED_INDEX, "label"),
            "Check automatically",
        )
