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
    """"Resume prompt…" (resume-prompt-durable-seed-and-mux-fix Phase 2): a
    lean prompt-composer dialog (no options checklist -- Resume has no use
    for Anchor/Bare/No Mux the way "New worktree…" does, those are separate
    submenu toggles here already), modeled on that dialog's own folded-in
    ``show_prompt`` field. On Confirm, decides the SAME ordinary resume
    ``_resume_decision`` builds for "Resume"/"Open", carrying the collected
    text as ``options["seed_prompt"]`` -- the engine (``resolve
    --worktree-id --seed``) delivers it durably on either a fresh launch or
    a live-mux reattach. Per the operator's own explicit scope-down, this
    stays a SEPARATE affordance from the read-only "Messages"
    (recent-messages) viewer, never folded into one screen."""
    title = rec.get("title") or rec.get("id4") or "this worktree"
    scr = PromptDlgScreen(
        f"Resume prompt · {title}",
        "Prompt (optional, queued as this session's next interactive "
        "turn once Copilot is ready):",
    )

    def _after(confirmed):
        if not confirmed:
            return
        decision = screen._resume_decision(
            rec, no_mux=no_mux, ahp=ahp, seed_prompt=scr.seed_prompt)
        screen._decide(decision)
    screen.app.push_screen(scr, _after)
