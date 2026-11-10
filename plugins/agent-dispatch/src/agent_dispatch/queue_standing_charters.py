"""Standing declarations in the native task/event store, without launching work."""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, fields

from .assignment_workspace import AssignmentWorkspace
from .queue_common import Task
from .queue_execution_handoff import require_execution_admission
from .queue_records import Status, TaskError
from .queue_relationship_continuation import continue_owner_session, current_binding
from .queue_standing_schema import IDENTITY_FIELDS, PROTECTED_FIELDS, STANDING_KIND
from .queue_task_recovery import require_task_recovery_safe
from .queue_task_relationships import _matches_receipt, latest_relationship, require_relationship


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class StandingCharterDeclaration:
    namespace: str
    charter_key: str
    lane_key: str
    effort_ref: str
    materialization_ref: str
    source_revision: str
    expected_materialization_revision: str

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if (
                not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 1024 or any(ord(char) < 32 for char in value)
            ):
                raise TaskError(f"standing declaration {field.name} must be bounded nonempty text")


class QueueStandingChartersMixin:
    def _require_standing_source(self, conn: sqlite3.Connection, repo: str) -> None:
        if self._producer_scope_row(conn, repo=repo, source=STANDING_KIND) is not None:
            raise TaskError("standing declaration source requires managed producer authority")

    def _require_standing_child_parent(
        self, conn: sqlite3.Connection, parent_id: str, repo: str,
    ) -> None:
        """Admit a new finite child without coupling its lifecycle to the parent."""
        row = conn.execute(
            "SELECT kind, repo, standing_charter FROM tasks WHERE id=?", (parent_id,),
        ).fetchone()
        if row is None or row["kind"] != STANDING_KIND or row["repo"] != repo:
            raise TaskError("finite child requires a standing parent in the same repo lane")
        if json.loads(row["standing_charter"])["lifecycle"] != "active":
            raise TaskError(
                "standing parent is retiring or retired; it admits no new finite children"
            )

    def _require_standing(
        self, conn: sqlite3.Connection, task_id: str, expected_revision: int,
    ) -> Task:
        task = self._fetch(conn, task_id)
        if task is None or task.kind != STANDING_KIND:
            raise TaskError("operation requires a standing charter task")
        if type(expected_revision) is not int or task.standing_revision != expected_revision:
            raise TaskError("standing charter revision changed; refresh before retrying")
        return task

    def _change_standing(
        self, conn: sqlite3.Connection, task: Task, changes: dict, *, action: str, ts: float,
        unassigned_worker: str | None = None,
    ) -> Task:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task.id,)).fetchone()
        after = {name: row[name] for name in PROTECTED_FIELDS}
        after.update(changes)
        after["standing_revision"] = task.standing_revision + 1
        event = {"revision": after["standing_revision"], "action": action, "after": after}
        conn.execute(
            "INSERT INTO task_events(task_id, ts, from_status, to_status, worker, note,"
            " standing_change, unassigned_worker) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (task.id, ts, task.status, after["status"], task.owner, f"standing:{action}",
             _encoded(event), unassigned_worker),
        )
        updates = {**changes, "standing_revision": after["standing_revision"], "updated_at": ts}
        assignments = ", ".join(f"{name}=?" for name in updates)
        conn.execute(
            f"UPDATE tasks SET {assignments} WHERE id=? AND standing_revision=?",
            (*updates.values(), task.id, task.standing_revision),
        )
        return self._fetch(conn, task.id)

    def reconcile_standing_charter(
        self, title: str, declaration: StandingCharterDeclaration, *, repo: str,
        expected_revision: int | None = None, now: float | None = None,
    ) -> Task:
        """Upsert one qualified lane; discovery never retires or resurrects it."""
        if not isinstance(declaration, StandingCharterDeclaration):
            raise TaskError("standing declaration must be validated before reconciliation")
        if not isinstance(title, str) or not title.strip() or len(title) > 1024:
            raise TaskError("standing title must be bounded nonempty text")
        canonical_repo = self._canonical_repo(repo)
        if canonical_repo is None:
            raise TaskError("standing charter requires a repository lane")
        document = asdict(declaration)
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_standing_source(conn, canonical_repo)
            identity = " AND ".join(
                f"json_extract(standing_charter, '$.declaration.{name}')=?" for name in IDENTITY_FIELDS
            )
            row = conn.execute(
                f"SELECT id FROM tasks WHERE repo=? AND kind=? AND {identity}",
                (canonical_repo, STANDING_KIND, *(document[name] for name in IDENTITY_FIELDS)),
            ).fetchone()
            if row is not None:
                task = self._fetch(conn, row["id"])
                standing = task.standing_charter
                if standing["lifecycle"] != "active":
                    raise TaskError("standing charter is retiring or retired; explicit re-enrollment required")
                if standing["declaration"] == document and task.title == title:
                    return task
                self._require_standing(conn, task.id, expected_revision)
                return self._change_standing(
                    conn, task, {
                        "title": title,
                        "standing_charter": _encoded({**standing, "declaration": document}),
                    }, action="reconcile", ts=ts,
                )
            if expected_revision is not None:
                raise TaskError("standing charter is absent; expected revision cannot be reconciled")
            return self._enroll_standing(conn, title, document, canonical_repo, ts)

    def _enroll_standing(
        self, conn: sqlite3.Connection, title: str, document: dict, canonical_repo: str, ts: float,
    ) -> Task:
        self._require_agent_backed_repo(canonical_repo)
        task_id = uuid.uuid4().hex
        standing = {
            "version": 1, "declaration": document, "declaration_generation": 1,
            "lifecycle": "active", "accepted_materialization": None,
        }
        conn.execute(
            "INSERT INTO tasks(id, title, repo, kind, source, status, standing_charter,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, title, canonical_repo, STANDING_KIND, STANDING_KIND, Status.QUEUED,
             _encoded(standing), ts, ts),
        )
        task = self._fetch(conn, task_id)
        return self._change_standing(conn, task, {}, action="enroll", ts=ts)

    def _require_standing_admission_open(
        self, conn: sqlite3.Connection, task: Task, ts: float,
    ) -> None:
        self.require_worker_admission()
        self._require_standing_source(conn, task.repo)
        require_execution_admission(conn, task.id)
        if task.standing_charter["lifecycle"] != "active":
            raise TaskError("standing charter is not open for reservation")
        if task.hold_reason is not None:
            raise TaskError("standing charter is held")
        if task.not_before > ts:
            raise TaskError("standing charter is not ready for reservation")

    def _require_standing_reservation_open(
        self, conn: sqlite3.Connection, task: Task, ts: float,
    ) -> None:
        self._require_standing_admission_open(conn, task, ts)
        if task.owner_session_id is not None:
            raise TaskError("standing charter already has a native attachment")

    def reserve_standing_charter(
        self, task_id: str, workspace: AssignmentWorkspace, *,
        expected_revision: int, now: float | None = None,
    ) -> Task:
        """Retain one workspace assignment, not acceptance or permission to launch."""
        if not isinstance(workspace, AssignmentWorkspace):
            raise TaskError("standing reservation requires its original workspace identity")
        if type(expected_revision) is not int or expected_revision < 1:
            raise TaskError("standing reservation requires a positive revision")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None or task.kind != STANDING_KIND:
                raise TaskError("operation requires a standing charter task")
            self._require_standing_reservation_open(conn, task, ts)
            if task.status == Status.CLAIMED:
                retained = latest_relationship(conn, task_id)
                request = (
                    json.loads(retained["task_relationship"])["request"]
                    if retained is not None else None
                )
                if (
                    request is None or task.owner != workspace.worker_id
                    or task.standing_revision != expected_revision + 1
                    or request["workspace"] != asdict(workspace)
                    or request["generation"] != task.generation
                    or request["reservation_key"] is not None
                    or task.standing_charter.get("reservation") != {
                        "expected_revision": expected_revision,
                        "assignment_event_id": request["event_id"],
                    }
                ):
                    raise TaskError("standing reservation conflicts with its retained assignment")
                event_id = request["event_id"]
            else:
                self._require_standing(conn, task_id, expected_revision)
                if task.status != Status.QUEUED or task.owner is not None:
                    raise TaskError("standing reservation requires an unassigned queued task")
                require_task_recovery_safe(self, conn, task_id)
                event_id = self._retain_task_relationship(
                    conn, task, workspace, worker_id=workspace.worker_id,
                    generation=task.generation + 1, ts=ts,
                )
                task = self._change_standing(
                    conn, task, {
                        "status": Status.CLAIMED, "owner": workspace.worker_id,
                        "generation": task.generation + 1, "claimed_at": ts,
                        "lease_expires_at": None, "last_seen_at": ts,
                        "last_liveness": None, "activity": None, "attempts": task.attempts + 1,
                        "standing_charter": _encoded({
                            **task.standing_charter,
                            "reservation": {
                                "expected_revision": expected_revision,
                                "assignment_event_id": event_id,
                            },
                        }),
                    }, action="reserve", ts=ts,
                )
        self._acknowledge_relationship_claim(
            task_id, workspace.worker_id, task.generation, event_id=event_id,
        )
        with self._connect() as conn:
            conn.execute("BEGIN")
            current = self._require_standing(conn, task_id, expected_revision + 1)
            self._require_standing_reservation_open(conn, current, self._now(now))
            if (
                current.status != Status.CLAIMED or current.owner != workspace.worker_id
                or current.generation != task.generation
                or latest_relationship(conn, task_id)["id"] != event_id
            ):
                raise TaskError("standing reservation changed during relationship reconciliation")
        self._notify_owned_transition()
        return current

    def _standing_relationship(
        self, conn: sqlite3.Connection, task: Task, workspace: AssignmentWorkspace,
    ) -> dict:
        row = latest_relationship(conn, task.id)
        try:
            retained = json.loads(row["task_relationship"]) if row is not None else {}
            request = retained["request"]
            valid = (
                retained["state"] == "bound" and request["event_id"] == row["id"]
                and request["task_id"] == task.id and request["workspace"] == asdict(workspace)
                and current_binding(retained)["generation"] == task.generation
                and request["reservation_key"] is None
                and _matches_receipt(retained["receipt"], request, phase="bound")
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise TaskError("standing assignment has no confirmed original relationship")
        require_relationship(conn, task.id)
        return retained

    def accept_standing_charter(
        self, task_id: str, workspace: AssignmentWorkspace, *,
        expected_revision: int, expected_generation: int,
        owner_session_id: str, expected_head_revision: int,
        expected_owner_session_id: str | None = None, now: float | None = None,
    ) -> Task:
        """Accept one already-existing native head, never launch or replace it."""
        if not isinstance(workspace, AssignmentWorkspace) or any(
            type(value) is not int or value < 1
            for value in (expected_revision, expected_generation, expected_head_revision)
        ):
            raise TaskError("standing acceptance requires its workspace and positive fencing revisions")
        if (
            not isinstance(owner_session_id, str) or not owner_session_id
            or owner_session_id != owner_session_id.strip() or len(owner_session_id) > 1024
        ):
            raise TaskError("standing acceptance requires an exact native session")
        succeeding = expected_owner_session_id is not None
        if succeeding and (
            not isinstance(expected_owner_session_id, str) or not expected_owner_session_id
            or expected_owner_session_id != expected_owner_session_id.strip()
            or len(expected_owner_session_id) > 1024 or expected_owner_session_id == owner_session_id
        ):
            raise TaskError("standing succession requires its exact previous native session")
        head = {"session_id": owner_session_id, "revision": expected_head_revision}
        if succeeding:
            head["predecessor_session_id"] = expected_owner_session_id
        accepted_generation = expected_generation + int(succeeding)

        def inspect(conn: sqlite3.Connection) -> tuple[Task, dict, bool]:
            task = self._fetch(conn, task_id)
            if task is None or task.kind != STANDING_KIND:
                raise TaskError("operation requires a standing charter task")
            if task.owner != workspace.worker_id:
                raise TaskError("standing assignment ownership incarnation changed")
            relationship = self._standing_relationship(conn, task, workspace)
            accepted = task.standing_charter["accepted_materialization"]
            if (
                task.status == Status.STARTED and task.owner_session_id == owner_session_id
                and task.generation == accepted_generation
                and task.standing_revision == expected_revision + 1 and isinstance(accepted, dict)
                and accepted.get("expected_revision") == expected_revision
                and accepted.get("expected_generation") == expected_generation
                and accepted.get("generation") == accepted_generation
                and accepted.get("head") == head
                and accepted.get("request_id") == relationship["request"]["request_id"]
                and accepted.get("relationship_ref") == relationship["receipt"]["ref"]
            ):
                return task, relationship, True
            self._require_standing(conn, task_id, expected_revision)
            if task.generation != expected_generation:
                raise TaskError("standing assignment ownership incarnation changed")
            if succeeding:
                self._require_standing_admission_open(conn, task, self._now(now))
                if (
                    task.status != Status.STARTED or task.owner_session_id != expected_owner_session_id
                    or not isinstance(accepted, dict) or accepted.get("generation") != task.generation
                    or not isinstance(accepted.get("head"), dict)
                    or accepted["head"].get("session_id") != expected_owner_session_id
                    or type(accepted["head"].get("revision")) is not int
                    or accepted["head"]["revision"] >= expected_head_revision
                    or accepted.get("request_id") != relationship["request"]["request_id"]
                    or accepted.get("relationship_ref") != relationship["receipt"]["ref"]
                ):
                    raise TaskError("standing succession requires its previously accepted native owner")
            else:
                self._require_standing_reservation_open(conn, task, self._now(now))
                if task.status != Status.CLAIMED:
                    raise TaskError("standing acceptance requires its reserved assignment")
            return task, relationship, False

        with self._connect() as conn:
            conn.execute("BEGIN")
            task, retained, replayed = inspect(conn)
            if replayed:
                return task
        admit = getattr(self._relationship_writer, "admit_standing", None)
        if not callable(admit):
            raise TaskError("selected relationship owner cannot admit a standing attachment")
        with admit(retained["request"], task.standing_charter["declaration"], head) as admission:
            if not isinstance(admission, dict) or any(
                admission.get(key) != value for key, value in {
                    "version": 1, "assignment_event_id": retained["request"]["event_id"],
                    "request_id": retained["request"]["request_id"],
                    "generation": retained["request"]["generation"],
                    "head": head, "relationship_ref": retained["receipt"]["ref"],
                    "materialization_ref": task.standing_charter["declaration"]["materialization_ref"],
                    "source_revision": task.standing_charter["declaration"]["source_revision"],
                    "materialization_revision":
                        task.standing_charter["declaration"]["expected_materialization_revision"],
                }.items()
            ):
                raise TaskError("standing admission differs from the original reserved assignment")
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current, relationship, replayed = inspect(conn)
                if replayed:
                    return current
                if relationship != retained:
                    raise TaskError("standing relationship changed during native admission")
                from .acquisition_receipts import validate_start

                validate_start(conn, current, owner_session_id, None)
                ts = self._now(now)
                if succeeding:
                    continue_owner_session(conn, current, owner_session_id, standing_succession=True)
                result = self._change_standing(
                    conn, current, {
                        "status": Status.STARTED, "owner_session_id": owner_session_id,
                        "generation": accepted_generation,
                        "started_at": ts, "last_seen_at": ts,
                        "activity": None, "activity_updated_at": ts,
                        "standing_charter": _encoded({
                            **current.standing_charter,
                            "accepted_materialization": {
                                **admission, "expected_revision": expected_revision,
                                "expected_generation": expected_generation,
                                "assignment_generation": admission["generation"],
                                "generation": accepted_generation,
                            },
                        }),
                    }, action="succeed" if succeeding else "accept", ts=ts,
                )
                self._record_attachment(
                    conn, task_id, ts=ts, old_session_id=current.owner_session_id,
                    new_session_id=owner_session_id,
                    worktree_id=workspace.worktree, machine=workspace.machine,
                    detach_reason="standing succession" if succeeding else "standing acceptance",
                )
        self._notify_owned_transition()
        return result

    def release_standing_attachment(
        self, task_id: str, workspace: AssignmentWorkspace, *,
        expected_revision: int, expected_generation: int,
        owner_session_id: str, expected_conclusion_revision: int, now: float | None = None,
    ) -> Task:
        """Detach an exactly concluded owner, then settle its original relationship."""
        if not isinstance(workspace, AssignmentWorkspace) or any(
            type(value) is not int or value < 1
            for value in (expected_revision, expected_generation, expected_conclusion_revision)
        ):
            raise TaskError("standing release requires its workspace and positive fencing revisions")
        if (
            not isinstance(owner_session_id, str) or not owner_session_id
            or owner_session_id != owner_session_id.strip() or len(owner_session_id) > 1024
        ):
            raise TaskError("standing release requires an exact native session")
        conclusion = {"session_id": owner_session_id, "revision": expected_conclusion_revision}

        def inspect(conn: sqlite3.Connection) -> tuple[Task, dict, dict, bool]:
            task = self._fetch(conn, task_id)
            if task is None or task.kind != STANDING_KIND:
                raise TaskError("operation requires a standing charter task")
            row = latest_relationship(conn, task_id)
            relationship = json.loads(row["task_relationship"]) if row is not None else {}
            request = relationship.get("request", {})
            receipt = relationship.get("receipt", {})
            if (
                row is None or request.get("event_id") != row["id"]
                or request.get("task_id") != task_id or request.get("workspace") != asdict(workspace)
                or request.get("reservation_key") is not None
                or current_binding(relationship).get("generation") != expected_generation
                or relationship.get("state") not in {"bound", "closed"}
                or not _matches_receipt(receipt, request, phase=relationship["state"])
            ):
                raise TaskError("standing release requires its original relationship")
            release = {
                "expected_revision": expected_revision, "expected_generation": expected_generation,
                "owner_session_id": owner_session_id, "conclusion": conclusion,
                "assignment_event_id": request["event_id"], "request_id": request["request_id"],
                "relationship_ref": receipt["ref"],
            }
            if (
                task.standing_revision == expected_revision + 1
                and task.generation == expected_generation + 1 and task.status == Status.QUEUED
                and task.owner is None and task.owner_session_id is None
                and task.standing_charter.get("release") == release
            ):
                return task, relationship, release, True
            self._require_standing(conn, task_id, expected_revision)
            relationship = self._standing_relationship(conn, task, workspace)
            accepted = task.standing_charter["accepted_materialization"]
            if (
                task.status != Status.STARTED or task.owner != workspace.worker_id
                or task.owner_session_id != owner_session_id or task.generation != expected_generation
                or task.standing_charter["lifecycle"] not in {"active", "retiring"}
                or not isinstance(accepted, dict) or accepted.get("generation") != expected_generation
                or not isinstance(accepted.get("head"), dict)
                or accepted["head"].get("session_id") != owner_session_id
                or type(accepted["head"].get("revision")) is not int
                or accepted["head"]["revision"] >= expected_conclusion_revision
                or accepted.get("request_id") != request["request_id"]
                or accepted.get("relationship_ref") != receipt["ref"]
            ):
                raise TaskError("standing release requires its previously accepted native owner")
            return task, relationship, release, False

        with self._connect() as conn:
            conn.execute("BEGIN")
            result, retained, release, replayed = inspect(conn)
        if not replayed:
            observe = getattr(self._relationship_writer, "inspect_conclusion", None)
            if not callable(observe):
                raise TaskError("selected relationship owner cannot confirm a native conclusion")
            receipt = observe(copy.deepcopy(retained["request"]), copy.deepcopy(conclusion))
            if (
                not _matches_receipt(receipt, retained["request"], phase="bound")
                or receipt.get("ref") != retained["receipt"]["ref"]
                or receipt.get("conclusion") != conclusion
                or type(receipt["conclusion"].get("revision")) is not int
            ):
                raise TaskError("native conclusion differs from the original standing attachment")
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current, relationship, release, replayed = inspect(conn)
                if replayed:
                    result = current
                else:
                    if relationship != retained:
                        raise TaskError("standing relationship changed during native conclusion")
                    ts = self._now(now)
                    standing = {
                        **current.standing_charter, "accepted_materialization": None, "release": release,
                    }
                    standing.pop("reservation", None)
                    result = self._change_standing(
                        conn, current, {
                            "status": Status.QUEUED, "owner": None, "owner_session_id": None,
                            "generation": expected_generation + 1, "claimed_at": None,
                            "started_at": None, "lease_expires_at": None, "last_seen_at": None,
                            "last_liveness": None, "activity": None, "activity_updated_at": ts,
                            "standing_charter": _encoded(standing),
                        }, action="release", ts=ts, unassigned_worker=workspace.worker_id,
                    )
                    self._record_attachment(
                        conn, task_id, ts=ts, old_session_id=owner_session_id, new_session_id=None,
                        worktree_id=workspace.worktree, machine=workspace.machine,
                        detach_reason="standing release",
                    )
        try:
            settled = self.reconcile_task_relationship(
                task_id, event_id=release["assignment_event_id"],
            )
            if (
                settled["state"] != "closed" or settled["request"] != retained["request"]
                or settled["receipt"]["ref"] != release["relationship_ref"]
            ):
                raise TaskError("original relationship has not closed")
        except Exception as exc:
            raise TaskError(
                "standing attachment released; original relationship close unresolved and retained"
            ) from exc
        self._notify_owned_transition()
        return result

    def retire_standing_charter(
        self, task_id: str, *, expected_revision: int, now: float | None = None,
    ) -> Task:
        """Close admission, retaining any occupant and all unresolved obligations."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_standing(conn, task_id, expected_revision)
            if task.standing_charter["lifecycle"] != "active":
                return task
            return self._change_standing(
                conn, task, {"standing_charter": _encoded({
                    **task.standing_charter, "lifecycle": "retiring",
                })}, action="retire", ts=self._now(now),
            )

    def settle_standing_retirement(
        self, task_id: str, *, expected_revision: int, now: float | None = None,
    ) -> Task:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_standing(conn, task_id, expected_revision)
            if task.standing_charter["lifecycle"] == "retired":
                return task
            if task.standing_charter["lifecycle"] != "retiring":
                raise TaskError("standing retirement must be requested before settlement")
            if task.owner is not None or task.owner_session_id is not None:
                raise TaskError("standing attachment must be settled through its native owner")
            require_task_recovery_safe(self, conn, task_id)
            return self._change_standing(
                conn, task, {
                    "status": Status.SUSPENDED,
                    "standing_charter": _encoded({**task.standing_charter, "lifecycle": "retired"}),
                }, action="retired", ts=self._now(now),
            )

    def reenroll_standing_charter(
        self, task_id: str, *, expected_revision: int, now: float | None = None,
    ) -> Task:
        """Explicitly reopen the same tombstone in a new declaration generation."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._require_standing(conn, task_id, expected_revision)
            self._require_standing_source(conn, task.repo)
            standing = task.standing_charter
            if standing["lifecycle"] != "retired":
                raise TaskError("standing re-enrollment requires settled retirement")
            require_task_recovery_safe(self, conn, task_id)
            return self._change_standing(
                conn, task, {
                    "status": Status.QUEUED,
                    "standing_charter": _encoded({
                        **standing, "lifecycle": "active", "accepted_materialization": None,
                        "declaration_generation": standing["declaration_generation"] + 1,
                    }),
                }, action="reenroll", ts=self._now(now),
            )
