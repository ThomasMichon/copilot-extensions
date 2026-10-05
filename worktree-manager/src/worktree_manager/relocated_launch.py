"""The relocated ``launch-session.{ps1,sh}`` delegation leg of ``_run_launch``
-- split out of ``__main__.py`` purely to keep that module under this repo's
module-size cap (no functional reason to split otherwise). This package's own
canonical muxed-launch implementation, plus the ``new_window`` modifier.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _core():
    """Lazy reference to ``__main__`` so a test's
    ``monkeypatch.setattr(entrypoint, "_is_windows", ...)`` (``entrypoint``
    being the ``__main__`` module) is observed here too -- a plain
    ``from .__main__ import _is_windows`` would instead freeze a copy of the
    pre-patch function at import time."""
    from . import __main__ as core

    return core


def _relocated_launch_script():
    """Locate the Manager-owned launch-session script, this package's own
    canonical muxed-launch implementation (Phase 3b Sub-slice 2a).

    ``worktree-manager/bin/launch-session.{ps1,sh}`` is copied verbatim into
    this package's own ``bin/`` sibling directory -- present both in an
    installed versioned slot (``<root>/versions/<ver>/{src,bin}``) and in a
    source checkout (``worktree-manager/{src,bin}``), since both layouts put
    ``bin/`` two levels above this file. Returns ``None`` when the sibling
    script is absent, so callers degrade to the graceful non-muxed launcher.py
    path (DQ9) exactly as before this script existed.
    """
    root = Path(__file__).resolve().parents[2]
    name = "launch-session.ps1" if _core()._is_windows() else "launch-session.sh"
    script = root / "bin" / name
    return script if script.exists() else None


def _run_relocated_mux_launch(req, plan, script) -> int:
    """Delegate an ordinary local launch to the relocated launch-session
    script -- the ONE canonical muxed-launch implementation. The script
    performs its own resolve/launch/attach/post-exit; this passes the
    already-resolved ``plan.worktree_id`` (never re-issuing ``--new``/
    ``--base``), so a ``mode == "new"`` request cannot create a second
    worktree by re-triggering creation inside the script's own resolve call.

    ``req.new_window`` opens the SAME script invocation in a brand-new,
    visible terminal window instead of running it in this process, so the
    mux-daemon registration the script performs always runs the identical
    way regardless of which window modifier was chosen.
    """
    args = ["--project", req.project]
    if req.mode == "base":
        args.append("--base")
    else:
        worktree_id = str(getattr(plan, "worktree_id", None) or req.worktree_id or "")
        if not worktree_id:
            print("error: could not resolve a worktree id for this launch.")
            return 1
        args += ["--worktree-id", worktree_id]
        if req.mode == "bare-resume":
            args.append("--bare-resume")

    is_windows = _core()._is_windows()
    no_mux = bool(getattr(req, "no_mux", False))

    if getattr(req, "new_window", False):
        from . import new_window_spawn

        argv = (
            ["pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(script), *args]
            if is_windows
            else ["bash", str(script), *args]
        )
        # A new window is a genuinely separate process (never replacing or
        # blocking this one), so a one-off env override is passed EXPLICITLY
        # to the spawned child rather than mutated onto `os.environ` -- that
        # global mutation would never be undone and would leak into every
        # later or concurrent spawn in this (long-lived Picker) process.
        env = None
        if no_mux:
            env = {**os.environ, "WORKTREE_NO_MUX": "1"}
        title = str(getattr(plan, "worktree_id", None) or req.worktree_id or req.project)
        try:
            new_window_spawn.spawn_detached_new_window(argv, title=title, env=env)
        except new_window_spawn.NewWindowSpawnError as error:
            print(f"error: could not open a new window: {error}")
            return 1
        return 0

    # Every other path below either exec-replaces this process (POSIX) or
    # blocks on it (Windows `Popen().wait()`) -- there is no "later spawn"
    # to leak into, so the pre-existing `os.environ` mutation is harmless
    # here (unlike the new_window branch above).
    if no_mux:
        os.environ["WORKTREE_NO_MUX"] = "1"
    if is_windows:
        argv = ["pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(script), *args]
        proc = subprocess.Popen(argv)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            try:
                return proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                return 130  # 128 + SIGINT(2)
    os.execvp("bash", ["bash", str(script), *args])
    return 1  # unreachable -- os.execvp replaces the process
