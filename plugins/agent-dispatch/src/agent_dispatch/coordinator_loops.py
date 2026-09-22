"""Coordinator background-loop helpers and cutover state."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Condition
from typing import Any

from pydantic import BaseModel

from .config import DEFAULT_HANDOFF_FALLBACK_GRACE, DEFAULT_ORPHAN_GRACE
from .events import EventBus
from .identity import name_for_repo
from .loop_governance import LoopGovernance
from .procutil import run_agent_worktrees_capture
from .queue import Status, Task, TaskQueue, machine_matches
from .worktree_status_relay import WorktreeStatusRelayStore

log = logging.getLogger("agent-dispatch.coordinator")
_GOVERNANCE_BACKOFF_SECONDS = 10.0

# Generation self-retire tuning. Default-ON (opt-out): validated on real cutovers
# (it arms for cutover-promoted coordinators and self-retires a demoted generation
# without dropping an in-flight claim), so it is now the default. Set
# ``AGENT_DISPATCH_SELF_RETIRE=0`` (or false/no/off) to disable it. Cadence and the
# K-confirmation count are env-tunable.
_SELF_RETIRE_DEFAULT_POLL_S = 30.0
_SELF_RETIRE_DEFAULT_CONFIRMATIONS = 3
_SELF_RETIRE_FORCE_EXIT_DEFAULT_S = 20.0

# Abandoned-passive reap (#5195): the periodic backstop for a passive
# `spawn_passive` stood up for a cutover whose orchestrator process died
# before that passive was ever promoted. Self-retire (above) only ever arms
# for a coordinator that has observed its OWN pid as `active`, so a
# never-promoted passive never triggers it and would otherwise linger
# indefinitely. Only the genuinely active coordinator runs this sweep (see
# the loop's arming phase, shared with self-retire), and it only acts on a
# breadcrumb aged past `grace_seconds` so a cutover that is still genuinely
# in flight is never disturbed. Default-ON (opt-out) like self-retire: it is
# a pure reconciliation of a durable fact (the breadcrumb) against the live
# routing table, and never touches anything but a stray of its own service.
_ABANDONED_PASSIVE_REAP_DEFAULT_POLL_S = 120.0
_ABANDONED_PASSIVE_REAP_DEFAULT_GRACE_S = 600.0

# Live self-update: a coordinator that notices its own running version has
# been superseded by a newer, fully-installed one (the ``current-version``
# marker -- see ``self_update.py``) spawns a self-triggered ``deploy`` and
# lets the existing self-retire loop above own its own graceful exit once
# that spawned deploy flips the routing table to the new generation.
#
# Opt-in (default-OFF), unlike self-retire: self-retire only ever *reacts* to
# a cutover someone else already committed, so it is safe to arm everywhere.
# This loop *initiates* one on its own, so a first release stays opt-in
# (``AGENT_DISPATCH_SELF_UPDATE=1``) until it has soaked in the field.
_SELF_UPDATE_DEFAULT_POLL_S = 60.0
_SELF_UPDATE_DEFAULT_CONFIRMATIONS = 3
_SELF_UPDATE_DEFAULT_COOLDOWN_S = 900.0


def _self_retire_settings() -> tuple[bool, float, int]:
    """``(enabled, poll_seconds, confirmations)`` for generation self-retire."""
    enabled = os.environ.get("AGENT_DISPATCH_SELF_RETIRE", "").strip().lower() not in (
        "0", "false", "no", "off",
    )
    try:
        poll = float(
            os.environ.get("AGENT_DISPATCH_SELF_RETIRE_POLL_S", "")
            or _SELF_RETIRE_DEFAULT_POLL_S
        )
        poll = max(1.0, poll)
    except ValueError:
        poll = _SELF_RETIRE_DEFAULT_POLL_S
    try:
        k = int(
            os.environ.get("AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS", "")
            or _SELF_RETIRE_DEFAULT_CONFIRMATIONS
        )
        k = max(1, k)
    except ValueError:
        k = _SELF_RETIRE_DEFAULT_CONFIRMATIONS
    return enabled, poll, k


def _self_retire_force_exit_seconds() -> float:
    """Deadline (seconds) after ``should_exit`` before self-retire force-exits."""
    try:
        deadline = float(
            os.environ.get("AGENT_DISPATCH_SELF_RETIRE_FORCE_EXIT_S", "")
            or _SELF_RETIRE_FORCE_EXIT_DEFAULT_S
        )
        return max(1.0, deadline)
    except ValueError:
        return _SELF_RETIRE_FORCE_EXIT_DEFAULT_S


async def _force_exit_after_should_exit(
    *,
    deadline_s: float,
    my_gen: int,
    my_pid: int,
    sleep=asyncio.sleep,
    force_exit=os._exit,
) -> None:
    """Hard backstop for self-retire's graceful uvicorn shutdown (#3068)."""
    await sleep(deadline_s)
    log.error(
        "self-retire: graceful shutdown did not complete within %.0fs of "
        "should_exit -- force-exiting to avoid an unreachable zombie "
        "coordinator (was gen %d, pid %d, see #3068)",
        deadline_s, my_gen, my_pid,
    )
    force_exit(1)


