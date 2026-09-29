"""Known Copilot CLI startup dialogs that block a detached/headless launch.

Split out of :mod:`agent_worktrees.sessions` (module-size cap) -- kept as its
own tiny, narrowly-scoped module because it is one specific, growable list of
"known interactive dialogs a detached embodiment must dismiss on its own,"
distinct from :func:`sessions.mux_seed_pane`'s general readiness-polling loop
that calls into it.

**Narrow by design.** Each entry matches a dialog's own distinctive copy, not
a generic "any selection dialog" rule -- a task's own legitimate interactive
prompt (e.g. an ``ask_user`` form) must never be touched here.
"""

from __future__ import annotations

import subprocess


def is_desktop_app_nudge(capture: str) -> bool:
    """True when ``capture`` shows Copilot's first-run desktop-app install nudge.

    Confirmed live: this dialog can resurface after a CLI auto-update even
    when ``~/.copilot/config.json``'s ``appTipShown`` was already recorded
    ``true`` (presumably reset by the update's own config migration). It waits
    for an arrow-key/Enter selection a detached/headless launch can never
    provide, so without dismissing it, a detached embodiment silently
    deadlocks until its ready-timeout and reports a generic "not ready" --
    indistinguishable from a genuinely slow or broken embodiment.
    """
    low = capture.lower()
    return "desktop app" in low and "yes, install" in low and "no, thanks" in low


def dismiss(mux_bin: str, pane_id: str) -> None:
    """Send ``Escape`` to ``pane_id`` -- confirmed live to cleanly dismiss the
    desktop-app nudge and let Copilot proceed to its ordinary ready prompt.
    Best-effort: a failed send is not fatal, the caller's own polling loop
    simply keeps waiting and eventually times out as before.
    """
    try:
        subprocess.run(
            [mux_bin, "send-keys", "-t", pane_id, "Escape"],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
