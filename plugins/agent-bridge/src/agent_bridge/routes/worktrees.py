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
import shlex
import signal
import time
from dataclasses import dataclass, replace
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agent_registry import AgentConfig, AgentResolver
from ..loop_governance import LoopGovernance
from ..models import SessionInfo, SessionStatus, WorktreeHandoffRequest
from ..session_manager import (
    DaemonDrainingError,
    ProviderTargetRefreshError,
    SessionManager,
)
from .worktree_holders import (
    chosen_holder_id as _chosen_holder_id,
    reservation_conflict_detail as _reservation_conflict_detail,
)
from .worktree_probe import (
    owning_agent as _owning_agent,
    probe_archived_owner as _probe_archived_owner,
    probe_live_worktree as _probe_live_worktree,
    reassign_worktree_ownership as _reassign_worktree_ownership,
    resolve_already_live as _resolve_already_live,
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
    # Interactive-mux (wt-<id> tmux/psmux) liveness on the owning machine --
    # the *second ownership* NF must see (#1883), distinct from a bridge
    # ACP session.
    mux_session: bool = False
    mux_clients: int | None = None
    mux_attached: bool | None = None
    # #2668 two-axis taxonomy, surfaced from ``agent-worktrees list --json``.
    # ``interface`` is cli|acp, ``origin`` is user|system|delegate,
    # ``picker_hidden`` is the Picker's own visibility verdict. All default
    # to "unknown/shown" for an older agent-worktrees runtime.
    interface: str | None = None
    origin: str | None = None
    picker_hidden: bool = False
    # worktree-status-core (#2917/#2956): the agent-asserted disposition
    # (follow_up + summary) and the derived live-intent pulse. ``summary``
    # is also what ``agent-dispatch focus`` writes (derive-don't-
    # duplicate). Freshness is computed at render time, not cached.
    follow_up: bool = False
    summary: str | None = None
    status_note_at: str | None = None
    live_intent: str | None = None
    live_intent_at: str | None = None
    live_intent_idle: bool = False
    # worktree-finality-and-obligations (Phase 5): the canonical closure
    # descriptor, surfaced raw/opaque from ``agent-worktrees list --json
    # --classify``. Only the consumer that knows the current
    # ``DESCRIPTOR_VERSION`` may treat it as authoritative. Absent on an
    # older runtime or when git classification wasn't run.
    closure: dict[str, Any] | None = None
    # agent-bridge-worktree-native-agents: the charter (spawn profile name)
    # bound to this worktree at create/embody time, surfaced from
    # ``agent-worktrees list --json`` as ``bound_agent``. None = unbound
    # (the venue's default agent drives, today's behavior).
    bound_agent: str | None = None

    def interactive_cli_state(self) -> str:
        """Classify interactive-CLI ownership from mux liveness.

        - ``held``    -- a mux session exists, terminal attached (or
          unknown): a live interactive CLI owns it. Do-not-disturb.
        - ``at-rest`` -- a mux session exists but detached: still a live
          process -- reclaim via take-over.
        - ``none``    -- no interactive mux session holds the worktree.
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
            "interface": self.interface,
            "origin": self.origin,
            "picker_hidden": self.picker_hidden,
            # worktree-status-core (#2956): disposition + derived live pulse.
            "follow_up": self.follow_up,
            "summary": self.summary,
            "status_note_at": self.status_note_at,
            "live_intent": self.live_intent,
            "live_intent_at": self.live_intent_at,
            "live_intent_idle": self.live_intent_idle,
            "closure": self.closure,
            "bound_agent": self.bound_agent,
        }


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
        self._crawl_lock = asyncio.Lock()
        self._governance: LoopGovernance | None = None
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
        self._resolver = resolver
        self._governance = governance
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
        # Same shutdown discipline for classify backfills
        # (#discussion_r4004943069) and in-flight archived-owner / live
        # probes: cancel/await so none outlives the rest of application
        # shutdown.
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

        Uses the crawl lock to prevent concurrent callers from stampeding
        multiple crawls simultaneously.

        worktree-finality-and-obligations (Phase 5): this FIRST, blocking
        crawl never attempts ``--classify`` -- it mirrors the Picker's own
        fast-then-classify two-phase pattern so a slow/old target never
        turns "no cache yet" into an ~90s stall for whichever request
        arrives first. A classify pass is kicked off as a fire-and-forget
        background backfill immediately after, tracked in
        ``_backfill_tasks`` so :meth:`stop` can cancel/await it.
        """
        if self._cache or not self._resolver:
            return
        async with self._crawl_lock:
            if self._cache:
                return
            await self._do_crawl(self._resolver, classify=False)
        task = asyncio.create_task(
            self._backfill_classify(self._resolver), name="worktree-classify-backfill",
        )
        self._backfill_tasks.add(task)
        task.add_done_callback(self._backfill_tasks.discard)

    async def _backfill_classify(self, resolver: AgentResolver) -> None:
        """Fire-and-forget classify backfill after the fast first paint.

        Best-effort: any failure just leaves the cache at its unclassified
        (no ``closure``) state until the next successful crawl.
        """
        try:
            if self._crawl_lock.locked():
                return
            async with self._crawl_lock:
                await self._do_crawl(resolver, classify=True)
        except Exception:
            log.exception("Worktree classify backfill failed")

    async def crawl(self, resolver: AgentResolver) -> None:
        """Crawl all eligible agents -- **single-flight**.

        Only one crawl runs at a time; an overlapping caller (a slow
        periodic tick, an on-demand request) coalesces onto the running
        crawl instead of spawning a second, duplicate set of
        ``agent-worktrees list`` subprocesses (the owner-tethered spawn
        discipline -- see the ``process-slot-ownership`` effort). Each
        individual ``list`` is still bounded by ``_CMD_TIMEOUT``; this
        bounds their **concurrency** to one.
        """
        if self._crawl_lock.locked():
            # A crawl is in flight -- wait for it and reuse its result
            # rather than starting an overlapping one.
            async with self._crawl_lock:
                return
        async with self._crawl_lock:
            await self._do_crawl(resolver, classify=True)

    async def _do_crawl(self, resolver: AgentResolver, *, classify: bool = True) -> None:
        """Crawl all eligible agents concurrently (the actual work).

        Callers hold ``_crawl_lock`` around this, so at most one crawl's
        worth of ``list`` subprocesses is ever in flight. ``classify=False``
        is the fast, bounded first-paint pass (:meth:`crawl_if_empty`);
        ``classify=True`` is the periodic loop and the background backfill.
        """
        eligible = [
            (name, cfg) for name, cfg in resolver.agents.items()
            if cfg.project and cfg.worktree_discovery
        ]
        if not eligible:
            return

        results = await asyncio.gather(
            *(self._crawl_agent(name, cfg, resolver, classify=classify)
              for name, cfg in eligible),
            return_exceptions=True,
        )
        result = self._governance.recheck("pre-mutation:update-worktree-cache") if self._governance else None
        if result is not None and result.get("status") != "ready":
            raise RuntimeError(
                "worktree discovery governance changed before cache update: "
                f"{result.get('reason')}"
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
        *,
        classify: bool = True,
    ) -> list[_WorktreeEntry]:
        """List worktrees for a single agent via subprocess or SSH.

        ``classify=False`` skips ``--classify`` entirely (a single legacy
        call bounded by ``_CMD_TIMEOUT``) -- used for the fast, blocking
        first-paint crawl so a synchronous caller is never exposed to the
        longer classify budget.
        """
        if not config.project:
            return []

        is_local = not config.host
        host: str | None = None
        user: str | None = None
        if not is_local:
            # SSH -- resolve through topology for correct alias/user
            try:
                target = resolver.resolve(agent_name)
            except (KeyError, ValueError) as exc:
                log.warning("Cannot resolve agent %s for discovery: %s", agent_name, exc)
                return []
            # If the resolved target is the local machine, run locally
            # instead of SSH (avoids loopback SSH failures)
            if _is_local_target(target.host, resolver):
                is_local = True
            else:
                host = target.host or config.host
                user = target.user or config.ssh_user

        async def _run(args: list[str], *, timeout: float | None = None):
            if is_local:
                return await _run_local_ex(config.project, args, timeout=timeout)
            return await _run_ssh_ex(
                host=host, user=user, project=config.project, args=args,
                timeout=timeout,
            )

        legacy_args = ["list", "--json", "--mux-details"]
        if not classify or agent_name in self._classify_unsupported:
            raw, _stderr = await _run(legacy_args)
            if raw is None:
                return []
            return _parse_worktree_list(raw, agent_name)

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
        classify_args = ["list", "--json", "--mux-details", "--classify"]

        raw, stderr = await _run(classify_args, timeout=_CLASSIFY_CMD_TIMEOUT)
        if raw is None:
            if stderr and _is_classify_unsupported(stderr):
                log.info(
                    "agent %s: --classify unsupported by remote agent-worktrees; "
                    "falling back to unclassified list (cached for future crawls)",
                    agent_name,
                )
                self._classify_unsupported.add(agent_name)
            else:
                log.warning(
                    "agent %s: classified worktree list failed/timed out; "
                    "falling back to unclassified list", agent_name,
                )
            raw, _stderr = await _run(legacy_args)

        if raw is None:
            return []

        return _parse_worktree_list(raw, agent_name)

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


def _resolve_local_binstub(project: str) -> str:
    """Resolve *project*'s binstub to a directly-executable path.

    ``asyncio.create_subprocess_exec`` never consults Windows' ``PATHEXT``
    the way a shell does, so an extensionless name can't resolve to the
    installed ``.cmd``/``.ps1`` shim (``FileNotFoundError: [WinError 2]``).
    ``shutil.which`` does the same PATHEXT-aware lookup on every platform.
    """
    import shutil
    from pathlib import Path

    explicit = Path.home() / ".local" / "bin" / project
    return shutil.which(str(explicit)) or shutil.which(project) or project


async def _run_local_ex(
    project: str, args: list[str] | None = None, *, timeout: float | None = None,
) -> tuple[str | None, str]:
    """Like :func:`_run_local`, also returning stderr (empty on success) so
    a caller can distinguish a timeout/crash from a rejected flag."""
    binstub_str = _resolve_local_binstub(project)
    cmd = [binstub_str, *(args if args is not None else ["list", "--json"])]
    return await _exec_ex(cmd, timeout=timeout)


def _is_local_target(ssh_host: str | None, resolver: AgentResolver) -> bool:
    """Check if an SSH host alias resolves to the local machine AND platform.

    True only when the alias points to the same machine key AND platform
    (wsl/windows/linux) -- avoids treating a Windows agent as "local" on
    WSL (or vice versa), even on the same physical machine.
    """
    if not ssh_host:
        return True

    import socket
    hostname = socket.gethostname().lower()
    host_lower = ssh_host.lower()

    from ..agent_registry import _detect_local_machine
    machine, platform = _detect_local_machine(resolver.machines)
    if not machine:
        # Can't identify our own machine -- only match exact hostname.
        return host_lower == hostname

    # Match the SSH alias against the local machine's environments, but
    # only the environment matching our platform.
    for env in machine.ssh_environments:
        if env.alias and env.alias.lower() == host_lower:
            return env.name == platform

    if host_lower == hostname or host_lower == machine.key.lower():
        # Ambiguous -- only treat as local if exactly one environment
        # matches our platform.
        matching = [e for e in machine.ssh_environments if e.name == platform]
        return len(matching) == 1

    return False


async def _run_ssh(
    host: str, user: str | None, project: str, args: list[str] | None = None,
    *, timeout: float | None = None,
) -> str | None:
    """Run ``<project> <args>`` on a remote machine via SSH."""
    stdout, _stderr = await _run_ssh_ex(
        host=host, user=user, project=project, args=args, timeout=timeout,
    )
    return stdout


async def _run_ssh_ex(
    host: str | None, user: str | None, project: str,
    args: list[str] | None = None, *, timeout: float | None = None,
) -> tuple[str | None, str]:
    """Like :func:`_run_ssh`, also returning stderr (empty on success)."""
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
    return await _exec_ex(cmd, timeout=timeout)


def _is_classify_unsupported(stderr: str) -> bool:
    """True if *stderr* is agent-worktrees rejecting an unrecognized
    ``--classify`` flag (an older agent-worktrees runtime). Mirrors
    ``picker_tui.data_ssh._is_classify_unsupported``."""
    s = stderr or ""
    return "unrecognized arguments" in s and "--classify" in s


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

    First request triggers an on-demand crawl when periodic discovery is
    disabled (subsequent requests return cached results).
    """
    cache = get_cache()
    await cache.crawl_if_empty()
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
    }

async def _find_cached_worktree_entry(
    worktree_id: str, resolver: Any,
) -> tuple[str, "_WorktreeEntry"] | tuple[None, None]:
    """Resolve a worktree id to its owning agent name + cache entry.

    Shared by both the fresh-spawn path and the resume-failed-restart path
    so charter resolution (:func:`_apply_bound_charter`) sees the same
    ``bound_agent`` either way. Falls back to a targeted live probe when
    the fleet-wide crawl cache hasn't seen this worktree yet (#6744) --
    the cache is a performance shortcut, never the authority.
    """
    cache = get_cache()
    await cache.crawl_if_empty()
    for agent_name, worktrees in cache.get_all().items():
        match = next((wt for wt in worktrees if wt.id == worktree_id), None)
        if match is not None:
            return agent_name, match
    probed = await cache.probe_live(worktree_id, resolver)
    if probed is not None:
        return probed
    return None, None


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
    """
    if not entry.bound_agent:
        return target
    canonical = resolver.canonical_agent_name(entry.bound_agent)
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


def _latest_session_for_worktree(mgr: Any, worktree_id: str) -> Any:
    """Return the most-recently-updated bridge session for a worktree, or None."""
    if mgr is None:
        return None
    for session in mgr.list_sessions():  # sorted newest-first
        if getattr(session.target, "worktree_id", None) == worktree_id:
            return session
    return None


async def _start_fresh_worktree_session(
    worktree_id: str, request: Request, mgr: Any, reclaim: bool
) -> Any:
    """Start a *fresh* owned ACP session in an existing worktree that has no
    prior bridge session (#1683), or None if it can't be resolved/started.

    Resolves the worktree's owning agent + on-disk path from the discovery
    cache and spawns a target scoped to that worktree directory -- keeps
    "take over a worktree whose interactive Copilot never persisted a
    session" working instead of 404-ing after the mux Copilot was killed.

    Serialized on a per-worktree lock (local/SSH targets aren't
    hard-guarded by the SessionManager) so two concurrent resumes can't
    each spawn a second owned controller. Returns the new (or raced-in)
    ``Session``, or None when unresolvable (caller then 404s). Raises 409
    on a raced-in live CLI and 502/503 on spawn failure.
    """
    if mgr is None:
        return None
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is None:
        return None

    owner_agent, entry = await _find_cached_worktree_entry(worktree_id, resolver)
    if entry is None or owner_agent is None:
        return None
    if not entry.path:
        # Defense in depth alongside the live probe's own exclusion of
        # reaped/tombstoned records (review #3121): even a cache-sourced
        # entry must carry a usable on-disk path before we spawn a fresh
        # session into it.
        log.warning(
            "resume_worktree %s: resolved entry has no on-disk path; "
            "refusing fresh-session fallback", worktree_id,
        )
        return None

    lock = _fresh_start_locks.setdefault(worktree_id, asyncio.Lock())
    async with lock:
        # Re-check under the lock (we awaited on the crawl above): a concurrent
        # resume may have already created the session, or a live interactive CLI
        # may have registered -- either way, do not spawn a second controller.
        raced = _latest_session_for_worktree(mgr, worktree_id)
        if raced is not None:
            # A concurrent fresh-start that connect-failed leaves a FAILED
            # session registered -- surface that as a 502 too, not a healthy 200.
            if getattr(raced, "status", None) == SessionStatus.FAILED:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Fresh session for worktree {worktree_id} failed to "
                        "start (connect/spawn error)"
                    ),
                )
            return raced
        if not reclaim:
            db = getattr(request.app.state, "db", None)
            if db is not None:
                holders = db.list_fresh_live_sessions(worktree_id, now=time.time())
                if holders:
                    # A live CLI registered during discovery -- return the same
                    # structured 409 the top-of-resume guard uses (consumers map
                    # it to "represent read-only"), not a misleading 404.
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "reason": "live_cli_holds_worktree",
                            "worktree_id": worktree_id,
                            "session_id": _chosen_holder_id(holders),
                        },
                    )

        try:
            target = resolver.resolve(owner_agent)
        except (KeyError, ValueError) as exc:
            log.warning(
                "resume_worktree %s: cannot resolve owning agent %s for a fresh "
                "session: %s", worktree_id, owner_agent, exc,
            )
            return None
        target = _apply_bound_charter(target, resolver, entry, worktree_id)

        # Scope the agent's spawn target to this worktree's directory + id (the
        # same augmentation a session roll / new-owned-chat applies), so the
        # fresh ACP session lands in the right worktree and is discoverable by
        # worktree id.
        target = replace(target, worktree_id=worktree_id, cwd=entry.path)
        try:
            fresh = await mgr.start_session(
                target, agent_name=owner_agent, caller_id=worktree_id,
            )
        except DaemonDrainingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            log.warning(
                "resume_worktree %s: fresh session start failed: %s",
                worktree_id, exc,
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Could not start a fresh session for worktree "
                    f"{worktree_id}: {exc}"
                ),
            ) from exc
        # start_session catches spawn/connection failures and returns a FAILED
        # session rather than raising -- surface that as a 502, not a healthy 200.
        if getattr(fresh, "status", None) == SessionStatus.FAILED:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Fresh session for worktree {worktree_id} failed to start "
                    "(connect/spawn error)"
                ),
            )
        # Claim the per-worktree ownership reservation for the fresh owned
        # session (#2912). The pre-spawn live-holder recheck above doesn't
        # cover mgr.start_session's own (slow, awaited) window, so this
        # result must be checked, not assumed.
        db = getattr(request.app.state, "db", None)
        if db is not None and not db.reserve_worktree_ownership(
            worktree_id, fresh.session_id, now=time.time(), reclaim=reclaim
        ):
            try:  # 'fresh' already started -- best-effort cleanup, don't leak it.
                await mgr.end_session(fresh.session_id, force=True)
            except Exception:
                log.warning("resume_worktree %s: fresh cleanup failed", worktree_id, exc_info=True)
            raise HTTPException(
                status_code=409, detail=_reservation_conflict_detail(db, worktree_id)
            )
        return fresh


