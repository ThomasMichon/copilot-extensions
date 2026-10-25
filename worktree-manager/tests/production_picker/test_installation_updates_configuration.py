"""Real Configuration navigation, contextual consent, durable receipt and refresh."""

import asyncio
import json
import threading

import pytest
from test_config_actions import _wait
from test_picker_tui import _fixture_source, _wait_for_initial_setup
from textual.widgets import Static

from worktree_manager import installation_updates as ui
from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_action_confirm import ActionConfirmScreen
from worktree_manager.production_picker.picker_tui.engine_dialogs import TaskMenuScreen
from worktree_manager.production_picker.picker_tui.engine_focus import FocusGroup
from worktree_manager.production_picker.picker_tui.configuration_form import ConfigurationResultScreen

pytestmark = pytest.mark.contract("worktree_manager.configuration_backstage")


def test_production_configuration_navigation_target_consent_and_refresh(tmp_path, monkeypatch):
    root = tmp_path / "pivots"
    root.mkdir()
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(root))
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugins))
    calls = []
    row = {
        "id": "guide", "title": "Fixture guide", "payload": "1", "selected": "not applicable",
        "running": "not applicable", "enabled": "inactive", "activity": "idle", "updatable": True,
        "card": {"title": "Fixture guide", "body": "Synthetic source and limitations"},
    }

    def read(runtime, ctx):
        assert threading.current_thread() is not threading.main_thread()
        assert runtime.pivot.name == ui.PIVOT_NAME
        return "ready", [dict(row)], "", {}

    # Keep the production runtime/thread, context, selection and action paths;
    # replace only its external owner process with an isolated synthetic peer.
    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "_exec_list", read)

    def execute(runtime, argv, **kwargs):
        calls.append(list(argv))
        assert argv[-2:] == ["update", "guide"]
        row.update(payload="2", activity="queued")
        return True, json.dumps({"request_id": "fixture-request", "state": "pending", "exit_code": None})

    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "run_resolved", execute)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            scr._open_cfgmenu()
            await pilot.pause()
            index = next(i for i, item in enumerate(scr.pivots) if getattr(item.get("pivot"), "name", None) == ui.PIVOT_NAME)
            while scr.htab != index:
                scr._rotate_config_section(1)
                await pilot.pause()
            reg = scr._reg_pivot()
            runtime = scr._pivot_runtime(reg)
            runtime.ensure("")
            await _wait(pilot, lambda: runtime.get("")[0] == "ready")
            assert scr._in_configuration()
            scr.sel = ("T", 0)
            scr.refresh()
            await pilot.pause()
            body = "\n".join(item.text.plain for item in scr.build_body(118))
            assert "Fixture guide" in body and "Installation & Updates" in scr.tab_bar(118, True).plain
            scr._open_task_menu()
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
            app.screen.dismiss(1)
            await _wait(pilot, lambda: isinstance(app.screen, ActionConfirmScreen))
            assert "Fixture guide" in app.screen._label
            assert "this machine only" in app.screen._label
            app.screen.action_cancel()
            await pilot.pause()
            assert calls == []
            scr._open_task_menu()
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
            app.screen.dismiss(1)
            await _wait(pilot, lambda: isinstance(app.screen, ActionConfirmScreen))
            group = app.screen.query_one("#action-confirm-buttons", FocusGroup)
            await _wait(pilot, lambda: group.has_focus)
            await pilot.press("left", "enter")
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and not app.screen.pending)
            text = str(app.screen.query_one("#config-result", Static).render())
            assert "Request accepted" in text and "not yet complete" in text
            app.screen.action_close()
            await _wait(pilot, lambda: runtime.get("")[1][0]["payload"] == "2")
            assert len(calls) == 1
            assert row["activity"] == "queued"  # Dismissing observation is not cancellation.
            assert "<svg" in app.export_screenshot()

    asyncio.run(run())


def test_native_pivot_uses_existing_contract_and_exact_row_target():
    reg = ui.configuration_pivot()
    assert reg.placement == "config" and reg.account_scoped
    update = next(action for action in reg.actions if action.key == "update")
    assert update.confirm
    assert pivots.format_template(update.run, {"id": "guide"})[-2:] == ["update", "guide"]
    assert not pivots.entry_matches(update.when, {"updatable": False})
