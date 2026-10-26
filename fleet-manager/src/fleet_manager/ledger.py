"""Explicit local operation journal; no executor, authentication or task scheduler."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .allocation import (RELEASE_INDEX, check_released_generation, create_release_history,
                         verify_release_index)
from .operations import AllocationRelease, OperationIntent, TargetReceipt
from .planning import PlanError, identifier, integer


class OperationLedger:
    """Persist admitted intent/outbox/audit atomically for one installation."""

    def __init__(self, path, *, authority, installation, enable_enrollment=False):
        if type(enable_enrollment) is not bool:
            raise PlanError("enrollment migration selection must be boolean")
        identifier(authority, "ledger authority")
        identifier(installation, "ledger installation")
        self.path = Path(path).resolve()
        self.authority, self.installation = authority, installation
        self._version = 5 if enable_enrollment else 4
        with self._transaction(verify=False) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, 5) or (version == 5 and not enable_enrollment):
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
                version = 1
            identity = connection.execute("SELECT authority,installation FROM identity").fetchall()
            if identity != [(authority, installation)]:
                raise PlanError("operation ledger identity mismatch")
            self._check_tables(connection, version)
            if version == 1:
                connection.execute(
                    "CREATE TABLE result_streams("
                    "target TEXT NOT NULL,installation TEXT NOT NULL,cursor INTEGER NOT NULL,"
                    "PRIMARY KEY(target,installation))"
                )
                version = 2
            if version == 2:
                connection.execute(
                    "CREATE TABLE allocation_claims("
                    "resource TEXT PRIMARY KEY NOT NULL,"
                    "operation_id INTEGER NOT NULL UNIQUE REFERENCES operations(id))"
                )
                version = 3
            if version == 3:
                create_release_history(connection)
                connection.execute("PRAGMA user_version=4")
                version = 4
            if version == 4 and enable_enrollment:
                from .enrollment_journal import create_schema
                create_schema(connection)
                connection.execute("PRAGMA user_version=5")

    @staticmethod
    def _check_tables(connection, version):
        columns = {
            "identity": ("authority", "installation"),
            "operations": ("id", "principal", "account", "operation", "intent"),
            "outbox": ("operation_id", "state"),
            "receipts": ("operation_id", "sequence", "receipt", "status"),
            "audit": ("id", "operation_id", "event"),
        }
        if version >= 2:
            columns["result_streams"] = ("target", "installation", "cursor")
        if version >= 3:
            columns["allocation_claims"] = ("resource", "operation_id")
        if version >= 4:
            columns["allocation_releases"] = ("operation_id", "resource", "generation", "proof")
        if version >= 5:
            columns["enrollment_floors"] = ("gateway", "revision", "digest")
            columns["machine_enrollments"] = (
                "operation_id", "gateway", "machine", "principal", "key_thumbprint", "binding",
                "withdrawn_revision")
            from .enrollment_journal import verify_schema
            verify_schema(connection)
        objects = connection.execute(
            "SELECT type,name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
        ).fetchall()
        expected_objects = {("table", name) for name in columns}
        if version >= 4:
            expected_objects.add(("index", RELEASE_INDEX))
            verify_release_index(connection)
        if set(objects) != expected_objects:
            raise PlanError("unrecognized operation ledger schema")
        for name, expected in columns.items():
            actual = tuple((row[1], row[6]) for row in
                           connection.execute(f"PRAGMA table_xinfo({name})"))
            if actual != tuple((column, 0) for column in expected):
                raise PlanError("unrecognized operation ledger columns")

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
                if connection.execute("PRAGMA user_version").fetchone()[0] != self._version:
                    raise PlanError("unsupported operation ledger schema")
                identity = connection.execute("SELECT authority,installation FROM identity").fetchall()
                if identity != [(self.authority, self.installation)]:
                    raise PlanError("operation ledger identity mismatch")
                verify_release_index(connection)
                if self._version == 5:
                    from .enrollment_journal import verify_schema
                    verify_schema(connection)
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
        if value["command"] == "machine.enroll":
            raise PlanError("machine enrollment requires its exclusive journal transaction")
        with self._transaction() as connection:
            return self._admit(connection, value, intent)

    def _admit(self, connection, value, intent):
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

    def admit_allocation(self, intent, *, resource):
        """Claim one caller-resolved resource with admission; no grant or effect fence."""
        identifier(resource, "allocation resource")
        value = self._scope(intent)
        if value["command"] != "workspace.allocate":
            raise PlanError("allocation claim requires workspace.allocate")
        with self._transaction() as connection:
            prior = self._find(connection, value)
            if prior:
                OperationIntent(bytes(prior[1])).check_repeat(intent)
                claim = connection.execute(
                    "SELECT resource FROM allocation_claims WHERE operation_id=?", (prior[0],),
                ).fetchone()
                if claim is None:
                    claim = connection.execute(
                        "SELECT resource FROM allocation_releases WHERE operation_id=?", (prior[0],),
                    ).fetchone()
                if claim is None:
                    raise PlanError("previous admission has no allocation claim")
                if claim[0] != resource:
                    raise PlanError("operation already claims a different resource")
                return prior[0]
            if connection.execute(
                "SELECT 1 FROM allocation_claims WHERE resource=?", (resource,),
            ).fetchone():
                raise PlanError("allocation resource is already claimed")
            check_released_generation(connection, value["generation"], resource)
            operation_id = self._admit(connection, value, intent)
            connection.execute(
                "INSERT INTO allocation_claims VALUES(?,?)", (resource, operation_id),
            )
            self._event(connection, operation_id, "allocation-claimed")
            return operation_id

    def release_allocation(self, intent, proof, *, resource, installation):
        """Caller authenticates exact node evidence before releasing a local claim."""
        identifier(resource, "allocation resource")
        identifier(installation, "release installation")
        value = self._scope(intent)
        proof.check_intent(intent, resource=resource, installation=installation)
        with self._transaction() as connection:
            row = self._find(connection, value)
            if row is None:
                raise PlanError("operation was not admitted")
            OperationIntent(bytes(row[1])).check_repeat(intent)
            operation_id = row[0]
            prior = connection.execute(
                "SELECT proof FROM allocation_releases WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if prior is not None:
                if bytes(prior[0]) != proof.encoded:
                    raise PlanError("conflicting allocation release evidence")
                return
            if connection.execute(
                "SELECT operation_id FROM allocation_claims WHERE resource=?", (resource,),
            ).fetchone() != (operation_id,):
                raise PlanError("operation does not hold the allocation claim")
            connection.execute(
                "INSERT INTO allocation_releases VALUES(?,?,?,?)",
                (operation_id, resource, value["generation"], proof.encoded),
            )
            connection.execute(
                "DELETE FROM allocation_claims WHERE resource=? AND operation_id=?",
                (resource, operation_id),
            )
            self._event(connection, operation_id, "allocation-released")

    def inspect_allocation(self, *, resource):
        """Return local held-claim evidence; absence is not permission to allocate."""
        identifier(resource, "allocation resource")
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT o.id,o.intent FROM allocation_claims c"
                " JOIN operations o ON o.id=c.operation_id WHERE c.resource=?", (resource,),
            ).fetchone()
            return ({"resource": resource, "operation_id": row[0],
                     "intent": OperationIntent(bytes(row[1])).to_dict()} if row else None)

    def allocation_release(self, intent):
        """Read accepted release evidence, not an absence-based execution grant."""
        value = self._scope(intent)
        with self._transaction(write=False) as connection:
            row = self._find(connection, value)
            if row is None:
                raise PlanError("operation was not admitted")
            OperationIntent(bytes(row[1])).check_repeat(intent)
            released = connection.execute(
                "SELECT proof FROM allocation_releases WHERE operation_id=?", (row[0],),
            ).fetchone()
            return AllocationRelease(bytes(released[0])) if released else None

    def record_receipt(self, intent, receipt):
        """Persist correlated target assertions; sequence alone is not authentication."""
        value = self._scope(intent)
        receipt.check_intent(intent)
        with self._transaction() as connection:
            self._record_receipt(connection, value, intent, receipt)

    def _record_receipt(self, connection, value, intent, receipt):
        claimed = receipt.to_dict()
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

    @staticmethod
    def _cursor(connection, target, installation):
        row = connection.execute(
            "SELECT cursor FROM result_streams WHERE target=? AND installation=?",
            (target, installation),
        ).fetchone()
        return row[0] if row else 0

    def result_cursor(self, *, target, installation):
        """Read one trusted node spool's durable receiver position, never an ack."""
        identifier(target, "result target")
        identifier(installation, "result installation")
        with self._transaction(write=False) as connection:
            return self._cursor(connection, target, installation)

    def reconcile_results(self, *, target, installation, after, entries):
        """Caller authenticates the spool; receipts/outbox/audit/cursor commit together."""
        identifier(target, "result target")
        identifier(installation, "result installation")
        integer(after, "result cursor")
        if not isinstance(entries, (list, tuple)) or len(entries) > 256:
            raise PlanError("result page must contain at most 256 entries")
        validated, cursor = [], after
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"cursor", "receipt"}:
                raise PlanError("invalid result entry fields")
            integer(entry["cursor"], "entry cursor", minimum=1)
            receipt = entry["receipt"]
            if not isinstance(receipt, TargetReceipt):
                raise PlanError("result entry requires an immutable receipt")
            if entry["cursor"] != cursor + 1:
                raise PlanError("result page has a gap or conflicting cursor")
            value = receipt.to_dict()
            if value["authority"] != self.authority or value["target"] != target:
                raise PlanError("result receipt authority or target mismatch")
            cursor = entry["cursor"]
            validated.append((cursor, receipt))
        with self._transaction() as connection:
            if after != self._cursor(connection, target, installation):
                raise PlanError("receiver cursor changed; reread before replay")
            for _, receipt in validated:
                value = receipt.to_dict()
                admitted = self._find(connection, value)
                if admitted is None:
                    raise PlanError("result operation was not admitted")
                intent = OperationIntent(bytes(admitted[1]))
                receipt.check_intent(intent)
                self._record_receipt(connection, intent.to_dict(), intent, receipt)
            if validated:
                connection.execute(
                    "INSERT INTO result_streams VALUES(?,?,?) ON CONFLICT(target,installation)"
                    " DO UPDATE SET cursor=excluded.cursor",
                    (target, installation, cursor),
                )
            return cursor

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
