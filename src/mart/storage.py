"""Ownership marker and cross-process writer lock for mart workspaces."""
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path


class WorkspaceStore:
    ENGINE = "data-refinery-mart"
    SCHEMA = 1

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.marker = self.root / "workspace.json"
        self.database = self.root / "mart.duckdb"

    def create(self):
        self.root.mkdir(parents=True, exist_ok=True)
        # Never adopt a pre-existing database or populated folder.
        if self.marker.exists() or any(self.root.iterdir()):
            raise ValueError("Workspace directory must be empty")
        data = {"engine": self.ENGINE, "schema": self.SCHEMA, "id": str(uuid.uuid4())}
        with self.marker.open("x", encoding="utf-8") as out:
            json.dump(data, out)
        return data

    def validate(self):
        with self.marker.open(encoding="utf-8") as source:
            data = json.load(source)
        if data.get("engine") != self.ENGINE or data.get("schema") != self.SCHEMA or not data.get("id"):
            raise ValueError("Not a supported mart workspace")
        return data

    @contextmanager
    def lock(self):
        self.validate()
        path = self.root / ".writer.lock"
        with path.open("a+b") as lock:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                if not lock.read(1):
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
