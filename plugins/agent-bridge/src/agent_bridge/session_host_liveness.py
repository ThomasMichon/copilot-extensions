"""Boundary-aware Session Host liveness for ``SessionManager``.

Split out of ``session_manager.py`` (module-size guard) to keep the
local-vs-remote pid-alive checks, the Phase 2 (#6744) dead-session settle,
and the live-host record queries in one small, focused unit. A mixin, not
a standalone service -- it reads ``SessionManager``'s own ``_host_index``/
``_db`` state and calls its other methods (``_reap_host_record``).
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

from .models import SessionStatus

if TYPE_CHECKING:
    from .session_manager import Session


class _HostLivenessMixin:
    """Boundary-aware Session Host liveness checks and settle."""

    def _rec_host_alive(self, rec: Any) -> bool:
        """Is a Session Host still alive? Boundary-aware.

        A **local** host is a local process, so ``pid_alive`` is authoritative.
        A **remote** host's ``host_pid`` is a *far-side* pid -- checking it
        against local processes is meaningless (and would randomly match an
        unrelated local pid). A remote host is instead treated as *presumed
        alive* here and **verified** by the actual forward + ATTACH probe in
        ``_reattach_one`` (which prunes on failure), so a truly-dead remote host
        is dropped when the reattach fails rather than by a bogus local pid check.
        """
        from .session_host.osutil import pid_alive
        if getattr(rec, "boundary", "local") == "local":
            return pid_alive(rec.host_pid)
        return True

    def _rec_child_alive(self, rec: Any) -> bool:
        """Is the copilot child alive? Local: ``pid_alive``; remote: presumed
        (a dead remote child surfaces as the host closing on ATTACH)."""
        from .session_host.osutil import pid_alive
        if getattr(rec, "boundary", "local") == "local":
            return pid_alive(rec.child_pid)
        return True

    def settle_dead_local_session(self, session: "Session") -> None:
        """Phase 2 (#6744): settle a session whose local host child is
        confirmed dead -- mirrors the reattach path's own dead-child settle
        (``mark_host_child_exited`` avoids hanging on a dead transport),
        reaps the stale host record, and persists STOPPED."""
        if session.client is not None:
            session.client.mark_host_child_exited(-1)
            session.client = None
        rec = self._host_index.get(session.session_id) if self._host_index else None
        if rec is not None:
            self._reap_host_record(rec, "resume_worktree: local host child dead")
        session.status = SessionStatus.STOPPED
        self._db.update_session_status(
            session.session_id, SessionStatus.STOPPED.value, time.time()
        )

    def _live_host_records(self) -> list[Any]:
        """Records whose host is (boundary-appropriately) alive."""
        if self._host_index is None:
            return []
        return [r for r in self._host_index.all() if self._rec_host_alive(r)]

    def _live_remote_host_sessions(self) -> set[str]:
        """Session ids backed by a live **remote** (ssh/codespace) Session Host.

        A remote host fronts a child whose turn / tool-call activity runs across
        the boundary and is **not** reflected by the local session status: a
        ``--reply-timeout`` detach, a resume-into-``[starting]``, a host reap, or
        a tunnel flap can leave the local status ``IDLE``/``STARTING`` while the
        far-side child is mid-work. So these sessions must not be judged
        idle/not-busy from local state alone (dotfiles#1633) -- the drain must
        count them and the idle reaper must not free their child. Local-boundary
        hosts are excluded (their pid + status are locally authoritative)."""
        out: set[str] = set()
        for rec in self._live_host_records():
            if getattr(rec, "boundary", "local") == "local":
                continue
            sid = getattr(rec, "session_id", None)
            if sid:
                out.add(sid)
        return out

    def _prune_dead_hosts(self) -> None:
        """Drop records whose host is dead (local only -- remote is verified by
        the reattach probe, never by a local pid check)."""
        if self._host_index is None:
            return
        for r in [r for r in self._host_index.all() if not self._rec_host_alive(r)]:
            with contextlib.suppress(Exception):
                self._host_index.remove(r.session_id)
