"""Finite POSIX group owner; its lifetime writer is retained only by the caller."""

from __future__ import annotations

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

from agent_procutil.owned_process import (
    OutputLimitExceeded,
    OwnedCleanupError,
    _observe_unreaped,
    _run_owned,
    require_contained_worker,
)

_MAX_REQUEST = 16 * 1024 * 1024
_lifetime_lock = threading.Lock()
_lifetime_writers: set[int] = set()
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
    if not sys.platform.startswith("linux") or not hasattr(os, "pidfd_open"):
        raise RuntimeError("caller-owned POSIX execution requires Linux pidfd group observation")
    fd = os.pidfd_open(os.getpid())
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


def _confirm_group(process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 2.0
    empty = 0
    try:
        while True:
            active = _observe_unreaped(process) is None
            with os.scandir("/proc") as entries:
                for entry in entries:
                    if not entry.name.isdigit():
                        continue
                    pid = int(entry.name)
                    if pid == process.pid:
                        continue
                    try:
                        if os.getpgid(pid) != process.pid:
                            continue
                        fd = os.pidfd_open(pid)
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
    if not isinstance(request, dict) or set(request) != {
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
        "timeout": set(), "decode": set(), "execution": set(), "os-error": {"errno"},
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
        raise subprocess.TimeoutExpired(request["argv"], request["timeout"])
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
) -> subprocess.CompletedProcess:
    request = _validate({
        "argv": argv, "timeout": timeout, "input_text": input_text, "capture": capture,
        "limits": [max_stdout_bytes, max_stderr_bytes],
    })
    frame = json.dumps(request, ensure_ascii=True, allow_nan=False) + "\n"
    if len(frame) > _MAX_REQUEST:
        raise ValueError("caller-owned request exceeds its frame limit")
    if os.name == "nt":
        return _run_owned(
            argv, timeout=timeout, input_text=input_text, capture=capture,
            capture_limits=(max_stdout_bytes, max_stderr_bytes),
        )
    _require_posix_support()
    with _lifetime_lock:
        reader, writer = os.pipe()
        _lifetime_writers.add(writer)
    try:
        os.set_inheritable(writer, False)
        try:
            result = _run_owned(
                [
                    sys.executable, "-I", "-S", "-u", "-c", _BOOTSTRAP,
                    str(Path(__file__).resolve().parents[1]), str(reader),
                ],
                timeout=timeout, input_text=frame, capture=True, pass_fds=(reader,),
                capture_limits=(6 * (max_stdout_bytes + max_stderr_bytes) + 4096, 65536),
                confirm_posix_group=True,
            )
        except subprocess.TimeoutExpired as error:
            raise subprocess.TimeoutExpired(argv, timeout) from error
        except OutputLimitExceeded as error:
            raise RuntimeError("caller-owned completion frame exceeded its byte budget") from error
    finally:
        os.close(reader)
        with _lifetime_lock:
            _lifetime_writers.remove(writer)
            os.close(writer)
    try:
        reply = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError("caller-owned completion frame is invalid") from error
    return _decode(reply, request, result.returncode)


def _retire() -> None:
    os.killpg(os.getpid(), signal.SIGKILL)


def _main() -> None:
    require_contained_worker()
    if len(sys.argv) != 2:
        raise RuntimeError("caller-owned lifetime descriptor is missing")
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
    response: dict = {"version": 1, "kind": "execution"}
    try:
        frame = sys.stdin.buffer.readline(_MAX_REQUEST + 1)
        if len(frame) > _MAX_REQUEST or not frame.endswith(b"\n"):
            raise ValueError("caller-owned request frame is invalid")
        request = _validate(json.loads(frame))
        try:
            result = _run_owned(
                request["argv"], timeout=request["timeout"],
                input_text=request["input_text"], capture=request["capture"],
                inherit_process_group=True, capture_limits=tuple(request["limits"]),
            )
            response.update(
                kind="completed", returncode=result.returncode,
                stdout=result.stdout, stderr=result.stderr,
            )
        except OutputLimitExceeded as error:
            response.update(kind="output-limit", stream=error.stream, limit=error.limit)
        except subprocess.TimeoutExpired:
            response["kind"] = "timeout"
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
