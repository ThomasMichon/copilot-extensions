"""``TaskQueue`` mixin: task-row storage, payload/result I/O, and creation.

Extracted from :mod:`agent_dispatch.queue` as the storage-facing core that the
other queue mixins already build on: canonical repo/token normalization,
attachment/event persistence, payload/result reads, and the task-creation path
all revolve around durable task-row storage rather than task-state transitions.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on queue-owned connection state
(``self._connect()``, ``self.payloads``, ``self.lease_seconds``) and on helper
methods supplied by the other composed mixins (notably the producer-fence
helpers). It is not usable standalone.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import sqlite3
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence

from .identity import canonical_reviewer_target, canonicalize_remote
from .payload import is_blob_ref
from .queue_common import (
    AttachmentRecord,
    CreationOutcome,
    StructuredResult,
    Task,
    TaskAttachmentEntry,
    WakeOperation,
    _TASK_SELECT,
    encode_result,
)
from .queue_producer_fences import (
    ProducerFenceError,
    ProducerScopeValidationError,
)
from .queue_records import Status, TaskError

log = logging.getLogger("agent-dispatch.queue")


class QueueStorageMixin:
    """Storage, payload/result I/O, and create methods for :class:`TaskQueue`."""

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
    def _record_attachment(
        conn: sqlite3.Connection,
        task_id: str,
        *,
        ts: float,
        old_session_id: str | None,
        new_session_id: str | None,
        worktree_id: str | None,
        machine: str | None,
        detach_reason: str,
    ) -> None:
        """Append to the task's durable attachment history when the owner session changes."""
        if old_session_id == new_session_id:
            return
        if old_session_id is not None:
            conn.execute(
                "UPDATE task_attachments SET detached_at = ?, detach_reason = ? "
                "WHERE task_id = ? AND session_id = ? AND detached_at IS NULL",
                (ts, detach_reason, task_id, old_session_id),
            )
        if new_session_id is not None:
            conn.execute(
                "INSERT INTO task_attachments "
                "(task_id, session_id, worktree_id, machine, attached_at, detached_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (task_id, new_session_id, worktree_id, machine, ts),
            )

    def attachment_history(self, task_id: str) -> list[AttachmentRecord]:
        """Every session that has ever attached to ``task_id``, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, worktree_id, machine, attached_at, detached_at, "
                "detach_reason FROM task_attachments WHERE task_id = ? "
                "ORDER BY attached_at DESC",
                (task_id,),
            ).fetchall()
        return [
            AttachmentRecord(
                session_id=row["session_id"],
                worktree_id=row["worktree_id"],
                machine=row["machine"],
                attached_at=row["attached_at"],
                detached_at=row["detached_at"],
                detach_reason=row["detach_reason"],
            )
            for row in rows
        ]

    def tasks_for_session(self, session_id: str) -> list[TaskAttachmentEntry]:
        """Every task ``session_id`` has ever attached to, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task_id, worktree_id, machine, attached_at, detached_at, "
                "detach_reason FROM task_attachments WHERE session_id = ? "
                "ORDER BY attached_at DESC",
                (session_id,),
            ).fetchall()
        return [
            TaskAttachmentEntry(
                task_id=row["task_id"],
                worktree_id=row["worktree_id"],
                machine=row["machine"],
                attached_at=row["attached_at"],
                detached_at=row["detached_at"],
                detach_reason=row["detach_reason"],
            )
            for row in rows
        ]

    def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None:
        row = conn.execute(f"SELECT {_TASK_SELECT} FROM tasks WHERE id = ?", (task_id,)).fetchone()
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
        row = conn.execute("SELECT * FROM wake_outbox WHERE id = ?", (operation_id,)).fetchone()
        return WakeOperation._from_row(row)

    def _payload_needs_spill(self, payload_ref: str | None, payload_inline: str | None) -> bool:
        return (
            payload_ref is None
            and payload_inline is not None
            and len(payload_inline.encode("utf-8")) > self.blob_threshold
        )

    def _spill_committed_payload(self, task_id: str, content: str) -> None:
        """Move a committed inline payload to the blob store."""
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
        """Resolve a task's payload content (inline or blob), or ``None``."""
        task = self.get(task_or_id) if isinstance(task_or_id, str) else task_or_id
        if task is None:
            raise TaskError(f"no such task: {task_or_id}")
        if task.payload_inline is not None:
            return task.payload_inline
        if is_blob_ref(task.payload_ref):
            return self.payloads.get(task.payload_ref)  # type: ignore[arg-type]
        return None

    def _encode_result(self, result: object | None) -> str | None:
        """Validate and canonically encode an optional JSON-compatible result."""
        return encode_result(result, max_bytes=self.result_max_bytes)

    def read_result(self, task_or_id: Task | str) -> StructuredResult | None:
        """Return a task's decoded structured completion result, or ``None``."""
        task = self.get(task_or_id) if isinstance(task_or_id, str) else task_or_id
        if task is None:
            raise TaskError(f"no such task: {task_or_id}")
        result = task.result
        return result if isinstance(result, (dict, list)) else None

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
        """Insert a task (default status ``queued``; ``proposed`` for a draft)."""
        if status not in (Status.QUEUED, Status.PROPOSED):
            raise TaskError(f"new task must be 'queued' or 'proposed', not {status!r}")
        if exclusive_key is not None:
            exclusive_key = exclusive_key.strip() or None
        if supersede_exclusive_key and exclusive_key is None:
            raise TaskError("supersede_exclusive_key requires a non-empty exclusive_key")
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
            compatible_request_hashes.add(self._producer_request_hash(pre_exclusive_fields))
            raw_requires = sorted(set(requires or ()))
            raw_excludes = sorted(set(excludes or ()))
            if (
                raw_requires != request_fields["requires"]
                or raw_excludes != request_fields["excludes"]
            ):
                legacy_fields = dict(request_fields)
                legacy_fields["requires"] = raw_requires
                legacy_fields["excludes"] = raw_excludes
                compatible_request_hashes.add(self._producer_request_hash(legacy_fields))
                legacy_fields.pop("exclusive_key")
                compatible_request_hashes.add(self._producer_request_hash(legacy_fields))
        ts = self._now(now)
        task_id = uuid.uuid4().hex
        spill_content = (
            payload_inline if self._payload_needs_spill(payload_ref, payload_inline) else None
        )
        accepted: Task | None = None
        request_already_recorded = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                managed = self._producer_scope_row(conn, repo=canonical_repo, source=source)
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
                                str(fence["request_id"]) if fence is not None else None
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
                            "task required label belongs to a different managed producer scope",
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
                    if requested_generation != managed["current_generation"] and request is None:
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
                            "producer_id does not match the selected producer for this generation",
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
                    capability_hash = self._capability_hash(str(fence["capability"]))
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
                                "accepted producer request has inconsistent producer metadata",
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
                        if required_label is not None and required_label not in set(labels or ()):
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
                if fence is not None and accepted is not None and not request_already_recorded:
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
                disposition=("replayed" if request_already_recorded else "deduplicated"),
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
        if claim_as and task.status == Status.CLAIMED:
            self._notify_owned_transition()
        outcome = CreationOutcome(
            task=task,
            disposition="created",
            event_type=("task.proposed" if status == Status.PROPOSED else "task.created"),
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
