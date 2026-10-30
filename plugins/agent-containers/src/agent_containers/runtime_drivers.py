"""Container runtime driver seam.

A fleet's members live in a *runtime*. Docker is the built-in default
runtime; a fleet that sets ``driver: <name>`` delegates every runtime
operation to an external driver command instead (see
``external_driver.py`` and ``docs/runtime-drivers.md``).

Leases, borrow/release, fleet sizing, readiness, trust posture and the
agent-bridge ``container:<name>`` resolver stay in agent-containers and are
runtime-agnostic. Only the operations below cross the seam:

* provision a named member tagged with its fleet/instance identity,
* enumerate members (including stopped ones) and inspect one member,
* start / stop / remove,
* non-interactive exec with a captured result,
* interactive streaming exec (ACP stdio and the SSH ``ProxyCommand``),
* copy host files into a member.

The pre-existing Docker call sites keep building their exact ``docker
exec``/``docker cp`` argv. :func:`run_routed` and :func:`stream_routed` sit at
their subprocess boundary: a command aimed at a member owned by an external
driver is re-targeted at that driver; every other command is left untouched,
so Docker behavior is byte-for-byte unchanged. Members become owned by a
driver when discovery (:func:`list_driver_members`) or provisioning reports
them, which every dispatch path performs before it executes anything.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import INTERNAL_LIFECYCLE_OWNER, ContainersConfig, FleetConfig

if TYPE_CHECKING:
    from .lifecycle import DockerContainerInfo

log = logging.getLogger("agent-containers")

DOCKER_DRIVER = "docker"
DRIVER_LABEL = "agent-containers.driver"
INSTANCE_LABEL = "agent-containers.instance"

# Docker/devcontainer provisioning keys a driver fleet must not set: the
# driver owns image selection, workspace setup and container creation.
DRIVER_CONFLICTING_KEYS = (
    "repo", "devcontainer_path", "devcontainer_config", "image",
    "network", "memory", "cpus", "pids_limit", "workspace_size", "home_size",
    "host_workspace_path", "host_home_path", "home_folder", "systemd_capable",
    "native_inference", "dispatch_only",
)


def _is_set(value: Any) -> bool:
    return not (value is None or value is False or value == "")


def validate_driver_fleet(fleet: FleetConfig) -> None:
    """Validate ``driver``/``driver_options`` and reject conflicting keys."""
    import json
    import re

    if fleet.driver is None:
        if fleet.driver_options:
            raise RuntimeError("driver_options requires 'driver'")
        return
    if not isinstance(fleet.driver, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{0,63}", fleet.driver
    ):
        raise RuntimeError(
            "driver must be a lowercase driver name "
            "([a-z0-9][a-z0-9._-]{0,63})"
        )
    if fleet.driver == DOCKER_DRIVER:
        raise RuntimeError(
            "driver: docker is the built-in default; omit 'driver' instead"
        )
    if not isinstance(fleet.driver_options, dict):
        raise RuntimeError("driver_options must be a key/value mapping")
    try:
        json.dumps(fleet.driver_options)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"driver_options must be JSON-serializable: {exc}"
        ) from exc
    if fleet.restricted:
        raise RuntimeError(
            "driver fleets must be trusted; the restricted profile requires "
            "the built-in Docker runtime"
        )
    if fleet.lifecycle_owner != INTERNAL_LIFECYCLE_OWNER:
        raise RuntimeError(
            "driver fleets are lifecycle-managed through their driver; "
            "lifecycle_owner must be agent-containers"
        )
    conflicting = [
        key for key in DRIVER_CONFLICTING_KEYS if _is_set(getattr(fleet, key))
    ]
    if conflicting:
        raise RuntimeError(
            f"driver fleets cannot set {', '.join(conflicting)}: the driver "
            "owns image, workspace and container settings; pass them through "
            "driver_options instead"
        )


@dataclass(frozen=True)
class DriverCapabilities:
    """What a runtime driver guarantees beyond the required verbs."""

    # The driver provides the in-container credential-relay env contract
    # (LC_GIT_CREDENTIAL_RELAY, _HOST, _TOKEN) itself, so neither
    # agent-containers nor agent-bridge wires a relay or reverse forward.
    owns_relay: bool = False
    # Members run sshd and the driver's streaming exec may run it as root,
    # so the trusted OpenSSH transport can ride on it as a ProxyCommand.
    ssh_transport: bool = False


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class DriverError(RuntimeError):
    """A typed runtime-driver failure (``code`` is the protocol error code)."""

    def __init__(self, driver: str, verb: str, code: str, message: str) -> None:
        super().__init__(
            f"runtime driver '{driver}' {verb} failed ({code}): {message}"
        )
        self.driver = driver
        self.verb = verb
        self.code = code


class RuntimeDriver(ABC):
    """Runtime operations for the members of one fleet."""

    name: str

    @property
    @abstractmethod
    def capabilities(self) -> DriverCapabilities: ...

    @abstractmethod
    def provision(
        self, *, name: str, workspace_folder: str, exec_user: str,
    ) -> str:
        """Create member ``name`` tagged with this fleet; return its name."""

    @abstractmethod
    def list_members(self) -> list[DockerContainerInfo]:
        """Every member of this fleet, including stopped ones."""

    # Every per-member operation accepts ``instance_id``: when set, the
    # operation must apply to that incarnation only and fail otherwise.

    @abstractmethod
    def inspect(self, name: str, *, instance_id: str | None = None) -> dict:
        """A Docker-inspect-shaped document (Id, Name, State, Config.Labels)."""

    @abstractmethod
    def state(self, name: str, *, instance_id: str | None = None) -> str | None:
        """The member's state, or ``None`` when it does not exist."""

    @abstractmethod
    def start(self, name: str, *, instance_id: str | None = None) -> None: ...

    @abstractmethod
    def stop(self, name: str, *, instance_id: str | None = None) -> None: ...

    @abstractmethod
    def remove(
        self, name: str, *, force: bool = False, instance_id: str | None = None,
    ) -> None: ...

    @abstractmethod
    def exec(
        self,
        name: str,
        argv: list[str],
        *,
        user: str | None = None,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        stdin: str | None = None,
        timeout: float = 30.0,
        instance_id: str | None = None,
    ) -> ExecResult:
        """Run ``argv`` to completion and capture its result."""

    @abstractmethod
    def stream_exec_argv(
        self,
        name: str,
        argv: list[str],
        *,
        user: str | None = None,
        env: dict[str, str] | None = None,
        inherit_env: list[str] | None = None,
        workdir: str | None = None,
        instance_id: str | None = None,
    ) -> list[str]:
        """Host argv that runs ``argv`` in the member with stdio passed through.

        ``env`` values appear in host argv and must never be secret; secrets
        are named in ``inherit_env`` and read from the spawned process's own
        environment.
        """

    @abstractmethod
    def copy_in(
        self, name: str, source: Path, target: str, *, instance_id: str | None = None,
    ) -> None:
        """Copy host path ``source`` into the member as ``target``."""


