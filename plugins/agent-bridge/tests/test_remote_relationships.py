"""Managed remote sessions retain a claim across ambiguous outcomes and retirement."""

from __future__ import annotations

import asyncio
import copy
import base64
import json
import hashlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from agent_bridge import relationship_authority as authority, remote_relationships as relationships
from agent_bridge.acp_client import AcpClient
from agent_bridge.models import SessionStatus, StartSessionRequest
from agent_bridge.routes.sessions import start_session, _session_info
from agent_bridge.session_manager import session_manager_from_config
from agent_bridge.models import ServiceConfig
from test_remote_acp_sessions import remote_manager as remote_manager, _remote_request

OWNER = "local/example/host"


@pytest.fixture
def managed(remote_manager, monkeypatch):
    manager, calls = remote_manager
    claims = {}
    operations = []

    async def command(operation, owner=None, payload=None):
        operations.append(operation)
        if operation == "capabilities":
            return {
                "schema": authority.SCHEMA, "version": 1, "metadata_version": 2,
                "lifetime": "retained-relationship", "ref_encoding": "namespace:l1.base64url",
                "operations": ["reserve", "inspect", "bind", "close"],
                "inspect_absence": True,
            }
        if operation == "reserve":
            request_id = payload["request_id"]
            if request_id not in claims:
                token = base64.urlsafe_b64encode(hashlib.sha256(request_id.encode()).digest()).decode().rstrip("=")
                assert manager._db.get_relationship(next(iter(manager._sessions))) is not None
                claims[request_id] = {
                    "schema": authority.SCHEMA, "version": 1, "ok": True,
                    "owner": owner, "creator": owner, "request_id": request_id,
                    "token": token, "phase": "pending", "ref": authority.NAMESPACE + ":l1.pending",
                    "producer": {
                        "version": 2, "lifetime": "retained-relationship", "namespace": authority.NAMESPACE,
                        "creator": owner, "request_id": request_id, "token": token,
                        "phase": "pending", "recovery": copy.deepcopy(payload["recovery"]),
                    },
                }
            result = claims[request_id]
        else:
            result = next(
                item for item in claims.values()
                if item["token"] == payload.get("token") or item["request_id"] == payload.get("request_id")
            )
        if operation == "bind":
            result["phase"] = result["producer"]["phase"] = "bound"
            result["producer"]["identity"] = copy.deepcopy(payload["identity"])
        if operation != "close":
            envelope = {
                key: value for key, value in result["producer"].items()
                if key not in {"namespace", "lifetime", "closeout"}
            }
            envelope["phase"] = "bound" if "identity" in envelope else "pending"
            encoded = base64.urlsafe_b64encode(json.dumps(
                envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("ascii")).decode("ascii").rstrip("=")
            result["ref"] = authority.NAMESPACE + ":l1." + encoded
        if operation == "close":
            proof = relationships.verification(manager, result["ref"])
            assert proof["relationship_verification"]["retired"] is True
            assert proof["relationship_verification"]["quiescent"] is True
            result["phase"] = result["producer"]["phase"] = "closed"
            result["producer"]["closeout"] = {"available": True, **proof}
        return copy.deepcopy(result)

    monkeypatch.setattr(authority, "command", command)
    return manager, calls, claims, operations


async def create(manager, **kwargs):
    return await start_session(
        StartSessionRequest(
            agent="remote-acp:example", caller_id="caller", caller_owner_ref=OWNER, **kwargs,
        ), _remote_request(manager),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "error"])
