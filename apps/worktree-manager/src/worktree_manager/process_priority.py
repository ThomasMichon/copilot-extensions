"""Self-boost this process's Windows scheduling priority.

Interim mitigation for https://github.com/psmux/psmux/issues/608-style
keystroke drop/lag under load on a heavily loaded box, applied to the
Manager's own user-interactive surfaces -- the Textual Picker and the
mux-daemon / "Mux Companion" resident process. Mirrors the rationale
psmux itself applies to its own server+client (see
``worktree-manager/bin/session-options.ps1``'s ``Set-AwPsmuxServerPriority``
for the psmux-side counterpart, applied externally since psmux is a
separate binary): a multiplexer/control surface starved at Normal priority
queues input behind every other process on the box, while the actual
workload it hosts is deliberately left untouched.

Self-applied (no PID lookup needed) at process startup, before any heavy
work, so the boost is inherited by the whole process. Never raises: an
unsupported platform, or a sandbox that forbids priority changes, just
leaves the process at Normal.

The Picker caller MUST call ``restore_normal_process_priority()`` once its
interactive TUI session ends and before dispatching the operator's launch
decision -- a decision dispatch (``launcher.execute()``, ``relocated_launch``,
etc.) spawns the actual workload (a shell, a Copilot session) as a CHILD of
this process, and Windows child processes inherit their parent's priority
class by default. Left unrestored, every workload the Picker launches would
silently inherit AboveNormal too, which is exactly the inheritance psmux's
own design (and this module's own docstring above) deliberately avoids.
"""

from __future__ import annotations

import os

_ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
_NORMAL_PRIORITY_CLASS = 0x00000020


def _set_current_process_priority_class(priority_class: int) -> None:
    """Best-effort, Windows-only: set THIS process's priority class. No-op elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # GetCurrentProcess returns a pseudo-HANDLE (-1); ctypes' default
        # restype (c_int) truncates that 64-bit value and silently hands
        # SetPriorityClass a bogus handle, so the call appears to succeed
        # but never actually touches the real process. Declare both
        # signatures explicitly so the handle round-trips correctly.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel32.GetCurrentProcess()
        kernel32.SetPriorityClass(ctypes.c_void_p(handle), ctypes.c_uint(priority_class))
    except Exception:  # pragma: no cover - Windows-only, best-effort
        pass


def raise_current_process_priority() -> None:
    """Best-effort: boost THIS process to AboveNormal on Windows. No-op elsewhere."""
    _set_current_process_priority_class(_ABOVE_NORMAL_PRIORITY_CLASS)


def restore_normal_process_priority() -> None:
    """Best-effort: restore THIS process to Normal on Windows. No-op elsewhere.

    Call before spawning/exec'ing a workload a boosted process hosts, so the
    workload's children don't silently inherit the boost (see module
    docstring).
    """
    _set_current_process_priority_class(_NORMAL_PRIORITY_CLASS)
