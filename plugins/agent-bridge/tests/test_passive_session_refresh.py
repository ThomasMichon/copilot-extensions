"""Passive promotion refreshes inherited projections without disrupting local readers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path
import time
from typing import cast
from unittest.mock import Mock

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.passive_maintenance import refresh_inherited_sessions
from agent_bridge.routes.sessions import _sse_event_stream
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_status import SessionStatus


@pytest.mark.asyncio
async def test_subscribed_session_refreshes_lifecycle_and_durable_cursor(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    try:
        db.create_session("watched", "watched", None, str(tmp_path), "local", "running", time.time())
        writer = EventLog(db=db, session_id="watched")
        writer.append("agent_message", {"text": "first"})
        manager = SessionManager(db, read_only_rehydrate=True)
        session = manager.get_session("watched")
        assert session is not None and session.event_log is not None
        session.awaiting_maintenance_refresh = True
        subscribed_log = session.event_log
        stream = _sse_event_stream(
            session, 0, server=None, mgr=manager, is_disconnected=None, signal_gaps=True,
        )
        async with aclosing(cast(AsyncGenerator[str, None], stream)) as reader:
            assert "id: 1\n" in await asyncio.wait_for(anext(reader), 2)
            assert session.subscriber_count == 1
            second = asyncio.create_task(anext(reader))
            await asyncio.sleep(0)
            writer.append("agent_message", {"text": "second"})
            db.update_session_stopped("watched", time.time(), False)
            refresh_inherited_sessions(manager)
            assert session.status == SessionStatus.STOPPED and not session.background_recovery_enabled
            assert session.event_log is subscribed_log and not session.awaiting_maintenance_refresh
            delivered = await asyncio.wait_for(second, 2)
            assert "id: 2\n" in delivered and "second" in delivered
            assert subscribed_log.append("agent_message", {"text": "third"}).id == 3
            assert "id: 3\n" in await asyncio.wait_for(anext(reader), 2)
        assert session.subscriber_count == 0
    finally:
        db.close()


def test_missing_durable_row_evicts_only_unused_projection(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    try:
        for sid in ("removed", "locally-owned"):
            db.create_session(sid, sid, None, str(tmp_path), "local", "running", time.time())
        manager = SessionManager(db, read_only_rehydrate=True)
        for session in manager.list_sessions():
            session.awaiting_maintenance_refresh = True
            db.delete_session(session.session_id)
        owned = manager.get_session("locally-owned")
        assert owned is not None
        owned.client = Mock()
        refresh_inherited_sessions(manager)
        assert manager.get_session("removed") is None
        assert manager.list_sessions() == [owned] and owned.client is not None
        assert db.list_sessions() == []
    finally:
        db.close()
