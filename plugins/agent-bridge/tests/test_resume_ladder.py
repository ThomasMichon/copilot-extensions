"""Tests for the resume recovery ladder (stop->resume xN) in resume_session (#1468)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import _MAX_RESUME_ROUNDS, SessionManager


def _mock_agent_proc():
    proc = MagicMock()
    proc.proc = MagicMock()
    proc.proc.pid = 12345
    proc.proc.returncode = None
    proc.proc.stderr.readline = AsyncMock(return_value=b"")
    return proc


def _events(session, event_type):
    return [e for e in session.event_log.get_events() if e.event == event_type]


async def _make_stopped_session(sm, spawn_target, mock_acp_client):
    """Create an IDLE session, then mark it STOPPED so it is resumable."""
    mock_acp_client.start = AsyncMock()
    mock_acp_client.load_session = AsyncMock()
    session = await sm.start_session(spawn_target, agent_name="a")
    session.status = SessionStatus.STOPPED
    sm._db.update_session_status(
        session.session_id, SessionStatus.STOPPED.value, 0.0
    )
    # Force the fresh-child path (no live Session Host to reattach to).
    sm._try_reattach_live_host = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
async def test_resume_retries_then_succeeds(tmp_db, spawn_target, mock_acp_client):
    """A stalled first launch is re-rolled; the second attempt lands IDLE, and the
    stalled round is recorded as an acp_resume_retry event (with the stderr tail)."""
    with patch("agent_bridge.session_manager.spawn", return_value=_mock_agent_proc()), \
         patch("agent_bridge.session_manager.AcpClient", return_value=mock_acp_client):
        sm = SessionManager(tmp_db)
        session = await _make_stopped_session(sm, spawn_target, mock_acp_client)

        # First launch stalls (TimeoutError), second succeeds.
        mock_acp_client.start = AsyncMock(side_effect=[asyncio.TimeoutError(), None])
        mock_acp_client.load_session = AsyncMock()
        mock_acp_client.stderr_tail = MagicMock(return_value="Resuming...")

        resumed = await sm.resume_session(session.session_id, drain=False)

    assert resumed.status == SessionStatus.IDLE
    retries = _events(session, "acp_resume_retry")
    assert len(retries) == 1
    assert retries[0].data["attempt"] == 1
    assert retries[0].data["will_retry"] is True
    assert retries[0].data["stderr_tail"] == "Resuming..."


@pytest.mark.asyncio
async def test_resume_exhausts_ladder_then_raises(tmp_db, spawn_target, mock_acp_client):
    """Every round stalls -> after _MAX_RESUME_ROUNDS the resume raises and the
    session is left STOPPED, with one retry marker per round (last: will_retry False)."""
    with patch("agent_bridge.session_manager.spawn", return_value=_mock_agent_proc()), \
         patch("agent_bridge.session_manager.AcpClient", return_value=mock_acp_client):
        sm = SessionManager(tmp_db)
        session = await _make_stopped_session(sm, spawn_target, mock_acp_client)

        mock_acp_client.start = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_acp_client.stderr_tail = MagicMock(return_value="")

        with pytest.raises(Exception):
            await sm.resume_session(session.session_id, drain=False)

    assert session.status == SessionStatus.STOPPED
    retries = _events(session, "acp_resume_retry")
    assert len(retries) == _MAX_RESUME_ROUNDS
    assert [r.data["attempt"] for r in retries] == list(range(1, _MAX_RESUME_ROUNDS + 1))
    assert retries[-1].data["will_retry"] is False
