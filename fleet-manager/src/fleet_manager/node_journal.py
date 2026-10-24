"""Explicit node persistence; no transport, authentication or remote executor."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .allocation import (RELEASE_INDEX, check_released_generation, create_release_history,
                         verify_release_index)
from .operations import AllocationRelease, OperationIntent, TargetReceipt
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
                version = 1
            self._verify(connection, version=version)
            if version == 1:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='allocation_owners' COLLATE NOCASE"
                ).fetchone():
                    raise PlanError("unrecognized node allocation schema")
                connection.execute(
                    "CREATE TABLE allocation_owners("
                    "resource TEXT PRIMARY KEY NOT NULL,"
                    "operation_id INTEGER NOT NULL UNIQUE REFERENCES operations(id))"
                )
                connection.execute("PRAGMA user_version=2")
                version = 2
            if version == 2:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='allocation_releases' COLLATE NOCASE"
                    " OR name=? COLLATE NOCASE", (RELEASE_INDEX,),
                ).fetchone():
                    raise PlanError("unrecognized node release schema")
                create_release_history(connection)
                connection.execute("PRAGMA user_version=3")
                version = 3
            if version == 3:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='external_owners' COLLATE NOCASE"
                ).fetchone():
                    raise PlanError("unrecognized node external ownership schema")
                connection.execute(
                    "CREATE TABLE external_owners("
                    "resource TEXT PRIMARY KEY NOT NULL,"
                    "operation_id INTEGER NOT NULL UNIQUE REFERENCES operations(id))"
                )
                connection.execute("PRAGMA user_version=4")

    def _verify(self, connection, *, version=4):
        if version not in (1, 2, 3, 4) or connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0] != version:
            raise PlanError("unsupported node journal schema")
        columns = {
            "identity": ("authority", "installation", "target"),
            "operations": ("id", "principal", "account", "operation", "intent"),
            "receipts": ("cursor", "operation_id", "sequence", "receipt"),
        }
        if version >= 2:
            columns["allocation_owners"] = ("resource", "operation_id")
        if version >= 3:
            columns["allocation_releases"] = ("operation_id", "resource", "generation", "proof")
            verify_release_index(connection)
        if version >= 4:
            columns["external_owners"] = ("resource", "operation_id")
        for name, expected in columns.items():
            kind = connection.execute(
                "SELECT type FROM sqlite_master WHERE name=?", (name,),
            ).fetchone()
            actual = tuple((row[1], row[6]) for row in
                           connection.execute(f"PRAGMA table_xinfo({name})"))
            if kind != ("table",) or actual != tuple((column, 0) for column in expected):
                raise PlanError("unrecognized node journal schema")
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
            return self._accept(connection, value, intent)

    def accept_named_external(self, intent, *, resource):
        """Serialize cooperative external writers in this canonical journal.

        This is not a distributed backend fence. The adopted effect owner must
        independently exclude other installations from the physical resource.
        UNKNOWN never transfers custody, even to a newer generation.
        """
        identifier(resource, "external resource")
        value = self._scope(intent)
        if value["command"] != "runner.converge":
            raise PlanError("unsupported named external operation")
        with self._transaction() as connection:
            prior = self._find(connection, value)
            held = connection.execute(
                "SELECT operation_id FROM external_owners WHERE resource=?", (resource,),
            ).fetchone()
            if prior:
                OperationIntent(bytes(prior[1])).check_repeat(intent)
                if held != (prior[0],):
                    raise PlanError("external operation no longer owns its resource")
                return self._latest(connection, prior[0])
            if held is not None:
                previous = OperationIntent(bytes(connection.execute(
                    "SELECT intent FROM operations WHERE id=?", held,
                ).fetchone()[0])).to_dict()
                if self._latest(connection, held[0]).to_dict()["status"] not in ("succeeded", "failed"):
                    raise PlanError("external resource has an unsettled operation")
                if value["generation"] <= previous["generation"]:
                    raise PlanError("external resource requires a newer generation")
            accepted = self._accept(connection, value, intent)
            connection.execute(
                "INSERT OR REPLACE INTO external_owners VALUES(?,?)",
                (resource, self._find(connection, value)[0]),
            )
            return accepted

    def _accept(self, connection, value, intent):
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

    def accept_allocation(self, intent, *, resource):
        """Own a canonical local SQLite resource with acceptance; never an external fence."""
        value = self._allocation_scope(intent, resource)
        with self._transaction() as connection:
            prior = self._find(connection, value)
            if prior:
                OperationIntent(bytes(prior[1])).check_repeat(intent)
                release = self._release(connection, prior[0])
                if release is not None:
                    release.check_intent(intent, resource=resource, installation=self.installation)
                    return self._latest(connection, prior[0])
                self._check_owner(connection, prior[0], resource)
                return self._latest(connection, prior[0])
            if connection.execute(
                "SELECT 1 FROM allocation_owners WHERE resource=?", (resource,),
            ).fetchone():
                raise PlanError("node allocation resource is already owned")
            check_released_generation(connection, value["generation"], resource)
            accepted = self._accept(connection, value, intent)
            operation_id = self._find(connection, value)[0]
            connection.execute(
                "INSERT INTO allocation_owners VALUES(?,?)", (resource, operation_id),
            )
            return accepted

    @staticmethod
    def _release(connection, operation_id):
        row = connection.execute(
            "SELECT proof FROM allocation_releases WHERE operation_id=?", (operation_id,),
        ).fetchone()
        return AllocationRelease(bytes(row[0])) if row else None

    def _allocation_scope(self, intent, resource):
        identifier(resource, "node allocation resource")
        value = self._scope(intent)
        if value["command"] != "workspace.allocate":
            raise PlanError("node allocation requires workspace.allocate")
        return value

    @staticmethod
    def _check_owner(connection, operation_id, resource):
        row = connection.execute(
            "SELECT operation_id FROM allocation_owners WHERE resource=?", (resource,),
        ).fetchone()
        if row != (operation_id,):
            raise PlanError("operation does not own the node allocation resource")

    def allocation_owner(self, *, resource):
        """Read held local ownership, not an execution grant or release acknowledgment."""
        identifier(resource, "node allocation resource")
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT o.intent FROM allocation_owners a"
                " JOIN operations o ON o.id=a.operation_id WHERE a.resource=?", (resource,),
            ).fetchone()
            return OperationIntent(bytes(row[0])) if row else None

    def allocation_release(self, intent):
        """Read permanent release evidence; no absence-based permission or transport ack."""
        with self._transaction(write=False) as connection:
            operation_id, _ = self._accepted(connection, intent)
            return self._release(connection, operation_id)

    @contextmanager
    def _cooperative(self, connection):
        owned = {"identity", "operations", "receipts", "allocation_owners", "allocation_releases",
                 "external_owners"}
        def authorize(action, first, second, *_):
            if action in (
                sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
                sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
            ):
                return sqlite3.SQLITE_DENY
            table = None
            if action in (
                sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE,
                sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_VIEW,
                sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_DROP_TEMP_VIEW,
                sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
            ):
                table = first
            elif action in (
                sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
                sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_INDEX,
                sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER,
                sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_TRIGGER,
            ):
                table = second
            elif action == sqlite3.SQLITE_REINDEX and first == RELEASE_INDEX:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY if table and table.lower() in owned else sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        try:
            yield
        finally:
            connection.set_authorizer(None)

    def release_cooperative(self, intent, retire, *, resource):
        """Trusted SQLite-only retirement, tombstone and owner removal commit together."""
        value = self._allocation_scope(intent, resource)
        with self._transaction() as connection:
            operation_id, _ = self._accepted(connection, intent)
            prior = self._release(connection, operation_id)
            if prior is not None:
                prior.check_intent(intent, resource=resource, installation=self.installation)
                return prior
            self._check_owner(connection, operation_id, resource)
            with self._cooperative(connection):
                if retire(connection) is not None:
                    raise PlanError("cooperative retirement must return None")
            self._verify(connection)
            self._accepted(connection, intent)
            self._check_owner(connection, operation_id, resource)
            del value["parameters"]
            value.update(schema="fleet-manager.allocation-release",
                         request_digest=intent.request_digest, resource=resource,
                         installation=self.installation)
            proof = AllocationRelease.from_dict(value)
            connection.execute(
                "INSERT INTO allocation_releases VALUES(?,?,?,?)",
                (operation_id, resource, value["generation"], proof.encoded),
            )
            connection.execute(
                "DELETE FROM allocation_owners WHERE resource=? AND operation_id=?",
                (resource, operation_id),
            )
            return proof

    def commit_cooperative(self, intent, apply, *, resource=None):
        """Trusted short SQLite-only callback returns a succeeded result reference.

        The callback must use this connection without committing, and perform no
        external effects. Its local effect and receipt commit or roll back together.
        """
        value = self._scope(intent)
        if value["command"] == "workspace.allocate":
            self._allocation_scope(intent, resource)
        elif resource is not None:
            raise PlanError("resource binding requires workspace.allocate")
        with self._transaction() as connection:
            operation_id, prior = self._accepted(connection, intent)
            if value["command"] == "workspace.allocate":
                self._check_owner(connection, operation_id, resource)
            status = prior.to_dict()["status"]
            if status in ("succeeded", "failed"):
                return prior
            if status == "unknown":
                raise PlanError("uncertain effect requires explicit reconciliation")
            with self._cooperative(connection):
                result_ref = apply(connection)
            self._verify(connection)
            if value["command"] == "workspace.allocate":
                self._accepted(connection, intent)
                self._check_owner(connection, operation_id, resource)
            receipt = self._receipt(intent, "succeeded",
                                    prior.to_dict()["sequence"] + 1, result_ref)
            self._append(connection, operation_id, receipt)
            return receipt

    def begin_external(self, intent):
        """Persist UNKNOWN before external work; return permission once, never on retry.

        A true return licenses only this caller's already-authorized attempt.
        A crash before or after the effect leaves UNKNOWN and forbids blind retry.
        """
        if self._scope(intent)["command"] == "workspace.allocate":
            raise PlanError("external allocation requires an effect-owner fence")
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
