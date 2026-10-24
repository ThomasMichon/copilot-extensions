"""Cold recovery participates in drain without admitting unrelated prompts."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from zdd import routing

from agent_bridge import cold_outages
from agent_bridge.app import _recover_owned_cold
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.routes.sessions import router as sessions_router
from agent_bridge.session_cold_drain import cold_drain
from agent_bridge.session_manager import DaemonDrainingError, Session, SessionManager
from agent_bridge.transport import SpawnTarget
from test_cold_drain import NATIVE, cleanup, live_session, messages, until

RecoveryFixture = tuple[SessionManager, str, Path]


def admitted_reconnect(
    manager: SessionManager,
    session_id: str,
    delivery_id: str | None,
    *,
    prompt: str = cold_outages.RECONNECT_PROMPT,
    no_resume: bool = True,
) -> bool:
    return cold_outages.reconnect_admitted_before_drain(
        manager,
        session_id,
        delivery_id,
        prompt=prompt,
        no_resume=no_resume,
    )


@pytest_asyncio.fixture
async def interrupted_successor(
    session_manager: SessionManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[RecoveryFixture]:
    successor = None
    try:
        session, transcript = await live_session(session_manager, tmp_path, monkeypatch)
        result = await cold_drain(
            session_manager,
            timeout=10,
            poll=0.01,
            source="fixture",
            reason=None,
            exclude_session_id=None,
        )
        assert result["drained"], result
        successor = SessionManager(session_manager.db)
        successor._generation_id = "fixture-successor"
        yield successor, session.session_id, transcript
    finally:
        if successor is not None:
            await cleanup(successor)
        await cleanup(session_manager)


@pytest.mark.asyncio
async def test_cold_recovery_closed_drain_has_no_launch_or_unknown_delivery(
    interrupted_successor: RecoveryFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session_id, transcript = interrupted_successor
    manager.set_draining(True)
    before = cold_outages.latest(manager.db, session_id)
    assert await cold_outages.recover(manager, automatic=True) == 0
    with pytest.raises(DaemonDrainingError):
        await manager.resume_session(session_id)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    endpoint = routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=44002,
        pid=os.getpid(),
        version="fixture",
    )
    app = FastAPI()
    app.state.session_manager = manager
    app.state.bound_port = endpoint.port
    app.state.ready = True
    app.state.cold_recovery_ready = True
    assert await _recover_owned_cold(app, automatic=True) == 0
    assert cold_outages.latest(manager.db, session_id) == before
    assert getattr(manager, "_active_acquisitions", 0) == 0
    assert len([message for message in messages(transcript) if "boot" in message]) == 1
    assert not [
        message for message in messages(transcript) if message.get("method") == "session/prompt"
    ]


@pytest.mark.asyncio
async def test_cold_recovery_entered_before_drain_finishes_same_native_reconnect(
    interrupted_successor: RecoveryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session_id, transcript = interrupted_successor
    entered, release = asyncio.Event(), asyncio.Event()
    connect = manager._connect_via_session_host

    async def blocked_connect(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return await connect(*args, **kwargs)

    monkeypatch.setattr(manager, "_connect_via_session_host", blocked_connect)
    recovering = asyncio.create_task(cold_outages.recover(manager, automatic=True))
    draining = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert manager._active_acquisitions == 1
        draining = asyncio.create_task(manager.drain(timeout=10, poll=0.01))
        await until(lambda: manager.is_draining)
        await asyncio.sleep(0.03)
        assert not draining.done()
        release.set()
        assert await asyncio.wait_for(recovering, timeout=10) == 1
        session = manager.get_session(session_id)
        if session._prompt_task is not None:
            await session._prompt_task
        result = await draining
        assert result["drained"] and result["clean"], result
        assert manager._active_acquisitions == 0
        assert cold_outages.latest(manager.db, session_id)["state"] == "reconnected"
        assert session.acp_session_id == NATIVE
        prompts = [
            message
            for message in messages(transcript)
            if message.get("method") == "session/prompt"
        ]
        assert len(prompts) == 1
        assert prompts[0]["params"]["sessionId"] == NATIVE
        assert len([message for message in messages(transcript) if "boot" in message]) == 2
    finally:
        release.set()
        await asyncio.gather(
            recovering,
            *([draining] if draining is not None else []),
            return_exceptions=True,
        )


@pytest.fixture
def dormant_recovery(
    session_manager: SessionManager,
    tmp_path: Path,
    mock_acp_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SessionManager, str, MagicMock, AsyncMock]:
    import agent_bridge.session_manager as core

    target = SpawnTarget(type="local", cwd=str(tmp_path))
    session = Session("dormant-session", "dormant", target)
    session.status = SessionStatus.STOPPED
    session.acp_session_id = NATIVE
    session_manager.db.create_session(
        session.session_id,
        session.name,
        None,
        target.cwd,
        "local",
        "stopped",
        1,
        acp_session_id=NATIVE,
        target_json=target.to_json(),
    )
    session.event_log = EventLog(db=session_manager.db, session_id=session.session_id)
    session_manager._sessions[session.session_id] = session
    session_manager._try_reattach_live_host = AsyncMock(return_value=False)
    session_manager._resume_via_new_remote_host = AsyncMock(return_value=None)
    session_manager._notify_host_reapable = AsyncMock()
    mock_acp_client.acp_session_id = NATIVE
    process = MagicMock()
    process.stderr.readline = AsyncMock(return_value=b"")
    spawn = AsyncMock(return_value=SimpleNamespace(proc=process))
    monkeypatch.setattr(core, "spawn", spawn)
    monkeypatch.setattr(core, "AcpClient", lambda **_kwargs: mock_acp_client)
    return session_manager, session.session_id, mock_acp_client, spawn


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["resume", "resync"])
async def test_ordinary_recovery_closed_cold_gate_is_503_without_effects(
    dormant_recovery: tuple[SessionManager, str, MagicMock, AsyncMock],
    entrypoint: str,
) -> None:
    manager, session_id, client, spawn = dormant_recovery
    result = await cold_drain(
        manager,
        timeout=0,
        poll=0.01,
        source="fixture",
        reason=None,
        exclude_session_id=None,
    )
    assert result["drained"] and manager.is_draining
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(sessions_router)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://fixture",
        ) as transport:
            response = await transport.post(f"/api/v1/sessions/{session_id}/{entrypoint}")
        assert response.status_code == 503
        assert "draining" in response.json()["detail"]
        spawn.assert_not_awaited()
        client.start.assert_not_awaited()
        client.shutdown.assert_not_awaited()
        manager._try_reattach_live_host.assert_not_awaited()
        assert cold_outages.latest(manager.db, session_id) is None
        assert manager.db.get_session(session_id)["status"] == "stopped"
        assert getattr(manager, "_active_acquisitions", 0) == 0
    finally:
        manager.set_draining(False)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["resume", "resync"])
@pytest.mark.parametrize("destructive", [False, True])
async def test_ordinary_recovery_entered_before_drain_is_counted(
    dormant_recovery: tuple[SessionManager, str, MagicMock, AsyncMock],
    entrypoint: str,
    destructive: bool,
) -> None:
    manager, session_id, _client, spawn = dormant_recovery
    entered, release = asyncio.Event(), asyncio.Event()
    process = spawn.return_value

    async def blocked_spawn(*_args: Any, **_kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        return process

    spawn.side_effect = blocked_spawn
    method = manager.resume_session if entrypoint == "resume" else manager.resync_session
    recovering = asyncio.create_task(method(session_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert manager._active_acquisitions == 1
        if destructive:
            result = await cold_drain(
                manager,
                timeout=0,
                poll=0.01,
                source="fixture",
                reason=None,
                exclude_session_id=None,
            )
        else:
            result = await manager.drain(timeout=0, poll=0.01)
        assert not result["drained"]
        assert manager._active_acquisitions == 1 and not recovering.done()
        release.set()
        await recovering
        assert manager._active_acquisitions == 0
        assert manager.get_session(session_id).status == SessionStatus.IDLE
        assert manager.get_session(session_id).acp_session_id == NATIVE
        spawn.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(recovering, return_exceptions=True)
        manager.set_draining(False)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["resume", "resync"])
async def test_ordinary_recovery_cancellation_releases_drain_accounting(
    dormant_recovery: tuple[SessionManager, str, MagicMock, AsyncMock],
    entrypoint: str,
) -> None:
    manager, session_id, _client, spawn = dormant_recovery
    entered = asyncio.Event()

    async def blocked_spawn(*_args: Any, **_kwargs: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    spawn.side_effect = blocked_spawn
    method = manager.resume_session if entrypoint == "resume" else manager.resync_session
    recovering = asyncio.create_task(method(session_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert manager._active_acquisitions == 1
    finally:
        recovering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await recovering
    assert manager._active_acquisitions == 0


@pytest.mark.asyncio
async def test_cold_recovery_admission_is_counted_by_destructive_drain(
    interrupted_successor: RecoveryFixture,
) -> None:
    manager, session_id, _transcript = interrupted_successor
    with cold_outages.recovery_admission(manager, session_id):
        result = await cold_drain(
            manager,
            timeout=0,
            poll=0.01,
            source="fixture",
            reason=None,
            exclude_session_id=None,
        )
        assert not result["drained"]
        assert result["failures"] == [
            {"session_id": "", "reason": "in-flight acquisition remains fenced"},
            {
                "session_id": "",
                "reason": "cold drain gate or execution census did not remain quiescent",
            },
        ]
    assert manager._active_acquisitions == 0
    assert cold_outages.latest(manager.db, session_id)["state"] == "interrupted"


@pytest.mark.asyncio
async def test_cold_recovery_cancellation_releases_drain_accounting(
    interrupted_successor: RecoveryFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session_id, _transcript = interrupted_successor
    entered = asyncio.Event()

    async def blocked_connect(*_args: Any, **_kwargs: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_connect_via_session_host", blocked_connect)
    recovering = asyncio.create_task(manager.resume_session(session_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert manager._active_acquisitions == 1
    finally:
        recovering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await recovering
    assert manager._active_acquisitions == 0
    assert cold_outages.latest(manager.db, session_id)["state"] == "interrupted"


@pytest.mark.asyncio
async def test_cold_recovery_drain_exception_requires_exact_task_and_kernel_custody(
    interrupted_successor: RecoveryFixture,
) -> None:
    manager, session_id, _transcript = interrupted_successor
    row = cold_outages.latest(manager.db, session_id)
    delivery_id = "cold-outage:" + row["outage_id"]
    with cold_outages.recovery_admission(manager, session_id):
        with pytest.raises(ValueError, match="kernel operation custody"):
            admitted_reconnect(manager, session_id, delivery_id)
        with cold_outages.operation(manager, session_id):
            manager.set_draining(True)
            assert admitted_reconnect(manager, session_id, delivery_id)
            assert not admitted_reconnect(manager, session_id, None)
            assert not admitted_reconnect(manager, session_id, delivery_id + "-other")
            assert not admitted_reconnect(
                manager,
                session_id,
                delivery_id,
                prompt="unrelated work",
                no_resume=True,
            )
            assert not admitted_reconnect(
                manager,
                session_id,
                delivery_id,
                prompt=cold_outages.RECONNECT_PROMPT,
                no_resume=False,
            )

            async def inherited_task() -> bool:
                return admitted_reconnect(manager, session_id, delivery_id)

            assert not await asyncio.create_task(inherited_task())
    assert not admitted_reconnect(manager, session_id, delivery_id)
    assert manager._active_acquisitions == 0