def _abandoned_passive_reap_settings() -> tuple[bool, float, float]:
    """``(enabled, poll_seconds, grace_seconds)`` for the abandoned-passive reap."""
    enabled = os.environ.get(
        "AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", ""
    ).strip().lower() not in ("0", "false", "no", "off")
    try:
        poll = float(
            os.environ.get("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_POLL_S", "")
            or _ABANDONED_PASSIVE_REAP_DEFAULT_POLL_S
        )
        poll = max(1.0, poll)
    except ValueError:
        poll = _ABANDONED_PASSIVE_REAP_DEFAULT_POLL_S
    try:
        grace = float(
            os.environ.get("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_GRACE_S", "")
            or _ABANDONED_PASSIVE_REAP_DEFAULT_GRACE_S
        )
        grace = max(0.0, grace)
    except ValueError:
        grace = _ABANDONED_PASSIVE_REAP_DEFAULT_GRACE_S
    return enabled, poll, grace


def _self_update_settings() -> tuple[bool, float, int, float]:
    """``(enabled, poll_seconds, confirmations, cooldown_seconds)``."""
    import math

    enabled = os.environ.get("AGENT_DISPATCH_SELF_UPDATE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    try:
        poll = float(
            os.environ.get("AGENT_DISPATCH_SELF_UPDATE_POLL_S", "")
            or _SELF_UPDATE_DEFAULT_POLL_S
        )
        if not math.isfinite(poll):
            raise ValueError(poll)
        poll = max(1.0, poll)
    except ValueError:
        poll = _SELF_UPDATE_DEFAULT_POLL_S
    try:
        k = int(
            os.environ.get("AGENT_DISPATCH_SELF_UPDATE_CONFIRMATIONS", "")
            or _SELF_UPDATE_DEFAULT_CONFIRMATIONS
        )
        k = max(1, k)
    except ValueError:
        k = _SELF_UPDATE_DEFAULT_CONFIRMATIONS
    try:
        cooldown = float(
            os.environ.get("AGENT_DISPATCH_SELF_UPDATE_COOLDOWN_S", "")
            or _SELF_UPDATE_DEFAULT_COOLDOWN_S
        )
        if not math.isfinite(cooldown):
            raise ValueError(cooldown)
        cooldown = max(0.0, cooldown)
    except ValueError:
        cooldown = _SELF_UPDATE_DEFAULT_COOLDOWN_S
    return enabled, poll, k, cooldown


def _spawn_self_deploy(python_path: Any) -> None:
    """Fire-and-forget a self-triggered ``deploy`` using *python_path*."""
    import subprocess

    from agent_procutil import detached_kwargs, windowless_python, windowless_python_env

    cmd = [windowless_python(python_path), "-m", "agent_dispatch", "deploy", "--json"]
    kwargs: dict[str, Any] = {
        "env": {**os.environ, **windowless_python_env(python_path)},
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    kwargs.update(detached_kwargs(breakaway=True))
    subprocess.Popen(cmd, **kwargs)  # noqa: S603


class DrainGate:
    """Process-wide drain state for the graceful daemon cutover."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._draining = False
        self._claims = 0

    @property
    def draining(self) -> bool:
        with self._condition:
            return self._draining

    @property
    def claims(self) -> int:
        with self._condition:
            return self._claims

    def set_draining(self, value: bool) -> None:
        with self._condition:
            self._draining = value
            self._condition.notify_all()

    @contextmanager
    def track_claim(self):
        """Count an in-flight claim so drain can wait for the safe point."""
        with self._condition:
            self._claims += 1
        try:
            yield
        finally:
            with self._condition:
                self._claims = max(0, self._claims - 1)
                self._condition.notify_all()

    def wait_for_claims(self, *, timeout: float, poll: float) -> bool:
        """Block until no claim is in flight (True) or ``timeout`` elapses (False)."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._claims > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(max(poll, 0.05), remaining))
            return True


class DrainRequest(BaseModel):
    """Request body for the zdd drain endpoint."""

    timeout: float = 300.0
    poll: float = 1.0
    force: bool = False


@dataclass
class LoopHealth:
    """Live status + backoff state for one periodic background loop."""

    name: str
    base_interval: float
    max_interval: float = 3600.0
    backoff_multiplier: float = 2.0
    current_interval: float = field(init=False)
    in_progress: bool = False
    last_started_at: float | None = None
    last_finished_at: float | None = None
    last_duration_s: float | None = None
    last_error: str | None = None
    last_timed_out: bool = False
    consecutive_failures: int = 0
    total_runs: int = 0
    total_timeouts: int = 0

    def __post_init__(self) -> None:
        self.current_interval = self.base_interval

    def record_backoff(self) -> None:
        """Recompute ``current_interval`` from ``consecutive_failures``."""
        if self.consecutive_failures:
            self.current_interval = min(
                self.max_interval,
                self.base_interval
                * (self.backoff_multiplier**self.consecutive_failures),
            )
        else:
            self.current_interval = self.base_interval

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_interval": self.base_interval,
            "current_interval": self.current_interval,
            "in_progress": self.in_progress,
            "last_started_at": self.last_started_at,
            "last_finished_at": self.last_finished_at,
            "last_duration_s": self.last_duration_s,
            "last_error": self.last_error,
            "last_timed_out": self.last_timed_out,
            "consecutive_failures": self.consecutive_failures,
            "total_runs": self.total_runs,
            "total_timeouts": self.total_timeouts,
        }


async def _run_supervised_cycle(
    health: LoopHealth,
    work: Callable[[], Any],
    *,
    cycle_timeout: float,
) -> Any | None:
    """Run one cycle of a periodic loop's blocking ``work``, bounded and recorded."""
    health.in_progress = True
    health.last_started_at = time.time()
    result = None
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(work), timeout=cycle_timeout
        )
    except (TimeoutError, asyncio.TimeoutError):
        health.last_timed_out = True
        health.last_error = f"cycle exceeded {cycle_timeout}s"
        health.consecutive_failures += 1
        health.total_timeouts += 1
        log.warning(
            "%s pass timed out after %.1fs (consecutive failures: %d)",
            health.name,
            cycle_timeout,
            health.consecutive_failures,
        )
    except Exception as exc:  # pragma: no cover -- never let the loop die on a blip
        health.last_timed_out = False
        health.last_error = str(exc)
        health.consecutive_failures += 1
        log.exception("%s pass failed", health.name)
    else:
        health.last_timed_out = False
        health.last_error = None
        health.consecutive_failures = 0
    finally:
        health.in_progress = False
        health.last_finished_at = time.time()
        health.last_duration_s = health.last_finished_at - (
            health.last_started_at or health.last_finished_at
        )
        health.total_runs += 1
        health.record_backoff()
    return result


