#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

from .engine_helpers import _DEFAULT_HOST_COLS, _DEFAULT_TARGET_ENVS, start_loader, target_rows
from .inbox import ensure_inbox
from .selection import ListSelection
from .. import update_stage

log = logging.getLogger("agent-worktrees.picker")

#: Guards the lazy, exactly-once construction of each screen's own
#: `_setup_epoch_lock` (see `PickerScreenRuntimeMixin._ensure_setup_epoch_lock`).
#: A single shared, module-level lock -- not any per-screen attribute, since
#: not every test double that constructs this mixin directly sets the same
#: fixed set of attributes `PickerScreen.__init__` does.
_SETUP_EPOCH_LOCK_INIT_LOCK = threading.Lock()


#: Minimum time between real ``update_stage.indicator_state()`` polls
#: (picker-performance-and-responsiveness Phase A). That call is a full
#: ``agent-worktrees stage-update --indicator-state --json`` subprocess
#: round trip -- cold Python-interpreter + CLI-module-import cost, not the
#: "two small files" its docstring once assumed -- so calling it synchronously
#: on every tick (previously every 5th frame, ~2x/sec) blocked the entire
#: Textual render/input loop for the call's full duration, observed at
#: 2-2.5s on a loaded machine: far longer than the polling interval itself,
#: so the UI thread was blocked almost continuously. ``AGENT_WORKTREES_
#: PICKER_UPDATE_STATE_POLL_SECS`` overrides it, ``<= 0`` disables polling
#: (the last-known state just stops refreshing).
def _update_state_poll_secs() -> float:
    try:
        return float(
            os.environ.get("AGENT_WORKTREES_PICKER_UPDATE_STATE_POLL_SECS", "30")
        )
    except (TypeError, ValueError):
        return 30.0


UPDATE_STATE_POLL_SECS = _update_state_poll_secs()


@dataclass(frozen=True)
class _SetupPayload:
    pivot_payload: object
    source_tabs: list[dict[str, object]]
    source_local: tuple[str, str] | None
    source_repo_branch: tuple[str, str]
    loader: object | None
    data: list[object]
    load_delay: dict[int, float]
    host_cols: list[str]
    target_env_list: list[str]