@router.post("/api/v1/worktrees/{worktree_id}/resume", response_model=SessionInfo)
async def resume_worktree(
    worktree_id: str, request: Request, reclaim: bool = False
) -> SessionInfo:
    """Resume a worktree by ensuring it has a live session.

    Worktree-level convenience verb: finds the current (most recent) session
    for the worktree and ensures it is live.

    - An already-live session is returned as-is.
    - A stopped session is resumed (ACP load_session reuses the same acp
      session id).
    - If that session can no longer be resumed (e.g. old/finalized
      worktrees), fall back to starting a fresh session in the same
      worktree directory, since it still exists on disk.

    **Atomic ownership guard (#2879).** If a *fresh* live interactive
    Copilot CLI already holds this worktree, refuse to resume it as an
    OWNED ACP session and return **409** (``reason: live_cli_holds_
    worktree``) -- owning it would spawn a second ``copilot`` child on the
    same worktree and the two would contend. This is race-free, unlike a
    consumer's best-effort represent-if-live preflight. ``reclaim=true``
    bypasses the guard: the caller has just terminated the interactive CLI.

    Returns 404 if the worktree has no session at all.
    """
    from .sessions import _session_info

    db = getattr(request.app.state, "db", None)
    if not reclaim and db is not None:
        holders = db.list_fresh_live_sessions(worktree_id, now=time.time())
        if holders:
            chosen_id = _chosen_holder_id(holders)
            log.info(
                "resume_worktree %s refused: fresh live CLI %s holds it "
                "(represent read-only)",
                worktree_id, chosen_id,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "live_cli_holds_worktree",
                    "worktree_id": worktree_id,
                    "session_id": chosen_id,
                },
            )

    mgr = getattr(request.app.state, "session_manager", None)
    session = _latest_session_for_worktree(mgr, worktree_id)
    if session is None:
        # No bridge session has ever existed for this worktree (e.g. a bare
        # interactive Copilot that took no turn, or a just-taken-over worktree
        # whose interactive CLI never persisted an ACP session). The worktree
        # still exists on disk, so -- rather than 404 and leave a taken-over
        # worktree unusable (#1683) -- start a *fresh* owned session in it.
        fresh = await _start_fresh_worktree_session(
            worktree_id, request, mgr, reclaim
        )
        if fresh is not None:
            return _session_info(fresh)
        raise HTTPException(
            status_code=404,
            detail=f"No session found for worktree {worktree_id}",
        )

    # Ownership reservation (#2912): take the per-worktree ACP-ownership
    # reservation before resuming, so a live-CLI registration must respect
    # it. Atomic across processes; ``reclaim`` force-takes it. A False
    # result means either a fresh live CLI raced it, or another active ACP
    # reservation owns it -- see worktree_holders.reservation_conflict_detail.
    if db is not None:
        reserved = db.reserve_worktree_ownership(
            worktree_id, session.session_id, now=time.time(), reclaim=reclaim
        )
        if not reserved:
            detail = _reservation_conflict_detail(db, worktree_id)
            log.info(
                "resume_worktree %s refused: %s (session %s)",
                worktree_id, detail["reason"], detail["session_id"],
            )
            raise HTTPException(status_code=409, detail=detail)

    if await _resolve_already_live(mgr, worktree_id, session):  # live-checked, #6744
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
    except ProviderTargetRefreshError as exc:
        raise HTTPException(
            status_code=502,
            detail=ProviderTargetRefreshError.public_message,
        ) from exc
    except Exception as exc:
        # Resume failed (e.g. ACP session gone). Fall back to a fresh session
        # in the same worktree so the worktree remains usable.
        log.warning(
            "resume_worktree %s: resume of %s failed (%s); starting fresh session",
            worktree_id, session.session_id, exc,
        )
        restart_target = session.target
        resolver = getattr(request.app.state, "resolver", None)
        if resolver is not None:
            owner_agent, cached_entry = await _find_cached_worktree_entry(
                worktree_id, resolver,
            )
            if cached_entry is not None and owner_agent is not None:
                # Re-resolve a CLEAN venue target rather than layering onto
                # ``session.target`` -- a session created through the fresh-
                # spawn path already carries the charter's copilot_args, so
                # re-applying the charter on top would append them a second
                # time on every resume-failed restart (review #3163).
                try:
                    restart_target = replace(
                        resolver.resolve(owner_agent),
                        worktree_id=worktree_id, cwd=cached_entry.path,
                    )
                except (KeyError, ValueError):
                    restart_target = session.target
                restart_target = _apply_bound_charter(
                    restart_target, resolver, cached_entry, worktree_id,
                )
        try:
            fresh = await mgr.start_session(
                restart_target,
                agent_name=session.agent_name,
                caller_id=session.caller_id,
            )
        except Exception as start_exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not resume or restart worktree {worktree_id}: {start_exc}",
            ) from start_exc
        _reassign_worktree_ownership(db, worktree_id, fresh.session_id)  # #3142
        return _session_info(fresh)


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

    if _is_local_target(target.host, resolver):
        return await _run_local(config.project, args)

    return await _run_ssh(
        host=target.host or config.host,
        user=target.user or config.ssh_user,
        project=config.project,
        args=args,
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
    cache = get_cache()
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
    cache = get_cache()
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
    cache = get_cache()
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
    cache = get_cache()
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
