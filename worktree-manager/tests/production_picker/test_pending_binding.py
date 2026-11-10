"""Consumer startup admission precedes engine authority, never substitutes for it."""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import threading
import sys
from types import SimpleNamespace

import pytest

from worktree_manager.production_picker.picker_tui import engine
from worktree_manager.production_picker.picker_tui.engine_loading import PickerBinding
from worktree_manager.first_run import SetupState


async def _click(pilot, selector):
    await pilot.pause()
    widget = pilot.app.screen.query_one(selector)
    widget.focus(scroll_visible=False)
    widget.scroll_visible(animate=False, immediate=True, top=True)
    await pilot.pause()
    assert await pilot.click(selector, offset=(1, 1)), (
        widget.region, widget.visible_region, pilot.app.focused,
    )


@pytest.mark.parametrize("front_door", [False, True])
def test_pending_binding_paints_and_escapes_before_slow_bootstrap(front_door):
    async def exercise():
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        activated = []

        def prepare():
            entered.set()
            try:
                assert release.wait(3)
                return PickerBinding(object(), True, lambda: activated.append(True))
            finally:
                finished.set()

        app = engine.PickerApp(
            None, prepare=prepare, project_label="example", front_door=front_door,
        )
        try:
            async with app.run_test() as pilot:
                assert await asyncio.to_thread(entered.wait, 1)
                assert not app.screen.query(engine.PickerScreen)
                assert "example" in str(app.screen.query_one("#binding-status").render())
                await pilot.press("escape")
                assert app.result is None
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 1)
        assert activated == []

    asyncio.run(exercise())


def test_binding_failure_is_visible_and_preserved():
    async def exercise():
        def prepare():
            raise RuntimeError("unknown project: example")

        app = engine.PickerApp(None, prepare=prepare, project_label="example")
        async with app.run_test() as pilot:
            for _ in range(30):
                if app.binding_error is not None:
                    break
                await pilot.pause(0.02)
            assert "unknown project" in str(app.screen.query_one("#binding-status").render())
            assert not app.screen.query(engine.PickerScreen)
            await pilot.press("escape")
        assert str(app.binding_error) == "unknown project: example"

    asyncio.run(exercise())


def test_front_door_uses_real_setup_state_and_reload_without_operational_source():
    async def exercise():
        calls = []

        def prepare(**kwargs):
            calls.append(kwargs)
            return SetupState(False, (), "Core absent. No launchable projects.")

        app = engine.PickerApp(None, prepare=prepare, front_door=True)
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            assert "Core absent" in str(app.query_one("#setup-status").render())
            assert not app.query(engine.PickerScreen)
            await _click(pilot, "#setup-reload")
            for _ in range(50):
                if len(calls) == 2 and app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            assert calls == [{}, {"project_override": None, "auto_open": False}]
            assert not app.query(engine.PickerScreen)
            await _click(pilot, "#setup-quit")

    asyncio.run(exercise())


