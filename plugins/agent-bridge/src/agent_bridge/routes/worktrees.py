"""Worktree discovery endpoints -- /api/v1/worktrees.

Lists worktrees across all configured agents by running
``<project> list --json`` locally or via SSH; cached in-memory and
refreshed periodically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
import time
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request

from ..agent_registry import AgentConfig, AgentResolver
from ..loop_governance import LoopGovernance
from ..models import SessionInfo, WorktreeHandoffRequest
from ..session_manager import (
    DaemonDrainingError,
    SessionManager,
)
from .worktree_binstub import resolve_local_binstub as _resolve_local_binstub
from .worktree_entry import WorktreeEntry as _WorktreeEntry
from .worktree_refresh import DiscoveryReadError, ObservedRows, WorktreeRefreshOwner, is_option_unsupported, validate_inventory
from .worktree_refresh import is_classify_unsupported as _is_classify_unsupported
from .worktree_probe import (
    find_worktree_entry as _find_cached_worktree_entry,  # noqa: F401 -- native resume shares this owner boundary
    owning_agent as _owning_agent,
    probe_archived_owner as _probe_archived_owner,
    probe_live_worktree as _probe_live_worktree,
    resolve_already_live as _resolve_already_live,  # noqa: F401 -- native resume shares this liveness boundary
)

log = logging.getLogger("agent-bridge")

router = APIRouter(tags=["worktrees"])

_CMD_TIMEOUT = 30.0
# Phase-5 (worktree-finality-and-obligations): a longer budget for a
# `--classify` crawl -- the extra ~5 git calls per worktree can exceed
# _CMD_TIMEOUT on a large/slow target. Mirrors the Picker's own 60s budget.
_CLASSIFY_CMD_TIMEOUT = 60.0
_GOVERNANCE_BACKOFF_SECONDS = 10.0

#: Per-worktree locks serializing the fresh-start spawn (#1683) so two
#: concurrent resumes can't each create a second owned controller in a
#: worktree the SessionManager doesn't hard-guard (local/SSH targets).
_fresh_start_locks: dict[str, asyncio.Lock] = {}


class WorktreeDiscoveryCache:
    """In-memory cache for discovered worktrees.

    When *interval* > 0, a background task refreshes the cache
    periodically. When 0 (the default), it's populated on-demand via
    :meth:`crawl_if_empty`.
    """

    def __init__(self, interval: float = 0) -> None:
        self._cache: dict[str, list[_WorktreeEntry]] = {}
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._resolver: AgentResolver | None = None
        self._refresh = WorktreeRefreshOwner()
        self._governance: LoopGovernance | None = None
        self._owner_thread: threading.Thread | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        # Python 3.10 has no Task.cancelling(); track cancellation
        # requests ourselves so worktree_runtime.capture_worktrees() can
        # detect an in-progress cancel without that 3.11-only API.
        self._cancel_requested = False
        # worktree-finality-and-obligations (Phase 5): the classify-backfill
        # tasks crawl_if_empty() spawns must obey the same daemon lifecycle as
        # the periodic loop -- retained here so stop() can cancel/await them
        # instead of leaving a live _exec_ex subprocess mutating the cache
        # during the remainder of application shutdown.
        self._backfill_tasks: set[asyncio.Task[None]] = set()
        # Phase-5 follow-up (review): an agent whose remote agent-worktrees
        # rejects --classify stays that way for the life of the process (it
        # doesn't get upgraded without a redeploy) -- cache the verdict per
        # agent so a long-lived bridge daemon doesn't repeat a failed
        # classify probe on every periodic sweep. Mirrors the Picker's own
        # per-source `use_classify` caching (picker_tui.data_ssh).
        self._classify_unsupported: set[str] = set()
        self._fresh_unsupported: set[str] = set()
        # Coalesce concurrent same-id archived probes onto one in-flight
        # task instead of an N-agent stampede per call.
        self._archive_probe_inflight: dict[
            str, asyncio.Task[tuple[str, AgentConfig] | None]
        ] = {}
        # Coalesce concurrent same-id live-existence probes (#6744) onto one
        # in-flight task, same rationale as ``_archive_probe_inflight`` above
        # but returning the full entry (needed for the worktree's on-disk
        # path), and not restricted to archived records.
        self._live_probe_inflight: dict[
            str, asyncio.Task["tuple[str, _WorktreeEntry] | None"]
        ] = {}

    def configure(self, *, interval: float) -> None:
        """Update the discovery interval (must be called before start)."""
        self._interval = interval

    def start(
        self,
        resolver: AgentResolver,
        *,
        governance: LoopGovernance | None = None,
    ) -> None:
        """Start periodic discovery in the background (if interval > 0)."""
        if self._refresh.closed:
            if self._refresh.pending:
                raise RuntimeError("Worktree discovery still owns a shutdown refresh")
            self._refresh = WorktreeRefreshOwner()
            self._cache = {}
        self._sync_sources(resolver)
        self._resolver = resolver
        self._governance = governance
        self._owner_thread = threading.current_thread()
        self._owner_loop = asyncio.get_running_loop()
        if self._interval > 0:
            self._cancel_requested = False
            self._task = asyncio.create_task(
                self._loop(resolver), name="worktree-discovery",
            )
            log.info(
                "Worktree periodic discovery enabled (interval=%.0fs)",
                self._interval,
            )
        else:
            # A completed (possibly cancelled) task from a prior
            # periodic run must not linger: capture_worktrees() refuses
            # any retained completed task regardless of the cancel flag.
            self._task = None
            self._cancel_requested = False
            log.info("Worktree periodic discovery disabled (on-demand only)")

    async def stop(self) -> None:
        await self._refresh.close()
        if self._task and not self._task.done():
            self._cancel_requested = True
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Cancel scheduling/joining wrappers, not the refresh they observe.
        # Exact-ID probes retain their existing cancellation policy.
        for pending in (
            [t for t in self._backfill_tasks if not t.done()],
            [t for t in self._archive_probe_inflight.values() if not t.done()],
            [t for t in self._live_probe_inflight.values() if not t.done()],
        ):
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception("Worktree background task raised during shutdown")

    def get_all(self) -> dict[str, list[_WorktreeEntry]]:
        return dict(self._cache)

    def _sync_sources(self, resolver: AgentResolver | None) -> None:
        self._cache = self._refresh.sync_sources(
            resolver, self._cache, self._classify_unsupported, self._fresh_unsupported,
        )

    def render_snapshot(self) -> dict[str, Any]:
        self._sync_sources(self._resolver)
        if self._refresh.needs_refresh(max_age=max(30.0, self._interval)):
            self._ensure_warm()
        return self._refresh.metadata(max_age=max(30.0, self._interval))

    def _ensure_warm(self) -> asyncio.Task[None] | None:
        resolver = self._resolver
        if resolver is None:
            return None
        return self._refresh.warm(
            lambda epoch: self._do_crawl(resolver, classify=False, epoch=epoch),
            lambda: self._backfill_classify(resolver), self._backfill_tasks,
        )

    async def refresh_live(self) -> dict[str, Any]:
        self._sync_sources(self._resolver)
        task = self._refresh.ensure(
            lambda epoch: self._do_crawl(self._resolver, classify=True, fresh=True, epoch=epoch),
            live=True,
        )
        return await self._refresh.wait(task)

    async def probe_archived(
        self, worktree_id: str, resolver: AgentResolver,
    ) -> tuple[str, AgentConfig] | None:
        """Single-flight archived-record probe: coalesces concurrent callers
        for the same ``worktree_id`` onto one in-flight task, shielded from
        a cancelled caller.
        """
        task = self._archive_probe_inflight.get(worktree_id)
        if task is None:
            task = asyncio.create_task(_probe_archived_owner(worktree_id, resolver))
            self._archive_probe_inflight[worktree_id] = task

            def _evict(t: asyncio.Task, wid: str = worktree_id) -> None:
                if self._archive_probe_inflight.get(wid) is t:
                    del self._archive_probe_inflight[wid]

            task.add_done_callback(_evict)
        # Shield: a cancelled caller must never cancel the shared task out
        # from under other concurrent callers; eviction is done-callback-only.
        return await asyncio.shield(task)

    async def probe_live(
        self, worktree_id: str, resolver: AgentResolver,
    ) -> tuple[str, "_WorktreeEntry"] | None:
        """Single-flight live-existence probe (#6744): coalesces concurrent
        callers for the same ``worktree_id`` onto one in-flight task, same
        rationale as :meth:`probe_archived` but returning the full entry
        (needed for the worktree's on-disk path) and not restricted to
        archived records -- covers a fresh, uncached, still-active worktree
        too. Used as the fallback when the fleet-wide crawl cache doesn't
        (yet) know about this worktree, so the cache never gates existence.
        """
        task = self._live_probe_inflight.get(worktree_id)
        if task is None:
            task = asyncio.create_task(_probe_live_worktree(worktree_id, resolver))
            self._live_probe_inflight[worktree_id] = task

            def _evict(t: asyncio.Task, wid: str = worktree_id) -> None:
                if self._live_probe_inflight.get(wid) is t:
                    del self._live_probe_inflight[wid]

            task.add_done_callback(_evict)
        return await asyncio.shield(task)

    async def crawl_if_empty(self) -> None:
        """Trigger a crawl only if the cache has no data yet.

        Legacy cold requests wait for the fast, unclassified first pass.
        Both it and its classify backfill use the retained refresh owner;
        abandoning this wait cannot cancel a source's work.
        """
        if self._cache or not self._resolver:
            return
        self._sync_sources(self._resolver)
        task = self._ensure_warm()
        if task is None:
            return
        await asyncio.shield(task)

    async def _backfill_classify(self, resolver: AgentResolver) -> None:
        """Fire-and-forget classify backfill after the fast first paint.

        Best-effort: any failure just leaves the cache at its unclassified
        (no ``closure``) state until the next successful crawl.
        """
        try:
            if self._refresh.pending or self._refresh.closed:
                return
            await self.crawl(resolver)
        except Exception:
            log.exception("Worktree classify backfill failed")

    async def crawl(self, resolver: AgentResolver) -> None:
        """Join the retained discovery holder, shielded from caller cancellation."""
        self._sync_sources(resolver)
        task = self._refresh.ensure(
            lambda epoch: self._do_crawl(resolver, classify=True, epoch=epoch),
        )
        if task is not None:
            await asyncio.shield(task)

    async def _do_crawl(
        self, resolver: AgentResolver | None, *, epoch: int,
        classify: bool = True, fresh: bool = False,
    ) -> None:
        """Crawl all eligible agents concurrently (the actual work).

        Only the refresh owner starts this operation. Publication is atomic
        and fenced against source replacement or shutdown.
        """
        self._sync_sources(self._resolver if self._resolver is not None else resolver)
        if resolver is None or not self._refresh.current(epoch):
            return
        eligible = [
            (name, cfg) for name, cfg in resolver.agents.items()
            if cfg.project and cfg.worktree_discovery
        ]
        results = await asyncio.gather(
            *(self._crawl_agent(name, cfg, resolver, epoch=epoch, classify=classify, **({"fresh": True} if fresh else {}))
              for name, cfg in eligible),
            return_exceptions=True,
        )
        result = self._governance.recheck("pre-mutation:update-worktree-cache") if self._governance else None
        if result is not None and result.get("status") != "ready":
            raise RuntimeError(
                "worktree discovery governance changed before cache update: "
                f"{result.get('reason')}"
            )

        self._sync_sources(self._resolver if self._resolver is not None else resolver)
        if not self._refresh.current(epoch):
            return
        updated = dict(self._cache)
        errors: dict[str, str | None] = {}
        observations: dict[str, float] = {}
        for (name, _), result in zip(eligible, results):
            if isinstance(result, BaseException):
                log.warning("Worktree discovery failed for %s: %s", name, result)
                errors[name] = (
                    str(result) if isinstance(result, DiscoveryReadError) else "source-read-failed"
                )
            else:
                updated[name] = result
                errors[name] = None
                observed = getattr(result, "observed", None)
                if observed is not None:
                    observations[name] = observed
        self._cache = updated
        self._refresh.published(epoch, errors, observed=observations)

    async def _crawl_agent(
        self,
        agent_name: str,
        config: AgentConfig,
        resolver: AgentResolver,
        *,
        epoch: int | None = None,
        classify: bool = True,
        fresh: bool = False,
    ) -> list[_WorktreeEntry]:
        """List worktrees for a single agent via subprocess or SSH.

        ``classify=False`` skips ``--classify`` entirely (a single legacy
        call bounded by ``_CMD_TIMEOUT``) -- used for the fast, blocking
        first-paint crawl so a synchronous caller is never exposed to the
        longer classify budget.
        """
        project = config.project
        if epoch is None:
            epoch = self._refresh.epoch
        if not project:
            return []
        if fresh and agent_name in self._fresh_unsupported:
            raise DiscoveryReadError("source-refresh-unsupported")

        is_local = not config.host
        host: str | None = None
        user: str | None = None
        if not is_local:
            # SSH -- resolve through topology for correct alias/user
            try:
                target = resolver.resolve(agent_name)
            except (KeyError, ValueError) as exc:
                log.warning("Cannot resolve agent %s for discovery: %s", agent_name, exc)
                raise DiscoveryReadError("source-unavailable") from exc
            # If the resolved target is the local machine, run locally
            # instead of SSH (avoids loopback SSH failures)
            if _is_local_target(target.host, resolver, target.execution_space_key):
                is_local = True
            else:
                host = target.host or config.host
                user = target.user or config.ssh_user

        async def _run(args: list[str], *, timeout: float | None = None):
            if is_local:
                return await _run_local_ex(project, args, timeout=timeout)
            return await _run_ssh_ex(
                host=host, user=user, project=project, args=args,
                timeout=timeout,
            )

        legacy_args = ["list", "--json", "--mux-details", *(["--fresh"] if fresh else [])]
        if not classify or agent_name in self._classify_unsupported:
            raw, stderr = await _run(legacy_args)
            if raw is None:
                if fresh and is_option_unsupported(stderr, "--fresh"):
                    if self._refresh.current(epoch):
                        self._fresh_unsupported.add(agent_name)
                    raise DiscoveryReadError("source-refresh-unsupported")
                raise DiscoveryReadError("source-read-failed")
            observed = validate_inventory(raw, fresh=fresh)
            return ObservedRows(_parse_worktree_list(raw, agent_name), observed)

        # worktree-finality-and-obligations (Phase 5): --classify is included
        # so the crawl carries the canonical closure descriptor through to
        # the cockpit projection. Two safety nets so a slow/old target never
        # loses discovery entirely:
        #   1. a longer, classify-specific timeout budget (the extra ~5 git
        #      calls per worktree can exceed the base _CMD_TIMEOUT) -- a
        #      timeout here falls back to the legacy (unclassified) call
        #      rather than dropping the whole crawl to []. Only reachable
        #      from the periodic loop/backfill, never the blocking first
        #      paint (see ``classify=False`` above).
        #   2. an explicit "--classify unrecognized" stderr check -- an
        #      older agent-worktrees runtime falls back the same way. The
        #      verdict is cached per agent (``_classify_unsupported``) so a
        #      long-lived daemon probes a permanently-old runtime once.
        classify_args = [*legacy_args, "--classify"]

        raw, stderr = await _run(classify_args, timeout=_CLASSIFY_CMD_TIMEOUT)
        if raw is None:
            if fresh and is_option_unsupported(stderr, "--fresh"):
                if self._refresh.current(epoch):
                    self._fresh_unsupported.add(agent_name)
                raise DiscoveryReadError("source-refresh-unsupported")
            if stderr and _is_classify_unsupported(stderr):
                log.info(
                    "agent %s: --classify unsupported by remote agent-worktrees; "
                    "falling back to unclassified list",
                    agent_name,
                )
                if self._refresh.current(epoch):
                    self._classify_unsupported.add(agent_name)
            else:
                log.warning(
                    "agent %s: classified worktree list failed/timed out; "
                    "falling back to unclassified list", agent_name,
                )
            raw, stderr = await _run(legacy_args)

        if raw is None:
            if fresh and is_option_unsupported(stderr, "--fresh"):
                if self._refresh.current(epoch):
                    self._fresh_unsupported.add(agent_name)
                raise DiscoveryReadError("source-refresh-unsupported")
            raise DiscoveryReadError("source-read-failed")

        observed = validate_inventory(raw, fresh=fresh)
        return ObservedRows(_parse_worktree_list(raw, agent_name), observed)

    async def _loop(self, resolver: AgentResolver) -> None:
        first_pass = True
        while True:
            if not first_pass:
                await asyncio.sleep(self._interval)
            result = self._governance.recheck("iteration-boundary:worktree-discovery") if self._governance else None
            if result is not None and result.get("status") != "ready":
                log.warning(
                    "Periodic worktree discovery backing off at %s: %s (%s)",
                    result.get("checkpoint"),
                    result.get("reason"),
                    result.get("status"),
                )
                await asyncio.sleep(_GOVERNANCE_BACKOFF_SECONDS)
                continue
            try:
                await self.crawl(resolver)
                if first_pass:
                    log.info("Worktree discovery started -- %d agents", len(self._cache))
            except Exception:
                log.exception("Periodic worktree discovery failed")
            first_pass = False


# -- Subprocess helpers -------------------------------------------------------


async def _run_local(
    project: str, args: list[str] | None = None, *, timeout: float | None = None,
) -> str | None:
    """Run ``<project> <args>`` locally (defaults to ``list --json``)."""
    stdout, _stderr = await _run_local_ex(project, args, timeout=timeout)
    return stdout


async def _run_local_ex(
    project: str, args: list[str] | None = None, *, timeout: float | None = None,
) -> tuple[str | None, str]:
    """Like :func:`_run_local`, also returning stderr (empty on success) so
    a caller can distinguish a timeout/crash from a rejected flag."""
    binstub_str = _resolve_local_binstub(project)
    cmd = [binstub_str, *(args if args is not None else ["list", "--json"])]
    return await _exec_ex(cmd, timeout=timeout)


def _is_local_target(
    ssh_host: str | None, resolver: AgentResolver, execution_space_key: str | None = None,
) -> bool:
    """Use the resolver's selected identity for transport loopback."""
    return resolver.is_local_transport(ssh_host, execution_space_key=execution_space_key)


async def _run_ssh(
    host: str, user: str | None, project: str, args: list[str] | None = None,
    *, timeout: float | None = None, ssh_shell: str | None = None,
) -> str | None:
    """Run ``<project> <args>`` on a remote machine via SSH."""
    stdout, _stderr = await _run_ssh_ex(
        host=host, user=user, project=project, args=args, timeout=timeout,
        **({"ssh_shell": ssh_shell} if ssh_shell is not None else {}),
    )
    return stdout


async def _run_ssh_ex(
    host: str | None, user: str | None, project: str,
    args: list[str] | None = None, *, timeout: float | None = None, ssh_shell: str | None = None,
) -> tuple[str | None, str]:
    """Run the profile and return stdout/stderr.

    The legacy ``user`` keyword stays accepted for caller compatibility but
    never overrides the SSH profile's login user.
    """
    if not host:
        raise ValueError("SSH target requires a host (SSH alias)")
    from ..worktree_owner_operation import owner_ssh_command

    remote_cmd = owner_ssh_command(
        project, args if args is not None else ["list", "--json"], ssh_shell,
    )

    cmd = [
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-T",
        host,
        remote_cmd,
    ]
    return await _exec_ex(cmd, timeout=timeout)


async def _exec(cmd: list[str], *, timeout: float | None = None) -> str | None:
    """Execute a command and return stdout, or None on failure."""
    stdout, _stderr = await _exec_ex(cmd, timeout=timeout)
    return stdout


async def _exec_ex(
    cmd: list[str], *, timeout: float | None = None,
) -> tuple[str | None, str]:
    """Like :func:`_exec`, also returning stderr (empty string on success
    or when unavailable) so a caller can distinguish a timeout/crash from
    a rejected flag."""
    proc: asyncio.subprocess.Process | None = None
    eff_timeout = _CMD_TIMEOUT if timeout is None else timeout
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Run the child in its OWN session/process group so a timeout can
            # reap the WHOLE subtree, not just the direct child. The discovery
            # command (``<project> list --json --mux-details``) may shell out to
            # per-worktree mux probes; if it ever spins again (#4439), killing
            # only the top process would orphan those grandchildren and leave
            # them pinning cores. POSIX-only; a no-op on Windows.
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=eff_timeout,
        )
        stderr_text = stderr.decode(errors="replace")
        if proc.returncode != 0:
            log.error(
                "Command failed (rc=%d): %s\nstderr: %s",
                proc.returncode, " ".join(cmd),
                stderr_text[:500],
            )
            return None, stderr_text
        return stdout.decode(), stderr_text
    except TimeoutError:
        log.error("Command timed out after %.0fs: %s", eff_timeout, " ".join(cmd))
        _kill_process_tree(proc)
        if proc:
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                pass
        return None, ""
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            _kill_process_tree(proc)
            try:
                await proc.communicate()
            except Exception:
                pass
        raise
    except Exception:
        log.exception("Failed to run: %s", " ".join(cmd))
        return None, ""


def _kill_process_tree(proc: asyncio.subprocess.Process | None) -> None:
    """SIGKILL the child *and its process group* so no grandchild (e.g. a
    mux probe) orphans and keeps pinning a core (#4439). Falls back to
    the child alone where process groups aren't available. Never raises.
    """
    if proc is None or proc.returncode is not None:
        return
    pid = proc.pid
    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass  # group unavailable -- fall through to a direct child kill
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


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
        if not isinstance(w, dict):
            log.warning("Invalid worktree row from %s", agent_name)
            continue
        wt_id = w.get("id") or w.get("name", "")
        if not isinstance(wt_id, str) or not wt_id:
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
            # #2668: taxonomy marks (absent on older agent-worktrees -> None/shown).
            interface=w.get("interface"),
            origin=w.get("origin"),
            picker_hidden=bool(w.get("picker_hidden", False)),
            # #2956: status core -- disposition + derived live pulse.
            follow_up=bool(w.get("follow_up", False)),
            summary=w.get("summary"),
            status_note_at=w.get("status_note_at"),
            live_intent=w.get("live_intent"),
            live_intent_at=w.get("live_intent_at"),
            live_intent_idle=bool(w.get("live_intent_idle", False)),
            # Phase 5: raw/opaque passthrough, absent unless --classify ran.
            closure=w.get("closure") if isinstance(w.get("closure"), dict) else None,
            bound_agent=w.get("bound_agent") or None,
        ))
    return entries


