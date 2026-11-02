"""Read retained v5 snapshot bytes without opening or migrating a node journal."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .allocation import verify_release_index
from .operations import SCOPE, TargetReceipt
from .planning import PlanError, canonical, decode, digest, identifier, integer, record


@dataclass(frozen=True)
class LegacyExternalObservation:
    """Original bytes only; not currentness, execution permission or writer custody."""

    intent: bytes
    receipts: tuple[bytes, ...]
    plan: bytes | None
    attempted_steps: tuple[tuple[str, bytes | None], ...]


def _schema(connection, authority, installation, target):
    if connection.execute("PRAGMA user_version").fetchone() != (5,):
        raise PlanError("legacy observation requires node journal generation 5")
    columns = {
        "identity": ("authority", "installation", "target"),
        "operations": ("id", "principal", "account", "operation", "intent"),
        "receipts": ("cursor", "operation_id", "sequence", "receipt"),
        "allocation_owners": ("resource", "operation_id"),
        "allocation_releases": ("operation_id", "resource", "generation", "proof"),
        "external_owners": ("resource", "operation_id"),
        "external_plans": ("operation_id", "resource", "plan"),
        "external_steps": ("operation_id", "step", "observation"),
    }
    for name, expected in columns.items():
        if connection.execute(
                "SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone() != ("table",):
            raise PlanError("legacy node journal schema differs")
        actual = tuple((row[1], row[6]) for row in connection.execute(f"PRAGMA table_xinfo({name})"))
        if actual != tuple((column, 0) for column in expected):
            raise PlanError("legacy node journal schema differs")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
        raise PlanError("legacy node journal has unsupported executable schema")
    verify_release_index(connection)
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise PlanError("legacy node journal references are inconsistent")
    if connection.execute("SELECT authority,installation,target FROM identity LIMIT 2").fetchall() != [
            (authority, installation, target)]:
        raise PlanError("legacy node journal identity mismatch")


def _blob(value):
    if type(value) is not bytes or not 1 <= len(value) <= 1024 * 1024:
        raise PlanError("legacy node journal evidence is not a bounded blob")
    return value


def _request(encoded):
    value = decode(_blob(encoded))
    record(value, "fleet-manager.operation-intent", {*SCOPE, "command", "parameters"})
    for field in SCOPE[:-1]:
        identifier(value[field], "legacy operation " + field)
    integer(value["generation"], "legacy operation generation", minimum=1)
    if (canonical(value) != encoded or value["command"] != "manager.converge"
            or type(value["parameters"]) is not dict
            or set(value["parameters"]) != {"instance", "deployment"}):
        raise PlanError("legacy observation requires its original v5 manager intent")
    for field, item in value["parameters"].items():
        identifier(item, "legacy operation " + field)
    return value


def _plan(encoded, request):
    value = decode(encoded)
    if (canonical(value) != encoded or type(value) is not dict
            or set(value) != {"deployment", "steps", "context"}
            or type(value["deployment"]) is not dict or type(value["context"]) is not dict
            or digest(value["deployment"]) != request["parameters"]["deployment"]
            or type(value["steps"]) is not list or not 1 <= len(value["steps"]) <= 32):
        raise PlanError("legacy external plan differs from its original operation")
    names = []
    for step in value["steps"]:
        if type(step) is not dict or set(step) != {"id", "action", "target"}:
            raise PlanError("legacy external plan step differs")
        for item in step.values():
            identifier(item, "legacy external plan step")
        names.append(step["id"])
    if len(set(names)) != len(names):
        raise PlanError("legacy external plan steps are ambiguous")
    return names


def inspect_legacy_external(snapshot: bytes, *, authority: str, installation: str,
                            target: str, request: bytes,
                            resource: str) -> LegacyExternalObservation:
    """Inspect one owner-supplied coherent snapshot, never a live database path.

    The producer must establish coherent capture and authenticate its original
    owner independently. No snapshot proves present custody or health. WAL/hot
    journal capture and repaired-runtime execution are deliberately unsupported.
    """
    for name, value in (("authority", authority), ("installation", installation),
                        ("target", target), ("resource", resource)):
        identifier(value, "legacy node " + name)
    if (type(snapshot) is not bytes or not 100 <= len(snapshot) <= 64 * 1024 * 1024
            or snapshot[:16] != b"SQLite format 3\0" or snapshot[18:20] != b"\x01\x01"):
        raise PlanError("legacy observation requires bounded rollback-mode snapshot bytes")
    value = _request(request)
    if value["authority"] != authority or value["target"] != target:
        raise PlanError("legacy observation requires its exact original manager operation")
    if not callable(getattr(sqlite3.Connection, "deserialize", None)):
        raise PlanError("legacy snapshot observation requires SQLite deserialize support")
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(snapshot)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        _schema(connection, authority, installation, target)
        operations = connection.execute(
            "SELECT id,intent FROM operations WHERE principal=? AND account=? AND operation=? LIMIT 2",
            (value["principal"], value["account"], value["operation"]),
        ).fetchall()
        if len(operations) != 1:
            raise PlanError("legacy original operation is missing or ambiguous")
        operation, original = operations[0]
        original = _blob(original)
        if original != request:
            raise PlanError("legacy original operation has conflicting content")
        if connection.execute(
                "SELECT operation_id FROM external_owners WHERE resource=? LIMIT 2",
                (resource,)).fetchall() != [(operation,)]:
            raise PlanError("legacy original operation does not own its resource")
        rows = connection.execute(
            "SELECT sequence,receipt FROM receipts WHERE operation_id=? ORDER BY sequence,cursor LIMIT 257",
            (operation,),
        ).fetchall()
        if not 1 <= len(rows) <= 256:
            raise PlanError("legacy original receipt history is missing or unbounded")
        receipts = []
        prior = None
        for expected, (sequence, raw) in enumerate(rows, 1):
            raw = _blob(raw)
            receipt = TargetReceipt(raw)
            current = receipt.to_dict()
            if (any(current[field] != value[field] for field in (*SCOPE, "command"))
                    or current["request_digest"] != digest(value)):
                raise PlanError("legacy original receipt correlation differs")
            status = current["status"]
            if (sequence != expected or current["sequence"] != expected
                    or (expected == 1 and status != "accepted")
                    or (expected > 1 and (prior in ("succeeded", "failed") or status == "accepted"))):
                raise PlanError("legacy original receipt history is inconsistent")
            receipts.append(raw)
            prior = status
        plans = connection.execute(
            "SELECT resource,plan FROM external_plans WHERE operation_id=? LIMIT 2", (operation,),
        ).fetchall()
        steps = connection.execute(
            "SELECT step,observation FROM external_steps WHERE operation_id=? ORDER BY step LIMIT 33",
            (operation,),
        ).fetchall()
        if len(plans) > 1 or len(steps) > 32 or (not plans and steps):
            raise PlanError("legacy original external plan is ambiguous")
        plan = None
        if plans:
            if plans[0][0] != resource:
                raise PlanError("legacy original external plan resource differs")
            plan = _blob(plans[0][1])
            names = _plan(plan, value)
            seen = set()
            for name, evidence in steps:
                if type(name) is not str or name not in names or name in seen:
                    raise PlanError("legacy original external step is ambiguous")
                seen.add(name)
                if evidence is not None:
                    evidence = _blob(evidence)
                    observed = decode(evidence)
                    if type(observed) is not dict or not observed or canonical(observed) != evidence:
                        raise PlanError("legacy original external step evidence differs")
        return LegacyExternalObservation(original, tuple(receipts), plan, tuple(steps))
    except sqlite3.Error:
        raise PlanError("legacy node journal snapshot could not be verified") from None
    finally:
        connection.close()
