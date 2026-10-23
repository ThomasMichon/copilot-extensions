"""Provider-declared form draft operations; no domain backend in the Manager."""

from worktree_manager.form_drafts import draft_ack, read_path, update_draft_context

from .steering import SubmitErrorScreen


class PivotFormDraftActionsMixin:
    def _run_pivot_form_draft(self, reg, action, ctx, title, values, screen, *, clear=False):
        from . import pivots

        spec = action.form["draft"]
        rt = self._pivot_runtime(reg)
        scope = self._pivot_scope_key()
        label = "Reset" if clear else "Save"
        argv = pivots.format_form_template(spec["clear" if clear else "save"], ctx, values)

        def work():
            try:
                ok, message = rt.run_json_resolved(argv)
                if not ok:
                    return False, message, None
                result = draft_ack(spec, ctx, message, clear=clear, expected_values=values)
                return True, message, result
            except Exception as exc:
                return False, f"{type(exc).__name__}: {exc}", None
            finally:
                rt.invalidate()

        def done(outcome):
            ok, message, result = outcome
            if ok:
                cleaned = True
                if clear:
                    update_draft_context(spec, ctx, result)
                    screen.draft_reset_complete(True, read_path(result, spec["revision_from"]))
                else:
                    cleaned = screen._delete_draft()
                rt.ensure(scope)
                self.refresh()
                self.debug = (
                    f"{label} · {title}" if cleaned
                    else "Shared Save acknowledged; local recovery cleanup failed"
                )
            else:
                if clear:
                    screen.draft_reset_complete(False)
                self.debug = f"{label} failed · response retained"
                self.app.push_screen(SubmitErrorScreen(label, message, screen._draft_path()))

        self._run_bg(label, work, done, quiet=True)
