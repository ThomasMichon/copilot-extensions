#!/usr/bin/env python3
'''"Launch in new window": open a brand-new, VISIBLE terminal window running
a worktree's launch, WITHOUT exiting the Picker.

A standalone module (rather than a method on
``PickerScreenWorktreeActionsMixin`` in ``engine_worktree_actions.py``)
purely to keep that already-large mixin file under this repo's flat
1000-line module-size cap. No functional reason to split otherwise --
``open_worktree_cli_headed`` takes the ``PickerScreen`` instance explicitly
instead of being a method on it.

Phase 9 (#5210): this used to shell out to the now-retired
`agent-worktrees copilot --headed`, which created/resumed the session
in-process and popped the new window itself -- a parallel code path that
never ran `launch-session.{ps1,sh}`, so Worktree Manager's own mux-daemon
registration never happened for a session opened this way. This now calls
`_run_launch` directly (in-process, same module as every other launch
decision) with `LaunchRequest.new_window=True`, so "Launch in new window"
is a launch-plan MODIFIER composed with the row's existing Open/Resume
decision, not a separate verb -- the identical `launch-session.{ps1,sh}`
plan execution (and its registration call) runs either way.
'''
from __future__ import annotations

import contextlib
import io


def open_worktree_cli_headed(screen, rec) -> None:
    """Only offered (see ``_session_action_verbs``) for a local,
    mux-live-or-resumable row, so no remote/SSH branch is needed here.
    Reuses the row's ordinary resume decision (the same one Open/Resume
    would dispatch), just adding ``new_window=True`` and running it directly
    instead of exiting the Picker through ``_decide``. Runs on a background
    thread (``screen._run_bg``) since opening the new window still shells
    out; reports success/failure through ``screen.debug`` rather than
    raising through action dispatch.

    ``_run_launch`` and the functions it calls are CLI-shaped: they
    ``print()`` their own error messages rather than returning them. Calling
    it in-process, on a background thread, WHILE the Textual TUI still owns
    the terminal (unlike every other ``_run_launch`` call site, which only
    ever runs after ``app.exit()`` has torn the TUI down) means a stray
    ``print()`` would corrupt the live render instead of being invisible --
    so this captures stdout for the duration of the call and surfaces it
    through ``screen.debug`` instead of letting it reach the real terminal.
    """
    from ... import __main__ as manager_main
    from .. import context
    from ...picker_app import LaunchRequest

    wt_id = (rec.get("raw") or {}).get("id")
    if not wt_id:
        screen.debug = "Launch in new window failed · no worktree id"
        return

    decision = screen._resume_decision(rec)
    opts = dict(decision.get("options") or {})
    project = context.project()

    def _work():
        request = LaunchRequest(
            project=project,
            worktree_id=str(wt_id),
            mode="bare-resume" if opts.get("bare_resume") else "resume",
            title=str(decision.get("title") or "") or None,
            no_mux=bool(opts.get("no_mux")),
            ahp=bool(opts.get("ahp")),
            new_window=True,
        )
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = manager_main._run_launch(request)
        except Exception as exc:  # pragma: no cover -- defensive; surfaced via screen.debug
            return False, str(exc)
        return rc == 0, buf.getvalue().strip() or None

    def _done(result):
        ok, detail = result
        screen.debug = (
            "Opened in a new window" if ok
            else f"Launch in new window failed · {detail or 'unknown error'}"
        )

    screen._run_bg("Launch in new window", _work, _done)
