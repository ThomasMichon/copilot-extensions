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
    ContainersConfig,
    DotfilesConfig,
    FleetConfig,
    HarnessConfig,
)
from .lifecycle import (
    DockerContainerInfo,
    _check_docker,
    _docker,
    get_container,
    inspect_container,
    list_containers,
    remove_container,
    start_container,
    stop_container,
)

log = logging.getLogger("agent-containers")


class RecreateMemberError(RuntimeError):
    """A targeted recreation failed after reporting its destructive boundary."""

    def __init__(self, message: str, *, old_container_removed: bool) -> None:
        self.old_container_removed = old_container_removed
        super().__init__(message)


@dataclass
class FleetOperationResult:
    """Per-member result for a fleet lifecycle operation."""

    created: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    unchanged: dict[str, str] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    recreated: list[str] = field(default_factory=list)
    deferred: dict[str, str] = field(default_factory=dict)
    rescues: dict[str, dict] = field(default_factory=dict)
    telemetry_abandoned: list[str] = field(default_factory=list)


def _creation_flags() -> int:
    return no_window_flags()


def _fleet_members(config: ContainersConfig, fleet_name: str) -> list[DockerContainerInfo]:
    """All existing members or deterministic-name conflicts for a fleet.

    An explicit foreign fleet label remains visible when the name occupies this
    fleet's deterministic slot, so callers can report/defer the conflict rather
    than silently ignoring or acting through the foreign configuration.
    """
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
        "--id-label", f"{SECURITY_PROFILE_LABEL}={fleet.security_profile}",
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


def _image_user(
    image: str,
    user: str,
    *,
    memory: str,
    cpus: float,
    pids_limit: int,
) -> tuple[int, int, str]:
    """Resolve a configured user's uid/gid/home from an image without host reach."""
    probe = (
        'uid=$(id -u "$1") && gid=$(id -g "$1") && '
        'home=$(getent passwd "$1" | cut -d: -f6) && '
        'test -n "$home" && printf "%s\\t%s\\t%s\\n" "$uid" "$gid" "$home"'
    )
    res = _docker(
        [
            "run", "--rm", "--network", "none",
            "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory", memory,
            "--memory-swap", memory,
            "--cpus", str(cpus),
            "--pids-limit", str(pids_limit),
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",  # noqa: S108
            "--entrypoint", "bash",
            image, "-c", probe, "--", user,
        ],
        timeout=120,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Restricted image '{image}' does not provide exec_user '{user}' "
            f"with a resolvable home: {res.stderr.strip() or res.stdout.strip()}"
        )
    parts = res.stdout.strip().split("\t")
    if len(parts) != 3:
        raise RuntimeError(
            f"Restricted image user probe returned an invalid result for '{user}'"
        )
    uid, gid, home = int(parts[0]), int(parts[1]), parts[2]
    if uid <= 0 or gid <= 0:
        raise RuntimeError(
            f"Restricted exec_user '{user}' must have a non-root uid and gid"
        )
    if not home.startswith("/") or home in {"/", "/tmp", "/run"}:  # noqa: S108
        raise RuntimeError(
            f"Restricted exec_user '{user}' has unsafe home directory '{home}'"
        )
    return uid, gid, home


def _image_id(image: str) -> str:
    """Resolve the immutable local Docker image ID for a configured reference."""
    res = _docker(["image", "inspect", "--format", "{{.Id}}", image], timeout=30)
    image_id = res.stdout.strip() if res.returncode == 0 else ""
    if not image_id:
        raise RuntimeError(
            f"Could not resolve immutable image ID for '{image}': "
            f"{res.stderr.strip() or res.stdout.strip()}"
        )
    return image_id


def _validate_restricted_network(network: str) -> None:
    """Require no network or a user-defined Docker-internal network."""
    lowered = network.lower()
    if lowered == "none":
        return
    if lowered in {"host", "bridge", "default"} or lowered.startswith("container:"):
        raise RuntimeError(
            f"Restricted network '{network}' is not isolated; use 'none' or "
            "a user-defined Docker internal network"
        )
    inspected = _docker(["network", "inspect", network], timeout=30)
    if inspected.returncode != 0:
        raise RuntimeError(
            f"Restricted network '{network}' is unavailable: "
            f"{inspected.stderr.strip() or inspected.stdout.strip()}"
        )
    try:
        networks = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Restricted network '{network}' returned invalid inspect data"
        ) from exc
    if not networks or not networks[0].get("Internal"):
        raise RuntimeError(
            f"Restricted network '{network}' must be created with --internal"
        )


