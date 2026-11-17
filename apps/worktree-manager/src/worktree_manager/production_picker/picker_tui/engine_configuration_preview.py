"""One ephemeral, context-bound Configuration form/preview/consent interaction."""

from copy import deepcopy

from worktree_manager.configuration_preview import (
    configuration_target,
    parse_configuration_preview,
)

from .. import context as picker_context
from .configuration_preview_screen import ConfigurationPreviewScreen
from .pivot_actions import format_form_template
from .pivot_manifest import resolve_path


def _provider_row(row):
    # The renderer adds these two cross-link labels to cached rows in place.
    # They are not provider configuration and may arrive after the first paint.
    return {key: value for key, value in row.items()
            if key not in ("_worktree_short", "worktree_title")}


class ConfigurationPreviewFlow:
    def __init__(self, engine, reg, action, row, ctx, fields, summary):
        self.engine, self.reg, self.action = engine, reg, action
        self.row, self.ctx = deepcopy(row), deepcopy(ctx)
        self.fields, self.summary = deepcopy(fields), summary
        self.ctx["configuration_preview"] = ""
        self.origin = (engine._pivot_descriptor_key(), engine._pivot_scope_key(), picker_context.project())
        self.runtime = engine._pivot_runtime(reg)
        self.generation = object()
        engine._configuration_preview_generation = self.generation

    def current(self):
        engine = self.engine
        try:
            project = picker_context.project()
        except RuntimeError:
            return False
        return (
            getattr(engine, "_configuration_preview_generation", None) is self.generation
            and self.origin == (
                engine._pivot_descriptor_key(), engine._pivot_scope_key(), project,
            )
            and engine._reg_pivot() == self.reg
            and self.action in engine._reg_pivot().actions
            and any(_provider_row(row) == _provider_row(self.row) for row in engine._task_rows())
        )

    def edit(self, values=None):
        fields = deepcopy(self.fields)
        if values is not None:
            for field in fields:
                if field["name"] in values:
                    field["default"] = values[field["name"]]
        self.engine._open_configuration_editor(
            self.action.label, fields, confirm=False, summary=self.summary,
            submit_label="Preview", submitted=self.preview,
        )

    def preview(self, values):
        values = deepcopy(values)
        valid_at_start = self.current()
        screen = ConfigurationPreviewScreen(self.action.label, values)
        attempt = object()
        self.attempt = attempt

        def work():
            if not valid_at_start:
                return None, "Configuration context changed; cancel and reopen the row."
            if self.action.preview_error:
                return None, self.action.preview_error
            try:
                target = configuration_target(resolve_path(self.row, "configuration.target"))
            except ValueError as exc:
                return None, str(exc)
            ok, output = self.runtime.preview_resolved(
                format_form_template(self.action.preview, self.ctx, values),
            )
            if not ok:
                return None, output
            try:
                return parse_configuration_preview(output, target), ""
            except ValueError as exc:
                return None, str(exc)

        def done(result):
            if not screen.is_mounted or self.attempt is not attempt:
                return
            if not self.current():
                screen.complete(None, "Configuration context changed; cancel and reopen the row.")
            else:
                preview, error = result or (None, "Provider preview failed")
                screen.complete(preview, error)

        def after(choice):
            self.attempt = None
            if choice is None:
                return
            if not self.current():
                self.engine.debug = "Configuration context changed; reopen the row for a fresh preview."
                self.engine.refresh()
                return
            if choice == "edit":
                self.edit(values)
            elif choice == "retry":
                self.preview(values)
            elif choice == "apply" and screen.preview is not None and not screen.preview.blocked:
                context = {**self.ctx, "configuration_preview": screen.preview.token}
                self.engine._submit_configuration_form(self.reg, self.action, context, values)

        self.engine.app.push_screen(screen, after)
        self.engine.call_after_refresh(
            lambda: self.engine._run_bg(self.action.label + " preview", work, done, quiet=True)
        )