def _resolve_owner_session_id(worker_id: str | None) -> str | None:
    """Best-effort owner-session resolution for liveness GC attribution."""
    if not worker_id or "/" not in worker_id:
        return None
    from . import tracking

    machine, _sep, worktree = worker_id.partition("/")
    if not worktree:
        return None
    is_remote = tracking.remote_dispatch.is_peer_machine(machine)
    session = tracking.resolve_live_session(
        worktree, machine=machine if is_remote else None
    )
    if not session:
        return None
    return session.get("session_id") or session.get("id")


def _reap_orphans(queue: TaskQueue, grace: float) -> int:
    """Reap unowned proposed/queued tasks pinned to a no-longer-live worktree."""
    from . import tracking
    from .identity import resolve_machine

    machine = resolve_machine()
    if not machine:
        return 0
    live = tracking.live_worktrees()
    if live is None:
        return 0
    counts = queue.reap_orphaned_targets(live, machine=machine, grace_secs=grace)
    return counts.get("reaped", 0)


async def _governance_backoff(
    governance: LoopGovernance | None,
    checkpoint: str,
    *,
    loop_name: str,
    backoff_seconds: float = _GOVERNANCE_BACKOFF_SECONDS,
) -> bool:
    if governance is None:
        return False
    result = governance.recheck(checkpoint)
    if result is None or result.get("status") == "ready":
        return False
    log.warning(
        "%s backing off at %s: %s (%s)",
        loop_name,
        result.get("checkpoint"),
        result.get("reason"),
        result.get("status"),
    )
    await asyncio.sleep(backoff_seconds)
    return True


