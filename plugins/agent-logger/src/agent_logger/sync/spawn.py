"""Detached, update-safe launcher for the session-end sync.

The ``sessionEnd`` hook fires a background sync when a Copilot session ends.
Two problems come with launching that process naively from the hook:

1. **cwd pinning.** A process started by the hook inherits the *ending
   session's* working directory -- a worktree. On Windows a live process
   holding a handle on that directory blocks the worktree from being pruned
   ("directory in use").

2. **Self-update collision.** The sync runs as the agent-logger venv's
   ``python -m agent_logger.sync.engine``. If agent-logger is updated while a
   sync is in flight, the installer reinstalls (and sometimes rebuilds) that
   venv and the ``agent_logger`` package under it -- on Windows a running
   interpreter locks those files, so the update and the sync collide.

This module addresses both by *staging* the ``agent_logger`` package into a
throwaway OS-temp directory and launching a fully **detached** child that runs
the sync from there, with its cwd set to that temp dir (never the worktree,
never the install). Because the child imports the *staged* copy of the package
(via ``PYTHONPATH``), an agent-logger reinstall underneath it cannot swap the
code out from under a running sync; third-party dependencies still resolve from
the venv's ``site-packages`` (which an agent-logger update does not touch).

The parent (the process the hook launches) only stages + spawns, then exits
immediately, so it holds the venv for a fraction of a second. A non-blocking
probe of the existing push lock deduplicates rapid session-ends: if a sync is
already running, we skip staging entirely. The child cleans up its own staging
directory on exit; stale directories from a crashed child are swept on the next
spawn.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agent_logger.sync.lock import sync_lock

#: Env var carrying the staging dir into the child so it can self-clean on exit.
STAGED_ENV = "AGENT_LOGGER_SYNC_STAGED"

#: Prefix for the throwaway staging directories under the OS temp dir.
_STAGE_PREFIX = "agent-logger-sync-"

#: Sweep staging dirs older than this (seconds) -- crashed-child leftovers.
_STALE_AGE_SEC = 6 * 3600


def _package_root() -> Path:
    """Absolute path of the installed ``agent_logger`` package directory."""
    # this file is <pkg>/agent_logger/sync/spawn.py -> parents[1] == agent_logger
    return Path(__file__).resolve().parents[1]


def _sweep_stale(tmp_root: Path) -> None:
    """Best-effort removal of staging dirs a crashed child never cleaned up."""
    now = time.time()
    with contextlib.suppress(OSError):
        for child in tmp_root.glob(f"{_STAGE_PREFIX}*"):
            try:
                if child.is_dir() and (now - child.stat().st_mtime) > _STALE_AGE_SEC:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue


def _stage_package(tmp_root: Path) -> Path:
    """Copy the ``agent_logger`` package into a fresh temp dir; return the dir.

    Only the package source is staged (no ``tests``/bytecode). The staged tree
    is placed at ``<staging>/agent_logger`` so ``<staging>`` can go straight on
    ``PYTHONPATH``.
    """
    staging = Path(tempfile.mkdtemp(prefix=_STAGE_PREFIX, dir=tmp_root))
    shutil.copytree(
        _package_root(),
        staging / "agent_logger",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "tests"),
    )
    return staging


def _detach_kwargs() -> dict:
    """Platform flags for a fully detached, windowless child process."""
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        return {"creationflags": creationflags, "close_fds": True}
    # POSIX: new session so the child outlives the hook's process group.
    return {"start_new_session": True, "close_fds": True}


def spawn_detached_sync(cfg, *, prune: bool = False) -> int:
    """Stage the package and launch a detached child that runs one sync pass.

    Returns quickly (0) after spawning. Dedupes against an in-flight sync via a
    non-blocking probe of the push lock. The launched child runs
    ``agent_logger.sync.engine run [--prune]`` from the staged copy with a
    neutral cwd; it removes its staging dir on exit.
    """
    # A sync already running? Skip staging entirely (rapid session-end dedupe).
    lock_file = cfg.home / "session-sync.lock"
    with sync_lock(lock_file, wait=False) as acquired:
        if not acquired:
            return 0
    # Lock released here; the spawned child re-acquires it for the real push.

    tmp_root = Path(tempfile.gettempdir())
    _sweep_stale(tmp_root)

    try:
        staging = _stage_package(tmp_root)
    except OSError:
        # Staging failed -- fall back to a plain detached run from the temp dir
        # so a session end is never left un-synced just because copytree failed.
        staging = None

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    cwd = str(staging if staging is not None else tmp_root)
    if staging is not None:
        env["PYTHONPATH"] = str(staging) + os.pathsep + env.get("PYTHONPATH", "")
        env[STAGED_ENV] = str(staging)
    else:
        env.pop(STAGED_ENV, None)

    cmd = [sys.executable, "-m", "agent_logger.sync.engine", "run"]
    if prune:
        cmd.append("--prune")

    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, detached background sync
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_detach_kwargs(),
        )
    except OSError:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        return 1
    return 0


def cleanup_staging() -> None:
    """Remove this process's staging dir, if it was launched staged.

    Called from the child's ``main()`` finally. The child's cwd is the staging
    dir, so we step out of it first (Windows cannot remove the cwd).
    """
    staged = os.environ.get(STAGED_ENV)
    if not staged:
        return
    target = Path(staged)
    with contextlib.suppress(OSError):
        os.chdir(tempfile.gettempdir())
    shutil.rmtree(target, ignore_errors=True)
