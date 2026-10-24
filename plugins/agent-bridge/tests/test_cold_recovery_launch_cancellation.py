"""A cancelled await cannot erase a real, still-running native launcher thread."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from zdd import diagnostics, process_exit

from agent_bridge import cold_outages
from agent_bridge.session_cold_drain import cold_drain
from agent_bridge.session_host import spawner
from agent_bridge.session_host.launcher import HostHandle
from agent_bridge.session_manager import SessionManager
from test_cold_drain import cleanup, live_session, messages, until


async def refuse_unsettled_launch(manager: SessionManager, session_id: str) -> None:
    result = await cold_drain(
        manager, timeout=0, poll=0.01, source="fixture",
        reason=None, exclude_session_id=None,
    )
    assert not result["drained"], result
    assert {
        "session_id": session_id,
        "reason": "native replacement launch outcome remains unresolved",
    } in result["failures"]
    assert cold_outages.latest(manager.db, session_id)["recovery_phase"] == "launching"


@pytest.mark.asyncio
@pytest.mark.parametrize("observer", ["same", "rehydrated", "orphan"])
async def test_cancelled_real_launcher_thread_remains_in_drain_census(
    session_manager: SessionManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    observer: str,
) -> None:
    successor = None
    inspecting = None
    recovering = None
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    handles: list[HostHandle] = []
    errors: list[BaseException] = []
    try:
        session, transcript = await live_session(session_manager, tmp_path, monkeypatch)
        result = await cold_drain(
            session_manager, timeout=10, poll=0.01, source="fixture",
            reason=None, exclude_session_id=None,
        )
        assert result["drained"], result
        successor = SessionManager(session_manager.db)
        successor._generation_id = "fixture-successor"
        launch = spawner.launch_session_host

        def blocked_launch(*args: Any, **kwargs: Any) -> HostHandle:
            entered.set()
            try:
                if not release.wait(timeout=30):
                    raise TimeoutError("fixture did not release its native launcher")
                handle = launch(*args, **kwargs)
                handles.append(handle)
                return handle
            except BaseException as exc:
                errors.append(exc)
                raise
            finally:
                finished.set()

        monkeypatch.setattr(spawner, "launch_session_host", blocked_launch)
        recovering = asyncio.create_task(successor.resume_session(session.session_id))
        await until(entered.is_set)
        assert successor._active_acquisitions == 1
        assert cold_outages.latest(successor.db, session.session_id)["recovery_phase"] == "launching"
        recovering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await recovering
        assert successor._active_acquisitions == 0
        assert not finished.is_set()
        inspecting = (
            SessionManager(successor.db, read_only_rehydrate=True)
            if observer == "rehydrated" else successor
        )
        if observer == "orphan":
            inspecting._sessions.pop(session.session_id)
        await refuse_unsettled_launch(inspecting, session.session_id)
        release.set()
        assert await asyncio.to_thread(finished.wait, 30)
        assert not errors, errors
        assert len(handles) == 1 and handles[0].proc.poll() is None
        await until(lambda: len([row for row in messages(transcript) if "boot" in row]) == 2)
        assert not [
            row for row in messages(transcript) if row.get("method") == "session/prompt"
        ]
        await refuse_unsettled_launch(inspecting, session.session_id)
        assert cold_outages.unsettled_launches(inspecting.db) == [session.session_id]
    finally:
        release.set()
        if entered.is_set():
            assert await asyncio.to_thread(finished.wait, 120)
        if recovering is not None and not recovering.done():
            recovering.cancel()
            await asyncio.gather(recovering, return_exceptions=True)
        for handle in handles:
            state = json.loads(Path(handle.state_file).read_text())
            for name in ("child", "host"):
                pid, birth = state[name + "_pid"], state[name + "_process_start_time"]
                if not process_exit.observe_original_exit(pid, birth):
                    result = await asyncio.to_thread(
                        diagnostics.terminate_pid_if_identity, pid, birth, exit_timeout=5,
                    )
                    assert result.get("exit_confirmed") or process_exit.observe_original_exit(
                        pid, birth,
                    ), result
            await asyncio.to_thread(handle.proc.wait, timeout=5)
        if inspecting is not None and inspecting is not successor:
            await cleanup(inspecting)
        if successor is not None:
            await cleanup(successor)
        await cleanup(session_manager)
