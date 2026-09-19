"""Tests for the cold-store-provider process-boundary client.

Mirrors ``test_cli_namespace_resolver.py``'s approach: mock ``subprocess.run``
directly rather than spawning real child processes, since the client's own
exit-code/JSON contract is what's under test, not subprocess plumbing.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from agent_bridge.cold_store import ColdStoreClient, ColdStoreSession


def _cp(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.mark.asyncio
async def test_fetch_session_success():
    payload = json.dumps({
        "session": {
            "session_id": "abc123",
            "status": "ended",
            "cwd": "/home/x/repo",
            "worktree_id": "wt-1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
        },
        "events": [{"type": "user.message", "content": "hi"}],
    })
    with patch("subprocess.run", return_value=_cp(0, payload)) as mock_run:
        client = ColdStoreClient(("/abs/agent-logger",))
        result = await client.fetch_session("abc123")
    assert mock_run.call_args.args[0] == [
        "/abs/agent-logger", "session-fetch", "abc123", "--json",
    ]
    assert isinstance(result, ColdStoreSession)
    assert result.session_id == "abc123"
    assert result.status == "ended"
    assert result.worktree_id == "wt-1"
    assert len(result.events) == 1


@pytest.mark.asyncio
async def test_fetch_session_not_found():
    with patch("subprocess.run", return_value=_cp(3)):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("nope") is None


@pytest.mark.asyncio
async def test_fetch_session_provider_error_is_none():
    with patch("subprocess.run", return_value=_cp(1, "", "boom")):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("x") is None


@pytest.mark.asyncio
async def test_fetch_session_unparseable_output_is_none():
    with patch("subprocess.run", return_value=_cp(0, "not json")):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("x") is None


@pytest.mark.asyncio
async def test_fetch_session_id_mismatch_is_none():
    payload = json.dumps({"session": {"session_id": "other"}, "events": []})
    with patch("subprocess.run", return_value=_cp(0, payload)):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("x") is None


@pytest.mark.asyncio
async def test_fetch_session_missing_session_key_is_none():
    payload = json.dumps({"events": []})
    with patch("subprocess.run", return_value=_cp(0, payload)):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("x") is None


@pytest.mark.asyncio
async def test_fetch_session_malformed_events_falls_back_to_empty():
    payload = json.dumps({
        "session": {"session_id": "x"},
        "events": ["not-a-dict"],
    })
    with patch("subprocess.run", return_value=_cp(0, payload)):
        client = ColdStoreClient(("/abs/agent-logger",))
        result = await client.fetch_session("x")
    assert result is not None
    assert result.events == ()


@pytest.mark.asyncio
async def test_fetch_session_missing_session_id_key_is_none():
    """A provider that omits `session_id` must not be treated as a match for
    the requested ID (the identity field is required, never defaulted)."""
    payload = json.dumps({"session": {"status": "ended"}, "events": []})
    with patch("subprocess.run", return_value=_cp(0, payload)):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("x") is None


@pytest.mark.asyncio
async def test_fetch_session_non_string_session_id_is_none():
    payload = json.dumps({"session": {"session_id": 12345}, "events": []})
    with patch("subprocess.run", return_value=_cp(0, payload)):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("x") is None


@pytest.mark.asyncio
async def test_fetch_session_malformed_optional_fields_normalize_to_none():
    """Wrong-typed optional fields (a provider bug) never fail the whole
    lookup -- they normalize to None just like an absent field would."""
    payload = json.dumps({
        "session": {
            "session_id": "x",
            "status": 42,
            "cwd": ["not", "a", "string"],
            "created_at": None,
        },
        "events": [],
    })
    with patch("subprocess.run", return_value=_cp(0, payload)):
        client = ColdStoreClient(("/abs/agent-logger",))
        result = await client.fetch_session("x")
    assert result is not None
    assert result.status is None
    assert result.cwd is None
    assert result.created_at is None


@pytest.mark.asyncio
async def test_fetch_session_spawn_failure_is_none():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        client = ColdStoreClient(("/abs/does-not-exist",))
        assert await client.fetch_session("x") is None


@pytest.mark.asyncio
async def test_fetch_session_timeout_is_none():
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=30),
    ):
        client = ColdStoreClient(("/abs/agent-logger",))
        assert await client.fetch_session("x") is None
