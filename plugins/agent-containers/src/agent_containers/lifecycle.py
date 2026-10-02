"""Docker dev-container discovery and lifecycle.

Wraps ``docker`` CLI calls. Targets the Docker Desktop WSL2 backend, so
``docker exec`` reaches containers uniformly from Windows or WSL.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field

from agent_procutil import no_window_flags

from .config import (
    FLEET_LABEL,
    SECURITY_GID_LABEL,
    SECURITY_HOME_LABEL,
    SECURITY_IMAGE_ID_LABEL,
    SECURITY_POLICY_LABEL,
    SECURITY_PROFILE_LABEL,
    SECURITY_UID_LABEL,
    TRUSTED_PROFILE,
    ContainersConfig,
    FleetConfig,
    is_sensitive_environment_name,
)

log = logging.getLogger("agent-containers")

# Container states docker reports; we treat "running" as ready and
# "exited"/"created" as startable.
RUNNING = "running"
STARTABLE_STATES = {"exited", "created", "paused"}
_SAFE_DOCKER_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_EXEC_OPTIONS_WITH_VALUE = {
    "-e",
    "--env",
    "--env-file",
    "-u",
    "--user",
    "-w",
    "--workdir",
    "--detach-keys",
}


def _creation_flags() -> int:
    return no_window_flags()


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
    except subprocess.TimeoutExpired as exc:
        command = _docker_operation_label(args)
        raise RuntimeError(
            f"{command} timed out after {timeout:.0f}s"
        ) from exc


def _docker_operation_label(args: list[str]) -> str:
    """Return a minimal timeout label without environment/command payloads."""
    if not args:
        return "docker operation"
    verb = args[0]
    target = None
    if verb == "exec":
        index = 1
        while index < len(args):
            value = args[index]
            if value in _EXEC_OPTIONS_WITH_VALUE:
                index += 2
                continue
            if value.startswith("-"):
                index += 1
                continue
            target = value
            break
    elif verb in {"inspect", "start", "stop", "unpause", "rm"} and len(args) > 1:
        target = args[-1]
    safe_verb = verb if re.fullmatch(r"[a-z][a-z0-9-]*", verb) else "operation"
    label = f"docker {safe_verb}"
    if target and _SAFE_DOCKER_TARGET_RE.fullmatch(target):
        label += f" {target}"
    return label


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
    security_profile: str = TRUSTED_PROFILE
    security_policy: str | None = None
    security_image_id: str | None = None

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
_PS_FORMAT = (
    '{{.Names}}\t{{.ID}}\t{{.Image}}\t{{.State}}\t{{.Status}}\t{{.Labels}}'
    f'\t{{{{.Label "{SECURITY_PROFILE_LABEL}"}}}}'
    f'\t{{{{.Label "{SECURITY_POLICY_LABEL}"}}}}'
    f'\t{{{{.Label "{SECURITY_IMAGE_ID_LABEL}"}}}}'
)
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
    profile = parts[6] if len(parts) > 6 and parts[6] else "unknown"
    policy = parts[7] if len(parts) > 7 and parts[7] else None
    image_id = parts[8] if len(parts) > 8 and parts[8] else None
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
        security_profile=profile,
        security_policy=policy,
        security_image_id=image_id,
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


def _parse_size(value: str) -> int:
    """Parse Docker-style byte sizes (for example 512m, 4g)."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?b?)?\s*", value.lower())
    if not match:
        raise ValueError(f"invalid size: {value}")
    amount = float(match.group(1))
    unit = (match.group(2) or "").rstrip("b")
    scale = {
        "": 1,
        "k": 1024,
        "m": 1024**2,
        "g": 1024**3,
        "t": 1024**4,
    }[unit]
    return int(amount * scale)


def inspect_container(name: str) -> dict:
    """Return one container's Docker inspect document."""
    res = _docker(["inspect", name], timeout=30)
    if res.returncode != 0:
        raise RuntimeError(
            f"docker inspect {name} failed: {res.stderr.strip() or res.stdout.strip()}"
        )
    try:
        rows = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"docker inspect {name} returned invalid JSON") from exc
    if not rows:
        raise RuntimeError(f"docker inspect {name} returned no container")
    return rows[0]


