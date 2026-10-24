"""Bounded installer children, with ownership surviving abrupt caller death."""

from __future__ import annotations

import logging
import importlib.util
import json
import math
import os
import select
import signal
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType


_RETENTION_SCOPE_ENV = "COPILOT_EXTENSIONS_INSTALLER_RETENTION_SCOPE"
_SCOPE_PACKET_LIMIT = 256 * 1024


class _ScopeDeadlineExpired(TimeoutError):
    pass


def _scope_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Installer retention packet has a duplicate field")
        value[key] = item
    return value


def _scope_packet(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    stream = temporary.open("x", encoding="utf-8")
    try:
        with stream:
            json.dump(value, stream, ensure_ascii=False)
        # Windows rename refuses an existing destination; no foreign packet is replaced.
        os.rename(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_scope_packet(path: Path) -> dict:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & 0x400
    ):
        raise RuntimeError("Installer retention packet is not an ordinary file")
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # Atomic rename can still hold DELETE access when the name becomes visible.
        handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        stream = os.fdopen(descriptor, "rb")
    else:
        stream = path.open("rb")
    with stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise RuntimeError("Installer retention packet identity changed")
        data = stream.read(_SCOPE_PACKET_LIMIT + 1)
    if len(data) > _SCOPE_PACKET_LIMIT:
        raise RuntimeError("Installer retention packet exceeds its bound")
    value = json.loads(data, object_pairs_hook=_scope_object)
    if not isinstance(value, dict):
        raise RuntimeError("Installer retention packet is not an object")
    return value


def _register_retention(context: str, root: Path, version: str, timeout: float) -> float:
    started = time.monotonic()
    scope = json.loads(context, object_pairs_hook=_scope_object)
    if (
        not isinstance(scope, dict)
        or set(scope) != {"version", "root", "directory", "nonce", "guardian_pid"}
        or type(scope["version"]) is not int or scope["version"] != 1
        or any(not isinstance(scope[key], str) for key in ("root", "directory", "nonce"))
        or len(scope["nonce"]) != 32
        or type(scope["guardian_pid"]) is not int or scope["guardian_pid"] <= 0
        or Path(scope["root"]).resolve() != root.resolve()
    ):
        raise ChildLaunchError("Installer child has invalid retention-scope evidence")
    directory = Path(scope["directory"])
    if not directory.is_absolute():
        raise ChildLaunchError("Installer retention scope is not an absolute private directory")
    metadata = directory.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise ChildLaunchError("Installer retention scope is not an ordinary private directory")
    request = directory / (uuid.uuid4().hex + ".request")
    acknowledgement = request.with_suffix(".ack")
    packet = {"nonce": scope["nonce"], "root": str(root.resolve()), "runtime_version": version}
    _scope_packet(request, packet)
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise ChildLaunchError("Installer retention registration timed out before child launch")
            try:
                observed = _read_scope_packet(acknowledgement)
            except FileNotFoundError:
                time.sleep(min(0.02, remaining))
                continue
            if observed.get("retained") is not True or observed != {**packet, "retained": True}:
                raise ChildLaunchError("Installer guardian did not prove the requested retention")
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise ChildLaunchError("Installer retention registration exhausted the child deadline")
            return remaining
    finally:
        request.unlink(missing_ok=True)
        acknowledgement.unlink(missing_ok=True)


class _RetentionScope:
    """The finite guardian co-retains each constructor target before writers start."""

    def __init__(self, directory: Path, root: Path, runtime: ModuleType, custody: ExitStack):
        self.directory = directory.absolute()
        self.directory.mkdir(mode=0o700, exist_ok=False)
        self.directory = self.directory.resolve(strict=True)
        self.root = root.resolve()
        self.runtime = runtime
        self.custody = custody
        self.nonce = uuid.uuid4().hex
        self.versions: set[str] = set()
        self.handled: set[str] = set()

    def environment(self) -> str:
        return json.dumps({
            "version": 1, "root": str(self.root),
            "directory": str(self.directory), "nonce": self.nonce,
            "guardian_pid": os.getpid(),
        }, ensure_ascii=False)

    def retain_requests(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with os.scandir(self.directory) as entries:
            pending = sorted(Path(entry.path) for entry in entries if entry.name.endswith(".request"))
        for path in pending:
            if path.name in self.handled:
                continue
            try:
                packet = _read_scope_packet(path)
            except FileNotFoundError:
                continue
            if (
                set(packet) != {"nonce", "root", "runtime_version"}
                or packet["nonce"] != self.nonce
                or not isinstance(packet["root"], str)
                or Path(packet["root"]).resolve() != self.root
                or not isinstance(packet["runtime_version"], str)
            ):
                raise RuntimeError("Installer guardian refused invalid retention registration")
            version = packet["runtime_version"]
            self.runtime.version_dir(self.root, version)
            if version.lower() not in self.versions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _ScopeDeadlineExpired(
                        "Installer retention registration exhausted the owner deadline"
                    )
                self.custody.enter_context(
                    self.runtime.retention_lease(self.root, version, timeout=remaining)
                )
                self.versions.add(version.lower())
            _scope_packet(path.with_suffix(".ack"), {**packet, "retained": True})
            self.handled.add(path.name)


class ChildLaunchError(RuntimeError):
    """An owned child could not start; no child cleanup was attempted."""


class ChildTimeoutExpired(subprocess.TimeoutExpired):
    """The child's deadline expired and every owned writer has settled."""


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
    procutil: ModuleType, construction_fd: int,
    runtime_root: Path | None = None, runtime_version: str | None = None,
    runtime_path: Path | None = None,
    allow_breakaway: bool = False,
    retention_scope: Path | None = None,
) -> subprocess.CompletedProcess:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("installer child timeout must be finite and positive")
    if type(allow_breakaway) is not bool:
        raise ValueError("installer child breakaway policy must be a boolean")
    if retention_scope is not None and (
        os.name != "nt" or runtime_root is None or runtime_version is None or runtime_path is None
    ):
        raise ValueError("installer retention scope requires complete Windows Job custody")
    if _RETENTION_SCOPE_ENV in env:
        if os.name != "nt" or retention_scope is not None:
            raise ChildLaunchError("Installer retention scopes cannot be nested or cross platforms")
        if runtime_root is None or runtime_version is None or runtime_path is None:
            raise ChildLaunchError("Scoped installer children require complete runtime custody")
        timeout = _register_retention(
            env[_RETENTION_SCOPE_ENV], runtime_root, runtime_version, timeout,
        )
        env = dict(env)
        env.pop(_RETENTION_SCOPE_ENV)
    if any(value is not None for value in (runtime_root, runtime_version, runtime_path)):
        if not all(value is not None for value in (runtime_root, runtime_version, runtime_path)):
            raise ValueError("installer retained child requires complete runtime custody")
        if os.name == "nt":
            return _retained_windows(
                argv, cwd=cwd, env=env, timeout=timeout, procutil=procutil,
                root=runtime_root, version=runtime_version, runtime_path=runtime_path,
                allow_breakaway=allow_breakaway,
                retention_scope=retention_scope,
            )
    options = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   encoding="utf-8", errors="replace")
    timeout_error = None
    if os.name == "nt":
        # An explicit application path avoids MAX_PATH command-token parsing.
        try:
            process, job = procutil.spawn_sync_in_kill_on_close_job(
                argv, require_job=True, allow_breakaway=allow_breakaway,
                executable=argv[0],
                **procutil.no_window_kwargs(), **options,
            )
        except procutil.SyncProcessLaunchError as error:
            raise ChildLaunchError(f"Installer child could not start: {error}") from error
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            timeout_error = error
        finally:
            try:
                _settle(lambda: job.terminate_and_wait(10))
            finally:
                _settle(lambda: process.wait(timeout=10))
    else:
        owner_read, owner_write = os.pipe()
        try:
            # The guardian retains the custody descriptor until all children
            # exit, including after SIGKILL closes the caller's liveness pipe.
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", str(Path(__file__).resolve()),
                     "--owner-fd", str(owner_read), "--", *argv],
                    pass_fds=(owner_read, construction_fd), start_new_session=True, **options,
                )
            except OSError as error:
                raise ChildLaunchError(f"Installer child could not start: {error}") from error
            os.close(owner_read)
            owner_read = -1
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                timeout_error = error
            finally:
                os.close(owner_write)
                owner_write = -1
                _settle(lambda: process.communicate(timeout=30))
        finally:
            if owner_read >= 0:
                os.close(owner_read)
            if owner_write >= 0:
                os.close(owner_write)
    if timeout_error is not None:
        # Read both captured streams only after every owned writer has settled.
        stdout, stderr = process.communicate(timeout=10)
        raise ChildTimeoutExpired(
            timeout_error.cmd, timeout_error.timeout, output=stdout, stderr=stderr,
        ) from timeout_error
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    result.check_returncode()
    return result


