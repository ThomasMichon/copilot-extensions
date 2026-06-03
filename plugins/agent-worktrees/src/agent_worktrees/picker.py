"""Custom ANSI TTY picker -- arrow-key menu with sections and dimming.

Platform backends:
- Windows: msvcrt for key reading
- Unix: tty/termios for raw mode

Falls back to a numbered prompt on non-interactive terminals.

Supports an optional "toggle" row (profile selector) cycled with Tab.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class ItemKind(str, Enum):
    """Menu item classification for rendering."""
    NORMAL = "normal"
    ACTION = "action"
    DIMMED = "dimmed"
    SEPARATOR = "separator"


@dataclass
class MenuItem:
    """A single menu item."""
    label: str
    kind: ItemKind = ItemKind.NORMAL
    value: object = None
    subtitle: str | None = None


@dataclass
class PickResult:
    """Result from the interactive picker."""
    selected: int = -1
    profile_idx: int = 0
    command: str | None = None


# --- ANSI helpers ---

ESC = "\033"


def _is_interactive() -> bool:
    """Check if stdin/stdout are interactive TTYs."""
    return (
        hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
        and hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    )


def _clear_line() -> None:
    sys.stdout.write(f"{ESC}[2K{ESC}[0G")


def _clear_screen() -> None:
    """Clear the entire screen and move cursor to top-left."""
    sys.stdout.write(f"{ESC}[2J{ESC}[H")
    sys.stdout.flush()


def _move_up(n: int) -> None:
    if n > 0:
        sys.stdout.write(f"{ESC}[{n}A")


def _color(code: str, text: str) -> str:
    return f"{ESC}[{code}m{text}{ESC}[0m"


def _term_width() -> int:
    """Get terminal width, defaulting to 80 if unknown."""
    return shutil.get_terminal_size((80, 24)).columns


def _visible_len(text: str) -> int:
    """Length of text excluding ANSI escape sequences."""
    import re
    return len(re.sub(r"\033\[[^m]*m", "", text))


def _display_width(text: str) -> int:
    """Terminal display width -- wide chars (emoji, CJK) count as 2 columns."""
    import unicodedata

    w = 0
    for ch in text:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def _truncate(text: str, max_width: int) -> str:
    """Truncate a plain-text label so rendered output fits in max_width columns.

    Uses Unicode-aware width calculation so emoji and CJK characters
    (which occupy 2 terminal columns) are measured correctly.
    """
    import unicodedata

    if _display_width(text) <= max_width:
        return text
    result: list[str] = []
    w = 0
    for ch in text:
        eaw = unicodedata.east_asian_width(ch)
        cw = 2 if eaw in ("W", "F") else 1
        if w + cw > max_width - 1:
            break
        result.append(ch)
        w += cw
    return "".join(result) + "…"


def _hide_cursor() -> None:
    sys.stdout.write(f"{ESC}[?25l")
    sys.stdout.flush()


def _show_cursor() -> None:
    sys.stdout.write(f"{ESC}[?25h")
    sys.stdout.flush()


def _term_height() -> int:
    """Get terminal height, defaulting to 24 if unknown."""
    return shutil.get_terminal_size((80, 24)).lines


def _enter_alt_screen() -> None:
    """Switch to the alternate screen buffer (prevents scroll-buffer pollution)."""
    sys.stdout.write(f"{ESC}[?1049h")
    sys.stdout.flush()


def _leave_alt_screen() -> None:
    """Leave the alternate screen buffer, restoring previous terminal content."""
    sys.stdout.write(f"{ESC}[?1049l")
    sys.stdout.flush()


def _build_line_map(items: Sequence[MenuItem]) -> list[tuple[int, bool]]:
    """Build a flat mapping from display lines to (item_index, is_subtitle).

    Each item produces one line; items with subtitles produce a second line.
    """
    lines: list[tuple[int, bool]] = []
    for i, item in enumerate(items):
        lines.append((i, False))
        if item.subtitle:
            lines.append((i, True))
    return lines


def _ensure_visible(
    line_map: list[tuple[int, bool]],
    scroll_offset: int,
    viewport_height: int,
    selected: int,
) -> int:
    """Return adjusted scroll_offset so the selected item is fully visible.

    When the item has a subtitle (2 display lines) and the viewport is too
    small for both, the primary label line is prioritised.
    """
    first_line: int | None = None
    last_line: int | None = None
    for ln, (idx, _) in enumerate(line_map):
        if idx == selected:
            if first_line is None:
                first_line = ln
            last_line = ln
    if first_line is None or last_line is None:
        return scroll_offset
    # Scroll up if selected item is above the viewport
    if first_line < scroll_offset:
        return first_line
    # Scroll down if selected item extends below the viewport
    if last_line >= scroll_offset + viewport_height:
        # Prefer showing the label (first_line) over the subtitle
        ideal = last_line - viewport_height + 1
        return min(ideal, first_line)
    return scroll_offset


# --- Key reading ---

def _read_key_windows() -> str | None:
    """Read a keypress on Windows using msvcrt."""
    import msvcrt

    ch = msvcrt.getwch()
    if ch == "\x1b":
        return "escape"
    if ch == "\r":
        return "enter"
    if ch == "\t":
        return "tab"
    if ch == ":":
        return "colon"
    if ch in ("\x00", "\xe0"):
        # Extended key -- read the scan code
        scan = msvcrt.getwch()
        if scan == "H":
            return "up"
        if scan == "P":
            return "down"
        return None
    return None


def _read_key_unix() -> str | None:
    """Read a keypress on Unix using tty/termios.

    Uses os.read() on the raw fd instead of sys.stdin.read() to avoid
    Python's internal BufferedReader consuming bytes that select() then
    can't see -- which caused arrow keys and other escape sequences to be
    misidentified as bare Esc.
    """
    import select
    import tty
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b"\x1b":
            # Check if more bytes are available (arrow/function key sequence).
            # Bare Esc has nothing following; escape sequences arrive as a
            # burst so the next bytes are already in the buffer.
            if select.select([fd], [], [], 0.05)[0]:
                seq = os.read(fd, 1)
                if seq == b"[":
                    code = os.read(fd, 1)
                    if code == b"A":
                        return "up"
                    if code == b"B":
                        return "down"
                    if code == b"Z":
                        return "shift-tab"
                    return None
                if seq == b"O":
                    code = os.read(fd, 1)
                    if code == b"A":
                        return "up"
                    if code == b"B":
                        return "down"
                    return None
                return None
            # No follow-up bytes -- bare Esc
            return "escape"
        if ch in (b"\r", b"\n"):
            return "enter"
        if ch == b"\t":
            return "tab"
        if ch == b"\x03":
            raise KeyboardInterrupt
        if ch == b"q":
            return "escape"
        if ch == b":":
            return "colon"
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key() -> str | None:
    """Read a single keypress, platform-appropriate."""
    if os.name == "nt":
        return _read_key_windows()
    return _read_key_unix()


# --- Rendering ---

def _render_menu(
    items: Sequence[MenuItem],
    selected: int,
    render_height: int,
    profile_labels: Sequence[str] | None = None,
    profile_idx: int = 0,
    *,
    line_map: list[tuple[int, bool]],
    scroll_offset: int = 0,
    viewport_height: int = 0,
    needs_scroll: bool = False,
) -> None:
    """Render the menu with viewport scrolling when items overflow the terminal.

    The total number of rendered lines is always exactly *render_height*,
    keeping the cursor-based overwrite model stable.
    """
    _move_up(render_height)
    max_w = _term_width()

    # Profile toggle bar (pinned at top, always visible)
    if profile_labels:
        _clear_line()
        parts: list[str] = []
        for i, lbl in enumerate(profile_labels):
            if i == profile_idx:
                parts.append(_color("1;36", lbl))  # bold cyan
            else:
                parts.append(_color("2", lbl))  # dim
        sys.stdout.write(f"   Tab: {' │ '.join(parts)}\n")

    # Scroll-up indicator (always occupies one line when scrolling is active)
    if needs_scroll:
        _clear_line()
        if scroll_offset > 0:
            above = scroll_offset
            sys.stdout.write(f"    {_color('2', f'▲ {above} more')}\n")
        else:
            sys.stdout.write("\n")

    # Visible lines from the viewport window
    visible = line_map[scroll_offset:scroll_offset + viewport_height]
    for item_idx, is_sub in visible:
        _clear_line()
        item = items[item_idx]
        if is_sub:
            sub_text = _truncate(item.subtitle or "", max_w - 10)
            if item_idx == selected:
                sys.stdout.write(f"    {_color('2;36', '  ' + sub_text)}\n")
            else:
                sys.stdout.write(f"    {_color('2', '  ' + sub_text)}\n")
        elif item.kind == ItemKind.SEPARATOR:
            sys.stdout.write(f"    {_color('2', _truncate(item.label, max_w - 4))}\n")
        elif item_idx == selected:
            sys.stdout.write(f"  {_color('1;36', '▶ ' + _truncate(item.label, max_w - 4))}\n")
        elif item.kind == ItemKind.ACTION:
            sys.stdout.write(f"    {_color('32', _truncate(item.label, max_w - 4))}\n")
        elif item.kind == ItemKind.DIMMED:
            sys.stdout.write(f"    {_color('2', _truncate(item.label, max_w - 4))}\n")
        else:
            sys.stdout.write(f"    {_color('0', _truncate(item.label, max_w - 4))}\n")

    # Scroll-down indicator (always occupies one line when scrolling is active)
    if needs_scroll:
        _clear_line()
        remaining = len(line_map) - (scroll_offset + viewport_height)
        if remaining > 0:
            sys.stdout.write(f"    {_color('2', f'▼ {remaining} more')}\n")
        else:
            sys.stdout.write("\n")

    sys.stdout.flush()


# --- Fallback numbered prompt ---

def _numbered_prompt(
    items: Sequence[MenuItem],
    default: int,
    title: str,
    profile_labels: Sequence[str] | None = None,
) -> PickResult:
    """Non-interactive fallback: numbered list with text input."""
    print()
    print(title)
    print()

    # Profile selection (if multiple)
    profile_idx = 0
    if profile_labels and len(profile_labels) > 1:
        print("  Backend:")
        for i, lbl in enumerate(profile_labels):
            default_mark = "  (default)" if i == 0 else ""
            print(f"    [{i + 1}] {lbl}{default_mark}")
        print()
        try:
            pchoice = input(f"Backend [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return PickResult(selected=-1, profile_idx=0)
        if pchoice:
            try:
                pidx = int(pchoice) - 1
                if 0 <= pidx < len(profile_labels):
                    profile_idx = pidx
            except ValueError:
                pass
        print()

    selectable: list[int] = []
    for i, item in enumerate(items):
        if item.kind == ItemKind.SEPARATOR:
            print(f"    {item.label}")
        else:
            num = len(selectable) + 1
            selectable.append(i)
            default_mark = "  (default)" if i == default else ""
            print(f"  [{num}] {item.label}{default_mark}")
            if item.subtitle:
                print(f"       {item.subtitle}")

    print()
    default_num = selectable.index(default) + 1 if default in selectable else 1

    try:
        choice = input(f"Choose [{default_num}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return PickResult(selected=-1, profile_idx=profile_idx)

    sel = default
    if choice:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(selectable):
                sel = selectable[idx]
        except ValueError:
            pass

    return PickResult(selected=sel, profile_idx=profile_idx)


# --- Public API ---

def pick(
    items: Sequence[MenuItem],
    *,
    title: str = "Select an option",
    subtitle: str = "Use ↑↓ arrows, Enter to select, Esc/q to cancel",
    default: int = 0,
    profile_labels: Sequence[str] | None = None,
    profile_default: int = 0,
) -> PickResult:
    """Show an interactive picker menu with optional profile toggle.

    Uses the alternate screen buffer to prevent scroll-buffer pollution
    and implements viewport scrolling when items overflow the terminal.

    Args:
        items: Menu items to display.
        title: Header text shown above the menu.
        subtitle: Help text below the title.
        default: Initially selected index.
        profile_labels: Labels for backend profiles (cycled with Tab).
        profile_default: Initially selected profile index.

    Returns:
        PickResult with selected item index (-1 if cancelled) and profile index.
    """
    if not items:
        return PickResult(selected=-1, profile_idx=profile_default)

    # Validate / clamp default
    if default < 0 or default >= len(items):
        default = 0
    if items[default].kind == ItemKind.SEPARATOR:
        for i, item in enumerate(items):
            if item.kind != ItemKind.SEPARATOR:
                default = i
                break
        else:
            # All items are separators -- nothing selectable
            return PickResult(selected=-1, profile_idx=profile_default)

    has_profiles = bool(profile_labels and len(profile_labels) > 1)

    if not _is_interactive():
        return _numbered_prompt(
            items, default, title,
            profile_labels=profile_labels if has_profiles else None,
        )

    # Build line map for viewport management
    line_map = _build_line_map(items)
    total_item_lines = len(line_map)

    # Calculate available space
    term_h = _term_height()
    max_w = _term_width()
    header_lines = 3  # title + subtitle + blank line
    profile_line_count = 1 if has_profiles else 0
    available = term_h - header_lines - 1  # -1 for bottom margin
    budget = available - profile_line_count

    if budget < 1:
        # Terminal impossibly small for interactive mode
        return _numbered_prompt(
            items, default, title,
            profile_labels=profile_labels if has_profiles else None,
        )

    if total_item_lines <= budget:
        needs_scroll = False
        viewport_height = total_item_lines
    else:
        needs_scroll = True
        viewport_height = max(budget - 2, 1)  # reserve 2 for indicators

    render_height = profile_line_count + viewport_height + (2 if needs_scroll else 0)

    # Enter alternate screen buffer -- isolates all picker output
    _enter_alt_screen()
    _clear_screen()

    # Truncate header lines to terminal width to avoid wrapping
    print(_truncate(title, max_w))
    if has_profiles:
        print(_truncate(f"   {subtitle}  ·  Tab: cycle backend", max_w))
    else:
        print(_truncate(f"   {subtitle}", max_w))
    print()

    # Write placeholder lines for the render area
    for _ in range(render_height):
        print()

    sel = default
    pidx = profile_default
    scroll_offset = 0

    # Ensure initial selection is visible
    scroll_offset = _ensure_visible(line_map, scroll_offset, viewport_height, sel)

    _hide_cursor()
    try:
        _render_menu(
            items, sel, render_height,
            profile_labels=profile_labels if has_profiles else None,
            profile_idx=pidx,
            line_map=line_map,
            scroll_offset=scroll_offset,
            viewport_height=viewport_height,
            needs_scroll=needs_scroll,
        )

        while True:
            key = _read_key()
            if key == "up":
                sel = (sel - 1) % len(items)
                while items[sel].kind == ItemKind.SEPARATOR:
                    sel = (sel - 1) % len(items)
                scroll_offset = _ensure_visible(
                    line_map, scroll_offset, viewport_height, sel,
                )
                _render_menu(
                    items, sel, render_height,
                    profile_labels=profile_labels if has_profiles else None,
                    profile_idx=pidx,
                    line_map=line_map,
                    scroll_offset=scroll_offset,
                    viewport_height=viewport_height,
                    needs_scroll=needs_scroll,
                )
            elif key == "down":
                sel = (sel + 1) % len(items)
                while items[sel].kind == ItemKind.SEPARATOR:
                    sel = (sel + 1) % len(items)
                scroll_offset = _ensure_visible(
                    line_map, scroll_offset, viewport_height, sel,
                )
                _render_menu(
                    items, sel, render_height,
                    profile_labels=profile_labels if has_profiles else None,
                    profile_idx=pidx,
                    line_map=line_map,
                    scroll_offset=scroll_offset,
                    viewport_height=viewport_height,
                    needs_scroll=needs_scroll,
                )
            elif key == "tab" and has_profiles:
                pidx = (pidx + 1) % len(profile_labels)  # type: ignore[arg-type]
                _render_menu(
                    items, sel, render_height,
                    profile_labels=profile_labels,
                    profile_idx=pidx,
                    line_map=line_map,
                    scroll_offset=scroll_offset,
                    viewport_height=viewport_height,
                    needs_scroll=needs_scroll,
                )
            elif key == "shift-tab" and has_profiles:
                pidx = (pidx - 1) % len(profile_labels)  # type: ignore[arg-type]
                _render_menu(
                    items, sel, render_height,
                    profile_labels=profile_labels,
                    profile_idx=pidx,
                    line_map=line_map,
                    scroll_offset=scroll_offset,
                    viewport_height=viewport_height,
                    needs_scroll=needs_scroll,
                )
            elif key == "enter":
                break
            elif key == "colon":
                return PickResult(selected=-1, profile_idx=pidx, command="system")
            elif key == "escape":
                sel = -1
                break
    except KeyboardInterrupt:
        sel = -1
    finally:
        _show_cursor()
        _leave_alt_screen()

    return PickResult(selected=sel, profile_idx=pidx)
