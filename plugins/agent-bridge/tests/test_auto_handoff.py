"""Tests for the opt-in, context-pressure-driven in-place handoff policy.

Covers the proactive (usage-driven) trigger and the prompt-triggered (E2)
trigger layered on top of the ``handoff_session`` primitive, plus the fail-safe
default (no opt-in -> pressure changes nothing).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.db import Database
from agent_bridge.models import AutoHandoffPolicy, SessionStatus
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _mock_agent_proc():
    proc = MagicMock()
    proc.proc = MagicMock()
    proc.proc.pid = 12345
    proc.proc.returncode = None
    proc.proc.stdin = MagicMock()
    proc.proc.stdout = MagicMock()
    proc.proc.stderr = MagicMock()
    proc.proc.stderr.readline = AsyncMock(return_value=b"")
    return proc


@pytest.fixture
def _patch_spawn():
    with patch("agent_bridge.session_manager.spawn") as mock_spawn:
        mock_spawn.return_value = _mock_agent_proc()
        yield mock_spawn


@pytest.fixture
def _patch_acp(mock_acp_client):
    # Session Hosts are always on (dotfiles#1478): a local start now connects via
    # _connect_via_session_host, which can't stand up in a unit test. Stub it to
    # return the mock client + a stable acp id so the predecessor start lands IDLE.
    async def _fake_host_connect(self, target, **kwargs):
        return mock_acp_client, mock_acp_client.acp_session_id

    with patch("agent_bridge.session_manager.AcpClient") as mock_cls, \
            patch.object(
                SessionManager, "_connect_via_session_host", _fake_host_connect
            ):
        mock_cls.return_value = mock_acp_client
        yield mock_cls


def _events(session, event_type):
    return [e for e in session.event_log.get_events() if e.event == event_type]


def _sm(tmp_db: Database, **policy) -> SessionManager:
    """A SessionManager with an explicit auto-handoff policy."""
    return SessionManager(tmp_db, auto_handoff=AutoHandoffPolicy(**policy))


def _cross_critical(sm: SessionManager, session: Session) -> None:
    """Drive a usage update that crosses the critical threshold."""
    sm._handle_usage_update(
        session, {"context_size": 100, "context_used": 95, "model": "m"}
    )


async def _drain_auto_tasks(sm: SessionManager) -> None:
    if sm._auto_handoff_tasks:
        await asyncio.gather(*list(sm._auto_handoff_tasks))


class TestOptInFailSafe:
    """Absent an explicit opt-in, context pressure changes nothing."""

    @pytest.mark.asyncio
    async def test_critical_without_optin_does_not_hand_off(
        self, tmp_db, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        sm = SessionManager(tmp_db)  # policy off by default
        pred = await sm.start_session(spawn_target, caller_id="wt-1")

        _cross_critical(sm, pred)

        # The warning is still emitted, but no handoff is owed or scheduled.
        assert _events(pred, "context_critical")
        assert pred._handoff_pending is False
        assert not sm._auto_handoff_tasks

    def test_single_checkout_agent_is_ineligible(self, tmp_db) -> None:
        sm = _sm(tmp_db, enabled=True)
        cmd_target = SpawnTarget(type="command", spawn_command=["x"])
        s = Session("id", "n", cmd_target, agent_name="cs", caller_id="wt")
        assert sm._auto_handoff_eligible(s) is False

    def test_local_worktree_agent_is_eligible_when_enabled(self, tmp_db) -> None:
        sm = _sm(tmp_db, enabled=True)
        s = Session("id", "n", SpawnTarget(type="local", cwd="/tmp/x"),
                    caller_id="wt")
        assert sm._auto_handoff_eligible(s) is True


class TestProactiveHandoff:
    """The usage-driven trigger fires an in-place cutover when idle."""

    @pytest.mark.asyncio
    async def test_fires_when_idle_and_unwatched(
        self, tmp_db, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        mock_acp_client.send_prompt = AsyncMock(return_value={
            "response_text": "## Objective\nShip it\n## Next steps\nGo",
            "stop_reason": "end_turn",
        })
        sm = _sm(tmp_db, enabled=True)
        pred = await sm.start_session(spawn_target, caller_id="wt-1")
        assert pred.status == SessionStatus.IDLE
        pred.subscriber_count = 0

        _cross_critical(sm, pred)  # idle => scheduled immediately
        assert sm._auto_handoff_tasks
        await _drain_auto_tasks(sm)

        # Predecessor retired (resumable), changeover announced, successor up.
        assert pred.status == SessionStatus.STOPPED
        ho = _events(pred, "session_handoff")
        assert ho and ho[0].data["reason"] == "context-pressure"
        succ = sm.get_session(ho[0].data["rolled_to"])
        assert succ is not None and succ.caller_id == "wt-1"
        if succ._prompt_task is not None:
            await succ._prompt_task

    @pytest.mark.asyncio
    async def test_deferred_when_watched(
        self, tmp_db, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        sm = _sm(tmp_db, enabled=True)  # unwatched_only defaults True
        pred = await sm.start_session(spawn_target, caller_id="wt-1")
        pred.subscriber_count = 1  # a human is attached

        _cross_critical(sm, pred)

        # Owed, but not fired: a watched session is not rolled out from under.
        assert pred._handoff_pending is True
        assert not sm._auto_handoff_tasks

    @pytest.mark.asyncio
    async def test_watched_fires_when_unwatched_only_disabled(
        self, tmp_db, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        mock_acp_client.send_prompt = AsyncMock(return_value={
            "response_text": "## Objective\nX", "stop_reason": "end_turn",
        })
        sm = _sm(tmp_db, enabled=True, unwatched_only=False)
        pred = await sm.start_session(spawn_target, caller_id="wt-1")
        pred.subscriber_count = 3  # attached, but policy ignores that

        _cross_critical(sm, pred)
        assert sm._auto_handoff_tasks
        await _drain_auto_tasks(sm)

        assert pred.status == SessionStatus.STOPPED
        succ = sm.get_session(_events(pred, "session_handoff")[0].data["rolled_to"])
        if succ and succ._prompt_task is not None:
            await succ._prompt_task


class TestPromptTriggeredHandoff:
    """A prompt into a saturated session hands off first, then delivers it."""

    @pytest.mark.asyncio
    async def test_prompt_rolls_then_delivers_to_successor(
        self, tmp_db, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        mock_acp_client.send_prompt = AsyncMock(return_value={
            "response_text": "## Objective\nX", "stop_reason": "end_turn",
        })
        sm = _sm(tmp_db, enabled=True)
        pred = await sm.start_session(spawn_target, caller_id="wt-1")
        pred._crossed_thresholds.add("critical")  # already saturated
        pred.subscriber_count = 2  # prompt path fires regardless of watchers

        result = await sm.submit_or_queue_prompt(pred.session_id, "next thing")

        # Predecessor retired; the user's prompt landed on a fresh successor.
        assert pred.status == SessionStatus.STOPPED
        succ = sm.get_session(_events(pred, "session_handoff")[0].data["rolled_to"])
        assert succ is not None and succ.session_id != pred.session_id
        # The successor is warm (seed turn running), so the user's prompt is
        # durably queued behind it rather than dropped.
        assert result["queued"] is True
        assert sm._db.count_pending_prompts(succ.session_id) == 1
        assert sm._db.count_pending_prompts(pred.session_id) == 0

    @pytest.mark.asyncio
    async def test_prompt_without_optin_is_normal_delivery(
        self, tmp_db, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        sm = SessionManager(tmp_db)  # policy off
        pred = await sm.start_session(spawn_target, caller_id="wt-1")
        pred._crossed_thresholds.add("critical")

        result = await sm.submit_or_queue_prompt(pred.session_id, "next thing")

        # No handoff: the prompt runs on the same (saturated) session.
        assert pred.status != SessionStatus.STOPPED
        assert result["queued"] is False
        assert not _events(pred, "session_handoff")
