"""Admission pauses keep the same queue, lane and pending worker identities."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.action_controls_cli import command
from agent_dispatch.config import Config
from agent_dispatch.queue import TaskError
from agent_dispatch.server import build_app
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests.test_supervisor import OwnerResultQueue, QueueBackedClient, _ok_spawn


def _policy(root: Path, active: bool) -> None:
    root.mkdir(exist_ok=True)
    (root / "action-controls.json").write_text(
        json.dumps({"worker-admission": {"disabled": active, "reason": "temporary"}}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("operation", ["claim", "reserve"])
def test_running_coordinator_rechecks_before_admission_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    queue = app.state.queue
    task = queue.create("pending", repo=TEST_REPO)
    before = queue.get(task.id)
    for _ in range(2):
        with pytest.raises(TaskError, match=r"worker-admission.*inhibited"):
            if operation == "claim":
                queue.claim_one("host/wt", repo=TEST_REPO, task_id=task.id)
            else:
                queue.reserve_spawn(task.id, reserved_by="lane")
        assert queue.get(task.id) == before
        assert queue.latest_reservation(task.id) is None
    _policy(root, False)
    if operation == "claim":
        claimed = queue.claim_one("host/wt", repo=TEST_REPO, task_id=task.id)
        assert claimed.id == task.id and claimed.attempts == 1
        _policy(root, True)
        queue.start(task.id, "host/wt")
        queue.heartbeat(task.id, "host/wt")
        assert queue.get(task.id).status == "started"
    else:
        reservation, reserved = queue.reserve_spawn(task.id, reserved_by="lane")
        assert reserved and reservation.attempt == 1
        _policy(root, True)
        duplicate, reserved = queue.reserve_spawn(task.id, reserved_by="lane")
        assert not reserved and duplicate == reservation


def test_rest_claim_and_reservation_report_explicit_retryable_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    task = app.state.queue.create("pending", repo=TEST_REPO)
    with TestClient(app) as api:
        assert api.get("/health").status_code == 200
        for path, body in (
            ("/claim", {"worker_id": "host/wt", "repo": TEST_REPO, "task_id": task.id}),
            ("/spawn-reservations", {"task_id": task.id, "reserved_by": "lane"}),
        ):
            result = api.post(path, json=body)
            assert result.status_code == 409
            assert result.json()["detail"]["action"] == "worker-admission"
        _policy(root, False)
        assert api.post("/claim", json={
            "worker_id": "host/wt", "repo": TEST_REPO, "task_id": task.id,
        }).json()["id"] == task.id


def test_same_supervisor_polls_and_restores_without_burning_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("pending")
    held = queue.create("independent hold")
    queue.set_hold(held.id, reason="separate", actor="operator")
    spawn = _ok_spawn()
    lane = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO)
    for _ in range(2):
        _policy(root, True)
        assert lane.poll_once() == []
        assert queue.latest_reservation(task.id) is None
        assert spawn.calls == []
    _policy(root, False)
    assert lane.poll_once() == [task.id]
    assert spawn.calls == [task.id]
    assert queue.latest_reservation(task.id).attempt == 1
    _policy(root, True)
    assert lane.poll_once() == []
    assert queue.get(held.id).hold_reason == "separate"


def test_redrive_pause_precedes_startup_fence_and_restores_same_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("pending")
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="lane")
    queue.record_spawn(
        reservation.key, session_handle="native-one", worktree="wt-one",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    before = queue.get_reservation(reservation.key)
    sends: list[str] = []
    lane = Supervisor(
        QueueBackedClient(queue), spawn_fn=_ok_spawn(), repo=TEST_REPO,
        supervisor_id="lane",
        liveness_fn=lambda *_: {"session_id": "native-one", "worktree_id": "wt-one"},
        redrive_fn=lambda _wt, _machine, _task, _session, res: (
            sends.append(res["key"]) or True
        ),
    )
    _policy(root, True)
    assert lane.redrive_unclaimed_spawns() == 0
    assert queue.get_reservation(reservation.key) == before
    assert sends == []
    _policy(root, False)
    assert lane.redrive_unclaimed_spawns() == 1
    after = queue.get_reservation(reservation.key)
    assert (after.key, after.attempt, after.worktree, after.session_handle) == (
        before.key, before.attempt, before.worktree, before.session_handle,
    )
    assert after.startup_complete is True
    assert sends == [reservation.key]


def test_claim_rechecks_after_selection_and_preserves_pending_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, False)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    queue = app.state.queue
    task = queue.create("pending", repo=TEST_REPO)
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="lane")
    queue.record_spawn(
        reservation.key, session_handle="native-one", worktree="wt-one",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    before = queue.get(task.id)
    pending = queue.get_reservation(reservation.key)
    original = queue._claim_fence_rejection

    def pause_during_selection(conn: sqlite3.Connection, row: sqlite3.Row) -> object:
        _policy(root, True)
        return original(conn, row)

    monkeypatch.setattr(queue, "_claim_fence_rejection", pause_during_selection)
    with pytest.raises(TaskError, match=r"worker-admission.*inhibited"):
        queue.claim_one("host/wt-one", repo=TEST_REPO, task_id=task.id)
    assert queue.get(task.id) == before
    assert queue.get_reservation(reservation.key) == pending
    monkeypatch.setattr(queue, "_claim_fence_rejection", original)
    _policy(root, False)
    assert queue.claim_one("host/wt-one", repo=TEST_REPO, task_id=task.id).attempts == 1
    assert queue.get_reservation(reservation.key) == pending


def test_admitted_launch_finishes_but_later_poll_rechecks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, False)
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    first = queue.create("first", now=1)
    second = queue.create("second", now=2)
    spawn = _ok_spawn()
    lane = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=3)
    reserve = client.reserve_spawn

    def reserve_then_pause(*args: object, **kwargs: object) -> dict:
        result = reserve(*args, **kwargs)
        _policy(root, True)
        return result

    monkeypatch.setattr(client, "reserve_spawn", reserve_then_pause)
    assert lane.poll_once() == [first.id]
    assert spawn.calls == [first.id]
    assert queue.latest_reservation(second.id) is None
    assert lane.poll_once() == []
    monkeypatch.setattr(client, "reserve_spawn", reserve)
    _policy(root, False)
    assert lane.poll_once() == [second.id]
    assert spawn.calls == [first.id, second.id]


def test_mcp_claim_retries_through_the_same_running_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_mcp_http import _boot, _call

    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    task = app.state.queue.create("pending", repo=TEST_REPO)
    url, stop = _boot(app)
    args = {"machine": "host", "worktree": "wt", "repo": TEST_REPO, "task_id": task.id}
    try:
        denied = asyncio.run(_call(url, "dispatch_claim", args))
        assert "worker-admission" in json.loads(denied.content[0].text)["error"]
        assert app.state.queue.get(task.id).attempts == 0
        _policy(root, False)
        admitted = asyncio.run(_call(url, "dispatch_claim", args))
        assert json.loads(admitted.content[0].text)["id"] == task.id
    finally:
        stop()


def test_pure_queue_does_not_inherit_the_host_action_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("embedded queue")
    assert queue.claim_one("host/wt", task_id=task.id).id == task.id


def test_fallback_does_not_consume_its_one_attempt_during_inhibition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch.coordinator_loops import _attempt_handoff_fallback

    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db")))
    queue = app.state.queue
    task = queue.create("handoff", repo=TEST_REPO, labels=["handoff"])
    calls: list[str] = []

    def spawn(task_id: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(task_id)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    assert _attempt_handoff_fallback(queue, task, spawn_fn=spawn) == "inhibited"
    assert queue.get_handoff_fallback_attempt(task.id) is None
    assert calls == []
    _policy(root, False)
    assert _attempt_handoff_fallback(queue, task, spawn_fn=spawn) == "launched"
    assert calls == [task.id]
    assert _attempt_handoff_fallback(queue, task, spawn_fn=spawn) == "skipped"


def test_restricted_startup_retry_is_checked_before_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    queue = OwnerResultQueue(tmp_path / "tasks.db")
    task = queue.create("pending")
    reservation, _ = queue.reserve_spawn(
        task.id, reserved_by="lane", driver="restricted-bridge",
    )
    spawn = _ok_spawn()
    spawn.restricted_bridge_for = lambda _: True
    lane = Supervisor(
        QueueBackedClient(queue), spawn_fn=spawn, repo=TEST_REPO, supervisor_id="lane",
    )
    prepared: list[str] = []
    monkeypatch.setattr(lane, "_prepare_spawn_task", lambda task, _res: (
        prepared.append(task["id"]) or task
    ))
    _policy(root, True)
    assert lane.reconcile_reserving() == 0
    assert spawn.calls == [] and prepared == []
    assert queue.get_reservation(reservation.key) == reservation
    _policy(root, False)
    assert lane._reconcile_restricted_start(asdict(reservation), asdict(task)) == 1
    assert spawn.calls == [task.id] and prepared == [task.id]


def test_status_preserves_automatic_field_and_advertises_actual_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, True)
    assert command(argparse.Namespace(action_command="status", action=None)) == 0
    status = json.loads(capsys.readouterr().out)
    assert "worker-admission" in status["contract"]["actions"]
    assert {item["action"] for item in status["automatic_attempts"]} == {
        "coordinator-replacement", "supervisor-self-update",
    }
    worker = next(item for item in status["action_attempts"]
                  if item["action"] == "worker-admission")
    assert worker["inhibited"] is True
