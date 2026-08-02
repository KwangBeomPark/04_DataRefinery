import csv
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from src.promotion_normalizer import (
    DAILY_SUPPORT_COLUMNS,
    EXCEL_MAX_DATA_ROWS,
    PromotionTemplateData,
    export_normalized,
    preview_daily_rows,
    validate_records,
)


class TestPromotionNormalizer(unittest.TestCase):
    def _valid_records(self):
        masters = [{
            "promotion_id": "PROMO-001",
            "promotion_name": "January support",
            "channel": "Dealer",
            "promotion_type": "Cash",
            "notes": "",
        }]
        rules = [{
            "support_rule_id": "RULE-001",
            "promotion_id": "PROMO-001",
            "model_code": "MODEL-A",
            "start_date": "2026-01-03",
            "end_date": "2026-01-07",
            "support_per_unit": "100.00",
            "currency": "PLN",
        }]
        return masters, rules

    def test_expands_an_inclusive_five_day_range_and_keeps_compact_outputs(self):
        masters, rules = self._valid_records()
        data, issues = validate_records(masters, rules)
        self.assertFalse(issues)
        self.assertEqual(data.estimated_daily_rows, 5)
        preview = preview_daily_rows(data)
        self.assertEqual(len(preview), 5)
        self.assertEqual(preview[0]["applied_date"], "2026-01-03")
        self.assertEqual(preview[-1]["applied_date"], "2026-01-07")
        self.assertEqual(preview[0]["support_per_unit"], "100.00")

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "promotion_input.xlsx"
            source.touch()
            progress = []
            result = export_normalized(
                data,
                source,
                timestamp=datetime(2030, 1, 2, 3, 4),
                progress=lambda percent, event: progress.append((percent, event)),
            )
            self.assertTrue(result.master_path.exists())
            self.assertTrue(result.rules_path.exists())
            with result.daily_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(tuple(rows[0]), DAILY_SUPPORT_COLUMNS)
        self.assertEqual(len(rows), 5)
        self.assertEqual(progress[0], (10, "compact"))
        self.assertIn((25, "daily"), progress)
        self.assertEqual(progress[-1], (95, "publishing"))

    def test_removes_all_staged_outputs_when_daily_export_fails(self):
        masters, rules = self._valid_records()
        data, issues = validate_records(masters, rules)
        self.assertFalse(issues)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "promotion_input.xlsx"
            source.touch()
            with patch("src.promotion_normalizer._write_daily_csv", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    export_normalized(data, source, timestamp=datetime(2030, 1, 2, 3, 4))

            self.assertEqual(list(Path(directory).glob("promotion_*")), [source])
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_reports_duplicate_ids_missing_master_and_invalid_dates(self):
        masters, rules = self._valid_records()
        masters.append({**masters[0], "promotion_name": "Duplicate"})
        rules.append({
            "support_rule_id": "RULE-002",
            "promotion_id": "MISSING",
            "model_code": "MODEL-A",
            "start_date": "2026-02-08",
            "end_date": "2026-02-01",
            "support_per_unit": "not-a-number",
            "currency": "",
        })
        data, issues = validate_records(masters, rules)
        self.assertIsNone(data)
        messages = "\n".join(issue.message for issue in issues)
        self.assertIn("must be unique", messages)
        self.assertIn("not in Promotion_Master", messages)
        self.assertIn("End date", messages)
        self.assertIn("non-negative", messages)

    def test_keeps_overlapping_rules_as_separate_daily_rows(self):
        masters, rules = self._valid_records()
        rules.append({
            "support_rule_id": "RULE-002",
            "promotion_id": "PROMO-001",
            "model_code": "MODEL-A",
            "start_date": "2026-01-05",
            "end_date": "2026-01-08",
            "support_per_unit": "50",
            "currency": "PLN",
        })
        data, issues = validate_records(masters, rules)
        self.assertFalse(issues)
        self.assertEqual(data.overlapping_rule_pairs, 1)
        self.assertEqual(data.estimated_daily_rows, 9)

    def test_rejects_excel_output_beyond_excel_row_limit(self):
        data = PromotionTemplateData(
            master_rows=({"promotion_id": "P", "promotion_name": "P", "channel": "", "promotion_type": "", "notes": ""},),
            support_rules=({
                "support_rule_id": "R",
                "promotion_id": "P",
                "model_code": "M",
                "start_date": date(1, 1, 1),
                "end_date": date(9999, 12, 31),
                "support_per_unit": __import__("decimal").Decimal("1"),
                "currency": "USD",
            },),
            estimated_daily_rows=EXCEL_MAX_DATA_ROWS + 1,
            overlapping_rule_pairs=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "promotion_input.xlsx"
            source.touch()
            with self.assertRaisesRegex(ValueError, "Excel"):
                export_normalized(data, source, daily_format="Excel (.xlsx)")