def restricted_policy_errors(
    info: DockerContainerInfo,
    fleet: FleetConfig,
    *,
    workspace_folder: str,
    exec_user: str,
    inspected: dict | None = None,
    migrating: bool = False,
) -> list[str]:
    """Inspect and validate the effective Docker boundary for a restricted fleet.

    ``migrating`` skips only the checks that compare against ``fleet`` (the
    CURRENT containers.yaml config) and necessarily fail across a deliberate
    restricted->trusted migration: image, policy fingerprint, explicit
    environment, and exact network/memory/cpu/pids/tmpfs-size VALUES. Every
    FIXED security invariant about the observed container's own build still
    applies unconditionally -- profile label, home/uid/gid, no bind mounts,
    no privileged/extra capabilities, read-only rootfs, no host device/
    namespace/port exposure, no credential-shaped env, network isolation
    (``none`` or exclusively Docker-internal networks), real positive
    memory/cpu/pids bounds (with memory==swap), and each tmpfs surface's
    fixed mount flags plus a real positive ``size=`` budget (its exact
    value excepted).
    """
    errors: list[str] = []
    try:
        fleet.validate_restricted()
    except RuntimeError as exc:
        errors.append(str(exc))
    doc = inspected or inspect_container(info.name)
    host = doc.get("HostConfig") or {}
    container = doc.get("Config") or {}
    labels = container.get("Labels") or {}
    home = labels.get(SECURITY_HOME_LABEL)
    uid_text = labels.get(SECURITY_UID_LABEL)
    gid_text = labels.get(SECURITY_GID_LABEL)

    if labels.get(SECURITY_PROFILE_LABEL) != "restricted":
        errors.append("security profile label is not restricted")
    if not migrating:
        expected_policy = fleet.security_policy_fingerprint(workspace_folder, exec_user)
        if labels.get(SECURITY_POLICY_LABEL) != expected_policy:
            errors.append("security policy fingerprint is stale")
        if container.get("Image") != fleet.image:
            errors.append("container image differs from configured image")
        if labels.get(SECURITY_IMAGE_ID_LABEL) != doc.get("Image"):
            errors.append("container image ID differs from provisioned image ID")
        current_image = _docker(
            ["image", "inspect", "--format", "{{.Id}}", fleet.image],
            timeout=30,
        )
        current_image_id = (
            current_image.stdout.strip() if current_image.returncode == 0 else ""
        )
        if not current_image_id or current_image_id != doc.get("Image"):
            errors.append("configured image reference differs from running image ID")
    if not home or not str(home).startswith("/"):
        errors.append("restricted home label is missing or invalid")
    try:
        uid = int(uid_text)
        gid = int(gid_text)
        if uid <= 0 or gid <= 0:
            raise ValueError
    except (TypeError, ValueError):
        uid = gid = -1
        errors.append("restricted exec user must have non-root uid and gid")
    env = container.get("Env") or []
    env_map = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in env
        if "=" in item
    }
    if home and f"HOME={home}" not in env:
        errors.append("HOME does not target the restricted writable home")
    if not migrating:
        for name, expected in fleet.environment.items():
            if env_map.get(name) != expected:
                errors.append(
                    f"explicit environment '{name}' differs from configuration"
                )
    sensitive = sorted(
        name for name in env_map if is_sensitive_environment_name(name)
    )
    if sensitive:
        errors.append(
            "credential-shaped environment values are present: "
            + ", ".join(sensitive)
        )

    if host.get("ReadonlyRootfs") is not True:
        errors.append("root filesystem is not read-only")
    if host.get("Privileged"):
        errors.append("container is privileged")
    cap_drop = {str(v).upper() for v in (host.get("CapDrop") or [])}
    if "ALL" not in cap_drop:
        errors.append("all Linux capabilities are not dropped")
    if host.get("CapAdd"):
        errors.append("Linux capabilities are re-added")
    security_opt = [str(v).lower() for v in (host.get("SecurityOpt") or [])]
    if not any(v.startswith("no-new-privileges") for v in security_opt):
        errors.append("no-new-privileges is not enforced")
    if any("unconfined" in v for v in security_opt):
        errors.append("an unconfined security profile is present")
    if host.get("Binds"):
        errors.append("host bind mounts are present")
    if doc.get("Mounts"):
        errors.append("persistent or image-declared mounts are present")
    if host.get("Devices") or host.get("DeviceRequests"):
        errors.append("host device access is present")
    for key in ("PidMode", "IpcMode", "UTSMode", "UsernsMode"):
        value = str(host.get(key) or "")
        if value == "host" or value.startswith("container:"):
            errors.append(f"{key} shares another namespace")
    if host.get("PortBindings") or host.get("PublishAllPorts"):
        errors.append("published ports are present")
    if host.get("ExtraHosts"):
        errors.append("extra host mappings are present")

    attached_networks = set(
        ((doc.get("NetworkSettings") or {}).get("Networks") or {}).keys()
    )
    if migrating:
        # FIXED invariant independent of the current fleet's configured
        # network name: the container must be network-isolated -- either
        # no network at all (with nothing attached), or every attached
        # network verified Docker-internal. An uninspectable non-"none"
        # mode (e.g. a namespace-sharing "container:<id>" with no own
        # Networks entries) is rejected rather than treated as safe.
        network_mode = host.get("NetworkMode")
        if network_mode == "none":
            # Docker's real shape for --network none: NetworkSettings
            # reports exactly one entry keyed "none" -> {} -- not an
            # absence of entries.
            if attached_networks != {"none"}:
                errors.append(
                    "network mode is 'none' but attached networks are unexpected"
                )
        elif not attached_networks:
            errors.append(
                f"network mode {network_mode!r} has no inspectable attached networks"
            )
        else:
            for net_name in attached_networks:
                inspected_net = _docker(["network", "inspect", net_name], timeout=30)
                try:
                    net_docs = (
                        json.loads(inspected_net.stdout)
                        if inspected_net.returncode == 0
                        else []
                    )
                except json.JSONDecodeError:
                    net_docs = []
                if not net_docs or not net_docs[0].get("Internal"):
                    errors.append(
                        f"attached network {net_name!r} is not Docker-internal"
                    )
    else:
        expected_network = fleet.effective_network()
        if host.get("NetworkMode") != expected_network:
            errors.append("network mode differs from configured restricted network")
        expected_networks = {expected_network} if expected_network else set()
        if attached_networks != expected_networks:
            errors.append("attached networks differ from configured restricted network")
        if expected_network != "none":
            network = _docker(["network", "inspect", expected_network], timeout=30)
            try:
                network_docs = json.loads(network.stdout) if network.returncode == 0 else []
            except json.JSONDecodeError:
                network_docs = []
            if not network_docs or not network_docs[0].get("Internal"):
                errors.append("configured restricted network is not Docker-internal")
            else:
                attached = (
                    ((doc.get("NetworkSettings") or {}).get("Networks") or {}).get(
                        expected_network
                    )
                    or {}
                )
                if attached.get("NetworkID") != network_docs[0].get("Id"):
                    errors.append("attached network ID differs from configured network")

    if migrating:
        # FIXED invariants independent of the current fleet's configured
        # values: real, positive bounds on memory/cpu/pids, and no extra
        # swap beyond the memory limit -- only the exact configured
        # values are exempt.
        observed_memory = int(host.get("Memory") or 0)
        if observed_memory <= 0:
            errors.append("memory limit is not a positive bound")
        elif int(host.get("MemorySwap") or 0) != observed_memory:
            errors.append("swap limit does not match the restricted no-extra-swap policy")
        if int(host.get("NanoCpus") or 0) <= 0:
            errors.append("CPU limit is not a positive bound")
        if int(host.get("PidsLimit") or 0) <= 0:
            errors.append("PID limit is not a positive bound")
    else:
        try:
            memory_bytes = _parse_size(fleet.effective_memory())
            if int(host.get("Memory") or 0) != memory_bytes:
                errors.append("memory limit differs from configured limit")
            if int(host.get("MemorySwap") or 0) != memory_bytes:
                errors.append("swap limit differs from configured memory limit")
        except (TypeError, ValueError):
            errors.append("memory limit is invalid")
        if int(host.get("NanoCpus") or 0) != int(fleet.effective_cpus() * 1_000_000_000):
            errors.append("CPU limit differs from configured limit")
        if int(host.get("PidsLimit") or 0) != fleet.effective_pids_limit():
            errors.append("PID limit differs from configured limit")

    tmpfs = host.get("Tmpfs") or {}
    if migrating:
        # ``workspace_folder`` is part of the OLD restricted policy
        # fingerprint -- it may have changed together with
        # security_profile, so requiring an exact match against the
        # CURRENT config's value would wrongly defer a migration whose
        # container is otherwise fully compliant. Derive the single
        # workspace-like surface from what's actually mounted instead.
        non_workspace = {home, "/tmp", "/run"}  # noqa: S108
        observed_workspace_candidates = set(tmpfs) - non_workspace
        if len(observed_workspace_candidates) != 1:
            errors.append("writable tmpfs surfaces differ from restricted policy")
            observed_workspace = None
        else:
            observed_workspace = next(iter(observed_workspace_candidates))
    else:
        required_tmpfs = {workspace_folder, home, "/tmp", "/run"}  # noqa: S108
        if set(tmpfs) != required_tmpfs:
            errors.append("writable tmpfs surfaces differ from restricted policy")
        observed_workspace = workspace_folder
    # FIXED flags every writable tmpfs surface must carry regardless of
    # profile/config: no setuid, no device nodes, owned by the restricted
    # exec user, private mode. Only the exact ``size=`` budget is
    # current-config-dependent (skipped during migration).
    base_flags = {"rw", "nosuid", "nodev", "exec", f"uid={uid}", f"gid={gid}", "mode=0700"}
    tmp_flags = {"rw", "nosuid", "nodev"}
    fixed_flags = {home: base_flags, "/tmp": tmp_flags, "/run": tmp_flags}  # noqa: S108
    if observed_workspace is not None:
        fixed_flags[observed_workspace] = base_flags
    expected_options = {
        workspace_folder: base_flags | {f"size={fleet.effective_workspace_size()}"},
        home: base_flags | {f"size={fleet.effective_home_size()}"},
        "/tmp": tmp_flags | {"size=512m"},  # noqa: S108
        "/run": tmp_flags | {"size=64m"},
    }
    for path, expected in (fixed_flags if migrating else expected_options).items():
        actual = set(str(tmpfs.get(path, "")).split(",")) if path else set()
        if migrating:
            size_tokens = [opt for opt in actual if opt.startswith("size=")]
            size_value = -1
            if len(size_tokens) == 1:
                try:
                    size_value = _parse_size(size_tokens[0].split("=", 1)[1])
                except (TypeError, ValueError):
                    size_value = -1
            if size_value <= 0:
                errors.append(f"{path} tmpfs size budget is missing or invalid")
            actual = {opt for opt in actual if not opt.startswith("size=")}
        if actual != expected:
            errors.append(f"{path} tmpfs options differ from restricted policy")


    return errors


