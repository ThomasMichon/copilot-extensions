"""External runtime drivers: discovery and the JSON command protocol client.

A driver is a command. agent-containers runs ``<command...> <verb> --json``
with a JSON request on stdin and reads one JSON response object from stdout;
interactive streaming exec instead passes its request as an argument and
passes stdio through. The full contract is ``docs/runtime-drivers.md``.

Drivers are discovered (first match wins) from:

1. a manifest ``<drivers dir>/<name>.json`` -- ``$AGENT_CONTAINERS_DRIVERS_DIR``
   or ``~/.agent-containers/drivers.d`` -- shaped
   ``{"schema_version": 1, "name": "<name>", "command": ["<exe>", ...]}``;
2. an executable named ``agent-containers-driver-<name>`` on ``PATH``.

Each manifest is its own fault boundary: an invalid entry becomes a finding
(see ``agent-containers drivers``) and never hides valid ones.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags

from .config import (
    FLEET_LABEL,
    RUNTIME_DIR,
    SECURITY_PROFILE_LABEL,
    TRUSTED_PROFILE,
    FleetConfig,
)
from .runtime_drivers import (
    DRIVER_LABEL,
    INSTANCE_LABEL,
    DriverCapabilities,
    DriverError,
    ExecResult,
    RuntimeDriver,
    bind_member,
)

log = logging.getLogger("agent-containers")

PROTOCOL_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
PATH_PREFIX = "agent-containers-driver-"
PROTOCOL_ENV = "AGENT_CONTAINERS_DRIVER_PROTOCOL"
DRIVERS_DIR_ENV = "AGENT_CONTAINERS_DRIVERS_DIR"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MEMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TIMEOUTS = {
    "capabilities": 30.0,
    "list": 60.0,
    "inspect": 60.0,
    "start": 300.0,
    "stop": 300.0,
    "remove": 300.0,
    "provision": 3600.0,
    "copy-in": 600.0,
}


@dataclass(frozen=True)
class DriverSpec:
    name: str
    command: tuple[str, ...]
    source: str  # manifest path, or "PATH"


def drivers_dir() -> Path:
    override = os.environ.get(DRIVERS_DIR_ENV)
    return Path(override).expanduser() if override else RUNTIME_DIR / "drivers.d"


def _resolve_executable(value: str) -> str | None:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(value)


def _load_manifest(path: Path) -> DriverSpec:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unreadable manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {data.get('schema_version')!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )
    name = data.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError("name must be a lowercase driver name")
    if name != path.stem:
        raise ValueError(f"name {name!r} does not match file name {path.name!r}")
    command = data.get("command")
    if (
        not isinstance(command, list) or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ValueError("command must be a non-empty list of strings")
    executable = _resolve_executable(command[0])
    if executable is None:
        raise ValueError(f"command executable {command[0]!r} was not found")
    return DriverSpec(name, (executable, *command[1:]), str(path))


def scan_drivers() -> tuple[dict[str, DriverSpec], list[dict[str, str]]]:
    """Return valid manifest drivers plus one finding per invalid entry."""
    specs: dict[str, DriverSpec] = {}
    findings: list[dict[str, str]] = []
    root = drivers_dir()
    try:
        entries = sorted(root.glob("*.json")) if root.is_dir() else []
    except OSError as exc:
        return specs, [{"path": str(root), "reason": f"cannot enumerate: {exc}"}]
    for path in entries:
        try:
            spec = _load_manifest(path)
        except ValueError as exc:
            findings.append({"path": str(path), "reason": str(exc)})
            continue
        specs[spec.name] = spec
    return specs, findings


def find_driver(name: str) -> DriverSpec:
    specs, findings = scan_drivers()
    if name in specs:
        return specs[name]
    on_path = shutil.which(PATH_PREFIX + name)
    if on_path:
        return DriverSpec(name, (on_path,), "PATH")
    detail = "".join(
        f"; {finding['path']}: {finding['reason']}"
        for finding in findings
        if Path(finding["path"]).stem == name
    )
    raise RuntimeError(
        f"runtime driver '{name}' is not registered: add "
        f"{drivers_dir() / (name + '.json')} or put {PATH_PREFIX}{name} on "
        f"PATH{detail}"
    )


def _require_member_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _MEMBER_RE.fullmatch(value):
        raise ValueError(f"{label} must match {_MEMBER_RE.pattern}")
    return value


class ExternalDriver(RuntimeDriver):
    """A fleet's runtime driven through the external command protocol."""

    def __init__(self, spec: DriverSpec, fleet_name: str, fleet: FleetConfig):
        self.spec = spec
        self.name = spec.name
        self.fleet_name = fleet_name
        self.fleet = fleet
        self._capabilities: DriverCapabilities | None = None

    # -- protocol plumbing -------------------------------------------------

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "driver": self.name,
            "fleet": self.fleet_name,
            "driverOptions": self.fleet.driver_options,
            **payload,
        }

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env[PROTOCOL_ENV] = str(PROTOCOL_VERSION)
        return env

    def call(self, verb: str, payload: dict[str, Any] | None = None, *,
             timeout: float | None = None) -> dict[str, Any]:
        """Run one non-interactive verb and return its validated response."""
        request = json.dumps(self._request(payload or {}))
        limit = timeout if timeout is not None else _TIMEOUTS.get(verb, 120.0)
        try:
            done = subprocess.run(
                [*self.spec.command, verb, "--json"],
                input=request, capture_output=True, text=True, timeout=limit,
                env=self._env(), creationflags=no_window_flags(),
            )
        except FileNotFoundError:
            raise DriverError(self.name, verb, "runtime_unavailable",
                              f"{self.spec.command[0]} not found") from None
        except subprocess.TimeoutExpired:
            raise DriverError(self.name, verb, "timeout",
                              f"no response within {limit:.0f}s") from None
        try:
            response = json.loads(done.stdout)
        except ValueError:
            response = None
        if not isinstance(response, dict):
            detail = (done.stderr or done.stdout or "").strip()[-500:]
            raise DriverError(self.name, verb, "protocol_error",
                              f"exit {done.returncode}, no JSON response: {detail}")
        if response.get("protocolVersion") != PROTOCOL_VERSION:
            raise DriverError(
                self.name, verb, "unsupported_protocol",
                f"responded with protocolVersion {response.get('protocolVersion')!r}, "
                f"expected {PROTOCOL_VERSION}",
            )
        if response.get("ok") is not True or done.returncode != 0:
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            raise DriverError(
                self.name, verb, str(error.get("code") or "driver_failed"),
                str(error.get("message") or f"exit {done.returncode}"),
            )
        return response

    def _member(self, payload: Any, *, expected: str | None = None):
        from .lifecycle import DockerContainerInfo

        if not isinstance(payload, dict):
            raise ValueError("member must be an object")
        name = _require_member_name(payload.get("name"), "member.name")
        if expected is not None and name != expected:
            raise ValueError(f"reported member {name!r}, expected {expected!r}")
        member_id = _require_member_name(payload.get("id"), "member.id")
        if payload.get("fleet") != self.fleet_name:
            raise ValueError(
                f"member {name!r} reports fleet {payload.get('fleet')!r}"
            )
        state = payload.get("state")
        if not isinstance(state, str) or not state:
            raise ValueError(f"member {name!r} has no state")
        instance = payload.get("instance") or name
        labels = {
            FLEET_LABEL: self.fleet_name,
            INSTANCE_LABEL: str(instance),
            SECURITY_PROFILE_LABEL: TRUSTED_PROFILE,
            DRIVER_LABEL: self.name,
        }
        repo = payload.get("repo")
        info = DockerContainerInfo(
            name=name,
            container_id=member_id,
            image=str(payload.get("image") or f"{self.name}:{self.fleet_name}"),
            state=state.lower(),
            status=str(payload.get("status") or state),
            labels=labels,
            fleet=self.fleet_name,
            security_profile=TRUSTED_PROFILE,
            driver=self.name,
            driver_repo=repo if isinstance(repo, str) and repo else None,
        )
        bind_member(self, info)
        return info

    def _member_or_error(self, verb: str, payload: Any, expected: str | None = None):
        try:
            return self._member(payload, expected=expected)
        except ValueError as exc:
            raise DriverError(self.name, verb, "protocol_error", str(exc)) from None

    # -- RuntimeDriver -----------------------------------------------------

    @property
    def capabilities(self) -> DriverCapabilities:
        if self._capabilities is None:
            raw = self.call("capabilities").get("capabilities")
            if not isinstance(raw, dict):
                raise DriverError(self.name, "capabilities", "protocol_error",
                                  "capabilities must be an object")
            self._capabilities = DriverCapabilities(
                owns_relay=raw.get("owns_relay") is True,
                ssh_transport=raw.get("ssh_transport") is True,
            )
        return self._capabilities

    def provision(self, *, name: str, workspace_folder: str, exec_user: str) -> str:
        response = self.call("provision", {
            "member": {"name": name, "fleet": self.fleet_name, "instance": name},
            "workspaceFolder": workspace_folder,
            "execUser": exec_user,
            "environment": dict(self.fleet.environment),
        })
        return self._member_or_error("provision", response.get("member"), name).name

    def list_members(self):
        members = self.call("list").get("members")
        if not isinstance(members, list):
            raise DriverError(self.name, "list", "protocol_error",
                              "members must be a list")
        return [self._member_or_error("list", member) for member in members]

    def _inspect_member(self, name: str, instance_id: str | None = None):
        try:
            response = self.call("inspect", self._identity(name, instance_id))
        except DriverError as exc:
            if exc.code in ("not_found", "conflict"):
                return None
            raise
        info = self._member_or_error("inspect", response.get("member"), name)
        if instance_id and info.container_id != instance_id:
            return None
        return info

    def inspect(self, name: str, *, instance_id: str | None = None) -> dict:
        info = self._inspect_member(name, instance_id)
        if info is None:
            from .lifecycle import ContainerNotFoundError

            raise ContainerNotFoundError(
                f"No such container: {instance_id or name} "
                f"(runtime driver '{self.name}')"
            )
        return {
            "Id": info.container_id,
            "Name": "/" + info.name,
            "State": {"Status": info.state},
            "Config": {"Image": info.image, "Labels": dict(info.labels)},
            "HostConfig": {},
        }

    def state(self, name: str, *, instance_id: str | None = None) -> str | None:
        info = self._inspect_member(name, instance_id)
        return info.state if info else None

    @staticmethod
    def _identity(name: str, instance_id: str | None) -> dict[str, str]:
        return {"name": name, **({"id": instance_id} if instance_id else {})}

    def start(self, name: str, *, instance_id: str | None = None) -> None:
        self.call("start", self._identity(name, instance_id))

    def stop(self, name: str, *, instance_id: str | None = None) -> None:
        self.call("stop", self._identity(name, instance_id))

    def remove(self, name: str, *, force: bool = False,
               instance_id: str | None = None) -> None:
        self.call("remove", {**self._identity(name, instance_id), "force": bool(force)})

    def exec(self, name, argv, *, user=None, env=None, workdir=None,
             stdin=None, timeout=30.0, instance_id=None) -> ExecResult:
        response = self.call("exec", {
            **self._identity(name, instance_id), "argv": list(argv), "user": user,
            "env": dict(env or {}), "workdir": workdir, "stdin": stdin,
            "timeoutSeconds": timeout,
        }, timeout=timeout + 15.0)
        code = response.get("exitCode")
        if not isinstance(code, int) or isinstance(code, bool):
            raise DriverError(self.name, "exec", "protocol_error",
                              "exitCode must be an integer")
        return ExecResult(
            code, str(response.get("stdout") or ""), str(response.get("stderr") or ""),
        )

    def stream_exec_argv(self, name, argv, *, user=None, env=None,
                         inherit_env=None, workdir=None, instance_id=None) -> list[str]:
        request = self._request({
            **self._identity(name, instance_id), "argv": list(argv), "user": user,
            "env": dict(env or {}), "inheritEnv": list(inherit_env or []),
            "workdir": workdir,
        })
        return [*self.spec.command, "exec-stream", "--json", "--request",
                json.dumps(request, separators=(",", ":"))]

    def copy_in(self, name: str, source: Path, target: str, *,
                instance_id: str | None = None) -> None:
        self.call("copy-in", {**self._identity(name, instance_id),
                              "source": str(Path(source).resolve()), "target": target})

