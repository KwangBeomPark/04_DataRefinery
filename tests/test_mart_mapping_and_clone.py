import csv
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import duckdb

from src.mart import Dimension, MartEngine, MartModel, Measure, WorkspaceStore
from src.mart.mapping import (
    AccountMappingEntry,
    detect_unmapped_measures,
    export_mapping_file,
    import_mapping_file,
    suggest_mapping_entries,
    validate_mapping_entries,
)


class MartMappingAndCloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkspaceStore(self.root / "ws_src")
        self.store.create()
        self.engine = MartEngine(self.store, preflight=lambda *_: {"allowed": True})
        self.columns = [
            "매출수량[400001]",
            "매출액[410000]",
            "원재료비(변동)[510001]",
            "감가상각비(고정)[520001]",
            "매출원가계[500000]",
            "영업이익[600000]",
        ]
        self.model = MartModel(
            name="ProfitModel",
            fact_table="profit_fact",
            dimensions=(
                Dimension("customer", ("cust_cd",), ("cust_nm",)),
                Dimension("model", ("model_cd",), ()),
            ),
            measures=tuple(
                Measure(f"m_{idx}", col, 18, 2, "KRW", kind="value")
                for idx, col in enumerate(self.columns, start=1)
            ),
            period_column="YYYYMM",
            partition_columns=(),
            row_key_columns=("YYYYMM", "cust_cd", "model_cd"),
        )

    def _write_sample_csv(self, filename: str, period: str) -> Path:
        path = self.root / filename
        fieldnames = ["YYYYMM", "cust_cd", "cust_nm", "model_cd", *self.columns]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "YYYYMM": period,
                    "cust_cd": "C001",
                    "cust_nm": "Alpha Corp",
                    "model_cd": "M001",
                    "매출수량[400001]": "10",
                    "매출액[410000]": "1000.00",
                    "원재료비(변동)[510001]": "400.00",
                    "감가상각비(고정)[520001]": "100.00",
                    "매출원가계[500000]": "500.00",
                    "영업이익[600000]": "500.00",
                }
            )
        return path

    def test_suggest_mapping_entries_classification_and_preserve(self):
        suggested = suggest_mapping_entries(self.model)
        by_col = {e.source_column: e for e in suggested}

        self.assertEqual(by_col["매출수량[400001]"].measure_type, "non_monetary")
        self.assertEqual(by_col["매출수량[400001]"].account_code, "400001")
        self.assertEqual(by_col["매출수량[400001]"].display_name, "매출수량")

        self.assertEqual(by_col["매출액[410000]"].measure_type, "amount")
        self.assertEqual(by_col["매출액[410000]"].major_category, "매출")
        self.assertEqual(by_col["매출액[410000]"].sign, 1)

        self.assertEqual(by_col["원재료비(변동)[510001]"].measure_type, "amount")
        self.assertEqual(by_col["원재료비(변동)[510001]"].major_category, "매출원가")
        self.assertEqual(by_col["원재료비(변동)[510001]"].medium_category, "변동비")
        self.assertEqual(by_col["원재료비(변동)[510001]"].sign, -1)

        self.assertEqual(by_col["감가상각비(고정)[520001]"].measure_type, "amount")
        self.assertEqual(by_col["감가상각비(고정)[520001]"].major_category, "매출원가")
        self.assertEqual(by_col["감가상각비(고정)[520001]"].medium_category, "고정비")
        self.assertEqual(by_col["감가상각비(고정)[520001]"].sign, -1)

        self.assertEqual(by_col["매출원가계[500000]"].measure_type, "subtotal_excluded")
        self.assertEqual(by_col["영업이익[600000]"].measure_type, "subtotal_excluded")

        custom = AccountMappingEntry(
            source_column="매출액[410000]",
            measure_type="amount",
            account_code="410000",
            display_name="제품매출액",
            major_category="영업수익",
            medium_category="제품",
            minor_category="국내",
            sign=1,
            sort_order=99,
        )
        merged = {e.source_column: e for e in suggest_mapping_entries(self.model, [custom])}
        self.assertEqual(merged["매출액[410000]"].major_category, "영업수익")
        self.assertEqual(merged["매출액[410000]"].display_name, "제품매출액")
        self.assertEqual(merged["매출액[410000]"].sort_order, 99)

    def test_xlsx_and_csv_roundtrip_and_invalid_row_error(self):
        entries = suggest_mapping_entries(self.model)
        xlsx_path = self.root / "mapping.xlsx"
        csv_path = self.root / "mapping.csv"

        export_mapping_file(xlsx_path, entries)
        export_mapping_file(csv_path, entries)

        loaded_xlsx = import_mapping_file(xlsx_path)
        loaded_csv = import_mapping_file(csv_path)
        self.assertEqual(
            [(e.source_column, e.measure_type, e.major_category, e.sign) for e in loaded_xlsx],
            [(e.source_column, e.measure_type, e.major_category, e.sign) for e in entries],
        )
        self.assertEqual(
            [(e.source_column, e.measure_type, e.major_category, e.sign) for e in loaded_csv],
            [(e.source_column, e.measure_type, e.major_category, e.sign) for e in entries],
        )

        bad_csv = self.root / "bad_mapping.csv"
        bad_csv.write_text(
            "source_column,measure_type,account_code,display_name,major_category,medium_category,minor_category,sign,sort_order\n"
            "매출액[410000],invalid_type,410000,매출액,매출,매출,,1,1\n",
            encoding="utf-8-sig",
        )
        with self.assertRaisesRegex(ValueError, "Row 2"):
            import_mapping_file(bad_csv)

        bad_sign_csv = self.root / "bad_sign.csv"
        bad_sign_csv.write_text(
            "source_column,measure_type,account_code,display_name,major_category,medium_category,minor_category,sign,sort_order\n"
            "매출액[410000],amount,410000,매출액,매출,매출,,0,1\n",
            encoding="utf-8-sig",
        )
        with self.assertRaisesRegex(ValueError, "Row 2"):
            import_mapping_file(bad_sign_csv)

    def test_validation_and_unmapped_detection(self):
        with self.assertRaises(ValueError):
            validate_mapping_entries(
                [
                    AccountMappingEntry("매출액[410000]", major_category="매출"),
                    AccountMappingEntry("매출액[410000]", major_category="매출"),
                ]
            )
        partial = [
            AccountMappingEntry("매출액[410000]", measure_type="amount", major_category="매출"),
            AccountMappingEntry("원재료비(변동)[510001]", measure_type="amount", major_category=""),
        ]
        unmapped = detect_unmapped_measures(self.model, partial)
        self.assertIn("원재료비(변동)[510001]", unmapped)
        self.assertIn("매출수량[400001]", unmapped)
        self.assertNotIn("매출액[410000]", unmapped)

    def test_save_and_load_account_mapping_in_duckdb_and_clone_blueprint(self):
        (self.store.root / "model.json").write_text(
            json.dumps(asdict(self.model), ensure_ascii=False),
            encoding="utf-8",
        )
        csv_jul = self._write_sample_csv("jul.csv", "202607")
        self.engine.load_snapshot(self.model, [csv_jul], {"YYYYMM": "202607"}, [csv_jul.name], complete=True)

        entries = suggest_mapping_entries(self.model)
        summary = self.engine.save_account_mapping(entries)
        self.assertEqual(summary["saved"], len(entries))
        self.assertEqual(summary["unmapped"], [])
        self.assertTrue((self.store.root / "mappings.json").is_file())

        # Verify persisted directly in DuckDB table
        conn = duckdb.connect(str(self.store.database), read_only=True)
        try:
            db_count = conn.execute("SELECT COUNT(*) FROM mart_account_mapping").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(db_count, len(entries))

        loaded = self.engine.load_account_mapping()
        self.assertEqual([e.source_column for e in loaded], [e.source_column for e in entries])

        # Prepare recipes.json and ui.json + a dummy export file to confirm exports are NOT cloned
        (self.store.root / "recipes.json").write_text(
            json.dumps({"version": 1, "recipes": [{"name": "summary", "mode": "wide_rollup"}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.store.root / "ui.json").write_text(
            json.dumps(
                {
                    "budget_gib": 5.0,
                    "warning_active": True,
                    "default_export_dir": "C:/exports",
                    "sharepoint": {"site": "https://example.invalid/sites/fin"},
                    "destinations": {"cand123": {"local_path": "C:/old.csv"}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.store.root / "exports" / "cand123").mkdir(parents=True)
        (self.store.root / "exports" / "cand123" / "data.csv").write_text("x\n1\n", encoding="utf-8")

        dest_dir = self.root / "ws_cloned"
        clone_res = self.engine.clone_blueprint(dest_dir)
        self.assertEqual(
            set(clone_res["copied"]),
            {"model.json", "mappings.json", "recipes.json", "ui.json"},
        )
        self.assertFalse((dest_dir / "mart.duckdb").exists())
        self.assertFalse((dest_dir / "exports").exists())

        src_ws_id = WorkspaceStore(self.store.root).validate()["id"]
        cloned_ws_id = WorkspaceStore(dest_dir).validate()["id"]
        self.assertNotEqual(src_ws_id, cloned_ws_id)

        cloned_ui = json.loads((dest_dir / "ui.json").read_text(encoding="utf-8"))
        self.assertEqual(cloned_ui["budget_gib"], 5.0)
        self.assertEqual(cloned_ui["default_export_dir"], "C:/exports")
        self.assertNotIn("destinations", cloned_ui)
        self.assertNotIn("warning_active", cloned_ui)

        # Load a new month into cloned workspace -> mart_account_mapping seeds automatically
        cloned_engine = MartEngine(WorkspaceStore(dest_dir), preflight=lambda *_: {"allowed": True})
        csv_aug = self._write_sample_csv("aug.csv", "202608")
        cloned_engine.load_snapshot(self.model, [csv_aug], {"YYYYMM": "202608"}, [csv_aug.name], complete=True)
        conn2 = duckdb.connect(str(dest_dir / "mart.duckdb"), read_only=True)
        try:
            cloned_map_count = conn2.execute("SELECT COUNT(*) FROM mart_account_mapping").fetchone()[0]
        finally:
            conn2.close()
        self.assertEqual(cloned_map_count, len(entries))


if __name__ == "__main__":
    unittest.main()
