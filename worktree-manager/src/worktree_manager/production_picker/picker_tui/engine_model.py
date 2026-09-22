#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import time

from . import derive
from .engine_helpers import ACTIVE_SPECS, BUTTON_SETS, DISPO_MARK, LIST_SPECS, SPINNER

class PickerScreenModelMixin:
    def button_set(self):
        kind = self._kind()
        if kind == "profiles":
            return ["PA", "PReset"] if self.grid_dirty() else ["PA"]
        if kind == "worktrees":
            # New worktree always; the bulk Clean / Sync buttons (which replaced
            # the standalone Maintenance view, #1427) whenever there are
            # worktrees in scope to act on; Toggle-hidden only when there's
            # something to reveal (or it's already revealing) so it never
            # clutters (#1422).
            bset = []
            if self._tab_supports("create"):
                bset.append("N")
            scoped = self._scope_data()
            if any(self._record_supports(rec, "cleanup") for rec in scoped):
                bset.append("K")
            if any(self._record_supports(rec, "sync") for rec in scoped):
                bset.append("SY")
            if self.show_hidden or self._hidden_count() > 0:
                bset.append("TH")
            return bset
        return BUTTON_SETS.get(kind, [])
    def active_button(self):
        bset = self.button_set()
        if not bset:
            return None
        return bset[self.btn_idx % len(bset)]
    def grid_dirty(self):
        return self.profiles_view.grid_dirty()
    def pending_count(self):
        return self.profiles_view.pending_count()
    def cell_locked(self, ti, hi):
        return self.profiles_view.cell_locked(ti, hi)
    def machine_state(self, i):
        label, m, e, ok = self.machines[i]
        if label == "All":
            return "ready"
        if not ok:
            return "disabled"
        if self.live:
            loader = self.loader
            if loader is None:
                return "loading"
            source_id = self.source_tabs[i].get("source_id")
            if source_id and hasattr(loader, "state_for_source"):
                return loader.state_for_source(source_id)
            return loader.state(m, e)
        if (m, e) == self._src_local():
            return "ready"
        return "ready" if (time.monotonic() - self.t0) >= self.load_delay[i] else "loading"
    def ready_envs(self):
        out = set()
        for i, (label, m, e, ok) in enumerate(self.machines):
            tab = self.source_tabs[i] if self.source_tabs else {}
            if (
                label != "All"
                and tab.get("source_kind") in (None, "machine-ssh")
                and self.machine_state(i) == "ready"
            ):
                out.add((m, e))
        return out
    def ready_source_ids(self):
        return {
            tab.get("source_id")
            for i, tab in enumerate(self.source_tabs)
            if i and tab.get("source_id") and self.machine_state(i) == "ready"
        }
    def spin(self):
        return SPINNER[self.frame % len(SPINNER)]
    def _src_local(self):
        """Cached local identity; rendering never resolves source/config I/O."""
        if self._source_local is not None:
            return self._source_local
        return (None, None)
    def _src_repo_branch(self):
        """Cached repo/branch labels; rendering never resolves source/config I/O."""
        return self._source_repo_branch
    def local_index(self):
        if not getattr(self, "_roster_ready", True) or not self.machines:
            return 1 if len(self.machines) > 1 else 0
        for i, (label, m, e, ok) in enumerate(self.machines):
            if (m, e) == self._src_local():
                return i
        return 1 if len(self.machines) > 1 else 0
    def is_all(self):
        return self.machine_idx == 0
    def _task_state(self):
        """(state, rows, error) for the current registered pivot at the current
        machine. ``state`` is idle|loading|ready|error. Triggers a background
        fetch of the pivot's ``list`` CLI on first view of a machine."""
        reg = self._reg_pivot()
        if reg is None:
            return ("idle", [], "")
        machine = self._pivot_scope_key()
        if machine is None:
            return (
                "error",
                [],
                "machine-scoped pivots are unavailable for provider sources",
            )
        rt = self._pivot_runtime(reg)
        rt.ensure(machine)
        return rt.get(machine)
    def _task_rows(self):
        """The flat, selectable list of task entries (dicts) for the current
        registered pivot -- the ('T', i) stops index into this."""
        rows = self._task_state()[1]
        return rows if isinstance(rows, list) else []
    def _task_summary(self):
        """The current registered pivot's summary dict (D1) for the current
        machine -- the substitution source for its ``summary`` header line.
        ``{}`` for a built-in pivot or a provider that emitted a bare array.
        Defensive: a runtime without ``get_summary`` (an older/fixture runtime)
        degrades to ``{}`` rather than raising."""
        reg = self._reg_pivot()
        if reg is None:
            return {}
        rt = self._pivot_runtime(reg)
        getter = getattr(rt, "get_summary", None)
        if getter is None:
            return {}
        scope = self._pivot_scope_key()
        return {} if scope is None else getter(scope)
    def _worktree_title_map(self):
        """``{short-id: title}`` from the Picker's loaded worktree records -- the
        lookup that correlates an account-scoped pivot's *claiming worktree id*
        (e.g. a CodeSpace lease's 4-hex beacon) to the owning worktree's title.
        Keyed by both the 4-hex ``id4`` and the full ``id`` (lower-cased)."""
        out: dict[str, str] = {}
        for r in (getattr(self, "data", None) or []):
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "")
            if not title:
                continue
            for f in ("id4", "id"):
                v = r.get(f)
                if v:
                    out[str(v).strip().lower()] = title
        return out
    def _enrich_pivot_rows(self, reg, rows):
        """Fill each row's ``worktree_title`` (claiming-worktree title) and
        ``_worktree_short`` (trailing 4-char id, matching the Worktrees
        list's own ``id4``) from ``reg.worktree_field``. In-place +
        idempotent; a no-op with no ``worktree_field``/rows.
        ``_worktree_short`` is what ``_column_row`` displays instead of the
        far-longer raw value (Phase 4 item 1 fix: the generic front-
        truncating ``_clip`` produced a meaningless prefix fragment on a
        real id). Kept separate from the raw field: ``_task_action_ctx``
        needs the real, full id."""
        if not reg or not getattr(reg, "worktree_field", None) or not rows:
            return
        titles = self._worktree_title_map()
        for rec in rows:
            if not isinstance(rec, dict):
                continue
            wt = rec.get(reg.worktree_field)
            rec["worktree_title"] = titles.get(str(wt).strip().lower(), "") if wt else ""
            rec["_worktree_short"] = str(wt)[-4:] if wt else ""
    def _worktree_claiming_task(self, rec):
        """Phase 4 REVERSE cross-link: ``(row, group_field)`` for the
        registered-pivot task entry claiming worktree ``rec``, or ``None`` --
        the mirror of ``_enrich_pivot_rows``' task->worktree correlation.
        Resolves the machine scope from ``rec``'s OWN ``machine`` field (via
        ``_machine_key_map``, the same display->registry-key translation
        ``_pivot_machine_id`` does for the currently selected tab), not the
        globally selected pivot machine -- so a claiming task shows up for
        every worktree row while browsing the cross-machine "All" scope,
        not only ones on whichever machine tab happens to be selected. See
        ``pivots.find_claiming_task`` for the matching logic."""
        from . import pivots as pivots_mod

        wid = str(rec.get("id") or "").strip().lower()
        wid4 = str(rec.get("id4") or "").strip().lower()
        display = rec.get("machine")
        machine = self._machine_key_map().get(display, display) if display else None
        return pivots_mod.find_claiming_task(
            self.pivots, self._pivot_runtimes, machine, wid, wid4)
    def _task_groups(self):
        """Task rows grouped for display. Groups by the pivot's ``group`` entry
        field (``group_field``) when the manifest declares one -- e.g. the
        agent-dispatch board's status group (Blocked/Proposed/Queued/Started/
        Completed/Abandoned) -- otherwise by the worktree field (the default
        pin-based grouping). Returns ``[(group_label, [(row_index, entry), ...]),
        ...]`` in first-seen order, so a ``list`` provider that already emits its
        rows in the intended section order (as ``--board`` does) controls the
        section order too."""
        reg = self._reg_pivot()
        rows = self._task_rows()
        if reg is None:
            return []
        group_key = reg.group_field or reg.worktree_field
        default_label = "· ungrouped" if reg.group_field else "· unpinned"
        groups: dict[str, list] = {}
        order: list[str] = []
        for i, r in enumerate(rows):
            gv = r.get(group_key) if group_key else None
            key = gv or default_label
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((i, r))
        return [(k, groups[k]) for k in order]
    def _selected_task(self):
        """The task entry under the ('T', i) cursor, or None."""
        if self.sel[0] != "T":
            return None
        rows = self._task_rows()
        i = self.sel[1]
        return rows[i] if 0 <= i < len(rows) else None
    def cur_machine(self):
        label, m, e, ok = self.machines[self.machine_idx]
        return m, e, ok
    def _current_tab(self):
        return self.source_tabs[self.machine_idx]
    @staticmethod
    def _record_supports(rec, capability):
        if rec.get("source_kind", "machine-ssh") == "machine-ssh":
            return True
        return bool((rec.get("source_capabilities") or {}).get(capability))
    @staticmethod
    def _row_key(rec):
        return rec.get("selection_id") or rec.get("id4")
    def _tab_supports(self, capability):
        if self.is_all():
            return capability == "create"
        tab = self._current_tab()
        if tab.get("source_kind", "machine-ssh") == "machine-ssh":
            return True
        return bool((tab.get("capabilities") or {}).get(capability))
    def _scope_data(self):
        """Worktrees in the active source-tab scope."""
        if self.is_all():
            ready_sources = self.ready_source_ids()
            ready_envs = self.ready_envs()
            return [
                w for w in self.data
                if (
                    w.get("source_id") in ready_sources
                    or (w.get("machine"), w.get("env")) in ready_envs
                )
            ]
        tab = self._current_tab()
        source_id = tab.get("source_id")
        if source_id:
            return [w for w in self.data if w.get("source_id") == source_id]
        m, e, _ = self.cur_machine()
        return [w for w in self.data if w["machine"] == m and w["env"] == e]
    def _hidden_count(self):
        """How many bridge/system worktrees are hidden in the current scope."""
        return sum(1 for w in self._scope_data() if w.get("hidden"))
    def _visible_scope_data(self):
        data = self._scope_data()
        if not self.show_hidden:
            data = [w for w in data if not w.get("hidden")]
        return data
    def current_list(self):
        """(cols, [(section_label, [records])]) for the selected machine tab.
        'All' interleaves every READY machine and shows the machine/env columns;
        a specific machine hides them (implied by the tab). Bridge/system
        worktrees are hidden unless Toggle-hidden is on (#1422). Worktrees with
        no owning Copilot session are split into a distinct 'Unowned' section so
        selecting one never silently cold-starts a blank conversation (#1026).
        ALWAYS the full, unfiltered set -- see current_list_visible() (#2228 P4)."""
        cols = ACTIVE_SPECS if self.is_all() else LIST_SPECS
        a, r, c = self.src.bucket(self._visible_scope_data())
        unowned = [w for w in r if w.get("sessionless")]
        if unowned:
            r = [w for w in r if not w.get("sessionless")]
            return cols, [
                ("Active", a), ("Recent", r), ("Completed", c),
                ("Unowned · no prior session (Open starts fresh)", unowned),
            ]
        return cols, [("Active", a), ("Recent", r), ("Completed", c)]
    def current_list_visible(self):
        """current_list() narrowed/sorted by the "/" bar (#2228 P4) --
        rendering/navigation ONLY; every other consumer keeps the full set."""
        cols, sections = self.current_list()
        if self._kind() != "worktrees":
            return cols, sections
        return cols, [(label, self.list_view.narrow(
            rows, ("title", "id", "id4"), derive.wt_row_always_visible,
            derive.WT_SORT_KEYS)) for label, rows in sections]
    def _wt_visible_records(self):
        """Flat, render-order VISIBLE Worktrees rows (nav-only counterpart to
        list_records())."""
        _cols, sections = self.current_list_visible()
        out = []
        for _label, rows in sections:
            out.extend(rows)
        return out
    def list_records(self):
        _cols, secs = self.current_list()
        out = []
        for _label, rows in secs:
            out.extend(rows)
        return out
    def create_target(self):
        """Where '+ New Worktree' lands: the active machine, or LOCAL when on
        'All'."""
        if self.is_all():
            return self._src_local()
        m, e, _ = self.cur_machine()
        return (m, e)
    def _update_actionable(self) -> bool:
        """True when the update indicator is a focusable action (#1430).

        Only when an update is *staged* (``available``) is there something to do
        (Enter = apply + restart); the spinner/✓ states are informational.
        """
        return getattr(self, "update_state", "idle") == "available"
    def _v_stops(self):
        """Top-region vertical stops, in visual (top-to-bottom) order: the update
        refresh icon ("UPD", 0) on the title line when an update is staged, then
        the View pivot row ("V", 0). The ⚙ Configuration entry is NOT a vertical
        stop -- it rides the pivot row's horizontal ◀▶ nav (#1426)."""
        out = []
        if self._update_actionable():
            out.append(("UPD", 0))
        out.append(("V", 0))
        return out
    def stops(self):
        """Vertical Up/Down flow. ("UPD",0)=update refresh icon (only when an
        update is staged), ("V",0)=View pivot row, ("M",0)=machine picker,
        ("BTN",0)=button row, then the table/grid rows."""
        if self._kind() == "worktrees":
            out = [*self._v_stops(), ("M", 0)]
            if self.button_set():
                out.append(("BTN", 0))
            for i in range(len(self._wt_visible_records())):
                out.append(("L", i))
        elif self._kind() == "maintenance":
            out = [*self._v_stops(), ("M", 0), ("BTN", 0)]
            groups = self.maint_groups()
            if any(rows for _st, rows in groups):
                out.append(("SA", 0))   # the "Select all" checkbox
            li = 0
            for gi, (_st, rows) in enumerate(groups):
                out.append(("GH", gi))
                for _ in rows:
                    out.append(("C", li))
                    li += 1
        elif self._kind() == "registered":
            # Registered pivot: View nav + machine sub-nav, then one stop per
            # task row (grouped rows are flattened in first-seen order).
            out = [*self._v_stops(), ("M", 0)]
            for i in range(len(self._task_rows())):
                out.append(("T", i))
        else:
            out = [*self._v_stops()]
            for i in range(len(self.targets)):
                out.append(("PR", i))
            out.append(("BTN", 0))   # Apply/Reset at the bottom
        return out
    def _pr_head(self):
        """Grid-region entry point, restoring the last-focused row (#1288).
        pcol (the host column) already persists across Tab in/out."""
        return ("PR", min(self.last_pr, max(0, len(self.targets) - 1)))
    def region_head(self, zone):
        if zone == "PR":
            return self._pr_head()
        if zone == "L":
            return self._l_head()
        return {"V": ("V", 0), "UPD": ("V", 0), "CFG": ("CFG", 0), "M": ("M", 0),
                "BTN": ("BTN", 0), "L": ("L", 0), "SA": ("SA", 0),
                "GH": ("GH", 0), "C": ("C", 0), "PR": ("PR", 0)}.get(
                    zone, ("V", 0))
    def region_heads(self):
        """Tab/Shift+Tab jump targets — the entry point of each major region.
        The View pivot row is one region; the ⚙ Configuration entry rides its
        horizontal ◀▶ nav, so Tab lands on the pivots and ◀▶ reaches Config."""
        v = [("V", 0)]
        if self._kind() == "worktrees":
            heads = [*v, ("M", 0)]
            if self.button_set():
                heads.append(("BTN", 0))
            if self._wt_visible_records():
                heads.append(self._l_head())
        elif self._kind() == "maintenance":
            heads = [*v, ("M", 0), ("BTN", 0)]
            if self.maint_records():
                heads.append(("SA", 0))   # Tab into the selection/list region
        elif self._kind() == "registered":
            heads = [*v, ("M", 0)]
            if self._task_rows():
                heads.append(("T", 0))
        else:
            heads = [*v, self._pr_head(), ("BTN", 0)]
        return heads
    def default_sel(self):
        kind = self._kind()
        d = {"worktrees": ("BTN", 0), "maintenance": ("BTN", 0),
             "profiles": ("PR", 0), "registered": ("M", 0)}.get(kind, ("BTN", 0))
        # The Profiles default (``("PR", 0)``) is only valid when there is at
        # least one target row. With no targets (or no host columns) that stop
        # doesn't exist, so fall back to the first real stop instead of handing
        # back a phantom the caller would ``stops.index()`` on (ValueError).
        stops = self.stops()
        if d not in stops:
            return stops[0] if stops else d
        return d
    def anchors(self):
        return self.region_heads()
    def cleanup_rows(self):
        """Maintenance worktree list, scoped to the active machine sub-pivot
        (All = every machine), each tagged with its prune disposition.

        Shows every in-scope worktree -- including in-use/work-bearing ones --
        so the disposition column is honest; the Cleanup dialog only *offers*
        the cleanable buckets, and the executor re-checks safety per worktree.
        """
        rows = self._visible_scope_data()
        for w in rows:
            bucket = w.get("cleanup_bucket", "wip")
            lvl = derive.BUCKET_DISPO.get(bucket, "")
            txt = derive.BUCKET_REASON.get(bucket, "")
            # Blended disposition (verdict + reason in one colored chip).
            w["dispo_level"] = lvl
            w["dispo"] = f" {DISPO_MARK[lvl]} {txt}" if lvl else ""
        return rows