class DockerDriver(RuntimeDriver):
    """The built-in runtime: the existing Docker/devcontainer code paths."""

    name = DOCKER_DRIVER

    def __init__(self, config: ContainersConfig, fleet_name: str, fleet: FleetConfig):
        self.config = config
        self.fleet_name = fleet_name
        self.fleet = fleet

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(owns_relay=False, ssh_transport=True)

    def provision(self, *, name: str, workspace_folder: str, exec_user: str) -> str:
        from . import fleet as fleet_mod

        if self.fleet.devcontainer_path:
            return fleet_mod._devcontainer_up(
                self.fleet_name, self.fleet, name,
                dotfiles=self.config.dotfiles, harness=self.config.harness,
                exec_user=exec_user,
            )
        return fleet_mod._image_run(
            self.fleet_name, self.fleet, name,
            workspace_folder=workspace_folder, exec_user=exec_user,
        )

    def list_members(self) -> list[DockerContainerInfo]:
        from . import fleet as fleet_mod

        return fleet_mod._fleet_members(self.config, self.fleet_name)

    # Docker pins an incarnation by addressing its container id directly.

    def inspect(self, name, *, instance_id=None) -> dict:
        from . import lifecycle

        return lifecycle.inspect_container(instance_id or name)

    def state(self, name, *, instance_id=None) -> str | None:
        from . import lifecycle

        return lifecycle.inspect_state(instance_id or name)

    def start(self, name, *, instance_id=None) -> None:
        from . import lifecycle

        lifecycle.start_container(instance_id or name)

    def stop(self, name, *, instance_id=None) -> None:
        from . import lifecycle

        lifecycle.stop_container(instance_id or name)

    def remove(self, name, *, force=False, instance_id=None) -> None:
        from . import lifecycle

        lifecycle.remove_container(instance_id or name, force=force)

    def _exec_prefix(
        self, name, *, interactive, user, env, inherit_env, workdir,
    ) -> list[str]:
        args = ["docker", "exec"]
        if interactive:
            args.append("-i")
        for key, value in sorted((env or {}).items()):
            args += ["-e", f"{key}={value}"]
        for key in inherit_env or []:
            args += ["-e", key]
        if workdir:
            args += ["-w", workdir]
        if user:
            args += ["-u", user]
        return [*args, name]

    def exec(self, name, argv, *, user=None, env=None, workdir=None,
             stdin=None, timeout=30.0, instance_id=None) -> ExecResult:
        from agent_procutil import no_window_flags

        command = self._exec_prefix(
            instance_id or name, interactive=stdin is not None, user=user, env=env,
            inherit_env=None, workdir=workdir,
        ) + list(argv)
        try:
            done = subprocess.run(
                command, input=stdin, capture_output=True, text=True,
                timeout=timeout, creationflags=no_window_flags(),
            )
        except FileNotFoundError:
            raise RuntimeError("docker CLI not found on PATH") from None
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"docker exec {name} timed out") from exc
        return ExecResult(done.returncode, done.stdout, done.stderr)

    def stream_exec_argv(self, name, argv, *, user=None, env=None,
                         inherit_env=None, workdir=None, instance_id=None) -> list[str]:
        return self._exec_prefix(
            instance_id or name, interactive=True, user=user, env=env,
            inherit_env=inherit_env, workdir=workdir,
        ) + list(argv)

    def copy_in(self, name, source, target, *, instance_id=None) -> None:
        from . import lifecycle

        res = lifecycle._docker(
            ["cp", str(source), f"{instance_id or name}:{target}"], timeout=300,
        )
        if res.returncode != 0:
            raise RuntimeError(f"docker cp into {name} failed: {res.stderr.strip()}")


