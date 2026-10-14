"""Apply a bound worktree charter without changing venue ownership."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .routes.worktrees import _WorktreeEntry

log = logging.getLogger("agent-bridge")


def _apply_bound_charter(
    target: Any, resolver: Any, entry: "_WorktreeEntry", worktree_id: str,
) -> Any:
    """Layer a worktree's bound charter onto its venue-resolved spawn target.

    agent-bridge-worktree-native-agents: a charter (``bound_agent``) is a
    spawn PROFILE, never a first-class fabric target -- the venue still
    owns host/cwd/project resolution. Only the charter's own launch shape
    (``copilot_path``, ``mcp_servers``, charter ``copilot_args`` APPENDED
    after the venue's own -- so venue-level staging like `--plugin-dir`/
    `--allow-all` survives -- and any charter ``env`` layered over the
    venue's) is borrowed, when the charter name resolves in the registry.
    An unresolvable or managed (non-spawnable) charter degrades to the
    venue default rather than failing the spawn.
    Selector padding is normalized here, not in persisted worktree text or JSON.
    """
    bound_agent = (entry.bound_agent or "").strip()
    if not bound_agent:
        return target
    canonical = resolver.canonical_agent_name(bound_agent)
    charter = resolver.agents.get(canonical) if canonical else None
    if charter is None:
        log.warning(
            "worktree %s: bound_agent %r not found in the agent registry; "
            "using the venue's default spawn profile",
            worktree_id, entry.bound_agent,
        )
        return target
    if getattr(charter, "managed", False):
        # Mirror AgentResolver._resolve_static's own managed guard: a
        # managed=true entry is explicitly non-spawnable and must never be
        # borrowed as a charter's launch shape, even though a worktree's
        # bound_agent binding is independent of that resolve-time gate.
        log.warning(
            "worktree %s: bound_agent %r is managed (non-spawnable); "
            "using the venue's default spawn profile",
            worktree_id, entry.bound_agent,
        )
        return target
    return replace(
        target,
        copilot_path=charter.copilot_path or target.copilot_path,
        copilot_args=[*target.copilot_args, *charter.copilot_args],
        mcp_servers=list(charter.mcp_servers) or target.mcp_servers,
        env={**target.env, **charter.env},
    )
