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
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agent_registry import AgentConfig, AgentResolver
from ..models import SessionInfo, SessionStatus

log = logging.getLogger("agent-bridge")

router = APIRouter(tags=["worktrees"])

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
    session_count: int = 0
    turn_count: int = 0
    # Interactive-mux (wt-<id> tmux/psmux) liveness on the owning machine.
    # This is the *second ownership* NF must see (#1883): a worktree held by a
    # live picker-launched Copilot CLI, distinct from a bridge ACP session.
    mux_session: bool = False
    mux_clients: int | None = None
    mux_attached: bool | None = None

    def interactive_cli_state(self) -> str:
        """Classify interactive-CLI ownership from mux liveness.

        - ``held``    -- a wt-<id> mux session exists and a terminal is
          attached (or attachment is unknown): a live interactive Copilot CLI
          owns the worktree and is being actively viewed.  Do-not-disturb.
        - ``at-rest`` -- a wt-<id> mux session exists but is detached (no
          terminal attached): the interactive Copilot is still running but
          nobody is watching.  Still a live process -- reclaim via take-over.
        - ``none``    -- no interactive mux session; the worktree is not held
          by an interactive Copilot CLI.
        """
        if not self.mux_session:
            return "none"
        if self.mux_attached is False:
            return "at-rest"
        return "held"

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
            "session_count": self.session_count,
            "turn_count": self.turn_count,
            "mux_session": self.mux_session,
            "mux_clients": self.mux_clients,
            "mux_attached": self.mux_attached,
            "interactive_cli": self.interactive_cli_state(),
        }


class WorktreeDiscoveryCache:
    """In-memory cache for discovered worktrees.

    When *interval* > 0, a background task refreshes the cache
    periodically.  When *interval* is 0 (the default), no background
    task is created and the cache is populated on-demand via
    :meth:`crawl_if_empty`.
    """

    def __init__(self, interval: float = 0) -> None:
        self._cache: dict[str, list[_WorktreeEntry]] = {}
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._resolver: AgentResolver | None = None
        self._crawl_lock = asyncio.Lock()

    def configure(self, *, interval: float) -> None:
        """Update the discovery interval (must be called before start)."""
        self._interval = interval

    def start(self, resolver: AgentResolver) -> None:
        """Start periodic discovery in the background (if interval > 0)."""
        self._resolver = resolver
        if self._interval > 0:
            self._task = asyncio.create_task(
                self._loop(resolver), name="worktree-discovery",
            )
            log.info(
                "Worktree periodic discovery enabled (interval=%.0fs)",
                self._interval,
            )
        else:
            log.info("Worktree periodic discovery disabled (on-demand only)")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_all(self) -> dict[str, list[_WorktreeEntry]]:
        return dict(self._cache)

    async def crawl_if_empty(self) -> None:
        """Trigger a crawl only if the cache has no data yet.

        Uses a lock to prevent concurrent callers from stampeding
        multiple crawls simultaneously.
        """
        if self._cache or not self._resolver:
            return
        async with self._crawl_lock:
            if self._cache:
                return
            await self.crawl(self._resolver)

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
            raw = await _run_local(config.project, ["list", "--json", "--mux-details"])
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
                raw = await _run_local(config.project, ["list", "--json", "--mux-details"])
            else:
                raw = await _run_ssh(
                    host=target.host or config.host,
                    user=target.user or config.ssh_user,
                    project=config.project,
                    args=["list", "--json", "--mux-details"],
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


async def _run_local(project: str, args: list[str] | None = None) -> str | None:
    """Run ``<project> <args>`` locally (defaults to ``list --json``)."""
    from pathlib import Path

    home = Path.home()
    binstub = home / ".local" / "bin" / project
    if not binstub.exists():
        # Fall back to PATH
        binstub_str = project
    else:
        binstub_str = str(binstub)

    cmd = [binstub_str, *(args if args is not None else ["list", "--json"])]
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

    from ..agent_registry import _detect_local_machine
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


async def _run_ssh(
    host: str, user: str | None, project: str, args: list[str] | None = None,
) -> str | None:
    """Run ``<project> <args>`` on a remote machine via SSH."""
    ssh_target = f"{user}@{host}" if user else host
    sub = " ".join(shlex.quote(a) for a in (args if args is not None else ["list", "--json"]))
    remote_cmd = f"{shlex.quote(project)} {sub}"

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
    proc: asyncio.subprocess.Process | None = None
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
        log.error("Command timed out after %.0fs: %s", _CMD_TIMEOUT, " ".join(cmd))
        if proc:
            proc.kill()
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                pass
        return None
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.kill()
            try:
                await proc.communicate()
            except Exception:
                pass
        raise
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
            session_count=w.get("session_count", 0),
            turn_count=w.get("turn_count", 0),
            mux_session=bool(w.get("mux_session", False)),
            mux_clients=w.get("mux_clients"),
            mux_attached=w.get("mux_attached"),
        ))
    return entries


