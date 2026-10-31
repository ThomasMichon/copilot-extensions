"""Enrollment transactions in the existing opt-in operation journal, never a second DB."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, canonical, decode, digest

if TYPE_CHECKING:
    from .ledger import OperationLedger

T = TypeVar("T")
TABLES = {
    "enrollment_floors": (
        "CREATE TABLE enrollment_floors("
        "gateway TEXT PRIMARY KEY NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL)"),
    "machine_enrollments": (
        "CREATE TABLE machine_enrollments("
        "operation_id INTEGER PRIMARY KEY REFERENCES operations(id),"
        "gateway TEXT NOT NULL,machine TEXT NOT NULL,principal TEXT NOT NULL,"
        "key_thumbprint TEXT NOT NULL,binding BLOB NOT NULL,withdrawn_revision INTEGER,"
        "UNIQUE(gateway,machine),UNIQUE(gateway,principal),UNIQUE(gateway,key_thumbprint))"),
}


def create_schema(connection: sqlite3.Connection) -> None:
    for statement in TABLES.values():
        connection.execute(statement)


def verify_schema(connection: sqlite3.Connection) -> None:
    for name, expected in TABLES.items():
        actual = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if actual != (expected,):
            raise PlanError("unrecognized enrollment journal constraints")


def _floor(connection: sqlite3.Connection, gateway: str, revision: int, fingerprint: str) -> None:
    prior = connection.execute(
        "SELECT revision,digest FROM enrollment_floors WHERE gateway=?", (gateway,)).fetchone()
    if prior is not None:
        if revision < prior[0]:
            raise PlanError("stale enrollment policy")
        if revision == prior[0] and fingerprint != prior[1]:
            raise PlanError("conflicting enrollment policy")
        if revision == prior[0]:
            return
    connection.execute(
        "INSERT INTO enrollment_floors VALUES(?,?,?) ON CONFLICT(gateway) "
        "DO UPDATE SET revision=excluded.revision,digest=excluded.digest",
        (gateway, revision, fingerprint))


def publish(ledger: OperationLedger, *, gateway: str, revision: int,
            fingerprint: str, validate: Callable[[tuple[dict, ...]], T],
            included: Callable[[dict], bool]) -> T:
    """Retain the accepted owner-policy floor before exposing admitted bindings."""
    if ledger._version != 5:
        raise PlanError("enrollment journal migration is required")
    with ledger._transaction() as connection:
        rows = connection.execute(
            "SELECT operation_id,binding FROM machine_enrollments "
            "WHERE gateway=? AND withdrawn_revision IS NULL ORDER BY operation_id",
            (gateway,)).fetchall()
        records = []
        for operation_id, raw in rows:
            binding = decode(bytes(raw))
            if included(binding):
                records.append(binding)
            else:
                connection.execute(
                    "UPDATE machine_enrollments SET withdrawn_revision=? WHERE operation_id=?",
                    (revision, operation_id))
                ledger._event(connection, operation_id, "machine-enrollment-withdrawn")
        result = validate(tuple(records))
        _floor(connection, gateway, revision, fingerprint)
        return result


def admit(ledger: OperationLedger, intent: OperationIntent, *, gateway: str,
          revision: int, fingerprint: str, binding: dict, thumbprint: str,
          validate: Callable[[tuple[dict, ...]], object]) -> int:
    """Fence source floors, original operation and exclusive identity atomically."""
    value = ledger._scope(intent)
    if (ledger._version != 5 or value["command"] != "machine.enroll"
            or value["parameters"] != {"enrollment": digest(binding)}
            or value["principal"] != binding["scope"]["principal"]
            or value["account"] != binding["scope"]["account"]
            or value["target"] != binding["scope"]["machine"]
            or value["generation"] != binding["scope"]["generation"]):
        raise PlanError("enrollment intent binding mismatch")
    with ledger._transaction() as connection:
        _floor(connection, gateway, revision, fingerprint)
        prior = ledger._find(connection, value)
        if prior is not None:
            OperationIntent(bytes(prior[1])).check_repeat(intent)
            retained = connection.execute(
                "SELECT binding,withdrawn_revision FROM machine_enrollments WHERE operation_id=?", (prior[0],)
            ).fetchone()
            if retained is None or retained[1] is not None or bytes(retained[0]) != canonical(binding):
                raise PlanError("enrollment operation conflicts with retained binding")
            return prior[0]
        scope = binding["scope"]
        if connection.execute(
            "SELECT count(*) FROM machine_enrollments WHERE gateway=?", (gateway,),
        ).fetchone()[0] >= 128:
            raise PlanError("enrollment claim capacity exhausted")
        if connection.execute(
            "SELECT 1 FROM machine_enrollments WHERE gateway=? "
            "AND (machine=? OR principal=? OR key_thumbprint=?)",
            (gateway, scope["machine"], scope["principal"], thumbprint),
        ).fetchone():
            raise PlanError("machine enrollment already exists; inspect original operation")
        operation_id = ledger._admit(connection, value, intent)
        connection.execute("INSERT INTO machine_enrollments VALUES(?,?,?,?,?,?,?)", (
            operation_id, gateway, scope["machine"], scope["principal"], thumbprint,
            canonical(binding), None))
        records = connection.execute(
            "SELECT binding FROM machine_enrollments WHERE gateway=? "
            "AND withdrawn_revision IS NULL ORDER BY operation_id",
            (gateway,)).fetchall()
        validate(tuple(decode(bytes(row[0])) for row in records))
        connection.execute("UPDATE outbox SET state='accepted' WHERE operation_id=?", (operation_id,))
        ledger._event(connection, operation_id, "machine-enrolled")
        return operation_id


def reconcile(ledger: OperationLedger, *, gateway: str, revision: int, fingerprint: str,
              validate: Callable[[tuple[dict, ...]], object],
              included: Callable[[dict], bool]) -> None:
    """Settle only exact bindings in the installed publication, including after restart."""
    with ledger._transaction() as connection:
        _floor(connection, gateway, revision, fingerprint)
        rows = connection.execute(
            "SELECT m.binding,o.intent FROM machine_enrollments m "
            "JOIN operations o ON o.id=m.operation_id WHERE m.gateway=? "
            "AND m.withdrawn_revision IS NULL ORDER BY m.operation_id",
            (gateway,)).fetchall()
        records = tuple(decode(bytes(row[0])) for row in rows)
        validate(records)
        for row, binding in zip(rows, records):
            if not included(binding):
                continue
            intent = OperationIntent(bytes(row[1]))
            value = intent.to_dict()
            receipt = {key: value[key] for key in (
                "operation", "authority", "principal", "account", "target", "generation", "command")}
            receipt.update({
                "schema": "fleet-manager.target-receipt", "version": 1,
                "request_digest": intent.request_digest, "sequence": 1,
                "status": "succeeded", "result_ref": digest(binding),
            })
            ledger._record_receipt(connection, value, intent, TargetReceipt.from_dict(receipt))
