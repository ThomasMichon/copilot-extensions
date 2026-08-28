"""Namespace resolver for local Docker dev containers.

Implements the agent-bridge ``NamespaceResolver`` interface so that fleet
containers can be addressed as ``container:<name>`` without pre-registration.
Resolution returns a ``SpawnTarget`` that launches a Copilot ACP agent through
the container wrapper. Trusted fleets use OpenSSH; restricted fleets retain the
direct ``docker exec -i`` boundary and expose a stable provider-target identity
without projecting host authority.

Trusted fleets may forward the host ``gh auth token`` into the container as
``GH_TOKEN``. Restricted fleets use a separate command builder that cannot
accept token or relay arguments.

Usage:
    from agent_containers.resolver import ContainerResolver
    resolver.register_namespace_resolver(ContainerResolver())
    # Then: agent-bridge send container:myrepo-1 "run the tests"
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import asdict
from typing import TYPE_CHECKING

from agent_procutil import no_window_flags

from ._invoke import module_argv
from .config import RESTRICTED_PROFILE, TRUSTED_PROFILE, load_config
from .lease import deploy_hold_status, get_deploy_hold, get_lease
from .lifecycle import (
    get_container,
    inspect_state,
    list_containers,
    restricted_policy_errors,
    start_container,
)
from .ssh_transport import prepare_ssh_config

if TYPE_CHECKING:
    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.transport import SpawnTarget

log = logging.getLogger("agent-containers")
VENUE_SCHEMA_VERSION = 1
VENUE_PROVIDER = "agent-containers"


def _creation_flags() -> int:
    return no_window_flags()


def host_gh_token() -> str | None:
    """Fetch the host's GitHub token via ``gh auth token`` (or None)."""
    try:
        res = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_creation_flags(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    token = res.stdout.strip()
    return token or None


def build_spawn_command(
    container: str,
    user: str,
    acp_command: str,
    forward_token: bool,
    relay_env: list[str] | None = None,
) -> list[str]:
    """Build the ``docker exec`` spawn command (token referenced by name).

    Used by the ``agent-containers exec`` transport wrapper (see __main__),
    NOT returned directly to agent-bridge. ``relay_env`` names additional env
    vars (e.g. LC_GIT_CREDENTIAL_RELAY*) to forward by name from the wrapper's
    process env, so their secret values never land in argv or logs.
    """
    cmd = ["docker", "exec", "-i"]
    if forward_token:
        # Reference by name only -- value comes from the process env, so it is
        # never written to argv or the agent-bridge log.
        cmd += ["-e", "GH_TOKEN"]
    for name in relay_env or []:
        cmd += ["-e", name]
    cmd += ["-u", user, container, "bash", "-lc", acp_command]
    return cmd


def build_restricted_spawn_command(
    container: str,
    user: str,
    acp_command: str,
) -> list[str]:
    """Build the restricted ``docker exec`` command with no host projection.

    This API deliberately accepts no token, relay, SSH, mount, or network
    arguments. The live Docker posture is validated before this command runs.
    """
    return [
        "docker",
        "exec",
        "-i",
        "-u",
        user,
        container,
        "bash",
        "-lc",
        acp_command,
    ]


def build_wrapper_command(name: str) -> list[str]:
    """Build the spawn command agent-bridge runs for a ``container:`` agent.

    Delegates to ``agent-containers exec --stdio <name>`` rather than docker
    directly. The wrapper fetches the host ``gh`` token at spawn time and
    injects it into the container's environment, so the token NEVER lands in
    the SpawnTarget (which agent-bridge persists to its SQLite DB) or in any log.

    Invokes the module directly (``python -m agent_containers``), never the
    ``.cmd`` binstub, so agent-bridge does not route the spawn through
    cmd.exe and mangle forwarded arguments (see ``._invoke``).
    """
    return [*module_argv(), "exec", "--stdio", name]


class ContainerResolver:
    """Namespace resolver for ``container:<name>`` agent routing."""

    @property
    def prefix(self) -> str:
        return "container"

    async def resolve(self, name: str) -> SpawnTarget:
        """Resolve a container name to a SpawnTarget over its venue transport.

        Thin wrapper over :meth:`resolve_spec` (the agent_bridge-free data path,
        also the ``namespace-resolve`` CLI seam for #892 Inc 3b). Note the narrow
        ``resolve(self, name)`` signature -- containers do not support cross-repo
        dispatch or plugin injection, which agent-bridge honors by introspecting
        this signature.
        """
        from agent_bridge.transport import SpawnTarget

        spec = await self.resolve_spec(name)
        return SpawnTarget(
            type=spec.get("type", "command"),
            spawn_command=spec["spawn_command"],
            user=spec.get("user"),
            container=spec.get("container"),
            venue=spec.get("venue"),
        )

    async def resolve_spec(self, name: str) -> dict:
        """Resolve a container name to a **plain-dict** spawn spec.

        The agent_bridge-free core of :meth:`resolve` -- returns
        ``{"type","spawn_command","user","workspace_folder","security_profile",
        "venue"}`` using only agent-containers + stdlib, so the
        ``agent-containers namespace-resolve`` CLI can emit it as JSON and
        agent-bridge reconstructs the ``SpawnTarget`` across a process boundary
        (#892 Inc 3b). ``workspace_folder`` is the container's concrete repo
        checkout (for the ACP session cwd); ``security_profile`` is the fleet's
        trust posture (``trusted``/``restricted``) that gates any host->venue
        projection. Raises ``KeyError`` when the container is not in the fleet.
        """
        config = load_config()
        info = await asyncio.to_thread(get_container, config, name)
        if info is None:
            members = await asyncio.to_thread(list_containers, config)
            raise KeyError(
                f"Container '{name}' not found. "
                f"Fleet members: {[c.name for c in members]}"
            )

        # Advisory lease check -- log, do not block (enforcement=advisory).
        lease = await asyncio.to_thread(get_lease, name)
        if lease is not None:
            log.info(
                "container:%s is leased by effort '%s' (host=%s) -- "
                "dispatching anyway (advisory leases)",
                name, lease.effort, lease.host,
            )

        fleet = config.fleets.get(info.fleet or "")
        if fleet is None:
            raise KeyError(
                f"Container '{name}' has no matching fleet configuration"
            )
        user = (fleet.exec_user if fleet else None) or config.exec_user

        # Spawn the transport wrapper, not docker directly. The wrapper fetches
        # the gh token at spawn time, keeping it out of the persisted SpawnTarget.
        spawn_cmd = build_wrapper_command(name)
        log.info("Resolved container:%s -> %s", name, " ".join(spawn_cmd))
        # Surface the venue's concrete workspace folder + trust posture so the
        # bridge can (a) set the ACP session cwd to the repo checkout instead of
        # the home-dir default, and (b) gate any host->venue projection (repo-own
        # plugins, etc.) on the fleet's trust posture -- a `restricted` fleet is a
        # fail-closed boundary and must never be silently upgraded (venue-parity).
        workspace_folder = fleet.workspace_folder or config.workspace_folder
        spec = {
            "type": "command",
            "spawn_command": spawn_cmd,
            "user": user,
            "workspace_folder": workspace_folder,
            "security_profile": fleet.security_profile,
        }
        actual_profile = getattr(
            info, "security_profile", fleet.security_profile,
        )
        supported_profile = actual_profile in {TRUSTED_PROFILE, RESTRICTED_PROFILE}
        restricted = fleet.restricted or actual_profile != TRUSTED_PROFILE
        hold_status = (
            await asyncio.to_thread(deploy_hold_status, name)
            if restricted
            else {"state": "none", "operation": None, "reason": None}
        )
        effective_profile = RESTRICTED_PROFILE if restricted else actual_profile
        profile_mismatch = (
            not supported_profile or fleet.security_profile != actual_profile
        )
        forward_gh, relay_enabled = config.credentials_for(fleet)
        if restricted or profile_mismatch:
            forward_gh, relay_enabled = False, False
        spec["venue"] = {
            "schema_version": VENUE_SCHEMA_VERSION,
            "provider": VENUE_PROVIDER,
            "kind": self.prefix,
            "target_id": f"{self.prefix}:{name}",
            "scope": "provider-instance",
            "instance_id": getattr(info, "container_id", None),
            "fleet": info.fleet,
            "workspace_folder": workspace_folder,
            # Backward-compatible trust key used by existing SpawnTarget
            # consumers. It is the fail-closed maximum of configured/observed
            # posture, not an attestation that the full policy was inspected.
            "security_profile": effective_profile,
            "configured_security_profile": fleet.security_profile,
            "observed_security_profile": actual_profile,
            "effective_security_profile": effective_profile,
            "state": getattr(info, "state", "unknown"),
            "ready": (
                bool(getattr(info, "is_running", False))
                and not profile_mismatch
                and hold_status["state"] == "none"
            ),
            "posture_verified": False,
            "transport": "docker-exec" if restricted else "ssh",
            "capabilities": {
                "container_local_workspace": True,
                "host_credentials": forward_gh,
                "credential_relay": relay_enabled,
                "session_host": not restricted,
            },
        }
        if restricted:
            spec["venue"]["lifecycle_hold"] = hold_status
        if not restricted:
            ssh_config = await asyncio.to_thread(
                prepare_ssh_config,
                name,
                user,
            )
            spec["container"] = {
                "name": name,
                "workspace_folder": workspace_folder,
                "security_profile": fleet.security_profile,
                "user": user,
                "acp_command": config.acp_command_for(fleet),
                "ssh": asdict(ssh_config),
                "provider_command": module_argv(),
                "relay_remote_port": (
                    config.relay_port if relay_enabled else None
                ),
            }
        return spec

    async def list(self) -> list[NamespaceAgentInfo]:
        """List fleet containers as namespace agent info (in-process path)."""
        from agent_bridge.agent_registry import NamespaceAgentInfo

        return [NamespaceAgentInfo(**spec) for spec in await self.list_specs()]

    async def list_specs(self) -> list[dict]:
        """List fleet containers as **plain-dict** agent specs.

        The agent_bridge-free core of :meth:`list` -- the ``namespace-list`` CLI
        seam emits these as JSON and agent-bridge reconstructs
        ``NamespaceAgentInfo`` across a process boundary (#892 Inc 3b).
        """
        config = load_config()
        containers = await asyncio.to_thread(list_containers, config)
        agents = []
        for c in containers:
            lease = await asyncio.to_thread(get_lease, c.name)
            fleet = config.fleets.get(c.fleet or "")
            hold_status = (
                await asyncio.to_thread(deploy_hold_status, c.name)
                if (fleet and fleet.restricted)
                or c.security_profile == RESTRICTED_PROFILE
                else {"state": "none", "operation": None, "reason": None}
            )
            repo = c.repo or (c.fleet or "")
            display = f"{c.name} ({repo})" if repo else c.name
            description = f"Local dev container: {c.image}"
            description += f" — {c.security_profile}"
            if lease:
                description += f" — leased by {lease.effort}"
            # Map docker state to a coarse ready/stopped signal.
            state = "running" if c.is_running else (c.state or "unknown")
            if hold_status["state"] != "none":
                state = "draining"
            agents.append({
                "name": c.name,
                "display_name": display,
                "description": description,
                "icon": "container",
                "state": state,
            })
        return agents

    async def ensure_ready(self, name: str) -> None:
        """Ensure the container exists and is running (start if stopped)."""
        config = load_config()
        info = await asyncio.to_thread(get_container, config, name)
        if info is None:
            raise RuntimeError(f"Container '{name}' is not a discovered fleet member")
        fleet = config.fleets.get(info.fleet or "")
        if fleet is None:
            raise RuntimeError(
                f"Container '{name}' has no matching fleet configuration"
            )
        actual_profile = getattr(info, "security_profile", "")
        if actual_profile not in {TRUSTED_PROFILE, RESTRICTED_PROFILE}:
            raise RuntimeError(
                f"Container '{name}' has unsupported live security profile "
                f"{actual_profile!r}"
            )
        if fleet.security_profile != actual_profile:
            raise RuntimeError(
                f"Container '{name}' security profile does not match its fleet "
                f"(configured={fleet.security_profile!r}, live={actual_profile!r})"
            )
        if fleet.restricted:
            deploy_hold = await asyncio.to_thread(get_deploy_hold, name)
            if deploy_hold:
                raise RuntimeError(
                    f"Container '{name}' is unavailable while provider "
                    f"{deploy_hold.operation} is in progress"
                )
        if fleet.restricted or info.security_profile == "restricted":
            errors = await asyncio.to_thread(
                restricted_policy_errors,
                info,
                fleet,
                workspace_folder=fleet.workspace_folder or config.workspace_folder,
                exec_user=fleet.exec_user or config.exec_user,
            )
            if errors:
                raise RuntimeError(
                    f"Container '{name}' does not satisfy the restricted "
                    f"security policy: {'; '.join(errors)}"
                )
        state = await asyncio.to_thread(inspect_state, name)
        if state is None:
            raise RuntimeError(f"Container '{name}' not found")
        if state == "running":
            return
        log.info("Container '%s' is '%s' -- starting", name, state)
        await asyncio.to_thread(start_container, name)
