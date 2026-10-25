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
import math
import platform
import subprocess
import threading
import time
from typing import Literal, overload

from agent_procutil import (
    contained_test_mode,
    detached_kwargs,
    no_window_kwargs,
    spawn_sync_in_kill_on_close_job,
)

#: 180s covers the documented worst-case provisioning ETA (120s) plus buffer
#: for a contended provisioning mutex.
DEFAULT_PUSH_TIMEOUT = 180.0
OBSERVATION_CLEANUP_SECONDS = 5.0


class ObservationUnavailable(subprocess.SubprocessError):
    """The observation cannot acquire its own descendant containment."""


class _ObservationReader:
    def __init__(self, stream):
        self.stream, self.chunks, self.error = stream, [], None
        self.done = threading.Event()
        self.attempted = False
        self.thread = threading.Thread(target=self._read, name="git-observation-pipe", daemon=True)

    def _read(self):
        try:
            while chunk := self.stream.read(65536):
                self.chunks.append(chunk)
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()


class _ObservationProcess:
    """Local reader/root custody for the private atomic Windows adapter."""

    def __init__(self, args, *, command_deadline, cleanup_deadline):
        self.args, self.deadline = args, command_deadline
        self.cleanup_deadline = cleanup_deadline
        self.native = self.job = None
        self.readers = []
        self.cleaning = False

    def attach(self, native, job):
        self.native, self.job = native, job
        self.stdin, self.stdout, self.stderr = native.stdin, native.stdout, native.stderr
        for stream in (self.stdout, self.stderr):
            self.readers.append(_ObservationReader(stream))

    @property
    def returncode(self):
        return self.native.returncode

    def begin_cleanup(self):
        self.cleaning = True
        self.deadline = self.cleanup_deadline

    def communicate(self, *, timeout):
        def remaining():
            return 0 if timeout == 0 else min(timeout, max(0, self.deadline - time.monotonic()))

        for reader in self.readers:
            if not reader.attempted:
                if self.cleaning:
                    reader.stream.close()
                    reader.done.set()
                else:
                    reader.attempted = True
                    reader.thread.start()
        for reader in self.readers:
            if not reader.done.wait(remaining()):
                raise subprocess.TimeoutExpired(self.args, timeout)
            if reader.attempted:
                reader.thread.join(remaining())
                if reader.thread.is_alive():
                    raise subprocess.TimeoutExpired(self.args, timeout)
            if reader.error is not None and not self.cleaning:
                raise reader.error
        return tuple(
            b"".join(reader.chunks).decode("utf-8", errors="replace")
            .replace("\r\n", "\n").replace("\r", "\n") for reader in self.readers
        )

    def wait(self, *, timeout):
        return self.native.wait(timeout)

    def close(self):
        if not self.cleaning or self.returncode is None or any(
            not reader.done.is_set() or (reader.attempted and reader.thread.is_alive())
            for reader in self.readers
        ):
            raise RuntimeError("native observation root/readers remain unconfirmed")
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                stream.close()
        self.native.close()


def _spawn_observation(cmd, *, cwd, env, deadline, command_deadline):
    from agent_procutil._windows_caller_owned import spawn
    from agent_procutil.owned_process import OwnedCleanupError

    process = _ObservationProcess(cmd, command_deadline=command_deadline, cleanup_deadline=deadline)
    try:
        native, job = spawn(
            cmd, cwd=cwd, env=env, cleanup_deadline=deadline,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OwnedCleanupError as error:
        attachment_error = None
        try:
            process.attach(error.process, error.job)
        except BaseException as interrupted_attachment:
            attachment_error = interrupted_attachment
        failure = ObservationCleanupError("atomic creation cleanup was not confirmed")
        failure.process, failure.job = process, error.job
        failure.operation_error = getattr(error, "operation_error", error.__cause__)
        cleanup_error = getattr(error, "cleanup_error", None)
        if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit)) and not isinstance(
            failure.operation_error, (KeyboardInterrupt, SystemExit),
        ):
            failure.operation_error = cleanup_error
        if isinstance(attachment_error, (KeyboardInterrupt, SystemExit)) and not isinstance(
            failure.operation_error, (KeyboardInterrupt, SystemExit),
        ):
            failure.operation_error = attachment_error
        failure.attachment_error = attachment_error
        failure._job_empty_confirmed = getattr(error, "_job_empty_confirmed", False)
        failure.observation_deadline, failure.observation_path = deadline, cwd
        raise failure from error
    try:
        process.attach(native, job)
    except BaseException as error:
        failure = ObservationCleanupError("atomic reader preparation was not confirmed")
        failure.process, failure.job = process, job
        failure.operation_error = error
        failure.observation_deadline, failure.observation_path = deadline, cwd
        raise failure from error
    return process, job


