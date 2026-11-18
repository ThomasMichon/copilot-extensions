"""Reader expansion preserves native service custody without enabling delivery."""

from __future__ import annotations

import copy
from collections.abc import Callable
import sqlite3
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.delegated_waiter_contract import validate_delegation
from agent_dispatch.queue_records import TaskError
from agent_dispatch.queue_run_waiter_records import run_waiter_summary
from agent_dispatch.repository_feed_waiter_contract import (
    CACHE_PROTOCOL, STORAGE_PROTOCOL, validate_feed_delegation,
)
from agent_dispatch.run_waiter_recovery import recover_run_waiters
from tests import _delegation_helpers as helpers
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests import test_delegated_replay_binding as scoped_fixtures
from tests import test_delegated_replay_consumer as replay_fixtures

pytestmark = pytest.mark.contract("agent_dispatch.repository_feed.storage")
identity = cast(Callable[..., dict[str, Any]], helpers.identity)
prepare = cast(Callable[..., dict[str, Any]], helpers.prepare)
task = cast(Callable[[TaskQueue], str], helpers.task)
intent = cast(Callable[..., dict[str, Any]], replay_fixtures.intent)
scoped_intent = cast(Callable[..., dict[str, Any]], scoped_fixtures.scoped_intent)
BINDING, SCOPED = replay_fixtures.BINDING, scoped_fixtures.SCOPED


def feed_intent(queue: TaskQueue, suffix: str = "feed") -> dict[str, Any]:
    return {
        "protocol": STORAGE_PROTOCOL,
        **{key: value for key, value in identity(queue, suffix).items()
           if key not in {"until", "shared"}},
        "feed": {
            "binding": {
                "protocol": CACHE_PROTOCOL, "socket": "/private/pr/service.sock",
                "identity": {
                    "namespace": "installation-a", "provider": "gitea",
                    "endpoint": "https://forge.example.com", "account": "observer",
                    "repo": "example/project",
                },
                "target": "project", "consumer": "dispatch:coordinator", "reviewer": "reviewer",
            },
            "request_id": "original:" + suffix, "issued_at": 1000, "deadline_at": 1300,
            "baseline": {"event_cursor": 7, "action_key": "a" * 64},
        },
    }