@pytest.mark.parametrize("initial", ["partial", "empty", "malformed"])
def test_observed_registry_recovery_requires_explicit_project_selection(
    monkeypatch, tmp_path, initial,
):
    from worktree_manager import first_run, harness_state
    from worktree_manager.production_picker import context, runner
    from worktree_manager.production_picker.picker_tui import setup_home

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    home = tmp_path / "home"
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    projects = registry / "projects.yaml"
    projects.write_text(
        "projects: [broken]\n" if initial == "malformed" else
        "projects:\n  example: {}\n" if initial == "partial" else
        "projects: {}\n",
        encoding="utf-8",
    )
    (registry / "repos.yaml").write_text(
        "repos:\n  example:\n    class: worktree\n", encoding="utf-8",
    )
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(home))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "WORKTREE_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(home / "manager"))
    monkeypatch.chdir(home)
    available = initial != "partial"
    monkeypatch.setattr(
        first_run, "core_status",
        lambda: SimpleNamespace(state="installed" if available else "partial"),
    )
    monkeypatch.setattr(first_run, "engine_available", lambda: available)

    def forbidden(*args, **kwargs):
        pytest.fail("unselected owner work must not run while observing or reloading setup")

    monkeypatch.setattr(harness_state, "build_projects", forbidden)
    monkeypatch.setattr(runner.engine_group_b, "picker_bootstrap", forbidden)
    monkeypatch.setattr(runner, "_start_housekeeping", forbidden)
    monkeypatch.setattr(runner, "_start_anchor_heal_check", forbidden)
    monkeypatch.setattr(setup_home, "diagnostic_argv", forbidden)
    monkeypatch.setattr(setup_home, "run_caller_owned", forbidden)

    def snapshot():
        return {
            str(path.relative_to(home)): path.read_bytes() if path.is_file() else None
            for path in home.rglob("*")
        }

    async def exercise(app):
        nonlocal available
        app.animation_level = "none"
        before = snapshot()
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            summary = str(app.query_one("#setup-status").render())
            if initial == "partial":
                assert "core: partial" in summary
                assert app.query_one("#setup-project-0").disabled
            else:
                assert not app.query("#setup-project-0")
                if initial == "malformed":
                    assert "Project registry unavailable" in summary
                    assert "No launchable" not in summary
                else:
                    assert "No launchable" in summary
            assert context.project_bootstrap() is None
            assert not app.query(engine.PickerScreen)
            assert snapshot() == before

            # Only the fixture owner repairs the input; Reload must reread it.
            projects.write_text("projects:\n  example: {}\n", encoding="utf-8")
            available = True
            corrected = snapshot()
            await _click(pilot, "#setup-reload")
            for _ in range(50):
                if app.query("#setup-project-0") and not app.query_one(
                    "#setup-project-0"
                ).disabled:
                    break
                await pilot.pause(.02)
            assert not app.query_one("#setup-project-0").disabled
            assert "core: installed" in str(app.query_one("#setup-status").render())
            assert app.binding_error is None
            assert context.project_bootstrap() is None
            assert not app.query(engine.PickerScreen)
            assert snapshot() == corrected
            await _click(pilot, "#setup-quit")
        assert snapshot() == corrected

    monkeypatch.setattr(engine.PickerApp, "run", lambda app: asyncio.run(exercise(app)))
    assert runner.run("", front_door=True) is None


@pytest.mark.parametrize("size", [(48, 18), (80, 24)])
def test_missing_source_guides_are_navigable_without_commands_or_writes(
    monkeypatch, tmp_path, size,
):
    from textual.containers import VerticalScroll
    from textual.widgets import Button, Static

    from worktree_manager.production_picker.picker_tui import setup_home

    monkeypatch.chdir(tmp_path)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("opening, scrolling or returning from a guide must not run a command")

    monkeypatch.setattr(setup_home, "diagnostic_argv", forbidden)
    monkeypatch.setattr(setup_home, "run_caller_owned", forbidden)

    async def exercise():
        observations = []

        def prepare(**kwargs):
            observations.append(kwargs)
            return SetupState(False, None, "Project registry unavailable. Reload to retry.")

        app = engine.PickerApp(None, prepare=prepare, front_door=True)
        app.animation_level = "none"
        async with app.run_test(size=size) as pilot:
            for _ in range(50):
                if app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            for index, (action, expected) in enumerate((
                ("core", "worktree-manager setup --apply"),
                ("machine", "There is no Add-machine writer"),
                ("repository", "It does not inspect or qualify"),
                ("plugins", "In-app plugin enablement is unavailable"),
            )):
                await _click(pilot, f"#setup-guide-{action}")
                assert isinstance(app.screen, setup_home.SetupGuideScreen)
                assert "Setup / Doctor" in str(app.screen.query(Static).first().render())
                body = app.screen.query(Static).last()
                assert expected in str(body.render())
                app.screen.query_one(VerticalScroll).focus()
                await pilot.press("pagedown", "pagedown")
                scroll = app.screen.query_one(VerticalScroll)
                assert scroll.scroll_y > 0 or scroll.max_scroll_y == 0
                if size == (48, 18):
                    assert scroll.max_scroll_y > 0
                if index % 2:
                    close = next(
                        button for button in app.screen.query(Button)
                        if str(button.label) == "Return to Setup / Doctor"
                    )
                    close.focus(scroll_visible=False)
                    await pilot.press("enter")
                else:
                    await pilot.press("escape")
                await pilot.pause()
                assert app.query("#setup-status")
                assert not app.query(engine.PickerScreen)
            assert observations == [{}]
            await _click(pilot, "#setup-reload")
            for _ in range(50):
                if len(observations) == 2 and app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            assert observations[-1] == {"project_override": None, "auto_open": False}
            await _click(pilot, "#setup-quit")
        assert list(tmp_path.iterdir()) == []

    asyncio.run(exercise())


