"""Read static workspace transport identity without launch-plugin discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_registry import AgentResolver


def identity(resolver: AgentResolver, owner: str) -> tuple[str, str | None] | None:
    """Project topology only; never resolve a launch configuration."""
    config = resolver.agents.get(owner)
    if config is None or config.managed or config.spawn_command or not config.project:
        return None
    if not config.host or resolver._is_local_loopback_agent(config):
        return "local", None
    try:
        machine, environment = resolver.resolve_ssh_environment(config.host, config.ssh_environment)
    except (KeyError, ValueError):
        return None
    return ("ssh", environment.alias) if machine.ssh_ready else None
