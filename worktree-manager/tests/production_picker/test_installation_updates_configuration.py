"""Real Configuration navigation, contextual consent, durable receipt and refresh."""

import asyncio
import dataclasses
import json
import sys
import threading
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

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


def test_isolated_home_tracks_explicit_profiles_without_a_real_home_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = Path.home()
    profile = tmp_path / "explicit-profile"
    profile.mkdir()
    with monkeypatch.context() as patch:
        patch.setenv("HOME", str(profile))
        patch.setenv("USERPROFILE", str(profile))
        assert Path.home() == profile
        patch.delenv("HOME")
        patch.delenv("USERPROFILE")
        assert Path.home() == sandbox


@pytest.mark.parametrize("kind", ["maintenance", "bootstrap"])
def test_production_automatic_control_cancel_save_and_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from worktree_manager import bootstrap_policy, maintenance_policy

    root = tmp_path / "manager"
    root.mkdir()
    path = root / "config.toml"
    path.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    switch = tmp_path / "selected-hold.json"
    if kind == "bootstrap":
        switch.write_text('{"active":true}', encoding="utf-8")
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(switch))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(ui.engine_client, "accept_inherited_engine_command", lambda: "")
    monkeypatch.setattr(tasks, "ACTION_TIMEOUT", 2)
    monkeypatch.setattr(tasks, "STRICT_OPTIONS_COMMAND_TIMEOUT", 1)
    calls = []
    controller = maintenance_policy if kind == "maintenance" else bootstrap_policy

    def read(runtime, ctx):
        assert threading.current_thread() is not threading.main_thread()
        return "ready", [controller.configuration_row()], "", {}

    def execute(runtime, argv, **kwargs):
        calls.append(list(argv))
        captured = StringIO()
        start = argv.index("worktree_manager.installation_updates") + 1
        with redirect_stdout(captured), redirect_stderr(captured):
            try:
                code = ui.main(list(argv[start:]))
            except SystemExit as error:
                code = error.code
        return code == 0, captured.getvalue()

    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "_exec_list", read)
    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "run_resolved", execute)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            scr._open_cfgmenu()
            await pilot.pause()
            index = next(
                i for i, item in enumerate(scr.pivots)
                if getattr(item.get("pivot"), "name", None) == ui.PIVOT_NAME
            )
            for _ in range(len(scr.pivots) + 1):
                if scr.htab == index:
                    break
                scr._rotate_config_section(1)
                await pilot.pause()
            assert scr.htab == index, "Installation & Updates section was not reachable"
            reg = scr._reg_pivot()
            runtime = scr._pivot_runtime(reg)
            runtime.ensure("")
            await _wait(pilot, lambda: runtime.get("")[0] == "ready")
            await _wait(pilot, lambda: len(scr._task_rows()) == 1)
            scr.sel = ("T", 0)
            scr.refresh()
            await pilot.pause()
            assert "OFF" in "\n".join(item.text.plain for item in scr.build_body(118))
            assert scr._selected_task() is not None, (
                scr.sel, scr.htab, scr._pivot_scope_key(), runtime.get(""),
            )
            scr._open_task_menu()
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
            app.screen.dismiss(1)
            await _wait(pilot, lambda: isinstance(app.screen, ActionConfirmScreen))
            assert ("Enable launch-time" if kind == "maintenance" else "Allow automatic") in app.screen._label
            if kind == "bootstrap":
                assert str(switch) in app.screen._label
            app.screen.action_cancel()
            await pilot.pause()
            assert calls == [] and path.read_bytes() == original
            assert switch.exists() is (kind == "bootstrap")
            scr.sel = ("T", 0)
            scr.refresh()
            await pilot.pause()
            scr._open_task_menu()
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
            app.screen.dismiss(1)
            await _wait(pilot, lambda: isinstance(app.screen, ActionConfirmScreen))
            await _wait(pilot, lambda: app.screen.query_one("#action-confirm-buttons", FocusGroup).has_focus)
            await pilot.press("left", "enter")
            await _wait(
                pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and not app.screen.pending,
            )
            rendered = str(app.screen.query_one("#config-result", Static).render())
            assert "No update or recurring worker was started" in rendered
            app.screen.action_close()
            await _wait(pilot, lambda: runtime.get("")[1][0]["enabled"] == "ON")
            assert len(calls) == 1
            assert controller.inspect_policy().enabled is True
            if kind == "bootstrap":
                assert not switch.exists() and path.read_bytes() == original
            assert "<svg" in app.export_screenshot()

    asyncio.run(run())


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