async def _gc_loop(
    queue: TaskQueue,
    interval: float,
    bus: EventBus,
    *,
    health: LoopHealth,
    cycle_timeout: float | None = None,
    governance: LoopGovernance | None = None,
    run_supervised_cycle: Callable[..., Any] = _run_supervised_cycle,
    governance_backoff: Callable[..., Any] = _governance_backoff,
) -> None:
    """Periodically garbage-collect tasks by **liveness**."""
    while True:
        await asyncio.sleep(health.current_interval)
        if await governance_backoff(
            governance,
            "iteration-boundary:liveness-gc",
            loop_name="liveness GC",
        ):
            continue
        if await governance_backoff(
            governance,
            "pre-mutation:reconcile-liveness",
            loop_name="liveness GC",
        ):
            continue
        counts = await run_supervised_cycle(
            health,
            queue.reconcile_liveness,
            cycle_timeout=cycle_timeout or min(interval, 120.0),
        )
        counts = counts or {}
        requeued = counts.get("requeued", 0)
        if requeued:
            log.info(
                "liveness GC requeued %d task(s) with a gone owner (checked %d)",
                requeued,
                counts.get("checked", 0),
            )
            bus.publish({"type": "task.reconciled", "requeued": requeued, **counts})


async def _orphan_reap_loop(
    queue: TaskQueue,
    interval: float,
    bus: EventBus,
    *,
    orphan_grace: float = DEFAULT_ORPHAN_GRACE,
    health: LoopHealth,
    cycle_timeout: float | None = None,
    governance: LoopGovernance | None = None,
    run_supervised_cycle: Callable[..., Any] = _run_supervised_cycle,
    governance_backoff: Callable[..., Any] = _governance_backoff,
) -> None:
    """Reap orphaned target pins without blocking held-task liveness GC."""
    while True:
        await asyncio.sleep(health.current_interval)
        if await governance_backoff(
            governance,
            "iteration-boundary:orphan-reap",
            loop_name="orphan reaper",
        ):
            continue
        if await governance_backoff(
            governance,
            "pre-mutation:reap-orphans",
            loop_name="orphan reaper",
        ):
            continue
        reaped = await run_supervised_cycle(
            health,
            lambda: _reap_orphans(queue, orphan_grace),
            cycle_timeout=cycle_timeout or min(interval, 60.0),
        )
        reaped = reaped or 0
        if reaped:
            log.info(
                "liveness GC reaped %d orphaned task(s) (target worktree gone)",
                reaped,
            )
            bus.publish({"type": "task.reaped", "reaped": reaped})


def _local_machine_name() -> str | None:
    from .remote_dispatch import local_machine

    return local_machine()


def _owned_worktree_refs_for_machine(
    queue: TaskQueue,
    machine: str | None,
    *,
    limit: int = 5000,
) -> list[tuple[str, str]]:
    """The current owned worktrees this coordinator may legitimately poll."""
    if not machine:
        return []
    refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for task in queue.list(status=list(Status.OWNED), limit=limit):
        if task.owner:
            owner_machine, _sep, owner_worktree = task.owner.partition("/")
        else:
            owner_machine = task.target_machine or ""
            owner_worktree = task.target_worktree or ""
        if (
            not task.repo
            or not owner_machine
            or not owner_worktree
            or owner_machine.casefold() != machine.casefold()
        ):
            continue
        key = (task.repo, owner_worktree)
        if key not in seen:
            seen.add(key)
            refs.append(key)
    return refs


