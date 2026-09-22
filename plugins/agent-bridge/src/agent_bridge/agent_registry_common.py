"""Shared agent-registry constants, helpers, and value types."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

_PROJECTS_YAML_DEFAULT = "~/.agent-worktrees/projects.yaml"
_REPOS_YAML_DEFAULT = "~/.agent-worktrees/repos.yaml"

_NAMESPACE_LIST_TTL_ENV = "AGENT_BRIDGE_NAMESPACE_LIST_TTL"
_NAMESPACE_LIST_DEFAULT_TTL = 12.0
_NAMESPACE_LIST_OFF = frozenset({"0", "off", "false", "no"})

_NAMESPACE_LIST_RESOLVER_TIMEOUT_ENV = "AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_TIMEOUT"
_NAMESPACE_LIST_RESOLVER_DEFAULT_TIMEOUT = 8.0


def _namespace_list_resolver_timeout() -> float:
    """Resolve the per-resolver ``list()`` timeout from the environment."""
    raw = str(os.environ.get(_NAMESPACE_LIST_RESOLVER_TIMEOUT_ENV, "")).strip().lower()
    if raw in _NAMESPACE_LIST_OFF:
        return 0.0
    if not raw:
        return _NAMESPACE_LIST_RESOLVER_DEFAULT_TIMEOUT
    try:
        value = float(raw)
        return value if value > 0 else 0.0
    except ValueError:
        return _NAMESPACE_LIST_RESOLVER_DEFAULT_TIMEOUT


def _namespace_list_ttl() -> float:
    """Resolve the namespace-list cache TTL from the environment."""
    raw = str(os.environ.get(_NAMESPACE_LIST_TTL_ENV, "")).strip().lower()
    if raw in _NAMESPACE_LIST_OFF:
        return 0.0
    if not raw:
        return _NAMESPACE_LIST_DEFAULT_TTL
    try:
        value = float(raw)
        return value if value > 0 else 0.0
    except ValueError:
        return _NAMESPACE_LIST_DEFAULT_TTL


def _normalize_repo_basename(name: str) -> str:
    """Normalize a repo name/key to its comparable basename."""
    return name.strip().lower().split("/")[-1].replace(".", "-")


@dataclass
class AgentConfig:
    """Parsed agent configuration from acp-agents.json."""

    name: str
    host: str | None = None
    ssh_user: str | None = None
    ssh_environment: str | None = None
    cwd: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = field(default_factory=list)
    managed: bool = False
    description: str | None = None
    display_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    icon: str | None = None
    worktree_root: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None
    spawnable_as_target: bool = True
    worktree_discovery: bool = True
    setup_script: str | None = None
    auto_discovered: bool = False
    derived: bool = False
    requires_admin: bool = False
    provider: str | None = None
    spawn_command: list[str] | None = None
    codespace: dict | None = None
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NamespaceAgentInfo:
    """Lightweight agent info returned by namespace resolvers."""

    name: str
    display_name: str = ""
    description: str = ""
    icon: str | None = None
    state: str = "available"
    aliases: list[str] = field(default_factory=list)


class AmbiguousAgentError(Exception):
    """A bare agent name matched more than one agent across namespaces."""

    def __init__(self, name: str, candidates: list[str]) -> None:
        self.name = name
        self.candidates = candidates
        listed = ", ".join(candidates)
        super().__init__(
            f"Agent name '{name}' is ambiguous -- it matches "
            f"{len(candidates)} agents: {listed}. "
            "Qualify it with a namespace (e.g. 'codespace:<name>') or use the "
            "exact name to disambiguate."
        )


class AgentRegistryLoadError(ValueError):
    """A configured explicit agent registry is missing or invalid."""
