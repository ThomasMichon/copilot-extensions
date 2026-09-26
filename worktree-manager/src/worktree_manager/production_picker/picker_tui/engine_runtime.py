#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import threading
import time

from .engine_helpers import _DEFAULT_HOST_COLS, _DEFAULT_TARGET_ENVS, start_loader, target_rows
from .selection import ListSelection
from .. import update_stage

class PickerScreenRuntimeMixin:
    def _poll_update_state(self):
        """Refresh the cached update-indicator state from the stage status.

        Cheap (two small files) and never fatal -- a read hiccup leaves the
        last state in place. Kept off the SSH/tmux hot path by the frame
        throttle in ``_tick``. A no-op once ``_update_state_pinned`` is set
        (:func:`capture.capture_async`'s ``update_state`` override, applied
        after this poll's ``call_after_refresh`` scheduling): this callback's
        exact fire time relative to that override is not guaranteed by pause
        count alone (Textual's mount lifecycle can defer it later than any
        fixed number of ``pilot.pause()`` calls), so an unconditional
        assignment here could silently clobber an explicit test/audit
        override moments after it was set -- a real, observed capture-race,
        not just a hypothetical one."""
        if getattr(self, "_update_state_pinned", False):
            return
        try:
            self.update_state = update_stage.indicator_state()
        except Exception:
            pass
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
        # PRs are reconciled separately via the #1423 path in setup().
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
    def _tick(self):
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
        if busy or nav or self.frame % 5 == 0:
            self.refresh()
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
    def setup(self):
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
        # Re-scan the pivot registry so a refresh ('r') picks up a newly
        # installed (or removed) contributed pivot without a picker restart.
        self._load_pivots()
        # A manual reload ('r') must also refresh the registered Tasks pivot, not
        # just the worktree lists (the pivot runtime is separate + has no TTL):
        # clear each pivot runtime's cache so the next frame's ensure() refetches,
        # so a task/card created by another session appears on demand.
        for _rt in getattr(self, "_pivot_runtimes", {}).values():
            try:
                _rt.invalidate()
            except Exception:
                pass
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
        self.source_tabs = [{
            "label": "All",
            "machine": None,
            "env": None,
            "ready": True,
            "source_kind": "all",
            "source_id": None,
            "capabilities": {},
        }, *tabs]
        self.machines = [
            (
                tab["label"],
                tab.get("machine"),
                tab.get("env"),
                bool(tab.get("ready")),
            )
            for tab in self.source_tabs
        ]
        local = next(
            (
                (tab.get("machine"), tab.get("env"))
                for tab in self.source_tabs
                if tab.get("local")
            ),
            None,
        )
        if local is None:
            try:
                local = self.src.LOCAL
            except Exception:
                local = self._source_local
        self._source_local = local or self._source_local
        with self._load_config_cache_scope():
            try:
                self._source_repo_branch = (
                    getattr(self.src, "REPO", "") or "",
                    getattr(self.src, "BRANCH", "") or "",
                )
            except Exception:
                self._source_repo_branch = ("", "")
        self.machine_idx = self.local_index()
        self.maint_sel = ListSelection()  # drop any stale Maintenance selection
        # Worktrees selection persists across reload (#2258 P3-7): it is NOT
        # hard-cleared here. Survivors are kept and vanished rows dropped by
        # _reconcile_wt_sel() at the end of setup, once the reloaded records are
        # available.
        self._last_poll = time.monotonic()   # first background poll is POLL_SECS out
        self._last_pivot_poll = time.monotonic()  # registered-pivot repoll (#staleness)
        if self.live:
            # Real background SSH loads: one daemon thread per machine,
            # spinner -> ✓/✗. Every source -- local included -- streams in on a
            # thread (#1432), so the picker paints and accepts keys immediately;
            # seed self.data empty and let the render tick fill it as each
            # machine resolves.
            self.data = []
            self.loader = (
                self.src.make_loader(source_snapshot)
                if source_snapshot is not None
                else self.src.make_loader()
            )
            # Load only the local tab in full up front; every other ready
            # remote gets a cheap connectivity ping instead until the
            # operator actually navigates onto its tab
            # (picker-lazy-per-machine-loading; see LiveLoader.start's own
            # docstring). ``local`` is a plain (machine, env) tuple or None.
            start_loader(self.loader, focus_keys={local} if local else None)
            self.data = self.loader.records()
        else:
            self.data = self.src.load()
            # Simulate background SSH status loads: local is instant, remotes
            # stagger in, the unreachable one (book2) is permanently disabled.
            self.t0 = time.monotonic()
            self.load_delay = {}
            d = 1.4
            for i, (label, m, e, ok) in enumerate(self.machines):
                if label == "All" or (m, e) == self._src_local() or not ok:
                    self.load_delay[i] = 0.0
                else:
                    self.load_delay[i] = d
                    d += 1.1
        # Profiles matrix axes are config-bound from machines.yaml (via the data
        # source); fall back to the built-in defaults for sources that don't
        # provide them (e.g. fixture sources in tests).
        hc = getattr(self.src, "host_cols", None)
        te = getattr(self.src, "target_envs", None)
        with self._load_config_cache_scope():
            self.host_cols = (hc() if callable(hc) else None) or list(_DEFAULT_HOST_COLS)
            target_env_list = (te() if callable(te) else None) or _DEFAULT_TARGET_ENVS
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
        # Best-effort background PR-state reconcile (#1423): correct already-
        # merged-but-stale PRs in the tracking store, then re-render so the
        # Picker stops showing merged worktrees as having open PRs. Sources
        # without the hook (fixtures/tests) skip it.
        self._pr_reconciled = False
        rec_fn = getattr(self.src, "reconcile_prs", None)
        if callable(rec_fn):
            self._start_pr_reconcile(rec_fn)
        # #4057/#1416: same after-first-paint pattern for the cached bound-Copilot
        # liveness reconcile -- surfaces a bare-resumed session (cwd=home) in the
        # Active section without a live per-worktree scan on the populate path.
        self._bound_live_reconciled = False
        blr_fn = getattr(self.src, "reconcile_bound_live", None)
        if callable(blr_fn):
            self._start_bound_live_reconcile(blr_fn)
        # Reconcile the persisted Worktrees selection against the freshly loaded
        # records (#2258 P3-7): keep survivors, drop rows that vanished, re-seat
        # a now-invalid range anchor. A no-op while records are still streaming.
        self._roster_ready = True
        self._reconcile_wt_sel()
    def _start_pr_reconcile(self, rec_fn):
        """Reconcile stale local PR states off the UI thread, then reload (#1423).

        Never blocks the first paint and never raises: a provider that is
        unconfigured/unreachable simply leaves the tracking state as-is. Only
        triggers a reload when something actually changed, so an all-current
        store costs one silent pass.
        """
        def work():
            try:
                changed = rec_fn()
            except Exception:
                changed = 0
            if changed:
                try:
                    self._reload_local_after_reconcile()
                except Exception:
                    pass
            self._pr_reconciled = True

        threading.Thread(target=work, name="pr-reconcile", daemon=True).start()
    def _start_bound_live_reconcile(self, blr_fn):
        """Reconcile cached bound-Copilot liveness off the UI thread, then reload.

        Mirrors :meth:`_start_pr_reconcile` (#4057/#1416): resolves the machine's
        live bound Copilots via the authoritative scan and stamps each worktree's
        cached ``bound_live`` so a bare-resumed session surfaces ACTIVE. Never
        blocks the first paint and never raises; only reloads when a bound-liveness
        transition actually happened (an all-current store costs one silent pass).
        """
        def work():
            try:
                changed = blr_fn()
            except Exception:
                changed = 0
            if changed:
                try:
                    self._reload_local_after_reconcile()
                except Exception:
                    pass
            self._bound_live_reconciled = True

        threading.Thread(
            target=work, name="bound-live-reconcile", daemon=True).start()
    def _reload_local_after_reconcile(self):
        """Re-fetch the local machine's rows after a PR reconcile wrote back.

        Mirrors the post-maintenance reload (#1421): in live mode the local
        source re-threads and the render tick picks up the fresh records; in the
        non-live path the data is reloaded in-place."""
        m, e = self._src_local()
        if self.live and self.loader is not None:
            self.loader.reload(m, e)
        elif not self.live:
            self.data = self.src.load()