def _retained_windows(
    argv, *, cwd, env, timeout, procutil, root, version, runtime_path, allow_breakaway,
    retention_scope,
):
    import msvcrt

    reader, writer = os.pipe()
    process = None
    try:
        guardian_python = sys._base_executable if retention_scope is not None else sys.executable
        handle = msvcrt.get_osfhandle(reader)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        os.set_handle_inheritable(handle, True)
        try:
            try:
                process = subprocess.Popen(
                    [guardian_python, "-I", *(["-S"] if retention_scope is not None else []),
                     "-B", str(Path(__file__).resolve()),
                     "--owner-handle", str(handle), "--runtime-root", str(root),
                     "--runtime-version", version, "--runtime-path", str(runtime_path),
                     "--procutil-path", str(Path(procutil.__file__).resolve()),
                     "--timeout", str(timeout),
                     *(["--retention-scope", str(retention_scope)] if retention_scope is not None else []),
                     *(["--allow-breakaway"] if allow_breakaway else []), "--", *argv],
                    cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                    startupinfo=startup, close_fds=True, executable=guardian_python,
                    **(
                        procutil.windowless_daemon_kwargs(breakaway=True)
                        if retention_scope is not None else procutil.no_window_kwargs()
                    ),
                )
            except OSError as error:
                raise ChildLaunchError(f"Installer guardian could not start: {error}") from error
        finally:
            os.set_handle_inheritable(handle, False)
        os.close(reader)
        reader = -1
        try:
            stdout, stderr = process.communicate(timeout=timeout + 30)
        finally:
            os.close(writer)
            writer = -1
            # The guardian releases retention only after its Job has no writers.
            _settle(lambda: process.communicate(timeout=30))
        if process.returncode != 0:
            if stderr:
                print(stderr, end="", file=sys.stderr, flush=True)
            raise RuntimeError("installer retention guardian did not confirm settlement")
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
        if outcome["timed_out"]:
            raise ChildTimeoutExpired(
                argv, timeout, output=outcome["stdout"], stderr=outcome["stderr"],
            )
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