def test_front_door_project_selection_is_explicit_and_binding_refusal_can_retry():
    async def exercise():
        calls = []

        def prepare(**kwargs):
            calls.append(kwargs)
            if kwargs.get("project_override"):
                raise RuntimeError("installed engine lacks picker-bootstrap")
            return SetupState(True, ("alpha", "beta"), "Choose a project")

        app = engine.PickerApp(None, prepare=prepare, front_door=True)
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-project-1"):
                    break
                await pilot.pause(.02)
            assert calls == [{}]
            await _click(pilot, "#setup-project-1")
            for _ in range(50):
                if app.query("#setup-status") and "lacks" in str(
                    app.query_one("#setup-status").render()
                ):
                    break
                await pilot.pause(.02)
            assert calls[-1] == {"project_override": "beta", "auto_open": False}
            assert "lacks picker-bootstrap" in str(app.query_one("#setup-status").render())
            assert app.binding_error is None
            assert not app.query(engine.PickerScreen)
            await _click(pilot, "#setup-reload")
            for _ in range(50):
                if app.query("#setup-project-1"):
                    break
                await pilot.pause(.02)
            assert app.query("#setup-project-1")
            await pilot.press("escape")

    asyncio.run(exercise())


def test_owner_loss_before_delivery_never_activates_binding():
    async def exercise():
        lost = False
        activated = []

        def prepare():
            nonlocal lost
            lost = True
            return PickerBinding(object(), True, lambda: activated.append(True))

        app = engine.PickerApp(None, prepare=prepare, lifetime_cancelled=lambda: lost)
        async with app.run_test() as pilot:
            await pilot.pause(.1)
        assert activated == []
        assert app.result is None

    asyncio.run(exercise())


@pytest.mark.parametrize("action,exit_code", [("setup", 0), ("doctor", 7)])
def test_setup_command_receipt_preserves_real_child_output_and_return(
    monkeypatch, tmp_path, action, exit_code,
):
    from worktree_manager.production_picker.picker_tui import setup_home

    script = tmp_path / "diagnostic.py"
    script.write_text(
        "import os, sys\nprint('first\\nlast')\n"
        "print('installation scope=' + os.environ.get('COPILOT_EXTENSIONS_CONTEXT', '(missing)'))\n"
        f"print('diagnostic detail', file=sys.stderr)\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )
    commands = []
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "fixture-context")

    def command(selected):
        commands.append(selected)
        return [sys.executable, "-B", str(script)]

    monkeypatch.setattr(setup_home, "diagnostic_argv", command)

    async def exercise():
        calls = []

        def prepare(**kwargs):
            calls.append(kwargs)
            return SetupState(False, (), "Core absent")

        app = engine.PickerApp(None, prepare=prepare, front_door=True)
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-plan"):
                    break
                await pilot.pause(.02)
            assert commands == []
            await _click(pilot, "#setup-plan" if action == "setup" else "#setup-doctor")
            for _ in range(200):
                if app.screen.query("#command-result-close") and not app.screen.query_one(
                    "#command-result-close"
                ).disabled:
                    break
                await pilot.pause(.02)
            text = str(app.screen.query_one("#command-result").render())
            assert f"Exit status: {exit_code}" in text
            assert commands == [action]
            assert (
                "installation scope=fixture-context" if action == "setup" else
                "installation scope=(missing)"
            ) in text
            assert "first" in text and "last" in text and "diagnostic detail" in text
            assert ("Command completed" if exit_code == 0 else "Command failed") in text
            await pilot.press("escape")
            for _ in range(50):
                if len(calls) == 2 and app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            assert calls[-1] == {"project_override": None, "auto_open": False}
            assert not app.query(engine.PickerScreen)
            await pilot.press("escape")

    asyncio.run(exercise())


