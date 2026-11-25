"""Worker admission refuses before spawn and retains TaskRunner settlement."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_index.indexing import runner
from agent_index.indexing.task_store import TaskRecord, TaskStore
from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError


@pytest.mark.parametrize(
    "error", [FileNotFoundError("missing UV"), RuntimeError("different runtime"),
              OwnedCleanupError(None, phase="test")],
    ids=["missing", "different", "cleanup"],
)
def test_prepared_worker_refuses_before_log_or_spawn(
    monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    attempts: list[str] = []
    selected_uv = "prepared UV binding"
    monkeypatch.setenv("AGENT_INDEX_UV", selected_uv)

    def refuse(python: str, *, uv: str | None = None) -> str:
        assert uv == selected_uv
        attempts.append(python)
        raise error

    class Store:
        @property
        def data_dir(self) -> Path:
            pytest.fail("worker log touched before admission")

    instance = cast(runner.TaskRunner, SimpleNamespace(store=Store()))
    monkeypatch.setattr(runner, "resolve_runtime_python", refuse)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("worker started"),
    )
    with pytest.raises(type(error)) as raised:
        runner.TaskRunner._spawn_worker(instance, "synthetic-task")
    assert raised.value is error
    assert len(attempts) == 1


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("prepared UV missing"),
        RuntimeError("different selected environment"),
        subprocess.TimeoutExpired("uv", 10),
        OutputLimitExceeded("stdout", 65536),
        UnicodeError("invalid lookup output"),
        OwnedCleanupError(None, phase="test"),
    ],
    ids=["missing", "different", "deadline", "output", "decoding", "cleanup"],
)
def test_admission_refusal_settles_without_worker_or_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, error: Exception,
) -> None:
    claimed = cast(TaskRecord, SimpleNamespace(id="task", source="synthetic-source"))
    updates: list[tuple[str, str, str]] = []
    store = cast(TaskStore, SimpleNamespace(
        data_dir=tmp_path,
        update_status=lambda *args: updates.append(args),
    ))
    events: list[tuple[str, dict[str, Any]]] = []

    class Bus:
        def publish(self, event: str, payload: dict[str, Any]) -> None:
            events.append((event, payload))

    instance = runner.TaskRunner(store, Bus())
    source_lock = threading.Lock()
    source_lock.acquire()
    attempts: list[str] = []
    caller_thread = threading.get_ident()

    async def acquire(source: str) -> Any:
        assert source == claimed.source
        return source_lock

    def refuse(python: str, *, uv: str | None = None) -> str:
        assert threading.get_ident() != caller_thread
        attempts.append(python)
        raise error

    instance._source_lock_acquire = acquire
    monkeypatch.setattr(runner, "resolve_runtime_python", refuse)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("worker started"),
    )
    asyncio.run(instance._launch_worker(claimed))
    assert updates == [(claimed.id, "failed", "worker spawn failed")]
    assert len(attempts) == 1
    assert not (tmp_path / "worker.log").exists()
    assert not source_lock.locked()
    assert not instance._worker_locks and not instance._active_workers
    assert not instance._worker_procs and not instance._monitors
    assert not events
    assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)
