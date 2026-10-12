"""Explicit node persistence; no transport, authentication or remote executor."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, identifier, integer


class NodeJournal:
    """Own local acceptance and receipts independently of a Gateway connection."""

    def __init__(self, path, *, authority, installation, target):
        for name, value in (("authority", authority), ("installation", installation),
                            ("target", target)):
            identifier(value, f"node {name}")
        self.path = Path(path).resolve()
        self.authority, self.installation, self.target = authority, installation, target
        with self._transaction(create=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute("SELECT 1 FROM sqlite_master").fetchone():
                    raise PlanError("unrecognized node journal database")
                connection.execute(
                    "CREATE TABLE identity(authority TEXT,installation TEXT,target TEXT)"
                )
                connection.execute("INSERT INTO identity VALUES(?,?,?)",
                                   (authority, installation, target))
                connection.execute(
                    "CREATE TABLE operations("
                    "id INTEGER PRIMARY KEY,principal TEXT NOT NULL,account TEXT NOT NULL,"
                    "operation TEXT NOT NULL,intent BLOB NOT NULL,"
                    "UNIQUE(principal,account,operation))"
                )
                connection.execute(
                    "CREATE TABLE receipts("
                    "cursor INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "operation_id INTEGER NOT NULL REFERENCES operations(id),"
                    "sequence INTEGER NOT NULL,receipt BLOB NOT NULL,"
                    "UNIQUE(operation_id,sequence))"
                )
                connection.execute("PRAGMA user_version=1")
            self._verify(connection)

    def _verify(self, connection):
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise PlanError("unsupported node journal schema")
        identity = connection.execute(
            "SELECT authority,installation,target FROM identity"
        ).fetchall()
        if identity != [(self.authority, self.installation, self.target)]:
            raise PlanError("node journal identity mismatch")

    @contextmanager
    def _transaction(self, *, create=False, write=True):
        connection = sqlite3.connect(
            str(self.path) if create else self.path.as_uri() + "?mode=rw",
            uri=not create, timeout=5, isolation_level=None,
        )
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if not create:
                self._verify(connection)
            yield connection
            connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def _scope(self, intent):
        value = intent.to_dict()
        if value["authority"] != self.authority or value["target"] != self.target:
            raise PlanError("intent authority or target differs from node")
        return value

    @staticmethod
    def _find(connection, value):
        return connection.execute(
            "SELECT id,intent FROM operations WHERE principal=? AND account=? AND operation=?",
            (value["principal"], value["account"], value["operation"]),
        ).fetchone()

    @staticmethod
    def _latest(connection, operation_id):
        row = connection.execute(
            "SELECT receipt FROM receipts WHERE operation_id=? ORDER BY sequence DESC LIMIT 1",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise PlanError("node acceptance receipt is missing")
        return TargetReceipt(bytes(row[0]))

    def _accepted(self, connection, intent):
        row = self._find(connection, self._scope(intent))
        if row is None:
            raise PlanError("operation was not accepted")
        OperationIntent(bytes(row[1])).check_repeat(intent)
        return row[0], self._latest(connection, row[0])

    @staticmethod
    def _append(connection, operation_id, receipt):
        connection.execute(
            "INSERT INTO receipts(operation_id,sequence,receipt) VALUES(?,?,?)",
            (operation_id, receipt.to_dict()["sequence"], receipt.encoded),
        )

    @staticmethod
    def _receipt(intent, status, sequence, result_ref=None):
        value = intent.to_dict()
        del value["parameters"]
        value.update(schema="fleet-manager.target-receipt",
                     request_digest=intent.request_digest, status=status,
                     sequence=sequence, result_ref=result_ref)
        return TargetReceipt.from_dict(value)

    def accept(self, intent):
        """Caller authenticates and authorizes first; duplicate delivery does no effect."""
        value = self._scope(intent)
        with self._transaction() as connection:
            prior = self._find(connection, value)
            if prior:
                OperationIntent(bytes(prior[1])).check_repeat(intent)
                return self._latest(connection, prior[0])
            cursor = connection.execute(
                "INSERT INTO operations(principal,account,operation,intent) VALUES(?,?,?,?)",
                (value["principal"], value["account"], value["operation"], intent.encoded),
            )
            receipt = self._receipt(intent, "accepted", 1)
            self._append(connection, cursor.lastrowid, receipt)
            return receipt

    def commit_cooperative(self, intent, apply):
        """Trusted short SQLite-only callback returns a succeeded result reference.

        The callback must use this connection without committing, and perform no
        external effects. Its local effect and receipt commit or roll back together.
        """
        self._scope(intent)
        with self._transaction() as connection:
            operation_id, prior = self._accepted(connection, intent)
            status = prior.to_dict()["status"]
            if status in ("succeeded", "failed"):
                return prior
            if status == "unknown":
                raise PlanError("uncertain effect requires explicit reconciliation")
            connection.set_authorizer(
                lambda action, *_: sqlite3.SQLITE_DENY if action in (
                    sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                    sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
                ) else sqlite3.SQLITE_OK
            )
            try:
                result_ref = apply(connection)
            finally:
                connection.set_authorizer(None)
            self._verify(connection)
            receipt = self._receipt(intent, "succeeded",
                                    prior.to_dict()["sequence"] + 1, result_ref)
            self._append(connection, operation_id, receipt)
            return receipt

    def begin_external(self, intent):
        """Persist UNKNOWN before external work; return permission once, never on retry.

        A true return licenses only this caller's already-authorized attempt.
        A crash before or after the effect leaves UNKNOWN and forbids blind retry.
        """
        self._scope(intent)
        with self._transaction() as connection:
            operation_id, prior = self._accepted(connection, intent)
            if prior.to_dict()["status"] != "accepted":
                return False
            receipt = self._receipt(intent, "unknown", prior.to_dict()["sequence"] + 1)
            self._append(connection, operation_id, receipt)
            return True

    def reconcile(self, intent, receipt):
        """Caller supplies attributable terminal evidence; parsing is not proof."""
        self._scope(intent)
        receipt.check_intent(intent)
        value = receipt.to_dict()
        if value["status"] not in ("succeeded", "failed"):
            raise PlanError("reconciliation requires terminal evidence")
        with self._transaction() as connection:
            operation_id, prior = self._accepted(connection, intent)
            if receipt.encoded == prior.encoded:
                return prior
            before = prior.to_dict()
            if before["status"] != "unknown":
                raise PlanError("reconciliation requires an uncertain effect")
            if value["sequence"] != before["sequence"] + 1:
                raise PlanError("reconciliation requires the next receipt sequence")
            self._append(connection, operation_id, receipt)
            return receipt

    def inspect(self, intent):
        """Return the original operation's latest receipt, without effect execution."""
        value = self._scope(intent)
        with self._transaction(write=False) as connection:
            if self._find(connection, value) is None:
                return None
            return self._accepted(connection, intent)[1]

    def results(self, *, after=0, limit=100):
        """Read append-only receipt history; transport owns its durable replay cursor."""
        integer(after, "result cursor")
        integer(limit, "result limit", minimum=1, maximum=256)
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT cursor,receipt FROM receipts WHERE cursor>? ORDER BY cursor LIMIT ?",
                (after, limit),
            ).fetchall()
            return [{"cursor": row[0], "receipt": TargetReceipt(bytes(row[1]))} for row in rows]
