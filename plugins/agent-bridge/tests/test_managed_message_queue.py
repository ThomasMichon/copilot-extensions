"""Ordinary managed input is durable before a carrier can take its turn."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge.db import Database
from agent_bridge.managed_messages import drain_ready
from agent_bridge.routes.prompts import router
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_status import SessionStatus
from agent_bridge.transport import SpawnTarget
from test_session_manager import _patch_acp as _patch_acp, _patch_spawn as _patch_spawn
from test_retained_turn_durability import observing_commits
from test_remote_relationships import (
    create as create_remote, managed as managed, remote_manager as remote_manager,
)


def _client(manager: SessionManager) -> httpx.AsyncClient:
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router, prefix="/api/v1/sessions")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture")


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["stopped", "carrier"])
async def test_known_managed_target_queues_before_carrier_and_delivers_original_once(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
    monkeypatch: pytest.MonkeyPatch, unavailable: str,
) -> None:
    session = await session_manager.start_session(spawn_target)
    if unavailable == "stopped":
        session.status = SessionStatus.STOPPED
        session_manager.db.update_session_status(session.session_id, "stopped", time.time())
    mock_acp_client.is_running = False
    resume = AsyncMock(side_effect=AssertionError("Message acceptance cannot implicitly resume a target"))
    monkeypatch.setattr(session_manager, "resume_session", resume)
    app = FastAPI()
    app.state.session_manager = session_manager
    app.include_router(router, prefix="/api/v1/sessions")
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {
        "prompt": "original queued input", "caller_id": "original-sender",
        "idempotency_key": "original-operation",
        "expected_native_session_id": session.acp_session_id,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture",
    ) as client:
        with observing_commits(session_manager.db) as acceptance_commits:
            accepted = await client.post(path, json=payload)
        if accepted.status_code == 404:
            legacy = await client.post(path.removesuffix("/messages") + "/turns", json={
                "prompt": payload["prompt"], "queue": True, "no_resume": True,
            })
            assert legacy.status_code == 409, legacy.text
            assert session_manager.db.list_pending_prompts(session.session_id) == []
            pytest.fail(f"Known target has no durable message ingress; old queue path refused: {legacy.text}")
        assert accepted.status_code == 202, accepted.text
        assert acceptance_commits == [2]
        original = accepted.json()
        assert original["queued"] is True and original["queue_id"] is not None
        reopened = Database(session_manager.db.db_path)
        try:
            stored, = reopened.list_pending_prompts(session.session_id)
            assert stored["id"] == original["queue_id"] and stored["prompt"] == payload["prompt"]
        finally:
            reopened.close()
        recovered = await client.post(path, json=payload)
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["duplicate"] is True
        assert recovered.json()["queue_id"] == original["queue_id"]
        await asyncio.sleep(0)
        resume.assert_not_awaited()
        mock_acp_client.send_prompt.assert_not_awaited()

        session.status = SessionStatus.IDLE
        mock_acp_client.is_running = True
        with observing_commits(session_manager.db) as handoff_commits:
            await session_manager._drain_pending_prompts(session)
        assert handoff_commits[0] == 2
        assert session._prompt_task is not None
        await session._prompt_task
        mock_acp_client.send_prompt.assert_awaited_once_with(payload["prompt"])
        assert session_manager.db.list_pending_prompts(session.session_id) == []
        recovered = await client.post(path, json=payload)
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["duplicate"] is True
        assert recovered.json()["queue_id"] == original["queue_id"]
        await asyncio.sleep(0)
        mock_acp_client.send_prompt.assert_awaited_once_with(payload["prompt"])


@pytest.mark.asyncio
async def test_consumer_heartbeat_recovers_lost_response_wake_without_sender_replay(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    path = f"/api/v1/sessions/{session.session_id}/messages"
    async with _client(session_manager) as client:
        accepted = await client.post(path, json={"prompt": "wake original", "idempotency_key": "wake"})
        assert accepted.status_code == 202
    session.status = SessionStatus.STOPPED
    await session_manager.refresh_host_reapable()
    mock_acp_client.send_prompt.assert_not_awaited()
    session.status = SessionStatus.IDLE
    mock_acp_client.is_running = True
    await session_manager.refresh_host_reapable()
    assert session._prompt_task is not None
    await session._prompt_task
    await session_manager.refresh_host_reapable()
    mock_acp_client.send_prompt.assert_awaited_once_with("wake original")


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["busy", "draining"])
async def test_acceptance_ignores_turn_and_daemon_admission(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any, unavailable: str,
) -> None:
    session = await session_manager.start_session(spawn_target)
    if unavailable == "busy":
        session.status = SessionStatus.RUNNING
    else:
        session_manager._draining = True
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "wait for consumer", "idempotency_key": "one-operation"}
    async with _client(session_manager) as client:
        replies = await asyncio.gather(*(client.post(path, json=payload) for _ in range(3)))
        assert all(reply.status_code == 202 for reply in replies)
        assert sum(not reply.json()["duplicate"] for reply in replies) == 1
        assert len({reply.json()["queue_id"] for reply in replies}) == 1
        assert len(session_manager.db.list_pending_prompts(session.session_id)) == 1
        conflict = await client.post(path, json={**payload, "prompt": "changed"})
        assert conflict.status_code == 409
        missing = await client.post("/api/v1/sessions/not-an-agent/messages", json=payload)
        assert missing.status_code == 404
        mock_acp_client.send_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("removal", ["remove", "clear", "delete"])
async def test_original_receipt_survives_explicit_queue_and_target_removal(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any, removal: str,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "original", "idempotency_key": "removed-original"}
    async with _client(session_manager) as client:
        first = await client.post(path, json=payload)
        assert first.status_code == 202
        queue_id = first.json()["queue_id"]
        if removal == "remove":
            assert session_manager.db.remove_pending_prompt(session.session_id, queue_id)
        elif removal == "clear":
            assert session_manager.db.clear_pending_prompts(session.session_id) == 1
        else:
            session_manager.db.delete_session(session.session_id)
        recovered = await client.post(path, json=payload)
        assert recovered.status_code == 202
        assert recovered.json() == {
            **first.json(), "duplicate": True, "delivery_state": "removed",
        }
        assert session_manager.db.list_pending_prompts(session.session_id) == []
        mock_acp_client.send_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_generation_settles_without_retargeting_or_blocking_next_input(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    path = f"/api/v1/sessions/{session.session_id}/messages"
    old = {"prompt": "do not retarget", "idempotency_key": "old",
           "expected_native_session_id": "previous-conversation"}
    new = {"prompt": "current input", "idempotency_key": "new"}
    async with _client(session_manager) as client:
        first = await client.post(path, json=old)
        assert first.status_code == 202
        assert (await client.post(path, json=new)).status_code == 202
        mock_acp_client.is_running = True
        await session_manager._drain_pending_prompts(session)
        assert session._prompt_task is not None
        await session._prompt_task
        mock_acp_client.send_prompt.assert_awaited_once_with("current input")
        recovered = await client.post(path, json=old)
        assert recovered.json()["queue_id"] == first.json()["queue_id"]
        assert recovered.json()["delivery_state"] == "target_changed"
        assert session_manager.db.list_pending_prompts(session.session_id) == []


@pytest.mark.asyncio
async def test_ended_history_recovers_original_but_cannot_accept_new_operations(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "original", "idempotency_key": "before-end"}
    async with _client(session_manager) as client:
        original = await client.post(path, json=payload)
        assert original.status_code == 202
        session_manager.db.clear_pending_prompts(session.session_id)
        session_manager.db.update_session_status(session.session_id, "ended", time.time())
        session_manager._sessions.pop(session.session_id)
        recovered = await client.post(path, json=payload)
        assert recovered.status_code == 202
        assert recovered.json()["queue_id"] == original.json()["queue_id"]
        assert recovered.json()["delivery_state"] == "removed"
        fresh = await client.post(path, json={**payload, "idempotency_key": "after-end"})
        assert fresh.status_code == 404
        assert session_manager.db.list_pending_prompts(session.session_id) == []
        mock_acp_client.send_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_write_rolls_back_input_and_original_key_together(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    db = session_manager.db
    db.execute_write(
        "CREATE TRIGGER reject_managed_receipt BEFORE INSERT ON managed_message_receipts "
        "BEGIN SELECT RAISE(ABORT,'controlled write failure'); END",
    )
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "retry same key only", "idempotency_key": "failed-write"}
    async with _client(session_manager) as client:
        failed = await client.post(path, json=payload)
        assert failed.status_code == 503
        assert db.list_pending_prompts(session.session_id) == []
        assert db.execute_read("SELECT * FROM managed_message_receipts") == []
        db.execute_write("DROP TRIGGER reject_managed_receipt")
        accepted = await client.post(path, json=payload)
        assert accepted.status_code == 202 and accepted.json()["duplicate"] is False
        mock_acp_client.send_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_retirement_settles_input_accepted_during_shutdown(
    managed: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _, _, _ = managed
    result = await cast(Callable[..., Awaitable[Any]], create_remote)(manager)
    session = manager.get_session(result.session_id)
    assert session is not None and session.client is not None
    paused, release = asyncio.Event(), asyncio.Event()
    shutdown = session.client.shutdown

    async def suspended_shutdown() -> None:
        paused.set()
        await release.wait()
        await shutdown()

    monkeypatch.setattr(session.client, "shutdown", suspended_shutdown)
    monkeypatch.setattr(manager, "_kick_pending_drain", AsyncMock())
    ending = asyncio.create_task(manager.end_session(session.session_id))
    payload = {"prompt": "accepted during retirement", "idempotency_key": "retirement-race"}
    path = f"/api/v1/sessions/{session.session_id}/messages"
    try:
        await asyncio.wait_for(paused.wait(), 2)
        async with _client(manager) as client:
            original = await client.post(path, json=payload)
            assert original.status_code == 202
            release.set()
            await asyncio.wait_for(ending, 2)
            assert manager.db.get_session(session.session_id)["status"] == "ended"
            recovered = await client.post(path, json=payload)
            assert recovered.status_code == 202
            assert recovered.json()["queue_id"] == original.json()["queue_id"]
            assert recovered.json()["delivery_state"] == "removed"
            assert manager.db.list_pending_prompts(session.session_id) == []
    finally:
        release.set()
        await asyncio.wait_for(ending, 2)


@pytest.mark.asyncio
async def test_legacy_consumer_cannot_bypass_original_generation_fence(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    db = session_manager.db
    path = f"/api/v1/sessions/{session.session_id}/messages"
    async with _client(session_manager) as client:
        accepted = await client.post(path, json={"prompt": "held", "idempotency_key": "legacy"})
        assert accepted.status_code == 202
        with pytest.raises(sqlite3.IntegrityError, match="managed_message_consumer_required"):
            with db._runtime_transaction(write=True) as conn:
                conn.execute("DELETE FROM pending_prompts WHERE session_id=?", (session.session_id,))
        assert len(db.list_pending_prompts(session.session_id)) == 1
        legacy_id = db.enqueue_prompt(session.session_id, "legacy input", time.time())
        assert db.remove_pending_prompt(session.session_id, legacy_id)


@pytest.mark.asyncio
async def test_queue_consumption_rolls_back_with_failed_turn_write(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    db = session_manager.db
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "original retry", "idempotency_key": "turn-write"}
    async with _client(session_manager) as client:
        accepted = await client.post(path, json=payload)
        assert accepted.status_code == 202
        original = accepted.json()
        db.execute_write(
            "CREATE TRIGGER reject_managed_turn BEFORE INSERT ON turns "
            "BEGIN SELECT RAISE(ABORT,'controlled turn write failure'); END",
        )
        mock_acp_client.is_running = True
        await session_manager._drain_pending_prompts(session)
        assert len(db.list_pending_prompts(session.session_id)) == 1
        recovered = await client.post(path, json=payload)
        assert recovered.json()["delivery_state"] == "queued"
        mock_acp_client.send_prompt.assert_not_awaited()
        db.execute_write("DROP TRIGGER reject_managed_turn")
        await session_manager._drain_pending_prompts(session)
        assert session._prompt_task is not None
        await session._prompt_task
        mock_acp_client.send_prompt.assert_awaited_once_with("original retry")
        assert session_manager.db.list_pending_prompts(session.session_id) == []
        recovered = await client.post(path, json=payload)
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["duplicate"] is True
        assert recovered.json()["queue_id"] == original["queue_id"]
        await asyncio.sleep(0)
        mock_acp_client.send_prompt.assert_awaited_once_with(payload["prompt"])


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["holder", "stored", "client"])
async def test_consumer_requires_original_native_in_memory_and_storage(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any, changed: str,
) -> None:
    session = await session_manager.start_session(spawn_target)
    assert session.acp_session_id
    mock_acp_client.is_running = False
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "original native only", "idempotency_key": "churn"}
    async with _client(session_manager) as client:
        accepted = await client.post(path, json=payload)
        assert accepted.status_code == 202
        if changed == "holder":
            session.acp_session_id = "other-native"
        elif changed == "client":
            mock_acp_client.acp_session_id = "other-native"
        else:
            session_manager.db.execute_write(
                "UPDATE sessions SET acp_session_id=? WHERE id=?", ("other-native", session.session_id),
            )
        mock_acp_client.is_running = True
        await session_manager._drain_pending_prompts(session)
        recovered = await client.post(path, json=payload)
        assert recovered.json()["delivery_state"] == "target_changed"
        assert recovered.json()["queue_id"] == accepted.json()["queue_id"]
        mock_acp_client.send_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_handoff_transfers_only_unbound_legacy_inputs(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "original native only", "idempotency_key": "handoff-bound"}
    successor = MagicMock(session_id="successor")

    async def handoff(*args: Any, **kwargs: Any) -> MagicMock:
        session_manager.db.clear_pending_prompts(session.session_id)
        return successor

    transfer = AsyncMock()
    monkeypatch.setattr(session_manager, "handoff_session", handoff)
    monkeypatch.setattr(session_manager, "submit_or_queue_prompt", transfer)
    async with _client(session_manager) as client:
        original = await client.post(path, json=payload)
        assert original.status_code == 202
        session_manager.db.enqueue_prompt(
            session.session_id, "legacy follow-up", time.time(), caller_id="legacy-sender",
        )
        await session_manager._run_auto_handoff(session.session_id)
        transfer.assert_awaited_once_with(
            successor.session_id, "legacy follow-up", caller_id="legacy-sender",
        )
        recovered = await client.post(path, json=payload)
        assert recovered.json()["queue_id"] == original.json()["queue_id"]
        assert recovered.json()["duplicate"] is True
        assert recovered.json()["delivery_state"] == "removed"
        mock_acp_client.send_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_committed_handoff_is_not_replayed_when_later_step_fails(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await session_manager.start_session(spawn_target)
    mock_acp_client.is_running = False
    path = f"/api/v1/sessions/{session.session_id}/messages"
    payload = {"prompt": "one handoff only", "idempotency_key": "unknown-effect"}
    async with _client(session_manager) as client:
        accepted = await client.post(path, json=payload)
        assert accepted.status_code == 202

        def fail_status(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("controlled failure after committed handoff")

        monkeypatch.setattr(session_manager.db, "update_session_status", fail_status)
        mock_acp_client.is_running = True
        await session_manager._drain_pending_prompts(session)
        recovered = await client.post(path, json=payload)
        assert recovered.json()["delivery_state"] == "handoff_committed"
        assert recovered.json()["queue_id"] == accepted.json()["queue_id"]
        assert recovered.json()["turn_index"] == 0
        assert len(session_manager.db.get_turns(session.session_id)) == 1
        assert session_manager.db.list_pending_prompts(session.session_id) == []
        await drain_ready(session_manager)
        mock_acp_client.send_prompt.assert_not_awaited()


@pytest.mark.parametrize("read_only", [False, True])
def test_damaged_prepared_managed_guard_is_not_silently_repaired(
    tmp_path: Path, read_only: bool,
) -> None:
    path = tmp_path / "prepared.db"
    db = Database(path)
    db.execute_write("DROP TRIGGER pending_managed_message_delete")
    db.close()
    with pytest.raises(ValueError, match="Managed messages require writable preparation"):
        Database(path, read_only=read_only)


@pytest.mark.asyncio
@pytest.mark.parametrize("credential,expected", [(None, 401), ("incorrect", 403), ("fixture-only", 202)])
async def test_real_serving_frontdoor_authenticates_before_durable_acceptance(
    session_manager: SessionManager, spawn_target: SpawnTarget,
    mock_acp_client: MagicMock, _patch_spawn: Any, _patch_acp: Any,
    credential: str | None, expected: int,
) -> None:
    from agent_bridge.app_routes import configure_serving_app
    from agent_bridge.models import ServiceConfig

    session = await session_manager.start_session(spawn_target)
    session.status = SessionStatus.STOPPED
    mock_acp_client.is_running = False
    app = configure_serving_app(ServiceConfig(), "fixture-only", lifespan=None)
    app.state.session_manager = session_manager
    app.state.db = session_manager.db
    app.state.ready = False
    headers = {"Authorization": f"Bearer {credential}"} if credential is not None else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture",
    ) as client:
        reply = await client.post(
            f"/api/v1/sessions/{session.session_id}/messages", headers=headers,
            json={"prompt": "authenticated original", "idempotency_key": "auth-control"},
        )
    assert reply.status_code == expected, reply.text
    assert session_manager.db.count_pending_prompts(session.session_id) == (1 if expected == 202 else 0)
    mock_acp_client.send_prompt.assert_not_awaited()