def _owns_worktree_ref(
    queue: TaskQueue,
    machine: str | None,
    repo: str,
    worktree_id: str,
    *,
    limit: int = 200,
) -> bool:
    if not machine:
        return False
    for task in queue.list(repo=repo, status=list(Status.OWNED), limit=limit):
        if task.owner:
            owner_machine, _sep, owner_worktree = task.owner.partition("/")
        else:
            owner_machine = task.target_machine or ""
            owner_worktree = task.target_worktree or ""
        if (
            owner_machine
            and owner_worktree
            and owner_machine.casefold() == machine.casefold()
            and owner_worktree == worktree_id
        ):
            return True
    return False


def _fetch_worktree_status_bundle(
    repo: str,
    worktree_id: str,
    *,
    resolve_project_name: Callable[[str | None], str | None] = name_for_repo,
    capture: Callable[..., Any] = run_agent_worktrees_capture,
    timeout: float = 60.0,
) -> dict[str, Any] | None:
    project = resolve_project_name(repo)
    if not project:
        return None
    result = capture(
        "worktree-status-bundle",
        "--worktree",
        worktree_id,
        "--json",
        timeout=timeout,
    )
    if result is None or result.returncode != 0:
        return None
    try:
        import json

        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("project") != project:
        return None
    return payload


def _refresh_worktree_status_relay(
    queue: TaskQueue,
    relay: WorktreeStatusRelayStore,
    *,
    poll_interval: float,
    resolve_machine: Callable[[], str | None] = _local_machine_name,
    fetch_bundle: Callable[[str, str], dict[str, Any] | None] = _fetch_worktree_status_bundle,
    refs: list[tuple[str, str]] | None = None,
    max_items: int | None = None,
) -> dict[str, int]:
    machine = resolve_machine()
    current_refs = _owned_worktree_refs_for_machine(queue, machine)
    current_ref_set = set(current_refs)
    refs = list(refs or current_refs)
    if refs:
        refs = [
            ref
            for ref in refs
            if _owns_worktree_ref(queue, machine, ref[0], ref[1])
        ]
    if max_items is not None:
        refs = refs[:max_items]
    refreshed = 0
    for repo, worktree_id in refs:
        bundle = fetch_bundle(repo, worktree_id)
        if bundle is None:
            continue
        relay.put(
            repo,
            worktree_id,
            bundle,
            fetched_at=time.time(),
            poll_interval_seconds=poll_interval,
        )
        refreshed += 1
    pruned = relay.prune(keep=current_ref_set)
    return {"checked": len(refs), "updated": refreshed, "pruned": pruned}


async def _worktree_status_relay_loop(
    queue: TaskQueue,
    interval: float,
    bus: EventBus,
    *,
    relay: WorktreeStatusRelayStore,
    signal: asyncio.Queue[None],
    health: LoopHealth,
    cycle_timeout: float | None = None,
    governance: LoopGovernance | None = None,
    run_supervised_cycle: Callable[..., Any] = _run_supervised_cycle,
    governance_backoff: Callable[..., Any] = _governance_backoff,
) -> None:
    """Keep per-worktree relay entries warm off the request/render path."""
    first_pass = True
    pending_refs: list[tuple[str, str]] = []
    while True:
        if not pending_refs:
            if not first_pass:
                try:
                    await asyncio.wait_for(signal.get(), timeout=health.current_interval)
                    while not signal.empty():
                        signal.get_nowait()
                except (TimeoutError, asyncio.TimeoutError):
                    pass
            first_pass = False
            try:
                pending_refs = _owned_worktree_refs_for_machine(
                    queue, _local_machine_name()
                )
            except Exception:
                log.warning(
                    "worktree-status relay snapshot failed", exc_info=True
                )
                await asyncio.sleep(_GOVERNANCE_BACKOFF_SECONDS)
                continue
        if not pending_refs:
            relay.prune()
            continue
        current_ref = [pending_refs.pop(0)]
        if await governance_backoff(
            governance,
            "iteration-boundary:worktree-status-relay",
            loop_name="worktree-status relay",
        ):
            pending_refs = current_ref + pending_refs
            continue
        if await governance_backoff(
            governance,
            "pre-mutation:worktree-status-relay",
            loop_name="worktree-status relay",
        ):
            pending_refs = current_ref + pending_refs
            continue
        counts = await run_supervised_cycle(
            health,
            lambda: _refresh_worktree_status_relay(
                queue,
                relay,
                poll_interval=interval,
                refs=current_ref,
                max_items=1,
            ),
            cycle_timeout=cycle_timeout or 75.0,
        )
        counts = counts or {}
        updated = counts.get("updated", 0)
        if updated:
            bus.publish(
                {"type": "worktree_status.relay_refreshed", **counts}
            )


