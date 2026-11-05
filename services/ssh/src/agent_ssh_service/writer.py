"""Transferable directory writer, fenced under the original OS writer guard."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable, Iterator
import os
from pathlib import Path
import sqlite3
import time
from typing import TYPE_CHECKING, cast

from fleet_manager.private_state import create_private_file, windows_private
from single_instance_lease import AlreadyRunningError, SingleInstance

from .config import ServiceConfig, check

if TYPE_CHECKING:
    from .store import DirectoryStore

TABLE = "service_writer"


def managed_owner(db: sqlite3.Connection) -> str | None:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,),
    ).fetchone()
    if not exists:
        return None
    row = db.execute("SELECT owner FROM service_writer WHERE id=1").fetchone()
    return cast(str, row[0]) if row is not None else None


class FencedWriter:
    """The owner is captured once; rechecking never adopts a later winner."""

    def __init__(self, config: ServiceConfig, owner: str, *, timeout: float = 5,
                 selected: Callable[[], object] = lambda: None) -> None:
        check(isinstance(owner, str) and 0 < len(owner) <= 256, "invalid-writer-owner")
        self.config, self.owner = config, owner
        self.root = Path(config.to_dict()["data_dir"])
        self.timeout, self.selected = timeout, selected

    @contextmanager
    def guard(self) -> Iterator[None]:
        """Serialize activation and complete transactions, not process lifetimes."""
        lease = SingleInstance(str(self.root), service="agent-ssh-service")
        if os.name == "nt":
            try:
                descriptor = create_private_file(lease.lock_path)
            except FileExistsError:
                windows_private(lease.lock_path)
            else:
                os.close(descriptor)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                lease.acquire()
                break
            except AlreadyRunningError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("writer-guard-unavailable-existing-owner-preserved") from None
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            lease.release()

    def claim(self, db: sqlite3.Connection) -> None:
        self.selected()
        db.execute("""CREATE TABLE IF NOT EXISTS service_writer (
            id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL)""")
        db.execute("INSERT OR REPLACE INTO service_writer VALUES (1, ?)", (self.owner,))

    def check(self, db: sqlite3.Connection) -> None:
        check(managed_owner(db) == self.owner, "stale-service-writer")

    def activate(self) -> DirectoryStore:
        from .store import DirectoryStore

        check("relay" not in self.config.to_dict(), "native-relay-transfer-unsupported")
        return DirectoryStore(self.config, writer=self, activate=True)

    def observed_owner(self) -> str | None:
        path = self.root / "directory.sqlite3"
        try:
            db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                return managed_owner(db)
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            return None

    def current(self) -> bool:
        return self.observed_owner() == self.owner
