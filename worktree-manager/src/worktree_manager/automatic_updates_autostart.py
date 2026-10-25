"""Explicit, current-user startup registration; consent and readiness are caller-owned."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterator

from agent_procutil import no_window_kwargs, windowless_python

from . import automatic_updates_startup as startup
from .mux_mapping_registry import _try_lock_file_once, _unlock_file

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_SCHEMA = startup.AUTOSTART_SCHEMA
_RUN_MAX_UTF16_UNITS = 260
UNIT_ENV = "WORKTREE_MANAGER_AUTOMATIC_UPDATES_UNIT"


def _platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "wsl" if "microsoft" in os.uname().release.lower() else "linux"
    return "unsupported"


def _paths(root: Path) -> tuple[Path, Path, str]:
    root = startup.checked_path(root, directory=True).resolve(strict=True)
    return root, root / "automatic-updates", startup.installation_name(root)


def _unit_path(name: str) -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    home = Path(configured) if configured else Path.home() / ".config"
    if not home.is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be absolute")
    return home / "systemd/user" / f"{name}.service"


def _bootstrap_body() -> str:
    return Path(startup.__file__).read_text(encoding="utf-8")


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _quote(value: str, *, environment_expansion: bool = False) -> str:
    if any(character in value for character in "\n\r\0"):
        raise ValueError("startup paths cannot contain newlines or NUL")
    # systemd specifiers and environment expansion are not shell quoting.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if environment_expansion:
        escaped = escaped.replace("$", "$$")
    return '"' + escaped + '"'


def _validate_run_entry(entry: str) -> int:
    units = len(entry.encode("utf-16-le")) // 2
    if units > _RUN_MAX_UTF16_UNITS:
        raise ValueError(
            f"Windows Run command is {units} UTF-16 code units; maximum is "
            f"{_RUN_MAX_UTF16_UNITS}; shorten the installation or real GUI Python path"
        )
    return units


def _command(root: Path, directory: Path, platform: str) -> list[str]:
    startup.selected_command(root, windows=platform == "windows")
    if platform == "windows":
        gui = Path(windowless_python(sys.executable, require_windowless=True))
        gui = startup.checked_path(gui.resolve(strict=True))
        if (gui.parent.parent / "pyvenv.cfg").exists():
            raise ValueError("Autorun requires a real base GUI interpreter, not a venv trampoline")
        return [str(gui), "-I", "-S", str(directory / "startup.py")]
    python = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    startup.checked_path(python)
    if root == python or root in python.parents:
        raise ValueError("systemd bootstrap interpreter must be outside the Manager installation")
    return [str(python), "-I", "-S", str(directory / "startup.py"), "--root", str(root)]


def _unit(root: Path, name: str, command: list[str]) -> str:
    working_directory = str(root)
    if any(character in working_directory for character in "\n\r\0"):
        raise ValueError("startup paths cannot contain newlines or NUL")
    if working_directory != working_directory.rstrip() or working_directory.endswith("\\"):
        raise ValueError("startup working directory cannot end in whitespace or a backslash")
    runtime_path = ":".join((
        str(Path.home() / ".local" / "bin"), "/usr/local/sbin", "/usr/local/bin",
        "/usr/sbin", "/usr/bin", "/sbin", "/bin",
    ))
    return (
        "[Unit]\nDescription=Worktree Manager automatic updates\n\n[Service]\n"
        "Type=simple\nExecStart=" + " ".join(
            _quote(item, environment_expansion=True) for item in command
        )
        # Unlike ExecStart, this path directive treats surrounding quotes literally.
        + "\nWorkingDirectory=" + working_directory.replace("%", "%%")
        + "\nEnvironment=" + _quote(f"{UNIT_ENV}={name}.service")
        + "\nEnvironment=" + _quote(f"PATH={runtime_path}")
        + "\nKillMode=process\nSendSIGKILL=no\nTimeoutStopSec=infinity"
        + "\nRestart=on-failure\nRestartSec=60\n\n[Install]\nWantedBy=default.target\n"
    )


def _systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *arguments], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=15, check=False, **no_window_kwargs(),
    )


def _unit_fields(unit: str, keys: tuple[str, ...]) -> dict[str, str]:
    completed = _systemctl("show", unit, *(f"--property={key}" for key in keys))
    if completed.returncode:
        raise RuntimeError(
            f"user manager cannot attest {unit}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)


def _loaded_owned(name: str, fields: dict[str, str]) -> bool:
    return (
        fields.get("Id") == f"{name}.service"
        and fields.get("FragmentPath") == str(_unit_path(name))
        and fields.get("NeedDaemonReload") == "no" and fields.get("DropInPaths") == ""
        and fields.get("KillMode") == "process" and fields.get("SendSIGKILL") == "no"
        and fields.get("TimeoutStopUSec") == "infinity"
    )


def _registry_read(name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    if kind != winreg.REG_SZ or not isinstance(value, str):
        raise ValueError("foreign Autorun registry value type")
    return value


def _registry_write(name: str, value: str | None) -> None:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE,
    ) as key:
        if value is None:
            winreg.DeleteValue(key, name)
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _read(path: Path) -> str | None:
    try:
        return startup.checked_path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _parent(path: Path) -> None:
    missing: list[Path] = []
    parent = path.parent
    while not parent.exists() and not parent.is_symlink():
        missing.append(parent)
        parent = parent.parent
    startup.checked_path(parent, directory=True)
    for item in reversed(missing):
        item.mkdir(mode=0o700)
        startup.checked_path(item, directory=True)
    startup.checked_path(path.parent, directory=True)


def _write(path: Path, body: str) -> None:
    _parent(path)
    if _read(path) == body:
        return
    if path.exists() or path.is_symlink():
        startup.checked_path(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _restore(path: Path, before: str | None, after: str) -> None:
    actual = _read(path)
    if actual == before:
        return
    if actual != after:
        raise RuntimeError(f"startup artifact changed during recovery: {path}")
    if before is None:
        path.unlink()
    else:
        _write(path, before)


def _publish(
    directory: Path, name: str, platform: str,
    body: str, entry: str, record: dict[str, object],
) -> None:
    bootstrap = directory / "startup.py"
    receipt = directory / "autostart.json"
    previous_body, previous_receipt = _read(bootstrap), _read(receipt)
    previous_entry = _registry_read(name) if platform == "windows" else _read(_unit_path(name))
    new_receipt = json.dumps(record, sort_keys=True) + "\n"
    try:
        _write(bootstrap, body)
        _write(receipt, new_receipt)
        if platform == "windows":
            if previous_entry != entry:
                _registry_write(name, entry)
        else:
            _write(_unit_path(name), entry)
    except _ERRORS as exc:
        try:
            if platform == "windows":
                actual = _registry_read(name)
                if actual != previous_entry:
                    if actual != entry:
                        raise RuntimeError("Autorun changed during recovery")
                    _registry_write(name, previous_entry)
            else:
                _restore(_unit_path(name), previous_entry, entry)
            _restore(receipt, previous_receipt, new_receipt)
            _restore(bootstrap, previous_body, body)
        except _ERRORS as recovery:
            raise RuntimeError(
                f"startup publication failed: {exc}; recovery incomplete: {recovery}"
            ) from exc
        raise


@contextmanager
def _lock(root: Path) -> Iterator[None]:
    path = root / "automatic-updates-autostart.lock"
    if path.exists() or path.is_symlink():
        startup.checked_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as stream:
        if not _try_lock_file_once(stream):
            raise ValueError("automatic update startup registration is busy")
        try:
            yield
        finally:
            _unlock_file(stream)


def _state(root: Path) -> tuple[dict[str, object], dict[str, object] | None]:
    root, directory, name = _paths(root)
    platform = _platform()
    result: dict[str, object] = {
        "platform": platform, "registered": False, "activation": "not-registered",
        "blockers": [], "name": name, "root": str(root), "service_active": False,
        "worker_readiness": "not-checked",
        "enabled": None, "service_state_known": False,
        "lifetime": (
            "current-user Windows logon session; no logged-out or pre-logon operation"
            if platform == "windows" else
            "Linux user manager/session; WSL distribution must be running; no linger configured"
        ),
    }
    if platform == "unsupported":
        raise ValueError("automatic update startup is unsupported on this platform")
    raw = _read(directory / "autostart.json")
    owner = json.loads(raw) if raw is not None else None
    if raw is not None and (
        not isinstance(owner, dict)
        or set(owner) != startup.AUTOSTART_FIELDS
        or owner.get("schema") != _SCHEMA
        or type(owner.get("version")) is not int or owner["version"] != 1
        or owner.get("root") != str(root) or owner.get("platform") != platform
        or owner.get("name") != name
        or not isinstance(owner.get("entry"), str)
        or not isinstance(owner.get("bootstrap"), str)
    ):
        raise ValueError("foreign or malformed startup ownership record")
    bootstrap = _read(directory / "startup.py")
    entry = (
        _registry_read(name) if platform == "windows"
        else _read(_unit_path(name))
    )
    if owner is None:
        if entry is not None or bootstrap is not None:
            raise ValueError("unowned startup entry or bootstrap; refusing replacement")
    elif entry not in (None, owner["entry"]) or (
        bootstrap is not None and _digest(bootstrap) != owner["bootstrap"]
    ):
        raise ValueError("foreign or edited startup entry/bootstrap; refusing replacement")
    result["registered"] = owner is not None and entry is not None and bootstrap is not None
    if result["registered"]:
        result["activation"] = "pending"
        result["blockers"] = ["worker-readiness-not-checked"]
        try:
            startup.selected_command(root, windows=platform == "windows")
        except (OSError, ValueError) as exc:
            result["activation"] = "blocked"
            result["blockers"] = [f"selected-Manager-unavailable: {exc}"]
    if platform != "windows":
        result["unit"] = f"{name}.service"
        try:
            fields = _unit_fields(f"{name}.service", ("ActiveState", "UnitFileState"))
            active = f"ActiveState={fields.get('ActiveState', '')}"
            result["enabled"] = fields.get("UnitFileState") == "enabled"
            if active in {"ActiveState=active", "ActiveState=activating",
                          "ActiveState=deactivating", "ActiveState=reloading"}:
                result["service_active"] = True
                result["service_state_known"] = True
            elif active in {"ActiveState=inactive", "ActiveState=failed"}:
                result["service_state_known"] = True
            else:
                result["activation"] = "blocked"
                result["blockers"] = [f"unknown-user-service-state: {active!r}"]
            if result["registered"] and not result["enabled"]:
                result["activation"] = "blocked"
                result["blockers"] = [
                    f"startup-unit-not-enabled: {fields.get('UnitFileState', 'unknown')}"
                ]
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            result["activation"] = "blocked"
            result["blockers"] = [f"user-manager-unavailable: {exc}"]
    else:
        result["enabled"] = result["registered"]
        if owner is not None and entry is not None:
            try:
                result["command_utf16_units"] = _validate_run_entry(entry)
            except ValueError as exc:
                result["activation"] = "blocked"
                result["enabled"] = False
                result["blockers"] = [str(exc)]
    return result, owner


def _failure(root: Path, exc: Exception) -> dict[str, object]:
    try:
        result = _state(root)[0]
    except _ERRORS:
        result = {"platform": _platform(), "root": str(root), "registered": False}
    result["activation"] = "blocked"
    result["blockers"] = [f"{type(exc).__name__}: {exc}"]
    return result


_ERRORS = (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError)


def inspect_autostart(root: Path) -> dict[str, object]:
    """Read registration only; never infer worker readiness or create artifacts."""
    try:
        return _state(root)[0]
    except _ERRORS as exc:
        return _failure(root, exc)


def activate_autostart(root: Path) -> dict[str, object]:
    """Caller-authorized start of an exact-owned enabled unit; never restart."""
    try:
        root, _, name = _paths(root)
        with _lock(root):
            result, owner = _state(root)
            if result["activation"] == "blocked" or result["platform"] == "windows":
                return result
            if owner is None or not result["registered"] or not result["enabled"]:
                raise ValueError("activation requires an exact-owned enabled startup registration")
            unit = f"{name}.service"
            fields = _unit_fields(unit, (
                "Id", "FragmentPath", "NeedDaemonReload", "DropInPaths",
                "KillMode", "SendSIGKILL", "TimeoutStopUSec",
            ))
            if not _loaded_owned(name, fields):
                raise RuntimeError("user manager cannot attest exact-owned startup activation")
            started = _systemctl("start", unit)
            if started.returncode:
                raise RuntimeError(
                    f"systemctl start failed: {started.stderr.strip() or started.stdout.strip()}"
                )
            return _state(root)[0]
    except _ERRORS as exc:
        return _failure(root, exc)


def native_supervisor_state(root: Path) -> str:
    """Prove this worker's native owner; uncertainty must never authorize a spawn."""
    if _platform() not in {"linux", "wsl"}:
        return "none"
    try:
        root, _, name = _paths(root)
        unit = f"{name}.service"
        hint = os.environ.get(UNIT_ENV)
        own_groups = startup.process_snapshot(os.getpid())[3]
        membership = any(unit in group.split("/") for group in own_groups)
        if hint and hint != unit:
            return "ambiguous"
        result, owner = _state(root)
        if owner is None or not result["registered"]:
            return "ambiguous" if hint or membership else "none"
        properties = (
            "Id", "ActiveState", "MainPID", "ControlGroup", "FragmentPath",
            "NeedDaemonReload", "KillMode", "SendSIGKILL", "TimeoutStopUSec", "DropInPaths",
        )
        before = _unit_fields(unit, properties)
        if before.get("ActiveState") in {"inactive", "failed"}:
            return "ambiguous" if hint or membership else "none"
        if (
            not _loaded_owned(name, before)
            or before.get("ActiveState") not in {"active", "activating"}
            or not before.get("ControlGroup", "").endswith("/" + unit)
        ):
            return "ambiguous"
        if not startup.systemd_process_owner(
            root, int(before.get("MainPID", "0")), before["ControlGroup"],
        ):
            return "ambiguous"
        after_state, after_owner = _state(root)
        if _unit_fields(unit, properties) != before or after_owner != owner or not after_state["service_active"]:
            return "ambiguous"
        return "systemd"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, IndexError) as exc:
        import logging

        logging.getLogger(__name__).warning("Cannot attest automatic-update supervisor: %s", exc)
        return "ambiguous"


