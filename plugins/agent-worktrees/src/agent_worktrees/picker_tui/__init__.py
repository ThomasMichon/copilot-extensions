"""Worktree Picker TUI (Textual) -- the overhauled multi-machine picker.

Ported from the aperture-labs ``worktree-picker-tty-overhaul`` prototype. The
``engine`` renders over any *source* exposing ``LOCAL`` / ``LOCAL_LABEL`` /
``machines()`` / ``load()`` / ``bucket`` / ``for_machine`` (and ``make_loader``
for live multi-machine). ``data_local`` is the real local source.

Rollout: the Textual picker is the **default everywhere** -- no opt-in needed.
- ``picker disable`` writes ``new_picker: false`` (machine-local or global) to
  opt a machine *out* to the legacy ANSI picker; ``picker enable`` restores the
  default.
- ``AGENT_WORKTREES_LEGACY_PICKER=1`` forces the legacy picker for one
  invocation (manual rollback; always wins).
- ``AGENT_WORKTREES_NEW_PICKER=1`` forces the new picker for one invocation
  (e.g. on a machine that opted out).
- Windows over SSH always auto-falls-back to legacy (Textual can't read the
  keyboard over Windows OpenSSH ConPTY -- see ``_new_picker_blocked_by_ssh``).
"""
from __future__ import annotations

import os


def new_picker_enabled(config=None) -> bool:
    """True when the TUI picker should be used instead of the legacy ANSI one.

    The Textual picker is the **default** (True); a machine opts *out* to legacy
    via ``picker disable`` (persisted ``new_picker: false``). Precedence
    (first match wins):
      1. ``AGENT_WORKTREES_LEGACY_PICKER`` env -> legacy (the rollback switch).
      2. ``AGENT_WORKTREES_NEW_PICKER`` env -> TUI.
      3. ``config.new_picker`` (persistent, machine-local > global; default True).
      4. default: TUI.
    """
    if os.environ.get("AGENT_WORKTREES_LEGACY_PICKER"):
        return False
    if os.environ.get("AGENT_WORKTREES_NEW_PICKER"):
        return True
    return bool(getattr(config, "new_picker", True))


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
