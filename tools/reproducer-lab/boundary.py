"""Pure declaration and static observation controls; never admit or start a lab."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

SCHEMA: Final = "copilot-extensions.reproducer-lab"
_HEX: Final = re.compile(r"[0-9a-f]{64}")
_NAME: Final = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_GIB: Final = 1024**3
_MIB: Final = 1024**2
_LIMITS: Final = {
    "memory_bytes": 8 * _GIB,
    "cpus": 4,
    "pids": 256,
    "persistent_bytes": 4 * _GIB,
    "tmp_bytes": 512 * _MIB,
    "run_bytes": 64 * _MIB,
    "output_bytes": 64 * _MIB,
}
_DESTINATIONS: Final = {
    "home": "/home/lab",
    "state": "/lab/state",
    "workspace": "/lab/workspace",
    "evidence": "/lab/evidence",
}


class DeclarationError(ValueError):
    """A declaration is malformed or exceeds this experiment tool's scope."""


@dataclass(frozen=True)
class Budgets:
    memory_bytes: int
    cpus: int
    pids: int
    persistent_bytes: int
    tmp_bytes: int
    run_bytes: int
    output_bytes: int


@dataclass(frozen=True)
class Experiment:
    name: str
    image: str
    source_sha: str
    budgets: Budgets

    @property
    def resource_prefix(self) -> str:
        return f"reproducer-lab-{self.name}"

    @property
    def volumes(self) -> dict[str, str]:
        return {
            destination: f"{self.resource_prefix}-{role}"
            for role, destination in _DESTINATIONS.items()
        }


@dataclass(frozen=True)
class StaticObservation:
    violations: tuple[str, ...]
    unresolved: tuple[str, ...]


