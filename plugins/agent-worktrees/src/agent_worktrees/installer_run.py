"""Run a runtime installer under a deadline that owns its whole process tree.

``subprocess.run(timeout=...)`` kills only the immediate child: the installer's
own ``pwsh``/``bash`` descendants (``uv``, a staged installer copy, a build)
keep running and can publish a slot after the updater has already labelled
the run from its timer. :func:`run_installer` instead

* starts the installer inside a kill-on-close Job on Windows (daemons it
  starts break away through ``agent_procutil.spawn_daemon``) or in its own
  session on POSIX, and on deadline expiry kills that whole tree (on POSIX,
  every process still in the session, whatever its process group);
* judges the outcome by the runtime's receipt -- the ``deploy-manifest.json``
  version the installer publishes -- rather than by the exit code alone, so
  an installer that exits 0 without activating the target is a failure, and
  a deadline that expires after the slot was published says so.

Statuses keep the shapes :func:`update_events.classify_status` already maps:
``OK``, ``installer exited <rc>`` (75/76/124 keep their outcomes),
``timed out (...)`` and a plain failure line for a missing receipt.
"""

from __future__ import annotations

import dataclasses
import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agent_procutil import contained_test_mode, no_window_kwargs, spawn_sync_in_kill_on_close_job

from . import push_timeout

#: How long to wait for a killed tree's root to be reaped before reporting.
_REAP_GRACE_S = 30.0
#: Exit code of the installers' own self-stage watchdog kill.
_WATCHDOG_EXIT = 124


@dataclasses.dataclass(frozen=True)
class InstallerRun:
    status: str
    rc: int | None
    receipt_version: str | None
    receipt_complete: bool
    duration_s: float


class InstallerCleanupError(RuntimeError):
    """The owned process tree could not be confirmed empty."""


class InstallerLaunchError(RuntimeError):
    """The strict launcher refused the child before execution."""


def _spawn(
    argv: Sequence[str], cwd: Path | None, env: Mapping[str, str], capture: bool,
    output_fd: int | None = None, *, require_tree_exit: bool = False,
):
    # Captured output is merged into one text stream; otherwise stdio stays
    # inherited (the installer reports to the updater's console).
    stdio = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True,
             "encoding": "utf-8", "errors": "replace"} if capture else {}
    if output_fd is not None:
        stdio = {"stdout": output_fd, "stderr": output_fd}
    if os.name == "nt":
        # Without a Job the deadline could not own the tree, so refuse to run.
        strict = (
            {"allow_breakaway": False, **no_window_kwargs()}
            if require_tree_exit else {}
        )
        return spawn_sync_in_kill_on_close_job(
            list(argv), cwd=cwd, env=dict(env), require_job=True, **strict, **stdio)
    # Contained test mode keeps the child in the harness's own group.
    session = {} if contained_test_mode() else {"start_new_session": True}
    return subprocess.Popen(list(argv), cwd=cwd, env=dict(env), **session, **stdio), None


def _all_pids() -> list[int]:
    if os.path.isdir("/proc"):
        return [int(name) for name in os.listdir("/proc") if name.isdigit()]
    listing = subprocess.run(["ps", "-A", "-o", "pid="], capture_output=True,
                             text=True, timeout=10, check=False).stdout
    return [int(tok) for tok in listing.split() if tok.isdigit()]


def _start_time(pid: int) -> str | None:
    """When ``pid`` started: tells a reused PID apart from the process it was."""
    if os.path.isdir("/proc"):
        try:
            with open(f"/proc/{pid}/stat", "rb") as stat:
                # The command name (field 2) may contain spaces and parentheses.
                tail = stat.read().rsplit(b")", 1)[1].split()
        except (OSError, IndexError):
            return None
        return None if not tail or tail[0] == b"Z" else tail[19].decode()
    try:
        started = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return started or None


def _session_census(sid: int) -> dict[int, str]:
    """``{pid: start time}`` of every live process in session ``sid``."""
    census = {}
    for pid in _all_pids():
        try:
            if pid == os.getpid() or os.getsid(pid) != sid:
                continue
        except OSError:
            continue
        started = _start_time(pid)
        if started is not None:
            census[pid] = started
    return census


@dataclasses.dataclass(frozen=True)
class _Member:
    pid: int
    started: str
    pidfd: int | None


def _bind(pid: int, started: str) -> _Member | None:
    """Pin a census entry to its process (a pidfd on Linux), or None if it is gone."""
    pidfd = None
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        try:
            pidfd = os.pidfd_open(pid)
        except ProcessLookupError:
            return None
        except OSError:
            pidfd = None
    # The census saw this start time; anything else at this PID is another process.
    if _start_time(pid) != started:
        if pidfd is not None:
            os.close(pidfd)
        return None
    return _Member(pid, started, pidfd)


def _send(member: _Member, sig: int) -> None:
    try:
        if member.pidfd is not None:
            signal.pidfd_send_signal(member.pidfd, sig)
        elif _start_time(member.pid) == member.started:
            os.kill(member.pid, sig)
    except OSError:
        pass


def _still_member(member: _Member, sid: int) -> bool:
    try:
        return os.getsid(member.pid) == sid and _start_time(member.pid) == member.started
    except OSError:
        return False