# ---------------------------------------------------------------------------
# Driver selection and member ownership


_lock = threading.Lock()
_drivers: dict[tuple[str, str], RuntimeDriver] = {}
# routing handle (member name or instance id) -> (driver, member name)
_owned: dict[str, tuple[RuntimeDriver, str]] = {}
# handles reported for different members/runtimes; never routed
_ambiguous: set[str] = set()


@dataclass(frozen=True)
class Route:
    """An externally owned member as addressed by one caller.

    ``instance_id`` is set when the caller addressed a specific incarnation
    (an instance id rather than the mutable name); every driver request then
    carries it so a recreated member is refused instead of acted on.
    """

    driver: RuntimeDriver
    name: str
    instance_id: str | None


def driver_for_fleet(
    config: ContainersConfig, fleet_name: str, fleet: FleetConfig,
) -> RuntimeDriver:
    """Resolve the runtime driver for one fleet (Docker when unset)."""
    if not fleet.driver:
        return DockerDriver(config, fleet_name, fleet)
    from .external_driver import ExternalDriver, find_driver

    key = (fleet_name, fleet.driver)
    with _lock:
        cached = _drivers.get(key)
        if cached is not None and getattr(cached, "fleet", None) == fleet:
            return cached
    spec = find_driver(fleet.driver)
    driver = ExternalDriver(spec, fleet_name, fleet)
    with _lock:
        _drivers[key] = driver
    return driver


def _runtime_key(driver: RuntimeDriver) -> tuple[str, str | None]:
    return driver.name, getattr(driver, "fleet_name", None)


def bind_member(driver: RuntimeDriver, info: DockerContainerInfo) -> None:
    """Record that ``info`` lives in ``driver``'s runtime.

    A handle already bound to a different member or runtime (for example two
    drivers both reporting instance id ``1``) becomes ambiguous and is
    refused rather than silently re-pointed.
    """
    with _lock:
        for handle in {info.name, info.container_id} - {""}:
            existing = _owned.get(handle)
            if existing is not None and (
                _runtime_key(existing[0]) != _runtime_key(driver)
                or existing[1] != info.name
            ):
                _ambiguous.add(handle)
                continue
            _owned[handle] = (driver, info.name)


def owner_of(target: str) -> Route | None:
    """The external-driver route for member/instance ``target``, if any."""
    with _lock:
        if target in _ambiguous:
            raise RuntimeError(
                f"'{target}' identifies members of more than one runtime "
                "driver; address the member by its unique fleet name"
            )
        entry = _owned.get(target)
    if entry is None:
        return None
    driver, name = entry
    return Route(driver, name, None if target == name else target)


