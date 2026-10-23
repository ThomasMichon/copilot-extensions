"""Native, provider-neutral original-request retention and recovery."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import json
import uuid

from rich.text import Text

from worktree_manager.form_requests import (
    observe_request, prepared_record, recovery_records, recovery_text,
    request_contract, resolve_request, retain_request,
)
from worktree_manager.form_draft_storage import write_record
from .engine_dialogs import TaskMenuScreen


class RetainedResponsesMenu(TaskMenuScreen):
    CSS = """
    RetainedResponsesMenu #task-frame { max-height: 90%; }
    RetainedResponsesMenu OptionList { max-height: 12; overflow-y: auto; }
    RetainedResponsesMenu #task-desc { max-height: 4; overflow-y: auto; }
    """


def current_request_registrations():
    from . import pivots

    report = pivots.scan_pivot_registry(materialize=False, previous={})
    return [
        contribution.pivot for contribution in report.contributions
        if contribution.owner and contribution.pivot is not None
    ]


def current_request_action(contract):
    matches = [
        (reg, action) for reg in current_request_registrations()
        for action in (*reg.actions, *reg.view_actions)
        if action.form and action.form.get("request")
        and request_contract(reg, action) == contract
    ]
    if len(matches) != 1:
        raise ValueError("Original attributed provider/action unavailable or changed; recovery is read-only")
    return matches[0]


def run_retained_request(path, record, *, receipt=False):
    from . import pivots, tasks

    reg, action = resolve_request(record, current_request_registrations())
    if not receipt:
        write_record(path, record)
    ctx = {**record["context"], "prepared_request": record["prepared_request"]}
    template = action.form["request"]["receipt"] if receipt else action.run
    argv = pivots.format_form_template(template, ctx, record["values"])
    ok, output = tasks.RegisteredPivotRuntime(reg).run_resolved(argv, preserve_output=True)
    try:
        observe_request(
            path, operation="receipt" if receipt else "submit", ok=ok, output=output,
        )
    except OSError as exc:
        return False, (
            f"{output}\n\nCommand completed; local observation could not be retained: {exc}."
            "\nThe original request remains retained. Check the owner's receipt."
        )
    return ok, output


class PivotFormRequestActionsMixin:
    def _prepare_pivot_form_submit(
        self, reg, action, ctx, title, values, *, draft_binding=None,
        draft_path=None, draft_cleanup=None,
    ):
        from . import pivots, tasks
        from .steering import SubmitErrorScreen, _steer_drafts_dir

        contract = request_contract(reg, action)
        ctx, values = deepcopy(ctx), deepcopy(values)
        ctx["request_id"] = uuid.uuid4().hex

        def work():
            try:
                current_reg, current_action = current_request_action(contract)
                spec = current_action.form["request"]
                argv = pivots.format_form_template(spec["prepare"], ctx, values)
                ok, output = tasks.RegisteredPivotRuntime(current_reg).run_json_resolved(argv)
                if not ok:
                    return False, output, None
                record = prepared_record(
                    current_reg, current_action, ctx, values, draft_binding, json.loads(output),
                )
                path = retain_request(_steer_drafts_dir(), record)
                return True, "", (path, record)
            except Exception as exc:
                return False, f"{type(exc).__name__}: {exc}", None

        def done(result):
            ok, output, retained = result
            if not ok:
                self.debug = "Preparation failed; no submit invoked"
                self.app.push_screen(SubmitErrorScreen(
                    action.label, output + "\nNo submit invoked; local response retained.", draft_path,
                ))
                return
            self._run_pivot_form_submit(
                reg, action, ctx, title, values, draft_binding=draft_binding,
                draft_path=draft_path, draft_cleanup=draft_cleanup,
                retained_request=retained,
            )

        self._run_bg("Preparing original request", work, done, quiet=True)

    def action_recover_forms(self):
        from .steering import _steer_drafts_dir

        def done(records):
            if not records:
                self.debug = "No retained form responses or original requests"
                return
            menu = SimpleNamespace(
                label="Retained form responses", title_field="title", id_field="id",
                worktree_field=None, subtitle_field=None,
            )
            choices = [
                SimpleNamespace(
                    label=Text(f"{record.get('task_id') or path.stem} - {diagnostic or 'Original request'}"),
                    description=Text(f"{diagnostic or 'Inspect, check receipt or retry the identical request'}\n{path}"),
                )
                for path, record, diagnostic in records
            ]

            def selected(index):
                if index is not None:
                    self._open_form_recovery(*records[index])

            self.app.push_screen(RetainedResponsesMenu(
                menu, {"title": "Original requests and previous values"}, choices,
            ), selected)

        self._run_bg(
            "Reading retained responses", lambda: recovery_records(_steer_drafts_dir()),
            done, quiet=True,
        )

    def _open_form_recovery(self, path, record, diagnostic):
        def work():
            if diagnostic:
                return diagnostic
            try:
                resolve_request(record, current_request_registrations())
                return ""
            except Exception as exc:
                return f"{type(exc).__name__}: {exc}"

        self._run_bg(
            "Resolving original provider", work,
            lambda detail: self._show_form_recovery(path, record, detail), quiet=True,
        )

    def _show_form_recovery(self, path, record, diagnostic):
        menu = SimpleNamespace(
            label="Original request recovery", title_field="title", id_field="id",
            worktree_field=None, subtitle_field="diagnostic",
        )
        choices = [SimpleNamespace(
            label="Inspect retained values and command observations",
            description=Text(diagnostic or "Read-only local data; not proof of owner acceptance."),
        )]
        if not diagnostic:
            choices.extend([
                SimpleNamespace(
                    label="Check original receipt",
                    description="Explicit read-only owner query. A missing/failed read retains uncertainty.",
                ),
                SimpleNamespace(
                    label="Retry identical original request",
                    description="Requires confirmation; retains original identity, values, scope and consent.",
                ),
            ])

        def selected(index):
            if index == 0:
                from .configuration_form import CommandResultScreen
                screen = CommandResultScreen(
                    "Retained form response", context=str(path), return_label="Return to Manager",
                )
                self.app.push_screen(screen)

                def work():
                    try:
                        return True, recovery_text(path, record, diagnostic)
                    except Exception as exc:
                        return False, f"{type(exc).__name__}: {exc}\nOriginal request retained at: {path}"

                self.call_after_refresh(lambda: self._run_bg(
                    "Reading original request", work,
                    lambda result: screen.complete(*result), quiet=True,
                ))
            elif index == 1:
                self._run_form_recovery(path, record, receipt=True)
            elif index == 2:
                from .engine_action_confirm import ActionConfirmScreen
                self.app.push_screen(
                    ActionConfirmScreen(
                        f"identical original request for {record.get('task_id')!r}; "
                        "do not send a new answer or bind to the current card"
                    ),
                    lambda confirmed: self._run_form_recovery(path, record) if confirmed else None,
                )

        self.app.push_screen(RetainedResponsesMenu(
            menu, {"title": str(record.get("task_id") or path.stem), "diagnostic": diagnostic}, choices,
        ), selected)

    def _run_form_recovery(self, path, record, *, receipt=False):
        from .configuration_form import CommandResultScreen

        label = "Check original receipt" if receipt else "Retry identical original request"
        screen = CommandResultScreen(label, context=str(path), return_label="Return to Manager")
        self.app.push_screen(screen)

        def work():
            try:
                reg = None if receipt else resolve_request(record, current_request_registrations())[0]
                ok, output = run_retained_request(path, record, receipt=receipt)
                return ok, output, reg
            except Exception as exc:
                return False, f"{type(exc).__name__}: {exc}", None

        def done(result):
            ok, output, reg = result
            if reg is not None:
                try:
                    runtime = self._pivot_runtime(reg)
                    runtime.invalidate()
                    scope = "" if reg.account_scoped else str(record["context"].get("machine") or "")
                    runtime.ensure(scope)
                    self.refresh()
                except Exception as exc:
                    output += f"\nTask display refresh failed: {type(exc).__name__}: {exc}"
            screen.complete(ok, (
                f"{output}\n\nOriginal request retained at: {path}\n"
                "Command completion is not proof of acceptance, original-wake delivery or consumption."
            ))
            self.debug = label + (" complete" if ok else " failed; original uncertainty retained")

        self.call_after_refresh(lambda: self._run_bg(label, work, done, quiet=True))
