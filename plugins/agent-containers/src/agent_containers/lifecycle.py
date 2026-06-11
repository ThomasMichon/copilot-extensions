"""Docker dev-container discovery and lifecycle.

Wraps ``docker`` CLI calls. Targets the Docker Desktop WSL2 backend, so
``docker exec`` reaches containers uniformly from Windows or WSL.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, field

from .config import FLEET_LABEL, ContainersConfig

log = logging.getLogger("agent-containers")

# Container states docker reports; we treat "running" as ready and
# "exited"/"created" as startable.
RUNNING = "running"
STARTABLE_STATES = {"exited", "created", "paused"}


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _docker(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run a docker CLI command, returning the CompletedProcess."""
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        raise RuntimeError("docker CLI not found on PATH") from None


def _check_docker() -> None:
    """Raise a helpful error if the docker daemon is unreachable."""
    res = _docker(["version", "--format", "{{.Server.Version}}"], timeout=15)
    if res.returncode != 0:
        raise RuntimeError(
            "Docker daemon not reachable. Is Docker Desktop running? "
            f"({res.stderr.strip()})"
        )


@dataclass
class DockerContainerInfo:
    """Summary of a Docker container relevant to the fleet."""

    name: str
    container_id: str
    image: str
    state: str  # running | exited | created | paused | ...
    status: str  # human-readable, e.g. "Up 3 minutes"
    labels: dict[str, str] = field(default_factory=dict)
    fleet: str | None = None
    local_folder: str | None = None  # devcontainer.local_folder, if present

    @property
    def is_running(self) -> bool:
        return self.state == RUNNING

    @property
    def repo(self) -> str:
        """Best-effort repo name from labels / local folder."""
        if self.local_folder:
            return self.local_folder.replace("\\", "/").rstrip("/").split("/")[-1]
        return self.fleet or ""


def _parse_labels(label_str: str) -> dict[str, str]:
    """Parse docker's comma-joined ``k=v`` label string."""
    labels: dict[str, str] = {}
    if not label_str:
        return labels
    for pair in label_str.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            labels[k.strip()] = v.strip()
    return labels


def _is_fleet_member(labels: dict[str, str], image: str, config: ContainersConfig) -> bool:
    """Decide whether a container belongs to the managed fleet.

    Preference order:
    1. Our own ``agent-containers.fleet`` label (set at ``up`` time).
    2. A ``devcontainer.local_folder`` label (VS Code / devcontainer CLI).
    3. Image-name prefix fallback (manually-built containers).
    """
    if FLEET_LABEL in labels:
        return True
    if "devcontainer.local_folder" in labels:
        return True
    return any(image.startswith(p) for p in config.image_prefixes)


# Tab-separated docker ps template. NOTE: `--format '{{json .}}'` is avoided
# because it is pathologically slow on Docker Desktop (tens of seconds vs.
# milliseconds for an explicit template). Order must match _PS_FIELDS.
_PS_FORMAT = "{{.Names}}\t{{.ID}}\t{{.Image}}\t{{.State}}\t{{.Status}}\t{{.Labels}}"
_PS_FIELD_COUNT = 6


def _row_to_info(
    line: str, config: ContainersConfig
) -> DockerContainerInfo | None:
    """Parse one tab-separated ``docker ps`` row into a DockerContainerInfo.

    Returns None for malformed rows or containers that are not fleet members.
    """
    parts = line.rstrip("\n").split("\t")
    if len(parts) < _PS_FIELD_COUNT:
        return None
    name, cid, image, state, status, label_str = parts[:_PS_FIELD_COUNT]
    labels = _parse_labels(label_str)
    if not _is_fleet_member(labels, image, config):
        return None
    return DockerContainerInfo(
        name=name,
        container_id=cid,
        image=image,
        state=state.lower(),
        status=status,
        labels=labels,
        fleet=labels.get(FLEET_LABEL),
        local_folder=labels.get("devcontainer.local_folder"),
    )


def list_containers(
    config: ContainersConfig, all_containers: bool = True
) -> list[DockerContainerInfo]:
    """List fleet-relevant containers via ``docker ps``.

    Includes stopped containers by default (``-a``) so warm-but-stopped
    fleet members are visible. Filters to fleet members per
    :func:`_is_fleet_member`.
    """
    _check_docker()
    args = ["ps", "--no-trunc", "--format", _PS_FORMAT]
    if all_containers:
        args.insert(1, "-a")

    res = _docker(args)
    if res.returncode != 0:
        raise RuntimeError(f"docker ps failed: {res.stderr.strip()}")

    containers: list[DockerContainerInfo] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        info = _row_to_info(line, config)
        if info is not None:
            containers.append(info)
    return containers


def get_container(config: ContainersConfig, name: str) -> DockerContainerInfo | None:
    """Return info for a single container by name, or None."""
    for c in list_containers(config):
        if c.name == name:
            return c
    return None


def inspect_state(name: str) -> str | None:
    """Return the container's state string, or None if it does not exist."""
    res = _docker(["inspect", "-f", "{{.State.Status}}", name])
    if res.returncode != 0:
        return None
    return res.stdout.strip().lower() or None


def start_container(name: str, timeout: float = 60.0) -> None:
    """Start a stopped container (idempotent if already running)."""
    res = _docker(["start", name], timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"docker start {name} failed: {res.stderr.strip()}")


def stop_container(name: str, timeout: float = 60.0) -> None:
    """Stop a running container (idempotent if already stopped)."""
    res = _docker(["stop", name], timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"docker stop {name} failed: {res.stderr.strip()}")


def remove_container(name: str, force: bool = False) -> None:
    """Remove a container."""
    args = ["rm", name]
    if force:
        args.insert(1, "-f")
    res = _docker(args)
    if res.returncode != 0:
        raise RuntimeError(f"docker rm {name} failed: {res.stderr.strip()}")