# -- Singleton cache (initialized via app lifespan) ---------------------------

_discovery_cache = WorktreeDiscoveryCache()


def get_cache() -> WorktreeDiscoveryCache:
    return _discovery_cache


# -- Route handlers -----------------------------------------------------------


@router.get("/api/v1/worktrees")
async def list_worktrees(request: Request) -> dict[str, Any]:
    """List discovered worktrees across all agents, grouped by agent name.

    Returns all worktrees reported by each machine's binstub.  Worktrees
    that have been deleted from disk are not returned (the binstub only
    reports what physically exists).

    Each worktree is decorated with linkage to its most recent bridge
    session (if any) so consumers can load history, resume, or detect a
    session that is still live:

    - ``session_id``: latest bridge session for this worktree, or None
    - ``acp_session_id``: that session's ACP-sourced id (durable identity),
      or None
    - ``session_status``: that session's status (idle/running/stopped/...)
    - ``session_turn_count``: number of prompt turns on that session
    - ``session_live``: True if the session is currently running or idle
      with a live process (i.e. attached/active, not stopped or ended)

    Each worktree also carries interactive-mux (``wt-<id>`` tmux/psmux)
    liveness on its owning machine -- the *second ownership* a consumer must
    respect (a live picker-launched Copilot CLI, distinct from a bridge ACP
    session, #1883):

    - ``mux_session``: True if a ``wt-<id>`` mux session exists on the machine
    - ``mux_clients``: attached terminal count (None if unknown)
    - ``mux_attached``: whether a terminal is attached (None if unknown)
    - ``interactive_cli``: ``held`` (attached/unknown), ``at-rest``
      (detached but running), or ``none`` -- so a consumer can render a
      do-not-disturb badge and route to take-over instead of a blind connect

    When periodic discovery is disabled, the first request triggers an
    on-demand crawl (subsequent requests return cached results).
    """
    cache = get_cache()
    await cache.crawl_if_empty()
    groups = cache.get_all()

    # Build worktree_id -> latest bridge session map for linkage.  A worktree
    # may have had several sessions over its life (session rolls); pick the
    # most recently updated one.  list_sessions() is already sorted
    # newest-first, so the first match per worktree wins.
    latest_by_wt: dict[str, Any] = {}
    mgr = getattr(request.app.state, "session_manager", None)
    if mgr is not None:
        for session in mgr.list_sessions():
            wt_id = getattr(session.target, "worktree_id", None)
            if wt_id and wt_id not in latest_by_wt:
                latest_by_wt[wt_id] = session

    def _decorate(wt: _WorktreeEntry) -> dict[str, Any]:
        entry = wt.to_dict()
        session = latest_by_wt.get(wt.id)
        if session is not None:
            status = session.status.value
            entry["session_id"] = session.session_id
            entry["acp_session_id"] = session.acp_session_id
            entry["session_status"] = status
            entry["session_turn_count"] = session.turn_count
            entry["session_live"] = status in ("running", "idle")
        else:
            entry["session_id"] = None
            entry["acp_session_id"] = None
            entry["session_status"] = None
            entry["session_turn_count"] = 0
            entry["session_live"] = False
        return entry

    return {
        "groups": {
            name: [_decorate(wt) for wt in worktrees]
            for name, worktrees in groups.items()
        },
    }


def _latest_session_for_worktree(mgr: Any, worktree_id: str) -> Any:
    """Return the most-recently-updated bridge session for a worktree, or None."""
    if mgr is None:
        return None
    for session in mgr.list_sessions():  # sorted newest-first
        if getattr(session.target, "worktree_id", None) == worktree_id:
            return session
    return None


