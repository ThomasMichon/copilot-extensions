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
    resume/create action.

    Launch-channel handling: this runs inside ``resolve``, whose **stdout
    (fd 1) is captured by the launcher for the JSON plan**. Textual's driver
    renders to ``sys.__stdout__`` -- so when stdout is captured (not a TTY) but
    stderr is the terminal, point ``sys.__stdout__`` at stderr for the duration
    of the TUI. The plan is emitted to the real fd 1 after the app exits (and
    ``sys.__stdout__`` is restored). Mirrors the legacy picker's
    ``stdout_to_stderr`` redirect.
    """
    import sys

    from .engine import PickerApp

    if source is None:
        if live:
            from . import data_ssh
            source = data_ssh
        else:
            from . import data_local
            source = data_local

    saved_stdout = sys.__stdout__
    redirect = (
        saved_stdout is not None
        and hasattr(saved_stdout, "isatty") and not saved_stdout.isatty()
        and sys.stderr is not None
        and hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    )
    try:
        if redirect:
            sys.__stdout__ = sys.stderr
        app = PickerApp(source, live=live)
        app.run()
    finally:
        if redirect:
            sys.__stdout__ = saved_stdout
    return app.result
