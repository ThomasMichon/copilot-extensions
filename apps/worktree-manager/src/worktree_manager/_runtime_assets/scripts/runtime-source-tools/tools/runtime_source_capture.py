"""Bounded native compile capture inside the installer's existing process owner."""
from __future__ import annotations

import base64
from contextlib import ExitStack
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Protocol, TypedDict

_CREATION_FLAGS = 0
if sys.platform == "win32":
    _CREATION_FLAGS = subprocess.CREATE_NO_WINDOW


class CaptureTransaction(Protocol):
    @property
    def argv(self) -> Sequence[str]: ...

    @property
    def cwd(self) -> Path: ...

    @property
    def environment(self) -> Mapping[str, str]: ...


class CaptureRequest(TypedDict):
    argv: list[str]
    cwd: str
    max_stdout: int
    max_stderr: int
    deadline: float


class CaptureResponse(TypedDict):
    status: str
    stdout: str
    stderr: str


def capture(
    transaction: CaptureTransaction, root: Path, *,
    max_stdout: int, max_stderr: int, deadline: float,
) -> tuple[bytes, bytes]:
    from installer_engine_process import run_owned
    from installer_wheel_inputs import _check_deadline, _ordinary
    from installer_wheel_snapshot import _open, _unchanged
    from runtime_profile import ProfileError

    _check_deadline(deadline)
    if any(type(value) is not int or value <= 0 for value in (max_stdout, max_stderr)):
        raise ProfileError("source capture needs positive stream byte budgets")
    previous = sys.path.copy()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "agent-procutil" / "src"))
    try:
        import agent_procutil
    finally:
        sys.path[:] = previous

    source = root / "capture.py"
    before = _ordinary(Path(__file__))
    with _open(Path(__file__)) as stream:
        opened = _unchanged(Path(__file__), before, stream)
        content = stream.read((1 << 20) + 1)
        _unchanged(Path(__file__), before, stream, opened=opened)
    if len(content) > 1 << 20:
        raise ProfileError("source capture worker exceeds its control budget")
    source.write_bytes(content)
    control = root / "capture.json"
    request = json.dumps({
        "argv": list(transaction.argv), "cwd": str(transaction.cwd),
        "max_stdout": max_stdout, "max_stderr": max_stderr, "deadline": deadline,
    }, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(request) > 1 << 20:
        raise ProfileError("source capture request exceeds its control budget")
    control.write_bytes(request)
    executable, environment = sys.executable, dict(transaction.environment)
    if os.name == "nt" and Path(executable).name.lower() == "pythonw.exe":
        executable, extra = agent_procutil.direct_python(executable)
        environment.pop("__PYVENV_LAUNCHER__", None)
        environment.update(extra)
    try:
        with ExitStack() as held:
            held.enter_context(_open(source))
            custody = held.enter_context(_open(control))
            result = run_owned(
                [executable, "-I", "-S", "-B", str(source), str(control)],
                cwd=root, env=environment, timeout=deadline - time.monotonic(),
                deadline=deadline, procutil=agent_procutil, construction_fd=custody.fileno(),
            )
        if len(result.stdout) > 4 * (max_stdout + max_stderr) + 1024 or result.stderr:
            raise ProfileError("source capture returned invalid bounded output")
        response = json.loads(result.stdout)
        if response.get("status") != "ok":
            raise ProfileError("source compile failed or exceeded its capture budget")
        output = base64.b64decode(response["stdout"], validate=True)
        diagnostic = base64.b64decode(response["stderr"], validate=True)
        if len(output) > max_stdout or len(diagnostic) > max_stderr:
            raise ProfileError("source capture exceeded its selected byte budgets")
        return output, diagnostic
    except ProfileError:
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        raise ProfileError("source compile capture failed") from None


def _capture(request: CaptureRequest) -> CaptureResponse | dict[str, str]:
    buffers = [bytearray(), bytearray()]
    limits = [request["max_stdout"], request["max_stderr"]]
    refused = threading.Event()

    def read(stream: BinaryIO, index: int) -> None:
        try:
            with stream:
                while True:
                    chunk = stream.read(min(65536, limits[index] - len(buffers[index]) + 1))
                    if not chunk:
                        return
                    if len(buffers[index]) + len(chunk) > limits[index]:
                        refused.set()
                        return
                    buffers[index].extend(chunk)
        except Exception:
            refused.set()

    # Inherit the canonical owner's Job/process group; never detach this child.
    process = subprocess.Popen(
        request["argv"], cwd=request["cwd"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=_CREATION_FLAGS,
    )
    assert process.stdout is not None and process.stderr is not None
    threads = [threading.Thread(target=read, args=(stream, index), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads) or process.poll() is None:
        if refused.wait(0.01) or time.monotonic() >= request["deadline"]:
            return {"status": "refused"}
    if refused.is_set() or process.returncode:
        return {"status": "refused"}
    return {"status": "ok", **{
        name: base64.b64encode(value).decode("ascii")
        for name, value in zip(("stdout", "stderr"), buffers)
    }}


if __name__ == "__main__":
    try:
        with open(sys.argv[1], "rb") as stream:
            request = json.loads(stream.read((1 << 20) + 1))
        response = _capture(request)
    except Exception:
        response = {"status": "refused"}
    print(json.dumps(response, separators=(",", ":")), flush=True)
    # Early refusal must leave settlement to the outer canonical owner, not
    # wait for reader threads or inherited pipes before that owner can act.
    os._exit(0)
