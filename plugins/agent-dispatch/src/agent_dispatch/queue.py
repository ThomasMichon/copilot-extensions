"""SQLite-backed leased task queue -- the agent-dispatch engine.

A single-writer, WAL-mode SQLite queue providing an **atomic leased claim** over
a set of *tasks*. This module is deliberately transport-free: it is a pure
library that the coordinator process wraps behind HTTP. Everything that must be
*correct under concurrency* lives here, patterned on a proven single-writer
leased-queue design.

Design notes
------------
* **Eight-state model** (see :class:`Status`):
  ``proposed -> queued -> claimed -> started -> completed`` plus dormant
  ``suspended`` and terminal ``abandoned`` / ``dead_letter``. ``proposed`` and
  ``suspended`` are never claimable; liveness recovery returns only actively
  held tasks to ``queued``.
* **Capability-gated claim.** A task carries a hard ``requires`` set (capability
  tokens or an ``agent:<id>`` identity pin); a worker advertises a capability
  set at claim time. A task is claimable only when ``requires`` is a subset of
  the worker's capabilities. ``affinity`` is a soft preference that orders
  candidates but never excludes.
* **Cooperative claiming = redundancy.** ``claim_one`` takes a write lock
  (``BEGIN IMMEDIATE``) and re-checks ``status='queued'`` before committing, so
  N capable workers racing for one task yield exactly one winner. A dead worker's
  lease expires and any other capable worker reclaims it -- no leader election.
* **Additive migrations.** ``_migrate`` runs ``CREATE TABLE IF NOT EXISTS`` plus
  idempotent ``ALTER TABLE`` column adds, so an existing DB upgrades safely (a
  bare ``CREATE TABLE IF NOT EXISTS`` never upgrades an existing table).
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .identity import canonical_reviewer_target, canonicalize_remote
from .payload import PayloadStore, is_blob_ref
from .registrations import (
    RegistrationError,
    RegistrationRecord,
    RegistrationStatus,
    derive_registration_id,
    validate_registration,
)

DEFAULT_LEASE_SECONDS = 15 * 60
#: The tighter lease applied to a claim taken in **evaluation** mode -- the
#: ``claimed`` window is meant to be a quick accept/reject assessment, so a
#: stuck evaluator auto-releases fast (vs the full work lease a ``start`` grants).
DEFAULT_EVAL_LEASE_SECONDS = 3 * 60
#: Maximum time one coordinator owns a claimed wake delivery before another
#: active coordinator may recover it after a crash or cutover.
DEFAULT_WAKE_DELIVERY_LEASE_SECONDS = 60
#: Payloads whose UTF-8 size exceeds this are spilled to a content-addressed blob
#: instead of being stored inline in the row.
DEFAULT_BLOB_THRESHOLD = 4096
#: Maximum UTF-8 size of a task's canonical JSON completion result. Results stay
#: in SQLite rather than the payload blob store so the result bytes, result_ref,
#: and terminal status commit in one transaction.
DEFAULT_RESULT_MAX_BYTES = 64 * 1024
#: Sentinel lane for rows created before ``repo`` became required. Backfilled on
#: migration so legacy tasks never leak into a real repo's default-scoped views.
LEGACY_REPO = "(legacy)"
_BUSY_TIMEOUT_MS = 5000
_MAX_AFFINITY = 1000
_PRODUCER_SCOPE_SOURCE_MAX = 64
_PRODUCER_SCOPE_LABEL_MAX = 64
_PRODUCER_ID_MAX = 128
_PRODUCER_REQUEST_ID_MAX = 128
_PRODUCER_CAPABILITY_MAX = 512
_PRODUCER_HISTORY_LIMIT = 32
_PRODUCER_BLOCKING_TASK_LIMIT = 20
_CLAIM_REJECTION_EVENT_LIMIT = 20
_PRODUCER_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$")
log = logging.getLogger("agent-dispatch.queue")


def worker_id_for(machine: str, worktree: str) -> str:
    """The canonical agent identity: the ``machine/worktree`` composite.

    This pair is the only durable agent id a multi-machine system has; the coordinator
    stamps it as a task's ``owner`` on claim, and an agent finds its own work by
    querying with the same pair (see :meth:`TaskQueue.mine`).
    """
    return f"{machine}/{worktree}"


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


#: Hard cap on a progress summary -- keeps the beat a line, never a transcript.
PROGRESS_SUMMARY_MAX = 280
#: Hard cap on a progress phase label and blocker/pr fields.
PROGRESS_PHASE_MAX = 40
_PROGRESS_PR_MAX = 120


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


def _progress_snapshot(
    phase: str,
    summary: str,
    *,
    blocker: str | None = None,
    pr: str | None = None,
    ts: float,
) -> dict[str, object]:
    """Build a bounded, latest-only progress snapshot dict.

    Every free-text field is hard-capped so a progress beat is a *status line*,
    not a chat log. ``summary`` is required; empty/whitespace collapses to a
    dash placeholder so the beat still records a timestamped heartbeat.
    """
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


class Status:
    """The eight task states (string constants, stored verbatim)."""

    PROPOSED = "proposed"
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTED = "started"
    #: Previously started, owner-preserving, dormant, and non-claimable.
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    #: Terminal failure: a held task requeued too many times (its owner kept
    #: going gone) -- an actionable dead-letter end state rather than churning
    #: crash -> gone -> requeue forever.
    DEAD_LETTER = "dead_letter"

    #: States a worker actively holds; recoverable by liveness GC (owner-gone).
    HELD = frozenset({CLAIMED, STARTED})
    #: Non-terminal states that retain an owner. Suspended tasks are deliberately
    #: excluded from HELD because they have no active lease or embodiment.
    OWNED = frozenset({CLAIMED, STARTED, SUSPENDED})
    #: Terminal states -- no further transitions.
    TERMINAL = frozenset({COMPLETED, ABANDONED, DEAD_LETTER})
    #: Non-terminal states from which an abandon (with permission) is allowed.
    ABANDONABLE = frozenset({PROPOSED, QUEUED, CLAIMED, STARTED, SUSPENDED})


class TaskError(RuntimeError):
    """Raised on an illegal state transition or a lease/ownership violation."""


class ProducerScopeValidationError(TaskError):
    """Raised when a producer scope or producer identity is not narrow and exact."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "invalid_producer_request",
        repo: str | None = None,
        source: str | None = None,
        producer_request_id: str | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.repo = repo
        self.source = source
        self.producer_request_id = producer_request_id

    def detail(self, *, operation: str) -> dict[str, object]:
        result: dict[str, object] = {
            "code": "producer_request_invalid",
            "operation": operation,
            "reason": self.reason,
            "message": str(self),
            "retryable": False,
        }
        for key in ("repo", "source", "producer_request_id"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


class ProducerFenceError(TaskError):
    """A create or handoff rejected by a producer-generation fence."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        repo: str | None = None,
        source: str | None = None,
        required_label: str | None = None,
        requested_producer: str | None = None,
        active_producer: str | None = None,
        requested_generation: int | None = None,
        current_generation: int | None = None,
        producer_request_id: str | None = None,
        retryable: bool = False,
        diagnostics: Mapping[str, object] | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.repo = repo
        self.source = source
        self.required_label = required_label
        self.requested_producer = requested_producer
        self.active_producer = active_producer
        self.requested_generation = requested_generation
        self.current_generation = current_generation
        self.producer_request_id = producer_request_id
        self.retryable = retryable
        self.diagnostics = dict(diagnostics or {})

    def detail(self, *, operation: str) -> dict[str, object]:
        """Return bounded, content-free rejection metadata."""
        result: dict[str, object] = {
            "code": "producer_fence_rejected",
            "operation": operation,
            "reason": self.reason,
            "message": str(self),
            "retryable": self.retryable,
        }
        for key in (
            "repo",
            "source",
            "required_label",
            "requested_producer",
            "active_producer",
            "requested_generation",
            "current_generation",
            "producer_request_id",
        ):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        result.update(self.diagnostics)
        return result

    def event(self, *, operation: str) -> dict[str, object]:
        result = self.detail(operation=operation)
        result.pop("message")
        return result


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
        raise ResultValidationError(
            "result must be a JSON object or array, not null or a scalar"
        )
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ResultValidationError(
            f"result is not JSON-compatible: {exc}"
        ) from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ResultTooLargeError(
            f"result exceeds the {max_bytes}-byte encoded limit"
        )
    return encoded


class SpawnState:
    """The lifecycle states of a spawn reservation."""

    #: Reserved; this spawner owns the (task, attempt) spawn but embody has not
    #: yet been launched (or its handle not yet recorded). A restart reconciles
    #: a reservation stuck here (spawn confirmed -> ``spawned``/``settled``, or
    #: lost -> ``failed`` so a fresh attempt can be reserved).
    RESERVING = "reserving"
    #: Embody launched; the session/worktree handle is recorded.
    SPAWNED = "spawned"
    #: The headless body was stopped intentionally while its task is suspended.
    #: The reservation remains the durable prior-body handle and prevents a
    #: replacement until an explicit resume request releases it.
    COLD = "cold"
    #: The body has stopped or failed and this reservation is concluding the
    #: exact allocation it created. A replacement stays fenced until cleanup
    #: completes or the allocation is explicitly held for attention.
    RELEASING = "releasing"
    #: The reserved (task, attempt) reached a terminal outcome and needs no
    #: further spawning.
    SETTLED = "settled"
    #: The spawn failed (or was lost); a fresh attempt may now be reserved.
    FAILED = "failed"
    #: A failed attempt was explicitly retired by an operator rearm. The row
    #: remains queryable for audit, but no longer counts toward dead-lettering.
    REARMED = "rearmed"

    #: States in which a reservation still "owns" the task's spawn -- no new
    #: attempt may be reserved while one of these is outstanding.
    ACTIVE = frozenset({RESERVING, SPAWNED, COLD, RELEASING})
    #: States a reservation may be released from (a new attempt is allowed).
    RELEASABLE = frozenset({SETTLED, FAILED, REARMED})


def spawn_key(task_id: str, attempt: int) -> str:
    """The canonical reservation key for a (task, attempt) spawn."""
    return f"dispatch-task:{task_id}:{attempt}"


@dataclass(frozen=True)
class Task:
    """A read-only snapshot of a task row."""

    id: str
    title: str
    prompt: str
    status: str
    repo: str | None = None
    requires: list[str] = field(default_factory=list)
    #: Anti-affinity: a hard **exclusion** token set (mirrors ``requires``). A
    #: worker whose advertised token set (capabilities + identity tokens
    #: ``machine:``/``worktree:``/``repo:``) intersects ``excludes`` is
    #: ineligible. Grows monotonically as workers decline with a "not me" token.
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
    #: Producer-selected evaluator identity. Evaluator services consume only
    #: terminal tasks stamped with their own identity; worker pools remain
    #: ordinary filters and never own domain judgment.
    evaluator_ref: str | None = None
    #: Logical resource identity whose spawned worker must be singular across
    #: task episodes. Distinct from ``dedup_key``, which identifies one exact
    #: task create request.
    exclusive_key: str | None = None
    dedup_key: str | None = None
    #: Bounded coordinator-owned create authority metadata. ``None`` preserves
    #: legacy/unmanaged create behavior. The nested scope is one exact canonical
    #: ``repo`` + ``source`` pair; ``producer_id`` is audit metadata rather than
    #: authority, and ``generation`` is permanently retired on handoff.
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
    #: Stable identity that performed the terminal completion. Retained after
    #: ``owner`` is cleared so only that identity may retry-fill a missing result.
    completed_by: str | None = None
    result_ref: str | None = None
    #: Optional schema-neutral completion result, decoded from canonical JSON.
    #: The coordinator stores it atomically with terminal completion.
    result: object | None = None
    #: Whether a structured completion result exists. Bulk reads populate this
    #: without selecting or decoding the result body.
    has_result: bool = False
    #: Latest-only structured progress beat (JSON: phase/summary/blocker/pr/ts),
    #: or None. The "how far toward the goal" signal for at-a-glance tracking.
    latest_progress: str | None = None
    #: Durable goal an agent works toward across turns and embodiments (the
    #: *resumable-goal* feature): the objective (``goal``) and the explicit
    #: criteria for *done* (``done_criteria``). Both None for a plain one-shot
    #: task. The accumulated (append-only) progress toward this goal lives in the
    #: ``task_progress`` table, read via :meth:`progress_log`.
    goal: str | None = None
    done_criteria: str | None = None
    #: The live-session identity that owns this task (captured at ``start``), and
    #: a monotonic fence bumped each claim. Liveness GC compares the *owner's*
    #: session identity -- not mere worktree occupancy -- and fences the requeue
    #: on (owner_session_id, generation) so a reused worktree or a resuming stale
    #: worker cannot corrupt recovery.
    owner_session_id: str | None = None
    generation: int = 0
    #: Informational "last observed" beat (past observation), set by claim/start/
    #: progress. Distinct from the deprecated ``lease_expires_at`` (a future
    #: deadline), which recovery no longer reads.
    last_seen_at: float | None = None
    #: The last liveness verdict GC recorded for this task's owner
    #: (``live``/``gone``/``unknown``), so the buildup metric can classify held
    #: tasks without re-probing the bridge on every ``/health`` call.
    last_liveness: str | None = None
    #: Background-published execution state, independent from lifecycle status.
    #: ``ACTIVE`` means an assigned body is executing now; ``STALLED`` means its
    #: turn is still running but has gone quiet. ``None`` means not executing or
    #: unknown. The supervisor refreshes ``activity_updated_at``.
    activity: str | None = None
    activity_updated_at: float | None = None
    #: Steering (the card + steer seam). ``card`` is the latest-only card object
    #: a worker posts when it needs operator input -- parsed from JSON to a dict
    #: (``{title, status, link, body, request_input, ts}``), or ``None``.
    #: ``awaiting_steer`` is ``True`` while the task is blocked on an operator
    #: answer (a card with a ``request_input`` form was posted and not yet
    #: answered). The submitted answers live in the ``task_steer`` table.
    card: dict | None = None
    awaiting_steer: bool = False
    #: A cold headless task has received a steer/resume request. The supervisor
    #: releases it for re-embodiment only after the prior body is confirmed
    #: stopped, preventing overlapping workers.
    resume_requested: bool = False
    #: Latest durable wake outbox operation for this task. ``wake_status`` is
    #: pending/delivering/delivered/failed/stale; ``wake_operation_id`` is the
    #: deterministic idempotency key used across retries and restarts.
    wake_seq: int = 0
    wake_status: str | None = None
    wake_operation_id: str | None = None

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
            producer_fence=(
                json.loads(row["producer_fence"])
                if row["producer_fence"]
                else None
            ),
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
                bool(row["has_result"])
                if "has_result" in columns
                else raw_result is not None
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
            awaiting_steer=bool(row["awaiting_steer"]),
            resume_requested=bool(row["resume_requested"]),
            wake_seq=row["wake_seq"],
            wake_status=row["wake_status"],
            wake_operation_id=row["wake_operation_id"],
        )


_TASK_DB_COLUMNS = tuple(
    field.name
    for field in dataclasses.fields(Task)
    if field.name not in {"result", "has_result"}
)
_TASK_SELECT = ", ".join((*_TASK_DB_COLUMNS, "result"))
_TASK_BULK_SELECT = ", ".join(
    (*_TASK_DB_COLUMNS, "result IS NOT NULL AS has_result")
)


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


@dataclass(frozen=True)
class ProducerScopeState:
    """Current and recent durable state for one permanent producer scope."""

    scope: dict[str, str]
    managed: bool
    required_label: str | None = None
    current_generation: int = 0
    active_producer: str | None = None
    generations: list[dict[str, object]] = field(default_factory=list)
    history_truncated: bool = False


@dataclass(frozen=True)
class ProducerScopeTransition:
    """A handoff result; the capability is present only on a new transition."""

    state: ProducerScopeState
    producer_capability: str | None = None
    replayed: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            **dataclasses.asdict(self.state),
            "producer_capability": self.producer_capability,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class SpawnReservation:
    """A read-only snapshot of a spawn-reservation row."""

    key: str
    task_id: str
    exclusive_key: str | None
    attempt: int
    state: str
    reserved_by: str | None = None
    session_handle: str | None = None
    worktree: str | None = None
    worktree_ownership: str | None = None
    creating_host: str | None = None
    driver: str | None = None
    release_requested: bool = False
    release_disposition: str | None = None
    detail: str | None = None
    conclusion_state: str | None = None
    conclusion_detail: str | None = None
    reserved_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> SpawnReservation:
        return cls(
            key=row["key"],
            task_id=row["task_id"],
            exclusive_key=row["exclusive_key"],
            attempt=row["attempt"],
            state=row["state"],
            reserved_by=row["reserved_by"],
            session_handle=row["session_handle"],
            worktree=row["worktree"],
            worktree_ownership=row["worktree_ownership"],
            creating_host=row["creating_host"],
            driver=row["driver"],
            release_requested=bool(row["release_requested"]),
            release_disposition=row["release_disposition"],
            detail=row["detail"],
            conclusion_state=row["conclusion_state"],
            conclusion_detail=row["conclusion_detail"],
            reserved_at=row["reserved_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ScheduleRecord:
    """A read-only snapshot of a registered recurring-schedule row.

    ``entry`` is the schedule dict the timer producer consumes verbatim (the
    same shape a hand-authored spec's ``schedules[]`` entry has). Persisting it
    turns the formerly hand-edited JSON spec into a managed registry the
    coordinator owns, so recurring jobs can be registered / listed / inspected /
    removed as first-class objects.
    """

    id: str
    entry: dict
    paused: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ScheduleRecord:
        return cls(
            id=row["id"],
            entry=json.loads(row["spec"]),
            paused=bool(row["paused"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ScheduleLease:
    """A read-only snapshot of a schedule *job-lease* row.

    The job-lease elects a **single producer** for a scope (e.g. the fleet
    chronicler) -- the axis of "which machine runs the timer", distinct from the
    engine's per-task claim. It is **pin-not-failover**: a first writer wins the
    scope and renews it; a different caller is refused and must NOT steal it,
    even if the recorded lease looks stale. This deliberately does *not*
    reintroduce a wall-clock TTL takeover (the complement of the engine's
    liveness-not-lease task recovery); reassignment is an explicit operator act
    (:meth:`TaskQueue.release_schedule_lease` with ``force``). ``expires_at`` /
    ``renewed_at`` are recorded for *observability* only -- staleness is
    reported, never auto-transferred.
    """

    scope: str
    holder: str
    holder_session: str | None = None
    acquired_at: float = 0.0
    renewed_at: float = 0.0
    expires_at: float | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ScheduleLease:
        return cls(
            scope=row["scope"],
            holder=row["holder"],
            holder_session=row["holder_session"],
            acquired_at=row["acquired_at"],
            renewed_at=row["renewed_at"],
            expires_at=row["expires_at"],
        )


@dataclass(frozen=True)
class ResourceReservation:
    """An atomic producer reservation for one external logical resource."""

    key: str
    owner: str
    token: str
    task_id: str | None = None
    acquired_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float | None = None

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ResourceReservation:
        return cls(
            key=row["key"],
            owner=row["owner"],
            token=row["token"],
            task_id=row["task_id"],
            acquired_at=row["acquired_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )


# Column name -> DDL type, applied additively so existing DBs upgrade in place.
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
    # Durable goal: the objective a worker loops toward (``goal``) and the
    # explicit criteria for when it is met (``done_criteria``). Both nullable --
    # a task with no goal behaves exactly as a plain one-shot task. The
    # append-only counterpart of ``latest_progress`` lives in ``task_progress``.
    "goal": "TEXT",
    "done_criteria": "TEXT",
    "evaluator_ref": "TEXT",
    "owner_session_id": "TEXT",
    "generation": "INTEGER NOT NULL DEFAULT 0",
    "last_seen_at": "REAL",
    "last_liveness": "TEXT",
    "activity": "TEXT",
    "activity_updated_at": "REAL",
    # Steering (the card + steer seam): ``card`` is a latest-only JSON object the
    # worker posts to describe what it needs from the operator (title/status/link/
    # body/request_input); ``awaiting_steer`` is 1 while the task is blocked on an
    # operator answer (set when a card carrying a ``request_input`` form is posted,
    # cleared when the operator submits a steer). The submitted answers accumulate
    # in the append-only ``task_steer`` table.
    "card": "TEXT",
    "awaiting_steer": "INTEGER NOT NULL DEFAULT 0",
    "resume_requested": "INTEGER NOT NULL DEFAULT 0",
    "wake_seq": "INTEGER NOT NULL DEFAULT 0",
    "wake_status": "TEXT",
    "wake_operation_id": "TEXT",
}


class TaskQueue:
    """A leased, capability-gated task queue over a SQLite database file.

    Instances are cheap; each operation opens its own short-lived connection so
    the queue is safe to share across threads (each thread gets its own
    connection). WAL mode + ``BEGIN IMMEDIATE`` on the write path give atomic
    claims without a process-wide lock.
    """

    #: The states a dedup *sweep* spans -- every state except the terminal
    #: ``abandoned`` (an abandoned task is not a live duplicate of new work).
    #: This is the corpus the agent-driven "sweep + explore + verify" dedup
    #: flow reads before creating a task; see :meth:`sweep`.
    SWEEP_STATES = (
        Status.PROPOSED,
        Status.QUEUED,
        Status.CLAIMED,
        Status.STARTED,
        Status.SUSPENDED,
        Status.COMPLETED,
    )

    def __init__(
        self,
        db_path: str | Path,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        eval_lease_seconds: int = DEFAULT_EVAL_LEASE_SECONDS,
        payload_dir: str | Path | None = None,
        blob_threshold: int = DEFAULT_BLOB_THRESHOLD,
        result_max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
    ):
        self.db_path = str(db_path)
        self.lease_seconds = lease_seconds
        #: Tight lease for an evaluation-mode claim (see ``claim_one(evaluation=)``).
        self.eval_lease_seconds = eval_lease_seconds
        self.blob_threshold = blob_threshold
        self.result_max_bytes = result_max_bytes
        # Blobs live in a ``payloads/`` directory beside the queue DB unless the
        # caller overrides it (e.g. a shared blob volume).
        if payload_dir is None:
            payload_dir = Path(self.db_path).parent / "payloads"
        self.payloads = PayloadStore(payload_dir)
        self._migrate()

    # -- connection / schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        deadline = time.monotonic() + (_BUSY_TIMEOUT_MS / 1000)
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    conn.close()
                    raise
                time.sleep(0.05)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY)")
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            for name, decl in _COLUMNS.items():
                if name == "id" or name in existing:
                    continue
                # name/decl are internal constants from _COLUMNS, never user input.
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError as exc:
                    # Another concurrently-starting coordinator may have added
                    # this exact column after our PRAGMA snapshot.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            # Canonicalize legacy lane spellings before the repo-scoped unique
            # dedup index is installed. The previous global dedup index already
            # prevented active duplicate keys, so normalization cannot expose an
            # active same-lane collision.
            for row in conn.execute(
                "SELECT id, repo FROM tasks WHERE repo IS NOT NULL"
            ).fetchall():
                canonical = canonicalize_remote(row["repo"])
                if canonical and canonical != row["repo"]:
                    conn.execute(
                        "UPDATE tasks SET repo = ? WHERE id = ?",
                        (canonical, row["id"]),
                    )
            desired_dedup_index = (
                "CREATE UNIQUE INDEX idx_tasks_dedup ON tasks(repo, dedup_key) "
                "WHERE dedup_key IS NOT NULL AND status IN "
                "('proposed','queued','claimed','started','suspended')"
            )
            current_index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_tasks_dedup'"
            ).fetchone()
            current_sql = " ".join(
                str(current_index["sql"] or "").split()
            ) if current_index else ""
            if current_sql != desired_dedup_index:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute("DROP INDEX IF EXISTS idx_tasks_dedup")
                    conn.execute(desired_dedup_index)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_repo ON tasks(repo)")
            # Sentinel-backfill rows created before ``repo`` became required so a
            # legacy task never leaks into a real repo's default-scoped views.
            # Idempotent: after the first run there are no NULL-repo rows (create
            # requires a repo).
            conn.execute(
                "UPDATE tasks SET repo = ? WHERE repo IS NULL", (LEGACY_REPO,)
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_events ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  ts REAL NOT NULL,"
                "  from_status TEXT,"
                "  to_status TEXT,"
                "  worker TEXT,"
                "  note TEXT"
                ")"
            )
            # Rows completed before ``completed_by`` existed retain their
            # original completing identity when the durable audit trail proves
            # exactly one owner.  A completion retry is a completed->completed
            # event, so only the original transition into the terminal state is
            # authoritative.  Ambiguous or unprovable legacy ownership stays
            # NULL and retry-fill fails closed.
            conn.execute(
                "UPDATE tasks SET completed_by = ("
                " SELECT MIN(worker) FROM task_events"
                " WHERE task_events.task_id = tasks.id"
                "   AND task_events.to_status = ?"
                "   AND task_events.from_status <> ?"
                "   AND task_events.worker IS NOT NULL"
                ") WHERE status = ? AND completed_by IS NULL"
                " AND 1 = ("
                " SELECT COUNT(DISTINCT worker) FROM task_events"
                " WHERE task_events.task_id = tasks.id"
                "   AND task_events.to_status = ?"
                "   AND task_events.from_status <> ?"
                "   AND task_events.worker IS NOT NULL"
                ")",
                (
                    Status.COMPLETED,
                    Status.COMPLETED,
                    Status.COMPLETED,
                    Status.COMPLETED,
                    Status.COMPLETED,
                ),
            )
            # Append-only progress log -- the *accumulated* counterpart of the
            # latest-only ``latest_progress`` beat (the *resumable-goal* feature).
            # Each ``record_progress`` appends one row here in addition to
            # overwriting ``latest_progress``, so a re-embodied worker resumes
            # from the recorded progress rather than restarting the goal.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_progress ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  ts REAL NOT NULL,"
                "  phase TEXT,"
                "  summary TEXT,"
                "  detail TEXT,"
                "  worker TEXT"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_progress_task "
                "ON task_progress(task_id)"
            )
            # Append-only steer inbox -- the operator's answers to a task's card
            # (the human-in-the-loop counterpart of ``task_progress``). Each
            # ``submit_steer`` appends one row; ``take_steer`` marks the oldest
            # untaken row consumed and hands it to the resumed worker.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_steer ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  ts REAL NOT NULL,"
                "  fields TEXT NOT NULL DEFAULT '{}',"
                "  sender TEXT,"
                "  taken INTEGER NOT NULL DEFAULT 0,"
                "  taken_at REAL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_steer_task "
                "ON task_steer(task_id)"
            )
            # Durable wake outbox. A steer/resume transaction inserts the wake
            # row before commit; the coordinator loop claims and delivers it
            # later. The row id is also the downstream idempotency key, so a
            # restart retry cannot enqueue the same wake twice.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS wake_outbox ("
                "  id TEXT PRIMARY KEY,"
                "  task_id TEXT NOT NULL,"
                "  generation INTEGER NOT NULL,"
                "  wake_seq INTEGER NOT NULL,"
                "  owner TEXT NOT NULL,"
                "  owner_session_id TEXT,"
                "  message TEXT,"
                "  status TEXT NOT NULL DEFAULT 'pending',"
                "  attempts INTEGER NOT NULL DEFAULT 0,"
                "  not_before REAL NOT NULL DEFAULT 0,"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  delivered_at REAL,"
                "  last_error TEXT,"
                "  delivery_token TEXT,"
                "  delivery_expires_at REAL,"
                "  UNIQUE(task_id, generation, wake_seq)"
                ")"
            )
            wake_columns = {
                r["name"] for r in conn.execute("PRAGMA table_info(wake_outbox)")
            }
            if "delivery_expires_at" not in wake_columns:
                conn.execute(
                    "ALTER TABLE wake_outbox ADD COLUMN delivery_expires_at REAL"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wake_outbox_due "
                "ON wake_outbox(status, not_before, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wake_outbox_task "
                "ON wake_outbox(task_id, wake_seq)"
            )
            # Spawn reservations -- the atomic "exactly one embody spawn per
            # (task, attempt)" record that closes the gap between the queue's
            # transactional claim and the non-transactional CLI-side spawn.
            # Distinct from the execution *claim* (which the embodied worker
            # makes under its own worktree identity); this row is taken by the
            # *spawner* (a `create --spawn` CLI, or the supervisor loop) BEFORE
            # launching embody, so a crash/re-poll/lease-expiry never
            # double-spawns. See :meth:`reserve_spawn`.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS spawn_reservations ("
                "  key TEXT PRIMARY KEY,"
                "  task_id TEXT NOT NULL,"
                "  exclusive_key TEXT,"
                "  attempt INTEGER NOT NULL,"
                "  state TEXT NOT NULL,"
                "  reserved_by TEXT,"
                "  session_handle TEXT,"
                "  worktree TEXT,"
                "  worktree_ownership TEXT,"
                "  creating_host TEXT,"
                "  driver TEXT,"
                "  release_requested INTEGER NOT NULL DEFAULT 0,"
                "  release_disposition TEXT,"
                "  detail TEXT,"
                "  conclusion_state TEXT,"
                "  conclusion_detail TEXT,"
                "  reserved_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL"
                ")"
            )
            reservation_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(spawn_reservations)"
                ).fetchall()
            }
            if "conclusion_state" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations "
                        "ADD COLUMN conclusion_state TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "conclusion_detail" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations "
                        "ADD COLUMN conclusion_detail TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "exclusive_key" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations "
                        "ADD COLUMN exclusive_key TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "release_requested" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations "
                        "ADD COLUMN release_requested INTEGER NOT NULL DEFAULT 0"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "release_disposition" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations "
                        "ADD COLUMN release_disposition TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            for column in ("worktree_ownership", "creating_host", "driver"):
                if column not in reservation_columns:
                    try:
                        conn.execute(
                            f"ALTER TABLE spawn_reservations ADD COLUMN {column} TEXT"
                        )
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc).lower():
                            raise
            conn.execute(
                "UPDATE spawn_reservations SET exclusive_key = ("
                " SELECT exclusive_key FROM tasks"
                " WHERE tasks.id = spawn_reservations.task_id"
                ") WHERE exclusive_key IS NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spawn_res_task "
                "ON spawn_reservations(task_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spawn_res_state "
                "ON spawn_reservations(state)"
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "DROP INDEX IF EXISTS idx_spawn_res_exclusive_active"
                )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_spawn_res_exclusive_active "
                    "ON spawn_reservations(exclusive_key) "
                    "WHERE exclusive_key IS NOT NULL "
                    "AND state IN ('reserving','spawned','cold','releasing')"
                )
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
            # Recurring-schedule registry -- the persisted form of the timer
            # producer's spec entries, so recurring jobs are managed first-class
            # (register/list/inspect/remove/pause) instead of a hand-edited JSON
            # file. ``spec`` is the JSON schedule dict the producer consumes.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schedules ("
                "  id TEXT PRIMARY KEY,"
                "  spec TEXT NOT NULL,"
                "  paused INTEGER NOT NULL DEFAULT 0,"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL"
                ")"
            )
            # Schedule job-leases -- single-producer election per scope
            # (pin-not-failover; see :class:`ScheduleLease`). A row's mere
            # presence pins the scope to ``holder``; no wall-clock takeover.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schedule_leases ("
                "  scope TEXT PRIMARY KEY,"
                "  holder TEXT NOT NULL,"
                "  holder_session TEXT,"
                "  acquired_at REAL NOT NULL,"
                "  renewed_at REAL NOT NULL,"
                "  expires_at REAL"
                ")"
            )
            # Producer resource reservations -- atomic election before a
            # producer creates work for an external resource. Unbound rows are
            # short leases so a crash between election and task creation can
            # recover; binding a task removes the expiry until terminal
            # reconciliation releases the row.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS resource_reservations ("
                "  key TEXT PRIMARY KEY,"
                "  owner TEXT NOT NULL,"
                "  token TEXT NOT NULL,"
                "  task_id TEXT,"
                "  acquired_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  expires_at REAL"
                ")"
            )
            resource_reservation_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(resource_reservations)"
                ).fetchall()
            }
            if "token" not in resource_reservation_columns:
                conn.execute(
                    "ALTER TABLE resource_reservations ADD COLUMN token TEXT"
                )
            for row in conn.execute(
                "SELECT key FROM resource_reservations "
                "WHERE token IS NULL OR token = ''"
            ).fetchall():
                conn.execute(
                    "UPDATE resource_reservations SET token = ? WHERE key = ?",
                    (secrets.token_urlsafe(24), row["key"]),
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_res_owner "
                "ON resource_reservations(owner)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_res_task "
                "ON resource_reservations(task_id)"
            )
            # Supervisor registration registry -- the durable set of units the
            # host's singleton supervisor runs (a lane to spawn for, a schedule,
            # an emitter, an evaluator). ``supervise register`` writes a row here
            # and RETURNS its handle instead of becoming the foreground loop; the
            # singleton daemon reconciles these rows into subprocesses. ``spec``
            # is the JSON config the unit's runtime consumes; ``machine``/``env``
            # scope it to exactly one host's supervisor. See ``registrations.py``.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS registrations ("
                "  id TEXT PRIMARY KEY,"
                "  kind TEXT NOT NULL,"
                "  spec TEXT NOT NULL,"
                "  machine TEXT,"
                "  env TEXT NOT NULL DEFAULT 'default',"
                "  status TEXT NOT NULL DEFAULT 'active',"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registrations_scope "
                "ON registrations(machine, env)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registrations_kind "
                "ON registrations(kind)"
            )
            self._migrate_producer_schema(conn)

    @staticmethod
    def _migrate_producer_schema(conn: sqlite3.Connection) -> None:
        """Install the producer-fence schema under one migration write lock."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            # The first fence prototype keyed authority by source+label. It was
            # never released; preserve any local prototype tables for inspection
            # but do not let their label-dependent authority reopen a real source.
            scope_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(producer_scopes)")
            }
            history_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(producer_scope_generations)"
                )
            }
            legacy_scope = bool(scope_columns) and "repo" not in scope_columns
            legacy_history = bool(history_columns) and "repo" not in history_columns
            if legacy_scope or legacy_history:
                # A renamed table keeps its old index name, which would block
                # creation of the canonical index on the replacement table.
                conn.execute("DROP INDEX IF EXISTS idx_producer_scope_history")
                if legacy_scope:
                    conn.execute(
                        "ALTER TABLE producer_scopes "
                        "RENAME TO producer_scopes_label_v1"
                    )
                if legacy_history:
                    conn.execute(
                        "ALTER TABLE producer_scope_generations "
                        "RENAME TO producer_scope_generations_label_v1"
                    )
            # Coordinator-owned task-create generations. A scope is permanently
            # one canonical repo lane + one exact task source. An optional label
            # also protects that label from alternate or omitted source claims;
            # omission under the managed source is rejected. Every handoff
            # retires N and activates N+1 atomically.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_scopes ("
                "  repo TEXT NOT NULL,"
                "  source TEXT NOT NULL,"
                "  required_label TEXT,"
                "  current_generation INTEGER NOT NULL,"
                "  active_producer TEXT NOT NULL,"
                "  capability_hash TEXT NOT NULL,"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  PRIMARY KEY(repo, source)"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_scope_generations ("
                "  repo TEXT NOT NULL,"
                "  source TEXT NOT NULL,"
                "  generation INTEGER NOT NULL,"
                "  producer_id TEXT NOT NULL,"
                "  capability_hash TEXT NOT NULL,"
                "  required_label TEXT,"
                "  state TEXT NOT NULL,"
                "  activated_at REAL NOT NULL,"
                "  retired_at REAL,"
                "  PRIMARY KEY(repo, source, generation)"
                ")"
            )
            desired_history_index = (
                "CREATE INDEX idx_producer_scope_history "
                "ON producer_scope_generations(repo, source, generation DESC)"
            )
            current_history_index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_producer_scope_history'"
            ).fetchone()
            current_history_sql = " ".join(
                str(current_history_index["sql"] or "").split()
            ) if current_history_index else ""
            if current_history_sql != desired_history_index:
                conn.execute("DROP INDEX IF EXISTS idx_producer_scope_history")
                conn.execute(desired_history_index)
            # Accepted managed create requests are durable independently from a
            # task's lifecycle and from ordinary dedup.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_create_requests ("
                "  repo TEXT NOT NULL,"
                "  source TEXT NOT NULL,"
                "  generation INTEGER NOT NULL,"
                "  request_id TEXT NOT NULL,"
                "  request_hash TEXT NOT NULL,"
                "  producer_id TEXT NOT NULL,"
                "  task_id TEXT NOT NULL,"
                "  accepted_at REAL NOT NULL,"
                "  PRIMARY KEY(repo, source, generation, request_id)"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_producer_requests_task "
                "ON producer_create_requests(task_id)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_claim_rejections ("
                "  task_id TEXT NOT NULL,"
                "  fingerprint TEXT NOT NULL,"
                "  observed_at REAL NOT NULL,"
                "  PRIMARY KEY(task_id, fingerprint)"
                ")"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _now(now: float | None) -> float:
        return time.time() if now is None else now

    @staticmethod
    def _canonical_repo(repo: str | None) -> str | None:
        if repo is None:
            return None
        canonical = canonicalize_remote(repo)
        if not canonical:
            raise TaskError("repo lane must be a canonical, non-empty remote")
        return canonical

    @classmethod
    def _canonical_selector_tokens(
        cls, tokens: Iterable[str], *, strict: bool = True
    ) -> list[str]:
        normalized: list[str] = []
        for token in tokens:
            if not isinstance(token, str):
                if strict:
                    raise TaskError("selector tokens must be strings")
                continue
            if token.startswith("repo:"):
                repo = canonicalize_remote(token.removeprefix("repo:"))
                if not repo:
                    if strict:
                        raise TaskError("repo selector must name a repo lane")
                    normalized.append(token)
                    continue
                token = f"repo:{repo}"
            normalized.append(token)
        return normalized

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        task_id: str,
        *,
        ts: float,
        from_status: str | None,
        to_status: str,
        worker: str | None = None,
        note: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO task_events (task_id, ts, from_status, to_status, worker, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, ts, from_status, to_status, worker, note),
        )

    @staticmethod
    def _completion_event_workers(
        conn: sqlite3.Connection, task_id: str
    ) -> list[str]:
        """Return distinct owners from authoritative completion transitions."""
        rows = conn.execute(
            "SELECT DISTINCT worker FROM task_events"
            " WHERE task_id = ? AND to_status = ? AND from_status <> ?"
            " AND worker IS NOT NULL ORDER BY worker",
            (task_id, Status.COMPLETED, Status.COMPLETED),
        )
        return [str(row["worker"]) for row in rows]

    @staticmethod
    def _validate_producer_token(value: object, *, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value:
            raise ProducerScopeValidationError(f"{field} must be a non-empty string")
        if value != value.strip():
            raise ProducerScopeValidationError(
                f"{field} must not have leading or trailing whitespace"
            )
        if len(value) > limit or not _PRODUCER_TOKEN_RE.fullmatch(value):
            raise ProducerScopeValidationError(
                f"{field} must be an exact token of at most {limit} characters "
                "using letters, digits, '.', '_', ':', '@', '/', or '-'"
            )
        return value

    @classmethod
    def _validate_producer_scope(
        cls, repo: object, source: object
    ) -> tuple[str, str]:
        canonical_repo = canonicalize_remote(repo if isinstance(repo, str) else None)
        if not canonical_repo:
            raise ProducerScopeValidationError(
                "producer scope repo must be a canonical, non-empty repo lane",
                reason="invalid_scope_repo",
            )
        return (
            canonical_repo,
            cls._validate_producer_token(
                source, field="producer scope source", limit=_PRODUCER_SCOPE_SOURCE_MAX
            ),
        )

    @classmethod
    def _validate_required_label(cls, value: object | None) -> str | None:
        if value is None:
            return None
        return cls._validate_producer_token(
            value,
            field="producer scope required_label",
            limit=_PRODUCER_SCOPE_LABEL_MAX,
        )

    @staticmethod
    def _validate_producer_capability(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise ProducerScopeValidationError(
                "producer_capability must be a non-empty string",
                reason="invalid_capability",
            )
        if len(value) > _PRODUCER_CAPABILITY_MAX:
            raise ProducerScopeValidationError(
                f"producer_capability must be at most {_PRODUCER_CAPABILITY_MAX} characters",
                reason="invalid_capability",
            )
        return value

    @staticmethod
    def _capability_hash(capability: str) -> str:
        return hashlib.sha256(capability.encode("utf-8")).hexdigest()

    @classmethod
    def _normalize_producer_fence(
        cls,
        producer_scope: Mapping[str, object] | None,
        producer_id: str | None,
        producer_generation: int | None,
        producer_capability: str | None,
        producer_request_id: str | None,
        *,
        repo: str,
        source: str | None,
    ) -> dict[str, object] | None:
        provided = (
            producer_scope is not None,
            producer_id is not None,
            producer_generation is not None,
            producer_capability is not None,
            producer_request_id is not None,
        )
        if not any(provided):
            return None
        if not all(provided):
            raise ProducerScopeValidationError(
                "producer_scope, producer_id, producer_generation, "
                "producer_capability, and producer_request_id must be provided together"
            )
        assert producer_scope is not None
        if set(producer_scope) != {"repo", "source"}:
            raise ProducerScopeValidationError(
                "producer_scope must contain exactly 'repo' and 'source'"
            )
        scope_repo, scope_source = cls._validate_producer_scope(
            producer_scope["repo"], producer_scope["source"]
        )
        producer = cls._validate_producer_token(
            producer_id, field="producer_id", limit=_PRODUCER_ID_MAX
        )
        capability = cls._validate_producer_capability(producer_capability)
        request_id = cls._validate_producer_token(
            producer_request_id,
            field="producer_request_id",
            limit=_PRODUCER_REQUEST_ID_MAX,
        )
        if (
            isinstance(producer_generation, bool)
            or not isinstance(producer_generation, int)
            or producer_generation < 1
        ):
            raise ProducerScopeValidationError(
                "producer_generation must be an integer greater than zero"
            )
        if repo != scope_repo:
            raise ProducerScopeValidationError(
                "producer scope repo must exactly match the task repo lane",
                reason="scope_repo_mismatch",
                repo=scope_repo,
                source=scope_source,
                producer_request_id=request_id,
            )
        if source != scope_source:
            raise ProducerScopeValidationError(
                "producer scope source must exactly match the task source",
                reason="scope_source_mismatch",
                repo=scope_repo,
                source=scope_source,
                producer_request_id=request_id,
            )
        return {
            "scope": {"repo": scope_repo, "source": scope_source},
            "producer_id": producer,
            "generation": producer_generation,
            "capability": capability,
            "request_id": request_id,
        }

    @staticmethod
    def _producer_request_hash(fields: Mapping[str, object]) -> str:
        try:
            encoded = json.dumps(
                fields,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProducerScopeValidationError(
                "managed create fields must be finite JSON values",
                reason="invalid_request_json",
            ) from exc
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _producer_scope_row(
        conn: sqlite3.Connection,
        *,
        repo: str,
        source: str | None,
    ) -> sqlite3.Row | None:
        if not source:
            return None
        return conn.execute(
            "SELECT repo, source, required_label, current_generation, "
            "active_producer, capability_hash FROM producer_scopes "
            "WHERE repo = ? AND source = ?",
            (repo, source),
        ).fetchone()

    @staticmethod
    def _required_label_scope_rows(
        conn: sqlite3.Connection,
        *,
        labels: Iterable[object],
    ) -> list[sqlite3.Row]:
        protected_labels = tuple(
            dict.fromkeys(label for label in labels if isinstance(label, str))
        )
        if not protected_labels:
            return []
        placeholders = ",".join("?" for _ in protected_labels)
        return conn.execute(
            "SELECT repo, source, required_label, current_generation, "
            "active_producer, capability_hash FROM producer_scopes "
            f"WHERE required_label IN ({placeholders})",
            protected_labels,
        ).fetchall()

    @classmethod
    def _task_fence_matches_scope(
        cls,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        repo: str,
        source: str,
        required_label: str,
    ) -> bool:
        """Return whether a task has durable accepted provenance for a scope."""
        if row["repo"] != repo or row["source"] != source:
            return False
        try:
            fence = json.loads(row["producer_fence"])
        except (TypeError, ValueError):
            return False
        if not isinstance(fence, dict) or set(fence) != {
            "scope",
            "producer_id",
            "generation",
            "request_id",
        }:
            return False
        scope = fence["scope"]
        generation = fence["generation"]
        if (
            not isinstance(scope, dict)
            or scope
            != {
                "repo": repo,
                "source": source,
            }
            or not isinstance(fence["producer_id"], str)
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or not isinstance(fence["request_id"], str)
        ):
            return False
        generation_row = conn.execute(
            "SELECT producer_id, required_label "
            "FROM producer_scope_generations "
            "WHERE repo = ? AND source = ? AND generation = ?",
            (repo, source, generation),
        ).fetchone()
        if (
            generation_row is None
            or generation_row["producer_id"] != fence["producer_id"]
            or generation_row["required_label"] != required_label
        ):
            return False
        request = conn.execute(
            "SELECT request_hash, producer_id, task_id "
            "FROM producer_create_requests "
            "WHERE repo = ? AND source = ? AND generation = ? "
            "AND request_id = ?",
            (
                repo,
                source,
                generation,
                fence["request_id"],
            ),
        ).fetchone()
        return bool(
            request is not None
            and request["producer_id"] == fence["producer_id"]
            and request["task_id"] == row["id"]
            and row["producer_request_hash"]
            and hmac.compare_digest(
                str(request["request_hash"]),
                str(row["producer_request_hash"]),
            )
        )

    @classmethod
    def _claim_fence_rejection(
        cls,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, object] | None:
        """Describe why a protected-label task cannot be claimed."""
        try:
            labels = json.loads(row["labels"] or "[]")
        except (TypeError, ValueError):
            labels = None
        if not isinstance(labels, list):
            return {
                "task_id": str(row["id"]),
                "status": str(row["status"]),
                "repo": str(row["repo"]),
                "reason": "invalid_labels",
            }
        scopes = cls._required_label_scope_rows(conn, labels=labels)
        if not scopes:
            return None
        if len(scopes) != 1:
            return {
                "task_id": str(row["id"]),
                "status": str(row["status"]),
                "repo": str(row["repo"]),
                "reason": "ambiguous_required_label",
            }
        managed = scopes[0]
        detail: dict[str, object] = {
            "task_id": str(row["id"]),
            "status": str(row["status"]),
            "repo": str(row["repo"]),
            "source": str(row["source"]) if row["source"] is not None else None,
            "required_label": str(managed["required_label"]),
            "owning_repo": str(managed["repo"]),
            "owning_source": str(managed["source"]),
        }
        if row["repo"] != managed["repo"]:
            detail["reason"] = "required_label_repo_mismatch"
        elif row["source"] != managed["source"]:
            detail["reason"] = "required_label_source_mismatch"
        elif not cls._task_fence_matches_scope(
            conn,
            row,
            repo=str(managed["repo"]),
            source=str(managed["source"]),
            required_label=str(managed["required_label"]),
        ):
            detail["reason"] = "producer_fence_mismatch"
        else:
            return None
        fingerprint_fields = {
            "task_id": row["id"],
            "repo": row["repo"],
            "source": row["source"],
            "labels": row["labels"],
            "producer_fence": row["producer_fence"],
            "producer_request_hash": row["producer_request_hash"],
            "owning_repo": managed["repo"],
            "owning_source": managed["source"],
            "required_label": managed["required_label"],
            "reason": detail["reason"],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_fields,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        detail["fingerprint"] = fingerprint[:16]
        detail["_fingerprint"] = fingerprint
        return detail

    @classmethod
    def _scope_blockers(
        cls,
        conn: sqlite3.Connection,
        *,
        repo: str,
        source: str,
        required_label: str,
        scope_exists: bool,
    ) -> dict[str, object] | None:
        """Summarize nonterminal label rows that are not accepted by this scope."""
        rows = conn.execute(
            f"SELECT {_TASK_BULK_SELECT}, producer_request_hash FROM tasks "
            "WHERE status IN (?,?,?,?,?) ORDER BY created_at ASC",
            (
                Status.PROPOSED,
                Status.QUEUED,
                Status.CLAIMED,
                Status.STARTED,
                Status.SUSPENDED,
            ),
        ).fetchall()
        blockers: list[sqlite3.Row] = []
        for row in rows:
            try:
                labels = json.loads(row["labels"] or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(labels, list) or required_label not in labels:
                continue
            if scope_exists and cls._task_fence_matches_scope(
                conn,
                row,
                repo=repo,
                source=source,
                required_label=required_label,
            ):
                continue
            blockers.append(row)
        if not blockers:
            return None
        status_counts: dict[str, int] = {}
        for row in blockers:
            status = str(row["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
        task_ids = [str(row["id"]) for row in blockers[:_PRODUCER_BLOCKING_TASK_LIMIT]]
        return {
            "blocking_task_count": len(blockers),
            "blocking_task_ids": task_ids,
            "blocking_status_counts": status_counts,
            "blocking_ids_truncated": len(blockers) > len(task_ids),
        }

    @staticmethod
    def _record_claim_rejection(
        conn: sqlite3.Connection,
        detail: dict[str, object],
        *,
        ts: float,
    ) -> bool:
        """Persist one audit row per task/mismatch fingerprint."""
        fingerprint = str(detail["_fingerprint"])
        inserted = conn.execute(
            "INSERT OR IGNORE INTO producer_claim_rejections "
            "(task_id, fingerprint, observed_at) VALUES (?,?,?)",
            (detail["task_id"], fingerprint, ts),
        )
        if inserted.rowcount != 1:
            return False
        TaskQueue._audit(
            conn,
            str(detail["task_id"]),
            ts=ts,
            from_status=Status.QUEUED,
            to_status=Status.QUEUED,
            note=f"producer.claim_rejected:{detail['reason']}",
        )
        return True

    @staticmethod
    def _producer_scope_state_from_conn(
        conn: sqlite3.Connection,
        repo: str,
        source: str,
        *,
        history_limit: int = _PRODUCER_HISTORY_LIMIT,
    ) -> ProducerScopeState:
        row = conn.execute(
            "SELECT required_label, current_generation, active_producer "
            "FROM producer_scopes WHERE repo = ? AND source = ?",
            (repo, source),
        ).fetchone()
        scope = {"repo": repo, "source": source}
        if row is None:
            return ProducerScopeState(scope=scope, managed=False)
        history = conn.execute(
            "SELECT generation, producer_id, state, activated_at, retired_at "
            "FROM producer_scope_generations WHERE repo = ? AND source = ? "
            "ORDER BY generation DESC LIMIT ?",
            (repo, source, history_limit + 1),
        ).fetchall()
        truncated = len(history) > history_limit
        generations = [
            {
                "generation": item["generation"],
                "producer_id": item["producer_id"],
                "state": item["state"],
                "activated_at": item["activated_at"],
                "retired_at": item["retired_at"],
            }
            for item in history[:history_limit]
        ]
        return ProducerScopeState(
            scope=scope,
            managed=True,
            required_label=row["required_label"],
            current_generation=row["current_generation"],
            active_producer=row["active_producer"],
            generations=generations,
            history_truncated=truncated,
        )

    def producer_scope_status(
        self, repo: str, source: str
    ) -> ProducerScopeState:
        """Inspect one exact producer scope without mutating it."""
        repo, source = self._validate_producer_scope(repo, source)
        with self._connect() as conn:
            return self._producer_scope_state_from_conn(conn, repo, source)

    def handoff_producer_scope(
        self,
        repo: str,
        source: str,
        *,
        producer_id: str,
        expected_generation: int,
        required_label: str | None = None,
        now: float | None = None,
    ) -> ProducerScopeTransition:
        """Atomically retire generation N and activate N+1 for one producer.

        ``expected_generation=0`` is the only way to move an unmanaged scope
        into managed generation 1. Managed scopes require an exact compare-and-
        swap against their current generation. A lost successful response may be
        retried with the same expected generation and producer, but the one-time
        capability is never returned again. No operation clears, decrements, or
        reopens a retired generation.
        """
        repo, source = self._validate_producer_scope(repo, source)
        label = self._validate_required_label(required_label)
        producer = self._validate_producer_token(
            producer_id, field="producer_id", limit=_PRODUCER_ID_MAX
        )
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise ProducerScopeValidationError(
                "expected_generation must be an integer greater than or equal to zero"
            )
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT required_label, current_generation, active_producer "
                "FROM producer_scopes WHERE repo = ? AND source = ?",
                (repo, source),
            ).fetchone()
            current_label = current["required_label"] if current is not None else None
            if current is not None and label is not None and label != current_label:
                raise ProducerFenceError(
                    "producer scope required_label is immutable",
                    reason="scope_label_mismatch",
                    repo=repo,
                    source=source,
                    required_label=current_label,
                    requested_producer=producer,
                    active_producer=current["active_producer"],
                    requested_generation=expected_generation,
                    current_generation=current["current_generation"],
                )
            effective_label = current_label if current is not None else label
            if effective_label is not None:
                conflict = conn.execute(
                    "SELECT repo, source FROM producer_scopes "
                    "WHERE required_label = ? "
                    "AND (repo <> ? OR source <> ?) "
                    "LIMIT 1",
                    (effective_label, repo, source),
                ).fetchone()
                if conflict is not None:
                    raise ProducerFenceError(
                        "producer scope required_label is already owned by "
                        "another producer scope on this coordinator",
                        reason="required_label_conflict",
                        repo=repo,
                        source=source,
                        required_label=effective_label,
                        requested_producer=producer,
                        requested_generation=expected_generation,
                        diagnostics={
                            "owning_repo": conflict["repo"],
                            "owning_source": conflict["source"],
                        },
                    )
                blockers = self._scope_blockers(
                    conn,
                    repo=repo,
                    source=source,
                    required_label=effective_label,
                    scope_exists=current is not None,
                )
                if blockers is not None:
                    raise ProducerFenceError(
                        "producer scope cannot transition while its managed label "
                        "has nonterminal tasks without matching accepted fence metadata",
                        reason="scope_not_quiescent",
                        repo=repo,
                        source=source,
                        required_label=effective_label,
                        requested_producer=producer,
                        active_producer=(
                            current["active_producer"]
                            if current is not None
                            else None
                        ),
                        requested_generation=expected_generation,
                        current_generation=(
                            current["current_generation"]
                            if current is not None
                            else 0
                        ),
                        diagnostics=blockers,
                    )
            if current is None:
                if expected_generation != 0:
                    raise ProducerFenceError(
                        "producer scope is unmanaged; initial handoff requires "
                        "expected_generation=0",
                        reason="unmanaged_scope",
                        repo=repo,
                        source=source,
                        required_label=label,
                        requested_producer=producer,
                        requested_generation=expected_generation,
                        current_generation=0,
                    )
                next_generation = 1
                capability = secrets.token_urlsafe(32)
                capability_hash = self._capability_hash(capability)
                conn.execute(
                    "INSERT INTO producer_scopes "
                    "(repo, source, required_label, current_generation, active_producer, "
                    "capability_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        repo,
                        source,
                        label,
                        next_generation,
                        producer,
                        capability_hash,
                        ts,
                        ts,
                    ),
                )
            else:
                current_generation = int(current["current_generation"])
                if expected_generation != current_generation:
                    if (
                        expected_generation + 1 == current_generation
                        and producer == current["active_producer"]
                    ):
                        state = self._producer_scope_state_from_conn(
                            conn, repo, source
                        )
                        conn.execute("COMMIT")
                        return ProducerScopeTransition(state=state, replayed=True)
                    raise ProducerFenceError(
                        f"producer fence generation mismatch: expected "
                        f"{expected_generation}, current is {current_generation}",
                        reason="generation_mismatch",
                        repo=repo,
                        source=source,
                        required_label=current_label,
                        requested_producer=producer,
                        active_producer=current["active_producer"],
                        requested_generation=expected_generation,
                        current_generation=current_generation,
                    )
                next_generation = current_generation + 1
                capability = secrets.token_urlsafe(32)
                capability_hash = self._capability_hash(capability)
                retired = conn.execute(
                    "UPDATE producer_scope_generations SET state = 'retired', "
                    "retired_at = ? WHERE repo = ? AND source = ? "
                    "AND generation = ? AND state = 'active'",
                    (ts, repo, source, current_generation),
                )
                if retired.rowcount != 1:
                    raise ProducerFenceError(
                        "producer fence history is inconsistent; current generation "
                        "is not active",
                        reason="inconsistent_history",
                        repo=repo,
                        source=source,
                        required_label=current_label,
                        active_producer=current["active_producer"],
                        current_generation=current_generation,
                    )
                conn.execute(
                    "UPDATE producer_scopes SET current_generation = ?, "
                    "active_producer = ?, capability_hash = ?, updated_at = ? "
                    "WHERE repo = ? AND source = ?",
                    (
                        next_generation,
                        producer,
                        capability_hash,
                        ts,
                        repo,
                        source,
                    ),
                )
            conn.execute(
                "INSERT INTO producer_scope_generations "
                "(repo, source, generation, producer_id, capability_hash, "
                "required_label, state, activated_at) "
                "VALUES (?,?,?,?,?,?, 'active', ?)",
                (
                    repo,
                    source,
                    next_generation,
                    producer,
                    capability_hash,
                    current["required_label"] if current is not None else label,
                    ts,
                ),
            )
            state = self._producer_scope_state_from_conn(conn, repo, source)
            conn.execute("COMMIT")
            return ProducerScopeTransition(
                state=state,
                producer_capability=capability,
            )

    def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None:
        row = conn.execute(
            f"SELECT {_TASK_SELECT} FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return Task._from_row(row) if row else None

    def _enqueue_wake(
        self,
        conn: sqlite3.Connection,
        task: Task,
        *,
        message: str | None,
        ts: float,
    ) -> WakeOperation:
        """Insert one owner wake in the caller's task-state transaction."""
        if not task.owner:
            raise TaskError(f"task {task.id!r} has no owner to wake")
        wake_seq = task.wake_seq + 1
        operation_id = f"wake:{task.id}:{task.generation}:{wake_seq}"
        conn.execute(
            "UPDATE tasks SET wake_seq = ?, wake_status = 'pending',"
            " wake_operation_id = ? WHERE id = ?",
            (wake_seq, operation_id, task.id),
        )
        conn.execute(
            "INSERT INTO wake_outbox "
            "(id, task_id, generation, wake_seq, owner, owner_session_id,"
            " message, status, attempts, not_before, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (
                operation_id,
                task.id,
                task.generation,
                wake_seq,
                task.owner,
                task.owner_session_id,
                message,
                ts,
                ts,
                ts,
            ),
        )
        self._audit(
            conn,
            task.id,
            ts=ts,
            from_status=task.status,
            to_status=task.status,
            worker=task.owner,
            note=f"wake pending ({operation_id})",
        )
        row = conn.execute(
            "SELECT * FROM wake_outbox WHERE id = ?", (operation_id,)
        ).fetchone()
        return WakeOperation._from_row(row)

    @staticmethod
    def _has_headless_reservation(
        conn: sqlite3.Connection, task_id: str
    ) -> bool:
        row = conn.execute(
            "SELECT 1 FROM spawn_reservations"
            " WHERE task_id = ? AND state IN (?, ?) AND"
            " (session_handle LIKE 'local-body:%' OR"
            " session_handle LIKE 'fleet-body:%')"
            " ORDER BY attempt DESC LIMIT 1",
            (task_id, SpawnState.SPAWNED, SpawnState.COLD),
        ).fetchone()
        return row is not None

    @staticmethod
    def _has_cold_headless_reservation(
        conn: sqlite3.Connection, task_id: str
    ) -> bool:
        row = conn.execute(
            "SELECT 1 FROM spawn_reservations"
            " WHERE task_id = ? AND state = ? AND"
            " (session_handle LIKE 'local-body:%' OR"
            " session_handle LIKE 'fleet-body:%')"
            " LIMIT 1",
            (task_id, SpawnState.COLD),
        ).fetchone()
        return row is not None

    # -- payload -------------------------------------------------------------

    def _payload_needs_spill(
        self, payload_ref: str | None, payload_inline: str | None
    ) -> bool:
        return (
            payload_ref is None
            and payload_inline is not None
            and len(payload_inline.encode("utf-8")) > self.blob_threshold
        )

    def _spill_committed_payload(self, task_id: str, content: str) -> None:
        """Move a committed inline payload to the blob store.

        The task commits with its complete inline content first. Blob I/O then
        happens without a SQLite write lock, followed by one guarded row update.
        A failed insert therefore cannot orphan a blob, and a crash before the
        update leaves a readable inline payload rather than a broken reference.
        """
        ref = self.payloads.put(content)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE tasks SET payload_ref = ?, payload_inline = NULL "
                "WHERE id = ? AND payload_ref IS NULL AND payload_inline = ?",
                (ref, task_id, content),
            )
            conn.execute("COMMIT")

    def read_payload(self, task_or_id: Task | str) -> str | None:
        """Resolve a task's payload content (inline or blob), or ``None``.

        Returns the inline text when present, the blob content when
        ``payload_ref`` is a ``blob:`` ref, and ``None`` for an absent payload or
        an external/opaque ``payload_ref`` (e.g. ``pr/123``) the caller resolves
        itself.
        """
        task = self.get(task_or_id) if isinstance(task_or_id, str) else task_or_id
        if task is None:
            raise TaskError(f"no such task: {task_or_id}")
        if task.payload_inline is not None:
            return task.payload_inline
        if is_blob_ref(task.payload_ref):
            return self.payloads.get(task.payload_ref)  # type: ignore[arg-type]
        return None

    def _encode_result(self, result: object | None) -> str | None:
        """Validate and canonically encode an optional JSON-compatible result.

        Results are structured objects/arrays, never JSON null, scalars, or
        double-encoded JSON strings. The hard byte limit bounds task rows.
        Results deliberately remain in SQLite instead of spilling to the payload
        blob store: completion must atomically persist the terminal status,
        ``result_ref``, and the result bytes in one coordinator transaction.
        """
        return encode_result(result, max_bytes=self.result_max_bytes)

    def read_result(self, task_or_id: Task | str) -> StructuredResult | None:
        """Return a task's decoded structured completion result, or ``None``."""
        task = self.get(task_or_id) if isinstance(task_or_id, str) else task_or_id
        if task is None:
            raise TaskError(f"no such task: {task_or_id}")
        result = task.result
        return result if isinstance(result, (dict, list)) else None

    # -- producers -----------------------------------------------------------

    @staticmethod
    def _legacy_reviewer_target(dedup_key: str | None) -> str | None:
        """Parse the pre-target-stable reviewer dedup format."""
        if not dedup_key or not dedup_key.startswith("recipe:reviewer:"):
            return None
        marker = ":pr="
        repo_marker = ":repo="
        pr_at = dedup_key.rfind(marker)
        repo_at = dedup_key.rfind(repo_marker)
        if pr_at < 0 or repo_at <= pr_at:
            return None
        change = dedup_key[pr_at + len(marker) : repo_at]
        repo = dedup_key[repo_at + len(repo_marker) :]
        if not repo or not change:
            return None
        return canonical_reviewer_target(repo, change)

    @classmethod
    def _reviewer_target(cls, dedup_key: str | None) -> str | None:
        if dedup_key and dedup_key.startswith("recipe:reviewer:target="):
            return dedup_key.removeprefix("recipe:reviewer:target=")
        return cls._legacy_reviewer_target(dedup_key)

    def create(
        self,
        title: str,
        *,
        repo: str | None = None,
        prompt: str = "",
        status: str = Status.QUEUED,
        requires: Sequence[str] | None = None,
        excludes: Sequence[str] | None = None,
        affinity: dict[str, str] | None = None,
        labels: Sequence[str] | None = None,
        payload_ref: str | None = None,
        payload_inline: str | None = None,
        target_machine: str | None = None,
        target_worktree: str | None = None,
        target_repo: str | None = None,
        source: str | None = None,
        origin_ref: str | None = None,
        evaluator_ref: str | None = None,
        exclusive_key: str | None = None,
        supersede_exclusive_key: bool = False,
        dedup_key: str | None = None,
        producer_scope: Mapping[str, object] | None = None,
        producer_id: str | None = None,
        producer_generation: int | None = None,
        producer_capability: str | None = None,
        producer_request_id: str | None = None,
        goal: str | None = None,
        done_criteria: str | None = None,
        not_before: float = 0.0,
        claim_as: str | None = None,
        now: float | None = None,
        _with_outcome: bool = False,
    ) -> Task | CreationOutcome:
        """Insert a task (default status ``queued``; ``proposed`` for a draft).

        ``repo`` is the **lane** -- the canonical remote of the producing agent's
        harness repo -- and is **required**: tasks stay in their own repo's lane,
        so a consumer only sees/claims work for its own repo. (A cross-repo
        *code* target is separate metadata, ``target_repo``; the lane agent does
        that work via ``working-cross-repo``, never by launching another repo's
        harness.)

        If ``dedup_key`` collides with an existing non-terminal task in the same
        repo lane, no new row is created and the *existing* task is returned.
        Terminal rows release the key so a later request can create new work.
        Managed creates use a separate ``producer_request_id`` ledger: an exact
        retry with that generation's capability returns the accepted task from
        any status after generation retirement, while a new request id retains
        ordinary dedup semantics. A managed ``required_label`` also binds back
        to its owning source, so caller-selected alternate or omitted sources
        cannot place unfenced work in the protected label pool.

        ``claim_as`` makes this an **atomic create-and-claim**: a brand-new task
        is inserted already ``claimed`` by that owner in the *same* transaction,
        so there is no queued-and-unclaimed gap for another worker to race into.
        On a ``dedup_key`` collision the existing task is returned **as-is**
        (never re-claimed) -- so a caller can tell it lost the race by seeing the
        returned task's ``owner`` is not itself. This is the lazy-carve
        open-ended-pickup primitive: ``create(dedup_key=<subject>, claim_as=me)``
        either mints the subject as mine or hands me the row someone else already
        took.
        """
        if status not in (Status.QUEUED, Status.PROPOSED):
            raise TaskError(f"new task must be 'queued' or 'proposed', not {status!r}")
        if exclusive_key is not None:
            exclusive_key = exclusive_key.strip() or None
        if supersede_exclusive_key and exclusive_key is None:
            raise TaskError(
                "supersede_exclusive_key requires a non-empty exclusive_key"
            )
        canonical_repo = self._canonical_repo(repo)
        if not canonical_repo:
            raise TaskError(
                "task requires a repo (the lane -- the producing repo's canonical "
                "remote); the CLI resolves it from the CWD or --repo"
            )
        if (
            isinstance(not_before, bool)
            or not isinstance(not_before, (int, float))
            or not math.isfinite(float(not_before))
        ):
            raise ProducerScopeValidationError(
                "not_before must be a finite number",
                reason="invalid_not_before",
                repo=canonical_repo,
                source=source,
                producer_request_id=producer_request_id,
            )
        not_before = float(not_before)
        normalized_requires = self._canonical_selector_tokens(requires or ())
        normalized_excludes = self._canonical_selector_tokens(excludes or ())
        fence = self._normalize_producer_fence(
            producer_scope,
            producer_id,
            producer_generation,
            producer_capability,
            producer_request_id,
            repo=canonical_repo,
            source=source,
        )
        fence_record = None
        if fence is not None:
            fence_record = {
                "scope": fence["scope"],
                "producer_id": fence["producer_id"],
                "generation": fence["generation"],
                "request_id": fence["request_id"],
            }
        fence_json = (
            json.dumps(fence_record, separators=(",", ":"), sort_keys=True)
            if fence_record is not None
            else None
        )
        request_hash = None
        compatible_request_hashes: set[str | None] = set()
        if fence is not None:
            request_fields = {
                "title": title,
                "repo": canonical_repo,
                "prompt": prompt,
                "status": status,
                "requires": sorted(set(normalized_requires)),
                "excludes": sorted(set(normalized_excludes)),
                "affinity": dict(affinity or {}),
                "labels": sorted(set(labels or ())),
                "payload_ref": payload_ref,
                "payload_inline": payload_inline,
                "target_machine": target_machine,
                "target_worktree": target_worktree,
                "target_repo": target_repo,
                "source": source,
                "origin_ref": origin_ref,
                "evaluator_ref": evaluator_ref,
                "exclusive_key": exclusive_key,
                "dedup_key": dedup_key,
                "producer_scope": fence["scope"],
                "producer_id": fence["producer_id"],
                "producer_generation": fence["generation"],
                "goal": goal,
                "done_criteria": done_criteria,
            }
            request_hash = self._producer_request_hash(request_fields)
            compatible_request_hashes.add(request_hash)
            pre_exclusive_fields = dict(request_fields)
            pre_exclusive_fields.pop("exclusive_key")
            compatible_request_hashes.add(
                self._producer_request_hash(pre_exclusive_fields)
            )
            raw_requires = sorted(set(requires or ()))
            raw_excludes = sorted(set(excludes or ()))
            if (
                raw_requires != request_fields["requires"]
                or raw_excludes != request_fields["excludes"]
            ):
                legacy_fields = dict(request_fields)
                legacy_fields["requires"] = raw_requires
                legacy_fields["excludes"] = raw_excludes
                compatible_request_hashes.add(
                    self._producer_request_hash(legacy_fields)
                )
                legacy_fields.pop("exclusive_key")
                compatible_request_hashes.add(
                    self._producer_request_hash(legacy_fields)
                )
        ts = self._now(now)
        task_id = uuid.uuid4().hex
        spill_content = (
            payload_inline
            if self._payload_needs_spill(payload_ref, payload_inline)
            else None
        )
        accepted: Task | None = None
        request_already_recorded = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                managed = self._producer_scope_row(
                    conn, repo=canonical_repo, source=source
                )
                label_scopes = self._required_label_scope_rows(
                    conn,
                    labels=labels or (),
                )
                if len(label_scopes) > 1:
                    raise ProducerFenceError(
                        "task labels resolve to ambiguous managed producer scopes",
                        reason="ambiguous_required_label",
                        repo=canonical_repo,
                        source=source,
                    )
                label_scope = label_scopes[0] if label_scopes else None
                if label_scope is not None:
                    owning_scope = {
                        "repo": str(label_scope["repo"]),
                        "source": str(label_scope["source"]),
                    }
                    if owning_scope["repo"] != canonical_repo:
                        raise ProducerFenceError(
                            "task required label belongs to a managed producer "
                            "scope in a different repo lane",
                            reason="required_label_scope_mismatch",
                            repo=canonical_repo,
                            source=source,
                            required_label=label_scope["required_label"],
                            active_producer=label_scope["active_producer"],
                            current_generation=label_scope["current_generation"],
                            producer_request_id=(
                                str(fence["request_id"])
                                if fence is not None
                                else None
                            ),
                            diagnostics={
                                "owning_repo": owning_scope["repo"],
                                "owning_source": owning_scope["source"],
                            },
                        )
                    if fence is None:
                        raise ProducerFenceError(
                            "task carries a managed required label and requires "
                            "producer authority for its owning scope",
                            reason="missing_fence",
                            repo=canonical_repo,
                            source=label_scope["source"],
                            required_label=label_scope["required_label"],
                            active_producer=label_scope["active_producer"],
                            current_generation=label_scope["current_generation"],
                        )
                    fence_scope = fence["scope"]
                    assert isinstance(fence_scope, dict)
                    if fence_scope != owning_scope:
                        raise ProducerFenceError(
                            "task required label belongs to a different managed "
                            "producer scope",
                            reason="required_label_scope_mismatch",
                            repo=canonical_repo,
                            source=label_scope["source"],
                            required_label=label_scope["required_label"],
                            requested_producer=str(fence["producer_id"]),
                            active_producer=label_scope["active_producer"],
                            requested_generation=int(fence["generation"]),
                            current_generation=label_scope["current_generation"],
                            producer_request_id=str(fence["request_id"]),
                            diagnostics={
                                "owning_repo": owning_scope["repo"],
                                "owning_source": owning_scope["source"],
                            },
                        )
                if fence is None:
                    if managed is not None:
                        raise ProducerFenceError(
                            "task source is permanently generation-managed and "
                            "requires producer authority",
                            reason="missing_fence",
                            repo=canonical_repo,
                            source=managed["source"],
                            required_label=managed["required_label"],
                            active_producer=managed["active_producer"],
                            current_generation=managed["current_generation"],
                        )
                else:
                    scope = fence["scope"]
                    assert isinstance(scope, dict)
                    scope_source = str(scope["source"])
                    requested_producer = str(fence["producer_id"])
                    requested_generation = int(fence["generation"])
                    request_id = str(fence["request_id"])
                    if managed is None:
                        raise ProducerFenceError(
                            "producer scope is not generation-managed",
                            reason="unmanaged_scope",
                            repo=canonical_repo,
                            source=scope_source,
                            requested_producer=requested_producer,
                            requested_generation=requested_generation,
                            producer_request_id=request_id,
                            current_generation=0,
                        )
                    required_label = managed["required_label"]
                    generation = conn.execute(
                        "SELECT producer_id, capability_hash, required_label "
                        "FROM producer_scope_generations "
                        "WHERE repo = ? AND source = ? AND generation = ?",
                        (
                            canonical_repo,
                            scope_source,
                            requested_generation,
                        ),
                    ).fetchone()
                    if generation is None:
                        raise ProducerFenceError(
                            "producer generation is not present in managed scope history",
                            reason="unknown_generation",
                            repo=canonical_repo,
                            source=scope_source,
                            required_label=required_label,
                            requested_producer=requested_producer,
                            active_producer=managed["active_producer"],
                            requested_generation=requested_generation,
                            current_generation=managed["current_generation"],
                            producer_request_id=request_id,
                        )
                    request = conn.execute(
                        "SELECT request_hash, producer_id, task_id "
                        "FROM producer_create_requests "
                        "WHERE repo = ? AND source = ? AND generation = ? "
                        "AND request_id = ?",
                        (
                            canonical_repo,
                            scope_source,
                            requested_generation,
                            request_id,
                        ),
                    ).fetchone()
                    if (
                        requested_generation != managed["current_generation"]
                        and request is None
                    ):
                        raise ProducerFenceError(
                            f"producer generation {requested_generation} is retired; "
                            f"current generation is {managed['current_generation']}",
                            reason="stale_generation",
                            repo=canonical_repo,
                            source=scope_source,
                            required_label=required_label,
                            requested_producer=requested_producer,
                            active_producer=managed["active_producer"],
                            requested_generation=requested_generation,
                            current_generation=managed["current_generation"],
                            producer_request_id=request_id,
                        )
                    if requested_producer != generation["producer_id"]:
                        raise ProducerFenceError(
                            "producer_id does not match the selected producer "
                            "for this generation",
                            reason="wrong_producer",
                            repo=canonical_repo,
                            source=scope_source,
                            required_label=required_label,
                            requested_producer=requested_producer,
                            active_producer=generation["producer_id"],
                            requested_generation=requested_generation,
                            current_generation=managed["current_generation"],
                            producer_request_id=request_id,
                        )
                    capability_hash = self._capability_hash(
                        str(fence["capability"])
                    )
                    if not hmac.compare_digest(
                        capability_hash, str(generation["capability_hash"])
                    ):
                        raise ProducerFenceError(
                            "producer capability is invalid for the requested generation",
                            reason="invalid_capability",
                            repo=canonical_repo,
                            source=scope_source,
                            required_label=required_label,
                            requested_producer=requested_producer,
                            active_producer=generation["producer_id"],
                            requested_generation=requested_generation,
                            current_generation=managed["current_generation"],
                            producer_request_id=request_id,
                        )
                    if request is not None:
                        if request["request_hash"] not in compatible_request_hashes:
                            raise ProducerFenceError(
                                "producer_request_id was already accepted with "
                                "different canonical create fields",
                                reason="request_mismatch",
                                repo=canonical_repo,
                                source=scope_source,
                                requested_producer=requested_producer,
                                requested_generation=requested_generation,
                                producer_request_id=request_id,
                            )
                        if request["producer_id"] != requested_producer:
                            raise ProducerFenceError(
                                "accepted producer request has inconsistent "
                                "producer metadata",
                                reason="inconsistent_request_ledger",
                                repo=canonical_repo,
                                source=scope_source,
                                requested_producer=requested_producer,
                                requested_generation=requested_generation,
                                producer_request_id=request_id,
                            )
                        row = conn.execute(
                            f"SELECT {_TASK_SELECT} FROM tasks WHERE id = ?",
                            (request["task_id"],),
                        ).fetchone()
                        if row is None:
                            raise ProducerFenceError(
                                "accepted producer request references a missing task",
                                reason="inconsistent_request_ledger",
                                repo=canonical_repo,
                                source=scope_source,
                                requested_generation=requested_generation,
                                producer_request_id=request_id,
                            )
                        accepted = Task._from_row(row)
                        request_already_recorded = True
                    if accepted is None:
                        if required_label is not None and required_label not in set(
                            labels or ()
                        ):
                            raise ProducerFenceError(
                                "managed producer scope requires its configured label",
                                reason="required_label_missing",
                                repo=canonical_repo,
                                source=scope_source,
                                required_label=required_label,
                                requested_producer=requested_producer,
                                active_producer=managed["active_producer"],
                                requested_generation=requested_generation,
                                current_generation=managed["current_generation"],
                                producer_request_id=request_id,
                            )
                        if requested_producer != managed["active_producer"]:
                            raise ProducerFenceError(
                                "producer_id is metadata and does not identify the "
                                "selected producer for this generation",
                                reason="wrong_producer",
                                repo=canonical_repo,
                                source=scope_source,
                                required_label=required_label,
                                requested_producer=requested_producer,
                                active_producer=managed["active_producer"],
                                requested_generation=requested_generation,
                                current_generation=managed["current_generation"],
                                producer_request_id=request_id,
                            )
                        if not hmac.compare_digest(
                            capability_hash, str(managed["capability_hash"])
                        ):
                            raise ProducerFenceError(
                                "producer capability is invalid for the current generation",
                                reason="invalid_capability",
                                repo=canonical_repo,
                                source=scope_source,
                                required_label=required_label,
                                requested_producer=requested_producer,
                                active_producer=managed["active_producer"],
                                requested_generation=requested_generation,
                                current_generation=managed["current_generation"],
                                producer_request_id=request_id,
                            )
                if accepted is None and dedup_key is not None:
                    existing = conn.execute(
                        f"SELECT {_TASK_SELECT} FROM tasks WHERE repo = ? "
                        "AND dedup_key = ? AND status IN (?,?,?,?,?)",
                        (
                            canonical_repo,
                            dedup_key,
                            Status.PROPOSED,
                            Status.QUEUED,
                            Status.CLAIMED,
                            Status.STARTED,
                            Status.SUSPENDED,
                        ),
                    ).fetchone()
                    if existing is not None:
                        accepted = Task._from_row(existing)
                    else:
                        target = (
                            self._reviewer_target(dedup_key)
                            if source == "recipe" and origin_ref == "reviewer"
                            else None
                        )
                        if target is not None:
                            rows = conn.execute(
                                f"SELECT {_TASK_SELECT} FROM tasks WHERE repo = ? "
                                "AND source = ? AND origin_ref = ? "
                                "AND status IN (?,?,?,?,?) "
                                "AND dedup_key LIKE 'recipe:reviewer:%'",
                                (
                                    canonical_repo,
                                    "recipe",
                                    "reviewer",
                                    Status.PROPOSED,
                                    Status.QUEUED,
                                    Status.CLAIMED,
                                    Status.STARTED,
                                    Status.SUSPENDED,
                                ),
                            ).fetchall()
                            legacy = next(
                                (
                                    row
                                    for row in rows
                                    if self._reviewer_target(row["dedup_key"]) == target
                                ),
                                None,
                            )
                            if legacy is not None:
                                accepted = Task._from_row(legacy)
                if (
                    fence is not None
                    and accepted is not None
                    and not request_already_recorded
                ):
                    raise ProducerFenceError(
                        "managed create dedup collided with a task that is not "
                        "this exact accepted producer request",
                        reason="unfenced_dedup_conflict",
                        repo=canonical_repo,
                        source=str(fence["scope"]["source"]),
                        requested_producer=str(fence["producer_id"]),
                        requested_generation=int(fence["generation"]),
                        producer_request_id=str(fence["request_id"]),
                        diagnostics={
                            "conflicting_task_id": accepted.id,
                            "conflicting_task_status": accepted.status,
                        },
                    )
                if accepted is None:
                    if supersede_exclusive_key:
                        superseded = conn.execute(
                            "SELECT id, status FROM tasks "
                            "WHERE repo = ? AND exclusive_key = ? "
                            "AND status IN (?, ?)",
                            (
                                canonical_repo,
                                exclusive_key,
                                Status.PROPOSED,
                                Status.QUEUED,
                            ),
                        ).fetchall()
                        for prior in superseded:
                            conn.execute(
                                "UPDATE tasks SET status = ?, owner = NULL, "
                                "lease_expires_at = NULL, activity = NULL, "
                                "activity_updated_at = ?, updated_at = ? "
                                "WHERE id = ?",
                                (
                                    Status.ABANDONED,
                                    ts,
                                    ts,
                                    prior["id"],
                                ),
                            )
                            self._audit(
                                conn,
                                prior["id"],
                                ts=ts,
                                from_status=prior["status"],
                                to_status=Status.ABANDONED,
                                note=f"superseded by exclusive task {task_id}",
                            )
                    conn.execute(
                        "INSERT INTO tasks (id, title, prompt, status, repo, requires, excludes,"
                        " affinity, labels, payload_ref, payload_inline, target_machine,"
                        " target_worktree, target_repo,"
                        " source, origin_ref, evaluator_ref, exclusive_key, dedup_key,"
                        " goal, done_criteria,"
                        " producer_fence, producer_request_hash,"
                        " not_before, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            task_id,
                            title,
                            prompt,
                            status,
                            canonical_repo,
                            json.dumps(normalized_requires),
                            json.dumps(normalized_excludes),
                            json.dumps(dict(affinity or {})),
                            json.dumps(list(labels or [])),
                            payload_ref,
                            payload_inline,
                            target_machine,
                            target_worktree,
                            target_repo,
                            source,
                            origin_ref,
                            evaluator_ref,
                            exclusive_key,
                            dedup_key,
                            goal,
                            done_criteria,
                            fence_json,
                            request_hash,
                            not_before,
                            ts,
                            ts,
                        ),
                    )
                    self._audit(
                        conn,
                        task_id,
                        ts=ts,
                        from_status=None,
                        to_status=status,
                        note="create",
                    )
                    if claim_as and status == Status.QUEUED:
                        lease = self.lease_seconds
                        conn.execute(
                            "UPDATE tasks SET status = ?, owner = ?, claimed_at = ?, "
                            "updated_at = ?, lease_expires_at = ?, last_seen_at = ?, "
                            "generation = generation + 1, attempts = 1 WHERE id = ?",
                            (
                                Status.CLAIMED,
                                claim_as,
                                ts,
                                ts,
                                ts + lease,
                                ts,
                                task_id,
                            ),
                        )
                        self._audit(
                            conn,
                            task_id,
                            ts=ts,
                            from_status=Status.QUEUED,
                            to_status=Status.CLAIMED,
                            worker=claim_as,
                            note="create-claim",
                        )
                    row = conn.execute(
                        f"SELECT {_TASK_SELECT} FROM tasks WHERE id = ?", (task_id,)
                    ).fetchone()
                    assert row is not None
                    accepted = Task._from_row(row)
                if fence is not None and not request_already_recorded:
                    scope = fence["scope"]
                    assert isinstance(scope, dict)
                    conn.execute(
                        "INSERT INTO producer_create_requests "
                        "(repo, source, generation, request_id, request_hash, "
                        "producer_id, task_id, accepted_at) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            canonical_repo,
                            scope["source"],
                            fence["generation"],
                            fence["request_id"],
                            request_hash,
                            fence["producer_id"],
                            accepted.id,
                            ts,
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        assert accepted is not None
        if accepted.id != task_id:
            outcome = CreationOutcome(
                task=accepted,
                disposition=(
                    "replayed" if request_already_recorded else "deduplicated"
                ),
                event_type=None,
            )
            return outcome if _with_outcome else accepted
        if spill_content is not None:
            try:
                self._spill_committed_payload(task_id, spill_content)
            except (OSError, sqlite3.Error) as exc:
                log.warning(
                    "payload spill compaction failed for committed task %s: %s",
                    task_id,
                    exc,
                )
        task = self.get(task_id)
        assert task is not None
        outcome = CreationOutcome(
            task=task,
            disposition="created",
            event_type=(
                "task.proposed" if status == Status.PROPOSED else "task.created"
            ),
        )
        return outcome if _with_outcome else task

    def create_outcome(self, title: str, **kwargs: object) -> CreationOutcome:
        """Create a task and report whether a lifecycle row was inserted."""
        kwargs["_with_outcome"] = True
        result = self.create(title, **kwargs)  # type: ignore[arg-type]
        assert isinstance(result, CreationOutcome)
        return result

    def propose(self, title: str, **kwargs: object) -> Task:
        """Create a task in the un-claimable ``proposed`` state."""
        kwargs["status"] = Status.PROPOSED
        result = self.create(title, **kwargs)  # type: ignore[arg-type]
        assert isinstance(result, Task)
        return result

    def propose_outcome(self, title: str, **kwargs: object) -> CreationOutcome:
        """Propose a task and report whether a lifecycle row was inserted."""
        kwargs["status"] = Status.PROPOSED
        return self.create_outcome(title, **kwargs)

    def approve(self, task_id: str, *, now: float | None = None) -> Task:
        """Move a ``proposed`` task to ``queued`` (makes it claimable)."""
        return self._transition(
            task_id, allowed={Status.PROPOSED}, to=Status.QUEUED, now=now, note="approve"
        )

    # -- consumer / lease ----------------------------------------------------

    def claim_one(
        self,
        worker_id: str,
        capabilities: Iterable[str] = (),
        *,
        repo: str | None = None,
        machine: str | None = None,
        worktree: str | None = None,
        task_id: str | None = None,
        now: float | None = None,
        lease_seconds: int | None = None,
        evaluation: bool = False,
        _with_outcome: bool = False,
    ) -> Task | None | ClaimOutcome:
        """Atomically lease the best eligible ``queued`` task, or ``None``.

        Eligible = ``status='queued'``, ``not_before <= now``, in the claimer's
        ``repo`` **lane** (when given -- a worker only claims its own repo's
        tasks), every token in the task's ``requires`` present in
        ``capabilities``, and — the **targeting gate** — the task's
        ``target_machine`` / ``target_worktree`` are unset or match the claiming
        agent's ``machine`` / ``worktree``. So an agent only claims work in its
        lane that is unassigned *or* assigned to it. A claimer that leaves
        ``machine`` / ``worktree`` unset can therefore only take *untargeted*
        tasks. The winning row is flipped to ``claimed`` under a write lock, so
        concurrent callers never double-claim.

        If ``task_id`` is given, only that task is considered (a spawned worker
        deterministically claiming *its* task) — still subject to the same gates,
        including the ``repo`` lane. Tasks carrying a managed required label are
        additionally claimable only when their persisted fence, generation, and
        accepted-request ledger row agree.

        ``worker_id`` is stamped as the task ``owner``; in a multi-machine system it is the
        canonical ``machine/worktree`` composite (see :func:`worker_id_for`).
        """
        repo = self._canonical_repo(repo)
        ts = self._now(now)
        caps = set(self._canonical_selector_tokens(capabilities))
        # The worker's FULL advertised token set for selector matching: its
        # capabilities plus its identity tokens (``machine:``/``worktree:``/
        # ``repo:``). This is what ``requires`` (affinity) and ``excludes``
        # (anti-affinity) are matched against, so a selector can target or
        # exclude by machine/worktree/repo generically -- e.g. a task with
        # ``excludes=['machine:anomalous-potato']`` is invisible to that machine.
        full_caps = set(caps)
        if machine:
            full_caps.add(f"machine:{machine}")
        if worktree:
            full_caps.add(f"worktree:{worktree}")
        if repo:
            full_caps.add(f"repo:{repo}")
        if lease_seconds is not None:
            lease = lease_seconds
        elif evaluation:
            lease = self.eval_lease_seconds  # tight evaluation-window lease
        else:
            lease = self.lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if task_id is not None:
                rows = conn.execute(
                    f"SELECT {_TASK_BULK_SELECT}, producer_request_hash FROM tasks "
                    "WHERE id = ? AND status = ? AND not_before <= ?",
                    (task_id, Status.QUEUED, ts),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_TASK_BULK_SELECT}, producer_request_hash FROM tasks "
                    "WHERE status = ? AND not_before <= ?"
                    " ORDER BY created_at ASC",
                    (Status.QUEUED, ts),
                ).fetchall()
            chosen: sqlite3.Row | None = None
            best_affinity = -1
            producer_rejections: list[dict[str, object]] = []
            for row in rows:
                if repo is not None and row["repo"] != repo:
                    continue  # lane isolation: never claim another repo's work
                rejection = self._claim_fence_rejection(conn, row)
                if rejection is not None:
                    if "_fingerprint" not in rejection:
                        rejection["_fingerprint"] = hashlib.sha256(
                            json.dumps(
                                {
                                    "task_id": row["id"],
                                    "repo": row["repo"],
                                    "source": row["source"],
                                    "labels": row["labels"],
                                    "producer_fence": row["producer_fence"],
                                    "producer_request_hash": row[
                                        "producer_request_hash"
                                    ],
                                    "reason": rejection["reason"],
                                },
                                ensure_ascii=True,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest()
                        rejection["fingerprint"] = str(
                            rejection["_fingerprint"]
                        )[:16]
                    if (
                        len(producer_rejections) < _CLAIM_REJECTION_EVENT_LIMIT
                        and self._record_claim_rejection(conn, rejection, ts=ts)
                    ):
                        rejection.pop("_fingerprint")
                        producer_rejections.append(rejection)
                    continue
                requires = set(
                    self._canonical_selector_tokens(
                        json.loads(row["requires"] or "[]"), strict=False
                    )
                )
                if not requires.issubset(full_caps):
                    continue
                excludes = set(
                    self._canonical_selector_tokens(
                        json.loads(row["excludes"] or "[]"), strict=False
                    )
                )
                if excludes & full_caps:
                    continue  # anti-affinity: this worker is excluded (incl. a prior "not me")
                if not machine_matches(row["target_machine"], machine):
                    continue
                if row["target_worktree"] is not None and row["target_worktree"] != worktree:
                    continue
                score = self._affinity_score(json.loads(row["affinity"] or "{}"), worker_id, caps)
                if score > best_affinity:
                    best_affinity, chosen = score, row
                    if score == _MAX_AFFINITY:
                        break
            if chosen is None:
                conn.execute("COMMIT")
                outcome = ClaimOutcome(
                    task=None,
                    producer_rejections=producer_rejections,
                )
                return outcome if _with_outcome else None
            conn.execute(
                "UPDATE tasks SET status = ?, owner = ?, claimed_at = ?, updated_at = ?,"
                " lease_expires_at = ?, last_seen_at = ?, generation = generation + 1,"
                " owner_session_id = NULL, last_liveness = NULL,"
                " attempts = attempts + 1 WHERE id = ? AND status = ?",
                (Status.CLAIMED, worker_id, ts, ts, ts + lease, ts, chosen["id"], Status.QUEUED),
            )
            self._audit(
                conn,
                chosen["id"],
                ts=ts,
                from_status=Status.QUEUED,
                to_status=Status.CLAIMED,
                worker=worker_id,
                note="claim",
            )
            task = self._fetch(conn, chosen["id"])
            conn.execute("COMMIT")
        outcome = ClaimOutcome(
            task=task,
            producer_rejections=producer_rejections,
        )
        return outcome if _with_outcome else task

    def claim_outcome(self, *args: object, **kwargs: object) -> ClaimOutcome:
        """Claim a task and return newly recorded producer rejection events."""
        kwargs["_with_outcome"] = True
        result = self.claim_one(*args, **kwargs)  # type: ignore[arg-type]
        assert isinstance(result, ClaimOutcome)
        return result

    def mine(
        self, machine: str, worktree: str, *, repo: str | None = None
    ) -> dict[str, list[Task]]:
        """Return an agent's inbox: tasks ``assigned`` to it and ``owned`` by it.

        Scoped to the ``repo`` lane when given (an agent's inbox is its own
        repo's work only).

        - ``assigned``: ``queued`` tasks targeted specifically at this agent —
          ``target_worktree == worktree``, or a machine-wide assignment
          (``target_machine == machine`` with no worktree pin). Untargeted open
          tasks are *not* listed here (they belong to no one in particular).
        - ``owned``: non-terminal tasks this agent has claimed/started/suspended
          (``owner == machine/worktree``).
        """
        repo = self._canonical_repo(repo)
        owner = worker_id_for(machine, worktree)
        repo_clause = " AND repo = ?" if repo is not None else ""
        repo_param: tuple = (repo,) if repo is not None else ()
        with self._connect() as conn:
            assigned_rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks WHERE status = ? AND ("  # noqa: S608 (repo_clause is a constant; all values parameterized)
                "  target_worktree = ?"
                "  OR (target_machine = ? COLLATE NOCASE AND target_worktree IS NULL)"
                ")" + repo_clause + " ORDER BY created_at ASC",
                (Status.QUEUED, worktree, machine, *repo_param),
            ).fetchall()
            owned_rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks "
                "WHERE owner = ? AND status IN (?, ?, ?)" + repo_clause  # noqa: S608 (constant clause; parameterized)
                + " ORDER BY created_at ASC",
                (
                    owner,
                    Status.CLAIMED,
                    Status.STARTED,
                    Status.SUSPENDED,
                    *repo_param,
                ),
            ).fetchall()
        return {
            "assigned": [Task._from_row(r) for r in assigned_rows],
            "owned": [Task._from_row(r) for r in owned_rows],
        }

    @staticmethod
    def _affinity_score(affinity: dict[str, str], worker_id: str, caps: set[str]) -> int:
        """Rank a queued task for a worker: exact agent match > capability hint > any."""
        if not affinity:
            return 0
        pref_agent = affinity.get("agent")
        if pref_agent in (worker_id, "same") and pref_agent is not None:
            return _MAX_AFFINITY
        pref_cap = affinity.get("capability")
        if pref_cap is not None and pref_cap in caps:
            return 1
        return 0

    def start(
        self,
        task_id: str,
        worker_id: str,
        *,
        owner_session_id: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Move a ``claimed`` task to ``started`` (owner must match).

        Commits the worker to the work. If ``owner_session_id`` is supplied (the
        worktree's current live-session id), it is **captured on the task** so
        liveness GC can later compare the *owner's session identity* -- not mere
        worktree occupancy -- and know whether *this* owner is still alive even if
        another session reuses the worktree. Also refreshes ``last_seen_at``.
        """
        ts = self._now(now)
        extra: dict[str, object] = {"last_seen_at": ts}
        if owner_session_id is not None:
            extra["owner_session_id"] = owner_session_id
        return self._transition(
            task_id,
            allowed={Status.CLAIMED},
            to=Status.STARTED,
            worker_id=worker_id,
            now=now,
            note="start",
            stamp="started_at",
            extra=extra,
        )

    def complete(
        self,
        task_id: str,
        worker_id: str,
        *,
        result_ref: str | None = None,
        result: StructuredResult | None = None,
        expected_status: str | None = None,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Complete work and return its task snapshot."""
        return self.complete_with_outcome(
            task_id,
            worker_id,
            result_ref=result_ref,
            result=result,
            expected_status=expected_status,
            expected_owner_session_id=expected_owner_session_id,
            expected_generation=expected_generation,
            now=now,
        ).task

    def complete_with_outcome(
        self,
        task_id: str,
        worker_id: str,
        *,
        result_ref: str | None = None,
        result: StructuredResult | None = None,
        expected_status: str | None = None,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> CompletionOutcome:
        """Complete active or suspended work (owner must match).

        A suspended task may resolve while no worker process is running (for
        example, an awaited external condition became true). Allowing the
        preserved owner to complete it directly avoids manufacturing a fake
        resume/active turn solely to reach the terminal state. Callers that
        act on a previously read suspended snapshot may supply its status,
        owner-session identity, and generation as an atomic transition fence.
        """
        encoded_result = self._encode_result(result)
        allowed = {Status.STARTED, Status.SUSPENDED}
        if expected_status is not None:
            if expected_status not in allowed:
                raise TaskError(
                    f"cannot expect {expected_status!r} when completing a task"
                )
            allowed = {expected_status}
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")

            if task.status == Status.COMPLETED and encoded_result is not None:
                completing_owner = task.completed_by
                if completing_owner is None:
                    completion_workers = self._completion_event_workers(conn, task_id)
                    if not completion_workers:
                        conn.execute("COMMIT")
                        raise TaskError(
                            f"task {task_id!r} has no unambiguous completing owner"
                            " in its completion events; cannot safely record a result"
                        )
                    if len(completion_workers) != 1:
                        conn.execute("COMMIT")
                        owners = ", ".join(repr(owner) for owner in completion_workers)
                        raise TaskError(
                            f"task {task_id!r} has ambiguous completing owners"
                            f" in its completion events ({owners});"
                            " cannot safely record a result"
                        )
                    completing_owner = completion_workers[0]
                if completing_owner != worker_id:
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"task {task_id!r} was completed by {completing_owner!r},"
                        f" not {worker_id!r}"
                    )
                row = conn.execute(
                    "SELECT result, result_ref FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                current_result = row["result"]
                current_ref = row["result_ref"]
                if result_ref is not None and current_ref not in (None, result_ref):
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"task {task_id!r} already has a different result_ref"
                    )
                if current_result is not None:
                    if current_result != encoded_result:
                        conn.execute("COMMIT")
                        raise TaskError(
                            f"task {task_id!r} already has a different result"
                        )
                    if task.completed_by is None:
                        conn.execute(
                            "UPDATE tasks SET completed_by = ?"
                            " WHERE id = ? AND completed_by IS NULL",
                            (completing_owner, task_id),
                        )
                        task = self._fetch(conn, task_id)
                        assert task is not None
                    conn.execute("COMMIT")
                    return CompletionOutcome(task, None)
                conn.execute(
                    "UPDATE tasks SET result = ?, result_ref = COALESCE(result_ref, ?),"
                    " completed_by = COALESCE(completed_by, ?), updated_at = ?"
                    " WHERE id = ?",
                    (encoded_result, result_ref, completing_owner, ts, task_id),
                )
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=Status.COMPLETED,
                    to_status=Status.COMPLETED,
                    worker=worker_id,
                    note="complete retry: result recorded",
                )
                completed = self._fetch(conn, task_id)
                assert completed is not None
                conn.execute("COMMIT")
                return CompletionOutcome(completed, "task.result_recorded")

            if task.status not in allowed:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot complete a {task.status!r} task"
                    f" (allowed: {sorted(allowed)})"
                )
            if task.owner not in (None, worker_id):
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}"
                )
            if expected_generation is not None and (
                task.generation != expected_generation
                or task.owner_session_id != expected_owner_session_id
            ):
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} ownership incarnation changed")

            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, activity = NULL,"
                " activity_updated_at = ?, completed_at = ?, result_ref = ?,"
                " result = ?, completed_by = ?, owner = NULL,"
                " lease_expires_at = NULL WHERE id = ?",
                (
                    Status.COMPLETED,
                    ts,
                    ts,
                    ts,
                    result_ref,
                    encoded_result,
                    worker_id,
                    task_id,
                ),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=Status.COMPLETED,
                worker=worker_id,
                note="complete",
            )
            completed = self._fetch(conn, task_id)
            assert completed is not None
            conn.execute("COMMIT")
        return CompletionOutcome(completed, "task.completed")

    def suspend(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str,
        now: float | None = None,
    ) -> Task:
        """Park a ``started`` task as dormant while preserving its owner.

        Suspension is owner-gated and requires a non-empty reason, recorded in
        the audit trail. Durable task context and owner/session/generation
        identity remain intact; active lease, activity, and liveness observation
        are cleared because no worker is running while suspended.
        """
        meaningful = _clip(reason, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("suspend requires a non-empty reason")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._fetch(conn, task_id)
            if current is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if (
                current.status == Status.SUSPENDED
                and current.owner == worker_id
            ):
                self._audit(
                    conn,
                    task_id,
                    ts=self._now(now),
                    from_status=Status.SUSPENDED,
                    to_status=Status.SUSPENDED,
                    worker=worker_id,
                    note=f"suspend: {meaningful}",
                )
                result = self._fetch(conn, task_id)
                conn.execute("COMMIT")
                return result  # type: ignore[return-value]
            conn.execute("COMMIT")
        return self._transition(
            task_id,
            allowed={Status.STARTED},
            to=Status.SUSPENDED,
            worker_id=worker_id,
            now=now,
            note=f"suspend: {meaningful}",
            extra={"lease_expires_at": None, "last_liveness": None},
            reject_pending_steer=True,
        )

    def resume(
        self,
        task_id: str,
        worker_id: str,
        *,
        wake_requested: bool = False,
        wake_message: str | None = None,
        adopt_owner_session_id: str | None = None,
        reuse_session: bool = False,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Wake an owned ``suspended`` task back to ``started``.

        The same owner, owner-session identity, worktree identity, generation,
        progress, and card are retained by default. A handoff successor may
        atomically adopt the task into its current session; that advances the
        generation so wakes and liveness observations from the prior
        incarnation become stale.
        """
        ts = self._now(now)
        extra: dict[str, object] = {
            "lease_expires_at": ts + self.lease_seconds,
            "last_seen_at": ts,
            "last_liveness": None,
            "awaiting_steer": 0,
            "resume_requested": 0,
        }
        if adopt_owner_session_id is not None:
            extra["owner_session_id"] = adopt_owner_session_id
        return self._transition(
            task_id,
            allowed={Status.SUSPENDED},
            to=Status.STARTED,
            worker_id=worker_id,
            now=ts,
            note="resume",
            extra=extra,
            bump_generation=adopt_owner_session_id is not None,
            expected_owner_session_id=expected_owner_session_id,
            expected_generation=expected_generation,
            reembody_headless_on_wake=not reuse_session,
            wake_requested=wake_requested,
            wake_message=wake_message,
        )

    def release_suspended(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Release a suspended task to ``queued`` for a replacement worker.

        Ownership and owner-session identity are cleared, and any active spawn
        reservation for the former embodiment receives a durable release
        request in the same transaction. The supervisor settles it only after
        liveness-safe teardown.
        """
        note = _clip(reason, PROGRESS_SUMMARY_MAX) or "release suspended task"
        return self._transition(
            task_id,
            allowed={Status.SUSPENDED},
            to=Status.QUEUED,
            worker_id=worker_id,
            now=now,
            note=note,
            extra={
                "owner": None,
                "owner_session_id": None,
                "lease_expires_at": None,
                "claimed_at": None,
                "last_liveness": None,
                "resume_requested": 0,
            },
            release_spawn=True,
            release_spawn_detail="task released from suspension",
        )

    def yield_task(
        self,
        task_id: str,
        worker_id: str,
        *,
        note: str | None = None,
        exclude: str | None = None,
        release_spawn: bool = True,
        now: float | None = None,
    ) -> Task:
        """Return an owned task to ``queued`` with updates.

        The recoverable-snag path (e.g. a merge conflict): the worker relinquishes
        the lease so the next scheduler cycle re-surfaces the task.

        Suspended tasks use :meth:`release_suspended`, which also releases the
        former embodiment's spawn reservation for replacement.

        ``exclude`` is an optional **"not me" anti-affinity token** appended to the
        task's ``excludes`` on the way back to the queue, so the *same* candidate
        isn't re-offered the task (a self-declining worker adds e.g.
        ``worktree:<self>`` -- the narrowest scope -- or a wider ``machine:<m>`` /
        ``agent:<def>`` when it knows the exclusion generalizes). Because excludes
        only ever grow, the candidate set shrinks monotonically: the task either
        finds a taker or becomes unclaimable (surfaced for the operator).

        ``release_spawn`` requests release of the current embodiment; it never
        settles the reservation inline. The supervisor confirms or performs
        teardown before making a replacement spawn eligible.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._fetch(conn, task_id)
            if (
                current is not None
                and current.status == Status.SUSPENDED
                and current.awaiting_steer
                and current.owner == worker_id
            ):
                self._audit(
                    conn,
                    task_id,
                    ts=self._now(now),
                    from_status=Status.SUSPENDED,
                    to_status=Status.SUSPENDED,
                    worker=worker_id,
                    note=note or "yield after blocking card: already suspended",
                )
                result = self._fetch(conn, task_id)
                conn.execute("COMMIT")
                return result  # type: ignore[return-value]
            conn.execute("COMMIT")
        extra: dict[str, object] = {
            "owner": None,
            "owner_session_id": None,
            "lease_expires_at": None,
            "claimed_at": None,
            "last_liveness": None,
        }
        if exclude:
            current = self.get(task_id)
            existing = list(current.excludes) if current is not None else []
            if exclude not in existing:
                existing.append(exclude)
            extra["excludes"] = json.dumps(existing)
        return self._transition(
            task_id,
            allowed=Status.HELD,
            to=Status.QUEUED,
            worker_id=worker_id,
            now=now,
            note=note or "yield",
            extra=extra,
            release_spawn=release_spawn,
            release_spawn_detail="task yielded",
        )

    def abandon(
        self,
        task_id: str,
        *,
        worker_id: str | None = None,
        permitted: bool = False,
        reason: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Move a task to terminal ``abandoned`` -- requires ``permitted=True``.

        Abandonment is permission-gated (human/policy), never a unilateral agent
        action; callers pass ``permitted=True`` once that gate is satisfied.
        """
        if not permitted:
            raise TaskError("abandon requires permission (permitted=True)")
        return self._transition(
            task_id,
            allowed=Status.ABANDONABLE,
            to=Status.ABANDONED,
            worker_id=worker_id,
            require_owner=False,
            now=now,
            note=reason or "abandon",
            extra={"owner": None, "lease_expires_at": None},
        )

    def heartbeat(self, task_id: str, worker_id: str, *, now: float | None = None) -> Task:
        """Extend the lease on a held task the worker still owns."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot heartbeat a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ?, updated_at = ? WHERE id = ?",
                (ts + self.lease_seconds, ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def bind_owner_session(
        self,
        task_id: str,
        worker_id: str,
        owner_session_id: str,
        *,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Bind a held headless task to its exact bridge session identity."""
        if not owner_session_id:
            raise TaskError("owner session id must be non-empty")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot bind owner session on a {task.status!r} task"
                )
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}"
                )
            if expected_generation is not None and task.generation != expected_generation:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} ownership incarnation changed")
            if task.owner_session_id not in {None, owner_session_id}:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} already bound to another owner session"
                )
            conn.execute(
                "UPDATE tasks SET owner_session_id=?, updated_at=? WHERE id=?",
                (owner_session_id, ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=worker_id,
                note=f"owner session bound ({owner_session_id})",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def set_activity(
        self,
        task_id: str,
        activity: str | None,
        *,
        reservation_key: str,
        now: float | None = None,
    ) -> Task:
        """Publish activity fenced to this task's active spawn reservation."""
        if activity not in {None, "ACTIVE", "IDLE", "STALLED"}:
            raise TaskError(
                f"invalid task activity {activity!r} "
                "(allowed: ACTIVE, IDLE, STALLED, or null)"
            )
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if (
                task.status == Status.SUSPENDED
                and activity not in {None, "IDLE"}
            ):
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot set active activity on suspended task {task_id!r}"
                )
            reservation = conn.execute(
                "SELECT task_id, state FROM spawn_reservations WHERE key = ?",
                (reservation_key,),
            ).fetchone()
            if (
                reservation is None
                or reservation["task_id"] != task_id
                or reservation["state"] != SpawnState.SPAWNED
            ):
                conn.execute("COMMIT")
                raise TaskError(
                    f"activity update requires task {task_id!r}'s active spawned "
                    f"reservation, got {reservation_key!r}"
                )
            conn.execute(
                "UPDATE tasks SET activity = ?, activity_updated_at = ? WHERE id = ?",
                (activity, ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def record_progress(
        self,
        task_id: str,
        worker_id: str,
        *,
        phase: str,
        summary: str,
        blocker: str | None = None,
        pr: str | None = None,
        detail: str | None = None,
        extend_lease: bool = True,
        now: float | None = None,
    ) -> Task:
        """Record a bounded progress beat on a held task the worker owns.

        Stores a **latest-only** structured snapshot (phase/summary/blocker/pr/ts)
        on the task and appends a bounded row to the audit trail -- so a reader
        sees "how far toward the goal" at a glance, never a transcript. Doubles as
        a heartbeat (refreshes the lease) since a worker reporting progress is
        alive. The summary is hard-capped (:data:`PROGRESS_SUMMARY_MAX`) so the
        beat can never balloon into a chat log.

        In addition to the latest-only beat, every call **appends** a row to the
        append-only ``task_progress`` log (the *resumable-goal* feature), so a
        re-embodied worker resumes from the accumulated progress rather than
        restarting the goal. ``detail`` is an optional longer note for the log
        row; when omitted it falls back to the beat's blocker/pr context. Read
        the accumulated log via :meth:`progress_log`.
        """
        ts = self._now(now)
        snapshot = _progress_snapshot(phase, summary, blocker=blocker, pr=pr, ts=ts)
        payload = json.dumps(snapshot, separators=(",", ":"))
        # The log row's detail: an explicit ``detail`` wins; otherwise carry the
        # beat's blocker/pr context so the durable log is at least as rich as the
        # latest-only beat it accumulates.
        log_detail = _clip(detail, PROGRESS_SUMMARY_MAX)
        if log_detail is None:
            parts = []
            if snapshot.get("blocker"):
                parts.append(f"blocker: {snapshot['blocker']}")
            if snapshot.get("pr"):
                parts.append(f"pr: {snapshot['pr']}")
            log_detail = "; ".join(parts) or None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot record progress on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}"
                )
            if extend_lease:
                conn.execute(
                    "UPDATE tasks SET latest_progress = ?, lease_expires_at = ?,"
                    " last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (payload, ts + self.lease_seconds, ts, ts, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET latest_progress = ?, last_seen_at = ?,"
                    " updated_at = ? WHERE id = ?",
                    (payload, ts, ts, task_id),
                )
            conn.execute(
                "INSERT INTO task_progress (task_id, ts, phase, summary, detail, worker) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    ts,
                    snapshot.get("phase") or None,
                    snapshot["summary"],
                    log_detail,
                    worker_id,
                ),
            )
            phase_tag = f"[{snapshot['phase']}] " if snapshot.get("phase") else ""
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=worker_id,
                note=f"progress: {phase_tag}{snapshot['summary']}",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    # -- steering: card + steer inbox ----------------------------------------

    def set_card(
        self,
        task_id: str,
        worker_id: str,
        *,
        card: dict,
        now: float | None = None,
    ) -> Task:
        """Attach a **card** to a held task the worker owns, describing what it
        needs from the operator.

        Stores the latest-only ``card`` object (title/status/link/body/
        request_input). When the card carries a non-empty ``request_input`` form
        the task is atomically marked **awaiting_steer** and suspended -- blocked
        work is dormant, so its worker process can be stopped while the durable
        task/card/owner remain. Posting a card without a ``request_input`` (a
        pure status/notification card) leaves the held state unchanged.
        """
        ts = self._now(now)
        card = {**card, "ts": ts}
        payload = json.dumps(card, separators=(",", ":"))
        awaiting = 1 if card.get("request_input") else 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot set a card on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}"
                )
            to_status = Status.SUSPENDED if awaiting else task.status
            lease_expires_at = None if awaiting else ts + self.lease_seconds
            conn.execute(
                "UPDATE tasks SET card = ?, awaiting_steer = ?, status = ?,"
                " lease_expires_at = ?, last_liveness = NULL,"
                " activity = NULL, activity_updated_at = ?,"
                " last_seen_at = ?, updated_at = ? WHERE id = ?",
                (
                    payload,
                    awaiting,
                    to_status,
                    lease_expires_at,
                    ts,
                    ts,
                    ts,
                    task_id,
                ),
            )
            note = "card posted (awaiting steer)" if awaiting else "card posted"
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=to_status,
                worker=worker_id,
                note=note,
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def submit_steer(
        self,
        task_id: str,
        *,
        fields: dict,
        sender: str | None = None,
        wake_requested: bool = False,
        wake_message: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Submit an operator's answer (a **steer**) to a task's card.

        Appends the answer to the append-only ``task_steer`` inbox and clears
        ``awaiting_steer`` (the operator has responded; the task is no longer
        blocked on a human). Deliberately **not** owner-gated -- the operator (or
        a surface acting for them), not the worker, submits a steer. Allowed on
        any non-terminal task. A suspended interactive task is atomically
        resumed to ``started`` while preserving its owner. A suspended
        headless task has no interactive inbox, so it is instead released to
        ``queued`` and its reservation settled for safe re-embodiment. When
        direct wake delivery is possible, the same transaction inserts a
        durable wake outbox row; bridge delivery happens later in the
        coordinator loop. The worker consumes the answer with
        :meth:`take_steer` when it resumes. A steer is **never** a verdict -- it
        carries operator *guidance*, and the coordinator has no path to set an
        Approve/Reject outcome from it.
        """
        ts = self._now(now)
        payload = json.dumps(fields, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status in Status.TERMINAL:
                conn.execute("COMMIT")
                raise TaskError(f"cannot steer a {task.status!r} task")
            conn.execute(
                "INSERT INTO task_steer (task_id, ts, fields, sender) VALUES (?, ?, ?, ?)",
                (task_id, ts, payload, sender),
            )
            resumed = task.status == Status.SUSPENDED
            cold_headless = bool(
                resumed and self._has_headless_reservation(conn, task_id)
            )
            if cold_headless:
                conn.execute(
                    "UPDATE tasks SET awaiting_steer = 0, resume_requested = 1,"
                    " updated_at = ?"
                    " WHERE id = ? AND status = ?",
                    (ts, task_id, Status.SUSPENDED),
                )
            elif resumed:
                conn.execute(
                    "UPDATE tasks SET status = ?, awaiting_steer = 0,"
                    " resume_requested = 0, lease_expires_at = ?,"
                    " last_seen_at = ?, last_liveness = NULL,"
                    " updated_at = ? WHERE id = ? AND status = ?",
                    (
                        Status.STARTED,
                        ts + self.lease_seconds,
                        ts,
                        ts,
                        task_id,
                        Status.SUSPENDED,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET awaiting_steer = 0, updated_at = ? WHERE id = ?",
                    (ts, task_id),
                )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=(
                    Status.SUSPENDED
                    if cold_headless
                    else Status.STARTED if resumed else task.status
                ),
                worker=sender,
                note=(
                    f"steer submitted{f' by {sender}' if sender else ''}"
                    f"{'; resume requested for cold body' if cold_headless else ''}"
                    f"{'; resumed' if resumed and not cold_headless else ''}"
                ),
            )
            result = self._fetch(conn, task_id)
            if (
                wake_requested
                and not cold_headless
                and result is not None
                and result.owner
                and result.owner_session_id is not None
            ):
                self._enqueue_wake(
                    conn,
                    result,
                    message=wake_message,
                    ts=ts,
                )
                result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def take_steer(
        self,
        task_id: str,
        worker_id: str,
        *,
        all_pending: bool = False,
        now: float | None = None,
    ) -> dict | list[dict] | None:
        """Consume pending steering for a held task the worker owns.

        By default returns and marks taken the oldest steer payload
        ``{id, ts, fields, sender}``. With ``all_pending=True``, returns every
        untaken steer oldest-first and marks the whole batch taken atomically.
        The all-pending form is the wake-side read: wakes are edge-triggered and
        may coalesce, so a resumed or replacement worker drains every answer
        before continuing. Owner-gated and lease-refreshing.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot take a steer on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}"
                )
            rows = conn.execute(
                "SELECT id, ts, fields, sender FROM task_steer "
                "WHERE task_id = ? AND taken = 0 ORDER BY id ASC"
                + ("" if all_pending else " LIMIT 1"),
                (task_id,),
            ).fetchall()
            if not rows:
                conn.execute(
                    "UPDATE tasks SET lease_expires_at = ?, last_seen_at = ?,"
                    " updated_at = ? WHERE id = ?",
                    (ts + self.lease_seconds, ts, ts, task_id),
                )
                conn.execute("COMMIT")
                return [] if all_pending else None
            conn.executemany(
                "UPDATE task_steer SET taken = 1, taken_at = ? WHERE id = ?",
                [(ts, row["id"]) for row in rows],
            )
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ?, last_seen_at = ?,"
                " updated_at = ? WHERE id = ?",
                (ts + self.lease_seconds, ts, ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=worker_id,
                note=(
                    f"{len(rows)} steers taken"
                    if all_pending
                    else "steer taken"
                ),
            )
            conn.execute("COMMIT")
        result = [
            {
                "id": row["id"],
                "ts": row["ts"],
                "fields": json.loads(row["fields"] or "{}"),
                "sender": row["sender"],
            }
            for row in rows
        ]
        return result if all_pending else result[0]

    def steer_log(self, task_id: str) -> list[dict]:
        """The full steer inbox for a task (oldest first), for inspection."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, fields, sender, taken, taken_at FROM task_steer "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "fields": json.loads(r["fields"] or "{}"),
                "sender": r["sender"],
                "taken": bool(r["taken"]),
                "taken_at": r["taken_at"],
            }
            for r in rows
        ]

    def list_wakes(self, task_id: str) -> list[WakeOperation]:
        """List a task's durable wake operations, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM wake_outbox WHERE task_id = ?"
                " ORDER BY wake_seq ASC",
                (task_id,),
            ).fetchall()
        return [WakeOperation._from_row(row) for row in rows]

    @staticmethod
    def _wake_is_current(task: Task | None, wake: WakeOperation) -> bool:
        return bool(
            task is not None
            and task.status == Status.STARTED
            and task.owner == wake.owner
            and wake.owner_session_id is not None
            and task.owner_session_id == wake.owner_session_id
            and task.generation == wake.generation
            and task.wake_operation_id == wake.id
        )

    def recover_inflight_wakes(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    ) -> int:
        """Return only expired ``delivering`` rows to pending.

        The downstream bridge receives the stable outbox id as its idempotency
        key, so retrying an ambiguous pre-restart delivery cannot enqueue a
        duplicate prompt. Rows created before delivery leases were introduced
        use ``updated_at + lease_seconds`` as their conservative expiry.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM wake_outbox WHERE status = 'delivering'"
                " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                (max(0.01, lease_seconds), ts),
            ).fetchall()
            for row in rows:
                wake = WakeOperation._from_row(row)
                conn.execute(
                    "UPDATE wake_outbox SET status = 'pending',"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " not_before = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'delivering'"
                    " AND COALESCE(delivery_expires_at, updated_at + ?) <= ?",
                    (ts, ts, wake.id, max(0.01, lease_seconds), ts),
                )
                conn.execute(
                    "UPDATE tasks SET wake_status = 'pending'"
                    " WHERE id = ? AND wake_operation_id = ?",
                    (wake.task_id, wake.id),
                )
                task = self._fetch(conn, wake.task_id)
                if task is not None:
                    self._audit(
                        conn,
                        wake.task_id,
                        ts=ts,
                        from_status=task.status,
                        to_status=task.status,
                        worker=wake.owner,
                        note=f"wake recovered ({wake.id})",
                    )
            conn.execute("COMMIT")
        return len(rows)

    def claim_due_wake(
        self,
        *,
        now: float | None = None,
        lease_seconds: float = DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    ) -> WakeOperation | None:
        """Atomically claim the oldest due current wake operation.

        Operations fenced out by task status/owner/session/generation or by a
        newer wake are marked ``stale`` instead of delivered.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            while True:
                row = conn.execute(
                    "SELECT * FROM wake_outbox"
                    " WHERE status = 'pending' AND not_before <= ?"
                    " ORDER BY created_at ASC, id ASC LIMIT 1",
                    (ts,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                wake = WakeOperation._from_row(row)
                task = self._fetch(conn, wake.task_id)
                if not self._wake_is_current(task, wake):
                    conn.execute(
                        "UPDATE wake_outbox SET status = 'stale', updated_at = ?,"
                        " last_error = 'task fence advanced' WHERE id = ?"
                        " AND status = 'pending'",
                        (ts, wake.id),
                    )
                    conn.execute(
                        "UPDATE tasks SET wake_status = 'stale'"
                        " WHERE id = ? AND wake_operation_id = ?",
                        (wake.task_id, wake.id),
                    )
                    if task is not None:
                        self._audit(
                            conn,
                            wake.task_id,
                            ts=ts,
                            from_status=task.status,
                            to_status=task.status,
                            worker=wake.owner,
                            note=f"wake stale ({wake.id})",
                        )
                    continue
                token = uuid.uuid4().hex
                cur = conn.execute(
                    "UPDATE wake_outbox SET status = 'delivering',"
                    " attempts = attempts + 1, delivery_token = ?,"
                    " delivery_expires_at = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'pending'",
                    (token, ts + max(0.01, lease_seconds), ts, wake.id),
                )
                if not cur.rowcount:
                    continue
                conn.execute(
                    "UPDATE tasks SET wake_status = 'delivering'"
                    " WHERE id = ? AND wake_operation_id = ?",
                    (wake.task_id, wake.id),
                )
                claimed = conn.execute(
                    "SELECT * FROM wake_outbox WHERE id = ?", (wake.id,)
                ).fetchone()
                conn.execute("COMMIT")
                return WakeOperation._from_row(claimed)

    def finish_wake(
        self,
        operation_id: str,
        delivery_token: str,
        *,
        delivered: bool,
        error: str | None = None,
        max_attempts: int = 8,
        retry_base: float = 1.0,
        now: float | None = None,
    ) -> WakeOperation:
        """Record delivery or retry a claimed wake with exponential backoff."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM wake_outbox WHERE id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such wake operation {operation_id!r}")
            wake = WakeOperation._from_row(row)
            if wake.status != "delivering" or wake.delivery_token != delivery_token:
                conn.execute("COMMIT")
                raise TaskError(f"wake operation {operation_id!r} is not held by this delivery")
            task = self._fetch(conn, wake.task_id)
            if not self._wake_is_current(task, wake):
                status = "stale"
                note = f"wake stale ({wake.id})"
                params = (status, ts, "task fence advanced", wake.id)
                conn.execute(
                    "UPDATE wake_outbox SET status = ?, updated_at = ?,"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = ? WHERE id = ?",
                    params,
                )
            elif delivered:
                status = "delivered"
                note = f"wake delivered ({wake.id})"
                conn.execute(
                    "UPDATE wake_outbox SET status = 'delivered', updated_at = ?,"
                    " delivered_at = ?, delivery_token = NULL,"
                    " delivery_expires_at = NULL, last_error = NULL"
                    " WHERE id = ?",
                    (ts, ts, wake.id),
                )
            elif wake.attempts >= max(1, max_attempts):
                status = "failed"
                note = f"wake failed ({wake.id})"
                conn.execute(
                    "UPDATE wake_outbox SET status = 'failed', updated_at = ?,"
                    " delivery_token = NULL, delivery_expires_at = NULL,"
                    " last_error = ? WHERE id = ?",
                    (ts, error or "delivery failed", wake.id),
                )
            else:
                status = "pending"
                delay = min(
                    60.0,
                    max(0.01, retry_base)
                    * float(2 ** max(0, wake.attempts - 1)),
                )
                note = f"wake retry scheduled ({wake.id})"
                conn.execute(
                    "UPDATE wake_outbox SET status = 'pending', updated_at = ?,"
                    " not_before = ?, delivery_token = NULL,"
                    " delivery_expires_at = NULL, last_error = ?"
                    " WHERE id = ?",
                    (ts, ts + delay, error or "delivery failed", wake.id),
                )
            conn.execute(
                "UPDATE tasks SET wake_status = ?"
                " WHERE id = ? AND wake_operation_id = ?",
                (status, wake.task_id, wake.id),
            )
            if task is not None:
                self._audit(
                    conn,
                    wake.task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=wake.owner,
                    note=note,
                )
            result = conn.execute(
                "SELECT * FROM wake_outbox WHERE id = ?", (wake.id,)
            ).fetchone()
            conn.execute("COMMIT")
        return WakeOperation._from_row(result)

    def wake_metrics(self, *, now: float | None = None) -> dict[str, int | float | None]:
        """Return durable outbox counts and oldest pending age."""
        ts = self._now(now)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM wake_outbox GROUP BY status"
            ).fetchall()
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM wake_outbox"
                " WHERE status IN ('pending', 'delivering')"
            ).fetchone()[0]
        counts = {
            "pending": 0,
            "delivering": 0,
            "delivered": 0,
            "failed": 0,
            "stale": 0,
        }
        counts.update({row["status"]: row["count"] for row in rows})
        return {
            **counts,
            "oldest_pending_age": (
                round(ts - oldest, 3) if oldest is not None else None
            ),
        }

    def recover_expired_leases(self, *, now: float | None = None) -> int:
        """Deprecated compatibility shim -- now runs a **liveness** GC pass.

        The recovery trigger moved from wall-clock lease expiry to worker
        liveness (see :meth:`reconcile_liveness`). This method is retained so the
        ``POST /recover`` route and any external caller keep working, but it no
        longer requeues on elapsed time: it requeues only tasks whose owner is
        **confirmed gone**. Returns the number requeued.
        """
        return self.reconcile_liveness(now=now)["requeued"]

    #: Liveness verdicts a reconcile acts on (mirror of ``tracking`` constants,
    #: duplicated here so the engine takes no import dependency on the resolver).
    LIVENESS_LIVE = "live"
    LIVENESS_GONE = "gone"
    LIVENESS_UNKNOWN = "unknown"
    #: A held task requeued this many times by GC (owner kept going gone) is
    #: retired to the terminal ``dead_letter`` state instead of churning forever.
    DEFAULT_MAX_ATTEMPTS = 5

    def reconcile_liveness(
        self,
        resolver: Callable[[str, str | None, str | None], str] | None = None,
        *,
        max_attempts: int | None = None,
        now: float | None = None,
    ) -> dict[str, int]:
        """Garbage-collect held tasks by reconciling them against **owner-session
        liveness** -- the recovery mechanism that replaces time-based lease expiry.

        For each ``claimed``/``started`` task the owner's liveness is resolved to a
        tri-state verdict (keyed on the task's captured ``owner_session_id`` -- not
        mere worktree occupancy) and acted on:

        - ``live``    -> leave it (the *same* owner still holds it, no matter how
          long -- there is **no** wall-clock expiry).
        - ``gone``    -> **fenced** requeue (owner confirmed gone). Past
          ``max_attempts`` requeues the task is retired to ``dead_letter`` instead.
        - ``unknown`` -> leave it (resolver couldn't tell, or identity not captured
          yet -- degrade safe; never requeue on ignorance).

        The last verdict is persisted to ``last_liveness`` so the buildup metric
        can classify held tasks without re-probing the bridge.

        ``resolver`` is ``(worktree, machine, owner_session_id) -> verdict``; the
        default shells :func:`tracking.liveness_verdict`. Injecting it keeps the
        engine subprocess-free and lets tests drive verdicts deterministically.

        **Fencing:** liveness is probed **outside** the write lock, then each gone
        task is requeued under a short transaction with a conditional update on
        ``(id, status, owner_session_id, generation)`` -- so if the owner
        registered, resumed, completed, or the task was re-claimed between probe
        and write, the update **no-ops** (no double-execution, no clobber).

        Returns counts: ``checked``/``live``/``gone``/``unknown``/``requeued``/
        ``dead_lettered``.
        """
        if resolver is None:
            from . import tracking

            def resolver(
                worktree: str, machine: str | None, owner_session_id: str | None
            ) -> str:
                return tracking.liveness_verdict(
                    worktree, machine=machine, owner_session_id=owner_session_id
                )

        cap = self.DEFAULT_MAX_ATTEMPTS if max_attempts is None else max_attempts
        ts = self._now(now)
        counts = {
            "checked": 0, "live": 0, "gone": 0, "unknown": 0,
            "requeued": 0, "dead_lettered": 0,
        }
        with self._connect() as conn:
            held = conn.execute(
                "SELECT id, owner, owner_session_id, generation, attempts"
                " FROM tasks WHERE status IN (?, ?)",
                (Status.CLAIMED, Status.STARTED),
            ).fetchall()
        # (task_id, verdict, owner_session_id, generation, attempts) per held task.
        probed: list[tuple[str, str, str | None, int, int]] = []
        for row in held:
            counts["checked"] += 1
            machine, _sep, worktree = (row["owner"] or "").partition("/")
            if not worktree:
                verdict = self.LIVENESS_UNKNOWN
            else:
                verdict = resolver(worktree, machine or None, row["owner_session_id"])
            counts[verdict] = counts.get(verdict, 0) + 1
            probed.append(
                (row["id"], verdict, row["owner_session_id"], row["generation"],
                 row["attempts"])
            )
        if not probed:
            return counts
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for task_id, verdict, owner_session_id, generation, attempts in probed:
                # Persist the last verdict for the buildup metric (fenced on the
                # generation so a re-claim mid-pass isn't tagged with a stale beat).
                conn.execute(
                    "UPDATE tasks SET last_liveness = ? WHERE id = ? AND generation = ?"
                    " AND status IN (?, ?)",
                    (verdict, task_id, generation, Status.CLAIMED, Status.STARTED),
                )
                if verdict != self.LIVENESS_GONE:
                    continue
                to_status = (
                    Status.DEAD_LETTER if attempts >= cap else Status.QUEUED
                )
                owner_clause = (
                    "owner_session_id = ?" if owner_session_id is not None
                    else "owner_session_id IS NULL"
                )
                params: list[object] = [to_status, ts]
                if to_status == Status.QUEUED:
                    # requeue: clear ownership + identity, bump attempts
                    set_sql = (
                        "status = ?, updated_at = ?, owner = NULL,"
                        " owner_session_id = NULL, lease_expires_at = NULL,"
                        " attempts = attempts + 1"
                    )
                else:
                    set_sql = "status = ?, updated_at = ?"
                sql = (
                    f"UPDATE tasks SET {set_sql} WHERE id = ? AND status IN (?, ?)"  # noqa: S608 (set_sql is a constant; all values parameterized)
                    f" AND generation = ? AND {owner_clause}"
                )
                params += [task_id, Status.CLAIMED, Status.STARTED, generation]
                if owner_session_id is not None:
                    params.append(owner_session_id)
                cur = conn.execute(sql, params)
                if cur.rowcount:
                    self._audit(
                        conn, task_id, ts=ts,
                        from_status=Status.STARTED, to_status=to_status,
                        note="owner-gone" if to_status == Status.QUEUED
                        else "owner-gone (dead-letter: max attempts)",
                    )
                    if to_status == Status.QUEUED:
                        counts["requeued"] += 1
                    else:
                        counts["dead_lettered"] += 1
            conn.execute("COMMIT")
        return counts

    def reap_orphaned_targets(
        self,
        live_worktrees: set[str] | None,
        *,
        machine: str,
        grace_secs: float,
        now: float | None = None,
    ) -> dict[str, int]:
        """Abandon **unowned** (proposed/queued) tasks pinned to a target worktree
        on ``machine`` that is no longer live.

        :meth:`reconcile_liveness` only recovers *owned* held tasks against their
        owner's session liveness; an **unowned** proposed/queued task has no owner,
        so nothing ever reaps it -- pinned (``--target-worktree``) to a worktree
        that was later pruned, it lingers forever. That is the context-handoff
        leak: a stored handoff whose live-cutover never completed (or a fallback
        the operator never resumed) accumulates one dead task per session. This
        closes it.

        ``live_worktrees`` is the set of worktree ids currently **live** on
        ``machine`` (e.g. ``agent-worktrees list --tracking-status active``). A
        task is reaped iff ALL hold:

        - status is ``proposed`` or ``queued`` (unowned -- no worker holds it);
        - ``target_machine`` case-insensitively equals ``machine`` (we only judge
          against a live-worktree set we actually have -- a task targeting another
          machine is that coordinator's to reap);
        - ``target_worktree`` is set and **not** in ``live_worktrees``;
        - it is older than ``grace_secs`` (a just-created handoff whose successor
          hasn't started yet is never reaped -- mirrors the liveness GC's refusal
          to act on a claim/register race).

        **Degrade safe:** ``live_worktrees is None`` (the caller's probe failed)
        reaps nothing -- never act on ignorance, exactly like the ``unknown``
        liveness verdict. **Fenced:** each abandon is conditional on ``(id,
        status, generation)``, so a task claimed/consumed between the read and the
        write no-ops (no clobber of freshly-picked-up work).

        Returns counts: ``checked`` / ``orphaned`` / ``reaped``.
        """
        counts = {"checked": 0, "orphaned": 0, "reaped": 0}
        if live_worktrees is None:
            return counts
        ts = self._now(now)
        cutoff = ts - max(0.0, grace_secs)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, target_worktree, generation, status FROM tasks"
                " WHERE status IN (?, ?)"
                "  AND target_worktree IS NOT NULL"
                "  AND target_machine IS NOT NULL"
                "  AND lower(target_machine) = lower(?)"
                "  AND created_at < ?",
                (Status.PROPOSED, Status.QUEUED, machine, cutoff),
            ).fetchall()
        victims: list[tuple[str, int, str]] = []
        for row in rows:
            counts["checked"] += 1
            if row["target_worktree"] in live_worktrees:
                continue
            counts["orphaned"] += 1
            victims.append((row["id"], row["generation"], row["status"]))
        if not victims:
            return counts
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for task_id, generation, status in victims:
                cur = conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ?, completed_at = ?,"
                    " owner = NULL, lease_expires_at = NULL"
                    " WHERE id = ? AND status = ? AND generation = ?",
                    (Status.ABANDONED, ts, ts, task_id, status, generation),
                )
                if cur.rowcount:
                    self._audit(
                        conn, task_id, ts=ts,
                        from_status=status, to_status=Status.ABANDONED,
                        note="orphaned: target worktree no longer live",
                    )
                    counts["reaped"] += 1
            conn.execute("COMMIT")
        return counts

    def backlog_health(
        self, *, repo: str | None = None, now: float | None = None
    ) -> dict[str, float | int | None]:
        """A queryable **buildup** signal: how much work is waiting to drain.

        In a healthy system tasks are short-lived; a growing, undraining backlog
        is a system-health signal that warrants attention (see the vision's
        *buildup-is-a-health-signal*). This surfaces the raw numbers -- it takes
        **no** action (escalate-or-demote is a consumer policy, not the
        engine). Reports, scoped to ``repo`` when given:

        - ``queued`` / ``proposed`` / ``held`` / ``suspended`` /
          ``dead_letter`` -- counts by phase.
        - ``oldest_queued_age`` -- seconds the oldest ``queued`` task has waited
          (``None`` when empty), the clearest "is it draining?" beat.
        - ``held_live`` / ``held_gone`` / ``held_unknown`` -- held tasks broken out
          by the **last GC liveness verdict** (a held task not yet reconciled
          counts as ``unknown``). A ``gone`` owner is requeued immediately, so the
          real backlog signal is ``held_live`` -- a live owner that has stopped
          progressing.
        - ``oldest_held_live_age`` -- seconds since the oldest **live**-owned held
          task last made progress (``last_seen_at``), i.e. the *stuck-but-alive*
          signal Q2 says buildup should surface (``None`` when none).
        """
        repo = self._canonical_repo(repo)
        ts = self._now(now)
        where_repo = " AND repo = ?" if repo is not None else ""
        args: tuple[object, ...] = (repo,) if repo is not None else ()
        with self._connect() as conn:
            def _count(status: str) -> int:
                return conn.execute(
                    f"SELECT COUNT(*) FROM tasks WHERE status = ?{where_repo}",  # noqa: S608 (constant clause; parameterized)
                    (status, *args),
                ).fetchone()[0]

            queued = _count(Status.QUEUED)
            proposed = _count(Status.PROPOSED)
            suspended = _count(Status.SUSPENDED)
            dead_letter = _count(Status.DEAD_LETTER)
            held_rows = conn.execute(
                "SELECT last_liveness, last_seen_at FROM tasks"  # noqa: S608 (constant clause; parameterized)
                f" WHERE status IN (?, ?){where_repo}",
                (Status.CLAIMED, Status.STARTED, *args),
            ).fetchall()
            oldest = conn.execute(
                f"SELECT MIN(created_at) FROM tasks WHERE status = ?{where_repo}",  # noqa: S608 (constant clause; parameterized)
                (Status.QUEUED, *args),
            ).fetchone()[0]
        held_live = held_gone = held_unknown = 0
        oldest_live_seen: float | None = None
        for row in held_rows:
            verdict = row["last_liveness"]
            if verdict == self.LIVENESS_LIVE:
                held_live += 1
                seen = row["last_seen_at"]
                if seen is not None and (oldest_live_seen is None or seen < oldest_live_seen):
                    oldest_live_seen = seen
            elif verdict == self.LIVENESS_GONE:
                held_gone += 1
            else:  # unknown or not-yet-reconciled (NULL)
                held_unknown += 1
        return {
            "queued": queued,
            "proposed": proposed,
            "held": len(held_rows),
            "suspended": suspended,
            "held_live": held_live,
            "held_gone": held_gone,
            "held_unknown": held_unknown,
            "dead_letter": dead_letter,
            "oldest_queued_age": round(ts - oldest, 3) if oldest is not None else None,
            "oldest_held_live_age": (
                round(ts - oldest_live_seen, 3) if oldest_live_seen is not None else None
            ),
        }

    def detach(self, task_id: str, *, now: float | None = None) -> Task:
        """Demote a hard worktree pin to a soft affinity (portability).

        A worktree-bound handoff becomes portable once local work is pushed: the
        ``worktree`` token moves out of ``requires`` and into ``affinity``.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            requires = [r for r in task.requires if not r.startswith("worktree:")]
            affinity = dict(task.affinity)
            if task.target_worktree:
                affinity["worktree"] = task.target_worktree
            conn.execute(
                "UPDATE tasks SET requires = ?, affinity = ?, target_worktree = NULL,"
                " updated_at = ? WHERE id = ?",
                (json.dumps(requires), json.dumps(affinity), ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    # -- generic transition --------------------------------------------------

    def _transition(
        self,
        task_id: str,
        *,
        allowed: Iterable[str],
        to: str,
        worker_id: str | None = None,
        require_owner: bool = True,
        now: float | None = None,
        note: str | None = None,
        stamp: str | None = None,
        extra: dict[str, object] | None = None,
        release_spawn: bool = False,
        release_spawn_detail: str = "task released from suspension",
        wake_requested: bool = False,
        wake_message: str | None = None,
        bump_generation: bool = False,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        reembody_headless_on_wake: bool = False,
        reject_pending_steer: bool = False,
    ) -> Task:
        ts = self._now(now)
        allowed_set = set(allowed)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in allowed_set:
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot {note or to} a {task.status!r} task (allowed: {sorted(allowed_set)})"
                )
            if require_owner and worker_id is not None and task.owner not in (None, worker_id):
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if reject_pending_steer:
                pending = conn.execute(
                    "SELECT 1 FROM task_steer"
                    " WHERE task_id = ? AND taken = 0 LIMIT 1",
                    (task_id,),
                ).fetchone()
                if pending is not None:
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"cannot suspend task {task_id!r}: pending steer;"
                        " take it and continue"
                    )
            if expected_generation is not None and (
                task.generation != expected_generation
                or task.owner_session_id != expected_owner_session_id
            ):
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} ownership incarnation changed"
                )
            if (
                reembody_headless_on_wake
                and task.owner_session_id is None
                and self._has_headless_reservation(conn, task_id)
            ):
                to = Status.SUSPENDED
                note = "resume requested for cold headless owner"
                extra = {
                    "lease_expires_at": None,
                    "last_liveness": None,
                    "awaiting_steer": 0,
                    "resume_requested": 1,
                }
                wake_requested = False
            sets = ["status = ?", "updated_at = ?"]
            params: list[object] = [to, ts]
            if bump_generation:
                sets.append("generation = generation + 1")
            # Preserve execution across claimed -> started; clear it when work
            # leaves the held lifecycle. The supervisor keeps held observations
            # fresh in the background.
            if to not in Status.HELD:
                sets.extend(["activity = NULL", "activity_updated_at = ?"])
                params.append(ts)
            if stamp is not None:
                sets.append(f"{stamp} = ?")
                params.append(ts)
            for col, val in (extra or {}).items():
                sets.append(f"{col} = ?")
                params.append(val)
            params.append(task_id)
            # Column names are internal constants; values are bound parameters.
            conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)  # noqa: S608
            if release_spawn:
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested = 1, "
                    "updated_at = ?, detail = COALESCE(detail, ?) WHERE task_id = ?"
                    " AND state IN (?, ?, ?)",
                    (
                        ts,
                        release_spawn_detail,
                        task_id,
                        SpawnState.RESERVING,
                        SpawnState.SPAWNED,
                        SpawnState.COLD,
                    ),
                )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=to,
                worker=worker_id,
                note=note,
            )
            result = self._fetch(conn, task_id)
            if wake_requested:
                self._enqueue_wake(
                    conn,
                    result,  # type: ignore[arg-type]
                    message=wake_message,
                    ts=ts,
                )
                result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    # -- read helpers --------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            return self._fetch(conn, task_id)

    def list(
        self,
        *,
        repo: str | None = None,
        status: str | Sequence[str] | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        evaluator_ref: str | None = None,
        source: str | None = None,
        origin_ref: str | None = None,
        exclusive_key: str | None = None,
        limit: int = 200,
    ) -> list[Task]:
        """List tasks, optionally filtered. Newest first.

        ``repo`` scopes to a single lane (the caller's repo by default, at the
        CLI). ``status`` accepts a single status *or* a sequence of statuses (an
        ``IN (...)`` filter), so a producer can browse several states in one
        call. :meth:`sweep` uses this to pull the whole non-abandoned corpus.
        """
        repo = self._canonical_repo(repo)
        clauses: list[str] = []
        params: list[object] = []
        if repo is not None:
            clauses.append("repo = ?")
            params.append(repo)
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                clauses.append(f"status IN ({placeholders})")
                params.extend(statuses)
        if target_machine is not None:
            clauses.append("target_machine = ? COLLATE NOCASE")
            params.append(target_machine)
        if target_repo is not None:
            clauses.append("target_repo = ?")
            params.append(target_repo)
        if evaluator_ref is not None:
            if evaluator_ref:
                clauses.append("evaluator_ref = ?")
                params.append(evaluator_ref)
            else:
                clauses.append("evaluator_ref IS NULL")
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if origin_ref is not None:
            clauses.append("origin_ref = ?")
            params.append(origin_ref)
        if exclusive_key is not None:
            clauses.append("exclusive_key = ?")
            params.append(exclusive_key)
        if label is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each("
                "CASE WHEN json_valid(tasks.labels) THEN tasks.labels ELSE '[]' END"
                ") "
                "WHERE json_each.value = ?)"
            )
            params.append(label)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            # `where` is built from literal clause strings; values are bound.
            rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks "
                f"{where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def find(self, text: str, *, repo: str | None = None, limit: int = 50) -> list[Task]:
        """Substring search over title/prompt -- one primitive in the
        agent-driven dedup flow (a quick targeted probe). Scoped to the ``repo``
        lane when given. For a full pre-create review, prefer :meth:`sweep`.
        """
        repo = self._canonical_repo(repo)
        like = f"%{text}%"
        repo_clause = " AND repo = ?" if repo is not None else ""
        repo_param: tuple = (repo,) if repo is not None else ()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks "
                "WHERE (title LIKE ? OR prompt LIKE ?)" + repo_clause  # noqa: S608 (constant clause; parameterized)
                + " ORDER BY created_at DESC LIMIT ?",
                (like, like, *repo_param, limit),
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def sweep(self, *, repo: str | None = None, limit: int = 500) -> list[Task]:
        """Return the dedup corpus: every non-abandoned task, newest first.

        Scoped to the ``repo`` lane when given (the CLI always passes the
        caller's repo -- a producer dedups against *its own* lane, since another
        repo's tasks are invisible to it). Backs the agent-driven
        *sweep + explore + verify* flow a producer runs before creating a task:
        it enumerates every ``proposed``/``queued``/``claimed``/``started``/
        ``suspended``/``completed`` task so the producer can read the
        descriptions and judge whether the work already exists -- no semantic
        index required.
        Correctness rests on each task carrying a self-contained title + prompt.
        (A future VEI adapter is a pluggable *optimization* over this same
        corpus, never a prerequisite.)
        """
        return self.list(repo=repo, status=self.SWEEP_STATES, limit=limit)

    def events(self, task_id: str) -> list[dict[str, object]]:
        """Return the append-only audit trail for a task, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, from_status, to_status, worker, note FROM task_events "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def progress_log(self, task_id: str) -> list[dict[str, object]]:
        """Return the accumulated append-only progress log for a task.

        Rows are chronological (oldest first) -- the durable, resumable record of
        every progress beat (the *resumable-goal* feature). Distinct from the
        latest-only ``latest_progress`` beat on the task row: a re-embodied worker
        reads this to continue toward the goal from recorded progress rather than
        restarting it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, phase, summary, detail, worker FROM task_progress "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- spawn reservations --------------------------------------------------

    def reserve_spawn(
        self, task_id: str, *, reserved_by: str | None = None, now: float | None = None
    ) -> tuple[SpawnReservation, bool]:
        """Atomically reserve the right to spawn an embody worker for ``task_id``.

        This is the primitive that makes "queued task -> exactly one host embody
        session" durable and idempotent. It is **distinct from the execution
        claim**: the claim is taken later by the embodied worker under its own
        worktree identity; this reservation is taken by the *spawner* (a
        ``create --spawn`` CLI, or the supervisor loop) *before* launching
        embody, so a crash / re-poll / lease-expiry between observing a
        spawn-eligible task and actually spawning it can never double-spawn.

        Semantics (all under one write lock):

        * If an **active** reservation
          (``reserving``/``spawned``/``cold``/``releasing``) already exists for
          the task, return it with ``False`` -- the task is already being
          spawned or its prior allocation is still being released; the caller
          must **not** spawn.
        * Otherwise mint a fresh reservation. ``attempt`` is ``max(prior
          attempts) + 1`` (``1`` for the first), keyed
          ``dispatch-task:<task_id>:<attempt>``, in state ``reserving``. Return
          it with ``True`` -- the caller owns this spawn.

        A prior ``failed``/``settled`` reservation therefore does not block a
        retry: the next attempt gets a fresh key.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? "
                "ORDER BY attempt ASC",
                (task_id,),
            ).fetchall()
            if task.exclusive_key is not None:
                active = conn.execute(
                    "SELECT * FROM spawn_reservations "
                    "WHERE exclusive_key = ? AND state IN (?, ?, ?, ?) "
                    "ORDER BY reserved_at ASC LIMIT 1",
                    (
                        task.exclusive_key,
                        SpawnState.RESERVING,
                        SpawnState.SPAWNED,
                        SpawnState.COLD,
                        SpawnState.RELEASING,
                    ),
                ).fetchone()
                if active is not None:
                    conn.execute("COMMIT")
                    return SpawnReservation._from_row(active), False
            else:
                for row in rows:
                    if row["state"] in SpawnState.ACTIVE:
                        conn.execute("COMMIT")
                        return SpawnReservation._from_row(row), False
            if task.status != Status.QUEUED or task.owner is not None:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} is {task.status!r} with owner "
                    f"{task.owner!r}; spawn reservation requires queued and unowned"
                )
            attempt = (max(r["attempt"] for r in rows) + 1) if rows else 1
            key = spawn_key(task_id, attempt)
            carried_worktree = task.affinity.get("worktree")
            worktree_ownership = "targeted" if carried_worktree else None
            carried_session = None
            if task.exclusive_key is not None:
                prior = conn.execute(
                    "SELECT session_handle, worktree FROM spawn_reservations "
                    "WHERE exclusive_key = ? AND worktree IS NOT NULL "
                    "ORDER BY reserved_at DESC LIMIT 1",
                    (task.exclusive_key,),
                ).fetchone()
                if prior is not None:
                    carried_worktree = prior["worktree"]
                    carried_session = prior["session_handle"]
                    worktree_ownership = "reused"
            conn.execute(
                "INSERT INTO spawn_reservations "
                "(key, task_id, exclusive_key, attempt, state, reserved_by, "
                "session_handle, worktree, worktree_ownership, reserved_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    task_id,
                    task.exclusive_key,
                    attempt,
                    SpawnState.RESERVING,
                    reserved_by,
                    carried_session,
                    carried_worktree,
                    worktree_ownership,
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,)
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row), True

    def rearm_spawn(
        self,
        task_id: str,
        *,
        permitted: bool = False,
        reason: str | None = None,
        min_failures: int = 3,
        now: float | None = None,
    ) -> dict[str, object]:
        """Atomically retire failed spawn attempts so one fresh retry is eligible.

        The task must still be queued and unowned, no active reservation may
        exist, and at least ``min_failures`` failed attempts must be present.
        All checks and the failed->rearmed transition share one
        ``BEGIN IMMEDIATE`` transaction with task claims and spawn reservations.
        """
        if not permitted:
            raise TaskError("rearming spawn reservations requires explicit permission")
        reason = (reason or "").strip()
        if not reason:
            raise TaskError("rearming spawn reservations requires a non-empty reason")
        if min_failures < 3:
            raise TaskError("min_failures must be at least 3")

        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status != Status.QUEUED or task.owner is not None:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} is {task.status!r} with owner "
                    f"{task.owner!r}; rearm requires queued and unowned"
                )
            rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt ASC",
                (task_id,),
            ).fetchall()
            active = [row["key"] for row in rows if row["state"] in SpawnState.ACTIVE]
            if active:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has active spawn reservation(s): "
                    f"{', '.join(active)}"
                )
            failed = [row for row in rows if row["state"] == SpawnState.FAILED]
            if len(failed) < min_failures:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} has {len(failed)} failed spawn reservation(s); "
                    f"at least {min_failures} required"
                )

            keys: list[str] = []
            for row in failed:
                keys.append(row["key"])
                prior = (row["detail"] or "").strip()
                detail = f"{prior}\nrearmed: {reason}".strip()
                conn.execute(
                    "UPDATE spawn_reservations "
                    "SET state = ?, updated_at = ?, detail = ? WHERE key = ?",
                    (SpawnState.REARMED, ts, detail, row["key"]),
                )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=Status.QUEUED,
                to_status=Status.QUEUED,
                worker="operator",
                note=f"spawn reservations rearmed: {reason}",
            )
            conn.execute("COMMIT")
        return {
            "task_id": task_id,
            "rearmed": len(keys),
            "reservation_keys": keys,
            "reason": reason,
            "next_attempt": max(row["attempt"] for row in rows) + 1,
        }

    def _update_reservation(
        self,
        key: str,
        *,
        to_state: str,
        allowed_from: frozenset[str],
        now: float | None = None,
        session_handle: str | None = None,
        worktree: str | None = None,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
    ) -> SpawnReservation:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] not in allowed_from:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not one of "
                    f"{sorted(allowed_from)} (cannot -> {to_state!r})"
                )
            conn.execute(
                "UPDATE spawn_reservations SET state = ?, updated_at = ?, "
                "session_handle = CASE WHEN ? IS NOT NULL THEN ? ELSE session_handle END, "
                "worktree = CASE WHEN ? IS NOT NULL THEN ? ELSE worktree END, "
                "detail = COALESCE(?, detail), "
                "conclusion_state = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_state END, "
                "conclusion_detail = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_detail END WHERE key = ?",
                (
                    to_state,
                    ts,
                    session_handle,
                    session_handle,
                    worktree,
                    worktree,
                    detail,
                    conclusion_state,
                    conclusion_state,
                    conclusion_detail,
                    conclusion_detail,
                    key,
                ),
            )
            if to_state in SpawnState.RELEASABLE:
                conn.execute(
                    "UPDATE tasks SET activity = NULL, activity_updated_at = ? "
                    "WHERE id = ?",
                    (ts, row["task_id"]),
                )
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,)
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row)

    def record_spawn(
        self,
        key: str,
        *,
        session_handle: str | None = None,
        worktree: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``spawned`` and record its embody session handle.

        Called right after a successful ``agent-worktrees embody`` launch. The
        handle is what lets a supervisor restart reconcile (join the reservation
        to the live session) instead of re-spawning.
        """
        return self._update_reservation(
            key,
            to_state=SpawnState.SPAWNED,
            allowed_from=frozenset(
                {SpawnState.RESERVING, SpawnState.SPAWNED, SpawnState.COLD}
            ),
            session_handle=session_handle,
            worktree=worktree,
            now=now,
        )

    def record_spawn_worktree(
        self,
        key: str,
        worktree: str,
        *,
        ownership: str = "unknown",
        creating_host: str | None = None,
        driver: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Record a reusable worktree while the reservation is still reserving.

        Replacing a carried worktree clears its carried session handle: a
        confirmed-missing worktree cannot safely retain a session binding from
        the vanished checkout.
        """
        worktree = worktree.strip()
        if not worktree:
            raise TaskError("spawn worktree must be non-empty")
        if ownership not in {"created", "targeted", "reused", "unknown"}:
            raise TaskError(f"invalid spawn worktree ownership: {ownership!r}")
        if ownership != "created":
            creating_host = None
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] != SpawnState.RESERVING:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not "
                    f"{SpawnState.RESERVING!r}"
                )
            conn.execute(
                "UPDATE spawn_reservations SET "
                "session_handle = CASE "
                "WHEN worktree IS NULL OR worktree <> ? THEN NULL "
                "ELSE session_handle END, "
                "worktree = ?, worktree_ownership = ?, creating_host = ?, "
                "driver = ?, updated_at = ? WHERE key = ?",
                (
                    worktree,
                    worktree,
                    ownership,
                    creating_host,
                    driver,
                    ts,
                    key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def record_cold(
        self, key: str, *, now: float | None = None
    ) -> SpawnReservation:
        """Mark a spawned headless body intentionally stopped and dormant."""
        return self._update_reservation(
            key,
            to_state=SpawnState.COLD,
            allowed_from=frozenset({SpawnState.SPAWNED, SpawnState.COLD}),
            now=now,
        )

    def fail_spawn(
        self, key: str, *, detail: str | None = None, now: float | None = None
    ) -> SpawnReservation:
        """Mark a reservation ``failed`` (spawn failed or lost), releasing the
        task so a fresh attempt may be reserved."""
        return self._update_reservation(
            key,
            to_state=SpawnState.FAILED,
            allowed_from=SpawnState.ACTIVE,
            detail=detail,
            now=now,
        )

    def request_spawn_release(
        self,
        key: str,
        *,
        detail: str | None = None,
        disposition: str = "failed",
        now: float | None = None,
    ) -> SpawnReservation:
        """Fence a failed attempt until its exact allocation is concluded."""
        if disposition not in {"failed", "settled"}:
            raise TaskError(f"invalid spawn release disposition: {disposition!r}")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] not in SpawnState.ACTIVE:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not active "
                    "(cannot request release)"
                )
            if row["state"] == SpawnState.RELEASING:
                release_disposition = (
                    "failed"
                    if "failed" in {row["release_disposition"], disposition}
                    else row["release_disposition"] or disposition
                )
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested = 1, "
                    "release_disposition = ?, detail = COALESCE(?, detail), "
                    "updated_at = ? WHERE key = ?",
                    (release_disposition, detail, ts, key),
                )
            else:
                conn.execute(
                    "UPDATE spawn_reservations SET state = ?, "
                    "release_requested = 1, release_disposition = ?, "
                    "detail = COALESCE(?, detail), conclusion_state = ?, "
                    "updated_at = ? WHERE key = ?",
                    (
                        SpawnState.RELEASING,
                        disposition,
                        detail,
                        "pending",
                        ts,
                        key,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,)
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row)

    def settle_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``settled`` (its task reached a terminal outcome).

        Repeating the call on an already-settled row is an idempotent detail
        update. Provenance-bearing allocations remain ``releasing`` until
        ground-layer conclusion is complete or deliberately held; legacy
        disposable-CLI settlement may record conclusion after settlement.
        """
        return self._update_reservation(
            key,
            to_state=SpawnState.SETTLED,
            allowed_from=SpawnState.ACTIVE | frozenset({SpawnState.SETTLED}),
            detail=detail,
            conclusion_state=conclusion_state,
            conclusion_detail=conclusion_detail,
            now=now,
        )

    def record_spawn_conclusion(
        self,
        key: str,
        *,
        conclusion_state: str,
        conclusion_detail: str,
        now: float | None = None,
    ) -> SpawnReservation:
        """Persist conclusion progress without releasing an active reservation."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] not in SpawnState.ACTIVE:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not active"
                )
            conn.execute(
                "UPDATE spawn_reservations SET updated_at = ?, "
                "conclusion_state = ?, conclusion_detail = ? WHERE key = ?",
                (ts, conclusion_state, conclusion_detail, key),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def get_reservation(self, key: str) -> SpawnReservation | None:
        """Return one reservation by key, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,)
            ).fetchone()
        return SpawnReservation._from_row(row) if row else None

    def latest_reservation(self, task_id: str) -> SpawnReservation | None:
        """Return the highest-attempt reservation for a task, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? "
                "ORDER BY attempt DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return SpawnReservation._from_row(row) if row else None

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | Sequence[str] | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
        limit: int = 200,
    ) -> list[SpawnReservation]:
        """List spawn reservations, newest first, optionally filtered by task or
        state (a single state or a set of states)."""
        repo = self._canonical_repo(repo)
        clauses: list[str] = []
        params: list[object] = []
        if task_id is not None:
            clauses.append("r.task_id = ?")
            params.append(task_id)
        if state is not None:
            states = [state] if isinstance(state, str) else list(state)
            clauses.append(f"r.state IN ({','.join('?' * len(states))})")
            params.extend(states)
        join_tasks = repo is not None or label is not None or resume_requested is not None
        if repo is not None:
            clauses.append("t.repo = ?")
            params.append(repo)
        if label is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(t.labels) WHERE value = ?)"
            )
            params.append(label)
        if conclusion_state is not None:
            clauses.append("r.conclusion_state = ?")
            params.append(conclusion_state)
        if resume_requested is not None:
            clauses.append("t.resume_requested = ?")
            params.append(1 if resume_requested else 0)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                # ``where`` is built only from constant column names + bound '?'
                # placeholders; every value goes through ``params`` (never
                # interpolated), so this is not an injection vector.
                "SELECT r.* FROM spawn_reservations r "
                f"{'JOIN tasks t ON t.id = r.task_id ' if join_tasks else ''}"
                f"{where} ORDER BY r.reserved_at DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
        return [SpawnReservation._from_row(r) for r in rows]

    # -- schedule registry ---------------------------------------------------

    def register_schedule(self, entry: dict, *, now: float | None = None) -> ScheduleRecord:
        """Register (or update) a recurring schedule by its ``id``.

        ``entry`` is a timer-producer schedule dict; it is validated eagerly
        (id + title + a resolvable lane + exactly one valid cadence) so a
        malformed schedule is rejected at register time rather than silently
        failing every tick. Re-registering the same ``id`` upserts the spec
        (preserving ``created_at`` and the ``paused`` flag).
        """
        from .producers.schedule import ScheduleError, due_occurrences

        sid = entry.get("id")
        if not sid or not str(sid).strip():
            raise TaskError("schedule needs a non-empty 'id'")
        if not str(entry.get("title") or "").strip():
            raise TaskError(f"schedule {sid!r} needs a 'title'")
        if not entry.get("repo"):
            raise TaskError(f"schedule {sid!r} needs a 'repo' (the task lane)")
        try:
            due_occurrences(entry, now=self._now(now))
        except ScheduleError as exc:
            raise TaskError(str(exc)) from exc

        ts = self._now(now)
        spec = json.dumps(entry)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT id FROM schedules WHERE id = ?", (sid,)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE schedules SET spec = ?, updated_at = ? WHERE id = ?",
                    (spec, ts, sid),
                )
            else:
                conn.execute(
                    "INSERT INTO schedules (id, spec, paused, created_at, updated_at) "
                    "VALUES (?, ?, 0, ?, ?)",
                    (sid, spec, ts, ts),
                )
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
            conn.execute("COMMIT")
        return ScheduleRecord._from_row(row)

    def list_schedules(self, *, include_paused: bool = True) -> list[ScheduleRecord]:
        """List registered schedules, ordered by id."""
        query = "SELECT * FROM schedules"
        if not include_paused:
            query += " WHERE paused = 0"
        query += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [ScheduleRecord._from_row(r) for r in rows]

    def get_schedule(self, sid: str) -> ScheduleRecord | None:
        """Return one registered schedule by id, or ``None``."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
        return ScheduleRecord._from_row(row) if row else None

    def remove_schedule(self, sid: str) -> bool:
        """Delete a registered schedule; return whether a row was removed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (sid,))
        return cur.rowcount > 0

    def set_schedule_paused(
        self, sid: str, paused: bool, *, now: float | None = None
    ) -> ScheduleRecord:
        """Pause/resume a schedule (a paused schedule is skipped by the registry
        tick but retains its definition). Raises if the schedule is unknown."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM schedules WHERE id = ?", (sid,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such schedule: {sid}")
            conn.execute(
                "UPDATE schedules SET paused = ?, updated_at = ? WHERE id = ?",
                (1 if paused else 0, ts, sid),
            )
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()
            conn.execute("COMMIT")
        return ScheduleRecord._from_row(row)

    # -- supervisor registrations --------------------------------------------

    @staticmethod
    def _registration_from_row(row: sqlite3.Row) -> RegistrationRecord:
        return RegistrationRecord(
            id=row["id"],
            kind=row["kind"],
            spec=json.loads(row["spec"]),
            machine=row["machine"],
            env=row["env"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def register_registration(
        self,
        kind: str,
        spec: dict,
        *,
        reg_id: str | None = None,
        machine: str | None = None,
        env: str = "default",
        now: float | None = None,
    ) -> RegistrationRecord:
        """Register (or upsert) a supervision unit; return its handle.

        ``kind`` and ``spec`` are validated eagerly (see
        :func:`registrations.validate_registration`) so a malformed unit is
        refused here rather than failing every reconcile. The id is the caller's
        explicit ``reg_id`` or a value **derived deterministically** from
        ``(kind, machine, env, spec)`` -- so re-registering the same unit
        **upserts** (idempotent by handle) rather than duplicating it, preserving
        ``created_at`` and the ``status`` flag across the upsert.
        """
        try:
            validate_registration(kind, spec)
        except RegistrationError as exc:
            raise TaskError(str(exc)) from exc
        env = env or "default"
        rid = reg_id or derive_registration_id(kind, spec, machine, env)
        ts = self._now(now)
        try:
            spec_json = json.dumps(spec)
        except TypeError as exc:
            raise TaskError(
                f"registration 'spec' is not JSON-serializable: {exc}"
            ) from exc
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT id FROM registrations WHERE id = ?", (rid,)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE registrations SET kind = ?, spec = ?, machine = ?, "
                    "env = ?, updated_at = ? WHERE id = ?",
                    (kind, spec_json, machine, env, ts, rid),
                )
            else:
                conn.execute(
                    "INSERT INTO registrations "
                    "(id, kind, spec, machine, env, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, kind, spec_json, machine, env, RegistrationStatus.ACTIVE, ts, ts),
                )
            row = conn.execute(
                "SELECT * FROM registrations WHERE id = ?", (rid,)
            ).fetchone()
            conn.execute("COMMIT")
        return self._registration_from_row(row)

    def list_registrations(
        self,
        *,
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[RegistrationRecord]:
        """List registrations, optionally filtered by kind / machine / env,
        ordered by id."""
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if machine is not None:
            clauses.append("machine = ?")
            params.append(machine)
        if env is not None:
            clauses.append("env = ?")
            params.append(env)
        if not include_paused:
            clauses.append("status != ?")
            params.append(RegistrationStatus.PAUSED)
        query = "SELECT * FROM registrations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._registration_from_row(r) for r in rows]

    def get_registration(self, rid: str) -> RegistrationRecord | None:
        """Return one registration by id, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM registrations WHERE id = ?", (rid,)
            ).fetchone()
        return self._registration_from_row(row) if row else None

    def remove_registration(self, rid: str) -> bool:
        """Delete a registration; return whether a row was removed."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM registrations WHERE id = ?", (rid,))
        return cur.rowcount > 0

    def set_registration_status(
        self, rid: str, status: str, *, now: float | None = None
    ) -> RegistrationRecord:
        """Set a registration's lifecycle status (e.g. pause/resume). Raises if
        the id is unknown or the status is invalid."""
        if status not in RegistrationStatus.ALL:
            raise TaskError(
                f"invalid registration status {status!r}; expected one of "
                f"{', '.join(sorted(RegistrationStatus.ALL))}"
            )
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM registrations WHERE id = ?", (rid,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such registration: {rid}")
            conn.execute(
                "UPDATE registrations SET status = ?, updated_at = ? WHERE id = ?",
                (status, ts, rid),
            )
            row = conn.execute(
                "SELECT * FROM registrations WHERE id = ?", (rid,)
            ).fetchone()
            conn.execute("COMMIT")
        return self._registration_from_row(row)

    # -- schedule job-leases (single-producer election) ----------------------

    def acquire_schedule_lease(
        self,
        scope: str,
        holder: str,
        *,
        holder_session: str | None = None,
        ttl: float | None = None,
        now: float | None = None,
    ) -> tuple[ScheduleLease, bool]:
        """Acquire or renew the job-lease for ``scope`` (pin-not-failover).

        Returns ``(lease, granted)``. A first writer wins the scope
        (``granted=True``); the same ``holder`` renews it (``granted=True``,
        refreshing ``renewed_at``/``expires_at``); a **different** caller is
        refused (``granted=False``) and MUST NOT run the scope's producer --
        the recorded lease is never auto-stolen, even when stale. This elects a
        single producer machine (e.g. the fleet chronicler on one host) without
        a wall-clock takeover. ``ttl`` only sets ``expires_at`` for
        observability; it does not enable a takeover.
        """
        ts = self._now(now)
        expires_at = (ts + ttl) if ttl else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schedule_leases "
                    "(scope, holder, holder_session, acquired_at, renewed_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (scope, holder, holder_session, ts, ts, expires_at),
                )
                granted = True
            elif row["holder"] == holder:
                conn.execute(
                    "UPDATE schedule_leases SET "
                    "holder_session = COALESCE(?, holder_session), "
                    "renewed_at = ?, expires_at = ? WHERE scope = ?",
                    (holder_session, ts, expires_at, scope),
                )
                granted = True
            else:
                granted = False
            row = conn.execute(
                "SELECT * FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
            conn.execute("COMMIT")
        return ScheduleLease._from_row(row), granted

    def release_schedule_lease(
        self, scope: str, holder: str, *, force: bool = False, now: float | None = None
    ) -> bool:
        """Release the job-lease for ``scope``. The current holder may release
        its own lease; ``force=True`` lets an operator reassign a lease held by
        a different (e.g. retired) holder. Returns whether a lease was removed;
        raises if a non-holder tries to release without ``force``."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT holder FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            if not force and row["holder"] != holder:
                conn.execute("COMMIT")
                raise TaskError(
                    f"lease {scope!r} is held by {row['holder']!r}, not {holder!r} "
                    "(use force to reassign)"
                )
            conn.execute("DELETE FROM schedule_leases WHERE scope = ?", (scope,))
            conn.execute("COMMIT")
        return True

    def get_schedule_lease(self, scope: str) -> ScheduleLease | None:
        """Return the job-lease for ``scope``, or ``None`` if unheld."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM schedule_leases WHERE scope = ?", (scope,)
            ).fetchone()
        return ScheduleLease._from_row(row) if row else None

    def list_schedule_leases(self) -> list[ScheduleLease]:
        """List all held job-leases, ordered by scope."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedule_leases ORDER BY scope"
            ).fetchall()
        return [ScheduleLease._from_row(r) for r in rows]

    # -- external producer resource reservations ----------------------------

    def acquire_resource_reservation(
        self,
        key: str,
        owner: str,
        *,
        ttl: float,
        token: str | None = None,
        now: float | None = None,
    ) -> tuple[ResourceReservation, bool]:
        """Atomically elect one owner for an external logical resource.

        An unbound reservation expires so another producer can recover after a
        crash before task creation. Once bound to a task, it remains owned until
        explicit terminal reconciliation releases it.
        """
        if not key or not owner:
            raise TaskError("resource reservation key and owner are required")
        if ttl <= 0:
            raise TaskError("resource reservation ttl must be positive")
        ts = self._now(now)
        expires_at = ts + ttl
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                token = secrets.token_urlsafe(24)
                conn.execute(
                    "INSERT INTO resource_reservations "
                    "(key, owner, token, task_id, acquired_at, updated_at, expires_at) "
                    "VALUES (?, ?, ?, NULL, ?, ?, ?)",
                    (key, owner, token, ts, ts, expires_at),
                )
                granted = True
            elif (
                token is not None
                and row["owner"] == owner
                and secrets.compare_digest(row["token"], token)
            ):
                if row["task_id"] is None:
                    conn.execute(
                        "UPDATE resource_reservations "
                        "SET updated_at = ?, expires_at = ? WHERE key = ?",
                        (ts, expires_at, key),
                    )
                granted = True
            elif row["task_id"] is None and (
                row["expires_at"] is not None and row["expires_at"] <= ts
            ):
                token = secrets.token_urlsafe(24)
                conn.execute(
                    "UPDATE resource_reservations SET owner = ?, token = ?, task_id = NULL, "
                    "acquired_at = ?, updated_at = ?, expires_at = ? WHERE key = ?",
                    (owner, token, ts, ts, expires_at, key),
                )
                granted = True
            else:
                granted = False
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            conn.execute("COMMIT")
        return ResourceReservation._from_row(row), granted

    def bind_resource_reservation(
        self,
        key: str,
        owner: str,
        token: str,
        task_id: str,
        *,
        now: float | None = None,
    ) -> ResourceReservation:
        """Bind an owned reservation to its created task."""
        if not token or not task_id:
            raise TaskError("resource reservation token and task_id are required")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such resource reservation: {key}")
            if row["owner"] != owner or not secrets.compare_digest(
                row["token"], token
            ):
                conn.execute("COMMIT")
                raise TaskError(
                    f"resource reservation {key!r} identity does not match"
                )
            if row["task_id"] not in (None, task_id):
                conn.execute("COMMIT")
                raise TaskError(
                    f"resource reservation {key!r} is already bound to "
                    f"{row['task_id']!r}"
                )
            conn.execute(
                "UPDATE resource_reservations "
                "SET task_id = ?, updated_at = ?, expires_at = NULL WHERE key = ?",
                (task_id, ts, key),
            )
            row = conn.execute(
                "SELECT * FROM resource_reservations WHERE key = ?", (key,)
            ).fetchone()
            conn.execute("COMMIT")
        return ResourceReservation._from_row(row)

    def release_resource_reservation(
        self, key: str, owner: str, token: str
    ) -> bool:
        """Release only the caller's own resource reservation.

        A non-owner receives ``False``; the current owner's reservation is
        never modified.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, token FROM resource_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            if row["owner"] != owner or not secrets.compare_digest(
                row["token"], token
            ):
                conn.execute("COMMIT")
                return False
            conn.execute(
                "DELETE FROM resource_reservations WHERE key = ?", (key,)
            )
            conn.execute("COMMIT")
        return True

    def list_resource_reservations(
        self,
        *,
        owner_prefix: str | None = None,
        task_id: str | None = None,
    ) -> list[ResourceReservation]:
        clauses: list[str] = []
        params: list[object] = []
        if owner_prefix is not None:
            clauses.append("substr(owner, 1, ?) = ?")
            params.extend((len(owner_prefix), owner_prefix))
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM resource_reservations"
                + where
                + " ORDER BY key",
                params,
            ).fetchall()
        return [ResourceReservation._from_row(row) for row in rows]
