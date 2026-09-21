"""Unit tests for `worktree_probe.resolve_already_live` / its
`_local_host_child_alive` helper (agent-bridge-cold-resume Phase 2,
aperture-labs #6744): never trust a stale RUNNING/IDLE status blindly --
verify against the actual Session Host child pid.
"""

from __future__ import annotations

import os

from agent_bridge.db import Database
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


def _session(sid: str, status: SessionStatus) -> Session:
    s = Session(sid, sid, SpawnTarget(type="local", cwd="/tmp/x"))
    s.status = status
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


def test_resolve_already_live_non_running_idle_is_false(tmp_path) -> None:
    """A STOPPED/FAILED/etc. session is never "already live" -- the live
    check is only meaningful for RUNNING/IDLE."""
    mgr = _mgr(tmp_path)
    session = _session("s1", SessionStatus.STOPPED)
    assert worktree_probe.resolve_already_live(mgr, None, "wt-1", session) is False


def test_resolve_already_live_reclassifies_confirmed_dead_session(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    session = _session("s1", SessionStatus.IDLE)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=0, child_pid=0, boundary="local",
    ))
    db = Database(tmp_path / "c.db")

    assert worktree_probe.resolve_already_live(mgr, db, "wt-1", session) is False
    assert session.status == SessionStatus.STOPPED
