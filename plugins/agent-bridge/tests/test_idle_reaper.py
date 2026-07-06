"""Idle-session reaper + subscriber tracking (#1826, ownership inversion).

The bridge owns session process lifetime by connection + state: an idle,
unwatched session past the TTL is stopped -- freeing its Copilot child while
staying resumable (fresh child + load_session replay). It never touches a
running/mid-turn session (goal 1), one with a live subscriber, or one hosting
active background sub-agents. A front (Neuron Forge) therefore need only
connect/disconnect and never reaps for resource reasons.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _mgr(tmp_path, *, ttl: float = 0.0) -> SessionManager:
    db = Database(tmp_path / "reap.db")
    return SessionManager(
        db,
        session_host_enabled=True,
        session_host_state_dir=str(tmp_path / "hosts"),
        idle_reap_ttl_seconds=ttl,
    )


def _session(
    mgr: SessionManager,
    sid: str = "s1",
    *,
    status: SessionStatus = SessionStatus.IDLE,
    idle_for: float = 0.0,
    subscribers: int = 0,
    background: bool = False,
) -> Session:
    s = Session(sid, sid, SpawnTarget(type="local", cwd="/tmp/x"))
    s.status = status
    s.turn_count = 1  # skip 0-turn worktree cleanup
    s.updated_at = time.time() - idle_for
    s.subscriber_count = subscribers
    if background:
        client = MagicMock()
        client.has_active_background_tasks = True
        s.client = client
    mgr._sessions[sid] = s
    return s


# -- subscriber tracking --------------------------------------------------

def test_add_remove_subscriber_counts(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    _session(mgr, "s1", subscribers=0)
    mgr.add_subscriber("s1")
    mgr.add_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 2
    mgr.remove_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 1
    mgr.remove_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 0
    # clamp at zero
    mgr.remove_subscriber("s1")
    assert mgr._sessions["s1"].subscriber_count == 0


def test_remove_last_subscriber_resets_idle_clock(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    s = _session(mgr, "s1", subscribers=1, idle_for=1000)
    mgr.remove_subscriber("s1")
    # touch() on the last unsubscribe starts the idle-reap TTL from *now*.
    assert time.time() - s.updated_at < 5


def test_subscriber_ops_on_unknown_session_are_noops(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    mgr.add_subscriber("nope")
    mgr.remove_subscriber("nope")  # must not raise


# -- idle reaper decision matrix ------------------------------------------

@pytest.mark.asyncio
async def test_reaper_disabled_when_ttl_zero(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=0)
    _session(mgr, "s1", idle_for=99999)
    assert await mgr.sweep_idle_sessions() == 0
    assert mgr._sessions["s1"].status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_stops_idle_unwatched_past_ttl_and_reaps_host(
    tmp_path, monkeypatch
) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    reasons: list[str] = []
    monkeypatch.setattr(
        mgr, "_reap_host_record", lambda rec, reason: reasons.append(reason)
    )
    s = _session(mgr, "s1", idle_for=120, subscribers=0)
    mgr._host_index.register(
        HostRecord(session_id="s1", port=1, host_pid=1, child_pid=1)
    )

    assert await mgr.sweep_idle_sessions() == 1
    assert s.status == SessionStatus.STOPPED  # resumable, not ended
    assert reasons  # the Copilot child was reaped (freed), not merely detached


@pytest.mark.asyncio
async def test_reaper_skips_watched_session(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "s1", idle_for=120, subscribers=1)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_skips_within_ttl(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=600)
    s = _session(mgr, "s1", idle_for=120)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_never_touches_running_session(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "s1", status=SessionStatus.RUNNING, idle_for=99999)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_reaper_skips_active_background_tasks(tmp_path) -> None:
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "s1", idle_for=99999, background=True)
    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_reaper_no_op_when_session_host_disabled(tmp_path) -> None:
    db = Database(tmp_path / "d.db")
    mgr = SessionManager(db, session_host_enabled=False, idle_reap_ttl_seconds=60)
    _session(mgr, "s1", idle_for=99999)
    assert await mgr.sweep_idle_sessions() == 0