def dispatch(target: str, operation: str, **kwargs: Any) -> tuple[bool, Any]:
    """Run a lifecycle ``operation`` on an externally owned member.

    Returns ``(False, None)`` when ``target`` is not owned by an external
    driver, so the caller continues on its Docker path.
    """
    route = owner_of(target)
    if route is None:
        return False, None
    method = getattr(route.driver, operation)
    return True, method(route.name, instance_id=route.instance_id, **kwargs)


def require_docker_member(target: str, operation: str) -> None:
    route = owner_of(target)
    if route is not None:
        raise RuntimeError(
            f"{operation} is not supported for '{route.name}' (runtime driver "
            f"'{route.driver.name}')"
        )


def clear_bindings() -> None:
    with _lock:
        _owned.clear()
        _ambiguous.clear()
        _drivers.clear()


def has_docker_fleets(config: ContainersConfig) -> bool:
    return not config.fleets or any(not f.driver for f in config.fleets.values())


def list_driver_members(config: ContainersConfig) -> list[DockerContainerInfo]:
    """Enumerate every external-driver fleet; one failing driver is skipped."""
    members: list[DockerContainerInfo] = []
    for fleet_name, fleet in config.fleets.items():
        if not fleet.driver:
            continue
        try:
            members.extend(
                driver_for_fleet(config, fleet_name, fleet).list_members()
            )
        except RuntimeError as exc:
            log.warning("Runtime driver for fleet '%s' is unavailable: %s", fleet_name, exc)
    return members


def merge_driver_members(
    config: ContainersConfig, containers: list, all_containers: bool,
) -> list:
    """Append driver-reported members to a Docker inventory.

    Docker rows that collide with a driver member's name or claim a driver
    fleet are dropped, so a runtime that is also visible to the Docker CLI
    never shows a member twice.
    """
    members = list_driver_members(config)
    if not members:
        return containers
    claimed = {m.name for m in members}
    driver_fleets = {n for n, f in config.fleets.items() if f.driver}
    kept = [c for c in containers if c.name not in claimed and c.fleet not in driver_fleets]
    if not all_containers:
        members = [m for m in members if m.is_running]
    return kept + members


# ---------------------------------------------------------------------------
# Subprocess-boundary routing for the existing Docker-shaped call sites


@dataclass
class _ExecArgs:
    target: str
    command: list[str]
    user: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    inherit_env: list[str] = field(default_factory=list)
    workdir: str | None = None
    interactive: bool = False
    unsupported: list[str] = field(default_factory=list)


_VALUE_OPTIONS = {"-u", "--user", "-e", "--env", "-w", "--workdir"}


def _parse_docker_exec(argv: list[str]) -> _ExecArgs | None:
    if len(argv) < 3 or argv[0] != "docker" or argv[1] != "exec":
        return None
    parsed = _ExecArgs(target="", command=[])
    index = 2
    while index < len(argv):
        option = argv[index]
        if option in _VALUE_OPTIONS and index + 1 < len(argv):
            value = argv[index + 1]
            if option in {"-u", "--user"}:
                parsed.user = value
            elif option in {"-w", "--workdir"}:
                parsed.workdir = value
            elif "=" in value:
                key, val = value.split("=", 1)
                parsed.env[key] = val
            else:
                parsed.inherit_env.append(value)
            index += 2
            continue
        if option.startswith("-"):
            letters = option[1:] if not option.startswith("--") else ""
            if option == "--interactive" or (letters and set(letters) <= {"i", "t"}):
                parsed.interactive = parsed.interactive or "i" in letters or option == "--interactive"
            elif option != "--tty":
                parsed.unsupported.append(option)
            index += 1
            continue
        break
    if index >= len(argv):
        return None
    parsed.target = argv[index]
    parsed.command = list(argv[index + 1:])
    return parsed


def _parse_docker_cp(argv: list[str]) -> tuple[str, str, str] | None:
    if len(argv) != 4 or argv[0] != "docker" or argv[1] != "cp":
        return None
    target, sep, path = argv[3].partition(":")
    if not sep or not target or not path:
        return None
    return argv[2], target, path


