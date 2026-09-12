"""Unit tests for preset_manager module."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from src.data_aggregator import ColumnGroupRule, DerivedFormulaRule, FilterCondition
from src.preset_manager import (
    AggregationPreset,
    PresetValidationError,
    _sanitize_filename,
    delete_preset,
    export_preset_file,
    import_preset_file,
    list_presets,
    load_preset,
    preset_exists,
    save_preset,
    validate_preset_against_columns,
)


class TestPresetManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.preset_dir = Path(self.temp_dir.name)

        self.sample_preset = AggregationPreset(
            name="TV사업부 연간 요약",
            description="TV 모델별 직접비 묶음 및 이익율 산출",
            group_by_keys=["디비전", "모델"],
            measure_sums=["매출", "영업이익"],
            column_groups=[
                ColumnGroupRule(new_column="직접비", source_columns=["비용1", "비용2"]),
            ],
            derived_formulas=[
                DerivedFormulaRule(
                    new_column="이익율(%)",
                    numerator_column="영업이익",
                    denominator_column="매출",
                    multiplier=100.0,
                )
            ],
            filters=[FilterCondition(column="디비전", operator="==", value="TV")],
            rollup_annual=True,
            month_column="월",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preset_exists(self):
        self.assertFalse(preset_exists("TV사업부 연간 요약", dir_path=self.preset_dir))
        save_preset(self.sample_preset, dir_path=self.preset_dir)
        self.assertTrue(preset_exists("TV사업부 연간 요약", dir_path=self.preset_dir))
        self.assertFalse(preset_exists("존재하지않는프리셋", dir_path=self.preset_dir))

    def test_save_and_load_preset(self):
        saved_path = save_preset(self.sample_preset, dir_path=self.preset_dir)
        self.assertTrue(saved_path.exists())

        loaded = load_preset("TV사업부 연간 요약", dir_path=self.preset_dir)
        self.assertEqual(loaded.name, self.sample_preset.name)
        self.assertEqual(loaded.description, self.sample_preset.description)
        self.assertEqual(loaded.group_by_keys, ["디비전", "모델"])
        self.assertEqual(loaded.column_groups[0].new_column, "직접비")
        self.assertEqual(loaded.derived_formulas[0].new_column, "이익율(%)")
        self.assertTrue(loaded.rollup_annual)

    def test_list_and_delete_presets(self):
        save_preset(self.sample_preset, dir_path=self.preset_dir)

        p2 = AggregationPreset(name="모바일 분기 요약")
        save_preset(p2, dir_path=self.preset_dir)

        presets = list_presets(dir_path=self.preset_dir)
        names = [p.name for p in presets]
        self.assertIn("TV사업부 연간 요약", names)
        self.assertIn("모바일 분기 요약", names)

        # Delete
        deleted = delete_preset("모바일 분기 요약", dir_path=self.preset_dir)
        self.assertTrue(deleted)
        remaining = list_presets(dir_path=self.preset_dir)
        self.assertEqual(len(remaining), 1)

    def test_export_and_import_preset_file(self):
        export_file = os.path.join(self.temp_dir.name, "shared_preset.json")
        out_path = export_preset_file(self.sample_preset, export_file)
        self.assertTrue(os.path.exists(out_path))

        # Import to a separate directory with save_to_local=True
        import_dir = Path(self.temp_dir.name) / "imported_store"
        imported = import_preset_file(export_file, save_to_local=True, dir_path=import_dir)
        self.assertEqual(imported.name, self.sample_preset.name)
        self.assertEqual(len(imported.column_groups), 1)
        self.assertTrue((import_dir / f"{_sanitize_filename(self.sample_preset.name)}.json").exists())

    def test_validate_preset_against_columns(self):
        # All columns present
        available = ["월", "디비전", "모델", "매출", "영업이익", "비용1", "비용2"]
        missing = validate_preset_against_columns(self.sample_preset, available)
        self.assertEqual(missing, [])

        # Missing '비용2' and '영업이익'
        partial = ["월", "디비전", "모델", "매출", "비용1"]
        missing_partial = validate_preset_against_columns(self.sample_preset, partial)
        self.assertIn("비용2", missing_partial)
        self.assertIn("영업이익", missing_partial)

        # Derived formula referencing column created by column_groups
        preset_with_chain = AggregationPreset(
            name="체인 계산",
            group_by_keys=["디비전"],
            measure_sums=[],
            column_groups=[ColumnGroupRule("총비용", ["비용1", "비용2"])],
            derived_formulas=[DerivedFormulaRule("비용율", "총비용", "매출")],
        )
        avail = ["디비전", "비용1", "비용2", "매출"]
        # '총비용' is created by column_groups, so it shouldn't be reported as missing!
        missing_chain = validate_preset_against_columns(preset_with_chain, avail)
        self.assertEqual(missing_chain, [])

    def test_invalid_json_handling(self):
        with self.assertRaises(PresetValidationError):
            AggregationPreset.from_dict({"no_name": "data"})

        # File not found
        with self.assertRaises(FileNotFoundError):
            load_preset("존재하지않는프리셋", dir_path=self.preset_dir)

    def test_sanitize_filename(self):
        # Reserved Windows device names
        self.assertEqual(_sanitize_filename("CON"), "_CON")
        self.assertEqual(_sanitize_filename("nul"), "_nul")
        self.assertEqual(_sanitize_filename("aux"), "_aux")
        self.assertEqual(_sanitize_filename("com1"), "_com1")

        # Invalid characters and whitespace/dots
        self.assertEqual(_sanitize_filename("hello:world/test?"), "hello_world_test_")
        self.assertEqual(_sanitize_filename("  test.name.  "), "test.name")

    def test_bundled_sample_preset_load(self):
        sample_file = Path("sample_data/sample_preset.json")
        if sample_file.exists():
            preset = import_preset_file(str(sample_file), save_to_local=False)
            self.assertEqual(preset.name, "TV사업부 연간 요약")
            self.assertEqual(preset.version, 1)
            self.assertTrue(preset.rollup_annual)


if __name__ == "__main__":
    unittest.main()
