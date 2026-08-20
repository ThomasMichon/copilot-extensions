"""Process-spawn helpers shared across the worker-spawn backends.

The coordinator / supervisor runs windowless -- under the installed service
(a hidden Windows Scheduled Task launched via ``conhost --headless``) or inside
the agent-bridge daemon. When such a windowless parent shells out to a *console*
launcher -- the ``agent-bridge`` / ``agent-worktrees`` ``.cmd`` binstubs (which
re-exec ``python.exe``) or ``ssh.exe`` -- Windows allocates a fresh console
window for the child, which **flashes on screen once per worker spawn**.

:func:`no_window_kwargs` supplies the ``CREATE_NO_WINDOW`` creation flag so the
launcher (and its inherited process tree) runs without a console window, while
still allowing captured stdout/stderr over pipes (unlike ``DETACHED_PROCESS``,
which fully detaches). It is a no-op off Windows.
"""

from __future__ import annotations

import os
from pathlib import Path

# Re-exported so existing callers keep using ``from .procutil import
# no_window_kwargs`` while the implementation is single-sourced in the shared
# ``agent_procutil`` lib.
from agent_procutil import detached_kwargs, no_window_kwargs

__all__ = [
    "detached_kwargs",
    "no_window_kwargs",
    "runtime_root",
    "relocate_off_payload",
]


def runtime_root() -> Path:
    """The agent-dispatch runtime root (``~/.agent-dispatch``) -- a stable dir that
    is **never** under the Copilot plugin payload. Safe as a daemon's working
    directory and as a spawn ``cwd``."""
    return Path.home() / ".agent-dispatch"


def relocate_off_payload() -> None:
    """Move the current process's working directory to :func:`runtime_root`.

    A long-lived daemon (the coordinator / supervisor) is lazy-started from a
    session-start hook and inherits that session's CWD -- which is often the
    **plugin payload dir** (``~/.copilot/installed-plugins/.../agent-dispatch``).
    On Windows a live process's CWD **locks that directory tree**, so a payload
    CWD makes ``copilot plugin update`` fail with ``os error 32`` (the runtime
    can never be updated while the daemon it installed is running). Relocating to
    the runtime root at daemon startup guarantees no daemon ever holds the payload
    -- independent of how it was launched. Best-effort; never fatal.
    """
    try:
        root = runtime_root()
        root.mkdir(parents=True, exist_ok=True)
        os.chdir(root)
    except OSError:
        pass
