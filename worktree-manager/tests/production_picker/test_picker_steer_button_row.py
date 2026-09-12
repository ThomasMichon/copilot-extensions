"""Tests for `SteerButtonRow`'s mouse click hit-testing (the Picker's
Confirm/Save/Cancel row for the steer-card form).

A prior version of `on_click` silently did nothing when the click's x
coordinate fell just outside every button's computed span (an off-by-one in
the span math, a click landing in the inter-button gap, or a click past the
last button) -- a real click was captured (event.stop()) but never dispatched
to any button, indistinguishable from the click never having happened at all.
This is the leading suspect for reports that the Picker's steer-card
Confirm/Save is "unreliable": the operator clicks, sees no error, and nothing
happens because the click landed one column off from where the span math
expected.

These tests exercise `on_click` directly against a `SteerButtonRow` instance
without mounting a full Textual app, since the method under test only
manipulates `self._idx` / `self.refresh()` / `self._on_press` and does not
require a live compositor.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult

from worktree_manager.production_picker.picker_tui.engine import SteerButtonRow


class _Event:
    """Minimal stand-in for a Textual click event: only `.x` is read."""

    def __init__(self, x: int) -> None:
        self.x = x
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _HostApp(App):
    """Mounts a single SteerButtonRow so on_click's self.refresh()/self.focus()
    calls have a live compositor to run against."""

    def __init__(self, row: SteerButtonRow) -> None:
        super().__init__()
        self._row = row

    def compose(self) -> ComposeResult:
        yield self._row


def _press_at(buttons, x: int) -> str | None:
    """Mount a SteerButtonRow with `buttons`, click at column `x`, and return
    the key of whichever button was pressed (or None if nothing was)."""
    pressed: list[str] = []
    row = SteerButtonRow(buttons, on_press=pressed.append)

    async def run() -> None:
        app = _HostApp(row)
        async with app.run_test():
            row.on_click(_Event(x))

    asyncio.run(run())
    return pressed[0] if pressed else None


# Two buttons: "Confirm" (span 0-9: " Confirm ") then a 2-col gap, then
# "Cancel" (span 11-19: " Cancel ").
_BUTTONS = [("confirm", "Confirm"), ("cancel", "Cancel")]


@pytest.mark.parametrize(
    "x,expected",
    [
        (0, "confirm"),       # start of Confirm's span
        (4, "confirm"),       # middle of Confirm's span
        (8, "confirm"),       # last column of Confirm's span
        (11, "cancel"),       # start of Cancel's span
        (15, "cancel"),       # middle of Cancel's span
        (18, "cancel"),       # last column of Cancel's span
    ],
)
def test_exact_span_hits_press_the_right_button(x, expected):
    assert _press_at(_BUTTONS, x) == expected


@pytest.mark.parametrize(
    "x,expected",
    [
        (9, "confirm"),    # inter-button gap, closer to Confirm's end
        (10, "cancel"),    # inter-button gap, closer to Cancel's start
        (-3, "confirm"),   # left of everything
        (25, "cancel"),    # right of everything
    ],
)
def test_near_miss_falls_back_to_the_nearest_button_instead_of_doing_nothing(
    x, expected
):
    assert _press_at(_BUTTONS, x) == expected


def test_every_column_in_and_around_the_row_presses_exactly_one_button():
    # Exhaustively sweep a wide column range: on_click must never be a no-op
    # (every one of these positions is a plausible click target on a real
    # terminal render), and must never raise.
    for x in range(-5, 30):
        pressed = _press_at(_BUTTONS, x)
        assert pressed in ("confirm", "cancel"), (
            f"click at x={x} pressed nothing"
        )