def start_container(name: str, timeout: float = 60.0) -> None:
    """Start a stopped container (idempotent if already running)."""
    res = _docker(["start", name], timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"docker start {name} failed: {res.stderr.strip()}")


def unpause_container(name: str, timeout: float = 60.0) -> None:
    """Unpause a paused container so liveness and evidence can be inspected."""
    res = _docker(["unpause", name], timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"docker unpause {name} failed: {res.stderr.strip()}")


def stop_container(name: str, timeout: float = 60.0) -> None:
    """Stop a running container (idempotent if already stopped)."""
    res = _docker(["stop", name], timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"docker stop {name} failed: {res.stderr.strip()}")


def remove_container(
    name: str,
    force: bool = False,
    *,
    timeout: float = 120.0,
) -> None:
    """Remove a container."""
    args = ["rm", name]
    if force:
        args.insert(1, "-f")
    try:
        res = _docker(args, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"docker rm {name} did not finish within {timeout:.0f}s"
        ) from exc
    if res.returncode != 0:
        raise RuntimeError(f"docker rm {name} failed: {res.stderr.strip()}")


def cmd_stop(name: str) -> int:
    """CLI handler for ``agent-containers stop <name>`` (picker-venue-pivots
    Phase 2) -- the per-container analogue of "down"'s whole-fleet scope,
    backing the Containers pivot's gated Stop action. Refuses a leased
    container (the same "settle the claim first" discipline "down"/"rm"
    already apply at the fleet level) rather than yanking it out from under
    an active borrow."""
    import sys

    from .lease import get_lease

    lease = get_lease(name)
    if lease:
        print(
            f"Container '{name}' is leased to {lease.effort}; release it "
            f"first (agent-containers release {name}) before stopping.",
            file=sys.stderr,
        )
        return 1
    try:
        stop_container(name)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Stopped: {name}")
    return 0


