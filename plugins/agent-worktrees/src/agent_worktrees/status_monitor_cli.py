"""CLI surface for the resident status monitor."""

from __future__ import annotations

import argparse
import os
import sys

from . import config as cfg

#: Bounded grace period a shutdown/handoff (runtime superseded, a newer
#: monitor taking ownership, or the empty-strike idle-exit path) waits for
#: an in-flight tracking-write compute to finish before closing the
#: tracking_write server anyway. Generous relative to a single verb
#: transaction's own cost (a lock/load/mutate/save, comparable to one
#: worktree_status fact), bounded so a genuinely wedged compute can never
#: block a shutdown indefinitely (2026-09-26 PR review finding).
_TRACKING_WRITE_SHUTDOWN_GRACE_S = 10.0

#: Same bounded-grace discipline, applied to the self-retire path itself.
#: Once this daemon observes a live, strictly-newer generation has taken over
#: (self_retire.is_superseded), it stops admitting new work and should exit
#: within one or two sweep cycles -- but the pre-existing gate required TWO
#: *consecutive* iterations of "superseded AND every busy_reasons() channel
#: clear" (sweep/hook/classify/tracking-write) before breaking, resetting the
#: counter to zero on any single busy tick in between. On a machine with
#: frequent concurrent CLI traffic (hook/classify/tracking-write calls arrive
#: often enough that some channel is near-always busy at the exact instant
#: checked), that AND-of-every-channel gate can go unsatisfied indefinitely --
#: a demoted daemon confirmed-superseded on every single iteration, yet never
#: accumulating two clean confirms in a row, observed lingering for *days*
#: (ThomasMichon/copilot-extensions#5326's follow-up: the drain_timeout fix
#: there covers the orchestrator-driven drain RPC; this covers the daemon's
#: own independent self-retire loop, which must never depend on a clean
#: confirm that can be perpetually denied by unrelated traffic). Fix: once
#: superseded is first observed, start a bounded deadline; reaching it forces
#: the break regardless of busy_reasons, so self-retire is always upper
#: bounded, same as the tracking-write shutdown grace above -- never the
#: multi-day lingering this constant replaces.
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


def _wake_interruptible_wait(wake_event, seconds: float) -> None:
    """Sleep up to ``seconds``, but return immediately (and clear the event)
    if :func:`resident_push.notify` sets ``wake_event`` first.

    This is what turns the periodic sweep interval into a mere **backstop**:
    an explicit status push (or any ``postToolUse`` mutation observed via the
    hook-IPC path) wakes the loop early instead of waiting out the full
    interval, while the interval itself still fires unconditionally for
    every other source of staleness (external git activity, a stale mux
    session, etc.) that has no explicit push of its own.
    """
    if wake_event.wait(timeout=seconds):
        wake_event.clear()


