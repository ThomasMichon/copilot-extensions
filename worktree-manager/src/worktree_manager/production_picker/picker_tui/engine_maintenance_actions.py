#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import threading

from .engine_dialogs import ScopeDlgScreen
from .engine_live_screens import ProgressScreen

class PickerScreenMaintenanceActionsMixin:
    def _confirm_new_worktree(self, dlg):
        """Confirmed New-worktree options -> the launch decision (#88 F4)."""
        on = {o["label"] for o in dlg["opts"] if o["on"]}
        tm, te = dlg["target"]
        self._decide({
            "action": "new", "machine": tm, "env": te,
            "is_local": (tm, te) == self.src.LOCAL,
            "options": {
                "anchor": "Anchor repo" in on,
                "bare": "Bare" in on,
                "no_mux": "No Mux" in on,
                "ahp": "AHP" in on,
                "local_model": "Local model" in on,
            },
        })
    def _confirm_cleanup(self, dlg):
        """Confirmed Clean/Sync scope -> build (and, when armed, start) the
        per-worktree maintenance progress run (#88 F4)."""
        picked = [o["label"] for o in dlg["opts"] if o["on"]]
        verb = dlg.get("verb", "Clean up")
        op = "sync" if verb.lower().startswith("sync") else "cleanup"
        ids = self._scope_union(dlg)
        recs = [w for w in self.data if self._row_key(w) in ids]
        if not recs:
            self.debug = (f"{verb.lower()} {dlg['scope']}: "
                          f"{', '.join(picked) or 'nothing'} → 0 worktrees")
            return
        # Extra confirm when a cleanup scope reaches past 'clean'
        # (Unused / Conversation-only / All) -- removing idle/empty trees
        # or trees that held conversation is a bigger commitment.
        include_unused = any(
            p in ("Unused", "All eligible") for p in picked)
        include_conversations = any(
            p in ("Conversation-only", "All eligible") for p in picked)
        beyond_clean = op == "cleanup" and (
            include_unused or include_conversations)
        items = [{"key": self._row_key(w), "id4": w["id4"], "title": w["title"],
                  "machine_env": w["machine_env"], "state": "pending"}
                 for w in recs]
        self.progress = {
            "verb": verb, "op": op, "scope": dlg["scope"], "items": items,
            "recs": recs, "picked": picked,
            "ticks": 0, "steps": 3, "done": False,
            "armed": not beyond_clean,
            "include_unused": include_unused,
            "include_conversations": include_conversations,
        }
        if self.progress["armed"]:
            self._start_progress()
        self._open_progress()
    def _open_progress(self):
        """Show the live maintenance/profiles progress run as a native Textual
        ``ModalScreen`` (#88 F4).

        The entry points (``_confirm_cleanup``, ``_run_op_progress``,
        ``_start_profiles_run``) build ``self.progress`` first; this pushes the
        ``ProgressScreen`` that renders it, ticks it forward (mock walker or
        real-executor poll) on its own interval, and mirrors the old
        ``_key_progress`` confirm gate. The screen mutates engine state directly
        (``self.progress`` / ``self.executor`` / ``self.debug``) and needs no
        callback."""
        self.app.push_screen(ProgressScreen(self))
    def _start_stop(self, recs):
        """Stop the Mux/Copilot wrapper of one or more worktrees (the 'Stop'
        action -- solo from the row sub-menu, or in aggregate from the bulk
        action menu, #2258 follow-up).

        Drives the same maintenance progress + executor path as Cleanup/Sync so
        the up-to-6 s graceful double-Ctrl-C quit never blocks the render loop
        and a remote worktree's kill goes over SSH (``restart <id>`` on the
        project binstub -- the CLI verb stays ``restart``; only the picker label
        is "Stop"). Only worktrees with a live mux are stopped. When it finishes
        the touched machines reload (``_refresh_after_maint``), so the rows
        re-render as stopped sessions -- ready to Resume with a fresh Mux +
        Copilot.
        """
        recs = [recs] if isinstance(recs, dict) else list(recs)
        live = [r for r in recs if r.get("mux_live")]
        if not live:
            self.debug = "no live session to stop"
            return
        self._run_op_progress("Stop", "restart", live, armed=True)
    def _start_reclaim(self, recs):
        """Reclaim the bound Copilot process(es) of one or more worktrees (the
        two-step-restore 'Reclaim' action).

        Runs the same maintenance progress path as Stop, but drives the
        ``reclaim`` op (kill the exact Copilot process holding the session's
        ``inuse.<pid>.lock``, **only bindings Stop cannot reach**) instead of a
        mux quit. Offered -- and here filtered -- on :meth:`_reclaimable`: a
        bound Copilot with no mux for Stop to reach (a bare orphan, a live lock
        whose homing could not be classified ``bare``, or one homed in a mux
        whose ``wt-<id>`` session is unreachable -- a detached psmux server,
        dotfiles #1447). Filtering on the same predicate the executor acts on
        keeps the verb honest -- a live, Stop-able muxed session is left to Stop,
        a bare orphan the cwd-keyed lock-scan never registered (#662/#1416) still
        reaps, and a bound-but-unclassifiable-or-detached Copilot is no longer
        stranded. After it finishes the touched machines reload, so the row
        re-renders without the stale bound process -- ready for a fresh Open /
        Bare resume.
        """
        recs = [recs] if isinstance(recs, dict) else list(recs)
        live = [r for r in recs if type(self)._reclaimable(r)]
        if not live:
            self.debug = "no Stop-unreachable bound process to reclaim"
            return
        self._run_op_progress("Reclaim", "reclaim", live, armed=True)
    def _start_repair(self, recs):
        """Repair the inconsistent mux+stray-orphan double-binding (the 'Repair'
        action, offered only in the WARNING state -- see :meth:`_warning`).

        Drives the SAME ``reclaim`` op as :meth:`_start_reclaim` (bare-only reap
        via ``reclaim_one``), which reaps only the bindings Stop cannot reach --
        here the **un-muxed** (bare/unknown-homing) stray orphan -- and leaves the
        live, **Stop-able mux-homed** session untouched (its ``wt-<id>`` mux is
        reachable, so ``filter_stop_unreachable`` preserves it). So the healthy
        ``wt-<id>`` mux is PRESERVED while the stray orphan and its lock residue
        are cleared. Filtered on the same ``_warning`` predicate the verb is gated
        on, so it acts if and only if the double-binding is real. After it
        finishes the touched machines reload and the row re-renders as a clean,
        singly-bound mux -- ready to Open/Stop.
        """
        recs = [recs] if isinstance(recs, dict) else list(recs)
        live = [r for r in recs if type(self)._warning(r)]
        if not live:
            self.debug = "no stray orphan to repair"
            return
        self._run_op_progress("Repair", "reclaim", live, armed=True)
    def _start_refresh(self, rec):
        """Per-row Refresh (picker-cache-first-paint, dotfiles#948).

        On-demand live gather + session-render-cache write-back for ONE worktree,
        run off the UI thread, then swap its row in place -- the only way to
        populate an **Unknown** row (the first paint reads cache only) and a cheap
        way to re-sync a stale one without a full-fleet reload. A LOCAL worktree
        is gathered + stamped via :func:`data_local.refresh_one`; a remote row
        (not resolvable in the local tracking store) falls back to reloading its
        owning machine through the live loader. Best-effort; never raises on the
        UI thread."""
        raw = rec.get("raw") or {}
        wt_id = raw.get("id")
        if not (getattr(self, "real_ops", False) and wt_id):
            self.debug = "refresh unavailable (no record)"
            return
        id4 = rec.get("id4")
        m, e = rec.get("machine"), rec.get("env")
        source_id = rec.get("source_id")
        self.debug = f"refreshing {id4}…"

        def work():
            row = None
            if rec.get("source_kind", "machine-ssh") == "machine-ssh":
                try:
                    from . import data_local
                    row = data_local.refresh_one(
                        wt_id, m, e, runner=self._provider_runner())
                except Exception:
                    row = None
            if row is not None:
                self._replace_row(wt_id, row)
                self.debug = f"refreshed {id4}"
                return
            # Remote (or gone locally): fall back to a machine-level reload.
            try:
                if self.live and self.loader is not None and source_id:
                    self.loader.reload_source(source_id)
                    self.debug = f"refreshing {rec.get('source_label') or id4}…"
                elif self.live and self.loader is not None and m and e:
                    self.loader.reload(m, e)
                    self.debug = f"refreshing {m} · {e}…"
                elif not self.live:
                    self._reload_local_after_reconcile()
            except Exception:
                pass

        threading.Thread(target=work, name="wt-refresh", daemon=True).start()
    def _provider_runner(self):
        """Return the Picker-owned tracked runner for local provider commands."""
        with self._provider_loader_lock:
            if self._provider_cancelled:
                raise RuntimeError("picker provider runner is cancelled")
            loader = getattr(self, "loader", None)
            if loader is not None:
                return loader._spawn
            if self._provider_loader is None:
                from . import data_ssh

                self._provider_loader = data_ssh.LiveLoader([])
            return self._provider_loader._spawn
    def _replace_row(self, wt_id, row):
        """Swap a single freshly-normalized row into ``self.data`` by worktree id
        (in place), appending if it is not already present. Used by the per-row
        Refresh so one row updates without rebuilding the whole data source."""
        for i, r in enumerate(self.data):
            if (r.get("raw") or {}).get("id") == wt_id:
                self.data[i] = row
                return
        self.data.append(row)
    def _start_finalize(self, recs):
        """Finalize one or more conversation-only / unused worktrees (the
        'Finalize' action -- solo or aggregate, #2258 follow-up).

        Finalize validates that nothing is unpushed (a no-commit worktree has
        nothing) and removes it -- the sanctioned lifecycle completion. Because
        it removes the worktree (and any conversation history), it runs behind a
        confirm gate (``armed=False``): the progress dialog asks before it
        executes. Only conversation-only / unused worktrees are targeted.
        """
        recs = [recs] if isinstance(recs, dict) else list(recs)
        targets = [r for r in recs
                   if r.get("cleanup_bucket") in ("conversation", "unused")]
        if not targets:
            self.debug = "no conversation-only / unused worktree to finalize"
            return
        self._run_op_progress("Finalize", "finalize", targets, armed=False)
    def _run_op_progress(self, verb, op, recs, *, armed):
        """Build (and, when ``armed``, start) a maintenance progress run over
        ``recs`` for a single ``op`` (restart / finalize). An unarmed run shows
        the confirm gate first (see ``_key_progress`` / ``ProgressScreen``)."""
        items = [{"key": self._row_key(r), "id4": r.get("id4"),
                  "title": r.get("title", ""),
                  "machine_env": r.get("machine_env", ""), "state": "pending"}
                 for r in recs]
        scope = (recs[0].get("id4", "") if len(recs) == 1
                 else f"{len(recs)} selected")
        self.progress = {
            "verb": verb, "op": op, "scope": scope,
            "items": items, "recs": recs, "picked": [],
            "ticks": 0, "steps": 3, "done": False, "armed": armed,
            "include_unused": False, "include_conversations": False,
        }
        if armed:
            self._start_progress()
        self._open_progress()
    def _start_progress(self):
        """Begin executing the armed progress run (real executor or mock walk)."""
        p = self.progress
        if not self.real_ops:
            return  # mock walker advances via _advance_progress
        from . import maintenance
        tasks = maintenance.build_tasks(
            p["op"], p["recs"], self.src,
            include_unused=p["include_unused"],
            include_conversations=p["include_conversations"],
        )
        self.executor = maintenance.MaintenanceExecutor(p["op"], tasks)
        self.executor.start()
    def _poll_executor(self):
        """Pull real per-item states from the maintenance executor."""
        p = self.progress
        ex = self.executor
        for it in p["items"]:
            it["state"] = ex.state(it.get("key", it["id4"]))
        if ex.is_done():
            p["done"] = True
    def _scope_union(self, dlg):
        """The net acted-on set for a Clean/Sync scope dialog: the union of the
        enabled buckets. (Per-row exclusion was retired with the live-filter
        model in #88 F4 -- scope now comes from the main-list selection before
        the dialog opens, and the modal's impact list shows this exact set.)"""
        s = set()
        for o in dlg["opts"]:
            if o["on"]:
                s |= set(o.get("ids", ()))
        return s
    def _selected_record(self):
        zone, i = self.sel
        if zone == "L":
            arr = self._wt_visible_records()
        elif zone == "C":
            arr = self.maint_records()
        else:
            return None
        return arr[i] if 0 <= i < len(arr) else None
    def _decide(self, decision):
        """Record a launch decision and exit the TUI so __main__ can act on it.

        ``PickerApp.result`` stays ``None`` on cancel (q/Esc); a dict here is a
        concrete instruction the caller maps onto resume/create/remote paths.
        """
        self.app.result = decision
        self.app.exit()
    def _resume_decision(self, rec, no_mux=False, ahp=False, bare_resume=False):
        """Build the resume decision for a worktree row/submenu selection.

        ``no_mux`` (the Open sub-menu toggle, #1343) launches directly without
        the PSMux/TMux wrapper. ``bare_resume`` (two-step restore) creates the
        worktree's mux but launches Copilot in HOME with no --resume, so a CLI
        bug that fails to start in a repo/worktree cwd is dodged; the operator
        finishes with a manual ``/resume <id>``.
        """
        raw = rec.get("raw") or {}
        m, e = rec.get("machine"), rec.get("env")
        decision = {
            "action": "resume",
            "worktree_id": raw.get("id"),
            "id4": rec.get("id4"),
            "machine": m,
            "env": e,
            "title": rec.get("title"),
            "is_local": (m, e) == self.src.LOCAL,
        }
        opts = {}
        if no_mux:
            opts["no_mux"] = True
        if ahp:
            opts["ahp"] = True
        if bare_resume:
            opts["bare_resume"] = True
        if opts:
            decision["options"] = opts
        return decision
    def _activate(self):
        zone = self.sel[0]
        if zone == "CFG":
            self._open_cfgmenu()
            return
        if zone == "UPD":
            # Refresh icon: apply the staged update and restart the picker on the
            # new version. The picker can't swap its own runtime venv, so the
            # launcher does it (action=refresh). #1430.
            self._decide({"action": "refresh"})
            return
        if zone == "V":
            self.sel = ("PR", 0) if self._kind() == "profiles" else ("M", 0)
        elif zone == "M":
            buttons = self.button_set()
            records = self._wt_visible_records()
            if buttons:
                self.sel = ("BTN", 0)
            elif records:
                self.sel = ("L", 0)
        elif zone == "PR":
            self.btn_idx = 0            # progress to the Apply button
            self.sel = ("BTN", 0)
        elif zone == "BTN":
            btn = self.active_button()
            if btn == "N":
                # New worktree… opens the options dialog directly (#1346).
                self._open_optmenu()
            elif btn == "TH":
                self.show_hidden = not self.show_hidden
                n = self._hidden_count()
                self.debug = (f"{'showing' if self.show_hidden else 'hiding'} "
                              f"{n} bridge/system worktree(s)")
                if self.sel not in self.stops():
                    self.sel = self.default_sel()
            elif btn == "K":
                self._open_cleanup()
            elif btn == "SY":
                self._open_sync()
            elif btn == "PReset":
                self.grid = dict(self.applied)
                self.debug = "reset · reverted to applied profiles"
            elif btn == "PA":
                self._apply_profiles()
        elif zone == "L":
            # Enter routing (#2258 follow-up): a real *multi*-selection (>1)
            # opens the bulk action menu; a single selection (or none) opens the
            # focused/selected row's sub-menu, which carries Open/Resume/Stop/…
            # A single selected row must still reach Open/Resume -- the operator
            # should not have to deselect it first (#1343 unified model).
            if len(self.wt_sel) > 1:
                self._open_wt_action_menu()
            else:
                self._open_submenu()
        elif zone == "C":
            # Maintenance: Enter opens the maintenance actions menu for the
            # selected set (#1345) -- it never opens/resumes a worktree.
            self._open_maint_menu()
        elif zone == "SA":
            self._toggle_maint_all()
        elif zone == "GH":
            self._toggle_group(self.sel[1])
        elif zone == "T":
            # Registered pivot: Enter opens the task action sub-menu.
            self._open_task_menu()
    def _local_model_hosts(self):
        """Machines that can host a local model (the agent runs on their GPU).

        Convention-driven from local-machine config -- intentionally NOT
        hardcoded. The data source may advertise the capability via
        ``src.local_model_hosts`` (a set of machine display names); until that
        convention is wired, this is empty, so the 'Local model' create option
        does not appear.
        """
        return set(getattr(self.src, "local_model_hosts", None) or ())
    def _open_optmenu(self):
        tm, te = self.create_target()
        opts = [
            {"label": "Anchor repo", "on": False,
             "hint": "launch in the main checkout, not a worktree"},
            {"label": "Bare", "on": False,
             "hint": "new worktree, no Copilot bootstrap"},
            {"label": "No Mux", "on": False, "hint": "skip PSMux/TMux wrapper"},
        ]
        if (tm, te) == self.src.LOCAL:
            opts.append({
                "label": "AHP",
                "on": False,
                "hint": "host Copilot through the configured same-machine AHP endpoint",
            })
        if tm in self._local_model_hosts():
            opts.append({"label": "Local model", "on": False,
                         "hint": f"run the agent on {tm}'s GPU"})
        # Open straight onto the Create button (section 1) with no options
        # checked, and confirm the target machine in the prompt (#1346).
        dlg = {"target": (tm, te), "section": 1, "bidx": 0,
               "verb": "New worktree", "confirm": "Create",
               "prompt": f"Creates on {tm} {te} · options (none required):",
               "opts": opts}

        def _after(confirmed):
            if confirmed:
                self._confirm_new_worktree(dlg)
        self.app.push_screen(ScopeDlgScreen(dlg), _after)
