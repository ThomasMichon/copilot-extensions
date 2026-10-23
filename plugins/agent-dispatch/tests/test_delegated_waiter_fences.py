from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import time

import pytest

from agent_dispatch.delegated_waiter_callback import complete
from agent_dispatch.delegated_waiter_contract import positive_timeout, validate_event
from agent_dispatch.queue import TaskError
from agent_dispatch.run_waiter_recovery import recover_run_waiters
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests._delegation_helpers import client_factory, event, identity, prepare, task

pytestmark = pytest.mark.contract("agent_dispatch.delegated_waiter.fences")

DELEGATION_COLUMNS = (
    ("kind", "TEXT NOT NULL DEFAULT 'process'"),
    ("delegation_json", "TEXT"), ("event_id", "TEXT"),
    ("replay_scope", "TEXT"), ("replay_request_id", "TEXT"),
    ("replay_registration_id", "TEXT"),
    ("replay_attempted", "INTEGER NOT NULL DEFAULT 0"),
    ("replay_settled", "INTEGER NOT NULL DEFAULT 0"),
)


def test_concurrent_initializers_accept_only_duplicate_delegation_columns(tmp_path):
    from agent_dispatch.queue_schema_migrations import run_queue_schema_migrations

    database = tmp_path / "tasks.db"
    TaskQueue(database)
    with sqlite3.connect(database, isolation_level=None) as connection, \
            sqlite3.connect(database, isolation_level=None) as concurrent:
        connection.row_factory = sqlite3.Row
        for name in ("request", "registration", "unsettled", "local"):
            connection.execute(f"DROP INDEX idx_run_waiters_replay_{name}")
        for name, _ in DELEGATION_COLUMNS:
            connection.execute(f"ALTER TABLE run_waiters DROP COLUMN {name}")

        class StaleSnapshot:
            def execute(self, sql, *args):
                cursor = connection.execute(sql, *args)
                if sql == "PRAGMA table_info(run_waiters)":
                    snapshot = list(cursor)
                    for name, declaration in DELEGATION_COLUMNS:
                        concurrent.execute(f"ALTER TABLE run_waiters ADD COLUMN {name} {declaration}")
                    return snapshot
                return cursor

        run_queue_schema_migrations(StaleSnapshot(), now_fn=time.time)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(run_waiters)")}
        assert {name for name, _ in DELEGATION_COLUMNS} <= columns
    restored = TaskQueue(database)
    assert restored.run_waiter_coordinator_id()


@pytest.mark.parametrize("column", [name for name, _ in DELEGATION_COLUMNS])
def test_delegation_schema_migration_propagates_other_operational_errors(tmp_path, column):
    from agent_dispatch.queue_schema_migrations import run_queue_schema_migrations

    database = tmp_path / "tasks.db"
    TaskQueue(database)
    with sqlite3.connect(database, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        for name in ("request", "registration", "unsettled", "local"):
            connection.execute(f"DROP INDEX idx_run_waiters_replay_{name}")
        connection.execute(f"ALTER TABLE run_waiters DROP COLUMN {column}")

        class FailingAddition:
            def execute(self, sql, *args):
                if sql.startswith(f"ALTER TABLE run_waiters ADD COLUMN {column} "):
                    raise sqlite3.OperationalError("database is locked")
                return connection.execute(sql, *args)

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            run_queue_schema_migrations(FailingAddition(), now_fn=time.time)


@pytest.mark.parametrize("timeout", [True, False, 0, -1, float("nan"), float("inf"), 2**63, "1"])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        positive_timeout(timeout)


def test_prepare_arm_race_replay_and_new_generation(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    prepared = prepare(queue, task_id, spec)
    assert prepare(queue, task_id, spec)["generation"] == prepared["generation"]
    factory = client_factory(queue)
    envelope = {"task_id": task_id, "generation": prepared["generation"], "delegation": spec}
    fired = event(spec)
    assert complete(envelope, fired, factory) == 1
    assert not queue.list_run_waiter_wakes(task_id)
    armed = queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                                 delegation={**spec, "registration_id": "registration"})
    assert armed["kind"] == "delegated"
    assert armed["pid"] == 0 and armed["host"] == "host"
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: complete(envelope, fired, factory), range(8))) == [0] * 8
    assert len(queue.list_run_waiter_wakes(task_id)) == 1
    # Simulate remote commit / lost local ACK and coordinator restore.
    restored = TaskQueue(tmp_path / "tasks.db")
    assert restored.run_waiter_coordinator_id() == spec["coordinator_id"]
    newer = prepare(restored, task_id, identity(restored, "two"))
    assert newer["generation"] == 2
    assert complete(envelope, fired, client_factory(restored)) == 0
    assert restored.list_pending_run_waiters()[0]["generation"] == 2
    assert len(restored.list_run_waiter_wakes(task_id)) == 1


