"""Aged managed relationships retain their original settlement handles."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent_bridge import relationship_authority as authority
from agent_bridge.acp_client import AcpClient
from agent_bridge.db import Database
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.session_manager import SessionManager, session_manager_from_config
from test_remote_relationships import (
    create,
    managed as managed,
    remote_manager as remote_manager,
)


def _age(db, session_id, now):
    aged = now - 10 * 86400
    db.execute_write(
        "UPDATE sessions SET updated_at=? WHERE id=?", (aged, session_id),
    )
    assert db.get_session(session_id)["updated_at"] < now - 7 * 86400


def _seed(db, session_id, status, now, phase=None):
    db.create_session(session_id, session_id, None, ".", "local", status, now)
    _age(db, session_id, now)
    if phase is not None:
        db.execute_write(
            "INSERT INTO remote_acp_relationships "
            "(id, session_id, owner, recovery_json, phase) VALUES (?, ?, ?, ?, ?)",
            (session_id, session_id, "local/example/host", "{}", phase),
        )


@pytest.mark.parametrize(
    "phase", ["pending", "reserved", "attempting", "binding", "bound", "retiring", "retired"],
)
@pytest.mark.parametrize("status", ["stopped", "ended"])
def test_nonclosed_handles_survive_startup_and_manual_gc(tmp_db, phase, status):
    now = time.time()
    _seed(tmp_db, "retained", status, now, phase)
    _seed(tmp_db, "closed", "stopped", now, "closed")
    _seed(tmp_db, "unowned", "stopped", now)
    _seed(tmp_db, "ended-unowned", "ended", now)

    assert set(tmp_db.gc_eligible_session_ids(
        ["stopped", "ended"], now - 7 * 86400,
    )) == {"closed", "unowned", "ended-unowned"}
    manager = SessionManager(tmp_db)
    assert manager.get_session("retained") is not None
    assert tmp_db.get_session("retained") is not None
    for session_id in ("closed", "unowned", "ended-unowned"):
        assert manager.get_session(session_id) is None
        assert tmp_db.get_session(session_id) is None
    assert tmp_db.get_relationship("closed")["phase"] == "closed"

    _seed(tmp_db, "manual-closed", "stopped", now, "closed")
    _seed(tmp_db, "manual-unowned", "stopped", now)
    manager._rehydrate(read_only=True)
    assert manager.get_session("manual-closed") is not None
    result = manager.gc(now=now)
    assert set(result["pruned"]) == {"manual-closed", "manual-unowned"}
    assert manager.get_session("retained") is not None
    assert tmp_db.get_session("retained") is not None
    for session_id in result["pruned"]:
        assert manager.get_session(session_id) is None
        assert tmp_db.get_session(session_id) is None
    assert tmp_db.get_relationship("manual-closed")["phase"] == "closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["stopped", "pending", "uncertain", "failed-close"])
async def test_aged_handle_rehydrates_and_retries_explicit_end(managed, monkeypatch, outcome):
    manager, calls, claims, _ = managed
    command = authority.command
    if outcome == "pending":
        monkeypatch.setattr(authority, "command", AsyncMock(return_value={}))
    elif outcome == "uncertain":
        async def lost(client, **kwargs):
            calls.append("ambiguous-new")
            raise ConnectionError("reply lost")

        monkeypatch.setattr(AcpClient, "new_session", lost)

    if outcome in {"pending", "uncertain"}:
        with pytest.raises(HTTPException):
            await create(manager)
        session = next(iter(manager._sessions.values()))
    else:
        result = await create(manager)
        session = manager.get_session(result.session_id)
        await manager.stop_session(session.session_id)

    if outcome == "failed-close":
        async def unavailable(operation, *args):
            if operation == "close":
                raise authority.RelationshipRefused("provider unavailable")
            return await command(operation, *args)

        monkeypatch.setattr(authority, "command", unavailable)
        with pytest.raises(authority.RelationshipRefused):
            await manager.end_session(session.session_id)
        assert manager._db.get_relationship(session.session_id)["phase"] == "retired"

    session_id = session.session_id
    original = manager._db.get_relationship(session_id)
    assert session.status == SessionStatus.STOPPED
    now = time.time()
    _age(manager._db, session_id, now)
    assert manager.gc(now=now)["pruned_count"] == 0
    assert manager.get_session(session_id) is session
    before_retry = list(calls)
    async def reconciled(operation, owner=None, payload=None):
        if outcome == "pending" and operation == "inspect" and payload.get("allow_missing") is True:
            return {
                "schema": authority.SCHEMA, "version": 1, "ok": True,
                "owner": original["owner"], "request_id": original["id"], "absent": True,
            }
        return await command(operation, owner, payload)

    monkeypatch.setattr(authority, "command", reconciled)

    reopened = Database(manager._db.db_path)
    try:
        restarted = session_manager_from_config(
            reopened, ServiceConfig(remote_acp=manager._remote_acp_targets),
        )
        restored = restarted.get_session(session_id)
        assert restored is not None and restored.session_id == session_id
        assert reopened.get_relationship(session_id) == original
        # The fake provider's verification callback must inspect the successor.
        manager._sessions = restarted._sessions
        await restarted.end_session(session_id)
        closed = reopened.get_relationship(session_id)
        assert closed["id"] == original["id"] and closed["phase"] == "closed"
        assert closed["uncertain"] == original["uncertain"]
        assert restarted.get_session(session_id) is None
        assert reopened.get_session(session_id)["status"] == "ended"
        _age(reopened, session_id, now)
        assert restarted.gc(now=now)["pruned"] == [session_id]
        assert reopened.get_session(session_id) is None
        assert reopened.get_relationship(session_id) == closed
        assert len(claims) == (0 if outcome == "pending" else 1) and calls == before_retry
    finally:
        reopened.close()


def test_passive_legacy_database_without_journal_keeps_normal_gc(tmp_db):
    now = time.time()
    _seed(tmp_db, "unowned", "stopped", now)
    tmp_db.execute_write("DROP TABLE remote_acp_relationships")
    passive = Database(tmp_db.db_path, read_only=True)
    try:
        assert passive.gc_retained_relationship_session_ids() == set()
        assert passive.gc_eligible_session_ids(
            ["stopped"], now - 7 * 86400,
        ) == ["unowned"]
    finally:
        passive.close()
