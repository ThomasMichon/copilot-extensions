"""Headless Pilot tests for ``SeedPromptScreen`` -- the optional "New
worktree…" first-prompt collector (Phase A,
``picker-new-session-prompt-and-composer``).
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from textual.app import App  # noqa: E402

from worktree_manager.production_picker.picker_tui.engine import (  # noqa: E402
    SeedPromptScreen,
)
from worktree_manager.production_picker.picker_tui.engine_focus import (  # noqa: E402
    FocusGroup,
)


class _Host(App):
    """A bare host that pushes one modal and records its dismiss result."""

    def __init__(self, modal) -> None:
        super().__init__()
        self._modal = modal
        self.result = "UNSET"

    def on_mount(self) -> None:
        self.push_screen(self._modal, self._done)

    def _done(self, r) -> None:
        self.result = r


def test_seed_prompt_launch_returns_typed_text():
    scr = SeedPromptScreen(target="tmichon-cloud1 local")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            scr._rec["primary"].text = "fix the flaky test"
            await pilot.press("enter")  # advance textarea -> button row
            await pilot.pause()
            group = scr.query_one("#seed-buttons", FocusGroup)
            assert group.value == "launch"
            await pilot.press("enter")  # activate Launch
            await pilot.pause()

    asyncio.run(run())
    assert app.result == "fix the flaky test"


def test_seed_prompt_skip_button_returns_empty_string():
    scr = SeedPromptScreen()
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            scr._rec["primary"].text = "typed then skipped"
            group = scr.query_one("#seed-buttons", FocusGroup)
            group.focus()
            group._idx = 1  # Skip
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert app.result == ""


def test_seed_prompt_escape_skips_with_no_prompt_entered():
    scr = SeedPromptScreen()
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())
    assert app.result == ""


def test_seed_prompt_blank_launch_returns_empty_string():
    """Confirming Launch with nothing typed behaves exactly like Skip --
    "leave blank to launch as before" (no regression for the zero-input
    path)."""
    scr = SeedPromptScreen()
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            group = scr.query_one("#seed-buttons", FocusGroup)
            group.focus()
            await pilot.pause()
            assert group.value == "launch"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert app.result == ""


def test_seed_prompt_strips_surrounding_whitespace():
    scr = SeedPromptScreen()
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            scr._rec["primary"].text = "  spaced out  \n"
            group = scr.query_one("#seed-buttons", FocusGroup)
            group.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(run())
    assert app.result == "spaced out"