def _image_run(
    fleet_name: str,
    fleet: FleetConfig,
    name: str,
    *,
    workspace_folder: str,
    exec_user: str,
) -> str:
    """Run one warm container directly from an image; return its name."""
    args = [
        "run", "-d",
        "--name", name,
        "--label", f"{FLEET_LABEL}={fleet_name}",
        "--label", f"{SECURITY_PROFILE_LABEL}={fleet.security_profile}",
    ]
    if fleet.restricted:
        network = fleet.effective_network()
        _validate_restricted_network(network)
        uid, gid, home = _image_user(
            fleet.image,
            exec_user,
            memory=fleet.effective_memory(),
            cpus=fleet.effective_cpus(),
            pids_limit=fleet.effective_pids_limit(),
        )
        image_id = _image_id(fleet.image)
        if home == workspace_folder or workspace_folder in {
            "/", "/tmp", "/run",  # noqa: S108
        }:
            raise RuntimeError(
                f"Restricted workspace_folder '{workspace_folder}' is unsafe"
            )
        policy = fleet.security_policy_fingerprint(workspace_folder, exec_user)
        args += [
            "--label", f"{SECURITY_POLICY_LABEL}={policy}",
            "--label", f"{SECURITY_HOME_LABEL}={home}",
            "--label", f"{SECURITY_UID_LABEL}={uid}",
            "--label", f"{SECURITY_GID_LABEL}={gid}",
            "--label", f"{SECURITY_IMAGE_ID_LABEL}={image_id}",
        ]
        # All writable surfaces are size-bounded tmpfs. No host path or
        # persistent cross-tenant volume is mounted.
        args += [
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--network", network,
            "--memory", fleet.effective_memory(),
            "--memory-swap", fleet.effective_memory(),
            "--cpus", str(fleet.effective_cpus()),
            "--pids-limit", str(fleet.effective_pids_limit()),
            "--env", f"HOME={home}",
            "--tmpfs",
            (
                f"{workspace_folder}:rw,nosuid,nodev,"
                f"exec,size={fleet.effective_workspace_size()},"
                f"uid={uid},gid={gid},mode=0700"
            ),
            "--tmpfs",
            (
                f"{home}:rw,nosuid,nodev,"
                f"exec,size={fleet.effective_home_size()},"
                f"uid={uid},gid={gid},mode=0700"
            ),
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",  # noqa: S108
            "--tmpfs", "/run:rw,nosuid,nodev,size=64m",
        ]
    else:
        # Historical trusted-development behavior: make the host gateway
        # reachable for credential relays and local development services.
        args += ["--add-host=host.docker.internal:host-gateway"]
        if fleet.effective_network():
            args += ["--network", fleet.effective_network()]
        if fleet.effective_memory():
            args += ["--memory", fleet.effective_memory()]
        if fleet.effective_cpus() is not None:
            args += ["--cpus", str(fleet.effective_cpus())]
        if fleet.effective_pids_limit() is not None:
            args += ["--pids-limit", str(fleet.effective_pids_limit())]
    for key, value in sorted(fleet.environment.items()):
        args += ["--env", f"{key}={value}"]
    args += [fleet.image, "sleep", "infinity"]
    res = _docker(args, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"docker run failed for {name}: {res.stderr.strip()}")
    return name


