"""Bounded installer children, with optional retained construction custody."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import logging
from importlib import import_module
import json
import math
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import ModuleType
import uuid


_GUARDIAN_MEMBERS = (
    "_installer_process.py", "_versioned_runtime.py",
    "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_source.py", "versioned_runtime_publication.py",
    "_construction_procutil/__init__.py", "_construction_procutil/_job_scopes.py",
    "_construction_procutil/_windows_job_types.py",
    "_construction_procutil/streaming.py", "_construction_procutil/_output_relay.py",
)
_GUARDIAN_SOURCE_LIMIT = 1024 * 1024
_GUARDIAN_PACKET_LIMIT = 12 * 1024 * 1024
_guardian_sources: dict[str, bytes] | None = globals().get("_guardian_sources")
_GUARDIAN_BOOTSTRAP = (
    "import base64,json,sys\n"
    "def _pairs(values):\n"
    "    result=dict(values)\n"
    "    if len(result)!=len(values): raise ValueError('duplicate guardian input member')\n"
    "    return result\n"
    f"_raw=sys.stdin.buffer.read({_GUARDIAN_PACKET_LIMIT + 1})\n"
    f"if len(_raw)>{_GUARDIAN_PACKET_LIMIT}: raise ValueError('guardian input exceeds bound')\n"
    "_encoded=json.loads(_raw,object_pairs_hook=_pairs)\n"
    f"if type(_encoded) is not dict or set(_encoded)!=set({_GUARDIAN_MEMBERS!r}): "
    "raise ValueError('guardian input has invalid members')\n"
    "if any(type(v) is not str for v in _encoded.values()): "
    "raise ValueError('guardian input has invalid values')\n"
    "_guardian_sources={k:base64.b64decode(v,validate=True) for k,v in _encoded.items()}\n"
    f"if any(not 0<len(v)<={_GUARDIAN_SOURCE_LIMIT} for v in _guardian_sources.values()): "
    "raise ValueError('guardian source exceeds bound')\n"
    "__file__='_installer_process.py'\n"
    "exec(compile(_guardian_sources[__file__],__file__,'exec'))\n"
)


class ChildLaunchError(RuntimeError):
    """An owned child could not start; no child cleanup was attempted."""


class ChildTimeoutExpired(subprocess.TimeoutExpired):
    """The child's deadline expired and every owned writer has settled."""


class _OwnerDisconnected(RuntimeError):
    pass


def _output_capture(process, procutil, on_output, output_fds=None, *, input_bytes=None):
    capture = import_module(procutil.__name__ + ".streaming").SyncProcessOutput
    options = {"input_bytes": input_bytes} if input_bytes is not None else {}
    return capture(process, on_output=on_output, output_fds=output_fds, **options)


def _settle(operation: Callable[[], object]) -> None:
    failure: BaseException | None = None
    while True:
        try:
            if failure is not None:
                time.sleep(1)
            operation()
            break
        except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit) as error:
            if failure is None:
                failure = error
            logging.getLogger(__name__).error(
                "Installer teardown is unsettled; retaining construction custody: %s", error,
            )
    if failure is not None:
        raise failure


