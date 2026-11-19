"""Service negotiation cannot race past the native task's preparation guards."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import copy
from dataclasses import replace
import importlib
import json
from pathlib import Path
import time
import sys
from typing import Any, cast

import pytest

from agent_dispatch.queue_common import Task
from agent_dispatch.queue_feed_preparation import FeedWaiterAdmission, require_feed_preparation
from agent_dispatch.queue_records import TaskError
from agent_dispatch.recipes.registry import dedup_key_for, render_recipe
from agent_dispatch.repository_feed_preparation import (
    RepositoryFeedPreparer, create_repository_feed_preparer,
)
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests import test_repository_feed_waiter_storage as storage

pytestmark = pytest.mark.contract("agent_dispatch.repository_feed.preparation")
feed_intent = cast(Callable[..., dict[str, Any]], storage.feed_intent)
TARGET = "https://forge.example.com/example/project"


class Client:
    deadline: float | None = None

    def __init__(self, binding: dict[str, Any]) -> None:
        self.binding = copy.deepcopy(binding)
        self.calls: list[str] = []
        self.during: Callable[[], None] = lambda: None
        self.capabilities: dict[str, Any] = {
            "protocol": "agent-pull-requests.repository-cache/v1",
            "identity": self.binding["identity"],
            "namespace": self.binding["identity"]["namespace"],
            "target": self.binding["target"],
            "consumer_binding": {
                "protocol": "agent-pull-requests.repository-consumer/v1",
                "consumer": self.binding["consumer"],
            },
        }

    async def negotiate(self) -> dict[str, Any]:
        self.calls.append("capabilities")
        await asyncio.sleep(0)
        self.during()
        return copy.deepcopy(self.capabilities)

    async def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(operation)
        assert operation == "next-actions"
        assert payload == {"reviewer": self.binding["reviewer"], "limit": 1}
        await asyncio.sleep(0)
        return {
            "protocol": "agent-pull-requests.reviewer-actions/v1",
            "identity": self.binding["identity"], "reviewer": self.binding["reviewer"],
        }


def setup(
    tmp_path: Path, *, target: str = TARGET,
) -> tuple[TaskQueue, str, dict[str, Any], Client, RepositoryFeedPreparer]:
    queue = TaskQueue(tmp_path / "tasks.db")
    recipe = render_recipe("reviewer", {"repo": target, "pr": "7", "land": "author"})
    create = cast(Callable[..., Task], queue.create)
    task = create(
        "review", repo="harness", source="recipe", origin_ref="reviewer",
        dedup_key=dedup_key_for(recipe),
    )
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker", owner_session_id="session")
    spec = feed_intent(queue)
    spec["feed"]["issued_at"] = time.time()
    spec["feed"]["deadline_at"] = spec["feed"]["issued_at"] + spec["timeout"]
    client = Client(spec["feed"]["binding"])
    preparer = RepositoryFeedPreparer(
        queue, binding=spec["feed"]["binding"], client=client, lane="harness",
        repository_target=target, land="author", checkpoint=lambda: None,
    )
    return queue, task.id, spec, client, preparer


async def prepare(
    preparer: RepositoryFeedPreparer, task_id: str, spec: dict[str, Any],
) -> dict[str, Any]:
    return await preparer.prepare(
        task_id, delegation=spec, worker_id="worker", resume_worktree="host/wt",
        host="host", reason="waiting for new reviewer activity",
    )


def test_native_prepare_reopen_and_retry_keep_original_intent_without_registration(tmp_path: Path) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    original = copy.deepcopy(spec)
    first = asyncio.run(prepare(preparer, task_id, spec))
    second = asyncio.run(prepare(preparer, task_id, spec))
    assert first["generation"] == second["generation"] == 1
    reopened = TaskQueue(tmp_path / "tasks.db")
    waiter = reopened.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None and waiter["delegation"] == original
    assert waiter["owner"] == "worker" and waiter["owner_session_id"] == "session"
    assert not waiter["replay_attempted"] and waiter["replay_registration_id"] is None
    assert client.calls == ["capabilities", "next-actions"] * 2 and client.deadline is None
    task = reopened.get(task_id)
    assert task is not None and task.status == "suspended"
    assert not reopened.list_run_waiter_wakes(task_id)
    associated = queue.lookup_dedup(task.dedup_key or "", repo="harness")
    assert associated is not None and associated.id == task_id


def test_suspended_task_cannot_replace_its_live_waiter(tmp_path: Path) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    original = asyncio.run(prepare(preparer, task_id, spec))
    before = queue.get(task_id)
    replacement = copy.deepcopy(spec)
    replacement["preparation_id"] = "replacement"
    replacement["feed"]["request_id"] = "replacement"
    with pytest.raises(TaskError, match="same live waiter"):
        asyncio.run(prepare(preparer, task_id, replacement))
    assert queue.get(task_id) == before
    retained = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert retained is not None and retained["generation"] == original["generation"]
    assert retained["state"] == "preparing" and not retained["replay_attempted"]
    assert queue.find_delegated_preparation(task_id, "replacement") is None
    assert not queue.list_run_waiter_wakes(task_id)
    assert client.calls == ["capabilities", "next-actions"] * 2


@pytest.mark.parametrize("replacement", [False, True])
def test_event_note_racing_negotiation_preserves_its_current_pending_wake(
    tmp_path: Path, replacement: bool,
) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    original = asyncio.run(prepare(preparer, task_id, spec))
    before = queue.get(task_id)
    assert before is not None
    if replacement:
        spec["preparation_id"] = "replacement"
        spec["feed"]["request_id"] = "replacement"
    def event_note() -> None:
        queue.append_event_note(
            task_id, sender="operator", note="new accepted instruction", wake_agent=True,
        )

    client.during = event_note
    with pytest.raises(TaskError, match="same live waiter"):
        asyncio.run(prepare(preparer, task_id, spec))
    current = queue.get(task_id)
    assert current is not None and current.steering_revision == before.steering_revision
    wake, = queue.list_run_waiter_wakes(task_id)
    assert wake.sender == "agent-dispatch-event-note" and wake.status == "pending"
    assert wake.waiter_generation == original["generation"]
    with queue._connect() as conn:
        assert queue._run_waiter_wake_is_current(conn, current, wake)
        assert conn.execute(
            "SELECT MAX(generation) FROM run_waiters WHERE task_id=?", (task_id,),
        ).fetchone()[0] == original["generation"]
    if replacement:
        assert queue.find_delegated_preparation(task_id, "replacement") is None


@pytest.mark.parametrize("change", [
    {"hold_reason": "operator hold"}, {"awaiting_steer": 1},
    {"owner_session_id": "successor"}, {"generation": 2}, {"owner": "other"},
    {"status": "queued"}, {"card": '{"summary":"new instruction"}'},
    {"dedup_key": "different"}, {"source": "other"}, {"origin_ref": "goal-driven"},
])
def test_atomic_task_admission_rejects_changes_during_service_negotiation(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    changed: list[Task | None] = []

    def race() -> None:
        with queue._connect() as conn:
            conn.execute(
                "UPDATE tasks SET " + ", ".join(key + "=?" for key in change) + " WHERE id=?",
                (*change.values(), task_id),
            )
        changed.append(queue.get(task_id))

    client.during = race
    with pytest.raises(TaskError, match="associated finite|changed|hold or operator"):
        asyncio.run(prepare(preparer, task_id, spec))
    assert queue.get(task_id) == changed[0]
    assert not queue.list_pending_run_waiters()
    assert not queue.list_run_waiter_wakes(task_id)


def test_standing_task_snapshot_cannot_enter_finite_feed_preparation(tmp_path: Path) -> None:
    queue, task_id, _, _, _ = setup(tmp_path)
    finite = queue.get(task_id)
    assert finite is not None and finite.dedup_key is not None
    standing = replace(finite, kind="standing-charter")
    admission = FeedWaiterAdmission.observe(standing, lane="harness", dedup_key=finite.dedup_key)
    with queue._connect() as conn, pytest.raises(TaskError, match="associated finite"):
        require_feed_preparation(conn, standing, admission, worker_id="worker")
    assert queue.get(task_id) == finite and not queue.list_pending_run_waiters()


@pytest.mark.parametrize("guard", ["execution", "relationship"])
def test_native_execution_and_assignment_guards_remain_effective(
    tmp_path: Path, guard: str,
) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)

    def close_admission() -> None:
        with queue._connect() as conn:
            if guard == "execution":
                conn.execute(
                    "INSERT INTO execution_admission_closures VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("scope", "harness", "recipe", "original-close", "{}", 1, time.time()),
                )
            else:
                conn.execute(
                    "UPDATE task_events SET task_relationship=? WHERE id="
                    "(SELECT MAX(id) FROM task_events WHERE task_id=?)",
                    (json.dumps({"state": "pending", "request": {"generation": 1}}), task_id),
                )

    client.during = close_admission
    with pytest.raises(TaskError, match="admission closed|protection is unconfirmed"):
        asyncio.run(prepare(preparer, task_id, spec))
    current = queue.get(task_id)
    assert current is not None and current.status == "started"
    assert not queue.list_pending_run_waiters() and not queue.list_run_waiter_wakes(task_id)


def test_missing_native_support_refuses_before_service_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    before = queue.get(task_id)
    monkeypatch.setattr(queue, "repository_feed_preparation_protocol", "prior-reader")
    with pytest.raises(TaskError, match="unsupported"):
        asyncio.run(prepare(preparer, task_id, spec))
    assert client.calls == [] and queue.get(task_id) == before


@pytest.mark.parametrize("dimension", ["protocol", "identity", "namespace", "target", "consumer_binding"])
def test_foreign_executing_selection_refuses_before_native_preparation(
    tmp_path: Path, dimension: str,
) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    before = queue.get(task_id)
    client.capabilities[dimension] = "foreign"
    with pytest.raises(ValueError, match="selection differs"):
        asyncio.run(prepare(preparer, task_id, spec))
    assert client.calls == ["capabilities"] and queue.get(task_id) == before
    assert not queue.list_pending_run_waiters()


def test_drain_checkpoint_after_negotiation_leaves_task_unchanged(tmp_path: Path) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    before = queue.get(task_id)

    def refuse() -> None:
        raise TaskError("owner is draining")

    client.during = lambda: setattr(preparer, "_checkpoint", refuse)
    with pytest.raises(TaskError, match="draining"):
        asyncio.run(prepare(preparer, task_id, spec))
    assert client.calls == ["capabilities"] and queue.get(task_id) == before
    assert not queue.list_pending_run_waiters()


def test_original_deadline_is_not_renewed_during_negotiation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    before = queue.get(task_id)
    client.during = lambda: monkeypatch.setattr(time, "time", lambda: spec["feed"]["deadline_at"])
    with pytest.raises(TaskError, match="deadline elapsed"):
        asyncio.run(prepare(preparer, task_id, spec))
    assert queue.get(task_id) == before and not queue.list_pending_run_waiters()
    assert client.deadline is None


def test_original_deadline_is_rechecked_after_native_write_lock_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, spec, _, preparer = setup(tmp_path)
    before = queue.get(task_id)
    times = iter((spec["feed"]["deadline_at"] - 1, spec["feed"]["deadline_at"]))
    monkeypatch.setattr(queue, "_now", lambda _now: next(times))
    with pytest.raises(TaskError, match="deadline elapsed"):
        asyncio.run(prepare(preparer, task_id, spec))
    assert queue.get(task_id) == before and not queue.list_pending_run_waiters()


def test_input_mutation_cannot_change_negotiated_original_request(tmp_path: Path) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    original = copy.deepcopy(spec)
    client.during = lambda: spec["feed"].update(request_id="replacement", baseline=None)
    asyncio.run(prepare(preparer, task_id, spec))
    waiter = queue.find_delegated_preparation(task_id, original["preparation_id"])
    assert waiter is not None and waiter["delegation"] == original


def test_lost_local_prepare_result_reconciles_original_waiter_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    original = queue.prepare_repository_feed_waiter

    def lose(*args: Any, **kwargs: Any) -> dict[str, Any]:
        original(*args, **kwargs)
        raise OSError("local result lost")

    monkeypatch.setattr(queue, "prepare_repository_feed_waiter", lose)
    with pytest.raises(OSError, match="result lost"):
        asyncio.run(prepare(preparer, task_id, spec))
    retained = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert retained is not None and retained["generation"] == 1
    monkeypatch.setattr(queue, "prepare_repository_feed_waiter", original)
    result = asyncio.run(prepare(preparer, task_id, spec))
    assert result["generation"] == retained["generation"] and result["delegation"] == spec
    assert client.calls == ["capabilities", "next-actions"] * 2
    assert not queue.list_run_waiter_wakes(task_id)


def test_cancelled_negotiation_never_suspends_or_registers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, spec, client, preparer = setup(tmp_path)
    before = queue.get(task_id)

    async def scenario() -> None:
        entered = asyncio.Event()

        async def hold() -> dict[str, Any]:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(client, "negotiate", hold)
        work = asyncio.create_task(prepare(preparer, task_id, spec))
        await entered.wait()
        with pytest.raises(TaskError, match="already in flight"):
            await prepare(preparer, task_id, spec)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work

    asyncio.run(scenario())
    assert client.deadline is None and queue.get(task_id) == before
    assert not queue.list_pending_run_waiters() and not queue.list_run_waiter_wakes(task_id)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix preparation")
@pytest.mark.parametrize("feature", ["current", "absent", "unsupported"])
def test_actual_async_unix_cache_and_native_queue_compose_without_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, feature: str,
) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "services/pr/src"))
    monkeypatch.syspath_prepend(str(root / "services/pr/tests"))
    host_fixture = importlib.import_module("test_bound_local_client")

    async def scenario() -> None:
        async with host_fixture.running(tmp_path, feature=feature) as host:
            target = host.identity.endpoint + "/" + host.identity.repo
            queue, task_id, spec, _, _ = setup(tmp_path, target=target)
            spec["repo"] = host.identity.repo
            spec["feed"]["binding"].update(
                socket=str(host.path), target="repository", consumer="consumer",
                identity=host.identity.document(),
            )
            selected = create_repository_feed_preparer(
                queue, binding=spec["feed"]["binding"], lane="harness",
                repository_target=target, land="author", checkpoint=lambda: None,
            )
            before_task, before_cache = queue.get(task_id), host.store.path.read_bytes()
            if feature == "current":
                await prepare(selected, task_id, spec)
                waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
                assert waiter is not None and waiter["delegation"] == spec
                assert not waiter["replay_attempted"] and not waiter["replay_registration_id"]
                assert [call["operation"] for call in host.calls] == ["capabilities", "next-actions"]
                assert host.calls[-1]["body"]["expected_consumer"] == "consumer"
            else:
                with pytest.raises(ValueError, match="unsupported"):
                    await prepare(selected, task_id, spec)
                assert queue.get(task_id) == before_task and not queue.list_pending_run_waiters()
                assert [call["operation"] for call in host.calls] == ["capabilities"]
            assert host.store.path.read_bytes() == before_cache
            assert not queue.list_run_waiter_wakes(task_id)

    asyncio.run(scenario())
