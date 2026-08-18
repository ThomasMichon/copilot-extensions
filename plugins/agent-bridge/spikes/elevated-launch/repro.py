"""Hermetic repro/validation for the elevated session-host launch fix.

Exercises the **real** local Session Host launch path
(``LocalSpawner -> launch_session_host``) through ``SessionManager`` for BOTH
repo-class launch shapes, using a fake ACP copilot child (``fake_copilot.py``),
so there is no elevation, real copilot, network, or credential dependency.

It proves two things:

1. **Both classes start clean under a sane budget.** A *singleton* launch shape
   (``cmd /c launch.cmd`` / ``sh launch.sh`` wrapper execing the child in an
   "anchor" dir) and a *worktree* launch shape (direct child argv in a fresh
   worktree dir) both reach ``session/new`` with NO ``internal_error`` -- the
   post-fix expectation.

2. **The readiness budget is what gated it.** Re-running the singleton shape with
   ``timeouts.session_host_ready = 0.001`` reproduces the pre-fix failure
   signature (a ``LAUNCH_ACP`` readiness ``TimeoutError`` that surfaced to the
   caller as ``RequestError.internal_error`` / a "500"); restoring the budget
   makes it pass again.

Run from the plugin's dev venv:

    .venv\\Scripts\\python.exe spikes\\elevated-launch\\repro.py

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE_COPILOT = HERE / "fake_copilot.py"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _make_repo(root: Path, name: str, *, base_repo: bool) -> Path:
    """Create a throwaway git repo modelling one class, with a launch shim."""
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "repro@example.com")
    _git(repo, "config", "user.name", "repro")
    (repo / "AGENTS.md").write_text(
        f"# {name}\n\nThrowaway elevated-launch repro repo "
        f"({'singleton/base_repo' if base_repo else 'worktree'} class).\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _singleton_child_argv(anchor: Path) -> list[str]:
    """The heavy-singleton launch shape: a launch cmd that execs the child.

    Mirrors ``cmd.exe /c launch-<repo>.cmd ... --acp --stdio`` (base_repo). The
    wrapper is what makes a real singleton launch heavier than a bare argv.
    """
    if sys.platform == "win32":
        launch = anchor / "launch.cmd"
        launch.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{FAKE_COPILOT}" %*\r\n',
            encoding="ascii",
        )
        return ["cmd.exe", "/c", str(launch), "--acp", "--stdio"]
    launch = anchor / "launch.sh"
    launch.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_COPILOT}" "$@"\n')
    launch.chmod(0o755)
    return ["/bin/sh", str(launch), "--acp", "--stdio"]


def _worktree_child_argv() -> list[str]:
    """The worktree-class launch shape: a direct child argv."""
    return [sys.executable, str(FAKE_COPILOT), "--acp", "--stdio"]


async def _drive_once(
    label: str, child_argv: list[str], cwd: Path, *, host_ready: float,
) -> tuple[bool, str]:
    """Start one session through the real SessionManager and report outcome."""
    from agent_bridge.db import Database
    from agent_bridge.models import PhasedTimeouts
    from agent_bridge.session_manager import SessionManager
    from agent_bridge.transport import SpawnTarget

    async def _fake_resolve(target, *, tracker=None, session_id=""):
        return child_argv, str(cwd), dict(os.environ)

    import agent_bridge.transport as _transport
    orig = _transport.resolve_local_launch
    _transport.resolve_local_launch = _fake_resolve

    tmp_db = Path(tempfile.mkdtemp(prefix="ab-repro-")) / "s.db"
    mgr = SessionManager(
        Database(tmp_db),
        timeouts=PhasedTimeouts(session_host_ready=host_ready),
        session_host_state_dir=str(tmp_db.parent / "hosts"),
    )
    host_pid = child_pid = None
    try:
        target = SpawnTarget(type="local", cwd=str(cwd))
        session = await asyncio.wait_for(mgr.start_session(target), timeout=60)
        if session.acp_session_id != "repro-sess":
            # A FAILED session (no acp_session_id) is exactly what new_session
            # turns into RequestError.internal_error (the "500") in production.
            return False, (
                f"{label}: session {getattr(session, 'status', '?')} "
                f"(acp_session_id={session.acp_session_id})"
            )
        if mgr._host_index and len(mgr._host_index):
            rec = mgr._host_index.all()[0]
            host_pid, child_pid = rec.host_pid, rec.child_pid
        with contextlib.suppress(Exception):
            await session.client.shutdown()
        return True, f"{label}: OK (acp_session_id={session.acp_session_id})"
    except Exception as exc:  # noqa: BLE001 -- the repro classifies any failure
        return False, f"{label}: {type(exc).__name__}: {exc}"
    finally:
        _transport.resolve_local_launch = orig
        for pid in (host_pid, child_pid):
            if pid:
                with contextlib.suppress(Exception):
                    if sys.platform == "win32":
                        subprocess.run(
                            ["taskkill", "/PID", str(pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        )
                    else:
                        os.kill(pid, 9)


async def _main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ab-elev-repro-"))
    results: list[tuple[bool, str]] = []
    try:
        singleton = _make_repo(root, "elev-singleton-test", base_repo=True)
        worktree = _make_repo(root, "elev-worktree-test", base_repo=False)

        # 1) Both classes start clean under the shipped (sane) budget.
        results.append(await _drive_once(
            "singleton  (base_repo, budget=90s)",
            _singleton_child_argv(singleton), singleton, host_ready=90.0,
        ))
        results.append(await _drive_once(
            "worktree   (worktree,  budget=90s)",
            _worktree_child_argv(), worktree, host_ready=90.0,
        ))

        # 2) The readiness budget is what gated it: a near-zero budget reproduces
        #    the pre-fix LAUNCH_ACP timeout -> internal_error signature.
        ok, msg = await _drive_once(
            "singleton  (base_repo, budget=0.001s)",
            _singleton_child_argv(singleton), singleton, host_ready=0.001,
        )
        # Here FAILURE is the expected outcome (proves the budget is the gate).
        reproduced = (not ok) and (
            "TimeoutError" in msg or "internal" in msg.lower()
            or "did not become ready" in msg or "FAILED" in msg
            or "SessionStatus.FAILED" in msg or "acp_session_id=None" in msg
        )
        results.append((
            reproduced,
            "regression gate: pre-fix timeout reproduced with tiny budget"
            if reproduced else f"regression gate: UNEXPECTED -> {msg}",
        ))
    finally:
        with contextlib.suppress(Exception):
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    print("\n=== elevated-launch repro results ===")
    for ok, msg in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    passed = all(ok for ok, _ in results)
    print(f"\n{'ALL PASS' if passed else 'FAILURES PRESENT'}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