def _kind_wakes_sweep(kind: str, targets: list[str] | None) -> bool:
    """Whether a hook-IPC ``kind`` actually invalidated something and should
    therefore wake the sweep loop early.

    ``postToolUse`` fires for EVERY tool call, but
    ``_ResidentHookPolicy.mutation_targets`` returns an empty list for a
    read-only one (e.g. ``git status``) -- waking the loop for those would
    turn the periodic backstop into a high-frequency render path for zero
    benefit (2026-09-27 Copilot review finding). ``targets is None`` means
    "invalidate everything" (a real, unbounded-scope mutation); a non-empty
    list means "invalidate these specific paths" (also real); an empty list
    means nothing was invalidated at all -- no wake. Pulled out as its own
    pure predicate so the "which kinds push" policy is directly unit-testable
    without needing a live ``HookIpcServer``/``_ResidentHookPolicy.ready()``
    to drive ``_decide`` end-to-end.
    """
    return kind == "postToolUse" and (targets is None or bool(targets))


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
    p.add_argument("--passive", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--control-port", type=int, default=None, help=argparse.SUPPRESS)


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
    from . import resident_push, self_retire, status_monitor_cutover, status_monitor_runtime, status_updater_cli
    from . import tracking_write
    from . import worktree_status_daemon
    from .hook_ipc import HookIpcServer, HookUnavailable

    core = _core()
    mux = "psmux" if shutil.which("psmux") else ("tmux" if shutil.which("tmux") else None)
    mux_bin = (shutil.which(mux) or mux) if mux else None
    interval = args.interval if getattr(args, "interval", None) and args.interval >= 2 else 15
    passive_mode = bool(getattr(args, "passive", False))

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
    runtime_superseded = _core_helper("_runtime_superseded", status_updater_cli._runtime_superseded)
    wait_for_lifecycle_priority = core._wait_for_lifecycle_priority
    classify_daemon_compute = core._classify_daemon_compute
    worktree_status_compute = core._worktree_status_compute
    shutdown_requested = threading.Event()
    shutdown_requested_since: float | None = None
    admission_closed = False
    published_lock = not passive_mode
    sweep_active = False
    self_retire_generation = None
    self_retire_confirms = 0
    self_retire_confirmations = 2
    self_retire_first_detected: float | None = None

    def _other_current_monitor() -> bool:
        """A *different*, live monitor on a non-superseded runtime owns the host."""
        if passive_mode:
            return False
        d = _locks.read_lock(lock)
        if not (_locks.lock_is_live(d) and isinstance(d, dict)):
            return False
        if d.get("pid") == os.getpid():
            return False
        if d.get("mux") is False and mux_bin:
            return False
        op = d.get("prefix")
        return not (op and runtime_superseded(prefix=op))

    # Atomic exclusivity gate -- MUST run before the lock-file check below,
    # not instead of it. The lock file remains the metadata channel other
    # tooling reads (pid/prefix/mux, supersession comparisons); it was never
    # sufficient alone for exclusivity, because "read the file, decide it's
    # not live, then write it" has a real gap between the read and the
    # write. Any number of monitors launched in the same narrow window (many
    # sessions' lifecycle hooks firing close together, which is exactly what
    # a burst of `agent-worktrees create`/`resume` calls or several mux panes
    # restarting in quick succession produces) could all pass that check
    # before any of them committed a write, and each surviving duplicate then
    # ran indefinitely until a periodic re-check happened to notice a sibling
    # -- observed in the wild as 100+ live `status-monitor` processes eating
    # double-digit percent CPU (aperture-labs#8036 follow-up). A
    # :class:`single_instance_lease.SingleInstance` (the same proven,
    # OS-level flock/msvcrt primitive already used for the `classify`
    # coalescing lock and the cutover orchestrator elsewhere in this plugin)
    # closes the gap: acquisition is one atomic kernel call, so of any
    # number of concurrent callers racing the same name, exactly one ever
    # wins -- every loser raises immediately here, never reaching the
    # expensive setup below, let alone running to completion.
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
    # Scope: this gate protects only an ORDINARY cold start (the reported
    # bug -- a burst of session-lifecycle hooks each independently deciding
    # to spawn, with no existing owner or an existing non-superseding one).
    # It deliberately does NOT extend to the cutover/promotion lifecycle
    # (`_handle_control("promote")`, route-driven activation in the
    # resident loop) -- that lifecycle relies on a genuinely different,
    # soft (eventually-consistent, deliberately-overlapping) ownership
    # model via self-retire/supersession polling, not a hard mutual-
    # exclusion claim, and retrofitting one there surfaced multiple real
    # correctness bugs under review (concurrent promote-handler races, a
    # predecessor/successor overlap window far longer than any reasonable
    # bounded wait, muxless-owner replacement never completing) that need
    # their own careful design, not a quick extension of this gate. See
    # the tracking issue for that follow-up.
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

    def _muxless_replacement_in_progress() -> bool:
        """Mirrors `_other_current_monitor()`'s own mux exception: an
        existing live owner recorded `mux: false` and this candidate has a
        mux binary available. That is a deliberate, already-tested
        replacement path (`test_ensure_replaces_muxless_owner_when_mux_is_
        available`) resolved via the existing soft self-retire/supersession
        polling, not this atomic lease -- contesting the lease here would
        block the replacement from ever completing (review finding,
        PR #5412), since the still-resident muxless owner legitimately
        holds it until it notices supersession on its own."""
        existing = _locks.read_lock(lock)
        return bool(
            _locks.lock_is_live(existing)
            and isinstance(existing, dict)
            and existing.get("pid") != os.getpid()
            and existing.get("mux") is False
            and mux_bin
        )

    _lease = None
    if not passive_mode and not _muxless_replacement_in_progress():
        _lease = _acquire_ownership_lease()
        if _lease is None:
            return 0

    if _other_current_monitor():
        return 0
    if not passive_mode:
        _locks.write_lock(lock, extra={"prefix": my_prefix, "mux": bool(mux_bin)})

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
    hook_client = load_hook_client_module()
    hook_policy = resident_hook_policy_type(hook_client)
    if hook_policy.ready():
        hook_policy.plugin_related_anchors()
    installation_context = registry_paths.installation_context()

    def _classify_compute(kind: str, payload: dict) -> dict:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                delay_s = float(payload.get("__test_delay_s") or 0.0)
            except (TypeError, ValueError):
                delay_s = 0.0
            if delay_s > 0:
                time.sleep(delay_s)
                return {"delayed": True}
        return classify_daemon_compute(kind, payload)

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
                if kind == "postToolUse":
                    targets = hook_policy.mutation_targets(payload)
                    if _kind_wakes_sweep(kind, targets):
                        # This tool call actually invalidated one or more
                        # cached segments (or all of them, when targets is
                        # None) -- wake the sweep loop now rather than
                        # waiting out `interval`, same as an explicit
                        # `status` push (resident_push.notify). A read-only
                        # tool call (empty targets) never wakes.
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

    def _start_request_surfaces() -> None:
        nonlocal hook_server, classify_server, tracking_write_server
        if admission_closed or not published_lock:
            return
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
        """Soft-close counterpart to :func:`_close_request_surfaces`: stops
        admitting new requests on each live request surface while leaving
        its socket open (see ``CoalescingServer.close_admission``'s own
        docstring for the full rationale) -- a single-shot RPC caller
        reaching this daemon during the bounded drain-only grace window
        (``_SELF_RETIRE_MAX_GRACE_S`` / an explicit shutdown's own grace)
        gets a structured ``{"fallback": true, "reason": reason}`` response
        instead of a bare OS-level connection-refused. The server objects
        stay the live ``hook_server``/``classify_server``/
        ``tracking_write_server`` references (never moved into the
        ``retired_*`` lists, never set to ``None``), so the existing
        ``_hook_busy``/``_classify_busy``/``_tracking_write_busy`` checks
        keep seeing their in-flight handler counts unchanged. Actual socket
        teardown happens once, unconditionally, in ``_close_request_surfaces``
        at real process exit (this function's own callers never close a
        socket).
        """
        if hook_server is not None:
            hook_server.close_admission(reason)
        if classify_server is not None:
            classify_server.close_admission(reason)
        if tracking_write_server is not None:
            tracking_write_server.close_admission(reason)

    def _enter_drain_only_state() -> None:
        nonlocal admission_closed, published_lock
        admission_closed = True
        published_lock = False
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

    def _hook_busy() -> bool:
        return any(
            server.active_handler_count() > 0
            for server in ([hook_server] if hook_server is not None else []) + retired_hook_servers
        )

    def _classify_busy() -> bool:
        return any(
            server.active_handler_count() > 0
            for server in ([classify_server] if classify_server is not None else [])
            + retired_classify_servers
        )

    def _tracking_write_busy() -> bool:
        return any(
            server.active_handler_count() > 0
            for server in ([tracking_write_server] if tracking_write_server is not None else [])
            + retired_tracking_write_servers
        ) or tracking_write.has_inflight_write()

    def _cleanup_retired_request_surfaces() -> None:
        with state_lock:
            retired_hook_servers[:] = [
                server for server in retired_hook_servers if server.active_handler_count() > 0
            ]
            retired_classify_servers[:] = [
                server for server in retired_classify_servers if server.active_handler_count() > 0
            ]
            if not tracking_write.has_inflight_write():
                retired_tracking_write_servers[:] = [
                    server
                    for server in retired_tracking_write_servers
                    if server.active_handler_count() > 0
                ]

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
        return reasons

    control_server = status_monitor_cutover.ControlServer(
        lambda action, payload: _handle_control(action, payload),
        port=getattr(args, "control_port", None),
    )
    control_server.start()
    status_monitor_cutover.publish_route(
        control_server.port,
        pid=os.getpid(),
        version=None,
    ) if not passive_mode else None
    _start_request_surfaces()
    if published_lock:
        _locks.write_lock(lock, extra=_lock_extra())
    empty_strikes = 0
    max_empty_strikes = 3

    def _handle_control(action: str, payload: dict) -> dict:
        nonlocal admission_closed, published_lock
        if action == "health":
            return {
                "status": "draining" if admission_closed else "ready",
                "published": published_lock,
                "pid": os.getpid(),
            }
        if action == "promote":
            with state_lock:
                _cleanup_retired_request_surfaces()
                published_lock = True
                _start_request_surfaces()
                _locks.write_lock(lock, extra=_lock_extra())
            wake_event.set()
            return {"adopted": True}
        if action == "shutdown":
            shutdown_requested.set()
            wake_event.set()
            return {"shutdown": True}
        if action == "undrain":
            with state_lock:
                admission_closed = False
                _cleanup_retired_request_surfaces()
                published_lock = True
                _start_request_surfaces()
                _locks.write_lock(lock, extra=_lock_extra())
            wake_event.set()
            return {"draining": False}
        if action != "drain":
            return {"ok": False, "error": f"unsupported action {action!r}"}
        timeout = float(payload.get("timeout") or 0.0)
        poll = float(payload.get("poll") or 0.1)
        force = bool(payload.get("force"))
        with state_lock:
            _enter_drain_only_state()
        deadline = time.time() + max(0.0, timeout)
        while True:
            _cleanup_retired_request_surfaces()
            reasons = _drain_busy_reasons()
            if not reasons:
                return {
                    "drained": True,
                    "clean": True,
                    "forced": False,
                    "busy_sessions": [],
                }
            if timeout <= 0 or time.time() >= deadline:
                return {
                    "drained": force,
                    "clean": False,
                    "forced": force,
                    "busy_sessions": reasons,
                }
            time.sleep(max(0.05, poll))

    try:
        while True:
            _cleanup_retired_request_surfaces()
            active_generation = status_monitor_cutover.active_generation_for_pid(os.getpid())
            if active_generation is not None and not published_lock:
                with state_lock:
                    if not published_lock:
                        published_lock = True
                        _start_request_surfaces()
                        _locks.write_lock(lock, extra=_lock_extra())
                wake_event.set()
            if self_retire_generation is None:
                self_retire_generation = active_generation
                if self_retire_generation is None and runtime_superseded():
                    break
            if shutdown_requested.is_set():
                if shutdown_requested_since is None:
                    shutdown_requested_since = time.monotonic()
                if (
                    not _drain_busy_reasons()
                    or time.monotonic() - shutdown_requested_since >= _SELF_RETIRE_MAX_GRACE_S
                ):
                    # Same bounded-upper-bound discipline as self-retire below:
                    # an explicit shutdown request must never wait on
                    # busy_reasons() forever either.
                    break
            if self_retire_generation is not None:
                try:
                    superseded = self_retire.is_superseded(
                        status_monitor_cutover.routing_dir(),
                        os.getpid(),
                        self_retire_generation,
                    )
                except Exception:
                    superseded = False
                if superseded:
                    with state_lock:
                        _enter_drain_only_state()
                    if self_retire_first_detected is None:
                        self_retire_first_detected = time.monotonic()
                else:
                    self_retire_first_detected = None
                grace_expired = (
                    self_retire_first_detected is not None
                    and time.monotonic() - self_retire_first_detected
                    >= _SELF_RETIRE_MAX_GRACE_S
                )
                if superseded and not _drain_busy_reasons():
                    self_retire_confirms += 1
                    if self_retire_confirms >= self_retire_confirmations:
                        break
                else:
                    self_retire_confirms = 0
                if grace_expired:
                    # Bounded upper bound: a confirmed-superseded daemon must
                    # never linger past this regardless of busy_reasons()
                    # never going quiet on its own (see _SELF_RETIRE_MAX_GRACE_S).
                    break
            with state_lock:
                can_sweep = published_lock and not admission_closed
                if can_sweep:
                    sweep_active = True
            if not can_sweep:
                _wake_interruptible_wait(wake_event, interval)
                continue
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
                    config_cache_for_project=_config_cache_for_project,
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
            _wake_interruptible_wait(wake_event, interval)
    finally:
        _close_request_surfaces()
        control_server.close()
        resident_push.reset()
        worktree_status_runtime.shutdown()
        managed_mux_runtime.shutdown()
        status_monitor_cutover.clear_route_if_owner(os.getpid())
        d = _locks.read_lock(lock)
        if isinstance(d, dict) and d.get("pid") == os.getpid():
            _locks.remove_lock(lock)
    return 0
