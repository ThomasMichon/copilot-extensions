"""Real Configuration navigation, contextual consent, durable receipt and refresh."""

import asyncio
import dataclasses
import json
import sys
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


def test_unresponsive_engine_preserves_manager_rows_through_production_list(tmp_path, monkeypatch):
    import time

    engine = tmp_path / "slow_engine.py"
    engine.write_text("import time; time.sleep(10)\n")
    command = tmp_path / "inventory.py"
    command.write_text(
        "from worktree_manager import installation_updates as ui\n"
        "ui.ENGINE_INVENTORY_TIMEOUT = 0.2\n"
        "raise SystemExit(ui.main())\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    reg = dataclasses.replace(ui.configuration_pivot(), list_cmd=(
        sys.executable, str(command), "--engine-argv",
        json.dumps([sys.executable, str(engine)]), "list",
    ))
    runtime = tasks.RegisteredPivotRuntime(reg)
    started = time.monotonic()
    state, rows, error, summary = runtime._exec_list({})
    assert state == "ready", error
    assert any(row["id"] == "worktree-manager" for row in rows)
    assert any("Engine inventory unavailable" in message for message in summary["errors"])
    assert time.monotonic() - started < tasks.LIST_TIMEOUT
    assert ui.ENGINE_INVENTORY_TIMEOUT < tasks.LIST_TIMEOUT


def test_selected_cell_context_survives_actual_configuration_transport(tmp_path, monkeypatch):
    from _installation_context_fixtures import namespaced_fixture, write_json
    from worktree_manager import engine_client

    home = tmp_path / "home"
    home.mkdir()
    install, _python = namespaced_fixture(home, windows=False)
    write_json(home / ".copilot-extensions" / "installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True},
    })
    legacy = home / ".agent-worktrees" / "registry.json"
    cell = install.parent / "registry.json"
    write_json(legacy, {"version": "legacy", "enabled": "inactive"})
    write_json(cell, {"version": "cell", "enabled": "active"})
    engine = tmp_path / "engine.py"
    engine.write_text(
        "import json,os,pathlib,sys\n"
        "pointer=os.environ.get('COPILOT_EXTENSIONS_CONTEXT')\n"
        f"root=pathlib.Path(pointer).parent if pointer else pathlib.Path({str(legacy.parent)!r})\n"
        "state=json.loads((root/'registry.json').read_text())\n"
        "if '--inventory' in sys.argv:\n"
        " print(json.dumps({'contract':1,'execution_source':str(root),'entries':["
        "{'id':'guide','title':'Guide','kind':'none','enabled':state['enabled'],'installed':True,"
        "'payload':state['version'],'selected':'not applicable','running':'not applicable',"
        "'source':str(root),'updatable':True,'limitation':''}]}))\n"
        "else:\n"
        " assert '--target' in sys.argv and pointer\n"
        " print(json.dumps({'request_id':'cell-request','state':'pending','exit_code':None,'context':pointer}))\n"
    )
    wrapper = tmp_path / "inventory.py"
    wrapper.write_text(
        "from pathlib import Path\n"
        "from worktree_manager import agent_plugin_runtime as apr,installation_updates as ui\n"
        f"apr._canonical_os_profile=lambda environment:Path({str(home)!r})\n"
        f"apr._durable_home=lambda ic,environment:Path({str(home / '.copilot-extensions')!r})\n"
        "raise SystemExit(ui.main())\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", [sys.executable, str(engine)])
    reg = ui.configuration_pivot()
    assert "--engine-context" in reg.list_cmd
    assert "COPILOT_EXTENSIONS_CONTEXT" not in tasks._child_process_env()
    reg = dataclasses.replace(reg, list_cmd=(sys.executable, str(wrapper), *reg.list_cmd[3:]))
    runtime = tasks.RegisteredPivotRuntime(reg)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    state, rows, error, summary = runtime._exec_list({})
    assert state == "ready", error
    row = next(row for row in rows if row["id"] == "guide")
    assert row["payload"] == "cell" and row["enabled"] == "active"
    assert str(install.parent) in row["source"]
    update = next(action for action in reg.actions if action.key == "update")
    argv = list(pivots.format_template(update.run, {"id": "guide"}))
    argv = [sys.executable, str(wrapper), *argv[3:]]
    ok, message = runtime.run_resolved(argv, preserve_output=True)
    assert ok, message
    assert "cell-request" in message and str(install) in message
    assert before == {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}


def test_catalog_failure_uses_production_warning_banner(monkeypatch):
    from plugin_resolve import inventory as plugin_inventory
    from worktree_manager.production_picker.picker_tui.engine_views import TasksView

    def fail(*args, **kwargs):
        raise ValueError("catalog metadata is unavailable")

    monkeypatch.setattr(plugin_inventory, "installed_item", fail)
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: {"contract": 1, "entries": []})
    monkeypatch.setattr(ui, "_operations", lambda **kwargs: {})
    summary = ui.inventory()["summary"]
    banner = TasksView(None)._banner_line(summary, 118)
    assert banner is not None and "Known catalog unavailable" in banner.plain


def test_read_only_candidate_never_launches_native_inventory_or_update(tmp_path, monkeypatch):
    import update_driver
    from worktree_manager import picker_review

    monkeypatch.setattr(picker_review, "_review", picker_review.Review(
        tmp_path, tmp_path, "fixture-candidate", ("synthetic-owner",),
    ))
    monkeypatch.setattr(ui.engine_client, "run_json", lambda *a, **k: pytest.fail("preview read live inventory"))
    monkeypatch.setattr(update_driver, "enqueue", lambda *a, **k: pytest.fail("preview enqueued update"))
    runtime = tasks.RegisteredPivotRuntime(ui.configuration_pivot())
    runtime._run_list("")
    state, rows, message = runtime.get("")
    assert state == "error" and not rows
    assert "Read-only candidate" in message
    ok, message = runtime.run_resolved(
        [*ui.configuration_pivot().list_cmd[:-1], "update", "worktree-manager"],
        preserve_output=True,
    )
    assert not ok and "Read-only candidate" in message
