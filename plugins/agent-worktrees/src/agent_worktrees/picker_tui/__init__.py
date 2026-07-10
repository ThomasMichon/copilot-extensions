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
    app = None
    try:
        if redirect:
            sys.__stdout__ = sys.stderr
        app = PickerApp(source, live=live, mock_mode=mock_mode)
        app.run()
    except Exception as exc:
        # The launcher sends the picker's stderr straight to the terminal and
        # never captures it, so an unhandled exception would vanish when the
        # screen is torn down. Persist the traceback (best-effort) before it's
        # lost, then re-raise so the terminal + exit code are unchanged.
        _write_picker_crash_log(exc, live=live, mock_mode=mock_mode, app=app)
        raise
    finally:
        if redirect:
            sys.__stdout__ = saved_stdout
    return app.result


def _write_picker_crash_log(exc, *, live, mock_mode, app=None):
    """Persist an unhandled picker exception to a crash log. Never raises.

    Writes the full traceback (plus best-effort screen context and build
    provenance) to ``~/.agent-worktrees/logs/picker-crash-<ts>-<pid>.log`` and
    prints a one-line pointer to stderr. A diagnostic must never mask the
    original failure, so every step swallows its own errors.
    """
    import os
    import sys
    import traceback
    from datetime import datetime, timezone

    try:
        from .. import config as cfg

        logs_dir = cfg.install_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        path = logs_dir / f"picker-crash-{now.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"

        try:
            from .._build_info import BUILD_INFO

            version = BUILD_INFO.get("version", "?")
            commit = BUILD_INFO.get("commit", "?")
        except Exception:
            version = commit = "?"

        header = [
            f"picker crash @ {now.isoformat(timespec='seconds')}",
            f"pid={os.getpid()} version={version} commit={commit} "
            f"live={live} mock_mode={mock_mode}",
        ]
        ctx = _picker_crash_context(app)
        if ctx:
            header.append(f"context: {ctx}")
        body = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        path.write_text("\n".join(header) + "\n\n" + body, encoding="utf-8")
        _prune_crash_logs(logs_dir)
        try:
            print(f"\n[picker] crashed -- traceback saved to {path}",
                  file=sys.stderr)
        except Exception:
            pass
    except Exception:
        pass


def _picker_crash_context(app) -> str:
    """Best-effort one-line snapshot of picker state at crash time. Never raises.

    The screen may be half-torn-down, so every field is fetched defensively."""
    if app is None:
        return ""
    try:
        scr = None
        try:
            from .engine import PickerScreen

            scr = app.query_one(PickerScreen)
        except Exception:
            scr = getattr(app, "screen", None)
        if scr is None:
            return ""
        bits = []
        for attr in ("sel", "machine_idx", "htab", "wt_anchor", "top",
                     "show_hidden", "live", "mock_mode"):
            try:
                bits.append(f"{attr}={getattr(scr, attr)!r}")
            except Exception:
                pass
        for label, fn in (("kind", lambda: scr._kind()),
                          ("wt_sel", lambda: len(scr.wt_sel)),
                          ("rows", lambda: len(scr.list_records()))):
            try:
                bits.append(f"{label}={fn()!r}")
            except Exception:
                pass
        return " ".join(bits)
    except Exception:
        return ""


def _prune_crash_logs(logs_dir, keep=25) -> None:
    """Keep only the newest ``keep`` crash logs. Never raises."""
    try:
        crashes = sorted(logs_dir.glob("picker-crash-*.log"))
        for old in crashes[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
