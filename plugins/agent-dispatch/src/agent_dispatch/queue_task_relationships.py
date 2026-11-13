"""Cross-owner assignment reconciliation on the original task audit event."""

from __future__ import annotations

import base64
import copy
import json
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import asdict

from .assignment_workspace import AssignmentWorkspace, same_assignment_worker
from .queue_common import Task
from .queue_records import Status, TaskError

SCHEMA = "copilot-extensions.resource-relationship"
WRITER = {
    "schema": "copilot-extensions.worktree-record-writer",
    "version": 1,
    "retained_relationships": 1,
    "creation_nonce": 1,
}


def valid_record_writer(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema") == WRITER["schema"]
        and all(
            type(value.get(key)) is int and value[key] == 1
            for key in ("version", "retained_relationships", "creation_nonce")
        )
    )


def _encoded(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def latest_relationship(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM task_events WHERE task_id=? AND task_relationship IS NOT NULL"
        " ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()


def require_relationship(conn: sqlite3.Connection, task_id: str) -> None:
    row = latest_relationship(conn, task_id)
    required = conn.execute(
        "SELECT 1 FROM spawn_reservations"
        " WHERE task_id=? AND task_relationship_required=1 LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None and required is None:
        return
    from .queue_relationship_schema import bound_relationship_condition

    protected = conn.execute(
        "SELECT " + bound_relationship_condition("t") + " FROM tasks t WHERE t.id=?",  # noqa: S608 -- fixed predicate
        (task_id,),
    ).fetchone()
    if protected is None or not protected[0]:
        raise TaskError(
            "task relationship protection is unconfirmed; reconcile its original assignment"
        )


def _matches_receipt(value: object, request: dict, *, phase: str | None = None) -> bool:
    workspace = AssignmentWorkspace(**request["workspace"])
    if not isinstance(value, dict) or not (
        value.get("schema") == SCHEMA
        and type(value.get("version")) is int
        and value["version"] == 1
        and value.get("ok") is True
        and value.get("owner") == workspace.owner_ref
        and value.get("creation_nonce") == workspace.creation_nonce
        and valid_record_writer(value.get("writer_contract"))
        and value.get("request_id") == request["request_id"]
    ):
        return False
    if value.get("absent") is True:
        return phase == "absent"
    metadata = value.get("producer")
    if not isinstance(metadata, dict) or not (
        {"version", "lifetime", "creator", "namespace", "request_id", "recovery", "token", "phase"}
        <= metadata.keys()
        and value.get("phase") in {"pending", "bound", "closed"}
        and metadata.get("version") == 2
        and metadata.get("lifetime") == "retained-relationship"
        and metadata.get("creator") == workspace.owner_ref
        and metadata.get("namespace") == request["namespace"]
        and metadata.get("request_id") == request["request_id"]
        and metadata.get("recovery") == request["recovery"]
        and value.get("token") == metadata.get("token")
        and value.get("phase") == metadata.get("phase")
        and (phase is None or value["phase"] == phase)
    ):
        return False
    envelope = {
        key: metadata[key] for key in ("version", "token", "creator", "request_id", "recovery")
    }
    envelope["phase"] = "bound" if "identity" in metadata else "pending"
    if "identity" in metadata:
        envelope["identity"] = metadata["identity"]
    expected_ref = (
        request["namespace"]
        + ":l1."
        + base64.urlsafe_b64encode(
            _encoded(envelope).encode("ascii"),
        )
        .decode("ascii")
        .rstrip("=")
    )
    from .client_errors import DispatchError
    from .task_relationships import AssignmentSubject

    ref = value.get("ref")
    if ref != expected_ref:
        return False
    try:
        subject = AssignmentSubject.decode(
            ref[len(request["namespace"]) + 1 :],
            authority=request["authority"],
        )
    except DispatchError:
        return False
    return (
        subject.assignment_event_id == request["event_id"]
        and subject.worker_id == workspace.worker_id
        and subject.task_id == request["task_id"]
        and ("identity" not in metadata or metadata["identity"] == request["identity"])
    )


class QueueTaskRelationshipsMixin:
    def _relationship_selection(self, task: Task) -> tuple[object, str | None]:
        if task.kind == "standing-charter" and self._standing_relationship_writer is not None:
            return self._standing_relationship_writer, "local"
        return self._relationship_writer, self._relationship_authority

    def _replay_relationship_claim(
        self,
        task_id: str | None,
        worker_id: str,
        workspace: AssignmentWorkspace | None,
    ) -> Task | None:
        if task_id is None:
            return None
        task = self.get(task_id)
        if task is not None and task.kind == "standing-charter":
            raise TaskError("standing charter requires explicit attachment admission")
        if workspace is None:
            return None
        value = self.task_relationship(task_id)
        if value is None:
            return None
        request = value["request"]
        if (
            request["reservation_key"] is not None
            or task.status != Status.CLAIMED
            or task.owner != worker_id
            or task.generation != request["generation"]
        ):
            return None
        if request["workspace"] != asdict(workspace):
            raise TaskError("claim replay conflicts with its original workspace")
        return self._acknowledge_relationship_claim(
            task_id, worker_id, task.generation, event_id=request["event_id"],
        )

    def _acknowledge_relationship_claim(
        self, task_id: str, worker_id: str, generation: int, *, event_id: int,
    ) -> Task:
        value = self.reconcile_task_relationship(task_id, event_id=event_id)
        if value["state"] != "bound":
            raise TaskError("task assignment was cancelled during relationship reconciliation")
        with self._connect() as conn:
            conn.execute("BEGIN")
            task = self._fetch(conn, task_id)
            current = latest_relationship(conn, task_id)
            if (
                task is None or task.status != Status.CLAIMED
                or task.owner != worker_id or task.generation != generation
                or current is None or current["id"] != event_id
            ):
                raise TaskError("task assignment changed during relationship reconciliation")
            require_relationship(conn, task_id)
            return task

    def _retain_task_relationship(
        self,
        conn: sqlite3.Connection,
        task: Task,
        workspace: AssignmentWorkspace,
        *,
        worker_id: str,
        generation: int,
        ts: float,
        reservation_key: str | None = None,
    ) -> int:
        writer, authority = self._relationship_selection(task)
        if writer is None or authority not in {
            "local",
            "central",
        }:
            raise TaskError("selected task owner has no qualified relationship writer")
        if not isinstance(workspace, AssignmentWorkspace):
            raise TaskError("task relationship requires an original workspace identity")
        if workspace.worker_id != worker_id:
            raise TaskError("task relationship workspace does not match the assigned worker")
        previous = latest_relationship(conn, task.id)
        if previous is not None and json.loads(previous["task_relationship"])["state"] not in {
            "closed",
            "absent",
        }:
            raise TaskError("previous task relationship requires original-owner settlement")
        cursor = conn.execute(
            "INSERT INTO task_events(task_id, ts, from_status, to_status, worker, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                task.id,
                ts,
                task.status,
                Status.CLAIMED,
                worker_id,
                "worktree assignment" if reservation_key else "claim",
            ),
        )
        event_id = cursor.lastrowid
        identity = {"assignment_event_id": event_id, "worker_id": worker_id}
        from .queue_assignment_cohort import capture_cohort

        request = {
            "request_id": secrets.token_hex(16),
            "event_id": event_id,
            "task_id": task.id,
            "generation": generation,
            "workspace": asdict(workspace),
            "reservation_key": reservation_key,
            "authority": authority,
            "namespace": f"dispatch-{authority}-task",
            "identity": identity,
            "cohort": capture_cohort(conn, task, reservation_key=reservation_key),
            "recovery": {
                "schema": "dispatch-task-assignment",
                "version": 2,
                "authority": authority,
                "task_id": task.id,
                "queue_identity": conn.execute(
                    "SELECT identity FROM workspace_queue_identity WHERE singleton=1",
                ).fetchone()["identity"],
                **identity,
            },
        }
        conn.execute(
            "UPDATE task_events SET task_relationship=? WHERE id=?",
            (_encoded({"request": request, "state": "pending", "receipt": None}), event_id),
        )
        return event_id

    def task_relationship(self, task_id: str, *, event_id: int | None = None) -> dict | None:
        if event_id is not None and (type(event_id) is not int or not 1 <= event_id <= 2**63 - 1):
            raise TaskError("invalid original task relationship event")
        with self._connect() as conn:
            row = (
                latest_relationship(conn, task_id)
                if event_id is None
                else conn.execute(
                    "SELECT * FROM task_events WHERE task_id=? AND id=?"
                    " AND task_relationship IS NOT NULL",
                    (task_id, event_id),
                ).fetchone()
            )
        return json.loads(row["task_relationship"]) if row is not None else None

    def _admit_original_relationship_request(self, request: dict) -> dict:
        from .task_obligations import read_snapshot

        task = self.get(request["task_id"])
        authority = self._relationship_selection(task)[1] if task is not None else None
        snapshot = read_snapshot(
            self, request["task_id"], assignment_event_id=request["event_id"],
        )
        if (
            authority != request["authority"]
            or snapshot is None
            or snapshot["queue_identity"] != request["recovery"]["queue_identity"]
            or snapshot["assignment"] != {
                "event_id": request["event_id"],
                "worker_id": AssignmentWorkspace(**request["workspace"]).worker_id,
            }
            or _encoded(snapshot.get("relationship")) != _encoded(request)
        ):
            raise TaskError("original queue and audited relationship request are unconfirmed")
        if request["reservation_key"] is not None:
            with self._connect() as conn:
                prepared = conn.execute(
                    "SELECT task_id, worktree, allocation_project, worktree_path,"
                    " execution_machine, creation_nonce, task_relationship_required,"
                    " task_relationship_event_id, task_relationship_generation"
                    " FROM spawn_reservations WHERE key=?", (request["reservation_key"],),
                ).fetchone()
            workspace = AssignmentWorkspace(**request["workspace"])
            expected = {
                "task_id": request["task_id"], "worktree": workspace.worktree,
                "allocation_project": workspace.project, "worktree_path": workspace.path,
                "creation_nonce": workspace.creation_nonce, "task_relationship_required": 1,
                "task_relationship_event_id": request["event_id"],
                "task_relationship_generation": request["generation"],
            }
            if (
                prepared is None
                or any(prepared[key] != value for key, value in expected.items())
                or not same_assignment_worker(
                    f"{prepared['execution_machine']}/{prepared['worktree']}", workspace.worker_id,
                )
            ):
                raise TaskError("original prepared workspace and reservation are unconfirmed")
        return snapshot

    def reconcile_task_relationship(
        self, task_id: str, *, event_id: int | None = None,
        authority_guard: Callable[[], None] | None = None,
    ) -> dict:
        """Replay the same ground-owner request; never hold SQLite during I/O."""
        value = self.task_relationship(task_id, event_id=event_id)
        if value is None:
            raise TaskError("no retained task relationship request")
        request = value["request"]
        task = self.get(task_id)
        selected_writer, authority = self._relationship_selection(task) if task is not None else (None, None)
        if (
            selected_writer is None
            or authority != request["authority"]
        ):
            raise TaskError("original relationship writer is unavailable; assignment retained")
        workspace = AssignmentWorkspace(**request["workspace"])
        if value["state"] in {"closed", "absent"}:
            return value
        snapshot = self._admit_original_relationship_request(request)
        retiring = snapshot["release_outcome"] in {
            "completed", "unassigned_queued", "relevance_closeout_ready",
        }
        if retiring and snapshot["release_outcome"] == "unassigned_queued":
            retiring = (
                same_assignment_worker(snapshot["unassignment"]["worker_id"], workspace.worker_id)
                and snapshot["unassignment"]["event_id"] > request["event_id"]
            )
        if not retiring and value["state"] == "bound":
            return value
        with self._connect() as conn:
            task = self._fetch(conn, task_id)
            current = latest_relationship(conn, task_id)
            from .queue_relationship_continuation import current_binding

            binding = current_binding(value)
            if not retiring and (
                current["id"] != request["event_id"]
                or task.generation != binding["generation"]
                or task.status not in {Status.CLAIMED, Status.STARTED, Status.SUSPENDED}
                or (
                    task.owner is not None
                    and not same_assignment_worker(task.owner, workspace.worker_id)
                )
            ):
                raise TaskError("original task assignment changed; relationship retained")
        common = {"expected_creation_nonce": workspace.creation_nonce}
        writer = selected_writer
        bind_request = getattr(writer, "bind_request", None)
        if callable(bind_request):
            writer = bind_request(copy.deepcopy(request))

        def write(operation: str, payload: dict, *, phase: str | None = None) -> dict:
            self._admit_original_relationship_request(request)
            if authority_guard is not None:
                authority_guard()
            try:
                result = writer(workspace, operation, {**payload, **common})
            except Exception as exc:
                raise TaskError(
                    "task relationship request is unresolved; reconcile its original assignment"
                ) from exc
            if not _matches_receipt(result, request, phase=phase):
                raise TaskError("ground owner did not confirm the exact task relationship")
            return result

        if retiring:
            self._admit_original_relationship_request(request)
            if authority_guard is not None:
                authority_guard()
            receipt = writer(
                workspace,
                "inspect",
                {
                    **common,
                    "request_id": request["request_id"],
                    "allow_missing": True,
                },
            )
            if isinstance(receipt, dict) and receipt.get("absent") is True:
                if not _matches_receipt(receipt, request, phase="absent"):
                    raise TaskError("ground owner did not confirm exact relationship absence")
                # Absence alone does not fence an earlier in-flight reserve.
                # Replay the original id, then retain its closed tombstone.
                receipt = write(
                    "reserve",
                    {
                        "kind": "task",
                        "namespace": request["namespace"],
                        "request_id": request["request_id"],
                        "recovery": request["recovery"],
                    },
                )
            else:
                if not _matches_receipt(receipt, request):
                    raise TaskError("ground owner did not confirm the original relationship")
            receipt = write("close", {"token": receipt["token"]}, phase="closed")
            state = "closed"
        else:
            receipt = write(
                "reserve",
                {
                    "kind": "task",
                    "namespace": request["namespace"],
                    "request_id": request["request_id"],
                    "recovery": request["recovery"],
                },
            )
            receipt = write(
                "bind",
                {
                    "token": receipt["token"],
                    "identity": request["identity"],
                },
                phase="bound",
            )
            state = "bound"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if authority_guard is not None:
                authority_guard()
            retained = conn.execute(
                "SELECT task_relationship FROM task_events WHERE id=? AND task_id=?",
                (request["event_id"], task_id),
            ).fetchone()
            if retained is None or json.loads(retained["task_relationship"])["request"] != request:
                raise TaskError("original task relationship request changed")
            task = self._fetch(conn, task_id)
            if state == "bound" and (
                latest_relationship(conn, task_id)["id"] != request["event_id"]
                or task.generation != binding["generation"]
                or task.status not in {Status.CLAIMED, Status.STARTED, Status.SUSPENDED}
                or (
                    task.owner is not None
                    and not same_assignment_worker(task.owner, workspace.worker_id)
                )
            ):
                raise TaskError(
                    "task changed during relationship write; original request retained"
                )
            result = {**json.loads(retained["task_relationship"]), "state": state, "receipt": receipt}
            conn.execute(
                "UPDATE task_events SET task_relationship=? WHERE id=?",
                (_encoded(result), request["event_id"]),
            )
            conn.commit()
        return result