def _object(value: object, keys: set[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DeclarationError(f"{field} must contain exactly the supported fields")
    return value


def parse_declaration(value: object) -> Experiment:
    """Parse finite synthetic inputs, without reading config or ambient state."""
    doc = _object(value, {"schema", "version", "name", "image", "source_sha", "budgets"}, "declaration")
    if doc["schema"] != SCHEMA or type(doc["version"]) is not int or doc["version"] != 1:
        raise DeclarationError("unsupported declaration schema/version")
    name = doc["name"]
    if not isinstance(name, str) or len(name) > 40 or _NAME.fullmatch(name) is None:
        raise DeclarationError("name must be a short lowercase experiment name")
    image = doc["image"]
    if not isinstance(image, str) or not image.startswith("sha256:") or not _HEX.fullmatch(image[7:]):
        raise DeclarationError("image must be an immutable sha256 image ID")
    source_sha = doc["source_sha"]
    if not isinstance(source_sha, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source_sha) is None:
        raise DeclarationError("source_sha must be a full immutable Git object ID")
    raw = _object(doc["budgets"], set(_LIMITS), "budgets")
    budgets: dict[str, int] = {}
    for field, maximum in _LIMITS.items():
        amount = raw[field]
        if type(amount) is not int or not 0 < amount <= maximum:
            raise DeclarationError(f"{field} must be a positive integer within the supported ceiling")
        budgets[field] = amount
    return Experiment(name, image, source_sha, Budgets(**budgets))


def inspect_static_boundary(
    experiment: Experiment, observed: Mapping[str, object],
) -> StaticObservation:
    """Reject known unsafe Docker inspect shapes, without claiming readiness.

    Observations must eventually come from a real host-owned bounded reader.
    Caller-supplied dictionaries are test inputs, never capacity or custody proof.
    """
    violations: list[str] = []
    host = observed.get("HostConfig")
    config = observed.get("Config")
    if not isinstance(host, dict) or not isinstance(config, dict):
        return StaticObservation(("container inspection is incomplete",), _UNRESOLVED)
    if observed.get("Image") != experiment.image:
        violations.append("physical image differs from the declaration")
    identity = observed.get("Id")
    if not isinstance(identity, str) or _HEX.fullmatch(identity) is None:
        violations.append("full physical container identity is missing")
    if config.get("User") != "1000:1000":
        violations.append("guest user must be the fixed non-root image user")
    if host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False:
        violations.append("read-only unprivileged root is not established")
    if (
        host.get("CapDrop") != ["ALL"]
        or "CapAdd" not in host
        or host["CapAdd"] not in (None, [])
    ):
        violations.append("all capabilities must be dropped without additions")
    if host.get("SecurityOpt") != ["no-new-privileges"]:
        violations.append("the fixed no-new-privileges security posture is missing")
    for field in (
        "Binds", "Devices", "DeviceRequests", "PortBindings", "ExtraHosts", "Links", "VolumesFrom",
    ):
        if field not in host or host[field] not in (None, [], {}):
            violations.append(f"forbidden Docker exposure: {field}")
    if host.get("PublishAllPorts") is not False:
        violations.append("published-port policy is missing or enabled")
    for field in ("PidMode", "IpcMode", "UTSMode", "UsernsMode"):
        if host.get(field) not in ("", "private"):
            violations.append(f"private namespace not established: {field}")
    if host.get("CgroupnsMode") != "private":
        violations.append("private namespace not established: CgroupnsMode")
    budgets = experiment.budgets
    for field, expected in (
        ("Memory", budgets.memory_bytes),
        ("MemorySwap", budgets.memory_bytes),
        ("NanoCpus", budgets.cpus * 10**9),
        ("PidsLimit", budgets.pids),
    ):
        if type(host.get(field)) is not int or host[field] != expected:
            violations.append(f"resource ceiling differs from declaration: {field}")
    if host.get("NetworkMode") != f"{experiment.resource_prefix}-internal":
        violations.append("network selection differs from the experiment")
    _check_mounts(experiment, observed.get("Mounts"), violations)
    _check_mount_declarations(experiment, host.get("Mounts"), violations)
    _check_volatile(experiment, host.get("Tmpfs"), violations)
    _check_environment(config.get("Env"), violations)
    # Static inspection cannot establish persistence, actual egress or ownership.
    return StaticObservation(tuple(violations), _UNRESOLVED)


_UNRESOLVED: Final = (
    "fresh persistent/evidence usage and backing free-space monitoring must be measured separately",
    "effective internal-network membership and egress have not been probed",
    "exclusive resource and host-driver custody have not been established",
    "guest source, volatile mounts and runtime configuration have not been qualified",
    "no provisioning or strict pre-effect capability is supplied by static inspection",
)


def _check_mounts(experiment: Experiment, mounts: object, violations: list[str]) -> None:
    if not isinstance(mounts, list) or len(mounts) != len(_DESTINATIONS):
        violations.append("persistent mount inventory is incomplete or has extra mounts")
        return
    expected = experiment.volumes
    seen: set[str] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            violations.append("persistent mount observation is malformed")
            continue
        destination = mount.get("Destination")
        if not isinstance(destination, str) or destination not in expected or destination in seen:
            violations.append("persistent mount destination is foreign or duplicated")
            continue
        seen.add(destination)
        if (
            mount.get("Type") != "volume"
            or mount.get("Name") != expected[destination]
            or mount.get("RW") is not True
        ):
            violations.append("persistent mount is not the declared writable named volume")
    if seen != set(expected):
        violations.append("a required persistent destination is missing")


def _check_mount_declarations(experiment: Experiment, mounts: object, violations: list[str]) -> None:
    if not isinstance(mounts, list) or len(mounts) != len(experiment.volumes):
        violations.append("full-volume mount declarations are incomplete")
        return
    seen: set[str] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            violations.append("full-volume mount declaration is malformed")
            continue
        target = mount.get("Target")
        if not isinstance(target, str) or target not in experiment.volumes or target in seen:
            violations.append("full-volume mount target is foreign or duplicated")
            continue
        seen.add(target)
        options = mount.get("VolumeOptions", {})
        if (
            mount.get("Type") != "volume"
            or mount.get("Source") != experiment.volumes[target]
            or mount.get("ReadOnly", False) is not False
            or not isinstance(options, dict)
            or options.get("Subpath", "") != ""
        ):
            violations.append("persistent mount must expose the full declared writable volume")


def _check_environment(environment: object, violations: list[str]) -> None:
    expected = {
        "HOME": "/home/lab",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONUNBUFFERED": "1",
    }
    if not isinstance(environment, list) or len(environment) != len(expected):
        violations.append("guest environment must contain only the fixed synthetic baseline")
        return
    seen: dict[str, str] = {}
    for entry in environment:
        if not isinstance(entry, str) or "=" not in entry:
            violations.append("guest environment is malformed")
            return
        name, value = entry.split("=", 1)
        if name in seen:
            violations.append("guest environment contains a duplicate key")
            return
        seen[name] = value
    if seen != expected:
        violations.append("guest environment differs from the fixed synthetic baseline")


def _check_volatile(experiment: Experiment, tmpfs: object, violations: list[str]) -> None:
    expected = {
        "/tmp": experiment.budgets.tmp_bytes,
        "/run": experiment.budgets.run_bytes,
    }
    if not isinstance(tmpfs, dict) or set(tmpfs) != set(expected):
        violations.append("volatile mount inventory must contain only bounded /tmp and /run")
        return
    fixed = {"rw", "nosuid", "nodev", "uid=1000", "gid=1000", "mode=0700"}
    for destination, budget in expected.items():
        options = tmpfs[destination]
        if not isinstance(options, str):
            violations.append("volatile mount options are malformed")
            continue
        tokens = options.split(",")
        if (
            len(tokens) != len(set(tokens))
            or set(tokens) != fixed | {f"size={budget}"}
        ):
            violations.append("volatile mount flags or byte ceiling differ from declaration")
