"""Allowlisted membership facts from the existing canonical Bridge tables."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from .owner_membership_schema import owner_expression as _owner_expression
from .relationship_authority import (
    RelationshipRefused,
    canonical_owner,
    confirm,
    confirm_absence,
    native_identity,
)
from .session_observation_contract import ObservationError, decode, encode, text

MAX_MEMBERS = 64
MEMBER_FIELDS = frozenset(
    {
        "member_id",
        "owner_ref",
        "registry",
        "bridge_session_id",
        "durable_session_id",
        "project",
        "worktree_id",
        "head_session_id",
        "current_answerability",
        "liveness",
        "freshness",
        "link_qualification",
        "retirement",
        "state",
        "transport",
        "incarnation",
        "admission_phase",
        "relationship_id",
        "uncertain_remote_outcome",
        "relationship_qualification",
    }
)


def owner_ref(value: Any) -> str:
    """Canonical owner selection is syntax, not external custody admission."""
    try:
        return canonical_owner(value)
    except (RelationshipRefused, TypeError) as exc:
        raise ObservationError(
            "invalid-owner", "an exact qualified owner reference is required"
        ) from exc


def _optional(value: Any) -> str | None:
    return text(value, "stored identity") if value is not None else None


def _object(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw) > 16384:
        raise ObservationError("source-unavailable", "stored relationship evidence is unavailable")
    try:
        result = decode(raw.encode("utf-8"))
    except (ObservationError, UnicodeError) as exc:
        raise ObservationError(
            "source-unavailable", "stored relationship evidence is invalid"
        ) from exc
    if not isinstance(result, dict):
        raise ObservationError("source-unavailable", "stored relationship evidence is invalid")
    return result


def _base(row: sqlite3.Row, owner: str, kind: str, identity: str) -> dict[str, Any]:
    return {
        "member_id": kind + ":" + text(identity, "member identity"),
        "owner_ref": owner,
        "registry": kind,
        "bridge_session_id": text(row["session_id"], "Bridge session"),
        "durable_session_id": _optional(row["durable_session_id"]),
        "project": _optional(row["project"]),
        "worktree_id": _optional(row["worktree_id"]),
        "head_session_id": None,
        "current_answerability": "unknown",
        "liveness": "unknown",
        "freshness": "unknown",
        "link_qualification": "reported",
        "retirement": None,
    }


def records(
    conn: sqlite3.Connection,
    owner: str,
    *,
    check: Callable[[], None],
) -> dict[str, bytes]:
    """Detach at most 64 safe records; never retain target/claim DTOs or tokens."""
    target_owner = "json_extract(target_json,'$.caller_owner_ref')"
    sessions = conn.execute(
        "SELECT id AS session_id, acp_session_id AS durable_session_id, status, target_type, "
        "created_at, json_extract(target_json,'$.project') AS project, "
        "json_extract(target_json,'$.worktree_id') AS worktree_id FROM sessions "
        f"WHERE json_valid(target_json) AND {_owner_expression(target_owner)}=? "
        "AND NOT EXISTS(SELECT 1 FROM remote_acp_relationships r WHERE r.session_id=sessions.id) "
        "LIMIT ?",
        (owner, MAX_MEMBERS + 1),
    ).fetchall()
    relationships = conn.execute(
        "SELECT r.id, r.session_id, r.phase, r.native_id AS durable_session_id, r.uncertain, "
        "CASE WHEN length(r.claim_json)<=16384 THEN r.claim_json END AS claim_json, "
        "CASE WHEN length(r.recovery_json)<=16384 THEN r.recovery_json END AS recovery_json, s.created_at, "
        "json_extract(r.recovery_json,'$.binding.project') AS project, "
        "CASE WHEN json_valid(s.target_json) THEN json_extract(s.target_json,'$.worktree_id') END AS worktree_id "
        "FROM remote_acp_relationships r LEFT JOIN sessions s ON s.id=r.session_id "
        f"WHERE {_owner_expression('r.owner')}=? LIMIT ?",
        (owner, MAX_MEMBERS + 1),
    ).fetchall()
    supervisor = "json_extract(venue,'$.supervisor_ref')"
    represented = conn.execute(
        "SELECT session_id, session_id AS durable_session_id, NULL AS project, worktree_id, "
        "status, pid, process_started_at, registered_at "
        f"FROM live_sessions WHERE json_valid(venue) AND {_owner_expression(supervisor)}=? LIMIT ?",
        (owner, MAX_MEMBERS + 1),
    ).fetchall()
    if len(sessions) + len(relationships) + len(represented) > MAX_MEMBERS:
        raise ObservationError(
            "membership-overflow", "owner membership exceeds its bounded snapshot"
        )
    result: dict[str, bytes] = {}
    for row in sessions:
        check()
        member = _base(row, owner, "owned", row["session_id"])
        member.update(
            state=text(row["status"], "session state"),
            transport=text(row["target_type"], "transport"),
            incarnation={"created_at": row["created_at"]},
            admission_phase="unknown",
        )
        if row["status"] == "ended":
            member["retirement"] = "session-retired"
        result[member["member_id"]] = encode(member)
    for row in relationships:
        check()
        member = _base(row, owner, "relationship", row["id"])
        phase = text(row["phase"], "relationship phase")
        member.update(
            state=phase,
            transport="remote-acp",
            admission_phase=phase,
            relationship_id=row["id"],
            uncertain_remote_outcome=bool(row["uncertain"]),
            incarnation={"relationship_id": row["id"]},
            relationship_qualification="unconfirmed",
        )
        if phase in {"bound", "closed"} and row["claim_json"] is not None:
            claim, recovery = _object(row["claim_json"]), _object(row["recovery_json"])
            try:
                absent = phase == "closed" and confirm_absence(
                    claim,
                    owner=owner,
                    request_id=row["id"],
                )
                if not absent:
                    confirm(
                        claim,
                        owner=owner,
                        request_id=row["id"],
                        recovery=recovery,
                        identity=native_identity(row["durable_session_id"])
                        if row["durable_session_id"]
                        else None,
                        closed=phase == "closed",
                    )
            except RelationshipRefused:
                member["relationship_qualification"] = "unknown"
            else:
                member["relationship_qualification"] = (
                    "reservation-absence-confirmed" if absent else "host-confirmed"
                )
                member["link_qualification"] = "host-confirmed"
                if absent:
                    member["retirement"] = "unreserved-relationship-closed"
                elif phase == "closed":
                    member["retirement"] = "confirmed-relationship-retired"
        if phase == "retired":
            member["retirement"] = "local-admission-retired"
        result[member["member_id"]] = encode(member)
    for row in represented:
        check()
        member = _base(row, owner, "represented", row["session_id"])
        member.update(
            state=text(row["status"], "registration state"),
            transport="represented-sdk",
            admission_phase="registration-only",
            incarnation={
                "pid": row["pid"],
                "process_started_at": row["process_started_at"],
                "registered_at": row["registered_at"],
            },
        )
        if row["status"] in {"expired", "taken-over"}:
            member["retirement"] = "registration-" + row["status"]
        result[member["member_id"]] = encode(member)
    return result
