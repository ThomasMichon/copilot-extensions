"""The **singleton supervisor daemon** -- the master runtime that runs the
registered work on one host.

Where :mod:`agent_dispatch.registrations` defines *what* is registered and the
coordinator store persists it, this module is *what runs it*: exactly **one**
supervisor process per machine-and-environment reads the registration registry
and drives each **active** registration in its **own subprocess**, reconciling
the running set against the registry on every tick -- starting a newly-registered
unit, restarting one whose spec changed, winding down one that was removed or
paused, and reviving one whose subprocess crashed. One busy or failing unit is
isolated in its own child, so it never blocks its siblings or the master.

Single-instance is enforced with the store's **pin-not-failover** election (a
schedule-lease over a ``supervisor:<machine>:<env>`` scope): the first daemon
wins the scope and a second **stands down** rather than spawning a rival loop --
the *one supervisor per machine-and-environment* the vision requires.

This increment lands the daemon mechanics and the **supervised-lane** unit
(reconstruct the ``agent-dispatch supervise`` foreground loop from a stored
spec). Folding the schedule / emitter / evaluator kinds into units the daemon
runs is the next increment; an unsupported kind is logged and skipped, never
fatal. The class is transport-light and fully injectable (launcher, clock,
sleep) so its reconcile logic is unit-tested without real subprocesses.

Registration fingerprinting, desired-set diffing, command-line building, and
the launcher/handle protocols used to live here too; they moved to
``supervisor_registration.py`` to keep this module under its
line-count ceiling as the repo grows. Every name is re-imported below so
existing ``from .supervisor_daemon import ...`` call sites and tests are
unaffected.

See ``visions/plugins/agent-dispatch`` -- Concept *the supervisor*, Feature
*registered-supervision*, Behavior *supervise-registers-and-returns*.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FUTURE_TIMEOUT
from concurrent.futures import wait as _wait_futures
from pathlib import Path
from typing import Any

from plugin_activation import read_json_object, write_json_object_atomic

from .companion import (
    CompanionController,
    CompanionError,
    CompanionIndeterminate,
    CompanionResolution,
    DefaultCompanionController,
    ManagedLaunchSnapshot,
    companion_authority_fingerprint,
    transition_group_receipt_path,
)
from .managed_runtime import (
    ManagedRuntimeError,
    ManagedRuntimeMaterializer,
    MaterializedRuntime,
    RuntimeMaterializer,
)
from .loop_governance import LoopGovernance
from .registrations import RegistrationKind

from .supervisor_registration import (  # noqa: F401 -- re-exported below
    DesiredRegistrationSet,
    Launcher,
    ManagedUnit,
    ProcHandle,
    ReconcileSummary,
    SELF_UPDATE_EXIT_CODE,
    SubprocessLauncher,
    UnsupportedKind,
    _SELF_UPDATE_DEFAULT_COOLDOWN_S,
    _SELF_UPDATE_DEFAULT_POLL_S,
    _is_connection_error,
    _lane_flags,
    _runtime_equivalence_fingerprint,
    _self_update_settings,
    _spawn_self_update_successor,
    _spec_fingerprint,
    build_command,
    merge_registration_sources,
    registration_logical_ids,
    registration_override_ids,
    registration_override_logical_ids,
    supervisor_lease_scope,
)

log = logging.getLogger("agent-dispatch.supervisor-daemon")
_GOVERNANCE_BACKOFF_SECONDS = 10.0
#: Bounded grace window for shutdown() to wait out an in-flight managed-
#: runtime task (materialize()/cleanup()) before a self-update hands off to
#: its successor. Long enough for the common case (a nearly-finished
#: install); never so long that a genuinely slow build (up to the lock's own
#: multi-minute budget) blocks the handoff and starves reconciles/
#: heartbeats -- unlike acquiring an actual lock, this is deliberately
#: best-effort, not a correctness guarantee.
_SHUTDOWN_GRACE_SECONDS = 15.0


def _remaining(deadline: float) -> float:
    """Seconds left until a ``time.monotonic()``-based deadline, never negative.

    Used to share one reconcile-call budget across every companion a single
    loop processes (see ``_poll_managed_health``/``_poll_managed_validate``/
    ``_poll_unmanaged_health``): the first slow companion consumes part of
    the shared window, so a later one only ever waits whatever is left of it
    -- never the full budget again -- keeping the loop's *total* added delay
    bounded by the budget once, not multiplied by however many companions
    are being reconciled.
    """
    return max(0.0, deadline - time.monotonic())


class SupervisorDaemon:
    """The one-per-host master that reconciles the registry into subprocesses.

    Drive :meth:`reconcile_once` on a cadence (or call :meth:`serve` to loop).
    Every dependency that touches the outside world -- the launcher, the clock,
    the sleep -- is injectable, so the reconcile logic is unit-tested with fakes
    and no real processes.
    """

    def __init__(
        self,
        client: Any,
        machine: str | None,
        env: str = "default",
        *,
        launcher: Launcher | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 5.0,
        restart_backoff: float = 5.0,
        max_restarts: int | None = None,
        lock: Any | None = None,
        declared_source: Callable[[], Iterable[Any]] | None = None,
        overrides_source: Callable[[], Mapping[str, dict]] | None = None,
        client_factory: Callable[[], Any] | None = None,
        companion_controller: CompanionController | None = None,
        runtime_materializer: RuntimeMaterializer | None = None,
        runtime_executor: Executor | None = None,
        self_update_enabled: bool = False,
        self_update_poll_interval: float = _SELF_UPDATE_DEFAULT_POLL_S,
        self_update_cooldown: float = _SELF_UPDATE_DEFAULT_COOLDOWN_S,
        self_update_argv: list[str] | None = None,
        self_update_install_dir: Callable[[], Path] | None = None,
        self_update_running_version: str | None = None,
        self_update_stale_target: Callable[[Path, str], Path | None] | None = None,
        self_update_spawn: Callable[[Any, list[str]], None] | None = None,
        reconcile_call_budget: float = 3.0,
    ):
        self.client = client
        #: Rebuilds the coordinator client by **re-resolving** its endpoint (the
        #: rendezvous ``endpoint.json``). The coordinator binds an OS-assigned
        #: ephemeral port, so a coordinator restart moves the port; a daemon that
        #: cached the old port at startup would wedge -- every reconcile failing to
        #: reach a dead port. On a connection-level failure the serve loop calls
        #: this to re-resolve and reconnect, mirroring the rendezvous cutover
        #: ladder. ``None`` disables reconnect (tests inject a fixed fake client).
        self._client_factory = client_factory
        self.machine = machine
        self.env = env or "default"
        self.launcher = launcher or SubprocessLauncher()
        self.clock = clock
        self.sleep = sleep
        self.poll_interval = max(0.5, float(poll_interval))
        #: Bounded per-call wait for an off-thread health/validate probe (see
        #: _poll_managed_health/_poll_managed_validate/_poll_unmanaged_health)
        #: before deferring its result to a later tick. Keeps the common,
        #: fast case (a healthy companion, an uncontended lock) behaving
        #: exactly as a synchronous call would -- starting/reviving a unit
        #: within the same reconcile_once() -- while still bounding how long
        #: one stuck/contended companion can hold up reconciling every other
        #: one, in the rare case its own check doesn't finish in time.
        self._reconcile_call_budget = max(0.0, float(reconcile_call_budget))
        #: Monotonic count of reconcile cycles started this process lifetime --
        #: the correlation id the serve-loop heartbeat log lines share, so a
        #: "cycle N starting" with no matching "cycle N finished" (the signature
        #: of a wedge: the process is alive but its single-threaded reconcile
        #: loop is blocked inside one cycle, e.g. on an unbounded subprocess
        #: call) is visible by tailing the log alone, without needing a stale
        #: heartbeat file to first go unnoticed.
        self._cycle_count = 0
        #: Quiet window before a crashed unit is restarted (a crash-loop damper).
        self.restart_backoff = max(0.0, float(restart_backoff))
        #: Cap on automatic restarts of a crash-looping unit (None = unbounded).
        self.max_restarts = max_restarts
        #: The single-instance lock (injectable for tests). Built lazily from the
        #: run-dir + scope when first needed, unless one was supplied.
        self._lock = lock
        #: Optional provider of the DECLARED profile set (the registrar's discovered
        #: declarations). Called every reconcile so appearing/changing/vanishing
        #: declarations are hot-reconciled alongside store-backed registrations --
        #: the *declarations-are-the-source-of-truth* path. Returns ProfileDeclaration
        #: objects; None disables declared supervision (store-only, legacy behavior).
        self.declared_source = declared_source
        #: Optional provider of the operator **override** map ({registration id ->
        #: record}). Called every reconcile and its overridden-off ids are subtracted
        #: from the desired set *after* the declared + store-backed sets are merged --
        #: so an override is a higher-precedence veto that discovery cannot undo (a
        #: re-declared unit stays wound down). None defaults to reading the local
        #: ``overrides.json`` store; a callable that raises is treated as "no
        #: overrides" (best-effort, never fatal).
        self.overrides_source = overrides_source
        #: The declared set most recently read successfully -- returned when a later
        #: discovery read *errors*, so a transient filesystem/env blip never winds
        #: down live declared units (only a successful read that no longer lists a
        #: unit does). Empty until the first successful read.
        self._last_declared: list[dict] = []
        self._published_declared_ids: set[str] = set()
        self._last_merge_diagnostics: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
        self._companion_controller = companion_controller
        self._runtime_materializer = runtime_materializer
        self._runtime_executor = runtime_executor
        self._owns_runtime_executor = runtime_executor is None
        self._last_companion_desired: dict[str, tuple[str, CompanionResolution | None]] = {}
        self._companion_resolutions: dict[str, CompanionResolution] = {}
        self._managed_uncertain: set[str] = set()
        self._managed_launch_failures: dict[str, tuple[str, float]] = {}
        self._managed_runtime_authorities: dict[str, str] = {}
        self._managed_runtime_results: dict[str, tuple[MaterializedRuntime, ...]] = {}
        self._managed_runtime_futures: dict[
            str, tuple[str, Future[tuple[MaterializedRuntime, ...]]]
        ] = {}
        self._managed_runtime_failures: dict[str, tuple[str, float, int]] = {}
        self._managed_cleanup_future: Future | None = None
        self._managed_cleanup_after = 0.0
        #: Every submitted materialize() future stays here until it actually
        #: completes -- unlike ``_managed_runtime_futures``, cancelling a
        #: withdrawn/changed registration's future (a no-op when it is
        #: already running, not merely queued) never removes it early, so
        #: the self-update handoff barrier (``_await_runtime_pool_idle``)
        #: keeps seeing it as in flight until it truly finishes.
        self._managed_runtime_inflight: set[Future] = set()
        #: Per-managed-companion health-probe and validate()+prepare_managed()
        #: futures. A companion's own health check or (re)launch validation
        #: can block for a long time (a health probe subprocess, or validate()
        #: waiting on the same interprocess lock a slow materialize() build
        #: holds) -- routing them through the runtime pool and only checking
        #: readiness synchronously means one stubborn companion's own check
        #: never blocks reconciling every *other* companion in this or later
        #: ticks. See _reconcile_managed's "one stubborn task holds up the
        #: whole reconcile loop" fix.
        self._managed_health_futures: dict[str, tuple[str, Future]] = {}
        self._managed_validate_futures: dict[str, tuple[str, Future]] = {}
        #: Same bounded-poll treatment for the _managed_uncertain selected-
        #: state recovery path's own validate() call (recover_live()'s
        #: precondition) -- separate from _managed_validate_futures since
        #: recovery never calls prepare_managed() the way a (re)launch does.
        self._managed_recovery_validate_futures: dict[str, tuple[str, Future]] = {}
        #: Same off-thread treatment for a *non*-managed companion's health
        #: probe (e.g. agent-ssh-dtssh-host) in reconcile_once's crash-revival
        #: pass -- a stuck probe subprocess must not block noticing/reviving
        #: any other unit that tick.
        self._unmanaged_health_futures: dict[str, Future] = {}
        #: Every health/validate probe future ever submitted, kept here until
        #: it is actually done() -- unlike the three dicts above, a snapshot
        #: superseding an in-flight probe (or a completed one being popped
        #: to allow a retry) never removes it from this set early. A probe
        #: can't be cancelled once running, so it keeps occupying a pool
        #: worker regardless of whether anything still cares about its
        #: result; this is what shutdown()'s grace wait actually drains, so
        #: an abandoned probe doesn't both saturate the pool AND evade the
        #: wait that's supposed to bound how long it can block interpreter
        #: exit. Pruned (entries already done() removed) at each poll and at
        #: shutdown, so it never grows unbounded over a long-running daemon.
        self._probe_inflight: set[Future] = set()
        #: An owned runtime executor abandoned by a failed self-update spawn
        #: while it still had a genuinely stuck probe/validate worker (see
        #: _maybe_self_update's failure branch): its own reference gets
        #: replaced so _runtime_pool() can lazily rebuild a live one for
        #: continued reconciliation, but the old (already-shutdown, still
        #: finishing its stuck work) executor object is kept here rather
        #: than silently discarded, so a repeated run of failed handoffs
        #: stays visible/managed instead of accumulating untracked pools.
        #: Its own futures remain tracked in _probe_inflight regardless of
        #: which executor submitted them, so shutdown()'s drain wait still
        #: covers them; this list exists for accounting/visibility, not a
        #: second drain path.
        self._retired_runtime_executors: list[Executor] = []
        #: Which futures shutdown()'s grace wait has already covered, and
        #: the running drained verdict across every shutdown() call this
        #: process lifetime -- keyed by future identity (not executor
        #: identity, since a retired executor still has its own futures in
        #: _probe_inflight/_managed_runtime_inflight even after
        #: self._runtime_executor itself becomes None), so a repeat call
        #: only pays the grace wait for a genuinely NEW future, never a
        #: second wait on one already accounted for.
        self._shutdown_waited_futures: set[Future] = set()
        self._shutdown_drained: bool = True
        self._units: dict[str, ManagedUnit] = {}
        #: Live self-update wiring (see the module-level notes above
        #: ``supervisor_lease_scope``). ``self_update_argv`` is this process's
        #: own ``sys.argv[1:]`` -- required (along with ``enabled``) to arm the
        #: loop; ``None`` leaves it permanently disabled regardless of the
        #: ``AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE`` env var, since there is
        #: nothing safe to respawn with.
        self.self_update_enabled = bool(self_update_enabled and self_update_argv)
        self.self_update_poll_interval = max(1.0, float(self_update_poll_interval))
        self.self_update_cooldown = max(0.0, float(self_update_cooldown))
        self._self_update_argv = self_update_argv
        if self_update_install_dir is None:
            from .runtime_version import install_dir as self_update_install_dir
        self._self_update_install_dir = self_update_install_dir
        if self_update_running_version is None:
            from . import __version__ as self_update_running_version
        self._self_update_running_version = self_update_running_version
        if self_update_stale_target is None:
            from .self_update import stale_target as self_update_stale_target
        self._self_update_stale_target = self_update_stale_target
        self._self_update_spawn = self_update_spawn or _spawn_self_update_successor
        #: Initialized to "never checked" so the very first tick is always
        #: eligible (rather than waiting a full poll interval after startup).
        self._self_update_last_check = float("-inf")
        self._self_update_last_spawn: float | None = None
        #: Set by ``_maybe_self_update`` the tick it triggers a handoff; read by
        #: ``serve`` to break its loop and pick the distinct return code.
        self.self_update_triggered = False
        self._governance = LoopGovernance()
        #: Cached canonical interpreter for spawning registration children --
        #: resolved once (not per-registration/per-tick) since the current-version
        #: slot cannot change without a full daemon cutover/restart anyway. See
        #: :func:`agent_dispatch.procutil.resolve_own_runtime_python`: every
        #: registration child MUST run under the same installed slot this daemon
        #: itself does, never whatever interpreter happened to launch it -- a raw
        #: ``sys.executable`` default here was one of the sites behind a production
        #: incident where an entire duplicate coordinator+supervisor process tree
        #: ran under the system Python instead of the versioned runtime.
        self._own_python_cached: str | None = None

    def _own_python(self) -> str:
        if self._own_python_cached is None:
            from .procutil import resolve_own_runtime_python

            self._own_python_cached = resolve_own_runtime_python()
        return self._own_python_cached

    def set_governance_recheck_fn(
        self,
        fn: Callable[[str], dict[str, Any]] | None,
    ) -> None:
        """Override the loop-governance recheck callback (tests)."""
        self._governance.set_recheck_fn(fn)

    def _recheck_governance(self, checkpoint: str) -> dict[str, Any] | None:
        return self._governance.recheck(checkpoint)

    # -- registry view -------------------------------------------------------

    def _declared(self) -> list[dict]:
        """The declared profile set for this (machine, env), as registrations.

        Re-read every reconcile (that is the *watch*): the daemon's existing
        start/restart/wind-down logic turns a re-read into hot-reconcile. On a read
        *error* the last successfully-read set is returned (not empty), so a transient
        discovery failure never tears down live declared units; a successful read that
        drops a unit still winds it down.
        """
        if self.declared_source is None:
            return []
        from .registrar_reconcile import declared_registrations
        from .supervisor_daemon_registration_publish import publish_declared_registrations

        try:
            decls = list(self.declared_source())
        except Exception:  # pragma: no cover - discovery is environment-dependent
            log.exception("failed to read declared profile set; keeping the last known set")
            return self._last_declared
        self._last_declared = declared_registrations(decls, machine=self.machine, env=self.env)
        self._published_declared_ids = publish_declared_registrations(
            self.client, self._last_declared, published_ids=self._published_declared_ids
        )
        return self._last_declared

    def _overridden_off(self) -> set[str]:
        """Registration ids an operator has disabled via the override store.

        Best-effort: read via the injected ``overrides_source`` or, by default, the
        local ``overrides.json``. Any failure yields an empty set (no override in
        effect) rather than tearing down units on a bad read -- the override is a
        *stop* signal, so its absence must fail safe toward "keep running what is
        declared".
        """
        from . import overrides as ov

        try:
            if self.overrides_source is not None:
                data = self.overrides_source()
            else:
                from .config import overrides_path

                data = ov.load_overrides(overrides_path())
        except Exception:  # pragma: no cover - override read is environment-dependent
            log.exception("failed to read operator overrides; treating as none")
            return set()
        return ov.overridden_off_ids(data)

    def _desired(self) -> dict[str, dict]:
        """Active registrations scoped to this daemon's (machine, env), minus
        operator overrides.

        Three inputs, one desired set: the coordinator's store-backed registrations,
        the registrar's **declared** profile set, then the operator **override** veto.
        Declared ids are namespaced (``declared:...``) to avoid colliding with
        *derived* store ids; if a caller-supplied store id ever collides, the
        **declared** entry wins (the declaration is the source of truth). Finally,
        any id an operator has **overridden off** is dropped from the desired set --
        applied *last*, so the override outranks both the declaration and the
        discovery layer and a later re-sync cannot quietly revive the unit (vision
        Behavior *overrides-take-precedence*). A dropped id is then wound down by the
        reconcile's stop-not-desired step."""
        declared = self._declared()
        regs = self.client.list_registrations(
            machine=self.machine, env=self.env, include_paused=False
        )
        regs = [r for r in regs if r.get("id") not in self._published_declared_ids]
        merged = merge_registration_sources(regs, declared)
        diagnostics = (tuple(merged.deduplicated), tuple(merged.conflicts))
        if diagnostics != self._last_merge_diagnostics:
            for rid in merged.deduplicated:
                log.info(
                    "equivalent declaration supersedes direct registration %s; "
                    "keeping the direct row dormant for reversible migration",
                    rid,
                )
            for conflict in merged.conflicts:
                log.warning(
                    "direct/declaration registration conflict: %s; preserving both",
                    conflict,
                )
            self._last_merge_diagnostics = diagnostics
        desired = merged.registrations
        overridden = self._overridden_off()
        for rid in overridden:
            desired.pop(rid, None)
            replacement = merged.replacements.get(rid)
            if replacement:
                desired.pop(replacement, None)
        for rid, registration in list(desired.items()):
            if registration_override_ids(registration) & overridden:
                desired.pop(rid, None)
        self._resolve_companion_desired(desired)
        self._materialize_managed_runtime_desired(desired)
        self._deduplicated = merged.deduplicated
        self._conflicts = merged.conflicts
        return desired

    def _companion(self) -> CompanionController:
        if self._companion_controller is None:
            self._companion_controller = DefaultCompanionController(
                self._spec_dir() / "companions"
            )
        return self._companion_controller

    def _resolve_companion_desired(self, desired: dict[str, dict]) -> None:
        current: dict[str, CompanionResolution] = {}
        self._managed_uncertain.clear()
        present = {
            rid
            for rid, registration in desired.items()
            if registration.get("kind") == RegistrationKind.PLUGIN_COMPANION
        }
        for rid in set(self._last_companion_desired) - present:
            self._last_companion_desired.pop(rid, None)
        for rid in present:
            registration = desired[rid]
            authority = companion_authority_fingerprint(registration)
            try:
                resolution = self._companion().resolve(
                    copy.deepcopy(registration), machine=self.machine, env=self.env
                )
                if (
                    resolution is not None
                    and companion_authority_fingerprint(resolution.registration) != authority
                ):
                    raise CompanionError("resolved companion changed declaration authority")
            except CompanionIndeterminate as exc:
                if registration.get("spec", {}).get("managed_runtime"):
                    self._managed_uncertain.add(rid)
                    unit = self._units.get(rid)
                    snapshot = (
                        unit.companion_resolution.managed_snapshot
                        if unit is not None and unit.companion_resolution is not None
                        else None
                    )
                    if snapshot is None:
                        try:
                            snapshot = self._companion().selected_managed(rid)
                        except (CompanionError, CompanionIndeterminate, OSError, ValueError) as error:
                            log.error("cannot recover managed companion %s: %s", rid, error)
                    if snapshot is not None and snapshot.authority == authority:
                        resolution = snapshot.resolution()
                        desired[rid] = resolution.registration
                        current[rid] = resolution
                        log.warning("managed companion %s provider is indeterminate: %s", rid, exc)
                    else:
                        desired.pop(rid, None)
                        self._last_companion_desired.pop(rid, None)
                        log.warning(
                            "managed companion %s provider is indeterminate with changed "
                            "or unconfirmed authority; withholding it: %s", rid, exc,
                        )
                    continue
                previous = self._last_companion_desired.get(rid)
                if previous is not None and previous[0] == authority:
                    resolution = previous[1]
                    log.warning(
                        "companion %s configuration is indeterminate; "
                        "retaining its last confirmed desired state: %s",
                        rid,
                        exc,
                    )
                else:
                    desired.pop(rid, None)
                    log.warning(
                        "companion %s configuration is indeterminate without "
                        "matching confirmed authority; withholding it: %s",
                        rid,
                        exc,
                    )
                    continue
            except CompanionError as exc:
                desired.pop(rid, None)
                self._last_companion_desired.pop(rid, None)
                log.error("invalid companion %s: %s", rid, exc)
                continue
            else:
                self._last_companion_desired[rid] = (authority, resolution)

            if resolution is None:
                desired.pop(rid, None)
            else:
                desired[rid] = resolution.registration
                current[rid] = resolution
        managed_companions = {
            rid
            for rid, unit in self._units.items()
            if unit.kind == RegistrationKind.PLUGIN_COMPANION
        }
        try:
            self._companion().reconcile_receipts(
                set(current) | managed_companions | self._managed_uncertain
            )
        except (CompanionError, CompanionIndeterminate, OSError) as exc:
            log.warning("companion receipt reconciliation is incomplete: %s", exc)
        self._companion_resolutions = current

    def _managed_runtime(self) -> RuntimeMaterializer:
        if self._runtime_materializer is None:
            self._runtime_materializer = ManagedRuntimeMaterializer()
        return self._runtime_materializer

    #: A slow first-time managed-runtime build (e.g. an ML-heavy companion
    #: installing torch/transformers) can run for tens of minutes. The actual
    #: physical build is serialized per plugin identity by the interprocess
    #: lock in ``managed_runtime.py`` (``_RootLock``; retention cleanup takes
    #: the same lock scope in ``managed_retention.py``), so a single-worker
    #: pool here buys no additional safety -- it only means that one slow
    #: build occupies the pool's only worker and starves everything else
    #: routed through it: the periodic retention cleanup
    #: (``_cleanup_managed_runtimes``, scheduled every 5 minutes) and any
    #: *other* companion's own materialize() call, for the entire duration of
    #: the slow build. A small pool keeps that queueing bounded without
    #: weakening the interprocess lock's own
    #: single-builder guarantee.
    _RUNTIME_POOL_WORKERS = 4

    def _runtime_pool(self) -> Executor:
        if self._runtime_executor is None:
            self._runtime_executor = ThreadPoolExecutor(
                max_workers=self._RUNTIME_POOL_WORKERS,
                thread_name_prefix="agent-dispatch-runtime",
            )
        return self._runtime_executor

    def _cleanup_managed_runtimes(self) -> None:
        """Keep root-lock waits and filesystem scans off the supervision thread."""
        future = self._managed_cleanup_future
        if future is not None:
            if not future.done():
                return
            self._managed_cleanup_future = None
            try:
                future.result()
            except (OSError, RuntimeError, ValueError) as exc:
                log.error("managed runtime retention preserved data: %s", exc)
        if self._runtime_materializer is not None and self.clock() >= self._managed_cleanup_after:
            self._managed_cleanup_after = self.clock() + 300.0
            self._managed_cleanup_future = self._runtime_pool().submit(
                self._companion().cleanup_managed
            )

    def _prune_managed_runtime_inflight(self) -> None:
        self._managed_runtime_inflight = {
            future for future in self._managed_runtime_inflight if not future.done()
        }

    def _harvest_managed_runtime_futures(self) -> None:
        self._prune_managed_runtime_inflight()
        for rid, (authority, future) in list(self._managed_runtime_futures.items()):
            if not future.done():
                continue
            self._managed_runtime_futures.pop(rid, None)
            try:
                result = tuple(future.result())
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                log.error("failed to materialize companion runtime %s: %s", rid, exc)
                previous = self._managed_runtime_failures.get(rid)
                attempts = (
                    previous[2] + 1
                    if previous is not None and previous[0] == authority
                    else 1
                )
                delay = min(
                    300.0,
                    max(self.poll_interval, 5.0) * (2 ** min(attempts - 1, 6)),
                )
                self._managed_runtime_failures[rid] = (
                    authority,
                    self.clock() + delay,
                    attempts,
                )
                continue
            self._managed_runtime_authorities[rid] = authority
            self._managed_runtime_results[rid] = result
            self._managed_runtime_failures.pop(rid, None)

    def _materialize_managed_runtime_desired(
        self, desired: Mapping[str, dict]
    ) -> None:
        """Prepare cells asynchronously; stale completions never authorize a launch."""
        present = {
            rid
            for rid, registration in desired.items()
            if registration.get("kind") == RegistrationKind.PLUGIN_COMPANION
            and isinstance(registration.get("spec"), dict)
            and registration["spec"].get("managed_runtime")
        }
        for rid in set(self._managed_runtime_authorities) - present:
            self._managed_runtime_authorities.pop(rid, None)
            self._managed_runtime_results.pop(rid, None)
        for rid in set(self._managed_runtime_failures) - present:
            self._managed_runtime_failures.pop(rid, None)
        for rid, (authority, future) in list(self._managed_runtime_futures.items()):
            if rid not in present or authority != companion_authority_fingerprint(desired[rid]):
                # cancel() is a no-op once the task is already running (not
                # merely queued) -- it stays in _managed_runtime_inflight
                # regardless, so the self-update handoff barrier still waits
                # for it to actually finish rather than losing track of it.
                future.cancel()
                self._managed_runtime_futures.pop(rid, None)
        self._harvest_managed_runtime_futures()
        for rid in present:
            if rid in self._managed_uncertain:
                continue
            registration = desired[rid]
            authority = companion_authority_fingerprint(registration)
            if self._managed_runtime_authorities.get(rid) == authority:
                continue
            failure = self._managed_runtime_failures.get(rid)
            if failure is not None:
                if failure[0] != authority:
                    self._managed_runtime_failures.pop(rid, None)
                elif self.clock() < failure[1]:
                    continue
            pending = self._managed_runtime_futures.get(rid)
            if pending is not None:
                continue
            future = self._runtime_pool().submit(
                self._managed_runtime().materialize,
                copy.deepcopy(registration),
            )
            self._managed_runtime_futures[rid] = (authority, future)
            self._managed_runtime_inflight.add(future)
        self._harvest_managed_runtime_futures()

    # -- unit lifecycle ------------------------------------------------------

    @staticmethod
    def _rollback_allowed(snapshot: ManagedLaunchSnapshot, registration: dict) -> bool:
        previous = snapshot.to_dict()["registration"]
        keys = ("id", "kind", "source", "owner", "machine", "env")
        if any(previous.get(key) != registration.get(key) for key in keys):
            return False
        old_plugin = previous.get("plugin")
        new_plugin = registration.get("plugin")
        if not isinstance(old_plugin, Mapping) or not isinstance(new_plugin, Mapping):
            return False
        if old_plugin.get("activation_scopes") != new_plugin.get("activation_scopes"):
            return False
        try:
            old_source_path = old_plugin["source_path"]
            old_root = old_plugin["root"]
            new_source_path = new_plugin["source_path"]
            new_root = new_plugin["root"]
            if not all(
                isinstance(value, str)
                for value in (old_source_path, old_root, new_source_path, new_root)
            ):
                return False
            old_source = Path(old_source_path).relative_to(old_root)
            new_source = Path(new_source_path).relative_to(new_root)
        except (KeyError, ValueError):
            return False
        return old_source == new_source

    @staticmethod
    def _transition_group(registration: Mapping[str, Any]) -> str | None:
        value = registration.get("transition_group")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _managed_transition_key(registration: Mapping[str, Any]) -> str:
        value = {
            "id": registration.get("id"),
            "owner": registration.get("owner"),
            "machine": registration.get("machine"),
            "env": registration.get("env"),
            "transition_group": registration.get("transition_group"),
            "spec": registration.get("spec"),
            "runtime_revision": registration.get("runtime_revision"),
            "companion_runtime": registration.get("companion_runtime"),
        }
        return json.dumps(value, sort_keys=True, default=str)

    def _transition_group_receipt_dir(self) -> Path:
        receipt_dir = getattr(self._companion(), "receipt_dir", None)
        return Path(receipt_dir) if receipt_dir is not None else self._spec_dir() / "companions"

    def _transition_group_path(self, group_id: str) -> Path:
        return transition_group_receipt_path(self._transition_group_receipt_dir(), group_id)

    def _transition_group_snapshots(
        self, value: object, *, members: tuple[str, ...], field: str
    ) -> dict[str, ManagedLaunchSnapshot] | None:
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != set(members):
            raise CompanionError(
                f"managed transition group {field} set is inconsistent"
            )
        snapshots: dict[str, ManagedLaunchSnapshot] = {}
        for registration_id in members:
            snapshot = ManagedLaunchSnapshot.from_dict(value[registration_id])
            if snapshot.to_dict()["registration"]["id"] != registration_id:
                raise CompanionError(
                    f"managed transition group {field} identity is inconsistent"
                )
            snapshots[registration_id] = snapshot
        return snapshots

    def _read_transition_group(
        self, group_id: str
    ) -> tuple[
        dict[str, ManagedLaunchSnapshot],
        dict[str, ManagedLaunchSnapshot] | None,
        dict[str, ManagedLaunchSnapshot] | None,
    ] | None:
        path = self._transition_group_path(group_id)
        if not (path.exists() or path.is_symlink()):
            return None
        _, record = read_json_object(path)
        if (
            not isinstance(record, dict)
            or set(record) != {"schema_version", "group_id", "members", "selected", "rollback", "pending"}
            or record.get("schema_version") != 1
            or record.get("group_id") != group_id
            or not isinstance(record.get("members"), list)
        ):
            raise CompanionError("managed transition group receipt is malformed")
        members = tuple(record["members"])
        if members != tuple(sorted(members)) or not all(
            isinstance(member, str) and member for member in members
        ):
            raise CompanionError("managed transition group members are malformed")
        selected = self._transition_group_snapshots(
            record["selected"], members=members, field="selected"
        )
        if selected is None:
            raise CompanionError("managed transition group selected set is missing")
        rollback = self._transition_group_snapshots(
            record.get("rollback"), members=members, field="rollback"
        )
        pending = self._transition_group_snapshots(
            record.get("pending"), members=members, field="pending"
        )
        return selected, rollback, pending

    def _write_transition_group(
        self,
        group_id: str,
        *,
        selected: Mapping[str, ManagedLaunchSnapshot],
        rollback: Mapping[str, ManagedLaunchSnapshot] | None,
        pending: Mapping[str, ManagedLaunchSnapshot] | None,
    ) -> None:
        members = tuple(sorted(selected))
        if not members:
            raise CompanionError("managed transition group must contain members")
        for record_field, snapshots in (("rollback", rollback), ("pending", pending)):
            if snapshots is not None and set(snapshots) != set(members):
                raise CompanionError(
                    f"managed transition group {record_field} members do not match selection"
                )
        path = self._transition_group_path(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_object_atomic(
            path,
            {
                "schema_version": 1,
                "group_id": group_id,
                "members": list(members),
                "selected": {
                    registration_id: selected[registration_id].to_dict()
                    for registration_id in members
                },
                "rollback": (
                    {
                        registration_id: rollback[registration_id].to_dict()
                        for registration_id in members
                    }
                    if rollback is not None
                    else None
                ),
                "pending": (
                    {
                        registration_id: pending[registration_id].to_dict()
                        for registration_id in members
                    }
                    if pending is not None
                    else None
                ),
            },
        )

    def _desired_managed_snapshot(
        self, rid: str, registration: dict
    ) -> ManagedLaunchSnapshot | None:
        if rid in self._managed_uncertain:
            return None
        authority = companion_authority_fingerprint(registration)
        if self._managed_runtime_authorities.get(rid) != authority:
            return None
        return ManagedLaunchSnapshot.capture(
            self._companion_resolutions[rid], self._managed_runtime_results[rid]
        )

    def _selected_group_snapshots(
        self, group_id: str, rids: tuple[str, ...]
    ) -> tuple[
        dict[str, ManagedLaunchSnapshot],
        dict[str, ManagedLaunchSnapshot] | None,
        dict[str, ManagedLaunchSnapshot] | None,
    ] | None:
        record = self._read_transition_group(group_id)
        if record is not None:
            selected, rollback, pending = record
            if set(selected) == set(rids):
                return selected, rollback, pending
            if pending is None and set(selected) <= set(rids):
                # A newly-declared member joined this transition group (e.g. a
                # sibling companion that was previously invalid/undeclared and
                # has now become valid -- the exact sequence that regressed
                # agent-index-service alongside agent-index-engine: dotfiles
                # #2111). There is no confirmed running snapshot for a
                # brand-new member to roll back to, and no in-flight
                # transition (`pending`) to protect, so this is safe to treat
                # like the "no persisted record" case below and rebuild fresh
                # -- rather than permanently wedging the whole group behind a
                # manual state-file deletion every time membership grows.
                record = None
            else:
                raise CompanionError("managed transition group membership changed")
        selected: dict[str, ManagedLaunchSnapshot] = {}
        for rid in rids:
            unit = self._units.get(rid)
            snapshot = (
                unit.companion_resolution.managed_snapshot
                if unit is not None
                and unit.companion_resolution is not None
                and unit.companion_resolution.managed_snapshot is not None
                else self._companion().selected_managed(rid)
            )
            if snapshot is None:
                return None
            selected[rid] = snapshot
        self._write_transition_group(group_id, selected=selected, rollback=None, pending=None)
        return selected, None, None

    def _restore_transition_group(
        self,
        group_id: str,
        selected: Mapping[str, ManagedLaunchSnapshot],
        rollback: Mapping[str, ManagedLaunchSnapshot] | None,
        summary: ReconcileSummary,
        *,
        deadline: float,
    ) -> bool:
        restored: dict[str, ManagedLaunchSnapshot] = dict(selected)
        needs_launch = {
            rid: snapshot
            for rid, snapshot in restored.items()
            if not (
                (unit := self._units.get(rid)) is not None
                and (
                    current := (
                        unit.companion_resolution.managed_snapshot
                        if unit.companion_resolution is not None
                        else None
                    )
                )
                is not None
                and current.fingerprint == snapshot.fingerprint
                and unit.proc is not None
                and unit.proc.poll() is None
            )
        }
        # Bounded and off-thread, like the forward cutover path's own
        # validate() polling -- a rollback triggered by a contended lock
        # (the exact failure mode this whole isolation change targets) must
        # not itself block the reconcile thread synchronously. All members
        # needing a (re)launch must confirm before any of them stops/
        # relaunches, deferring the whole restore (not a partial one) to the
        # next tick otherwise. Every member is polled unconditionally (a
        # plain list, not any() over a generator) so a pending member never
        # short-circuits submission of the members after it -- the same
        # any()-short-circuit mistake already fixed once for the forward
        # cutover path (see _reconcile_transition_groups) must not recur here.
        validated = [
            self._poll_managed_validate(rid, snapshot, deadline)
            for rid, snapshot in needs_launch.items()
        ]
        if any(result is None for result in validated):
            return False
        for rid, snapshot in needs_launch.items():
            unit = self._units.get(rid)
            if unit is not None and not self._stop(rid):
                return False
            self._launch_managed(
                snapshot, summary, bucket="revived", precondition_confirmed=True
            )
        self._write_transition_group(
            group_id, selected=restored, rollback=rollback, pending=None
        )
        return True

    def _launch_managed(
        self,
        snapshot: ManagedLaunchSnapshot,
        summary: ReconcileSummary,
        *,
        bucket: str,
        precondition_confirmed: bool = False,
    ) -> None:
        """Commit to launching a managed companion.

        ``precondition_confirmed=True`` tells this call that prepare_managed()
        and validate() already ran (and succeeded) moments ago in this same
        reconcile pass via ``_poll_managed_validate`` -- so it must not
        re-run them here synchronously, which would defeat that call's whole
        purpose of keeping a contended-lock wait off the reconcile thread.
        Every other caller (a fresh launch never preflighted this tick, or a
        launch-failure fallback re-attempt) still runs them here as before.
        """
        resolution = snapshot.resolution()
        if not precondition_confirmed:
            self._companion().prepare_managed(snapshot)
            self._managed_runtime().validate(resolution.registration, snapshot.runtimes)
        launched = self._companion().launch(resolution, fingerprint=snapshot.fingerprint)
        rid = resolution.registration["id"]
        self._units[rid] = ManagedUnit(
            registration_id=rid,
            kind=RegistrationKind.PLUGIN_COMPANION,
            fingerprint=snapshot.fingerprint,
            registration=resolution.registration,
            companion_resolution=resolution,
            proc=launched.process,
            started_at=self.clock(),
        )
        getattr(summary, "recovered" if launched.recovered else bucket).append(rid)

    def _reconcile_transition_groups(
        self, desired: dict[str, dict], summary: ReconcileSummary, *, deadline: float
    ) -> set[str]:
        grouped: dict[str, list[str]] = {}
        for rid, registration in desired.items():
            if (
                registration.get("kind") == RegistrationKind.PLUGIN_COMPANION
                and registration.get("spec", {}).get("managed_runtime")
                and self._transition_group(registration) is not None
            ):
                grouped.setdefault(self._transition_group(registration) or "", []).append(rid)
        handled: set[str] = set()
        for group_id, members in sorted(grouped.items()):
            rids = tuple(sorted(members))
            try:
                current = self._selected_group_snapshots(group_id, rids)
                if current is None:
                    continue
                selected, rollback, pending = current
                desired_snapshots = {
                    rid: self._desired_managed_snapshot(rid, desired[rid]) for rid in rids
                }
                wants_transition = any(
                    self._managed_transition_key(
                        (
                            self._companion_resolutions[rid].registration
                            if rid in self._companion_resolutions
                            else desired[rid]
                        )
                    )
                    != self._managed_transition_key(selected[rid].resolution().registration)
                    for rid in rids
                )
                if pending is None:
                    if (
                        not wants_transition
                        and all(snapshot is not None for snapshot in desired_snapshots.values())
                        and not any(
                            desired_snapshots[rid].fingerprint != selected[rid].fingerprint
                            for rid in rids
                        )
                    ):
                        continue
                    handled.update(rids)
                    if not all(snapshot is not None for snapshot in desired_snapshots.values()):
                        continue
                    target = {
                        rid: desired_snapshots[rid]  # type: ignore[index]
                        for rid in rids
                    }
                else:
                    target = pending
                    handled.update(rids)
                if pending is not None:
                    if not self._restore_transition_group(
                        group_id, selected, rollback, summary, deadline=deadline
                    ):
                        summary.skipped.extend(rids)
                    continue
                blocked = False
                for rid in rids:
                    failure = self._managed_launch_failures.get(rid)
                    if (
                        failure is not None
                        and failure[0] == target[rid].fingerprint
                        and self.clock() < failure[1]
                    ):
                        blocked = True
                        break
                if blocked:
                    continue
                for rid in rids:
                    if not self._rollback_allowed(selected[rid], desired[rid]):
                        raise CompanionError(
                            "managed transition group rollback authority changed"
                        )
                # Off-thread and bounded, like _poll_managed_validate's own
                # non-grouped counterpart -- a slow validate() (e.g. waiting
                # on the same interprocess lock a concurrent materialize()
                # build holds) for one group member must not block the
                # reconcile thread. Unlike the non-grouped path, ALL members
                # must confirm before committing (a transition group is an
                # atomic unit): if any is still pending, defer the whole
                # group this tick and retry next tick, rather than commit a
                # partially-validated transition. Every member is polled
                # unconditionally (a plain list comprehension, not `any(...)`
                # over a generator) so a member found pending never short-
                # circuits submission of the members after it -- otherwise
                # those later members would never even start their own
                # off-thread check this tick, indefinitely postponing the
                # whole group behind whichever member happens to be checked
                # first.
                validated = [
                    self._poll_managed_validate(rid, target[rid], deadline)
                    for rid in rids
                ]
                if any(result is None for result in validated):
                    continue
                next_rollback = (
                    dict(selected)
                    if any(
                        target[rid].fingerprint != selected[rid].fingerprint
                        for rid in rids
                    )
                    else rollback
                )
                self._write_transition_group(
                    group_id,
                    selected=selected,
                    rollback=rollback,
                    pending=target,
                )
                try:
                    for rid in rids:
                        if target[rid].fingerprint == selected[rid].fingerprint:
                            continue
                        unit = self._units.get(rid)
                        crashed = (
                            unit is not None
                            and unit.proc is None
                            and unit.fingerprint == target[rid].fingerprint
                        )
                        if unit is not None and not self._stop(rid):
                            raise CompanionIndeterminate(
                                f"managed transition group member {rid} could not stop"
                            )
                        self._launch_managed(
                            target[rid],
                            summary,
                            bucket=(
                                "revived"
                                if crashed
                                else "restarted" if unit is not None else "started"
                            ),
                            precondition_confirmed=True,
                        )
                except (CompanionError, CompanionIndeterminate, ManagedRuntimeError, OSError) as exc:
                    delay = max(self.restart_backoff, self.poll_interval)
                    for rid in rids:
                        self._managed_launch_failures[rid] = (
                            target[rid].fingerprint,
                            self.clock() + delay,
                        )
                    log.error(
                        "managed transition group %s launch failed: %s", group_id, exc
                    )
                    if not self._restore_transition_group(
                        group_id, selected, rollback, summary, deadline=deadline
                    ):
                        summary.skipped.extend(rids)
                    continue
                self._write_transition_group(
                    group_id,
                    selected=target,
                    rollback=next_rollback,
                    pending=None,
                )
                for rid in rids:
                    self._managed_launch_failures.pop(rid, None)
            except (
                CompanionError,
                CompanionIndeterminate,
                ManagedRuntimeError,
                OSError,
                ValueError,
                subprocess.SubprocessError,
            ) as exc:
                log.error(
                    "managed transition group %s cannot safely reconcile: %s",
                    group_id,
                    exc,
                )
                handled.update(rids)
                summary.skipped.extend(rids)
        return handled

    def _prune_probe_inflight(self) -> None:
        self._probe_inflight = {f for f in self._probe_inflight if not f.done()}

    def _poll_managed_health(
        self, rid: str, snapshot: ManagedLaunchSnapshot, deadline: float
    ) -> bool | None:
        """Poll (or start) an off-thread health probe for a running managed unit.

        Waits only until ``deadline`` (a shared, monotonic-clock deadline for
        the *whole* calling loop, not a fresh budget per rid -- see
        ``_reconcile_managed``/``reconcile_once``) for a result -- long
        enough that the common case (a healthy companion, no contention)
        still resolves within this same call, exactly as a direct synchronous
        call would. Returns ``None`` once that shared deadline is reached
        with the probe still running (or its previous result raised) -- the
        caller must never block further, so a genuinely stuck or
        indeterminate probe only ever delays *this* rid's own next decision
        (plus, at most, whatever of the shared budget is still left when its
        turn comes), never accumulating N-times-the-budget across every
        other companion this loop still has to reconcile.

        Tracked by ``(rid, snapshot.fingerprint)``, not ``rid`` alone: if the
        registration changes while an old probe is still blocked, that stale
        probe's eventual result must never be applied to the new snapshot
        (a launch already superseded it) -- a fresh probe starts instead,
        exactly mirroring ``_poll_managed_validate``'s own staleness guard.
        A probe can't be cancelled once running, so an abandoned one (from a
        superseded snapshot, or one whose result already propagated) is
        still tracked in ``_probe_inflight`` until it actually finishes --
        that is what ``shutdown()`` drains, independent of whichever rid, if
        any, still cares about its result.
        """
        self._prune_probe_inflight()
        tracked = self._managed_health_futures.get(rid)
        if tracked is not None and tracked[0] != snapshot.fingerprint:
            self._managed_health_futures.pop(rid, None)
            tracked = None
        if tracked is None:
            future = self._runtime_pool().submit(
                self._companion().health, snapshot.resolution()
            )
            self._probe_inflight.add(future)
            self._managed_health_futures[rid] = (snapshot.fingerprint, future)
        else:
            future = tracked[1]
        try:
            result = future.result(timeout=_remaining(deadline))
        except _FUTURE_TIMEOUT:
            return None
        except (CompanionError, CompanionIndeterminate, OSError) as exc:
            self._managed_health_futures.pop(rid, None)
            log.warning("managed companion %s health is indeterminate: %s", rid, exc)
            return None
        except BaseException:
            # Any other exception still must release this rid's tracking
            # entry before propagating -- otherwise every subsequent poll
            # would re-raise the same cached (already-completed) future's
            # exception forever, permanently blocking a retry.
            self._managed_health_futures.pop(rid, None)
            raise
        self._managed_health_futures.pop(rid, None)
        return result

    def _poll_managed_validate(
        self, rid: str, snapshot: ManagedLaunchSnapshot, deadline: float
    ) -> bool | None:
        """Poll (or start) off-thread ``prepare_managed()``/``validate()``.

        Waits only until ``deadline`` (see ``_poll_managed_health``'s
        docstring -- a shared deadline for the whole calling loop, never a
        fresh budget per rid) for the result -- long enough that the common
        case (an uncontended lock) still resolves within this same call,
        exactly as a direct synchronous call would. Returns ``True`` once
        validation has succeeded for this exact snapshot, or ``None`` once
        the shared deadline is reached with the check still in flight (a
        fresh check is submitted only if none is already running for this
        snapshot's fingerprint -- a stale check for a since-superseded one is
        dropped from this rid's own tracking, but -- like
        ``_poll_managed_health`` -- stays in ``_probe_inflight`` until it
        actually finishes, since it can't be cancelled and shutdown() still
        needs to drain it). A failure re-raises the original exception once
        the check completes (after releasing this rid's tracking entry, so a
        retry is possible), so the caller's existing per-rid error handling
        (backoff bookkeeping, authority invalidation) applies unchanged --
        only *when* that handling runs is ever deferred, never *whether*.
        This only ever routes the two potentially slow calls (validate() can
        wait on the same interprocess lock a concurrent materialize() build
        holds) off the reconcile thread, so one companion's own slow/stuck
        validation never accumulates delay on top of every other companion's
        own check in the same loop.
        """
        self._prune_probe_inflight()
        tracked = self._managed_validate_futures.get(rid)
        if tracked is not None and tracked[0] != snapshot.fingerprint:
            self._managed_validate_futures.pop(rid, None)
            tracked = None
        if tracked is None:
            def _check() -> None:
                self._companion().prepare_managed(snapshot)
                self._managed_runtime().validate(
                    snapshot.resolution().registration, snapshot.runtimes
                )

            future = self._runtime_pool().submit(_check)
            self._probe_inflight.add(future)
            self._managed_validate_futures[rid] = (snapshot.fingerprint, future)
        else:
            future = tracked[1]
        try:
            future.result(timeout=_remaining(deadline))
        except _FUTURE_TIMEOUT:
            return None
        except BaseException:
            self._managed_validate_futures.pop(rid, None)
            raise
        self._managed_validate_futures.pop(rid, None)
        return True

    def _poll_managed_recovery_validate(
        self, rid: str, previous: ManagedLaunchSnapshot, deadline: float
    ) -> bool | None:
        """Bounded-poll sibling of ``_poll_managed_validate`` for the
        ``_managed_uncertain`` selected-state recovery path's own precondition
        check (``recover_live()`` is only attempted once this validate()
        succeeds). Kept separate from ``_managed_validate_futures`` since
        recovery never calls ``prepare_managed()`` the way a (re)launch does
        -- otherwise identical contract: never blocks past the shared
        per-loop ``deadline``, drops a stale check for a superseded
        fingerprint, and keeps every submitted future in ``_probe_inflight``
        for ``shutdown()`` regardless of whether this rid still references it.
        """
        self._prune_probe_inflight()
        tracked = self._managed_recovery_validate_futures.get(rid)
        if tracked is not None and tracked[0] != previous.fingerprint:
            self._managed_recovery_validate_futures.pop(rid, None)
            tracked = None
        if tracked is None:
            future = self._runtime_pool().submit(
                self._managed_runtime().validate,
                previous.resolution().registration,
                previous.runtimes,
            )
            self._probe_inflight.add(future)
            self._managed_recovery_validate_futures[rid] = (previous.fingerprint, future)
        else:
            future = tracked[1]
        try:
            future.result(timeout=_remaining(deadline))
        except _FUTURE_TIMEOUT:
            return None
        except BaseException:
            self._managed_recovery_validate_futures.pop(rid, None)
            raise
        self._managed_recovery_validate_futures.pop(rid, None)
        return True

    def _reconcile_managed(
        self,
        desired: dict[str, dict],
        summary: ReconcileSummary,
        *,
        skip: set[str] | None = None,
        deadline: float | None = None,
    ) -> set[str]:
        """Keep prior launch authority separate from each prepared replacement."""
        # One shared deadline for every companion this call reconciles (not a
        # fresh budget per rid): a slow companion's own check only ever
        # consumes part of this one window, so a later companion in the same
        # call waits at most whatever is left of it, never the full budget
        # again -- the loop's total added delay is bounded by the budget
        # once, not multiplied by however many companions are processed.
        # Callers reconciling more than this phase alone in the same tick
        # (see reconcile_once(), which also has an unmanaged-companion health
        # phase) must pass the SAME deadline they use there, so the whole
        # tick shares one budget rather than one per phase.
        if deadline is None:
            deadline = time.monotonic() + self._reconcile_call_budget
        all_managed_runtime = {
            rid for rid, reg in desired.items()
            if reg.get("kind") == RegistrationKind.PLUGIN_COMPANION
            and reg.get("spec", {}).get("managed_runtime")
        }
        managed = all_managed_runtime - (skip or set())
        # Cleanup purges by the FULL managed-runtime set, not just this
        # call's own `managed` (which excludes `skip`): a transition-group
        # member is also a managed_runtime companion, just reconciled by
        # _reconcile_transition_groups instead of the per-rid loop below --
        # purging by `managed` alone would immediately wipe out the very
        # entries that call just set moments earlier in this same tick
        # (see _poll_managed_validate's shared tracking dicts), forcing a
        # redundant resubmission next tick instead of reusing the in-flight
        # check.
        for rid in set(self._managed_launch_failures) - all_managed_runtime:
            self._managed_launch_failures.pop(rid, None)
        for rid in set(self._managed_health_futures) - all_managed_runtime:
            self._managed_health_futures.pop(rid, None)
        for rid in set(self._managed_validate_futures) - all_managed_runtime:
            self._managed_validate_futures.pop(rid, None)
        for rid in set(self._managed_recovery_validate_futures) - all_managed_runtime:
            self._managed_recovery_validate_futures.pop(rid, None)
        for rid in managed:
            registration = desired[rid]
            authority = companion_authority_fingerprint(registration)
            unit = self._units.get(rid)
            previous = (
                unit.companion_resolution.managed_snapshot
                if unit is not None and unit.companion_resolution is not None
                else None
            )
            try:
                if (
                    unit is not None and unit.companion_resolution is not None
                    and unit.proc is not None and unit.proc.poll() is not None
                ):
                    self._companion().retire_crashed(unit.companion_resolution, unit.proc)
                    unit.proc = None
                if previous is None and unit is None:
                    previous = self._companion().selected_managed(rid)
                if previous is not None and not self._rollback_allowed(previous, registration):
                    previous = None
                    if unit is not None:
                        if not self._stop(rid):
                            summary.skipped.append(rid)
                            continue
                        summary.stopped.append(rid)
                        unit = None
                    self._companion().retire_managed(rid)

                if rid in self._managed_uncertain:
                    if unit is None and previous is not None and previous.authority == authority:
                        if self._poll_managed_recovery_validate(rid, previous, deadline) is None:
                            continue
                        recovered = self._companion().recover_live(previous)
                        if recovered is not None:
                            resolution = previous.resolution()
                            self._units[rid] = ManagedUnit(
                                registration_id=rid, kind=RegistrationKind.PLUGIN_COMPANION,
                                fingerprint=previous.fingerprint, registration=resolution.registration,
                                companion_resolution=resolution, proc=recovered.process,
                                started_at=self.clock(),
                            )
                            summary.recovered.append(rid)
                    continue

                # Recover selected state before considering a new provider environment
                # or materializer result. Recovery never runs a configuration provider.
                failure = self._managed_launch_failures.get(rid)
                if (
                    unit is None and previous is not None and previous.authority == authority
                    and failure is None
                ):
                    if self._poll_managed_recovery_validate(rid, previous, deadline) is None:
                        continue
                    try:
                        self._launch_managed(
                            previous, summary, bucket="started", precondition_confirmed=True
                        )
                    except (CompanionError, CompanionIndeterminate, ManagedRuntimeError, OSError) as exc:
                        log.error("managed companion %s selected recovery failed: %s", rid, exc)
                        failure = (
                            previous.fingerprint,
                            self.clock() + max(self.restart_backoff, self.poll_interval),
                        )
                        self._managed_launch_failures[rid] = failure
                    else:
                        continue

                if self._managed_runtime_authorities.get(rid) != authority:
                    summary.skipped.append(rid)
                    continue
                snapshot = ManagedLaunchSnapshot.capture(
                    self._companion_resolutions[rid], self._managed_runtime_results[rid]
                )
                if failure is not None and failure[0] == snapshot.fingerprint and self.clock() < failure[1]:
                    continue
                if unit is not None and unit.fingerprint == snapshot.fingerprint:
                    if unit.dead:
                        continue
                    if unit.proc is not None and unit.proc.poll() is None:
                        healthy = self._poll_managed_health(rid, snapshot, deadline)
                        if healthy is None:
                            # Probe still in flight (or just submitted) --
                            # never block on it; treat as healthy for this
                            # tick and re-check next time, so a stuck probe
                            # only delays *this* rid's own next decision, not
                            # reconciliation of any other companion.
                            continue
                        if healthy is not False:
                            continue
                        summary.unhealthy.append(rid)
                    else:
                        if self.max_restarts is not None and unit.restarts >= self.max_restarts:
                            unit.dead = True
                            continue
                        if self.clock() < unit.restart_after:
                            continue

                # Both validation and immutable snapshot construction precede
                # retirement -- run off-thread (validate() can wait on the same
                # interprocess lock a slow materialize() build holds) so a
                # stuck validation only stalls this rid, never any other
                # companion's own reconciliation this or later ticks.
                if self._poll_managed_validate(rid, snapshot, deadline) is None:
                    continue
                crashed = (
                    unit is not None and unit.proc is None
                    and unit.fingerprint == snapshot.fingerprint
                )
                restarts = unit.restarts + 1 if crashed else 0
                if unit is not None and not self._stop(rid):
                    summary.skipped.append(rid)
                    continue
                try:
                    self._launch_managed(
                        snapshot, summary,
                        bucket="revived" if crashed else "restarted" if unit else "started",
                        precondition_confirmed=True,
                    )
                except (CompanionError, CompanionIndeterminate, ManagedRuntimeError, OSError) as exc:
                    self._managed_launch_failures[rid] = (
                        snapshot.fingerprint, self.clock() + max(self.restart_backoff, self.poll_interval),
                    )
                    log.error("managed companion %s launch failed: %s", rid, exc)
                    summary.skipped.append(rid)
                    if previous is not None and previous.fingerprint != snapshot.fingerprint:
                        # Same bounded/off-thread precondition as the primary
                        # launch above -- a rollback to the last-known-good
                        # snapshot must not itself block the reconcile thread
                        # on a contended lock either.
                        if self._poll_managed_validate(rid, previous, deadline) is not None:
                            self._launch_managed(
                                previous, summary, bucket="revived", precondition_confirmed=True
                            )
                    continue
                self._managed_launch_failures.pop(rid, None)
                launched_unit = self._units[rid]
                launched_unit.restarts = restarts
                if crashed:
                    launched_unit.restart_after = self.clock() + self.restart_backoff
            except (
                CompanionError, CompanionIndeterminate, ManagedRuntimeError,
                OSError, ValueError, subprocess.SubprocessError,
            ) as exc:
                log.error("managed companion %s cannot safely reconcile: %s", rid, exc)
                summary.skipped.append(rid)
                if isinstance(exc, (ManagedRuntimeError, CompanionError)):
                    self._managed_runtime_authorities.pop(rid, None)
                    self._managed_runtime_results.pop(rid, None)
                    self._managed_runtime_failures[rid] = (
                        authority, self.clock() + max(self.poll_interval, 5.0), 1,
                    )
        return managed

    def _spec_dir(self) -> Path:
        from .config import run_dir

        scope = supervisor_lease_scope(self.machine, self.env)
        slug = scope.replace(":", "-")
        d = Path(run_dir()) / "supervisor" / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _materializer(self, reg: dict) -> Callable[[str, dict], str]:
        """A per-registration spec writer: persist an inline spec dict to a stable
        file under the run dir so the kind's runtime (a subprocess) can read it.

        The path is deterministic per (registration id, name), so a restart
        rewrites the same file rather than leaking new ones.
        """
        rid = reg["id"]

        def materialize(name: str, payload: dict) -> str:
            safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in rid)
            path = self._spec_dir() / f"{safe}.{name}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return str(path)

        return materialize

    def _start(self, reg: dict, summary: ReconcileSummary, *, bucket: str) -> None:
        rid = reg["id"]
        resolution = self._companion_resolutions.get(rid)
        if reg.get("kind") == RegistrationKind.PLUGIN_COMPANION:
            if resolution is None:
                summary.skipped.append(rid)
                return
            try:
                launched = self._companion().launch(
                    resolution, fingerprint=_spec_fingerprint(reg)
                )
            except (CompanionError, CompanionIndeterminate, OSError) as exc:
                log.error("failed to launch companion %s: %s", rid, exc)
                summary.skipped.append(rid)
                return
            proc = launched.process
        else:
            try:
                cmd = build_command(
                    reg, python=self._own_python(), materialize=self._materializer(reg),
                )
            except UnsupportedKind as exc:
                log.warning("skipping registration %s: %s", rid, exc)
                summary.skipped.append(rid)
                return
            try:
                proc = self.launcher.launch(reg, cmd)
            except OSError as exc:  # pragma: no cover -- launch failure is environmental
                log.error("failed to launch registration %s: %s", rid, exc)
                summary.skipped.append(rid)
                return
        existing = self._units.get(rid)
        restarts = existing.restarts if existing else 0
        self._units[rid] = ManagedUnit(
            registration_id=rid,
            kind=reg.get("kind", ""),
            fingerprint=_spec_fingerprint(reg),
            registration=reg,
            companion_resolution=resolution,
            proc=proc,
            started_at=self.clock(),
            restarts=restarts,
        )
        if reg.get("kind") == RegistrationKind.PLUGIN_COMPANION and launched.recovered:
            summary.recovered.append(rid)
        else:
            getattr(summary, bucket).append(rid)

    def _poll_unmanaged_health(
        self, rid: str, resolution: CompanionResolution | None, deadline: float
    ) -> bool | None:
        """Non-managed sibling of ``_poll_managed_health`` (see its docstring):
        a stuck or indeterminate probe subprocess for one companion must never
        block crash-revival of any other unit in this or later ticks, beyond
        the shared per-loop deadline. Also shares its ``_probe_inflight``
        tracking, so a probe abandoned by ``_stop()`` (unit removed/
        restarted while its probe was still running) still gets drained by
        ``shutdown()``.
        """
        self._prune_probe_inflight()
        future = self._unmanaged_health_futures.get(rid)
        if future is None:
            future = self._runtime_pool().submit(self._companion().health, resolution)
            self._probe_inflight.add(future)
            self._unmanaged_health_futures[rid] = future
        try:
            result = future.result(timeout=_remaining(deadline))
        except _FUTURE_TIMEOUT:
            return None
        except (
            CompanionError,
            CompanionIndeterminate,
            OSError,
            subprocess.SubprocessError,
        ) as exc:
            self._unmanaged_health_futures.pop(rid, None)
            log.warning(
                "companion %s health is indeterminate; keeping it: %s", rid, exc
            )
            return None
        except BaseException:
            self._unmanaged_health_futures.pop(rid, None)
            raise
        self._unmanaged_health_futures.pop(rid, None)
        return result

    def _stop(self, rid: str) -> bool:
        unit = self._units.get(rid)
        if unit is None:
            return False
        proc = unit.proc
        if proc is not None and unit.kind == RegistrationKind.PLUGIN_COMPANION:
            try:
                self._companion().stop(unit.companion_resolution, proc)
            except (CompanionError, CompanionIndeterminate, OSError, subprocess.SubprocessError):
                if (
                    unit.companion_resolution is not None
                    and unit.companion_resolution.managed_snapshot is not None
                ):
                    log.exception("cannot confirm retirement of managed companion %s", rid)
                    return False
                log.exception("error stopping companion %s; forcing retirement", rid)
                with contextlib.suppress(
                    CompanionError,
                    CompanionIndeterminate,
                    OSError,
                    subprocess.SubprocessError,
                ):
                    self._companion().retire_crashed(
                        unit.companion_resolution, proc
                    )
        elif proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:  # pragma: no cover -- best-effort teardown
                log.exception("error stopping registration %s", rid)
        self._units.pop(rid, None)
        self._unmanaged_health_futures.pop(rid, None)
        self._managed_health_futures.pop(rid, None)
        self._managed_validate_futures.pop(rid, None)
        self._managed_recovery_validate_futures.pop(rid, None)
        return True

    # -- reconcile -----------------------------------------------------------

    def reconcile_once(self) -> ReconcileSummary:
        """Bring the running set in line with the registry, once.

        Order: stop units that vanished or were paused; restart units whose spec
        changed; revive crashed units (after a backoff, up to ``max_restarts``);
        start newly-registered units. Returns a summary of what changed.
        """
        summary = ReconcileSummary()
        desired = self._desired()
        summary.deduplicated = list(self._deduplicated)
        summary.conflicts = list(self._conflicts)
        # One shared deadline for every off-thread health/validate probe this
        # whole tick submits -- both the managed-companion phase below and
        # the unmanaged-companion crash-revival phase further down -- so a
        # slow companion in one phase can't add its own separate budget on
        # top of a slow companion in the other, doubling the tick's worst
        # case. See _reconcile_managed's own docstring for the per-call
        # sharing rationale this extends across phases.
        reconcile_deadline = time.monotonic() + self._reconcile_call_budget

        # 1. stop units no longer desired (removed or paused)
        for rid in list(self._units):
            if rid not in desired:
                unit = self._units[rid]
                if self._stop(rid):
                    summary.stopped.append(rid)
                    if (
                        unit.companion_resolution is not None
                        and unit.registration.get("spec", {}).get("managed_runtime")
                        and rid not in self._managed_uncertain
                    ):
                        self._companion().forget_managed(rid)

        # 2. restart units whose definition changed
        grouped = self._reconcile_transition_groups(desired, summary, deadline=reconcile_deadline)
        managed = self._reconcile_managed(desired, summary, skip=grouped, deadline=reconcile_deadline)
        self._cleanup_managed_runtimes()
        for rid, reg in desired.items():
            if rid in managed or rid in grouped:
                continue
            unit = self._units.get(rid)
            if unit is not None and _spec_fingerprint(reg) != unit.fingerprint:
                if self._stop(rid):
                    if unit.registration.get("spec", {}).get("managed_runtime"):
                        self._companion().forget_managed(rid)
                    self._start(reg, summary, bucket="restarted")

        # 3. revive crashed units (backoff-gated, cap-bounded)
        now = self.clock()
        # Shared deadline for this pass -- the SAME one computed at the top
        # of reconcile_once(), not a fresh one: see that deadline's own
        # comment for why a separate budget per phase would let one slow
        # companion here compound with one slow companion in the managed
        # phase above, doubling this tick's worst case.
        health_deadline = reconcile_deadline
        for rid, reg in desired.items():
            if rid in managed or rid in grouped:
                continue
            unit = self._units.get(rid)
            if unit is None or unit.proc is None:
                continue
            if (
                unit.kind == RegistrationKind.PLUGIN_COMPANION
                and unit.proc.poll() is None
            ):
                healthy = self._poll_unmanaged_health(
                    rid, unit.companion_resolution, health_deadline
                )
                if healthy is False:
                    summary.unhealthy.append(rid)
                    self._stop(rid)
                    self._start(reg, summary, bucket="restarted")
                    continue
                if healthy is None:
                    # Probe just submitted or still in flight -- never block;
                    # treat as healthy for THIS tick and re-check next time --
                    # UNLESS the process has already exited in the meantime,
                    # since a probe that will never resolve for an already-
                    # exited process (e.g. a socket-based check that just
                    # hangs) must not permanently suppress crash-revival by
                    # deferring forever. Fall through to the existing crash
                    # handling below in that case.
                    if unit.proc.poll() is None:
                        continue
            if unit.proc.poll() is None:
                continue  # still running
            # crashed
            if unit.kind == RegistrationKind.PLUGIN_COMPANION:
                try:
                    self._companion().retire_crashed(
                        unit.companion_resolution, unit.proc
                    )
                except (
                    CompanionError,
                    CompanionIndeterminate,
                    OSError,
                    subprocess.SubprocessError,
                ) as exc:
                    log.error(
                        "could not retire crashed companion %s tree: %s",
                        rid,
                        exc,
                    )
                    summary.skipped.append(rid)
                    continue
            if self.max_restarts is not None and unit.restarts >= self.max_restarts:
                log.error(
                    "registration %s exceeded max restarts (%d); leaving stopped",
                    rid, self.max_restarts,
                )
                unit.dead = True
                unit.proc = None
                summary.skipped.append(rid)
                continue
            if unit.restart_after and now < unit.restart_after:
                continue  # still in backoff
            unit.restarts += 1
            self._units.pop(rid, None)
            # Any in-flight health probe belonged to the crashed process;
            # never let it linger and have its (stale) result silently
            # applied to the freshly-revived one on a later tick -- it stays
            # tracked in _probe_inflight for shutdown() regardless.
            self._unmanaged_health_futures.pop(rid, None)
            self._start(reg, summary, bucket="revived")
            revived = self._units.get(rid)
            if revived is not None:
                revived.restarts = unit.restarts
                revived.restart_after = now + self.restart_backoff

        # 4. start newly-registered units
        for rid, reg in desired.items():
            if rid in managed or rid in grouped:
                continue
            if rid not in self._units:
                self._start(reg, summary, bucket="started")

        def _alive(u: ManagedUnit) -> bool:
            return u.proc is not None and u.proc.poll() is None

        summary.running = sorted(
            rid for rid, u in self._units.items() if not u.dead and _alive(u)
        )
        summary.backing_off = sorted(
            rid for rid, u in self._units.items() if not u.dead and not _alive(u)
        )
        summary.dead = sorted(rid for rid, u in self._units.items() if u.dead)
        return summary

    # -- serve loop ----------------------------------------------------------

    def _build_lock(self) -> Any:
        from .config import run_dir
        from .single_instance import SingleInstance, lock_path_for

        scope = supervisor_lease_scope(self.machine, self.env)
        return SingleInstance(lock_path_for(run_dir(), scope))

    def acquire_singleton(self) -> bool:
        """Win the single-instance election for this (machine, env), or stand down.

        Uses a crash-safe **OS lock file** over a ``supervisor:<machine>:<env>``
        scope: the kernel releases the lock automatically if the daemon dies, so a
        restart reacquires cleanly, while a *live* second daemon is refused
        (returns ``False``) and must NOT run -- the *one supervisor per
        machine-and-environment* guarantee, without a crash leaving a permanent
        lock.
        """
        if self._lock is None:
            self._lock = self._build_lock()
        return bool(self._lock.acquire())

    def release_singleton(self) -> None:
        """Release this daemon's single-instance lock (best-effort)."""
        if self._lock is None:
            return
        try:
            self._lock.release()
        except Exception:  # pragma: no cover -- best-effort
            log.exception("error releasing supervisor lock")

    def _await_runtime_pool_idle(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for in-flight managed runtime work.

        Returns ``True`` once every tracked materialize()/cleanup() future has
        finished (or none were in flight), ``False`` if any is still running
        when the timeout elapses. Uses ``_managed_runtime_inflight`` (not
        ``_managed_runtime_futures``) for materialize() futures: a withdrawn
        or changed registration's future is cancelled and dropped from the
        latter even when still actually running (cancel() is a no-op once a
        task has started), so only the former is guaranteed to keep tracking
        it until it truly completes.
        """
        pending = list(self._managed_runtime_inflight)
        if self._managed_cleanup_future is not None:
            pending.append(self._managed_cleanup_future)
        if not pending:
            return True
        _done, not_done = _wait_futures(pending, timeout=timeout)
        return not not_done

    def shutdown(self) -> bool:
        """Wind down every running unit (best-effort).

        Returns ``True`` once every tracked in-flight materialize/cleanup/
        probe future actually finished before the bounded grace window
        elapsed, ``False`` if any remained pending when this method
        proceeded to the non-blocking pool shutdown below (this method's
        own return is never itself held up past ``_SHUTDOWN_GRACE_SECONDS``
        either way -- ``False`` only flags that a worker thread is still
        alive doing real work). Callers that are about to let the process
        exit and can prove nothing else depends on this daemon still being
        up (see ``serve()``'s self-update-triggered exit path) may treat
        ``False`` as licence to force-exit rather than let a stuck worker
        thread delay interpreter shutdown -- see the note there for why a
        plain ``shutdown(wait=False)`` cannot itself guarantee that: Python's
        ``concurrent.futures.thread`` module registers a process-wide
        ``atexit`` hook that joins every live ``ThreadPoolExecutor`` worker
        thread at interpreter exit regardless, so a genuinely stuck health/
        validate call (e.g. still waiting on the same interprocess lock a
        slow materialize() build holds, up to its own multi-minute ceiling)
        can otherwise delay actual process exit for as long as that
        operation's own ceiling allows.
        """
        for rid in list(self._units):
            self._stop(rid)
        # Give any in-flight materialize()/cleanup() task -- and any
        # in-flight health/validate probe, whether it belongs to the
        # current owned executor or one a previous failed self-update
        # handoff already retired (see _maybe_self_update; a retired
        # executor's own futures stay in _probe_inflight/
        # _managed_runtime_inflight regardless of which pool submitted
        # them) -- ONE shared, bounded grace window (long enough for the
        # common near-finished case), so THIS method's own return is never
        # held up indefinitely. This runs even when no *current* executor
        # exists (only retired ones remain): gating the whole wait on a
        # live self._runtime_executor would silently skip draining a
        # genuinely still-running retired worker and falsely report a
        # clean drain. A single shared wait (not per-executor) keeps the
        # total grace bounded by _SHUTDOWN_GRACE_SECONDS once. Self-update
        # itself requires a *real* drain-or-defer decision (see
        # _maybe_self_update's own _await_runtime_pool_idle call before
        # ever reaching this method) for materialize()/cleanup()
        # specifically, since a build still running here would otherwise
        # race the successor it hands off to; a stuck health or validate
        # probe never gates that decision (only its own rid's
        # reconciliation, per _poll_managed_health/_poll_managed_validate),
        # so it is only ever drained here, best-effort -- reflected in this
        # method's own return value rather than a reason to defer
        # self-update.
        self._prune_probe_inflight()
        pending = set(self._managed_runtime_inflight) | self._probe_inflight
        if self._managed_cleanup_future is not None:
            pending.add(self._managed_cleanup_future)
        # Idempotent re-entry: a future already covered by a prior grace
        # wait (e.g. serve()'s finally re-calling shutdown() after
        # _maybe_self_update already did, during the same handoff) is not
        # waited on again -- only a genuinely NEW future (never seen by an
        # earlier shutdown() call) can still change the drained verdict, so
        # re-waiting on the same still-stuck one twice would just double
        # the delay for no new information.
        new_pending = pending - self._shutdown_waited_futures
        if new_pending:
            _done, not_done = _wait_futures(new_pending, timeout=_SHUTDOWN_GRACE_SECONDS)
            self._shutdown_drained = self._shutdown_drained and not not_done
        self._shutdown_waited_futures |= pending
        if self._retired_runtime_executors:
            # Sweep any executor a previous failed self-update handoff
            # retired rather than silently discarded -- their own futures
            # are already covered by the drain above regardless of which
            # pool submitted them; this just re-issues shutdown() on each
            # (a no-op if already called) so a long-running daemon's own
            # accounting never loses track of how many are still pending.
            for retired in self._retired_runtime_executors:
                with contextlib.suppress(Exception):
                    retired.shutdown(wait=False, cancel_futures=True)
            self._retired_runtime_executors.clear()
        if self._runtime_executor is not None and self._owns_runtime_executor:
            self._runtime_executor.shutdown(wait=False, cancel_futures=True)
        return self._shutdown_drained

    def _maybe_self_update(self) -> bool:
        """Check for a newer installed version and hand off to it if found.

        Fail-safe and non-blocking: a no-op unless ``self_update_enabled`` and
        the poll cadence has elapsed since the last check; a staleness-check
        failure (a marker read raising, etc.) never propagates. Returns
        ``True`` once a successor daemon has been spawned and this process
        should exit; ``False`` otherwise, including when the spawn itself
        failed (in which case this daemon reclaims its singleton lease and
        keeps running rather than exiting into a void).
        """
        if not self.self_update_enabled:
            return False
        now = self.clock()
        if now - self._self_update_last_check < self.self_update_poll_interval:
            return False
        self._self_update_last_check = now
        if (
            self._self_update_last_spawn is not None
            and now - self._self_update_last_spawn < self.self_update_cooldown
        ):
            return False
        try:
            root = self._self_update_install_dir()
            target = self._self_update_stale_target(
                root, self._self_update_running_version
            )
        except Exception:
            log.debug("supervisor self-update staleness check failed", exc_info=True)
            return False
        if target is None:
            return False
        before_spawn = self._recheck_governance("pre-mutation:self-update")
        if before_spawn is not None and before_spawn.get("status") != "ready":
            log.warning(
                "supervisor self-update skipped at %s: %s (%s)",
                before_spawn.get("checkpoint"),
                before_spawn.get("reason"),
                before_spawn.get("status"),
            )
            return False
        # A managed runtime build/cleanup still in flight must fully drain
        # before handing off: the successor's _legacy_lock_handshake() only
        # samples the legacy lock once, so it cannot by itself force a wait
        # for an old-process worker that has not yet reached its own lock
        # acquisition. Rather than spawn a successor that might race an
        # old-version build still running in this process, defer this
        # self-update cycle entirely and retry on the next poll -- this is
        # not itself a spawn attempt, so it must not arm the cooldown below.
        if not self._await_runtime_pool_idle(_SHUTDOWN_GRACE_SECONDS):
            log.info(
                "supervisor self-update: deferred at %s -- a managed runtime "
                "build or cleanup is still in flight; retrying next poll "
                "rather than risk an overlapping old/new build",
                target,
            )
            return False
        self._self_update_last_spawn = now
        # Every reconcile tick already runs to completion before the next one
        # starts, so this is always a safe cutover point (unlike the
        # coordinator, there is no in-flight request to drain). Wind down
        # every managed unit and release the singleton lease *before* spawning
        # the successor, so its own `acquire_singleton()` never races this
        # daemon's still-held lock.
        drained = self.shutdown()
        self.release_singleton()
        try:
            self._self_update_spawn(target, self._self_update_argv)
        except Exception:
            log.warning(
                "supervisor self-update: failed to spawn a successor daemon "
                "at %s; reclaiming the singleton lease to stay on this version",
                target, exc_info=True,
            )
            if not self.acquire_singleton():
                log.error(
                    "supervisor self-update: spawn failed AND could not "
                    "reclaim %s -- this host now has no supervisor daemon",
                    supervisor_lease_scope(self.machine, self.env),
                )
            # shutdown() above already shut down our owned runtime executor
            # in anticipation of the handoff; since we are staying up, drop
            # the dead reference so the next _runtime_pool() call lazily
            # rebuilds a live one instead of raising on every submit(). If
            # it did not fully drain (a probe/validate worker is still
            # genuinely running), keep the old executor referenced rather
            # than silently discard it -- an unmanaged repeated-failure loop
            # would otherwise accumulate untracked live pools with no
            # further shutdown() call ever accounting for them.
            if self._owns_runtime_executor:
                if not drained and self._runtime_executor is not None:
                    self._retired_runtime_executors.append(self._runtime_executor)
                self._runtime_executor = None
            return False
        log.info(
            "supervisor self-update: detected a newer installed version at "
            "%s -- spawned a successor daemon and handing off",
            target,
        )
        return True

    def _reconnect(self) -> bool:
        """Rebuild the coordinator client by re-resolving its endpoint.

        Called when a reconcile cycle fails at the connection level -- the classic
        cause being a coordinator restart that moved its OS-assigned ephemeral
        port, leaving this daemon pointed at a dead one. Re-resolving reads the
        fresh rendezvous ``endpoint.json`` so the next tick reaches the live
        coordinator. Best-effort: a factory that itself raises leaves the old
        client in place and the loop simply retries next tick. Returns True when
        the client was rebuilt.
        """
        if self._client_factory is None:
            return False
        try:
            new_client = self._client_factory()
        except Exception:  # pragma: no cover -- re-resolve is environment-dependent
            log.exception("failed to re-resolve coordinator endpoint; will retry")
            return False
        old = self.client
        self.client = new_client
        close = getattr(old, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()
        log.info("re-resolved coordinator endpoint after a connection failure")
        return True

    def serve(
        self,
        *,
        once: bool = False,
        single_instance: bool = True,
        on_cycle: Callable[[ReconcileSummary], None] | None = None,
        on_cycle_start: Callable[[int, float], None] | None = None,
    ) -> int:
        """Run the daemon: reconcile each tick until interrupted.

        Returns ``0`` normally, ``3`` if it stood down because another daemon
        already holds this scope's singleton lease, or ``SELF_UPDATE_EXIT_CODE``
        if it handed off to a spawned successor running a newer installed
        version (see ``self_update_enabled``). With ``once`` it runs a single
        reconcile and returns (still lease-guarded). ``single_instance=False``
        skips the election (for tests / a deliberately unguarded run).

        Every cycle logs a heartbeat at both boundaries -- "cycle N starting"
        before :meth:`reconcile_once`, "cycle N finished in Xs" after -- always,
        not only when something changed. A start with no matching finish is the
        wedge signature (the process is alive but blocked inside one cycle);
        tailing the log alone reveals it instead of waiting for a stale
        heartbeat file to be noticed. ``on_cycle_start`` additionally lets a
        caller (the CLI) persist the cycle-start timestamp somewhere queryable
        (e.g. the runtime-status file) for programmatic staleness checks.
        """
        if single_instance and not self.acquire_singleton():
            log.info(
                "another supervisor daemon already holds %s; standing down",
                supervisor_lease_scope(self.machine, self.env),
            )
            return 3
        try:
            while True:
                boundary = self._recheck_governance("iteration-boundary")
                if boundary is not None and boundary.get("status") != "ready":
                    log.warning(
                        "supervisor daemon backing off at %s: %s (%s)",
                        boundary.get("checkpoint"),
                        boundary.get("reason"),
                        boundary.get("status"),
                    )
                    if once:
                        break
                    self.sleep(_GOVERNANCE_BACKOFF_SECONDS)
                    continue
                try:
                    before_reconcile = self._recheck_governance("pre-mutation:reconcile")
                    if (
                        before_reconcile is not None
                        and before_reconcile.get("status") != "ready"
                    ):
                        log.warning(
                            "supervisor daemon skipped reconcile at %s: %s (%s)",
                            before_reconcile.get("checkpoint"),
                            before_reconcile.get("reason"),
                            before_reconcile.get("status"),
                        )
                        if once:
                            break
                        self.sleep(_GOVERNANCE_BACKOFF_SECONDS)
                        continue
                    self._cycle_count += 1
                    cycle_id = self._cycle_count
                    cycle_started_at = self.clock()
                    log.info("supervisor reconcile cycle %d starting", cycle_id)
                    if on_cycle_start is not None:
                        with contextlib.suppress(Exception):
                            on_cycle_start(cycle_id, cycle_started_at)
                    summary = self.reconcile_once()
                    cycle_duration = self.clock() - cycle_started_at
                    log.info(
                        "supervisor reconcile cycle %d finished in %.2fs: "
                        "running=%d backing_off=%d dead=%d",
                        cycle_id,
                        cycle_duration,
                        len(summary.running),
                        len(summary.backing_off),
                        len(getattr(summary, "dead", []) or []),
                    )
                    if on_cycle is not None:
                        on_cycle(summary)
                    if self._maybe_self_update():
                        self.self_update_triggered = True
                        break
                except KeyboardInterrupt:
                    break
                except Exception as exc:  # pragma: no cover -- never die on a blip
                    log.exception("supervisor reconcile cycle failed")
                    # A connection-level failure most likely means the coordinator
                    # restarted onto a new ephemeral port; re-resolve its endpoint
                    # so the next tick reconnects instead of wedging forever.
                    if _is_connection_error(exc):
                        self._reconnect()
                if once:
                    break
                try:
                    self.sleep(self.poll_interval)
                except KeyboardInterrupt:
                    break
        finally:
            drained = self.shutdown()
            if self._client_factory is not None:
                close = getattr(self.client, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
            if single_instance:
                self.release_singleton()
            if self.self_update_triggered and not drained:
                # A successor is already spawned and live (see
                # _maybe_self_update); this process's only remaining job is
                # to vanish. A plain return here still runs Python's normal
                # interpreter-exit machinery, which joins every live
                # ThreadPoolExecutor worker thread (see shutdown()'s
                # docstring) -- so a genuinely stuck probe/validate call
                # would otherwise delay this process's exit for as long as
                # its own underlying operation's ceiling allows, well after
                # the successor has already taken over. os._exit() bypasses
                # that atexit join entirely; safe specifically here since
                # every unit was already asked to stop above and nothing
                # else in this process depends on a cooperative return from
                # serve().
                log.warning(
                    "supervisor self-update: a probe/validate worker was "
                    "still running past the shutdown grace window; forcing "
                    "process exit rather than wait on it (the successor is "
                    "already up)",
                )
                os._exit(SELF_UPDATE_EXIT_CODE)
        if self.self_update_triggered:
            return SELF_UPDATE_EXIT_CODE
        return 0
