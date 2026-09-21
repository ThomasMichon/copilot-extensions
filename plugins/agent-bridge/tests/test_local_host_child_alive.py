"""Unit tests for `SessionManager.local_host_child_alive` (agent-bridge-cold-
resume Phase 2, aperture-labs #6744): never trust a stale RUNNING/IDLE status
blindly -- verify against the actual Session Host child pid.
"""

from __future__ import annotations

import os

from agent_bridge.db import Database
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import SessionManager


def _mgr(tmp_path) -> SessionManager:
    return SessionManager(
        Database(tmp_path / "c.db"),
        session_host_state_dir=str(tmp_path / "hosts"),
    )


def test_no_host_record_is_inconclusive(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    assert mgr.local_host_child_alive("no-such-session") is None


def test_local_record_with_live_pid_is_true(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=os.getpid(), child_pid=os.getpid(),
        boundary="local",
    ))
    assert mgr.local_host_child_alive("s1") is True


def test_local_record_with_dead_pid_is_false(tmp_path) -> None:
    """The core Phase 2 signal: a local record whose child pid is confirmed
    dead (0 -- never a real pid) must resolve False, never elevate a stale
    status to trusted-alive."""
    mgr = _mgr(tmp_path)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=0, child_pid=0, boundary="local",
    ))
    assert mgr.local_host_child_alive("s1") is False


def test_remote_record_is_inconclusive_not_falsely_confirmed(tmp_path) -> None:
    """A remote (ssh/codespace) host's pid is a far-side pid -- checking it
    locally would be meaningless, so this must degrade to None (defer to
    the existing status), never a false True or a false False."""
    mgr = _mgr(tmp_path)
    mgr._host_index.register(HostRecord(
        session_id="s1", port=1, host_pid=0, child_pid=0, boundary="codespace",
    ))
    assert mgr.local_host_child_alive("s1") is None
