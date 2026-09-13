"""``TaskQueue`` mixin: producer scope/fence validation and generation handoff.

Extracted from :mod:`agent_dispatch.queue` (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2423) as the next natural
cluster after ``queue_spawn_reservations.py``: producer-scope token/fence
validation, claim-time fence rejection classification, and the exclusive
generation handoff (``handoff_producer_scope``) are a distinct concern --
multi-tenant producer identity and generation fencing over the
``producer_scopes``/``producer_scope_generations``/``producer_create_requests``/
``producer_claim_rejections`` tables -- from the task-claim and spawn-
reservation lifecycles that surround it in ``queue.py``. None of these
methods is monkeypatched by name anywhere in the test suite, so, like
``ScheduleRegistrationMixin`` and ``RoutingAssignmentMixin``, this is a plain
mixin, not a proxy-forwarding split.

``ProducerScopeValidationError``/``ProducerFenceError`` (raised across this
cluster) and ``ProducerScopeState``/``ProducerScopeTransition`` (returned by
it) move here too -- ``queue.py`` re-exports all four for existing
``from agent_dispatch.queue import ...`` consumers (``coordinator.py``,
``mcp_http.py``, and the test suite), matching the ``queue_records.py``
precedent of giving cross-module names their own home rather than a
lazy-import/``TYPE_CHECKING`` trick.

:class:`ProducerFenceMixin` is composed into
:class:`agent_dispatch.queue.TaskQueue` via multiple inheritance; it relies
on ``self._connect()``/``self._now()`` from that class, ``TaskQueue._audit``
(reached via ``cls._audit`` since ``cls`` resolves to the real ``TaskQueue``
at call time through the mixin composition), and a lazy, function-local
import of ``queue.py``'s ``_TASK_BULK_SELECT`` constant (a plain SQL-column
string, not a type -- the lazy-import/``TYPE_CHECKING`` gotcha this effort's
prior slice hit only applies to names used in type annotations, which
``get_type_hints()`` must resolve; a runtime-only string constant has no such
gap). It is not usable standalone.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .identity import canonicalize_remote
from .queue_records import Status, TaskError

#: Bounds and validation pattern for producer-supplied identity/scope tokens --
#: exact tokens only (no whitespace, no wildcards), so a scope or fence can
#: never be satisfied by an approximate match.
_PRODUCER_SCOPE_SOURCE_MAX = 64
_PRODUCER_SCOPE_LABEL_MAX = 64
_PRODUCER_ID_MAX = 128
_PRODUCER_REQUEST_ID_MAX = 128
_PRODUCER_CAPABILITY_MAX = 512
_PRODUCER_HISTORY_LIMIT = 32
_PRODUCER_BLOCKING_TASK_LIMIT = 20
_PRODUCER_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$")


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


class ProducerFenceMixin:
    """Producer scope/fence validation and generation-handoff methods for
    :class:`agent_dispatch.queue.TaskQueue`."""

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
    def _validate_producer_scope(cls, repo: object, source: object) -> tuple[str, str]:
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
        from .queue import _TASK_BULK_SELECT

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

    @classmethod
    def _record_claim_rejection(
        cls,
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
        cls._audit(
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

    def producer_scope_status(self, repo: str, source: str) -> ProducerScopeState:
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
                            current["active_producer"] if current is not None else None
                        ),
                        requested_generation=expected_generation,
                        current_generation=(
                            current["current_generation"] if current is not None else 0
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
                        state = self._producer_scope_state_from_conn(conn, repo, source)
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
                        "producer fence history is inconsistent; current generation is not active",
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
