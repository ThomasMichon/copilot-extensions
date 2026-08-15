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
    # Session Hosts are always on (dotfiles#1478): a local start now connects via
    # _connect_via_session_host (a survivable host over a loopback socket) which
    # can't stand up in a unit test. Stub it to hand back the mock client + a
    # stable acp id. (resume_session still uses the classic spawn/load_session
    # path the module-level patches cover.)
    async def _fake_host_connect(target, **kwargs):
        return mock_acp_client, mock_acp_client.acp_session_id
    sm._connect_via_session_host = _fake_host_connect
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

        with pytest.raises(asyncio.TimeoutError):
            await sm.resume_session(session.session_id, drain=False)

    assert session.status == SessionStatus.STOPPED
    retries = _events(session, "acp_resume_retry")
    assert len(retries) == _MAX_RESUME_ROUNDS
    assert [r.data["attempt"] for r in retries] == list(range(1, _MAX_RESUME_ROUNDS + 1))
    assert retries[-1].data["will_retry"] is False
    # The type is preserved even though ``str(asyncio.TimeoutError())`` is empty.
    assert retries[-1].data["error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_resume_recreates_when_allowed(tmp_db, spawn_target, mock_acp_client):
    """allow_recreate: after the stop->resume ladder is exhausted, a FRESH ACP
    session (new_session) is created in place -- same bridge id, new acp id,
    context dropped -- recorded as acp_resume_recreated (#1468)."""
    with patch("agent_bridge.session_manager.spawn", return_value=_mock_agent_proc()), \
         patch("agent_bridge.session_manager.AcpClient", return_value=mock_acp_client):
        sm = SessionManager(tmp_db)
        session = await _make_stopped_session(sm, spawn_target, mock_acp_client)
        old_acp = session.acp_session_id

        # start() always succeeds; every load_session (resume) stalls; the fresh
        # new_session (recreate) succeeds.
        mock_acp_client.start = AsyncMock()
        mock_acp_client.load_session = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_acp_client.new_session = AsyncMock(return_value="fresh-acp-999")
        mock_acp_client.stderr_tail = MagicMock(return_value="Resuming...")

        # Stale context-pressure state from the (dropped) old ACP session.
        session.context_used = 999
        session.context_size = 1000
        session._crossed_thresholds = {"warning", "critical"}
        session._handoff_pending = True

        resumed = await sm.resume_session(
            session.session_id, drain=False, allow_recreate=True
        )

    assert resumed.status == SessionStatus.IDLE
    assert resumed.acp_session_id == "fresh-acp-999"
    # Fresh empty session -> context-usage / handoff state is reset so stale
    # "critical"/pending-handoff can't misfire.
    assert resumed.context_used is None
    assert resumed.context_size is None
    assert resumed._crossed_thresholds == set()
    assert resumed._handoff_pending is False
    # The resume ladder still ran (3 stalled rounds) before the recreate.
    assert len(_events(session, "acp_resume_retry")) == _MAX_RESUME_ROUNDS
    recreated = _events(session, "acp_resume_recreated")
    assert len(recreated) == 1
    assert recreated[0].data["old_acp_session_id"] == old_acp
    assert recreated[0].data["new_acp_session_id"] == "fresh-acp-999"
    assert recreated[0].data["context_dropped"] is True
    # A durable recreated lifecycle event is emitted for SSE/telemetry consumers.
    scs = [e for e in _events(session, "session_state_changed") if e.data.get("recreated")]
    assert len(scs) == 1 and scs[0].data["status"] == "idle"


@pytest.mark.asyncio
async def test_resume_recreate_failure_raises(tmp_db, spawn_target, mock_acp_client):
    """If even the fresh new_session recreate fails, the session is left STOPPED
    and the error surfaces (no silent wedged session)."""
    with patch("agent_bridge.session_manager.spawn", return_value=_mock_agent_proc()), \
         patch("agent_bridge.session_manager.AcpClient", return_value=mock_acp_client):
        sm = SessionManager(tmp_db)
        session = await _make_stopped_session(sm, spawn_target, mock_acp_client)

        mock_acp_client.start = AsyncMock()
        mock_acp_client.load_session = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_acp_client.new_session = AsyncMock(side_effect=RuntimeError("no fresh"))
        mock_acp_client.stderr_tail = MagicMock(return_value="")

        with pytest.raises(RuntimeError):
            await sm.resume_session(
                session.session_id, drain=False, allow_recreate=True
            )

    assert session.status == SessionStatus.STOPPED
    assert len(_events(session, "acp_resume_recreated")) == 0