def test_fresh_home_real_setup_plan_is_read_only(monkeypatch, tmp_path):
    from worktree_manager import first_run

    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.chdir(home)
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(home / "manager"))
    monkeypatch.setenv("PATH", "")
    for key in (
        "AGENT_WORKTREES_ENGINE_COMMAND", "AGENT_WORKTREES_ENGINE_INSTALL_ROOT",
        "AGENT_WORKTREES_ENGINE_VERSION", "AGENT_WORKTREES_INSTALL_DIR",
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
    ):
        monkeypatch.delenv(key, raising=False)

    async def exercise():
        app = engine.PickerApp(
            None, prepare=lambda **_: first_run.observe(), front_door=True,
        )
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-plan"):
                    break
                await pilot.pause(.02)
            assert not app.query(engine.PickerScreen)
            await _click(pilot, "#setup-guide-core")
            app.screen.query_one("#setup-guide-plan").focus()
            await pilot.press("enter")
            for _ in range(300):
                if app.screen.query("#command-result-close") and not app.screen.query_one(
                    "#command-result-close"
                ).disabled:
                    break
                await pilot.pause(.02)
            text = str(app.screen.query_one("#command-result").render())
            assert "plan / dry-run" in text
            assert "nothing was changed" in text
            assert "Exit status: 1" in text
            await pilot.press("escape")
            for _ in range(50):
                if app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            await _click(pilot, "#setup-reload")
            for _ in range(50):
                if app.query("#setup-status"):
                    break
                await pilot.pause(.02)
            await pilot.press("escape")
        assert list(home.iterdir()) == []

    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="real child profile selection uses Windows USERPROFILE")
