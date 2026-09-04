"""Lifecycle primitives for attributed plugin companion processes."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from agent_procutil import contained_test_mode, detached_kwargs, no_window_kwargs
from plugin_activation import read_json_object, write_json_object_atomic

from .registrations import (
    RegistrationError,
    RegistrationKind,
    validate_companion_config_result,
    validate_companion_health_result,
)

_REPARSE_POINT = 0x400
_RECEIPT_VERSION = 1
_REQUEST_VERSION = 1


class CompanionError(RuntimeError):
    """A confirmed-invalid companion declaration or lifecycle operation."""


class CompanionIndeterminate(RuntimeError):
    """A transient provider, probe, or process-inspection failure."""


@dataclass(frozen=True)
class CompanionResolution:
    """One confirmed active companion runtime."""

    registration: dict
    command: tuple[str, ...]
    stop_command: tuple[str, ...] | None
    health_probe: tuple[str, ...] | None
    cwd: str
    environment: Mapping[str, str]
    startup_timeout: float
    stop_timeout: float
    health_timeout: float


@dataclass(frozen=True)
class CompanionLaunch:
    """A launched or safely recovered process."""

    process: CompanionProcess
    recovered: bool = False


class CompanionProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class CompanionController(Protocol):
    """Supervisor-facing lifecycle boundary."""

    def resolve(
        self, registration: dict, *, machine: str | None, env: str
    ) -> CompanionResolution | None: ...

    def launch(self, resolution: CompanionResolution, *, fingerprint: str) -> CompanionLaunch: ...

    def health(self, resolution: CompanionResolution) -> bool | None: ...

    def stop(self, resolution: CompanionResolution, process: CompanionProcess) -> None: ...

    def retire_crashed(
        self, resolution: CompanionResolution, process: CompanionProcess
    ) -> None: ...

    def reconcile_receipts(self, adopted_registration_ids: set[str]) -> None: ...


def companion_authority_fingerprint(registration: dict) -> str:
    """Identity that authorizes retention of a prior provider result."""
    return json.dumps(
        {
            "id": registration.get("id"),
            "kind": registration.get("kind"),
            "owner": registration.get("owner"),
            "plugin": registration.get("plugin"),
            "runtime_revision": registration.get("runtime_revision"),
            "machine": registration.get("machine"),
            "env": registration.get("env"),
            "spec": registration.get("spec"),
        },
        sort_keys=True,
        default=str,
    )


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _checked_plugin_root(registration: dict) -> Path:
    plugin = registration.get("plugin")
    root_value = plugin.get("root") if isinstance(plugin, Mapping) else None
    if not isinstance(root_value, str) or not root_value:
        raise CompanionError("plugin companion lacks attributed plugin root")
    root = Path(root_value).expanduser()
    try:
        info = root.lstat()
        canonical = root.resolve(strict=True)
    except OSError as exc:
        raise CompanionIndeterminate(f"plugin companion root is unavailable: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise CompanionError("plugin companion root must be a regular non-reparse directory")
    return canonical


def _resolve_command(root: Path, value: object, *, field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(part, str) and part for part in value)
    ):
        raise CompanionError(f"plugin companion '{field}' must be a non-empty argv")
    relative = PurePosixPath(value[0].replace("\\", "/"))
    current = root
    try:
        for part in relative.parts:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise CompanionError(
                    f"plugin companion '{field}' crosses a symlink or reparse point"
                )
        canonical = current.resolve(strict=True)
        canonical.relative_to(root)
        info = canonical.lstat()
    except CompanionError:
        raise
    except (OSError, ValueError) as exc:
        raise CompanionIndeterminate(
            f"plugin companion '{field}' is unavailable beneath its plugin root"
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise CompanionError(f"plugin companion '{field}' target must be a regular file")

    suffix = canonical.suffix.casefold()
    if suffix == ".py":
        prefix = (sys.executable, str(canonical))
    elif suffix == ".sh":
        shell = shutil.which("bash")
        if not shell:
            raise CompanionIndeterminate(f"plugin companion '{field}' requires bash")
        prefix = (shell, str(canonical))
    elif suffix == ".ps1":
        shell = shutil.which("pwsh")
        if shell is None and os.name == "nt":
            fallback = (
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            shell = str(fallback) if fallback.is_file() else None
        if not shell:
            raise CompanionIndeterminate(f"plugin companion '{field}' requires PowerShell")
        prefix = (shell, "-NoProfile", "-NonInteractive", "-File", str(canonical))
    else:
        prefix = (str(canonical),)
    return (*prefix, *value[1:])


def _base_environment(registration: dict) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("AGENT_DISPATCH_")
    }
    environment["COPILOT_COMPANION_ID"] = str(registration.get("id") or "")
    environment["COPILOT_COMPANION_OWNER"] = str(registration.get("owner") or "")
    return environment


def _merge_provider_environment(
    environment: dict[str, str], contributed: object
) -> dict[str, str]:
    if contributed is None:
        return environment
    if not isinstance(contributed, Mapping) or not all(
        isinstance(key, str) and key and isinstance(value, str)
        for key, value in contributed.items()
    ):
        raise CompanionError("companion provider environment must map strings to strings")
    reserved = sorted(
        key
        for key in contributed
        if key.upper().startswith(("AGENT_DISPATCH_", "COPILOT_COMPANION_"))
    )
    if reserved:
        raise CompanionError(f"companion provider environment sets reserved keys: {reserved}")
    return {**environment, **contributed}


def _request(registration: dict, *, machine: str | None, env: str) -> str:
    plugin = registration.get("plugin") or {}
    return json.dumps(
        {
            "schema_version": _REQUEST_VERSION,
            "registration_id": registration.get("id"),
            "plugin": registration.get("owner"),
            "plugin_version": plugin.get("version"),
            "activation_scopes": list(plugin.get("activation_scopes") or []),
            "machine": machine,
            "environment": env,
        }
    )


def _run_captured(
    command: tuple[str, ...],
    *,
    cwd: str,
    environment: Mapping[str, str],
    timeout: float,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = detached_kwargs()
    try:
        process = subprocess.Popen(  # noqa: S603 -- attributed fixed argv
            list(command),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=dict(environment),
            **kwargs,
        )
        try:
            stdout, stderr = process.communicate(input_text, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if contained_test_mode():
                process.kill()
            elif os.name == "nt":
                _terminate_windows_tree(process.pid)
            else:
                _terminate_posix_group(process.pid, grace=1.0)
            stdout, stderr = process.communicate()
            raise CompanionIndeterminate("companion command timed out") from exc
        return subprocess.CompletedProcess(
            command, process.returncode, stdout=stdout, stderr=stderr
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CompanionIndeterminate(str(exc)) from exc


def resolve_companion(
    registration: dict,
    *,
    machine: str | None,
    env: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_captured,
) -> CompanionResolution | None:
    """Resolve paths and the optional provider into confirmed desired state."""
    if registration.get("kind") != RegistrationKind.PLUGIN_COMPANION:
        raise CompanionError("not a plugin companion registration")
    spec = registration.get("spec")
    if not isinstance(spec, dict):
        raise CompanionError("plugin companion spec must be an object")
    root = _checked_plugin_root(registration)
    command = _resolve_command(root, spec.get("command"), field="command")
    stop_command = (
        _resolve_command(root, spec["stop_command"], field="stop_command")
        if "stop_command" in spec
        else None
    )
    health_probe = (
        _resolve_command(root, spec["health_probe"], field="health_probe")
        if "health_probe" in spec
        else None
    )
    environment = _base_environment(registration)
    arguments: list[str] = []

    if "config_provider" in spec:
        provider = _resolve_command(root, spec["config_provider"], field="config_provider")
        completed = runner(
            provider,
            cwd=str(root),
            environment=environment,
            timeout=float(spec.get("config_timeout_seconds", 15.0)),
            input_text=_request(registration, machine=machine, env=env),
        )
        if completed.returncode != 0:
            raise CompanionIndeterminate(
                "companion config provider exited "
                f"{completed.returncode}: {(completed.stderr or '').strip()}"
            )
        try:
            result = json.loads(completed.stdout or "")
            validate_companion_config_result(result)
        except (ValueError, RegistrationError) as exc:
            raise CompanionIndeterminate(
                "companion config provider returned an invalid result"
            ) from exc
        if not result["active"]:
            return None
        arguments = list(result.get("arguments") or [])
        environment = _merge_provider_environment(environment, result.get("environment"))

    resolved_registration = dict(registration)
    resolved_registration["companion_runtime"] = {
        "arguments": arguments,
        "environment": {
            key: value
            for key, value in environment.items()
            if key not in os.environ or os.environ.get(key) != value
        },
        "environment_digest": hashlib.sha256(
            json.dumps(sorted(environment.items()), separators=(",", ":")).encode()
        ).hexdigest(),
    }
    return CompanionResolution(
        registration=resolved_registration,
        command=(*command, *arguments),
        stop_command=stop_command,
        health_probe=health_probe,
        cwd=str(root),
        environment=environment,
        startup_timeout=float(spec.get("startup_timeout_seconds", 30.0)),
        stop_timeout=float(spec.get("stop_timeout_seconds", 15.0)),
        health_timeout=float(spec.get("health_timeout_seconds", 10.0)),
    )


def _safe_slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)


def _command_digest(command: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(list(command), separators=(",", ":")).encode()).hexdigest()


def _linux_start_token(pid: int) -> str | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    close = stat_text.rfind(")")
    fields = stat_text[close + 2 :].split()
    return f"{boot_id}:{fields[19]}" if len(fields) > 19 else None


def _windows_start_token(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            process,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(process)


def process_start_token(pid: int) -> str | None:
    """Return an OS process-start identity token, or ``None`` when uncertain."""
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_start_token(pid)
    token = _linux_start_token(pid)
    if token is not None:
        return token
    completed = _run_captured(
        ("ps", "-o", "lstart=", "-p", str(pid)),
        cwd=os.getcwd(),
        environment=os.environ,
        timeout=5.0,
    )
    value = (completed.stdout or "").strip()
    return value or None


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process = kernel32.OpenProcess(0x1000, False, pid)
        if process:
            kernel32.CloseHandle(process)
            return True
        return ctypes.get_last_error() != 87
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_exists(pgid: int) -> bool:
    if os.name == "nt":
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _IdentityProcess:
    """Recovered POSIX process-group leader fenced by its start token."""

    def __init__(
        self,
        pid: int,
        token: str,
        token_source: Callable[[int], str | None] = process_start_token,
    ):
        self.pid = pid
        self._token = token
        self._token_source = token_source

    def poll(self) -> int | None:
        current = self._token_source(self.pid)
        if current == self._token:
            return None
        if current is None and _process_exists(self.pid):
            return None
        if current is None and _process_group_exists(self.pid):
            return None
        return 0

    def terminate(self) -> None:
        current = self._token_source(self.pid)
        if current == self._token:
            _terminate_posix_group(self.pid)
            return
        if current is None and _process_exists(self.pid):
            raise CompanionIndeterminate(
                "recovered companion process identity could not be confirmed"
            )
        if current is None and _process_group_exists(self.pid):
            raise CompanionIndeterminate(
                "recovered companion group exists without its identity leader"
            )

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.monotonic() + (timeout or 0)
        while self.poll() is None:
            if timeout is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.05)
        return 0


class _WindowsJobProcess:
    """Popen adapter retaining a kill-on-close Windows Job."""

    def __init__(self, process: subprocess.Popen[bytes], job: int):
        self._process = process
        self._job = job
        self.pid = process.pid

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def terminate(self) -> None:
        import ctypes
        from ctypes import wintypes

        job, self._job = self._job, 0
        if job:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(job)
        with contextlib.suppress(OSError):
            self._process.terminate()

    def __del__(self) -> None:
        if self._job:
            with contextlib.suppress(Exception):
                self.terminate()


class _GatedProcess:
    def __init__(self, process: CompanionProcess, gate_write: int):
        self._process = process
        self._gate_write = gate_write
        self.pid = process.pid

    def release(self) -> None:
        os.write(self._gate_write, b"1")
        os.close(self._gate_write)
        self._gate_write = -1

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def terminate(self) -> None:
        if self._gate_write >= 0:
            with contextlib.suppress(OSError):
                os.close(self._gate_write)
            self._gate_write = -1
        if os.name == "nt":
            self._process.terminate()
        else:
            _terminate_posix_group(self.pid)


def _bind_windows_job(process: subprocess.Popen[bytes]) -> CompanionProcess:
    if os.name != "nt":
        return process
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise CompanionIndeterminate("could not create Windows companion Job")
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        job, wintypes.HANDLE(int(process._handle))
    )
    if not assigned:
        kernel32.CloseHandle(job)
        with contextlib.suppress(Exception):
            _terminate_windows_tree(process.pid)
        raise CompanionIndeterminate("could not contain Windows companion process")
    return _WindowsJobProcess(process, job)


def _launch_gated(resolution: CompanionResolution) -> _GatedProcess:
    gate_read, gate_write = os.pipe()
    os.set_inheritable(gate_read, True)
    environment = dict(resolution.environment)
    environment["COPILOT_COMPANION_COMMAND"] = base64.urlsafe_b64encode(
        json.dumps(list(resolution.command)).encode()
    ).decode("ascii")
    kwargs: dict[str, object] = no_window_kwargs()
    if os.name == "nt":
        import msvcrt

        handle = msvcrt.get_osfhandle(gate_read)
        os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        kwargs["startupinfo"] = startup
        kwargs["close_fds"] = True
        environment["COPILOT_COMPANION_GATE_HANDLE"] = str(handle)
    else:
        kwargs["pass_fds"] = (gate_read,)
        kwargs["close_fds"] = True
        kwargs["start_new_session"] = True
        environment["COPILOT_COMPANION_GATE_FD"] = str(gate_read)
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "agent_dispatch.companion_gate"],
            stdin=subprocess.DEVNULL,
            cwd=resolution.cwd,
            env=environment,
            **kwargs,
        )
        contained = _bind_windows_job(process)
        return _GatedProcess(contained, gate_write)
    except Exception:
        with contextlib.suppress(OSError):
            os.close(gate_write)
        raise
    finally:
        os.close(gate_read)


def _terminate_posix_group(pid: int, *, grace: float = 5.0) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def _terminate_windows_tree(pid: int) -> None:
    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
    subprocess.run(  # noqa: S603 -- fixed system executable and owned PID
        [str(taskkill), "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        timeout=15,
        check=False,
        **no_window_kwargs(),
    )


def _force_retire(process: CompanionProcess) -> None:
    process.terminate()
    with contextlib.suppress(Exception):
        process.wait(timeout=5)


class DefaultCompanionController:
    """Real companion lifecycle with durable, creation-time-fenced receipts."""

    def __init__(
        self,
        receipt_dir: str | Path,
        *,
        resolver: Callable[..., CompanionResolution | None] = resolve_companion,
        runner: Callable[..., subprocess.CompletedProcess[str]] = _run_captured,
        token_source: Callable[[int], str | None] = process_start_token,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.receipt_dir = Path(receipt_dir)
        self.resolver = resolver
        self.runner = runner
        self.token_source = token_source
        self.sleeper = sleeper
        self.monotonic = monotonic

    def resolve(
        self, registration: dict, *, machine: str | None, env: str
    ) -> CompanionResolution | None:
        return self.resolver(registration, machine=machine, env=env, runner=self.runner)

    def _receipt_path(self, registration_id: str) -> Path:
        digest = hashlib.sha256(registration_id.encode()).hexdigest()[:16]
        return self.receipt_dir / (f"{_safe_slug(registration_id)}-{digest}.companion.json")

    def _read_receipt(self, registration_id: str) -> dict | None:
        path = self._receipt_path(registration_id)
        if not path.exists():
            return None
        try:
            _, value = read_json_object(path)
        except Exception as exc:
            raise CompanionIndeterminate(f"companion receipt is unreadable: {exc}") from exc
        if not isinstance(value, dict):
            raise CompanionIndeterminate("companion receipt is not a JSON object")
        return value

    def _delete_receipt(self, registration_id: str) -> None:
        with contextlib.suppress(OSError):
            self._receipt_path(registration_id).unlink()

    def reconcile_receipts(self, adopted_registration_ids: set[str]) -> None:
        """Retire identity-confirmed POSIX companions no longer in desired state."""
        if not self.receipt_dir.exists():
            return
        failures: list[str] = []
        for path in self.receipt_dir.glob("*.companion.json"):
            try:
                _, receipt = read_json_object(path)
                if not isinstance(receipt, dict):
                    raise CompanionIndeterminate(f"receipt {path.name} is not a JSON object")
                if receipt.get("schema_version") != _RECEIPT_VERSION:
                    raise CompanionIndeterminate(f"receipt {path.name} has an unsupported version")
                registration_id = receipt.get("registration_id")
                if not isinstance(registration_id, str) or not registration_id:
                    raise CompanionIndeterminate(
                        f"receipt {path.name} has no registration identity"
                    )
                if registration_id in adopted_registration_ids:
                    continue
                self._retire_receipt(receipt)
                path.unlink(missing_ok=True)
            except (CompanionError, CompanionIndeterminate, OSError) as exc:
                failures.append(f"{path.name}: {exc}")
        if failures:
            raise CompanionIndeterminate(
                "could not reconcile companion receipts: " + "; ".join(failures)
            )

    def _retire_receipt(self, receipt: dict) -> None:
        pid = receipt.get("pid")
        token = receipt.get("start_token")
        if not isinstance(pid, int) or not isinstance(token, str):
            raise CompanionError("companion receipt has invalid process identity")
        current = self.token_source(pid)
        if current is None:
            if _process_exists(pid):
                raise CompanionIndeterminate("companion process identity could not be confirmed")
            if _process_group_exists(pid):
                raise CompanionIndeterminate("companion group exists without its identity leader")
            return
        if current != token:
            return
        if os.name == "nt":
            _terminate_windows_tree(pid)
        else:
            _terminate_posix_group(pid)

    def _recover(
        self, resolution: CompanionResolution, fingerprint: str
    ) -> CompanionProcess | None:
        receipt = self._read_receipt(resolution.registration["id"])
        if receipt is None:
            return None
        if receipt.get("schema_version") != _RECEIPT_VERSION:
            raise CompanionIndeterminate("companion receipt version is unsupported")
        if receipt.get("fingerprint") != fingerprint:
            self._retire_receipt(receipt)
            self._delete_receipt(resolution.registration["id"])
            return None
        if receipt.get("command_digest") != _command_digest(resolution.command):
            raise CompanionIndeterminate("companion receipt command is inconsistent")
        pid = receipt.get("pid")
        token = receipt.get("start_token")
        if not isinstance(pid, int) or not isinstance(token, str):
            raise CompanionIndeterminate("companion receipt identity is invalid")
        current = self.token_source(pid)
        if current is None:
            if _process_exists(pid):
                raise CompanionIndeterminate("companion process identity could not be confirmed")
            if _process_group_exists(pid):
                raise CompanionIndeterminate("companion group exists without its identity leader")
            self._delete_receipt(resolution.registration["id"])
            return None
        if current != token:
            self._delete_receipt(resolution.registration["id"])
            return None
        if os.name == "nt":
            self._retire_receipt(receipt)
            self._delete_receipt(resolution.registration["id"])
            return None
        return _IdentityProcess(pid, token, self.token_source)

    def launch(self, resolution: CompanionResolution, *, fingerprint: str) -> CompanionLaunch:
        recovered = self._recover(resolution, fingerprint)
        if recovered is not None:
            return CompanionLaunch(recovered, recovered=True)
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        process = _launch_gated(resolution)
        token = self.token_source(process.pid)
        if token is None:
            process.terminate()
            raise CompanionIndeterminate(
                "launched companion process identity could not be recorded"
            )
        receipt = {
            "schema_version": _RECEIPT_VERSION,
            "registration_id": resolution.registration["id"],
            "pid": process.pid,
            "start_token": token,
            "fingerprint": fingerprint,
            "command_digest": _command_digest(resolution.command),
            "runtime_revision": resolution.registration.get("runtime_revision"),
            "containment": ("windows-job" if os.name == "nt" else "posix-process-group"),
        }
        try:
            write_json_object_atomic(self._receipt_path(resolution.registration["id"]), receipt)
            process.release()
        except Exception as exc:
            process.terminate()
            self._delete_receipt(resolution.registration["id"])
            raise CompanionIndeterminate(f"companion receipt/release failed: {exc}") from exc
        if resolution.health_probe is not None:
            deadline = self.monotonic() + resolution.startup_timeout
            confirmed_unhealthy = False
            while True:
                if process.poll() is not None:
                    _force_retire(process)
                    self._delete_receipt(resolution.registration["id"])
                    raise CompanionError("companion exited before becoming ready")
                try:
                    result = self.health(resolution)
                except CompanionIndeterminate:
                    result = None
                if result is True:
                    break
                if result is False:
                    confirmed_unhealthy = True
                if self.monotonic() >= deadline:
                    _force_retire(process)
                    self._delete_receipt(resolution.registration["id"])
                    error = CompanionError if confirmed_unhealthy else CompanionIndeterminate
                    raise error("companion did not become ready before its startup timeout")
                self.sleeper(min(0.25, max(0.0, deadline - self.monotonic())))
        elif process.poll() is not None:
            _force_retire(process)
            self._delete_receipt(resolution.registration["id"])
            raise CompanionError("companion exited during startup")
        return CompanionLaunch(process)

    def health(self, resolution: CompanionResolution) -> bool | None:
        if resolution.health_probe is None:
            return None
        completed = self.runner(
            resolution.health_probe,
            cwd=resolution.cwd,
            environment=resolution.environment,
            timeout=resolution.health_timeout,
            input_text=_request(
                resolution.registration,
                machine=resolution.registration.get("machine"),
                env=str(resolution.registration.get("env") or "default"),
            ),
        )
        if completed.returncode != 0:
            raise CompanionIndeterminate(
                "companion health probe exited "
                f"{completed.returncode}: {(completed.stderr or '').strip()}"
            )
        try:
            result = json.loads(completed.stdout or "")
            validate_companion_health_result(result)
        except (ValueError, RegistrationError) as exc:
            raise CompanionIndeterminate(
                "companion health probe returned an invalid result"
            ) from exc
        return bool(result["healthy"])

    def stop(self, resolution: CompanionResolution, process: CompanionProcess) -> None:
        if resolution.stop_command is not None and process.poll() is None:
            with contextlib.suppress(CompanionIndeterminate):
                self.runner(
                    resolution.stop_command,
                    cwd=resolution.cwd,
                    environment=resolution.environment,
                    timeout=resolution.stop_timeout,
                )
            with contextlib.suppress(Exception):
                process.wait(timeout=resolution.stop_timeout)
        _force_retire(process)
        self._delete_receipt(resolution.registration["id"])

    def retire_crashed(self, resolution: CompanionResolution, process: CompanionProcess) -> None:
        _force_retire(process)
        self._delete_receipt(resolution.registration["id"])
