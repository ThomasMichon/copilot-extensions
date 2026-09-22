#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import threading

from .engine_dialogs import SubMenuScreen, WtDetailsScreen
from .engine_live_screens import MsgViewScreen

class PickerScreenWorktreeActionsMixin:
    @staticmethod
    def _cleanable(rec):
        """Whether a worktree's cleanup bucket is one the Cleanup flow offers."""
        return rec.get("cleanup_bucket") in (
            "clean", "unused", "conversation", "gone")
    @staticmethod
    def _warning(rec) -> bool:
        """Inconsistent state: a healthy ``wt-<id>`` mux session AND a *separate*
        bare (un-muxed) bound Copilot on the same worktree (the #662
        double-binding). Stop reaches the mux but not the stray orphan; Reclaim
        would kill the orphan but is unsafe to auto-offer beside a live mux (it
        reaps un-muxed bindings machine-wide for the id). So the row is flagged
        WARNING and offered **Stop + Repair** -- Repair reaps the stray orphan
        while preserving the healthy mux, then re-derives state.
        """
        return bool(rec.get("mux_live") and rec.get("session_bare_orphan"))
    @staticmethod
    def _reclaimable(rec) -> bool:
        """Whether a bound Copilot or lock residue that **Stop cannot reach**
        (i.e. no live mux) is present, so the Reclaim verb applies. The
        load-bearing invariant: a worktree holding a bound lock or lock residue
        must always expose *some* lifecycle verb (Stop when muxed, else Reclaim)
        -- never strand the row with no way to act on it.

        True when there is **no live mux** AND any of:

        * ``session_bare_orphan`` -- the machine-wide scan flagged a **bare**
          (un-muxed) bound Copilot (#662/#1416).
        * a live bound lock -- ``session_lock_live`` (authoritative menu-open
          verdict) or ``session_bound_live`` (cached off-hot-path hint) -- whose
          homing could not be positively classified ``bare``.
        * ``session_bridge_live`` -- a live **bridge-owned** Copilot (#4272),
          read file-first from ``bridge.lock`` (cwd=home, so invisible to the
          mux + cwd-keyed lock scans -- #1416). Without this, an
          ACTIVE-via-bridge worktree with no mux and no row-visible inuse lock
          would fall through to Resume/Bare-resume -- which then FAIL because the
          bridge Copilot already holds the session -- stranding the row.
          ``reclaim_one`` resolves the bridge.lock's owner pid (via
          :func:`reclaim.resolve_bridge_bound`) so this verb actually reaps it.
        * ``session_lock_stale`` -- stale ``inuse.<pid>.lock`` residue from a
          crashed/killed session (file present, pid dead). Reclaim clears it to
          zero ("to the point where the pid lock file is removed").

        The **no-mux guard** keeps Reclaim mutually exclusive with the muxed
        ``{Open, Stop}`` group and routes the mux+orphan double-binding to
        WARNING/Repair instead (see :meth:`_warning`).
        """
        if rec.get("mux_live"):
            return False
        return bool(
            rec.get("session_bare_orphan")
            or rec.get("session_lock_live")
            or rec.get("session_bound_live")
            or rec.get("session_bridge_live")
            or rec.get("session_lock_stale")
        )
    @staticmethod
    def _session_action_verbs(rec) -> list[str]:
        """The session-lifecycle verbs a worktree's Actions menu offers, from its
        (freshly menu-verified) liveness signals -- a pure, record-driven helper
        so the gating is unit-testable and each verb lights up *when and only
        when* it applies. The caller appends bridge/system navigation +
        cross-plugin verbs, which need engine state.

        Mutually-exclusive lifecycle groups, in precedence order (the operator's
        state map). The five axes: **M**=live ``wt-<id>`` mux, **Lf/Pa**=lock
        file / live bound process, **stale**=lock residue, **H**=identifiable
        head session, **D**=worktree dir exists.

        1. **WARNING** (M ∧ stray bare orphan) -> ``Stop`` + ``Repair``.
        2. **healthy mux** (M) -> ``Open`` (only when D -- a leaked mux on a gone
           dir offers ``Stop`` alone) + ``Stop``.
        3. **bound / residue, no mux** (¬M ∧ (Pa ∨ Lf ∨ stale)) -> ``Restore``
           when a head session is known, plus ``Reclaim`` as the lower-level
           fallback. Restore performs the platform remux prep and immediately
           resumes/attaches through the normal mux launcher.
        4. **resumable** (¬M ∧ ¬bound ∧ H ∧ D) -> ``Resume`` + ``Bare resume``.
        5. **sessionless** (¬M ∧ ¬bound ∧ ¬H ∧ D) -> ``Open`` (cold start).
           Refresh (always) also attempts to recover a *lost* head session so a
           falsely-sessionless worktree becomes resumable on the next paint.
        6. **gone** (¬D, no live mux/lock) -> cleanup verbs only.

        Group exclusivity ({Open,Stop} ⊻ {Resume,Bare resume} ⊻ {Reclaim}) holds
        because M, (¬M ∧ bound), and (¬M ∧ ¬bound) partition every state.

        ``Refresh`` is ALWAYS appended -- the on-demand live gather + cache
        write-back (the only way to populate an **Unknown** row and re-derive a
        stale one), which also runs the head-session recovery repair.
        """
        if rec.get("source_kind", "machine-ssh") != "machine-ssh":
            capabilities = rec.get("source_capabilities") or {}
            acts = []
            if capabilities.get("messages"):
                acts.append("Messages")
            if capabilities.get("refresh"):
                acts.append("Refresh")
            return acts
        gone = rec.get("cleanup_bucket") == "gone"
        warning = PickerScreenWorktreeActionsMixin._warning(rec)
        reclaimable = PickerScreenWorktreeActionsMixin._reclaimable(rec)
        if warning:
            acts = ["Stop", "Repair"]
        elif rec.get("mux_live"):
            # Open attaches the live mux (needs the dir); a leaked mux on a gone
            # worktree offers Stop alone to clear it.
            acts = ["Open", "Stop"] if not gone else ["Stop"]
        elif reclaimable:
            restorable = bool(
                rec.get("last_session_id")
                and (
                    rec.get("session_bare_orphan")
                    or rec.get("session_lock_live")
                    or rec.get("session_bound_live")
                    or rec.get("session_bridge_live")
                )
            )
            acts = ["Restore", "Reclaim"] if restorable else ["Reclaim"]
        elif gone:
            acts = []
        elif not rec.get("sessionless"):
            # A stopped worktree with prior history (not positively sessionless)
            # -> Resume (relaunch + resume its last conversation). "Bare resume"
            # (create the mux but launch Copilot in HOME with no --resume, then
            # a manual ``/resume <id>``) is offered only when we have a concrete
            # head-session id to hand the operator.
            acts = ["Resume"]
            if rec.get("last_session_id"):
                acts.append("Bare resume")
        else:
            # Sessionless: cold-start Open (resuming nothing would silently
            # blank-start, #1026). A worktree that only *looks* sessionless
            # because tracking lost its head session is repaired by Refresh.
            acts = ["Open"]
        # Read-only "Messages" peek -- an auxiliary, non-lifecycle verb offered
        # for any worktree that could have a session to peek (not positively
        # sessionless) and is not in the inconsistent WARNING state. Independent
        # of the lifecycle group above (live, resumable, or residual all peek).
        if not warning and not rec.get("sessionless"):
            acts.append("Messages")
        # FF-sync / cleanup / finalize -- offered only in the non-broken,
        # non-residue lifecycle states (never beside Reclaim/Repair, where a
        # Cleanup/Sync would race the residue). A gone worktree still offers
        # Cleanup to prune the leftover record.
        if acts and not warning and not reclaimable and not gone:
            if rec.get("ff_eligible"):
                acts.append("Sync")
            if PickerScreenWorktreeActionsMixin._cleanable(rec):
                acts.append("Cleanup")
            if rec.get("cleanup_bucket") in ("conversation", "unused"):
                # A conversation-only / unused worktree (no commits) can be
                # wrapped up directly -- finalize validates nothing is unpushed
                # and removes it (#2258 follow-up).
                acts.append("Finalize")
        elif gone and PickerScreenWorktreeActionsMixin._cleanable(rec):
            acts.append("Cleanup")
        acts.append("Refresh")
        return acts
    def _submenu_target(self):
        """The record the per-row sub-menu acts on: the single selected row when
        exactly one is selected (so Enter opens/resumes *it* even if the cursor
        has drifted, #2258 follow-up), otherwise the focused row."""
        if len(self.wt_sel) == 1:
            wid = next(iter(self.wt_sel.ids))
            for r in self.list_records():
                if self._row_key(r) == wid or r.get("id4") == wid:
                    return r
        return self._selected_record()
    def _open_submenu(self):
        rec = self._submenu_target()
        if not rec:
            return
        # Always-async UX: OPEN the Actions menu IMMEDIATELY from the record's
        # current (bulk-derived) liveness -- transition to the next component at
        # once, show cached content, never freeze while probing. #4057: opening the
        # menu is one of the two moments we want the TRUTH for THIS worktree's mux +
        # bound-Copilot liveness, but that re-verify touches the mux + session locks
        # (cross-process). So when it's warranted (a real worktree with a record) we
        # run it OFF the render flow and REFINE the verb set in place when it lands;
        # the modal carries a footer spinner meanwhile. A cheap record-existence
        # stat decides whether to probe -- a mock/fixture row (no record) just opens
        # with its hand-set liveness, no spinner, no refine.
        _wt_id = (rec.get("raw") or {}).get("id")
        _need_verify = False
        if (
            self.real_ops
            and _wt_id
            and rec.get("source_kind", "machine-ssh") == "machine-ssh"
        ):
            try:
                from .. import config as _config
                _need_verify = (_config.tracking_dir() / f"{_wt_id}.yaml").exists()
            except Exception:
                _need_verify = False
        self._push_wt_submenu(rec, loading=_need_verify)
        if not _need_verify:
            return

        def _verify():
            try:
                import types as _types

                from .. import sessions as _sessions
                from .. import tracking as _tracking
                verdict = _sessions.verify_worktree_active(
                    _types.SimpleNamespace(worktree_id=_wt_id))
                try:
                    # #4057: warm the ground-layer cache from this authoritative
                    # read so the next populate can prefer the hint (throttled).
                    _tracking.stamp_mux_live(_wt_id, verdict.mux_live, refresh=True)
                except Exception:
                    pass
                return verdict
            except Exception:
                return None

        def _done(verdict):
            if verdict is not None:
                rec["mux_live"] = verdict.mux_live
                rec["attached"] = verdict.mux_clients > 0
                rec["session_lock_live"] = bool(verdict.live_session_ids)
                rec["session_bare_orphan"] = verdict.bare
            # Refine the OPEN menu's verbs in place + drop its footer spinner. The
            # main footer stays quiet -- the modal owns the load indicator.
            self._refresh_wt_submenu(rec)

        self._run_bg("Actions", _verify, _done, quiet=True)
    def _wt_submenu_verbs(self, rec):
        """Compute the worktree Actions verb list + the cross-plugin ``ext`` map
        from the record's CURRENT liveness. Pure -- engine state only, no IO -- so
        it is safe on the render flow and is recomputed cheaply on an in-place
        refine."""
        # Primary verb + Stop/Reclaim gating is the pure, record-driven
        # ``_session_action_verbs`` (unit-tested #4058); the bridge/system nav +
        # cross-plugin verbs below need engine state, so they are appended here.
        acts = self._session_action_verbs(rec)
        execution_leg = rec.get("execution_leg")
        if not isinstance(execution_leg, dict):
            execution_leg = (rec.get("raw") or {}).get("execution_leg")
        if (
            isinstance(execution_leg, dict)
            and execution_leg.get("provider") == "ahp"
            and execution_leg.get("state", "unknown") in {"active", "unknown"}
            and rec.get("source_kind", "machine-ssh") == "machine-ssh"
            and (rec.get("machine"), rec.get("env")) == self.src.LOCAL
        ):
            acts.append("Dispose hosted session")
        if rec.get("source_kind", "machine-ssh") != "machine-ssh":
            acts.append("View details")
            return acts, {}
        if self._reciprocal_target_row(rec) is not None:
            acts.append("Go to controller")
        # #1424/#2178: a bridge/system worktree is host-owned. Prefer jumping to
        # the *caller* worktree that requested it (the "caller-id"), when that
        # worktree is loaded; otherwise fall back to jumping to its own host tab.
        # Only offer an action that actually resolves, so it's never dead.
        if rec.get("kind") in ("bridge", "system"):
            caller = (rec.get("raw") or {}).get("caller_worktree")
            if caller and any(
                    (w.get("raw") or {}).get("id") == caller for w in self.data):
                acts.append("Jump to caller")
            elif self._machine_index_for(
                    rec.get("machine"),
                    rec.get("env"),
                    rec.get("source_id"),
            ) is not None:
                acts.append("Jump to host")
        # Cross-plugin worktree actions (#B): any installed layer can add a verb
        # to this menu. Only actions whose `when` matches the worktree appear,
        # and a contributed label never shadows a built-in verb.
        from . import pivots as _pivots

        ext: dict = {}
        for act in getattr(self, "wt_actions", []) or []:
            if act.label in acts or act.label in ext:
                continue
            try:
                if _pivots.worktree_action_matches(act, rec):
                    acts.append(act.label)
                    ext[act.label] = act
            except Exception:
                continue

        # View details (fold-the-claims-list follow-up): the menu's header
        # used to spell out the FULL held-claims/asset list inline, unbounded,
        # which could push the always-needed verb list past the modal's
        # max-height with no scrollbar. That full breakdown (plus path/id/
        # session/relation detail) now lives behind this always-available,
        # last-listed verb instead, so the core Actions menu itself never
        # grows past a few fixed header lines.
        acts.append("View details")
        return acts, ext
    def _reciprocal_target_row(self, rec):
        """Return the one exact loaded controller target, if navigable."""
        relation = rec.get("reciprocal_relation") or {}
        actions = relation.get("actions") or []
        candidates = [
            action
            for action in actions
            if isinstance(action, dict)
            and action.get("kind") == "navigate-worktree"
            and isinstance(action.get("target"), dict)
        ]
        if len(candidates) != 1:
            return None
        target = candidates[0]["target"]
        matches = [
            row
            for row in self.data
            if (row.get("raw") or {}).get("id") == target.get("worktree_id")
            and (row.get("raw") or {}).get("repo") == target.get("project")
            and (
                not target.get("machine")
                or row.get("machine") == target.get("machine")
            )
        ]
        return matches[0] if len(matches) == 1 else None
    def _push_wt_submenu(self, rec, *, loading=False):
        """Open the native ``SubMenuScreen`` for ``rec`` IMMEDIATELY from its
        current (cached) liveness -- never blocking the render flow. When an
        authoritative re-verify is in flight (``loading=True``), the modal shows the
        footer spinner and the verb set is refined in place by
        :meth:`_refresh_wt_submenu` when the probe lands."""
        acts, ext = self._wt_submenu_verbs(rec)
        self._wt_submenu_ext = ext
        # Migrated to a native Textual ``ModalScreen`` (#88 F4): the modal returns
        # the chosen ``(action_label, no_mux, ahp)`` via ``dismiss`` (or ``None`` on
        # cancel); ``_wt_submenu_dispatch`` runs the selected verb.
        self.app.push_screen(
            SubMenuScreen(rec, acts, loading=loading, engine=self),
            lambda result: self._wt_submenu_dispatch(rec, result),
        )
    def _refresh_wt_submenu(self, rec):
        """Refine the OPEN Actions menu's verbs in place after the async liveness
        verify lands (and drop its footer spinner). A no-op if the operator already
        closed the menu."""
        acts, ext = self._wt_submenu_verbs(rec)
        self._wt_submenu_ext = ext
        scr = getattr(self.app, "screen", None)
        if isinstance(scr, SubMenuScreen):
            scr.refresh_actions(acts)
    def _wt_submenu_dispatch(self, rec, result):
        """Run the chosen worktree Actions verb (the ``SubMenuScreen`` dismiss
        callback). Reads the CURRENT cross-plugin map (``_wt_submenu_ext``) so a
        live in-place verb refine stays consistent."""
        if result is None:
            return
        cur, no_mux, ahp = result
        ext = getattr(self, "_wt_submenu_ext", {}) or {}
        if cur in ext:
            # A cross-plugin contributed action (#B): run it and rescan.
            self._run_wt_action(ext[cur], rec)
            return
        if cur == "Open":
            self._decide(self._resume_decision(rec, no_mux=no_mux, ahp=ahp))
        elif cur == "Resume":
            # #4043: No-Mux now rides Resume too (a stopped worktree can be
            # resumed without the mux wrapper for troubleshooting), not just
            # Open. no_mux is inert unless the toggle was flipped.
            self._decide(self._resume_decision(rec, no_mux=no_mux, ahp=ahp))
        elif cur == "Bare resume":
            # Two-step restore: mux + Copilot in HOME, no --resume (#outage).
            self._decide(self._resume_decision(rec, bare_resume=True))
        elif cur == "Messages":
            # Read-only peek at the worktree's latest session messages.
            self._open_msgview(rec)
        elif cur == "Jump to host":
            # Internal navigation -- stay in the picker (#1424).
            self._jump_to_worktree((rec.get("raw") or {}).get("id"))
        elif cur == "Jump to caller":
            # Navigate to the worktree that requested this bridge (#2178).
            self._jump_to_worktree(
                (rec.get("raw") or {}).get("caller_worktree"))
        elif cur == "Go to controller":
            target = self._reciprocal_target_row(rec)
            if target is None:
                self.debug = "controller target is no longer uniquely available"
            else:
                self._jump_to_worktree(
                    (target.get("raw") or {}).get("id"),
                    source_id=target.get("source_id"),
                )
        elif cur == "Sync":
            # Real per-worktree FF-sync via the shared dialog (#1427).
            self._open_sync(ids={self._row_key(rec)})
        elif cur == "Cleanup":
            self._open_cleanup(ids={self._row_key(rec)})
        elif cur == "Finalize":
            # Wrap up a conversation-only / unused worktree (#2258 follow-up).
            self._start_finalize([rec])
        elif cur == "Dispose hosted session":
            self._decide({
                "action": "dispose-hosted-session",
                "worktree_id": (rec.get("raw") or {}).get("id") or rec.get("id4"),
                "title": rec.get("title"),
                "is_local": rec.get("is_local", True),
                "machine": rec.get("machine"),
                "env": rec.get("env"),
            })
        elif cur == "Stop":
            # Stop the worktree's Mux/Copilot wrapper on demand (#1343),
            # freeing it to be re-Opened/Resumed with a fresh Mux + Copilot.
            self._start_stop(rec)
        elif cur == "Reclaim":
            # Kill the exact Copilot process holding the session's lock
            # (bare orphans Stop cannot reach); then re-Open / Bare resume.
            self._start_reclaim(rec)
        elif cur == "Restore":
            decision = self._resume_decision(rec)
            decision["action"] = "restore"
            self._decide(decision)
        elif cur == "Repair":
            # Reconcile the mux+stray-orphan double-binding: reap the bare
            # orphan while preserving the healthy mux, clear stale locks.
            self._start_repair(rec)
        elif cur == "Refresh":
            # picker-cache-first-paint (dotfiles#948): on-demand live gather
            # + cache write-back for THIS worktree (populates an Unknown row
            # / re-syncs a stale one) without a full-fleet reload.
            self._start_refresh(rec)
        elif cur == "View details":
            # Instant, IO-free scrollable card (fold-the-claims-list
            # follow-up): everything the Actions menu's own bounded header
            # can't fit -- full title/path/id/status, held claims, session
            # detail -- from data already on ``rec`` (no new gather).
            self.app.push_screen(WtDetailsScreen(rec))
    def _wt_action_ctx(self, rec: dict) -> dict:
        """Placeholder context for a contributed worktree action's argv template:
        the worktree's id/machine/env/repo plus its (scalar) raw fields."""
        raw = rec.get("raw") or {}
        ctx = {k: v for k, v in raw.items()
               if isinstance(v, (str, int, float, bool))}
        ctx.update({
            "worktree": raw.get("id") or rec.get("id4"),
            "id": raw.get("id") or rec.get("id4"),
            "id4": rec.get("id4"),
            "machine": str(rec.get("machine") or raw.get("machine") or ""),
            "env": str(rec.get("env") or ""),
            "repo": getattr(self.src, "REPO", "") or "",
            "title": rec.get("title", ""),
            "state": rec.get("state", ""),
        })
        return ctx
    def _run_wt_action(self, action, rec: dict) -> None:
        """Run a contributed worktree action (subprocess), report the outcome,
        and rescan (the action may have changed worktree/session state). The
        subprocess runs OFF the render flow via :meth:`_run_bg`."""
        from . import tasks

        label, source = action.label, action.source
        ctx = self._wt_action_ctx(rec)

        def _work():
            return tasks.run_worktree_action(action, ctx)

        def _done(result):
            ok, msg = result
            self.debug = (f"{label} ({source}): {msg or 'done'}" if ok
                          else f"{label} ({source}) failed: {msg}")
            try:
                self.setup()
                self.sel = self.default_sel()
            except Exception:
                pass

        self._run_bg(label, _work, _done)
    def _open_msgview(self, rec, *, limit=3):
        """Open the recent-messages overlay for a worktree and start loading.

        Read-only companion to the disposition summary: shows the last few
        conversation turns of the worktree's latest session so the operator can
        tell what it was doing (and whether it still needs follow-up) without
        opening it. The load runs off the render thread -- local worktrees call
        the attributable provider CLI; remote worktrees run the same
        ``recent-messages`` contract over SSH. Never blocks or crashes the UI:
        a failure resolves to an error line in the overlay.

        Migrated to a native Textual ``ModalScreen`` (#88 F4): this builds the
        engine-owned ``self.msgview`` dict, starts the daemon loader thread
        (which populates it under ``_msgview_lock``), then pushes the
        ``MsgViewScreen`` that renders it, repaints while it loads, and mirrors
        the old ``_key_msgview`` (scroll + close). The screen holds no state of
        its own and needs no callback.
        """
        self.msgview = {
            "rec": rec, "limit": limit, "loading": True,
            "messages": [], "error": None, "session_id": None, "scroll": 0,
            "sessions": [],
        }
        threading.Thread(
            target=self._msgview_worker, args=(rec, limit),
            name="msgview-load", daemon=True,
        ).start()
        self.app.push_screen(MsgViewScreen(self))
    def _load_worktree_sessions(self, rec, wt_id, m, e):
        """Fetch the worktree's Copilot session registry (id + title + head).

        Best-effort, off the render thread: local worktrees call the provider
        CLI; remote worktrees run ``list-sessions`` over SSH. Returns a
        list of session dicts (each carries ``id``, ``name``, ``is_head``,
        ``state``) or ``[]`` on any error -- the session list is a diagnostic
        aid, never worth failing the overlay over.
        """
        try:
            if (m, e) == getattr(self.src, "LOCAL", None):
                from worktree_manager import engine_client

                from .. import context

                return engine_client.list_worktree_sessions(
                    context.project(), wt_id, runner=self._provider_runner())
            from . import data_ssh, maintenance
            argv = data_ssh.list_sessions_argv(
                m,
                e,
                wt_id,
                source_id=rec.get("source_id"),
                expected_instance_id=(rec.get("source") or {}).get("instance_id"),
                expected_assignment=(rec.get("source") or {}).get("venue", {}).get(
                    "assignment"
                ),
                expected_transport_fingerprint=(rec.get("source") or {}).get(
                    "transport_fingerprint"
                ),
            )
            if argv is None:
                return []
            payload = maintenance._ssh_json(argv)
            sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
            return sessions if isinstance(sessions, list) else []
        except Exception:
            return []
    def _msgview_worker(self, rec, limit):
        """Daemon-thread body: fetch recent messages + the session list."""
        wt_id = (rec.get("raw") or {}).get("id")
        m, e = rec.get("machine"), rec.get("env")
        sessions_list: list = []
        try:
            if not wt_id:
                payload = {"error": "no worktree id"}
            elif (m, e) == getattr(self.src, "LOCAL", None):
                from worktree_manager import engine_client

                from .. import context

                payload = engine_client.recent_worktree_messages(
                    context.project(),
                    wt_id,
                    limit=limit,
                    runner=self._provider_runner(),
                )
            else:
                from . import data_ssh, maintenance
                argv = data_ssh.recent_messages_argv(
                    m,
                    e,
                    wt_id,
                    source_id=rec.get("source_id"),
                    expected_instance_id=(rec.get("source") or {}).get("instance_id"),
                    expected_assignment=(rec.get("source") or {}).get(
                        "venue", {}
                    ).get("assignment"),
                    expected_transport_fingerprint=(rec.get("source") or {}).get(
                        "transport_fingerprint"
                    ),
                    limit=limit,
                )
                if argv is None:
                    payload = {"error": f"no remote route to {m} {e}"}
                else:
                    payload = maintenance._ssh_json(argv)
        except Exception as exc:  # never let the loader thread crash the UI
            payload = {"error": str(exc) or type(exc).__name__}

        # The session list is a separate, best-effort fetch (diagnostic aid);
        # a failure here never turns the overlay into an error.
        if wt_id:
            sessions_list = self._load_worktree_sessions(rec, wt_id, m, e)

        with self._msgview_lock:
            # A late result for a viewer the operator already closed / reopened
            # is dropped (identity check on the live overlay's rec).
            mv = self.msgview
            if mv is None or mv.get("rec") is not rec:
                return
            mv["loading"] = False
            mv["sessions"] = sessions_list
            if payload.get("error"):
                mv["error"] = payload["error"]
            else:
                mv["messages"] = payload.get("messages", [])
                mv["session_id"] = payload.get("session_id")
    def _key_msgview(self, key):
        """Keys for the recent-messages overlay: scroll + close."""
        mv = self.msgview
        if key in ("escape", "q", "tab", "enter"):
            self.msgview = None
            return
        if key == "down":
            mv["scroll"] = mv.get("scroll", 0) + 1
        elif key == "up":
            mv["scroll"] = max(0, mv.get("scroll", 0) - 1)
    def _machine_index_for(self, machine, env, source_id=None):
        """Index of the (machine, env) tab in ``self.machines``, or None."""
        for i, (label, m, e, _ok) in enumerate(self.machines):
            tab_source_id = (
                self.source_tabs[i].get("source_id") if self.source_tabs else None
            )
            if label != "All" and (
                (source_id and tab_source_id and tab_source_id == source_id)
                or ((not source_id or not tab_source_id) and m == machine and e == env)
            ):
                return i
        return None
    def _find_internal_worktree(self, wid, source_id=None):
        if not wid:
            return None, "no worktree id"
        matches = [
            row
            for row in self.data
            if (row.get("raw") or {}).get("id") == wid
            and (source_id is None or row.get("source_id") == source_id)
        ]
        if not matches:
            return None, "worktree not found on any loaded source"
        if len(matches) != 1:
            return None, "worktree id is ambiguous across loaded sources"
        return matches[0], None
    def _jump_to_worktree(self, wid, source_id=None):
        """#1424/#1425: navigate to the Worktrees view, the host machine tab of
        the worktree with id ``wid``, and highlight that row.

        Resolves the row by **stable worktree id** (never a live list index,
        which shifts under machine-switch / reveal-hidden / background refresh),
        reveals hidden so a bridge/system row is focusable, and switches off any
        registered pivot. Internal navigation only -- never exits the picker.
        Returns ``(ok, message)``.
        """
        row, error = self._find_internal_worktree(wid, source_id)
        if row is None:
            return False, error
        machine, env = row.get("machine"), row.get("env")
        idx = self._machine_index_for(
            machine,
            env,
            source_id=row.get("source_id"),
        )
        if idx is None:
            self.debug = f"jump: host {machine} {env} not available"
            return False, f"host {machine} {env} not available"
        # Land on the Worktrees pivot (the jump can originate from a registered
        # pivot's internal action).
        for i, p in enumerate(self.pivots):
            if p["kind"] == "worktrees":
                self.htab = i
                break
        self.machine_idx = idx
        # Reveal the hidden set only if the jump target is itself hidden
        # (origin-based, #2668): a User-origin bridge/ACP worktree is already
        # visible, so jumping to it must not force the whole automation set open.
        if row.get("hidden") if "hidden" in row else (
                (row.get("kind") or "session") in ("system", "bridge")):
            self.show_hidden = True
        # If the target is filtered out by an active "/" query, clear the
        # query first (so the jump can't silently fail) then resolve index.
        # Only clear when the query actually HIDES the target (review
        # finding): a target already visible under the current filter must
        # not have the operator's query wiped out from under them.
        visible = self._wt_visible_records()
        target_visible = any(
            (r.get("raw") or {}).get("id") == wid for r in visible)
        if not target_visible and self.list_view.query:
            full_match = any(
                (r.get("raw") or {}).get("id") == wid
                for r in self.list_records())
            if full_match:
                self._wt_remap(self.list_view.clear)
        records = self._wt_visible_records()
        target_i = next(
            (i for i, r in enumerate(records)
             if (r.get("raw") or {}).get("id") == wid),
            None,
        )
        if target_i is None:
            # The tab may still be loading, or the row is filtered out; land on a
            # safe default so focus is never left on a phantom stop.
            self.sel = self.default_sel()
            self.debug = f"jumped to {machine} {env} (row not yet loaded)"
            return True, f"switched to {machine} {env}"
        self.sel = ("L", target_i)
        self.btn_idx = 0
        self.debug = f"jumped to {machine} {env} · …{str(wid)[-4:]}"
        return True, f"jumped to {machine} {env}"
    def _internal_pivot_action(self, verb, ctx):
        """Dispatch a registered pivot's INTERNAL (picker-navigation) action
        (#1425). Tiny and defensive: an unknown verb is a reported failure, never
        an exception. ``jump-host`` navigates to the entry's worktree by id;
        ``open-cli`` opens that worktree into a CLI session (exits the picker with
        a resume decision -- the shared launch-plumbing, #2253); ``open-venue``
        (picker-venue-pivots Phase 3) opens a remote venue row (a CodeSpace or
        fleet container) into a live/dormant Copilot session via the venue's own
        `copilot` verb, the same exit-and-launch plumbing."""
        if verb == "jump-host":
            return self._jump_to_worktree(
                ctx.get("worktree") or ctx.get("id"),
                ctx.get("source_id"),
            )
        if verb == "open-cli":
            return self._open_worktree_cli(
                ctx.get("worktree") or ctx.get("id"),
                ctx.get("source_id"),
            )
        if verb == "open-venue":
            return self._open_venue(ctx)
        return False, f"unknown internal action: {verb}"
    def _open_worktree_cli(self, wid, source_id=None):
        """#2253: open the worktree ``wid`` into a CLI session, the same way
        selecting its Worktrees row + Open does.

        Resolves the row by **stable worktree id** across loaded machines, builds
        the standard resume decision, and exits the picker so ``__main__`` maps it
        onto the resume/launch path (which, for a task pinned to that worktree,
        surfaces the handoff via ``/resume-handoff`` in the opened session). The
        launch-decision plumbing -- not a subprocess -- so a remote worktree still
        routes through the normal SSH handoff. Returns ``(ok, message)``; on
        success the caller's post-action re-anchor is a harmless no-op since the
        app is already exiting."""
        row, error = self._find_internal_worktree(wid, source_id)
        if row is None:
            return False, error
        if row.get("source_kind") != "machine-ssh":
            return False, "provider-backed worktrees are read-only"
        decision = self._resume_decision(row)
        self._decide(decision)
        return True, f"opening …{str(wid)[-4:]} into a CLI session"

    def _open_venue(self, ctx):
        """picker-venue-pivots Phase 3: open a CodeSpaces/Containers pivot row
        into a live/dormant Copilot session via the venue's own ``copilot``
        verb (``agent-codespaces copilot <name>`` / ``agent-containers copilot
        <name>``) -- the same reserve/ensure-mux/attach-or-embody contract
        either provider already implements for a live *or* dormant venue
        (embody-or-attach is uniform; there is no separate "live-only" case
        to gate on). Exits the picker so ``__main__`` can hand the operator a
        real TTY (the venue command needs one for its interactive SSH
        session) -- the same exit-and-launch plumbing ``open-cli`` uses for a
        local worktree, just with a different provider on the other end."""
        provider = ctx.get("provider")
        venue = ctx.get("id")
        if not provider or not venue:
            return False, "missing provider/venue identity for this row"
        self._decide({
            "action": "open-venue",
            "provider": str(provider),
            "venue": str(venue),
            "title": ctx.get("title"),
        })
        return True, f"opening {venue} into a Copilot session"