def _load_resource(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("installer guardian resource could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _supervise_windows(args, argv):
    import ctypes
    from ctypes import wintypes

    runtime = _load_resource("installer_guardian_runtime", args.runtime_path)
    procutil = _load_resource("installer_guardian_procutil", args.procutil_path)
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

    try:
        with ExitStack() as custody:
            custody.enter_context(runtime.retention_lease(Path(args.runtime_root), args.runtime_version))
            scope = (
                _RetentionScope(args.retention_scope, Path(args.runtime_root), runtime, custody)
                if args.retention_scope is not None else None
            )
            environment = dict(os.environ)
            environment.pop(_RETENTION_SCOPE_ENV, None)
            if scope is not None:
                scope.versions.add(args.runtime_version.lower())
                environment[_RETENTION_SCOPE_ENV] = scope.environment()
            if disconnected():
                raise RuntimeError("installer caller exited before child execution")
            try:
                process, job = procutil.spawn_sync_in_kill_on_close_job(
                    argv, require_job=True, allow_breakaway=args.allow_breakaway,
                    executable=argv[0],
                    env=environment,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", **procutil.no_window_kwargs(),
                )
            except procutil.SyncProcessLaunchError as error:
                return {
                    "returncode": 1, "stdout": "", "stderr": "",
                    "timed_out": False, "launch_error": str(error),
                }
            deadline = time.monotonic() + args.timeout
            timed_out = False
            try:
                while True:
                    if disconnected():
                        raise RuntimeError("installer caller exited during child execution")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    if scope is not None:
                        try:
                            scope.retain_requests(remaining)
                        except _ScopeDeadlineExpired:
                            timed_out = True
                            break
                    try:
                        stdout, stderr = process.communicate(timeout=min(remaining, 0.1))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                try:
                    _settle(lambda: job.terminate_and_wait(10))
                finally:
                    _settle(lambda: process.wait(timeout=10))
            stdout, stderr = process.communicate(timeout=10)
            return {
                "returncode": process.returncode, "stdout": stdout,
                "stderr": stderr, "timed_out": timed_out, "launch_error": None,
            }
    finally:
        kernel.CloseHandle(args.owner_handle)


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
    parser.add_argument("--runtime-path")
    parser.add_argument("--procutil-path")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--allow-breakaway", action="store_true")
    parser.add_argument("--retention-scope", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command[0] != "--" or len(args.command) < 2:
        parser.error("a child argv after -- is required")
    if args.retention_scope is not None and args.owner_handle is None:
        parser.error("retention scope requires a Windows retained Job guardian")
    if args.owner_handle is not None:
        if os.name != "nt" or not all((
            args.runtime_root, args.runtime_version, args.runtime_path, args.procutil_path,
        )) or args.timeout is None or not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error("Windows guardian requires complete runtime custody and a finite timeout")
        print(json.dumps(_supervise_windows(args, args.command[1:])), flush=True)
        raise SystemExit(0)
    raise SystemExit(_supervise(args.owner_fd, args.command[1:]))
