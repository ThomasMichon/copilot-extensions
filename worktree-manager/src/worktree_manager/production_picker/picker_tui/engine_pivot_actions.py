#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import logging

from .engine_dialogs import CfgMenuScreen, ScopeDlgScreen, TaskMenuScreen
from .create_action_screen import CreateActionScreen
from .background import run_background
from .inbox import ensure_inbox
from .steering import PivotCardScreen, PivotFormScreen, SubmitErrorScreen, _normalize_form_fields, _steer_draft_path

log = logging.getLogger("agent-worktrees.picker")

class PickerScreenPivotActionsMixin:
    def _cfgmenu_items(self):
        """The ⚙ Configuration menu entries, in display order: built-in
        config-placed pivots (Profiles) first, then cross-plugin contributed
        Configuration sections (#B slice 2). Each is a typed descriptor --
        ``{"kind": "pivot", "idx", "label"}`` (switches to that pivot) or
        ``{"kind": "section", "section", "label"}`` (runs a contributed command
        on Enter)."""
        items: list[dict] = [
            {"kind": "pivot", "idx": i, "label": self.htabs[i]}
            for i in self._config_pivots()
        ]
        for cs in getattr(self, "config_sections", []) or []:
            items.append({"kind": "section", "section": cs, "label": cs.label})
        return items
    def _open_cfgmenu(self):
        """Open the Configuration menu: the pivots placed under ⚙ Configuration
        (Profiles) plus any contributed Configuration sections. Selecting a
        pivot switches to it and focuses its body; selecting a section runs its
        command. User-local config only -- never repo-managed settings.

        Migrated to a native Textual ``ModalScreen`` (#88 F4): ``push_screen``s a
        ``CfgMenuScreen`` that owns the stack, focus, and key routing and returns
        the chosen item index via ``dismiss(int|None)``; the callback acts on the
        selection (``None`` cancels)."""
        items = self._cfgmenu_items()
        if not items:
            return
        # If the current pivot is already config-hosted, pre-select it.
        cur = next(
            (n for n, it in enumerate(items)
             if it["kind"] == "pivot" and it["idx"] == self.htab),
            0,
        )

        def _after(choice):
            if choice is None:
                return
            target = items[choice]
            if target["kind"] == "section":
                # A cross-plugin contributed Configuration section (#B slice 2):
                # run it and rescan (it may have changed config/session state).
                self._run_config_section(target["section"])
                return
            self.htab = target["idx"]
            self.btn_idx = 0
            self.top = 0
            self.sel = self.default_sel()      # focus the hosted pivot's body
        self.app.push_screen(CfgMenuScreen(items, cur), _after)
    def _config_section_ctx(self) -> dict:
        """Placeholder context for a contributed config section's argv template:
        the current machine identity and the picker's repo (config sections are
        global -- there is no per-worktree context)."""
        return {
            "machine": self._pivot_machine_id() or "",
            "repo": getattr(self.src, "REPO", "") or "",
        }
    def _run_config_section(self, section) -> None:
        """Run a contributed Configuration section (subprocess), report the
        outcome, and rescan (it may have changed config/session state). The
        subprocess runs OFF the render flow via :meth:`_run_bg`."""
        from . import tasks

        label, source = section.label, section.source
        ctx = self._config_section_ctx()

        def _work():
            return tasks.run_config_section(section, ctx)

        def _done(result):
            ok, msg = result
            self.debug = (f"{label} ({source}): {msg or 'done'}" if ok
                          else f"{label} ({source}) failed: {msg}")
            try:
                self._start_setup_reload_worker()
                self.sel = self.default_sel()
            except Exception:
                pass

        self._run_bg(label, _work, _done)
    def _open_task_menu(self):
        """Open the Enter action sub-menu for the focused task, built from the
        registered pivot's declared ``actions``.

        Migrated to a native Textual ``ModalScreen`` (#88 F4): ``push_screen``s a
        ``TaskMenuScreen`` that owns the stack, focus, and key routing and returns
        the chosen action index via ``dismiss(int|None)``; the callback runs the
        selected action (``None`` cancels)."""
        reg = self._reg_pivot()
        rec = self._selected_task()
        if reg is None or not rec or not reg.actions:
            if reg is not None and not reg.actions:
                self.debug = f"{reg.label}: no actions declared"
            return
        # D3: a pivot action may carry a `when` gate, so a verb only appears for
        # a row whose entry matches (e.g. Release only for an in-use CodeSpace).
        # Reuse the same matcher the contributed worktree actions use.
        from . import pivots as _pivots

        actions = [
            a for a in reg.actions
            if _pivots.entry_matches(getattr(a, "when", None), rec)
        ]
        if not actions:
            self.debug = f"{reg.label}: no actions for this row"
            return

        def _after(choice):
            if choice is not None:
                self._run_task_action(reg, actions[choice], rec)
        self.app.push_screen(TaskMenuScreen(reg, rec, actions), _after)
    def _task_action_ctx(self, reg, rec):
        """Placeholder context for an action's argv template: the entry's own
        fields plus picker context ({machine}, {worktree}, {id}, {title})."""
        ctx = {k: v for k, v in rec.items() if isinstance(k, str)}
        ctx.setdefault("id", rec.get(reg.id_field))
        ctx["task_id"] = rec.get(reg.id_field)
        ctx["title"] = rec.get(reg.title_field)
        wt = rec.get(reg.worktree_field) if reg.worktree_field else None
        ctx["worktree"] = wt or ""
        ctx["machine"] = self._pivot_machine_id() or ""
        # picker-venue-pivots Phase 3: the pivot's own `list` argv[0] is the
        # provider binstub name (e.g. "agent-codespaces"/"agent-containers"),
        # reused verbatim by the "open-venue" internal action so it never
        # hardcodes a provider list of its own.
        ctx["provider"] = reg.list_cmd[0] if reg.list_cmd else ""
        return ctx
    def _run_bg(self, label, work, done=None, *, quiet=False):
        """Run a blocking cross-process / IO callable OFF the Textual render flow.

        Every pivot/worktree/config action shells out (agent-dispatch, git,
        ssh, ...); running that inline in the key/modal-dismiss handler
        blocked the event loop for up to the action timeout (30s), freezing
        the TUI and reading as a crash. A thin, signature-preserving wrapper
        around ``background.run_background`` -- the one sanctioned
        implementation of "thread + marshal the outcome back via the inbox"
        (see inbox.py/background.py); no caller of ``_run_bg`` needs to
        change. Returns immediately.

        While it runs, the footer shows the shared animated spinner +
        ``label`` (via ``_busy_label``). Pass ``quiet=True`` when a
        different surface already shows the load state -- then the main
        footer is left alone and ``done`` is still invoked (``None`` on
        error) so the caller can always finalize.

        The worker is tracked on ``self._bg_threads`` and honors
        ``self._bg_cancel``, set by ``on_unmount`` when the picker is torn
        down."""
        def _set_busy_label(value):
            self._busy_label = value

        def _set_debug(value):
            self.debug = value

        # A minimal test double that never ran PickerScreen.__init__ (and so
        # never got an Inbox) still gets a working one here, lazily --
        # _run_bg's own contract (never requiring a widget's real __init__)
        # must not regress just because the marshalling mechanism changed.
        inbox = ensure_inbox(self)

        run_background(
            inbox=inbox,
            bg_threads=self._bg_threads,
            cancel=self._bg_cancel,
            label=label,
            work=work,
            done=done,
            quiet=quiet,
            set_busy_label=_set_busy_label,
            set_debug=_set_debug,
            thread_name_prefix="pivot-action",
        )
    def _run_task_action(self, reg, action, rec):
        """Execute one task action, then invalidate the cached list so the row
        reflects the new state on the next fetch. An INTERNAL (navigation) action
        is dispatched in-process and does NOT invalidate the cache (#1425). A
        ``progress`` action (D4) streams into the modal ProgressScreen instead of
        the sync status-line path."""
        ctx = self._task_action_ctx(reg, rec)
        title = rec.get(reg.title_field) or rec.get(reg.id_field) or "task"
        if action.internal:
            ok, msg = self._internal_pivot_action(action.internal, ctx)
            # A navigation action re-anchors selection itself; only guard against
            # a now-invalid stop.
            if self.sel not in self.stops():
                self.sel = self.default_sel()
            if not ok:
                self.debug = f"{action.label} failed · {msg or 'see logs'}"
            return
        if getattr(action, "card", None) is not None:
            # A5: read-only scrollable card-detail modal; no subprocess, no cache
            # invalidation (nothing mutated).
            self._open_pivot_card(reg, action, rec)
            return
        if getattr(action, "form", None) is not None:
            # A5: native elicitation modal -> on submit, {field.<name>} tokens in
            # the action's `run` are substituted and the command is run (the steer
            # transport). Cache invalidation happens after the submitted run.
            self._open_pivot_form(reg, action, rec, ctx, title)
            return
        if getattr(action, "progress", False):
            self._run_task_action_progress(reg, action, ctx, title)
            return
        rt = self._pivot_runtime(reg)

        def _work():
            ok, msg = rt.run_action(action, ctx)
            rt.invalidate()   # off-thread: the next fetch reflects the new state
            return ok, msg

        def _done(result):
            ok, msg = result
            # The focused row may vanish after the action; re-anchor selection.
            if self.sel not in self.stops():
                self.sel = self.default_sel()
            short = (msg or "").splitlines()[0][:80] if msg else ""
            if ok:
                # Kick an immediate re-fetch + repaint of the current scope so the
                # row reflects the mutation at once (see _run_pivot_form_submit).
                try:
                    rt.ensure(self._pivot_scope_key())
                except Exception:
                    pass
                self.refresh()
                self.debug = f"{action.label} · {title}" + (f" — {short}" if short else "")
            else:
                self.debug = f"{action.label} failed · {short or 'see command output'}"

        self._run_bg(action.label, _work, _done)
    def _run_task_action_progress(self, reg, action, ctx, title):
        """Run a D4 progress-reporting pivot action: stream its NDJSON progress
        into the modal :class:`ProgressScreen`.

        Builds an ``action-stream`` progress dict the screen renders live (verb +
        message + optional pct bar), spawns a daemon reader that drives the
        pivot runtime's :meth:`RegisteredPivotRuntime.run_action_stream`, and
        updates the dict in place per frame. A late reader (the run the operator
        already closed) is dropped by identity. Esc cancels via the recorded
        ``cancel`` callback; the cache is invalidated on completion so the row
        reflects the mutation."""
        import threading as _threading

        rt = self._pivot_runtime(reg)
        cancel_ev = _threading.Event()
        prog = {
            "kind": "action-stream",
            "verb": action.label,
            "title": str(title),
            "msg": "starting…",
            "pct": None,
            "done": False,
            "error": "",
            "armed": True,
            "items": [],
            "cancel": cancel_ev.set,
        }
        self.progress = prog

        def _on_frame(pct, msg):
            if self.progress is prog:
                if pct is not None:
                    prog["pct"] = max(0.0, min(100.0, pct))
                if msg:
                    prog["msg"] = msg

        def _worker():
            ok, msg = rt.run_action_stream(
                action, ctx, _on_frame, should_cancel=cancel_ev.is_set)
            rt.invalidate()
            if self.progress is prog:
                prog["done"] = True
                if not ok:
                    prog["error"] = msg or "failed"
                elif msg:
                    prog["msg"] = msg

        _threading.Thread(target=_worker, daemon=True).start()
        self._open_progress()
    def _open_pivot_card(self, reg, action, rec):
        """Open a read-only scrollable card-detail modal for the focused task
        (A5). The card's title/status/link/body are pulled from the action's
        declared dotted paths (defaults ``card.*``); nothing is mutated, so no
        subprocess runs and the list cache is untouched."""
        from . import pivots as _pivots

        spec = action.card or {}
        card = {
            "title": _pivots.resolve_path(rec, spec.get("title_from")),
            "status": _pivots.resolve_path(rec, spec.get("status_from")),
            "link": _pivots.resolve_path(rec, spec.get("link_from")),
            "body": _pivots.resolve_path(rec, spec.get("body_from")),
        }
        row_title = rec.get(reg.title_field) or rec.get(reg.id_field) or ""
        self.app.push_screen(PivotCardScreen(str(row_title), card))
    def _open_pivot_form(self, reg, action, rec, ctx, title):
        """Open the docked card + tabbed-elicitation modal for a ``kind:"form"``
        action. Reads the request-input field spec from the action's
        ``fields_from`` dotted path (e.g. ``card.request_input``) and the card
        prose (title/status/link/body) from the standard ``card.*`` paths (the
        action's ``title_from`` overrides the header). On Confirm it substitutes
        ``{field.<name>}`` / ``{fields}`` (+ entry tokens like ``{task_id}``)
        into ``run`` and executes it via the pivot runtime (the steer
        transport). Save persists the answer as the task's durable
        ``card_draft`` on the coordinator instead of submitting a steer -- the
        task stays blocked; Reset (wired inside the modal itself) clears that
        same coordinator draft. Escape behaves exactly like Save."""
        from . import pivots as _pivots

        spec = action.form or {}
        raw_fields = _pivots.resolve_path(rec, spec.get("fields_from"))
        fields = _normalize_form_fields(raw_fields)
        card = {
            "title": _pivots.resolve_path(rec, spec.get("title_from")) or title,
            "status": _pivots.resolve_path(rec, "card.status"),
            "link": _pivots.resolve_path(rec, "card.link"),
            "body": (_pivots.resolve_path(rec, spec.get("body_from"))
                     or _pivots.resolve_path(rec, "card.body")),
        }
        task_id = ctx.get("task_id") or rec.get(reg.id_field) or ""

        def _after(result):
            if result is None:
                self.debug = f"{action.label} · cancelled (no submit)"
                return
            kind = result.get("action")
            values = result.get("values") or {}
            if kind == "confirm":
                self._run_pivot_form_submit(reg, action, ctx, title, values)
            elif kind == "save":
                self._run_pivot_form_draft_save(reg, ctx, title, values)

        def _on_clear_draft():
            self._run_pivot_form_draft_clear(reg, ctx, title)

        self.app.push_screen(
            PivotFormScreen(
                card, fields, action.label, task_id=str(task_id),
                on_clear_draft=_on_clear_draft,
            ),
            _after,
        )
    def _run_pivot_form_submit(self, reg, action, ctx, title, values):
        """Run a submitted form action (A5): resolve ``{field.<name>}`` + entry
        tokens in one safe pass, run the command via the pivot runtime, then
        invalidate the cached list so the row reflects the new state. The command
        (e.g. ``agent-dispatch steer submit``) is a subprocess, so it runs OFF the
        render flow via :meth:`_run_bg` -- Confirm returns instantly and the UI
        stays live while the coordinator round-trip completes.

        A blocking (but async -- the render flow is never frozen) modal
        progress overlay covers that whole round-trip (operator report,
        2026-10-07): Confirm used to dismiss straight back to the list with
        only a footer status line as evidence anything was happening, so a
        slow or hung coordinator looked indistinguishable from 'it worked'
        until the result eventually, silently, landed. Reuses the existing
        ``action-stream`` :class:`ProgressScreen` kind
        (``_run_task_action_progress``'s own shape): a spinner while
        in-flight, then an explicit done state the operator dismisses
        themselves (Enter/Esc/Space, same as every other action-stream run)
        -- so a successful delivery is *seen*, not just inferred from the
        overlay's absence. On failure the overlay hands off to the richer,
        full-detail :class:`SubmitErrorScreen` (draft-recovery guidance an
        action-stream run's own terse inline error can't carry) instead of
        rendering the failure itself."""
        from . import pivots as _pivots

        rt = self._pivot_runtime(reg)
        argv = _pivots.format_form_template(action.run, ctx, values)
        task_id = ctx.get("task_id")

        prog = {
            "kind": "action-stream",
            "verb": action.label,
            "title": str(title),
            "msg": "delivering…",
            "pct": None,
            "done": False,
            "error": "",
            "armed": True,
            "items": [],
            "cancel": None,
        }
        self.progress = prog
        self._open_progress()

        def _work():
            try:
                ok, msg = rt.run_resolved(argv)
            except Exception as exc:  # never let a delivery attempt vanish silently
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            else:
                try:
                    rt.invalidate()
                except Exception:
                    pass
            return ok, msg

        def _done(result):
            ok, msg = result
            if self.sel not in self.stops():
                self.sel = self.default_sel()
            short = (msg or "").splitlines()[0][:80] if msg else ""
            if ok:
                # The steer form's Confirm always saves a draft before this
                # subprocess even runs (so a failed submission never loses the
                # operator's answer). Once the submission has genuinely
                # succeeded, that draft has served its purpose -- clear it so a
                # future card for this task doesn't restore stale content
                # (the coordinator clears its own ``card_draft`` server-side on
                # a successful steer; this is just the local copy).
                if task_id:
                    path = _steer_draft_path(str(task_id))
                    try:
                        if path and path.exists():
                            path.unlink()
                    except OSError:
                        pass
                # ``_work`` already invalidated the cached list; kick an immediate
                # re-fetch of the current scope and repaint so the row reflects
                # the new state at once, instead of waiting on the idle ~2fps tick
                # (the Tasks pivot has no poll loop of its own). Combined with the
                # runtime's generation guard, a Confirmed card's status updates in
                # the pivot the moment the coordinator round-trip lands.
                try:
                    rt.ensure(self._pivot_scope_key())
                except Exception:
                    pass
                self.refresh()
                self.debug = f"{action.label} · {title}" + (f" — {short}" if short else "")
                if self.progress is prog:
                    prog["done"] = True
                    prog["msg"] = short or "delivered"
            else:
                # Fail-fast (#2453): a delivery failure must never be reducible
                # to this easily-missed footer line alone -- a blocking modal
                # names exactly where the answer is still safely recoverable
                # from (the local draft this method deliberately did NOT
                # delete above). Pop the in-flight progress overlay first
                # (synchronously -- it's still the top screen; nothing could
                # have interjected during Confirm's own round-trip) so the
                # richer error surface replaces it instead of stacking on
                # top of it.
                self.debug = f"{action.label} failed · {short or 'see command output'}"
                draft_path = _steer_draft_path(str(task_id)) if task_id else None
                if self.progress is prog:
                    self.progress = None
                    self.app.pop_screen()
                self.app.push_screen(
                    SubmitErrorScreen(action.label, msg or short, draft_path)
                )

        self._run_bg(action.label, _work, _done)
    def _open_create_action(self):
        """Open the pivot-level ``create_action`` modal (Phase B,
        picker-new-session-prompt-and-composer) -- the registered-pivot
        counterpart to ``_open_pivot_form``, minus the row/card: there is no
        selected entry to resolve fields/tokens against, so ``fields`` comes
        straight from the manifest's own static ``create_action.fields``,
        except any field declaring ``options_command`` (Phase B item 3): that
        field's ``options`` are instead resolved live, off the render flow,
        right before the modal opens -- see :meth:`_open_create_action_now`."""
        reg = self._reg_pivot()
        if reg is None or reg.create_action is None:
            return
        action = reg.create_action
        fields = list(action.fields)
        if not any(f.get("options_command") for f in fields):
            self._open_create_action_now(reg, action, fields)
            return

        from . import tasks

        def _work():
            resolved = []
            for f in fields:
                cmd = f.get("options_command")
                if not cmd:
                    resolved.append(f)
                    continue
                live = tasks.resolve_dynamic_options(cmd)
                merged = dict(f)
                if live:
                    merged["options"] = live
                resolved.append(merged)
            return resolved

        def _done(resolved_fields):
            self._open_create_action_now(reg, action, resolved_fields)

        self._run_bg(f"{action.label} · loading options", _work, _done, quiet=True)

    def _open_create_action_now(self, reg, action, fields):
        def _after(values):
            if values is None:
                self.debug = f"{action.label} · cancelled"
                return
            self._run_create_action(reg, action, values)

        self.app.push_screen(
            CreateActionScreen(action.label, fields, confirm=action.confirm),
            _after,
        )
    def _run_create_action(self, reg, action, values):
        """Run a submitted ``create_action`` (Phase B): substitute
        ``{field.<name>}``/``{fields}`` tokens (no entry tokens -- there is no
        row) and run the command via the pivot runtime, off the render flow
        (:meth:`_run_bg`) exactly like the row-scoped form action. Invalidates
        the cached list after every completed attempt (success OR failure,
        mirroring `_run_pivot_form_submit`) -- a failing command may still
        have mutated state before returning nonzero, and the old rows would
        otherwise stay cached indefinitely."""
        from . import pivots as _pivots

        rt = self._pivot_runtime(reg)
        argv = _pivots.format_form_template(action.run, {}, values)

        def _work():
            try:
                ok, msg = rt.run_resolved(argv)
            except Exception as exc:  # never let a delivery attempt vanish silently
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            else:
                try:
                    rt.invalidate()
                except Exception:
                    pass
            return ok, msg

        def _done(result):
            ok, msg = result
            short = (msg or "").splitlines()[0][:80] if msg else ""
            if ok:
                try:
                    rt.ensure(self._pivot_scope_key())
                except Exception:
                    pass
                self.refresh()
                self.debug = f"{action.label} · created" + (f" — {short}" if short else "")
            else:
                self.debug = f"{action.label} failed · {short or 'see command output'}"
                self.app.push_screen(SubmitErrorScreen(action.label, msg or short, None))

        self._run_bg(action.label, _work, _done)
    def _run_pivot_form_draft_save(self, reg, ctx, title, values):
        """Persist an operator's not-yet-submitted draft answer as the task's
        durable ``card_draft`` on the coordinator (``agent-dispatch card draft
        save``) -- the Steer form's **Save**. Never touches ``awaiting_steer``
        or the task's status; the task stays blocked exactly as before. Runs
        off the render flow via :meth:`_run_bg`, mirroring
        :meth:`_run_pivot_form_submit`."""
        from . import pivots as _pivots

        rt = self._pivot_runtime(reg)
        task_id = ctx.get("task_id")
        argv = _pivots.format_form_template(
            ["agent-dispatch", "card", "draft", "save", "{task_id}", "{fields}"],
            ctx, values,
        )

        def _work():
            try:
                return rt.run_resolved(argv)
            except Exception as exc:
                return False, f"{type(exc).__name__}: {exc}"

        def _done(result):
            ok, msg = result
            short = (msg or "").splitlines()[0][:80] if msg else ""
            if ok:
                self.debug = f"Save · {title}" + (f" — {short}" if short else "")
            else:
                self.debug = f"Save failed · {short or 'see command output'}"
                draft_path = _steer_draft_path(str(task_id)) if task_id else None
                self.app.push_screen(
                    SubmitErrorScreen("Save", msg or short, draft_path)
                )

        self._run_bg("Save", _work, _done, quiet=True)
    def _run_pivot_form_draft_clear(self, reg, ctx, title):
        """Clear a task's durable ``card_draft`` on the coordinator
        (``agent-dispatch card draft clear``) -- the Steer form's **Reset**.
        Never touches ``awaiting_steer``/status. Best-effort: Reset already
        cleared the local draft and every on-screen field synchronously, so a
        failure here only means the coordinator keeps a now-superseded draft
        around -- worth surfacing, but not worth blocking on."""
        from . import pivots as _pivots

        rt = self._pivot_runtime(reg)
        argv = _pivots.format_form_template(
            ["agent-dispatch", "card", "draft", "clear", "{task_id}"], ctx, {}
        )

        def _work():
            try:
                return rt.run_resolved(argv)
            except Exception as exc:
                return False, f"{type(exc).__name__}: {exc}"

        def _done(result):
            ok, msg = result
            short = (msg or "").splitlines()[0][:80] if msg else ""
            self.debug = (
                f"Reset · {title}" if ok
                else f"Reset: coordinator draft not cleared · {short or 'see command output'}"
            )

        self._run_bg("Reset", _work, _done, quiet=True)
    def _scope_label(self):
        return "All sources" if self.is_all() else self._current_tab()["label"]
    def _impact_rows(self, ids):
        """Display rows ``(id4, machine_env, title)`` for the worktrees in
        ``ids`` -- the read-only impact list the Clean/Sync modal shows so the
        operator sees exactly what Confirm will act on."""
        return [(w["id4"], w.get("machine_env", ""), w.get("title", ""))
                for w in self.data if self._row_key(w) in ids]
    def _open_cleanup(self, ids=None):
        # With no explicit selection (the bulk "Clean" button), default the
        # checkboxes to the full *safe* cleanable set -- Merged & finalized +
        # Unused + Conversation-only -- so one Confirm sweeps every worktree the
        # safety model already deems prunable. Unsafe work never reaches a
        # cleanable bucket (dirty / ahead / follow-up / active are classified
        # UNSAFE and are not offered here), and the reap re-checks each worktree,
        # so the broader default stays behind the beyond-clean confirm gate. An
        # explicit selection stays conservative -- only the already-merged bucket
        # is pre-checked -- since the operator already hand-picked the scope.
        bulk = ids is None
        scope = self._scope_label() if bulk else f"{len(ids)} selected"
        rows = self.cleanup_rows()
        rows = [w for w in rows if self._record_supports(w, "cleanup")]
        if not bulk:
            rows = [w for w in rows if self._row_key(w) in ids]
        clean = {
            self._row_key(w) for w in rows if w["cleanup_bucket"] == "clean"
        }
        unused = {
            self._row_key(w) for w in rows if w["cleanup_bucket"] == "unused"
        }
        convo = {
            self._row_key(w)
            for w in rows
            if w["cleanup_bucket"] == "conversation"
        }
        gone = {
            self._row_key(w) for w in rows if w["cleanup_bucket"] == "gone"
        }
        all_eligible = clean | unused | convo | gone
        dlg = {
            "verb": "Clean up", "prompt": "Select what to prune:",
            "confirm": "Confirm", "scope": scope, "section": 0, "bidx": 0,
            "opts": [
                {"label": "Merged & finalized", "on": True, "ids": clean,
                 "hint": f"work is on the default branch · {len(clean)}"},
                {"label": "Unused", "on": bulk, "ids": unused,
                 "hint": f"no commits, no conversation · {len(unused)}"},
                {"label": "Conversation-only", "on": bulk, "ids": convo,
                 "hint": f"no commits, but the session has chat history "
                         f"· {len(convo)}"},
                {"label": "All eligible", "on": False, "ids": all_eligible,
                 "hint": f"every prunable worktree · {len(all_eligible)}"},
            ],
        }

        def _after(confirmed):
            if confirmed:
                self._confirm_cleanup(dlg)
        self.app.push_screen(ScopeDlgScreen(dlg, self._impact_rows), _after)
    def _open_sync(self, ids=None):
        scope = self._scope_label() if ids is None else f"{len(ids)} selected"
        if ids is not None:
            rows = [w for w in self.data if self._row_key(w) in ids]
        else:
            rows = self._scope_data()
        rows = [w for w in rows if self._record_supports(w, "sync")]
        eligible = {self._row_key(w) for w in rows if w.get("ff_eligible")}
        skipped = len(rows) - len(eligible)
        dlg = {
            "verb": "Sync", "prompt": "Fast-forward worktrees onto the default "
            "branch (FF-only):",
            "confirm": "Confirm", "scope": scope, "section": 0, "bidx": 0,
            "opts": [
                {"label": "Eligible", "on": True, "ids": eligible,
                 "hint": f"clean · behind · no local commits · {len(eligible)}"
                         + (f"  ({skipped} skipped: ahead/dirty/active)"
                            if skipped else "")},
            ],
        }

        def _after(confirmed):
            if confirmed:
                self._confirm_cleanup(dlg)
        self.app.push_screen(ScopeDlgScreen(dlg, self._impact_rows), _after)
