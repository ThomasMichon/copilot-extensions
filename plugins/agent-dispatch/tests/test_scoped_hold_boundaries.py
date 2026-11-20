"""Regression cases for allocator identity, retained sends and bounded queue visibility."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import ordinary_bridge
from agent_dispatch.action_controls import require_worker_admission
from agent_dispatch.client_errors import DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import WorkerAdmissionInhibited
from agent_dispatch.supervisor import Supervisor
from tests import test_native_owner_wake as native
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, task
from tests.test_scoped_worker_holds import command

q = controls.q
api = controls.api
hosted = native.hosted


def test_fresh_placement_checks_allocator_as_well_as_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue = TaskQueue(tmp_path / "queue.db", worker_admission=require_worker_admission)
    revision = publish(queue, pool("selected"))
    row = task(queue)
    for owner in ("allocator", "selected"):
        command("hold", "--pool", owner, "--reason", "stop")
        with pytest.raises(WorkerAdmissionInhibited):
            queue.place_pool_task(row.id, reserved_by="allocator", catalog_revision=revision)
        assert queue.latest_reservation(row.id) is None and queue.get(row.id) == row
        command("resume", "--pool", owner)
    assert queue.place_pool_task(row.id, reserved_by="allocator", catalog_revision=revision).reserved


@pytest.mark.parametrize("lane_labels", [None, ["lane"]])
def test_held_full_pages_cannot_hide_older_eligible_task(
    q: TaskQueue, api: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lane_labels: list[str] | None,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    eligible = task(q, labels=["lane"])
    with q._connect() as conn:
        # Tied timestamps exercise a real keyset boundary, not a time-only cursor.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)")]
        bind = ", ".join("?" if c in ("id", "created_at", "labels") else c for c in cols)
        values = {"created_at": 200, "labels": '["lane","paused"]'}
        conn.executemany(f"INSERT INTO tasks SELECT {bind} FROM tasks WHERE id=?", [
            [*({"id": f"!paused-{i:04}", **values}[c] for c in cols if c in ("id", "created_at", "labels")),
             eligible.id] for i in range(405)
        ])
    command("hold", "--label", "paused", "--reason", "stop")
    attempts: list[str] = []
    def observe(_client: object, row: dict[str, str], _owner: str, **_kw: object) -> dict[str, bool]:
        attempts.append(row["id"])
        return {"reserved": False}
    monkeypatch.setattr(ordinary_bridge, "reserve_for_backend", observe)
    requests: list[httpx.Request] = []
    with controls.connected(api, requests) as client:
        lane = Supervisor(client, supervisor_id="workers", repo="example", labels=lane_labels,
                          spawn_fn=lambda _: pytest.fail("no granted reservation"))
        for cycle in range(3):
            requests.clear()
            lane.poll_once()
            pages = [r for r in requests if r.url.path == "/tasks" and "limit" in r.url.params]
            assert all(int(r.url.params["limit"]) <= 200 for r in pages)
            assert len([r for r in requests if r.url.path == "/worker-admission/task-page"]) == 1
            if cycle < 2:
                assert attempts == []
        assert attempts == [eligible.id]
        with q._connect() as conn:
            conn.execute("UPDATE tasks SET labels=?,created_at=? WHERE id=?", ('["lane"]', 300, eligible.id))
        def offline(*_: object) -> None:
            raise httpx.ReadTimeout("supplement unavailable")
        monkeypatch.setattr("agent_dispatch.admission_page.next_page", offline)
        attempts.clear()
        lane.poll_once()
        assert attempts == [eligible.id]


@pytest.mark.parametrize("hold_at", ["supervisor", "coordinator"])
def test_pending_redrive_observes_only_original_receipt(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_at: str,
) -> None:
    queue, task_id = hosted
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue.resume(task_id, native.OWNER, reuse_session=True)
    queue.yield_task(task_id, native.OWNER, release_spawn=False)
    calls: list[list[str]] = []
    receipt_present = False

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "--receipt-only" not in argv or not receipt_present:
            return subprocess.CompletedProcess(argv, 70, "", "")
        import json
        receipt = {"version": 1, "session_id": native.BRIDGE, "native_session_id": native.NATIVE,
                   "invocation_key": argv[argv.index("--idempotency-key") + 1], "turn_index": 1,
                   "admission": "accepted", "queued": False}
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt), "")

    monkeypatch.setattr(subprocess, "run", run)
    with TestClient(create_app(queue, token="data", control_token="control", enable_mcp=False,
                               verification_interval=0)) as api, controls.connected(api, []) as client:
        lane = Supervisor(client, repo="queue-project", machine="host-a", spawn_fn=lambda _: pytest.fail("no spawn"))
        original = queue.latest_reservation(task_id)
        assert original is not None
        fresh = client.get_reservation(original.key)["ordinary_redrive"]
        def observe_only(facts: dict[str, Any]) -> None:
            client.record_spawn(original.key, session_handle=original.session_handle, worktree=original.worktree,
                                startup_complete=False, startup_revision=facts["startup_revision"],
                                ordinary_redrive={**facts, "observe_only": True})

        with pytest.raises(DispatchError):
            observe_only(fresh)
        assert queue.latest_reservation(task_id) == original
        assert lane.redrive_unclaimed_spawns() == 0
        before = queue.latest_reservation(task_id)
        assert before and not before.startup_complete and len(calls) == 1
        if hold_at == "supervisor":
            command("hold", "--pool", lane.supervisor_id, "--reason", "stop intake")
        else:
            def deny(*_: object) -> None:
                raise WorkerAdmissionInhibited()
            monkeypatch.setattr(queue, "_spawn_admission", deny)
        pending = client.get_reservation(before.key)["ordinary_redrive"]
        with pytest.raises(DispatchError):
            observe_only({**pending, "invocation": {**pending["invocation"], "prompt": "not the original prompt"}})
        assert lane.redrive_unclaimed_spawns() == 0
        assert queue.latest_reservation(task_id) == before
        receipt_present = True
        assert lane.redrive_unclaimed_spawns() == 1
        assert len(calls) == 3 and all("--receipt-only" in argv for argv in calls[1:])
        after = queue.latest_reservation(task_id)
        assert after and after.startup_complete and after.startup_revision == before.startup_revision
        assert after.agent_info == before.agent_info and after.session_handle == before.session_handle