@pytest.mark.parametrize("timed_out", [False, True])
def test_timeout_and_cancelled_callback(tmp_path, timed_out):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    queue.cancel_preparing_run_waiter(task_id, generation=1, reason="cancelled")
    assert complete({"task_id": task_id, "generation": 1, "delegation": spec},
                    event(spec, timed_out=timed_out), client_factory(queue)) == 0
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("change", [
    {"registration_id": "other"}, {"subscriber_id": "other"}, {"number": True},
    {"notification_protocol": "future/v2"}, {"transitions": ["checks_changed"]},
    {"merged": "yes"}, {"pr_state": None}, {"unexpected": "field"},
])
def test_invalid_or_mismatching_callback_cannot_wake(tmp_path, change):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                         delegation={**spec, "registration_id": "registration"})
    fired = {**event(spec), **change}
    with pytest.raises((ValueError, RuntimeError)):
        complete({"task_id": task_id, "generation": 1, "delegation": spec},
                 fired, client_factory(queue))
    assert not queue.list_run_waiter_wakes(task_id)


def test_partial_snapshot_rejected(tmp_path):
    spec = identity(TaskQueue(tmp_path / "tasks.db"))
    fired = event(spec)
    del fired["checks_state"]
    with pytest.raises(ValueError):
        validate_event(fired, spec)


@pytest.mark.parametrize("capability", [None, [], "future"])
def test_callback_refuses_malformed_coordinator_capability(tmp_path, capability):
    from contextlib import contextmanager
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)

    @contextmanager
    def factory(*args, **kwargs):
        with client_factory(queue)(*args, **kwargs) as client:
            client.health = lambda: {"delegated_run_waiters": capability}
            yield client

    assert complete({"task_id": task_id, "generation": 1, "delegation": spec},
                    event(spec), factory) == 1
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("capabilities", [None, "acknowledged_notifications/v1", {}])
def test_recovery_treats_malformed_owner_capabilities_as_unavailable(monkeypatch, capabilities):
    from agent_dispatch import delegated_waiter_cli, procutil
    from agent_dispatch.delegated_waiter_recovery import probe_local_owner
    monkeypatch.setattr(procutil, "agent_pull_requests_launch_prefix", lambda: ["owner"])
    monkeypatch.setattr(delegated_waiter_cli, "owner_request", lambda *_: {
        "capabilities": capabilities,
    })
    assert probe_local_owner() is None


@pytest.mark.parametrize("owner", [None, {}, {"example/project#7": ["dispatch:one"]}])
def test_delegated_missing_owner_and_expiry_recovery(tmp_path, owner):
    clock = [100.0]
    queue = TaskQueue(tmp_path / "tasks.db")
    queue._now = lambda now: clock[0] if now is None else now
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                         delegation={**spec, "registration_id": "registration"})
    clock[0] = 250
    probe_calls = []
    counts = recover_run_waiters(
        queue, current_machine="host", process_exists=lambda _: pytest.fail("no PID probe"),
        start_token_for_pid=lambda _: pytest.fail("no PID identity"),
        delegated_owner_probe=lambda: probe_calls.append(True) or owner,
    )
    assert len(probe_calls) == 1
    assert counts["recovered"] == (0 if owner else 1)
    if owner:
        clock[0] = 521
        counts = recover_run_waiters(
            queue, current_machine="another-host", process_exists=lambda _: True,
            start_token_for_pid=lambda _: None, delegated_owner_probe=lambda: owner,
        )
        assert counts["recovered"] == 1
    assert len(queue.list_run_waiter_wakes(task_id)) == 1


def test_process_cannot_arm_delegated_and_unknown_kind_rejected(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    prepare(queue, task_id, identity(queue))
    with pytest.raises(TaskError):
        queue.arm_run_waiter(task_id, generation=1, pid=1, host="host", start_token="one")
    with pytest.raises(TaskError):
        queue.prepare_run_waiter(task_id, worker_id="worker", resume_worktree="host/wt",
                                 command=["wait"], reason="waiting", kind="future")


def test_recovery_owner_keeps_checking_delegates_without_process_rescans(tmp_path):
    import asyncio
    from agent_dispatch.run_waiter_recovery import maintain_delegated_recovery
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                         delegation={**spec, "registration_id": "registration"})
    sleeps = []
    recovered = []

    async def sleep(interval):
        sleeps.append(interval)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    async def recover():
        recovered.append(True)
        queue.recover_dead_run_waiter(task_id, generation=1, reason="owner missing",
                                     message="re-check", sender="recovery")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(maintain_delegated_recovery(queue, recover, sleep=sleep))
    assert recovered == [True]
    assert sleeps == [60.0] * 3


def test_delegated_summary_surfaces_target_and_deadline(tmp_path):
    from agent_dispatch.coordinator_tasks import _run_waiter_summary
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = task(queue)
    spec = identity(queue)
    prepare(queue, task_id, spec)
    queue.arm_run_waiter(task_id, generation=1, kind="delegated",
                         delegation={**spec, "registration_id": "registration"})
    summary = _run_waiter_summary(queue, task_id)
    assert summary["kind"] == "delegated" and summary["generation"] == 1
    assert summary["repo"] == "example/project" and summary["number"] == 7
    assert summary["observation_deadline"] == summary["created_at"] + 300
    assert summary["command"] == []


def test_callback_import_optimization_preserves_public_queue_exports():
    import agent_dispatch
    from agent_dispatch import queue
    for name in agent_dispatch.__all__:
        assert agent_dispatch.__getattr__(name) is getattr(queue, name)
    with pytest.raises(AttributeError):
        agent_dispatch.__getattr__("unknown")
