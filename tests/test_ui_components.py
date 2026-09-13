import tkinter as tk
import unittest

from src.ui_components import UpdateMenu


class TestUpdateMenu(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.enabled = tk.BooleanVar(self.root, value=True)
        self.language = tk.StringVar(self.root, value="English")
        self.language_changes = []

    def tearDown(self):
        self.root.destroy()

    def build(self, with_language=True):
        extra = {}
        if with_language:
            extra = {
                "language_variable": self.language,
                "languages": ("English", "한국어", "Polski"),
                "on_language_changed": lambda: self.language_changes.append(self.language.get()),
            }
        return UpdateMenu(
            self.root,
            self.enabled,
            on_check=lambda: None,
            on_download=lambda: None,
            on_preference_changed=lambda: None,
            **extra,
        )

    def test_update_actions_are_kept_in_one_compact_menu(self):
        component = self.build(with_language=False)
        component.set_texts(
            status="Up to date",
            check="Check now",
            download="Download update",
            enabled="Check automatically",
        )
        component.set_download_enabled(True)

        menu = component._menu
        self.assertEqual(menu.index("end"), 5)
        self.assertEqual(menu.entrycget(component.status_index, "label"), "Up to date")
        self.assertEqual(menu.entrycget(component.check_index, "label"), "Check now")
        self.assertEqual(menu.entrycget(component.download_index, "state"), "normal")
        self.assertEqual(menu.entrycget(component.enabled_index, "label"), "Check automatically")

    def test_language_lives_in_the_same_menu_as_a_submenu(self):
        component = self.build()
        component.set_texts(status="s", check="c", download="d", enabled="e")
        component.set_language_label("Language")

        menu = component._menu
        self.assertEqual(menu.entrycget(0, "label"), "Language")
        self.assertEqual(menu.type(0), "cascade")
        # The update entries shifted, and the indices moved with them.
        self.assertEqual(menu.entrycget(component.status_index, "label"), "s")
        self.assertEqual(menu.entrycget(component.enabled_index, "label"), "e")

    def test_choosing_a_language_sets_the_shared_variable(self):
        component = self.build()
        submenu = component._language_menu

        self.assertEqual(submenu.index("end"), 2)
        submenu.invoke(1)

        self.assertEqual(self.language.get(), "한국어")
        self.assertEqual(self.language_changes, ["한국어"])  # the app re-labels on this callback

    def test_language_label_is_harmless_without_a_language_section(self):
        component = self.build(with_language=False)
        component.set_language_label("Language")  # must not raise

        self.assertIsNone(component._language_menu)

    def test_download_stays_disabled_until_there_is_something_to_download(self):
        component = self.build()
        component.set_texts(status="s", check="c", download="d", enabled="e")

        self.assertEqual(component._menu.entrycget(component.download_index, "state"), "disabled")
        component.set_download_enabled(True)
        self.assertEqual(component._menu.entrycget(component.download_index, "state"), "normal")


if __name__ == "__main__":
    unittest.main()
