"""Hermetic repro/validation for the #1612 resume-spawn crash-isolation fix.

Exercises the **real** local resume path
(``SessionManager.resume_session`` -> ``transport.spawn`` -> ``spawn_raw`` ->
``asyncio.create_subprocess_exec``) for a ``command``-type session whose
persisted target executable has **gone missing** since it was first started --
the exact signature of dotfiles#1612, where a stopped local ``command`` session's
persisted ``session.target`` pointed under a venv / worktree / version-slot path
that no longer resolved, so the resume ``CreateProcess`` raised
``FileNotFoundError: [WinError 2]``.

The report's central claim is that this spawn failure escaped the async
``resume_session`` handler and took the **whole uvicorn listener down**
(connection-refused, not a per-session 500), after which even a **fresh**
``start_session`` 500'd -- *"once the spawn machinery is poisoned, no session can
be created"*. This repro pins the post-fix invariant: a resume whose target
cannot be spawned must fail as a **contained, per-session error** and leave the
``SessionManager`` fully able to serve everything else.

It proves three things, all with a fake ACP copilot child (no real copilot,
network, credentials, elevation, or a live daemon):

1. **A missing-target resume fails CONTAINED, not catastrophically.** A session
   started clean (fake copilot) then STOPPED, whose target is then repointed at a
   **nonexistent executable**, raises an ``OSError`` / ``FileNotFoundError`` out
   of ``resume_session`` that the caller can catch -- and the session lands back
   in a terminal ``stopped`` state (not wedged in ``starting``).

2. **The spawn machinery is NOT poisoned** (the #1612 escalation: after the
   wedge, even a *fresh* ``start_session`` 500'd). Immediately after the failed
   resume, a **brand-new** ``start_session`` with a valid target still reaches
   ``idle`` on the SAME manager -- proving the failed ``CreateProcess`` did not
   take down the event loop / spawn path.

3. **Resume itself is healthy** (the failure was target-specific, not a wedged
   resume path): repointing the stopped session back at a valid command and
   resuming reaches ``idle``.

It also records the still-open **route-mapping** gap #1612 flagged: the raised
type is an ``OSError`` subclass, which the HTTP ``resume_session`` route
(``routes/sessions.py``) does not map -- it catches only
``KeyError``/``ValueError``/``RuntimeError`` -- so containment currently relies on
the ``SessionManager``'s ``except Exception`` resume ladder rather than the route.
(Informational; not a gating fail.)

Run from the plugin's dev venv:

    .venv\\Scripts\\python.exe spikes\\resume-spawn-crash-isolation\\repro.py

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE_COPILOT = HERE / "fake_copilot.py"


def _valid_command() -> list[str]:
    """A spawn command whose executable exists (the fake ACP copilot child)."""
    return [sys.executable, str(FAKE_COPILOT), "--acp", "--stdio"]


def _missing_command(root: Path) -> list[str]:
    """A spawn command whose executable does NOT exist.

    Models a persisted ``session.target`` pointing under a venv / worktree /
    version-slot that has since moved or been cleaned -- the resume
    ``CreateProcess`` then raises ``FileNotFoundError: [WinError 2]`` (#1612).
    """
    missing = root / "gone-slot" / (
        "python.exe" if sys.platform == "win32" else "python"
    )
    return [str(missing), str(FAKE_COPILOT), "--acp", "--stdio"]


def _make_manager(tmp: Path):
    from agent_bridge.db import Database
    from agent_bridge.session_manager import SessionManager

    return SessionManager(
        Database(tmp / "sessions.db"),
        session_host_state_dir=str(tmp / "hosts"),
    )


async def _start_idle(mgr, cwd: Path):
    """Start a process-owned ``command`` session and drive it to IDLE."""
    from agent_bridge.transport import SpawnTarget

    target = SpawnTarget(
        type="command", cwd=str(cwd), spawn_command=_valid_command(),
    )
    return await asyncio.wait_for(mgr.start_session(target), timeout=60)


def _kill(pid) -> None:
    if not pid:
        return
    with contextlib.suppress(Exception):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            import os
            os.kill(int(pid), 9)


async def _main() -> int:
    from agent_bridge.transport import SpawnTarget

    root = Path(tempfile.mkdtemp(prefix="ab-resume-repro-"))
    cwd = root / "wt"
    cwd.mkdir(parents=True, exist_ok=True)
    mgr = _make_manager(root)
    results: list[tuple[bool, str]] = []
    sessions_seen: list = []
    raised: BaseException | None = None
    try:
        # --- setup: a clean IDLE command session, then STOP it ---------------
        s = await _start_idle(mgr, cwd)
        sessions_seen.append(s)
        setup_ok = s.acp_session_id == "repro-sess" and s.status.value == "idle"
        await mgr.stop_session(s.session_id)
        setup_ok = setup_ok and s.status.value == "stopped"
        results.append((
            setup_ok,
            f"setup: command session reached IDLE (acp={s.acp_session_id}) "
            f"then STOPPED ({s.status.value})",
        ))

        # --- 1) missing-target resume fails CONTAINED (not catastrophically) -
        s.target = SpawnTarget(
            type="command", cwd=str(cwd), spawn_command=_missing_command(root),
        )
        try:
            await asyncio.wait_for(mgr.resume_session(s.session_id), timeout=60)
        except BaseException as exc:  # the repro classifies any failure
            raised = exc
        contained = (
            isinstance(raised, OSError)          # FileNotFoundError [WinError 2]
            and s.status.value == "stopped"       # terminal, not wedged
        )
        results.append((
            contained,
            (f"missing-target resume raised a CONTAINED "
             f"{type(raised).__name__} and the session is terminal "
             f"({s.status.value})")
            if raised is not None else
            "missing-target resume did NOT raise (UNEXPECTED -- a silent wedge)",
        ))

        # --- 2) the spawn machinery is NOT poisoned --------------------------
        # #1612 escalation: after the wedge, even a FRESH start_session 500'd
        # ("no session can be created"). Prove a brand-new session on the SAME
        # manager still starts clean.
        fresh_ok = False
        fresh_msg = ""
        try:
            f = await _start_idle(mgr, cwd)
            sessions_seen.append(f)
            fresh_ok = f.acp_session_id == "repro-sess" and f.status.value == "idle"
        except BaseException as exc:  # the repro classifies any failure
            fresh_msg = f" ({type(exc).__name__}: {exc})"
        results.append((
            fresh_ok,
            "spawn machinery survives: a FRESH start_session reached IDLE after "
            "the failed resume"
            if fresh_ok else
            f"spawn machinery POISONED: fresh start_session failed{fresh_msg}",
        ))

        # --- 3) resume itself is healthy (failure was target-specific) -------
        s.target = SpawnTarget(
            type="command", cwd=str(cwd), spawn_command=_valid_command(),
        )
        resume_ok = False
        resume_msg = ""
        try:
            r = await asyncio.wait_for(mgr.resume_session(s.session_id), timeout=60)
            resume_ok = r.status.value == "idle"
        except BaseException as exc:  # the repro classifies any failure
            resume_msg = f" ({type(exc).__name__}: {exc})"
        results.append((
            resume_ok,
            "resume health: repointing the stopped session at a VALID target "
            "resumes to IDLE"
            if resume_ok else
            f"resume health: valid-target resume FAILED{resume_msg}",
        ))
    finally:
        for sess in sessions_seen:
            with contextlib.suppress(Exception):
                if getattr(sess, "client", None):
                    await sess.client.shutdown()
            _kill(getattr(sess, "pid", None))
        with contextlib.suppress(Exception):
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    # Informational: the still-open ROUTE-mapping gap #1612 flagged. The HTTP
    # resume route (routes/sessions.py) catches only KeyError/ValueError/
    # RuntimeError, so this OSError escapes it -> containment currently relies on
    # the SessionManager's `except Exception` resume ladder, not the route.
    route_note = (
        isinstance(raised, OSError)
        and not isinstance(raised, (KeyError, ValueError, RuntimeError))
    )

    print("\n=== resume-spawn crash-isolation repro results ===")
    for ok, msg in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    if route_note:
        print(
            "  [NOTE] resume raised an OSError subclass, which the HTTP resume "
            "route does not map (it catches only KeyError/ValueError/"
            "RuntimeError) -- containment relies on SessionManager's "
            "except-Exception ladder; consider mapping OSError -> 500 at the "
            "route (dotfiles#1612)."
        )
    passed = all(ok for ok, _ in results)
    print(f"\n{'ALL PASS' if passed else 'FAILURES PRESENT'}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
