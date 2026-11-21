"""Frozen admission destinations remain attributable after retarget, restart or retirement."""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_dispatch import bridge
from agent_dispatch.execution_send_destination import ExecutionDestination, require_clear
from agent_dispatch.provisional_driver_work import require_no_outstanding_driver_work
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_execution_custody import ExecutionSendIdentity
from agent_dispatch.queue_records import TaskError
from tests import test_coordinator_execution_sender as sender
from tests import test_execution_send_custody as sends

setup = sends.setup
cohort = sender.cohort


def test_retarget_does_not_change_admitted_destination_or_authorize_another_send(
    setup: tuple[TaskQueue, ExecutionSendIdentity, Task],
) -> None:
    queue, identity, _ = setup
    identity = replace(identity, destination=ExecutionDestination(
        "driver", "Fixture-Host", "original-tree",
    ))
    key = identity.reservation_key
    queue.record_spawn_worktree(key, "original-tree", ownership="targeted",
                                execution_machine="fixture-host", driver="cli")
    moved = replace(identity, destination=ExecutionDestination(
        "driver", "fixture-host", "replacement-tree",
    ))
    with pytest.raises(TaskError, match="conflicts with its admitted reservation"):
        queue.admit_execution_send(moved, source="scheduled", expected_producer_generation=1)
    queue.admit_execution_send(identity, source="scheduled", expected_producer_generation=1)
    original = queue.settle_execution_send(identity, outcome="unknown", detail="lost reply")
    updated = queue.record_spawn_worktree(key, "replacement-tree", ownership="targeted",
                                          execution_machine="fixture-host", driver="cli")
    assert updated.worktree == "replacement-tree"
    replay = queue.admit_execution_send(
        identity, source="scheduled", expected_producer_generation=1,
    )
    assert replay.replayed and replay.identity == original.identity and replay.state == "unknown"
    restarted = TaskQueue(queue.db_path)
    with restarted._connect() as conn:
        with pytest.raises(TaskError, match="unresolved"):
            require_clear(conn, "fixture-host", "original-tree", "other")
        require_clear(conn, "fixture-host", "replacement-tree", "other")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE execution_send_custody SET identity='{}'")
    with pytest.raises(TaskError, match="conflicts"):
        restarted.admit_execution_send(moved, source="scheduled", expected_producer_generation=1)
    restarted.settle_execution_send(identity, outcome="sent", detail="owner confirmed")
    with restarted._connect() as conn:
        require_clear(conn, "fixture-host", "original-tree", "other")


@pytest.mark.parametrize(("host", "native", "tree", "blocked"), [
    ("host-a", "other", "driver", True),
    ("host-a", "native", "different", True),
    ("foreign", "native", "driver", False),
])
def test_unresolved_original_send_after_task_and_reservation_end(
    setup: tuple[TaskQueue, ExecutionSendIdentity, Task],
    host: str, native: str, tree: str, blocked: bool,
) -> None:
    queue, identity, other = setup
    identity = replace(identity, destination=ExecutionDestination("driver", host, tree, native))
    queue.record_spawn_worktree(identity.reservation_key, tree, ownership="created",
                                execution_machine=host, driver="cli")
    queue.record_spawn(identity.reservation_key, worktree=tree, session_handle=native)
    queue.admit_execution_send(identity, source="scheduled", expected_producer_generation=1)
    queue.settle_execution_send(identity, outcome="unknown", detail="lost reply")
    queue.settle_spawn(identity.reservation_key)
    observer = queue.create("Unrelated observer", repo="other")
    assert isinstance(observer, Task)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (other.id,))
        row = conn.execute("SELECT identity,state FROM execution_send_custody"
                           " WHERE reservation_key=?", (identity.reservation_key,)).fetchone()
        assert row["state"] == "unknown"
        envelope: dict[str, Any] = json.loads(row["identity"])
        assert envelope["reservation_key"] == identity.reservation_key
        assert envelope["runtime_id"] == identity.runtime_id
        retained = queue.get_reservation(identity.reservation_key)
        assert retained is not None and retained.state == "settled"
        target = {"task_id": observer.id, "execution_machine": "host-a", "worktree": "driver"}
        if blocked:
            with pytest.raises(TaskError, match="unresolved"):
                require_no_outstanding_driver_work(queue, conn, observer, target, "native")
        else:
            require_no_outstanding_driver_work(queue, conn, observer, target, "native")