def test_namespaced_only_home_and_actual_setup_report_use_same_scope(
    monkeypatch, tmp_path, record_property,
):
    import json
    import time

    from _installation_context_fixtures import namespaced_fixture
    from worktree_manager import first_run

    home = tmp_path / "cell-home"
    home.mkdir()
    context, _ = namespaced_fixture(home, windows=True)
    (home / ".copilot-extensions" / "installation-mode.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True},
    }), encoding="utf-8")
    for key in ("HOME", "USERPROFILE", "AGENT_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(home / "manager"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(first_run, "engine_available", lambda: False)
    monkeypatch.chdir(home)
    assert not (home / ".agent-worktrees").exists()

    def snapshot():
        return {
            str(path.relative_to(home)): path.read_bytes() if path.is_file() else None
            for path in home.rglob("*")
        }

    before = snapshot()
    started = time.monotonic()
    state = first_run.observe()
    record_property("namespaced_observation_seconds", time.monotonic() - started)
    assert "core: installed" in state.summary

    async def exercise():
        app = engine.PickerApp(None, prepare=lambda **_: state, front_door=True)
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-plan"):
                    break
                await pilot.pause(.02)
            assert "core: installed" in str(app.query_one("#setup-status").render())
            await _click(pilot, "#setup-plan")
            for _ in range(300):
                if not app.screen.query_one("#command-result-close").disabled:
                    break
                await pilot.pause(.02)
            text = str(app.screen.query_one("#command-result").render())
            assert "plan / dry-run" in text
            assert "already installed" in text
            assert "Exit status: 1" in text
            await pilot.press("escape")
            for _ in range(50):
                if app.query("#setup-quit"):
                    break
                await pilot.pause(.02)
            await _click(pilot, "#setup-quit")
        assert snapshot() == before

    asyncio.run(exercise())


@pytest.mark.parametrize("real_child", [False, True])
def test_diagnostic_timeout_keeps_partial_streams_without_exit_status(
    monkeypatch, tmp_path, real_child,
):
    from worktree_manager.production_picker.picker_tui import setup_home

    if real_child:
        script = tmp_path / "slow-diagnostic.py"
        script.write_text(
            "import sys, time\nprint('report before stall', flush=True)\n"
            "print('detail before stall', file=sys.stderr, flush=True)\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            setup_home, "diagnostic_argv", lambda _: [sys.executable, "-B", str(script)],
        )
        monkeypatch.setattr(setup_home, "DIAGNOSTIC_TIMEOUT", 5.0)
    else:
        def timeout(argv, **kwargs):
            raise subprocess.TimeoutExpired(
                argv, 5, output=b"report before stall \xff", stderr=b"detail before stall",
            )
        monkeypatch.setattr(setup_home, "run_caller_owned", timeout)

    async def exercise():
        app = engine.PickerApp(
            None, prepare=lambda **_: SetupState(False, (), "Core absent"), front_door=True,
        )
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-plan"):
                    break
                await pilot.pause(.02)
            await _click(pilot, "#setup-plan")
            for _ in range(400):
                if not app.screen.query_one("#command-result-close").disabled:
                    break
                await pilot.pause(.02)
            text = str(app.screen.query_one("#command-result").render())
            assert "timed out" in text
            assert "stdout (partial)" in text and "report before stall" in text
            assert "stderr (partial)" in text and "detail before stall" in text
            assert "Exit status:" not in text
            await pilot.press("escape")
            await pilot.pause()
            await _click(pilot, "#setup-quit")

    asyncio.run(exercise())


def test_setup_cleanup_failure_retains_custody_and_never_retries(monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError
    from worktree_manager.production_picker.picker_tui import setup_home

    calls = []
    error = OwnedCleanupError(None)

    def fail(*args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(setup_home, "run_caller_owned", fail)

    async def exercise():
        app = engine.PickerApp(
            None, prepare=lambda **_: SetupState(False, (), "Core absent"), front_door=True,
        )
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-plan"):
                    break
                await pilot.pause(.02)
            await _click(pilot, "#setup-plan")
            for _ in range(50):
                if not app.screen.query_one("#command-result-close").disabled:
                    break
                await pilot.pause(.02)
            assert "unconfirmed" in str(app.screen.query_one("#command-result").render())
            await pilot.press("escape")
            await pilot.pause()
            assert app.binding_error is error
            assert app.query_one("#setup-plan").disabled
            assert "No retry" in str(app.query_one("#setup-status").render())
            assert len(calls) == 1
            await _click(pilot, "#setup-quit")

    asyncio.run(exercise())


def test_real_bare_entry_composes_front_door_and_binding_retry(monkeypatch):
    from worktree_manager import __main__ as cli, first_run, process_priority
    from worktree_manager.production_picker import runner

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(sys, "stdout", Terminal())
    monkeypatch.setattr(process_priority, "raise_current_process_priority", lambda: None)
    monkeypatch.setattr(process_priority, "restore_normal_process_priority", lambda: None)
    monkeypatch.setattr(
        first_run, "observe",
        lambda: SetupState(True, ("alpha", "beta"), "Choose a project"),
    )
    monkeypatch.setattr(runner.engine_client, "engine_available", lambda: True)

    def refuse(_):
        raise RuntimeError("fixture engine capability refused")

    monkeypatch.setattr(runner.engine_group_b, "picker_bootstrap", refuse)

    async def exercise(app):
        async with app.run_test() as pilot:
            for _ in range(50):
                if app.query("#setup-project-0"):
                    break
                await pilot.pause(.02)
            await _click(pilot, "#setup-project-0")
            for _ in range(50):
                if app.query("#setup-status") and "capability refused" in str(
                    app.query_one("#setup-status").render()
                ):
                    break
                await pilot.pause(.02)
            assert "capability refused" in str(app.query_one("#setup-status").render())
            assert app.binding_error is None
            assert not app.query(engine.PickerScreen)
            await _click(pilot, "#setup-reload")
            for _ in range(50):
                if app.query("#setup-project-0"):
                    break
                await pilot.pause(.02)
            assert app.query("#setup-project-0")
            await _click(pilot, "#setup-quit")

    monkeypatch.setattr(engine.PickerApp, "run", lambda app: asyncio.run(exercise(app)))
    assert cli.main([]) == 0


def test_successful_binding_mounts_operational_widget_in_original_app(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_local

    class Source:
        LOCAL = ("host", "Win")
        bucket = staticmethod(data_local.bucket)
        for_machine = staticmethod(data_local.for_machine)

    monkeypatch.setattr(engine.PickerScreen, "_finish_mount", lambda self: None)
    monkeypatch.setattr(engine.PickerScreen, "_after_first_refresh", lambda self: None)

    async def exercise():
        activated = []
        app = engine.PickerApp(
            None,
            prepare=lambda: PickerBinding(Source(), True, lambda: activated.append(True)),
        )
        async with app.run_test() as pilot:
            for _ in range(30):
                if app.query(engine.PickerScreen) and not app.query("#binding-status"):
                    break
                await pilot.pause(0.02)
            assert app.binding_error is None
            screen = app.query_one(engine.PickerScreen)
            assert screen.live
            assert activated == [True]
            assert not app.query("#binding-status")
            app.exit()

    asyncio.run(exercise())


def test_owner_loss_after_binding_exits_and_cancels_the_live_loader(monkeypatch):
    from worktree_manager.production_picker.picker_tui import data_local

    mounted = threading.Event()
    class Source:
        LOCAL = ("host", "Win")
        bucket = staticmethod(data_local.bucket)
        for_machine = staticmethod(data_local.for_machine)

    monkeypatch.setattr(engine.PickerScreen, "_finish_mount", lambda self: mounted.set())
    monkeypatch.setattr(engine.PickerScreen, "_after_first_refresh", lambda self: None)

    async def exercise():
        lost = False
        cancelled = []
        app = engine.PickerApp(
            None, prepare=lambda: PickerBinding(Source(), True, lambda: None),
            lifetime_cancelled=lambda: lost,
        )
        async with app.run_test() as pilot:
            for _ in range(50):
                if mounted.is_set():
                    break
                await pilot.pause(.02)
            assert mounted.is_set()
            screen = app.query_one(engine.PickerScreen)
            screen.loader = SimpleNamespace(cancel=lambda: cancelled.append(True))
            app.result = {"action": "new"}
            lost = True
            for _ in range(50):
                if not app.is_running:
                    break
                await pilot.pause(.02)
            assert not app.is_running
        assert app.result is None
        assert cancelled == [True]

    asyncio.run(exercise())


def test_authoritative_loader_does_not_wait_for_advisory(monkeypatch):
    class Source:
        LOCAL = ("host", "Win")

        def bootstrap_rows(self):
            raise AssertionError("advisory read must not run before current feed")

        def source_snapshot(self):
            return ()

        def make_loader(self, snapshot):
            assert snapshot == ()
            return loader

    class Loader:
        def start(self, *, focus_keys=None):
            calls.append({"focus_keys": focus_keys})

    calls = []
    loader = Loader()
    screen = engine.PickerScreen(Source(), live=True)
    monkeypatch.setattr(screen, "_setup_live_pivots", lambda: None)
    monkeypatch.setattr(screen, "_prepare_live_source", lambda _: {"local": Source.LOCAL})
    monkeypatch.setattr(screen, "_apply_from_worker", lambda fn: fn())
    monkeypatch.setattr(screen, "_apply_live_source", lambda prepared, value: calls.append(value))
    screen._setup_live_async()
    assert calls == [{"focus_keys": {Source.LOCAL}}, loader]


@pytest.mark.parametrize("mode", ["batched", "failed", "empty"])
def test_nonlive_setup_reload_retains_scalars_for_locality_input(monkeypatch, tmp_path, mode):
    from worktree_manager.production_picker import context, project_config as cfg
    from worktree_manager.production_picker.picker_tui import data_local, tasks

    class Source:
        LOCAL = data_local.LOCAL
        REPO, BRANCH = "example", "dev"
        bucket = staticmethod(data_local.bucket)
        for_machine = staticmethod(data_local.for_machine)
        is_local = staticmethod(data_local.is_local)
        host_cols = staticmethod(data_local.host_cols)
        target_envs = staticmethod(data_local.target_envs)

        @staticmethod
        def machines():
            return [("Local", *Source.LOCAL, True)]

        @staticmethod
        def load():
            cfg.load_config()
            return []

    calls = []
    alias = "fixture-local"

    def legacy_read(*args):
        calls.append(args)
        if mode == "failed":
            raise cfg.engine_client.EngineError("temporary timeout")
        if mode == "empty":
            return ""
        raise AssertionError("bound scalars must survive setup/reload")

    monkeypatch.setattr(cfg.engine_client, "get_value", legacy_read)
    monkeypatch.setattr(cfg.harness_state, "home", lambda: tmp_path)
    monkeypatch.setattr(cfg.harness_state, "project_config", lambda name: {"machine": alias})
    monkeypatch.setattr(
        cfg.harness_state, "build_projects",
        lambda: [SimpleNamespace(name="example", anchor=str(tmp_path))],
    )
    monkeypatch.setattr(cfg.harness_state, "repos_registry", lambda: {})
    monkeypatch.setattr(tasks, "prewarm_optional_modules", lambda: None)
    monkeypatch.setattr(engine.PickerScreen, "_scan_pivot_payload", lambda self: None)
    monkeypatch.setattr(engine.PickerScreen, "_prewarm_machine_key_map", lambda self: None)
    for name in ("_poll_update_state", "_poll_manager_update_state", "_poll_orphan_state"):
        monkeypatch.setattr(engine.PickerScreen, name, lambda self: None)
    context.set_project("example")
    cfg.bind_bootstrap("example", {
        "config_dir": str(tmp_path), "repo_dir": str(tmp_path), "machine": alias,
    } if mode == "batched" else None)

    async def exercise():
        app = engine.PickerApp(Source(), live=False)
        async with app.run_test() as pilot:
            screen = app.query_one(engine.PickerScreen)
            for phase in ("initial", "reload"):
                if phase == "reload":
                    screen._start_setup_reload_worker()
                for _ in range(50):
                    if screen._setup_epoch and screen._setup_applied_epoch == screen._setup_epoch:
                        break
                    await pilot.pause(0.02)
                assert screen._setup_epoch and screen._setup_applied_epoch == screen._setup_epoch
                prepared_calls = list(calls)
                generation = cfg._file_generation
                assert generation is not None and generation.current() and not generation.expired(), (
                    phase, generation and (generation.ttl, generation.current(), generation.expired()),
                    screen._config_cache_session.ttl,
                )
                assert data_local.is_local(alias, Source.LOCAL[1])
                decision = screen._resume_decision({
                    "raw": {"id": "fixture"}, "machine": alias, "env": Source.LOCAL[1],
                })
                assert decision["action"] == "resume" and decision["is_local"] is True
                assert calls == prepared_calls
                if mode == "batched":
                    assert calls == []
                else:
                    assert len(calls) == (3 if phase == "initial" else 6)
            app.exit()

    try:
        asyncio.run(exercise())
    finally:
        cfg.bind_bootstrap("example", None)
        context.reset()
