"""Durable queue-to-turn admission survives daemon loss and rejected commits."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import textwrap
from unittest.mock import AsyncMock

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge.acp_client import AcpClient
from agent_bridge.db import Database
from agent_bridge.db_events import PendingPromptRemovedError
from agent_bridge.models import ServiceConfig
from agent_bridge.session_manager import session_manager_from_config
from test_remote_acp_sessions import remote_manager as remote_manager
from test_remote_relationships import create, managed as managed


@pytest.mark.asyncio
async def test_daemon_exit_during_claim_inspection_preserves_queued_followup(
    managed, monkeypatch,
):
    manager, calls, claims, _ = managed
    result = await create(manager)
    sid = result.session_id
    queue_id = manager.db.enqueue_prompt(sid, "follow-up", 1, caller_id="sender")
    manager.db.flush()
    script = textwrap.dedent("""
        import asyncio, os, sys
        from types import SimpleNamespace
        from agent_bridge import relationship_authority
        from agent_bridge.db import Database
        from agent_bridge.models import ServiceConfig, SessionStatus
        from agent_bridge.session_manager import session_manager_from_config

        async def crash(operation, *args):
            assert operation == "inspect"
            os._exit(73)

        async def main():
            db = Database(sys.argv[1])
            manager = session_manager_from_config(db, ServiceConfig())
            session = manager.get_session(sys.argv[2])
            session.client = SimpleNamespace(is_running=True)
            session.status = SessionStatus.IDLE
            relationship_authority.command = crash
            await manager._drain_pending_prompts(session)
            raise AssertionError("inspection did not crash")

        asyncio.run(main())
    """)
    child = subprocess.run(
        [sys.executable, "-c", script, str(manager.db.db_path), sid],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert child.returncode == 73, child.stderr
    reopened = Database(manager.db.db_path)
    restarted = session_manager_from_config(
        reopened, ServiceConfig(remote_acp=manager._remote_acp_targets),
    )
    restored = restarted.get_session(sid)
    try:
        queued = reopened.list_pending_prompts(sid)
        assert [row["id"] for row in queued] == [queue_id]
        assert queued[0]["prompt"] == "follow-up" and queued[0]["caller_id"] == "sender"
        assert reopened.get_turns(sid) == []
        monkeypatch.setattr(
            AcpClient, "send_prompt", AsyncMock(return_value={"response_text": "done"}),
        )
        await restarted.resume_session(sid)
        await asyncio.wait_for(restored._prompt_task, 2)
        assert [row["prompt"] for row in reopened.get_turns(sid)] == ["follow-up"]
        assert reopened.list_pending_prompts(sid) == []
        assert len(claims) == 1 and calls.count("new") == 1
    finally:
        await restarted.stop_session(sid)
        await manager.stop_session(sid)
        reopened.close()


@pytest.mark.parametrize("failure", ["turn-insert", "queue-removed"])
def test_queue_to_turn_transaction_refuses_without_consuming_work(tmp_db, failure):
    tmp_db.create_session("s1", "session", None, ".", "local", "idle", 1)
    queue_id = tmp_db.enqueue_prompt("s1", "follow-up", 1)
    if failure == "turn-insert":
        tmp_db.create_turn("s1", 0, "already-admitted", 1)
        error = sqlite3.IntegrityError
    else:
        tmp_db.remove_pending_prompt("s1", queue_id)
        error = ValueError
    with pytest.raises(error):
        tmp_db.create_turn("s1", 0, "follow-up", 1, pending_prompt_id=queue_id)
    if failure == "turn-insert":
        assert [row["id"] for row in tmp_db.list_pending_prompts("s1")] == [queue_id]
        assert [row["prompt"] for row in tmp_db.get_turns("s1")] == ["already-admitted"]
    else:
        assert tmp_db.list_pending_prompts("s1") == [] and tmp_db.get_turns("s1") == []


def test_passive_database_cannot_consume_a_queued_prompt(tmp_db):
    tmp_db.create_session("s1", "session", None, ".", "local", "idle", 1)
    queue_id = tmp_db.enqueue_prompt("s1", "follow-up", 1)
    passive = Database(tmp_db.db_path, read_only=True)
    try:
        with pytest.raises(PermissionError, match="Read-only"):
            passive.create_turn("s1", 0, "follow-up", 1, pending_prompt_id=queue_id)
        assert passive.get_turns("s1") == []
        assert [row["id"] for row in passive.list_pending_prompts("s1")] == [queue_id]
    finally:
        passive.close()


@pytest.mark.asyncio
async def test_removing_selected_prompt_during_inspection_drains_next(managed, monkeypatch):
    from agent_bridge import relationship_authority

    manager, _, _, _ = managed
    result = await create(manager)
    sid = result.session_id
    session = manager.get_session(sid)
    first = manager.db.enqueue_prompt(sid, "removed", 1)
    manager.db.enqueue_prompt(sid, "next", 2)
    inspecting, release = asyncio.Event(), asyncio.Event()
    original = relationship_authority.command

    async def paused(operation, *args):
        if operation == "inspect" and not inspecting.is_set():
            inspecting.set()
            await release.wait()
        return await original(operation, *args)

    monkeypatch.setattr(relationship_authority, "command", paused)
    send = AsyncMock(return_value={"response_text": "done"})
    monkeypatch.setattr(AcpClient, "send_prompt", send)
    drain = asyncio.create_task(manager._drain_pending_prompts(session))
    try:
        await asyncio.wait_for(inspecting.wait(), 2)
        assert manager.remove_pending_prompt(sid, first)
        release.set()
        await asyncio.wait_for(drain, 2)
        await asyncio.wait_for(session._prompt_task, 2)
        assert [row["prompt"] for row in manager.db.get_turns(sid)] == ["next"]
        assert manager.db.list_pending_prompts(sid) == []
        assert session.turn_count == 1
        send.assert_awaited_once()
    finally:
        release.set()
        await drain
        await manager.stop_session(sid)


def test_changed_queue_row_is_not_reported_as_removed(tmp_db):
    tmp_db.create_session("s1", "session", None, ".", "local", "idle", 1)
    queue_id = tmp_db.enqueue_prompt("s1", "original", 1)
    with pytest.raises(ValueError) as refused:
        tmp_db.create_turn("s1", 0, "different", 1, pending_prompt_id=queue_id)
    assert not isinstance(refused.value, PendingPromptRemovedError)
    assert tmp_db.get_turns("s1") == []
    assert tmp_db.list_pending_prompts("s1")[0]["prompt"] == "original"
