import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.mart import (
    AccountMappingEntry,
    Dimension,
    MartEngine,
    MartModel,
    MartRecipe,
    Measure,
    WorkspaceStore,
)
from src.mart.recipes import build_default_recipes


class MartRecipesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkspaceStore(self.root / "ws")
        self.store.create()
        self.engine = MartEngine(self.store, preflight=lambda *_: {"allowed": True})

        self.measure_specs = [
            ("매출수량[400001]", 18, 2, "non_monetary", "수량/비금액", "비금액지표", 1),
            ("매출액[410000]", 18, 2, "amount", "매출", "매출", 1),
            ("원재료비(변동)[510001]", 18, 2, "amount", "매출원가", "변동비", -1),
            ("감가상각비(고정)[520001]", 18, 2, "amount", "매출원가", "고정비", -1),
            ("미세조정비[530001]", 28, 15, "amount", "판관비", "판관비", -1),
            ("매출원가계[500000]", 18, 2, "subtotal_excluded", "원본소계(제외)", "원본집계/손익(제외)", 1),
        ]
        self.model = MartModel(
            name="Profit",
            fact_table="profit",
            dimensions=(
                Dimension("customer", ("cust_cd",), ("cust_nm",)),
                Dimension("model", ("model_cd",), ()),
            ),
            measures=tuple(
                Measure(f"m_{i}", col, prec, scale, "KRW", kind="value")
                for i, (col, prec, scale, *_rest) in enumerate(self.measure_specs, start=1)
            ),
            period_column="YYYYMM",
            partition_columns=(),
            row_key_columns=("YYYYMM", "cust_cd", "model_cd"),
        )
        self.csv_path = self.root / "profit_202607.csv"
        headers = ["YYYYMM", "cust_cd", "cust_nm", "model_cd", *(s[0] for s in self.measure_specs)]
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerow(
                {
                    "YYYYMM": "202607",
                    "cust_cd": "C01",
                    "cust_nm": "Acme",
                    "model_cd": "M1",
                    "매출수량[400001]": "10.00",
                    "매출액[410000]": "1000.00",
                    "원재료비(변동)[510001]": "400.00",
                    "감가상각비(고정)[520001]": "0.00",
                    "미세조정비[530001]": "5.95E-11",
                    "매출원가계[500000]": "400.00",
                }
            )
            writer.writerow(
                {
                    "YYYYMM": "202607",
                    "cust_cd": "C02",
                    "cust_nm": "Beta",
                    "model_cd": "M2",
                    "매출수량[400001]": "5.00",
                    "매출액[410000]": "500.00",
                    "원재료비(변동)[510001]": "0.00",
                    "감가상각비(고정)[520001]": "100.00",
                    "미세조정비[530001]": "0",
                    "매출원가계[500000]": "100.00",
                }
            )

        self.engine.load_snapshot(
            self.model,
            [self.csv_path],
            {"YYYYMM": "202607"},
            [self.csv_path.name],
            complete=True,
        )
        self.entries = [
            AccountMappingEntry(
                source_column=col,
                measure_type=mtype,
                account_code=col.split("[")[1].rstrip("]"),
                display_name=col.split("[")[0],
                major_category=major,
                medium_category=med,
                minor_category="",
                sign=sign,
                sort_order=idx,
            )
            for idx, (col, _p, _s, mtype, major, med, sign) in enumerate(self.measure_specs, start=1)
        ]

    def test_wide_rollup_excludes_subtotals_and_reconciles_exact_decimal(self):
        self.engine.save_account_mapping(self.entries)
        recipe = MartRecipe(
            name="손익_대분류_요약",
            mode="wide_rollup",
            rollup_level="major",
            group_by=("YYYYMM", "cust_cd"),
            include_non_monetary=("매출수량[400001]",),
            apply_sign=False,
        )
        manifest = self.engine.create_recipe_export(recipe)
        recon = manifest["recipe_reconciliation"]
        self.assertTrue(recon["matches"])
        self.assertEqual(recon["diff"], "0")
        # 매출(1500) + 매출원가(400+100=500, subtotal 500 excluded!) + 판관비(0.0000000000595)
        self.assertEqual(Decimal(recon["total_source_amount"]), Decimal("2000.0000000000595"))
        self.assertEqual(Decimal(recon["total_exported_amount"]), Decimal("2000.0000000000595"))
        self.assertEqual(recon["excluded_subtotals"], ["매출원가계[500000]"])

        out_csv = self.store.root / "exports" / manifest["candidate"] / "data.csv"
        with out_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        c01 = next(r for r in rows if r["cust_cd"] == "C01")
        self.assertEqual(Decimal(c01["매출"]), Decimal("1000"))
        self.assertEqual(Decimal(c01["매출원가"]), Decimal("400"))
        self.assertEqual(c01["판관비"], "0.000000000059500")
        self.assertEqual(Decimal(c01["매출수량[400001]"]), Decimal("10"))

        # Candidate works seamlessly with approval & local publish
        self.engine.approve_export(manifest["candidate"], "controller")
        published = self.root / "published" / "summary.csv"
        res = self.engine.publish_local(manifest["candidate"], published)
        self.assertTrue(published.is_file())
        self.assertEqual(res["sha256"], manifest["sha256"])

    def test_long_detail_exclude_zero_apply_sign_and_category_filter(self):
        self.engine.save_account_mapping(self.entries)
        recipe = MartRecipe(
            name="원가_상세_0원제외",
            mode="long_detail",
            group_by=("YYYYMM", "cust_cd"),
            major_categories=("매출원가",),
            exclude_zero=True,
            apply_sign=True,
        )
        manifest = self.engine.create_recipe_export(recipe)
        recon = manifest["recipe_reconciliation"]
        self.assertTrue(recon["matches"])
        self.assertEqual(recon["diff"], "0")
        # 2 cust rows x 2 cost accounts = 4 cells, 2 are 0 -> 2 rows written, 2 filtered
        self.assertEqual(manifest["rows"], 2)
        self.assertEqual(recon["filtered_zero_rows"], 2)
        self.assertEqual(Decimal(recon["total_exported_amount"]), Decimal("-500"))

        out_csv = self.store.root / "exports" / manifest["candidate"] / "data.csv"
        with out_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(Decimal(r["amount"]) != 0 for r in rows))

        # Tiny non-zero decimal is never treated as zero or converted to E-11
        all_long = MartRecipe(
            name="전체_상세_0원제외",
            mode="long_detail",
            group_by=("YYYYMM", "cust_cd"),
            exclude_zero=True,
            apply_sign=False,
        )
        manifest_all = self.engine.create_recipe_export(all_long)
        out_all = self.store.root / "exports" / manifest_all["candidate"] / "data.csv"
        with out_all.open("r", encoding="utf-8-sig", newline="") as handle:
            rows_all = list(csv.DictReader(handle))
        tiny_rows = [r for r in rows_all if r["source_column"] == "미세조정비[530001]"]
        self.assertEqual(len(tiny_rows), 1)
        self.assertEqual(tiny_rows[0]["amount"], "0.000000000059500")
        self.assertNotIn("E", tiny_rows[0]["amount"].upper())
        self.assertTrue(manifest_all["recipe_reconciliation"]["matches"])

    def test_unmapped_policy_fail_vs_other_bucket_and_recipes_persistence(self):
        # Save mapping omitting '미세조정비[530001]'
        partial = [e for e in self.entries if e.source_column != "미세조정비[530001]"]
        save_res = self.engine.save_account_mapping(partial)
        self.assertEqual(save_res["unmapped"], ["미세조정비[530001]"])

        with self.assertRaisesRegex(ValueError, "Unmapped measures"):
            self.engine.create_recipe_export(MartRecipe(name="strict", mode="wide_rollup", unmapped_policy="fail"))

        manifest = self.engine.create_recipe_export(
            MartRecipe(name="allow_other", mode="wide_rollup", unmapped_policy="other_bucket")
        )
        recon = manifest["recipe_reconciliation"]
        self.assertTrue(recon["matches"])
        self.assertEqual(recon["unmapped_columns"], ["미세조정비[530001]"])

        defaults = build_default_recipes(self.model, self.entries)
        self.assertEqual([r.mode for r in defaults], ["wide_rollup", "long_detail", "wide_raw"])
        self.engine.save_recipes(defaults)
        reloaded = self.engine.load_recipes()
        self.assertEqual([r.name for r in reloaded], [r.name for r in defaults])

        raw_manifest = self.engine.create_recipe_export(defaults[2])
        self.assertTrue(raw_manifest["recipe_reconciliation"]["matches"])

    def test_wide_207_measures_long_unpivot_reconciles_exact(self):
        # Verify scale & performance with 207 numeric columns
        ws_dir = WorkspaceStore(self.root / "ws207")
        ws_dir.create()
        eng207 = MartEngine(ws_dir, preflight=lambda *_: {"allowed": True})
        measure_cols = [f"acct_{i:03d}" for i in range(207)]
        model207 = MartModel(
            name="Wide207",
            fact_table="wide207",
            dimensions=(Dimension("cust", ("cust_cd",), ()),),
            measures=tuple(Measure(f"m{i}", c, 18, 2, "KRW", kind="cost") for i, c in enumerate(measure_cols)),
            period_column="YYYYMM",
            row_key_columns=("YYYYMM", "cust_cd"),
        )
        csv207 = self.root / "w207.csv"
        with csv207.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["YYYYMM", "cust_cd", *measure_cols])
            writer.writerow(["202607", "C1", *["10.00" if i < 5 else "0" for i in range(207)]])
            writer.writerow(["202607", "C2", *["20.00" if i < 3 else "0" for i in range(207)]])
        eng207.load_snapshot(model207, [csv207], {"YYYYMM": "202607"}, [csv207.name], complete=True)
        entries207 = [
            AccountMappingEntry(
                source_column=c,
                measure_type="amount",
                major_category="원가" if i % 2 == 0 else "판관비",
                medium_category="변동비",
                sign=-1,
                sort_order=i + 1,
            )
            for i, c in enumerate(measure_cols)
        ]
        eng207.save_account_mapping(entries207)
        manifest = eng207.create_recipe_export(
            MartRecipe(name="long_207", mode="long_detail", group_by=("YYYYMM", "cust_cd"), exclude_zero=True)
        )
        recon = manifest["recipe_reconciliation"]
        self.assertTrue(recon["matches"])
        self.assertEqual(manifest["rows"], 8)
        self.assertEqual(recon["filtered_zero_rows"], 207 * 2 - 8)
        self.assertEqual(Decimal(recon["total_exported_amount"]), Decimal("110"))

    def test_recipe_exports_work_with_real_resource_preflight(self):
        from dataclasses import asdict
        from datetime import datetime, timezone

        import duckdb

        from src.mart.capacity import Measurement, ResourcePreflight

        self.engine.save_account_mapping(self.entries)
        now = datetime.now(timezone.utc)
        preflight = ResourcePreflight(
            reserve_bytes=0,
            space_probe=lambda target: Measurement(10 * 1024**3, str(target), "test", now),
            memory_probe=lambda: Measurement(8 * 1024**3, "memory", "test", now),
        )
        eng_real = MartEngine(self.store, preflight=preflight)

        rollup_manifest = eng_real.create_recipe_export(
            MartRecipe(name="real_rollup", mode="wide_rollup", group_by=("YYYYMM", "cust_cd"))
        )
        self.assertTrue(rollup_manifest["recipe_reconciliation"]["matches"])

        long_recipe = MartRecipe(
            name="real_long",
            mode="long_detail",
            group_by=("YYYYMM", "cust_cd"),
            exclude_zero=True,
        )
        detail_manifest = eng_real.create_recipe_export(long_recipe)
        self.assertTrue(detail_manifest["recipe_reconciliation"]["matches"])
        self.assertGreater(detail_manifest["recipe_reconciliation"]["filtered_zero_rows"], 0)

        # Direct preflight call with raw MartRecipe object also works cleanly
        db = duckdb.connect(str(self.store.database), read_only=True)
        try:
            direct = preflight(
                "export",
                self.store,
                self.store.database.stat().st_size,
                definition=long_recipe,
                model=asdict(self.model),
                destination=self.store.root / "exports",
                db=db,
            )
        finally:
            db.close()
        self.assertTrue(direct["allowed"])


if __name__ == "__main__":
    unittest.main()
