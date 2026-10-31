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
import codecs
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

from agent_procutil import JobHandle, contained_test_mode, no_window_kwargs, spawn_sync_in_kill_on_close_job
from agent_procutil.streaming import SyncProcessOutput

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
    """Post-launch failure; retain native custody when cleanup is unverified."""

    def __init__(
        self, message: str, *, process: subprocess.Popen | None = None,
        job: JobHandle | None = None,
    ) -> None:
        super().__init__(message)
        self.process = process
        self.job = job


class InstallerLaunchError(RuntimeError):
    """The strict launcher refused the child before execution."""


def _spawn(
    argv: Sequence[str], cwd: Path | None, env: Mapping[str, str], capture: bool,
    output_fd: int | None = None, *, require_tree_exit: bool = False,
):
    # Keep channels distinct while observing their raw bytes for progress.
    stdio = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
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


def _kill_tree(proc: subprocess.Popen, job, capture: SyncProcessOutput | None = None) -> str:
    """Kill the whole tree; return whatever captured output remains."""
    if job is not None:
        job.close()
    elif os.name != "nt" and not contained_test_mode():
        if proc.returncode is None or _start_time(proc.pid) is None:
            _kill_session(proc)
    else:
        push_timeout._kill_tree(proc)
    try:
        out, _ = (
            capture.finish(_REAP_GRACE_S, stop_at_idle=os.name != "nt") if capture is not None
            else proc.communicate(timeout=_REAP_GRACE_S)
        )
    except subprocess.TimeoutExpired:
        if capture is not None:
            raise InstallerCleanupError("installer output did not settle after process-tree retirement") from None
        out = None
    return out or ""


def _settle_strict_tree(
    proc: subprocess.Popen, job: JobHandle | None, capture: SyncProcessOutput | None,
) -> None:
    if job is None:
        raise RuntimeError("owned Windows Job is unavailable")
    deadline = time.monotonic() + _REAP_GRACE_S
    job.terminate_and_wait(_REAP_GRACE_S)
    if capture is not None:
        capture.finish(max(0, deadline - time.monotonic()))
    else:
        proc.communicate(timeout=max(0, deadline - time.monotonic()))


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
    """Keep native output live; deliver legacy logs after verified producer settlement."""
    if log is not None and output_fd is not None:
        raise ValueError("installer output_fd and log are mutually exclusive")
    with ExitStack() as resources:
        spools = {
            name: resources.enter_context(tempfile.TemporaryFile())
            for name in ("stdout", "stderr")
        } if log is not None else None
        return _run_installer(
            argv, cwd=cwd, env=env, deadline_s=deadline_s,
            target_version=target_version, receipt=receipt, log=log,
            output_fd=output_fd, require_tree_exit=require_tree_exit,
            output_spools=spools,
        )