def run_owned(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
    procutil: ModuleType | None, construction_fd: int | None,
    runtime_root: Path | None = None, runtime_version: str | None = None,
    runtime_path: Path | None = None,
    deadline: float | None = None,
    on_output: Callable[[str, bytes], None] | None = None,
    output_fds: Mapping[str, int] | None = None,
    guardian_sources: dict[str, bytes] | None = None,
) -> subprocess.CompletedProcess:
    """Settle owned writers before returning or propagating a timeout.

    POSIX callers may omit procutil. With no construction descriptor, storage
    custody remains caller-held only; abrupt caller-death retention is not
    established by this mode.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("installer child timeout must be finite and positive")
    if os.name == "nt" and procutil is None:
        raise ValueError("Windows installer children require native process utilities")
    if (on_output is not None or output_fds) and procutil is None:
        raise ValueError("incremental installer output requires native process utilities")
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ValueError("installer child deadline must be finite")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ChildTimeoutExpired(argv, 0, output="", stderr="")
        timeout = min(timeout, remaining)
    if any(value is not None for value in (runtime_root, runtime_version, runtime_path)):
        if not all(value is not None for value in (runtime_root, runtime_version, runtime_path)):
            raise ValueError("installer retained child requires complete runtime custody")
        if os.name == "nt":
            return _retained_windows(
                argv, cwd=cwd, env=env, timeout=timeout, procutil=procutil,
                root=runtime_root, version=runtime_version, runtime_path=runtime_path,
                on_output=on_output,
                output_fds=output_fds,
                guardian_sources=guardian_sources,
                **({"deadline": deadline} if deadline is not None else {}),
            )
    options = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   encoding="utf-8", errors="replace")
    timeout_error = None
    capture = None

    def communicate():
        if capture is None:
            return process.communicate(
                timeout=timeout if deadline is None else max(0, min(timeout, deadline - time.monotonic())),
            )
        capture.wait(timeout, deadline=deadline, wait_for_eof=False)
        return capture.output()

    def finish(grace):
        return capture.finish(grace) if capture is not None else process.communicate(timeout=grace)

    if os.name == "nt":
        # An explicit application path avoids MAX_PATH command-token parsing.
        try:
            process, job = procutil.spawn_sync_in_kill_on_close_job(
                argv, require_job=True, allow_breakaway=False,
                executable=argv[0],
                **procutil.no_window_kwargs(), **options,
            )
        except procutil.SyncProcessLaunchError as error:
            raise ChildLaunchError(f"Installer child could not start: {error}") from error
        try:
            if on_output is not None or output_fds:
                capture = _output_capture(process, procutil, on_output, output_fds)
            stdout, stderr = communicate()
        except subprocess.TimeoutExpired as error:
            timeout_error = error
        finally:
            try:
                _settle(lambda: job.terminate_and_wait(10))
            finally:
                _settle(lambda: process.wait(timeout=10))
                stdout, stderr = _settle_output(finish, 10, capture=capture)
    else:
        owner_read, owner_write = os.pipe()
        try:
            # The guardian retains the custody descriptor until all children
            # exit, including after SIGKILL closes the caller's liveness pipe.
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", str(Path(__file__).resolve()),
                     "--owner-fd", str(owner_read), "--", *argv],
                    pass_fds=(owner_read,) if construction_fd is None else (owner_read, construction_fd),
                    start_new_session=True, **options,
                )
            except OSError as error:
                raise ChildLaunchError(f"Installer child could not start: {error}") from error
            os.close(owner_read)
            owner_read = -1
            try:
                if on_output is not None or output_fds:
                    capture = _output_capture(process, procutil, on_output, output_fds)
                stdout, stderr = communicate()
            except subprocess.TimeoutExpired as error:
                timeout_error = error
            finally:
                os.close(owner_write)
                owner_write = -1
                stdout, stderr = _settle_output(finish, 30, capture=capture)
        finally:
            if owner_read >= 0:
                os.close(owner_read)
            if owner_write >= 0:
                os.close(owner_write)
    if timeout_error is None and deadline is not None and time.monotonic() >= deadline:
        timeout_error = subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
    if timeout_error is not None:
        # Read both captured streams only after every owned writer has settled.
        raise ChildTimeoutExpired(
            timeout_error.cmd, timeout_error.timeout, output=stdout, stderr=stderr,
        ) from timeout_error
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    result.check_returncode()
    return result


def _settle_output(finish, grace, *, capture=None):
    result = []
    _settle(lambda: result.append(finish(grace)))
    if capture is not None:
        capture.deliver()
    return result[0]


def _guardian_packet(
    procutil: ModuleType, runtime_path: Path, sources: dict[str, bytes] | None,
) -> str:
    if sources is None:
        paths = {
            "_installer_process.py": Path(__file__),
            "_versioned_runtime.py": runtime_path,
            **{name: runtime_path.with_name(name) for name in _GUARDIAN_MEMBERS[2:6]},
            "_construction_procutil/__init__.py": Path(procutil.__file__),
            "_construction_procutil/_job_scopes.py": Path(procutil.__file__).with_name("_job_scopes.py"),
            "_construction_procutil/_windows_job_types.py": Path(procutil.__file__).with_name("_windows_job_types.py"),
            "_construction_procutil/streaming.py": Path(procutil.__file__).with_name("streaming.py"),
            "_construction_procutil/_output_relay.py": Path(procutil.__file__).with_name("_output_relay.py"),
        }
        sources = {}
        for name, path in paths.items():
            with path.open("rb") as incoming:
                sources[name] = incoming.read(_GUARDIAN_SOURCE_LIMIT + 1)
    if (
        type(sources) is not dict or set(sources) != set(_GUARDIAN_MEMBERS)
        or any(type(value) is not bytes or not 0 < len(value) <= _GUARDIAN_SOURCE_LIMIT
               for value in sources.values())
    ):
        raise ValueError("installer guardian requires the exact bounded source closure")
    return json.dumps({name: base64.b64encode(value).decode("ascii") for name, value in sources.items()})


@contextmanager
def _load_guardian_resources(
    sources: dict[str, bytes],
) -> Iterator[tuple[ModuleType, ModuleType]]:
    if (
        type(sources) is not dict or set(sources) != set(_GUARDIAN_MEMBERS)
        or any(type(value) is not bytes or not 0 < len(value) <= _GUARDIAN_SOURCE_LIMIT
               for value in sources.values())
    ):
        raise ValueError("installer guardian requires the exact bounded source closure")
    prefix = "installer_guardian_" + uuid.uuid4().hex
    runtime = ModuleType(prefix + "_runtime")
    procutil = ModuleType(prefix + "_procutil")
    scopes = ModuleType(procutil.__name__ + "._job_scopes")
    job_types = ModuleType(procutil.__name__ + "._windows_job_types")
    registered: list[ModuleType] = []

    def register(module: ModuleType, member: str) -> None:
        module.__file__ = member
        sys.modules[module.__name__] = module
        registered.append(module)

    def execute(module: ModuleType, member: str) -> None:
        exec(compile(sources[member], member, "exec"), module.__dict__)

    try:
        register(runtime, "_versioned_runtime.py")
        runtime._retained_runtime_components = {}
        for member in _GUARDIAN_MEMBERS[2:6]:
            component = member.removesuffix(".py")
            module = ModuleType(runtime.__name__ + "_" + component)
            register(module, member)
            execute(module, member)
            runtime._retained_runtime_components[component] = module
        runtime._retained_runtime_components["versioned_runtime_publication"]._leases = (
            runtime._retained_runtime_components["versioned_runtime_leases"]
        )
        execute(runtime, "_versioned_runtime.py")
        procutil.__package__, procutil.__path__ = procutil.__name__, []
        scopes.__package__ = procutil.__name__
        register(procutil, "_construction_procutil/__init__.py")
        execute(procutil, "_construction_procutil/__init__.py")
        register(scopes, "_construction_procutil/_job_scopes.py")
        execute(scopes, "_construction_procutil/_job_scopes.py")
        job_types.__package__ = procutil.__name__
        register(job_types, "_construction_procutil/_windows_job_types.py")
        execute(job_types, "_construction_procutil/_windows_job_types.py")
        for member in ("_output_relay", "streaming"):
            module = ModuleType(procutil.__name__ + "." + member)
            module.__package__ = procutil.__name__
            resource = "_construction_procutil/" + member + ".py"
            register(module, resource)
            module._retained_source_bytes = sources[resource]
            execute(module, resource)
        yield runtime, procutil
    finally:
        for module in reversed(registered):
            if sys.modules.get(module.__name__) is module:
                del sys.modules[module.__name__]


def _retained_windows(
    argv, *, cwd, env, timeout, procutil, root, version, runtime_path,
    deadline=None, guardian_sources=None, on_output=None, output_fds=None,
):
    import msvcrt

    execution_deadline = time.monotonic() + timeout
    if deadline is not None:
        execution_deadline = min(execution_deadline, deadline)
    packet = _guardian_packet(procutil, runtime_path, guardian_sources)
    reader, writer = os.pipe()
    process = None
    capture = None
    timeout_error = None
    try:
        handle = msvcrt.get_osfhandle(reader)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        os.set_handle_inheritable(handle, True)
        try:
            try:
                if time.monotonic() >= execution_deadline:
                    raise ChildTimeoutExpired(argv, timeout, output="", stderr="")
                process = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-c", _GUARDIAN_BOOTSTRAP,
                     "--owner-handle", str(handle), "--runtime-root", str(root),
                     "--runtime-version", version,
                     "--timeout", str(timeout),
                     *(["--progress-output"] if on_output is not None or output_fds else []),
                     "--deadline", str(execution_deadline), "--", *argv],
                    cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                    startupinfo=startup, close_fds=True, executable=sys.executable,
                    **procutil.no_window_kwargs(),
                )
            except OSError as error:
                raise ChildLaunchError(f"Installer guardian could not start: {error}") from error
        finally:
            os.set_handle_inheritable(handle, False)
        os.close(reader)
        reader = -1
        try:
            if on_output is None and not output_fds:
                stdout, stderr = process.communicate(
                    input=packet, timeout=max(0, execution_deadline - time.monotonic()) + 30,
                )
            else:
                capture = _output_capture(
                    process, procutil,
                    (lambda name, data: on_output(name, data) if name == "stderr" else None)
                    if on_output is not None else None,
                    {"stderr": output_fds["stderr"]} if output_fds and "stderr" in output_fds else None,
                    input_bytes=packet.encode("utf-8"),
                )
                capture.wait(
                    timeout, startup_timeout=timeout + 30,
                    deadline=execution_deadline, wait_for_eof=False,
                )
        except subprocess.TimeoutExpired as error:
            timeout_error = error
        finally:
            os.close(writer)
            writer = -1
            if capture is None and process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
                process.stdin = None
            # The guardian releases retention only after its Job has no writers.
            stdout, stderr = _settle_output(
                capture.finish if capture is not None else lambda grace: process.communicate(timeout=grace),
                30, capture=capture,
            )
        if process.returncode != 0:
            raise RuntimeError(
                f"installer retention guardian did not confirm settlement "
                f"(exit {process.returncode}): {stderr[-4096:]}",
            )
        try:
            outcome = json.loads(stdout)
        except ValueError:
            raise RuntimeError("installer retention guardian returned invalid completion") from None
        if (
            not isinstance(outcome, dict)
            or set(outcome) != {"returncode", "stdout", "stderr", "timed_out", "launch_error"}
            or type(outcome["returncode"]) is not int
            or type(outcome["timed_out"]) is not bool
            or not isinstance(outcome["stdout"], str) or not isinstance(outcome["stderr"], str)
            or (outcome["launch_error"] is not None
                and not isinstance(outcome["launch_error"], str))
        ):
            raise RuntimeError("installer retention guardian returned invalid completion")
        if outcome["launch_error"] is not None:
            raise ChildLaunchError(f"Installer child could not start: {outcome['launch_error']}")
        if timeout_error is not None or outcome["timed_out"] or time.monotonic() >= execution_deadline:
            raise ChildTimeoutExpired(
                argv, timeout, output=outcome["stdout"], stderr=outcome["stderr"],
            ) from timeout_error
        result = subprocess.CompletedProcess(
            argv, outcome["returncode"], outcome["stdout"], outcome["stderr"],
        )
        result.check_returncode()
        return result
    finally:
        if reader >= 0:
            os.close(reader)
        if writer >= 0:
            os.close(writer)


def _supervise_windows(args, argv):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.PeekNamedPipe.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    )
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)

    def disconnected():
        if kernel.PeekNamedPipe(args.owner_handle, None, 0, None, None, None):
            return False
        error = ctypes.get_last_error()
        if error in (109, 233):
            return True
        raise ctypes.WinError(error)

    absolute_deadline = getattr(args, "deadline", None)
    expired = {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True, "launch_error": None}
    try:
        with _load_guardian_resources(_guardian_sources) as (runtime, procutil):
            if absolute_deadline is not None and time.monotonic() >= absolute_deadline:
                return expired
            with runtime.retention_lease(Path(args.runtime_root), args.runtime_version):
                return _run_guardian_child(args, argv, procutil, disconnected, absolute_deadline, expired)
    finally:
        kernel.CloseHandle(args.owner_handle)


def _run_guardian_child(args, argv, procutil, disconnected, absolute_deadline, expired):
    deadline = time.monotonic() + args.timeout
    if absolute_deadline is not None:
        deadline = min(deadline, absolute_deadline)
    if disconnected():
        raise RuntimeError("installer caller exited before child execution")
    if time.monotonic() >= deadline:
        return expired
    try:
        process, job = procutil.spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False, executable=argv[0],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **procutil.no_window_kwargs(),
        )
    except procutil.SyncProcessLaunchError as error:
        return {
            "returncode": 1, "stdout": "", "stderr": "",
            "timed_out": False, "launch_error": str(error),
        }
    timed_out = False
    capture = None
    try:
        if getattr(args, "progress_output", False):
            def observe_owner():
                if disconnected():
                    raise _OwnerDisconnected("installer caller exited during child execution")

            capture = _output_capture(
                process, procutil, None,
                {name: sys.stderr.fileno() for name in ("stdout", "stderr")},
            )
            try:
                capture.wait(
                    args.timeout, deadline=deadline,
                    on_wait=observe_owner, wait_for_eof=False,
                )
            except subprocess.TimeoutExpired:
                timed_out = True
            except _OwnerDisconnected:
                pass
        else:
            while True:
                if disconnected():
                    raise RuntimeError("installer caller exited during child execution")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    stdout, stderr = process.communicate(timeout=min(remaining, 0.1))
                    timed_out = time.monotonic() >= deadline
                    break
                except subprocess.TimeoutExpired:
                    continue
    finally:
        try:
            _settle(lambda: job.terminate_and_wait(10))
        finally:
            _settle(lambda: process.wait(timeout=10))
            stdout, stderr = _settle_output(
                capture.finish if capture is not None else lambda grace: process.communicate(timeout=grace),
                10, capture=capture,
            )
    timed_out = timed_out or time.monotonic() >= deadline
    return {
        "returncode": process.returncode, "stdout": stdout,
        "stderr": stderr, "timed_out": timed_out, "launch_error": None,
    }


def _terminate_group(process: subprocess.Popen) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Darwin reports EPERM for a group whose only members are zombies; the
        # ps check below still refuses to settle while a live member remains.
        pass
    deadline = time.monotonic() + 10
    while True:
        observation = subprocess.run(
            ["ps", "-eo", "pid=,pgid=,stat="],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=True, timeout=5,
        )
        rows = [line.split() for line in observation.stdout.splitlines()]
        if any(len(row) != 3 for row in rows):
            raise RuntimeError("Cannot verify installer process-group teardown")
        try:
            live = any(int(row[1]) == process.pid and not row[2].startswith("Z") for row in rows)
        except ValueError as error:
            raise RuntimeError("Cannot parse installer process-group teardown evidence") from error
        if not live:
            process.wait(timeout=10)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Installer process group still has live writers after termination")
        time.sleep(0.05)


def _child_exited_unreaped(pid: int) -> bool:
    waitid = getattr(os, "waitid", None)
    if waitid is not None:
        return waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    # Python 3.11/3.12 on macOS lack waitid. ps observes zombie state without
    # reaping our direct child, keeping its PID/PGID reserved through cleanup.
    observation = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=5,
    )
    states = observation.stdout.split()
    if len(states) != 1:
        raise RuntimeError("Cannot observe unreaped installer child state")
    return states[0].startswith("Z")


def _supervise(owner_fd: int, argv: list[str]) -> int:
    process = None
    try:
        if select.select([owner_fd], [], [], 0)[0] and not os.read(owner_fd, 1):
            raise RuntimeError("Installer owner exited before child construction")
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
        # Observe exit without reaping the group leader. Its unreaped identity
        # prevents PID/PGID reuse before owned descendants are terminated.
        while True:
            if _child_exited_unreaped(process.pid):
                break
            if select.select([owner_fd], [], [], 0.05)[0] and not os.read(owner_fd, 1):
                raise RuntimeError("Installer owner exited during child construction")
    finally:
        if process is not None:
            _settle(lambda: _terminate_group(process))
        os.close(owner_fd)
    if process is None or process.returncode is None:
        raise RuntimeError("Installer child cleanup returned without an exit status")
    return process.returncode if process.returncode >= 0 else 128 - process.returncode


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    ownership = parser.add_mutually_exclusive_group(required=True)
    ownership.add_argument("--owner-fd", type=int)
    ownership.add_argument("--owner-handle", type=int)
    parser.add_argument("--runtime-root")
    parser.add_argument("--runtime-version")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--deadline", type=float)
    parser.add_argument("--progress-output", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command[0] != "--" or len(args.command) < 2:
        parser.error("a child argv after -- is required")
    if args.deadline is not None and (not math.isfinite(args.deadline) or args.deadline <= 0):
        parser.error("a finite positive monotonic deadline is required")
    if args.owner_handle is not None:
        if os.name != "nt" or not all((
            args.runtime_root, args.runtime_version,
        )) or args.timeout is None or not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error("Windows guardian requires complete runtime custody and a finite timeout")
        print(json.dumps(_supervise_windows(args, args.command[1:])), flush=True)
        raise SystemExit(0)
    raise SystemExit(_supervise(args.owner_fd, args.command[1:]))