@pytest.mark.parametrize("ok", [True, False])
def test_production_diagnostic_is_explicit_read_only_and_preserves_outcome(tmp_path, monkeypatch, ok):
    from worktree_manager import doctor_cli

    monkeypatch.setattr(tasks, "ACTION_TIMEOUT", 2)
    monkeypatch.setattr(tasks, "STRICT_OPTIONS_COMMAND_TIMEOUT", 1)
    original = tmp_path / "config.toml"
    original.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    before = original.read_bytes()
    calls = []
    started = threading.Event()
    release = threading.Event()
    row = {
        "id": "worktree-manager", "title": "Worktree Manager", "updatable": True,
        "card": {"title": "Manager", "body": "Installed versions are not live-health proof."},
    }

    def read(runtime, ctx):
        return "ready", [dict(row)], "", {}

    def doctor(rest):
        assert rest == [] and threading.current_thread() is not threading.main_thread()
        print("Native diagnostic report: " + ("checks passed" if ok else "required prerequisite MISSING"))
        return 0 if ok else 1

    def execute(runtime, argv, **kwargs):
        calls.append(list(argv))
        started.set()
        assert release.wait(5), "Diagnostic result was never released"
        assert argv[-2:] == ["doctor", "worktree-manager"]
        captured = StringIO()
        start = argv.index("worktree_manager.installation_updates") + 1
        with redirect_stdout(captured), redirect_stderr(captured):
            code = ui.main(list(argv[start:]))
        return code == 0, captured.getvalue()

    monkeypatch.setattr(ui.engine_client, "accept_inherited_engine_command", lambda: "")
    monkeypatch.setattr(doctor_cli, "cmd_doctor", doctor)
    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "_exec_list", read)
    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "run_resolved", execute)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        try:
            async with app.run_test(size=(118, 40)) as pilot:
                scr = app.query_one(PickerScreen)
                await _wait_for_initial_setup(pilot, scr)
                scr._open_cfgmenu()
                await pilot.pause()
                index = next(
                    i for i, item in enumerate(scr.pivots)
                    if getattr(item.get("pivot"), "name", None) == ui.PIVOT_NAME
                )
                for _ in range(len(scr.pivots) + 1):
                    if scr.htab == index:
                        break
                    scr._rotate_config_section(1)
                    await pilot.pause()
                assert scr.htab == index
                reg = scr._reg_pivot()
                runtime = scr._pivot_runtime(reg)
                runtime.ensure("")
                await _wait(pilot, lambda: runtime.get("")[0] == "ready" and bool(scr._task_rows()))
                assert not calls and not started.is_set() and original.read_bytes() == before
                scr.sel = ("T", 0)
                scr.refresh()
                await pilot.pause()
                visible = [action for action in reg.actions if pivots.entry_matches(action.when, row)]
                chosen = next(i for i, action in enumerate(visible) if action.key == "doctor")
                scr._open_task_menu()
                await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
                assert not calls
                app.screen.dismiss(chosen)
                await _wait(
                    pilot, lambda: isinstance(app.screen, ConfigurationResultScreen) and started.is_set(),
                )
                pending = str(app.screen.query_one("#config-result", Static).render())
                assert "read-only" in pending.lower() and "Applying configuration" not in pending
                assert app.screen.pending
                release.set()
                await _wait(pilot, lambda: not app.screen.pending)
                rendered = str(app.screen.query_one("#config-result", Static).render())
                assert "Native diagnostic report" in rendered
                assert ("Diagnostics completed" if ok else "Diagnostics failed or are incomplete") in rendered
                assert "configuration saved" not in rendered.lower()
                assert original.read_bytes() == before and len(calls) == 1
                app.screen.action_close()
                await _wait(pilot, lambda: not isinstance(app.screen, ConfigurationResultScreen))
                assert app.query_one(PickerScreen) is scr and scr.htab == index
        finally:
            release.set()

    asyncio.run(run())


