"""Mux state verification + repair for handoff cutovers.

A handoff cutover isn't genuinely complete just because a new pane was
created and a successor session associated with it -- the operator's actual
console tab must be showing that successor pane. ``tmux new-window``
(without ``-d``) *selects* the new window by default, but nothing previously
*verified* that assumption held, or *repaired* it when it didn't (a
differently configured mux server, a race with another attached client,
etc.). This module makes that an explicit, checked, self-healing step rather
than an unverified side effect of the spawn call
(efforts/active/handoff-cutover-lifecycle-journal).

**Verification is session-scoped by design.** tmux exposes no per-client
"current window" -- every ``client_*`` format variable (``client_tty``,
``client_session``, ...) stops at which *session* a client is attached to;
the active window is a property of the *session* (all its attached clients
observe the same one). So querying the session's active pane is already
authoritative for "what does every attached client of this session show."
**Repair still targets the attached client(s) explicitly** (``switch-client
-c <client-tty>``) rather than only the session-level ``select-window`` --
strictly more precise, and the direction tmux itself points a caller that
wants to affect a specific client's view, in case an operator has actually
re-pointed a client at this session from a different one or a future tmux
adds per-client divergence this module should already be robust to.
"""

from __future__ import annotations

import subprocess

from . import sessions


def verify_and_doctor_current_pane(
    pane_id: str,
    session_name: str,
    *,
    mux: str | None = None,
    retries: int = 1,
) -> dict:
    """Ensure ``pane_id`` is the active pane of ``session_name``; repair if not.

    Returns ``{ok, was_current, doctored, active_pane}``. ``ok`` is True iff
    ``pane_id`` is confirmed the session's active pane after any repair
    attempt -- a caller should treat ``ok=False`` as a failed cutover, not a
    cosmetic issue, since the operator's console tab is the ground truth for
    "did the handoff actually take."
    """
    mux_bin = sessions._mux_bin(mux)
    active = sessions.mux_active_pane_named(session_name, mux=mux_bin)
    if active == pane_id:
        return {
            "ok": True, "was_current": True, "doctored": False,
            "active_pane": active,
        }
    doctored = False
    for _ in range(max(retries, 0)):
        if not _select_pane(pane_id, session_name, mux_bin):
            break
        doctored = True
        active = sessions.mux_active_pane_named(session_name, mux=mux_bin)
        if active == pane_id:
            return {
                "ok": True, "was_current": False, "doctored": True,
                "active_pane": active,
            }
    return {
        "ok": False, "was_current": False, "doctored": doctored,
        "active_pane": active,
    }


def _attached_client_ttys(session_name: str, mux_bin: str) -> list[str]:
    """TTYs of every client currently attached to ``session_name``."""
    try:
        r = subprocess.run(
            [mux_bin, "list-clients", "-t", session_name, "-F", "#{client_tty}"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return []
        return [line for line in r.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def _select_pane(pane_id: str, session_name: str, mux_bin: str) -> bool:
    """Point every attached client of ``session_name`` at ``pane_id``.

    Prefers ``switch-client -c <client-tty>`` per attached client (the
    client-precise primitive) over a bare session-level select; falls back
    to ``select-window``/``select-pane`` when no client is attached (a
    headless/detached session, or if the client list can't be read) so the
    session's own state is still corrected for whenever a client next
    attaches.
    """
    try:
        clients = _attached_client_ttys(session_name, mux_bin)
        if clients:
            ok = True
            for tty in clients:
                r = subprocess.run(
                    [mux_bin, "switch-client", "-c", tty, "-t", pane_id],
                    capture_output=True, timeout=5,
                )
                ok = ok and r.returncode == 0
            return ok
        r = subprocess.run(
            [mux_bin, "select-window", "-t", pane_id],
            capture_output=True, timeout=5,
        )
        ok = r.returncode == 0
        r2 = subprocess.run(
            [mux_bin, "select-pane", "-t", pane_id],
            capture_output=True, timeout=5,
        )
        return ok and r2.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
