"""Remote-boundary awareness for busy/drain + the idle reaper (dotfiles#1633).

agent-bridge historically judged "is this session busy / idle?" purely from the
**local** frontend state (``Session.status`` / local background tasks). For a
``codespace:`` / ``ssh`` session -- the majority of hosts -- the turn's real work
runs across the Session-Host boundary, so the local status is not authoritative:
a ``--reply-timeout`` detach, a resume-into-``[starting]``, a host reap, or a
tunnel flap can leave the local status ``IDLE``/``STARTING`` while the far-side
child is mid tool-call. That let two reapers destroy live remote work:

* ``drain`` reported a false-clean "0 session(s) busy" and a cutover tore a live
  remote turn down;
* the idle reaper "freed the child" of a locally-idle codespace session whose
  remote child was actually running a long build poll.

These tests pin the fix: both ``busy_sessions`` and ``sweep_idle_sessions`` are
now boundary-aware via ``_live_remote_host_sessions``. Local-boundary hosts keep
their original behavior (their pid + status are locally authoritative).
"""

from __future__ import annotations

import time

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


def _mgr(tmp_path, *, ttl: float = 0.0) -> SessionManager:
    return SessionManager(
        Database(tmp_path / "rb.db"),
        session_host_state_dir=str(tmp_path / "hosts"),
        idle_reap_ttl_seconds=ttl,
    )


def _session(
    mgr: SessionManager,
    sid: str,
    *,
    status: SessionStatus = SessionStatus.IDLE,
    idle_for: float = 0.0,
    turns: int = 1,
) -> Session:
    s = Session(sid, sid, SpawnTarget(type="command", cwd="/tmp/x"))
    s.status = status
    s.turn_count = turns
    s.updated_at = time.time() - idle_for
    s.subscriber_count = 0
    mgr._sessions[sid] = s
    return s


def _register_host(mgr: SessionManager, sid: str, *, boundary: str) -> None:
    # A remote host's host_pid is a far-side pid (never checked locally); a local
    # host's must be a live pid so ``_rec_host_alive`` -> pid_alive() is True.
    import os
    host_pid = os.getpid() if boundary == "local" else 999_000_001
    mgr._host_index.register(
        HostRecord(
            session_id=sid, port=1, host_pid=host_pid, child_pid=host_pid,
            boundary=boundary,
        )
    )


# -- idle reaper: remote hosts are never reaped on local idle signals --------

@pytest.mark.asyncio
async def test_idle_reaper_skips_live_remote_host(tmp_path) -> None:
    """A locally-idle codespace/ssh session with a live remote host must NOT be
    reaped: its far-side child may be mid remote tool-call (dotfiles#1633)."""
    mgr = _mgr(tmp_path, ttl=60)
    s = _session(mgr, "remote1", idle_for=99_999)  # way past TTL, unwatched
    _register_host(mgr, "remote1", boundary="codespace")

    assert await mgr.sweep_idle_sessions() == 0
    assert s.status == SessionStatus.IDLE  # child NOT freed


@pytest.mark.asyncio
async def test_idle_reaper_still_reaps_local_host(tmp_path, monkeypatch) -> None:
    """Regression: a local-boundary host is still reaped (local pid + status are
    authoritative), so the fix narrows only the remote case."""
    mgr = _mgr(tmp_path, ttl=60)
    reasons: list[str] = []
    monkeypatch.setattr(
        mgr, "_reap_host_record", lambda rec, reason: reasons.append(reason)
    )
    s = _session(mgr, "local1", idle_for=99_999)
    _register_host(mgr, "local1", boundary="local")

    assert await mgr.sweep_idle_sessions() == 1
    assert s.status == SessionStatus.STOPPED  # reaped (resumable), child freed
    assert reasons


# -- busy_sessions: remote hosts are accounted for ---------------------------

def test_busy_sessions_counts_starting(tmp_path) -> None:
    """A mid connect/resume (STARTING) session is fragile and must not be torn
    down -- the exact state the reverted victim was in at the cutover."""
    mgr = _mgr(tmp_path)
    _session(mgr, "starting1", status=SessionStatus.STARTING)
    assert "starting1" in set(mgr.busy_sessions())


def test_busy_sessions_counts_live_remote_host_when_not_idle(tmp_path) -> None:
    """A live remote host whose session is not at-rest IDLE (here STARTING) is
    busy -- drain must not report a false-clean "0 busy"."""
    mgr = _mgr(tmp_path)
    _session(mgr, "remote1", status=SessionStatus.STARTING)
    _register_host(mgr, "remote1", boundary="codespace")
    assert "remote1" in set(mgr.busy_sessions())


def test_busy_sessions_excludes_at_rest_idle_remote_host(tmp_path) -> None:
    """An at-rest IDLE remote host is preserved across a cutover by host
    reattach, so it need not block drain (avoids blocking every redeploy)."""
    mgr = _mgr(tmp_path)
    _session(mgr, "remote_idle", status=SessionStatus.IDLE)
    _register_host(mgr, "remote_idle", boundary="codespace")
    assert "remote_idle" not in set(mgr.busy_sessions())


def test_busy_sessions_ignores_local_host(tmp_path) -> None:
    """A local-boundary host does not change busy semantics: an idle local
    session is still not busy (local status is authoritative)."""
    mgr = _mgr(tmp_path)
    _session(mgr, "local_idle", status=SessionStatus.IDLE)
    _register_host(mgr, "local_idle", boundary="local")
    assert "local_idle" not in set(mgr.busy_sessions())


def test_busy_sessions_running_and_starting_together(tmp_path) -> None:
    mgr = _mgr(tmp_path)
    _session(mgr, "idle", status=SessionStatus.IDLE)
    _session(mgr, "running", status=SessionStatus.RUNNING)
    _session(mgr, "starting", status=SessionStatus.STARTING)
    assert set(mgr.busy_sessions()) == {"running", "starting"}