@pytest.mark.parametrize("stage", ["claim-inspection", "turn-persisted"])
async def test_queue_drain_recovers_only_before_turn_persistence(
    managed, monkeypatch, stage, failure,
):
    manager, _, claims, _ = managed
    result = await create(manager)
    session = manager.get_session(result.session_id)
    sid = session.session_id
    queue_id = manager.db.enqueue_prompt(sid, "follow-up", session.updated_at, caller_id="sender")
    paused, release = asyncio.Event(), asyncio.Event()
    prompt_started, finish_prompt = asyncio.Event(), asyncio.Event()
    original = authority.command

    async def pause():
        paused.set()
        await release.wait()
        if failure == "error":
            raise RuntimeError("admission-tail failure")

    async def command(operation, *args):
        if operation == "inspect" and stage == "claim-inspection":
            await pause()
        return await original(operation, *args)

    async def notify(current):
        if stage == "turn-persisted":
            await pause()

    async def run_prompt(current, turn_index, prompt):
        prompt_started.set()
        await finish_prompt.wait()

    monkeypatch.setattr(authority, "command", command)
    monkeypatch.setattr(manager, "_notify_host_reapable", notify)
    monkeypatch.setattr(manager, "_run_prompt", run_prompt)
    task = asyncio.create_task(manager._drain_pending_prompts(session))
    try:
        await asyncio.wait_for(paused.wait(), 2)
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            await task
        queued = manager.db.list_pending_prompts(sid)
        turns = manager.db.get_turns(sid)
        if stage == "claim-inspection":
            assert [row["prompt"] for row in queued] == ["follow-up"]
            assert queued[0]["id"] == queue_id
            assert queued[0]["caller_id"] == "sender"
            assert turns == [] and session.turn_count == 0
            monkeypatch.setattr(authority, "command", original)
            await manager._drain_pending_prompts(session)
        else:
            assert queued == []
        await asyncio.wait_for(prompt_started.wait(), 2)
        assert [row["prompt"] for row in manager.db.get_turns(sid)] == ["follow-up"]
        assert manager.db.count_pending_prompts(sid) == 0
        assert session.turn_count == 1 and len(claims) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        finish_prompt.set()
        if session._prompt_task is not None:
            await session._prompt_task
        monkeypatch.setattr(manager, "_notify_host_reapable", AsyncMock())
        await manager.stop_session(sid)


@pytest.mark.asyncio
async def test_ordinary_owner_forwarding_claim_precedes_network_and_inspection(managed, monkeypatch):
    manager, calls, claims, operations = managed
    original = AcpClient.new_session

    async def new(client, **kwargs):
        assert operations == ["capabilities", "reserve"]
        row = manager._db.get_relationship(next(iter(manager._sessions)))
        assert row["phase"] == "attempting" and row["claim"]["phase"] == "pending"
        return await original(client, **kwargs)

    monkeypatch.setattr(AcpClient, "new_session", new)
    result = await create(manager, caller_session_id="caller-session")
    session = manager.get_session(result.session_id)
    assert session.target.caller_owner_ref == OWNER
    assert session.caller_session_id == "caller-session"
    assert result.remote_relationship["ownership"] == "owned"
    assert _session_info(session, manager).remote_relationship == result.remote_relationship
    assert operations == ["capabilities", "reserve", "bind"]
    assert calls == ["connect", "new"] and len(claims) == 1
    await manager.stop_session(session.session_id)
    assert relationships.inspection(manager, session)["ownership"] == "owned"


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["", "bare", "local//host"])
async def test_invalid_supplied_owner_never_degrades_to_unowned(remote_manager, owner):
    manager, calls = remote_manager
    with pytest.raises(HTTPException):
        await start_session(StartSessionRequest(
            agent="remote-acp:example", caller_owner_ref=owner,
        ), _remote_request(manager))
    assert not calls and not manager._sessions


@pytest.mark.asyncio
async def test_missing_capability_refuses_before_network_and_keeps_journal(managed, monkeypatch):
    manager, calls, _, _ = managed
    monkeypatch.setattr(authority, "command", AsyncMock(return_value={}))
    with pytest.raises(HTTPException):
        await create(manager)
    session = next(iter(manager._sessions.values()))
    assert session.status == SessionStatus.STOPPED and not calls
    assert manager._db.get_relationship(session.session_id)["phase"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["reserve", "bind", "close"])
async def test_applied_response_loss_reconciles_original_claim(managed, monkeypatch, operation):
    manager, calls, claims, _ = managed
    original = authority.command
    lost = False

    async def command(op, *args):
        nonlocal lost
        result = await original(op, *args)
        if op == operation and not lost:
            lost = True
            raise authority.RelationshipUnknown("lost response")
        return result

    monkeypatch.setattr(authority, "command", command)
    result = await create(manager)
    session = manager.get_session(result.session_id)
    await manager.end_session(session.session_id)
    assert lost and len(claims) == 1 and calls.count("new") == 1
    row = manager._db.get_relationship(session.session_id)
    assert row["phase"] == "closed"
    assert manager.get_session(session.session_id) is None


@pytest.mark.asyncio
async def test_unknown_create_is_not_replayed_and_retirement_discloses_uncertainty(managed, monkeypatch):
    manager, calls, claims, _ = managed

    async def lost(client, **kwargs):
        calls.append("ambiguous-new")
        raise ConnectionError("reply lost")

    monkeypatch.setattr(AcpClient, "new_session", lost)
    with pytest.raises(HTTPException):
        await create(manager)
    session = next(iter(manager._sessions.values()))
    row = manager._db.get_relationship(session.session_id)
    assert row["phase"] == "attempting" and row["uncertain"]
    with pytest.raises(authority.RelationshipUnknown):
        await manager.resume_session(session.session_id)
    assert calls == ["connect", "ambiguous-new"] and len(claims) == 1
    await manager.end_session(session.session_id)
    row = manager._db.get_relationship(session.session_id)
    assert row["phase"] == "closed" and row["uncertain"]
    assert relationships.verification(manager, row["claim"]["ref"])["exists"] is True


