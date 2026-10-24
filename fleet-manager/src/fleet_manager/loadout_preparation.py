"""Durable review proposals, not deployment grants, effect receipts or a work queue."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

from .planning import MAX_BYTES, MAX_PLAN_BYTES, PlanError, canonical, digest, identifier, integer, record

_APPLICATION = 0x464D4C50
_SCHEMA = {
    "identity": "CREATE TABLE identity(fleet TEXT NOT NULL,installation TEXT NOT NULL)",
    "proposal": (
        "CREATE TABLE proposal(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
        "generation INTEGER NOT NULL,sequence INTEGER NOT NULL,observed INTEGER NOT NULL,"
        "candidate BLOB NOT NULL,plan BLOB,retained BLOB,decision BLOB)"
    ),
}


class PreparationStore:
    """One explicitly owned current proposal and retained last valid plan.

    Authentication and controller lifetime belong to the embedding. A stored
    review decision must be revalidated by the deployment owner; it is never
    an offline permit, accepted operation or proof of readiness.
    """

    def __init__(self, path: Path, *, fleet: str, installation: str) -> None:
        identifier(fleet, "preparation fleet")
        identifier(installation, "preparation installation")
        self.path = path.resolve()
        self.fleet, self.installation = fleet, installation
        with self._transaction(initialize=True):
            pass

    @contextmanager
    def _transaction(self, *, initialize=False):
        connection = sqlite3.connect(
            str(self.path) if initialize else self.path.as_uri() + "?mode=rw",
            uri=not initialize, timeout=1, isolation_level=None,
        )
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            objects = dict(connection.execute(
                "SELECT CASE WHEN length(CAST(name AS BLOB))<=128 THEN name END,"
                "CASE WHEN length(CAST(sql AS BLOB))<=4096 THEN sql END "
                "FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 3",
            ))
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            if initialize and not objects and not version and not application:
                for statement in _SCHEMA.values():
                    connection.execute(statement)
                connection.execute("INSERT INTO identity VALUES(?,?)",
                                   (self.fleet, self.installation))
                connection.execute(f"PRAGMA application_id={_APPLICATION}")
                connection.execute("PRAGMA user_version=1")
            elif objects != _SCHEMA or version != 1 or application != _APPLICATION:
                raise PlanError("unrecognized loadout preparation store")
            if connection.execute("SELECT fleet,installation FROM identity LIMIT 2").fetchall() != [
                (self.fleet, self.installation),
            ]:
                raise PlanError("loadout preparation store identity mismatch")
            yield connection
            connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @staticmethod
    def _json(raw: bytes, *, output=False):
        if type(raw) is not bytes or len(raw) > (MAX_PLAN_BYTES if output else MAX_BYTES):
            raise PlanError("invalid stored preparation bytes")
        try:
            value = json.loads(raw)
            if canonical(value, output=output) != raw:
                raise PlanError("invalid stored preparation encoding")
        except (ValueError, UnicodeError, RecursionError):
            raise PlanError("invalid stored preparation encoding") from None
        return value

    def _candidate(self, candidate, plan):
        record(candidate, "fleet-manager.loadout-candidate", {
            "fleet", "installation", "status", "reason", "sources", "plan_digest",
            "plan_bytes", "inventory_expires_at", "execution_admission",
        })
        if ((candidate["fleet"], candidate["installation"]) != (self.fleet, self.installation)
                or candidate["status"] not in ("prepared", "blocked")
                or candidate["execution_admission"] != "not-implemented"
                or not isinstance(candidate["sources"], dict)):
            raise PlanError("invalid prepared candidate binding")
        integer(candidate["plan_bytes"], "prepared plan bytes", maximum=MAX_PLAN_BYTES)
        integer(candidate["inventory_expires_at"], "prepared inventory expiry")
        if candidate["reason"] is not None:
            identifier(candidate["reason"], "preparation reason")
        if (candidate["status"] == "blocked") != (candidate["reason"] is not None):
            raise PlanError("prepared candidate requires an explicit degraded reason")
        if plan is None:
            if (candidate["status"] != "blocked" or candidate["plan_digest"] is not None
                    or candidate["plan_bytes"] != 0 or candidate["inventory_expires_at"] != 0):
                raise PlanError("missing prepared plan content")
            return
        encoded = canonical(plan, output=True)
        if (not isinstance(plan, dict) or plan.get("schema") != "fleet-manager.loadout-design"
                or plan.get("version") != 1
                or plan.get("activation_authorized") is not False
                or plan.get("live_readiness_verified") is not False
                or candidate["plan_digest"] != hashlib.sha256(encoded).hexdigest()
                or candidate["plan_bytes"] != len(encoded)
                or candidate["inventory_expires_at"] != plan.get("inventory_expires_at")
                or (candidate["status"] == "blocked") != bool(plan.get("blockers"))
                or plan.get("digest") != digest(
                    {key: value for key, value in plan.items() if key != "digest"}, output=True,
                )):
            raise PlanError("prepared plan content differs from its candidate")

    def _read(self, connection):
        row = connection.execute(
            "SELECT generation,sequence,observed,"
            "CASE WHEN length(candidate)<=1048576 THEN candidate END,"
            "CASE WHEN length(plan)<=4194304 THEN plan END,"
            "CASE WHEN length(decision)<=1048576 THEN decision END FROM proposal",
        ).fetchone()
        if row is None:
            return None
        generation, sequence, observed, candidate, plan, decision = row
        for name, value in (("generation", generation), ("sequence", sequence), ("observed", observed)):
            integer(value, f"stored preparation {name}", minimum=0 if name == "observed" else 1)
        candidate = self._json(candidate)
        plan = None if plan is None else self._json(plan, output=True)
        self._candidate(candidate, plan)
        decision = None if decision is None else self._json(decision)
        if decision is not None:
            if (not isinstance(decision, dict)
                    or set(decision) != {"generation", "digest", "reviewer", "reviewed_at",
                                         "activation_authorized"}
                    or decision["generation"] != generation or decision["digest"] != digest(candidate)
                    or decision["activation_authorized"] is not False):
                raise PlanError("stored prepared review identity differs")
            self._reviewer(decision["reviewer"])
            integer(decision["reviewed_at"], "stored review time")
        return {
            "generation": generation, "sequence": sequence, "observed_at": observed,
            "candidate": candidate, "digest": digest(candidate),
            "plan": plan, "decision": decision,
        }

    def read(self) -> dict | None:
        with self._transaction() as connection:
            return self._read(connection)

    def publish(self, candidate: dict, plan: dict | None, *, expected: int, now: int) -> dict:
        encoded = canonical(candidate)
        plan_bytes = None if plan is None else canonical(plan, output=True)
        self._candidate(candidate, plan)
        integer(expected, "expected preparation generation")
        integer(now, "preparation observation time")
        with self._transaction() as connection:
            current = self._read(connection)
            generation = current["generation"] if current else 0
            if generation != expected:
                raise PlanError("prepared generation changed during acquisition")
            if current and current["candidate"] == candidate:
                if current["plan"] != plan:
                    raise PlanError("prepared candidate has conflicting plan content")
                return current
            integer(generation, "preparation generation", maximum=2**63 - 2)
            sequence = current["sequence"] if current else 0
            integer(sequence, "preparation sequence", maximum=2**63 - 2)
            retained = connection.execute(
                "SELECT CASE WHEN typeof(retained)='blob' AND length(retained)<=4194304 "
                "THEN retained END,retained IS NULL FROM proposal",
            ).fetchone()
            if retained is not None and retained[0] is None and not retained[1]:
                raise PlanError("invalid retained preparation plan")
            previous = retained[0] if retained else None
            connection.execute(
                "INSERT OR REPLACE INTO proposal VALUES(1,?,?,?,?,?,?,NULL)",
                (generation + 1, sequence + 1, now, encoded, plan_bytes,
                 plan_bytes if plan is not None and not plan["blockers"] else previous),
            )
            return self._read(connection)

    @staticmethod
    def _reviewer(reviewer):
        if not isinstance(reviewer, dict) or set(reviewer) != {"principal", "account", "policy_revision"}:
            raise PlanError("an explicit current review scope is required")
        for value in reviewer.values():
            identifier(value, "review scope")

    def confirm(self, *, generation: int, selected_digest: str, reviewer: dict, now: int) -> dict:
        integer(generation, "selected preparation generation", minimum=1)
        identifier(selected_digest, "selected preparation digest")
        self._reviewer(reviewer)
        integer(now, "review time")
        with self._transaction() as connection:
            current = self._read(connection)
            if (current is None or current["generation"] != generation
                    or current["digest"] != selected_digest):
                raise PlanError("selected prepared generation changed")
            candidate = current["candidate"]
            if (candidate["status"] != "prepared" or current["plan"] is None
                    or now >= candidate["inventory_expires_at"]):
                raise PlanError("selected prepared generation is blocked or expired")
            if current["decision"] is not None:
                if current["decision"]["reviewer"] != reviewer:
                    raise PlanError("prepared review decision belongs to another scope")
                return current
            decision = {
                "generation": generation, "digest": selected_digest,
                "reviewer": reviewer, "reviewed_at": now,
                "activation_authorized": False,
            }
            integer(current["sequence"], "preparation sequence", maximum=2**63 - 2)
            connection.execute(
                "UPDATE proposal SET sequence=sequence+1,observed=?,decision=? WHERE singleton=1",
                (now, canonical(decision)),
            )
            return self._read(connection)
