"""Durable review proposals, not deployment grants, effect receipts or a work queue."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

from .planning import (
    MAX_BYTES, MAX_PLAN_BYTES, PlanError, canonical, configuration_capacity,
    digest, identifier, integer, record,
)

PLAN_CHUNK = 16 * 1024
_APPLICATION = 0x464D4C50
_SCHEMA = {
    "identity": "CREATE TABLE identity(fleet TEXT NOT NULL,installation TEXT NOT NULL)",
    "proposal": (
        "CREATE TABLE proposal(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
        "generation INTEGER NOT NULL,sequence INTEGER NOT NULL,observed INTEGER NOT NULL,"
        "candidate BLOB NOT NULL,plan BLOB,retained BLOB,decision BLOB)"
    ),
}


class InvalidPlanRange(PlanError):
    """A selected current plan exists, but the requested range is invalid."""


class PreparedPlanChanged(PlanError):
    """The selected generation/digest no longer names an available current plan."""


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
        self._plan_cache: tuple[int, str, bytes] | None = None
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

    def _candidate(self, candidate, plan, *, validate_plan=True):
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
        if candidate["plan_bytes"] == 0:
            if (plan is not None or candidate["status"] != "blocked"
                    or candidate["plan_digest"] is not None or candidate["inventory_expires_at"] != 0):
                raise PlanError("missing prepared plan content")
            return
        selected = candidate["plan_digest"]
        if (not isinstance(selected, str) or len(selected) != 64
                or any(character not in "0123456789abcdef" for character in selected)):
            raise PlanError("invalid prepared plan digest")
        if not validate_plan:
            return
        if plan is None:
            raise PlanError("missing prepared plan content")
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
        composition = plan.get("composition")
        if not isinstance(composition, dict) or composition.get("fleet_id") != self.fleet:
            raise PlanError("prepared composition fleet differs from its store")
        suggested = plan.get("suggested_config")
        configuration_capacity(suggested, fleet_id=self.fleet)
        extensions = suggested.get("extensions")
        if (not isinstance(extensions, dict)
                or extensions.get("loadout_composition") != composition):
            raise PlanError("prepared and suggested compositions differ")

    def _read(self, connection, *, include_plan=True):
        row = connection.execute(
            "SELECT generation,sequence,observed,"
            "CASE WHEN length(candidate)<=1048576 THEN candidate END,"
            "CASE WHEN ? AND length(plan)<=4194304 THEN plan END,"
            "CASE WHEN length(decision)<=1048576 THEN decision END,"
            "CASE WHEN plan IS NULL THEN 0 WHEN typeof(plan)='blob' THEN length(plan) ELSE -1 END "
            "FROM proposal", (int(include_plan),),
        ).fetchone()
        if row is None:
            self._plan_cache = None
            return None
        generation, sequence, observed, candidate, encoded_plan, decision, size = row
        for name, value in (("generation", generation), ("sequence", sequence), ("observed", observed)):
            integer(value, f"stored preparation {name}", minimum=0 if name == "observed" else 1)
        candidate = self._json(candidate)
        integer(size, "stored plan size", maximum=MAX_PLAN_BYTES)
        plan = None if encoded_plan is None else self._json(encoded_plan, output=True)
        self._candidate(candidate, plan, validate_plan=include_plan)
        if candidate["plan_bytes"] != size:
            raise PlanError("stored plan size differs from its candidate")
        selected_digest = digest(candidate)
        decision = None if decision is None else self._json(decision)
        if decision is not None:
            if (not isinstance(decision, dict)
                    or set(decision) != {"generation", "digest", "reviewer", "reviewed_at",
                                         "activation_authorized"}
                    or decision["generation"] != generation or decision["digest"] != selected_digest
                    or decision["activation_authorized"] is not False):
                raise PlanError("stored prepared review identity differs")
            self._reviewer(decision["reviewer"])
            integer(decision["reviewed_at"], "stored review time")
        result = {
            "generation": generation, "sequence": sequence, "observed_at": observed,
            "candidate": candidate, "digest": selected_digest, "decision": decision,
        }
        if include_plan:
            result["plan"] = plan
            self._plan_cache = (
                (generation, selected_digest, encoded_plan) if encoded_plan is not None else None
            )
        elif self._plan_cache is not None and self._plan_cache[:2] != (generation, selected_digest):
            self._plan_cache = None
        return result

    def read(self) -> dict | None:
        with self._transaction() as connection:
            return self._read(connection)

    def read_metadata(self) -> dict | None:
        with self._transaction() as connection:
            current = self._read(connection, include_plan=False)
            if (current is not None and current["candidate"]["plan_bytes"]
                    and self._plan_cache is None):
                self._read(connection)
            return current

    def read_plan_range(self, *, generation: int, selected_digest: str, offset: int) -> bytes:
        integer(generation, "selected preparation generation", minimum=1)
        identifier(selected_digest, "selected preparation digest")
        integer(offset, "prepared plan offset")
        with self._transaction() as connection:
            current = self._read(connection, include_plan=False)
            if (current is None or current["generation"] != generation
                    or current["digest"] != selected_digest or not current["candidate"]["plan_bytes"]):
                raise PreparedPlanChanged("selected prepared generation changed")
            if offset >= current["candidate"]["plan_bytes"] or offset % PLAN_CHUNK:
                raise InvalidPlanRange("invalid prepared plan range")
            if self._plan_cache is None:
                self._read(connection)
            cached = self._plan_cache
            if cached is None or cached[:2] != (generation, selected_digest):
                raise PlanError("selected prepared plan is unavailable")
            return cached[2][offset:offset + PLAN_CHUNK]

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