@pytest.mark.asyncio
async def test_known_native_after_lost_bind_recovers_without_new(managed, monkeypatch):
    manager, calls, _, _ = managed
    original = authority.command
    refuse = True

    async def command(op, *args):
        if refuse and op == "bind":
            raise authority.RelationshipRefused("unavailable")
        return await original(op, *args)

    monkeypatch.setattr(authority, "command", command)
    with pytest.raises(HTTPException):
        await create(manager)
    session = next(iter(manager._sessions.values()))
    assert manager._db.get_relationship(session.session_id)["native_id"] == "server-owned-session"
    refuse = False
    await manager.resume_session(session.session_id)
    assert calls.count("new") == 1 and calls[-1] == ("load", "server-owned-session")
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_same_worktree_session_successor_reuses_but_other_owner_and_unowned_cannot(managed):
    manager, calls, _, _ = managed
    first = await create(manager, caller_session_id="old")
    again = await start_session(StartSessionRequest(
        agent="remote-acp:example", caller_id="caller", caller_owner_ref=OWNER + "#successor",
        caller_session_id="new",
    ), _remote_request(manager))
    assert first.session_id == again.session_id
    for owner in [None, "local/example/other"]:
        with pytest.raises(HTTPException):
            await start_session(StartSessionRequest(
                agent="remote-acp:example", caller_id="caller", caller_owner_ref=owner,
            ), _remote_request(manager))
    assert calls.count("new") == 1
    await manager.end_session(first.session_id)


@pytest.mark.asyncio
async def test_restart_preserves_owner_native_claim_and_local_retirement(managed):
    manager, calls, _, _ = managed
    result = await create(manager)
    session = manager.get_session(result.session_id)
    await manager.stop_session(session.session_id)
    restarted = session_manager_from_config(
        manager._db, ServiceConfig(remote_acp=manager._remote_acp_targets),
    )
    restored = restarted.get_session(session.session_id)
    assert restored.target.caller_owner_ref == OWNER and restored.acp_session_id == session.acp_session_id
    assert relationships.inspection(restarted, restored)["ownership"] == "owned"
    await restarted.resume_session(restored.session_id)
    assert calls.count("new") == 1
    await restarted.stop_session(restored.session_id)


@pytest.mark.asyncio
async def test_end_failure_is_retryable_and_fences_further_admission(managed, monkeypatch):
    manager, _, _, _ = managed
    result = await create(manager)
    session = manager.get_session(result.session_id)
    original = authority.command
    unavailable = True

    async def command(op, *args):
        if op == "close" and unavailable:
            raise authority.RelationshipRefused("provider unavailable")
        return await original(op, *args)

    monkeypatch.setattr(authority, "command", command)
    with pytest.raises(authority.RelationshipRefused):
        await manager.end_session(session.session_id)
    assert manager.get_session(session.session_id) is session
    assert manager._db.get_relationship(session.session_id)["phase"] == "retired"
    with pytest.raises(authority.RelationshipRefused):
        await manager.resume_session(session.session_id)
    unavailable = False
    await manager.end_session(session.session_id)
    assert manager.get_session(session.session_id) is None


@pytest.mark.asyncio
async def test_conditional_idle_end_uses_existing_turn_lock_without_deadlock(managed):
    manager, _, _, _ = managed
    result = await create(manager)
    await asyncio.wait_for(manager.end_session_if_idle(result.session_id), 2)
    assert manager._db.get_relationship(result.session_id)["phase"] == "closed"


@pytest.mark.asyncio
async def test_end_waits_for_create_then_retires_without_reopening(managed, monkeypatch):
    manager, _, _, _ = managed
    original = AcpClient.new_session
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(client, **kwargs):
        entered.set()
        await release.wait()
        return await original(client, **kwargs)

    monkeypatch.setattr(AcpClient, "new_session", delayed)
    pending = asyncio.create_task(create(manager))
    await asyncio.wait_for(entered.wait(), 2)
    session = next(iter(manager._sessions.values()))
    ending = asyncio.create_task(manager.end_session(session.session_id))
    release.set()
    await asyncio.wait_for(asyncio.gather(pending, ending), 3)
    assert manager._db.get_relationship(session.session_id)["phase"] == "closed"
    assert manager.get_session(session.session_id) is None


