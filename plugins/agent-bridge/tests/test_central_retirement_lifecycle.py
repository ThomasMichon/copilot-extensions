"""Relay adoption must not join slow Host recovery or lose a late native exit."""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
import pytest
from zdd import diagnostics, process_exit, routing


@pytest.mark.asyncio
@pytest.mark.parametrize("late_exit", [False, True])
async def test_real_recovery_adopts_relay_before_pending_initial_host_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, late_exit: bool,
) -> None:
    from agent_bridge import app as app_module, central_retirement, cold_outages

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: str(pid))
    old = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41001, pid=101, version="old",
        process_start_time="101",
    )
    new, _ = routing.publish_active_with_previous(
        tmp_path, bind="127.0.0.1", port=41002, pid=202, version="new",
        process_start_time="202", demote_existing=True,
    )
    exited, attempted = threading.Event(), threading.Event()
    if not late_exit:
        exited.set()

    class Reference:
        def wait(self, timeout: float) -> bool:
            return exited.wait(timeout)

        def close(self) -> None:
            pass

    def unconfirmed(*_args: Any, **_kwargs: Any) -> dict[str, bool]:
        attempted.set()
        return {"exit_confirmed": False}

    monkeypatch.setattr(process_exit, "open_process", lambda *_a, **_kw: Reference())
    monkeypatch.setattr(central_retirement, "retire_predecessor", unconfirmed)
    recover = AsyncMock(return_value=0)
    monkeypatch.setattr(cold_outages, "recover", recover)
    relay_ready, host_ready = asyncio.Event(), asyncio.Event()
    app = FastAPI()
    app.state.config = SimpleNamespace(enable_credential_relay=True)
    app.state.ready = True
    app.state.shutting_down = False
    app.state.auth_token = "fixture-token"
    app.state.independent_runtime_start = True
    app.state.session_manager = SimpleNamespace(is_draining=False)

    async def adopt() -> bool:
        app.state.credential_relay_ready = True
        relay_ready.set()
        return True

    app.state.adopt_relay = adopt
    initial = asyncio.create_task(host_ready.wait())
    task = asyncio.create_task(app_module._retire_previous_daemon(
        app, old, new, initial_recovery_task=initial,
    ))
    try:
        if late_exit:
            assert await asyncio.to_thread(attempted.wait, 3)
            assert app.state.ready and not relay_ready.is_set()
            assert not task.done()
            exited.set()
        await asyncio.wait_for(relay_ready.wait(), 3)
        assert not initial.done() and not task.done()
        recover.assert_not_awaited()
        host_ready.set()
        await asyncio.wait_for(task, 3)
        recover.assert_awaited_once()
        assert recover.await_args is not None
        assert recover.await_args.kwargs["confirmed_only"] is False
    finally:
        exited.set()
        host_ready.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, initial, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_exit_does_not_adopt_after_successor_is_reselected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import app as app_module, central_retirement

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    old = routing.publish_active(tmp_path, bind="127.0.0.1", port=41001, pid=101, version="old")
    new, _ = routing.publish_active_with_previous(
        tmp_path, bind="127.0.0.1", port=41002, pid=202, version="new", demote_existing=True,
    )
    from dataclasses import replace
    old = replace(old, process_start_time="101")
    exited, attempted = threading.Event(), threading.Event()

    class Reference:
        def wait(self, timeout: float) -> bool:
            return exited.wait(timeout)

        def close(self) -> None:
            pass

    def unconfirmed(*_args: Any, **_kwargs: Any) -> dict[str, bool]:
        attempted.set()
        return {"exit_confirmed": False}

    monkeypatch.setattr(process_exit, "open_process", lambda *_a, **_kw: Reference())
    monkeypatch.setattr(central_retirement, "retire_predecessor", unconfirmed)
    app = FastAPI()
    app.state.independent_runtime_start = True
    app.state.auth_token = "fixture-token"
    app.state.config = SimpleNamespace(enable_credential_relay=True)
    app.state.adopt_relay = AsyncMock(return_value=True)
    task = asyncio.create_task(app_module._retire_previous_daemon(app, old, new))
    try:
        assert await asyncio.to_thread(attempted.wait, 3)
        assert new.pid is not None
        assert routing.restore_previous_if_owner(tmp_path, pid=new.pid, generation=new.generation)
        exited.set()
        await asyncio.wait_for(task, 3)
        app.state.adopt_relay.assert_not_awaited()
    finally:
        exited.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
