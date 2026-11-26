"""Finite POSIX group owner; its lifetime writer is retained only by the caller."""

from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Callable, Protocol, TypedDict, cast

from agent_procutil import SyncProcessLaunchError
from agent_procutil.owned_process import (
    OutputLimitExceeded,
    OwnedCleanupError,
    UnreapedChild,
    _observe_unreaped,
    _run_owned,
    _PreparationTimeout,
    require_contained_worker,
)
from agent_procutil._linux_pidfd import open_pidfd
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil._spawn_signals import defer_termination

_MAX_REQUEST = 16 * 1024 * 1024
_lifetime_lock = threading.Lock()
_lifetime_writers: set[int] = set()
_lifetime_readers: set[int] = set()
_uncertain_lifetime_descriptors: set[int] = set()
_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from agent_procutil._caller_owned import _main;_main()"
)


class _BudgetFrame(TypedDict):
    deadline: float
    cleanup_reserve: float


class _RequiredRequest(TypedDict):
    argv: list[str]
    timeout: float | None
    input_text: str | None
    capture: bool
    limits: list[int]


class _Request(_RequiredRequest, total=False):
    binary: bool
    input_bytes: str
    budget: _BudgetFrame


class _NestedBudget(Protocol):
    def enclosed_budget(self) -> OwnedExecutionBudget: ...


class _Poller(Protocol):
    def register(self, fd: int, eventmask: int) -> None: ...
    def poll(self, timeout: int) -> list[tuple[int, int]]: ...


class _PollModule(Protocol):
    POLLIN: int
    POLLERR: int
    POLLNVAL: int

    def poll(self) -> _Poller: ...


class _PosixNative(Protocol):
    def getpgid(self, pid: int) -> int: ...
    def killpg(self, pgid: int, sig: int) -> None: ...
    def set_blocking(self, fd: int, blocking: bool) -> None: ...


class _Closeable(Protocol):
    def close(self) -> None: ...


class _CleanupProcess(UnreapedChild, Protocol):
    @property
    def stdin(self) -> _Closeable | None: ...

    @property
    def stdout(self) -> _Closeable | None: ...

    @property
    def stderr(self) -> _Closeable | None: ...

    def wait(self, *, timeout: float) -> int: ...


class _PendingOutput(Protocol):
    def finish(
        self, timeout: float, *, deadline: float,
    ) -> tuple[str | bytes | None, str | bytes | None]: ...


_Captured = str | bytes | None


def _after_fork_child() -> None:
    try:
        for writer in _lifetime_writers:
            os.close(writer)
        _lifetime_writers.clear()
    finally:
        _lifetime_lock.release()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_lifetime_lock.acquire, after_in_parent=_lifetime_lock.release,
        after_in_child=_after_fork_child,
    )


def _pidfd_pid(fd: int) -> int:
    fields = [
        line.partition(":")[2].strip()
        for line in Path(f"/proc/self/fdinfo/{fd}").read_text(encoding="ascii").splitlines()
        if line.partition(":")[0] == "Pid"
    ]
    if len(fields) != 1:
        raise RuntimeError("native caller ownership requires readable pidfd identity")
    return int(fields[0])


def _require_posix_support() -> None:
    if sys.platform == "darwin":
        from agent_procutil._darwin_caller_owned import require_support

        require_support()
        return
    if not sys.platform.startswith("linux"):
        raise RuntimeError("caller-owned POSIX execution requires Linux pidfd group observation")
    fd = open_pidfd(os.getpid())
    try:
        if _pidfd_pid(fd) != os.getpid():
            raise RuntimeError("native pidfd identity is unavailable")
    finally:
        os.close(fd)


def _pidfd_exited(fd: int) -> bool:
    native = cast(_PollModule, select)
    observer = native.poll()
    observer.register(fd, native.POLLIN)
    events = observer.poll(0)
    if any(mask & (native.POLLERR | native.POLLNVAL) for _fd, mask in events):
        raise RuntimeError("native pidfd exit observation failed")
    return any(mask & native.POLLIN for _fd, mask in events)


