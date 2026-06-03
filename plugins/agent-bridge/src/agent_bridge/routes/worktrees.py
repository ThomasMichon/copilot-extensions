"""Worktree discovery endpoints -- /api/v1/worktrees.

Lists worktrees across all configured agents by running
``<project> list --json`` locally or via SSH on each machine.
Results are cached in-memory and refreshed periodically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request

from ..agent_registry import AgentConfig, AgentResolver

log = logging.getLogger("agent-bridge")

router = APIRouter(tags=["worktrees"])

DEFAULT_INTERVAL = 60.0  # seconds between sweeps
_CMD_TIMEOUT = 30.0


@dataclass
class _WorktreeEntry:
    """A discovered worktree on a machine."""

    id: str
    agent_name: str
    machine: str
    path: str
    branch: str
    status: str
    title: str | None = None
    started_at: str | None = None
    resume_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "machine": self.machine,
            "path": self.path,
            "branch": self.branch,
            "status": self.status,
            "title": self.title,
            "started_at": self.started_at,
            "resume_count": self.resume_count,
        }


class WorktreeDiscoveryCache:
    """In-memory cache for discovered worktrees, refreshed periodically."""

    def __init__(self, interval: float = DEFAULT_INTERVAL) -> None:
        self._cache: dict[str, list[_WorktreeEntry]] = {}
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self, resolver: AgentResolver) -> None:
        """Start periodic discovery in the background."""
        self._task = asyncio.create_task(
            self._loop(resolver), name="worktree-discovery",
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_all(self) -> dict[str, list[_WorktreeEntry]]:
        return dict(self._cache)

    async def crawl(self, resolver: AgentResolver) -> None:
        """Crawl all eligible agents concurrently."""
        eligible = [
            (name, cfg) for name, cfg in resolver.agents.items()
            if cfg.project
        ]
        if not eligible:
            return

        results = await asyncio.gather(
            *(self._crawl_agent(name, cfg, resolver) for name, cfg in eligible),
            return_exceptions=True,
        )

        for (name, _), result in zip(eligible, results):
            if isinstance(result, Exception):
                log.warning("Worktree discovery failed for %s: %s", name, result)
            else:
                self._cache[name] = result

    async def _crawl_agent(
        self,
        agent_name: str,
        config: AgentConfig,
        resolver: AgentResolver,
    ) -> list[_WorktreeEntry]:
        """List worktrees for a single agent via subprocess or SSH."""
        if not config.project:
            return []

        if not config.host:
            # Local
            raw = await _run_local(config.project)
        else:
            # SSH -- resolve through topology for correct alias/user
            try:
                target = resolver.resolve(agent_name)
            except (KeyError, ValueError) as exc:
                log.warning("Cannot resolve agent %s for discovery: %s", agent_name, exc)
                return []

            # If the resolved target is the local machine, run locally
            # instead of SSH (avoids loopback SSH failures)
            if _is_local_target(target.host, resolver):
                raw = await _run_local(config.project)
            else:
                raw = await _run_ssh(
                    host=target.host or config.host,
                    user=target.user or config.ssh_user,
                    project=config.project,
                )

        if raw is None:
            return []

        return _parse_worktree_list(raw, agent_name)

    async def _loop(self, resolver: AgentResolver) -> None:
        # Initial crawl
        await self.crawl(resolver)
        log.info("Worktree discovery started -- %d agents", len(self._cache))
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.crawl(resolver)
            except Exception:
                log.exception("Periodic worktree discovery failed")


# -- Subprocess helpers -------------------------------------------------------


async def _run_local(project: str) -> str | None:
    """Run ``<project> list --json`` locally."""
    import os
    from pathlib import Path

    home = Path.home()
    binstub = home / ".local" / "bin" / project
    if not binstub.exists():
        # Fall back to PATH
        binstub_str = project
    else:
        binstub_str = str(binstub)

    cmd = [binstub_str, "list", "--json"]
    return await _exec(cmd)


def _is_local_target(ssh_host: str | None, resolver: AgentResolver) -> bool:
    """Check if an SSH host alias resolves to the local machine AND environment.

    Returns True only when the SSH alias points to the same machine key
    AND the same platform (wsl/windows/linux) as the one we're running on.
    This avoids treating a Windows agent as "local" when running on WSL
    (or vice versa), even though they share the same physical machine.
    """
    if not ssh_host:
        return True

    import socket
    hostname = socket.gethostname().lower()
    host_lower = ssh_host.lower()

    from ..agent_registry import _detect_local_machine, _detect_platform
    machine, platform = _detect_local_machine(resolver.machines)
    if not machine:
        # If we can't identify our own machine, only match exact hostname
        return host_lower == hostname

    # Check if the SSH alias matches any alias for the local machine's
    # environments — but only the environment matching our platform
    for env in machine.ssh_environments:
        if env.alias and env.alias.lower() == host_lower:
            # Alias matches — is it our platform?
            return env.name == platform

    # Direct hostname match only if we can't resolve via aliases
    if host_lower == hostname or host_lower == machine.key.lower():
        # Ambiguous — could be any environment. Only treat as local
        # if there's exactly one environment and it matches our platform.
        matching = [e for e in machine.ssh_environments if e.name == platform]
        return len(matching) == 1

    return False


async def _run_ssh(host: str, user: str | None, project: str) -> str | None:
    """Run ``<project> list --json`` on a remote machine via SSH."""
    ssh_target = f"{user}@{host}" if user else host
    remote_cmd = f"{shlex.quote(project)} list --json"

    cmd = [
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-T",
        ssh_target,
        remote_cmd,
    ]
    return await _exec(cmd)


async def _exec(cmd: list[str]) -> str | None:
    """Execute a command and return stdout, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CMD_TIMEOUT,
        )
        if proc.returncode != 0:
            log.error(
                "Command failed (rc=%d): %s\nstderr: %s",
                proc.returncode, " ".join(cmd),
                stderr.decode(errors="replace")[:500],
            )
            return None
        return stdout.decode()
    except TimeoutError:
        log.error("Command timed out: %s", " ".join(cmd))
        return None
    except Exception:
        log.exception("Failed to run: %s", " ".join(cmd))
        return None


