"""CLI surface for the resident status monitor."""

from __future__ import annotations

import argparse
import os
import sys

#: Bounded grace period a shutdown/handoff (runtime superseded, a newer
#: monitor taking ownership, or the empty-strike idle-exit path) waits for
#: an in-flight tracking-write compute to finish before closing the
#: tracking_write server anyway. Generous relative to a single verb
#: transaction's own cost (a lock/load/mutate/save, comparable to one
#: worktree_status fact), bounded so a genuinely wedged compute can never
#: block a shutdown indefinitely (2026-09-26 PR review finding).
_TRACKING_WRITE_SHUTDOWN_GRACE_S = 10.0


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
        "(one process instead of one per session; default-on, opt out via "
        "AGENT_WORKTREES_STATUS_MONITOR=0)",
    )
    p.add_argument("--interval", type=int, default=15, help="Sweep cadence in seconds (min 2)")


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
    from . import status_monitor_runtime, status_updater_cli
    from . import tracking_write
    from . import worktree_status_daemon
    from .hook_ipc import HookIpcServer, HookUnavailable

    core = _core()
    mux = "psmux" if shutil.which("psmux") else ("tmux" if shutil.which("tmux") else None)
    mux_bin = (shutil.which(mux) or mux) if mux else None
    interval = args.interval if getattr(args, "interval", None) and args.interval >= 2 else 15

    # Stage D (agent-cli-lazy-dispatch): these six are owned by
    # status_updater_cli/status_monitor_runtime (both already cluster-free);
    # resolving them via `_core_helper` -- direct sibling import, falling back
    # to a monkeypatched `__main__` override if one is present -- instead of
    # `core.attr`, is what lets `status-monitor` stay cluster-free too.
    lock = _core_helper("_monitor_lock_path", status_monitor_runtime._monitor_lock_path)()
    my_prefix = os.path.realpath(sys.prefix)
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
    wait_for_lifecycle_priority = core._wait_for_lifecycle_priority
    classify_daemon_compute = core._classify_daemon_compute
    worktree_status_compute = core._worktree_status_compute
    runtime_superseded = _core_helper("_runtime_superseded", status_updater_cli._runtime_superseded)

    def _other_current_monitor() -> bool:
        """A *different*, live monitor on a non-superseded runtime owns the host."""
        d = _locks.read_lock(lock)
        if not (_locks.lock_is_live(d) and isinstance(d, dict)):
            return False
        if d.get("pid") == os.getpid():
            return False
        if d.get("mux") is False and mux_bin:
            return False
        op = d.get("prefix")
        return not (op and runtime_superseded(prefix=op))

    if _other_current_monitor():
        return 0
    _locks.write_lock(lock, extra={"prefix": my_prefix, "mux": bool(mux_bin)})

    ctx_done: set[str] = set()
    reconciler = session_catalog.ResidentSessionReconciler(
        register_monitor_session=_core_helper("_register_session_for_monitor", status_monitor_runtime._register_session_for_monitor)
    )
    pane_reconciler = pane_reaper.ResidentPaneReconciler(
        activate_project=activate_project_for_path
    )
    try:
        cache_ttl = float(os.environ.get("AGENT_WORKTREES_STATUS_CACHE_SECONDS", "60"))
    except ValueError:
        cache_ttl = 60.0
    segment_cache = status_segment_cache_type(cache_ttl)
    published: dict[tuple[str, str], str] = {}
    incarnations: dict[str, str] = {}
    session_projects: dict[str, str] = {}
    state_lock = threading.RLock()
    lifecycle_priority = threading.Event()
    lifecycle_count_lock = threading.Lock()
    lifecycle_count = 0
    lifecycle_claims: dict[str, float] = {}
    governance = loop_governance.LoopGovernance()
    hook_client = load_hook_client_module()
    hook_policy = resident_hook_policy_type(hook_client)
    if hook_policy.ready():
        hook_policy.plugin_related_anchors()
    installation_context = registry_paths.installation_context()

    def _decide(kind: str, payload: dict, deadline: float) -> dict:
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
    if hook_policy.ready():
        try:
            hook_server = HookIpcServer(_decide)
            hook_server.start()
        except Exception:
            hook_server = None

    classify_server = None
    try:
        classify_server = classify_daemon.start_server(classify_daemon_compute)
        classify_server.start()
    except Exception:
        classify_server = None

    tracking_write_server = None
    try:
        # Load every verb-owning module (currently none -- see
        # tracking_write._VERB_MODULES) eagerly at monitor startup, not
        # lazily on the first request: a Phase 3 verb module's own
        # `register_verb` call is then guaranteed to have run before this
        # daemon ever answers a `tracking_write` request, closing the
        # 2026-09-26 PR review's "load production verbs at the monitor's
        # own startup/import path" finding at this call site specifically
        # (compute/run_direct already call this too, idempotently).
        tracking_write._ensure_verb_modules_loaded()
        tracking_write_server = tracking_write.start_server(tracking_write.compute)
        tracking_write_server.start()
    except Exception:
        tracking_write_server = None

    worktree_status_runtime = worktree_status_daemon.InProcessRuntime()
    worktree_status_runtime.start(
        _core_helper("_aw_runtime_home", status_monitor_runtime._aw_runtime_home)() / "worktree-status-cache.sqlite3",
        worktree_status_compute,
    )

    managed_mux_runtime = mux_link.InProcessRuntime()
    managed_mux_runtime.start(
        _core_helper("_aw_runtime_home", status_monitor_runtime._aw_runtime_home)() / "managed-mux-cache.json"
    )

    def _lock_extra() -> dict:
        extra = {"prefix": my_prefix, "mux": bool(mux_bin)}
        if isinstance(installation_context, dict):
            extra.update(
                {
                    "marketplaceId": str(installation_context.get("marketplaceId") or ""),
                    "installReceipt": str(installation_context.get("installReceipt") or ""),
                    "pluginRoot": str(installation_context.get("pluginRoot") or ""),
                }
            )
        if hook_server is not None:
            extra.update(hook_server.rendezvous())
        if classify_server is not None:
            extra.update(classify_daemon.rendezvous_fields(classify_server))
        if tracking_write_server is not None:
            extra.update(tracking_write.rendezvous_fields(tracking_write_server))
        extra.update(worktree_status_runtime.lock_extra())
        extra.update(managed_mux_runtime.lock_extra())
        return extra

    _locks.write_lock(lock, extra=_lock_extra())
    empty_strikes = 0
    max_empty_strikes = 3

    def _tracking_write_busy() -> bool:
        # subscriber_count() alone is not enough: a client releases its own
        # lease as soon as its own request call returns or times out, which
        # can happen well before the daemon-side compute this triggered
        # actually finishes (CoalescingServer never cancels an accepted
        # owner). tracking_write.has_inflight_write() tracks the compute
        # itself, in this same process, independent of any client's own
        # lease lifecycle (2026-09-26 PR review finding).
        return (
            tracking_write_server is not None
            and tracking_write_server.subscriber_count() > 0
        ) or tracking_write.has_inflight_write()

    try:
        while True:
            if runtime_superseded():
                break
            if _other_current_monitor():
                return 0
            try:
                core._status_monitor_recheck(governance, "iteration-boundary")
                core._status_monitor_recheck(governance, "pre-mutation:lock-renewal")
                _locks.write_lock(lock, extra=_lock_extra())

                picker_projects = monitor_roots.live_picker_projects()
                demand_projects = core.list_cache.recent_demand_projects()
                external_projects = picker_projects | demand_projects
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
            except status_monitor_governance_deferred as exc:
                print(
                    "status-monitor backing off at "
                    f"{exc.result.get('checkpoint')}: "
                    f"{exc.result.get('reason')} ({exc.result.get('status')})",
                    file=sys.stderr,
                )
                time.sleep(core._GOVERNANCE_BACKOFF_SECONDS)
                continue
            if served < 0:
                time.sleep(interval)
                continue

            if (
                served == 0
                and not external_projects
                and not reconciler.has_live_worktree_mux
                and not worktree_status_runtime.has_active_demand()
                and not managed_mux_runtime.has_active_demand()
                and not _tracking_write_busy()
            ):
                empty_strikes += 1
                if empty_strikes >= max_empty_strikes:
                    retry_mux = _core_helper("_monitor_list_sessions", status_monitor_runtime._monitor_list_sessions)(mux_bin) if mux_bin else {}
                    retry_wt = bool(
                        retry_mux is not None and any(name.startswith("wt-") for name in retry_mux)
                    )
                    if retry_wt:
                        reconciler.observe_mux(set(retry_mux))
                    if (
                        retry_wt
                        or monitor_roots.live_picker_projects()
                        or core.list_cache.recent_demand_projects()
                        or worktree_status_runtime.has_active_demand()
                        or managed_mux_runtime.has_active_demand()
                        or _tracking_write_busy()
                    ):
                        empty_strikes = 0
                    else:
                        break
            else:
                empty_strikes = 0
            time.sleep(interval)
    finally:
        if hook_server is not None:
            hook_server.close()
        if classify_server is not None:
            classify_server.close()
        if tracking_write_server is not None:
            # 2026-09-26 PR review: CoalescingServer.close() does not drain
            # an already-dispatched handler thread -- closing while a write
            # is still executing (runtime_superseded/_other_current_monitor
            # can reach this `finally` regardless of the empty-strike path
            # above) could terminate the process mid-transaction, leaving a
            # non-idempotent write incomplete. Waits on the same combined
            # `_tracking_write_busy()` predicate the empty-strike branch
            # uses -- `has_inflight_write()` alone misses the window between
            # a request being accepted (subscriber registered) and its
            # `compute()` call actually starting (where that counter
            # increments), so subscriber_count() must stay part of the
            # check here too. Bounded: a shutdown must still terminate
            # eventually, never wait forever on a wedged compute.
            _wait_for_tracking_write_idle(_tracking_write_busy)
            tracking_write_server.close()
        worktree_status_runtime.shutdown()
        managed_mux_runtime.shutdown()
        d = _locks.read_lock(lock)
        if isinstance(d, dict) and d.get("pid") == os.getpid():
            _locks.remove_lock(lock)
    return 0
