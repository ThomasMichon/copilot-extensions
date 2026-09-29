"""Bound + diagnostic for a stalled ``git push`` (split out of :mod:`git_ops`
to stay under its shrink-only module-size ceiling; see
``tools/module-size-baseline.json``).

A repo's pre-push hook re-invokes the agent-worktrees binstub on every
invocation, which can itself stall for the same reasons a direct CLI call
can (a self-update racing the runtime-slot swap, or the mutex-gated
self-provisioning path's own ~30-120s venv build; see
ThomasMichon/copilot-extensions#4547). Left unbounded, that stall
propagates into an indefinite hang with no diagnostic -- exactly what has
driven agents to bypass ``create-pr`` for a raw ``git push`` + ``gh pr
create``, silently losing PR attribution (the manual fallback embeds no
marker).

:func:`run_bounded` additionally kills the WHOLE process tree on a stall,
not just the immediate ``git`` process: plain ``subprocess.run(timeout=...)``
only terminates its direct child, so a pre-push hook it spawned -- and that
hook's own provisioning descendants -- can otherwise keep running (and keep
holding a lock/mutex) after the caller has already given up and returned.
"""

from __future__ import annotations

import os
import platform
import subprocess

from agent_procutil import contained_test_mode, detached_kwargs

#: 180s covers the documented worst-case provisioning ETA (120s) plus buffer
#: for a contended provisioning mutex.
DEFAULT_PUSH_TIMEOUT = 180.0


def _kill_tree(proc: subprocess.Popen) -> None:
    """Terminate *proc* and, best-effort, its descendant tree on a stall.

    A PID-based tree sweep (``taskkill /T`` on Windows, ``killpg`` on
    POSIX) is only ever run while ``proc.poll()`` confirms -- via the OS
    handle Popen already holds, not a bare PID lookup -- that *this exact*
    process is STILL running: a PID stays reserved to it as long as any
    handle (ours included) stays open, so a "still running" verdict here
    means the PID cannot yet have been reused. If *proc* has ALREADY
    exited on its own (a genuine race: the stalled command completes right
    as our timeout fires), its PID is skipped entirely rather than risking
    a now-unverifiable identity match -- the "stale or mismatched identity
    is refused" the sweep can't safely resolve. Either way, ``proc.kill()``
    is always ALSO called afterward as a handle-bound belt-and-suspenders
    step for the root specifically, since that operates on Popen's open OS
    handle and is therefore safe regardless of PID reuse.

    Skips the PID-based sweep entirely under
    ``COPILOT_EXTENSIONS_TEST_CONTAINED=1`` too: in that mode
    ``detached_kwargs()`` deliberately leaves POSIX *proc* in the CALLER's
    own process group (so the test harness can retain descendant
    ownership) -- ``killpg`` there would kill the harness's own worker,
    not just *proc*'s tree.
    """
    if proc.poll() is None and not contained_test_mode():
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False,
            )
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        proc.kill()
    except OSError:
        pass


def run_bounded(
    cmd: list[str], *, cwd: str | os.PathLike | None, env: dict, timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd*, capturing output; on a stall past *timeout*, kill its
    ENTIRE process tree (see module docstring) before re-raising
    :class:`subprocess.TimeoutExpired` with whatever output was captured.

    ``detached_kwargs()`` (not a hand-rolled ``creationflags``/
    ``start_new_session``) puts *cmd* in its own process group/session --
    the headless-launch guard requires routing platform process-creation
    flags through ``agent_procutil`` rather than reimplementing them here.
    """
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
        **detached_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = exc.stdout or "", exc.stderr or ""
        exc.stdout, exc.stderr = stdout, stderr
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def message(exc: subprocess.TimeoutExpired, timeout: float | None) -> str:
    """Actionable message for a timed-out push, forwarding any partial
    hook/git output (both streams) already captured instead of a bare
    timeout.
    """
    def _text(raw: object) -> str:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return (raw or "").strip()

    partial = "\n".join(t for t in (_text(exc.stdout), _text(exc.stderr)) if t)
    lines = [
        f"git push timed out after {timeout:.0f}s without completing.",
        "Possible cause: this repo's pre-push hook re-invokes the "
        "agent-worktrees binstub, which can stall the same way a direct "
        "CLI call can -- a self-update racing the runtime-slot swap, or "
        "the provisioning mutex waiting on its own ~30-120s venv build "
        "(see ThomasMichon/copilot-extensions#4547). This is one possible "
        "cause among others (e.g. a genuine network stall, or a repo whose "
        "hook does not invoke agent-worktrees at all).",
    ]
    if partial:
        lines += ["Partial hook/git output captured before the timeout:", partial]
    lines.append(
        "The branch may or may not have been pushed -- check "
        "'git log <remote>/<branch>', or simply re-run create-pr/push-changes "
        "(idempotent) rather than a manual 'git push' + 'gh pr create', which "
        "would silently skip PR attribution."
    )
    return "\n".join(lines)

