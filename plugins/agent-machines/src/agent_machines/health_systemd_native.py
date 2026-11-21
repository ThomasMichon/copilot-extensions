"""Finite user-manager operations for one installation-owned service/timer pair."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, cast

from . import health_slots as slots
from .health_enrollment_state import linux_definition
from .health_jobs import LinuxDefinition

PROPERTIES = (
    "Id", "Names", "LoadState", "ActiveState", "SubState", "FragmentPath", "SourcePath",
    "DropInPaths", "Transient", "NeedDaemonReload", "Description", "UnitFileState",
    "MainPID", "ControlPID", "InvocationID", "ControlGroup", "Result",
    "ExecMainCode", "ExecMainStatus", "ExecMainStartTimestamp", "NextElapseUSecRealtime", "Job",
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _timestamp(value: str) -> str | None:
    try:
        instant = datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return instant.isoformat()


def _cgroup_empty(group: str, root: Path = Path("/sys/fs/cgroup")) -> bool:
    if not group:
        return True
    if not group.startswith("/") or ".." in group.split("/") or "\0" in group:
        return False
    try:
        if not (root / "cgroup.controllers").is_file():
            return False
        directory = root / group.lstrip("/")
        try:
            with (directory / "cgroup.events").open("rb") as stream:
                events = stream.read(4097)
        except FileNotFoundError:
            return not directory.exists()
        return len(events) <= 4096 and b"populated 0" in events.splitlines()
    except OSError:
        return False


def _stamp(info: os.stat_result, uid: int) -> tuple[int, int, int, int]:
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or info.st_uid != uid or info.st_mode & 0o022 or not info.st_ino
        or info.st_size > 16000
    ):
        raise slots.HealthError("foreign-or-drifted-health-pair")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _optional_directory(path: Path) -> bool:
    plain = cast(Callable[..., Path], slots._plain)
    if not path.is_absolute() or ".." in path.parts:
        raise slots.HealthError("absolute-plain-path-required")
    for directory in (*reversed(path.parents), path):
        try:
            directory.lstat()
        except FileNotFoundError:
            return False
        plain(directory, directory=True)
    return True


def read_file(path: Path, uid: int) -> tuple[str | None, tuple[int, int, int, int] | None]:
    if sys.platform != "linux":
        raise slots.HealthError("native-health-job-unsupported")
    plain = cast(Callable[..., Path], slots._plain)
    if not _optional_directory(path.parent):
        return None, None
    plain(path, optional=True)
    try:
        before = _stamp(path.lstat(), uid)
    except FileNotFoundError:
        return None, None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if _stamp(os.fstat(stream.fileno()), uid) != before:
            raise slots.HealthError("native-health-pair-changed")
        raw = stream.read(16001)
        after = _stamp(os.fstat(stream.fileno()), uid)
    plain(path)
    if before != after or before != _stamp(path.lstat(), uid) or len(raw) > 16000:
        raise slots.HealthError("native-health-pair-changed")
    return raw.decode("utf-8"), before


def _link(scope: Path, unit: str, uid: int) -> tuple[int, int, str] | None:
    path = scope / "timers.target.wants" / unit
    if not _optional_directory(path.parent):
        return None
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else ""
    if (
        not target or info.st_uid != uid or not info.st_ino
        or target not in {"../" + unit, str(scope / unit)}
    ):
        raise slots.HealthError("foreign-or-overridden-health-pair")
    return info.st_dev, info.st_ino, target


class NativePair:
    def __init__(self, name: str, owner: str) -> None:
        if (
            re.fullmatch(r"ce-health-[a-f0-9]{32}", name) is None
            or re.fullmatch(r"urn:copilot-extensions:health-job:[a-f0-9]{64}", owner) is None
            or name != "ce-health-" + owner.rsplit(":", 1)[1][:32]
        ):
            raise slots.HealthError("invalid-native-job-owner")
        self.name, self.owner = name, owner
        self.units = (name + ".service", name + ".timer")
        self.native_cleanup_error: object | None = None
        self._native_begin: Callable[[], None] | None = None
        self._native_settled: Callable[[], None] | None = None

    def _context(self) -> tuple[int, Path]:
        if sys.platform != "linux":
            raise slots.HealthError("native-health-job-unsupported")
        uid = os.getuid()
        if os.geteuid() != uid:
            raise slots.HealthError("local-job-account-mismatch")
        runtime = "/run/user/" + str(uid)
        if (
            os.environ.get("XDG_RUNTIME_DIR", runtime) != runtime
            or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + runtime + "/bus")
            != "unix:path=" + runtime + "/bus"
            or os.environ.get("SYSTEMD_UNIT_PATH")
        ):
            raise slots.HealthError("native-user-manager-scope-mismatch")
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        if not config.is_absolute() or ".." in config.parts:
            raise slots.HealthError("native-user-manager-scope-mismatch")
        return uid, config / "systemd" / "user"

    def _command(self, *arguments: str, timeout: int = 15, missing: bool = False) -> str:
        from agent_procutil.owned_budget import OwnedExecutionBudget
        from agent_procutil.owned_process import run_caller_owned

        executable = next((path for path in ("/usr/bin/systemctl", "/bin/systemctl") if Path(path).is_file()), None)
        if executable is None:
            raise slots.HealthError("native-user-manager-unavailable")
        mutating = bool(arguments and arguments[0] != "show")
        if mutating and self._native_begin is not None:
            self._native_begin()
        client_settled: list[bool] = []
        try:
            result = run_caller_owned(
                [executable, "--user", "--no-pager", *arguments],
                timeout=timeout, budget=OwnedExecutionBudget.from_timeout(timeout, cleanup_reserve=5),
                max_stdout_bytes=64 * 1024, max_stderr_bytes=4096,
                env={**os.environ, "LC_ALL": "C", "TZ": "UTC", "SYSTEMD_COLORS": "0"},
                on_settled=lambda: client_settled.append(True),
            )
            client_settled.append(True)
        finally:
            if client_settled and mutating and self._native_settled is not None:
                self._native_settled()
        if result.returncode != 0 and not (
            missing and result.returncode in {1, 4} and "LoadState=not-found" in result.stdout.splitlines()
        ):
            raise slots.HealthError("native-health-pair-operation-unconfirmed")
        return cast(str, result.stdout)

    def _properties(self, unit: str) -> dict[str, str]:
        text = self._command("show", unit, "--property=" + ",".join(PROPERTIES), missing=True)
        properties: dict[str, str] = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in PROPERTIES or key in properties:
                raise slots.HealthError("native-health-pair-response-invalid")
            properties[key] = value
        if not {"LoadState", "ActiveState", "FragmentPath", "DropInPaths", "Job"}.issubset(properties):
            raise slots.HealthError("native-health-pair-response-invalid")
        return properties

    def _overrides(self, directories: list[Path], unit: str) -> None:
        suffix = unit.rsplit(".", 1)[1]
        # systemd also applies prefix-truncated drop-ins to dashed unit names.
        candidates = {unit + ".d", suffix + ".d", "ce-." + suffix + ".d", "ce-health-." + suffix + ".d"}
        for directory in directories:
            for name in candidates:
                try:
                    (directory / name).lstat()
                except FileNotFoundError:
                    continue
                raise slots.HealthError("foreign-or-overridden-health-pair")

    def snapshot(self) -> dict[str, Any]:
        uid, scope = self._context()
        unit_path = self._command("show", "--property=UnitPath", "--value").strip()
        directories = [Path(value) for value in unit_path.split()]
        if (
            not directories or len(directories) > 128
            or any(not path.is_absolute() for path in directories) or scope not in directories
        ):
            raise slots.HealthError("native-user-manager-scope-unconfirmed")
        artifacts: dict[str, str] = {}
        stamps: dict[str, tuple[int, int, int, int] | None] = {}
        native: dict[str, dict[str, str]] = {}
        for unit in self.units:
            self._overrides(directories, unit)
            for directory in directories:
                if directory == scope:
                    continue
                try:
                    (directory / unit).lstat()
                except FileNotFoundError:
                    continue
                raise slots.HealthError("foreign-or-overridden-health-pair")
            text, stamp = read_file(scope / unit, uid)
            if text is not None:
                if not text.startswith("[Unit]\nDescription=" + self.owner + "\n"):
                    raise slots.HealthError("foreign-or-drifted-health-pair")
                artifacts[unit] = text
            stamps[unit] = stamp
            properties = self._properties(unit)
            if properties.get("Job", "0") not in {"", "0"}:
                raise slots.HealthError("native-health-pair-operation-unconfirmed")
            fragment = properties["FragmentPath"]
            if (
                properties["DropInPaths"] or properties.get("SourcePath")
                or properties.get("Transient") not in {"no", None}
                or (fragment and fragment != str(scope / unit))
                or (fragment and properties.get("Description") != self.owner)
                or properties["LoadState"] not in {"loaded", "not-found"}
                or (properties.get("Names") and properties["Names"].split() != [unit])
            ):
                raise slots.HealthError("foreign-or-overridden-health-pair")
            native[unit] = properties
        service, timer = (native[unit] for unit in self.units)
        link_state = _link(scope, self.units[1], uid)
        if service.get("UnitFileState") not in {None, "", "static", "disabled"} or timer.get("UnitFileState") not in {
            None, "", "disabled", "enabled",
        }:
            raise slots.HealthError("foreign-or-overridden-health-pair")
        enabled = timer.get("UnitFileState") == "enabled" and timer["ActiveState"] == "active"
        active = (
            service["ActiveState"] not in {"inactive", "failed"}
            or service.get("MainPID", "0") != "0" or service.get("ControlPID", "0") != "0"
            or not _cgroup_empty(service.get("ControlGroup", ""))
        )
        definition: LinuxDefinition = {
            "schema": "agent-machines.health.linux-definition", "version": 1, "platform": "linux",
            "owner": self.owner, "account_uid": uid, "enabled": enabled, "artifacts": artifacts,
        }
        configured = bool(artifacts) or link_state is not None or active or any(
            item["FragmentPath"] for item in native.values()
        )
        loaded = len(artifacts) == 2 and all(
            item["LoadState"] == "loaded" and item.get("NeedDaemonReload") == "no" for item in native.values()
        )
        return {
            "configured": configured, "definition": definition if configured else None,
            "digest": _digest({"stamps": stamps, "artifacts": artifacts, "native": native, "link": link_state}),
            "enabled": enabled, "timer_enabled": timer.get("UnitFileState") == "enabled",
            "timer_active": timer["ActiveState"] == "active", "loaded": loaded,
            "link_identity": link_state,
            "file_identities": stamps,
            "service_loaded": service["LoadState"] == "loaded",
            "timer_loaded": timer["LoadState"] == "loaded",
            "instances": [service.get("InvocationID") or "unknown"] if active else [],
            "account_uid": uid, "state": service["ActiveState"],
            "last_result": {
                "result": service.get("Result"), "code": service.get("ExecMainCode"),
                "status": service.get("ExecMainStatus"),
            } if not active and service.get("ExecMainStartTimestamp") and service.get("ExecMainCode", "0") != "0" else None,
            "last_run": _timestamp(service.get("ExecMainStartTimestamp", "")),
            "next_run": _timestamp(timer.get("NextElapseUSecRealtime", "")),
        }

    def _publish(self, scope: Path, artifacts: dict[str, str], expected: str) -> None:
        uid, actual_scope = self._context()
        if scope != actual_scope or self.snapshot()["digest"] != expected:
            raise slots.HealthError("native-health-pair-changed")
        before_files = {unit: read_file(scope / unit, uid) for unit in self.units}
        if self.snapshot()["digest"] != expected:
            raise slots.HealthError("native-health-pair-changed")
        scope.mkdir(parents=True, exist_ok=True)
        plain = cast(Callable[..., Path], slots._plain)
        plain(scope, directory=True)
        for unit, text in artifacts.items():
            before, stamp = before_files[unit]
            if before == text:
                continue
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=scope, prefix=unit + ".tmp.", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(text.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                if read_file(scope / unit, uid)[1] != stamp:
                    raise slots.HealthError("native-health-pair-changed")
                os.replace(temporary, scope / unit)
                cast(Callable[[Path], None], slots._sync_directory)(scope)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def call(
        self, operation: str, *, expected: str = "", definition: LinuxDefinition | None = None,
        on_native_begin: Callable[[], None] | None = None,
        on_native_settled: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        previous = self._native_begin, self._native_settled
        self._native_begin, self._native_settled = on_native_begin, on_native_settled
        try:
            return self._call(operation, expected=expected, definition=definition)
        finally:
            self._native_begin, self._native_settled = previous

    def _call(
        self, operation: str, *, expected: str = "", definition: LinuxDefinition | None = None,
    ) -> dict[str, Any]:
        if operation not in {"read", "create", "replace", "enable", "disable", "remove"}:
            raise slots.HealthError("native-health-job-unsupported")
        observed = self.snapshot()
        if operation == "read":
            return observed
        if observed["digest"] != expected:
            raise slots.HealthError("native-health-pair-changed")
        uid, scope = self._context()
        if operation in {"create", "replace"}:
            if (
                definition is None or definition["enabled"] or definition["owner"] != self.owner
                or definition["account_uid"] != uid
            ):
                raise slots.HealthError("invalid-native-job-definition")
            linux_definition(definition, {
                "store": "/", "policy_token": "0" * 64, "account_uid": uid, "enabled": False,
            })
            if observed["enabled"] or observed["timer_enabled"] or observed["timer_active"] or observed["instances"]:
                raise slots.HealthError("native-health-pair-not-settled")
            if operation == "create" and observed["configured"]:
                raise slots.HealthError("native-health-pair-changed")
            self._publish(scope, definition["artifacts"], expected)
            self._command("daemon-reload")
            result = self.snapshot()
            if not result["loaded"] or result["definition"] != definition:
                raise slots.HealthError("native-health-pair-readback-failed")
            return result
        if operation == "enable":
            if not observed["loaded"] or not observed["configured"]:
                raise slots.HealthError("native-health-pair-readback-failed")
            self._command("enable", self.units[1])
            self._command("start", self.units[1])
            result = self.snapshot()
            if not result["enabled"]:
                raise slots.HealthError("native-health-pair-readback-failed")
            return result
        if observed["timer_loaded"] or observed["timer_active"]:
            self._command("stop", self.units[1], timeout=60)
        link = _link(scope, self.units[1], uid)
        if link != observed["link_identity"]:
            raise slots.HealthError("native-health-pair-changed")
        if link is not None:
            (scope / "timers.target.wants" / self.units[1]).unlink()
            cast(Callable[[Path], None], slots._sync_directory)(scope / "timers.target.wants")
        self._command("daemon-reload")
        withdrawing = self.snapshot()
        if (
            withdrawing["timer_enabled"] or withdrawing["timer_active"]
            or (withdrawing["definition"] or {}).get("artifacts", {})
            != (observed["definition"] or {}).get("artifacts", {})
        ):
            raise slots.HealthError("native-health-pair-settlement-unconfirmed")
        if observed["service_loaded"] or observed["instances"]:
            # Stop through the manager: the finite action never escapes its cgroup.
            self._command("stop", self.units[0], timeout=60)
        settled = self.snapshot()
        if settled["timer_enabled"] or settled["timer_active"] or settled["instances"]:
            raise slots.HealthError("native-health-pair-settlement-unconfirmed")
        if operation == "remove":
            if (
                (settled["definition"] or {}).get("artifacts", {})
                != (observed["definition"] or {}).get("artifacts", {})
                or settled["file_identities"] != observed["file_identities"]
            ):
                raise slots.HealthError("native-health-pair-changed")
            before_files = {unit: read_file(scope / unit, uid)[1] for unit in self.units}
            if self.snapshot()["digest"] != settled["digest"]:
                raise slots.HealthError("native-health-pair-changed")
            for unit in self.units:
                _, stamp = read_file(scope / unit, uid)
                if stamp != before_files[unit]:
                    raise slots.HealthError("native-health-pair-changed")
                if stamp is not None:
                    (scope / unit).unlink()
                    cast(Callable[[Path], None], slots._sync_directory)(scope)
            self._command("daemon-reload")
            settled = self.snapshot()
            if settled["configured"]:
                raise slots.HealthError("native-health-pair-removal-unconfirmed")
        return settled
