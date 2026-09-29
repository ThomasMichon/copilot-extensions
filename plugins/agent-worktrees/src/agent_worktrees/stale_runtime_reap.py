"""Reap CLI-verb processes still running under a superseded runtime slot.

A version bump publishes a fresh ``versions/<v>`` slot, but a one-shot CLI
verb invocation (``list --json``, ``status --json``, ...) that is still
mid-flight on the OUTGOING slot at cutover time has no self-check to retire
itself -- only the resident status-monitor LOOP rechecks
``_runtime_superseded`` each tick (dotfiles#911 is the prior incident for
that loop's own case). Left alone, such an invocation can wedge indefinitely
on a lock/IPC call and pile up across every deploy it survives: #4268
observed 18 such processes for a single project over roughly two hours,
resolved only by a manual, by-hand kill.

:func:`reap` closes that gap: called from the cutover reap
(``_restart_status_monitor``) alongside the status-monitor singleton's own
known-pid reap, it additionally terminates any OTHER live process whose
resolved executable is still under a superseded ``versions/<old>`` slot --
except one descended from a registered, still-live worktree launcher root
(see :mod:`launch_registry`): that shields a live launcher's own short-lived
``resolve``/``activity-log``/``get`` subprocess calls from being killed
mid-flight by this same sweep (#4454 follow-up).
"""

from __future__ import annotations

import os
import sys


def reap(cfg) -> list[int]:
    """Terminate + return the pids of processes still on a superseded slot.

    ``cfg`` is the ``agent_worktrees.config`` module (passed in rather than
    imported here to keep this module import-light and easily fakeable in
    tests). Best-effort; never raises.
    """
    try:
        from . import launch_registry
        from . import procs as _procs

        install_dir = cfg.install_dir()
        versions_root = os.path.join(str(install_dir), "versions")
        current = os.path.realpath(sys.prefix)
        protect = launch_registry.active_launch_pids(install_dir)
        return [
            t["pid"]
            for t in _procs.terminate_processes_under_executable(
                versions_root, exclude=current, protect_ancestors=protect,
            )
            if t.get("killed")
        ]
    except Exception:
        return []


def summary_bits(reaped: list[int] | None) -> list[str]:
    """The ``status-monitor-restart`` summary fragment(s) for a reap result."""
    if not reaped:
        return []
    return [f"reaped {len(reaped)} stale-runtime process(es)"]


def summary_suffix(reaped: list[int] | None) -> str:
    """The same fragment, formatted to append onto an existing sentence."""
    bits = summary_bits(reaped)
    return f"; {bits[0]}" if bits else ""
