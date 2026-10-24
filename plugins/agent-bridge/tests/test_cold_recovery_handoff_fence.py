"""Cold reconnect disposition must precede every context-handoff admission."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_bridge import cold_outages
from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.models import AutoHandoffPolicy, SessionStatus
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget

HandoffFixture = tuple[SessionManager, Session, MagicMock]


@pytest.fixture
def handoff_context(
    tmp_db: Database, spawn_target: SpawnTarget, mock_acp_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> HandoffFixture:
    manager = SessionManager(tmp_db, auto_handoff=AutoHandoffPolicy(enabled=True))
    session = Session("predecessor", "predecessor", spawn_target, caller_id="worktree")
    session.status = SessionStatus.IDLE
    session.acp_session_id = mock_acp_client.acp_session_id
    session.client = mock_acp_client
    tmp_db.create_session(
        session.session_id, session.name, None, spawn_target.cwd, "local", "idle", 1,
        acp_session_id=session.acp_session_id, target_json=spawn_target.to_json(),
    )
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    monkeypatch.setattr(manager, "_notify_host_reapable", AsyncMock())
    monkeypatch.setattr(manager, "resume_session", AsyncMock())
    monkeypatch.setattr(manager, "start_session", AsyncMock())
    monkeypatch.setattr(manager, "stop_session", AsyncMock())
    return manager, session, mock_acp_client


def _outage(
    manager: SessionManager, session: Session, state: str, *,
    evidence: dict[str, Any] | None = None,
) -> None:
    manager.db.execute_write(
        "INSERT INTO cold_outages "
        "(outage_id,session_id,native_session_id,target_json,previous_status,"
        "owner_generation,execution_json,interrupted_turns_json,state,"
        "reconnect_evidence_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "outage", session.session_id, session.acp_session_id, session.target.to_json(),
            "running", "previous-generation", "{}", "[]", state,
            json.dumps(evidence) if evidence is not None else None, 1,
        ),
    )


def _assert_no_handoff(manager: SessionManager, session: Session, client: MagicMock) -> None:
    manager.start_session.assert_not_awaited()
    manager.resume_session.assert_not_awaited()
    manager.stop_session.assert_not_awaited()
    client.send_prompt.assert_not_awaited()
    assert manager.db.get_turns(session.session_id) == []
    assert manager.db.get_session(session.session_id)["successor_id"] is None
    assert not [
        event for event in session.event_log.get_events()
        if event.event in {"handoff_brief", "session_handoff"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["prepared", "interrupted", "reconnect_admitted", "reconnect_unknown", "reconnected"],
)
@pytest.mark.parametrize("entry", ["prompt", "automatic", "external-seed"])
async def test_cold_handoff_refuses_unconfirmed_outage(
    handoff_context: HandoffFixture, state: str, entry: str, caplog: pytest.LogCaptureFixture,
) -> None:
    manager, session, client = handoff_context
    _outage(manager, session, state)
    session._crossed_thresholds.add("critical")
    if entry == "automatic":
        session._handoff_pending = True
        manager._schedule_auto_handoff_if_pending(session)
        await asyncio.gather(*list(manager._auto_handoff_tasks))
        assert "cold outage fences new prompts" in caplog.text
    else:
        with pytest.raises(ValueError, match="cold outage fences new prompts"):
            if entry == "prompt":
                await manager.submit_or_queue_prompt(session.session_id, "next task")
            else:
                await manager.handoff_session(session.session_id, seed_text="external seed")
    _assert_no_handoff(manager, session, client)
    assert cold_outages.latest(manager.db, session.session_id)["state"] == state
    assert manager.db.count_pending_prompts(session.session_id) == 0


@pytest.mark.asyncio
async def test_cold_handoff_turn_settle_cannot_escape_unknown_reconnect(
    handoff_context: HandoffFixture, caplog: pytest.LogCaptureFixture,
) -> None:
    manager, session, client = handoff_context

    async def settle_with_unknown(_prompt: str) -> dict[str, str]:
        _outage(manager, session, "reconnect_unknown")
        session._handoff_pending = True
        return {"response_text": "", "stop_reason": "end_turn"}

    client.send_prompt.side_effect = settle_with_unknown
    await manager.submit_prompt(session.session_id, "original turn")
    await session._prompt_task
    await asyncio.gather(*list(manager._auto_handoff_tasks))

    manager.start_session.assert_not_awaited()
    manager.resume_session.assert_not_awaited()
    manager.stop_session.assert_not_awaited()
    assert client.send_prompt.await_count == 1
    assert len(manager.db.get_turns(session.session_id)) == 1
    assert cold_outages.latest(manager.db, session.session_id)["state"] == "reconnect_unknown"
    assert manager.db.get_session(session.session_id)["successor_id"] is None
    assert "cold outage fences new prompts" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_outage", [False, True])
async def test_cold_handoff_allows_ordinary_and_confirmed_outage(
    handoff_context: HandoffFixture, completed_outage: bool,
) -> None:
    manager, session, client = handoff_context
    if completed_outage:
        _outage(
            manager, session, "reconnected",
            evidence={
                "kind": "provider-prompt-response",
                "native_session_id": session.acp_session_id,
                "turn_index": 0,
                "stop_reason": "end_turn",
            },
        )
    successor = Session("successor", "successor", session.target)
    successor.status = SessionStatus.IDLE
    manager.db.create_session(
        successor.session_id, successor.name, None, session.target.cwd, "local", "idle", 1,
    )
    manager.start_session.return_value = successor

    result = await manager.handoff_session(session.session_id, seed=False)

    assert result is successor
    assert client.send_prompt.await_count == 1
    assert "Author a CONTINUATION BRIEF" in client.send_prompt.await_args.args[0]
    manager.start_session.assert_awaited_once()
    manager.stop_session.assert_awaited_once()
    assert manager.db.get_session(session.session_id)["successor_id"] == successor.session_id
    assert manager.db.get_session(successor.session_id)["predecessor_id"] == session.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_outage", [False, True])
@pytest.mark.parametrize("new_stop", [False, True])
async def test_stopped_handoff_accepts_only_its_authorized_token_consumption(
    handoff_context: HandoffFixture, completed_outage: bool, new_stop: bool,
) -> None:
    manager, session, client = handoff_context
    if completed_outage:
        _outage(
            manager, session, "reconnected",
            evidence={
                "kind": "provider-prompt-response", "native_session_id": session.acp_session_id,
                "turn_index": 0, "stop_reason": "end_turn",
            },
        )
    manager.db.request_cold_recovery_stop(session.session_id)
    original_token = manager.db.get_session(session.session_id)["cold_recovery_stop_token"]
    session.status = SessionStatus.STOPPED
    client.is_running = False

    async def authorized_resume(*_args: Any, **_kwargs: Any) -> Session:
        assert manager.db.clear_cold_recovery_stop(session.session_id, original_token)
        session.status = SessionStatus.IDLE
        client.is_running = True
        return session

    manager.resume_session.side_effect = authorized_resume
    successor = Session("successor", "successor", session.target)
    successor.status = SessionStatus.IDLE
    manager.db.create_session(
        successor.session_id, successor.name, None, session.target.cwd, "local", "idle", 1,
    )
    manager.start_session.return_value = successor
    if new_stop:
        async def brief_with_new_stop(_prompt: str) -> dict[str, str]:
            manager.db.request_cold_recovery_stop(session.session_id)
            return {"response_text": "retained brief", "stop_reason": "end_turn"}

        client.send_prompt.side_effect = brief_with_new_stop
        with pytest.raises(RuntimeError, match="stop was requested"):
            await manager.handoff_session(session.session_id, seed=False)
        current = manager.db.get_session(session.session_id)["cold_recovery_stop_token"]
        assert current is not None and current != original_token
        manager.start_session.assert_not_awaited()
    else:
        assert await manager.handoff_session(session.session_id, seed=False) is successor
        assert manager.db.get_session(session.session_id)["cold_recovery_stop_token"] is None
        manager.start_session.assert_awaited_once()
    manager.resume_session.assert_awaited_once_with(session.session_id, drain=False)
    assert client.send_prompt.await_count == 1


@pytest.mark.asyncio
async def test_cold_handoff_rechecks_outage_after_awaited_brief(
    handoff_context: HandoffFixture,
) -> None:
    manager, session, client = handoff_context

    async def brief_with_outage(_prompt: str) -> dict[str, str]:
        _outage(manager, session, "reconnect_unknown")
        return {"response_text": "continuation brief", "stop_reason": "end_turn"}

    client.send_prompt.side_effect = brief_with_outage
    with pytest.raises(ValueError, match="cold outage fences new prompts"):
        await manager.handoff_session(session.session_id)

    assert client.send_prompt.await_count == 1
    manager.start_session.assert_not_awaited()
    manager.stop_session.assert_not_awaited()
    assert manager.db.get_session(session.session_id)["successor_id"] is None
    assert cold_outages.latest(manager.db, session.session_id)["state"] == "reconnect_unknown"


@pytest.mark.asyncio
async def test_cold_handoff_rechecks_outage_after_awaited_resume(
    handoff_context: HandoffFixture,
) -> None:
    manager, session, client = handoff_context
    client.is_running = False
    session.status = SessionStatus.STOPPED

    async def resume_with_outage(*_args: Any, **_kwargs: Any) -> None:
        client.is_running = True
        session.status = SessionStatus.IDLE
        _outage(manager, session, "reconnect_unknown")

    manager.resume_session.side_effect = resume_with_outage
    with pytest.raises(ValueError, match="cold outage fences new prompts"):
        await manager.handoff_session(session.session_id)
    manager.resume_session.assert_awaited_once()
    manager.start_session.assert_not_awaited()
    manager.stop_session.assert_not_awaited()
    client.send_prompt.assert_not_awaited()
    assert manager.db.get_turns(session.session_id) == []