@router.post("/api/v1/worktrees/{worktree_id}/resume", response_model=SessionInfo)
async def resume_worktree(worktree_id: str, request: Request) -> SessionInfo:
    """Resume a worktree by ensuring it has a live session.

    Worktree-level convenience verb: finds the current (most recent) session
    for the worktree and ensures it is live.

    - An already-live session is returned as-is.
    - A stopped session is resumed (ACP load_session reuses the same acp
      session id).
    - If that session can no longer be resumed (e.g. the agent no longer
      knows it -- common for old/finalized worktrees), fall back to starting
      a fresh session in the same worktree directory, since the worktree
      still exists on disk.  This keeps "open existing worktree" robust.

    Returns 404 if the worktree has no session at all.
    """
    from .sessions import _session_info

    mgr = getattr(request.app.state, "session_manager", None)
    session = _latest_session_for_worktree(mgr, worktree_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for worktree {worktree_id}",
        )

    # Already live -- nothing to do, return current state.
    if session.status in (SessionStatus.RUNNING, SessionStatus.IDLE):
        return _session_info(session)

    try:
        resumed = await mgr.resume_session(session.session_id)
        return _session_info(resumed)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session.session_id} not found",
        ) from exc
    except ValueError as exc:
        # Not actually stopped / nothing to resume -- return current state.
        log.info("resume_worktree %s: %s; returning current state", worktree_id, exc)
        return _session_info(session)
    except Exception as exc:
        # Resume failed (e.g. ACP session gone). Fall back to a fresh session
        # in the same worktree so the worktree remains usable.
        log.warning(
            "resume_worktree %s: resume of %s failed (%s); starting fresh session",
            worktree_id, session.session_id, exc,
        )
        try:
            fresh = await mgr.start_session(
                session.target,
                agent_name=session.agent_name,
                caller_id=session.caller_id,
            )
        except Exception as start_exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Could not resume or restart worktree {worktree_id}: "
                    f"{start_exc}"
                ),
            ) from start_exc
        return _session_info(fresh)


# -- Session reading (worktree-scoped) ----------------------------------------


async def _run_for_agent(
    agent_name: str,
    config: AgentConfig,
    resolver: AgentResolver,
    args: list[str],
) -> str | None:
    """Run ``<project> <args>`` on the host that owns ``agent_name``.

    Mirrors the host dispatch in ``_crawl_agent`` (local vs SSH, resolved
    through topology), but with an arbitrary subcommand instead of the
    hardcoded ``list --json``.
    """
    if not config.project:
        return None

    if not config.host:
        return await _run_local(config.project, args)

    try:
        target = resolver.resolve(agent_name)
    except (KeyError, ValueError) as exc:
        log.warning("Cannot resolve agent %s for session read: %s", agent_name, exc)
        return None

    if _is_local_target(target.host, resolver):
        return await _run_local(config.project, args)

    return await _run_ssh(
        host=target.host or config.host,
        user=target.user or config.ssh_user,
        project=config.project,
        args=args,
    )


def _owning_agent(
    worktree_id: str, request: Request,
) -> tuple[str, AgentConfig] | None:
    """Resolve which configured agent owns ``worktree_id``.

    Uses the discovery cache to find the agent group that contains the
    worktree, then looks up that agent's config on the resolver.  Returns
    ``(agent_name, config)`` or None if the worktree isn't known.
    """
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is None:
        return None

    cache = get_cache()
    for agent_name, worktrees in cache.get_all().items():
        if any(wt.id == worktree_id for wt in worktrees):
            config = resolver.agents.get(agent_name)
            if config is not None:
                return agent_name, config
    return None


