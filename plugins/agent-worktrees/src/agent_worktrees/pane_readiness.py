"""Copilot pane readiness helpers for safe detached seed injection."""

from __future__ import annotations

import re


_BUSY_STATUS = re.compile(
    r"^\s*(?:[^\w\s~/.]\s*)?"
    r"(?:resuming session|loading|starting|initializing|authenticating|connecting)\b",
    re.IGNORECASE,
)


def input_region(capture: str) -> str:
    """Bottom live-input region, not scrollback transcript history.

    With the boxed input drawn, that is the box and its footer plus the two
    lines above it (the status line and the directory banner); otherwise the
    last few lines of the pane.
    """
    lines = [line.rstrip() for line in capture.splitlines() if line.strip()]
    rail = max((i for i, line in enumerate(lines) if "╻▄" in line), default=None)
    if rail is not None:
        return "\n".join(lines[max(0, rail - 2):])
    return "\n".join(lines[-8:])


def is_busy(region: str) -> bool:
    """True when a status line in the live region says Copilot isn't taking
    input yet: the line *starts* with a busy verb (after an optional spinner
    glyph), so a banner path or a transcript sentence that merely contains the
    word doesn't count."""
    return any(_BUSY_STATUS.match(line) for line in region.splitlines())


#: Shell prompts (incl. attached ``PS C:\repo>`` / ``C:\repo>``) and exit lines.
_SHELL_TAIL = re.compile(
    r"[❯$#%]\s*$|(?:^|\s)>\s*$|^\s*(?:PS\b[^>]*|[A-Za-z]:\\[^>]*)>\s*$|\bexited\b",
    re.IGNORECASE,
)
#: Lines Copilot draws under its input box (the key-hint footer).
_MAX_FOOTER_LINES = 2
#: One key-hint segment of Copilot's footer: a key, then a short lowercase
#: action ("← open sidebar", "/ commands", "shift+tab mode"). A footer row is
#: made only of these, joined by " · "; any other text under the box (e.g.
#: "Connection closed; press Enter to reconnect") means it is stale scrollback.
_FOOTER_SEGMENT = re.compile(
    r"^(?:[←→↑↓/?@!#]|esc|tab|enter|space|(?:ctrl|shift|alt|cmd)\+\S+)"
    r"\s+[a-z][a-z' -]*$"
)


def _is_footer_row(line: str) -> bool:
    return all(_FOOTER_SEGMENT.match(seg.strip()) for seg in line.split("·"))


def _live_box(capture: str) -> bool:
    """A complete input box (top rail, then bottom rail) with nothing under it
    but Copilot's footer rows. A box left in the scrollback above a shell
    prompt (Copilot exited) or any other later output is not live input."""
    lines = [line.rstrip() for line in capture.splitlines() if line.strip()]
    top = max((i for i, line in enumerate(lines) if "╻▄" in line), default=None)
    if top is None:
        return False
    bottom = next((i for i in range(top + 1, len(lines)) if "╹▀" in lines[i]), None)
    if bottom is None:
        return False
    tail = lines[bottom + 1:]
    return len(tail) <= _MAX_FOOTER_LINES and all(
        _is_footer_row(line) and not _SHELL_TAIL.search(line) and not _SHELL_HEAD.match(line)
        for line in tail
    )


#: A shell prompt at the start of a line, even with typed text after it
#: (``user@host:~/dir$ ...``, ``PS C:\repo> ...``, ``C:\repo> ...``, or a bare
#: ``$ ...`` / ``# ...`` / ``% ...`` / ``❯ ...``).
_SHELL_HEAD = re.compile(
    r"^\s*(?:[\w.-]+@[\w.-]+(?::\S*)?\s*[$#%>]|PS\s+\S[^>]*>|[A-Za-z]:\\[^>]*>|[$#%❯](?:\s|$))"
)


#: The legacy footer's own cue, a whole footer segment: an optional non-prompt
#: glyph, then "[press] esc to interrupt" -- nothing typed before or after.
_INTERRUPT_SEGMENT = re.compile(r"^(?:[^\w\s$#%❯>]\s*)?(?:press\s+)?esc\s+to\s+interrupt$", re.IGNORECASE)


def _is_interrupt_footer_row(line: str) -> bool:
    """A footer row built only from Copilot's grammar: ``·``-joined segments,
    each the interrupt cue or a key hint, at least one the interrupt cue. Any
    other text on the line (a prompt, a path, typed input) is not Copilot's."""
    segs = [seg.strip() for seg in line.split("·")]
    return (any(_INTERRUPT_SEGMENT.match(s) for s in segs)
            and all(_INTERRUPT_SEGMENT.match(s) or _FOOTER_SEGMENT.match(s) for s in segs))


def _live_footer(region: str) -> bool:
    """The "esc to interrupt" footer as the live bottom line: anything below it
    (a prompt, an exit line, ``Connection closed``) means it is stale, and the
    line must match the footer's own grammar -- a shell line that merely
    contains both words (``user@host ~/esc/interrupt % ...``, ``~/repo ❯ press
    esc to interrupt``) is a shell, not Copilot."""
    lines = [line for line in region.splitlines() if line.strip()]
    if not lines:
        return False
    last = lines[-1]
    return (_is_interrupt_footer_row(last)
            and not _SHELL_TAIL.search(last) and not _SHELL_HEAD.match(last))


def ready_signature(capture: str) -> str | None:
    """Stable cue for a live Copilot input prompt, or ``None`` when not ready."""
    region = input_region(capture)
    low = region.lower()
    if "enter to select" in low or is_busy(region):
        return None
    # Copilot CLI >= 1.0.89 boxed input, drawn at the live bottom of the pane.
    if _live_box(capture):
        return "boxed-input"
    # Older Copilot builds expose a footer while the input is live. Require
    # the footer in the bottom region; a bare shell prompt that happens to use
    # the same caret glyph is not enough.
    if _live_footer(region):
        return "interrupt-footer"
    return None
