"""Explicit lifetime delegation for an already accepted standing pairing."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import asdict, dataclass

from .assignment_workspace import AssignmentWorkspace
from .queue_common import Task
from .queue_records import Status, TaskError
from .queue_standing_charters import _encoded


@dataclass(frozen=True)
class StandingRecoveryGrant:
    grant_id: str
    authority_ref: str
    expires_at: float
    max_attempts: int

    def __post_init__(self) -> None:
        for value in (self.grant_id, self.authority_ref):
            if (
                not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 1024 or any(ord(char) < 32 for char in value)
            ):
                raise TaskError("standing recovery grant requires bounded identity and authority reference")
        try:
            finite_expiry = (
                type(self.expires_at) in {int, float}
                and math.isfinite(self.expires_at) and self.expires_at > 0
            )
        except OverflowError:
            finite_expiry = False
        if not finite_expiry or type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 2**31 - 1:
            raise TaskError("standing recovery grant requires finite expiry and a positive attempt limit")


def _recovery(task: Task) -> dict | None:
    value = task.standing_charter.get("recovery")
    if value is None:
        return None
    if not (
        isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1
        and isinstance(value.get("request"), dict) and isinstance(value.get("binding"), dict)
        and isinstance(value.get("state"), str) and value["state"] in {"granted", "revoked"}
        and type(value.get("attempts")) is int and value["attempts"] >= 0
        and "pending" in value and (value["pending"] is None or isinstance(value["pending"], dict))
        and {"grant_id", "authority_ref", "expires_at", "max_attempts",
             "workspace", "expected_revision", "expected_generation", "owner_session_id"}
        <= value["request"].keys()
        and {"generation", "owner", "owner_session_id", "workspace", "assignment_event_id",
             "request_id", "relationship_ref", "declaration_generation", "declaration",
             "accepted_materialization"} <= value["binding"].keys()
    ):
        raise TaskError("standing recovery state is unsupported or unconfirmed; original pairing retained")
    StandingRecoveryGrant(**{
        name: value["request"][name]
        for name in ("grant_id", "authority_ref", "expires_at", "max_attempts")
    })
    AssignmentWorkspace.from_dict(value["request"]["workspace"])
    AssignmentWorkspace.from_dict(value["binding"]["workspace"])
    return value


def recovery_binding(task: Task, relationship: dict) -> dict:
    """Keep declaration, acceptance and original assignment independent of revision bumps."""
    standing = task.standing_charter
    accepted = standing.get("accepted_materialization")
    if (
        task.status != Status.STARTED or not task.owner or not task.owner_session_id
        or task.owner != AssignmentWorkspace.from_dict(relationship["request"]["workspace"]).worker_id
        or standing["lifecycle"] != "active"
        or not isinstance(accepted, dict) or accepted.get("generation") != task.generation
        or accepted.get("head", {}).get("session_id") != task.owner_session_id
        or accepted.get("request_id") != relationship["request"]["request_id"]
        or accepted.get("relationship_ref") != relationship["receipt"]["ref"]
    ):
        raise TaskError("standing recovery requires its accepted active native pairing")
    declaration = standing["declaration"]
    if any(accepted.get(key) != declaration[field] for key, field in (
        ("materialization_ref", "materialization_ref"),
        ("source_revision", "source_revision"),
        ("materialization_revision", "expected_materialization_revision"),
    )):
        raise TaskError("standing recovery requires acceptance of the current declaration")
    return copy.deepcopy({
        "generation": task.generation, "owner": task.owner,
        "owner_session_id": task.owner_session_id,
        "workspace": relationship["request"]["workspace"],
        "assignment_event_id": relationship["request"]["event_id"],
        "request_id": relationship["request"]["request_id"],
        "relationship_ref": relationship["receipt"]["ref"],
        "declaration_generation": standing["declaration_generation"],
        "declaration": declaration, "accepted_materialization": accepted,
    })


def _operation_id(value: object) -> None:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > 1024 or any(ord(char) < 32 for char in value)
    ):
        raise TaskError("standing recovery requires a bounded original operation identity")


class QueueStandingRecoveryMixin:
    def grant_standing_recovery(
        self, task_id: str, workspace: AssignmentWorkspace, grant: StandingRecoveryGrant, *,
        expected_revision: int, expected_generation: int, owner_session_id: str,
        now: float | None = None,
    ) -> Task:
        """Grant bounded continuation of one pairing, never staffing or a launch."""
        if not isinstance(workspace, AssignmentWorkspace) or not isinstance(grant, StandingRecoveryGrant):
            raise TaskError("standing recovery requires its exact workspace and validated grant")
        if any(type(value) is not int or value < 1 for value in (expected_revision, expected_generation)):
            raise TaskError("standing recovery requires positive fencing revisions")
        request = {
            **asdict(grant), "workspace": asdict(workspace),
            "expected_revision": expected_revision, "expected_generation": expected_generation,
            "owner_session_id": owner_session_id,
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None or task.kind != "standing-charter":
                raise TaskError("operation requires a standing charter task")
            previous = _recovery(task)
            if isinstance(previous, dict) and previous["request"]["grant_id"] == grant.grant_id:
                if previous["request"] != request:
                    raise TaskError("standing recovery grant identity conflicts with its original request")
                # Returning the retained outcome never renews a revoked or expired grant.
                return task
            self._require_standing(conn, task_id, expected_revision)
            self._require_standing_admission_open(conn, task, self._now(now))
            if task.generation != expected_generation or task.owner_session_id != owner_session_id:
                raise TaskError("standing recovery pairing incarnation changed")
            relationship = self._standing_relationship(conn, task, workspace)
            binding = recovery_binding(task, relationship)
            if grant.expires_at <= self._now(now):
                raise TaskError("standing recovery grant is already expired")
            if isinstance(previous, dict) and previous.get("pending") is not None:
                raise TaskError("standing recovery has an unresolved original operation")
            # A logical grant ID cannot be recycled after a replacement grant.
            if conn.execute(
                "SELECT 1 FROM task_events WHERE task_id=? AND"
                " json_extract(standing_change, '$.after.standing_charter') IS NOT NULL AND"
                " json_extract(json_extract(standing_change, '$.after.standing_charter'),"
                " '$.recovery.request.grant_id')=? LIMIT 1", (task_id, grant.grant_id),
            ).fetchone():
                raise TaskError("standing recovery grant identity is already retained in history")
            result = self._change_standing(conn, task, {
                "standing_charter": _encoded({
                    **task.standing_charter,
                    "recovery": {
                        "version": 1, "request": request, "binding": binding,
                        "state": "granted", "attempts": 0, "pending": None,
                    },
                }),
            }, action="recovery-grant", ts=self._now(now))
        self._notify_owned_transition()
        return result

    def revoke_standing_recovery(
        self, task_id: str, *, grant_id: str, expected_revision: int,
        now: float | None = None,
    ) -> Task:
        """Revoke future effects without settling or discarding a pending operation."""
        if type(expected_revision) is not int or expected_revision < 1:
            raise TaskError("standing recovery revocation requires a positive revision")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None or task.kind != "standing-charter":
                raise TaskError("operation requires a standing charter task")
            recovery = _recovery(task)
            if not isinstance(recovery, dict) or recovery["request"]["grant_id"] != grant_id:
                raise TaskError("standing recovery grant identity changed")
            if recovery["state"] == "revoked" and recovery.get("revoked_revision") == expected_revision:
                return task
            self._require_standing(conn, task_id, expected_revision)
            result = self._change_standing(conn, task, {
                "standing_charter": _encoded({
                    **task.standing_charter,
                    "recovery": {
                        **recovery, "state": "revoked", "revoked_revision": expected_revision,
                    },
                }),
            }, action="recovery-revoke", ts=self._now(now))
        self._notify_owned_transition()
        return result

    def _standing_recovery_authority(self, conn, task: Task, ts: float, *, pending: bool = False) -> dict:
        if task is None or task.kind != "standing-charter":
            raise TaskError("operation requires a standing charter task")
        recovery = _recovery(task)
        if not isinstance(recovery, dict) or recovery["state"] != "granted":
            raise TaskError("standing pairing has no active recovery grant")
        self._require_standing_admission_open(conn, task, ts)
        workspace = AssignmentWorkspace.from_dict(recovery["binding"]["workspace"])
        relationship = self._standing_relationship(conn, task, workspace)
        if recovery["binding"] != recovery_binding(task, relationship):
            raise TaskError("standing recovery grant no longer matches the accepted pairing")
        if recovery["request"]["expires_at"] <= ts:
            raise TaskError("standing recovery grant is expired")
        if not pending and recovery["attempts"] >= recovery["request"]["max_attempts"]:
            raise TaskError("standing recovery attempt limit is exhausted")
        if not pending and recovery.get("pending") is not None:
            raise TaskError("standing recovery has an unresolved original operation")
        return recovery

    def standing_recovery_authority(self, task_id: str, *, now: float | None = None) -> dict:
        """Read effective delegation; this never proves liveness or authorizes a send."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            return copy.deepcopy(self._standing_recovery_authority(
                conn, self._fetch(conn, task_id), self._now(now),
            ))

    def prepare_standing_recovery(
        self, task_id: str, *, operation_id: str, grant_id: str,
        expected_revision: int, trigger: str, now: float | None = None,
    ) -> dict:
        """Write ahead of the native owner call; preparation is not launch admission."""
        _operation_id(operation_id)
        if trigger not in {"boot", "confirmed-loss"}:
            raise TaskError("standing recovery trigger must be boot or confirmed-loss")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None or task.kind != "standing-charter":
                raise TaskError("operation requires a standing charter task")
            recovery = _recovery(task)
            pending = recovery.get("pending") if recovery is not None else None
            if isinstance(pending, dict) and pending.get("operation_id") == operation_id:
                if pending.get("grant_id") != grant_id or pending.get("trigger") != trigger:
                    raise TaskError("standing recovery operation conflicts with its original request")
                return copy.deepcopy(pending)
            self._require_standing(conn, task_id, expected_revision)
            recovery = self._standing_recovery_authority(conn, task, ts)
            if recovery["request"]["grant_id"] != grant_id:
                raise TaskError("standing recovery grant identity changed")
            if conn.execute(
                "SELECT 1 FROM task_events WHERE task_id=? AND "
                "json_extract(json_extract(standing_change, '$.after.standing_charter'), "
                "'$.recovery.pending.operation_id')=? LIMIT 1", (task_id, operation_id),
            ).fetchone():
                raise TaskError("standing recovery operation identity is already retained in history")
            workspace = AssignmentWorkspace.from_dict(recovery["binding"]["workspace"])
            relationship = self._standing_relationship(conn, task, workspace)
            participant = relationship.get("native_participant")
            from .ordinary_writer_admission import writer_requirement

            if not (
                isinstance(participant, dict) and type(participant.get("version")) is int
                and participant["version"] == 1
                and isinstance(participant.get("journal_key"), str)
                and re.fullmatch(r"[0-9a-f]{64}", participant["journal_key"]) is not None
                and all(isinstance(participant.get(key), str) and participant[key].strip()
                        for key in ("bridge_installation", "bridge_session_id", "native_session_id"))
                and participant["native_session_id"] == task.owner_session_id
            ):
                raise TaskError("standing recovery requires its original exact native participant")
            requirement = writer_requirement(participant.get("writer_requirement"))
            if requirement["creation_nonce"] != workspace.creation_nonce:
                raise TaskError("standing recovery native workspace incarnation changed")
            pending = {
                "version": 1, "operation_id": operation_id, "grant_id": grant_id,
                "trigger": trigger, "state": "prepared", "prepared_at": ts,
                "binding": copy.deepcopy(recovery["binding"]),
                "native_participant": copy.deepcopy(participant),
            }
            self._change_standing(conn, task, {
                "standing_charter": _encoded({
                    **task.standing_charter, "recovery": {
                        **recovery, "attempts": recovery["attempts"] + 1, "pending": pending,
                    },
                }),
            }, action="recovery-prepare", ts=ts)
        self._notify_owned_transition()
        return copy.deepcopy(pending)

    def claim_standing_recovery(
        self, task_id: str, *, operation_id: str, now: float | None = None,
    ) -> dict:
        """Consume once before invoking; a retry must inspect, never invoke again."""
        _operation_id(operation_id)
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            recovery = self._standing_recovery_authority(conn, task, ts, pending=True)
            pending = recovery["pending"]
            if not (
                isinstance(pending, dict) and type(pending.get("version")) is int
                and pending["version"] == 1
                and pending.get("operation_id") == operation_id and pending.get("state") == "prepared"
                and pending.get("grant_id") == recovery["request"]["grant_id"]
                and pending.get("binding") == recovery["binding"]
            ):
                raise TaskError("standing recovery original operation is held; inspect without replay")
            pending = {**pending, "state": "invoking", "invoked_at": ts}
            self._change_standing(conn, task, {
                "standing_charter": _encoded({
                    **task.standing_charter, "recovery": {**recovery, "pending": pending},
                }),
            }, action="recovery-invoke", ts=ts)
        self._notify_owned_transition()
        return copy.deepcopy(pending)

    def settle_standing_recovery(
        self, task_id: str, *, operation_id: str, outcome: dict,
        now: float | None = None, prepared_only: bool = False,
    ) -> Task:
        """Retain uncertain effects; exact native reconciliation also works after revocation."""
        _operation_id(operation_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None or task.kind != "standing-charter":
                raise TaskError("operation requires a standing charter task")
            recovery = _recovery(task)
            pending = recovery.get("pending") if recovery is not None else None
            if not isinstance(pending, dict) or pending.get("operation_id") != operation_id:
                if recovery is not None and recovery.get("last_result") == outcome and (
                    isinstance(outcome, dict) and outcome.get("operation_id") == operation_id
                ):
                    return task
                raise TaskError("standing recovery lost its original pending operation")
            if prepared_only and (
                pending.get("state") != "prepared"
                or not isinstance(outcome, dict) or outcome.get("state") != "not-started"
            ):
                raise TaskError("standing recovery preparation was consumed; inspect without replay")
            participant = pending.get("native_participant", {})
            if not (
                isinstance(outcome, dict)
                and (pending.get("state") in {"invoking", "unknown"}
                     or pending.get("state") == "prepared" and outcome.get("state") == "not-started")
                and set(outcome) == {"version", "operation_id", "state", "journal_key",
                                     "bridge_session_id", "native_session_id"}
                and type(outcome["version"]) is int and outcome["version"] == 1
                and outcome["operation_id"] == operation_id
                and outcome["state"] in {"complete", "not-started", "unknown"}
                and outcome["journal_key"] == participant.get("journal_key")
                and outcome["bridge_session_id"] == participant.get("bridge_session_id")
                and outcome["native_session_id"] == pending["binding"]["owner_session_id"]
            ):
                raise TaskError("standing recovery outcome does not prove its original native operation")
            if recovery.get("last_result") == outcome:
                return task
            settled = outcome["state"] != "unknown"
            result = self._change_standing(conn, task, {
                "standing_charter": _encoded({
                    **task.standing_charter, "recovery": {
                        **recovery,
                        "pending": None if settled else {**pending, "state": "unknown"},
                        "last_result": copy.deepcopy(outcome),
                    },
                }),
            }, action="recovery-settle" if settled else "recovery-unknown", ts=self._now(now))
        self._notify_owned_transition()
        return result
