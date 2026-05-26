"""Shared output helpers — colored status lines and ANSI formatting."""

from __future__ import annotations

import contextlib
import os
import sys
from typing import Iterator


def _supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()


@contextlib.contextmanager
def stdout_to_stderr() -> Iterator[None]:
    """Redirect sys.stdout to sys.stderr so all print/write goes to the terminal.

    Callers can still write to the real stdout via sys.__stdout__.
    Re-evaluates color support after the swap since stderr may be a TTY
    even when stdout is a pipe.
    """
    global _COLOR
    saved = sys.stdout
    saved_color = _COLOR
    sys.stdout = sys.stderr
    _COLOR = _supports_color()
    try:
        yield
    finally:
        sys.stdout = saved
        _COLOR = saved_color

# ANSI color codes
_COLORS: dict[str, str] = {
    "reset": "\033[0m",
    "red": "\033[0;31m",
    "green": "\033[0;32m",
    "yellow": "\033[0;33m",
    "cyan": "\033[0;36m",
    "magenta": "\033[0;35m",
    "dim": "\033[2m",
    "bold": "\033[1m",
}


def _c(color: str, text: str) -> str:
    if not _COLOR:
        return text
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def ok(msg: str) -> None:
    print(f"  {_c('green', '✓')} {msg}")


def changed(msg: str) -> None:
    print(f"  {_c('yellow', '→')} {msg}")


def skipped(msg: str) -> None:
    print(f"  {_c('cyan', '○')} {msg}")


def err(msg: str) -> None:
    print(f"  {_c('red', '✗')} {msg}")


def header(name: str) -> None:
    bar = "═" * max(0, 56 - len(name))
    print()
    print(f"{_c('cyan', f'═══ {name} ')}{_c('dim', bar)}")


def dry_run(msg: str) -> None:
    print(f"  {_c('magenta', '▷')} (dry-run) {msg}")


def warn(msg: str) -> None:
    print(f"  {_c('yellow', '⚠️')}  {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")
