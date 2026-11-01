"""Configured-cohort projection under the existing catalog transaction owner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import math
import re
import sqlite3
from typing import TYPE_CHECKING

from .queue_records import TaskError
from .registrar_configuration import ConfiguredSupervisorCohort, SupervisorScopeIntent


@dataclass(frozen=True)
class PoolConfiguredCohort:
    """Retained configuration observation, not current-file or runtime attestation."""

    revision: int
    observation: ConfiguredSupervisorCohort | None


def _encode_observation(value: ConfiguredSupervisorCohort) -> str:
    if not isinstance(value, ConfiguredSupervisorCohort):
        raise TaskError("pool cohort requires the registrar's whole observation")
    if not isinstance(value.state, str) or value.state not in {
        "complete", "unconfirmed", "unavailable",
    }:
        raise TaskError("pool cohort observation has an unsupported state")
    if value.source_revision is not None and (
        not isinstance(value.source_revision, str)
        or re.fullmatch("[0-9a-f]{64}", value.source_revision) is None
    ):
        raise TaskError("pool cohort requires an exact source-byte revision")
    if not isinstance(value.members, tuple) or not isinstance(value.reason, str):
        raise TaskError("pool cohort requires immutable members and an explicit reason")
    members = []
    for member in value.members:
        if not isinstance(member, SupervisorScopeIntent):
            raise TaskError("pool cohort requires normalized supervisor scopes")
        raw = {
            "machine": member.machine, "execution_machine": member.execution_machine,
            "env": member.env,
        }
        if any(
            not isinstance(item, str) or not item.strip() or item != item.strip()
            for item in raw.values()
        ):
            raise TaskError("pool cohort scope identities must be explicit")
        members.append(raw)
    members.sort(key=lambda item: (item["execution_machine"], item["env"]))
    if len({(item["execution_machine"], item["env"]) for item in members}) != len(members):
        raise TaskError("pool cohort contains duplicate execution scopes")
    if value.state == "complete":
        if (
            type(value.revision) is not int or value.revision < 0
            or value.source_revision is None or value.reason
        ):
            raise TaskError("complete pool cohort requires its configured and source revisions")
    elif value.revision is not None or members or not value.reason.strip():
        raise TaskError("unconfirmed or unavailable pool cohort cannot present members")
    return json.dumps(
        {
            "state": value.state, "source_revision": value.source_revision,
            "revision": value.revision, "members": members, "reason": value.reason,
        },
        sort_keys=True, separators=(",", ":"),
    )


def cohort_from_clock(row: sqlite3.Row) -> PoolConfiguredCohort:
    raw = row["cohort_observation"]
    if raw is None:
        return PoolConfiguredCohort(row["cohort_revision"], None)
    value = json.loads(raw)
    return PoolConfiguredCohort(
        row["cohort_revision"],
        ConfiguredSupervisorCohort(
            value["state"], value["source_revision"], value["revision"],
            tuple(SupervisorScopeIntent(**item) for item in value["members"]),
            value["reason"],
        ),
    )


def require_cohort_scope(
    conn: sqlite3.Connection, machine: str, env: str, expected: int | None,
) -> None:
    """Fence fresh metadata writes; accepted-result recovery runs before this."""
    current = cohort_from_clock(
        conn.execute("SELECT * FROM pool_catalog_clock WHERE singleton = 1").fetchone()
    )
    if expected is not None and (type(expected) is not int or expected < 0):
        raise TaskError("pool cohort revision must be a nonnegative integer")
    if current.revision == 0 and expected is None:
        return
    if expected != current.revision:
        raise TaskError("configured pool cohort revision changed or was not supplied")
    observation = current.observation
    if observation is None or not observation.complete:
        raise TaskError("configured pool cohort is unconfirmed or unavailable")
    if (machine, env) not in {
        (member.execution_machine, member.env) for member in observation.members
    }:
        raise TaskError("publisher scope is not in the configured pool cohort")


class QueuePoolCohortMixin:
    """Project the registrar owner without discovering or configuring a roster."""

    if TYPE_CHECKING:
        _connect: Callable[[], sqlite3.Connection]
        _now: Callable[[float | None], float]

    @staticmethod
    def _migrate_pool_cohort(conn: sqlite3.Connection) -> None:
        for table, columns in (
            ("pool_catalog_clock", (
                ("cohort_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("cohort_observation", "TEXT"), ("cohort_request", "TEXT"),
                ("cohort_last_complete", "TEXT"),
            )),
            ("pool_publication_scopes", (
                ("enrollment_cohort_revision", "INTEGER"),
                ("definitions_cohort_revision", "INTEGER"),
            )),
        ):
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns:
                if name not in existing:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc).lower():
                            raise
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pool_catalog_cohort_history ("
            " revision INTEGER PRIMARY KEY, observation TEXT NOT NULL,"
            " accepted_at REAL NOT NULL)"
        )

    def observe_pool_configured_cohort(
        self, observation: ConfiguredSupervisorCohort, *,
        expected_revision: int, now: float | None = None,
    ) -> PoolConfiguredCohort:
        """CAS one whole owner observation; no caller, scan or rollout is wired."""
        encoded = _encode_observation(observation)
        if type(expected_revision) is not int or expected_revision < 0:
            raise TaskError("pool cohort expected revision must be a nonnegative integer")
        ts = self._now(now)
        if not math.isfinite(ts):
            raise TaskError("pool cohort requires a finite observation time")
        request = json.dumps([expected_revision, encoded], separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            clock = conn.execute(
                "SELECT * FROM pool_catalog_clock WHERE singleton = 1"
            ).fetchone()
            current = cohort_from_clock(clock)
            if current.revision == expected_revision + 1 and clock["cohort_request"] == request:
                return current
            if current.revision != expected_revision:
                raise TaskError("pool cohort revision changed; refusing stale observation")
            if clock["cohort_observation"] == encoded:
                return current
            if observation.complete and clock["cohort_last_complete"] is not None:
                raw = json.loads(clock["cohort_last_complete"])
                if observation.revision < raw["revision"]:
                    raise TaskError("configured supervisor revision cannot regress")
                if observation.revision == raw["revision"] and (
                    json.loads(encoded)["members"] != raw["members"]
                ):
                    raise TaskError("configured supervisor revision reused for changed members")
            conn.execute(
                "INSERT INTO pool_catalog_cohort_history VALUES (?, ?, ?)",
                (current.revision + 1, encoded, ts),
            )
            conn.execute(
                "UPDATE pool_catalog_clock SET revision = revision + 1,"
                " cohort_revision = cohort_revision + 1, cohort_observation = ?,"
                " cohort_request = ?, cohort_last_complete ="
                " CASE WHEN ? THEN ? ELSE cohort_last_complete END WHERE singleton = 1",
                (encoded, request, observation.complete, encoded),
            )
            return cohort_from_clock(conn.execute(
                "SELECT * FROM pool_catalog_clock WHERE singleton = 1"
            ).fetchone())
