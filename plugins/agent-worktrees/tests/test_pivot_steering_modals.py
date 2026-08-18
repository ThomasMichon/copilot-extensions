"""Headless Pilot tests for the A5 steering-seam DISPATCH-pivot modals.

Drives the two net-new native modals -- ``PivotCardScreen`` (read-only card
detail) and ``PivotFormScreen`` (the elicitation form) -- in isolation via a
minimal host ``App`` + Textual's ``run_test`` pilot, asserting the form gathers
text/textarea/choice answers and returns them (or ``None`` on cancel) and the
card renders its parts and closes. No coordinator, no subprocess: the modal only
gathers the operator's answer; the caller runs the steer transport.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from textual.app import App  # noqa: E402
from textual.widgets import Input, RadioButton, RadioSet, TextArea  # noqa: E402

from agent_worktrees.picker_tui.engine import (  # noqa: E402
    PivotCardScreen,
    PivotFormScreen,
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


def _fields():
    return [
        {"name": "feedback", "type": "textarea"},
        {"name": "note", "type": "text"},
        {"name": "decision", "type": "choice", "options": ["revise", "post-approved"]},
    ]


def test_form_collects_and_submits_with_default_choice():
    scr = PivotFormScreen("Steer PR 123", "recommend post", _fields(), "Submit")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            scr.query_one("#ff-0", TextArea).text = "looks good\nsecond line"
            scr.query_one("#ff-1", Input).value = "a note"
            await pilot.pause()
            # The first choice option is pre-selected -> a submit is always valid.
            await pilot.press("ctrl+s")
            await pilot.pause()

    asyncio.run(run())
    assert app.result == {
        "feedback": "looks good\nsecond line",
        "note": "a note",
        "decision": "revise",
    }


def test_form_choice_change_via_keys():
    scr = PivotFormScreen("Steer", "", _fields(), "Submit")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            rs = scr.query_one("#ff-2", RadioSet)
            rs.focus()
            await pilot.pause()
            # Move the highlight to the second option and commit it, then submit.
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()

    asyncio.run(run())
    assert app.result["decision"] == "post-approved"


def test_form_cancel_returns_none():
    scr = PivotFormScreen("Steer", "", _fields(), "Submit")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())
    assert app.result is None


def test_form_with_no_fields_submits_empty():
    scr = PivotFormScreen("Notice", "nothing to answer", [], "OK")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()

    asyncio.run(run())
    assert app.result == {}


def test_form_choice_defaults_first_option_pressed():
    scr = PivotFormScreen("Steer", "", _fields(), "Submit")
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            rs = scr.query_one("#ff-2", RadioSet)
            buttons = list(rs.query(RadioButton))
            assert buttons[0].value is True
            assert buttons[1].value is False

    asyncio.run(run())


def _card():
    return {
        "title": "Card T",
        "status": "recommend post-approved",
        "link": "https://ado/pr/123",
        "body": "The full review body.\nLine two.",
    }


def test_card_renders_parts_and_closes():
    scr = PivotCardScreen("Row Title", _card())
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, PivotCardScreen)
            body = scr.query_one("#card-body")
            rendered = body.render()
            text = getattr(rendered, "plain", None) or str(rendered)
            assert "Card T" in text
            assert "recommend post-approved" in text
            assert "https://ado/pr/123" in text
            assert "The full review body." in text
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PivotCardScreen)

    asyncio.run(run())
    assert app.result is None


def test_card_empty_renders_placeholder():
    scr = PivotCardScreen("", {})
    app = _Host(scr)

    async def run():
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            body = scr.query_one("#card-body")
            rendered = body.render()
            text = getattr(rendered, "plain", None) or str(rendered)
            assert "empty card" in text

    asyncio.run(run())