def reconcile_up(
    config: ContainersConfig,
    fleet_name: str,
    count: int | None = None,
    recreate: bool = False,
    force_abandon: bool = False,
) -> FleetOperationResult:
    """Provision (or top up) a fleet to ``count`` containers.

    Returns independent create/recreate/defer results. Existing members are
    left in place (warm reuse).

    ``recreate`` addresses image/policy drift: a restricted fleet's containers
    pin the image ID and security policy they were built against, so after the
    fleet image is rebuilt (or the policy changes) a still-running member no
    longer matches and dispatch is refused. Without ``recreate`` such drift
    raises (the historical behavior); with it, the drifted members are removed
    and re-provisioned fresh on the current image/policy. Active, unknown, or
    leased members remain running and are reported as deferred. Container names
    are deterministic, but replacement is admitted only after any lease is
    released.
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
    if fleet.restricted and fleet.devcontainer_path:
        raise RuntimeError(
            f"Restricted fleet '{fleet_name}' must use the image backend; "
            "devcontainer workspace mounts cannot provide the no-host-worktree boundary"
        )
    if fleet.restricted:
        fleet.validate_restricted()
        config.acp_command_for(fleet)

    target = count if count is not None else fleet.size
    existing = _fleet_members(config, fleet_name)
    workspace_folder = fleet.workspace_folder or config.workspace_folder
    exec_user = fleet.exec_user or config.exec_user
    if fleet.restricted:
        current_image_id = _image_id(fleet.image) if existing else None
        expected_policy = fleet.security_policy_fingerprint(
            workspace_folder,
            exec_user,
        )
        stale = [
            c
            for c in existing
            if (
                getattr(c, "fleet", None) is not None
                and getattr(c, "fleet", None) != fleet_name
            )
            or c.security_profile != fleet.security_profile
            or c.security_policy != expected_policy
            or (current_image_id and c.security_image_id != current_image_id)
        ]
        if stale:
            if not recreate:
                raise RuntimeError(
                    f"Restricted fleet '{fleet_name}' has containers with a stale "
                    f"or mismatched security policy: "
                    f"{', '.join(c.name for c in stale)}. "
                    "Recreate them before dispatch (pass recreate=True / "
                    "`up --recreate`)."
                )
            from .replacement import destroy_restricted_member

            result = FleetOperationResult()
            removed_names = set()
            for member in stale:
                if (
                    getattr(member, "fleet", None)
                    and getattr(member, "fleet", None) != fleet_name
                ):
                    result.deferred[member.name] = (
                        f"container fleet label {member.fleet!r} conflicts with "
                        f"requested fleet {fleet_name!r}"
                    )
                    continue
                log.info(
                    "Recreating drifted fleet container %s (image/policy mismatch)",
                    member.name,
                )
                try:
                    decision = destroy_restricted_member(
                        config,
                        fleet,
                        member,
                        operation="recreate",
                        force_remove=True,
                        force_abandon=force_abandon,
                    )
                except RuntimeError as exc:
                    result.deferred[member.name] = str(exc)
                    continue
                if decision.status == "removed":
                    removed_names.add(member.name)
                    result.removed.append(member.name)
                    if decision.rescue:
                        result.rescues[member.name] = decision.rescue
                    if decision.telemetry_abandoned:
                        result.telemetry_abandoned.append(member.name)
                else:
                    result.deferred[member.name] = (
                        decision.reason or "replacement deferred"
                    )
            existing = [c for c in existing if c.name not in removed_names]
        else:
            result = FleetOperationResult()
    else:
        result = FleetOperationResult()
    need = target - len(existing)
    if need <= 0:
        log.info(
            "Fleet '%s' already has %d/%d containers", fleet_name, len(existing), target
        )
        return result

    prefix = fleet.prefix(fleet_name)
    indices = _next_indices(existing, prefix, need)
    for idx in indices:
        name = f"{prefix}-{idx}"
        log.info("Provisioning fleet container %s", name)
        if fleet.devcontainer_path:
            result.created.append(
                _devcontainer_up(
                    fleet_name, fleet, name,
                    dotfiles=config.dotfiles, harness=config.harness,
                    exec_user=exec_user,
                )
            )
        else:
            result.created.append(
                _image_run(
                    fleet_name,
                    fleet,
                    name,
                    workspace_folder=workspace_folder,
                    exec_user=exec_user,
                )
            )
        if name in result.removed:
            result.recreated.append(name)
    return result


def up(
    config: ContainersConfig,
    fleet_name: str,
    count: int | None = None,
    recreate: bool = False,
    force_abandon: bool = False,
) -> list[str]:
    """Compatibility wrapper returning only members created during this call."""
    return reconcile_up(
        config,
        fleet_name,
        count=count,
        recreate=recreate,
        force_abandon=force_abandon,
    ).created


def recreate_member(
    config: ContainersConfig,
    name: str,
    *,
    expected_container_id: str,
    timeout: float = 600.0,
) -> dict[str, str | bool]:
    """Recreate one trusted fleet member after an identity-checked remove."""
    _check_docker()
    info = get_container(config, name)
    if info is None:
        raise RuntimeError(f"Container '{name}' is not a discovered fleet member")
    if info.container_id.lower() != expected_container_id.lower():
        raise RuntimeError(
            f"Container '{name}' identity changed before recreation"
        )
    fleet_name = info.fleet or ""
    fleet = config.fleets.get(fleet_name)
    if fleet is None:
        raise RuntimeError(
            f"Container '{name}' has no matching fleet configuration"
        )
    if fleet.restricted or info.security_profile != "trusted":
        raise RuntimeError(
            "Targeted parity recreation is supported only for trusted fleets"
        )

    old_id = info.container_id
    remove_container(old_id, force=True, timeout=timeout)
    workspace_folder = fleet.workspace_folder or config.workspace_folder
    exec_user = fleet.exec_user or config.exec_user
    try:
        if fleet.devcontainer_path:
            _devcontainer_up(
                fleet_name,
                fleet,
                name,
                dotfiles=config.dotfiles,
                harness=config.harness,
                exec_user=exec_user,
            )
        else:
            _image_run(
                fleet_name,
                fleet,
                name,
                workspace_folder=workspace_folder,
                exec_user=exec_user,
            )
    except Exception as exc:
        raise RecreateMemberError(
            f"Container '{name}' was removed but its replacement failed",
            old_container_removed=True,
        ) from exc

    try:
        replacement = get_container(config, name)
        if replacement is None:
            raise RuntimeError(
                f"Container '{name}' replacement was not discoverable"
            )
        if replacement.state != "running":
            raise RuntimeError(
                f"Container '{name}' replacement is not running "
                f"(state={replacement.state!r})"
            )
        if (
            replacement.name != name
            or replacement.fleet != fleet_name
            or replacement.security_profile != "trusted"
        ):
            raise RuntimeError(
                f"Container '{name}' replacement does not match its trusted fleet"
            )
        if replacement.container_id.lower() == old_id.lower():
            raise RuntimeError(
                f"Container '{name}' recreation preserved the old identity"
            )
    except Exception as exc:
        raise RecreateMemberError(
            str(exc),
            old_container_removed=True,
        ) from exc
    return {
        "name": name,
        "old_container_id": old_id,
        "new_container_id": replacement.container_id,
        "running": True,
        "identity_changed": True,
    }


def down_fleet(
    config: ContainersConfig,
    fleet_name: str,
    *,
    force_abandon: bool = False,
) -> FleetOperationResult:
    """Stop members independently, rescuing restricted tmpfs evidence first."""
    result = FleetOperationResult()
    fleet = config.fleets.get(fleet_name)
    for c in _fleet_members(config, fleet_name):
        if c.fleet and c.fleet != fleet_name:
            result.deferred[c.name] = (
                f"container fleet label {c.fleet!r} conflicts with "
                f"requested fleet {fleet_name!r}"
            )
            continue
        restricted = (fleet and fleet.restricted) or c.security_profile == "restricted"
        if restricted and (fleet is None or not fleet.restricted):
            result.deferred[c.name] = (
                "restricted container has no matching restricted fleet configuration"
            )
            continue
        if restricted and c.state == "running":
            from .replacement import stop_restricted_member

            try:
                decision = stop_restricted_member(
                    config,
                    fleet,
                    c,
                    force_abandon=force_abandon,
                )
            except RuntimeError as exc:
                result.deferred[c.name] = str(exc)
                continue
            if decision.status != "stopped":
                result.deferred[c.name] = decision.reason or "stop deferred"
                continue
            if decision.rescue:
                result.rescues[c.name] = decision.rescue
            if decision.telemetry_abandoned:
                result.telemetry_abandoned.append(c.name)
            result.stopped.append(c.name)
        elif restricted and c.state in {"exited", "created"}:
            from .rescue import (
                container_generation,
                record_telemetry_loss,
                verified_capture_for_instance,
            )

            try:
                generation = container_generation(inspect_container(c.container_id))
                if (
                    verified_capture_for_instance(
                        c.name,
                        c.container_id,
                        generation,
                    )
                    is None
                ):
                    record_telemetry_loss(
                        container=c.name,
                        container_instance=c.container_id,
                        container_generation=generation,
                        reason="already_stopped",
                    )
            except RuntimeError as exc:
                result.deferred[c.name] = str(exc)
                continue
            result.unchanged[c.name] = (
                "already stopped; tmpfs evidence unavailable or previously rescued"
            )
        elif restricted:
            result.deferred[c.name] = (
                f"container state {c.state!r} is not safely stoppable"
            )
        elif c.is_running:
            stop_container(c.name)
            result.stopped.append(c.name)
    return result


def down(
    config: ContainersConfig,
    fleet_name: str,
    force_abandon: bool = False,
) -> list[str]:
    """Compatibility wrapper returning only members stopped during this call."""
    return down_fleet(
        config,
        fleet_name,
        force_abandon=force_abandon,
    ).stopped


def start(config: ContainersConfig, fleet_name: str) -> list[str]:
    """Start all stopped containers in a fleet."""
    from .lifecycle import restricted_policy_errors

    fleet = config.fleets.get(fleet_name)
    if fleet is None:
        raise RuntimeError(f"Fleet '{fleet_name}' is not defined in containers.yaml")
    started = []
    for c in _fleet_members(config, fleet_name):
        if not c.is_running:
            if fleet.restricted or c.security_profile == "restricted":
                if not fleet.restricted or c.security_profile != "restricted":
                    raise RuntimeError(
                        f"Container '{c.name}' security profile does not match its fleet"
                    )
                errors = restricted_policy_errors(
                    c,
                    fleet,
                    workspace_folder=fleet.workspace_folder or config.workspace_folder,
                    exec_user=fleet.exec_user or config.exec_user,
                )
                if errors:
                    raise RuntimeError(
                        f"Container '{c.name}' does not satisfy the restricted "
                        f"security policy: {'; '.join(errors)}"
                    )
            start_container(c.name)
            started.append(c.name)
    return started


def remove_fleet(
    config: ContainersConfig,
    fleet_name: str,
    *,
    force: bool = False,
    force_abandon: bool = False,
) -> FleetOperationResult:
    """Remove fleet members independently, deferring unsafe restricted members."""
    result = FleetOperationResult()
    requested_fleet = config.fleets.get(fleet_name)
    for c in _fleet_members(config, fleet_name):
        if c.fleet and c.fleet != fleet_name:
            result.deferred[c.name] = (
                f"container fleet label {c.fleet!r} conflicts with "
                f"requested fleet {fleet_name!r}"
            )
            continue
        fleet = requested_fleet
        if (fleet and fleet.restricted) or c.security_profile == "restricted":
            if fleet is None or not fleet.restricted:
                result.deferred[c.name] = (
                    "restricted container has no matching restricted fleet configuration"
                )
                continue
            from .replacement import destroy_restricted_member

            try:
                decision = destroy_restricted_member(
                    config,
                    fleet,
                    c,
                    operation="remove",
                    force_remove=force,
                    force_abandon=force_abandon,
                )
            except RuntimeError as exc:
                result.deferred[c.name] = str(exc)
                continue
            if decision.status != "removed":
                result.deferred[c.name] = decision.reason or "removal deferred"
                continue
            if decision.rescue:
                result.rescues[c.name] = decision.rescue
            if decision.telemetry_abandoned:
                result.telemetry_abandoned.append(c.name)
        else:
            remove_container(c.name, force=force)
        result.removed.append(c.name)
    return result


def rm(
    config: ContainersConfig,
    fleet_name: str,
    force: bool = False,
    force_abandon: bool = False,
) -> list[str]:
    """Compatibility wrapper returning only members removed during this call."""
    return remove_fleet(
        config,
        fleet_name,
        force=force,
        force_abandon=force_abandon,
    ).removed
