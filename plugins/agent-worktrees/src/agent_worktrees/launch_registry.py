"""Registry of live worktree-launcher process roots (#4454 follow-up).

``stale_runtime_reap`` unconditionally kills every process whose resolved
executable sits under a superseded ``versions/<old>`` slot, machine-wide, with
no age check or "is this actually wedged" test (see its own docstring). That
is correct for a truly abandoned CLI-verb invocation, but a *live* worktree
launcher (``launch-session.ps1``/``.sh``) repeatedly shells out to short-lived
``agent_worktrees resolve``/``activity-log``/``get`` subprocesses throughout an
interactive session -- any one of those can be caught mid-flight by a cutover
reap that fires (from a completely different, concurrent launch's own
self-update) at the wrong instant, killing a legitimate call and crashing the
launcher with no diagnostic.

This module is the fix's other half: each launcher registers its own root pid
here, once, right after it learns its worktree id. The reap then treats any
process descended from a registered, still-live root as protected -- shielding
every subprocess call that root spawns, without needing each of those
short-lived calls to register individually.

One lock file per **``(worktree id, pid)``** pair, reusing :mod:`locks`'
provable-liveness primitive (pid + start-time token) so a crashed launcher's
stale entry reads as dead rather than falsely protecting an unrelated, later
process that happens to reuse its pid. Keying by pid as well as worktree id
matters: a worktree can legitimately have more than one live launcher root at
once (a fast re-attach to an already-live mux session starts a second
launcher process alongside the first, which keeps running until its own
attach loop notices the session and exits) -- keying by worktree id alone
would let the second registration silently evict the first, leaving its
still-running process tree unprotected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import locks as _locks

__all__ = ["active_launch_pids", "add_parsers", "cmd_register_launch", "register_launch"]


def add_parsers(sub) -> None:
    """Register the ``register-launch`` argparse subcommand on ``sub``.

    Named ``add_parsers`` (not e.g. ``add_subparser``) to match the fixed
    convention ``lazy_cli_dispatch.dispatch_lazy`` expects of every
    ``_LAZY_DISPATCH_TABLE``-registered module, so this launcher-only verb's
    own module owns both its parser and its handler and can be fast-path
    dispatched without importing the full ~110-command CLI surface (#4481
    review -- this call fires synchronously on every interactive launch, so
    its import cost matters).
    """
    sp = sub.add_parser(
        "register-launch",
        help="Record this launcher's root pid so a version-cutover reap "
        "protects its subprocess calls (internal)",
    )
    sp.add_argument("--worktree-id", required=True, help="Worktree ID this launch owns")
    sp.add_argument(
        "--pid", type=int, default=None, help="Root pid to protect (default: this process)"
    )
    sp.add_argument(
        "--launch-id", dest="launch_id", default=None, help="Launch-flow correlation id"
    )


def _safe_component(value: str) -> str:
    """Sanitize ``value`` for use as a filename component."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in value)


def _locks_dir(install_dir: str | Path) -> Path:
    return Path(install_dir) / "launch-locks"


def _lock_path(install_dir: str | Path, worktree_id: str, pid: int) -> Path:
    return _locks_dir(install_dir) / f"launch.{_safe_component(worktree_id)}.{pid}.lock"


def register_launch(
    install_dir: str | Path,
    worktree_id: str,
    *,
    pid: int | None = None,
    launch_id: str | None = None,
) -> bool:
    """Record ``pid`` (default: this process) as a live launcher root for
    ``worktree_id``. Returns success; best-effort, never raises.

    Keyed by ``(worktree_id, pid)`` -- a second launch for the same worktree
    (e.g. a fast re-attach racing an already-running one) gets its own entry
    rather than evicting the first. Re-registering the SAME pid for the same
    worktree simply refreshes its lock (harmless, matches this pid's own
    liveness either way).
    """
    if not worktree_id:
        return False
    if pid is None:
        pid = os.getpid()
    if not pid:
        return False
    extra = {"worktree_id": worktree_id}
    if launch_id:
        extra["launch_id"] = launch_id
    return _locks.write_lock(_lock_path(install_dir, worktree_id, pid), pid=pid, extra=extra)


def active_launch_pids(install_dir: str | Path) -> set[int]:
    """Return the set of currently-live registered launcher root pids.

    Opportunistically prunes any lock file whose recorded owner is no longer
    live (dead pid, or a pid-reuse start-time mismatch) so the directory
    self-heals without needing a separate GC pass. Best-effort: an
    unreadable directory yields an empty set rather than raising.
    """
    pids: set[int] = set()
    root = _locks_dir(install_dir)
    try:
        entries = list(root.glob("launch.*.lock"))
    except OSError:
        return pids
    for entry in entries:
        data = _locks.read_lock(entry)
        if _locks.lock_is_live(data):
            pid = data.get("pid") if isinstance(data, dict) else None
            if isinstance(pid, int):
                pids.add(pid)
        else:
            _locks.remove_lock(entry)
    return pids


def cmd_register_launch(args) -> int:
    """``agent-worktrees register-launch`` -- record this launch's root pid.

    Called once by ``launch-session.ps1``/``.sh`` as soon as it knows its
    worktree id, so a version-cutover reap later in this same run (or from a
    concurrent launch's own self-update) can recognize this launcher's
    subprocess calls as protected rather than orphaned.

    Returns 1 on a failed write (missing worktree id, or the lock file
    itself couldn't be written) so the caller's own best-effort wrapper can
    log a diagnostic -- the launcher itself must still treat this as
    non-fatal and continue unprotected (#4481 review: silently exiting 0 on
    every path made that failure mode invisible).
    """
    from . import config as _cfg

    worktree_id = getattr(args, "worktree_id", None)
    if not worktree_id:
        print("Usage: register-launch --worktree-id ID [--pid PID]", file=sys.stderr)
        return 1
    pid = getattr(args, "pid", None)
    launch_id = getattr(args, "launch_id", None)
    ok = register_launch(_cfg.install_dir(), worktree_id, pid=pid, launch_id=launch_id)
    if not ok:
        print(f"register-launch: could not record launch for {worktree_id!r}", file=sys.stderr)
        return 1
    return 0
