"""ACP and native SDK receivers share the durable coordinator inbox."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
import pytest

from agent_bridge import acp_inbox
from agent_bridge.events import EventLog
from agent_bridge.live_delivery_state import LiveDeliveryState
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.models import SessionStatus
from agent_bridge.routes import live_delivery_state, live_sessions
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget


@pytest.fixture
def receiver(session_manager, tmp_db, mock_acp_client, tmp_path, monkeypatch):
    session = Session(
        "owned-1", "worker", SpawnTarget(
            cwd=str(tmp_path), worktree_id="worktree-1", project="example/repo",
        ), agent_name="worker",
    )
    session.status = SessionStatus.IDLE
    session.acp_session_id = "native-1"
    session.client = mock_acp_client
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    tmp_db.create_session(
        session.session_id, session.name, session.agent_name, str(tmp_path),
        "local", "idle", time.time(), target_json=session.target.to_json(),
    )
    session_manager._sessions[session.session_id] = session
    monkeypatch.setattr(session_manager, "_notify_host_reapable", AsyncMock())
    monkeypatch.setattr(session_manager, "_schedule_auto_handoff_if_pending", lambda _s: None)
    app = FastAPI()
    app.state.db = tmp_db
    app.state.session_manager = session_manager
    app.state.live_event_store = LiveEventStore()
    app.state.live_delivery_state = LiveDeliveryState()
    app.include_router(live_sessions.router)
    app.include_router(live_delivery_state.router)
    return session_manager, session, app


@pytest.mark.asyncio
async def test_registered_acp_receives_same_queue_and_receipt_without_governance(receiver, tmp_db):
    manager, session, app = receiver
    assert tmp_db.reserve_worktree_ownership("worktree-1", "owned-1", now=time.time())
    acp_inbox.register(manager, session)
    assert tmp_db.get_live_session("owned-1")["role"] == "acp"
    assert tmp_db.reserve_worktree_ownership("worktree-1", "owned-1", now=time.time())
    tmp_db.execute_write(
        "INSERT INTO bridge_runtime_epoch VALUES (1,'unrelated-installation',1,1,'closed','{}')"
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        resolved = await api.get("/api/v1/live-sessions/resolve", params={"handle": "worktree-1"})
        assert resolved.status_code == 200 and resolved.json()["session_id"] == "owned-1"
        response = await api.post("/api/v1/live-sessions/owned-1/messages", json={
            "sender": "peer", "body": "hello", "delivery": "queue", "idempotency_key": "one",
        })
        assert response.status_code == 200, response.text
        admitted = response.json()
        await asyncio.wait_for(session._prompt_task, 2)
        observed = await api.post(
            f'/api/v1/live-sessions/owned-1/messages/{admitted["message_id"]}/delivery',
            json={"timeout": 0},
        )
        assert observed.status_code == 200
        assert observed.json()["receipt"]["stage"] == "consumed"
        assert observed.json()["evidence"] == "completed_acp_prompt"
        duplicate = await api.post("/api/v1/live-sessions/owned-1/messages", json={
            "sender": "peer", "body": "hello", "delivery": "queue", "idempotency_key": "one",
        })
        assert duplicate.json()["message_id"] == admitted["message_id"]
    session.client.send_prompt.assert_awaited_once()
    prompt = session.client.send_prompt.await_args.args[0]
    assert '<agent-message from="peer" msg-id="' in prompt and "hello" in prompt
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        tmp_db.create_turn("owned-1", 99, "not an inbox delivery", time.time())


@pytest.mark.asyncio
async def test_busy_receiver_returns_receipt_then_drains_without_replacing_turn(receiver, tmp_db):
    manager, session, app = receiver
    session.status = SessionStatus.RUNNING
    acp_inbox.register(manager, session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        sent = await api.post("/api/v1/live-sessions/owned-1/messages", json={
            "sender": "peer", "body": "later", "delivery": "queue",
        })
    assert sent.status_code == 200, sent.text
    mid = sent.json()["message_id"]
    assert tmp_db.live_message_receipt("owned-1", mid)["stage"] == "admitted"
    session.client.send_prompt.assert_not_awaited()
    assert session.status == SessionStatus.RUNNING
    session.status = SessionStatus.IDLE
    await acp_inbox.deliver(manager, session)
    await asyncio.wait_for(session._prompt_task, 2)
    assert tmp_db.live_message_receipt("owned-1", mid)["stage"] == "consumed"
    assert tmp_db.execute_read("SELECT live_message_id FROM turns")[0][0] == mid


@pytest.mark.asyncio
async def test_delivery_wait_observes_actual_acp_input_without_process_observations(receiver):
    manager, session, app = receiver
    session.status = SessionStatus.RUNNING
    session.client._pending_permission_future = asyncio.get_running_loop().create_future()
    session.client._pending_elicitations = {}
    acp_inbox.register(manager, session)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
            sent = await api.post("/api/v1/live-sessions/owned-1/messages", json={
                "sender": "peer", "body": "later",
            })
            assert sent.status_code == 200
            mid = sent.json()["message_id"]
            observed = await api.post(
                f"/api/v1/live-sessions/owned-1/messages/{mid}/delivery", json={"timeout": 1},
            )
        assert observed.json()["outcome"] == "blocked"
        assert observed.json()["attention"]["reasons"] == ["permission"]
        assert observed.json()["receipt"]["stage"] == "admitted"
        session.client.send_prompt.assert_not_awaited()
    finally:
        session.client._pending_permission_future.cancel()


@pytest.mark.asyncio
async def test_uncertain_acp_send_is_not_replayed_after_receiver_refresh(receiver, tmp_db):
    manager, session, app = receiver
    acp_inbox.register(manager, session)
    session.client.send_prompt.side_effect = ConnectionResetError("response lost")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        sent = await api.post("/api/v1/live-sessions/owned-1/messages", json={
            "sender": "peer", "body": "once",
        })
    assert sent.status_code == 200, sent.text
    await asyncio.wait_for(session._prompt_task, 2)
    await acp_inbox.refresh(manager)
    session.client.send_prompt.assert_awaited_once()
    receipt = tmp_db.live_message_handoff_receipt("owned-1", sent.json()["message_id"])
    assert receipt["stage"] == "unknown" and receipt["consumed_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_dormant_acp_refuses_before_enqueue_without_resume(receiver, tmp_db, connected):
    manager, session, app = receiver
    acp_inbox.register(manager, session)
    session.status = SessionStatus.STOPPED
    session.client.is_running = connected
    manager.resume_session = AsyncMock(side_effect=AssertionError("must not resume"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        sent = await api.post("/api/v1/live-sessions/owned-1/messages", json={
            "sender": "peer", "body": "not admitted",
        })
    assert sent.status_code == 404
    assert not tmp_db.execute_read("SELECT id FROM live_messages")
    manager.resume_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_acp_reply_wait_observes_only_its_original_queued_turn(receiver, tmp_db):
    manager, session, app = receiver
    acp_inbox.register(manager, session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as api:
        sent = await api.post("/api/v1/live-sessions/native-1/messages", json={
            "sender": "peer", "body": "hello", "wait": True, "wait_timeout": 1,
        })
    assert sent.status_code == 200, sent.text
    result = sent.json()
    assert result["session_id"] == "owned-1"
    assert result["receipt"]["stage"] == "consumed"
    assert result["replied"] and result["reply"] == "Hello from the agent"
    assert len(tmp_db.execute_read("SELECT id FROM live_messages")) == 1


@pytest.mark.asyncio
async def test_real_acp_client_ordinary_prompt_does_not_require_launch_or_turn_custody():
    from agent_bridge.acp_client import AcpClient
    from agent_bridge.runtime_custody import RuntimeCustodyError

    client = AcpClient()
    connection = SimpleNamespace(prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")))
    client._connection = connection
    client._acp_session_id = "native-1"
    client._acquisition_launch = object()
    client._native_turn_transport = object()
    with pytest.raises(RuntimeCustodyError, match="original turn attempt"):
        await client.send_prompt("explicit lifecycle-owned invocation")
    result = await client.send_prompt("ordinary input", ordinary_message=True)
    assert result["stop_reason"] == "end_turn"
    connection.prompt.assert_awaited_once()
    assert connection.prompt.await_args.kwargs["session_id"] == "native-1"
    assert connection.prompt.await_args.kwargs["prompt"][0].text == "ordinary input"


@pytest.mark.asyncio
async def test_root_input_consumption_releases_wait_before_acp_turn_completion():
    from agent_bridge.acp_client import AcpClient

    client = AcpClient()
    client._acp_session_id = "native-1"
    consumed = asyncio.Event()
    release = asyncio.Event()
    entered = asyncio.Event()
    prompt = '<agent-message from="peer" msg-id="17">once</agent-message>'

    async def rpc(**_kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(stop_reason="end_turn")

    client._connection = SimpleNamespace(prompt=rpc)
    task = asyncio.create_task(client.send_prompt(
        prompt, ordinary_message=True, on_consumed=consumed.set,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        event = {"sessionId": "native-1", "type": "user.message", "data": {"content": prompt}}
        for invalid in (
            {**event, "sessionId": "other"}, {**event, "agentId": "child"},
            {**event, "data": {"content": prompt, "parentToolCallId": "parent"}},
            {**event, "data": {"content": "different input"}},
        ):
            client._subagent_attr.handle_raw_event(invalid)
            assert not consumed.is_set()
        client._subagent_attr.handle_raw_event(event)
        assert consumed.is_set() and not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, 1)
