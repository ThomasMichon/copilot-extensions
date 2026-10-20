"""Durable native identities and live admission survive interrupted attachments."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from agent_bridge import relationship_authority as authority
from agent_bridge.acp_client import AcpClient
from agent_bridge.models import ServiceConfig, SessionStatus, StartSessionRequest
from agent_bridge.routes.sessions import start_session
from agent_bridge.session_manager import session_manager_from_config
from agent_bridge.session_manager import DaemonDrainingError
from test_remote_acp_sessions import _remote_request, remote_manager as remote_manager
from test_remote_relationships import OWNER, create, managed as managed


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("owner", [OWNER, "local/example/other", None])
async def test_failed_load_retains_exact_native_matching_and_owner(restart, owner, managed, monkeypatch):
    manager, calls, claims, _ = managed
    load = AcpClient.load_session

    async def unavailable(client, **kwargs):
        raise ConnectionError("load reply unavailable")

    monkeypatch.setattr(AcpClient, "load_session", unavailable)
    with pytest.raises(HTTPException):
        await create(manager, remote_session_id="saved-native")
    session = next(iter(manager._sessions.values()))
    original_id = session.session_id
    assert session.acp_session_id is None and session._remote_requested_session_id is None
    assert manager._db.get_relationship(original_id)["requested_native_id"] == "saved-native"
    if restart:
        restarted = session_manager_from_config(
            manager._db, ServiceConfig(remote_acp=manager._remote_acp_targets),
        )
        manager._sessions.clear()
        manager = restarted
    monkeypatch.setattr(AcpClient, "load_session", load)
    before = list(calls)
    request = StartSessionRequest(
        agent="https://example.com/acp", caller_owner_ref=owner,
        caller_id="different-caller", remote_session_id="saved-native", force_new=True,
    )
    if owner != OWNER:
        with pytest.raises(HTTPException) as refused:
            await start_session(request, _remote_request(manager))
        assert refused.value.status_code == 409 and calls == before
    else:
        retried = await start_session(request, _remote_request(manager))
        assert retried.session_id == original_id
        assert calls[-1] == ("load", "saved-native")
        assert manager._db.get_relationship(original_id)["native_id"] == "saved-native"
    assert len(claims) == 1 and len(manager._sessions) == 1 and "new" not in calls
    await manager.end_session(original_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "end"])
async def test_restart_reconciles_native_id_committed_before_relationship_bind(managed, monkeypatch, operation):
    manager, calls, claims, _ = managed

    async def lost(client, **kwargs):
        raise ConnectionError("remote acknowledgement unavailable")

    monkeypatch.setattr(AcpClient, "new_session", lost)
    with pytest.raises(HTTPException):
        await create(manager)
    session = next(iter(manager._sessions.values()))
    row = manager._db.get_relationship(session.session_id)
    assert row["phase"] == "attempting" and row["native_id"] is None
    # Reproduce the durable boundary after sessions.acp_session_id commits.
    manager._db.update_session_acp_id(session.session_id, "accepted-native")
    restarted = session_manager_from_config(
        manager._db, ServiceConfig(remote_acp=manager._remote_acp_targets),
    )
    manager._sessions.clear()
    restored = restarted.get_session(session.session_id)
    assert restored.acp_session_id == "accepted-native"
    assert manager._db.get_relationship(session.session_id)["native_id"] is None
    if operation == "resume":
        await restarted.resume_session(restored.session_id)
        current = manager._db.get_relationship(restored.session_id)
        assert current["phase"] == "bound" and current["native_id"] == "accepted-native"
        assert not current["uncertain"] and len(claims) == 1
        assert calls[-1] == ("load", "accepted-native") and "new" not in calls
    await restarted.end_session(restored.session_id)
    audit = manager._db.get_relationship(restored.session_id)
    assert audit["phase"] == "closed" and audit["native_id"] == "accepted-native"
    manager._db.delete_session(restored.session_id)
    assert manager._db.get_relationship(restored.session_id)["native_id"] == "accepted-native"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["permission", "form", "prompt"])
async def test_stop_during_authority_inspection_refuses_admission(managed, monkeypatch, operation):
    manager, _, _, _ = managed
    started = await create(manager)
    session = manager.get_session(started.session_id)
    client = session.client
    permission, form = Mock(), Mock()
    client.resolve_permission = permission
    client.resolve_elicitation = form
    command = authority.command
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(op, *args):
        result = await command(op, *args)
        if op == "inspect":
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(authority, "command", delayed)
    if operation == "permission":
        admission = manager.answer_permission(session.session_id, "request", "allow")
    elif operation == "form":
        admission = manager.answer_ask_user(session.session_id, "tool", {"answer": "explicit"})
    else:
        admission = manager.submit_prompt(session.session_id, "must not start")
    task = asyncio.create_task(admission)
    await asyncio.wait_for(entered.wait(), 2)
    await manager.stop_session(session.session_id)
    release.set()
    with pytest.raises(authority.RelationshipRefused):
        await task
    assert session.status == SessionStatus.STOPPED and session.client is None
    assert session.turn_count == 0 and manager._db.execute_read(
        "SELECT * FROM turns WHERE session_id=?", (session.session_id,),
    ) == []
    permission.assert_not_called()
    form.assert_not_called()
    await manager.end_session(session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["absent", "reserved", "unknown", "wrong-owner", "uncertain"])
async def test_unconfirmed_reservation_retirement_requires_authoritative_reconciliation(
    managed, monkeypatch, outcome,
):
    manager, calls, claims, _ = managed

    async def unavailable():
        raise authority.RelationshipRefused("reservation authority refused")

    monkeypatch.setattr(authority, "require_capability", unavailable)
    with pytest.raises(HTTPException):
        await create(manager)
    session = next(iter(manager._sessions.values()))
    row = manager._db.get_relationship(session.session_id)
    command = authority.command
    if outcome == "uncertain":
        manager._db.execute_write(
            "UPDATE remote_acp_relationships SET uncertain=1 WHERE session_id=?", (session.session_id,),
        )
    if outcome == "reserved":
        reserved = await command("reserve", row["owner"], {
            "request_id": row["id"], "namespace": authority.NAMESPACE,
            "kind": "bridge", "recovery": row["recovery"],
        })
    inspected = []

    async def reconcile(op, owner=None, payload=None):
        if op == "reserve":
            pytest.fail("retirement must not create a reservation")
        if op == "inspect" and "allow_missing" in payload:
            inspected.append(payload)
            if outcome == "unknown":
                raise authority.RelationshipUnknown("inspection unavailable")
            if outcome == "reserved":
                return reserved
            return {
                "schema": authority.SCHEMA, "version": 1, "ok": True,
                "owner": "local/example/other" if outcome == "wrong-owner" else row["owner"],
                "request_id": row["id"], "absent": True,
            }
        return await command(op, owner, payload)

    monkeypatch.setattr(authority, "command", reconcile)
    if outcome in {"unknown", "wrong-owner", "uncertain"}:
        with pytest.raises(authority.RelationshipRefused):
            await manager.end_session(session.session_id)
        assert manager.get_session(session.session_id) is session
        assert manager._db.get_relationship(session.session_id)["phase"] == "retired"
    else:
        await manager.end_session(session.session_id)
        audit = manager._db.get_relationship(session.session_id)
        assert audit["phase"] == "closed" and manager.get_session(session.session_id) is None
        if outcome == "absent":
            assert audit["claim"] is None and audit["absence"]["request_id"] == row["id"]
            manager._db.delete_session(session.session_id)
            assert manager._db.get_relationship(session.session_id)["absence"]["absent"] is True
    assert inspected == [{"request_id": row["id"], "allow_missing": True}]
    assert not calls and len(claims) == (1 if outcome == "reserved" else 0)


@pytest.mark.asyncio
async def test_clean_drain_during_authority_inspection_cannot_admit_a_turn(managed, monkeypatch):
    expected = json.loads((
        Path(__file__).resolve().parents[1]
        / "contract/fixtures/http/current/managed-drain-generation-43.json"
    ).read_text(encoding="utf-8"))
    manager, _, _, _ = managed
    started = await create(manager)
    session = manager.get_session(started.session_id)
    command = authority.command
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(op, *args):
        result = await command(op, *args)
        if op == "inspect":
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(authority, "command", delayed)
    admission = asyncio.create_task(manager.submit_prompt(session.session_id, "must not start after drain"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        drained = await manager.drain(timeout=1, poll=0.01)
        assert drained["clean"] == expected["drain_result"]["clean"] and manager.is_draining
        assert session.status.value == expected["during_claim_inspection"]["session_status"]
        assert manager._active_acquisitions == expected["during_claim_inspection"]["active_acquisitions"]
        release.set()
        with pytest.raises(DaemonDrainingError) as refused:
            await admission
        assert type(refused.value).__name__ == expected["released_inspection"]["refusal"]
        assert session.turn_count == expected["released_inspection"]["turn_count"]
        assert manager._db.execute_read(
            "SELECT * FROM turns WHERE session_id=?", (session.session_id,),
        ) == expected["released_inspection"]["persisted_turns"]
    finally:
        release.set()
        if not admission.done():
            admission.cancel()
        await asyncio.gather(admission, return_exceptions=True)
        watchdog = manager._drain_watchdog
        manager.set_draining(False)
        if watchdog is not None:
            await asyncio.gather(watchdog, return_exceptions=True)
        await manager.end_session(session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["reserve", "bind", "close"])
async def test_lost_transaction_remains_unknown_when_reconciliation_refuses(managed, monkeypatch, operation):
    manager, calls, claims, _ = managed
    command = authority.command
    lose = True

    async def lost(op, *args):
        nonlocal lose
        if op == "inspect" and not lose:
            raise authority.RelationshipRefused("inspection unavailable")
        result = await command(op, *args)
        if op == operation and lose:
            lose = False
            raise authority.RelationshipUnknown("response lost")
        return result

    if operation == "close":
        started = await create(manager)
        session = manager.get_session(started.session_id)
        monkeypatch.setattr(authority, "command", lost)
        with pytest.raises(authority.RelationshipUnknown):
            await manager.end_session(session.session_id)
        assert manager._db.get_relationship(session.session_id)["phase"] == "retired"
    else:
        monkeypatch.setattr(authority, "command", lost)
        with pytest.raises(HTTPException) as refused:
            await create(manager)
        assert refused.value.detail["code"] == "relationship_outcome_unknown"
        session = next(iter(manager._sessions.values()))
    assert len(claims) == 1 and calls.count("new") == (0 if operation == "reserve" else 1)
    assert manager.get_session(session.session_id) is session
    monkeypatch.setattr(authority, "command", command)
    await manager.end_session(session.session_id)
    assert manager._db.get_relationship(session.session_id)["phase"] == "closed"


@pytest.mark.asyncio
async def test_relationship_subject_lookup_is_indexed_and_refuses_ambiguity(managed, monkeypatch):
    manager, _, _, _ = managed
    started = await create(manager)
    await manager.end_session(started.session_id)
    row = manager._db.get_relationship(started.session_id)
    subject = row["claim"]["ref"]
    execute_read = manager._db.execute_read
    observed = []

    def read(sql, params=()):
        observed.append((sql, params))
        return execute_read(sql, params)

    monkeypatch.setattr(manager._db, "execute_read", read)
    assert manager._db.relationship_subject(subject)["session_id"] == started.session_id
    sql, params = next(query for query in observed if "json_extract(claim_json" in query[0])
    plan = execute_read("EXPLAIN QUERY PLAN " + sql, params)
    assert any("USING INDEX idx_remote_acp_relationship_ref" in item["detail"] for item in plan)
    manager._db.execute_write(
        "INSERT INTO remote_acp_relationships "
        "(id,session_id,owner,recovery_json,requested_native_id,native_id,claim_json,phase,uncertain) "
        "SELECT 'duplicate-audit','duplicate-session',owner,recovery_json,requested_native_id,"
        "native_id,claim_json,phase,uncertain FROM remote_acp_relationships WHERE session_id=?",
        (started.session_id,),
    )
    assert manager._db.relationship_subject(subject) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("repeated", [False, True])
async def test_cancelled_start_persists_accepted_id_before_awaiting_cleanup(managed, monkeypatch, repeated):
    manager, calls, claims, _ = managed
    accepted, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    shutdown = AcpClient.shutdown

    async def known(client, **kwargs):
        calls.append("accepted-new")
        client._acp_session_id = "accepted-before-cleanup"
        accepted.set()
        await asyncio.Event().wait()

    async def delayed_shutdown(client):
        cleaning.set()
        await release.wait()
        await shutdown(client)

    monkeypatch.setattr(AcpClient, "new_session", known)
    monkeypatch.setattr(AcpClient, "shutdown", delayed_shutdown)
    creating = asyncio.create_task(create(manager))
    try:
        await asyncio.wait_for(accepted.wait(), 2)
        creating.cancel()
        await asyncio.wait_for(cleaning.wait(), 2)
        session = next(iter(manager._sessions.values()))
        assert manager._db.get_session(session.session_id)["acp_session_id"] == "accepted-before-cleanup"
        assert manager._db.get_relationship(session.session_id)["native_id"] == "accepted-before-cleanup"
        if repeated:
            creating.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await creating
        assert session.status == SessionStatus.STOPPED
    finally:
        release.set()
        if not creating.done():
            creating.cancel()
        await asyncio.gather(creating, return_exceptions=True)
        monkeypatch.setattr(AcpClient, "shutdown", shutdown)
        for previous in manager._sessions.values():
            if previous.client is not None:
                await shutdown(previous.client)
    restarted = session_manager_from_config(
        manager._db, ServiceConfig(remote_acp=manager._remote_acp_targets),
    )
    manager._sessions = restarted._sessions
    restored = restarted.get_session(session.session_id)
    await restarted.resume_session(restored.session_id)
    assert calls == ["connect", "accepted-new", "connect", ("load", "accepted-before-cleanup")]
    assert len(claims) == 1
    await restarted.end_session(restored.session_id)