def run_routed(
    argv: list[str], *, input_text: str | None = None, timeout: float = 30.0,
) -> subprocess.CompletedProcess | None:
    """Run a Docker-shaped exec/cp through the owning external driver.

    Returns ``None`` (the caller runs ``argv`` unchanged) unless ``argv``
    targets a member owned by an external driver.
    """
    parsed = _parse_docker_exec(argv)
    if parsed is not None:
        route = owner_of(parsed.target)
        if route is None:
            return None
        driver, name = route.driver, route.name
        if parsed.unsupported:
            raise RuntimeError(
                f"runtime driver '{driver.name}' cannot express docker exec "
                f"options {parsed.unsupported}"
            )
        env = dict(parsed.env)
        for key in parsed.inherit_env:
            if key in os.environ:
                env[key] = os.environ[key]
        result = driver.exec(
            name, parsed.command, user=parsed.user, env=env,
            workdir=parsed.workdir,
            stdin=input_text if parsed.interactive else None,
            timeout=timeout, instance_id=route.instance_id,
        )
        return subprocess.CompletedProcess(
            [driver.name, "exec", name], result.exit_code, result.stdout, result.stderr,
        )
    copied = _parse_docker_cp(argv)
    if copied is not None:
        source, target, path = copied
        route = owner_of(target)
        if route is None:
            return None
        route.driver.copy_in(
            route.name, Path(source), path, instance_id=route.instance_id,
        )
        return subprocess.CompletedProcess(
            [route.driver.name, "copy-in", route.name], 0, "", "",
        )
    return None


def stream_routed(argv: list[str]) -> list[str]:
    """Re-target an interactive ``docker exec`` at its owning external driver."""
    parsed = _parse_docker_exec(argv)
    if parsed is None:
        return argv
    route = owner_of(parsed.target)
    if route is None:
        return argv
    if parsed.unsupported:
        raise RuntimeError(
            f"runtime driver '{route.driver.name}' cannot express docker exec "
            f"options {parsed.unsupported}"
        )
    return route.driver.stream_exec_argv(
        route.name, parsed.command, user=parsed.user, env=parsed.env,
        inherit_env=parsed.inherit_env, workdir=parsed.workdir,
        instance_id=route.instance_id,
    )


# ---------------------------------------------------------------------------
# Launch planning


@dataclass(frozen=True)
class LaunchPlan:
    """How a trusted ACP launch reaches one member."""

    driver: RuntimeDriver | None  # None: built-in Docker
    ssh: bool  # trusted OpenSSH transport (else direct streaming exec)
    agent_relay: bool  # agent-containers/agent-bridge wire the relay


def launch_plan(
    fleet: FleetConfig, name: str, relay_enabled: bool, *, require_ssh: bool = False,
) -> LaunchPlan:
    """Resolve transport and relay wiring, failing closed on missing capability.

    ``require_ssh`` is for SSH-only launches (interactive ``copilot`` and
    Session Host), which a driver without ``ssh_transport`` cannot serve.
    """
    if not getattr(fleet, "driver", None):
        return LaunchPlan(None, True, relay_enabled)
    route = owner_of(name)
    if route is None:
        raise RuntimeError(
            f"Container '{name}' belongs to driver fleet but was not reported "
            f"by runtime driver '{fleet.driver}'; refusing dispatch"
        )
    driver = route.driver
    caps = driver.capabilities
    if require_ssh and not caps.ssh_transport:
        raise RuntimeError(
            f"Container '{name}': this launch needs the trusted SSH transport, "
            f"which runtime driver '{driver.name}' does not support"
        )
    agent_relay = relay_enabled and not caps.owns_relay
    if agent_relay and not caps.ssh_transport:
        raise RuntimeError(
            f"Container '{name}': the credential relay is enabled but runtime "
            f"driver '{driver.name}' neither owns the relay (owns_relay) nor "
            "supports the SSH transport (ssh_transport) needed to forward it; "
            "set relay_enabled: false for this fleet or use a driver with one "
            "of those capabilities"
        )
    return LaunchPlan(driver, caps.ssh_transport, agent_relay)


def discover(config: ContainersConfig) -> None:
    """Bind driver members for entry points that address one directly.

    Fresh CLI processes that receive a member name or instance id without
    first enumerating the fleet call this so the id routes to its driver.
    """
    if any(f.driver for f in config.fleets.values()):
        list_driver_members(config)


def require_ssh_transport(config: ContainersConfig, name: str, operation: str) -> None:
    """Refuse an SSH-only operation on a driver member that lacks SSH."""
    if not any(f.driver for f in config.fleets.values()):
        return
    from .lifecycle import get_container

    info = get_container(config, name)
    if info is None or not getattr(info, "driver", None):
        return
    route = owner_of(name)
    if route is None or not route.driver.capabilities.ssh_transport:
        raise RuntimeError(
            f"{operation} needs the trusted SSH transport, which runtime "
            f"driver '{info.driver}' does not support for '{name}'"
        )
