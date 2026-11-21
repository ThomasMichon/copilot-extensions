"""Queue-backed fixtures shared by the supervisor behavioral suites."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Literal, TypedDict, TypeVar

import pytest
from fastapi.testclient import TestClient
from typing_extensions import Required, Unpack

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import SpawnReservation, Status, TaskError
from tests._helpers import TEST_REPO, RepoDefaultingQueue, required
from tests.test_pool_placement_client import connected

Record = dict[str, object]
Records = list[Record]


class ProbeOptions(TypedDict, total=False):
    local_body_verdict_fn: Callable[[str], str]
    fleet_verdict_fn: Callable[[str, str], str]
    verdict_fn: Callable[[str, str | None, str | None], str]
    worktree_directory_present_fn: Callable[[str, str | None], bool | None]


class WorkspaceOptions(TypedDict, total=False):
    ownership: str
    creating_host: str | None
    execution_machine: str | None
    driver: str | None
    allocation_project: str | None
    worktree_path: str | None
    creation_nonce: str | None
    pool_creation_nonce: str | None
    worktree_writer_requirement: Record | None
    now: float | None


class ListOptions(TypedDict, total=False):
    target_machine: str | None
    target_repo: str | None
    label: str | None
    evaluator_ref: str | None
    require_verification: bool | None
    source: str | None
    origin_ref: str | None
    exclusive_key: str | None
    category: str | None
    standing_parent_id: str | None
    kind: str | None


class CreateOptions(TypedDict, total=False):
    prompt: str
    category: str
    requires: Sequence[str] | None
    excludes: Sequence[str] | None
    affinity: dict[str, str] | None
    labels: Sequence[str] | None
    payload_ref: str | None
    payload_inline: str | None
    target_machine: str | None
    target_worktree: str | None
    target_repo: str | None
    source: str | None
    origin_ref: str | None
    evaluator_ref: str | None
    exclusive_key: str | None
    supersede_exclusive_key: bool
    dedup_key: str | None
    producer_scope: Mapping[str, object] | None
    producer_id: str | None
    producer_generation: int | None
    producer_capability: str | None
    producer_request_id: str | None
    goal: str | None
    done_criteria: str | None
    not_before: float
    require_verification: bool
    standing_parent_id: str | None
    claim_as: str | None
    now: float | None


class AllocationOptions(TypedDict, total=False):
    worktree_writer_requirement: Record | None
    cohort_transport: str | None


class DeferOptions(TypedDict, total=False):
    now: float | None
    expected_generation: int | None
    startup_revision: int | None
    cooling_token: str | None


class CoolingOptions(TypedDict, total=False):
    startup_revision: Required[int]
    session_handle: Required[str]
    state: Required[str]
    now: float | None


class OwnerResultQueue(RepoDefaultingQueue):
    """Fixture allocations carry synthetic owner facts unless explicitly unknown."""

    def record_spawn_worktree(
        self, key: str, worktree: str, **kwargs: Unpack[WorkspaceOptions]
    ) -> SpawnReservation:
        from agent_dispatch import embody

        reservation = required(self.get_reservation(key))
        task = required(self.get(reservation.task_id))
        kwargs.setdefault("allocation_project", embody.project_for_task(asdict(task)))
        kwargs.setdefault("worktree_path", f"/fixture/{worktree}")
        return super().record_spawn_worktree(key, worktree, **kwargs)


class QueueBackedClient:
    """A DispatchClient stand-in backed by a real TaskQueue (dicts in/out)."""

    def __init__(self, queue: TaskQueue) -> None:
        self._q = queue

    def __enter__(self) -> QueueBackedClient:
        return self

    def __exit__(self, *_exc: object) -> Literal[False]:
        return False

    def list(
        self,
        *,
        repo: str | None = None,
        status: str | Sequence[str] | None = None,
        limit: int = 200,
        **kw: Unpack[ListOptions],
    ) -> Records:
        return [asdict(t) for t in self._q.list(repo=repo, status=status, limit=limit, **kw)]

    def create(
        self,
        title: str,
        *,
        repo: str | None = None,
        proposed: bool = False,
        **kwargs: Unpack[CreateOptions],
    ) -> Record:
        status = Status.PROPOSED if proposed else Status.QUEUED
        return asdict(self._q.create(title, repo=repo or TEST_REPO, status=status, **kwargs))

    def get(self, task_id: str) -> Record:
        t = self._q.get(task_id)
        if t is None:
            raise DispatchError(404, "no such task")
        return asdict(t)

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | Sequence[str] | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        native_release_pending: bool | None = None,
        pool_accountable: bool = False,
        resume_requested: bool | None = None,
        limit: int = 200,
    ) -> Records:
        states = state.split(",") if isinstance(state, str) else state
        rows: list[SpawnReservation] = self._q.list_reservations(
            task_id=task_id,
            state=states,
            repo=repo,
            label=label,
            conclusion_state=conclusion_state,
            native_release_pending=native_release_pending,
            pool_accountable=pool_accountable,
            resume_requested=resume_requested,
            limit=limit,
        )
        return [
            {**asdict(r), **({"pool_accountable": True} if pool_accountable else {})} for r in rows
        ]

    def reserve_spawn(
        self,
        task_id: str,
        *,
        reserved_by: str | None = None,
        allow_suspended_reembodiment: bool = False,
        allocation_revision: int | None = None,
        driver: str | None = None,
    ) -> Record:
        res, ok = self._q.reserve_spawn(
            task_id,
            reserved_by=reserved_by,
            allow_suspended_reembodiment=allow_suspended_reembodiment,
            allocation_revision=allocation_revision,
            driver=driver,
        )
        return {"reserved": ok, "reservation": asdict(res)}

    def record_spawn(
        self,
        key: str,
        *,
        session_handle: str | None = None,
        worktree: str | None = None,
        startup_complete: bool | None = None,
        startup_revision: int | None = None,
        ordinary_redrive: Record | None = None,
    ) -> Record:
        try:
            return asdict(
                self._q.record_spawn(
                    key,
                    session_handle=session_handle,
                    worktree=worktree,
                    startup_complete=startup_complete,
                    startup_revision=startup_revision,
                    ordinary_redrive=ordinary_redrive,
                )
            )
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    def fence_spawn_startup(self, key: str, *, startup_revision: int) -> Record:
        try:
            return asdict(self._q.fence_spawn_startup(key, startup_revision=startup_revision))
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    def get_reservation(self, key: str) -> Record:
        from agent_dispatch.ordinary_agent_storage import reservation_native_facts

        reservation = self._q.get_reservation(key)
        if reservation is None:
            raise DispatchError(404, f"no such reservation: {key}")
        return {**asdict(reservation), **reservation_native_facts(self._q, key)}

    def attachments(self, task_id: str) -> Records:
        return [asdict(item) for item in self._q.attachment_history(task_id)]

    def record_spawn_worktree(
        self, key: str, worktree: str, **kwargs: Unpack[WorkspaceOptions]
    ) -> Record:
        return asdict(self._q.record_spawn_worktree(key, worktree, **kwargs))

    def record_spawn_allocation(
        self, key: str, project: str, **kwargs: Unpack[AllocationOptions]
    ) -> Record:
        return asdict(self._q.record_spawn_allocation(key, project, **kwargs))

    def fail_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        force: bool = False,
        confirmed_absent: bool = False,
        release_requested: bool = False,
    ) -> Record:
        return asdict(
            self._q.fail_spawn(
                key,
                detail=detail,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
                claim_token=claim_token,
                force=force,
                confirmed_absent=confirmed_absent,
                release_requested=release_requested,
            )
        )

    def defer_spawn(
        self, key: str, *, detail: str | None = None, **kwargs: Unpack[DeferOptions]
    ) -> Record:
        try:
            return asdict(self._q.defer_spawn(key, detail=detail, **kwargs))
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    def claim_spawn_cooling(self, key: str, **kwargs: Unpack[CoolingOptions]) -> Record:
        try:
            return asdict(self._q.claim_spawn_cooling(key, **kwargs))
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    def request_spawn_release(
        self,
        key: str,
        *,
        detail: str | None = None,
        disposition: str = "failed",
        session_handle: str | None = None,
        worktree: str | None = None,
        finished_startup_revision: int | None = None,
    ) -> Record:
        return asdict(
            self._q.request_spawn_release(
                key,
                detail=detail,
                disposition=disposition,
                session_handle=session_handle,
                worktree=worktree,
                finished_startup_revision=finished_startup_revision,
            )
        )

    def retire_spawn(
        self,
        key: str,
        *,
        exact_absence: bool,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
    ) -> Record:
        return asdict(
            self._q.retire_spawn(
                key,
                exact_absence=exact_absence,
                detail=detail,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
            )
        )

    def record_cold(
        self,
        key: str,
        *,
        release_exclusive: bool = False,
        startup_revision: int | None = None,
        cooling_token: str | None = None,
    ) -> Record:
        try:
            return asdict(
                self._q.record_cold(
                    key,
                    release_exclusive=release_exclusive,
                    startup_revision=startup_revision,
                    cooling_token=cooling_token,
                )
            )
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    def settle_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        release_requested: bool = False,
    ) -> Record:
        return asdict(
            self._q.settle_spawn(
                key,
                detail=detail,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
                claim_token=claim_token,
                release_requested=release_requested,
            )
        )

    def record_spawn_conclusion(
        self,
        key: str,
        *,
        conclusion_state: str,
        conclusion_detail: str,
        detail: str | None = None,
        claim_token: str | None = None,
        claimant_machine: str | None = None,
    ) -> Record:
        return asdict(
            self._q.record_spawn_conclusion(
                key,
                conclusion_state=conclusion_state,
                conclusion_detail=conclusion_detail,
                detail=detail,
                claim_token=claim_token,
                claimant_machine=claimant_machine,
            )
        )

    def claim_spawn_conclusion_retry(
        self, key: str, *, claimant_machine: str | None = None
    ) -> Record:
        reservation, claimed, claim_token = self._q.claim_spawn_conclusion_retry(
            key, claimant_machine=claimant_machine
        )
        return {
            "claimed": claimed,
            "claim_token": claim_token,
            "reservation": asdict(reservation),
        }

    def validate_spawn_conclusion_claim(
        self, key: str, claim_token: str, *, claimant_machine: str | None = None
    ) -> Record:
        return asdict(
            self._q.validate_spawn_conclusion_claim(
                key, claim_token, claimant_machine=claimant_machine
            )
        )

    def heartbeat(self, task_id: str, worker_id: str) -> Record:
        return asdict(self._q.heartbeat(task_id, worker_id))

    def set_activity(
        self,
        task_id: str,
        activity: str | None,
        *,
        reservation_key: str,
        observed_at: float | None = None,
        admission_fence: Record | None = None,
    ) -> Record:
        return asdict(
            self._q.set_activity(
                task_id,
                activity,
                reservation_key=reservation_key,
                observed_at=observed_at,
                admission_fence=admission_fence,
            )
        )

    def bind_owner_session(
        self,
        task_id: str,
        worker_id: str,
        owner_session_id: str,
        *,
        expected_generation: int | None = None,
    ) -> Record:
        return asdict(
            self._q.bind_owner_session(
                task_id,
                worker_id,
                owner_session_id,
                expected_generation=expected_generation,
            )
        )

    def yield_task(
        self,
        task_id: str,
        worker_id: str,
        *,
        note: str | None = None,
        exclude: str | None = None,
        release_spawn: bool = True,
    ) -> Record:
        return asdict(
            self._q.yield_task(
                task_id,
                worker_id,
                note=note,
                exclude=exclude,
                release_spawn=release_spawn,
            )
        )

    def abandon(
        self,
        task_id: str,
        *,
        worker_id: str | None = None,
        permitted: bool = False,
        reason: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
    ) -> Record:
        return asdict(
            self._q.abandon(
                task_id,
                worker_id=worker_id,
                permitted=permitted,
                reason=reason,
                expected_status=expected_status,
                expected_generation=expected_generation,
                expected_owner_session_id=expected_owner_session_id,
            )
        )

    def confirm(
        self,
        task_id: str,
        *,
        actor: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
    ) -> Record:
        return asdict(
            self._q.confirm(
                task_id,
                actor=actor,
                expected_status=expected_status,
                expected_generation=expected_generation,
            )
        )

    def reopen_completed(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        steer_fields: Record | None = None,
        sender: str | None = None,
        expected_status: str | None = None,
        expected_generation: int | None = None,
    ) -> Record:
        return asdict(
            self._q.reopen_completed(
                task_id,
                reason=reason,
                steer_fields=steer_fields,
                sender=sender,
                expected_status=expected_status,
                expected_generation=expected_generation,
            )
        )

    def release(self, task_id: str, worker_id: str, *, reason: str | None = None) -> Record:
        return asdict(self._q.release_suspended(task_id, worker_id, reason=reason))

    def suspend(self, task_id: str, worker_id: str, *, reason: str) -> Record:
        return asdict(self._q.suspend(task_id, worker_id, reason=reason))

    def resume(
        self,
        task_id: str,
        worker_id: str,
        *,
        wake: bool = True,
        message: str | None = None,
        adopt_session: bool = False,
        reuse_session: bool = False,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
    ) -> Record:
        try:
            return asdict(
                self._q.resume(
                    task_id,
                    worker_id,
                    wake_requested=wake,
                    wake_message=message,
                    adopt_owner_session_id=(expected_owner_session_id if adopt_session else None),
                    reuse_session=reuse_session,
                    expected_owner_session_id=expected_owner_session_id,
                    expected_generation=expected_generation,
                )
            )
        except TaskError as exc:
            raise DispatchError(409, str(exc)) from exc

    def progress_log(self, task_id: str) -> Records:
        return self._q.progress_log(task_id)


@pytest.fixture
def q(tmp_path: Path) -> OwnerResultQueue:
    return OwnerResultQueue(tmp_path / "tasks.db")


@pytest.fixture
def client(q: OwnerResultQueue) -> QueueBackedClient:
    return QueueBackedClient(q)


@pytest.fixture
def supervisor_api(q: OwnerResultQueue) -> Iterator[TestClient]:
    app = create_app(q, token="data", control_token="control", enable_mcp=False,
                     verification_interval=0)
    with TestClient(app) as api:
        yield api


@pytest.fixture
def supervisor_http(supervisor_api: TestClient) -> Iterator[DispatchClient]:
    with connected(supervisor_api, []) as client:
        yield client


class RecordingSpawn:
    allocation_interface: str
    allocation_project: str
    requires_reusable_worktree: bool
    allocation_project_for: Callable[[Record], str]
    allocation_no_pair: bool

    def __init__(self, handle: Record | None = None) -> None:
        self.handle = handle
        self.calls: list[str] = []

    def __call__(self, task: Record) -> tuple[bool, Record]:
        task_id = task["id"]
        assert isinstance(task_id, str)
        self.calls.append(task_id)
        return True, self.handle or {"session": "sess-1", "worktree": "wt-1"}


def _ok_spawn(handle: Record | None = None) -> RecordingSpawn:
    return RecordingSpawn(handle)


_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


def record_result(calls: list[_Item], call: _Item, result: _Result) -> _Result:
    """Record an injected probe or operation and return its specified result."""
    calls.append(call)
    return result
