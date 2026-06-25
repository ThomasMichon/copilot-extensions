"""Namespace resolver for GitHub Codespaces.

Implements the agent-bridge ``NamespaceResolver`` interface so that
codespace agents can be addressed as ``codespace:<name>`` without
pre-registration. The resolver queries ``gh codespace list`` on demand
and builds SpawnTargets that launch ``agent-codespaces ssh --stdio``.

Usage:
    from agent_codespaces.resolver import CodespaceResolver

    resolver = CodespaceResolver()
    bridge_resolver.register_namespace_resolver(resolver)

    # Then: agent-bridge send codespace:my-cs-name "do the work"
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ._invoke import module_argv
from .config import load_merged_config
from .lifecycle import list_codespaces

if TYPE_CHECKING:
    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.transport import SpawnTarget

log = logging.getLogger("agent-codespaces")


class AmbiguousCodespaceError(ValueError):
    """A friendly codespace name matched more than one codespace.

    Carries the raw candidate names so the caller can disambiguate by raw
    name. Subclasses ValueError so agent-bridge surfaces it as a 400 with the
    enumerated candidates.
    """

    def __init__(self, name: str, raw_candidates: list[str]) -> None:
        self.name = name
        self.raw_candidates = raw_candidates
        listed = ", ".join(f"codespace:{c}" for c in raw_candidates)
        super().__init__(
            f"Codespace name '{name}' is ambiguous -- it matches "
            f"{len(raw_candidates)} codespaces: {listed}. "
            "Use the full (raw) name to disambiguate."
        )


def _find_codespace(codespaces, name: str):
    """Find a codespace by raw name or friendly (display) name.

    An exact raw-name match always wins (unambiguous). Otherwise the friendly
    display name is matched (exact, then case-insensitive). Raises
    ``AmbiguousCodespaceError`` if a friendly name matches more than one
    codespace, or ``KeyError`` if nothing matches.
    """
    # 1. Exact raw name -- authoritative and unambiguous.
    for c in codespaces:
        if c.name == name:
            return c
    # 2. Friendly (display) name -- exact, then case-insensitive.
    lname = name.lower()
    friendly = [
        c for c in codespaces
        if c.display_name and (
            c.display_name == name or c.display_name.lower() == lname
        )
    ]
    if len(friendly) == 1:
        return friendly[0]
    if len(friendly) > 1:
        raise AmbiguousCodespaceError(name, [c.name for c in friendly])
    # 3. Case-insensitive raw name.
    for c in codespaces:
        if c.name.lower() == lname:
            return c
    raise KeyError(name)


def _friendly_aliases(cs) -> list[str]:
    """Alternate names a codespace also answers to (its friendly display name)."""
    aliases: list[str] = []
    if cs.display_name and cs.display_name != cs.name:
        aliases.append(cs.display_name)
    return aliases


def _build_spawn_command(codespace_name: str, acp_command: str) -> list[str]:
    """Build the spawn command for a codespace agent.

    The ``acp_command`` is read from ``codespaces.yaml`` defaults and
    passed as ``--remote-cmd`` to ``agent-codespaces ssh --stdio``.

    Invokes the module directly (``python -m agent_codespaces``) rather
    than the ``.cmd`` binstub so agent-bridge does not route the spawn
    through cmd.exe, which would expand ``%VAR%`` tokens in the
    ``--remote-cmd`` payload and mangle it (see ``._invoke``).
    """
    return [
        *module_argv(),
        "ssh", codespace_name, "--stdio",
        # The bridge dispatch is the authoritative transport for this CodeSpace
        # and must succeed even when a stale incumbent (e.g. a prior dispatch
        # child that has not fully exited) still holds the per-target SSH lock.
        # --force lets it reclaim the target; ad-hoc CLI calls omit it and are
        # rejected against a busy target instead.
        "--force",
        "--remote-cmd", acp_command,
    ]


class CodespaceResolver:
    """Namespace resolver for ``codespace:<name>`` agent routing.

    Resolves codespace names on demand by querying ``gh codespace list``
    and returning SpawnTargets that use ``agent-codespaces ssh --stdio``
    for transport.
    """

    @property
    def prefix(self) -> str:
        return "codespace"

    async def resolve(self, name: str) -> "SpawnTarget":
        """Resolve a codespace name to a SpawnTarget.

        Accepts either the raw codespace name or its friendly (display) name.
        Accepts CodeSpaces in Available or Shutdown state.  Shutdown
        CodeSpaces will be started automatically by ``gh`` during the
        SSH connection (``gh codespace ssh --config`` triggers startup).
        """
        from agent_bridge.transport import SpawnTarget

        codespaces = await asyncio.to_thread(list_codespaces)
        try:
            cs = _find_codespace(codespaces, name)
        except KeyError:
            connectable = [
                c.name for c in codespaces
                if c.state in ("Available", "Shutdown")
            ]
            raise KeyError(
                f"Codespace '{name}' not found. Available: {connectable}"
            ) from None

        _CONNECTABLE_STATES = {"Available", "Shutdown"}
        if cs.state not in _CONNECTABLE_STATES:
            raise ValueError(
                f"Codespace '{cs.name}' is in state '{cs.state}' "
                f"(must be one of {_CONNECTABLE_STATES} to spawn an agent)"
            )

        if cs.state == "Shutdown":
            log.info(
                "Codespace '%s' is Shutdown — will auto-start during SSH "
                "connection (may take 60-120 s)",
                cs.name,
            )

        config = load_merged_config()
        # Always spawn against the RAW codespace name (gh requires it), even if
        # the caller addressed it by friendly name. Resolve the launch command
        # per CodeSpace *repository* so the agent lands in the right checkout
        # (e.g. odsp-web-codespaces -> /workspaces/odsp-web), not the global
        # default workspace folder.
        spawn_cmd = _build_spawn_command(
            cs.name, config.effective_acp_command_for(cs.repository)
        )
        log.info("Resolved codespace:%s -> %s", cs.name, " ".join(spawn_cmd))

        return SpawnTarget(
            type="command",
            spawn_command=spawn_cmd,
            user=config.ssh_user,
        )

    async def list(self) -> list["NamespaceAgentInfo"]:
        """List all codespaces as namespace agent info."""
        from agent_bridge.agent_registry import NamespaceAgentInfo

        codespaces = await asyncio.to_thread(list_codespaces)
        agents = []
        for cs in codespaces:
            repo_short = cs.repository.split("/")[-1] if cs.repository else ""
            display = cs.display_name or cs.name
            if repo_short:
                display = f"{display} ({repo_short})"

            description = f"GitHub Codespace: {cs.repository}"
            if cs.branch:
                description += f"@{cs.branch}"

            state = cs.state.lower() if cs.state else "unknown"

            agents.append(NamespaceAgentInfo(
                name=cs.name,
                display_name=display,
                description=description,
                icon="codespace",
                state=state,
                aliases=_friendly_aliases(cs),
            ))

        return agents

    async def ensure_ready(self, name: str) -> None:
        """Verify codespace is reachable (or can be auto-started).

        Accepts the raw or friendly name, and Available/Shutdown states.
        Shutdown CodeSpaces are auto-started by ``gh`` when the SSH connection
        is established, so they are considered "ready" here.
        """
        codespaces = await asyncio.to_thread(list_codespaces)
        try:
            cs = _find_codespace(codespaces, name)
        except KeyError:
            raise RuntimeError(f"Codespace '{name}' not found") from None
        if cs.state in ("Available", "Shutdown"):
            return
        raise RuntimeError(
            f"Codespace '{cs.name}' is '{cs.state}' (not in a connectable state)."
        )
