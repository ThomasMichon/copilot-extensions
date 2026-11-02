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
from typing import Callable

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

_MAX_REQUEST = 16 * 1024 * 1024
_lifetime_lock = threading.Lock()
_lifetime_writers: set[int] = set()
_lifetime_readers: set[int] = set()
_uncertain_lifetime_descriptors: set[int] = set()
_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from agent_procutil._caller_owned import _main;_main()"
)


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
    observer = select.poll()
    observer.register(fd, select.POLLIN)
    events = observer.poll(0)
    if any(mask & (select.POLLERR | select.POLLNVAL) for _fd, mask in events):
        raise RuntimeError("native pidfd exit observation failed")
    return any(mask & select.POLLIN for _fd, mask in events)


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
                        if os.getpgid(pid) != process.pid:
                            continue
                        fd = open_pidfd(pid)
                        try:
                            # A reaped/reused numeric PID is not proof of this group's exit.
                            if _pidfd_pid(fd) != pid:
                                active = True
                            elif os.getpgid(pid) == process.pid:
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


def _validate(request: object) -> dict:
    if not isinstance(request, dict) or set(request) - {"binary", "budget"} != {
        "argv", "timeout", "input_text", "capture", "limits",
    }:
        raise ValueError("invalid caller-owned request")
    argv = request["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise ValueError("native child argv must be a nonempty list of strings")
    if any("\0" in arg for arg in argv):
        raise ValueError("native child argv must not contain NUL characters")
    timeout = request["timeout"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("native child timeout must be finite and positive")
    if request["input_text"] is not None and not isinstance(request["input_text"], str):
        raise ValueError("native child input_text must be text or None")
    if request["input_text"] is not None:
        request["input_text"].encode("utf-8")
    if type(request["capture"]) is not bool:
        raise ValueError("native child capture must be a boolean")
    if type(request.get("binary", False)) is not bool:
        raise ValueError("native child binary capture must be a boolean")
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
    return request


def _decode(reply: object, request: dict, returncode: int) -> subprocess.CompletedProcess:
    if (
        not isinstance(reply, dict) or type(reply.get("version")) is not int
        or reply["version"] != 1 or returncode != -signal.SIGKILL
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
            try:
                stdout, stderr = (base64.b64decode(stream, validate=True) for stream in (stdout, stderr))
                if any(len(stream) > limit for stream, limit in zip((stdout, stderr), request["limits"])):
                    raise ValueError("binary completion exceeds capture limits")
            except ValueError:
                raise RuntimeError("caller-owned binary result is invalid") from None
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
        partial = [None, None]
        if "output" in reply:
            try:
                partial = [base64.b64decode(reply[name], validate=True) for name in ("output", "stderr")]
                if any(len(value) > limit for value, limit in zip(partial, request["limits"])):
                    raise ValueError("timeout evidence exceeds its byte budget")
            except (TypeError, ValueError):
                raise RuntimeError("caller-owned timeout evidence is invalid") from None
        raise subprocess.TimeoutExpired(request["argv"], request["timeout"], *partial)
    if kind == "os-error":
        errno = reply.get("errno")
        if type(errno) is not int:
            raise RuntimeError("caller-owned native failure is invalid")
        raise OSError(errno, "caller-owned native execution or cleanup failed")
    if kind == "decode":
        raise UnicodeError("caller-owned text is not valid UTF-8")
    raise RuntimeError("caller-owned execution or cleanup did not complete")


def run(
    argv: list[str], *, timeout: float, max_stdout_bytes: int, max_stderr_bytes: int,
    input_text: str | None, capture: bool,
    on_settled: Callable[[], None] | None = None,
    cwd: str | os.PathLike | None = None,
    env: dict[str, str] | None = None,
    allow_daemon_breakaway: bool = False,
    binary: bool = False,
    budget: OwnedExecutionBudget | None = None,
    borrow_parent_scope: bool = False,
) -> subprocess.CompletedProcess:
    if on_settled is not None and not callable(on_settled):
        raise TypeError("settlement notification must be callable")
    execution_budget = budget
    try:
        if type(borrow_parent_scope) is not bool:
            raise TypeError("borrow_parent_scope must be a boolean")
        if borrow_parent_scope and budget is None:
            raise ValueError("borrowing an enclosing group requires an aggregate budget")
        if borrow_parent_scope and os.name != "nt" and on_settled is not None:
            raise ValueError("borrowed group settlement belongs to its enclosing owner")
        if budget is not None and not isinstance(budget, OwnedExecutionBudget):
            raise TypeError("budget must be an OwnedExecutionBudget")
        if budget is not None and time.monotonic() >= budget.execution_deadline:
            raise subprocess.TimeoutExpired(argv, timeout)
        request = _validate({
            "argv": argv, "timeout": timeout, "input_text": input_text, "capture": capture,
            "limits": [max_stdout_bytes, max_stderr_bytes],
            **({"binary": binary} if binary is not False else {}),
            **({"budget": {"deadline": budget.deadline, "cleanup_reserve": budget.cleanup_reserve}}
               if budget is not None else {}),
        })
        if budget is not None and os.name != "nt" and not borrow_parent_scope:
            target_deadline = min(time.monotonic() + timeout, budget.execution_deadline)
            capped = OwnedExecutionBudget(target_deadline + budget.cleanup_reserve, budget.cleanup_reserve)
            enclosed = capped.enclosed_budget()
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
                argv, timeout=timeout, input_text=input_text, capture=capture,
                capture_limits=(max_stdout_bytes, max_stderr_bytes),
                on_settled=on_settled,
                allow_daemon_breakaway=allow_daemon_breakaway,
                binary=binary,
                **({"budget": execution_budget} if execution_budget is not None else {}),
                **({"cwd": cwd} if cwd is not None else {}),
                **({"env": env} if env is not None else {}),
            )
        except _PreparationTimeout as error:
            if on_settled is not None:
                on_settled()
            raise subprocess.TimeoutExpired(argv, timeout) from error
    if borrow_parent_scope:
        return _run_owned(
            argv, timeout=timeout, input_text=input_text, capture=capture,
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
                    sys.executable, "-I", "-S", "-u", "-c", _BOOTSTRAP,
                    str(Path(__file__).resolve().parents[1]), str(reader),
                    *([str(os.getpid())] if sys.platform == "darwin" else []),
                ],
                timeout=timeout + (budget.cleanup_reserve / 2 if budget else 0),
                input_text=frame, capture=True, pass_fds=(reader,),
                capture_limits=(6 * (max_stdout_bytes + max_stderr_bytes) + 4096, 65536),
                confirm_posix_group=True,
                on_settled=lambda: native_settled.append(True),
                **({"budget": execution_budget} if execution_budget is not None else {}),
                **({"cwd": cwd} if cwd is not None else {}),
                **({"env": env} if env is not None else {}),
            )
        except _PreparationTimeout as error:
            preparation_refused = True
            raise subprocess.TimeoutExpired(argv, timeout) from error
        except SyncProcessLaunchError:
            preparation_refused = True
            raise
        except subprocess.TimeoutExpired as error:
            raise subprocess.TimeoutExpired(argv, timeout) from error
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
    os.killpg(os.getpid(), signal.SIGKILL)


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
    os.set_blocking(lifetime, False)
    try:
        initial = os.read(lifetime, 1)
    except BlockingIOError:
        initial = None
    if initial is not None:
        _retire()
    os.set_blocking(lifetime, True)

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
    response: dict = {"version": 1, "kind": "execution"}
    try:
        frame = sys.stdin.buffer.readline(_MAX_REQUEST + 1)
        if len(frame) > _MAX_REQUEST or not frame.endswith(b"\n"):
            raise ValueError("caller-owned request frame is invalid")
        request = _validate(json.loads(frame))
        budget = OwnedExecutionBudget(**request["budget"]) if "budget" in request else None
        try:
            result = _run_owned(
                request["argv"], timeout=request["timeout"],
                input_text=request["input_text"], capture=request["capture"],
                inherit_process_group=True, capture_limits=tuple(request["limits"]),
                binary=request.get("binary", False),
                **({"budget": budget} if budget is not None else {}),
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