def _registration_plan(
    root: Path, directory: Path, name: str, platform: str,
) -> tuple[str, str, dict[str, object]]:
    command = _command(root, directory, platform)
    body = _bootstrap_body()
    entry = (
        subprocess.list2cmdline(command) if platform == "windows"
        else _unit(root, name, command)
    )
    if platform == "windows":
        _validate_run_entry(entry)
    record = {
        "schema": _SCHEMA, "version": 1, "root": str(root), "platform": platform,
        "name": name, "bootstrap": _digest(body), "entry": entry,
    }
    return body, entry, record


def _require_refresh_allowed(
    result: dict[str, object], owner: dict[str, object] | None, entry: str,
) -> None:
    if owner is not None and result["service_active"] and owner["entry"] != entry:
        raise ValueError("active service replacement requires separate stop/handover approval")


def inspect_autostart_refresh(root: Path) -> dict[str, object]:
    """Preview this runtime's owned-registration refresh without any writes."""
    try:
        root, directory, name = _paths(root)
        result, owner = _state(root)
        if result["registered"]:
            _, entry, _ = _registration_plan(root, directory, name, str(result["platform"]))
            _require_refresh_allowed(result, owner, entry)
        return result
    except _ERRORS as exc:
        return _failure(root, exc)


def register_autostart(root: Path) -> dict[str, object]:
    """Caller-authorized registration only; never starts a service or process."""
    try:
        root, directory, name = _paths(root)
        result, owner = _state(root)
        platform = str(result["platform"])
        body, entry, record = _registration_plan(root, directory, name, platform)
        if (
            owner == record and result["registered"] and result["enabled"]
            and result["activation"] != "blocked"
        ):
            return result
        with _lock(root):
            result, owner = _state(root)
            _require_refresh_allowed(result, owner, entry)
            if (
                owner == record and result["registered"] and result["enabled"]
                and result["activation"] != "blocked"
            ):
                return result
            _publish(directory, name, platform, body, entry, record)
            if platform != "windows" and not (
                owner and owner["entry"] == entry and result["registered"] and result["enabled"]
            ):
                for arguments in (("daemon-reload",), ("enable", f"{name}.service")):
                    completed = _systemctl(*arguments)
                    if completed.returncode:
                        status = _state(root)[0]
                        status["activation"] = "blocked"
                        status["blockers"] = [
                            f"systemctl {' '.join(arguments)} failed: "
                            f"{completed.stderr.strip() or completed.stdout.strip()}"
                        ]
                        return status
            return _state(root)[0]
    except _ERRORS as exc:
        return _failure(root, exc)


