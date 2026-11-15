"""Required async containment refuses execution and settles failed starts."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import agent_procutil as pu


@pytest.mark.parametrize("failure", ["missing", "raised"])
def test_assignment_failure_never_resumes(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    process = Mock(pid=123, wait=AsyncMock(return_value=-1))
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assignment = Mock(return_value=None)
    if failure == "raised":
        assignment.side_effect = OSError("fixture assignment failure")
    monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", assignment)
    resume = Mock(return_value=True)
    monkeypatch.setattr(pu, "_resume_suspended_process", resume)
    with pytest.raises((RuntimeError, OSError), match="assignment fail"):
        asyncio.run(pu.spawn_in_kill_on_close_job("fixture", require_job=True))
    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()
    resume.assert_not_called()


def test_cancellation_during_failed_start_still_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        waiting, release = asyncio.Event(), asyncio.Event()

        async def wait() -> int:
            waiting.set()
            await release.wait()
            return -1

        process = Mock(pid=123, wait=AsyncMock(side_effect=wait))
        monkeypatch.setattr(pu, "_is_windows", lambda: True)
        monkeypatch.setattr(pu.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
        monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", lambda *_a, **_kw: None)
        task = asyncio.create_task(pu.spawn_in_kill_on_close_job("fixture", require_job=True))
        await waiting.wait()
        task.cancel()
        assert not task.done()
        release.set()
        with pytest.raises(RuntimeError, match="before child resume"):
            await task
        process.wait.assert_awaited_once_with()

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_failed_assignment_settles_without_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-run"
    processes: list[asyncio.subprocess.Process] = []
    original_spawn = pu.asyncio.create_subprocess_exec

    async def record_spawn(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(pu.asyncio, "create_subprocess_exec", record_spawn)
    monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", lambda *_a, **_kw: None)
    resume = Mock(return_value=True)
    monkeypatch.setattr(pu, "_resume_suspended_process", resume)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="before child resume"):
            await asyncio.wait_for(
                pu.spawn_in_kill_on_close_job(
                    sys.executable, "-c",
                    "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                    str(marker), require_job=True, **pu.no_window_kwargs(),
                ),
                timeout=10,
            )
        assert len(processes) == 1 and processes[0].returncode is not None
        assert not marker.exists()
        resume.assert_not_called()

    asyncio.run(scenario())