# -- Singleton cache (initialized via app lifespan) ---------------------------

_discovery_cache = WorktreeDiscoveryCache()


def get_cache() -> WorktreeDiscoveryCache:
    return _discovery_cache


def cache_for_app(app: FastAPI) -> WorktreeDiscoveryCache:
    """Use the lifespan's retained cache, with legacy lookup for detached apps."""
    if hasattr(app.state, "worktree_discovery_cache"):
        cache = app.state.worktree_discovery_cache
        if not isinstance(cache, WorktreeDiscoveryCache):
            raise RuntimeError("Application worktree cache ownership is invalid")
        return cache
    return get_cache()


# -- Route handlers -----------------------------------------------------------


@router.get("/api/v1/worktrees")
async def list_worktrees(
    request: Request,
    render: bool = Query(False),
    refresh: bool = Query(False),
) -> dict[str, Any]:
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
    - ``durable_session_id``: the identifier to persist or deep-link with --
      ``acp_session_id`` when known, else the (non-durable) bridge
      ``session_id``
    - ``session_status``: that session's status (idle/running/stopped/...)
    - ``session_turn_count``: number of prompt turns on that session
    - ``session_live``: True if the session is currently running or idle
      with a live process (attached/active, not stopped or ended)

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

    ``render=true`` opts into prompt cache-only rendering with warming/stale
    evidence. Unversioned cold requests retain the blocking first crawl.
    ``refresh=true`` joins one owned refresh for at most ten wall seconds;
    expiry never releases or cancels that holder.
    """
    cache = cache_for_app(request.app)
    refresh_result = None
    if refresh:
        refresh_result = await cache.refresh_live()
    elif not render:
        await cache.crawl_if_empty()
    metadata = cache.render_snapshot() if render and not refresh else cache._refresh.metadata()
    groups = cache.get_all()

    # Build worktree_id -> latest bridge session map for linkage. A worktree
    # may have had several sessions over its life; list_sessions() is
    # already sorted newest-first, so the first match per worktree wins.
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
            public_status, at_rest, _liveness = session.public_state()
            status = public_status.value
            entry["session_id"] = session.session_id
            entry["acp_session_id"] = session.acp_session_id
            entry["durable_session_id"] = session.acp_session_id or session.session_id
            entry["session_status"] = status
            entry["session_at_rest"] = at_rest
            entry["session_turn_count"] = session.turn_count
            entry["session_live"] = status in ("running", "idle")
        else:
            entry["session_id"] = None
            entry["acp_session_id"] = None
            entry["durable_session_id"] = None
            entry["session_status"] = None
            entry["session_at_rest"] = False
            entry["session_turn_count"] = 0
            entry["session_live"] = False
        return entry

    return {
        "groups": {
            name: [_decorate(wt) for wt in worktrees]
            for name, worktrees in groups.items()
        },
        "discovery": metadata,
        **({"refresh": refresh_result} if refresh_result is not None else {}),
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
async def resume_worktree(
    worktree_id: str, request: Request, reclaim: bool = False, strict: bool = False
) -> SessionInfo:
    """Keep the original HTTP shape while always preserving the native head."""
    from ..worktree_native_resume import resume_native_worktree

    return await resume_native_worktree(worktree_id, request, "acp", reclaim=reclaim, strict=strict)


@router.post("/api/v1/worktrees/{worktree_id}/handoff", response_model=SessionInfo)
async def handoff_worktree(
    worktree_id: str, request: Request, reason: str | None = None, seed: bool = True
) -> SessionInfo:
    """Hand a worktree's current session off to a fresh successor in place.

    Worktree-level convenience verb: a UI consumer that only knows the
    worktree handle (no session id, no ``/new``/``/clear`` affordance) can
    request an in-place changeover. Resolves the worktree's current
    session and hands it off; the returned successor becomes the
    worktree's new current session.

    Errors: 404 (no session), 409 (single-checkout agent or mid-turn), 502
    (successor failed to spawn -- predecessor retained), 503 (draining).
    """
    from .sessions import _session_info

    mgr = getattr(request.app.state, "session_manager", None)
    session = _latest_session_for_worktree(mgr, worktree_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for worktree {worktree_id}",
        )
    try:
        successor = await mgr.handoff_session(
            session.session_id, reason=reason, seed=seed
        )
    except DaemonDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Session {session.session_id} not found"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _session_info(successor)


@router.post(
    "/api/v1/worktrees/{worktree_id}/handoff-request",
    response_model=SessionInfo,
)
async def handoff_worktree_request(
    worktree_id: str,
    handoff: WorktreeHandoffRequest,
    request: Request,
) -> SessionInfo:
    """Perform an externally-seeded in-place handoff for a worktree session.

    This is the control-plane seam used by ``context-handoff``'s best-effort
    `agent-bridge handoff-request` ping from some OTHER extension-enabled
    session. The caller already composed the durable baton, so it supplies
    the exact successor opening turn (``seed_text``) plus the session it
    believes currently owns the worktree. Resolves the worktree's current
    bridge session, verifies it still matches, and reuses
    ``SessionManager.handoff_session`` with the external seed.

    Additive only: agent-bridge's own ACP-hosted sessions don't rely on it,
    remaining fully self-contained under ``SessionManager``'s internal
    context-pressure + self-authored-brief flow.

    Errors: 404 (no current session, or requested session no longer
    matches), 409 (single-checkout agent or mid-turn), 502 (successor
    failed to spawn), 503 (draining).
    """
    from .sessions import _session_info

    mgr = getattr(request.app.state, "session_manager", None)
    session = _latest_session_for_worktree(mgr, worktree_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for worktree {worktree_id}",
        )
    requested = mgr.get_session(handoff.session_id) if mgr is not None else None
    if (
        requested is None
        or getattr(requested.target, "worktree_id", None) != worktree_id
        or requested.session_id != session.session_id
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                f"No current session found for worktree {worktree_id} "
                f"matching {handoff.session_id}"
            ),
        )
    try:
        successor = await mgr.handoff_session(
            session.session_id,
            reason="context-handoff-request",
            seed_text=handoff.seed_text,
            handoff_token=handoff.handoff_token,
        )
    except DaemonDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session.session_id} not found",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _session_info(successor)


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

    if _is_local_target(target.host, resolver, target.execution_space_key):
        return await _run_local(config.project, args)

    return await _run_ssh(
        host=target.host or config.host,
        user=target.user or config.ssh_user,
        project=config.project,
        args=args,
        **({"ssh_shell": target.ssh_shell} if target.ssh_shell in {"pwsh", "powershell", "cmd"} else {}),
    )


@router.get("/api/v1/worktrees/{worktree_id}/sessions")
async def list_worktree_sessions(
    worktree_id: str, request: Request,
) -> dict[str, Any]:
    """List the CLI sessions belonging to a worktree.

    Shells to ``<project> list-sessions --worktree <id> --json`` on the
    owning machine -- the authoritative, branch-independent session
    registry maintained by agent-worktrees (counts sessions launched by
    the picker *and* by agent-bridge / Mission Control).

    For the worktree's full head-succession/fork-lineage graph, see
    ``GET /api/v1/worktrees/{id}/lineage`` instead.
    """
    cache = cache_for_app(request.app)
    await cache.crawl_if_empty()

    owner = await _owning_agent(worktree_id, request)
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
    # session-lifecycle: forward the ground-layer's asserted head so a consumer
    # (Neuron Forge) resolves the current session head-first (derive-dont-
    # duplicate -- the bridge keeps no head of its own).
    head_session = data.get("head_session") if isinstance(data, dict) else None
    return {
        "worktree_id": worktree_id,
        "agent_name": agent_name,
        "head_session": head_session if isinstance(head_session, str) else None,
        "sessions": sessions if isinstance(sessions, list) else [],
    }


@router.get("/api/v1/worktrees/{worktree_id}/lineage")
async def get_worktree_lineage(
    worktree_id: str, request: Request,
) -> dict[str, Any]:
    """Return a worktree's authoritative, bounded session/handoff/controller
    lineage graph -- the current head, every session with its
    predecessor/successor, the head-transition history, and the handoff
    ledger (each entry carries its own ``state``, so a caller can tell a
    genuine **fork** from ordinary resolved/cancelled history). Shells out
    to ``<project> worktree-lineage --worktree <id> --json`` on the
    machine that owns the worktree.
    """
    cache = cache_for_app(request.app)
    await cache.crawl_if_empty()

    owner = await _owning_agent(worktree_id, request)
    if owner is None:
        raise HTTPException(
            status_code=404,
            detail=f"Worktree {worktree_id} not found on any agent",
        )
    agent_name, config = owner
    resolver = request.app.state.resolver

    raw = await _run_for_agent(
        agent_name, config, resolver,
        ["worktree-lineage", "--worktree", worktree_id, "--json"],
    )
    if raw is None:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to read lineage for worktree {worktree_id}",
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Invalid lineage JSON for worktree {worktree_id}",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail=f"Invalid lineage JSON for worktree {worktree_id}",
        )
    return {"agent_name": agent_name, **data}


@router.get("/api/v1/worktrees/{worktree_id}/sessions/{session_id}/transcript")
async def get_worktree_session_transcript(
    worktree_id: str, session_id: str, request: Request,
) -> dict[str, Any]:
    """Return the rendered transcript for a session in a worktree.

    Shells out to ``<project> session-transcript <session_id> --json`` on
    the machine that owns the worktree, so Neuron Forge (or any consumer)
    can view a CLI transcript for *any* session in *any* worktree.

    Falls through to a registered cold-store provider when there is no
    live owning agent, or the owning agent's local session-state has
    nothing for this session (mirrors ``get_session``'s bare-lookup
    fallback).
    """
    cache = cache_for_app(request.app)
    await cache.crawl_if_empty()

    owner = await _owning_agent(worktree_id, request)
    agent_name: str | None = None
    events: list[Any] = []
    meta: dict[str, Any] | None = None

    if owner is not None:
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
        events = data.get("events", []) if isinstance(data, dict) else []
        meta = data.get("meta") if isinstance(data, dict) else None

    if not events:
        mgr: SessionManager = request.app.state.session_manager
        cold = await mgr.fetch_cold_store_session(session_id)
        # ``cold is not None`` is already the provider's validated identity
        # hit -- a legitimate archive can have zero events, so gate on
        # ``worktree_id`` instead of event truthiness: this route is
        # worktree-scoped but cold-store is keyed by session_id alone, so a
        # provider answer for a *different* worktree_id must not be
        # accepted as this worktree's transcript.
        if cold is not None and (
            cold.worktree_id is None or cold.worktree_id == worktree_id
        ):
            events = list(cold.events)
            meta = {
                "read_only": True,
                "at_rest": True,
                "worktree_id": cold.worktree_id,
            }
        elif owner is None:
            raise HTTPException(
                status_code=404,
                detail=f"Worktree {worktree_id} not found on any agent",
            )

    return {
        "worktree_id": worktree_id,
        "agent_name": agent_name,
        "session_id": session_id,
        "events": events,
        "meta": meta,
    }


@router.post("/api/v1/worktrees/{worktree_id}/restart")
async def restart_worktree_copilot(
    worktree_id: str, request: Request, force: bool = False,
    expected_holder: str | None = None,
) -> dict[str, Any]:
    """Restart a worktree's interactive (mux-launched) Copilot in place.
    Shells to ``<project> restart <id> --json`` on the owning machine:
    graceful double Ctrl-C then a hard mux ``kill-session`` fallback,
    keeping the worktree **on disk** for a later relaunch/ACP-resume.
    ``force=true`` skips the graceful quit. ``expected_holder``, when given,
    fences the invalidate-on-take-over below to that live-session id (#2906
    race hardening; see ``db.expire_live_sessions_for_worktree``). Returns
    ``{worktree_id, had_session, method, ok}`` (``method``: none | graceful |
    hard | failed).
    """
    cache = cache_for_app(request.app)
    await cache.crawl_if_empty()

    owner = await _owning_agent(worktree_id, request)
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

    method = data.get("method", "unknown")
    ok = bool(data.get("ok", False))
    had_session = bool(data.get("had_session", False))

    # Invalidate-on-take-over (#2906): demote the live registration (fenced
    # to expected_holder), only when had_session -- a no-op is never proof
    # a bare claimant was terminated.
    if ok and had_session:
        db = getattr(request.app.state, "db", None)
        if db is not None:
            try:
                n = db.expire_live_sessions_for_worktree(
                    worktree_id, now=time.time(), expected_session_id=expected_holder
                )
                if n:
                    log.info(
                        "restart_worktree %s: expired %d live registration(s)",
                        worktree_id, n,
                    )
            except Exception:
                # CLI stopped, but its registration may still be 'live' --
                # report failure so a caller never forces past that row.
                log.warning("restart_worktree %s: invalidation failed", worktree_id, exc_info=True)
                ok = False

    return {
        "worktree_id": data.get("worktree_id", worktree_id),
        "agent_name": agent_name,
        "had_session": had_session,
        "method": method,
        "ok": ok,
    }
