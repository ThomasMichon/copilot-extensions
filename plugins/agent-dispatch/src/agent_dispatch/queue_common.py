"""Shared queue helpers and task-scoped record types.

This module gives :mod:`agent_dispatch.queue`'s extracted mixins a stable,
dependency-light home for the symbols they previously had to reach back into
``queue.py`` for: queue-wide constants, task/wake dataclasses, bounded
text/progress helpers, result encoding, and the SQL column/select fragments
derived from :class:`Task`.

Like :mod:`agent_dispatch.queue_records`, it exists to break real circular
dependencies: ``queue.py`` composes mixins into :class:`TaskQueue`, so a mixin
cannot safely import these names from ``queue.py`` at module import time.
Defining them here lets both the composition root and the mixins import them as
ordinary top-level names, preserving runtime introspection and monkeypatch
compatibility when ``queue.py`` re-exports them.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .queue_records import TaskError

DEFAULT_LEASE_SECONDS = 15 * 60
DEFAULT_EVAL_LEASE_SECONDS = 3 * 60
DEFAULT_WAKE_DELIVERY_LEASE_SECONDS = 60
DEFAULT_BLOB_THRESHOLD = 4096
DEFAULT_RESULT_MAX_BYTES = 64 * 1024
LEGACY_REPO = "(legacy)"
_BUSY_TIMEOUT_MS = 5000
_MAX_AFFINITY = 1000
_CLAIM_REJECTION_EVENT_LIMIT = 20

#: Hard cap on a progress summary -- keeps the beat a line, never a transcript.
PROGRESS_SUMMARY_MAX = 280
#: Hard cap on a progress phase label and blocker/pr fields.
PROGRESS_PHASE_MAX = 40
_PROGRESS_PR_MAX = 120


def worker_id_for(machine: str, worktree: str) -> str:
    """The canonical agent identity: the ``machine/worktree`` composite.

    This pair is the only durable agent id a multi-machine system has; the coordinator
    stamps it as a task's ``owner`` on claim, and an agent finds its own work by
    querying with the same pair (see :meth:`TaskQueue.mine`).
    """
    return f"{machine}/{worktree}"


def _claimant_worktree_machine(task: Task) -> tuple[str | None, str | None]:
    """The task's actual claimant identity (``machine``, ``worktree``), for
    recording a durable attachment row.

    Prefers ``task.owner`` (the real claimant, stamped by ``claim_one`` as
    ``worker_id_for(machine, worktree)`` -- correct for the common **unpinned**
    claim, where ``target_worktree``/``target_machine`` are never set) over
    the target pin, falling back to the pin only when there is no owner yet
    (e.g. a not-yet-claimed pinned task). A worktree id cannot itself contain
    ``/``, so splitting on the first one is exact, unlike ``__main__._split_owner``
    which defensively tolerates one for a value of uncertain origin.
    """
    if task.owner:
        machine, _, worktree = task.owner.partition("/")
        if worktree:
            return machine, worktree
    return task.target_machine, task.target_worktree


def machine_matches(target: str | None, machine: str | None) -> bool:
    """True when a task's stored ``target_machine`` matches the ``machine`` a
    caller is scoping to -- **case-insensitively**.

    Machine names (a machine's registry key / SSH alias) are lowercase
    by convention, but a caller may pass a display-cased variant (the worktree
    picker scopes ``inbox`` by the ``machines.yaml`` display name ``Anomalous-Potato``
    while a task's ``target_machine`` is stored as the identity ``anomalous-potato``).
    A case-sensitive comparison would then hide legitimately-targeted work. An
    unset ``target_machine`` (a machine-agnostic task) matches any caller.
    """
    if target is None:
        return True
    if machine is None:
        return False
    return target.casefold() == machine.casefold()


def _clip(text: str | None, limit: int) -> str | None:
    """Trim whitespace and hard-cap ``text`` to ``limit`` chars (ellipsis if cut)."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "\u2026"
    return text


def _check_expected_status(task: "Task", expected_status: str | None) -> None:
    """Fence a mutating action against a caller-supplied ``expected_status``."""
    if expected_status is not None and task.status != expected_status:
        raise TaskError(
            f"task {task.id!r} changed; refresh and retry (expected status "
            f"{expected_status!r}, now {task.status!r})"
        )


def _progress_snapshot(
    phase: str,
    summary: str,
    *,
    blocker: str | None = None,
    pr: str | None = None,
    ts: float,
) -> dict[str, object]:
    """Build a bounded, latest-only progress snapshot dict."""
    snapshot: dict[str, object] = {
        "phase": _clip(phase, PROGRESS_PHASE_MAX) or "",
        "summary": _clip(summary, PROGRESS_SUMMARY_MAX) or "-",
        "ts": ts,
    }
    blocker_c = _clip(blocker, PROGRESS_SUMMARY_MAX)
    if blocker_c:
        snapshot["blocker"] = blocker_c
    pr_c = _clip(pr, _PROGRESS_PR_MAX)
    if pr_c:
        snapshot["pr"] = pr_c
    return snapshot


def _task_transition_spec(name: str) -> tuple[frozenset[str], str]:
    """Resolve a declared task transition's ``(from_states, to_state)``."""
    from .task_state_machine import TRANSITIONS_BY_NAME

    transition = TRANSITIONS_BY_NAME[name]
    return transition.from_states, transition.to_state


class ResultValidationError(TaskError):
    """Raised when a completion result is not a structured JSON value."""


class ResultTooLargeError(TaskError):
    """Raised when a completion result exceeds the configured byte limit."""


StructuredResult = dict[str, Any] | list[Any]


def encode_result(
    result: object | None,
    *,
    max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
) -> str | None:
    """Validate and canonically encode an optional structured JSON result."""
    if result is None:
        return None
    if not isinstance(result, (dict, list)):
        raise ResultValidationError("result must be a JSON object or array, not null or a scalar")
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ResultValidationError(f"result is not JSON-compatible: {exc}") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ResultTooLargeError(f"result exceeds the {max_bytes}-byte encoded limit")
    return encoded


@dataclass(frozen=True)
class Task:
    """A read-only snapshot of a task row."""

    id: str
    title: str
    prompt: str
    status: str
    repo: str | None = None
    requires: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    affinity: dict[str, str] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    payload_ref: str | None = None
    payload_inline: str | None = None
    target_machine: str | None = None
    target_worktree: str | None = None
    target_repo: str | None = None
    source: str | None = None
    origin_ref: str | None = None
    evaluator_ref: str | None = None
    exclusive_key: str | None = None
    dedup_key: str | None = None
    producer_fence: dict | None = None
    owner: str | None = None
    attempts: int = 0
    not_before: float = 0.0
    lease_expires_at: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    claimed_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    completed_by: str | None = None
    result_ref: str | None = None
    result: object | None = None
    has_result: bool = False
    latest_progress: str | None = None
    goal: str | None = None
    done_criteria: str | None = None
    owner_session_id: str | None = None
    generation: int = 0
    last_seen_at: float | None = None
    last_liveness: str | None = None
    activity: str | None = None
    activity_updated_at: float | None = None
    card: dict | None = None
    awaiting_steer: bool = False
    card_draft: dict | None = None
    resume_requested: bool = False
    hold_reason: str | None = None
    hold_actor: str | None = None
    hold_at: float | None = None
    wake_seq: int = 0
    wake_status: str | None = None
    wake_operation_id: str | None = None
    monitor_kind: str | None = None
    monitor_not_before: float | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> Task:
        columns = set(row.keys())
        raw_result = row["result"] if "result" in columns else None
        return cls(
            id=row["id"],
            title=row["title"],
            prompt=row["prompt"],
            status=row["status"],
            repo=row["repo"],
            requires=json.loads(row["requires"] or "[]"),
            excludes=json.loads(row["excludes"] or "[]"),
            affinity=json.loads(row["affinity"] or "{}"),
            labels=json.loads(row["labels"] or "[]"),
            payload_ref=row["payload_ref"],
            payload_inline=row["payload_inline"],
            target_machine=row["target_machine"],
            target_worktree=row["target_worktree"],
            target_repo=row["target_repo"],
            source=row["source"],
            origin_ref=row["origin_ref"],
            evaluator_ref=row["evaluator_ref"],
            exclusive_key=row["exclusive_key"],
            dedup_key=row["dedup_key"],
            producer_fence=(json.loads(row["producer_fence"]) if row["producer_fence"] else None),
            owner=row["owner"],
            attempts=row["attempts"],
            not_before=row["not_before"],
            lease_expires_at=row["lease_expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            claimed_at=row["claimed_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            completed_by=row["completed_by"],
            result_ref=row["result_ref"],
            result=json.loads(raw_result) if raw_result is not None else None,
            has_result=(
                bool(row["has_result"]) if "has_result" in columns else raw_result is not None
            ),
            latest_progress=row["latest_progress"],
            goal=row["goal"],
            done_criteria=row["done_criteria"],
            owner_session_id=row["owner_session_id"],
            generation=row["generation"],
            last_seen_at=row["last_seen_at"],
            last_liveness=row["last_liveness"],
            activity=row["activity"],
            activity_updated_at=row["activity_updated_at"],
            card=json.loads(row["card"]) if row["card"] else None,
            card_draft=json.loads(row["card_draft"]) if row["card_draft"] else None,
            awaiting_steer=bool(row["awaiting_steer"]),
            resume_requested=bool(row["resume_requested"]),
            hold_reason=(row["hold_reason"] if "hold_reason" in columns else None),
            hold_actor=(row["hold_actor"] if "hold_actor" in columns else None),
            hold_at=(row["hold_at"] if "hold_at" in columns else None),
            wake_seq=row["wake_seq"],
            wake_status=row["wake_status"],
            wake_operation_id=row["wake_operation_id"],
            monitor_kind=(row["monitor_kind"] if "monitor_kind" in columns else None),
            monitor_not_before=(
                row["monitor_not_before"] if "monitor_not_before" in columns else None
            ),
        )


_TASK_DB_COLUMNS = tuple(
    field.name for field in dataclasses.fields(Task) if field.name not in {"result", "has_result"}
)
_TASK_SELECT = ", ".join((*_TASK_DB_COLUMNS, "result"))
_TASK_BULK_SELECT = ", ".join((*_TASK_DB_COLUMNS, "result IS NOT NULL AS has_result"))


@dataclass(frozen=True)
class WakeOperation:
    """A durable owner-wake outbox row."""

    id: str
    task_id: str
    generation: int
    wake_seq: int
    owner: str
    owner_session_id: str | None
    message: str | None
    status: str
    attempts: int
    not_before: float
    created_at: float
    updated_at: float
    delivered_at: float | None = None
    last_error: str | None = None
    delivery_token: str | None = None
    delivery_expires_at: float | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> WakeOperation:
        return cls(**{field.name: row[field.name] for field in dataclasses.fields(cls)})


@dataclass(frozen=True)
class CompletionOutcome:
    """A completed task plus the observable event caused by this invocation."""

    task: Task
    event_type: str | None


@dataclass(frozen=True)
class AttachmentRecord:
    """One entry in a task's durable attachment history."""

    session_id: str
    worktree_id: str | None
    machine: str | None
    attached_at: float
    detached_at: float | None
    detach_reason: str | None


@dataclass(frozen=True)
class TaskAttachmentEntry:
    """One task a given session id has ever attached to."""

    task_id: str
    worktree_id: str | None
    machine: str | None
    attached_at: float
    detached_at: float | None
    detach_reason: str | None


@dataclass(frozen=True)
class CreationOutcome:
    """A create result plus whether it inserted a new lifecycle row."""

    task: Task
    disposition: str
    event_type: str | None


@dataclass(frozen=True)
class ClaimOutcome:
    """A claim result plus newly recorded managed-producer rejections."""

    task: Task | None
    producer_rejections: list[dict[str, object]] = field(default_factory=list)


_COLUMNS: dict[str, str] = {
    "id": "TEXT PRIMARY KEY",
    "title": "TEXT NOT NULL DEFAULT ''",
    "prompt": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'queued'",
    "repo": "TEXT",
    "requires": "TEXT NOT NULL DEFAULT '[]'",
    "excludes": "TEXT NOT NULL DEFAULT '[]'",
    "affinity": "TEXT NOT NULL DEFAULT '{}'",
    "labels": "TEXT NOT NULL DEFAULT '[]'",
    "payload_ref": "TEXT",
    "payload_inline": "TEXT",
    "target_machine": "TEXT",
    "target_worktree": "TEXT",
    "target_repo": "TEXT",
    "source": "TEXT",
    "origin_ref": "TEXT",
    "exclusive_key": "TEXT",
    "dedup_key": "TEXT",
    "producer_fence": "TEXT",
    "producer_request_hash": "TEXT",
    "owner": "TEXT",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "not_before": "REAL NOT NULL DEFAULT 0",
    "lease_expires_at": "REAL",
    "created_at": "REAL NOT NULL DEFAULT 0",
    "updated_at": "REAL NOT NULL DEFAULT 0",
    "claimed_at": "REAL",
    "started_at": "REAL",
    "completed_at": "REAL",
    "completed_by": "TEXT",
    "result_ref": "TEXT",
    "result": "TEXT",
    "latest_progress": "TEXT",
    "goal": "TEXT",
    "done_criteria": "TEXT",
    "evaluator_ref": "TEXT",
    "owner_session_id": "TEXT",
    "generation": "INTEGER NOT NULL DEFAULT 0",
    "last_seen_at": "REAL",
    "last_liveness": "TEXT",
    "activity": "TEXT",
    "activity_updated_at": "REAL",
    "card": "TEXT",
    "card_draft": "TEXT",
    "awaiting_steer": "INTEGER NOT NULL DEFAULT 0",
    "resume_requested": "INTEGER NOT NULL DEFAULT 0",
    "hold_reason": "TEXT",
    "hold_actor": "TEXT",
    "hold_at": "REAL",
    "wake_seq": "INTEGER NOT NULL DEFAULT 0",
    "wake_status": "TEXT",
    "wake_operation_id": "TEXT",
    "monitor_kind": "TEXT",
    "monitor_not_before": "REAL",
}
