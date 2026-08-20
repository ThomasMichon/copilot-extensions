"""Redeploy is DETACH-only, not cancel (dotfiles#1661).

A frontend redeploy / cutover / shutdown is a *transport* event, not an explicit
host-agent cancel: in-flight remote turns are left running on their Session Host
(which buffers frames -- "tmux for the agent") and the successor frontend
reattaches and continues the SAME turn. Cancelling the remote task is reserved
for explicit host actions (``interrupt_turn`` / an explicit stop). The legacy
cancel-then-Resume behavior is opt-in via ``cancel_turns_on_redeploy=True``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _mgr(tmp_path, *, cancel_on_redeploy: bool = False) -> SessionManager:
    return SessionManager(
        Database(tmp_path / "r.db"),
        session_host_state_dir=str(tmp_path / "hosts"),
        cancel_turns_on_redeploy=cancel_on_redeploy,
        graceful_cancel_settle_seconds=0.1,  # keep the opt-in settle wait short
    )


def _running_session(
    mgr: SessionManager, sid: str, *, boundary: str | None = None
) -> Session:
    s = Session(sid, sid, SpawnTarget(type="command", cwd="/tmp/x"))
    s.status = SessionStatus.RUNNING
    s.acp_session_id = "acp-" + sid
    client = MagicMock()
    client.cancel_prompt = AsyncMock()
    client.shutdown = AsyncMock()
    client.is_running = True
    client.has_active_background_tasks = False
    s.client = client
    mgr._sessions[sid] = s
    if boundary:
        mgr._host_index.register(HostRecord(
            session_id=sid, port=1, host_pid=999_001, child_pid=999_001,
            boundary=boundary,
        ))
    return s


async def _never() -> None:
    await asyncio.Event().wait()


# -- graceful_cancel_for_redeploy: detach-only default -----------------------

@pytest.mark.asyncio
async def test_redeploy_default_is_detach_only_no_cancel(tmp_path) -> None:
    mgr = _mgr(tmp_path)  # default: cancel_turns_on_redeploy=False
    s = _running_session(mgr, "s1", boundary="codespace")
    res = await mgr.graceful_cancel_for_redeploy()
    s.client.cancel_prompt.assert_not_awaited()  # remote turn NOT cancelled
    assert res["mode"] == "detach-only"
    assert res["preserved"] == ["s1"]
    assert res["cancelled"] == []
    # No spurious Resume nudge is armed (nothing was cancelled).
    assert mgr._host_index.get("s1").resume_on_reattach is False


@pytest.mark.asyncio
async def test_redeploy_optin_cancels_turns(tmp_path) -> None:
    mgr = _mgr(tmp_path, cancel_on_redeploy=True)
    s = _running_session(mgr, "s1", boundary="codespace")
    res = await mgr.graceful_cancel_for_redeploy()
    s.client.cancel_prompt.assert_awaited_once()  # legacy: cancels
    assert res["mode"] == "cancel"
    assert res["cancelled"] == ["s1"]
    assert mgr._host_index.get("s1").resume_on_reattach is True


@pytest.mark.asyncio
async def test_redeploy_optin_excludes_self(tmp_path) -> None:
    # The session updating its own bridge is never cancelled.
    mgr = _mgr(tmp_path, cancel_on_redeploy=True)
    s = _running_session(mgr, "self", boundary="codespace")
    res = await mgr.graceful_cancel_for_redeploy(exclude_session_id="self")
    s.client.cancel_prompt.assert_not_awaited()
    assert res["cancelled"] == []


# -- _quiesce_session / stop_session: the cancel_turn gate -------------------

@pytest.mark.asyncio
async def test_quiesce_detach_does_not_cancel_turn(tmp_path, monkeypatch) -> None:
    import agent_bridge.session_manager as sm
    monkeypatch.setattr(sm, "_cleanup_worktree", AsyncMock())
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mgr, "_detach_host", AsyncMock())
    s = _running_session(mgr, "s1")
    s._prompt_task = asyncio.create_task(_never())
    client = s.client  # captured: _quiesce nulls session.client

    await mgr._quiesce_session(s, cancel_turn=False)

    client.cancel_prompt.assert_not_awaited()  # remote turn left running
    client.shutdown.assert_awaited()           # frontend client still torn down


@pytest.mark.asyncio
async def test_quiesce_explicit_cancels_turn(tmp_path, monkeypatch) -> None:
    import agent_bridge.session_manager as sm
    monkeypatch.setattr(sm, "_cleanup_worktree", AsyncMock())
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mgr, "_detach_host", AsyncMock())
    s = _running_session(mgr, "s1")
    s._prompt_task = asyncio.create_task(_never())
    client = s.client

    await mgr._quiesce_session(s, cancel_turn=True)

    client.cancel_prompt.assert_awaited_once()  # explicit stop cancels


@pytest.mark.asyncio
async def test_stop_session_detach_only(tmp_path, monkeypatch) -> None:
    import agent_bridge.session_manager as sm
    monkeypatch.setattr(sm, "_cleanup_worktree", AsyncMock())
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mgr, "_detach_host", AsyncMock())
    s = _running_session(mgr, "s1")
    s._prompt_task = asyncio.create_task(_never())
    client = s.client

    await mgr.stop_session("s1", cancel_turn=False)

    client.cancel_prompt.assert_not_awaited()
    assert s.status == SessionStatus.STOPPED  # detached + resumable


@pytest.mark.asyncio
async def test_stop_session_explicit_default_cancels(tmp_path, monkeypatch) -> None:
    import agent_bridge.session_manager as sm
    monkeypatch.setattr(sm, "_cleanup_worktree", AsyncMock())
    mgr = _mgr(tmp_path)
    monkeypatch.setattr(mgr, "_detach_host", AsyncMock())
    s = _running_session(mgr, "s1")
    s._prompt_task = asyncio.create_task(_never())
    client = s.client

    await mgr.stop_session("s1")  # default cancel_turn=True

    client.cancel_prompt.assert_awaited_once()


# -- drain: preserves live-host-backed turns (detach-only) ------------------

@pytest.mark.asyncio
async def test_drain_preserves_host_backed_turns(tmp_path) -> None:
    mgr = _mgr(tmp_path)  # detach-only default
    _running_session(mgr, "hosted", boundary="codespace")
    res = await mgr.drain(timeout=0.5, poll=0.05)
    # The host-backed RUNNING turn is preserved (reattach), not waited on:
    assert res["drained"] is True
    assert res["clean"] is True
    assert "hosted" not in res["busy_sessions"]


@pytest.mark.asyncio
async def test_drain_optin_waits_on_running(tmp_path) -> None:
    # Legacy opt-in: nothing is preserved, so a RUNNING turn is waited on and
    # (never settling here) times out -- the pre-#1661 behavior.
    mgr = _mgr(tmp_path, cancel_on_redeploy=True)
    _running_session(mgr, "hosted", boundary="codespace")
    res = await mgr.drain(timeout=0.3, poll=0.05, force=False)
    assert res["clean"] is False
    assert "hosted" in res["busy_sessions"]
