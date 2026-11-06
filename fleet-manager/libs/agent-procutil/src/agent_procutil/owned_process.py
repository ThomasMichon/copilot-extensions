"""Bounded children in owned roots or an explicitly inherited containment scope."""

from __future__ import annotations

import errno
import math
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from typing import Callable, Protocol

from agent_procutil import (
    SyncProcessLaunchError,
    no_window_kwargs,
    spawn_sync_in_kill_on_close_job,
)
from agent_procutil.owned_budget import OwnedExecutionBudget

_THREAD_START_CODE = getattr(threading.Thread.start, "__code__", None)


def _pipe_worker_definitely_unstarted(
    worker: threading.Thread, started: threading.Event, error: BaseException,
) -> bool:
    """Only direct stdlib pre-launch refusals attest that no worker was created."""
    traceback = error.__traceback__
    while traceback is not None and traceback.tb_next is not None:
        traceback = traceback.tb_next
    return (
        type(error) is RuntimeError
        and str(error) in {"can't start new thread", "thread.__init__() not called"}
        and traceback is not None
        and traceback.tb_frame.f_code is _THREAD_START_CODE
        and traceback.tb_frame.f_locals.get("self") is worker
        and not started.is_set()
    )


class UnreapedChild(Protocol):
    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...


class OutputLimitExceeded(subprocess.SubprocessError):
    """An owned stream exceeded its byte budget; captured content is not disclosed."""

    def __init__(self, stream: str, limit: int) -> None:
        self.stream = stream
        self.limit = limit
        super().__init__(f"native child {stream} exceeded its {limit}-byte limit")


class OwnedCleanupError(subprocess.SubprocessError):
    """Cleanup is unconfirmed; retain exact process/Job custody for the owner."""

    def __init__(
        self, process: UnreapedChild | None, job=None, *, cleanup_resources=(),
        cleanup_retry: Callable[[float], None] | None = None,
        on_settled: Callable[[], None] | None = None,
    ) -> None:
        self.process = process
        self.job = job
        self.cleanup_resources = tuple(cleanup_resources)
        self._retry_resources = self.cleanup_resources
        self._cleanup_retry = cleanup_retry
        self._cleanup_lock = threading.Lock()
        self._cleanup_complete = False
        self._on_settled: list[Callable[[], None]] = []
        self._settlement_attempted = 0
        self._settlement_error: BaseException | None = None
        self._retain_on_settled(on_settled)
        super().__init__("native child cleanup did not complete")

    def _retain_on_settled(self, callback: Callable[[], None] | None) -> None:
        if callback is not None and not any(existing is callback for existing in self._on_settled):
            self._on_settled.append(callback)

    def retry_cleanup(self, *, deadline: float) -> None:
        """Settle retained native objects, never infer cleanup from their absence."""
        if not math.isfinite(deadline) or deadline <= time.monotonic():
            raise TimeoutError("owned cleanup retry deadline expired")
        if not self._cleanup_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise TimeoutError("owned cleanup retry is still in progress")
        try:
            if self._cleanup_retry is None or self.cleanup_resources != self._retry_resources:
                raise RuntimeError("retained native cleanup has no complete retry operation")
            if not self._cleanup_complete:
                self._cleanup_retry(deadline)
                if time.monotonic() >= deadline:
                    raise TimeoutError("owned cleanup retry deadline expired")
                self._cleanup_complete = True
            if self._settlement_error is not None:
                raise RuntimeError(
                    "native settlement callback delivery is unconfirmed"
                ) from self._settlement_error
            while self._settlement_attempted < len(self._on_settled):
                if time.monotonic() >= deadline:
                    raise TimeoutError("owned settlement callback deadline expired")
                callback = self._on_settled[self._settlement_attempted]
                self._settlement_attempted += 1
                try:
                    callback()
                except BaseException as error:
                    self._settlement_error = error
                    raise
        finally:
            self._cleanup_lock.release()


class _PreparationTimeout(subprocess.TimeoutExpired):
    """Known refusal before attempting any native process creation."""


def _require_windows_job() -> None:
    import ctypes
    from ctypes import wintypes

    from agent_procutil import _kernel32

    kernel = _kernel32()
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    contained = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(contained)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not contained.value:
        raise RuntimeError("contained worker requires an existing Windows Job")
    # NULL-job limit queries can describe another Job in the nested/venv
    # chain. Strictness is enforced at every producer's suspended spawn,
    # using its retained Job handle, never inferred from that query.


