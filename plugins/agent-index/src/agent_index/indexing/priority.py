"""Host-resource good citizenship for the background indexer.

The index worker runs as a **dedicated short-lived subprocess** (``agent-index
index-worker``), so it may lower its OWN scheduling priority without touching the
retrieval service that serves queries. This keeps background/webhook-driven
reindexing from driving the host to critical CPU load: the work yields to
foreground/interactive processes under contention while still running at full
speed on an idle box (``nice`` only bites when something else wants the core).

Applied ONCE at worker startup, before any indexing threads or the out-of-process
FTS/embedding children are created, so the lowered priority is inherited by the
whole indexing workload (child threads inherit the creating thread's niceness;
child processes inherit the parent's).

Every step is best-effort and platform-guarded: an unsupported platform (or a
sandbox that forbids priority changes) simply runs at normal priority rather than
failing the indexing job.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)


def lower_current_process_priority(nice: int) -> None:
    """Lower this process's CPU (and best-effort IO) priority for background work.

    ``nice`` is the POSIX nice *increment* (a positive number lowers priority).
    A value <= 0 disables the throttle so an explicit full-speed run is possible.
    Never raises: every backend is wrapped so an unsupported platform is a no-op.
    """
    if nice <= 0:
        return
    _lower_cpu_priority(nice)
    _lower_io_priority()


def _lower_cpu_priority(nice: int) -> None:
    # POSIX: os.nice() is RELATIVE and returns the new value, so it only ever
    # lowers priority relative to the (possibly already-niced) parent -- it can
    # never raise this worker above the process that spawned it.
    if hasattr(os, "nice"):
        try:
            new = os.nice(nice)  # type: ignore[attr-defined]
            log.debug("indexer CPU priority lowered (nice=%d)", new)
            return
        except OSError:
            log.debug("os.nice failed; leaving CPU priority unchanged", exc_info=True)
            return

    # Windows: drop the whole process to a below-normal (or idle) priority class.
    if sys.platform.startswith("win"):
        try:
            import ctypes

            below_normal = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
            idle = 0x00000040  # IDLE_PRIORITY_CLASS
            cls = idle if nice >= 15 else below_normal
            handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            if ctypes.windll.kernel32.SetPriorityClass(handle, cls):  # type: ignore[attr-defined]
                log.debug("indexer CPU priority lowered (win class 0x%x)", cls)
            else:
                log.debug("SetPriorityClass returned 0; priority unchanged")
        except Exception:  # pragma: no cover - Windows-only, best-effort
            log.debug("could not lower Windows priority", exc_info=True)


def _lower_io_priority() -> None:
    # Linux-only, best-effort: best-effort IO class at the lowest level (-c2 -n7),
    # mirroring the container/wrapper throttle used by the hosted deployments. A
    # no-op where the block scheduler is 'none' (e.g. WSL2) but correct on a real
    # block device. Shells out to the `ionice` binary (arch-independent) rather
    # than hardcoding the ioprio_set syscall number; skipped when absent.
    if not sys.platform.startswith("linux"):
        return
    import shutil
    import subprocess

    exe = shutil.which("ionice")
    if not exe:
        return
    try:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [exe, "-c", "2", "-n", "7", "-p", str(os.getpid())],
            check=False,
            capture_output=True,
            timeout=5,
        )
        log.debug("indexer IO priority lowered (ionice best-effort -c2 -n7)")
    except Exception:  # pragma: no cover - best-effort
        log.debug("could not lower IO priority", exc_info=True)