@pytest.mark.parametrize("damaged", [False, True], ids=["unattributed", "malformed"])
def test_legacy_send_remains_uncertain_after_migration_without_backfill(
    setup: tuple[TaskQueue, ExecutionSendIdentity, Task],
    damaged: bool,
) -> None:
    queue, identity, _ = setup
    queue.record_spawn_worktree(identity.reservation_key, "foreign", ownership="targeted",
                                execution_machine="foreign", driver="cli")
    queue.admit_execution_send(identity, source="scheduled", expected_producer_generation=1)
    with queue._connect() as conn:
        encoded = conn.execute("SELECT identity FROM execution_send_custody").fetchone()[0]
        assert "destination" not in json.loads(encoded)
        conn.execute("DROP TRIGGER execution_send_identity_immutable")
        for field in ("workspace", "native"):
            conn.execute(f"DROP INDEX idx_send_destination_v1_{field}")
        if damaged:
            encoded = "{"
            conn.execute("UPDATE execution_send_custody SET identity=?", (encoded,))
    restarted = TaskQueue(queue.db_path)
    with restarted._connect() as conn:
        assert conn.execute("SELECT identity FROM execution_send_custody").fetchone()[0] == encoded
        with pytest.raises(TaskError, match="no comparable immutable"):
            require_clear(conn, "host", "my-tree", "native")
    if damaged:
        with pytest.raises(TaskError, match="matching retained custody"):
            restarted.settle_execution_send(identity, outcome="sent", detail="not attributable")
        return
    restarted.settle_execution_send(identity, outcome="not_sent", detail="original owner receipt")
    with restarted._connect() as conn:
        require_clear(conn, "host", "my-tree", "native")


def test_container_and_session_routes_are_frozen_before_transport(
    cohort: tuple[TaskQueue, Any, Any, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = cohort[0]
    operations: list[str] = []

    def destination(kind: str) -> dict[str, Any]:
        operations.append(kind)
        with queue._connect() as conn:
            row = conn.execute("SELECT identity,state FROM execution_send_custody WHERE kind=?",
                               (kind,)).fetchone()
            assert row["state"] == "pending"
            with pytest.raises(TaskError, match="no comparable immutable"):
                require_clear(conn, "host", "worktree", "native")
        dest = json.loads(row["identity"])["destination"]
        assert isinstance(dest, dict) and dest["version"] == 1
        return dest

    def launch(*args: object, **kwargs: object) -> SimpleNamespace:
        dest = destination("launch")
        assert (dest["route"], dest["address"], dest["workspace"]) == (
            "container", kwargs["agent"], kwargs["target_dir"],
        )
        return SimpleNamespace(returncode=0, stdout='{"session_id":"fixture"}', stderr="")

    def deliver(session: str, *args: object, **kwargs: object) -> bool:
        dest = destination("deliver")
        assert (dest["route"], dest["address"], dest["native"]) == (
            "bridge-session", session, None,
        )
        return True

    monkeypatch.setattr(bridge, "spawn_worker", launch)
    monkeypatch.setattr(bridge, "resume_worker", deliver)
    post = cast(Callable[..., dict[str, Any]], sender._post)
    assert post(cohort)["coordinator_launch"]["ok"] is True
    assert operations == ["launch", "deliver"]
    with queue._connect() as conn:
        require_clear(conn, "host", "worktree", "native")
