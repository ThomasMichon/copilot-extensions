from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

import pytest

from agent_dispatch.script_worker import ScriptTaskProtocolError, ScriptTaskRuntime
from agent_dispatch.client import DispatchClient


class FakeClient:
    def __init__(self) -> None:
        self.claim_calls: list[dict[str, Any]] = []
        self.start_calls: list[tuple[str, str]] = []
        self.complete_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.failure_reports: list[dict[str, Any]] = []
        self.abandon_calls: list[dict[str, Any]] = []
        self.heartbeat_calls = 0
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def claim(self, **kwargs: Any) -> dict[str, Any]:
        self.claim_calls.append(kwargs)
        return {"id": kwargs["task_id"], "generation": 7}

    def start(self, task_id: str, worker_id: str) -> dict[str, Any]:
        self.start_calls.append((task_id, worker_id))
        return {
            "id": task_id,
            "generation": 7,
            "owner_session_id": "owner-session-1",
        }

    def heartbeat(self, task_id: str, worker_id: str) -> dict[str, Any]:
        self.heartbeat_calls += 1
        return {
            "id": task_id,
            "generation": 7,
            "owner_session_id": "owner-session-1",
        }

    def progress(self, *args: object, **kwargs: Any) -> dict[str, Any]:
        self.failure_reports.append(kwargs)
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def set_card(self, *args: object, **kwargs: Any) -> dict[str, Any]:
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def steer_take(self, *args: object, **kwargs: Any) -> dict[str, Any]:
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def complete(self, task_id: str, worker_id: str, **kwargs: Any) -> dict[str, Any]:
        self.complete_calls.append((task_id, worker_id, kwargs))
        return {"generation": 7, "owner_session_id": "owner-session-1"}

    def abandon(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        self.abandon_calls.append(kwargs)
        pytest.fail("the running script cannot certify its own exit")

def test_report_success_uses_generation_and_owner_session_fences() -> None:
    client = FakeClient()
    runtime = ScriptTaskRuntime(client=cast(DispatchClient, client), task_id="task-1", worker_id="script-1")

    runtime.announce_start()
    runtime.report_success({"ok": True})
    runtime.close()

    _task_id, _worker_id, kwargs = client.complete_calls[0]
    assert kwargs["expected_generation"] == 7
    assert kwargs["expected_owner_session_id"] == "owner-session-1"


def test_implicit_return_reports_failure_and_removes_task_file(tmp_path: Path) -> None:
    client = FakeClient()
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")
    runtime = ScriptTaskRuntime(
        client=cast(DispatchClient, client),
        task_id="task-1",
        worker_id="script-1",
        task_file=str(task_file),
    )

    with pytest.raises(ScriptTaskProtocolError):
        with runtime as worker:
            worker.announce_start()

    assert len(client.failure_reports) == 1
    assert "without report_success() or report_failure()" in client.failure_reports[0]["summary"]
    assert not task_file.exists()
    assert client.closed is True


@pytest.mark.parametrize("with_exception", [False, True])
def test_unknown_failure_delivery_preserves_outcome_and_never_resends(
    monkeypatch: pytest.MonkeyPatch, with_exception: bool,
) -> None:
    client = FakeClient()
    runtime = ScriptTaskRuntime(cast(DispatchClient, client), "task", "script-one", heartbeat_seconds=0)
    calls = []

    def lost(*args: object, **kwargs: Any) -> None:
        calls.append(kwargs)
        raise OSError("reply lost")

    monkeypatch.setattr(client, "progress", lost)
    if with_exception:
        with pytest.raises(ValueError, match="original"), runtime:
            runtime.announce_start()
            raise ValueError("original")
    else:
        with pytest.raises(OSError, match="reply lost"), runtime:
            runtime.announce_start()
            runtime.report_failure("failure")
    with pytest.raises(ScriptTaskProtocolError):
        runtime.report_failure("failure")
    assert len(calls) == 1 and not client.abandon_calls and client.closed


def test_exception_path_reports_failure_and_heartbeats(tmp_path: Path) -> None:
    client = FakeClient()
    task_file = tmp_path / "task.json"
    task_file.write_text("{}", encoding="utf-8")
    runtime = ScriptTaskRuntime(
        client=cast(DispatchClient, client),
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
    assert client.failure_reports == [{"phase": "script-failure", "summary": "ValueError: boom"}]
    assert not task_file.exists()