@pytest.mark.asyncio
async def test_managed_client_refuses_older_daemon_before_http():
    from agent_bridge.client import BridgeClient, BridgeClientError
    from agent_bridge.protocol import REMOTE_ACP_OWNERSHIP_PROTOCOL_VERSION

    client = BridgeClient("http://127.0.0.1:1", "test")
    client.daemon_supports = Mock(side_effect=lambda version: version < REMOTE_ACP_OWNERSHIP_PROTOCOL_VERSION)
    client._request = Mock()
    with pytest.raises(BridgeClientError) as exc:
        client.start_session(agent="remote-acp:example", caller_owner_ref=OWNER)
    assert exc.value.status == 426
    client._request.assert_not_called()


@pytest.mark.asyncio
async def test_programmatic_owner_affinity_without_caller_id_is_replayable(managed):
    manager, calls, claims, _ = managed
    body = StartSessionRequest(agent="remote-acp:example", caller_owner_ref=OWNER)
    first = await start_session(body, _remote_request(manager))
    again = await start_session(body, _remote_request(manager))
    assert first.session_id == again.session_id and len(claims) == 1
    assert calls.count("new") == 1
    await manager.end_session(first.session_id)


@pytest.mark.asyncio
async def test_explicit_known_native_does_not_fork_owned_relationship(managed):
    manager, calls, claims, _ = managed
    first = await create(manager)
    same = await start_session(StartSessionRequest(
        agent="https://example.com/acp", caller_owner_ref=OWNER, caller_id="different",
        remote_session_id="server-owned-session", force_new=True,
    ), _remote_request(manager))
    assert same.session_id == first.session_id and len(claims) == 1 and calls.count("new") == 1
    await manager.end_session(first.session_id)


@pytest.mark.asyncio
async def test_managed_caller_does_not_adopt_existing_unowned_native(managed):
    manager, calls, claims, _ = managed
    first = await start_session(StartSessionRequest(agent="remote-acp:example"), _remote_request(manager))
    with pytest.raises(HTTPException):
        await create(manager, remote_session_id="server-owned-session")
    assert len(claims) == 0 and calls.count("new") == 1
    await manager.stop_session(first.session_id)


