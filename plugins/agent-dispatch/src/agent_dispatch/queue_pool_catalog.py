"""Provider-side publication storage; no supervisor enrollment or task placement."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .pool_instances import PoolInstanceDefinition, ordered_pool_instances
from .queue_pool_cohort import (
    PoolConfiguredCohort, QueuePoolCohortMixin, cohort_from_clock, require_cohort_scope,
)
from .queue_records import TaskError

POOL_CATALOG_VERSION = 1
POOL_PUBLICATION_TTL_SECONDS = 30.0


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TaskError(f"pool publication requires an explicit {field}")
    return value


def _revision(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise TaskError(f"pool publication {field} must be a nonnegative integer")
    return value


def advance_pool_registry_revision(conn: sqlite3.Connection) -> None:
    """Invalidate publication in the registration writer's existing transaction."""
    conn.execute(
        "UPDATE pool_catalog_clock SET revision = revision + 1, "
        "registration_revision = registration_revision + 1 WHERE singleton = 1"
    )


@dataclass(frozen=True)
class PoolPublicationScope:
    machine: str
    env: str
    publisher_id: str
    generation: int
    revision: int
    state: str
    input_revision: str | None
    registration_revision: int | None
    valid_until: float | None
    definitions: tuple[PoolInstanceDefinition, ...]
    definitions_generation: int | None
    definitions_revision: int | None
    reason: str | None
    enrollment_cohort_revision: int | None
    definitions_cohort_revision: int | None


@dataclass(frozen=True)
class PoolCatalogSnapshot:
    version: int
    revision: int
    registration_revision: int
    scopes: tuple[PoolPublicationScope, ...]
    definitions: tuple[PoolInstanceDefinition, ...]
    blockers: tuple[str, ...]
    configured_cohort: PoolConfiguredCohort

    @property
    def ready(self) -> bool:
        """Retained metadata readiness, not current-file or execution support."""
        return bool(self.scopes) and not self.blockers

    @property
    def configured_ready(self) -> bool:
        """Configured coverage only; explicit empty intent can be confirmed."""
        observation = self.configured_cohort.observation
        return observation is not None and observation.complete and not self.blockers


