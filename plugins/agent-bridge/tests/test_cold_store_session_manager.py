"""Tests for SessionManager's cold-store-provider fallback (Phase 2b)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_bridge.cold_store import ColdStoreSession
from agent_bridge.db import Database
from agent_bridge.session_manager import SessionManager


@pytest.mark.asyncio
async def test_fetch_cold_store_session_no_resolver_is_none(tmp_path):
    mgr = SessionManager(Database(tmp_path / "s.db"))
    assert await mgr.fetch_cold_store_session("x") is None


@pytest.mark.asyncio
async def test_fetch_cold_store_session_no_client_registered_is_none(tmp_path):
    mgr = SessionManager(Database(tmp_path / "s.db"))

    class _Resolver:
        def get_cold_store_client(self, capability):
            return None

    mgr.set_resolver(_Resolver())
    assert await mgr.fetch_cold_store_session("x") is None


@pytest.mark.asyncio
async def test_fetch_cold_store_session_delegates_to_client(tmp_path):
    mgr = SessionManager(Database(tmp_path / "s.db"))
    expected = ColdStoreSession(session_id="abc")
    fake_client = AsyncMock()
    fake_client.fetch_session.return_value = expected

    class _Resolver:
        def get_cold_store_client(self, capability):
            assert capability == "session-fetch"
            return fake_client

    mgr.set_resolver(_Resolver())
    result = await mgr.fetch_cold_store_session("abc")
    assert result is expected
    fake_client.fetch_session.assert_awaited_once_with("abc")


@pytest.mark.asyncio
async def test_fetch_cold_store_session_resolver_without_method_is_none(tmp_path):
    mgr = SessionManager(Database(tmp_path / "s.db"))
    mgr.set_resolver(object())  # legacy/mock resolver with no cold-store support
    assert await mgr.fetch_cold_store_session("x") is None
