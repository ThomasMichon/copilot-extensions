"""CLI surface for the resident status monitor."""

from __future__ import annotations

from agent_procutil.daemon_instances import daemon_entrypoint

import argparse
import os
import sys

from . import config as cfg
from .sessions_pane_args import sweep_registered_mux_pane_args as sweep_mux_pane_args
from .status_monitor_wake import _kind_wakes_sweep, _wake_interruptible_wait

_DEFAULT_SWEEP_INTERVAL_S = 30

#: Bounded grace period a shutdown/handoff (runtime superseded, a newer
#: monitor taking ownership, or the empty-strike idle-exit path) waits for
#: an in-flight tracking-write compute to finish before closing the
#: tracking_write server anyway. Generous relative to a single verb
#: transaction's own cost (a lock/load/mutate/save, comparable to one
#: worktree_status fact), bounded so a genuinely wedged compute can never
#: block a shutdown indefinitely (2026-09-26 PR review finding).
_TRACKING_WRITE_SHUTDOWN_GRACE_S = 10.0

# Explicit shutdown grace. Supersession closes admission and waits for all
# accepted work independently; this deadline cannot force predecessor retirement.
_SELF_RETIRE_MAX_GRACE_S = 120.0


def _wait_for_tracking_write_idle(
    is_busy,
    *,
    grace_s: float = _TRACKING_WRITE_SHUTDOWN_GRACE_S,
    poll_interval_s: float = 0.1,
    now=None,
    sleep=None,
) -> None:
    """Block until ``is_busy()`` is false or ``grace_s`` elapses.

    ``is_busy`` should be a caller's combined busy predicate (e.g. this
    module's own ``_tracking_write_busy``, not ``tracking_write.
    has_inflight_write`` alone) -- a request already accepted (registered as
    a ``CoalescingServer`` subscriber) but not yet inside its ``compute()``
    call (where the in-flight counter increments) would otherwise slip past
    a check that only looked at the in-flight counter (2026-09-26 PR review
    finding).

    Extracted as its own function (rather than inlined in the shutdown
    ``finally`` block) so the wait/deadline logic itself is directly unit-
    testable without needing to drive the full ``cmd_status_monitor``
    lifecycle. ``now``/``sleep`` default to the real ``time`` module;
    overridden by tests to make the deadline/poll behavior deterministic
    without a real wall-clock wait.
    """
    import time as _time

    now = now or _time.time
    sleep = sleep or _time.sleep
    deadline = now() + grace_s
    while is_busy() and now() < deadline:
        sleep(poll_interval_s)


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "status-monitor",
        help="Resident, coalescing status tracker for every wt-* session "
        "(one process instead of one per session; mandatory, always-on "
        "infrastructure -- not operator-configurable)",
    )
    p.add_argument(
        "--interval", type=int, default=_DEFAULT_SWEEP_INTERVAL_S,
        help="Correctness backstop in seconds (default 30, min 2); pushes wake immediately",
    )
    p.add_argument("--passive", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--control-port", type=int, default=None, help=argparse.SUPPRESS)


@daemon_entrypoint("agent-worktrees-status-monitor", source=__file__, purpose="production")
def cmd_status_monitor(args: argparse.Namespace) -> int:
    """One resident, coalescing tracker for every ``wt-*`` session's status bar.

    Replaces N per-session ``status-updater`` loops with a single sweep on the
    interval. Single-active on the host via a liveness lock; idle-exits after a
    short run of sweeps with no managed session, Picker heartbeat, or recent
    list demand; self-retires when a newer runtime supersedes it.
    """
    import shutil
    import threading
    import time

    from . import classify_daemon, loop_governance, monitor_roots, mux_link, pane_reaper, registry_paths, session_catalog
    from . import locks as _locks
    from . import resident_push, status_monitor_cutover, status_monitor_runtime, status_updater_cli
    from .status_monitor_handover import MonitorHandover
    from .runtime_monitor_completion import recover as complete_publication
    from . import runtime_monitor_preparation as preparation
    from .runtime_monitor_identity import publish_lock
    from . import repository_map, tracking_write
    from . import handoff_prompt_receipt_control as receipt_control
    from . import worktree_status_daemon
    from .status_monitor_drain import DrainTrace, drain_with_diagnostics
    from .status_monitor_retirement import drain_observations, prune_retired_surfaces, surface_busy
    from .worktrees_runtime import READ_ACTION, WorktreesRuntime
    from .worktrees_projection_index import ProjectionError, ProjectionUnavailable
    from .worktrees_producer import publish_active_project
    from .allocation_link_runtime import AllocationLinkRuntime
    from .hook_ipc import HookIpcServer, HookUnavailable

    core = _core()
    mux = "psmux" if shutil.which("psmux") else ("tmux" if shutil.which("tmux") else None)
    mux_bin = (shutil.which(mux) or mux) if mux else None
    interval = (
        args.interval
        if getattr(args, "interval", None) and args.interval >= 2
        else _DEFAULT_SWEEP_INTERVAL_S
    )
    passive_mode = bool(getattr(args, "passive", False))
    if passive_mode and not preparation.register(status_monitor_runtime._aw_runtime_home(), getattr(args, "control_port", None)):
        return 0

    # Resolve sibling helpers directly while preserving test entrypoint overrides.
    lock = _core_helper("_monitor_lock_path", status_monitor_runtime._monitor_lock_path)()
    my_prefix = os.path.realpath(sys.prefix)
    my_slot = os.path.dirname(my_prefix) if os.path.basename(my_prefix) == ".venv" else my_prefix
    from .runtime_identity import PROCESS_GENERATION, PROCESS_VERSION
    token = str(os.getpid())
    activate_project_for_path = _core_helper("_activate_project_for_path", status_updater_cli._activate_project_for_path)
    load_hook_client_module = core._load_hook_client_module
    status_segment_cache_type = core._StatusSegmentCache
    resident_hook_policy_type = core._ResidentHookPolicy
    resident_lifecycle_runway = core._RESIDENT_LIFECYCLE_RUNWAY_S
    status_monitor_governance_deferred = core._StatusMonitorGovernanceDeferred
    claim_resident_lifecycle = core._claim_resident_lifecycle
    resident_hook_lock_timeout = core._resident_hook_lock_timeout
    resident_hook_should_yield = core._resident_hook_should_yield
    release_resident_lifecycle = core._release_resident_lifecycle
    runtime_superseded = _core_helper("_runtime_superseded", status_updater_cli._runtime_superseded)
    wait_for_lifecycle_priority = core._wait_for_lifecycle_priority
    classify_daemon_compute = core._classify_daemon_compute
    worktree_status_compute = core._worktree_status_compute
    shutdown_requested = threading.Event()
    shutdown_requested_since: float | None = None
    admission_closed = False
    published_lock = not passive_mode
    discovery_published = False
    sweep_active = False
    handover = MonitorHandover(passive=passive_mode)
    self_retire_confirms = 0
    self_retire_confirmations = 2
    from .runtime_monitor_retirements import ResidentRetirements

    retirements = ResidentRetirements(status_monitor_runtime._aw_runtime_home())

    def _other_current_monitor() -> bool:
        """A *different*, live monitor on a non-superseded runtime owns the host."""
        if passive_mode:
            return False
        d = _locks.read_lock(lock)
        if not (_locks.lock_is_running(d) and isinstance(d, dict)):
            return False
        if d.get("pid") == os.getpid():
            return False
        if d.get("mux") is False and mux_bin:
            return False
        op = d.get("prefix")
        identity = {"generation": d["runtime_generation"]} if d.get("runtime_generation") else {}
        return not (op and runtime_superseded(prefix=op, **identity))

    # Acquire atomic kernel exclusivity BEFORE checking lock-file metadata.
    # A read/check/write alone races concurrent cold starts, letting duplicate
    # residents survive until a periodic recheck; losers must exit before setup.
    #
    # Deliberately a SEPARATE file from `lock` (`status-monitor.lock`, the
    # metadata channel written below): `SingleInstance.acquire()` writes its
    # own pid bytes into the first 20 bytes of whatever file it locks, and
    # `_locks.write_lock()` separately replaces `lock`'s entire contents with
    # JSON metadata -- sharing one path means either write corrupts the
    # other, and on POSIX a metadata replace (a new inode at the same path)
    # strands the held flock on the OLD inode, silently defeating the gate
    # for the next contender that opens the replacement (review finding,
    # PR #5412).
    #
    # This lease covers ordinary cold starts. Installer replacement starts a
    # passive successor, then promotes its separately serialized route; it
    # never force-stops the incumbent to acquire this cold-start lease.
    _OWNERSHIP_LEASE_NAME = "status-monitor-singleton.lock"

    def _acquire_ownership_lease():
        """Acquire the atomic exclusivity lease, or return ``None`` if
        another live process already holds it."""
        from single_instance_lease import AlreadyRunningError, SingleInstance

        lease = SingleInstance(
            status_monitor_runtime._aw_runtime_home(),
            service="status-monitor",
            lock_name=_OWNERSHIP_LEASE_NAME,
        )
        try:
            lease.acquire()
            return lease
        except AlreadyRunningError:
            return None

    _lease = None
    if not passive_mode:
        _lease = _acquire_ownership_lease()
        if _lease is None:
            return 0

    if _other_current_monitor():
        return 0
    if not passive_mode:
        if not _locks.write_lock(lock, extra={"prefix": my_prefix, "mux": bool(mux_bin)}):
            # Best-effort I/O failure (`locks.write_lock()` never raises).
            # Continuing anyway would leave us believing we're the
            # published owner when no metadata actually says so -- abort
            # rather than run in that inconsistent state.
            if _lease is not None:
                _lease.release()
            return 1

    ctx_done: set[str] = set()

    def _register_unmanaged_monitor_session(sess: str, path: str | None) -> bool:
        if status_monitor_runtime._manager_owned_mapping_for_session(
            sess, managed_mux_cache=managed_mux_runtime.cache
        ):
            return False
        return _core_helper(
            "_register_session_for_monitor", status_monitor_runtime._register_session_for_monitor
        )(sess, path)

    reconciler = session_catalog.ResidentSessionReconciler(
        register_monitor_session=_register_unmanaged_monitor_session
    )
    pane_reconciler = pane_reaper.ResidentPaneReconciler(
        activate_project=activate_project_for_path
    )
    try:
        cache_ttl = float(os.environ.get("AGENT_WORKTREES_STATUS_CACHE_SECONDS", "60"))
    except ValueError:
        cache_ttl = 60.0
    config_cache_ttl = max(60.0, float(interval))
    config_cache_sessions: dict[str, cfg.ConfigCacheSession] = {}

    def _config_cache_for_project(project: str):
        session = config_cache_sessions.get(project)
        if session is None:
            session = cfg.ConfigCacheSession(ttl=config_cache_ttl)
            config_cache_sessions[project] = session
        return session

    segment_cache = status_segment_cache_type(cache_ttl)
    wake_event = threading.Event()
    repositories_runtime = repository_map.RepositoryMapRuntime(
        status_monitor_runtime._aw_runtime_home()
    )
    worktrees_runtime = WorktreesRuntime(
        status_monitor_runtime._aw_runtime_home() / "worktrees-current.sqlite3",
        wake=wake_event,
        elected=lambda: status_monitor_cutover.active_generation_for_pid(os.getpid()) is not None,
        admitted=handover.read_admitted,
    )
    resident_push.bind(wake_event, segment_cache)
    published: dict[tuple[str, str], str] = {}
    incarnations: dict[str, str] = {}
    session_projects: dict[str, str] = {}
    state_lock = threading.RLock()
    lifecycle_priority = threading.Event()
    lifecycle_count_lock = threading.Lock()
    lifecycle_count = 0
    lifecycle_claims: dict[str, float] = {}
    governance = loop_governance.LoopGovernance()

    def _allocation_owner_elected() -> bool:
        core._status_monitor_recheck(governance, "pre-mutation:allocation-link")
        return status_monitor_cutover.active_generation_for_pid(os.getpid()) is not None

    allocation_linker = AllocationLinkRuntime(
        elected=_allocation_owner_elected, context_lock=state_lock, wake=wake_event,
    )
    hook_client = load_hook_client_module()
    hook_policy = resident_hook_policy_type(hook_client)
    if hook_policy.ready():
        hook_policy.plugin_related_anchors()
    installation_context = registry_paths.installation_context()
    receipts = receipt_control.for_runtime(
        status_monitor_runtime._aw_runtime_home(), PROCESS_GENERATION,
        lambda: published_lock and not admission_closed and not shutdown_requested.is_set(),
        port=lambda: control_server.port,
    )

    def _classify_compute(kind: str, payload: dict) -> dict:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                delay_s = float(payload.get("__test_delay_s") or 0.0)
            except (TypeError, ValueError):
                delay_s = 0.0
            if delay_s > 0:
                time.sleep(delay_s)
                return {"delayed": True}
        return classify_daemon_compute(
            kind, payload,
            **({"cleanup_owner": segment_cache} if core.git_ops._windows_observation() else {}),
        )

    def _decide(kind: str, payload: dict, deadline: float) -> dict:
        if admission_closed and core.git_ops._windows_observation():
            if kind == "snapshot":
                return {"segment": segment_cache.pending_segment()}
            raise HookUnavailable
        nonlocal lifecycle_count
        is_lifecycle = kind == "sessionStart"
        provisioning_start_event = threading.Event() if is_lifecycle else None
        lifecycle_key = ""
        lifecycle_completed = False
        if is_lifecycle:
            with lifecycle_count_lock:
                lifecycle_key, claimed = claim_resident_lifecycle(payload, lifecycle_claims)
                if not claimed:
                    return {}
                lifecycle_count += 1
                lifecycle_priority.set()
        remaining = deadline - time.time()
        lock_timeout = resident_hook_lock_timeout(kind, remaining)
        try:
            if (
                remaining <= 0
                or resident_hook_should_yield(kind, lifecycle_priority)
                or not state_lock.acquire(timeout=lock_timeout)
            ):
                raise HookUnavailable
            try:
                if is_lifecycle and deadline - time.time() < resident_lifecycle_runway:
                    raise HookUnavailable
                core._status_monitor_recheck(governance, "pre-mutation:hook-response")
                result = core._resident_hook_decision(
                    kind,
                    payload,
                    segment_cache=segment_cache,
                    policy=hook_policy,
                    deadline=deadline,
                    provisioning_start_event=provisioning_start_event,
                )
                lifecycle_completed = True
                targets = hook_policy.mutation_targets(payload) if kind == "postToolUse" else None
                if _kind_wakes_sweep(kind, targets):
                    # This tool call actually invalidated one or more
                    # cached segments (or all of them, when targets is
                    # None), or this was an explicit non-postToolUse wake
                    # kind (e.g. handoffWake) -- wake the sweep loop now
                    # rather than waiting out `interval`, same as an
                    # explicit `status` push (resident_push.notify). A
                    # read-only postToolUse tool call (empty targets)
                    # never wakes.
                    wake_event.set()
                return result
            finally:
                state_lock.release()
                if provisioning_start_event is not None:
                    provisioning_start_event.set()
        except status_monitor_governance_deferred as exc:
            print(
                "resident hook refused "
                f"{kind} at {exc.result.get('checkpoint')}: "
                f"{exc.result.get('reason')} ({exc.result.get('status')})",
                file=sys.stderr,
            )
            raise HookUnavailable from exc
        finally:
            if is_lifecycle:
                with lifecycle_count_lock:
                    lifecycle_count -= 1
                    release_resident_lifecycle(
                        lifecycle_key,
                        lifecycle_claims,
                        completed=lifecycle_completed,
                    )
                    if lifecycle_count == 0:
                        lifecycle_priority.clear()

    hook_server = None
    classify_server = None
    tracking_write_server = None
    retired_hook_servers: list[HookIpcServer] = []
    retired_classify_servers = []
    retired_tracking_write_servers = []

    def _start_request_surfaces() -> bool:
        nonlocal hook_server, classify_server, tracking_write_server
        if admission_closed or not published_lock or shutdown_requested.is_set() or not handover.promotion_requested:
            return False
        if handover.generation is None:
            handover.generation = status_monitor_cutover.active_generation_for_pid(os.getpid())
        if handover.generation is None:
            return False
        retirements.refresh(cleanup=False)
        try:
            worktrees_runtime.activate()
        except (ProjectionError, ProjectionUnavailable, OSError) as exc:
            print(f"current-roster authority unavailable: {exc}", file=sys.stderr)
            return False
        repositories_runtime.activate()
        allocation_linker.activate()
        if hook_server is None and hook_policy.ready():
            try:
                hook_server = HookIpcServer(_decide)
                hook_server.start()
            except Exception:
                hook_server = None
        elif hook_server is not None:
            hook_server.open_admission()
        if classify_server is None:
            try:
                classify_server = classify_daemon.start_server(_classify_compute)
                classify_server.start()
            except Exception:
                classify_server = None
        elif classify_server is not None:
            classify_server.open_admission()
        if tracking_write_server is None:
            try:
                tracking_write._ensure_verb_modules_loaded()
                tracking_write_server = tracking_write.start_server(tracking_write.compute)
                tracking_write_server.start()
            except Exception:
                tracking_write_server = None
        elif tracking_write_server is not None:
            tracking_write_server.open_admission()
        return True

    def _close_request_surfaces() -> None:
        nonlocal hook_server, classify_server, tracking_write_server
        if hook_server is not None:
            retired_hook_servers.append(hook_server)
            hook_server.close()
            hook_server = None
        if classify_server is not None:
            retired_classify_servers.append(classify_server)
            classify_server.close()
            classify_server = None
        if tracking_write_server is not None:
            retired_tracking_write_servers.append(tracking_write_server)
            tracking_write_server.close()
            tracking_write_server = None

    def _close_request_surface_admission(reason: str = "superseded") -> None:
        """Keep degraded read routes and handler counts until custody settles."""
        keep_cleanup_reads = segment_cache.observation_busy(worktree_status_runtime)
        if hook_server is not None and not keep_cleanup_reads:
            hook_server.close_admission(reason)
        if classify_server is not None and not keep_cleanup_reads:
            classify_server.close_admission(reason)
        if tracking_write_server is not None:
            tracking_write_server.close_admission(reason)

    def _enter_drain_only_state() -> None:
        nonlocal admission_closed, published_lock
        admission_closed = True
        published_lock = False
        if core.git_ops._windows_observation():
            segment_cache.close_observation_admission()
        receipts.close_admission()
        worktrees_runtime.close_admission("draining")
        repositories_runtime.close_admission("draining")
        allocation_linker.close_admission()
        _close_request_surface_admission()
        # Stop admitting new managed-mux push observations too (a genuine
        # "new source" admission vector, distinct from worktree_status_runtime's
        # stateless per-worktree reads, which stay open -- see close_admission's
        # own docstring).
        managed_mux_runtime.close_admission()

    worktree_status_runtime = worktree_status_daemon.InProcessRuntime()
    worktree_status_runtime.start(
        _core_helper("_aw_runtime_home", status_monitor_runtime._aw_runtime_home)() / "worktree-status-cache.sqlite3",
        worktree_status_compute,
        **({"cleanup_owner": segment_cache} if core.git_ops._windows_observation() else {}),
    )

    managed_mux_runtime = mux_link.InProcessRuntime()
    managed_mux_runtime.start(
        _core_helper("_aw_runtime_home", status_monitor_runtime._aw_runtime_home)() / "managed-mux-cache.json"
    )

    def _lock_extra() -> dict:
        extra = {
            "prefix": my_prefix, "mux": bool(mux_bin),
            "runtime_generation": PROCESS_GENERATION,
            "version": PROCESS_VERSION,
            "runtime_slot": os.path.basename(my_slot),
        }
        if isinstance(installation_context, dict):
            extra.update(
                {
                    "marketplaceId": str(installation_context.get("marketplaceId") or ""),
                    "installReceipt": str(installation_context.get("installReceipt") or ""),
                    "pluginRoot": str(installation_context.get("pluginRoot") or ""),
                }
            )
        if hook_server is not None and published_lock:
            extra.update(hook_server.rendezvous())
        if classify_server is not None and published_lock:
            extra.update(classify_daemon.rendezvous_fields(classify_server))
        if tracking_write_server is not None and published_lock:
            extra.update(tracking_write.rendezvous_fields(tracking_write_server))
        if published_lock:
            extra.update(worktree_status_runtime.lock_extra())
            extra.update(managed_mux_runtime.lock_extra())
        return extra

    def _publish_lock(*, routing_held: bool = False) -> bool:
        nonlocal discovery_published
        discovery_published = publish_lock(lock, extra=_lock_extra(), routing_held=routing_held)
        return discovery_published

    def _hook_busy() -> bool:
        return surface_busy(hook_server, retired_hook_servers)

    def _classify_busy() -> bool:
        return surface_busy(classify_server, retired_classify_servers)

    def _tracking_write_busy() -> bool:
        return surface_busy(
            tracking_write_server, retired_tracking_write_servers,
        ) or tracking_write.has_inflight_write()

    def _cleanup_retired_request_surfaces() -> None:
        with state_lock:
            prune_retired_surfaces(
                retired_hook_servers, retired_classify_servers, retired_tracking_write_servers,
                tracking_write.has_inflight_write,
            )

    def _drain_busy_reasons() -> list[str]:
        reasons: list[str] = []
        if sweep_active:
            reasons.append("sweep")
        if _hook_busy():
            reasons.append("hook")
        if _classify_busy():
            reasons.append("classify")
        if _tracking_write_busy():
            reasons.append("tracking-write")
        if allocation_linker.active_count():
            reasons.append("allocation-link")
        if worktrees_runtime.feed.active_count():
            reasons.append("worktrees-feed")
        reasons.extend(worktrees_runtime.agenda_busy(control_server))
        if control_server.current_read_count():
            reasons.append("worktrees-read")
        if control_server.current_read_count(repository_map.READ_ACTION):
            reasons.append("repos-map-read")
        if repositories_runtime.health()["repositories"]["refreshing"]:
            reasons.append("repos-map-producer")
        if receipts.active_count():
            reasons.append("prompt-receipt")
        reasons.extend(segment_cache.custody_busy_reasons(worktree_status_runtime))
        return reasons

    def _drain_observations() -> dict[str, int]:
        return drain_observations(
            sweep_active=sweep_active,
            hooks=([hook_server] if hook_server is not None else []) + retired_hook_servers,
            classifiers=([classify_server] if classify_server is not None else []) + retired_classify_servers,
            writers=([tracking_write_server] if tracking_write_server is not None else []) + retired_tracking_write_servers,
            tracking_write=tracking_write, allocation_linker=allocation_linker,
            worktrees_runtime=worktrees_runtime, control_server=control_server,
            repositories_runtime=repositories_runtime, receipts=receipts, lifecycle_count=lifecycle_count,
            segment_cache=segment_cache, worktree_status_runtime=worktree_status_runtime,
        )

    control_server = status_monitor_cutover.ControlServer(
        lambda action, payload: _handle_control(action, payload),
        port=getattr(args, "control_port", None),
        stream_handle=worktrees_runtime.stream,
    )
    empty_strikes = 0
    idle_guard = None
    max_empty_strikes = 3

    from . import status_monitor_recovery_contract as guarded_recovery

    def _handle_control(action: str, payload: dict, *, routing_held: bool = False) -> dict:
        nonlocal admission_closed, published_lock
        if action == preparation.ACTION:
            with state_lock:
                if published_lock or not preparation.cancel(
                    payload, root=status_monitor_runtime._aw_runtime_home(), port=control_server.port, passive=passive_mode,
                ):
                    return {"shutdown": False}
                return _handle_control("shutdown", {})
        if action == "health":
            health = guarded_recovery.monitor_health(
                worktrees_runtime, receipts, repositories_runtime, allocations=allocation_linker,
                shutdown=shutdown_requested.is_set(), draining=admission_closed,
                published=published_lock and discovery_published,
            )
            health["predecessorRetirement"] = retirements.health()
            health["drain"] = _drain_observations()
            health["capabilities"].append(preparation.CAPABILITY)
            return health
        if action == receipt_control.ACTION:
            return receipts.handle(payload)
        if action in {"agenda-read", "agenda-publish"}:
            return worktrees_runtime.agenda_handle(action, payload)
        if action == "picker-bootstrap":
            return worktrees_runtime.picker_bootstrap(payload)
        if action == READ_ACTION:
            if not handover.read_admitted():
                worktrees_runtime.close_admission("superseded")
            return worktrees_runtime.handle(payload)
        if action == repository_map.READ_ACTION:
            if not handover.read_admitted():
                repositories_runtime.close_admission("superseded")
            return repositories_runtime.read(payload)
        if action == "promote":
            trace = DrainTrace(
                status_monitor_cutover.routing_dir(), role="daemon",
                port=control_server.port, action=action, observe=_drain_observations,
            )
            with trace.stage("state-lock"):
                with state_lock:
                    if shutdown_requested.is_set():
                        return {"adopted": False, "error": "monitor is shutting down"}
                    handover.promotion_requested = True
                    if status_monitor_cutover.active_generation_for_pid(os.getpid()) is None:
                        return {"adopted": False, "error": "current-roster authority unavailable"}
                    handover.reopen()
                    admission_closed = False
                    with trace.stage("retired-cleanup"):
                        _cleanup_retired_request_surfaces()
                    published_lock = True
                    with trace.stage("activate-publisher"):
                        published_lock = _start_request_surfaces()
                    if not published_lock:
                        trace.emit("promotion-refused", "fail")
                        wake_event.set()
                        return {"adopted": False, "error": "current-roster authority unavailable"}
                    receipts.open_admission()
                    if core.git_ops._windows_observation():
                        segment_cache.open_observation_admission()
                    with trace.stage("publish-lock"):
                        if not _publish_lock():
                            _enter_drain_only_state()
                            return {"adopted": False, "error": "monitor discovery publication refused"}
            trace.emit("complete", "ok")
            wake_event.set()
            return {"adopted": True}
        if action == "shutdown":
            with state_lock:
                shutdown_requested.set()
                _enter_drain_only_state()
                worktrees_runtime.close_admission("shutdown")
                repositories_runtime.close_admission("shutdown")
            wake_event.set()
            return {"shutdown": True}
        if action == guarded_recovery.ACTION:
            with state_lock:
                return guarded_recovery.handle(
                    payload, directory=status_monitor_cutover.routing_dir(),
                    generation=PROCESS_GENERATION, port=control_server.port,
                    reopen=lambda: _handle_control("undrain", {}, routing_held=True),
                    close_on_failure=_enter_drain_only_state,
                    lock_timeout=status_monitor_cutover._READ_TIMEOUT_S,
                )
        if action == "undrain":
            trace = DrainTrace(
                status_monitor_cutover.routing_dir(), role="daemon",
                port=control_server.port, action=action, observe=_drain_observations,
            )
            with trace.stage("state-lock"):
                with state_lock:
                    if shutdown_requested.is_set():
                        return {"draining": True, "error": "monitor is shutting down"}
                    admission_closed = False
                    handover.reopen()
                    with trace.stage("retired-cleanup"):
                        _cleanup_retired_request_surfaces()
                    published_lock = True
                    with trace.stage("activate-publisher"):
                        published_lock = _start_request_surfaces()
                    if not published_lock:
                        _enter_drain_only_state()
                        trace.emit("undrain-refused", "fail")
                        wake_event.set()
                        return {"draining": True, "error": "current-roster authority unavailable"}
                    receipts.open_admission()
                    if core.git_ops._windows_observation():
                        segment_cache.open_observation_admission()
                    with trace.stage("publish-lock"):
                        if not _publish_lock(routing_held=routing_held):
                            _enter_drain_only_state()
                            return {"draining": True, "error": "monitor discovery publication refused"}
            trace.emit("complete", "ok")
            wake_event.set()
            return {"draining": False}
        if action != "drain":
            return {"ok": False, "error": f"unsupported action {action!r}"}
        timeout = float(payload.get("timeout") or 0.0)
        poll = float(payload.get("poll") or 0.1)
        with state_lock:
            if core.git_ops._windows_observation():
                segment_cache.close_observation_admission()
            force = bool(payload.get("force")) and not (
                core.git_ops._windows_observation() and any(segment_cache.observation_counts())
            )
        return drain_with_diagnostics(
            state_lock=state_lock, enter=_enter_drain_only_state,
            cleanup=_cleanup_retired_request_surfaces, busy=_drain_busy_reasons,
            observe=_drain_observations, directory=status_monitor_cutover.routing_dir(),
            port=control_server.port, timeout=timeout, poll=poll, force=force,
        )

    from . import guarded_anchors

    guarded_anchors.prepare_resident()
    control_server.start()
    if not passive_mode:
        status_monitor_cutover.publish_route(control_server.port, pid=os.getpid(), version=None)
    published_lock = _start_request_surfaces()
    if published_lock:
        _publish_lock()

    def _idle_candidate_still_empty() -> bool:
        retirements.refresh(cleanup=False)
        return (
            status_monitor_cutover.active_generation_for_pid(os.getpid()) is not None
            and published_lock and not admission_closed and not shutdown_requested.is_set()
            and not monitor_roots.live_picker_projects()
            and not core.list_cache.recent_demand_projects()
            and not reconciler.has_live_worktree_mux
            and not worktree_status_runtime.has_active_demand()
            and not managed_mux_runtime.has_active_demand()
            and not worktrees_runtime.demand_projects()
            and not repositories_runtime.has_active_demand()
            and not _drain_busy_reasons()
            and not allocation_linker.has_demand()
            and not retirements.pending
        )

    try:
        repositories_runtime.start(interval)
        while True:
            complete_publication(
                status_monitor_runtime._aw_runtime_home(),
                promote=lambda: _handle_control("promote", {}),
                ready=lambda: guarded_recovery.serving_ready(_handle_control("health", {})),
            )
            if core.git_ops._windows_observation():
                segment_cache.settle_pending_once()
            _cleanup_retired_request_surfaces()
            active_generation = status_monitor_cutover.active_generation_for_pid(os.getpid())
            retirements.refresh(cleanup=active_generation is not None)
            readers_ready = (
                worktrees_runtime.health()["worktrees"]["status"] == "ready"
                and repositories_runtime.health()["repositories"]["admitting"]
            )
            if handover.promotion_requested and active_generation is not None and (not published_lock or not readers_ready or not discovery_published):
                with state_lock:
                    if (
                        not admission_closed and not shutdown_requested.is_set()
                        and (not published_lock or not readers_ready or not discovery_published)
                    ):
                        published_lock = True
                        published_lock = _start_request_surfaces()
                        if published_lock:
                            _publish_lock()
            if handover.generation is None:
                handover.generation = active_generation if handover.promotion_requested else None
                if handover.promotion_requested and handover.generation is None and runtime_superseded():
                    if not core.git_ops._windows_observation():
                        break
                    with state_lock:
                        _enter_drain_only_state()
                    if not any(segment_cache.observation_counts()):
                        break
            if shutdown_requested.is_set():
                if shutdown_requested_since is None:
                    shutdown_requested_since = time.monotonic()
                if (
                    not _drain_busy_reasons()
                    or (
                        time.monotonic() - shutdown_requested_since >= _SELF_RETIRE_MAX_GRACE_S
                        and not segment_cache.observation_busy(worktree_status_runtime)
                    )
                ):
                    # The bounded request may report blocked; native custody stays owned.
                    break
            if handover.generation is not None:
                try:
                    superseded = handover.observe(status_monitor_cutover.routing_dir(), os.getpid())
                except Exception:
                    superseded = False
                if superseded:
                    with state_lock:
                        _enter_drain_only_state()
                _lease = handover.release_cold_lease(
                    _lease, status_monitor_cutover.routing_dir(), os.getpid(),
                )
                if superseded and not _drain_busy_reasons():
                    self_retire_confirms += 1
                    if self_retire_confirms >= self_retire_confirmations:
                        break
                else:
                    self_retire_confirms = 0
            with state_lock:
                can_sweep = published_lock and not admission_closed and active_generation is not None
                if can_sweep:
                    sweep_active = True
            if not can_sweep:
                _wake_interruptible_wait(wake_event, interval)
                continue
            try:
                core._status_monitor_recheck(governance, "iteration-boundary")
                core._status_monitor_recheck(governance, "pre-mutation:lock-renewal")
                _publish_lock()

                sweep_mux_pane_args()
                with state_lock:
                    allocation_linker.discover()
                allocation_linker.tick()
                picker_projects = monitor_roots.live_picker_projects()
                demand_projects = core.list_cache.recent_demand_projects()
                external_projects = picker_projects | demand_projects | worktrees_runtime.demand_projects()
                served = core._monitor_sweep(
                    mux_bin,
                    token,
                    my_prefix,
                    ctx_done,
                    interval=interval,
                    picker_projects=external_projects,
                    catalog_observer=reconciler.observe_mux,
                    pane_observer=pane_reconciler.observe,
                    segment_cache=segment_cache,
                    published=published,
                    incarnations=incarnations,
                    session_projects=session_projects,
                    project_lock=state_lock,
                    lifecycle_priority=lifecycle_priority,
                    governance=governance,
                    managed_mux_cache=managed_mux_runtime.cache,
                    config_cache_for_project=_config_cache_for_project,
                    roster_publisher=lambda: publish_active_project(
                        worktrees_runtime, observe_records=allocation_linker.observe,
                        **({"cleanup_owner": segment_cache} if core.git_ops._windows_observation() else {}),
                    ),
                    roster_failure=worktrees_runtime.producer_failed,
                )
                wait_for_lifecycle_priority(lifecycle_priority)
                with state_lock:
                    try:
                        core._status_monitor_recheck(governance, "pre-mutation:reconcile-sessions")
                        reconciler.step()
                    except status_monitor_governance_deferred:
                        raise
                    except Exception:
                        pass
                    try:
                        core._status_monitor_recheck(governance, "pre-mutation:reap-panes")
                        pane_reconciler.step(mux_bin)
                    except status_monitor_governance_deferred:
                        raise
                    except Exception:
                        pass
                sweep_active = False
            except status_monitor_governance_deferred as exc:
                sweep_active = False
                print(
                    "status-monitor backing off at "
                    f"{exc.result.get('checkpoint')}: "
                    f"{exc.result.get('reason')} ({exc.result.get('status')})",
                    file=sys.stderr,
                )
                time.sleep(core._GOVERNANCE_BACKOFF_SECONDS)
                continue
            if served < 0:
                _wake_interruptible_wait(wake_event, interval)
                continue

            if (
                served == 0
                and not external_projects
                and _idle_candidate_still_empty()
            ):
                empty_strikes += 1
                if empty_strikes >= max_empty_strikes:
                    retry_mux = _core_helper("_monitor_list_sessions", status_monitor_runtime._monitor_list_sessions)(mux_bin) if mux_bin else {}
                    retry_wt = bool(
                        retry_mux is not None and any(name.startswith("wt-") for name in retry_mux)
                    )
                    if retry_wt:
                        reconciler.observe_mux(set(retry_mux))
                    if retry_wt or not _idle_candidate_still_empty():
                        empty_strikes = 0
                    else:
                        idle_guard = handover.idle_guard(status_monitor_cutover.routing_dir())
                        if idle_guard is not None:
                            with state_lock:
                                if _idle_candidate_still_empty():
                                    _enter_drain_only_state()
                                    handover.retiring = True
                                else:
                                    idle_guard.release()
                                    idle_guard = None
                            if idle_guard is not None:
                                if not _drain_busy_reasons():
                                    break
                                # Accepted work drains under resident custody, not
                                # the installer gate; admission stays closed.
                                idle_guard.release()
                                idle_guard = None
                        empty_strikes = 0
            else:
                empty_strikes = 0
            _wake_interruptible_wait(wake_event, interval)
    finally:
        from .status_monitor_retirement import retire_monitor

        def _close_monitor_admission():
            if core.git_ops._windows_observation():
                segment_cache.close_observation_admission()
            with state_lock:
                shutdown_requested.set()
                _enter_drain_only_state()
                worktrees_runtime.close_admission("shutdown")
                repositories_runtime.close_admission("shutdown")

        retire_monitor(
            close_admission=_close_monitor_admission, repositories_runtime=repositories_runtime,
            worktrees_runtime=worktrees_runtime, close_request_surfaces=_close_request_surfaces,
            control_server=control_server, receipts=receipts,
            worktree_status_runtime=worktree_status_runtime, managed_mux_runtime=managed_mux_runtime,
            lock=lock, original_error=sys.exc_info()[1],
            allocation_linker=allocation_linker,
            retirement_guard=idle_guard,
            finish_observation_custody=lambda: segment_cache.finish_custody(
                worktree_status_runtime, lambda: _wake_interruptible_wait(wake_event, interval),
            ),
        )
    return 0
