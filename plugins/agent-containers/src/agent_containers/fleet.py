"""Fleet provisioning -- create/start/stop/remove a pool of dev containers.

A *fleet* is a named pool of long-lived dev containers built from one
devcontainer spec. Containers are kept warm (stopped, not destroyed) between
uses; an effort borrows one via the lease broker.

Two provisioning backends:

* ``devcontainer_path`` set -> use the ``devcontainer`` CLI (full lifecycle:
  build, onCreate clone + rush install, postStart). Each instance is tagged
  with id-labels (including ``agent-containers.fleet``) and renamed to
  ``<prefix>-<n>``. This is Model A (repo cloned inside the container).
* ``image`` set -> ``docker run`` a warm container directly (lightweight; for
  images that already carry their tooling).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys

from .config import FLEET_LABEL, ContainersConfig, DotfilesConfig, FleetConfig, HarnessConfig
from .lifecycle import (
    DockerContainerInfo,
    _check_docker,
    _docker,
    list_containers,
    remove_container,
    start_container,
    stop_container,
)

log = logging.getLogger("agent-containers")


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _fleet_members(config: ContainersConfig, fleet_name: str) -> list[DockerContainerInfo]:
    """All existing containers belonging to a fleet."""
    members = list_containers(config)
    prefix = None
    fleet = config.fleets.get(fleet_name)
    if fleet:
        prefix = fleet.prefix(fleet_name)
    out = []
    for c in members:
        if c.fleet == fleet_name:
            out.append(c)
        elif prefix and c.name.startswith(f"{prefix}-"):
            out.append(c)
    return out


def _next_indices(existing: list[DockerContainerInfo], prefix: str, count: int) -> list[int]:
    """Return ``count`` instance indices not already used by ``existing``."""
    used = set()
    for c in existing:
        suffix = c.name[len(prefix) + 1 :] if c.name.startswith(f"{prefix}-") else ""
        if suffix.isdigit():
            used.add(int(suffix))
    indices = []
    n = 1
    while len(indices) < count:
        if n not in used:
            indices.append(n)
        n += 1
    return indices


def _devcontainer_up(
    fleet_name: str,
    fleet: FleetConfig,
    name: str,
    dotfiles: DotfilesConfig | None = None,
    harness: HarnessConfig | None = None,
    exec_user: str = "vscode",
) -> str:
    """Bring up one container via the devcontainer CLI; return its name.

    Tags the container with ``agent-containers.fleet`` (via id-label, which
    devcontainer applies as a docker label) and renames it to ``name``. When
    ``fleet.devcontainer_config`` is set it is passed as ``--config`` (for
    nested specs). When ``dotfiles.repo`` is set the host dotfiles repo is
    reproduced inside the container after creation (via ``docker cp``); likewise
    ``harness.repo`` reproduces the control-plane harness checkout at its
    (distinct) ``target``.
    """
    devcontainer_exe = shutil.which("devcontainer")
    if not devcontainer_exe:
        raise RuntimeError(
            "devcontainer CLI not found. Install with "
            "`npm i -g @devcontainers/cli`, or use an image-based fleet."
        )
    args = [
        devcontainer_exe, "up",
        "--workspace-folder", fleet.devcontainer_path,
        "--id-label", f"{FLEET_LABEL}={fleet_name}",
        "--id-label", f"agent-containers.instance={name}",
    ]
    config_path = fleet.resolved_config()
    if config_path:
        args += ["--config", config_path]
    log.info("devcontainer up: %s", " ".join(args))
    res = subprocess.run(
        args, capture_output=True, text=True, timeout=1800,
        creationflags=_creation_flags(),
    )
    if res.returncode != 0:
        raise RuntimeError(f"devcontainer up failed for {name}: {res.stderr.strip()}")

    container_id = None
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        container_id = obj.get("containerId") or container_id
    if not container_id:
        raise RuntimeError(
            f"Could not determine containerId from devcontainer up output for {name}"
        )

    rename = _docker(["rename", container_id, name])
    if rename.returncode != 0:
        log.warning("Could not rename %s to %s: %s", container_id, name, rename.stderr.strip())
        name = container_id

    if dotfiles and dotfiles.host_repo():
        _materialize_repo(name, exec_user, dotfiles, label="dotfiles")
    if harness and harness.host_repo():
        _materialize_repo(name, exec_user, harness, label="harness")
    return name


def _materialize_repo(
    container: str, user: str, spec: DotfilesConfig | HarnessConfig, *, label: str,
) -> None:
    """Reproduce a host repo (``spec.repo``) inside the container (copy + optional
    install).

    Copies the host repo into the container at ``spec.target`` via ``docker cp``
    (the host checkout is only read, never mounted, so it is never mutated),
    chowns it to the remote user, then runs ``spec.install_command`` (if any) in
    ``target`` as that user. Used for BOTH the dotfiles shim (``label`` =
    ``"dotfiles"``, runs ``install.sh``) and the control-plane harness (``label``
    = ``"harness"``, no install by default). Best-effort: a failed copy/install
    is warned about, never fatal (the container is already usable).
    """
    host_repo = spec.host_repo()
    if host_repo is None:
        return
    target = spec.target

    mk = _docker(
        ["exec", "-u", "0", container, "bash", "-lc", f"mkdir -p {target}"],
        timeout=60,
    )
    if mk.returncode != 0:
        log.warning(
            "%s target mkdir failed in %s: %s",
            label, container, mk.stderr.strip() or mk.stdout.strip(),
        )
        return
    cp = _docker(
        ["cp", f"{host_repo.as_posix()}/.", f"{container}:{target}"], timeout=300
    )
    if cp.returncode != 0:
        log.warning(
            "%s copy into %s failed: %s",
            label, container, cp.stderr.strip() or cp.stdout.strip(),
        )
        return
    chown = _docker(
        ["exec", "-u", "0", container, "chown", "-R", f"{user}:{user}", target],
        timeout=120,
    )
    if chown.returncode != 0:
        log.warning(
            "%s chown in %s failed (continuing): %s",
            label, container, chown.stderr.strip() or chown.stdout.strip(),
        )
    log.info("Reproduced %s repo at %s in %s", label, target, container)

    if not spec.install_command:
        return
    res = _docker(
        [
            "exec", "-u", user, "-w", target, container,
            "bash", "-lc", spec.install_command,
        ],
        timeout=600,
    )
    if res.returncode != 0:
        log.warning(
            "%s install_command failed in %s (non-fatal): %s",
            label, container, res.stderr.strip() or res.stdout.strip(),
        )
    else:
        log.info("Ran %s install_command in %s", label, container)


def _image_run(fleet_name: str, fleet: FleetConfig, name: str) -> str:
    """Run one warm container directly from an image; return its name."""
    args = [
        "run", "-d",
        "--name", name,
        "--label", f"{FLEET_LABEL}={fleet_name}",
        "--add-host=host.docker.internal:host-gateway",
        fleet.image, "sleep", "infinity",
    ]
    res = _docker(args, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"docker run failed for {name}: {res.stderr.strip()}")
    return name


def up(config: ContainersConfig, fleet_name: str, count: int | None = None) -> list[str]:
    """Provision (or top up) a fleet to ``count`` containers.

    Returns the names of containers created during this call. Existing
    members are left in place (warm reuse).
    """
    _check_docker()
    fleet = config.fleets.get(fleet_name)
    if fleet is None:
        raise RuntimeError(
            f"Fleet '{fleet_name}' is not defined in containers.yaml"
        )
    if not fleet.devcontainer_path and not fleet.image:
        raise RuntimeError(
            f"Fleet '{fleet_name}' needs either 'devcontainer_path' or 'image'"
        )

    target = count if count is not None else fleet.size
    existing = _fleet_members(config, fleet_name)
    need = target - len(existing)
    if need <= 0:
        log.info(
            "Fleet '%s' already has %d/%d containers", fleet_name, len(existing), target
        )
        return []

    prefix = fleet.prefix(fleet_name)
    indices = _next_indices(existing, prefix, need)
    exec_user = fleet.exec_user or config.exec_user
    created: list[str] = []
    for idx in indices:
        name = f"{prefix}-{idx}"
        log.info("Provisioning fleet container %s", name)
        if fleet.devcontainer_path:
            created.append(
                _devcontainer_up(
                    fleet_name, fleet, name,
                    dotfiles=config.dotfiles, harness=config.harness,
                    exec_user=exec_user,
                )
            )
        else:
            created.append(_image_run(fleet_name, fleet, name))
    return created


def down(config: ContainersConfig, fleet_name: str) -> list[str]:
    """Stop all running containers in a fleet (kept warm, not removed)."""
    stopped = []
    for c in _fleet_members(config, fleet_name):
        if c.is_running:
            stop_container(c.name)
            stopped.append(c.name)
    return stopped


def start(config: ContainersConfig, fleet_name: str) -> list[str]:
    """Start all stopped containers in a fleet."""
    started = []
    for c in _fleet_members(config, fleet_name):
        if not c.is_running:
            start_container(c.name)
            started.append(c.name)
    return started


def rm(config: ContainersConfig, fleet_name: str, force: bool = False) -> list[str]:
    """Remove all containers in a fleet (destructive)."""
    removed = []
    for c in _fleet_members(config, fleet_name):
        remove_container(c.name, force=force)
        removed.append(c.name)
    return removed
