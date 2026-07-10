"""Worktree Picker TUI (Textual) -- the overhauled multi-machine picker.

Ported from the aperture-labs ``worktree-picker-tty-overhaul`` prototype. The
``engine`` renders over any *source* exposing ``LOCAL`` / ``LOCAL_LABEL`` /
``machines()`` / ``load()`` / ``bucket`` / ``for_machine`` (and ``make_loader``
for live multi-machine). ``data_local`` is the real local source.

Rollout is gated by config + env:
- ``new_picker: true`` in ``~/.<project>/config.yaml`` or the global
  ``~/.agent-worktrees/config.yaml`` opts a machine into the TUI persistently.
- ``AGENT_WORKTREES_NEW_PICKER=1`` forces the TUI for one invocation.
- ``AGENT_WORKTREES_LEGACY_PICKER=1`` forces the legacy ANSI picker (rollback;
  always wins).
"""
from __future__ import annotations

import os


def new_picker_enabled(config=None) -> bool:
    """True when the TUI picker should be used instead of the legacy ANSI one.

    Precedence (first match wins):
      1. ``AGENT_WORKTREES_LEGACY_PICKER`` env -> legacy (the rollback switch).
      2. ``AGENT_WORKTREES_NEW_PICKER`` env -> TUI.
      3. ``config.new_picker`` (persistent, machine-local > global).
      4. default: legacy.
    """
    if os.environ.get("AGENT_WORKTREES_LEGACY_PICKER"):
        return False
    if os.environ.get("AGENT_WORKTREES_NEW_PICKER"):
        return True
    return bool(getattr(config, "new_picker", False))


def run_tui_picker(source=None, live=False, mock_mode=None):
    """Run the TUI picker and return its result (a launch decision or None).

    With no source: ``live=True`` selects the multi-machine SSH source
    (``data_ssh``, async per-machine loader); otherwise the local-only source
    (``data_local``). Returns ``app.result``, which the caller maps onto a
    resume/create action.

    ``mock_mode`` (default ``None`` -> resolved from the environment) is the
    explicit dev sandbox: real data is shown but mutating actions are simulated
    (no side effects). It never turns on implicitly -- see
    ``engine._resolve_mock_mode``.

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
        app = PickerApp(source, live=live, mock_mode=mock_mode)
        app.run()
    finally:
        if redirect:
            sys.__stdout__ = saved_stdout
    return app.result