class ObservationCleanupError(subprocess.SubprocessError):
    """An owned observation did not confirm complete cleanup."""

    def try_settle(self) -> bool:
        if getattr(self, "_settled", False):
            return True
        if not getattr(self, "_job_empty_confirmed", False):
            if self.job is None:
                raise RuntimeError("no owned Job to confirm")
            if not self.job.try_terminate_and_confirm_empty():
                return False
            self._job_empty_confirmed = True
        try:
            if hasattr(self.process, "begin_cleanup"):
                self.process.begin_cleanup()
            self.process.communicate(timeout=0)
            self.process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return False
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        if hasattr(self.process, "close"):
            self.process.close()
        self._settled = True
        return True


class ObservationCleanupPending(subprocess.SubprocessError):
    """The existing observation owner has not released this scope."""


def _observation_options(deadline: float, *, cap: float | None = None) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= OBSERVATION_CLEANUP_SECONDS:
        error = subprocess.TimeoutExpired("git observation", max(0, remaining))
        error.observation_started = False
        raise error
    return {
        "timeout": min(remaining, cap) if cap is not None else remaining,
        "kill_tree": True, "observation_deadline": deadline,
    }


def _observation_wait(cmd: list[str], deadline: float, reserve: float = 0) -> float:
    remaining = deadline - time.monotonic() - reserve
    if remaining <= 0:
        error = subprocess.TimeoutExpired(cmd, 0)
        error.observation_started = False
        raise error
    return remaining


