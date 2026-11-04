import csv
from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.mart import (
    Dimension,
    ExportDefinition,
    MartEngine,
    MartModel,
    Measure,
    WorkspaceStore,
)


class MartCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkspaceStore(self.root / "workspace")
        self.store.create()
        self.engine = MartEngine(self.store, preflight=lambda *_: {"allowed": True})
        self.model = MartModel(
            "Sales",
            "sales",
            (Dimension("customer", ("customer", "ship_to"), ("name",), "period"),),
            (Measure("amount", "amount", 10, 2, "USD", kind="cost"),),
            "period",
            ("entity",),
            ("invoice",),
        )

    def source(self, filename, rows):
        path = self.root / filename
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "period",
                    "entity",
                    "invoice",
                    "customer",
                    "ship_to",
                    "name",
                    "amount",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
        return path

    @staticmethod
    def row(period, invoice, name, amount="1.25"):
        return {
            "period": period,
            "entity": "A",
            "invoice": invoice,
            "customer": "001",
            "ship_to": "02",
            "name": name,
            "amount": amount,
        }

    def load(self, path, period):
        return self.engine.load_snapshot(
            self.model,
            [path],
            {"period": period, "entity": "A"},
            [path.name],
            complete=True,
        )

    def test_conflicting_column_roles_block_before_changes(self):
        source = self.source("data.csv", [self.row("2026-01", "1", "name")])
        self.load(source, "2026-01")
        for column in ("period", "entity", "invoice", "customer", "ship_to", "name"):
            with self.subTest(column=column):
                model = replace(self.model, measures=(Measure("numeric_code", column),))
                with self.assertRaisesRegex(ValueError, "measure source column"):
                    self.engine.load_snapshot(
                        model,
                        [source],
                        {"period": "2026-01", "entity": "A"},
                        [source.name],
                        complete=True,
                    )
        for model in (
            replace(
                self.model,
                measures=(Measure("one", "amount"), Measure("two", "amount")),
            ),
            replace(self.model, excluded_columns=("customer",)),
            replace(self.model, excluded_columns=("amount",)),
        ):
            with self.assertRaises(ValueError):
                self.engine._validate_model(model)
        shared = replace(
            self.model,
            dimensions=self.model.dimensions
            + (Dimension("other", ("customer",), ("ship_to",)),),
        )
        self.engine._validate_model(shared)
        self.assertEqual(self.engine.catalog()["revision"], 1)
        self.assertEqual(self.engine.sample()["rows"][0]["customer"], "001")
        db = self.engine._connect()
        try:
            self.assertEqual(db.execute("SELECT sk FROM dim_customer").fetchone()[0], 1)
        finally:
            db.close()

    def test_preexisting_invalid_model_and_candidate_are_blocked(self):
        model = replace(
            self.model,
            measures=(Measure("numeric_code", "customer"),),
            excluded_columns=("amount",),
        )
        source = self.source("data.csv", [self.row("2026-01", "1", "name")])
        with mock.patch("src.mart.model.validate_column_roles"):
            self.engine.load_snapshot(
                model,
                [source],
                {"period": "2026-01", "entity": "A"},
                [source.name],
                complete=True,
            )
            candidate = self.engine.create_export(
                ExportDefinition("bad", ("period", "customer"))
            )["candidate"]
            self.engine.approve_export(candidate, "reviewer")
        for action in (
            lambda: self.engine.sample(),
            lambda: self.engine.create_export(
                ExportDefinition("bad", ("period", "customer"))
            ),
            lambda: self.engine.reconcile(),
            lambda: self.engine.publish_local(candidate, self.root / "published.csv"),
        ):
            with self.assertRaisesRegex(ValueError, "measure source column"):
                action()
        publisher = mock.Mock()
        with self.assertRaisesRegex(ValueError, "measure source column"):
            self.engine.publish_sharepoint(candidate, publisher, "out.csv")
        publisher.resolve.assert_not_called()
        self.assertFalse((self.root / "published.csv").exists())
        self.assertEqual(self.engine.catalog()["revision"], 1)

    def test_period_validation_and_export_granularity(self):
        for value in ("202601", "2026-1", "2026-02-30"):
            source = self.source("invalid.csv", [self.row(value, "1", "name")])
            with self.assertRaises(ValueError):
                self.load(source, value)
            self.assertFalse(self.store.database.exists())
        source = self.source("day.csv", [self.row("2026-01-15", "1", "name")])
        self.load(source, "2026-01-15")
        month = self.source("month.csv", [self.row("2026-02", "2", "name")])
        with self.assertRaisesRegex(ValueError, "granularity"):
            self.load(month, "2026-02")
        with self.assertRaisesRegex(ValueError, "granularity"):
            self.engine.create_export(
                ExportDefinition(
                    "bad", ("period", "customer", "amount"), period_end="2026-01"
                )
            )
        with self.assertRaisesRegex(ValueError, "Invalid period"):
            self.engine.create_export(
                ExportDefinition(
                    "bad", ("period", "customer", "amount"), period_end="2026-02-30"
                )
            )
        candidate = self.engine.create_export(
            ExportDefinition(
                "ok", ("period", "customer", "amount"), period_end="2026-01-31"
            )
        )
        self.assertEqual(candidate["rows"], 1)
        self.assertEqual(self.engine.catalog()["revision"], 1)

    def test_empty_database_reports_model_not_loaded(self):
        db = self.engine._connect(create=True)
        db.close()
        for action in (self.engine.sample, self.engine.reconcile):
            with self.assertRaisesRegex(ValueError, "No model has been loaded"):
                action()

    def test_publish_missing_database_does_not_create_database(self):
        source = self.source("day.csv", [self.row("2026-01", "1", "name")])
        self.load(source, "2026-01")
        candidate = self.engine.create_export(
            ExportDefinition("out", ("period", "customer", "amount"))
        )["candidate"]
        self.engine.approve_export(candidate, "reviewer")
        saved = self.root / "fixture-db-saved.duckdb"
        self.store.database.rename(saved)
        with self.assertRaises(FileNotFoundError):
            self.engine.publish_local(candidate, self.root / "out.csv")
        self.assertFalse(self.store.database.exists())
        self.assertTrue(saved.exists())

    def test_approved_export_callback_never_runs_for_unapproved_stale_or_tampered(self):
        source = self.source("one.csv", [self.row("2026-01", "1", "name")])
        self.load(source, "2026-01")
        candidate = self.engine.create_export(
            ExportDefinition("out", ("period", "customer", "amount"))
        )["candidate"]
        callback = mock.Mock()
        with self.assertRaises(FileNotFoundError):
            self.engine.with_approved_export(candidate, callback)
        callback.assert_not_called()
        self.engine.approve_export(candidate, "reviewer")
        self.engine.with_approved_export(candidate, callback)
        callback.assert_called_once()
        callback.reset_mock()
        other = self.source("two.csv", [self.row("2026-02", "2", "name")])
        self.load(other, "2026-02")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.engine.with_approved_export(candidate, callback)
        callback.assert_not_called()
        fresh = self.engine.create_export(
            ExportDefinition("out", ("period", "customer", "amount"))
        )["candidate"]
        self.engine.approve_export(fresh, "reviewer")
        (self.store.root / "exports" / fresh / "data.csv").write_text(
            "tampered", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.engine.with_approved_export(fresh, callback)
        callback.assert_not_called()

    def test_invalid_legacy_period_blocks_reads_and_publication(self):
        source = self.source("legacy.csv", [self.row("2026-01", "1", "name")])
        self.load(source, "2026-01")
        candidate = self.engine.create_export(
            ExportDefinition("out", ("period", "customer", "amount"))
        )["candidate"]
        self.engine.approve_export(candidate, "reviewer")
        db = self.engine._connect(create=True)
        try:
            db.execute(
                "UPDATE mart_scopes SET scope=?",
                [json.dumps({"period": "202601", "entity": "A"})],
            )
        finally:
            db.close()
        for action in (
            self.engine.catalog,
            self.engine.sample,
            self.engine.reconcile,
            lambda: self.engine.create_export(
                ExportDefinition("out", ("period", "customer", "amount"))
            ),
            lambda: self.engine.publish_local(candidate, self.root / "out.csv"),
        ):
            with self.assertRaisesRegex(ValueError, "Period must"):
                action()
        callback = mock.Mock()
        with self.assertRaisesRegex(ValueError, "Period must"):
            self.engine.with_approved_export(candidate, callback)
        callback.assert_not_called()

    def test_dimension_natural_key_wins_over_other_dimension_attributes(self):
        dims = (
            Dimension("region", ("region",)),
            Dimension("store", ("store",), ("region",), "period"),
        )
        for index, ordered in enumerate((dims, tuple(reversed(dims)))):
            with self.subTest(order=index):
                store = WorkspaceStore(self.root / ("ordered-" + str(index)))
                store.create()
                engine = MartEngine(store, preflight=lambda *_: {"allowed": True})
                model = MartModel(
                    "M",
                    "sales",
                    ordered,
                    (Measure("amount", "amount"),),
                    "period",
                    (),
                    ("invoice",),
                )
                for month, region, amount in [
                    ("2026-01", "R1", "1.25"),
                    ("2026-02", "R2", "2.50"),
                ]:
                    source = self.root / ("input-" + str(index) + ".csv")
                    with source.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.writer(handle)
                        writer.writerow(
                            ["period", "invoice", "region", "store", "amount"]
                        )
                        writer.writerow([month, month, region, "S1", amount])
                    engine.load_snapshot(
                        model, [source], {"period": month}, [source.name], complete=True
                    )
                db = engine._connect()
                try:
                    self.assertEqual(
                        db.execute("SELECT region FROM dim_store").fetchone()[0], "R2"
                    )
                finally:
                    db.close()
                self.assertEqual(
                    [(r["period"], r["region"]) for r in engine.sample()["rows"]],
                    [("2026-01", "R1"), ("2026-02", "R2")],
                )
                definition = ExportDefinition(
                    "group",
                    ("region", "amount"),
                    group_by=("region",),
                    filters=(("region", "R1"),),
                )
                result = engine.create_export(definition)
                with (store.root / "exports" / result["candidate"] / "data.csv").open(
                    encoding="utf-8-sig", newline=""
                ) as handle:
                    self.assertEqual(
                        list(csv.DictReader(handle)),
                        [{"region": "R1", "amount": "1.25"}],
                    )

    def test_duplicate_attributes_use_first_declared_dimension_consistently(self):
        dimensions = (
            Dimension("customer", ("customer",), ("region",), "period"),
            Dimension("store", ("store",), ("region",), "period"),
        )
        for index, ordered in enumerate((dimensions, tuple(reversed(dimensions)))):
            store = WorkspaceStore(self.root / ("attribute-" + str(index)))
            store.create()
            engine = MartEngine(store, preflight=lambda *_: {"allowed": True})
            model = MartModel(
                "M",
                "sales",
                ordered,
                (Measure("amount", "amount"),),
                "period",
                (),
                ("invoice",),
            )
            source = self.root / ("attribute-input-" + str(index) + ".csv")
            for month, location, region, amount in [
                ("2026-01", "S1", "R1", "1.25"),
                ("2026-02", "S2", "R2", "2.50"),
            ]:
                with source.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(
                        ["period", "invoice", "customer", "store", "region", "amount"]
                    )
                    writer.writerow([month, month, "C1", location, region, amount])
                engine.load_snapshot(
                    model, [source], {"period": month}, [source.name], complete=True
                )
            expected = "R2" if index == 0 else "R1"
            self.assertEqual(engine.sample()["rows"][0]["region"], expected)
            result = engine.create_export(
                ExportDefinition(
                    "out", ("period", "region", "amount"), period_end="2026-01"
                )
            )
            with (store.root / "exports" / result["candidate"] / "data.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                self.assertEqual(list(csv.DictReader(handle))[0]["region"], expected)

    def test_fact_period_partition_and_grain_survive_type1_changes(self):
        model = replace(
            self.model,
            dimensions=(
                Dimension(
                    "customer", ("customer",), ("period", "entity", "invoice"), "period"
                ),
                Dimension("month", ("period",)),
            ),
        )
        first = self.source("jan.csv", [self.row("2026-01", "i1", "unused", "1.25")])
        # The model intentionally excludes an otherwise unused source column.
        model = replace(model, excluded_columns=("name", "ship_to"))
        self.engine.load_snapshot(
            model,
            [first],
            {"period": "2026-01", "entity": "A"},
            [first.name],
            complete=True,
        )
        second = self.source(
            "feb.csv", [dict(self.row("2026-02", "i2", "unused", "2.50"), entity="B")]
        )
        self.engine.load_snapshot(
            model,
            [second],
            {"period": "2026-02", "entity": "B"},
            [second.name],
            complete=True,
        )
        rows = self.engine.sample()["rows"]
        self.assertEqual(
            [(r["period"], r["entity"], r["invoice"], r["amount"]) for r in rows],
            [("2026-01", "A", "i1", "1.25"), ("2026-02", "B", "i2", "2.50")],
        )
        db = self.engine._connect()
        try:
            self.assertEqual(
                db.execute("SELECT period,entity,invoice FROM dim_customer").fetchone(),
                ("2026-02", "B", "i2"),
            )
        finally:
            db.close()
        candidate = self.engine.create_export(
            ExportDefinition(
                "jan",
                ("period", "entity", "invoice", "customer", "amount"),
                period_start="2026-01",
                period_end="2026-01",
            )
        )
        with (self.store.root / "exports" / candidate["candidate"] / "data.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            self.assertEqual(
                list(csv.DictReader(handle)),
                [
                    {
                        "period": "2026-01",
                        "entity": "A",
                        "invoice": "i1",
                        "customer": "001",
                        "amount": "1.25",
                    }
                ],
            )

    def test_old_semantics_and_invalid_approved_bounds_are_blocked_everywhere(self):
        source = self.source("day.csv", [self.row("2026-01-15", "1", "name")])
        self.load(source, "2026-01-15")
        candidate = self.engine.create_export(
            ExportDefinition("out", ("period", "customer", "amount"))
        )["candidate"]
        self.engine.approve_export(candidate, "reviewer")
        folder = self.store.root / "exports" / candidate
        manifest_path, approval_path = (
            folder / "manifest.json",
            folder / "approval.json",
        )
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(original["export_semantics_version"], 2)
        for changes in (
            {"version": None},
            {"version": 0},
            {"version": 1},
            {"end": "2026-01"},
            {"end": "2026-02-30"},
            {"start": "2026-02-01", "end": "2026-01-01"},
            {"model_hash": "0" * 64},
        ):
            with self.subTest(changes=changes):
                data = json.loads(json.dumps(original))
                if "version" in changes:
                    if changes["version"] is None:
                        data.pop("export_semantics_version")
                    else:
                        data["export_semantics_version"] = changes["version"]
                if "start" in changes:
                    data["definition"]["period_start"] = changes["start"]
                if "end" in changes:
                    data["definition"]["period_end"] = changes["end"]
                if "model_hash" in changes:
                    data["model_sha256"] = changes["model_hash"]
                manifest_path.write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
                approval = json.loads(approval_path.read_text(encoding="utf-8"))
                approval["manifest_sha256"] = hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest()
                approval_path.write_text(json.dumps(approval), encoding="utf-8")
                callback, publisher = mock.Mock(), mock.Mock()
                destination = self.root / "never-created.csv"
                for action in (
                    lambda: self.engine.approve_export(candidate, "reviewer"),
                    lambda: self.engine.with_approved_export(candidate, callback),
                    lambda: self.engine.publish_sharepoint(
                        candidate, publisher, "data.csv"
                    ),
                    lambda: self.engine.publish_local(candidate, destination),
                ):
                    with self.assertRaises(ValueError):
                        action()
                callback.assert_not_called()
                publisher.resolve.assert_not_called()
                self.assertFalse(destination.exists())

    def test_four_months_correction_idempotence_and_reconcile(self):
        files = []
        for month in range(1, 5):
            period = f"2026-{month:02}"
            path = self.source(
                f"{month}.csv",
                [self.row(period, str(month), f"name{month}", str(month) + ".00")],
            )
            files.append(path)
            self.assertEqual(self.load(path, period)["revision"], month)
        self.assertFalse(self.load(files[0], "2026-01")["changed"])
        self.source("2.csv", [self.row("2026-02", "2", "corrected old", "20.00")])
        self.assertEqual(self.load(files[1], "2026-02")["revision"], 5)
        result = self.engine.sample()
        self.assertEqual(
            [r["amount"] for r in result["rows"]], ["1.00", "20.00", "3.00", "4.00"]
        )
        self.assertTrue(all(r["name"] == "name4" for r in result["rows"]))
        self.assertTrue(self.engine.reconcile()["matches"])
        db = self.engine._connect()
        try:
            self.assertEqual(
                db.execute("SELECT sk FROM dim_customer").fetchall(), [(1,)]
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM mart_audit_rows").fetchone()[0], 1
            )
            audit = json.loads(
                db.execute("SELECT payload FROM mart_audit_rows").fetchone()[0]
            )
            self.assertEqual(audit["version"], 2)
            self.assertEqual(audit["measures"]["amount"], "2.00")
            self.assertEqual(
                audit["dimensions"]["customer"], {"sk": 1, "natural_key": ["001", "02"]}
            )
            self.assertEqual(audit["row"]["invoice"], "2")
            self.assertEqual(
                db.execute(
                    "SELECT row_count,LENGTH(sha256) FROM mart_audit_batches WHERE revision=5"
                ).fetchone(),
                (1, 64),
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM mart_history").fetchone()[0], 5
            )
        finally:
            db.close()

    def test_idempotent_new_source_path_is_protected_from_publish(self):
        first = self.source("first.csv", [self.row("2026-01", "1", "name")])
        self.load(first, "2026-01")
        other = self.root / "same.csv"
        other.write_bytes(first.read_bytes())
        self.assertEqual(self.load(other, "2026-01"), {"revision": 1, "changed": False})
        definition = ExportDefinition("out", ("period", "customer", "amount"))
        candidate = self.engine.create_export(definition)["candidate"]
        self.engine.approve_export(candidate, "reviewer")
        original = other.read_bytes()
        with self.assertRaisesRegex(ValueError, "input file"):
            self.engine.publish_local(candidate, other)
        self.assertEqual(other.read_bytes(), original)
        db = self.engine._connect()
        try:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM mart_history").fetchone()[0], 1
            )
        finally:
            db.close()

    def test_audit_failure_rolls_back_correction(self):
        path = self.source("x.csv", [self.row("2026-01", "1", "name", "1.25")])
        self.load(path, "2026-01")
        self.source("x.csv", [self.row("2026-01", "1", "name", "4.25")])
        controls = self.engine._scope_controls

        def wrong(db, model, scope):
            value = controls(db, model, scope)
            return {"rows": value["rows"], "sums": {"amount": "999.00"}}

        with mock.patch.object(self.engine, "_scope_controls", side_effect=wrong):
            with self.assertRaisesRegex(ValueError, "Audit copy controls differ"):
                self.load(path, "2026-01")
        self.assertEqual(self.engine.catalog()["revision"], 1)
        self.assertEqual(self.engine.sample()["rows"][0]["amount"], "1.25")

    def test_rejections_preserve_committed_data(self):
        path = self.source("x.csv", [self.row("2026-02", "2", "old")])
        with self.assertRaises(ValueError):
            self.engine.load_snapshot(
                self.model,
                [path],
                {"period": "2026-02", "entity": "A"},
                ["missing.csv"],
                complete=True,
            )
        self.load(path, "2026-02")
        for rows in (
            [self.row("2026-02", "2", "old")] * 2,
            [self.row("2026-02", "2", "old", "1.234")],
            [self.row("2026-02", "", "old")],
            [self.row("2026-02", "2", "changed")],
        ):
            self.source("x.csv", rows)
            with self.assertRaises(ValueError):
                self.load(path, "2026-02")
        self.assertEqual(self.engine.catalog()["revision"], 1)
        self.assertEqual(self.engine.sample()["rows"][0]["amount"], "1.25")

    def test_exports_approval_stale_and_backup_restore(self):
        path = self.source("x.csv", [self.row("2026-02", "2", "old", "")])
        self.load(path, "2026-02")
        self.assertIsNone(self.engine.sample()["rows"][0]["amount"])
        definition = ExportDefinition("analysis", ("period", "customer", "amount"))
        export = self.engine.create_export(definition)
        self.engine.approve_export(export["candidate"], "reviewer")
        dest = self.root / "published.csv"
        self.engine.publish_local(export["candidate"], dest)
        old = dest.read_bytes()
        self.source("new.csv", [self.row("2026-03", "3", "new")])
        self.load(self.root / "new.csv", "2026-03")
        with self.assertRaises(ValueError):
            self.engine.publish_local(export["candidate"], dest)
        self.assertEqual(dest.read_bytes(), old)
        backup = self.root / "backup"
        self.assertEqual(self.engine.backup(backup)["revision"], 2)
        result = MartEngine.restore_backup(
            backup, self.root / "restored", preflight=lambda *_: {"allowed": True}
        )
        self.assertEqual(result["revision"], 2)
        restored = MartEngine(WorkspaceStore(self.root / "restored"))
        self.assertTrue(restored.reconcile()["matches"])
        self.assertEqual(restored.sample()["rows"][0]["customer"], "001")

    def test_unicode_fixed_attributes_and_decimal_38(self):
        model = MartModel(
            "매출",
            "매출",
            (Dimension("거래처", ("고객 코드",), ("이름",)),),
            (Measure("금액", "금액", 38, 0, "USD"),),
            "기간",
            (),
            ("전표",),
        )
        path = self.root / "unicode.csv"

        def write(name, amount):
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["기간", "전표", "고객 코드", "이름", "금액"]
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "기간": "2026-01",
                        "전표": "1",
                        "고객 코드": "001",
                        "이름": name,
                        "금액": amount,
                    }
                )

        write("고객", "9" * 38)
        self.engine.load_snapshot(
            model, [path], {"기간": "2026-01"}, [path.name], complete=True
        )
        self.assertEqual(self.engine.sample()["rows"][0]["금액"], "9" * 38)
        write("변경", "9" * 38)
        with self.assertRaises(ValueError):
            self.engine.load_snapshot(
                model, [path], {"기간": "2026-01"}, [path.name], complete=True
            )
        write("고객", "1" + "0" * 38)
        with self.assertRaises(ValueError):
            self.engine.load_snapshot(
                model, [path], {"기간": "2026-01"}, [path.name], complete=True
            )
        self.assertEqual(self.engine.catalog()["revision"], 1)

    def test_long_cost_and_existing_destination_protection(self):
        path = self.source("x.csv", [self.row("2026-02", "2", "old")])
        self.load(path, "2026-02")
        export = self.engine.create_export(
            ExportDefinition("cost", ("period", "customer"), "long", ("amount",))
        )
        self.engine.approve_export(export["candidate"], "reviewer")
        with self.assertRaises(ValueError):
            self.engine.publish_local(export["candidate"], path)
        existing = self.root / "existing.csv"
        existing.write_text("original", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.engine.publish_local(export["candidate"], existing)
        self.assertEqual(existing.read_text(encoding="utf-8"), "original")
        output = self.root / "output.csv"
        self.engine.publish_local(export["candidate"], output)
        self.assertIn("amount,1.25", output.read_text(encoding="utf-8-sig"))

    def test_grouped_filtered_export(self):
        path = self.source(
            "x.csv",
            [
                self.row("2026-02", "2", "old", "2.00"),
                self.row("2026-02", "3", "old", "3.00"),
            ],
        )
        self.load(path, "2026-02")
        definition = ExportDefinition(
            "summary",
            ("customer", "amount"),
            group_by=("customer",),
            filters=(("entity", "A"),),
            period_start="2026-02",
            period_end="2026-02",
        )
        result = self.engine.create_export(definition)
        csv_path = self.store.root / "exports" / result["candidate"] / "data.csv"
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(
                list(csv.reader(handle)), [["customer", "amount"], ["001", "5.00"]]
            )
        filtered = self.engine.create_export(
            ExportDefinition(
                "empty",
                ("customer", "amount"),
                group_by=("customer",),
                filters=(("entity", "B"),),
            )
        )
        self.assertEqual(filtered["rows"], 0)

    def test_partial_multifile_declaration_cannot_replace_existing_scope(self):
        first = self.source("first.csv", [self.row("2026-02", "1", "old", "2.00")])
        second = self.source("second.csv", [self.row("2026-02", "2", "old", "3.00")])
        scope = {"period": "2026-02", "entity": "A"}
        self.engine.load_snapshot(
            self.model, [first, second], scope, [first.name, second.name], complete=True
        )
        with self.assertRaises(ValueError):
            self.engine.load_snapshot(
                self.model, [first], scope, [first.name], complete=True
            )
        self.assertEqual(self.engine.catalog()["revision"], 1)
        self.assertEqual(len(self.engine.sample()["rows"]), 2)
        self.assertEqual(
            self.engine.reconcile()["scopes"][0]["actual"]["sums"]["amount"], "5.00"
        )

    def test_authority_missing_and_mixed_granularity_are_rejected(self):
        model = MartModel(
            "S",
            "sales",
            (Dimension("customer", ("customer", "ship_to"), ("name",), "authority"),),
            (Measure("amount", "amount", 10, 2),),
            "period",
            ("entity",),
            ("invoice",),
        )
        path = self.root / "authority.csv"

        def write(authority):
            item = self.row("2026-02", "2", "old")
            item["authority"] = authority
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(item))
                writer.writeheader()
                writer.writerow(item)

        write("2026-02")
        self.engine.load_snapshot(
            model,
            [path],
            {"period": "2026-02", "entity": "A"},
            [path.name],
            complete=True,
        )
        for value in ("", "2026-02-01", "2026-13"):
            write(value)
            with self.assertRaises(ValueError):
                self.engine.load_snapshot(
                    model,
                    [path],
                    {"period": "2026-02", "entity": "A"},
                    [path.name],
                    complete=True,
                )
        self.assertEqual(self.engine.catalog()["revision"], 1)

    def test_source_changed_before_commit_rolls_back(self):
        import src.mart.engine as engine_module

        path = self.source("x.csv", [self.row("2026-02", "2", "old")])
        self.load(path, "2026-02")
        self.source("x.csv", [self.row("2026-02", "2", "old", "2.00")])
        real_hash = engine_module._hash_file
        calls = 0

        def changed(target):
            nonlocal calls
            result = real_hash(target)
            if Path(target) == path:
                calls += 1
                if calls == 2:
                    self.source("x.csv", [self.row("2026-02", "2", "old", "3.00")])
            return result

        with (
            mock.patch.object(engine_module, "_hash_file", side_effect=changed),
            self.assertRaises(ValueError),
        ):
            self.load(path, "2026-02")
        self.assertEqual(self.engine.catalog()["revision"], 1)
        self.assertEqual(self.engine.sample()["rows"][0]["amount"], "1.25")

    def test_default_preflight_blocks_and_reads_do_not_create(self):
        engine = MartEngine(self.store)
        with self.assertRaises(FileNotFoundError):
            engine.catalog()
        self.assertFalse(self.store.database.exists())
        path = self.source("x.csv", [self.row("2026-02", "2", "old")])
        with self.assertRaises(ValueError):
            engine.load_snapshot(
                self.model,
                [path],
                {"period": "2026-02", "entity": "A"},
                [path.name],
                complete=True,
            )
        self.assertFalse(self.store.database.exists())


if __name__ == "__main__":
    unittest.main()
