#!/usr/bin/env python3
"""PickerScreen mixin extracted from ``engine.py``."""
from __future__ import annotations

import logging

from .engine_action_confirm import confirm_then
from .engine_dialogs import ScopeDlgScreen, TaskMenuScreen
from .create_action_screen import CreateActionScreen
from .background import run_background
from .inbox import ensure_inbox
from .engine_form_drafts import PivotFormDraftActionsMixin
from .registered_scopes import read_scope, request_scopes
from .steering import PivotCardScreen, PivotFormScreen, SubmitErrorScreen, _normalize_form_fields, _steer_draft_path

log = logging.getLogger("agent-worktrees.picker")

class PickerScreenPivotActionsMixin(PivotFormDraftActionsMixin):
    def _run_view_action(self, action):
        from worktree_manager.pivot_progress import PivotProgressScreen

        reg = self._reg_pivot()
        if reg is None or action not in self._pivot_view_actions():
            return
        rt = self._pivot_runtime(reg)
        scopes = request_scopes(self, reg)
        ctx = dict(self._task_summary())
        ctx["machine"] = scopes[0] if len(scopes) == 1 else ""
        ctx["_manager_refresh_scopes"] = scopes

        def completed():
            for scope in scopes:
                rt.invalidate(scope)
                rt.ensure(scope)
            self.refresh()

        def run():
            if action.internal or action.form or action.card or not action.progress:
                self._run_task_action(reg, action, ctx)
            else:
                self.app.push_screen(PivotProgressScreen(
                    rt, action, ctx, completed=completed,
                ))

        confirm_then(self.app, action, run)

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
        return items
    def _open_cfgmenu(self):
        """Enter Configuration before scheduling any provider read."""
        indices = self._config_pivots()
        if not indices:
            return
        if not self._in_configuration():
            self._config_return = (self.htab, self.sel, self.top, self.btn_idx)
            self._config_return_key = self._pivot_descriptor_key()
        target = next(
            (index for index in indices if self._pivot_descriptor_key(index)
             == getattr(self, "_last_config_key", None)), indices[0],
        )
        self.htab = target if target in indices else indices[0]
        self.sel = ("M", 0)
        self.btn_idx = self.top = 0
        self.refresh()
        self.call_after_refresh(self._load_current_config_section)
    def _config_section_ctx(self) -> dict:
        """Placeholder context for a contributed config section's argv template:
        the local machine identity and the picker's repo (config sections are
        global -- there is no per-worktree context)."""
        return {
            "machine": self._source_local[0] or "",
            "repo": getattr(self.src, "REPO", "") or "",
        }
    def _run_config_section(self, section) -> None:
        """Run a contributed Configuration section (subprocess), report the
        outcome, and rescan (it may have changed config/session state). The
        subprocess runs OFF the render flow via :meth:`_run_bg`."""
        from . import tasks

        label, source = section.label, section.source
        ctx = self._config_section_ctx()
        action = getattr(section, "configure_action", None)

        def _work():
            ok, msg = tasks.run_config_section(section, ctx)
            if action is None:
                return ok, msg
            fields = None
            if ok:
                try:
                    fields = tasks.resolve_form_fields(action.fields, strict=True)
                except ValueError as exc:
                    ok, msg = False, str(exc)
            return ok, msg, fields

        def _done(result):
            if action is None:
                ok, msg = result
                fields = None
            else:
                ok, msg, fields = result
            self.debug = (f"{label} ({source}): {msg or 'done'}" if ok
                          else f"{label} ({source}) failed: {msg}")
            if action is not None:
                if ok:
                    self._open_config_action(section, ctx, msg, fields)
                else:
                    self.app.push_screen(SubmitErrorScreen(label, msg, None))
                return
            try:
                self._start_setup_reload_worker()
                self.sel = self.default_sel()
            except Exception:
                pass

        self._run_bg(label, _work, _done)
    def _open_config_action(self, section, ctx, status, fields):
        action = section.configure_action

        def _after(values):
            if values is None:
                self.debug = f"{section.label} · cancelled"
                return
            self._run_config_action(section, ctx, values)

        summary = "\n\n".join(part for part in (section.description, status) if part)
        self._open_configuration_editor(
            action.label, fields, confirm=action.confirm,
            summary=summary, submitted=_after, submit_label="Apply",
        )
    def _run_config_action(self, section, ctx, values):
        from . import tasks

        def _work():
            return tasks.run_config_section(section.configure_action, ctx, fields=values)

        def _reload():
            key = (section.source, section.key)
            self._config_section_states.pop(key, None)
            if self._kind() == "config_section" and self._config_section_key() == key:
                self._load_current_config_section(force=True)

        self._run_configuration_command(section.label, _work, _reload)
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
            if choice is None:
                return
            action = actions[choice]
            if reg.placement == "config" and action.form is not None:
                self._run_task_action(reg, action, rec)
                return
            # ``confirm: true`` gates the run behind an explicit are-you-sure.
            confirm_then(self.app, action,
                         lambda action=action: self._run_task_action(reg, action, rec))
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
        ctx["machine"] = "" if reg.placement == "config" else (
            rec["_manager_scope"] if "_manager_scope" in rec
            else self._pivot_machine_id() or ""
        )
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
        if "_manager_scope" in rec and getattr(action, "card", None) is None:
            state = self._pivot_runtime(reg).get(rec["_manager_scope"])[0]
            if state != "ready":
                self.debug = "Source read is not current; press r to retry before running this action."
                return
        ctx = self._task_action_ctx(reg, rec)
        scopes = ctx.get("_manager_refresh_scopes", (read_scope(reg, ctx["machine"]),))
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
        if reg.placement == "config":
            self._submit_configuration_form(reg, action, ctx, {})
            return
        if getattr(action, "progress", False):
            self._run_task_action_progress(reg, action, ctx, title)
            return
        rt = self._pivot_runtime(reg)
        if getattr(action, "result_view", None) == "text":
            from . import pivots as _pivots
            from .configuration_form import CommandResultScreen
            argv = _pivots.format_template(action.run, ctx)

            def work():
                result = rt.run_resolved(argv, preserve_output=True)
                for scope in scopes:
                    rt.invalidate(scope)
                return result

            def refreshed():
                for scope in scopes:
                    rt.ensure(scope)

            self._run_command_result(
                action.label,
                work,
                refreshed,
                CommandResultScreen(
                    action.label, context=reg.label, return_label=f"Return to {reg.label}",
                ),
            )
            return

        def _work():
            ok, msg = rt.run_action(action, ctx)
            for scope in scopes:
                rt.invalidate(scope)
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
                    for scope in scopes:
                        rt.ensure(scope)
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
        scopes = ctx.get("_manager_refresh_scopes", (read_scope(reg, ctx["machine"]),))
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
            for scope in scopes:
                rt.invalidate(scope)
                rt.ensure(scope)
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
        if reg.placement == "config":
            from .configuration_form import ConfigurationDetailScreen
            self.app.push_screen(ConfigurationDetailScreen(str(row_title), card))
            return
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
        if reg.placement == "config":
            from .pivot_create_action import _parse_create_fields
            try:
                fields = list(_parse_create_fields(
                    raw_fields, where="configuration fields", dynamic_other=False,
                ))
            except ValueError as error:
                self.app.push_screen(SubmitErrorScreen(action.label, str(error), None))
                return
            summary = str(
                _pivots.resolve_path(rec, spec.get("body_from"))
                or _pivots.resolve_path(rec, "card.body") or action.description
            )
            self._configuration_preview_generation = object()
            if action.preview:
                from .engine_configuration_preview import ConfigurationPreviewFlow
                ConfigurationPreviewFlow(self, reg, action, rec, ctx, fields, summary).edit()
                return

            def after(values):
                if values is not None:
                    self._submit_configuration_form(reg, action, ctx, values)

            self._open_configuration_editor(
                action.label, fields, confirm=action.confirm,
                summary=summary, submitted=after,
            )
            return
        card = {
            "title": _pivots.resolve_path(rec, spec.get("title_from")) or title,
            "status": _pivots.resolve_path(rec, "card.status"),
            "link": _pivots.resolve_path(rec, "card.link"),
            "body": (_pivots.resolve_path(rec, spec.get("body_from"))
                     or _pivots.resolve_path(rec, "card.body")),
        }
        task_id = ctx.get("task_id") or rec.get(reg.id_field) or ""
        from worktree_manager.form_drafts import form_binding, shared_values
        try:
            binding = form_binding(reg, action, rec, ctx, fields, card)
        except ValueError as error:
            self.app.push_screen(SubmitErrorScreen(action.label, str(error), None))
            return
        from copy import deepcopy
        ctx = deepcopy(ctx)
        draft = spec.get("draft")

        def _after(result):
            if result is None:
                self.debug = f"{action.label} · cancelled (no submit)"
                return
            kind = result.get("action")
            values = result.get("values") or {}
            if kind == "confirm":
                self._run_pivot_form_submit(
                    reg, action, ctx, title, values, draft_binding=screen._draft_binding,
                    draft_path=screen._draft_path(), draft_cleanup=screen._delete_draft,
                )
            elif kind == "save" and draft:
                self._run_pivot_form_draft(reg, action, ctx, title, values, screen)

        def _on_clear_draft():
            self._run_pivot_form_draft(reg, action, ctx, title, {}, screen, clear=True)

        screen = PivotFormScreen(
            card, fields, action.label, task_id=str(task_id),
            on_clear_draft=_on_clear_draft if draft else None,
            draft_binding=binding, draft_values=shared_values(draft, rec, fields),
        )
        self.app.push_screen(screen, _after)
    def _run_pivot_form_submit(
        self, reg, action, ctx, title, values, *, draft_binding=None,
        draft_path=None, draft_cleanup=None, retained_request=None,
    ):
        """Run a submitted form off the render flow with single-pass values.

        Declared text results retain full provider output in the shared command
        screen; other forms retain their progress/error overlays. Both show
        explicit completion and preserve the existing draft on command failure.
        Refresh the task snapshot after the round-trip, not after worker startup.
        """
        from . import pivots as _pivots

        if (action.form or {}).get("request") and retained_request is None:
            self._prepare_pivot_form_submit(
                reg, action, ctx, title, values, draft_binding=draft_binding,
                draft_path=draft_path, draft_cleanup=draft_cleanup,
            )
            return
        rt = self._pivot_runtime(reg)
        argv = _pivots.format_form_template(action.run, ctx, values)
        task_id = ctx.get("task_id")
        scope = read_scope(reg, str(ctx.get("machine") or ""))
        text_result = retained_request is not None or getattr(action, "result_view", None) == "text"
        result_screen = None

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
        if text_result:
            from .configuration_form import CommandResultScreen
            result_screen = CommandResultScreen(
                action.label, context=reg.label, return_label=f"Return to {reg.label}",
            )
            self.app.push_screen(result_screen)
        else:
            self.progress = prog
            self._open_progress()

        def _work():
            try:
                if retained_request is not None:
                    from .engine_form_requests import run_retained_request
                    ok, msg = run_retained_request(*retained_request)
                else:
                    ok, msg = (
                        rt.run_resolved(argv, preserve_output=True) if text_result
                        else rt.run_resolved(argv)
                    )
            except Exception as exc:  # never let a delivery attempt vanish silently
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            else:
                try:
                    rt.invalidate(scope)
                except Exception:
                    pass
            if retained_request is not None:
                msg = (msg or "") + f"\n\nOriginal request retained at: {retained_request[0]}"
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
                if draft_cleanup is not None:
                    draft_cleanup()
                elif task_id:
                    path = _steer_draft_path(str(task_id), draft_binding)
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
                    rt.ensure(scope)
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
                retained_path = draft_path or (_steer_draft_path(str(task_id), draft_binding) if task_id else None)
                if self.progress is prog:
                    self.progress = None
                    self.app.pop_screen()
                if result_screen is None:
                    self.app.push_screen(
                        SubmitErrorScreen(action.label, msg or short, retained_path)
                    )
                elif retained_path:
                    msg = (msg or "") + f"\n\nLocal draft retained at: {retained_path}"
            if result_screen is not None and result_screen.is_mounted:
                result_screen.complete(ok, msg)

        if text_result:
            self.call_after_refresh(lambda: self._run_bg(action.label, _work, _done, quiet=True))
        else:
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
            return tasks.resolve_form_fields(fields)

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
        scopes = request_scopes(self, reg)
        argv = _pivots.format_form_template(action.run, {}, values)

        def _work():
            try:
                ok, msg = rt.run_resolved(argv)
            except Exception as exc:  # never let a delivery attempt vanish silently
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            else:
                try:
                    for scope in scopes:
                        rt.invalidate(scope)
                except Exception:
                    pass
            return ok, msg

        def _done(result):
            ok, msg = result
            short = (msg or "").splitlines()[0][:80] if msg else ""
            if ok:
                try:
                    for scope in scopes:
                        rt.ensure(scope)
                except Exception:
                    pass
                self.refresh()
                self.debug = f"{action.label} · created" + (f" — {short}" if short else "")
            else:
                self.debug = f"{action.label} failed · {short or 'see command output'}"
                self.app.push_screen(SubmitErrorScreen(action.label, msg or short, None))

        self._run_bg(action.label, _work, _done)
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
