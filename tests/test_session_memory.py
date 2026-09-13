"""Unit tests for the per-file configuration memory.

Every test redirects `session_memory._store_path` into a temporary directory so
the user's real AppData store is never read or written.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import session_memory
from src.session_memory import (
    MAX_REMEMBERED_FILES,
    clear,
    forget,
    prune_missing,
    recall,
    remember,
)


class TestSessionMemory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store_file = self.root / "store" / "recent_configurations.json"
        patcher = patch.object(session_memory, "_store_path", lambda: self.store_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp_dir.cleanup)

        self.source = str(self.root / "월별실적원장.csv")
        self.configuration = {
            "group_by_keys": ["디비전", "모델"],
            "measure_sums": ["매출", "영업이익"],
            "rollup_annual": True,
            "output_format": "xlsx",
        }

    def _raw_entries(self):
        return json.loads(self.store_file.read_text(encoding="utf-8"))["entries"]

    def test_remember_then_recall_round_trip(self):
        remember(self.source, self.configuration)
        self.assertEqual(recall(self.source), self.configuration)
        self.assertTrue(self.store_file.exists())

    def test_stored_copy_is_detached_from_the_caller(self):
        remember(self.source, self.configuration)
        self.configuration["group_by_keys"].append("추가됨")
        self.assertEqual(recall(self.source)["group_by_keys"], ["디비전", "모델"])

    def test_relative_segments_hit_the_same_entry(self):
        detoured = os.path.join(str(self.root), "sub", "..", "월별실적원장.csv")
        remember(detoured, self.configuration)
        self.assertEqual(recall(self.source), self.configuration)
        self.assertEqual(len(self._raw_entries()), 1)

    @unittest.skipUnless(os.name == "nt", "case/separator folding is a Windows path rule")
    def test_case_and_separator_variants_hit_the_same_entry(self):
        remember(r"C:\Data\Sales.csv", {"mode": "first"})
        remember("c:/data/SALES.CSV", {"mode": "second"})
        self.assertEqual(len(self._raw_entries()), 1)
        self.assertEqual(recall(r"C:\DATA\sales.csv"), {"mode": "second"})

    def test_unknown_file_returns_none(self):
        self.assertIsNone(recall(self.source))
        remember(self.source, self.configuration)
        self.assertIsNone(recall(str(self.root / "다른파일.csv")))

    def test_overwriting_replaces_the_entry_and_refreshes_updated_at(self):
        remember(self.source, self.configuration)

        # Backdate the stored timestamp so the refresh is observable regardless
        # of how coarse the clock is between two consecutive calls.
        store = json.loads(self.store_file.read_text(encoding="utf-8"))
        key = next(iter(store["entries"]))
        store["entries"][key]["updated_at"] = "2000-01-01T00:00:00"
        self.store_file.write_text(json.dumps(store), encoding="utf-8")

        remember(self.source, {"group_by_keys": ["모델"], "output_format": "csv"})

        entries = self._raw_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(recall(self.source), {"group_by_keys": ["모델"], "output_format": "csv"})
        self.assertGreater(entries[key]["updated_at"], "2000-01-01T00:00:00")
        self.assertEqual(entries[key]["file_path"], self.source)

    def test_forget_reports_whether_an_entry_was_dropped(self):
        remember(self.source, self.configuration)
        other = str(self.root / "기타.csv")
        remember(other, {"group_by_keys": []})

        self.assertTrue(forget(self.source))
        self.assertFalse(forget(self.source))
        self.assertIsNone(recall(self.source))
        self.assertIsNotNone(recall(other))

    def test_clear_drops_every_entry(self):
        remember(self.source, self.configuration)
        remember(str(self.root / "기타.csv"), {"group_by_keys": []})

        clear()

        self.assertIsNone(recall(self.source))
        self.assertEqual(self._raw_entries(), {})

    def test_cap_evicts_the_least_recently_updated_entry(self):
        paths = [str(self.root / f"파일{index:03d}.csv") for index in range(MAX_REMEMBERED_FILES + 1)]
        for index, path in enumerate(paths[:MAX_REMEMBERED_FILES]):
            remember(path, {"index": index})
        self.assertEqual(len(self._raw_entries()), MAX_REMEMBERED_FILES)

        # Touching the oldest entry again must move it out of the eviction line.
        remember(paths[0], {"index": 0, "touched": True})
        remember(paths[MAX_REMEMBERED_FILES], {"index": MAX_REMEMBERED_FILES})

        self.assertEqual(len(self._raw_entries()), MAX_REMEMBERED_FILES)
        self.assertEqual(recall(paths[0]), {"index": 0, "touched": True})
        self.assertIsNone(recall(paths[1]))
        self.assertEqual(recall(paths[MAX_REMEMBERED_FILES]), {"index": MAX_REMEMBERED_FILES})

    def test_missing_and_empty_stores_recall_as_none(self):
        self.assertIsNone(recall(self.source))

        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        self.store_file.write_text("", encoding="utf-8")
        self.assertIsNone(recall(self.source))

    def test_corrupt_store_recalls_as_none_and_is_repaired_by_the_next_write(self):
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        self.store_file.write_text('{"version": 1, "entries": {"c:\\\\x.csv": {"config', encoding="utf-8")

        self.assertIsNone(recall(self.source))

        remember(self.source, self.configuration)

        self.assertEqual(recall(self.source), self.configuration)
        self.assertEqual(len(self._raw_entries()), 1)

    def test_entries_of_the_wrong_shape_are_ignored(self):
        remember(self.source, self.configuration)

        store = json.loads(self.store_file.read_text(encoding="utf-8"))
        key = next(iter(store["entries"]))
        store["entries"][key] = "not-an-entry"
        self.store_file.write_text(json.dumps(store), encoding="utf-8")

        self.assertIsNone(recall(self.source))

    def test_non_serialisable_configuration_raises_and_keeps_the_store(self):
        remember(self.source, self.configuration)
        before = self.store_file.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            remember(self.source, {"widget": object()})
        with self.assertRaises(ValueError):
            remember(self.source, ["not", "a", "dict"])

        self.assertEqual(self.store_file.read_text(encoding="utf-8"), before)
        self.assertEqual(recall(self.source), self.configuration)

    def test_prune_missing_drops_only_entries_whose_file_is_gone(self):
        present = self.root / "실존파일.csv"
        present.write_text("a,b\n1,2\n", encoding="utf-8")
        vanished = str(self.root / "사라진파일.csv")

        remember(str(present), {"index": 1})
        remember(vanished, {"index": 2})

        self.assertEqual(prune_missing(), 1)
        self.assertEqual(recall(str(present)), {"index": 1})
        self.assertIsNone(recall(vanished))
        self.assertEqual(prune_missing(), 0)

    def test_prune_missing_on_a_missing_store_is_zero(self):
        self.assertEqual(prune_missing(), 0)
        self.assertFalse(self.store_file.exists())


if __name__ == "__main__":
    unittest.main()
