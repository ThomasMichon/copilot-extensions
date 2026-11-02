"""Actions prompt first-paint, late eligibility and compact viewport contracts."""
from __future__ import annotations

import asyncio
import threading

import pytest
from textual.app import App
from textual.screen import Screen
from textual.widgets import OptionList

from test_picker_tui import _fixture_source
from worktree_manager.seed_history import HistoryCursor
from worktree_manager.production_picker import context
from worktree_manager.production_picker.picker_tui import actions_prompt
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_dialogs import SubMenuScreen
from worktree_manager.production_picker.picker_tui.inbox import Inbox
from worktree_manager.production_picker.picker_tui.seed_prompt_field import SeedPromptField


@pytest.mark.parametrize("eligible", [False, True])
def test_history_read_never_blocks_actions_paint_or_escape(eligible, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(context, "_project", "example")
    monkeypatch.setattr(context, "_bootstrap", None)

    class Host(Screen):
        _run_bg = PickerScreen._run_bg

        def __init__(self):
            super().__init__()
            self.src = _fixture_source()
            self.inbox = Inbox(self)
            self._bg_threads = set()
            self._bg_cancel = threading.Event()
            self._setup_epoch = self._wt_submenu_epoch = 0

        def on_inbox_updated(self):
            self.inbox.drain_apply()

        def _wt_submenu_verbs(self, _rec):
            return ["Resume"], {}

        def _wt_submenu_dispatch(self, *_args, **_kwargs):
            pass

    class Store:
        def __init__(self, project):
            assert project == "example"

        def cursor(self):
            entered.set()
            assert release.wait(5), "test must release the history reader"
            return HistoryCursor(["history text"])

    monkeypatch.setattr(actions_prompt, "SeedHistory", Store)

    async def run():
        app, host = App(), Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(host)
            rec = dict(_fixture_source().load()[0], mux_live=False, sessionless=False, is_local=eligible)
            menu = SubMenuScreen.open_for(host, rec)
            painted = asyncio.Event()
            menu.call_after_refresh(painted.set)
            await asyncio.wait_for(painted.wait(), 1)
            assert menu.is_mounted
            if eligible:
                async with asyncio.timeout(1):
                    while not entered.is_set():
                        await asyncio.sleep(.01)
            else:
                assert not entered.is_set()
            await asyncio.wait_for(pilot.press("escape"), 1)
            assert app.screen is not menu
            release.set()
            async with asyncio.timeout(1):
                while host._bg_threads:
                    await asyncio.sleep(.01)
            await pilot.pause()
            assert menu._prompt_rec["primary"].seed_store is None
    try:
        asyncio.run(run())
    finally:
        release.set()


def test_history_completion_preserves_new_text_and_cursor(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(context, "_project", "example")
    monkeypatch.setattr(context, "_bootstrap", None)
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)

    class Store:
        def __init__(self, project):
            pass

        def cursor(self):
            assert release.wait(5)
            return HistoryCursor(["history text"])

    monkeypatch.setattr(actions_prompt, "SeedHistory", Store)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 24)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            rec = dict(screen.list_records()[0], mux_live=False, sessionless=False, is_local=True)
            menu = screen._push_wt_submenu(rec)
            await pilot.pause()
            field = menu.query_one(SeedPromptField)
            field.text = "a new draft"
            field.move_cursor((0, 3))
            cursor = field.cursor_location
            release.set()
            for _ in range(20):
                await pilot.pause()
                if field.seed_store is not None:
                    break
            assert isinstance(field.seed_store, Store)
            assert field.text == "a new draft" and field.cursor_location == cursor
            await pilot.press("escape")
    try:
        asyncio.run(run())
    finally:
        release.set()


@pytest.mark.parametrize("refinement", ["remote", "no-resume"])
def test_late_ineligible_prompt_refuses_seedless_launch_and_retains_draft(refinement, monkeypatch):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            rec = dict(screen.list_records()[0], mux_live=False, sessionless=False, is_local=True)
            decisions = []
            screen._decide = decisions.append
            menu = screen._push_wt_submenu(rec)
            await pilot.pause()
            field = menu.query_one(SeedPromptField)
            field.text = "retain this exact draft"
            if refinement == "remote":
                rec["is_local"] = False
                actions, action = list(menu._actions), "Resume"
            else:
                rec["mux_live"] = True
                actions, action = ["Open", "Stop", "Refresh"], "Open"
            menu.refresh_actions(actions, status="verified")
            options = menu.query_one("#sub-list", OptionList)
            options.highlighted = actions.index(action)
            options.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is menu and not decisions
            assert field.text == "retain this exact draft"
            assert menu.query_one("#sub-prompt").display
            await pilot.press("escape")
            await pilot.pause()
            assert not decisions
    asyncio.run(run())


@pytest.mark.parametrize("size", [(118, 24), (80, 24)])
def test_compact_actions_keep_prompt_and_all_options_reachable(size, monkeypatch):
    monkeypatch.setattr(PickerScreen, "_tick", lambda self: None)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.query_one(PickerScreen)
            rec = dict(
                screen.list_records()[0], mux_live=False, sessionless=False,
                is_local=True, cleanup_bucket="conversation", session_count=2,
                ff_eligible=True,
            )
            menu = screen._push_wt_submenu(rec)
            await pilot.pause()
            options = menu.query_one("#sub-list", OptionList)
            assert options.size.height >= 3
            assert options.region.bottom <= menu.query_one("#sub-frame").content_region.bottom
            for label, index in [
                ("Resume", menu._actions.index("Resume")),
                ("Cleanup", menu._actions.index("Cleanup")),
                ("Finalize", menu._actions.index("Finalize")),
                ("No Mux", menu._nomux_index), ("AHP", menu._ahp_index),
            ]:
                options.highlighted = index
                options.scroll_to_highlight()
                await pilot.pause()
                visible = "".join(
                    segment.text for y in range(options.size.height)
                    for segment in options.render_line(y)
                )
                assert label in visible
            assert options.scroll_y > 0
            field = menu.query_one(SeedPromptField)
            field.focus()
            await pilot.pause()
            assert menu.focused is field and field.region.height >= 3
            assert field.size.height >= 1
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not menu and app.result is None
    asyncio.run(run())
