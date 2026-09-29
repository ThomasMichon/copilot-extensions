#!/usr/bin/env python3
"""copilot --headed (#copilot-headed): open a brand-new, VISIBLE terminal
window attached to a worktree's session, WITHOUT exiting the Picker.

A standalone module (rather than a method on
``PickerScreenWorktreeActionsMixin`` in ``engine_worktree_actions.py``)
purely to keep that already-large mixin file under this repo's flat
1000-line module-size cap. No functional reason to split otherwise --
``open_worktree_cli_headed`` takes the ``PickerScreen`` instance explicitly
instead of being a method on it.
"""
from __future__ import annotations


def open_worktree_cli_headed(screen, rec) -> None:
    """Only offered (see ``_session_action_verbs``) for a local,
    mux-live-or-resumable row, so no remote/SSH branch is needed here. Runs
    like ``_embody_task_cli``: a background subprocess reporting through
    ``screen.debug`` rather than raising through action dispatch."""
    from ... import engine_client
    from .. import context

    wt_id = (rec.get("raw") or {}).get("id")
    if not wt_id:
        screen.debug = "Launch in new window failed · no worktree id"
        return

    def _work():
        try:
            return True, engine_client.run_json(
                context.project(),
                ["copilot", "--worktree-id", wt_id, "--headed", "--json"],
                timeout=30,
            )
        except engine_client.EngineError as exc:
            return False, str(exc)

    def _done(result):
        ok, payload = result
        if not ok:
            screen.debug = f"Launch in new window failed · {payload}"
            return
        spawner = payload.get("spawner") if isinstance(payload, dict) else None
        screen.debug = (
            f"Opened in a new window ({spawner})" if spawner
            else "Opened in a new window"
        )

    screen._run_bg("Launch in new window", _work, _done)