def test_feed_and_prior_rows_keep_exact_custody_across_storage_restart(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    queue = TaskQueue(database)
    specs = [identity(queue), intent(queue, "legacy"), scoped_intent(queue), feed_intent(queue)]
    tasks = [task(queue) for _ in specs]
    for task_id, spec in zip(tasks, specs):
        prepare(queue, task_id, spec, now=1000)
    original = copy.deepcopy(specs[-1])
    specs[-1]["feed"]["binding"]["identity"]["account"] = "changed-input"
    queue = TaskQueue(database)
    persisted = queue.find_delegated_preparation(tasks[-1], "feed")
    assert persisted is not None and persisted["delegation"] == original
    waiter, = queue.list_replay_waiters(original["feed"]["binding"])
    current_task = queue.get(tasks[-1])
    assert current_task is not None and waiter["task_generation"] == current_task.generation
    assert waiter["owner"] == "worker" and waiter["owner_session_id"] == "session"
    assert not waiter["replay_attempted"] and waiter["replay_registration_id"] is None
    assert run_waiter_summary(waiter)["observation_deadline"] == 1300
    assert run_waiter_summary(waiter)["delivery"] == "repository-feed"
    assert [row["task_id"] for row in queue.list_pending_run_waiters(local_only=True)] == tasks[:1]
    assert [row["task_id"] for row in queue.list_replay_waiters(BINDING)] == tasks[1:2]
    assert [row["task_id"] for row in queue.list_replay_waiters(SCOPED)] == tasks[2:3]
    assert all(not queue.list_run_waiter_wakes(task_id) for task_id in tasks)


def test_original_request_conflicts_before_another_task_is_suspended(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    first, second = task(queue), task(queue)
    spec = feed_intent(queue)
    prepared = prepare(queue, first, spec)
    assert prepare(queue, first, spec)["generation"] == prepared["generation"]
    with pytest.raises(TaskError, match="already bound"):
        prepare(queue, second, {**spec, "preparation_id": "another"})
    second_task = queue.get(second)
    assert second_task is not None and second_task.status == "started"
    assert queue.find_delegated_preparation(second, "another") is None
    changed = copy.deepcopy(spec)
    changed["feed"]["baseline"] = None
    with pytest.raises(TaskError, match="identity conflict"):
        prepare(queue, first, changed)
    persisted = queue.find_delegated_preparation(first, "feed")
    assert persisted is not None and persisted["delegation"] == spec


@pytest.mark.parametrize("armed", [False, True])
def test_feed_custody_never_becomes_a_local_pid_or_preparing_recovery(
    tmp_path: Path, armed: bool,
) -> None:
    database = tmp_path / "tasks.db"
    queue = TaskQueue(database)
    task_id, spec = task(queue), feed_intent(queue)
    prepared = prepare(queue, task_id, spec, now=1000)
    waiter, = queue.list_replay_waiters(spec["feed"]["binding"])
    queue.replay_waiter_progress(waiter, attempt=True)
    if armed:
        queue.replay_waiter_progress(waiter, registration_id="original-registration")
        queue.arm_run_waiter(
            task_id, generation=prepared["generation"], kind="delegated",
            delegation={**spec, "registration_id": "original-registration"}, now=1001,
        )
    queue = TaskQueue(database)

    def forbidden(*_args: object) -> NoReturn:
        pytest.fail("service-owned intent reached local recovery")

    counts = recover_run_waiters(
        queue, process_exists=forbidden, start_token_for_pid=forbidden,
        delegated_owner_probe=forbidden, current_machine="host", arm_grace_seconds=0.01,
    )
    assert counts == {"checked": 0, "live": 0, "unknown": 0, "recovered": 0}
    retained, = queue.list_replay_waiters(spec["feed"]["binding"])
    assert retained["state"] == ("active" if armed else "preparing")
    assert retained["replay_attempted"] and not retained["replay_settled"]
    assert retained["delegation"]["feed"] == spec["feed"]
    assert not queue.list_run_waiter_wakes(task_id)
    if armed:
        assert retained["replay_registration_id"] == "original-registration"
        assert queue.list_active_run_waiters(local_only=True) == []


@pytest.mark.parametrize("dimension", [
    "protocol", "socket", "target", "consumer", "reviewer",
    "namespace", "provider", "endpoint", "account", "repo",
])
def test_changed_binding_cannot_adopt_original_unsettled_rows(
    tmp_path: Path, dimension: str,
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    spec = feed_intent(queue)
    task_id = task(queue)
    prepare(queue, task_id, spec)
    changed = copy.deepcopy(spec["feed"]["binding"])
    selected = changed["identity"] if dimension in changed["identity"] else changed
    selected[dimension] += "-other"
    assert queue.list_replay_waiters(changed) == []
    assert len(queue.list_replay_waiters(spec["feed"]["binding"])) == 1
    assert not queue.list_run_waiter_wakes(task_id)


def test_callback_transport_refuses_feed_before_task_or_outbox_changes(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id, spec = task(queue), feed_intent(queue)
    before = queue.get(task_id)
    client = TestClient(create_app(queue, enable_mcp=False))
    health = client.get("/health").json()["delegated_run_waiters"]
    assert health["protocol"] == "delegated_run_waiters/v1"
    result = client.post(f"/tasks/{task_id}/run-waiter/register", json={
        "worker_id": "worker", "reason": "waiting", "host": "host",
        "resume_worktree": "host/wt", "kind": "delegated", "delegation": spec,
    })
    assert result.status_code == 422
    assert queue.get(task_id) == before
    assert not queue.list_pending_run_waiters() and not queue.list_run_waiter_wakes(task_id)
    with pytest.raises(ValueError):
        validate_delegation(spec)


@pytest.mark.parametrize("field,value", [
    ("protocol", "delegated_run_waiters/v1"), ("until", ["merged"]),
    ("shared", False), ("installation", {}), ("replay", {}),
    ("registration_id", "not-prepared"), ("timeout", True), ("number", False),
])
def test_feed_variant_rejects_mixed_or_invalid_outer_identity(
    tmp_path: Path, field: str, value: object,
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    spec = feed_intent(queue)
    spec[field] = value
    task_id = task(queue)
    with pytest.raises(ValueError):
        prepare(queue, task_id, spec)
    current_task = queue.get(task_id)
    assert current_task is not None and current_task.status == "started"
    assert not queue.list_pending_run_waiters()


@pytest.mark.parametrize("change", [
    {"deadline_at": 1400}, {"issued_at": float("nan")},
    {"baseline": {"event_cursor": True, "action_key": None}},
    {"baseline": {"event_cursor": 0, "action_key": "unknown"}},
    {"baseline": {}}, {"replacement": True},
])
def test_invalid_original_request_cannot_enter_storage(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    spec = feed_intent(queue)
    spec["feed"].update(change)
    with pytest.raises(ValueError):
        validate_feed_delegation(spec)


def test_unknown_and_known_empty_baselines_remain_distinct_and_detached(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    spec = feed_intent(queue)
    spec["feed"]["baseline"] = None
    assert validate_feed_delegation(spec)["feed"]["baseline"] is None
    spec["feed"]["baseline"] = {"event_cursor": 7, "action_key": None}
    selected = validate_feed_delegation(spec)
    spec["feed"]["baseline"]["event_cursor"] = 99
    assert selected["feed"]["baseline"] == {"event_cursor": 7, "action_key": None}


@pytest.mark.parametrize("section,field,value", [
    ("binding", "socket", "relative/service.sock"),
    ("binding", "socket", "/private/../other/service.sock"),
    ("binding", "protocol", "agent-pull-requests.watch/v2"),
    ("identity", "endpoint", "https://user:password@forge.example.com"),
    ("identity", "repo", "different/project"),
    ("identity", "account", "observer\n"),
])
def test_invalid_or_mixed_routing_does_not_suspend_a_task(
    tmp_path: Path, section: str, field: str, value: str,
) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    spec, task_id = feed_intent(queue), task(queue)
    binding = spec["feed"]["binding"]
    (binding if section == "binding" else binding["identity"])[field] = value
    with pytest.raises(ValueError):
        prepare(queue, task_id, spec)
    current_task = queue.get(task_id)
    assert current_task is not None and current_task.status == "started"
    assert not queue.list_pending_run_waiters()


def test_corrupt_feed_row_is_refused_without_rewriting_original_storage(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    queue = TaskQueue(database)
    spec = feed_intent(queue)
    prepare(queue, task(queue), spec)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE run_waiters SET delegation_json = replace(delegation_json, ?, ?)",
                     (STORAGE_PROTOCOL, "unsupported/feed"))
    with sqlite3.connect(database) as conn:
        before, = conn.execute("SELECT delegation_json FROM run_waiters").fetchone()
    with pytest.raises(ValueError, match="repository-feed waiter identity"):
        TaskQueue(database).list_replay_waiters(spec["feed"]["binding"])
    with sqlite3.connect(database) as conn:
        after, = conn.execute("SELECT delegation_json FROM run_waiters").fetchone()
    assert after == before
