"""Explicit local operation journal; no executor, authentication or task scheduler."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, identifier


class OperationLedger:
    """Persist admitted intent/outbox/audit atomically for one installation."""

    def __init__(self, path, *, authority, installation):
        identifier(authority, "ledger authority")
        identifier(installation, "ledger installation")
        self.path = Path(path).resolve()
        self.authority, self.installation = authority, installation
        with self._transaction(verify=False) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise PlanError("unsupported operation ledger schema")
            if version == 0:
                existing = connection.execute(
                    "SELECT 1 FROM sqlite_master"
                ).fetchone()
                if existing:
                    raise PlanError("unrecognized operation ledger database")
                connection.execute("CREATE TABLE identity(authority TEXT, installation TEXT)")
                connection.execute("INSERT INTO identity VALUES(?,?)", (authority, installation))
                connection.execute(
                    "CREATE TABLE operations("
                    "id INTEGER PRIMARY KEY, principal TEXT NOT NULL, account TEXT NOT NULL,"
                    "operation TEXT NOT NULL, intent BLOB NOT NULL,"
                    "UNIQUE(principal,account,operation))"
                )
                connection.execute(
                    "CREATE TABLE outbox("
                    "operation_id INTEGER PRIMARY KEY REFERENCES operations(id),"
                    "state TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE receipts("
                    "operation_id INTEGER PRIMARY KEY REFERENCES operations(id),"
                    "sequence INTEGER NOT NULL, receipt BLOB NOT NULL, status TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE audit("
                    "id INTEGER PRIMARY KEY, operation_id INTEGER NOT NULL REFERENCES operations(id),"
                    "event TEXT NOT NULL)"
                )
                connection.execute("PRAGMA user_version=1")
            identity = connection.execute("SELECT authority,installation FROM identity").fetchall()
            if identity != [(authority, installation)]:
                raise PlanError("operation ledger identity mismatch")

    @contextmanager
    def _transaction(self, *, verify=True, write=True):
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=rw" if verify else str(self.path),
            timeout=5, isolation_level=None, uri=verify,
        )
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if verify:
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise PlanError("unsupported operation ledger schema")
                identity = connection.execute("SELECT authority,installation FROM identity").fetchall()
                if identity != [(self.authority, self.installation)]:
                    raise PlanError("operation ledger identity mismatch")
            yield connection
            connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @staticmethod
    def _event(connection, operation_id, event):
        connection.execute("INSERT INTO audit(operation_id,event) VALUES(?,?)",
                           (operation_id, event))

    def _scope(self, intent):
        value = intent.to_dict()
        if value["authority"] != self.authority:
            raise PlanError("intent authority differs from ledger")
        return value

    @staticmethod
    def _find(connection, value):
        return connection.execute(
            "SELECT id,intent FROM operations WHERE principal=? AND account=? AND operation=?",
            (value["principal"], value["account"], value["operation"]),
        ).fetchone()

    def admit(self, intent):
        """Caller must already authorize scope. This method performs no effects."""
        value = self._scope(intent)
        with self._transaction() as connection:
            prior = self._find(connection, value)
            if prior:
                OperationIntent(bytes(prior[1])).check_repeat(intent)
                return prior[0]
            cursor = connection.execute(
                "INSERT INTO operations(principal,account,operation,intent) VALUES(?,?,?,?)",
                (value["principal"], value["account"], value["operation"], intent.encoded),
            )
            operation_id = cursor.lastrowid
            connection.execute("INSERT INTO outbox VALUES(?,?)", (operation_id, "pending"))
            self._event(connection, operation_id, "admitted")
            return operation_id

    def record_receipt(self, intent, receipt):
        """Persist correlated target assertions; sequence alone is not authentication."""
        value = self._scope(intent)
        receipt.check_intent(intent)
        claimed = receipt.to_dict()
        with self._transaction() as connection:
            admitted = self._find(connection, value)
            if admitted is None:
                raise PlanError("operation was not admitted")
            OperationIntent(bytes(admitted[1])).check_repeat(intent)
            operation_id = admitted[0]
            prior = connection.execute(
                "SELECT sequence,receipt,status FROM receipts WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if prior:
                if claimed["sequence"] == prior[0] and receipt.encoded == bytes(prior[1]):
                    return
                if claimed["sequence"] <= prior[0]:
                    raise PlanError("stale or conflicting receipt sequence")
                if prior[2] in ("succeeded", "failed"):
                    raise PlanError("terminal outcome cannot be replaced")
                if prior[2] == "unknown" and claimed["status"] == "accepted":
                    raise PlanError("uncertain outcome cannot revert to acceptance")
            connection.execute(
                "INSERT INTO receipts VALUES(?,?,?,?) ON CONFLICT(operation_id) "
                "DO UPDATE SET sequence=excluded.sequence,receipt=excluded.receipt,status=excluded.status",
                (operation_id, claimed["sequence"], receipt.encoded, claimed["status"]),
            )
            state = {"accepted": "accepted", "unknown": "uncertain",
                     "succeeded": "outcome", "failed": "outcome"}[claimed["status"]]
            connection.execute("UPDATE outbox SET state=? WHERE operation_id=?", (state, operation_id))
            self._event(connection, operation_id, claimed["status"])

    def inspect(self, intent):
        """Read durable operation status; never infer parent objective settlement."""
        value = self._scope(intent)
        with self._transaction(write=False) as connection:
            admitted = self._find(connection, value)
            if admitted is None:
                return None
            OperationIntent(bytes(admitted[1])).check_repeat(intent)
            state = connection.execute("SELECT state FROM outbox WHERE operation_id=?",
                                       (admitted[0],)).fetchone()[0]
            row = connection.execute("SELECT receipt FROM receipts WHERE operation_id=?",
                                     (admitted[0],)).fetchone()
            return {"operation_id": admitted[0], "state": state,
                    "receipt": TargetReceipt(bytes(row[0])).to_dict() if row else None}
