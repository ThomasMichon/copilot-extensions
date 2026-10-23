"""Cold recovery participates in drain without admitting unrelated prompts."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from zdd import routing

from agent_bridge import cold_outages
from agent_bridge.app import _recover_owned_cold
from agent_bridge.session_cold_drain import cold_drain
from agent_bridge.session_manager import DaemonDrainingError, SessionManager
from test_cold_drain import NATIVE, cleanup, live_session, messages, until

RecoveryFixture = tuple[SessionManager, str, Path]


def admitted_reconnect(
    manager: SessionManager, session_id: str, delivery_id: str | None, *,
    prompt: str = cold_outages.RECONNECT_PROMPT, no_resume: bool = True,
) -> bool:
    return cold_outages.reconnect_admitted_before_drain(
        manager, session_id, delivery_id, prompt=prompt, no_resume=no_resume,
    )


@pytest_asyncio.fixture
async def interrupted_successor(
    session_manager: SessionManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[RecoveryFixture]:
    successor = None
    try:
        session, transcript = await live_session(session_manager, tmp_path, monkeypatch)
        result = await cold_drain(
            session_manager, timeout=10, poll=0.01, source="fixture",
            reason=None, exclude_session_id=None,
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
    interrupted_successor: RecoveryFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session_id, transcript = interrupted_successor
    manager.set_draining(True)
    before = cold_outages.latest(manager.db, session_id)
    assert await cold_outages.recover(manager, automatic=True) == 0
    with pytest.raises(DaemonDrainingError):
        await manager.resume_session(session_id)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    endpoint = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=44002, pid=os.getpid(), version="fixture",
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
    assert not [message for message in messages(transcript) if message.get("method") == "session/prompt"]


@pytest.mark.asyncio
async def test_cold_recovery_entered_before_drain_finishes_same_native_reconnect(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
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
            message for message in messages(transcript)
            if message.get("method") == "session/prompt"
        ]
        assert len(prompts) == 1
        assert prompts[0]["params"]["sessionId"] == NATIVE
        assert len([message for message in messages(transcript) if "boot" in message]) == 2
    finally:
        release.set()
        await asyncio.gather(
            recovering, *([draining] if draining is not None else []),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_cold_recovery_admission_is_counted_by_destructive_drain(
    interrupted_successor: RecoveryFixture,
) -> None:
    manager, session_id, _transcript = interrupted_successor
    with cold_outages.recovery_admission(manager, session_id):
        result = await cold_drain(
            manager, timeout=0, poll=0.01, source="fixture",
            reason=None, exclude_session_id=None,
        )
        assert not result["drained"]
        assert result["failures"] == [
            {"session_id": "", "reason": "in-flight acquisition remains fenced"},
            {"session_id": "", "reason": "cold drain gate or execution census did not remain quiescent"},
        ]
    assert manager._active_acquisitions == 0
    assert cold_outages.latest(manager.db, session_id)["state"] == "interrupted"


@pytest.mark.asyncio
async def test_cold_recovery_cancellation_releases_drain_accounting(
    interrupted_successor: RecoveryFixture, monkeypatch: pytest.MonkeyPatch,
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
                manager, session_id, delivery_id, prompt="unrelated work", no_resume=True,
            )
            assert not admitted_reconnect(
                manager, session_id, delivery_id, prompt=cold_outages.RECONNECT_PROMPT,
                no_resume=False,
            )

            async def inherited_task() -> bool:
                return admitted_reconnect(manager, session_id, delivery_id)

            assert not await asyncio.create_task(inherited_task())
    assert not admitted_reconnect(manager, session_id, delivery_id)
    assert manager._active_acquisitions == 0
