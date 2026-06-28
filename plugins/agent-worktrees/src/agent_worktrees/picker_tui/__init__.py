"""Worktree Picker TUI (Textual) -- the overhauled multi-machine picker.

Ported from the aperture-labs ``worktree-picker-tty-overhaul`` prototype. The
``engine`` renders over any *source* exposing ``LOCAL`` / ``LOCAL_LABEL`` /
``machines()`` / ``load()`` / ``bucket`` / ``for_machine`` (and ``make_loader``
for live multi-machine). ``data_local`` is the real local source.

Rollout is gated by env:
- ``AGENT_WORKTREES_NEW_PICKER=1`` opts INTO the TUI (during the port).
- ``AGENT_WORKTREES_LEGACY_PICKER=1`` will force the legacy ANSI picker once the
  TUI becomes the default (final slice).
"""
from __future__ import annotations

import os


def new_picker_enabled() -> bool:
    """True when the TUI picker should be used instead of the legacy ANSI one.

    During the port the TUI is opt-in (``AGENT_WORKTREES_NEW_PICKER=1``). The
    legacy override (``AGENT_WORKTREES_LEGACY_PICKER=1``) always wins, so it
    keeps working as the rollback switch after the default flips.
    """
    if os.environ.get("AGENT_WORKTREES_LEGACY_PICKER"):
        return False
    return bool(os.environ.get("AGENT_WORKTREES_NEW_PICKER"))


def run_tui_picker(source=None, live=False):
    """Run the TUI picker and return its result (a launch decision or None).

    With no source: ``live=True`` selects the multi-machine SSH source
    (``data_ssh``, async per-machine loader); otherwise the local-only source
    (``data_local``). Returns ``app.result``, which the caller maps onto a
    resume/create action (wired in a later slice).
    """
    from .engine import PickerApp

    if source is None:
        if live:
            from . import data_ssh
            source = data_ssh
        else:
            from . import data_local
            source = data_local
    app = PickerApp(source, live=live)
    app.run()
    return app.result