def _run_installer(
    argv: Sequence[str], *, cwd: Path | None, env: Mapping[str, str],
    deadline_s: float, target_version: str | None,
    receipt: Callable[[], str | None] | None, log: Callable[[str], None] | None,
    output_fd: int | None, require_tree_exit: bool,
    output_spools: Mapping[str, BinaryIO] | None,
) -> InstallerRun:
    """Run ``argv`` until it exits or emits nothing for ``deadline_s``; never raises for a
    spawn failure (it becomes the status).

    ``receipt`` returns the runtime's published version (``None`` when there is
    none); with ``target_version`` it decides success. Without a target the
    exit code alone decides, as before. With ``log``, stdout and stderr are
    passed to it as complete lines after process-tree settlement (with a final
    partial line); native output remains live through a cancellable relay.
    ``output_fd`` streams both
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
    from update_driver import participant_environment

    env = {**env, **participant_environment()}
    decoders = {name: codecs.getincrementaldecoder("utf-8")(errors="replace")
                for name in ("stdout", "stderr")}
    pending = {"stdout": "", "stderr": ""}
    inherited = {"stdout": sys.stdout, "stderr": sys.stderr}
    descriptors = {}
    if log is None and output_fd is None:
        for name, stream in inherited.items():
            try:
                descriptors[name] = stream.fileno()
            except (AttributeError, io.UnsupportedOperation):
                descriptors[name] = None
    if output_fd is not None:
        descriptors = {name: output_fd for name in ("stdout", "stderr")}
    elif output_spools is not None:
        descriptors = {name: spool.fileno() for name, spool in output_spools.items()}

    def relay(name: str, chunk: bytes) -> None:
        if log is not None or descriptors.get(name) is None:
            text = decoders[name].decode(chunk)
            if log is None:
                inherited[name].write(text)
                inherited[name].flush()
            else:
                pending[name] += text
                while "\n" in pending[name]:
                    line, pending[name] = pending[name].split("\n", 1)
                    log(line.rstrip("\r"))

    def deliver_output() -> None:
        capture.deliver()
        for name, spool in (output_spools or {}).items():
            spool.seek(0)
            while chunk := spool.read(4096):
                relay(name, chunk)

    try:
        strict = {"require_tree_exit": True} if require_tree_exit else {}
        proc, job = _spawn(argv, cwd, env, True, **strict)
    except (OSError, RuntimeError) as exc:
        if require_tree_exit:
            if isinstance(exc, OSError):
                raise
            raise InstallerLaunchError(str(exc)) from exc
        return InstallerRun(str(exc)[:120], None, None, False, time.monotonic() - started)
    capture = None
    expired = False
    try:
        capture = SyncProcessOutput(
            proc, on_output=relay if output_spools is None and any(
                fd is None for fd in descriptors.values()
            ) else None,
            output_fds={name: fd for name, fd in descriptors.items() if fd is not None},
            capture=False,
        )
        try:
            capture.wait(deadline_s, wait_for_eof=False)
            rc: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            expired, rc = True, None
            if not require_tree_exit:
                _kill_tree(proc, job, capture)
                job = None
        if not require_tree_exit and not expired:
            _kill_tree(proc, job, capture)
            job = None
    except BaseException as exc:
        if require_tree_exit:
            try:
                _settle_strict_tree(proc, job, capture)
            except BaseException as cleanup_error:
                failure = InstallerCleanupError(
                    f"process-tree cleanup unverified: {cleanup_error}", process=proc, job=job,
                )
                failure.add_note(f"cleanup failed after {type(exc).__name__}: {exc}")
                job = None
                raise failure from exc
        else:
            _kill_tree(proc, job, capture)
        job = None
        if capture is not None:
            try:
                deliver_output()
            except BaseException as delivery_error:
                exc.add_note(f"deferred output delivery failed: {delivery_error}")
        if require_tree_exit and isinstance(exc, OSError):
            raise InstallerCleanupError(f"installer output failed after verified cleanup: {exc}") from exc
        raise
    else:
        if require_tree_exit:
            try:
                _settle_strict_tree(proc, job, capture)
            except BaseException as exc:
                failure = InstallerCleanupError(
                    f"process-tree cleanup unverified: {exc}", process=proc, job=job,
                )
                job = None
                raise failure from exc
            job = None
    finally:
        if job is not None:
            # Everything the installer left behind dies with it; daemons it
            # started have broken away.
            job.close()
    deliver_output()
    if rc == _WATCHDOG_EXIT and os.name != "nt" and not contained_test_mode():
        _sweep_exited_session(proc)
    if output_fd is None:
        for name, decoder in decoders.items():
            tail = decoder.decode(b"", final=True)
            if log is not None:
                if pending[name] or tail:
                    log((pending[name] + tail).rstrip("\r"))
            elif tail:
                inherited[name].write(tail)
                inherited[name].flush()
    deployed = receipt() if receipt is not None else None
    status, complete = _judge(rc, expired, target_version, deployed)
    return InstallerRun(status, rc, deployed, complete, time.monotonic() - started)
