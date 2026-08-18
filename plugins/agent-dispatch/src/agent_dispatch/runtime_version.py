"""Emit a ``running-version.json`` marker for the launch-path reconciler.

``copilot plugin update`` bumps the *installed plugin* (payload) but does **not**
restart the deployed runtime, so a coordinator can silently keep serving an older
build than its plugin. The reconciler (agent-worktrees ``reconcile.py``) compares
the payload version against the runtime's on-disk ``deploy-manifest.json`` -- but
that manifest can match the payload while the *running* process still lags (an
installer wrote the manifest without the process cycling, or a ``-Fresh`` restart
health-passed while an orphan survived). Writing the *actually-imported* version
on boot gives the reconciler a truthful running-version signal, so it can redeploy
even when the on-disk manifest looks current (dotfiles #533).

The file is intentionally distinct from ``deploy-manifest.json`` (installer-owned):
``{"version", "pid", "started_at"}``. A reader treats a **dead pid** (or a missing
file) as *no running version* and falls back to the on-disk manifest -- so this is
purely additive and safe for a service that has not adopted it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

RUNNING_VERSION_FILE = "running-version.json"

#: The versioned-runtime ``current-version`` marker (a plain text file naming the
#: active ``versions/<name>`` slot). Written by the installer's activation step;
#: read here so a long-running daemon can notice its slot was swapped and reload.
CURRENT_VERSION_FILE = "current-version"


def install_dir() -> Path:
    """Runtime root for the coordinator (``~/.agent-dispatch``)."""
    return Path.home() / ".agent-dispatch"


def read_active_version(directory: Path | None = None) -> str | None:
    """The active on-disk runtime version, or ``None`` when absent/unreadable.

    Reads the versioned-runtime ``current-version`` marker (a plain text file, so
    it is never blocked by RedirectionGuard/WinError 448 the way *traversing* the
    ``.venv`` junction is) under the runtime root. A non-elevated ``update`` builds
    the new slot and atomically rewrites this marker; a daemon comparing it against
    the version it is running detects the swap and reload-exits so its launcher can
    restart it on the new slot -- the live-update path for a scheduled-task daemon
    that cannot re-register itself without elevation. Best-effort: a read failure
    returns ``None`` (treated as "no change"), never raising into the serve loop.
    """
    d = directory or install_dir()
    try:
        txt = (d / CURRENT_VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return txt or None


def write_running_version(directory: Path | None = None) -> None:
    """Record the running coordinator's version + pid on boot (best-effort).

    Never raises: a write failure only degrades the reconciler's running-version
    signal (it falls back to the on-disk manifest), never the server.
    """
    d = directory or install_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": __version__,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        (d / RUNNING_VERSION_FILE).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError:
        pass