def _confirm_group(process: UnreapedChild, *, budget: OwnedExecutionBudget | None = None) -> None:
    if sys.platform == "darwin":
        from agent_procutil._darwin_caller_owned import GroupCensus

        try:
            census = GroupCensus()
            deadline = time.monotonic() + (budget.cleanup_timeout() if budget else 2.0)
            empty = 0
            while True:
                active = _observe_unreaped(process) is None
                active |= census.active(process.pid)
                empty = 0 if active else empty + 1
                if empty == 2:
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("native process group still has live members")
                time.sleep(0.005)
        except (OSError, RuntimeError, ValueError) as error:
            raise OwnedCleanupError(process) from error
        return
    deadline = time.monotonic() + (budget.cleanup_timeout() if budget else 2.0)
    empty = 0
    try:
        while True:
            active = _observe_unreaped(process) is None
            with os.scandir("/proc") as entries:
                for entry in entries:
                    if budget:
                        budget.cleanup_timeout()
                    if not entry.name.isdigit():
                        continue
                    pid = int(entry.name)
                    if pid == process.pid:
                        continue
                    try:
                        if cast(_PosixNative, os).getpgid(pid) != process.pid:
                            continue
                        fd = open_pidfd(pid)
                        try:
                            # A reaped/reused numeric PID is not proof of this group's exit.
                            if _pidfd_pid(fd) != pid:
                                active = True
                            elif cast(_PosixNative, os).getpgid(pid) == process.pid:
                                active |= not _pidfd_exited(fd)
                        finally:
                            os.close(fd)
                    except ProcessLookupError:
                        continue
            empty = 0 if active else empty + 1
            if empty == 2:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("native process group still has live members")
            time.sleep(0.005)
    except (OSError, RuntimeError, ValueError) as error:
        raise OwnedCleanupError(process) from error


def _zombie_only_group(process: UnreapedChild) -> bool:
    """Confirm the group holds no live member besides its exited, unreaped leader."""
    if sys.platform != "darwin":
        return False
    from agent_procutil._darwin_caller_owned import GroupCensus

    return not GroupCensus().active(process.pid)


def _posix_cleanup_error(
    process: _CleanupProcess, *, confirm_group: bool = False, inherit_process_group: bool = False,
    output: _PendingOutput | None = None, native_settled: bool = False,
    on_settled: Callable[[], None] | None = None,
    group_settled: bool = False,
) -> OwnedCleanupError:
    """Retain the exact unreaped leader and remaining stream custody for retry."""
    def retry(deadline: float) -> None:
        from agent_procutil.owned_process import _retire_posix_group

        nonlocal native_settled

        def remaining() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("owned POSIX cleanup deadline expired")
            return seconds

        if not native_settled:
            # Popen retains a completed reap even if interruption prevented this
            # closure from recording its own stage before the previous unwind.
            if process.returncode is None:
                remaining()
                if not group_settled:
                    _retire_posix_group(process, inherit_process_group=inherit_process_group)
                    if confirm_group:
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


@defer_termination()
def _settle_exited_group(
    process: _CleanupProcess, *, budget: OwnedExecutionBudget | None,
    on_settled: Callable[[], None] | None,
) -> None:
    from agent_procutil.owned_process import _retire_posix_group

    try:
        _retire_posix_group(process)
        _confirm_group(process, budget=budget)
    except BaseException as error:
        raise _posix_cleanup_error(
            process, confirm_group=True, on_settled=on_settled,
        ) from error


