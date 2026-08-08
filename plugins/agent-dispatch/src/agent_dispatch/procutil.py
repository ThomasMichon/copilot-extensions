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


def detached_kwargs() -> dict:
    """``Popen`` kwargs that fully **detach** a child so it outlives its parent.

    Used to hand a blocking wait to a cheap OS-level waiter process that survives
    the worker being torn down (the *hibernate-the-wait* substrate). On Windows,
    ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` cuts the child from the parent
    console and process group; on POSIX, ``start_new_session=True`` puts it in its
    own session so a parent exit / signal never reaps it.
    """
    if os.name == "nt":
        detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return {"creationflags": detached | new_group}
    return {"start_new_session": True}