def require_contained_worker() -> None:
    """Validate kernel posture; this lifetime contract grants no application authority."""
    if os.name == "nt":
        _require_windows_job()
    else:
        pid = os.getpid()
        if os.getpgrp() != pid or os.getsid(0) != pid:
            raise RuntimeError("contained worker requires its outer owner's session/group root")


def _remaining(deadline: float, argv: list[str], timeout: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv, timeout)
    return remaining


def _observe_unreaped(process: UnreapedChild):
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError("native child custody requires the default SIGCHLD disposition")
    if process.returncode is not None:
        raise RuntimeError("native child custody was already released")
    if sys.platform == "darwin":
        from agent_procutil._darwin_caller_owned import observe_unreaped

        return observe_unreaped(process)
    try:
        status = os.waitid(
            os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except OSError as error:
        raise RuntimeError("native child custody is unconfirmed") from error
    if status is not None and status.si_pid != process.pid:
        raise RuntimeError("native child identity changed")
    return status


def _zombie_only_group(process: subprocess.Popen) -> bool:
    """Confirm the group holds no live member besides its exited, unreaped leader."""
    if sys.platform != "darwin":
        return False
    from agent_procutil._darwin_caller_owned import GroupCensus

    return not GroupCensus().active(process.pid)


def _retire_posix_group(
    process: UnreapedChild, *, inherit_process_group: bool = False,
) -> None:
    # Exclusive Popen ownership plus WNOWAIT pins the original PID through termination.
    # Never poll, communicate or wait this child before this boundary.
    exited = _observe_unreaped(process) is not None
    try:
        if inherit_process_group:
            os.kill(process.pid, signal.SIGKILL)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin refuses killpg() for a group left with only its exited leader;
        # leader exit alone never proves settlement, so a census must confirm it.
        if inherit_process_group or not exited or not _zombie_only_group(process):
            raise


def _posix_cleanup_error(
    process, *, confirm_group=False, inherit_process_group=False,
    output=None, native_settled=False, on_settled=None,
) -> OwnedCleanupError:
    """Retain the exact unreaped leader and remaining stream custody for retry."""
    def retry(deadline):
        nonlocal native_settled

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("owned POSIX cleanup deadline expired")
            return seconds

        if not native_settled:
            # Popen retains a completed reap even if interruption prevented this
            # closure from recording its own stage before the previous unwind.
            if process.returncode is None:
                remaining()
                _retire_posix_group(process, inherit_process_group=inherit_process_group)
                if confirm_group:
                    from agent_procutil._caller_owned import _confirm_group
                    _confirm_group(process, budget=OwnedExecutionBudget(deadline, .01))
                process.wait(timeout=remaining())
            native_settled = True
        if output is not None:
            output.finish(remaining(), deadline=deadline)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                remaining()
                stream.close()

    return OwnedCleanupError(
        process, cleanup_resources=(("output", output),) if output is not None else (),
        cleanup_retry=retry, on_settled=on_settled,
    )


def _communicate_unreaped(
    process: subprocess.Popen, *, input_text: str | bytes | None, deadline: float,
    argv: list[str], timeout: float, capture_limits: tuple[int, int] | None = None,
    binary: bool = False,
    capture_timeout: bool = False,
) -> tuple[str | None, str | None]:
    streams = (process.stdout, process.stderr)
    output = (bytearray(), bytearray())
    data = memoryview(input_text.encode("utf-8") if isinstance(input_text, str) else input_text) if input_text is not None else None
    offset = 0
    def remaining():
        try:
            return _remaining(deadline, argv, timeout)
        except subprocess.TimeoutExpired as error:
            if capture_limits is not None and capture_timeout:
                error.output, error.stderr = (
                    bytes(value) if stream is not None else None
                    for value, stream in zip(output, streams)
                )
            raise

    with selectors.DefaultSelector() as selector:
        for index, stream in enumerate(streams):
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
        if process.stdin is not None:
            if data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, -1)
            else:
                process.stdin.close()
        while selector.get_map():
            for key, _mask in selector.select(remaining()):
                if key.data == -1:
                    try:
                        offset += os.write(key.fd, data[offset:offset + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(data)
                    if offset == len(data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if chunk:
                        if (
                            capture_limits is not None
                            and len(output[key.data]) + len(chunk) > capture_limits[key.data]
                        ):
                            raise OutputLimitExceeded(
                                ("stdout", "stderr")[key.data], capture_limits[key.data],
                            )
                        output[key.data].extend(chunk)
                    else:
                        selector.unregister(key.fileobj)
    while _observe_unreaped(process) is None:
        time.sleep(min(.01, remaining()))
    return tuple(
        (bytes(data) if binary else data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"))
        if stream is not None else None
        for data, stream in zip(output, streams)
    )


def _communicate_windows(
    process: subprocess.Popen, job, *, input_text: str | None, deadline: float,
    argv: list[str], timeout: float,
) -> tuple[str | None, str | None]:
    if job is None:
        raise RuntimeError("native child Windows Job custody is unavailable")
    stop = threading.Event()
    expired = threading.Event()
    retirement = threading.Lock()
    retired = False
    failures = []

    def retire():
        nonlocal retired
        with retirement:
            if not retired:
                job.terminate_and_wait(2.0)
                retired = True

    def supervise():
        if stop.wait(max(0.0, deadline - time.monotonic())):
            return
        expired.set()
        try:
            retire()
        except BaseException as error:
            failures.append(error)

    watchdog = (
        threading.Thread(target=supervise, name="pr-native-input-deadline", daemon=False)
        if input_text is not None else None
    )
    try:
        if watchdog is not None:
            watchdog.start()
        stdout, stderr = process.communicate(
            input_text, timeout=_remaining(deadline, argv, timeout),
        )
        completed_at = time.monotonic()
        stop.set()
    finally:
        stop.set()
        try:
            retire()
        finally:
            if watchdog is not None and watchdog.ident is not None:
                watchdog.join()
            process.wait(timeout=2.0)
            # All Job-owned pipe ends are now closed. Join readers before the
            # caller closes their streams; stdin writing ran on this caller.
            for reader in (
                getattr(process, "stdout_thread", None), getattr(process, "stderr_thread", None),
            ):
                if reader is not None:
                    reader.join()
        if failures:
            raise failures[0]
    if expired.is_set() or completed_at >= deadline:
        raise subprocess.TimeoutExpired(argv, timeout, stdout, stderr)
    return stdout, stderr


def _communicate_windows_bounded(
    process: subprocess.Popen, job, *, input_text: str | bytes | None, deadline: float,
    argv: list[str], timeout: float, capture_limits: tuple[int, int],
    binary: bool = False,
    budget: OwnedExecutionBudget | None = None,
) -> tuple[str | None, str | None]:
    if job is None:
        raise RuntimeError("native child Windows Job custody is unavailable")
    output = (bytearray(), bytearray())
    primary: list[BaseException] = []
    error_lock = threading.Lock()
    retirement = threading.Lock()
    retiring = threading.Event()
    retirement_error: list[BaseException] = []
    retired = False

    def note(error: BaseException) -> None:
        with error_lock:
            if not primary:
                primary.append(error)

    def cleanup_timeout(retry_deadline: float | None = None) -> float:
        if retry_deadline is None:
            return budget.cleanup_timeout() if budget else 2.0
        remaining = retry_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("owned cleanup retry deadline expired")
        return min(2.0, remaining)

    def retire(retry_deadline: float | None = None) -> None:
        nonlocal retired
        acquired = retirement.acquire(timeout=cleanup_timeout(retry_deadline))
        if not acquired:
            raise TimeoutError("owned retirement lock did not settle")
        try:
            if retired:
                return
            if retiring.is_set():
                if retirement_error and retry_deadline is None:
                    raise retirement_error[0]
            retiring.set()
            try:
                process.retire_job(job, cleanup_timeout(retry_deadline))
                retired = True
                retirement_error.clear()
            except BaseException as error:
                retirement_error[:] = [error]
                raise
        finally:
            retirement.release()

    def fail(error: BaseException) -> None:
        note(error)
        try:
            retire()
        except BaseException:
            # The retained retirement error is surfaced by the caller's cleanup boundary.
            return

    def read(stream, index: int) -> None:
        try:
            while True:
                size = min(65536, capture_limits[index] - len(output[index]) + 1)
                chunk = stream.read(size)
                if not chunk:
                    return
                if len(output[index]) + len(chunk) > capture_limits[index]:
                    fail(OutputLimitExceeded(("stdout", "stderr")[index], capture_limits[index]))
                    return
                output[index].extend(chunk)
        except (OSError, ValueError) as error:
            if not retiring.is_set():
                fail(error)
        except BaseException as error:
            fail(error)

    def write() -> None:
        try:
            data = memoryview(input_text.encode("utf-8") if isinstance(input_text, str) else input_text)
            offset = 0
            while offset < len(data):
                written = process.stdin.write(data[offset:offset + 65536])
                if not written:
                    raise OSError("native child stdin write made no progress")
                offset += written
        except (OSError, ValueError) as error:
            if not retiring.is_set() and not (
                isinstance(error, OSError) and error.errno in (errno.EPIPE, errno.EINVAL)
            ):
                fail(error)
        except BaseException as error:
            fail(error)
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError) as error:
                if not retiring.is_set() and not (
                    isinstance(error, OSError) and error.errno in (errno.EPIPE, errno.EINVAL)
                ):
                    fail(error)

    def run_worker(callback, started, completed, *args) -> None:
        started.set()
        try:
            callback(*args)
        finally:
            completed.set()

    workers = []
    attempted = []
    started_workers = {}
    unstarted = set()
    cleanup_error = None

    def retry_cleanup(retry_deadline: float) -> None:
        retire(retry_deadline)
        for worker, completed in attempted:
            if worker in unstarted:
                continue
            if not completed.wait(cleanup_timeout(retry_deadline)):
                raise RuntimeError("native child pipe worker completion is unconfirmed")
            worker.join(timeout=cleanup_timeout(retry_deadline))
            if worker.is_alive():
                raise RuntimeError("native child pipe worker did not settle")
        process.wait(timeout=cleanup_timeout(retry_deadline))
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                cleanup_timeout(retry_deadline)
                stream.close()
        cleanup_timeout(retry_deadline)
        process.close()

    try:
        for index, stream in enumerate((process.stdout, process.stderr)):
            if stream is not None:
                started = threading.Event()
                completed = threading.Event()
                worker = threading.Thread(
                    target=run_worker, args=(read, started, completed, stream, index), daemon=True,
                )
                workers.append((worker, completed))
                started_workers[worker] = started
        if process.stdin is not None:
            started = threading.Event()
            completed = threading.Event()
            worker = threading.Thread(
                target=run_worker, args=(write, started, completed), daemon=True,
            )
            workers.append((worker, completed))
            started_workers[worker] = started
        for worker, completed in workers:
            attempted.append((worker, completed))
            try:
                worker.start()
            except BaseException as error:
                if _pipe_worker_definitely_unstarted(worker, started_workers[worker], error):
                    unstarted.add(worker)
                raise
        for worker, _completed in workers:
            while worker.is_alive():
                worker.join(min(0.05, _remaining(deadline, argv, timeout)))
        while process.poll() is None:
            remaining = _remaining(deadline, argv, timeout)
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(argv, timeout)
    except BaseException as error:
        note(error)
    finally:
        try:
            retire()
        except BaseException as error:
            cleanup_error = error
        for worker, completed in attempted:
            if worker in unstarted:
                continue
            try:
                if not completed.wait(budget.cleanup_timeout() if budget else 2.0):
                    raise RuntimeError("native child pipe worker startup or completion is unconfirmed")
                worker.join(timeout=budget.cleanup_timeout() if budget else 2.0)
                if worker.is_alive():
                    raise RuntimeError("native child pipe worker did not settle")
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        try:
            process.wait(timeout=budget.cleanup_timeout() if budget else 2.0)
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
    if cleanup_error is not None:
        raise OwnedCleanupError(
            process, job, cleanup_resources=[
                ("pipe-worker", (worker, completed)) for worker, completed in attempted
            ] + [
                ("pipe-worker-start", (worker, started_workers[worker])) for worker, _ in attempted
            ] + [("pipe-worker-unstarted", worker) for worker in unstarted],
            cleanup_retry=retry_cleanup,
        ) from cleanup_error
    if primary:
        if isinstance(primary[0], subprocess.TimeoutExpired):
            # Readers have joined and retirement succeeded; preserve raw,
            # budgeted evidence without decoding or adding it to the message.
            primary[0].output = bytes(output[0]) if process.stdout is not None else None
            primary[0].stderr = bytes(output[1]) if process.stderr is not None else None
        raise primary[0]
    return tuple(
        (bytes(data) if binary else data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"))
        if stream is not None else None
        for data, stream in zip(output, (process.stdout, process.stderr))
    )


def _run_owned(
    argv: list[str], *, timeout: float | None, input_text: str | bytes | None = None,
    capture: bool = True, inherit_process_group: bool = False,
    capture_limits: tuple[int, int] | None = None, pass_fds: tuple[int, ...] = (),
    confirm_posix_group: bool = False,
    on_settled: Callable[[], None] | None = None,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    allow_daemon_breakaway: bool = False,
    binary: bool = False,
    budget: OwnedExecutionBudget | None = None,
    borrow_parent_scope: bool = False,
    inactivity_timeout: float | None = None,
    output_fds: Mapping[str, int] | None = None,
) -> subprocess.CompletedProcess:
    streaming = inactivity_timeout is not None or output_fds is not None
    if timeout is None and not streaming or timeout is not None and (
        type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
    ):
        raise ValueError("native child timeout must be finite and positive")
    windows = os.name == "nt"
    if streaming and (windows or input_text is not None or inherit_process_group):
        raise ValueError("streaming owned execution requires an exclusive POSIX group without input")
    if inactivity_timeout is not None and (
        type(inactivity_timeout) not in (int, float)
        or not math.isfinite(inactivity_timeout) or inactivity_timeout <= 0
    ):
        raise ValueError("native child inactivity timeout must be finite and positive")
    if timeout is None and inactivity_timeout is None:
        raise ValueError("streaming execution requires an absolute or inactivity deadline")
    if type(inherit_process_group) is not bool:
        raise ValueError("inherit_process_group must be a boolean")
    if type(binary) is not bool or binary and capture_limits is None:
        raise ValueError("binary capture requires explicit stream limits")
    if isinstance(input_text, bytes) and not binary:
        raise ValueError("byte stdin requires binary capture")
    if type(allow_daemon_breakaway) is not bool:
        raise ValueError("allow_daemon_breakaway must be a boolean")
    if inherit_process_group:
        if on_settled is not None or confirm_posix_group:
            raise ValueError("inherited process-group settlement belongs to its enclosing owner")
        if borrow_parent_scope:
            parent = os.getppid()
            if os.name == "nt" or os.getpgrp() != parent or os.getsid(0) != parent:
                raise RuntimeError("borrowed caller requires its enclosing parent's native group")
        else:
            require_contained_worker()
    if not windows:
        _require_posix_execution_support()
    deadline = time.monotonic() + timeout if timeout is not None else None
    if budget is not None:
        if not isinstance(budget, OwnedExecutionBudget):
            raise TypeError("budget must be an OwnedExecutionBudget")
        deadline = (
            min(deadline, budget.execution_deadline) if deadline is not None
            else budget.execution_deadline
        )
        if deadline <= time.monotonic():
            raise _PreparationTimeout(argv, timeout)
    bounded_windows = windows and capture_limits is not None
    if on_settled is not None and not (
        bounded_windows or not windows and confirm_posix_group
    ):
        raise ValueError("settlement notification requires whole-scope native confirmation")
    spawn = spawn_sync_in_kill_on_close_job
    if bounded_windows:
        from agent_procutil._windows_caller_owned import spawn

    try:
        process, job = spawn(
            argv, require_job=True, allow_breakaway=allow_daemon_breakaway,
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture or streaming else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture or streaming else subprocess.DEVNULL,
            **({"text": False, "bufsize": 0} if bounded_windows or streaming else {
                "text": True, "encoding": "utf-8", "errors": "strict",
            }),
            **no_window_kwargs(),
            **({"start_new_session": True} if not windows and not inherit_process_group else {}),
            **({"pass_fds": pass_fds} if pass_fds else {}),
            **({"cwd": cwd} if cwd is not None else {}),
            **({"env": env} if env is not None else {}),
            **({"budget": budget, "command_deadline": deadline, "on_settled": on_settled}
               if bounded_windows and budget is not None else {}),
        )
    except subprocess.TimeoutExpired as error:
        raise subprocess.TimeoutExpired(argv, timeout) from error
    except OwnedCleanupError as error:
        error._retain_on_settled(on_settled)
        raise
    retained = False
    stream_capture = None
    try:
        if streaming:
            from agent_procutil.streaming import SyncProcessOutput

            stream_capture = SyncProcessOutput(
                process, output_fds=output_fds, capture=capture,
                cleanup_deadline=deadline,
            )
            if deadline is not None:
                _remaining(deadline, argv, timeout)
            stream_capture.wait(
                inactivity_timeout, deadline=deadline, wait_for_eof=False,
                exit_observer=lambda: _observe_unreaped(process) is not None,
                cleanup_deadline=budget.deadline if budget else None,
            )
            if deadline is not None:
                _remaining(deadline, argv, timeout)
            stdout, stderr = None, None
        elif bounded_windows:
            stdout, stderr = _communicate_windows_bounded(
                process, job, input_text=input_text, deadline=deadline,
                argv=argv, timeout=timeout, capture_limits=capture_limits,
                binary=binary,
                **({"budget": budget} if budget is not None else {}),
            )
        elif windows:
            stdout, stderr = _communicate_windows(
                process, job, input_text=input_text, deadline=deadline, argv=argv, timeout=timeout,
            )
        else:
            stdout, stderr = _communicate_unreaped(
                process, input_text=input_text, deadline=deadline, argv=argv, timeout=timeout,
                capture_limits=capture_limits,
                binary=binary,
                **({"capture_timeout": True} if budget is not None else {}),
            )
    except OwnedCleanupError as error:
        retained = True
        error._retain_on_settled(on_settled)
        if stream_capture is not None:
            error.cleanup_resources = (*error.cleanup_resources, ("output", stream_capture))
        raise
    finally:
        try:
            if bounded_windows and not retained and job.handle:
                try:
                    process.retire_job(job, budget.cleanup_timeout() if budget else 2.0)
                    process.wait(timeout=budget.cleanup_timeout() if budget else 2.0)
                except BaseException as error:
                    retained = True
                    raise OwnedCleanupError(process, job, on_settled=on_settled) from error
            elif not windows:
                if confirm_posix_group:
                    from agent_procutil._caller_owned import _confirm_group

                    try:
                        _retire_posix_group(process)
                        _confirm_group(process, **({"budget": budget} if budget else {}))
                        process.wait(timeout=budget.cleanup_timeout() if budget else 2.0)
                    except BaseException as error:
                        retained = True
                        raise _posix_cleanup_error(
                            process, confirm_group=True, output=stream_capture,
                            native_settled=process.returncode is not None, on_settled=on_settled,
                        ) from error
                else:
                    try:
                        _retire_posix_group(process, inherit_process_group=inherit_process_group)
                        process.wait(timeout=budget.cleanup_timeout() if budget else 2.0)
                    except BaseException as error:
                        retained = True
                        raise _posix_cleanup_error(
                            process, inherit_process_group=inherit_process_group,
                            native_settled=process.returncode is not None, on_settled=on_settled,
                        ) from error
        finally:
            if not retained:
                if streaming and stream_capture is None:
                    retained = True
                    raise OwnedCleanupError(process, on_settled=on_settled) from sys.exc_info()[1]
                if stream_capture is not None:
                    try:
                        stream_capture.finish(
                            budget.cleanup_timeout() if budget else 2.0,
                            deadline=budget.deadline if budget else time.monotonic() + 2.0,
                        )
                        stdout, stderr = stream_capture.output() if capture else (None, None)
                        pending = sys.exc_info()[1]
                        if isinstance(pending, subprocess.TimeoutExpired) and capture:
                            pending.output, pending.stderr = stdout, stderr
                    except BaseException as error:
                        retained = True
                        if not windows:
                            raise _posix_cleanup_error(
                                process, output=stream_capture, native_settled=True,
                                on_settled=on_settled,
                            ) from error
                        raise OwnedCleanupError(
                            process, cleanup_resources=(("output", stream_capture),),
                            on_settled=on_settled,
                        ) from error
                try:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
                    if bounded_windows:
                        process.close()
                except BaseException as error:
                    if not windows:
                        raise _posix_cleanup_error(
                            process, native_settled=True, on_settled=on_settled,
                        ) from error
                    raise OwnedCleanupError(process, job, on_settled=on_settled) from error
                if on_settled is not None:
                    on_settled()
    if streaming and capture and not binary:
        stdout, stderr = (
            data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            if data is not None else None for data in (stdout, stderr)
        )
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run_owned(
    argv: list[str], *, timeout: float | None, input_text: str | None = None,
    capture: bool = True, inherit_process_group: bool = False,
    cwd: str | os.PathLike | None = None, env: dict[str, str] | None = None,
    on_settled: Callable[[], None] | None = None,
    inactivity_timeout: float | None = None,
    output_fds: Mapping[str, int] | None = None,
    budget: OwnedExecutionBudget | None = None,
) -> subprocess.CompletedProcess:
    """Own a finite root, or borrow its verified scope without detaching nested work.

    Borrowed POSIX children are individually retired before reaping. Only their
    outer owner may sweep the inherited group, including descendants, before
    reporting whole-scope completion. Callers retain exclusive Popen custody.
    """
    if on_settled is not None and not callable(on_settled):
        raise TypeError("settlement notification must be callable")
    if inherit_process_group and on_settled is not None:
        raise ValueError("inherited process-group settlement belongs to its enclosing owner")
    if os.name != "nt" and (
        on_settled is not None or inactivity_timeout is not None or output_fds is not None
    ):
        try:
            require_caller_owned_support()
        except (OSError, RuntimeError) as error:
            cause = (
                error if isinstance(error, OSError) and error.errno is not None
                else OSError(errno.ENOTSUP, str(error))
            )
            raise SyncProcessLaunchError(cause) from error
    return _run_owned(
        argv, timeout=timeout, input_text=input_text, capture=capture,
        inherit_process_group=inherit_process_group,
        cwd=cwd, env=env, on_settled=on_settled,
        confirm_posix_group=on_settled is not None or (
            inactivity_timeout is not None or output_fds is not None
        ),
        inactivity_timeout=inactivity_timeout, output_fds=output_fds, budget=budget,
    )


def _require_posix_execution_support() -> None:
    if sys.platform == "darwin":
        from agent_procutil._darwin_caller_owned import _waitid

        _waitid()
    elif not all(hasattr(os, name) for name in (
        "waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT",
    )):
        raise RuntimeError("native child execution requires unreaped POSIX custody")
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError("native child custody requires the default SIGCHLD disposition")


def require_caller_owned_support() -> None:
    """Validate native observation before accepting effects into a durable worker."""
    if os.name != "nt":
        from agent_procutil._caller_owned import _require_posix_support

        _require_posix_execution_support()
        _require_posix_support()


def run_caller_owned(
    argv: list[str], *, timeout: float, max_stdout_bytes: int, max_stderr_bytes: int,
    input_text: str | None = None, capture: bool = True,
    input_bytes: bytes | None = None,
    on_settled: Callable[[], None] | None = None,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    binary: bool = False,
    budget: OwnedExecutionBudget | None = None,
    borrow_parent_scope: bool = False,
) -> subprocess.CompletedProcess:
    """Execute with caller lifetime/stream budgets; optional binary stdin is byte-exact."""
    from agent_procutil._caller_owned import run

    return run(
        argv, timeout=timeout, max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes, input_text=input_text, capture=capture,
        **({"input_bytes": input_bytes} if input_bytes is not None else {}),
        on_settled=on_settled,
        **({"cwd": cwd} if cwd is not None else {}),
        **({"env": env} if env is not None else {}),
        **({"binary": binary} if binary is not False else {}),
        **({"budget": budget} if budget is not None else {}),
        **({"borrow_parent_scope": borrow_parent_scope} if borrow_parent_scope is not False else {}),
    )


def run_installation_owned(
    argv: list[str], *, timeout: float, max_stdout_bytes: int, max_stderr_bytes: int,
    on_settled: Callable[[], None] | None = None,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Own a trusted installer or service transition, allowing detached product daemons.

    Ordinary descendants remain in the cleanup Job. Only a daemon's explicit
    breakaway can leave it; an external restrictive Job is never bypassed.
    """
    from agent_procutil import contained_test_mode
    from agent_procutil._caller_owned import run

    return run(
        argv, timeout=timeout, max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes, input_text=None, capture=True,
        on_settled=on_settled, allow_daemon_breakaway=not contained_test_mode(),
        **({"cwd": cwd} if cwd is not None else {}),
        **({"env": env} if env is not None else {}),
    )
