"""Historical ordinary native admission evidence, independent of the session DB."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

from .native_keyed_admission import admission_receipt
from .native_send_admission import NativeSendBusy, NativeSendPrompt, read_receipt, submit
from .native_send_settlement import request_digest


class NativeReceiptRecords:
    """Caller-owned private store. No record or receipt constitutes a runnable turn."""

    def __init__(self, path: Path) -> None:
        self.path = path.absolute()

    @classmethod
    def create(cls, directory: Path) -> NativeReceiptRecords:
        directory.mkdir(mode=0o700)
        store = cls(directory / "native-receipts.db")
        with closing(sqlite3.connect(store.path)) as db:
            db.execute("CREATE TABLE native_v1(delivery TEXT PRIMARY KEY, digest TEXT NOT NULL, "
                       "accepted_turn INTEGER CHECK(accepted_turn>=0))")
        return store

    def _open(self, *, write: bool) -> sqlite3.Connection:
        db = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
        db.execute("PRAGMA trusted_schema=OFF")
        if write:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
        return db

    @staticmethod
    def _read(db: sqlite3.Connection, session: str, request: NativeSendPrompt) -> dict[str, Any] | None:
        row = db.execute("SELECT digest,accepted_turn FROM native_v1 WHERE delivery=?",
                         (request.delivery_id(),)).fetchone()
        if row is None:
            raise KeyError("no retained native operation")
        if row[0] != request_digest(session, request):
            raise ValueError("native_invocation_conflict")
        if row[1] is None:
            return None
        if type(row[1]) is not int or row[1] < 0:
            raise ValueError("native_admission_history_unproven")
        return admission_receipt(session, row[1], request)

    def inspect(self, session: str, request: NativeSendPrompt) -> dict[str, Any] | None:
        """None means unproven, not absent. Never opens a session or calls transport."""
        with closing(self._open(write=False)) as db:
            return self._read(db, session, request)

    def _accept(self, session: str, request: NativeSendPrompt, receipt: dict[str, Any]) -> dict[str, Any]:
        turn = receipt.get("turn_index")
        if type(turn) is not int or turn < 0 or json.dumps(receipt, sort_keys=True) != json.dumps(
            admission_receipt(session, turn, request), sort_keys=True,
        ):
            raise ValueError("native_admission_history_unproven")
        with closing(self._open(write=True)) as db:
            existing = self._read(db, session, request)
            if existing is not None and existing != receipt:
                raise ValueError("native_admission_history_unproven")
            db.execute("UPDATE native_v1 SET accepted_turn=? WHERE delivery=?", (turn, request.delivery_id()))
            db.commit()
        return receipt

    async def reconcile(self, manager: Any, session: str, request: NativeSendPrompt) -> dict[str, Any] | None:
        """Consult the exact original holder's receipt-only path, not settlement or send."""
        try:
            retained = self.inspect(session, request)
        except KeyError:
            return await read_receipt(manager, session, request)
        if retained is not None:
            return retained
        observed = await read_receipt(manager, session, request)
        return None if observed is None else self._accept(session, request, observed)

    async def submit_once(self, manager: Any, session: str, request: NativeSendPrompt) -> dict[str, Any] | None:
        """One no-resume attempt. Cancellation, process loss and ambiguous errors never re-arm it."""
        if not request.no_resume:
            raise ValueError("retained admission requires no_resume")
        with closing(self._open(write=True)) as db:
            inserted = db.execute("INSERT INTO native_v1(delivery,digest) VALUES(?,?) "
                                  "ON CONFLICT(delivery) DO NOTHING",
                                  (request.delivery_id(), request_digest(session, request))).rowcount
            if inserted:
                holder = manager.get_session(session)
                if holder is None or holder.session_id != session:
                    raise ValueError("canonical_bridge_session_required")
                if holder.acp_session_id != request.expected_native_session_id:
                    raise ValueError("expected_native_mismatch")
            self._read(db, session, request)
            db.commit()
        if not inserted:
            return await self.reconcile(manager, session, request)
        try:
            receipt = await submit(manager, session, request)
        except NativeSendBusy:
            with closing(self._open(write=True)) as db:
                db.execute("DELETE FROM native_v1 WHERE delivery=? AND digest=? AND accepted_turn IS NULL",
                           (request.delivery_id(), request_digest(session, request)))
                db.commit()
            raise
        return self._accept(session, request, receipt)
