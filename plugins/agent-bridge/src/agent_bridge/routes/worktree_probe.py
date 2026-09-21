"""Single-target worktree existence/ownership resolution (#3015, #6744).

Fan-out probes used when the fleet-wide ``WorktreeDiscoveryCache`` doesn't
(yet) know about a worktree -- a targeted, live ``list --worktree-id`` query
across every eligible agent, returning on first match rather than waiting on
a slow/unreachable one. Two flavors:

- :func:`owning_agent` / :func:`probe_archived_owner` -- resolve *who owns*
  an already-gone worktree from an archived/tombstoned record (session and
  lineage routes, #3015).
- :func:`probe_live_worktree` -- resolve a *full entry* (owner + on-disk
  path) from an on-disk-live record only (deliberately excludes archived/
  reaped ones), used as the cache-blind/stale-cache resume fallback (#6744,
  review #3121).

Split out of ``worktrees.py`` (module-size cap; see CONTRIBUTING.md's
Componentization convention). These fan-out helpers call back into
``worktrees.py`` (``_run_for_agent``, ``_parse_worktree_list``, ``get_cache``)
via a **deferred, module-qualified** lookup (``from . import worktrees as
wt`` inside each function, then ``wt.<name>``) rather than a top-level
``from .worktrees import <name>``. This avoids a circular import (this
module is itself imported by ``worktrees.py``) and, just as importantly,
keeps a test's ``patch("agent_bridge.routes.worktrees._run_for_agent", ...)``
effective -- a top-level import would freeze the reference at import time
and silently bypass the patch, since the patched attribute lives on the
``worktrees`` module object, not this one.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from fastapi import Request

from ..agent_registry import AgentConfig, AgentResolver

if TYPE_CHECKING:
    from .worktrees import _WorktreeEntry


async def owning_agent(
    worktree_id: str, request: Request,
) -> tuple[str, AgentConfig] | None:
    """Resolve which configured agent owns ``worktree_id``.

    Checks the live discovery cache first; falls back to an explicit
    ``archived`` record probe (cache never crawls tombstoned worktrees,
    #3015) so an archived worktree's owner still resolves.
    """
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is None:
        return None

    from . import worktrees as wt

    cache = wt.get_cache()
    for agent_name, entries in cache.get_all().items():
        if any(entry.id == worktree_id for entry in entries):
            config = resolver.agents.get(agent_name)
            if config is not None:
                return agent_name, config

    return await cache.probe_archived(worktree_id, resolver)


async def probe_archived_owner(
    worktree_id: str, resolver: AgentResolver,
) -> tuple[str, AgentConfig] | None:
    """Fan an archived-record probe across every eligible agent, returning
    on first match instead of waiting on a slow/unreachable agent (avoidable
    latency otherwise). Only ever called single-flight via
    :meth:`WorktreeDiscoveryCache.probe_archived`.
    """
    from . import worktrees as wt

    eligible = [
        (name, cfg) for name, cfg in resolver.agents.items()
        if cfg.project and cfg.worktree_discovery
    ]
    if not eligible:
        return None
    args = [
        "list", "--json", "--tracking-status", "archived", "--all",
        "--worktree-id", worktree_id,
    ]
    tasks = {
        asyncio.create_task(wt._run_for_agent(name, cfg, resolver, args)): (name, cfg)
        for name, cfg in eligible
    }
    pending = set(tasks)
    match: tuple[str, AgentConfig] | None = None
    try:
        while pending and match is None:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.cancelled() or task.exception() is not None:
                    continue
                if worktree_id_in_payload(task.result(), worktree_id):
                    match = tasks[task]
                    break
        return match
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def worktree_id_in_payload(raw: str | None, worktree_id: str) -> bool:
    """True if a ``list --json`` payload's ``worktrees`` array names ``worktree_id``."""
    if raw is None:
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    worktrees = data.get("worktrees") if isinstance(data, dict) else None
    return isinstance(worktrees, list) and any(
        isinstance(wt, dict) and wt.get("id") == worktree_id for wt in worktrees
    )


async def probe_live_worktree(
    worktree_id: str, resolver: AgentResolver,
) -> "tuple[str, _WorktreeEntry] | None":
    """Fan a targeted, single-worktree ``list --worktree-id`` probe across
    every eligible agent, returning the first match's owner + entry (#6744).

    Mirrors :func:`probe_archived_owner`'s fan-out (returning on first match
    instead of waiting on a slow/unreachable agent), but resolves a fresh,
    genuinely-live worktree the crawl cache simply hasn't seen yet
    (cache-blind or stale-cache), not a tombstoned one -- deliberately
    **without** ``--all``/``--tracking-status archived``, so a reaped record
    whose on-disk directory is gone never matches here (a resumed worktree
    must have a real checkout to spawn a fresh session into, review #3121).
    Returns the full ``_WorktreeEntry`` (needed for the worktree's on-disk
    path), not just the owning agent. Only ever called single-flight via
    :meth:`WorktreeDiscoveryCache.probe_live`.
    """
    from . import worktrees as wt

    eligible = [
        (name, cfg) for name, cfg in resolver.agents.items()
        if cfg.project and cfg.worktree_discovery
    ]
    if not eligible:
        return None
    args = ["list", "--json", "--mux-details", "--worktree-id", worktree_id]
    tasks = {
        asyncio.create_task(wt._run_for_agent(name, cfg, resolver, args)): name
        for name, cfg in eligible
    }
    pending = set(tasks)
    match: tuple[str, "_WorktreeEntry"] | None = None
    try:
        while pending and match is None:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.cancelled() or task.exception() is not None:
                    continue
                raw = task.result()
                if raw is None:
                    continue
                agent_name = tasks[task]
                found = next(
                    (e for e in wt._parse_worktree_list(raw, agent_name)
                     if e.id == worktree_id),
                    None,
                )
                if found is not None:
                    match = (agent_name, found)
                    break
        return match
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