@defer_termination()
def _finish_posix_process(
    process: _CleanupProcess, *, confirm_group: bool, inherit_process_group: bool,
    budget: OwnedExecutionBudget | None, group_settled: bool,
    output: _PendingOutput | None, on_settled: Callable[[], None] | None,
) -> None:
    from agent_procutil.owned_process import _retire_posix_group

    try:
        if not group_settled:
            _retire_posix_group(process, inherit_process_group=inherit_process_group)
            if confirm_group:
                if budget is None:
                    _confirm_group(process)
                else:
                    _confirm_group(process, budget=budget)
                group_settled = True
        process.wait(timeout=budget.cleanup_timeout() if budget else 2.0)
    except BaseException as error:
        raise _posix_cleanup_error(
            process, confirm_group=confirm_group, inherit_process_group=inherit_process_group,
            output=output, native_settled=process.returncode is not None,
            on_settled=on_settled, group_settled=group_settled,
        ) from error


def _validate(request: object) -> _Request:
    if not isinstance(request, dict) or set(request) - {"binary", "budget", "input_bytes"} != {
        "argv", "timeout", "input_text", "capture", "limits",
    }:
        raise ValueError("invalid caller-owned request")
    argv = request["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise ValueError("native child argv must be a nonempty list of strings")
    if any("\0" in arg for arg in argv):
        raise ValueError("native child argv must not contain NUL characters")
    timeout = request["timeout"]
    if timeout is not None and (
        type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
    ):
        raise ValueError("native child timeout must be None or finite and positive")
    if request["input_text"] is not None and not isinstance(request["input_text"], str):
        raise ValueError("native child input_text must be text or None")
    if request["input_text"] is not None:
        request["input_text"].encode("utf-8")
    if type(request["capture"]) is not bool:
        raise ValueError("native child capture must be a boolean")
    if type(request.get("binary", False)) is not bool:
        raise ValueError("native child binary capture must be a boolean")
    if "input_bytes" in request:
        if request["input_text"] is not None or request.get("binary") is not True:
            raise ValueError("byte stdin requires binary capture and no text stdin")
        if not isinstance(request["input_bytes"], str):
            raise ValueError("invalid native byte stdin")
        base64.b64decode(request["input_bytes"], validate=True)
    if "budget" in request:
        value = request["budget"]
        if not isinstance(value, dict) or set(value) != {"deadline", "cleanup_reserve"}:
            raise ValueError("invalid owned aggregate budget")
        OwnedExecutionBudget(**value)
    limits = request["limits"]
    if not isinstance(limits, list) or len(limits) != 2 or any(
        type(limit) is not int or limit <= 0 for limit in limits
    ):
        raise ValueError("native child stream limits must be positive integers")
    # Each member and optional frame was checked above; keep the wire contract
    # explicit rather than propagating JSON's dynamically typed dictionary.
    return cast(_Request, request)


def _decode(
    reply: object, request: _Request, returncode: int,
) -> subprocess.CompletedProcess[_Captured]:
    if (
        not isinstance(reply, dict) or type(reply.get("version")) is not int
        or reply["version"] != 1 or returncode != -getattr(signal, "SIGKILL")
    ):
        raise RuntimeError("caller-owned completion is unconfirmed")
    kind = reply.get("kind")
    fields = {
        "completed": {"returncode", "stdout", "stderr"},
        "output-limit": {"stream", "limit"},
        "timeout": ({"output", "stderr"} if "output" in reply else set()),
        "decode": set(), "execution": set(), "os-error": {"errno"},
    }
    if not isinstance(kind, str) or kind not in fields or set(reply) != {
        "version", "kind", *fields[kind],
    }:
        raise RuntimeError("caller-owned completion fields are invalid")
    if kind == "completed":
        code = reply.get("returncode")
        stdout, stderr = reply.get("stdout"), reply.get("stderr")
        expected = str if request["capture"] else type(None)
        if type(code) is not int or not isinstance(stdout, expected) or not isinstance(stderr, expected):
            raise RuntimeError("caller-owned result is invalid")
        if request.get("binary", False) and request["capture"]:
            if not isinstance(stdout, str) or not isinstance(stderr, str):
                raise RuntimeError("caller-owned binary result is invalid")
            try:
                raw_stdout, raw_stderr = (base64.b64decode(stream, validate=True) for stream in (stdout, stderr))
                if any(len(stream) > limit for stream, limit in zip((raw_stdout, raw_stderr), request["limits"])):
                    raise ValueError("binary completion exceeds capture limits")
            except ValueError:
                raise RuntimeError("caller-owned binary result is invalid") from None
            return subprocess.CompletedProcess(request["argv"], code, raw_stdout, raw_stderr)
        return subprocess.CompletedProcess(request["argv"], code, stdout, stderr)
    if kind == "output-limit":
        stream = reply.get("stream")
        if stream not in ("stdout", "stderr"):
            raise RuntimeError("caller-owned stream identity is invalid")
        limit = request["limits"][("stdout", "stderr").index(stream)]
        if type(reply.get("limit")) is not int or reply["limit"] != limit:
            raise RuntimeError("caller-owned stream limit is invalid")
        raise OutputLimitExceeded(stream, limit)
    if kind == "timeout":
        partial: list[bytes | None] = [None, None]
        if "output" in reply:
            try:
                partial = [base64.b64decode(reply[name], validate=True) for name in ("output", "stderr")]
                if any(value is not None and len(value) > limit for value, limit in zip(partial, request["limits"])):
                    raise ValueError("timeout evidence exceeds its byte budget")
            except (TypeError, ValueError):
                raise RuntimeError("caller-owned timeout evidence is invalid") from None
        raise subprocess.TimeoutExpired(request["argv"], cast(float, request["timeout"]), *partial)
    if kind == "os-error":
        errno = reply.get("errno")
        if type(errno) is not int:
            raise RuntimeError("caller-owned native failure is invalid")
        raise OSError(errno, "caller-owned native execution or cleanup failed")
    if kind == "decode":
        raise UnicodeError("caller-owned text is not valid UTF-8")
    raise RuntimeError("caller-owned execution or cleanup did not complete")


def run(
    argv: list[str], *, timeout: float | None, max_stdout_bytes: int, max_stderr_bytes: int,
    input_text: str | None, capture: bool,
    input_bytes: bytes | None = None,
    on_settled: Callable[[], None] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    allow_daemon_breakaway: bool = False,
    binary: bool = False,
    budget: OwnedExecutionBudget | None = None,
    borrow_parent_scope: bool = False,
) -> subprocess.CompletedProcess[_Captured]:
    if on_settled is not None and not callable(on_settled):
        raise TypeError("settlement notification must be callable")
    execution_budget = budget
    try:
        if input_bytes is not None and not isinstance(input_bytes, bytes):
            raise TypeError("native byte stdin must be bytes")
        if input_bytes is not None and 4 * ((len(input_bytes) + 2) // 3) > _MAX_REQUEST:
            raise ValueError("caller-owned request exceeds its frame limit")
        if type(borrow_parent_scope) is not bool:
            raise TypeError("borrow_parent_scope must be a boolean")
        if borrow_parent_scope and budget is None:
            raise ValueError("borrowing an enclosing group requires an aggregate budget")
        if borrow_parent_scope and os.name != "nt" and on_settled is not None:
            raise ValueError("borrowed group settlement belongs to its enclosing owner")
        if budget is not None and not isinstance(budget, OwnedExecutionBudget):
            raise TypeError("budget must be an OwnedExecutionBudget")
        if budget is not None and time.monotonic() >= budget.execution_deadline:
            raise subprocess.TimeoutExpired(argv, cast(float, timeout))
        request = _validate({
            "argv": argv, "timeout": timeout, "input_text": input_text, "capture": capture,
            "limits": [max_stdout_bytes, max_stderr_bytes],
            **({"input_bytes": base64.b64encode(input_bytes).decode("ascii")}
               if input_bytes is not None else {}),
            **({"binary": binary} if binary is not False else {}),
            **({"budget": {"deadline": budget.deadline, "cleanup_reserve": budget.cleanup_reserve}}
               if budget is not None else {}),
        })
        if budget is not None and os.name != "nt" and not borrow_parent_scope:
            target_deadline = budget.execution_deadline
            if timeout is not None:
                target_deadline = min(time.monotonic() + timeout, target_deadline)
            capped = OwnedExecutionBudget(target_deadline + budget.cleanup_reserve, budget.cleanup_reserve)
            enclosed = cast(_NestedBudget, capped).enclosed_budget()
            execution_budget = OwnedExecutionBudget(capped.deadline, capped.cleanup_reserve / 2)
            request["budget"] = {"deadline": enclosed.deadline, "cleanup_reserve": enclosed.cleanup_reserve}
        frame = json.dumps(request, ensure_ascii=True, allow_nan=False) + "\n"
        if len(frame) > _MAX_REQUEST:
            raise ValueError("caller-owned request exceeds its frame limit")
        if os.name != "nt":
            _require_posix_support()
    except BaseException:
        # No native launch has been attempted at this preparation boundary.
        if on_settled is not None:
            on_settled()
        raise
    if os.name == "nt":
        try:
            return _run_owned(
                argv, timeout=timeout, input_text=input_bytes if input_bytes is not None else input_text, capture=capture,
                capture_limits=(max_stdout_bytes, max_stderr_bytes),
                on_settled=on_settled,
                allow_daemon_breakaway=allow_daemon_breakaway,
                binary=binary,
                budget=execution_budget, cwd=cwd, env=env,
            )
        except _PreparationTimeout as error:
            if on_settled is not None:
                on_settled()
            raise subprocess.TimeoutExpired(argv, cast(float, timeout)) from error
    if borrow_parent_scope:
        return _run_owned(
            argv, timeout=timeout, input_text=input_bytes if input_bytes is not None else input_text, capture=capture,
            capture_limits=(max_stdout_bytes, max_stderr_bytes),
            inherit_process_group=True, borrow_parent_scope=True, budget=budget,
            binary=binary, cwd=cwd, env=env,
        )
    reader = writer = None
    launch_attempted = False
    preparation_refused = False
    native_settled = []
    try:
        with _lifetime_lock:
            reader, writer = os.pipe()
            _lifetime_readers.add(reader)
            _lifetime_writers.add(writer)
        os.set_inheritable(writer, False)
        launch_attempted = True
        try:
            result = _run_owned(
                [
                    sys.executable, "-I", "-B", "-S", "-u", "-c", _BOOTSTRAP,
                    str(Path(__file__).resolve().parents[1]), str(reader),
                    *([str(os.getpid())] if sys.platform == "darwin" else []),
                ],
                timeout=(timeout + (budget.cleanup_reserve / 2 if budget else 0)
                         if timeout is not None else None),
                input_text=frame, capture=True, pass_fds=(reader,),
                capture_limits=(6 * (max_stdout_bytes + max_stderr_bytes) + 4096, 65536),
                confirm_posix_group=True,
                on_settled=lambda: native_settled.append(True),
                budget=execution_budget, cwd=cwd, env=env,
            )
        except _PreparationTimeout as error:
            preparation_refused = True
            raise subprocess.TimeoutExpired(argv, cast(float, timeout)) from error
        except SyncProcessLaunchError:
            preparation_refused = True
            raise
        except OwnedCleanupError as error:
            error._retain_on_settled(on_settled)
            raise
        except subprocess.TimeoutExpired as error:
            raise subprocess.TimeoutExpired(argv, cast(float, timeout)) from error
        except OutputLimitExceeded as error:
            raise RuntimeError("caller-owned completion frame exceeded its byte budget") from error
    finally:
        pending = sys.exc_info()[1]
        failed = []
        cause = None
        for descriptor, owned in ((reader, _lifetime_readers), (writer, _lifetime_writers)):
            if descriptor is None:
                continue
            try:
                with _lifetime_lock:
                    owned.discard(descriptor)
                    try:
                        os.close(descriptor)
                    except BaseException:
                        # The number may already be reused; never auto-close it at fork.
                        _uncertain_lifetime_descriptors.add(descriptor)
                        raise
            except BaseException as error:
                failed.append(("descriptor", descriptor))
                cause = error
        if failed:
            raise OwnedCleanupError(
                getattr(pending, "process", None), getattr(pending, "job", None),
                cleanup_resources=(*getattr(pending, "cleanup_resources", ()), *failed),
            ) from cause
        if (native_settled or not launch_attempted or preparation_refused) and on_settled is not None:
            on_settled()
    try:
        reply = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError("caller-owned completion frame is invalid") from error
    return _decode(reply, request, result.returncode)


def _retire() -> None:
    cast(_PosixNative, os).killpg(os.getpid(), getattr(signal, "SIGKILL"))


def _main() -> None:
    require_contained_worker()
    darwin = sys.platform == "darwin"
    if len(sys.argv) != (3 if darwin else 2):
        raise RuntimeError("caller-owned lifetime descriptor is missing")
    owner_watch = None
    if darwin:
        from agent_procutil._darwin_caller_owned import OwnerWatch

        owner_watch = OwnerWatch(int(sys.argv[2]))
    lifetime = int(sys.argv[1])
    if not stat.S_ISFIFO(os.fstat(lifetime).st_mode):
        raise RuntimeError("caller-owned lifetime descriptor is not a pipe")
    cast(_PosixNative, os).set_blocking(lifetime, False)
    try:
        initial = os.read(lifetime, 1)
    except BlockingIOError:
        initial = None
    if initial is not None:
        _retire()
    cast(_PosixNative, os).set_blocking(lifetime, True)

    def watch() -> None:
        try:
            os.read(lifetime, 1)
        finally:
            _retire()

    watcher = threading.Thread(target=watch, name="native-caller-lifetime", daemon=True)
    watcher.start()
    if owner_watch is not None:
        def watch_owner() -> None:
            try:
                owner_watch.wait()
            finally:
                _retire()

        threading.Thread(
            target=watch_owner, name="native-caller-exit", daemon=True,
        ).start()
    response: dict[str, object] = {"version": 1, "kind": "execution"}
    try:
        frame = sys.stdin.buffer.readline(_MAX_REQUEST + 1)
        if len(frame) > _MAX_REQUEST or not frame.endswith(b"\n"):
            raise ValueError("caller-owned request frame is invalid")
        request = _validate(json.loads(frame))
        budget = OwnedExecutionBudget(**request["budget"]) if "budget" in request else None
        try:
            result = _run_owned(
                request["argv"], timeout=request["timeout"],
                input_text=base64.b64decode(request["input_bytes"], validate=True)
                if "input_bytes" in request else request["input_text"], capture=request["capture"],
                inherit_process_group=True, completion_supervisor=True,
                capture_limits=(request["limits"][0], request["limits"][1]),
                binary=request.get("binary", False),
                budget=budget,
            )
            if request.get("binary", False) and request["capture"]:
                result.stdout, result.stderr = (
                    base64.b64encode(stream).decode("ascii") for stream in (result.stdout, result.stderr)
                )
            response.update(
                kind="completed", returncode=result.returncode,
                stdout=result.stdout, stderr=result.stderr,
            )
        except OutputLimitExceeded as error:
            response.update(kind="output-limit", stream=error.stream, limit=error.limit)
        except subprocess.TimeoutExpired as error:
            response["kind"] = "timeout"
            if error.output is not None or error.stderr is not None:
                response.update(
                    output=base64.b64encode(error.output or b"").decode("ascii"),
                    stderr=base64.b64encode(error.stderr or b"").decode("ascii"),
                )
        except UnicodeError:
            response["kind"] = "decode"
        except OSError as error:
            if type(error.errno) is int:
                response.update(kind="os-error", errno=error.errno)
            else:
                response["kind"] = "execution"
        except Exception:
            response["kind"] = "execution"
        sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
        sys.stdout.flush()
    finally:
        # The caller also retains unreaped custody and sweeps this same group before return.
        _retire()
