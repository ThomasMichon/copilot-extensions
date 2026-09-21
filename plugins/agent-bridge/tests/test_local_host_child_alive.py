"""Unit tests for `worktree_probe.resolve_already_live` / its
`_local_host_child_alive` helper, and `SessionManager.settle_dead_local_session`
(agent-bridge-cold-resume Phase 2, #6744): never trust a stale
RUNNING/IDLE status blindly -- verify against the actual Session Host child
pid, and persist the reclassification, not just mutate the in-memory object.
"""

from __future__ import annotations

import os
import time

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.routes import worktree_probe
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _mgr(tmp_path) -> SessionManager:
    return SessionManager(
        Database(tmp_path / "c.db"),
        session_host_state_dir=str(tmp_path / "hosts"),
    )


def _seeded_session(mgr: SessionManager, sid: str, status: SessionStatus) -> Session:
    """A session registered both in-memory and as a real persisted row, so a
    settle's DB write has an actual row to affect (review #3142: a test that
    never inserts a row can't distinguish a real UPDATE from a silent no-op)."""
    s = Session(sid, sid, SpawnTarget(type="local", cwd="/tmp/x"))
    s.status = status
    s.event_log = EventLog(session_id=sid)
    mgr._sessions[sid] = s
    mgr._db.create_session(
        sid, sid, None, "/tmp/x", "local", status.value, time.time(),
    )
    return s


def test_no_host_record_is_inconclusive(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    assert worktree_probe._local_host_child_alive(mgr, "no-such-session") is None


def test_local_record_with_live_pid_is_true(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=os.getpid(), child_pid=os.getpid(),
        boundary="local",
    ))
    assert worktree_probe._local_host_child_alive(mgr, "s1") is True


def test_local_record_with_dead_pid_is_false(tmp_path) -> None:
    """The core Phase 2 signal: a local record whose child pid is confirmed
    dead (0 -- never a real pid) must resolve False, never elevate a stale
    status to trusted-alive."""
    mgr = _mgr(tmp_path)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=0, child_pid=0, boundary="local",
    ))
    assert worktree_probe._local_host_child_alive(mgr, "s1") is False


def test_remote_record_is_inconclusive_not_falsely_confirmed(tmp_path) -> None:
    """A remote (ssh/codespace) host's pid is a far-side pid -- checking it
    locally would be meaningless, so this must degrade to None (defer to
    the existing status), never a false True or a false False."""
    mgr = _mgr(tmp_path)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=0, child_pid=0, boundary="codespace",
    ))
    assert worktree_probe._local_host_child_alive(mgr, "s1") is None


@pytest.mark.asyncio
async def test_resolve_already_live_non_running_idle_is_false(tmp_path) -> None:
    """A STOPPED/FAILED/etc. session is never "already live" -- the live
    check is only meaningful for RUNNING/IDLE."""
    mgr = _mgr(tmp_path)
    session = _seeded_session(mgr, "s1", SessionStatus.STOPPED)
    assert await worktree_probe.resolve_already_live(mgr, "wt-1", session) is False


@pytest.mark.asyncio
async def test_resolve_already_live_reclassifies_and_persists_confirmed_dead_session(
    tmp_path,
) -> None:
    mgr = _mgr(tmp_path)
    session = _seeded_session(mgr, "s1", SessionStatus.IDLE)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=0, child_pid=0, boundary="local",
    ))

    assert await worktree_probe.resolve_already_live(mgr, "wt-1", session) is False
    assert session.status == SessionStatus.STOPPED
    # The persisted row, not just the in-memory object, must reflect it --
    # otherwise a regression dropping the DB write would still pass (#3142).
    row = mgr._db.execute_read("SELECT status FROM sessions WHERE id=?", ("s1",))
    assert row[0]["status"] == SessionStatus.STOPPED.value
    # The stale host record is reaped, not left to linger.
    assert mgr._host_index.get("s1") is None
    # Subscribers must see the transition too, not just DB/memory (#3142).
    events = [e for e in session.event_log._events if e.event == "session_state_changed"]
    assert events and events[-1].data["status"] == SessionStatus.STOPPED.value


@pytest.mark.asyncio
async def test_resolve_already_live_cancels_stale_prompt_task(tmp_path) -> None:
    """(review #3142) A RUNNING session's in-flight prompt task must be
    cancelled as part of settling -- otherwise its own exception handler
    would race and write IDLE back over our STOPPED once mark_host_child_
    exited wakes it, silently reviving a session we just declared dead."""
    import asyncio

    mgr = _mgr(tmp_path)
    session = _seeded_session(mgr, "s1", SessionStatus.RUNNING)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=0, child_pid=0, boundary="local",
    ))

    async def _never_finishes():
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_never_finishes())
    session._prompt_task = task

    assert await worktree_probe.resolve_already_live(mgr, "wt-1", session) is False
    assert task.cancelled()
    assert session.status == SessionStatus.STOPPED

