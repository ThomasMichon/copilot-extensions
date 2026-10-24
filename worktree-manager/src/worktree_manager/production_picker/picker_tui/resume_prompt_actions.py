#!/usr/bin/env python3
"""``open_resume_prompt``: the "Resume prompt…" Actions-menu verb.

A standalone module (rather than a method on
``PickerScreenWorktreeActionsMixin`` in ``engine_worktree_actions.py``)
purely to keep that already-large mixin file under this repo's flat
1000-line module-size cap -- the same reason ``headed_actions.py`` splits
out "Launch in new window". No functional reason to split otherwise --
``open_resume_prompt`` takes the ``PickerScreen`` instance explicitly
instead of being a method on it.
"""
from __future__ import annotations

from .engine_prompt_dialog import PromptDlgScreen

def open_resume_prompt(screen, rec, *, no_mux=False, ahp=False) -> None:
    """Compose a retry-staged prompt for a stopped worktree's resume."""
    if not rec.get("is_local", True):
        screen.debug = "Resume prompt requires a local native target; use ordinary Resume."
        return

    def _eligible():
        wt_id = (rec.get("raw") or {}).get("id")
        current, _error = screen._find_internal_worktree(wt_id, rec.get("source_id"))
        if current is None or "Resume prompt…" not in screen._session_action_verbs(current):
            screen.debug = "Resume prompt is cold-start-only; refresh and use Open for a live session."
            return False
        return True

    if not _eligible():
        return
    decision = screen._resume_decision(rec, no_mux=no_mux, ahp=ahp)
    if decision.get("deferred"):
        screen._decide(decision)
        return
    leg = rec.get("execution_leg") or (rec.get("raw") or {}).get("execution_leg")
    refusal: str | None = None
    if ahp:
        refusal = "Prompt launch is unsupported with AHP; choose a native cold resume."
    elif isinstance(leg, dict) and leg.get("state", "unknown") in {"active", "unknown"}:
        refusal = (
            f"Prompt launch is unsupported while the execution leg is {leg.get('state', 'unknown')}; "
            "choose a native cold resume."
        )
    title = rec.get("title") or rec.get("id4") or "this worktree"
    scr = PromptDlgScreen(
        f"Resume prompt · {title}",
        "Prompt (optional; saved for retry until Copilot takes this cold resume):",
        seed_refusal=refusal,
        launch_decision=decision,
        journal_enabled=getattr(screen, "real_ops", True),
    )

    def _after(confirmed):
        if not confirmed:
            return
        # Preserve an accepted intent even if the row became live while the
        # composer was open. The engine stages it, then refuses a live launch.
        screen._decide(scr.launch_decision)
    screen.app.push_screen(scr, _after)
