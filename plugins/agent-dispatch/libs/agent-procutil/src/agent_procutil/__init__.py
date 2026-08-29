"""Windows-headless / detached process-spawn helpers (shared, vendored per plugin).

A copilot-extensions runtime frequently runs **windowless**: under a hidden
Windows Scheduled Task launched via ``conhost --headless``, inside the
agent-bridge daemon, or from a session-start hook whose parent has no console of
its own. When such a windowless parent shells out to a *console* program -- a
``.cmd`` binstub that re-execs ``python.exe``, or ``git.exe`` / ``ssh.exe`` /
``pwsh.exe`` -- Windows allocates a fresh console **window** for the child, which
flashes on screen once per spawn.

These helpers standardize the fix across every plugin so the behavior (and the
exact creation flags) can't drift between copies:

* :func:`no_window_kwargs` -- ``CREATE_NO_WINDOW`` for a non-interactive child
  whose output is still captured over pipes (the common case: git/gh/az checks).
* :func:`detached_kwargs` -- fully detach a background child so it outlives its
  parent AND carries no console (so no window); optionally break away from a
  parent Job object.
* :func:`windowless_daemon_kwargs` -- keep a Windows daemon in a
  ``CREATE_NO_WINDOW`` host while optionally breaking away from an inherited
  Job object; use this when its own children must inherit the windowless host.
* :func:`windowless_python` -- select ``pythonw.exe`` for a detached Python
  daemon on Windows so the venv launcher cannot allocate a second console.

Both are no-ops off Windows (``no_window_kwargs`` -> ``{}``; ``detached_kwargs``
-> ``start_new_session=True``), so call sites stay platform-agnostic::

    subprocess.run(cmd, capture_output=True, **no_window_kwargs())
    subprocess.Popen(
        [windowless_python(), "-m", "my_daemon"],
        **detached_kwargs(breakaway=True),
    )

Vendored per plugin at ``plugins/<plugin>/libs/agent-procutil``; every copy's
``src`` tree MUST stay byte-identical (enforced by
``tools/check-vendored-libs-sync.py``).
"""

from __future__ import annotations

import os
import subprocess
import sys

# Win32 process-creation flags. Read from ``subprocess`` when present (Windows)
# and fall back to the stable ABI literals so this module imports cleanly off
# Windows too. CREATE_BREAKAWAY_FROM_JOB has no ``subprocess`` alias.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_CONTAINED_TEST_ENV = "COPILOT_EXTENSIONS_TEST_CONTAINED"

__all__ = [
    "contained_test_mode",
    "no_window_flags",
    "no_window_kwargs",
    "detached_kwargs",
    "windowless_daemon_kwargs",
    "windowless_python",
]


def contained_test_mode() -> bool:
    """Whether the process is running beneath the repository test supervisor."""
    return os.environ.get(_CONTAINED_TEST_ENV) == "1"


def _is_windows() -> bool:
    return os.name == "nt"


def no_window_flags() -> int:
    """The ``CREATE_NO_WINDOW`` creation-flag **int** on Windows, else ``0``.

    For call sites that pass ``creationflags=`` directly (rather than splatting
    kwargs)::

        subprocess.run(cmd, capture_output=True, creationflags=no_window_flags())

    Off Windows this is ``0`` (a harmless no-op creationflags value).
    """
    return _CREATE_NO_WINDOW if _is_windows() else 0


def no_window_kwargs() -> dict:
    """``subprocess`` kwargs that suppress a console window on Windows.

    Returns ``{"creationflags": CREATE_NO_WINDOW}`` on Windows and ``{}``
    elsewhere, so it splats into a ``subprocess.run`` / ``Popen`` call while
    still allowing captured stdout/stderr over pipes (unlike a full detach).
    """
    if _is_windows():
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def windowless_python(executable: str | os.PathLike[str] | None = None) -> str:
    """Return the interpreter for a fully detached Python daemon.

    A Windows venv ``python.exe`` is a console-subsystem launcher. Even when it
    starts under ``DETACHED_PROCESS``, it re-execs the base ``python.exe`` as a
    child, which allocates a fresh console that Windows Terminal may capture.
    Its ``pythonw.exe`` sibling is a GUI-subsystem launcher and never allocates
    that console. Off Windows, or when no sibling exists, return the requested
    interpreter unchanged.
    """
    python = os.fspath(executable) if executable is not None else sys.executable
    if not _is_windows():
        return python
    candidate = os.path.join(os.path.dirname(python), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else python


def detached_kwargs(*, breakaway: bool = False) -> dict:
    """``Popen`` kwargs that fully **detach** a background child from its parent.

    On Windows, ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` cuts the child
    from the parent console and process group -- a detached process has no
    console, so no window ever appears. Pass ``breakaway=True`` to also add
    ``CREATE_BREAKAWAY_FROM_JOB`` so the child survives when the parent lives in
    a Job object that would otherwise kill its whole tree (the case for a
    daemon spawned from a session-start hook).

    On POSIX, ``start_new_session=True`` puts the child in its own session so a
    parent exit / signal never reaps it. When
    ``COPILOT_EXTENSIONS_TEST_CONTAINED=1``, Windows Job breakaway and POSIX
    session detachment are suppressed so the test runner retains descendant
    ownership.
    """
    if _is_windows():
        flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
        if breakaway and not contained_test_mode():
            flags |= _CREATE_BREAKAWAY_FROM_JOB
        return {"creationflags": flags}
    if contained_test_mode():
        return {}
    return {"start_new_session": True}


def windowless_daemon_kwargs(*, breakaway: bool = False) -> dict:
    """``Popen`` kwargs for a survivable daemon whose children stay windowless.

    Windows uses ``CREATE_NO_WINDOW`` rather than ``DETACHED_PROCESS`` because
    a console-subsystem child spawned by a detached process can allocate a new
    visible console. POSIX uses the same new-session behavior as
    :func:`detached_kwargs`. Contained tests suppress Job breakaway and POSIX
    session detachment.
    """
    if _is_windows():
        flags = _CREATE_NO_WINDOW
        if breakaway and not contained_test_mode():
            flags |= _CREATE_BREAKAWAY_FROM_JOB
        return {"creationflags": flags}
    if contained_test_mode():
        return {}
    return {"start_new_session": True}
