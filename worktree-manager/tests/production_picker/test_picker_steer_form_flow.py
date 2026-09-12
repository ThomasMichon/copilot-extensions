"""Tests for the steer form's Confirm/Save/Reset flow (PivotFormScreen).

Covers the redesign requested after a live Picker session found that closing
the dialog (by any path) did not reliably leave a recoverable trace of the
operator's answer: Confirm now always writes its draft *before* the (async,
post-dismiss) submission runs, Reset requires an are-you-sure and clears the
form in place without closing, and the draft is only cleared once the caller
has confirmed the actual submission succeeded.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.app import App
from textual.widgets import Input

from worktree_manager.production_picker.picker_tui import engine as engine_mod
from worktree_manager.production_picker.picker_tui.engine import (
    PivotFormScreen,
    ResetConfirmScreen,
    _steer_draft_path,
)

_CARD = {"title": "Review draft", "status": "rec", "body": "body"}
_FIELDS = [{"name": "feedback", "type": "text"}]


class _HostApp(App):
    def __init__(self, screen: PivotFormScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result = "unset"

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._on_result)

    def _on_result(self, value) -> None:
        self.result = value


def _draft_values(task_id: str) -> dict | None:
    path = _steer_draft_path(task_id)
    if not path or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("values")


def test_confirm_writes_the_draft_before_dismissing(tmp_path, monkeypatch):
    monkeypatch.setenv(engine_mod._STEER_DRAFTS_ENV, str(tmp_path))
    screen = PivotFormScreen(_CARD, _FIELDS, "Steer", task_id="t-confirm")

    async def run():
        app = _HostApp(screen)
        async with app.run_test() as pilot:
            screen.query_one("#q-0", Input).value = "ship it"
            await pilot.pause()
            screen._confirm()
            await pilot.pause()
        return app

    app = asyncio.run(run())
    assert app.result == {"feedback": "ship it"}
    # The draft is written as part of _confirm, unconditionally -- it is the
    # caller's job (once it confirms actual submission success) to clear it,
    # not this screen's dismissal.
    assert _draft_values("t-confirm") == {"feedback": "ship it"}


def test_save_writes_the_draft_and_dismisses_with_none(tmp_path, monkeypatch):
    monkeypatch.setenv(engine_mod._STEER_DRAFTS_ENV, str(tmp_path))
    screen = PivotFormScreen(_CARD, _FIELDS, "Steer", task_id="t-save")

    async def run():
        app = _HostApp(screen)
        async with app.run_test() as pilot:
            screen.query_one("#q-0", Input).value = "draft text"
            await pilot.pause()
            screen.action_save()
            await pilot.pause()
        return app

    app = asyncio.run(run())
    assert app.result is None
    assert _draft_values("t-save") == {"feedback": "draft text"}


def test_reset_confirmation_declined_leaves_the_form_untouched_and_open(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(engine_mod._STEER_DRAFTS_ENV, str(tmp_path))
    screen = PivotFormScreen(_CARD, _FIELDS, "Steer", task_id="t-reset-no")

    async def run():
        app = _HostApp(screen)
        async with app.run_test() as pilot:
            screen.query_one("#q-0", Input).value = "keep me"
            await pilot.pause()
            screen._on_reset_pressed()
            await pilot.pause()
            assert isinstance(app.screen, ResetConfirmScreen)
            app.screen.action_stay()
            await pilot.pause()
            # Declining stays on the steer form -- never dismissed.
            assert app.result == "unset"
            assert screen.query_one("#q-0", Input).value == "keep me"

    asyncio.run(run())


def test_reset_confirmed_clears_fields_without_closing(tmp_path, monkeypatch):
    monkeypatch.setenv(engine_mod._STEER_DRAFTS_ENV, str(tmp_path))
    screen = PivotFormScreen(_CARD, _FIELDS, "Steer", task_id="t-reset-yes")

    async def run():
        app = _HostApp(screen)
        async with app.run_test() as pilot:
            screen.query_one("#q-0", Input).value = "clear me"
            await pilot.pause()
            screen.action_save()  # leaves a draft on disk to prove reset clears it
            await pilot.pause()

        # action_save above already dismissed the screen; re-mount a second
        # instance to exercise reset against a still-open dialog.
        screen2 = PivotFormScreen(_CARD, _FIELDS, "Steer", task_id="t-reset-yes")
        app2 = _HostApp(screen2)
        async with app2.run_test() as pilot:
            screen2.query_one("#q-0", Input).value = "clear me too"
            await pilot.pause()
            screen2._on_reset_pressed()
            await pilot.pause()
            assert isinstance(app2.screen, ResetConfirmScreen)
            app2.screen.action_reset()
            await pilot.pause()
            # Confirming reset never closes the steer form.
            assert app2.result == "unset"
            assert screen2.query_one("#q-0", Input).value == ""
        return app2

    asyncio.run(run())
    assert _draft_values("t-reset-yes") is None


@pytest.mark.parametrize("verdict,expect_stay", [(True, False), (False, True)])
def test_reset_confirm_screen_reports_the_chosen_verdict(verdict, expect_stay):
    async def run():
        screen = ResetConfirmScreen()

        class _App(App):
            def on_mount(self):
                self.push_screen(screen, self._got)
                self.got = None

            def _got(self, value):
                self.got = value

        app = _App()
        async with app.run_test() as pilot:
            if verdict:
                screen.action_reset()
            else:
                screen.action_stay()
            await pilot.pause()
        return app.got

    got = asyncio.run(run())
    assert got is (not expect_stay)