def _kill_session(proc: subprocess.Popen) -> None:
    """Kill every process in the installer's session.

    The installers' self-stage watchdog runs the staged copy under ``set -m``,
    which gives it its own process group, so a group kill misses it; it stays
    in the installer's session. Daemons started with ``setsid`` have left the
    session and survive. Each member is pinned to the process the census saw
    (a reused PID is never signalled) and stopped; a stopped process cannot
    leave the session, so it is killed only if it is still a member once
    stopped, and resumed otherwise.
    """
    sid = proc.pid
    for _ in range(3):  # members forked mid-sweep are caught by the next pass
        members = [m for m in (_bind(pid, started)
                               for pid, started in _session_census(sid).items()) if m]
        if not members:
            break
        try:
            for member in members:
                _send(member, signal.SIGSTOP)
            for member in members:
                _send(member, signal.SIGKILL if _still_member(member, sid) else signal.SIGCONT)
        finally:
            for member in members:
                if member.pidfd is not None:
                    os.close(member.pidfd)
    try:
        proc.kill()
    except OSError:
        pass


def _sweep_exited_session(proc: subprocess.Popen) -> None:
    """After the installers' own watchdog kill (exit 124), clear what is left.

    The watchdog signals only the staged installer's group, so a descendant
    that ignored the signal or changed groups can still publish. The installer
    is reaped, but its id stays reserved while any session member lives; a
    process holding that id now means the session is gone and the id reused.
    """
    if _start_time(proc.pid) is None:
        _kill_session(proc)


def _kill_tree(proc: subprocess.Popen, job) -> str:
    """Kill the whole tree; return whatever captured output remains."""
    if job is not None:
        job.close()
    elif os.name != "nt" and not contained_test_mode():
        _kill_session(proc)
    else:
        push_timeout._kill_tree(proc)
    try:
        out, _ = proc.communicate(timeout=_REAP_GRACE_S)
    except subprocess.TimeoutExpired:
        out = None
    return out or ""


def _judge(
    rc: int | None, expired: bool, target: str | None, deployed: str | None,
) -> tuple[str, bool]:
    from .reconcile import _versions_equal

    complete = bool(target and deployed and _versions_equal(deployed, target))
    # 124 is the installer's own watchdog kill: the same deadline, one level down.
    if expired or rc == _WATCHDOG_EXIT:
        where = "slot published; deadline hit during cutover" if complete else (
            "deadline hit during build")
        return f"timed out ({where})", complete
    if rc != 0:
        return f"installer exited {rc}", complete
    if target is None or complete:
        return "OK", complete
    return (
        f"runtime not at target after a successful installer exit "
        f"(deployed {deployed or 'nothing'}, expected {target})"
    ), complete


def run_installer(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str],
    deadline_s: float,
    target_version: str | None = None,
    receipt: Callable[[], str | None] | None = None,
    log: Callable[[str], None] | None = None,
    output_fd: int | None = None,
    require_tree_exit: bool = False,
) -> InstallerRun:
    """Run ``argv`` until it exits or ``deadline_s`` passes; never raises for a
    spawn failure (it becomes the status).

    ``receipt`` returns the runtime's published version (``None`` when there is
    none); with ``target_version`` it decides success. Without a target the
    exit code alone decides, as before. With ``log``, stdout and stderr are
    captured and passed to it line by line once the run ends (also after a
    deadline kill); otherwise they stay inherited. ``output_fd`` streams both
    channels to an owning human-output adapter without bypassing containment
    or receipt judgment. It cannot be combined with ``log``.

    ``require_tree_exit`` is Windows-only: prohibit breakaway and confirm an
    empty Job before returning any launched command's outcome. Cleanup failure
    raises ``InstallerCleanupError``, never a retry-licensing timeout outcome.
    Spawn OS errors propagate in this mode so the caller retains native codes.
    A containment refusal raises ``InstallerLaunchError`` before execution.
    """
    started = time.monotonic()
    if log is not None and output_fd is not None:
        raise ValueError("installer output_fd and log are mutually exclusive")
    if require_tree_exit and os.name != "nt":
        raise ValueError("confirmed process-tree exit requires a Windows Job")
    try:
        strict = {"require_tree_exit": True} if require_tree_exit else {}
        if output_fd is None:
            proc, job = _spawn(argv, cwd, env, log is not None, **strict)
        else:
            proc, job = _spawn(argv, cwd, env, False, output_fd=output_fd, **strict)
    except (OSError, RuntimeError) as exc:
        if require_tree_exit:
            if isinstance(exc, OSError):
                raise
            raise InstallerLaunchError(str(exc)) from exc
        return InstallerRun(str(exc)[:120], None, None, False, time.monotonic() - started)
    expired, out = False, ""
    try:
        try:
            out = proc.communicate(timeout=deadline_s)[0] or ""
            rc: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            expired, rc = True, None
            if not require_tree_exit:
                out = _kill_tree(proc, job)
                job = None
        if require_tree_exit:
            try:
                if job is None:
                    raise RuntimeError("owned Windows Job is unavailable")
                cleanup_deadline = time.monotonic() + _REAP_GRACE_S
                job.terminate_and_wait(_REAP_GRACE_S)
                job = None
                out = proc.communicate(timeout=max(0, cleanup_deadline - time.monotonic()))[0] or ""
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise InstallerCleanupError(f"process-tree cleanup unverified: {exc}") from exc
    except BaseException as exc:
        if require_tree_exit:
            if job is not None:
                job.close()
        else:
            _kill_tree(proc, job)
        job = None
        if require_tree_exit and isinstance(exc, OSError):
            raise InstallerCleanupError(f"process-tree cleanup unverified after launch: {exc}") from exc
        raise
    finally:
        if job is not None:
            # Everything the installer left behind dies with it; daemons it
            # started have broken away.
            job.close()
    if rc == _WATCHDOG_EXIT and os.name != "nt" and not contained_test_mode():
        _sweep_exited_session(proc)
    if log is not None:
        for line in out.splitlines():
            log(line)
    deployed = receipt() if receipt is not None else None
    status, complete = _judge(rc, expired, target_version, deployed)
    return InstallerRun(status, rc, deployed, complete, time.monotonic() - started)