def _find_stale_handoff_tasks(
    queue: TaskQueue, grace: float, machine: str, *, now: float | None = None
) -> list[Task]:
    """Unclaimed ``handoff``-labeled tasks past the bounded reconciliation window."""
    ts = queue._now(now)
    cutoff = ts - max(0.0, grace)
    tasks = queue.list(status=[Status.PROPOSED, Status.QUEUED], label="handoff")
    return [
        task
        for task in tasks
        if machine_matches(task.target_machine, machine) and task.created_at < cutoff
    ]


def _attempt_handoff_fallback(
    queue: TaskQueue,
    task: Task,
    *,
    spawn_fn: Callable[..., Any] | None = None,
) -> str:
    """Attempt exactly one fallback launch for ``task``."""
    if not queue.claim_handoff_fallback(task.id):
        return "skipped"
    from . import bridge
    from .handoff_fallback_seed import build_fallback_seed

    if spawn_fn is None:
        spawn_fn = bridge.spawn_worker
    try:
        seed = build_fallback_seed(task.id, task.title)
    except ValueError as exc:
        queue.record_handoff_fallback_outcome(task.id, outcome="error", detail=str(exc))
        return "error"
    try:
        result = spawn_fn(
            task.id,
            worker_id=f"handoff-fallback:{task.id}",
            prompt=seed,
            worktree_id=task.target_worktree,
            reclaim=True,
            wait=False,
        )
    except bridge.BridgeUnavailable as exc:
        queue.record_handoff_fallback_outcome(task.id, outcome="unavailable", detail=str(exc))
        return "unavailable"
    except Exception as exc:
        queue.record_handoff_fallback_outcome(task.id, outcome="error", detail=str(exc))
        log.warning("handoff-fallback launch raised for task %s", task.id, exc_info=True)
        return "error"
    if result.returncode == 0:
        queue.record_handoff_fallback_outcome(task.id, outcome="launched")
        return "launched"
    detail = (result.stderr or result.stdout or "").strip()[:500] or None
    queue.record_handoff_fallback_outcome(task.id, outcome="failed", detail=detail)
    return "failed"


def _reconcile_handoff_fallback(
    queue: TaskQueue,
    grace: float = DEFAULT_HANDOFF_FALLBACK_GRACE,
) -> dict[str, int]:
    """Attempt-launch every stale unclaimed ``handoff`` task on this machine."""
    from .identity import resolve_machine

    counts = {
        "checked": 0, "launched": 0, "skipped": 0,
        "unavailable": 0, "failed": 0, "error": 0,
    }
    machine = resolve_machine()
    if not machine:
        return counts
    for task in _find_stale_handoff_tasks(queue, grace, machine):
        counts["checked"] += 1
        outcome = _attempt_handoff_fallback(queue, task)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


async def _handoff_fallback_loop(
    queue: TaskQueue,
    interval: float,
    bus: EventBus,
    *,
    grace: float = DEFAULT_HANDOFF_FALLBACK_GRACE,
    health: LoopHealth,
    cycle_timeout: float | None = None,
    governance: LoopGovernance | None = None,
    run_supervised_cycle: Callable[..., Any] = _run_supervised_cycle,
    governance_backoff: Callable[..., Any] = _governance_backoff,
) -> None:
    """Periodically reconcile a stale unclaimed ``handoff`` task into a fallback launch."""
    while True:
        await asyncio.sleep(health.current_interval)
        if await governance_backoff(
            governance,
            "iteration-boundary:handoff-fallback",
            loop_name="handoff fallback",
        ):
            continue
        if await governance_backoff(
            governance,
            "pre-mutation:handoff-fallback",
            loop_name="handoff fallback",
        ):
            continue
        counts = await run_supervised_cycle(
            health,
            lambda: _reconcile_handoff_fallback(queue, grace),
            cycle_timeout=cycle_timeout or min(interval, 60.0),
        )
        counts = counts or {}
        launched = counts.get("launched", 0)
        if launched or counts.get("checked", 0):
            log.info(
                "handoff fallback: checked %d stale unclaimed handoff task(s), "
                "launched %d, %s",
                counts.get("checked", 0), launched, counts,
            )
        if launched:
            bus.publish({"type": "task.handoff_fallback", "launched": launched, **counts})
