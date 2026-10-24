"""Controlled ACP owners and real SQLite, not native platform qualification."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from zdd.diagnostics import process_start_time

from agent_bridge.acp_client import AcpClient
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_custody import (
    RuntimeAdmission, RuntimeCustodyError, RuntimeInstallation, RuntimeProducer,
)
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget
from test_runtime_custody import close, installation

TransportOwner = tuple[SessionManager, Session, AcpClient, RuntimeInstallation, RuntimeProducer, RuntimeAdmission]


@pytest.fixture
def transport_owner(
    session_manager: SessionManager, spawn_target: SpawnTarget, monkeypatch: pytest.MonkeyPatch,
) -> TransportOwner:
    db = session_manager.db
    owner = installation(db)
    epoch = db.initialize_runtime_epoch(owner)
    producer = RuntimeProducer("controlled-frontend", os.getpid(), process_start_time(os.getpid()))
    admission = db.enroll_runtime_producer(owner, epoch.epoch, producer)
    db.create_session("s", "s", None, spawn_target.cwd, "local", "idle", 1.0)
    db.update_session_acp_id("s", "native-a")
    session = Session("s", "s", spawn_target)
    client = AcpClient()
    client._process = SimpleNamespace(pid=432101, returncode=None)
    client._connection = SimpleNamespace(prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")))
    client._acp_session_id = "native-a"
    session.client, session.acp_session_id, session.status = client, "native-a", SessionStatus.IDLE
    session_manager._sessions["s"] = session
    session_manager._notify_host_reapable = AsyncMock()
    monkeypatch.setattr("zdd.diagnostics.process_start_time",
                        lambda pid: "controlled-child-birth" if pid == 432101
                        else producer.process_start)
    return session_manager, session, client, owner, producer, admission


async def enroll(selected: TransportOwner) -> None:
    manager, session, _, owner, _, admission = selected
    await manager.enroll_native_turn_transport(session.session_id, owner, admission)


async def run_turn(selected: TransportOwner, mode: str) -> None:
    manager, session, _, _, _, _ = selected
    if mode == "direct":
        assert await manager.submit_prompt("s", "original prompt") == 0
        await session._prompt_task
    else:
        await manager._run_turn_sync(session, "original prompt")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "synchronous"])
async def test_selected_native_transport_retains_original_before_actual_call(
    transport_owner: TransportOwner, mode: str,
) -> None:
    manager, session, client, _, _, admission = transport_owner
    await enroll(transport_owner)

    async def observe(**kwargs: Any) -> SimpleNamespace:
        row = manager.db.get_turn("s", 0)
        assert row["native_handoff_started_at"] is not None
        assert row["runtime_custody_json"] == admission.document()
        handoff = json.loads(row["native_handoff_json"])
        assert handoff["consumer"]["native_session_id"] == kwargs["session_id"] == "native-a"
        assert handoff["consumer"]["pid"] == client._process.pid
        assert kwargs["prompt"][0].text == row["prompt"] == "original prompt"
        return SimpleNamespace(stop_reason="end_turn")

    client._connection.prompt.side_effect = observe
    await run_turn(transport_owner, mode)
    row = manager.db.get_turn("s", 0)
    assert row["native_accepted_at"] is not None
    assert row["native_consumed_at"] is None
    assert session.turn_count == 1
    client._connection.prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_selected_native_transport_refuses_client_replacement_behind_prompt_lock(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    original_connection = client._connection
    await client._prompt_lock.acquire()
    try:
        await manager.submit_prompt("s", "original prompt")
        await asyncio.sleep(0)
        replacement = AcpClient()
        replacement._connection = SimpleNamespace(prompt=AsyncMock())
        session.client = replacement
    finally:
        client._prompt_lock.release()
    await session._prompt_task
    original_connection.prompt.assert_not_awaited()
    replacement._connection.prompt.assert_not_awaited()
    row = manager.db.get_turn("s", 0)
    assert row["native_handoff_json"] is not None
    assert row["native_handoff_started_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "connection", "native", "session-native", "process", "birth", "dead",
    "session", "target", "cwd", "loading", "closed",
])
async def test_selected_native_transport_rechecks_after_lock_wait(
    transport_owner: TransportOwner, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    manager, session, client, owner, producer, _ = transport_owner
    await enroll(transport_owner)
    original = client._connection
    await client._prompt_lock.acquire()
    try:
        await manager.submit_prompt("s", "original prompt")
        await asyncio.sleep(0)
        if change == "connection":
            client._connection = SimpleNamespace(prompt=AsyncMock())
        elif change == "native":
            client._acp_session_id = "replacement"
        elif change == "session-native":
            session.acp_session_id = "replacement"
        elif change == "process":
            client._process = SimpleNamespace(pid=432101, returncode=None)
        elif change == "birth":
            monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "reused-pid")
        elif change == "dead":
            client._process.returncode = 1
        elif change == "session":
            manager._sessions["s"] = Session("s", "replacement", session.target)
        elif change == "target":
            from dataclasses import replace
            session.target = replace(session.target)
        elif change == "cwd":
            session.target.cwd += "/replacement"
        elif change == "loading":
            client._loading_session = True
        else:
            close(manager.db, owner, producer)
    finally:
        client._prompt_lock.release()
    await session._prompt_task
    original.prompt.assert_not_awaited()
    assert manager.db.get_turn("s", 0)["native_handoff_started_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["closed", "client"])
async def test_selected_native_transport_rechecks_after_committed_begin(
    transport_owner: TransportOwner, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    manager, session, client, owner, producer, _ = transport_owner
    await enroll(transport_owner)
    begin = manager.db.begin_native_turn_handoff

    def race(*args: Any, **kwargs: Any) -> bool:
        assert begin(*args, **kwargs)
        if change == "closed":
            close(manager.db, owner, producer)
        else:
            session.client = AcpClient()
        return True

    monkeypatch.setattr(manager.db, "begin_native_turn_handoff", race)
    await run_turn(transport_owner, "direct")
    client._connection.prompt.assert_not_awaited()
    row = manager.db.get_turn("s", 0)
    assert row["native_handoff_started_at"] is not None
    assert row["native_outcome"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "synchronous"])
async def test_selected_native_transport_records_lost_reply_without_resend(
    transport_owner: TransportOwner, mode: str,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    selected = session._native_turn_transport
    client._connection.prompt.side_effect = ConnectionResetError("controlled lost reply")
    if mode == "synchronous":
        with pytest.raises(ConnectionResetError):
            await run_turn(transport_owner, mode)
    else:
        await run_turn(transport_owner, mode)
    before = manager.db.get_turn("s", 0)
    assert before["native_outcome"] == "unknown"
    assert before["native_accepted_at"] is None and before["native_consumed_at"] is None
    with pytest.raises(RuntimeCustodyError, match="held"):
        await client.send_prompt("original prompt", native_attempt=selected.attempt(0, "original prompt"))
    assert manager.db.get_turn("s", 0) == before
    client._connection.prompt.assert_awaited_once()
    assert len(manager.db.get_turns("s")) == 1


@pytest.mark.asyncio
async def test_selected_native_transport_reports_original_reply_after_closure_and_replacement(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, owner, producer, _ = transport_owner
    await enroll(transport_owner)

    async def reply(**kwargs: Any) -> SimpleNamespace:
        close(manager.db, owner, producer)
        session.client = AcpClient()
        manager.db.update_session_acp_id("s", "replacement-native")
        return SimpleNamespace(stop_reason="end_turn")

    client._connection.prompt.side_effect = reply
    await run_turn(transport_owner, "direct")
    row = manager.db.get_turn("s", 0)
    assert row["native_accepted_at"] is not None and row["native_consumed_at"] is None
    assert json.loads(row["native_handoff_json"])["consumer"]["native_session_id"] == "native-a"
    assert manager.db.runtime_custody_inventory(owner)["restore_qualified"] is False


@pytest.mark.asyncio
async def test_selected_native_transport_cancellation_retains_unknown(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    called = asyncio.Event()

    async def wait_for_cancel(**kwargs: Any) -> None:
        called.set()
        await asyncio.Future()

    client._connection.prompt.side_effect = wait_for_cancel
    await manager.submit_prompt("s", "original prompt")
    await asyncio.wait_for(called.wait(), 2)
    session._prompt_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await session._prompt_task
    row = manager.db.get_turn("s", 0)
    assert row["native_handoff_started_at"] is not None and row["native_outcome"] == "unknown"
    assert row["native_accepted_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["direct", "synchronous"])
async def test_default_native_transport_remains_unenrolled(
    transport_owner: TransportOwner, mode: str,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await run_turn(transport_owner, mode)
    row = manager.db.get_turn("s", 0)
    assert row["native_handoff_json"] is None and row["runtime_custody_json"] is None
    assert session._native_turn_transport is None and client._native_turn_transport is None
    client._connection.prompt.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("unsupported", [
    "host", "remote", "private-idle", "busy", "birth", "local-command", "local-container",
])
async def test_explicit_native_transport_enrollment_refuses_unsupported_owners(
    transport_owner: TransportOwner, monkeypatch: pytest.MonkeyPatch, unsupported: str,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    if unsupported == "host":
        client._host_mode = True
    elif unsupported == "remote":
        client._remote_transport = object()
    elif unsupported == "private-idle":
        client._idle_transport = object()
    elif unsupported == "busy":
        client._prompt_in_flight = True
    elif unsupported == "local-command":
        session.target.spawn_command = ["synthetic-receiver"]
    elif unsupported == "local-container":
        session.target.container = {"name": "synthetic-container"}
    else:
        monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: None)
    with pytest.raises(RuntimeCustodyError):
        await enroll(transport_owner)
    assert session._native_turn_transport is None and client._native_turn_transport is None
    assert manager.db.get_turns("s") == []


@pytest.mark.asyncio
async def test_selected_native_transport_does_not_fall_back_to_lifecycle_handoff(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    manager.start_session = AsyncMock()
    manager.resume_session = AsyncMock()
    with pytest.raises(RuntimeCustodyError, match="lifecycle handoff"):
        await manager.handoff_session("s")
    manager.start_session.assert_not_awaited()
    manager.resume_session.assert_not_awaited()
    client._connection.prompt.assert_not_awaited()
    assert manager.db.get_turns("s") == []


@pytest.mark.asyncio
async def test_selected_native_transport_requires_original_attempt_on_direct_client_call(
    transport_owner: TransportOwner,
) -> None:
    _, _, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    with pytest.raises(RuntimeCustodyError, match="original turn attempt"):
        await client.send_prompt("unretained direct input")
    client._connection.prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_native_transport_keeps_queued_admission_atomic(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    queue_id = manager.db.enqueue_prompt("s", "original prompt", 1.0)
    await manager._drain_pending_prompts(session)
    await session._prompt_task
    row = manager.db.get_turn("s", 0)
    assert row["native_handoff_json"] is not None and row["native_accepted_at"] is not None
    assert manager.db.count_pending_prompts("s") == 0
    client._connection.prompt.assert_awaited_once()
    assert queue_id > 0


@pytest.mark.asyncio
async def test_selected_native_transport_rejects_implicit_resume_without_dropping_queue(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    client._process.returncode = 1
    manager.resume_session = AsyncMock()
    queue_id = manager.db.enqueue_prompt("s", "original prompt", 1.0)
    await manager._drain_pending_prompts(session)
    with pytest.raises(RuntimeCustodyError):
        await manager.submit_prompt("s", "new original")
    manager.resume_session.assert_not_awaited()
    assert manager.db.peek_pending_prompt("s")["id"] == queue_id
    assert manager.db.get_turns("s") == []


@pytest.mark.asyncio
async def test_selected_native_transport_preserves_original_keyed_receipt_after_closure(
    transport_owner: TransportOwner,
) -> None:
    from agent_bridge.native_keyed_admission import KeyedNativePrompt, submit

    manager, session, client, owner, producer, _ = transport_owner
    session.target.worktree_id, session.target.project = "worktree-a", "synthetic"
    manager.db.execute_write(
        "INSERT INTO workspace_embodiments "
        "(key_digest,facts_digest,state,session_id,worktree_json,native_session_id) "
        "VALUES ('key','facts','complete','s',?,'native-a')",
        (json.dumps({"creation_nonce": "nonce", "id": "worktree-a", "repo": "synthetic",
                     "path": session.target.cwd}),),
    )
    request = KeyedNativePrompt(
        invocation_key="original-key", expected_native_session_id="native-a", prompt="original prompt",
        binding={
            "worktree_id": "worktree-a", "project": "synthetic", "path": session.target.cwd,
            "creation_nonce": "nonce", "task_id": "task-a", "admission_generation": 0,
            "original_reservation_key": "reservation", "creation_key": "creation",
            "allocation_revision": 1, "expected_turn_index": 0, "origin_kind": "created",
        },
    )
    await enroll(transport_owner)
    receipt = await submit(manager, "s", request)
    await session._prompt_task
    before = manager.db.get_turn("s", 0)
    assert before["native_handoff_json"] is not None
    close(manager.db, owner, producer)
    assert await submit(manager, "s", request) == receipt
    assert manager.db.get_turn("s", 0) == before
    client._connection.prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_selected_native_transport_lost_begin_reply_is_not_a_new_send_grant(
    transport_owner: TransportOwner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    begin = manager.db.begin_native_turn_handoff

    def lose_reply(*args: Any, **kwargs: Any) -> bool:
        assert begin(*args, **kwargs)
        raise ConnectionResetError("controlled lost begin reply")

    monkeypatch.setattr(manager.db, "begin_native_turn_handoff", lose_reply)
    await run_turn(transport_owner, "direct")
    row = manager.db.get_turn("s", 0)
    assert row["native_handoff_started_at"] is not None and row["native_accepted_at"] is None
    monkeypatch.setattr(manager.db, "begin_native_turn_handoff", begin)
    with pytest.raises(RuntimeCustodyError, match="held"):
        await client.send_prompt(
            "original prompt", native_attempt=session._native_turn_transport.attempt(0, "original prompt"),
        )
    client._connection.prompt.assert_not_awaited()
    assert manager.db.get_turn("s", 0) == row


@pytest.mark.asyncio
async def test_explicit_native_transport_enrollment_does_not_upgrade_prior_turn(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    manager.db.create_turn("s", 0, "possibly already sent", 1.0)
    session.turn_count = 1
    await enroll(transport_owner)
    with pytest.raises(RuntimeCustodyError, match="Legacy"):
        await client.send_prompt(
            "possibly already sent",
            native_attempt=session._native_turn_transport.attempt(0, "possibly already sent"),
        )
    assert manager.db.get_turn("s", 0)["native_handoff_json"] is None
    client._connection.prompt.assert_not_awaited()
    await run_turn(transport_owner, "synchronous")
    assert manager.db.get_turn("s", 1)["native_handoff_json"] is not None
    client._connection.prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_native_transport_enrollment_cannot_replace_incarnation(
    transport_owner: TransportOwner,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    await enroll(transport_owner)
    original = session._native_turn_transport
    await enroll(transport_owner)
    assert session._native_turn_transport is original
    session.client = AcpClient()
    with pytest.raises(RuntimeCustodyError, match="owner changed"):
        await enroll(transport_owner)
    assert session._native_turn_transport is original and client._native_turn_transport is original
