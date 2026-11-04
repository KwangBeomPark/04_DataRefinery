import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.mart.capacity import (
    Measurement,
    collect_capacity,
    evaluate_preflight,
    file_size,
)
from src.mart.publication import SharePointProfile, SharePointPublisher, TransportError


def measure(value):
    return Measurement(
        value,
        "test",
        "injected",
        datetime.now(timezone.utc),
        "ok" if value is not None else "unknown",
    )


class MartOperationsTests(unittest.TestCase):
    def test_warning_hysteresis_and_missing_wal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "x.db"
        db.write_bytes(b"x" * 80)
        assert file_size(str(db) + ".wal", absent_zero=True).value == 0
        assert collect_capacity([db], 100)["warning"] is True
        db.write_bytes(b"x" * 75)
        assert collect_capacity([db], 100, True)["warning"] is True
        db.write_bytes(b"x" * 74)
        assert collect_capacity([db], 100, True)["warning"] is False
        assert collect_capacity([db])["ratio"] is None

    def test_unknown_wal_blocks_ratio(self):
        def probe(path, **kwargs):
            return measure(None if str(path).endswith(".wal") else 80)

        result = collect_capacity(["db"], 100, probe=probe)
        assert result["known_bytes"] == 80
        assert (
            not result["complete"]
            and result["ratio"] is None
            and result["warning"] is None
        )

    def test_preflight_sums_same_volume_once_and_unknown_blocks(self):
        calls = []

        def probe(path):
            calls.append(path)
            return measure(100)

        kwargs = {
            "estimate_supported": True,
            "reserve_bytes": 10,
            "space_probe": probe,
            "volume_probe": lambda p: "one",
        }
        assert evaluate_preflight([("a", 45), ("b", 45)], **kwargs)["allowed"]
        assert calls == ["a"]
        assert not evaluate_preflight([("a", 45), ("b", 46)], **kwargs)["allowed"]
        assert not evaluate_preflight([("a", 1)], estimate_supported=False)["allowed"]
        assert not evaluate_preflight(
            [("a", 1)], **{**kwargs, "space_probe": lambda p: measure(None)}
        )["allowed"]

    def test_publication_requires_readback_and_stable_version(self):
        profile = SharePointProfile(
            "https://example.invalid/sites/demo", "tenant", "client", "Documents"
        )
        calls = []

        def transport(method, url, headers, body):
            calls.append((method, url, headers))
            if method == "PUT":
                assert headers["If-Match"] == '"old"'
                return json.dumps({"id": "item", "size": 3, "eTag": '"new"'}).encode()
            if url.endswith("/content"):
                return b"abc"
            return json.dumps({"eTag": '"new"'}).encode()

        publisher = SharePointPublisher(profile, transport, "token")
        target = {"drive_id": "drive", "folder_id": "folder"}
        assert (
            publisher.publish(target, "a.csv", b"abc", expected_etag='"old"').status
            == "verified"
        )

        def conflict(method, url, headers, body):
            raise TransportError(412)

        publisher.transport = conflict
        result = publisher.publish(target, "a.csv", b"abc", expected_etag='"old"')
        assert result.status == "failed" and result.previous_remote == "unknown"
        with self.assertRaises(ValueError):
            publisher.publish(target, "a.csv", b"abc")

    def test_device_poll_slow_down_and_cancel(self):
        from threading import Event

        ticks = [0]
        delays = []
        events = []
        pending = ["authorization_pending", "slow_down", "success"]

        def transport(method, url, headers, body):
            if url.endswith("devicecode"):
                return json.dumps(
                    {
                        "device_code": "secret",
                        "user_code": "ABCD",
                        "verification_uri": "https://example.invalid",
                        "expires_in": 60,
                        "interval": 2,
                    }
                ).encode()
            error = pending.pop(0)
            if error != "success":
                raise TransportError(400, payload={"error": error})
            return b'{"access_token":"secret-token"}'

        def wait(seconds):
            delays.append(seconds)
            ticks[0] += seconds

        publisher = SharePointPublisher(
            SharePointProfile(
                "https://example.invalid/sites/demo", "tenant", "client", "docs"
            ),
            transport,
        )
        assert (
            publisher.login_device(events.append, clock=lambda: ticks[0], wait=wait)
            == "authenticated"
        )
        assert delays == [2, 2, 7]
        assert "device_code" not in events[0] and "secret-token" not in repr(publisher)
        event = Event()
        event.set()
        assert (
            publisher.login_device(
                events.append, cancel=event, clock=lambda: ticks[0], wait=wait
            )
            == "cancelled"
        )

    def test_redirect_validation_and_etag_race(self):
        from hashlib import sha256

        profile = SharePointProfile(
            "https://example.invalid/sites/demo", "tenant", "client", "docs"
        )
        location = ["https://example.invalid/download?secret=yes"]
        etag = ['"new"']
        downloads = []

        def transport(method, url, headers, body):
            if method == "PUT":
                return b'{"id":"id","size":3,"eTag":"\\"new\\""}'
            if url.endswith("/content"):
                raise TransportError(302, location=location[0])
            return json.dumps({"eTag": etag[0]}).encode()

        def download(url, host):
            downloads.append((url, host))
            return sha256(b"abc").digest()

        publisher = SharePointPublisher(profile, transport, "token", download)
        target = {"drive_id": "d", "folder_id": "f"}
        assert (
            publisher.publish(target, "x.csv", b"abc", expected_absent=True).status
            == "verified"
        )
        etag[0] = '"changed"'
        assert (
            publisher.publish(target, "x.csv", b"abc", expected_absent=True).status
            == "unverified"
        )
        location[0] = "https://other.invalid/download"
        assert (
            publisher.publish(target, "x.csv", b"abc", expected_absent=True).status
            == "unverified"
        )
        assert len(downloads) == 2

    def test_resolve_follows_only_graph_pages(self):
        profile = SharePointProfile(
            "https://example.invalid/sites/demo",
            "tenant",
            "client",
            "docs",
            "My Folder",
        )

        def transport(method, url, headers, body):
            if url.endswith("/sites/example.invalid:/sites/demo"):
                return b'{"id":"site"}'
            if url.endswith("/drives"):
                return b'{"value":[],"@odata.nextLink":"https://graph.microsoft.com/v1.0/page"}'
            if url.endswith("/page"):
                return b'{"value":[{"name":"docs","id":"drive"}]}'
            assert "My%20Folder" in url
            return b'{"id":"folder","folder":{},"webUrl":"https://example.invalid/sites/demo/Shared%20Documents/My%20Folder"}'

        publisher = SharePointPublisher(profile, transport, "token")
        result = publisher.resolve()
        assert result["folder_id"] == "folder"
        assert "Shared%20Documents" in result["folder_url"]

        def hostile(method, url, headers, body):
            if url.endswith("/sites/example.invalid:/sites/demo"):
                return b'{"id":"site"}'
            if url.endswith("/drives"):
                return b'{"value":[{"name":"docs","id":"drive"}]}'
            return b'{"id":"folder","folder":{},"webUrl":"https://evil.invalid/sites/demo/docs"}'

        publisher.transport = hostile
        with self.assertRaises(ValueError):
            publisher.resolve()

    def test_context_callback_receives_load_inputs(self):
        from src.mart.engine import MartEngine

        class Callback:
            accepts_context = True

            def __call__(self, operation, workspace, size, **context):
                return {"allowed": True, "operation": operation, "context": context}

        engine = MartEngine(object(), Callback())
        result = engine._capacity("load", 42, files=("input.csv",), model="model")
        assert result["context"]["files"] == ("input.csv",)
        assert result["context"]["model"] == "model"