@pytest.mark.asyncio
async def test_cancelled_creation_retains_uncertain_claim_and_never_replays_new(managed, monkeypatch):
    manager, _, claims, _ = managed
    entered = asyncio.Event()

    async def pending(client, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(AcpClient, "new_session", pending)
    creating = asyncio.create_task(create(manager))
    await asyncio.wait_for(entered.wait(), 2)
    creating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creating
    session = next(iter(manager._sessions.values()))
    assert session.status == SessionStatus.STOPPED
    assert len(claims) == 1 and manager._db.get_relationship(session.session_id)["uncertain"]
    with pytest.raises(authority.RelationshipUnknown):
        await manager.resume_session(session.session_id)
    await manager.end_session(session.session_id)


@pytest.mark.asyncio
async def test_permission_admission_refuses_retirement_committed_during_inspection(managed, monkeypatch):
    manager, _, _, _ = managed
    first = await create(manager)
    session = manager.get_session(first.session_id)
    original = authority.command

    async def command(op, *args):
        result = await original(op, *args)
        if op == "inspect":
            manager._db.retire_relationship(session.session_id)
        return result

    monkeypatch.setattr(authority, "command", command)
    answer = Mock()
    session.client.resolve_permission = answer
    with pytest.raises(authority.RelationshipRefused):
        await manager.answer_permission(session.session_id, "request", "allow")
    answer.assert_not_called()
    monkeypatch.setattr(authority, "command", original)
    await manager.end_session(session.session_id)


@pytest.mark.asyncio
async def test_closed_receipt_requires_exact_provider_owned_retirement(managed):
    manager, _, _, _ = managed
    first = await create(manager)
    await manager.end_session(first.session_id)
    row = manager._db.get_relationship(first.session_id)
    for changed in [None, {"available": True, "exists": False}]:
        result = copy.deepcopy(row["claim"])
        result["producer"]["closeout"] = changed
        with pytest.raises(authority.RelationshipUnknown):
            authority.confirm(
                result, owner=OWNER, request_id=row["id"], recovery=row["recovery"],
                identity=result["producer"]["identity"], closed=True,
            )


@pytest.mark.asyncio
async def test_unowned_inspection_does_not_require_new_tables_in_older_passive_database(remote_manager):
    manager, _ = remote_manager
    result = await start_session(StartSessionRequest(agent="remote-acp:example"), _remote_request(manager))
    session = manager.get_session(result.session_id)
    manager._db.execute_write("DROP TABLE remote_acp_relationships")
    manager._db.execute_write("DROP TABLE remote_acp_relationship_authority")
    assert _session_info(session, manager).remote_relationship == {"ownership": "unowned"}
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_relationship_insert_failure_rolls_back_session_insert(managed, monkeypatch):
    from agent_bridge import db_relationships

    manager, _, _, _ = managed
    first = await create(manager)
    session = manager.get_session(first.session_id)
    row = manager._db.get_relationship(first.session_id)
    monkeypatch.setattr(db_relationships.uuid, "uuid4", lambda: SimpleNamespace(hex=row["id"]))
    with pytest.raises(sqlite3.IntegrityError):
        manager._db.create_relationship(
            "rollback-subject", OWNER, relationships.binding(session), None, name="rollback",
            agent_name="remote-acp:example", caller_id=None, caller_session_id=None,
            target_json=session.target.to_json(),
        )
    assert manager._db.get_session("rollback-subject") is None
    assert manager._db.get_relationship("rollback-subject") is None
    await manager.stop_session(first.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["end", "stop-end", "stop-resume-end"])
async def test_closed_transport_with_live_local_task_cannot_verify_retirement(managed, operation):
    manager, _, _, _ = managed
    first = await create(manager)
    session = manager.get_session(first.session_id)
    await session.client.shutdown()
    pending = asyncio.get_running_loop().create_future()
    session.client = SimpleNamespace(
        is_running=False, has_active_background_tasks=False, shutdown=AsyncMock(),
        acp_session_id=session.acp_session_id,
        _remote_transport=SimpleNamespace(_tasks={pending}),
    )
    original = session.client
    if operation.startswith("stop"):
        original.shutdown.side_effect = RuntimeError("local shutdown failed")
        await manager.stop_session(session.session_id)
        assert session.client is original
        original.shutdown.side_effect = None
        if operation == "stop-resume-end":
            with pytest.raises(authority.RelationshipUnknown):
                await manager.resume_session(session.session_id)
            assert session.client is original
    with pytest.raises(authority.RelationshipUnknown):
        await manager.end_session(session.session_id)
    row = manager._db.get_relationship(session.session_id)
    assert row["phase"] == "retiring"
    assert relationships.verification(manager, row["claim"]["ref"])["relationship_verification"]["retired"] is False
    assert relationships.verification(manager, row["claim"]["ref"])["relationship_verification"]["quiescent"] is False
    pending.set_result(None)
    await manager.end_session(session.session_id)
    assert manager._db.get_relationship(session.session_id)["phase"] == "closed"


@pytest.mark.parametrize("declared_owner", [None, "", OWNER, "bare"])
def test_cli_declared_managed_owner_cannot_become_unowned_when_probes_fail(monkeypatch, declared_owner):
    from agent_bridge import session_start_cli
    from agent_bridge.client import BridgeClientError

    if declared_owner is None:
        monkeypatch.delenv("AGENT_WORKTREES_OWNER_REF", raising=False)
    else:
        monkeypatch.setenv("AGENT_WORKTREES_OWNER_REF", declared_owner)
    core = session_start_cli._core()
    monkeypatch.setattr(core, "_get_caller_id", lambda: None)
    monkeypatch.setattr(core, "_worktrees_get", lambda key: None)
    monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
    monkeypatch.setattr(core, "_startup_request_timeout", lambda: 1)
    monkeypatch.setattr(core, "_phased_timeouts", lambda: SimpleNamespace(codespace_boot=1, session_start=1))
    monkeypatch.setattr(core, "_wait_for_idle", Mock())
    client = SimpleNamespace(
        daemon_supports=Mock(return_value=True),
        start_session=Mock(return_value={"session_id": "unowned", "name": "unowned", "status": "idle"}),
    )
    preference = session_start_cli._SenderPreference("remote-acp:example", "example")
    if declared_owner is None:
        assert session_start_cli._start_agent_session(
            client, "remote-acp:example", sender_preference=preference,
        ) == "unowned"
        assert client.start_session.call_args.kwargs["caller_owner_ref"] is None
    else:
        with pytest.raises(BridgeClientError) as refused:
            session_start_cli._start_agent_session(
                client, "remote-acp:example", sender_preference=preference,
            )
        assert refused.value.status == 422
        client.start_session.assert_not_called()
