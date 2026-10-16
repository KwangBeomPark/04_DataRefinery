"""Source/previous-output preservation and explicit numeric-notation regression tests."""

import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.data_aggregator import (
    AggregationNumericError,
    AggregationOutputError,
    AggregationSpec,
    AggregatorError,
    DerivedFormulaRule,
    _coerce_numeric_series,
    aggregate_dataset,
    preview_aggregation,
    validate_aggregation_output_name,
)


class TestAggregationSafety(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "input.csv"
        self.output = self.root / "output.csv"
        self.write_rows(["100", "200"])

    def write_rows(self, values):
        with self.source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["customer", "sales"])
            writer.writerows(["001", value] for value in values)

    def spec(self, **kwargs):
        return AggregationSpec(**{
            "file_path": str(self.source), "group_by_keys": ["customer"],
            "measure_sums": ["sales"], "output_format": "csv",
            "output_path": str(self.output), **kwargs,
        })

    def test_source_paths_and_case_aliases_are_rejected_without_change(self):
        original = self.source.read_bytes()
        paths = [str(self.source), os.path.relpath(self.source), str(self.root / "." / self.source.name)]
        if os.name == "nt":
            paths.append(str(self.source).swapcase())
        for destination in paths:
            with self.subTest(destination=destination):
                with self.assertRaises(AggregationOutputError):
                    aggregate_dataset(self.spec(output_path=destination))
                self.assertEqual(self.source.read_bytes(), original)

    def test_existing_hardlink_to_source_is_rejected(self):
        os.link(self.source, self.output)
        original = self.source.read_bytes()
        with self.assertRaises(AggregationOutputError):
            aggregate_dataset(self.spec())
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.output.read_bytes(), original)

    def test_late_source_alias_and_unapproved_output_are_preserved(self):
        writer = pd.DataFrame.to_csv
        original = self.source.read_bytes()
        for alias in (True, False):
            with self.subTest(alias=alias):
                if self.output.exists():
                    self.output.unlink()

                def write_then_create_target(frame, *args, **kwargs):
                    result = writer(frame, *args, **kwargs)
                    if alias:
                        os.link(self.source, self.output)
                    else:
                        self.output.write_bytes(b"another writer's result")
                    return result

                with patch.object(pd.DataFrame, "to_csv", write_then_create_target):
                    with self.assertRaises(AggregationOutputError):
                        aggregate_dataset(self.spec(allow_output_overwrite=False))
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual(self.output.read_bytes(), original if alias else b"another writer's result")
                self.assertFalse(list(self.root.glob("*.tmp")))

    def test_existing_unapproved_output_is_not_replaced(self):
        self.output.write_bytes(b"previous good output")
        with self.assertRaises(AggregationOutputError):
            aggregate_dataset(self.spec(allow_output_overwrite=False))
        self.assertEqual(self.output.read_bytes(), b"previous good output")

    @unittest.skipUnless(os.name == "nt", "Windows no-replace rename contract")
    def test_atomic_no_replace_when_destination_arrives_after_final_check(self):
        rename = os.rename

        def late_destination(source, target):
            Path(target).write_bytes(b"late result")
            return rename(source, target)

        with patch("src.data_aggregator.os.rename", side_effect=late_destination):
            with self.assertRaises(AggregationOutputError):
                aggregate_dataset(self.spec(allow_output_overwrite=False))
        self.assertEqual(self.output.read_bytes(), b"late result")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_filename_field_rejects_paths_devices_and_unsupported_extensions(self):
        for name in ("../source.csv", r"..\source.csv", "C:\\source.csv", "file.csv:stream",
                     "CON.csv", "LPT1.xlsx", "file.csv.", "file.exe", "file"):
            with self.subTest(name=name):
                with self.assertRaises(AggregationOutputError):
                    validate_aggregation_output_name(name)
        validate_aggregation_output_name("거래선 요약.csv")

    def test_polish_notation_and_nonbreaking_space(self):
        self.write_rows(["1.234,56", "1 234,56", "1\u00a0234,56", "1\u202f234,56"])
        result = aggregate_dataset(self.spec(number_mode="Polish", allow_output_overwrite=False))
        self.assertAlmostEqual(pd.read_csv(result.out_path)["sales"].iloc[0], 4938.24)

    def test_wrong_notation_and_invalid_values_preserve_previous_output(self):
        for value in ("1.234,56", "1 234,56", "12,34", "NaN", "nan", "inf", "1e400", "(123)"):
            with self.subTest(value=value):
                self.write_rows(["100", value])
                original = self.source.read_bytes()
                self.output.write_bytes(b"previous good output")
                with self.assertRaises(AggregationNumericError) as caught:
                    aggregate_dataset(self.spec(chunksize=1))
                self.assertEqual(caught.exception.column, "sales")
                self.assertEqual(caught.exception.count, 1)
                self.assertTrue(caught.exception.examples)
                self.assertEqual(self.output.read_bytes(), b"previous good output")
                self.assertEqual(self.source.read_bytes(), original)

    def test_preview_checks_formula_only_inputs(self):
        spec = self.spec(measure_sums=[], derived_formulas=[DerivedFormulaRule("ratio", "sales", "sales")])
        frame, _ = preview_aggregation(spec)
        self.assertEqual(frame["ratio"].iloc[0], 1)
        self.write_rows(["1.234,56"])
        with self.assertRaises(AggregationNumericError):
            preview_aggregation(spec)
        with self.assertRaises(AggregationNumericError):
            aggregate_dataset(spec)

    def test_real_missing_values_are_distinct_from_nan_text(self):
        values, errors = _coerce_numeric_series(pd.Series([None, float("nan"), "", "-", "nan"]), "English")
        self.assertEqual(errors, 1)
        self.assertTrue(values.isna().all())
        self.write_rows(["100", "", "-"])
        spec = self.spec()
        frame, _ = preview_aggregation(spec)
        result = aggregate_dataset(spec)
        self.assertEqual(result.missing_numbers_by_column, {"sales": 2})
        self.assertEqual(frame.attrs["missing_numbers_by_column"], {"sales": 2})
        self.assertEqual(pd.read_csv(result.out_path)["sales"].iloc[0], 100)

    def test_numeric_grammar_scientific_and_mixed_separators(self):
        for mode, valid, invalid in (
            ("English", ["1,234.56", ".5", "-1.2e3"], ["1,23", "1,234e3", "1.234,56"]),
            ("Polish", ["1.234,56", ",5", "-1,2e3"], ["1.23", "1.234 567,8", "1.234e3"]),
        ):
            with self.subTest(mode=mode):
                _, errors = _coerce_numeric_series(pd.Series(valid), mode)
                self.assertEqual(errors, 0)
                _, errors = _coerce_numeric_series(pd.Series(invalid), mode)
                self.assertEqual(errors, len(invalid))

    def test_overflow_in_aggregation_or_formula_blocks_both_paths(self):
        for values, multiplier in ((["1e308", "1e308"], 1), (["1", "1"], 1e309)):
            with self.subTest(values=values, multiplier=multiplier):
                self.write_rows(values)
                spec = self.spec(derived_formulas=[DerivedFormulaRule("ratio", "sales", "sales", multiplier)])
                self.output.write_bytes(b"previous output")
                with self.assertRaises(AggregatorError):
                    preview_aggregation(spec)
                with self.assertRaises(AggregatorError):
                    aggregate_dataset(spec)
                self.assertEqual(self.output.read_bytes(), b"previous output")


if __name__ == "__main__":
    unittest.main()