def _parse_worktree_list(raw: str, agent_name: str) -> list[_WorktreeEntry]:
    """Parse agent-worktrees JSON output into WorktreeEntry objects."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Invalid JSON from worktree list for %s: %s", agent_name, raw[:200])
        return []

    # Handle enveloped ({"version":1,"worktrees":[...]}) or flat list
    items = data.get("worktrees", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    entries: list[_WorktreeEntry] = []
    for w in items:
        wt_id = w.get("id") or w.get("name", "")
        if not wt_id:
            continue
        entries.append(_WorktreeEntry(
            id=wt_id,
            agent_name=agent_name,
            machine=w.get("machine", agent_name),
            path=w.get("path", ""),
            branch=w.get("branch", ""),
            status=w.get("status", "active" if w.get("active") else "ended"),
            title=w.get("title"),
            started_at=w.get("started_at"),
            resume_count=w.get("resume_count", 0),
        ))
    return entries


# -- Singleton cache (initialized via app lifespan) ---------------------------

_discovery_cache = WorktreeDiscoveryCache()


def get_cache() -> WorktreeDiscoveryCache:
    return _discovery_cache


# -- Route handlers -----------------------------------------------------------


_TERMINAL_STATUSES = frozenset({"finalized", "ended"})


@router.get("/api/v1/worktrees")
async def list_worktrees(
    request: Request,
    include_finalized: bool = False,
) -> dict[str, Any]:
    """List discovered worktrees across all agents, grouped by agent name.

    By default only non-finalized worktrees are returned.  Pass
    ``?include_finalized=true`` to include completed worktrees.
    """
    cache = get_cache()
    groups = cache.get_all()
    return {
        "groups": {
            name: [
                wt.to_dict() for wt in worktrees
                if include_finalized or wt.status not in _TERMINAL_STATUSES
            ]
            for name, worktrees in groups.items()
        },
    }