@pytest.mark.parametrize("ready", [True, False])
def test_diagnostic_runs_native_report_through_actual_child_transport(tmp_path, monkeypatch, ready):
    home = tmp_path / "home"
    home.mkdir()
    sentinel = home / "config.toml"
    sentinel.write_text("unchanged = true\n", encoding="utf-8")
    wrapper = tmp_path / "diagnostic.py"
    wrapper.write_text(
        "from pathlib import Path\n"
        "from types import SimpleNamespace as N\n"
        "from worktree_manager import doctor_cli as d, installation_updates as ui\n"
        f"d.detect_baseline=lambda:[N(name='git',present={ready!r},satisfied={ready!r},"
        "optional=False,version='1' if " + repr(ready) + " else None,min_required='1',path=None,notes=None)]\n"
        "d.core_status=lambda:N(state='installed',runtime_dir='fixture',runtime_present=True,"
        "venv_present=True,binstub='fixture',installed=True)\n"
        "d.self_status=lambda:N(installed_version='fixture',binstub='fixture',root=Path('fixture'))\n"
        "d.coverage=lambda:N(source_kind='checkout',ok=True,uncovered=(),phantom=(),published_prereq_gaps=())\n"
        "d.mis_registered_repos=lambda:[]\n"
        f"d.daemon_health.default_root=lambda:Path({str(home / '.worktree-manager')!r})\n"
        "d.daemon_health.mux_daemon_cutover._iter_mux_daemon_pids=lambda:set()\n"
        "raise SystemExit(ui.main())\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(ui.engine_client, "_INHERITED_ENGINE_COMMAND", None)
    reg = ui.configuration_pivot()
    action = next(action for action in reg.actions if action.key == "doctor")
    assert pivots.entry_matches(action.when, {"id": "worktree-manager"})
    assert not pivots.entry_matches(action.when, {"id": "guide"})
    argv = list(pivots.format_template(action.run, {"id": "worktree-manager"}))
    argv = [sys.executable, "-B", str(wrapper), *argv[4:]]
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    runtime = tasks.RegisteredPivotRuntime(reg)
    ok, message = runtime.run_resolved(argv, preserve_output=True)
    assert ok is ready, message
    assert "doctor" in message and "prerequisites:" in message
    assert ("prerequisites satisfied" if ready else "MISSING") in message
    assert before == {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}


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


@pytest.mark.parametrize("operation", ["update", "doctor"])
def test_selected_cell_context_survives_actual_configuration_transport(tmp_path, monkeypatch, operation):
    import update_driver
    from _installation_context_fixtures import namespaced_fixture, write_json
    from worktree_manager import engine_client
    from worktree_manager.production_picker.picker_tui import pivot_list_command

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
    observation = tmp_path / "engine-observation.json"
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
        f" pathlib.Path({str(observation)!r}).write_text(json.dumps({{'context':pointer,'arguments':sys.argv[1:],'has_custody':bool(os.environ.get('UPDATE_DRIVER_WITNESS'))}}))\n"
    )
    wrapper = tmp_path / "inventory.py"
    wrapper.write_text(
        "import json,os,sys,update_driver\n"
        "from pathlib import Path\n"
        "from worktree_manager import agent_plugin_runtime as apr,doctor_cli,installation_updates as ui\n"
        f"apr._canonical_os_profile=lambda environment:Path({str(home)!r})\n"
        f"apr._durable_home=lambda ic,environment:Path({str(home / '.copilot-extensions')!r})\n"
        "ic=apr._load_installation_context()\n"
        "apr._load_installation_context=lambda:ic\n"
        # This transport fixture models an ordinary profile, not native WSL registration.
        "ic._is_wsl_guest=lambda:False\n"
        "def diagnostic(args):\n"
        " assert args == []\n"
        " print(json.dumps({'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT')}))\n"
        " return 0\n"
        "doctor_cli.cmd_doctor=diagnostic\n"
        "original=update_driver.runtime_command\n"
        "def command(module,args=()):\n"
        " if module=='worktree_manager': return (sys.executable,str(Path(__file__).resolve()),'execute',*args),{}\n"
        " return original(module,args)\n"
        "update_driver.runtime_command=command\n"
        "if sys.argv[1:2]==['execute']:\n"
        " from worktree_manager import __main__ as manager\n"
        " raise SystemExit(manager.main(sys.argv[2:]))\n"
        "raise SystemExit(ui.main())\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", [sys.executable, str(engine)])
    reg = ui.configuration_pivot()
    assert "--engine-context" in reg.list_cmd
    assert "COPILOT_EXTENSIONS_CONTEXT" not in tasks._child_process_env()
    reg = dataclasses.replace(reg, list_cmd=(sys.executable, "-B", str(wrapper), *reg.list_cmd[4:]))
    runtime = tasks.RegisteredPivotRuntime(reg)
    observed = {}
    original_run = pivot_list_command.run_caller_owned

    def observe(*args, **kwargs):
        result = original_run(*args, **kwargs)
        observed["stderr"] = result.stderr
        return result

    monkeypatch.setattr(pivot_list_command, "run_caller_owned", observe)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    state, rows, error, summary = runtime._exec_list({})
    if state != "ready":
        pytest.fail(error + "\n" + observed.get("stderr", "no captured provider diagnostics"))
    row = next(row for row in rows if row["id"] == "guide")
    assert row["payload"] == "cell" and row["enabled"] == "active"
    assert str(install.parent) in row["source"]
    action = next(action for action in reg.actions if action.key == operation)
    target = "guide" if operation == "update" else "worktree-manager"
    argv = list(pivots.format_template(action.run, {"id": target}))
    argv = [sys.executable, "-B", str(wrapper), *argv[4:]]
    ok, message = runtime.run_resolved(argv, preserve_output=True)
    assert ok, message
    if operation == "doctor":
        assert json.loads(message) == {"context": str(install)}
        assert before == {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
        return
    receipt = json.loads(message.splitlines()[0])
    request = update_driver.wait(
        receipt["request_id"], root=home / ".copilot-extensions/update-driver", timeout=30,
    )
    assert request.state == "completed" and request.exit_code == 0
    seen = json.loads(observation.read_text())
    assert seen["context"] == str(install) and seen["has_custody"] is True
    assert seen["arguments"] == ["update", "--no-manager", "--target", "guide"]
    assert all(path.read_bytes() == body for path, body in before.items())


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