class PickerScreenRuntimeMixin:
    def _poll_update_state(self):
        """Refresh ``self.update_state`` from the engine's stage-update
        indicator -- throttled and OFF the render thread
        (picker-performance-and-responsiveness Phase A).

        ``update_stage.indicator_state()`` is a full ``agent-worktrees
        stage-update --indicator-state --json`` subprocess round trip (cold
        interpreter + CLI-module-import cost), not the "two small files" an
        earlier version of this docstring assumed -- measured at 2-2.5s on a
        loaded machine. This method used to call it synchronously from
        ``_tick`` every 5th frame (~2x/sec): since a single call already
        costs far longer than the interval between calls, the Textual
        render/input loop was blocked almost continuously, dropping/delaying
        keystrokes. Now it's wall-clock-throttled to
        :data:`UPDATE_STATE_POLL_SECS` and, when due, the actual subprocess
        call runs via :meth:`_run_bg` on a background thread -- mirroring
        :meth:`_poll_manager_update_state`'s existing pattern -- so this
        call always returns immediately and never blocks a keypress.

        Never fatal -- a read hiccup leaves the last state in place. A no-op
        once ``_update_state_pinned`` is set
        (:func:`capture.capture_async`'s ``update_state`` override, applied
        after this poll's ``call_after_refresh`` scheduling): this callback's
        exact fire time relative to that override is not guaranteed by pause
        count alone (Textual's mount lifecycle can defer it later than any
        fixed number of ``pilot.pause()`` calls), so an unconditional
        assignment here could silently clobber an explicit test/audit
        override moments after it was set -- a real, observed capture-race,
        not just a hypothetical one. Re-checked again in ``_done`` below, in
        case the pin lands while the background call is in flight."""
        if getattr(self, "_update_state_pinned", False):
            return
        now = time.monotonic()
        if now - self._last_update_state_poll < UPDATE_STATE_POLL_SECS:
            return
        if self._update_state_poll_pending:
            return
        self._last_update_state_poll = now
        self._update_state_poll_pending = True

        def _work():
            try:
                return update_stage.indicator_state()
            except Exception:
                return None

        def _done(state):
            self._update_state_poll_pending = False
            if state is not None and not getattr(self, "_update_state_pinned", False):
                self.update_state = state

        self._run_bg("update-stage-poll", _work, _done, quiet=True)
    def _poll_manager_update_state(self):
        """Refresh ``self.manager_update_state`` from the cached
        manager-update-check status (cheap, read-only, no network) and, if
        the cache is stale or missing, kick a background thread to actually
        check GitHub -- never on the render thread, since the network fetch
        itself is not cheap. Safe to call every launch: the cache means a
        real fetch only happens once per
        ``manager_update_check.CHECK_INTERVAL_SECS``. A no-op once
        ``_manager_update_state_pinned`` is set -- see ``_poll_update_state``'s
        docstring for why an unconditional assignment here can clobber an
        explicit capture/audit override."""
        if getattr(self, "_manager_update_state_pinned", False):
            return
        from ... import manager_update_check as _muc

        try:
            self.manager_update_state = _muc.indicator_state()
        except Exception:
            return
        if not _muc.should_check():
            return

        def _work():
            try:
                _muc.check_now()
                return _muc.indicator_state()
            except Exception:
                return None

        def _done(state):
            if state is not None and not getattr(
                    self, "_manager_update_state_pinned", False):
                self.manager_update_state = state

        self._run_bg("manager-update-check", _work, _done, quiet=True)
    #: Re-check the local claims-orphanage at most this often (seconds) --
    #: an ``--abandon`` finalize re-homing an obligation is rare, so this is
    #: a background courtesy poll, not a hot path.
    _ORPHAN_POLL_SECS = 120.0
    def _poll_orphan_state(self, *, force=False):
        """Refresh ``self._orphans`` -- the local claims-orphanage summary
        (worktree-claims-transitive-finalization Phase 4 item 2) -- off the
        render thread. An orphaned obligation (re-homed by an ``--abandon``
        finalize, awaiting ``claims cleanup``) has no live worktree row of
        its own to surface on, so this is tracked as independent screen
        state rather than folded into ``self.data`` -- read by
        ``status_text()`` (the count chip) and the 'o' Orphanage screen.
        Cached; ``force=True`` (the 'r' full-reload key) bypasses the cache.
        Uses ``getattr`` defaults for its own cache attrs (not a bare
        ``self._orphans...`` read): some lightweight test doubles mix in
        this ``PickerScreenRuntimeMixin`` directly without running the real
        ``PickerScreen.__init__`` that normally seeds them.

        Each call starts its own independent background thread (via
        ``_run_bg``), so the mount-time poll and a forced 'r' poll can
        overlap; a generation counter (bumped per call, compared in
        ``_done``) guards against an older request finishing after a
        newer one and clobbering its fresher snapshot with stale data."""
        now = time.monotonic()
        checked_at = getattr(self, "_orphans_checked_at", None)
        if not force and checked_at is not None and now - checked_at < self._ORPHAN_POLL_SECS:
            return
        self._orphans_checked_at = now
        fetch = getattr(self.src, "orphans", None)
        if not callable(fetch):
            return

        generation = getattr(self, "_orphans_poll_generation", 0) + 1
        self._orphans_poll_generation = generation

        def _work():
            try:
                return fetch()
            except Exception:
                return None

        def _done(rows):
            if rows is not None and getattr(
                    self, "_orphans_poll_generation", generation) == generation:
                self._orphans = rows


        self._run_bg("orphan-check", _work, _done, quiet=True)
    def _maybe_repoll(self):
        """Fire a bounded, in-place background refresh of machine state (#1421).

        Keeps the open picker's lists live without a restart. Conservative
        (``POLL_SECS``, default 45s; env-overridable, ``<=0`` disables) and
        courteous: only on the Worktrees/Maintenance tabs (Profiles reads no
        worktree data), skips while a maintenance/apply dialog is up (its own
        reload owns the refresh), never flips a machine to its connect spinner
        (rows update in place), and polls only the machines currently in view --
        a specific-machine tab never fans out to the whole fleet. The loader's
        per-source in-flight guard keeps a slow machine from being re-hit.
        """
        from . import engine as engine_mod

        poll_secs = engine_mod.POLL_SECS
        if poll_secs <= 0 or self.loader is None:
            return
        if self._kind() not in ("worktrees", "maintenance") or self.progress is not None:
            return
        now = time.monotonic()
        if now - self._last_poll < poll_secs:
            return
        self._last_poll = now
        try:
            self.loader.repoll_silent(self._poll_keys())
        except Exception:
            pass
        # Reconcile remote tabs' PR state on their own owning machine, once per
        # source, in the background after first paint (#2102). The local tab's
        # PRs are reconciled separately via the #1423 setup-reload apply path.
        recon = getattr(self.loader, "reconcile_remote_prs", None)
        if callable(recon):
            try:
                recon(self._poll_keys())
            except Exception:
                pass
    def _poll_keys(self):
        """Source keys to background-poll: all ready sources or the current tab."""
        if self.is_all():
            return self.ready_envs() | self.ready_source_ids()
        source_id = self._current_tab().get("source_id")
        if source_id:
            return {source_id}
        m, e = self.cur_machine()[:2]
        return {(m, e)}
    def _maybe_repoll_pivot(self):
        """Keep an open registered pivot (Tasks) live without a keypress.

        The registered-pivot runtime caches its ``list`` per scope with no TTL and
        is only invalidated by the pivot's *own* actions -- so a task/card created
        by **another** session (e.g. a claimer posting a steer card) never appears
        in an already-open Tasks pivot. On the same conservative cadence as the
        worktree repoll (``POLL_SECS``; ``<=0`` disables), force a background,
        swap-in-place refetch of the current pivot scope. Skips while a modal /
        progress dialog is up. Cheap: one ``list`` subprocess per interval, and
        the runtime's own in-flight guard coalesces overlapping polls."""
        from . import engine as engine_mod

        poll_secs = engine_mod.POLL_SECS
        if poll_secs <= 0 or self.progress is not None:
            return
        reg = self._reg_pivot()
        if reg is None:
            return
        now = time.monotonic()
        if now - self._last_pivot_poll < poll_secs:
            return
        self._last_pivot_poll = now
        scope = self._pivot_scope_key()
        if scope is None:
            return
        try:
            self._pivot_runtime(reg).repoll(scope)
        except Exception:
            pass
    def _maybe_refresh_worker_pivots(self):
        """Keep venue pivots that declare a ``worker`` block loaded, so a
        Worktrees row can show the remote worker it supervises without the
        operator first visiting that pivot. First sight kicks one background
        ``list``; after that a swap-in-place repoll runs every
        ``2 * POLL_SECS`` (venue listings are costlier than a status ping).
        Account-scoped pivots use the shared scope; a machine-scoped one uses
        the current machine. Never blocks, never raises."""
        from . import engine as engine_mod

        poll_secs = engine_mod.POLL_SECS
        if self.progress is not None:
            return
        now = time.monotonic()
        due = poll_secs > 0 and now - getattr(self, "_last_worker_poll", 0.0) >= 2 * poll_secs
        if due:
            self._last_worker_poll = now
        for d in getattr(self, "pivots", None) or []:
            reg = d.get("pivot")
            if reg is None or getattr(reg, "worker", None) is None:
                continue
            try:
                scope = "" if reg.account_scoped else self._pivot_machine_id()
                if scope is None:
                    continue
                rt = self._pivot_runtime(reg)
                rt.ensure(scope)
                if due:
                    rt.repoll(scope)
            except Exception:
                continue
    def on_inbox_updated(self, message) -> None:
        """Wake from ``self.inbox.post(...)``. Draining here (not just on
        the next ``_tick``) gives a background producer's outcome prompt
        effect even if the screen's own tick were ever slowed/paused; the
        tick below drains too, so applying twice for the same batch is a
        guaranteed-safe no-op (``Inbox.drain`` empties what it returns)."""
        message.stop()
        self._drain_inbox()
    def _drain_inbox(self) -> int:
        """Apply every closure posted to ``self.inbox`` since the last
        drain. The sole place that turns a background producer's posted
        outcome into actual widget/state mutation -- see inbox.py.

        ``Inbox.drain_apply()`` deliberately re-raises a closure's own
        exception (so it's never silently swallowed) -- but this method is
        invoked from ``_tick()`` and the ``InboxUpdated`` message handler,
        both squarely on the render flow: letting an uncaught producer bug
        escape here would terminate rendering entirely, for every producer
        sharing this one inbox, not just the one that actually failed. Each
        producer's closure is expected to record its own diagnosed failure
        before it could ever raise (see the setup-reload worker's `_apply`
        for the pattern) -- this is the last-resort net for one that
        doesn't, logged so it's at least diagnosable rather than silently
        lost along with the render flow.
        """
        try:
            return self.inbox.drain_apply()
        except Exception:
            log.warning(
                "_drain_inbox: a posted closure raised -- the render flow "
                "continues, but this producer's own outcome was not fully "
                "applied and recorded no diagnosed failure of its own",
                exc_info=True,
            )
            return 0
    def _tick(self):
        self._drain_inbox()
        self.frame += 1
        if self._frame_health is not None:
            self._frame_health.tick(
                frame=self.frame,
                debug=self.debug,
                busy=self._busy_label,
            )
        self.pulse = (self.frame // 5) % 2
        busy = False
        # A background action (_run_bg) drives the footer spinner: keep the tick
        # at full fps so it animates.
        if self._busy_label:
            busy = True
        # In live mode, stream in worktrees as each machine's load resolves.
        if self.live and self.loader is not None:
            self._apply_loader_records()
            _ready, loading, _failed = self.loader.counts()
            busy = busy or loading > 0
            self._maybe_repoll()
            if self._wt_reconcile_after is not None:
                self._process_pending_wt_reconcile()
        # Keep an open registered pivot (Tasks) live too -- independent of the
        # worktree loader / live mode, so a card posted by another session shows
        # up without a manual reload (#staleness).
        self._maybe_repoll_pivot()
        self._maybe_refresh_worker_pivots()
        # Poll the launcher's update stage ~twice a second (#1430), keeping
        # the tick busy (spinner animating) while a stage is in flight.
        if self.frame % 5 == 0:
            self._poll_update_state()
        if self.update_state == "checking":
            busy = True
        # ProgressScreen/MsgViewScreen (#88 F4) are native ModalScreens that
        # tick their own advancement/repaint now, so this tick no longer
        # nudges them.
        # Full 10fps only while something animates (SSH spinner/progress
        # dialog); idle throttles to ~2fps since keystrokes already repaint
        # synchronously -- this only paces the cosmetic pulse and avoids
        # flooding an SSH/tmux link with full-screen repaints.
        # Held-arrow flood guard: a pending nav refresh is coalesced to the
        # tick instead of firing per keystroke, so it can't outrun draining.
        nav = self._nav_dirty
        self._nav_dirty = False
        if busy or nav:
            self.refresh()
        elif self.frame % 5 == 0:
            # pivot-streaming-transport Phase 4: this is the ONLY branch that
            # can fire with `busy` and `nav` both false -- a purely
            # clock-driven cosmetic pulse tick, nothing else changed. Narrow
            # the segment refresh accordingly (see `_refresh_nf_segments`'s
            # own audit of exactly which segments that's safe for).
            self.refresh(cause="pulse")
    def _advance_progress(self):
        """Drive the progress sub-dialog forward.

        When the run is unarmed (awaiting the extra confirm) nothing advances.
        With the real executor active, mirror its per-item states; otherwise run
        the mock walker (the safe simulation): walk the selected worktrees one
        at a time (pending -> running -> done), paced by the render tick."""
        p = self.progress
        if not p.get("armed", True):
            return
        if p.get("kind") == "action-stream":
            # Driven by the D4 reader thread, not the mock walker.
            return
        if self.executor is not None:
            self._poll_executor()
            return
        p["ticks"] += 1
        cur = p["ticks"] // p["steps"]   # index currently "running"
        items = p["items"]
        for j, it in enumerate(items):
            if it["state"] == "failed":
                continue
            if j < cur:
                it["state"] = "done"
            elif j == cur:
                it["state"] = "running"
            else:
                it["state"] = "pending"
        if cur >= len(items):
            p["done"] = True
            for it in items:
                if it["state"] != "failed":
                    it["state"] = "done"
    def _prime_setup_reload(self):
        # Kick off the data_ssh prewarm import FIRST, before anything else in
        # this method -- including the pivot-registry scan below. The prewarm
        # exists specifically so the FIRST switch onto a registered pivot
        # (e.g. Tasks) never pays a synchronous multi-module import on the
        # render/key-handling thread (see prewarm_optional_modules' own
        # docstring): it only closes that race if it gets the earliest
        # possible head start. The previous ordering ran the (potentially
        # slow, synchronous) pivot-registry scan FIRST and only started the
        # prewarm thread after it returned -- so by the time this call
        # finally unblocked the render thread and the operator's next
        # keypress landed (often immediately, since the app *looks* ready
        # the moment input resumes), the prewarm thread had barely started,
        # and a fast pivot-switch keypress still raced (and often lost
        # against) the same import lock this was meant to avoid. Starting it
        # first lets it run concurrently with the scan instead of after it,
        # maximizing its lead time over the operator's next keypress.
        from . import tasks as _tasks_mod; threading.Thread(target=_tasks_mod.prewarm_optional_modules, daemon=True).start()
        # Also prewarm the (uncached, and far more expensive -- 2+ seconds on
        # a machine with many registered repos) machine-key-map RESULT, not
        # just the data_ssh import -- see _prewarm_machine_key_map's own
        # docstring. Started here too, for the same maximize-the-head-start
        # reason as the import prewarm above.
        self._prewarm_machine_key_map()

    def _invalidate_setup_reload_caches(self):
        # A manual reload ('r') must also refresh the registered Tasks pivot, not
        # just the worktree lists (the pivot runtime is separate + has no TTL):
        # clear each pivot runtime's cache so the next frame's ensure() refetches,
        # so a task/card created by another session appears on demand.
        for _rt in getattr(self, "_pivot_runtimes", {}).values():
            try:
                _rt.invalidate()
            except Exception:
                pass
        try:
            from .. import project_config as _cfg

            _cfg.clear_caches()
        except Exception:
            pass

    def _ensure_setup_epoch_lock(self) -> threading.Lock:
        """Lazily resolve ``self._setup_epoch_lock`` -- the dedicated lock
        serializing epoch allocation (``_next_setup_epoch``) against the
        wake-failure fallback's own epoch-currency check + failure
        publication (see ``_start_setup_reload_worker``). Without a SHARED
        lock across both, "check epoch currency, then publish" is a
        classic check-then-act race: a newer reload can allocate a fresh
        epoch (and, if its own wake also fails, publish its own newer
        failure) in the gap between an older worker's check passing and
        its own publish actually running, letting that older worker
        downgrade `_setup_failed_epoch` back down afterward.

        Guarded by a shared, module-level lock (`_SETUP_EPOCH_LOCK_INIT_LOCK`)
        so this lazy construction itself can't race two different
        `Lock()` instances into existence for the SAME screen -- each
        screen still ends up with its own dedicated `_setup_epoch_lock`,
        created at most once. Production code reaches it the normal way
        via `PickerScreen.__init__` constructing it eagerly; a test
        double that never ran that `__init__` gets it lazily instead.
        """
        lock = getattr(self, "_setup_epoch_lock", None)
        if lock is not None:
            return lock
        with _SETUP_EPOCH_LOCK_INIT_LOCK:
            lock = getattr(self, "_setup_epoch_lock", None)
            if lock is None:
                lock = self._setup_epoch_lock = threading.Lock()
            return lock

    def _next_setup_epoch(self) -> int:
        with self._ensure_setup_epoch_lock():
            self._setup_epoch += 1
            return self._setup_epoch

    def _dispose_setup_payload(self, payload: _SetupPayload | None) -> None:
        if payload is None:
            return
        loader = payload.loader
        cancel = getattr(loader, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    def _track_setup_payload(self, epoch: int, payload: _SetupPayload | None) -> None:
        if payload is None:
            return
        with self._setup_payloads_lock:
            self._pending_setup_payloads[epoch] = payload

    def _release_setup_payload(self, epoch: int) -> _SetupPayload | None:
        with self._setup_payloads_lock:
            return self._pending_setup_payloads.pop(epoch, None)

    def _dispose_pending_setup_payloads(self) -> None:
        with self._setup_payloads_lock:
            pending = list(self._pending_setup_payloads.values())
            self._pending_setup_payloads.clear()
        for payload in pending:
            self._dispose_setup_payload(payload)

    def _collect_setup_payload(self) -> _SetupPayload:
        """Collect the synchronous setup/reload inputs with no widget mutation."""
        loader = None
        try:
            pivot_payload = self._scan_pivot_payload()
            snapshot_fn = getattr(self.src, "source_snapshot", None)
            with self._load_config_cache_scope():
                source_snapshot = snapshot_fn() if callable(snapshot_fn) else None
            # Source tabs gain a leading "All" entry that interleaves every source.
            source_tabs = getattr(self.src, "source_tabs", None)
            if callable(source_tabs):
                tabs = list(
                    source_tabs(source_snapshot)
                    if source_snapshot is not None
                    else source_tabs()
                )
            else:
                machine_tabs = (
                    self.src.machines(source_snapshot)
                    if source_snapshot is not None
                    else self.src.machines()
                )
                tabs = [
                    {
                        "label": label,
                        "machine": machine,
                        "env": env,
                        "ready": ready,
                        "source_kind": "machine-ssh",
                        "source_id": None,
                        "capabilities": {},
                    }
                    for label, machine, env, ready in machine_tabs
                ]
            source_tabs = [{
                "label": "All",
                "machine": None,
                "env": None,
                "ready": True,
                "source_kind": "all",
                "source_id": None,
                "capabilities": {},
            }, *tabs]
            local = next(
                (
                    (tab.get("machine"), tab.get("env"))
                    for tab in source_tabs
                    if tab.get("local")
                ),
                None,
            )
            # Warm ``self.src.LOCAL`` unconditionally, off the render thread
            # (this method already runs on the setup/reload worker thread),
            # even when ``local`` was already found via ``source_tabs`` above
            # (the real ``data_ssh`` source always sets a ``"local"`` key per
            # tab, so the ``if local is None:`` fallback below was otherwise
            # dead in practice). ``data_ssh.LOCAL`` is a PEP 562 module
            # attribute resolved lazily on first access (see
            # ``data_ssh._resolve_local()``) and memoized forever after --
            # that first resolution reads
            # ``worktree_manager.production_picker.project_config``'s
            # ``lru_cache``-backed project/machine lookups (a real file read
            # plus an ``engine_client`` cross-process call the first time;
            # free on every call after, module-wide). It must be touched HERE
            # so that one-time cost lands on this worker thread, not left for
            # whichever later render-thread comparison (e.g.
            # ``_wt_submenu_verbs()``'s ``(machine, env) == self.src.LOCAL``,
            # reached the instant the operator opens ANY worktree row's
            # Actions menu) touches it first (#picker-menu-open-latency).
            with self._load_config_cache_scope():
                try:
                    self.src.LOCAL
                except Exception:
                    pass
                if local is None:
                    try:
                        local = self.src.LOCAL
                    except Exception:
                        local = self._source_local
                try:
                    source_repo_branch = (
                        getattr(self.src, "REPO", "") or "",
                        getattr(self.src, "BRANCH", "") or "",
                    )
                except Exception:
                    source_repo_branch = ("", "")
            if self.live:
                # Real background SSH loads: one daemon thread per machine,
                # spinner -> ✓/✗. Every source -- local included -- streams in on a
                # thread (#1432), so the picker paints and accepts keys immediately;
                # seed self.data empty and let the render tick fill it as each
                # machine resolves.
                loader = (
                    self.src.make_loader(source_snapshot)
                    if source_snapshot is not None
                    else self.src.make_loader()
                )
                # Load only the local tab in full up front; every other ready
                # remote gets a cheap connectivity ping instead until the
                # operator actually navigates onto its tab
                # (picker-lazy-per-machine-loading; see LiveLoader.start's own
                # docstring). ``local`` is a plain (machine, env) tuple or None.
                start_loader(loader, focus_keys={local} if local else None)
                data = loader.records()
                load_delay = {}
            else:
                data = self.src.load()
                # Simulate background SSH status loads: local is instant, remotes
                # stagger in, the unreachable one (book2) is permanently disabled.
                load_delay = {}
                d = 1.4
                for i, tab in enumerate(source_tabs):
                    label = tab["label"]
                    m = tab.get("machine")
                    e = tab.get("env")
                    ok = bool(tab.get("ready"))
                    if label == "All" or (m, e) == self._src_local() or not ok:
                        load_delay[i] = 0.0
                    else:
                        load_delay[i] = d
                        d += 1.1
            # Profiles matrix axes are config-bound from machines.yaml (via the data
            # source); fall back to the built-in defaults for sources that don't
            # provide them (e.g. fixture sources in tests).
            hc = getattr(self.src, "host_cols", None)
            te = getattr(self.src, "target_envs", None)
            with self._load_config_cache_scope():
                host_cols = (hc() if callable(hc) else None) or list(_DEFAULT_HOST_COLS)
                target_env_list = (te() if callable(te) else None) or _DEFAULT_TARGET_ENVS
            return _SetupPayload(
                pivot_payload=pivot_payload,
                source_tabs=source_tabs,
                source_local=local,
                source_repo_branch=source_repo_branch,
                loader=loader,
                data=data,
                load_delay=load_delay,
                host_cols=host_cols,
                target_env_list=target_env_list,
            )
        except Exception:
            if loader is not None:
                cancel = getattr(loader, "cancel", None)
                if callable(cancel):
                    try:
                        cancel()
                    except Exception:
                        pass
            raise

    def _apply_setup_payload(self, payload: _SetupPayload) -> None:
        """Install a collected setup/reload payload. UI-thread only."""
        if self.debug == "loading":
            self._busy_label = None
            self.debug = "ready"
        self._install_pivot_payload(payload.pivot_payload)
        self.source_tabs = payload.source_tabs
        self.machines = [
            (
                tab["label"],
                tab.get("machine"),
                tab.get("env"),
                bool(tab.get("ready")),
            )
            for tab in self.source_tabs
        ]
        self._source_local = payload.source_local or self._source_local
        self._source_repo_branch = payload.source_repo_branch
        self.machine_idx = self.local_index()
        self.maint_sel = ListSelection()  # drop any stale Maintenance selection
        # Worktrees selection persists across reload (#2258 P3-7): it is NOT
        # hard-cleared here. Survivors are kept and vanished rows dropped by
        # _reconcile_wt_sel() at the end of setup, once the reloaded records are
        # available.
        self._last_poll = time.monotonic()   # first background poll is POLL_SECS out
        self._last_pivot_poll = time.monotonic()  # registered-pivot repoll (#staleness)
        previous_loader = getattr(self, "loader", None)
        self.loader = payload.loader
        if previous_loader is not None and previous_loader is not self.loader:
            cancel = getattr(previous_loader, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass
        self.data = payload.data
        if self.live:
            self.load_delay = {}
        else:
            self.t0 = time.monotonic()
            self.load_delay = dict(payload.load_delay)
        self.host_cols = payload.host_cols
        target_env_list = payload.target_env_list
        # Profiles matrix: seed a "self · agent" profile on each host.
        self.targets = target_rows(target_env_list)
        self.grid = {}
        for ti, t in enumerate(self.targets):
            for hi in range(len(self.host_cols)):
                self.grid[(ti, hi)] = self.cell_locked(ti, hi)
        self.applied = dict(self.grid)   # everything starts "applied"
        self._prof_unavailable = set()   # cleared until a load marks columns
        # Real per-host columns: when the data source exposes profile IO
        # (production data_local / data_ssh), stream each host's saved column
        # in on a background thread so SSH never blocks the UI. Sources without
        # these hooks (fixtures/tests) keep the seeded self·agent diagonal.
        self._prof_load = getattr(self.src, "load_profile_column", None)
        self._prof_apply = getattr(self.src, "apply_profile_column", None)
        self._prof_loaded = False
        if callable(self._prof_load):
            self.profiles_view.start_load()
        # Phase 3d Step 6: the local Group C reconcile is now folded into the
        # authoritative classify load itself (`data_local.load(classify=True)`),
        # so setup/reload no longer spawns separate post-apply PR/bound-live
        # reconcile threads. Keep the legacy flags true for tests and any
        # observational call sites: once this payload applies, the current epoch
        # already includes the freshest available Group C overlay.
        self._pr_reconciled = True
        self._bound_live_reconciled = True
        # Reconcile the persisted Worktrees selection against the freshly loaded
        # records (#2258 P3-7): keep survivors, drop rows that vanished, re-seat
        # a now-invalid range anchor. A no-op while records are still streaming.
        self._roster_ready = True
        self._reconcile_wt_sel()

    def _apply_setup_failure(self, epoch: int, err: Exception) -> None:
        self._setup_failed_epoch = epoch
        self._busy_label = "Load failed"
        self.debug = f"setup-failed: {err}"

    def _publish_setup_failure_if_current(
        self, epoch: int, cancel: threading.Event, err: Exception
    ) -> None:
        """The ONE place that publishes a diagnosed setup/reload failure
        -- every potentially-superseded call site (off-thread, or
        synchronous-but-still-racing-with-another-thread's-own-epoch-
        allocation) routes through here instead of calling
        ``_apply_setup_failure`` directly. Re-checking epoch currency
        immediately before publishing is not, by itself, atomic with
        epoch ALLOCATION (``_next_setup_epoch``) -- a newer reload could
        still interleave between that check passing and the publish
        actually running without a lock shared across both. Holding
        ``_ensure_setup_epoch_lock()`` here closes that window: while
        this call holds it, `_next_setup_epoch()` (which acquires the
        very same lock) cannot allocate a fresh epoch out from under this
        check, and vice versa.
        """
        with self._ensure_setup_epoch_lock():
            if not cancel.is_set() and epoch == self._setup_epoch:
                self._apply_setup_failure(epoch, err)

    def _start_setup_reload_worker(self) -> int:
        """Schedule a setup/reload collect pass and apply it only if current."""
        self._prime_setup_reload()
        epoch = self._next_setup_epoch()
        cancel = self._bg_cancel
        # A minimal test double that never ran PickerScreen.__init__ still
        # gets a working Inbox here, lazily -- resolved on this (the calling)
        # thread, before the worker below ever touches it.
        inbox = ensure_inbox(self)
        app_lookup_error: Exception | None = None
        try:
            app = self.app
        except Exception as exc:
            app = None
            app_lookup_error = exc

        def _worker():
            payload = None
            err = None
            try:
                payload = self._collect_setup_payload()
            except Exception as exc:
                err = exc
            self._track_setup_payload(epoch, payload)
            if cancel.is_set() or epoch != self._setup_epoch:
                self._dispose_setup_payload(self._release_setup_payload(epoch))
                return

            def _apply():
                if cancel.is_set() or epoch != self._setup_epoch:
                    self._dispose_setup_payload(self._release_setup_payload(epoch))
                    return
                if err is not None:
                    self._dispose_setup_payload(self._release_setup_payload(epoch))
                    self._publish_setup_failure_if_current(epoch, cancel, err)
                    self.refresh()
                    return
                payload_to_apply = self._release_setup_payload(epoch) or payload
                try:
                    self._invalidate_setup_reload_caches()
                    self._apply_setup_payload(payload_to_apply)
                except Exception as apply_exc:
                    # Record a diagnosed failure instead of letting this
                    # escape -- this closure runs inside
                    # ``Inbox.drain_apply()`` on the render flow itself
                    # (via ``_tick()``/``on_inbox_updated``), so a bare
                    # re-raise here would propagate into Textual's render
                    # loop and could terminate it, with no
                    # `_setup_failed_epoch` ever recorded for a poller to
                    # see (the exact #5220 failure mode, just triggered by
                    # `_apply_setup_payload` instead of a wake failure).
                    self._dispose_setup_payload(payload_to_apply)
                    self._publish_setup_failure_if_current(epoch, cancel, apply_exc)
                    self.refresh()
                    return
                self._setup_applied_epoch = epoch
                self.refresh()

            if app is None:
                # `self.app` was not resolvable when this worker was
                # scheduled (e.g. this screen wasn't yet mounted into a
                # running App). This used to silently drop the collected
                # payload -- neither `_setup_applied_epoch` nor
                # `_setup_failed_epoch` was ever set, so a poller (most
                # notably capture.py's `_wait_for_initial_setup`, used by
                # the headless `picker screenshot`/`picker mock` capture
                # path) would spin until ITS OWN unrelated timeout instead
                # of ever seeing the real cause (#5220). Record a
                # diagnosed failure directly (best-effort: there is no
                # live App to hop back onto via `call_from_thread` here,
                # so this mutates the screen's attributes off-thread,
                # exactly as already-established off-thread pollers like
                # `_poll_update_state` do) rather than dropping silently.
                self._dispose_setup_payload(self._release_setup_payload(epoch))
                self._publish_setup_failure_if_current(
                    epoch,
                    cancel,
                    app_lookup_error
                    or RuntimeError(
                        "no Textual App was resolvable for this screen when "
                        "the setup/reload worker was scheduled (self.app "
                        "was None)"
                    ),
                )
                return
            wake_error: list[Exception] = []
            if not inbox.post(
                f"setup-reload:{epoch}", _apply, on_wake_failed=wake_error.append
            ):
                # The posted `_apply` closure is still sitting in the inbox
                # at this point (post() only failed to *wake* the render
                # flow, never the record itself) -- if left there, a later,
                # unrelated drain (e.g. the next `_tick()`) would still
                # invoke it, re-disposing/re-applying state this fallback
                # is about to tear down itself. Discard it first so this is
                # the only path that ever decides this epoch's outcome.
                #
                # But a wake failure and `_tick()`'s own periodic drain are
                # racing each other independently: `_tick()` drains
                # unconditionally, whether or not THIS post's wake
                # succeeded, so a proactive tick landing between `post()`
                # failing and this `discard()` call can already have
                # claimed and run `_apply` itself. `discard()`'s own
                # return value is the single source of truth for who won
                # that race: `True` means this call genuinely removed a
                # still-pending closure (nothing else could have run it),
                # so the fallback below is this worker's alone to run.
                # `False` means `_apply` already ran via the render flow --
                # whatever outcome IT recorded (success or its own
                # diagnosed failure) is authoritative, and disposing the
                # payload or recording a conflicting failure here would be
                # wrong (the payload may already be in active use, or a
                # genuine success already recorded would be incorrectly
                # overwritten as failed).
                #
                # #5220's other traced failure mode: the App's event loop
                # not running, or already stopped/shutting down. A poller
                # elsewhere (capture.py's `_wait_for_initial_setup`) must
                # see the real cause instead of spinning until its own
                # unrelated timeout, so this records a diagnosed failure
                # directly -- the same narrow, already-established
                # off-thread-mutation exception as the "app is None" branch
                # above, for the same reason: there is nothing else to hand
                # this outcome to.
                if inbox.discard(f"setup-reload:{epoch}"):
                    self._dispose_setup_payload(self._release_setup_payload(epoch))
                    # Prefer the real underlying exception `post_message`
                    # raised, when there was one -- falling back to a
                    # generic message only when the wake instead returned
                    # `False` without raising at all (an already-closing/
                    # closed pump), which carries no further detail of
                    # its own. `_publish_setup_failure_if_current` is what
                    # actually guards this against a newer, superseding
                    # epoch (see its own docstring for the exact race).
                    underlying = wake_error[0] if wake_error else None
                    self._publish_setup_failure_if_current(
                        epoch,
                        cancel,
                        underlying or RuntimeError(
                            "could not wake the render flow to apply "
                            "this setup/reload payload"
                        ),
                    )

        threading.Thread(
            target=_worker, name=f"picker-setup-reload:{epoch}", daemon=True
        ).start()
        return epoch

    def setup_sync_for_tests(self):
        """Run setup/reload synchronously for unit tests only.

        Production entrypoints must schedule ``_start_setup_reload_worker()``
        instead of calling the collect/apply path inline on the UI thread.
        """
        self._prime_setup_reload()
        epoch = self._next_setup_epoch()
        try:
            payload = self._collect_setup_payload()
        except Exception:
            self._setup_failed_epoch = epoch
            raise
        try:
            self._invalidate_setup_reload_caches()
            self._apply_setup_payload(payload)
        except Exception:
            self._dispose_setup_payload(payload)
            raise
        self._setup_applied_epoch = epoch