def remove_autostart(root: Path) -> dict[str, object]:
    """Remove an exact-owned registration; an active Linux unit is never stopped."""
    try:
        root, directory, name = _paths(root)
        with _lock(root):
            result, owner = _state(root)
            if owner is None:
                return result
            if result["platform"] == "windows":
                actual = _registry_read(name)
                if actual not in (None, owner["entry"]):
                    raise ValueError("Autorun changed before removal; refusing deletion")
                if actual is not None:
                    _registry_write(name, None)
                # Keep the bootstrap/receipt: an already-launched ensure client may use it.
            else:
                path = _unit_path(name)
                if _read(path) is None:
                    result["registered"] = False
                    result["enabled"] = False
                    result["activation"] = "not-registered"
                    result["blockers"] = []
                    return result
                if not result["service_state_known"]:
                    return result
                if result["service_active"]:
                    result["activation"] = "blocked"
                    result["blockers"] = ["active-service-stop-requires-separate-approval"]
                    return result
                if _read(path) not in (None, owner["entry"]):
                    raise ValueError("service changed before removal; refusing disable")
                completed = _systemctl("disable", f"{name}.service")
                if completed.returncode:
                    raise RuntimeError(f"systemctl disable failed: {completed.stderr.strip()}")
                actual = _read(path)
                if actual not in (None, owner["entry"]):
                    raise ValueError("service changed during removal; refusing deletion")
                if actual is not None:
                    path.unlink()
                completed = _systemctl("daemon-reload")
                if completed.returncode:
                    raise RuntimeError(f"systemctl daemon-reload failed: {completed.stderr.strip()}")
            result["registered"] = False
            result["enabled"] = False
            result["activation"] = "not-registered"
            result["blockers"] = []
            return result
    except _ERRORS as exc:
        return _failure(root, exc)
