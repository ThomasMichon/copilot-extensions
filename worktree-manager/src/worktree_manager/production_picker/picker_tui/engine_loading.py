#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import os
import threading
import time

from .engine_helpers import _DEFAULT_HOST_COLS, _DEFAULT_TARGET_ENVS, target_rows
from .selection import ListSelection

class PickerScreenLoadingMixin:
    def on_mount(self):
        if self.live:
            # Chrome first: paint built-in tabs + empty lists, then run the
            # roster/pivot/worktree fill off the UI thread.
            self._setup_skeleton()
        else:
            self.setup()
        self._finish_mount()
        # Record the completed first refresh in every Picker mode. Live data
        # startup also waits for this boundary so it cannot contend with paint.
        self.call_after_refresh(self._after_first_refresh)
    def _after_first_refresh(self):
        if (
            os.environ.get("AGENT_WORKTREES_PICKER_FRAME_HEALTH")
            or os.environ.get("AGENT_WORKTREES_LAUNCH_TRACE")
        ):
            from .frame_health import FrameHealthReporter

            self._frame_health = FrameHealthReporter.from_env()
            if self._frame_health is not None:
                self._frame_health.start()
        callback = self._after_first_refresh_callback
        if callable(callback):
            threading.Thread(
                target=callback,
                name="picker-after-first-refresh",
                daemon=True,
            ).start()
        if not self.live:
            return
        threading.Thread(
            target=self._setup_live_async,
            name="picker-setup",
            daemon=True,
        ).start()
    def _record_first_refresh(self):
        """Compatibility alias for older tests and launch-trace call sites."""
        self._after_first_refresh()
    def _finish_mount(self):
        self.sel = self.default_sel()
        # NF: focus the region widget that owns the default sel (deferred until
        # the children are mounted). ``_nf_mounted`` stays False until then so the
        # framework's mount-time auto-focus can't move sel.
        self.call_after_refresh(self._nf_initial_focus)
        # Update indicator (#1430): the launcher stages the marketplace update
        # in the background; the picker polls its status file to show a
        # spinner -> checkmark (current) / refresh (update available) next to
        # the version. Deferred to the first refresh (picker-startup-latency
        # follow-up): its FIRST call pays the (otherwise lazily-deferred)
        # engine-runtime import cost, which used to run synchronously in
        # ``on_mount`` -- before Textual's first paint. ``update_state``
        # starts "idle" so the very first frame just shows no glyph rather
        # than blocking on it; the deferred poll corrects it moments later,
        # same always-async-then-refine pattern used elsewhere in this file.
        self.update_state = "idle"
        self._upd_poll_frame = -1
        self.call_after_refresh(self._poll_update_state)
        # Manager-self update check (distinct from update_state above, which
        # is the engine/marketplace payload's own staged-update signal --
        # see manager_update_check's module docstring for why conflating the
        # two read as "the checkmark says I'm current" when it never checked
        # the Manager's own version at all). Cheap/cached: a picker relaunch
        # within CHECK_INTERVAL_SECS reads the persisted result with no
        # network hit; only a stale/missing cache triggers a real background
        # fetch here.
        self.manager_update_state = "idle"
        self.call_after_refresh(self._poll_manager_update_state)
        # ~10 fps drives the SSH spinner and the slower live-glyph pulse.
        self.set_interval(0.1, self._tick)
    def _setup_skeleton(self):
        """First-paint chrome with no config, roster, or registry I/O."""
        self._roster_ready = False
        self._load_pivots(scan=False)
        self.data = []
        self.loader = None
        self.debug = "loading"
        self._busy_label = "Loading…"
        self._last_poll = time.monotonic()
        self._last_pivot_poll = time.monotonic()
        try:
            from . import data_local

            machine, env = data_local.LOCAL
            label = f"{machine} {env}"
        except Exception:
            machine, env, label = None, None, "local"
        self.source_tabs = [
            {
                "label": "All",
                "machine": None,
                "env": None,
                "ready": True,
                "source_kind": "all",
                "source_id": None,
                "capabilities": {},
            },
            {
                "label": label,
                "machine": machine,
                "env": env,
                "ready": True,
                "source_kind": "machine-ssh",
                "source_id": None,
                "capabilities": {},
            },
        ]
        self.machines = [
            (tab["label"], tab.get("machine"), tab.get("env"), bool(tab.get("ready")))
            for tab in self.source_tabs
        ]
        self._source_local = (machine, env)
        self.machine_idx = 1 if len(self.machines) > 1 else 0
        self.maint_sel = ListSelection()
        self.host_cols = list(_DEFAULT_HOST_COLS)
        self.targets = target_rows(_DEFAULT_TARGET_ENVS)
        self.grid = {}
        self.applied = {}
        self._prof_unavailable = set()
    def _setup_live_async(self):
        """Publish bootstrap rows, then fill roster and pivots independently."""
        bootstrap_fn = getattr(self.src, "bootstrap_rows", None)
        if callable(bootstrap_fn):
            try:
                bootstrap_rows = bootstrap_fn()
            except Exception:
                bootstrap_rows = None
            if bootstrap_rows is not None:

                def apply_bootstrap():
                    if not self._roster_ready and self.loader is None:
                        self.data = bootstrap_rows
                        self.refresh()

                self._apply_from_worker(apply_bootstrap)

        threading.Thread(
            target=self._setup_live_pivots,
            name="picker-pivots",
            daemon=True,
        ).start()

        err = None
        loader = None
        prepared = None
        try:
            snapshot_fn = getattr(self.src, "source_snapshot", None)
            snapshot = snapshot_fn() if callable(snapshot_fn) else None
            prepared = self._prepare_live_source(snapshot)
            loader = (
                self.src.make_loader(snapshot)
                if snapshot is not None
                else self.src.make_loader()
            )
            loader.start()
        except Exception as exc:
            err = exc

        def apply():
            if err is not None:
                self.debug = f"setup-failed: {err}"
                self._busy_label = "Load failed"
                self.refresh()
                return
            self._apply_live_source(prepared, loader)
            self._busy_label = None
            self.refresh()

        self._apply_from_worker(apply)
    def _setup_live_pivots(self):
        """Scan contributed pivots without delaying local or fleet rows."""
        # Prewarm FIRST, before the (potentially slow) registry scan below --
        # same reasoning as the non-live setup() ordering fix: this thread is
        # already off the render thread, but the prewarm's own purpose is to
        # finish importing data_ssh before the operator's first pivot-switch
        # keypress, and every second spent scanning before the import even
        # starts is a second less of head start against that keypress.
        from . import tasks as _tasks_mod; _tasks_mod.prewarm_optional_modules()
        # Also prewarm the machine-key-map RESULT (not just the data_ssh
        # import): see _prewarm_machine_key_map's own docstring -- the
        # underlying agent_worktrees.config.load_config() call is uncached
        # and was profiled at several seconds on a machine with many
        # registered repos. _machine_key_map() itself never blocks on this
        # (it degrades to an empty/fallback map immediately), so this is
        # purely a head start, not a correctness requirement here.
        self._prewarm_machine_key_map()
        pivot_payload = self._scan_pivot_payload()

        def apply():
            self._install_pivot_payload(pivot_payload)
            for runtime in getattr(self, "_pivot_runtimes", {}).values():
                try:
                    runtime.invalidate()
                except Exception:
                    pass
            self.refresh()

        self._apply_from_worker(apply)
    def _apply_from_worker(self, callback):
        """Apply on Textual's thread; inline only for direct main-thread tests."""
        try:
            self.app.call_from_thread(callback)
        except Exception:
            if threading.current_thread() is threading.main_thread():
                callback()
    def _prepare_live_source(self, snapshot):
        """Resolve source/config-derived values on the setup worker."""
        source_tabs = getattr(self.src, "source_tabs", None)
        if callable(source_tabs):
            tabs = list(
                source_tabs(snapshot)
                if snapshot is not None
                else source_tabs()
            )
        else:
            machine_tabs = (
                self.src.machines(snapshot)
                if snapshot is not None
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
        metadata_fn = getattr(self.src, "setup_metadata", None)
        if callable(metadata_fn):
            metadata = metadata_fn(snapshot)
        else:
            hc = getattr(self.src, "host_cols", None)
            te = getattr(self.src, "target_envs", None)
            metadata = {
                "host_cols": hc() if callable(hc) else None,
                "target_envs": te() if callable(te) else None,
            }
        local = next(
            (
                (tab.get("machine"), tab.get("env"))
                for tab in tabs
                if tab.get("local")
            ),
            None,
        )
        if local is None:
            try:
                local = self.src.LOCAL
            except Exception:
                local = None
        try:
            repo_branch = (
                getattr(self.src, "REPO", "") or "",
                getattr(self.src, "BRANCH", "") or "",
            )
        except Exception:
            repo_branch = ("", "")
        return {
            "tabs": tabs,
            "local": local,
            "repo_branch": repo_branch,
            **(metadata or {}),
        }
    def _apply_live_source(self, prepared, loader):
        """Install fully-prefetched live state. UI-thread only; no source I/O."""
        tabs = prepared.get("tabs") or []
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
        self.loader = loader
        self._reconcile_bootstrap_source_ids(tabs, prepared.get("local"))
        self._apply_loader_records()
        self._source_local = prepared.get("local") or self._source_local
        self._source_repo_branch = prepared.get("repo_branch") or ("", "")
        self._roster_ready = True
        self.machine_idx = next(
            (
                index
                for index, tab in enumerate(self.source_tabs)
                if index and tab.get("local")
            ),
            1 if len(self.source_tabs) > 1 else 0,
        )
        self.maint_sel = ListSelection()
        self._last_poll = time.monotonic()
        self._last_pivot_poll = time.monotonic()
        self.host_cols = prepared.get("host_cols") or list(_DEFAULT_HOST_COLS)
        target_env_list = prepared.get("target_envs") or _DEFAULT_TARGET_ENVS
        self.targets = target_rows(target_env_list)
        self.grid = {}
        for ti, target in enumerate(self.targets):
            for hi in range(len(self.host_cols)):
                self.grid[(ti, hi)] = self.cell_locked(ti, hi)
        self.applied = dict(self.grid)
        self._prof_unavailable = set()
        self._prof_load = getattr(self.src, "load_profile_column", None)
        self._prof_apply = getattr(self.src, "apply_profile_column", None)
        self._prof_loaded = False
        if callable(self._prof_load):
            self.profiles_view.start_load()
        self._pr_reconciled = False
        rec_fn = getattr(self.src, "reconcile_prs", None)
        if callable(rec_fn):
            self._start_pr_reconcile(rec_fn)
        self._bound_live_reconciled = False
        blr_fn = getattr(self.src, "reconcile_bound_live", None)
        if callable(blr_fn):
            self._start_bound_live_reconcile(blr_fn)
        self._reconcile_wt_sel()
    def _apply_loader_records(self):
        """Merge incomplete stream prefixes over cached rows."""
        if self.loader is None:
            return
        records = self.loader.records()
        authoritative_fn = getattr(self.loader, "authoritative_source_ids", None)
        if not callable(authoritative_fn):
            _ready, loading, failed = self.loader.counts()
            if records or (loading == 0 and failed == 0):
                self.data = records
            return

        authoritative = set(authoritative_fn())
        current_sources = {
            rec.get("source_id") for rec in self.data if rec.get("source_id")
        }
        incoming_sources = {
            rec.get("source_id") for rec in records if rec.get("source_id")
        }
        if current_sources | incoming_sources <= authoritative:
            self.data = records
            return

        incoming = {
            self._row_key(rec): rec
            for rec in records
            if self._row_key(rec) is not None
        }
        merged = []
        seen = set()
        for rec in self.data:
            key = self._row_key(rec)
            if key in incoming:
                merged.append(incoming[key])
                seen.add(key)
            elif rec.get("source_id") not in authoritative:
                merged.append(rec)
        for rec in records:
            key = self._row_key(rec)
            if key is None or key not in seen:
                merged.append(rec)
                if key is not None:
                    seen.add(key)
        self.data = merged
    def _reconcile_bootstrap_source_ids(self, tabs, local):
        """Rewrite bootstrap rows onto the canonical local source identity.

        The cache-only bootstrap rows can land before roster resolution, so they
        are normalized with the hostname-based local source identity from
        ``data_local``. Once the authoritative roster arrives we know the real
        local tab identity (for example a machine key that differs from the
        hostname). Re-key those temporary rows before merging loader records so
        the authoritative local rows replace them instead of duplicating them.
        """
        if not self.data or not local:
            return
        local_tab = next((tab for tab in tabs if tab.get("local")), None)
        canonical_source_id = (
            local_tab.get("source_id")
            if isinstance(local_tab, dict)
            else None
        )
        if not canonical_source_id:
            return
        updated = []
        changed = False
        for rec in self.data:
            if (
                (rec.get("machine"), rec.get("env")) != local
                or rec.get("source_id") == canonical_source_id
            ):
                updated.append(rec)
                continue
            patched = dict(rec)
            patched["source_id"] = canonical_source_id
            selection_id = patched.get("selection_id")
            if isinstance(selection_id, str) and "\x1f" in selection_id:
                _old_source, row_id = selection_id.split("\x1f", 1)
                patched["selection_id"] = f"{canonical_source_id}\x1f{row_id}"
            updated.append(patched)
            changed = True
        if changed:
            self.data = updated
    def on_unmount(self):
        # Picker is tearing down (a launch decision, cancel, or quit). Signal
        # every in-flight ``_run_bg`` pivot-action worker first: each checks
        # ``_bg_cancel`` right before it would otherwise race
        # ``app.call_from_thread`` against an app that (by the time the worker's
        # blocking work() call returns) may already be gone -- so a worker still
        # running at this moment drops its outcome quietly instead of logging a
        # "could not marshal" warning for what is really just this expected exit.
        self._bg_cancel.set()
        # Kill any in-flight SSH prefetch so it never orphans into a heavy
        # git-classify churning on the machine we're about to hand off into --
        # the picker perf bug where Copilot "slows to a crawl" after launch.
        loader = getattr(self, "loader", None)
        if loader is not None:
            try:
                loader.cancel()
            except Exception:
                pass
        with self._provider_loader_lock:
            self._provider_cancelled = True
            provider_loader = self._provider_loader
        if provider_loader is not None and provider_loader is not loader:
            try:
                provider_loader.cancel()
            except Exception:
                pass
        if self._frame_health is not None:
            self._frame_health.close(wait=True)
        # D2: tear down any held streaming pivot channel (a ``subscribe`` stream
        # runs until close) so no ``list --stream`` child is orphaned on exit.
        for rt in getattr(self, "_pivot_runtimes", {}).values():
            closer = getattr(rt, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
