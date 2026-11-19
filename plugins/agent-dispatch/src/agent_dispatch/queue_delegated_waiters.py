"""Delegation identity checks for the existing prepare/arm/finish transactions."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from .delegated_waiter_contract import (
    validate_delegation, validate_event, validate_retained_delegation,
)
from .queue_records import Status, TaskError
from .queue_replay_waiters import ReplayWaiterProgressMixin
from .queue_common import RunWaiterWakeOperation, Task
from .queue_feed_preparation import (
    PREPARATION_PROTOCOL, REGISTRATION_PROTOCOL, FeedWaiterAdmission,
    require_feed_creation_deadline,
)
from .queue_run_waiter_transition_cleanup import CLAIM_RELEASE_SENDER
from .repository_feed_waiter_contract import validate_feed_delegation


class DelegatedWaiterBindingMixin(ReplayWaiterProgressMixin):
    if TYPE_CHECKING:
        prepare_run_waiter: Callable[..., dict[str, Any]]
        _now: Callable[[float | None], float]

    repository_feed_preparation_protocol = PREPARATION_PROTOCOL
    repository_feed_registration_protocol = REGISTRATION_PROTOCOL
    _check_feed_preparation_deadline = staticmethod(require_feed_creation_deadline)

    def _commit_run_waiter_preparation(
        self, conn: sqlite3.Connection, delegation: dict[str, Any] | None,
        task_id: str, generation: int, creation_cutoff: int | None, now: float | None,
    ) -> None:
        self._store_replay_intent(conn, delegation, task_id, generation)
        if creation_cutoff is not None:
            if delegation is None or "feed" not in delegation:
                raise TaskError("repository-feed creation cutoff requires its original feed")
            require_feed_creation_deadline(delegation, creation_cutoff, self._now(now))
        conn.execute("COMMIT")

    def prepare_repository_feed_waiter(
        self, task_id: str, *, admission: FeedWaiterAdmission, delegation: dict[str, Any],
        worker_id: str, resume_worktree: str, reason: str,
        host: str | None = None, now: float | None = None,
        creation_cutoff: int | None = None,
    ) -> dict[str, Any]:
        """Native preparation after the embedding negotiates actual service support."""
        selected = validate_feed_delegation(delegation)
        if not isinstance(admission, FeedWaiterAdmission) or admission.task_id != task_id:
            raise TaskError("repository-feed preparation requires its original task basis")
        return self.prepare_run_waiter(
            task_id, worker_id=worker_id, resume_worktree=resume_worktree,
            reason=reason, host=host, command=[], kind="delegated",
            delegation=selected, now=now, _feed_admission=admission,
            _feed_creation_cutoff=creation_cutoff,
        )

    @staticmethod
    def _run_waiter_matches_task(waiter: dict[str, Any], task: Task | None) -> bool:
        return bool(
            task is not None
            and task.status == Status.SUSPENDED
            and task.owner == waiter["owner"]
            and task.generation == int(waiter["task_generation"])
            and task.owner_session_id == waiter.get("owner_session_id")
        )

    def _run_waiter_wake_is_current(
        self,
        conn: sqlite3.Connection,
        task: Task | None,
        wake: RunWaiterWakeOperation,
    ) -> bool:
        if wake.sender == CLAIM_RELEASE_SENDER:
            return task is not None and task.generation == wake.task_generation
        if not (
            task is not None
            and task.status == Status.SUSPENDED
            and task.owner == wake.owner
            and task.generation == wake.task_generation
            and task.owner_session_id == wake.owner_session_id
        ):
            return False
        row = conn.execute("SELECT MAX(generation) AS generation FROM run_waiters WHERE task_id = ?", (wake.task_id,)).fetchone()
        latest_generation = int((row["generation"] if row is not None else 0) or 0)
        return latest_generation == int(wake.waiter_generation)

    def run_waiter_coordinator_id(self) -> str:
        with self._connect() as conn:
            return str(conn.execute("SELECT id FROM run_waiter_identity").fetchone()["id"])

    def find_delegated_preparation(
        self, task_id: str, preparation_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._find_delegated_preparation(conn, task_id, preparation_id)

    def _find_delegated_preparation(
        self, conn: sqlite3.Connection, task_id: str, preparation_id: str,
    ) -> dict[str, Any] | None:
        for row in conn.execute("SELECT * FROM run_waiters WHERE task_id = ?", (task_id,)):
            value = self._run_waiter_from_row(row)
            if value["delegation"] and value["delegation"]["preparation_id"] == preparation_id:
                return value
        return None

    def _validate_waiter_preparation(
        self, kind: str, delegation: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if kind not in ("process", "delegated") or (kind == "process" and delegation is not None):
            raise TaskError("invalid run waiter kind")
        if kind == "delegated":
            delegation = validate_retained_delegation(delegation)
            if delegation["coordinator_id"] != self.run_waiter_coordinator_id():
                raise TaskError("delegated waiter belongs to another coordinator")
        return delegation

    @staticmethod
    def _validate_waiter_arm(
        waiter: dict[str, Any], *, kind: str, delegation: dict[str, Any] | None,
        pid: int | None, host: str | None, start_token: str | None,
    ) -> None:
        if waiter["kind"] != kind:
            raise TaskError("run waiter kind mismatch")
        if kind == "delegated":
            value = validate_retained_delegation(delegation, armed=True)
            if {k: v for k, v in value.items() if k != "registration_id"} != waiter["delegation"]:
                raise TaskError("delegated waiter identity mismatch")
        elif kind != "process" or not pid or not host or not start_token:
            raise TaskError("invalid process waiter identity")

    @staticmethod
    def _delegated_finish_is_replay(
        waiter: dict[str, Any], delegation: dict[str, Any] | None,
        event: dict[str, Any] | None,
    ) -> bool:
        if delegation is None:
            if waiter["kind"] != "process":
                raise TaskError("run waiter kind mismatch")
            return False
        validate_delegation(delegation)
        event = validate_event(event, delegation)
        stored = waiter["delegation"]
        if waiter["kind"] != "delegated" or not stored:
            raise TaskError("run waiter kind mismatch")
        if {k: v for k, v in stored.items() if k != "registration_id"} != delegation:
            raise TaskError("delegated waiter identity mismatch")
        if stored.get("registration_id") not in (None, event["registration_id"]):
            raise TaskError("watch registration mismatch")
        if waiter["state"] != "active":
            if waiter.get("event_id") not in (None, event["event_id"]):
                raise TaskError("watch event mismatch")
            return True
        if stored.get("registration_id") is None:
            raise TaskError("delegated waiter has no registration")
        return False
