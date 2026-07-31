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
import subprocess


def no_window_kwargs() -> dict:
    """``subprocess`` kwargs that suppress a console window on Windows.

    Returns ``{"creationflags": CREATE_NO_WINDOW}`` on Windows and ``{}``
    elsewhere, so it can be splatted into a ``subprocess.run``/``Popen`` call:

    ``subprocess.run(cmd, capture_output=True, **no_window_kwargs())``
    """
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
