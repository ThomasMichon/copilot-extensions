from __future__ import annotations

import time

import pytest

from agent_dispatch.script_worker import ScriptTaskProtocolError, ScriptTaskRuntime


class FakeClient:
    def __init__(self) -> None:
        self.claim_calls = []
        self.start_calls = []
        self.complete_calls = []
        self.abandon_calls = []
        self.heartbeat_calls = 0
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def claim(self, **kwargs):
        self.claim_calls.append(kwargs)
        return {"id": kwargs["task_id"], "generation": 7}

    def start(self, task_id, worker_id):
        self.start_calls.append((task_id, worker_id))
        return {
            "id": task_id,
            "generation": 7,
            "owner_session_id": "owner-session-1",
        }

    def heartbeat(self, task_id, worker_id):
        self.heartbeat_calls += 1
        return {
            "id": task_id,
            "generation": 7,
            "owner_session_id": "owner-session-1",
        }

    def progress(self, *args, **kwargs):
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def set_card(self, *args, **kwargs):
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def steer_take(self, *args, **kwargs):
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def complete(self, task_id, worker_id, **kwargs):
        self.complete_calls.append((task_id, worker_id, kwargs))
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def abandon(self, task_id, **kwargs):
        self.abandon_calls.append((task_id, kwargs))
        return {"generation": 7, "owner_session_id": "owner-session-1"}


def test_report_success_uses_generation_and_owner_session_fences():
    client = FakeClient()
    runtime = ScriptTaskRuntime(client=client, task_id="task-1", worker_id="script-1")

    runtime.announce_start()
    runtime.report_success({"ok": True})
    runtime.close()

    _task_id, _worker_id, kwargs = client.complete_calls[0]
    assert kwargs["expected_generation"] == 7
    assert kwargs["expected_owner_session_id"] == "owner-session-1"


def test_implicit_return_abandons_and_removes_task_file(tmp_path):
    client = FakeClient()
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")
    runtime = ScriptTaskRuntime(
        client=client,
        task_id="task-1",
        worker_id="script-1",
        task_file=str(task_file),
    )

    with pytest.raises(ScriptTaskProtocolError):
        with runtime as worker:
            worker.announce_start()

    assert client.abandon_calls
    assert "without report_success() or report_failure()" in client.abandon_calls[0][1]["reason"]
    assert not task_file.exists()
    assert client.closed is True


def test_exception_path_abandons_and_heartbeats(tmp_path):
    client = FakeClient()
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")
    runtime = ScriptTaskRuntime(
        client=client,
        task_id="task-1",
        worker_id="script-1",
        task_file=str(task_file),
        heartbeat_seconds=0.01,
    )

    with pytest.raises(ValueError, match="boom"):
        with runtime as worker:
            worker.announce_start()
            time.sleep(0.05)
            raise ValueError("boom")

    assert client.heartbeat_calls >= 1
    assert client.abandon_calls
    assert "ValueError: boom" in client.abandon_calls[0][1]["reason"]
    assert not task_file.exists()
