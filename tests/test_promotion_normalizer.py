import csv
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from src.promotion_normalizer import (
    DAILY_SUPPORT_COLUMNS,
    EXCEL_MAX_DATA_ROWS,
    PROMOTION_MASTER_COLUMNS,
    SUPPORT_RULE_COLUMNS,
    PromotionTemplateData,
    export_normalized,
    load_template,
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

    def test_removes_published_and_staged_outputs_when_second_publish_replace_fails(self):
        masters, rules = self._valid_records()
        data, issues = validate_records(masters, rules)
        self.assertFalse(issues)

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            source = directory_path / "promotion_input.xlsx"
            source_content = b"original source template content"
            source.write_bytes(source_content)

            sentinel = directory_path / "unrelated_sentinel.txt"
            sentinel_content = "sentinel content must remain untouched"
            sentinel.write_text(sentinel_content, encoding="utf-8")

            original_replace = Path.replace
            replace_calls = 0
            first_target = None
            first_target_existed_before_failure = False

            def fake_replace(self_path, target, *args, **kwargs):
                nonlocal replace_calls, first_target, first_target_existed_before_failure
                replace_calls += 1
                if replace_calls == 1:
                    first_target = Path(target)
                    result = original_replace(self_path, target, *args, **kwargs)
                    first_target_existed_before_failure = first_target.exists()
                    return result
                if replace_calls == 2:
                    raise OSError("simulated publish failure on second replace")
                return original_replace(self_path, target, *args, **kwargs)

            with patch.object(Path, "replace", new=fake_replace):
                with self.assertRaisesRegex(OSError, "simulated publish failure on second replace"):
                    export_normalized(data, source, timestamp=datetime(2030, 1, 2, 3, 4))

            self.assertEqual(replace_calls, 2)
            self.assertTrue(first_target_existed_before_failure)
            self.assertIsNotNone(first_target)
            self.assertFalse(first_target.exists())
            self.assertTrue(source.exists())
            self.assertEqual(source.read_bytes(), source_content)
            self.assertTrue(sentinel.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), sentinel_content)
            self.assertEqual(list(directory_path.glob("promotion_*")), [source])
            self.assertEqual(list(directory_path.glob(".*.tmp")), [])
            self.assertEqual(sorted(p.name for p in directory_path.iterdir()), sorted([source.name, sentinel.name]))

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

    def _create_template_file(
        self,
        path: Path,
        master_headers: list,
        master_rows: list,
        rules_headers: list,
        rules_rows: list,
    ) -> None:
        import openpyxl

        workbook = openpyxl.Workbook()
        ws_master = workbook.active
        ws_master.title = "Promotion_Master"
        ws_master.append(master_headers)
        for row in master_rows:
            ws_master.append(row)

        ws_rules = workbook.create_sheet("Support_Rules")
        ws_rules.append(rules_headers)
        for row in rules_rows:
            ws_rules.append(row)

        workbook.save(path)

    def test_rejects_duplicate_required_headers_in_promotion_master(self):
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "duplicate_master.xlsx"
            self._create_template_file(
                template_path,
                list(PROMOTION_MASTER_COLUMNS) + ["promotion_id"],
                [["PROMO-001", "January support", "Dealer", "Cash", "", "PROMO-002"]],
                list(SUPPORT_RULE_COLUMNS),
                [["RULE-001", "PROMO-002", "MODEL-A", "2026-01-03", "2026-01-07", "100.00", "PLN"]],
            )
            data, issues = load_template(template_path)
            self.assertIsNone(data)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].sheet, "Promotion_Master")
            self.assertEqual(issues[0].row, 1)
            self.assertEqual(issues[0].column, "promotion_id")
            self.assertIn("Duplicate column header", issues[0].message)

    def test_rejects_duplicate_required_headers_in_support_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "duplicate_rules.xlsx"
            self._create_template_file(
                template_path,
                list(PROMOTION_MASTER_COLUMNS),
                [["PROMO-001", "January support", "Dealer", "Cash", ""]],
                list(SUPPORT_RULE_COLUMNS) + ["support_rule_id"],
                [["RULE-001", "PROMO-001", "MODEL-A", "2026-01-03", "2026-01-07", "100.00", "PLN", "RULE-002"]],
            )
            data, issues = load_template(template_path)
            self.assertIsNone(data)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].sheet, "Support_Rules")
            self.assertEqual(issues[0].row, 1)
            self.assertEqual(issues[0].column, "support_rule_id")
            self.assertIn("Duplicate column header", issues[0].message)

    def test_loads_valid_template_with_extra_blank_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "valid_template.xlsx"
            self._create_template_file(
                template_path,
                list(PROMOTION_MASTER_COLUMNS) + ["", None, "   "],
                [["PROMO-001", "January support", "Dealer", "Cash", "", None, "", None]],
                list(SUPPORT_RULE_COLUMNS) + [None, ""],
                [["RULE-001", "PROMO-001", "MODEL-A", "2026-01-03", "2026-01-07", "100.00", "PLN", None, None]],
            )
            data, issues = load_template(template_path)
            self.assertFalse(issues)
            self.assertIsNotNone(data)
            self.assertEqual(len(data.master_rows), 1)
            self.assertEqual(data.master_rows[0]["promotion_id"], "PROMO-001")
            self.assertEqual(len(data.support_rules), 1)
            self.assertEqual(data.support_rules[0]["support_rule_id"], "RULE-001")
            self.assertEqual(data.estimated_daily_rows, 5)