def _run_observation(cmd: list[str], *, cwd, env: dict, timeout, deadline: float):
    if platform.system() != "Windows":
        raise ObservationUnavailable("bounded observation is Windows-only; POSIX work remains open")
    from agent_procutil.owned_process import require_caller_owned_support

    try:
        require_caller_owned_support()
    except (RuntimeError, OSError) as error:
        raise ObservationUnavailable(str(error)) from error
    wait = _observation_wait(cmd, deadline, OBSERVATION_CLEANUP_SECONDS)
    if timeout is not None:
        wait = min(wait, timeout)
    if wait <= 0:
        error = subprocess.TimeoutExpired(cmd, 0)
        error.observation_started = False
        raise error
    command_deadline = min(deadline - OBSERVATION_CLEANUP_SECONDS, time.monotonic() + wait)
    _observation_wait(cmd, command_deadline)
    try:
        proc, job = _spawn_observation(
            cmd, cwd=cwd, env=env, deadline=deadline, command_deadline=command_deadline,
        )
    except (RuntimeError, OSError) as error:
        raise ObservationUnavailable(str(error)) from error
    if job is None:
        failure = ObservationCleanupError("strict observation Job unavailable after launch")
        failure.process, failure.job = proc, job
        failure.observation_deadline, failure.observation_path = deadline, cwd
        raise failure
    stdout = stderr = ""
    job_empty_confirmed = False
    operation_error = None
    try:
        try:
            stdout, stderr = proc.communicate(
                timeout=_observation_wait(cmd, command_deadline),
            )
        except BaseException as error:
            operation_error = error
            if isinstance(error, subprocess.TimeoutExpired):
                error.observation_started = True
            raise
    finally:
        try:
            job.terminate_and_wait(_observation_wait(cmd, deadline))
            job_empty_confirmed = True
            if hasattr(proc, "begin_cleanup"):
                proc.begin_cleanup()
            proc.communicate(timeout=_observation_wait(cmd, deadline))
            proc.wait(timeout=_observation_wait(cmd, deadline))
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            if hasattr(proc, "close"):
                proc.close()
        except BaseException as error:
            failure = ObservationCleanupError("observation cleanup was not confirmed")
            failure.process, failure.job = proc, job
            failure._job_empty_confirmed = job_empty_confirmed
            failure.operation_error = operation_error
            if isinstance(error, (KeyboardInterrupt, SystemExit)) and not isinstance(
                operation_error, (KeyboardInterrupt, SystemExit),
            ):
                failure.operation_error = error
            failure.cleanup_error = error
            failure.observation_deadline, failure.observation_path = deadline, cwd
            raise failure from error
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


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
    A child inheriting a Manager-owned group also skips ``killpg``: only
    the outer launch owner may terminate that group, never this member.
    """
    if proc.poll() is None and not contained_test_mode():
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False,
                **no_window_kwargs(),
            )
        else:
            import signal
            try:
                group = os.getpgid(proc.pid)
                if group != os.getpgrp():
                    os.killpg(group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        proc.kill()
    except OSError:
        pass


@overload
def run_bounded(
    cmd: list[str], *, cwd: str | os.PathLike | None, env: dict, timeout: float | None,
    inherit_process_group: bool = False, binary: Literal[False] = False, input: None = None,
    observation_deadline: float | None = None,
) -> subprocess.CompletedProcess[str]: ...


@overload
def run_bounded(
    cmd: list[str], *, cwd: str | os.PathLike | None, env: dict, timeout: float | None,
    inherit_process_group: bool = False, binary: Literal[True], input: bytes | None = None,
    observation_deadline: float | None = None,
) -> subprocess.CompletedProcess[bytes]: ...


def run_bounded(
    cmd: list[str], *, cwd: str | os.PathLike | None, env: dict, timeout: float | None,
    inherit_process_group: bool = False, binary: bool = False, input: bytes | None = None,
    observation_deadline: float | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run *cmd*, capturing output; on a stall past *timeout*, kill its
    ENTIRE process tree (see module docstring) before re-raising
    :class:`subprocess.TimeoutExpired` with whatever output was captured.

    Windows captured children start headless and require a kill-on-close Job
    before they can execute. POSIX retains its existing process-group cleanup.
    ``inherit_process_group`` is for unbounded foreground work inside the
    Manager's owned group; the outer owner retains descendant cleanup.
    ``binary`` retains opaque bytes, with optional bytes supplied on stdin.
    ``observation_deadline`` opts text-only, independent observations into a
    single absolute monotonic ceiling, reserving cleanup inside that ceiling.
    """
    if input is not None and not binary:
        raise ValueError("bytes input requires binary capture")
    if observation_deadline is not None:
        if type(observation_deadline) not in (float, int) or not math.isfinite(observation_deadline):
            raise ValueError("observation_deadline must be a finite monotonic instant")
        if binary or input is not None or inherit_process_group:
            raise ValueError("observation requires text capture and independent containment")
        return _run_observation(
            cmd, cwd=cwd, env=env, timeout=timeout, deadline=observation_deadline,
        )
    windows = platform.system() == "Windows"
    proc, job = spawn_sync_in_kill_on_close_job(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=not binary, env=env,
        **({"stdin": subprocess.PIPE} if input is not None else {}),
        **({} if binary else {"encoding": "utf-8", "errors": "replace"}),
        require_job=windows,
        allow_breakaway=False,
        **(no_window_kwargs() if windows else {} if inherit_process_group else detached_kwargs()),
    )
    try:
        try:
            stdout, stderr = proc.communicate(
                timeout=timeout, **({"input": input} if input is not None else {}),
            )
        except subprocess.TimeoutExpired as exc:
            if job is not None:
                job.close()
            else:
                _kill_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = exc.stdout or "", exc.stderr or ""
            exc.stdout, exc.stderr = stdout, stderr
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    except BaseException:
        if job is None:
            _kill_tree(proc)
        raise
    finally:
        if job is not None:
            job.close()


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
        f"git push timed out after {timeout:g}s without completing.",
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
