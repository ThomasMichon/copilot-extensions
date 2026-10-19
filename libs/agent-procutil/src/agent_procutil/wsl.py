"""Opt-in, caller-bounded Windows commands from WSL; never a daemon launcher."""

from __future__ import annotations

import json
import math
import os
import platform
import selectors
import subprocess
import threading
import time
from pathlib import Path

from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import run_owned

_MAX_REQUEST = 2 * 1024 * 1024
_MAX_RESPONSE = 16 * 1024 * 1024
_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv[1]);"
    "from agent_procutil.wsl import _windows_main;_windows_main()"
)


def _owner_command(windows_python: str, package_root: str) -> list[str]:
    return [windows_python, "-I", "-S", "-u", "-c", _BOOTSTRAP, package_root]


def _request(argv: list[str], timeout: float, input_text: str | None, capture: bool) -> dict:
    if (
        not isinstance(argv, list) or not argv or not argv[0]
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
    ):
        raise ValueError("argv must be a nonempty list of NUL-free strings")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if input_text is not None and not isinstance(input_text, str):
        raise TypeError("input_text must be text or None")
    if type(capture) is not bool:
        raise TypeError("capture must be a boolean")
    request = {
        "version": 1, "argv": argv, "timeout": timeout,
        "input_text": input_text, "capture": capture,
    }
    if len(json.dumps(request, ensure_ascii=True).encode("ascii")) + 1 > _MAX_REQUEST:
        raise ValueError("Windows command request exceeds its byte bound")
    return request


def _exchange(process: subprocess.Popen, request: bytes, deadline: float) -> bytes:
    output = bytearray()
    errors = bytearray()
    offset = 0
    with selectors.DefaultSelector() as selector:
        for stream, role in (
            (process.stdin, "request"), (process.stdout, "response"), (process.stderr, "error"),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(
                stream, selectors.EVENT_WRITE if role == "request" else selectors.EVENT_READ, role,
            )
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Windows command deadline expired")
            for key, _mask in selector.select(remaining):
                try:
                    if key.data == "request":
                        offset += os.write(key.fd, request[offset:offset + 65536])
                        if offset == len(request):
                            # Keep this pipe open: EOF is the Windows owner's death signal.
                            selector.unregister(key.fileobj)
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            (output if key.data == "response" else errors).extend(chunk)
                            if len(output) + len(errors) > _MAX_RESPONSE:
                                raise RuntimeError("Windows owner response exceeds its byte bound")
                except BlockingIOError:
                    continue
    return bytes(output)


def run_windows_owned(
    argv: list[str], *, windows_python: str, timeout: float,
    input_text: str | None = None, capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a Windows command through an explicit, WSL-invokable pythonw.exe.

    The selected Windows interpreter must be able to read this installed package
    through WSL's normal Windows path mapping. No Windows runtime is discovered,
    installed or copied. Existing native Windows/POSIX spawn APIs are unchanged.
    """
    request = _request(argv, timeout, input_text, capture)
    if os.name != "posix" or "microsoft" not in platform.release().lower():
        raise RuntimeError("Windows interop ownership requires WSL")
    python = Path(windows_python)
    if not python.is_absolute() or python.name.lower() != "pythonw.exe" or not python.is_file():
        raise ValueError("windows_python must name an existing absolute GUI pythonw.exe")
    deadline = time.monotonic() + timeout
    mapped = run_owned(
        ["wslpath", "-w", str(Path(__file__).resolve().parent.parent)],
        timeout=timeout,
    )
    if mapped.returncode or not mapped.stdout or "\n" in mapped.stdout.rstrip("\r\n"):
        raise RuntimeError("Installed process utility has no Windows-accessible path")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv, timeout)
    request["timeout"] = remaining
    payload = (json.dumps(request, ensure_ascii=True) + "\n").encode("ascii")
    process = subprocess.Popen(  # noqa: S603 - explicit GUI interpreter, no shell
        _owner_command(str(python), mapped.stdout.rstrip("\r\n")),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0, **no_window_kwargs(),
    )
    try:
        try:
            response = _exchange(process, payload, deadline)
        except TimeoutError as error:
            raise subprocess.TimeoutExpired(argv, timeout) from error
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait(timeout=3)
            raise RuntimeError("Windows owner exit is unconfirmed; do not replay") from error
        finally:
            process.stdout.close()
            process.stderr.close()
    return _completed(argv, timeout, response, process.returncode)


def _completed(
    argv: list[str], timeout: float, response: bytes, owner_returncode: int,
) -> subprocess.CompletedProcess:
    try:
        result = json.loads(response)
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Windows owner returned no complete result; do not replay") from error
    if (
        not isinstance(result, dict)
        or type(result.get("version")) is not int or result["version"] != 1
    ):
        raise RuntimeError("Unsupported Windows owner result; do not replay")
    if result.get("error") == "timeout":
        raise subprocess.TimeoutExpired(argv, timeout)
    if result.get("error"):
        raise RuntimeError("Windows owner refused or failed execution; do not replay")
    if (
        set(result) != {"version", "returncode", "stdout", "stderr"}
        or type(result["returncode"]) is not int
        or any(result[name] is not None and not isinstance(result[name], str)
               for name in ("stdout", "stderr"))
        or owner_returncode != 0
    ):
        raise RuntimeError("Windows owner completion is unconfirmed; do not replay")
    return subprocess.CompletedProcess(
        argv, result["returncode"], result["stdout"], result["stderr"],
    )


def _read_request() -> dict:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = os.read(0, 65536)
        if not chunk:
            raise ValueError("Caller disconnected before request completion")
        data.extend(chunk)
        if len(data) > _MAX_REQUEST or b"\n" in data[:-1]:
            raise ValueError("Invalid bounded request framing")
    value = json.loads(data)
    if not isinstance(value, dict) or set(value) != {
        "version", "argv", "timeout", "input_text", "capture",
    } or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported request")
    return _request(value["argv"], value["timeout"], value["input_text"], value["capture"])


def _emit(result: dict) -> None:
    data = memoryview(json.dumps(result, ensure_ascii=True).encode("ascii"))
    if len(data) > _MAX_RESPONSE:
        data = memoryview(b'{"version":1,"error":"response-limit"}')
    while data:
        data = data[os.write(1, data):]


def _watch_caller() -> None:
    try:
        os.read(0, 1)
    except OSError:
        pass
    # Exiting closes the strict self-Job even during target creation/assignment.
    os._exit(125)


def _windows_main() -> None:
    from agent_procutil import _assign_suspended_to_kill_on_close_job

    if os.name != "nt":
        raise RuntimeError("Windows owner must execute on Windows")
    owner_job = _assign_suspended_to_kill_on_close_job(os.getpid(), allow_breakaway=False)
    if owner_job is None:
        _emit({"version": 1, "error": "owner-job-unavailable"})
        os._exit(1)
    try:
        request = _read_request()
        threading.Thread(target=_watch_caller, name="wsl-caller-lifetime", daemon=True).start()
        result = run_owned(
            request["argv"], timeout=request["timeout"],
            input_text=request["input_text"], capture=request["capture"],
        )
        _emit({
            "version": 1, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
    except subprocess.TimeoutExpired:
        _emit({"version": 1, "error": "timeout"})
    except (OSError, ValueError, RuntimeError):
        _emit({"version": 1, "error": "execution-failed"})
    # Do not close a self-Job from a Python finalizer before the result is flushed.
    # Kernel process exit releases it, including any interrupted nested spawn.
    os._exit(0)
