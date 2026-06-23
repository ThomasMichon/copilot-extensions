"""Shared test fixtures for agent-bridge tests."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import time

import pytest

from agent_bridge.config import load_or_create_auth_token
from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.models import ServiceConfig
from agent_bridge.session_manager import SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Database]:
    """Create a temporary SQLite database."""
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def event_log(tmp_db: Database) -> EventLog:
    """Create an EventLog backed by the tmp database."""
    # Events have a FK to sessions, so create the parent session first
    tmp_db.create_session(
        "test-session", "test", None, ".", "local", "idle", time.time()
    )
    return EventLog(db=tmp_db, session_id="test-session")


@pytest.fixture
def session_manager(tmp_db: Database) -> SessionManager:
    """Create a SessionManager with a temporary DB."""
    return SessionManager(tmp_db)


@pytest.fixture
def spawn_target() -> SpawnTarget:
    """A local SpawnTarget for testing."""
    return SpawnTarget(type="local", cwd="/tmp/test-dir")


@pytest.fixture
def mock_acp_client():
    """Create a mock AcpClient."""
    client = MagicMock()
    client.is_running = True
    client.pid = 12345
    client.acp_session_id = "acp-test-123"
    client.start = AsyncMock()
    client.new_session = AsyncMock(return_value="acp-test-123")
    client.load_session = AsyncMock()
    client.send_prompt = AsyncMock(return_value={
        "response_text": "Hello from the agent",
        "thought_text": "Thinking...",
        "tool_calls": [],
        "stop_reason": "end_turn",
        "error": None,
    })
    client.cancel_prompt = AsyncMock()
    client.shutdown = AsyncMock()
    return client


@pytest.fixture
def test_config(tmp_path: Path) -> ServiceConfig:
    """Create a test ServiceConfig."""
    return ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        log_level="warning",
    )


@pytest.fixture
def auth_token(tmp_path: Path) -> str:
    """Generate a test auth token."""
    return "test-token-abc123"