def cmd_remove(name: str, *, force: bool = False) -> int:
    """CLI handler for ``agent-containers remove <name>`` -- the
    per-container analogue of "rm"'s whole-fleet scope, backing the
    Containers pivot's gated Remove action. Refuses a leased container for
    the same reason `cmd_stop` does."""
    import sys

    from .lease import get_lease

    lease = get_lease(name)
    if lease:
        print(
            f"Container '{name}' is leased to {lease.effort}; release it "
            f"first (agent-containers release {name}) before removing.",
            file=sys.stderr,
        )
        return 1
    try:
        remove_container(name, force=force)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Removed: {name}")
    return 0


#: Matches `__main__._BUSY_EXIT` -- the shared "operation deferred/blocked,
#: retryable" exit code convention this CLI uses across its commands.
_BUSY_EXIT = 75


def cmd_release(target: str) -> int:
    """CLI handler for ``agent-containers release <target>``."""
    import sys

    from .lease import ProviderAdmissionError, release
    from .provider_ssh import remove_stale_worktree_sources

    try:
        released = release(target)
    except ProviderAdmissionError as exc:
        print(f"Release blocked: {exc}", file=sys.stderr)
        return _BUSY_EXIT
    try:
        removed = remove_stale_worktree_sources(target)
    except (OSError, RuntimeError) as exc:
        if released:
            print(f"Released: {target}")
        print(f"Picker source cleanup failed after release: {exc}", file=sys.stderr)
        return 1
    if released:
        print(f"Released: {target}")
        if removed:
            print(f"Removed Picker source registrations: {removed}")
        return 0
    if removed:
        print(f"Removed stale Picker source registrations: {removed}")
        return 0
    print(f"No lease found for '{target}'", file=sys.stderr)
    return 1