@router.get("/api/v1/worktrees/{worktree_id}/sessions")
async def list_worktree_sessions(
    worktree_id: str, request: Request,
) -> dict[str, Any]:
    """List the CLI sessions belonging to a worktree.

    Shells out to ``<project> list-sessions --worktree <id> --json`` on the
    machine that owns the worktree (local or via SSH).  This is the
    authoritative, branch-independent session registry maintained by
    agent-worktrees -- it counts sessions launched by the picker *and* by
    agent-bridge / Mission Control (which carry no ``branch`` field).
    """
    cache = get_cache()
    await cache.crawl_if_empty()

    owner = _owning_agent(worktree_id, request)
    if owner is None:
        raise HTTPException(
            status_code=404,
            detail=f"Worktree {worktree_id} not found on any agent",
        )
    agent_name, config = owner
    resolver = request.app.state.resolver

    raw = await _run_for_agent(
        agent_name, config, resolver,
        ["list-sessions", "--worktree", worktree_id, "--json"],
    )
    if raw is None:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to read sessions for worktree {worktree_id}",
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid session JSON for worktree {worktree_id}",
        ) from exc

    sessions = data.get("sessions", data) if isinstance(data, dict) else data
    return {
        "worktree_id": worktree_id,
        "agent_name": agent_name,
        "sessions": sessions if isinstance(sessions, list) else [],
    }


@router.get("/api/v1/worktrees/{worktree_id}/sessions/{session_id}/transcript")
async def get_worktree_session_transcript(
    worktree_id: str, session_id: str, request: Request,
) -> dict[str, Any]:
    """Return the rendered transcript for a session in a worktree.

    Shells out to ``<project> session-transcript <session_id> --json`` on the
    machine that owns the worktree.  Lets Neuron Forge (and any other
    consumer) view a CLI transcript for *any* session in *any* worktree,
    including ones it did not launch, without crawling session-state itself.
    """
    cache = get_cache()
    await cache.crawl_if_empty()

    owner = _owning_agent(worktree_id, request)
    if owner is None:
        raise HTTPException(
            status_code=404,
            detail=f"Worktree {worktree_id} not found on any agent",
        )
    agent_name, config = owner
    resolver = request.app.state.resolver

    raw = await _run_for_agent(
        agent_name, config, resolver,
        ["session-transcript", session_id, "--json"],
    )
    if raw is None:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to read transcript for session {session_id}",
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid transcript JSON for session {session_id}",
        ) from exc

    return {
        "worktree_id": worktree_id,
        "agent_name": agent_name,
        "session_id": session_id,
        "events": data.get("events", []) if isinstance(data, dict) else [],
        "meta": data.get("meta") if isinstance(data, dict) else None,
    }


@router.post("/api/v1/worktrees/{worktree_id}/restart")
async def restart_worktree_copilot(
    worktree_id: str, request: Request, force: bool = False,
) -> dict[str, Any]:
    """Restart a worktree's interactive (mux-launched) Copilot in place.

    Shells out to ``<project> restart <id> --json`` on the machine that owns
    the worktree (local or via SSH).  The agent-worktrees ``restart`` primitive
    terminates the worktree's interactive Copilot -- graceful double Ctrl-C into
    the ``wt-<id>`` mux pane, then a hard mux ``kill-session`` fallback -- while
    **keeping the worktree on disk**, so a caller can relaunch (picker) or
    ACP-resume (Neuron Forge "Take over", #1388).

    This targets the **interactive mux Copilot**, not a bridge ACP session --
    distinct from ``DELETE /sessions/{id}`` / the worktree ``terminate`` path,
    which stop bridge-owned sessions.  Pass ``force=true`` to skip the graceful
    quit and hard-kill the mux session immediately (``--no-graceful``).

    Returns the primitive's JSON verdict:
    ``{worktree_id, had_session, method, ok}`` where ``method`` is one of
    ``none`` | ``graceful`` | ``hard`` | ``failed``.
    """
    cache = get_cache()
    await cache.crawl_if_empty()

    owner = _owning_agent(worktree_id, request)
    if owner is None:
        raise HTTPException(
            status_code=404,
            detail=f"Worktree {worktree_id} not found on any agent",
        )
    agent_name, config = owner
    resolver = request.app.state.resolver

    args = ["restart", worktree_id, "--json"]
    if force:
        args.append("--no-graceful")

    raw = await _run_for_agent(agent_name, config, resolver, args)
    if raw is None:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to restart Copilot for worktree {worktree_id}",
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid restart JSON for worktree {worktree_id}",
        ) from exc

    return {
        "worktree_id": data.get("worktree_id", worktree_id),
        "agent_name": agent_name,
        "had_session": bool(data.get("had_session", False)),
        "method": data.get("method", "unknown"),
        "ok": bool(data.get("ok", False)),
    }
