"""Actions-owned prompt eligibility and asynchronous history lifetime."""
from __future__ import annotations

from .. import context
from ...launch_confirmation import confirm_before_effect
from ...seed_history import HistoryCursor, SeedHistory
from .seed_prompt_field import compose_seed_prompt


class ActionsPromptMixin:
    def _init_prompt(self):
        self._prompt_widgets, self._prompt_rec = compose_seed_prompt(history_on_mount=False)
        self._history_requested = False
        self.launch_decision = None
        self._prompt_refusal = None

    def _show_prompt(self):
        return self._can_prompt() or bool(self._prompt_rec["primary"].text.strip())

    def _load_prompt_history(self):
        if (
            self._loading or not self._can_prompt()
            or not (self._rec.get("raw") or {}).get("id")
            or self._history_requested or self._engine is None
        ):
            return
        self._history_requested = True
        try:
            project = context.project()
        except RuntimeError:
            return
        engine = self._engine
        bootstrap = context.project_bootstrap()
        source, setup = engine.src, getattr(engine, "_setup_epoch", None)
        epoch = getattr(engine, "_wt_submenu_epoch", None)

        def work():
            store = SeedHistory(project)
            return store, HistoryCursor(store.entries())

        def done(result):
            if (
                not self.is_mounted or engine.app.screen is not self
                or engine.src is not source or getattr(engine, "_setup_epoch", None) != setup
                or getattr(engine, "_wt_submenu_epoch", None) != epoch
                or context.project_bootstrap() is not bootstrap
                or not self._can_prompt()
            ):
                return
            try:
                if context.project() != project:
                    return
            except RuntimeError:
                return
            if result is None:
                self.notify("Resume history could not be read; the draft is unchanged.", severity="warning")
                return
            field = self._prompt_rec["primary"]
            field.seed_store, field.seed_cursor = result

        engine._run_bg("Resume history", work, done, quiet=True)

    def _accepted_prompt(self, action):
        self._prompt_refusal = None
        field = self._prompt_rec["primary"]
        seed = field.text.strip()
        if seed and action == "Resume" and not (self._rec.get("raw") or {}).get("id"):
            self.notify("Resume prompt requires a worktree id; nothing launched.", severity="error")
            field.focus()
            return None
        if seed and action in {"Open", "Resume", "Launch in new window"} and (
            action != "Resume" or not self._can_prompt()
        ):
            self.query_one("#sub-prompt").display = True
            self.notify(
                "This draft requires a local cold Resume. Clear it or refresh eligibility; nothing launched.",
                severity="error",
            )
            field.focus()
            return None
        if action != "Resume" or not self._can_prompt():
            return ""
        leg = self._rec.get("execution_leg") or (self._rec.get("raw") or {}).get("execution_leg")
        refusal = None
        if seed and self._ahp:
            refusal = "Prompt is unsupported with AHP; choose a native cold resume."
        elif seed and isinstance(leg, dict) and leg.get("state", "unknown") in {"active", "unknown"}:
            refusal = f"Prompt requires a cold resume; execution leg is {leg.get('state', 'unknown')}."
        refusal = field.refusal(refusal_reason=refusal)
        if refusal:
            if self._engine is not None and getattr(self._engine, "real_ops", False):
                self._prompt_refusal = refusal
                return seed
            self.notify(refusal, severity="error")
            field.focus()
            return None
        return seed

    def _confirm_prompt_resume(self, seed):
        engine = self._engine
        if engine is None or not getattr(engine, "real_ops", False):
            return False
        field = self._prompt_rec["primary"]
        decision = engine._resume_decision(
            self._rec, no_mux=self._no_mux, ahp=self._ahp, seed_prompt=seed,
        )

        def saved(result):
            self.launch_decision = result
            self.dismiss(("Resume", self._no_mux, self._ahp, seed))

        confirm_before_effect(
            self, context.project(), decision, surface="worktree-actions", saved=saved,
            prompt=str(field.text), history=field.seed_store or SeedHistory(context.project()),
            refusal=self._prompt_refusal or ((
                decision.get("reason") or "Locality is not ready; refresh and retry."
            ) if decision.get("deferred") else None),
        )
        return True