class QueuePoolCatalogMixin(QueuePoolCohortMixin):
    """Internal primitives for the control-authenticated reconciliation owner.

    Only that owner may account for a publisher and publish its effective set.
    Metadata authorization is not proof of the publisher's OS-lock admission.
    Ordinary allocators cannot supply their preferred pool subset. No production
    supervisor or allocator consumes these primitives.
    """

    if TYPE_CHECKING:
        _connect: Callable[[], sqlite3.Connection]
        _now: Callable[[float | None], float]

    @staticmethod
    def _migrate_pool_catalog(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pool_catalog_clock ("
            " singleton INTEGER PRIMARY KEY CHECK(singleton = 1),"
            " revision INTEGER NOT NULL, registration_revision INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO pool_catalog_clock"
            " (singleton, revision, registration_revision) VALUES (1, 0, 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pool_publication_scopes ("
            " machine TEXT NOT NULL, env TEXT NOT NULL, publisher_id TEXT NOT NULL,"
            " generation INTEGER NOT NULL, revision INTEGER NOT NULL,"
            " state TEXT NOT NULL, input_revision TEXT, registration_revision INTEGER,"
            " valid_until REAL, definitions TEXT NOT NULL, reason TEXT,"
            " definitions_generation INTEGER, definitions_revision INTEGER,"
            " request TEXT, PRIMARY KEY(machine, env))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pool_publication_generations ("
            " machine TEXT NOT NULL, env TEXT NOT NULL, publisher_id TEXT NOT NULL,"
            " generation INTEGER NOT NULL, created_at REAL NOT NULL,"
            " PRIMARY KEY(machine, env, generation), UNIQUE(machine, env, publisher_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pool_publication_history ("
            " machine TEXT NOT NULL, env TEXT NOT NULL, generation INTEGER NOT NULL,"
            " revision INTEGER NOT NULL, request TEXT NOT NULL, accepted_at REAL NOT NULL,"
            " PRIMARY KEY(machine, env, generation, revision))"
        )
        QueuePoolCohortMixin._migrate_pool_cohort(conn)

    @staticmethod
    def _pool_scope_from_row(row: sqlite3.Row) -> PoolPublicationScope:
        return PoolPublicationScope(
            row["machine"], row["env"], row["publisher_id"], row["generation"],
            row["revision"], row["state"], row["input_revision"],
            row["registration_revision"], row["valid_until"],
            ordered_pool_instances(
                PoolInstanceDefinition.from_dict(value)
                for value in json.loads(row["definitions"])
            ),
            row["definitions_generation"], row["definitions_revision"],
            row["reason"],
            row["enrollment_cohort_revision"], row["definitions_cohort_revision"],
        )

    def pool_registration_revision(self) -> int:
        """Take this before the owner's registry/declaration/override collection."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT registration_revision FROM pool_catalog_clock WHERE singleton = 1"
            ).fetchone()["registration_revision"]

    def begin_pool_publication(
        self, machine: str, env: str, publisher_id: str, *,
        expected_generation: int, now: float | None = None,
        expected_cohort_revision: int | None = None,
    ) -> PoolPublicationScope:
        """CAS a new lock-holder incarnation; never auto-take over from age.

        Enrollment is a separate control-owner obligation. The owning caller
        must retain its original request after an unknown result, not rebase a
        failed acquisition onto a newer generation.
        """
        machine, env = _identity(machine, "machine"), _identity(env, "environment")
        publisher_id = _identity(publisher_id, "publisher_id")
        expected_generation = _revision(expected_generation, "expected_generation")
        if expected_cohort_revision is not None:
            expected_cohort_revision = _revision(expected_cohort_revision, "cohort revision")
        ts = self._now(now)
        if not math.isfinite(ts):
            raise TaskError("pool publication requires a finite observation time")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM pool_publication_scopes WHERE machine = ? AND env = ?",
                (machine, env),
            ).fetchone()
            generation = row["generation"] if row is not None else 0
            if row is not None and (
                row["publisher_id"] == publisher_id
                and expected_generation + 1 == generation
                and row["enrollment_cohort_revision"] == expected_cohort_revision
            ):
                return self._pool_scope_from_row(row)
            require_cohort_scope(conn, machine, env, expected_cohort_revision)
            if expected_generation != generation:
                raise TaskError("pool publisher generation changed; refusing enrollment")
            seen = conn.execute(
                "SELECT 1 FROM pool_publication_generations "
                "WHERE machine = ? AND env = ? AND publisher_id = ?",
                (machine, env, publisher_id),
            ).fetchone()
            if seen is not None:
                raise TaskError("pool publisher incarnation cannot be re-enrolled")
            conn.execute(
                "INSERT INTO pool_publication_generations VALUES (?, ?, ?, ?, ?)",
                (machine, env, publisher_id, generation + 1, ts),
            )
            conn.execute(
                "INSERT INTO pool_publication_scopes"
                " (machine, env, publisher_id, generation, revision, state, definitions,"
                " enrollment_cohort_revision)"
                " VALUES (?, ?, ?, ?, 0, 'pending', '[]', ?)"
                " ON CONFLICT(machine, env) DO UPDATE SET"
                " publisher_id = excluded.publisher_id, generation = excluded.generation,"
                " revision = 0, state = 'pending', request = NULL, reason = NULL,"
                " enrollment_cohort_revision = excluded.enrollment_cohort_revision",
                (machine, env, publisher_id, generation + 1, expected_cohort_revision),
            )
            conn.execute("UPDATE pool_catalog_clock SET revision = revision + 1 WHERE singleton = 1")
            row = conn.execute(
                "SELECT * FROM pool_publication_scopes WHERE machine = ? AND env = ?",
                (machine, env),
            ).fetchone()
            return self._pool_scope_from_row(row)

    def publish_pool_catalog_scope(
        self, machine: str, env: str, publisher_id: str, *,
        generation: int, expected_revision: int, registration_revision: int,
        input_revision: str, definitions: tuple[PoolInstanceDefinition, ...],
        reason: str | None = None, now: float | None = None,
        expected_cohort_revision: int | None = None,
    ) -> PoolPublicationScope:
        """Atomically replace the entire scope, or record unavailable inputs.

        ``input_revision`` is the existing owner's complete effective-input
        revision, not a completeness proof supplied by an allocator. No caller
        is migrated until that owner and its transport are implemented.
        """
        machine, env = _identity(machine, "machine"), _identity(env, "environment")
        publisher_id = _identity(publisher_id, "publisher_id")
        input_revision = _identity(input_revision, "input_revision")
        generation = _revision(generation, "generation")
        expected_revision = _revision(expected_revision, "expected_revision")
        registration_revision = _revision(registration_revision, "registration_revision")
        if expected_cohort_revision is not None:
            expected_cohort_revision = _revision(expected_cohort_revision, "cohort revision")
        if not isinstance(definitions, tuple) or any(
            not isinstance(value, PoolInstanceDefinition) for value in definitions
        ):
            raise TaskError("pool publication requires normalized definitions")
        definitions = ordered_pool_instances(definitions)
        if any((value.machine, value.env) != (machine, env) for value in definitions):
            raise TaskError("pool publication contains a foreign instance scope")
        if reason is not None:
            reason = _identity(reason, "unavailable reason")
            if definitions:
                raise TaskError("unavailable pool publication cannot present a partial set")
        encoded = json.dumps(
            [value.to_dict() for value in definitions], sort_keys=True, separators=(",", ":")
        )
        request_fields = [registration_revision, input_revision, encoded, reason]
        if expected_cohort_revision is not None:
            request_fields.append(expected_cohort_revision)
        request = json.dumps(request_fields, separators=(",", ":"))
        ts = self._now(now)
        if not math.isfinite(ts):
            raise TaskError("pool publication requires a finite observation time")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM pool_publication_scopes WHERE machine = ? AND env = ?",
                (machine, env),
            ).fetchone()
            if row is None or (row["publisher_id"], row["generation"]) != (publisher_id, generation):
                raise TaskError("pool publisher is not the current enrolled generation")
            if row["revision"] != expected_revision:
                if row["revision"] == expected_revision + 1 and row["request"] == request:
                    return self._pool_scope_from_row(row)
                raise TaskError("pool publication revision changed; refusing stale snapshot")
            require_cohort_scope(conn, machine, env, expected_cohort_revision)
            current = conn.execute(
                "SELECT registration_revision FROM pool_catalog_clock WHERE singleton = 1"
            ).fetchone()["registration_revision"]
            if registration_revision != current:
                raise TaskError("registration inputs changed during pool collection")
            conn.execute(
                "UPDATE pool_publication_scopes SET revision = revision + 1, state = ?,"
                " input_revision = ?, registration_revision = ?, valid_until = ?,"
                " definitions = CASE WHEN ? IS NULL THEN ? ELSE definitions END,"
                " definitions_generation = CASE WHEN ? IS NULL THEN ? ELSE definitions_generation END,"
                " definitions_revision = CASE WHEN ? IS NULL THEN ? ELSE definitions_revision END,"
                " definitions_cohort_revision = CASE WHEN ? IS NULL THEN ? ELSE definitions_cohort_revision END,"
                " reason = ?, request = ? WHERE machine = ? AND env = ?",
                (
                    "complete" if reason is None else "unavailable", input_revision,
                    registration_revision, ts + POOL_PUBLICATION_TTL_SECONDS,
                    reason, encoded, reason, generation, reason, expected_revision + 1,
                    reason, expected_cohort_revision,
                    reason, request, machine, env,
                ),
            )
            conn.execute(
                "INSERT INTO pool_publication_history VALUES (?, ?, ?, ?, ?, ?)",
                (machine, env, generation, expected_revision + 1, request, ts),
            )
            conn.execute("UPDATE pool_catalog_clock SET revision = revision + 1 WHERE singleton = 1")
            row = conn.execute(
                "SELECT * FROM pool_publication_scopes WHERE machine = ? AND env = ?",
                (machine, env),
            ).fetchone()
            return self._pool_scope_from_row(row)

    @classmethod
    def _pool_catalog_from_conn(cls, conn: sqlite3.Connection, *, now: float) -> PoolCatalogSnapshot:
        """All expected scopes under the caller's transaction, never a subset."""
        clock = conn.execute("SELECT * FROM pool_catalog_clock WHERE singleton = 1").fetchone()
        scopes = tuple(
            cls._pool_scope_from_row(row)
            for row in conn.execute("SELECT * FROM pool_publication_scopes ORDER BY machine, env")
        )
        blockers: list[str] = []
        cohort = cohort_from_clock(clock)
        observation = cohort.observation
        if cohort.revision:
            if observation is None or not observation.complete:
                blockers.append("configured pool cohort is unconfirmed or unavailable")
            else:
                expected = {(member.execution_machine, member.env) for member in observation.members}
                enrolled = {(scope.machine, scope.env) for scope in scopes}
                blockers.extend(
                    f"{machine}/{env}: configured scope has not enrolled"
                    for machine, env in sorted(expected - enrolled)
                )
                blockers.extend(
                    f"{machine}/{env}: enrolled scope is not configured"
                    for machine, env in sorted(enrolled - expected)
                )
        elif not scopes:
            blockers.append("no enrolled pool publication scopes")
        for scope in scopes:
            problem = (
                scope.state if scope.state != "complete"
                else "publication attribution mismatch"
                if (scope.definitions_generation, scope.definitions_revision) != (
                    scope.generation, scope.revision,
                )
                else "registration inputs changed"
                if scope.registration_revision != clock["registration_revision"]
                else "configured cohort changed"
                if cohort.revision and scope.definitions_cohort_revision != cohort.revision
                else "publication expired"
                if scope.valid_until is None or now >= scope.valid_until
                else None
            )
            if problem:
                blockers.append(f"{scope.machine}/{scope.env}: {problem}")
        return PoolCatalogSnapshot(
            POOL_CATALOG_VERSION, clock["revision"], clock["registration_revision"],
            scopes, ordered_pool_instances(value for scope in scopes for value in scope.definitions),
            tuple(blockers), cohort,
        )

    def pool_catalog_snapshot(self, *, now: float | None = None) -> PoolCatalogSnapshot:
        """Inspection only; placement must recheck inside its own write transaction."""
        ts = self._now(now)
        if not math.isfinite(ts):
            raise TaskError("pool catalog requires a finite observation time")
        with self._connect() as conn:
            conn.execute("BEGIN")
            return self._pool_catalog_from_conn(conn, now=ts)
