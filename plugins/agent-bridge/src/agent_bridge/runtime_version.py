"""Emit a ``running-version.json`` marker for the launch-path reconciler.

``copilot plugin update`` bumps the *installed plugin* (payload) but does **not**
restart the deployed daemon, so agent-bridge can silently keep serving an older
build than its plugin -- the exact incident that motivated dotfiles #533 (a
``plugin update`` advanced the payload while the running daemon kept serving the
old runtime until a manual ``agent-bridge service restart``). The reconciler
(agent-worktrees ``reconcile.py``) compares the payload version against the
runtime's on-disk ``deploy-manifest.json``, which can match the payload while the
*running* daemon still lags. Emitting the actually-imported version on boot gives
the reconciler a truthful running-version signal.

The file is distinct from ``deploy-manifest.json`` (installer-owned):
``{"version", "pid", "started_at"}``. A reader treats a **dead pid** (or a missing
file) as *no running version* and falls back to the on-disk manifest, so this is
purely additive and safe.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

RUNNING_VERSION_FILE = "running-version.json"


def install_dir() -> Path:
    """Runtime root for the daemon (``~/.agent-bridge``)."""
    return Path.home() / ".agent-bridge"


def write_running_version(
    directory: Path | None = None,
    *,
    pid: int | None = None,
    version: str | None = None,
) -> None:
    """Record the running daemon's version + pid on boot (best-effort).

    On the normal boot path both ``pid`` and ``version`` default to *this*
    process (``os.getpid()`` / ``__version__``). The cutover reconciler passes
    them explicitly to point the marker at the freshly cut-over daemon, whose pid
    differs from the deploy process's and which -- being a relay-disabled passive
    -- never wrote its own marker (dotfiles #533 caveat #1).

    Never raises: a write failure only degrades the reconciler's running-version
    signal (it falls back to the on-disk manifest), never the daemon.
    """
    d = directory or install_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": version or __version__,
            "pid": pid if pid is not None else os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        (d / RUNNING_VERSION_FILE).write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError:
        pass
