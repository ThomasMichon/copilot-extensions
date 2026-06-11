"""Namespace resolver for local Docker dev containers.

Implements the agent-bridge ``NamespaceResolver`` interface so that fleet
containers can be addressed as ``container:<name>`` without pre-registration.
Resolution returns a ``SpawnTarget`` that launches a Copilot ACP agent inside
the container via ``docker exec -i``.

The host ``gh auth token`` is forwarded into the container as ``GH_TOKEN`` so
the in-container Copilot CLI is authenticated headlessly. The token is passed
via the spawned process's *environment* (``SpawnTarget.env``) and referenced by
name in the docker command (``-e GH_TOKEN``), so it never appears in argv or logs.

Usage:
    from agent_containers.resolver import ContainerResolver
    resolver.register_namespace_resolver(ContainerResolver())
    # Then: agent-bridge send container:odsp-web-1 "run the tests"
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from typing import TYPE_CHECKING

from .config import load_config
from .lease import get_lease
from .lifecycle import get_container, inspect_state, list_containers, start_container

if TYPE_CHECKING:
    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.transport import SpawnTarget

log = logging.getLogger("agent-containers")


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


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
    container: str, user: str, acp_command: str, forward_token: bool
) -> list[str]:
    """Build the ``docker exec`` spawn command (token referenced by name)."""
    cmd = ["docker", "exec", "-i"]
    if forward_token:
        # Reference by name only -- value comes from the process env, so it is
        # never written to argv or the agent-bridge log.
        cmd += ["-e", "GH_TOKEN"]
    cmd += ["-u", user, container, "bash", "-lc", acp_command]
    return cmd


class ContainerResolver:
    """Namespace resolver for ``container:<name>`` agent routing."""

    @property
    def prefix(self) -> str:
        return "container"

    async def resolve(self, name: str) -> SpawnTarget:
        """Resolve a container name to a SpawnTarget over ``docker exec``."""
        from agent_bridge.transport import SpawnTarget

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
        workspace = (fleet.workspace_folder if fleet else None) or config.workspace_folder
        user = (fleet.exec_user if fleet else None) or config.exec_user
        acp_command = config.effective_acp_command(
            workspace_folder=workspace,
            acp_command=(fleet.acp_command if fleet else None),
        )

        env: dict[str, str] = {}
        forward = config.forward_gh_token
        if forward:
            token = await asyncio.to_thread(host_gh_token)
            if token:
                env["GH_TOKEN"] = token
            else:
                forward = False
                log.warning(
                    "forward_gh_token is on but `gh auth token` returned nothing; "
                    "the in-container Copilot CLI may be unauthenticated."
                )

        spawn_cmd = build_spawn_command(name, user, acp_command, forward)
        log.info("Resolved container:%s -> %s", name, " ".join(spawn_cmd))
        return SpawnTarget(type="command", spawn_command=spawn_cmd, env=env, user=user)

    async def list(self) -> list[NamespaceAgentInfo]:
        """List fleet containers as namespace agent info."""
        from agent_bridge.agent_registry import NamespaceAgentInfo

        config = load_config()
        containers = await asyncio.to_thread(list_containers, config)
        agents = []
        for c in containers:
            lease = await asyncio.to_thread(get_lease, c.name)
            repo = c.repo or (c.fleet or "")
            display = f"{c.name} ({repo})" if repo else c.name
            description = f"Local dev container: {c.image}"
            if lease:
                description += f" — leased by {lease.effort}"
            # Map docker state to a coarse ready/stopped signal.
            state = "running" if c.is_running else (c.state or "unknown")
            agents.append(
                NamespaceAgentInfo(
                    name=c.name,
                    display_name=display,
                    description=description,
                    icon="container",
                    state=state,
                )
            )
        return agents

    async def ensure_ready(self, name: str) -> None:
        """Ensure the container exists and is running (start if stopped)."""
        state = await asyncio.to_thread(inspect_state, name)
        if state is None:
            raise RuntimeError(f"Container '{name}' not found")
        if state == "running":
            return
        log.info("Container '%s' is '%s' -- starting", name, state)
        await asyncio.to_thread(start_container, name)