class PublicationBoundaryTests(unittest.TestCase):
    def test_approved_remote_requires_current_revision_and_unchanged_bytes(self):
        import csv
        from src.mart import (
            Dimension,
            ExportDefinition,
            MartEngine,
            MartModel,
            Measure,
            WorkspaceStore,
        )

        class Publisher:
            def __init__(self):
                self.calls = []

            def resolve(self):
                self.calls.append("resolve")
                return {"drive_id": "d", "folder_id": "f"}

            def observe_target(self, target, filename):
                self.calls.append("observe")
                return {"absent": True}

            def publish(self, target, filename, data, **condition):
                self.calls.append(("publish", condition))
                return "verified"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = WorkspaceStore(root / "space")
            store.create()
            engine = MartEngine(store, lambda *_: {"allowed": True})
            model = MartModel(
                "M",
                "sales",
                (Dimension("customer", ("customer",)),),
                (Measure("amount", "amount"),),
                "period",
                (),
                ("id",),
            )
            source = root / "source.csv"

            def write(period, amount):
                with source.open("w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(["period", "id", "customer", "amount"])
                    writer.writerow([period, "1", "001", amount])

            write("2026-01", "1.00")
            engine.load_snapshot(
                model, [source], {"period": "2026-01"}, [source.name], complete=True
            )
            candidate = engine.create_export(
                ExportDefinition("x", ("customer", "amount"))
            )["candidate"]
            engine.approve_export(candidate, "reviewer")
            publisher = Publisher()
            self.assertEqual(
                engine.publish_sharepoint(candidate, publisher, "out.csv"), "verified"
            )
            self.assertEqual(
                publisher.calls[-1][1], {"expected_etag": None, "expected_absent": True}
            )
            publisher.calls.clear()
            write("2026-02", "2.00")
            engine.load_snapshot(
                model, [source], {"period": "2026-02"}, [source.name], complete=True
            )
            with self.assertRaisesRegex(ValueError, "stale"):
                engine.publish_sharepoint(candidate, publisher, "out.csv")
            self.assertEqual(publisher.calls, [])
            candidate = engine.create_export(
                ExportDefinition("x", ("customer", "amount"))
            )["candidate"]
            engine.approve_export(candidate, "reviewer")
            (store.root / "exports" / candidate / "data.csv").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "integrity"):
                engine.publish_sharepoint(candidate, publisher, "out.csv")
            self.assertEqual(publisher.calls, [])

    def test_observe_only_explicit_404_is_absence(self):
        profile = SharePointProfile(
            "https://example.invalid/sites/demo", "tenant", "client", "docs"
        )
        target = {"drive_id": "d", "folder_id": "f"}

        def missing(*args):
            raise TransportError(404)

        publisher = SharePointPublisher(profile, missing, "token")
        self.assertEqual(publisher.observe_target(target, "out.csv"), {"absent": True})

        def denied(*args):
            raise TransportError(403)

        publisher.transport = denied
        with self.assertRaises(TransportError):
            publisher.observe_target(target, "out.csv")
        publisher.transport = lambda *args: b'{"eTag":"v1"}'
        self.assertEqual(publisher.observe_target(target, "out.csv")["etag"], "v1")

    def test_sharepoint_excel_query_escapes_and_preserves_codes(self):
        from src.mart.excel import sharepoint_m_code

        code = sharepoint_m_code(
            "https://example.invalid/sites/demo",
            "https://example.invalid/sites/demo/Shared%20Documents/%ED%95%9C%EA%B8%80%20reports",
            "out.csv",
            ["code", "amount"],
            ["amount"],
        )
        self.assertIn("SharePoint.Files", code)
        self.assertIn('{"code", type text}', code)
        self.assertIn('{"amount", type number}', code)
        self.assertIn("Encoding=65001", code)
        self.assertIn("Shared Documents/한글 reports/", code)
        self.assertNotIn("File.Contents", code)
        escaped = sharepoint_m_code(
            "https://example.invalid/sites/demo",
            "https://example.invalid/sites/demo/Shared%20Documents",
            "out.csv",
            ['code#(lf)"\t'],
            [],
        )
        self.assertIn('code#(#)(lf)""#(tab)', escaped)
