"""Local configuration form contracts, including an opt-in real Workers round-trip."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
import subprocess

import pytest
from agent_procutil import no_window_flags
from textual.widgets import Input, Static

from test_picker_tui import _fixture_source, _wait_for_initial_setup
from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.configuration_form import (
    ConfigurationFormScreen as CreateActionScreen, ConfigurationResultScreen,
)
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.engine_focus import FocusGroup
from worktree_manager.production_picker.picker_tui.pivot_actions import ManifestError
from worktree_manager.production_picker.picker_tui.steering import SubmitErrorScreen

pytestmark = pytest.mark.contract("worktree_manager.config_actions")


def _section(*, options=None, run=None, status=None):
    return {
        "key": "local",
        "label": "Local controls",
        "run": status or [sys.executable, "-c", "print('declared, not running')"],
        "description": "Local machine only; declarations are not edited.",
        "configure_action": {
            "label": "Local controls",
            "fields": [
                {"name": "pool", "type": "choice", "options_command": options or [
                    sys.executable, "-c", "print('[\"fixture-pool\"]')",
                ]},
                {"name": "action", "type": "choice", "options": ["disable", "enable"]},
                {"name": "reason", "type": "text",
                 "show_when": {"field": "action", "equals": "disable"}},
            ],
            "run": run or [sys.executable, "-c", "print('applied')"],
            "confirm": True,
        },
    }


def _install_section(tmp_path, monkeypatch, section):
    registry = tmp_path / "pivots"
    registry.mkdir()
    (registry / "local.json").write_text(
        json.dumps({"config_sections": [section]}), encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(registry))
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugins))


async def _wait(pilot, predicate):
    deadline = time.monotonic() + tasks.ACTION_TIMEOUT + tasks.STRICT_OPTIONS_COMMAND_TIMEOUT + 5
    while time.monotonic() < deadline:
        await pilot.pause()
        if predicate():
            return
        await asyncio.sleep(0.01)
    screen = pilot.app.query_one(PickerScreen)
    form = pilot.app.screen
    details = (
        f"; values={form._collect()}; visible={form._visible_question_indexes()}; "
        f"focus={getattr(pilot.app.focused, 'id', None)}"
        if isinstance(form, CreateActionScreen) else ""
    )
    raise AssertionError(
        f"configuration transition did not finish: {type(pilot.app.screen).__name__}; "
        f"{screen.debug}; busy={getattr(screen, '_busy_label', '')}{details}"
    )


async def _open(scr, pilot):
    if isinstance(scr.app.screen, ConfigurationResultScreen):
        await _wait(pilot, lambda: not scr.app.screen.pending)
        scr.app.screen.action_close()
        await pilot.pause()
    scr._open_cfgmenu()
    await pilot.pause()
    index = next(i for i, descriptor in enumerate(scr.pivots)
                 if descriptor["kind"] == "config_section")
    while scr.htab != index:
        scr._rotate_machine(1)
        await pilot.pause()
    await _wait(pilot, lambda: scr._config_section_states.get(
        scr._config_section_key(), {},
    ).get("state") in ("ready", "error"))
    state = scr._config_section_states[scr._config_section_key()]
    if state["state"] == "error":
        return scr
    scr._activate_config_section()
    await _wait(
        pilot,
        lambda: isinstance(scr.app.screen, (CreateActionScreen, SubmitErrorScreen))
        and scr.app.screen.is_mounted,
    )
    await pilot.pause()
    return scr.app.screen


async def _fill_and_apply(screen, pilot, *, action="disable", reason=""):
    await _wait(pilot, lambda: screen._q[0]["primary"].has_focus)
    await pilot.press("enter")
    await _wait(pilot, lambda: screen._q[1]["primary"].has_focus)
    if action == "enable":
        await pilot.press("down")
    await pilot.press("enter")
    if action == "disable":
        question = screen.query_one("#q-2", Input)
        await _wait(pilot, lambda: question.has_focus)
        question.value = reason
        await pilot.press("enter")
    await _wait(pilot, lambda: screen.query_one("#create-buttons", FocusGroup).has_focus)
    assert screen._collect()["action"] == action
    await pilot.press("enter")
    await _wait(pilot, lambda: bool(screen.query("#create-confirm-prompt-buttons")))
    group = screen.query_one("#create-confirm-prompt-buttons", FocusGroup)
    await _wait(pilot, lambda: group.is_mounted and group.has_focus)
    assert group.value == "no"


async def _cancel_then_confirm(screen, pilot):
    await pilot.press("enter")
    await _wait(pilot, lambda: not screen._confirming)
    await _wait(pilot, lambda: screen.query_one("#create-buttons", FocusGroup).has_focus)
    await pilot.press("enter")
    await _wait(pilot, lambda: bool(screen.query("#create-confirm-prompt-buttons")))
    group = screen.query_one("#create-confirm-prompt-buttons", FocusGroup)
    await _wait(pilot, lambda: group.is_mounted and group.has_focus)
    assert group.value == "no"
    await pilot.press("left", "enter")


def test_config_action_parser_reuses_strict_fields_without_free_text_fallback():
    [section] = pivots.parse_config_sections({"config_sections": [_section()]}, name="fixture")
    action = section.configure_action
    assert action.confirm is True
    assert not action.fields[0].get("allow_other")
    assert action.fields[0]["options_command"][0] == sys.executable
    ordinary = pivots.parse_config_sections(
        {"config_sections": [{"label": "Status", "run": ["status"]}]}, name="fixture",
    )[0]
    assert ordinary.configure_action is None
    for malformed in ("invalid", {"label": "Controls", "run": []}, {
        "label": "Controls", "run": ["apply"],
        "fields": [{"name": "pool"}, {"name": " pool "}],
    }):
        section = _section()
        section["configure_action"] = malformed
        with pytest.raises(ManifestError):
            pivots.parse_config_sections({"config_sections": [section]}, name="fixture")


@pytest.mark.parametrize("expression,match", [
    ("print('[]')", "no usable choices"),
    ("print('[1]')", "array of strings"),
    ("print('invalid')", "invalid JSON"),
    ("import sys; print('discovery failed', file=sys.stderr); sys.exit(2)", "discovery failed"),
])
def test_config_options_fail_closed(expression, match):
    with pytest.raises(ValueError, match=match):
        tasks.resolve_dynamic_options([sys.executable, "-c", expression], strict=True)
    assert tasks.resolve_dynamic_options([sys.executable, "-c", expression]) == []


def test_config_choices_use_read_query_budget_without_changing_create_budget(monkeypatch):
    from types import SimpleNamespace

    timeouts = []

    def run(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout='["pool"]')

    monkeypatch.setattr(tasks.subprocess, "run", run)
    assert tasks.resolve_dynamic_options(["fixture"], strict=True) == ["pool"]
    assert tasks.resolve_dynamic_options(["fixture"]) == ["pool"]
    assert timeouts == [tasks.STRICT_OPTIONS_COMMAND_TIMEOUT, tasks.OPTIONS_COMMAND_TIMEOUT]


def test_config_options_error_identifies_the_field():
    with pytest.raises(ValueError, match="field 'pool' options_command"):
        tasks.resolve_form_fields([{
            "name": "pool", "type": "choice",
            "options_command": [sys.executable, "-c", "print('[]')"],
        }], strict=True)


def test_config_form_cancel_and_apply_native_controls(tmp_path, monkeypatch):
    applied = tmp_path / "applied.json"
    command = [
        sys.executable, "-c",
        "import json, pathlib, sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))",
        str(applied), "{field.pool}", "{field.action}", "{field.reason}",
    ]
    _install_section(tmp_path, monkeypatch, _section(run=command))

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            screen = await _open(scr, pilot)
            assert isinstance(screen, CreateActionScreen), scr.debug
            assert "declared, not running" in str(screen.query_one("#create-summary", Static).render())
            assert not screen._q[0].get("other")
            assert screen._submit_label == "Apply"
            assert not applied.exists()
            await pilot.press("escape")
            assert not applied.exists()

            screen = await _open(scr, pilot)
            await _fill_and_apply(screen, pilot, reason="literal {pool} [bold]")
            text = str(screen.query_one("#create-confirm-prompt", Static).render())
            assert "pool: fixture-pool" in text and "action: disable" in text
            assert "Local machine only" in text
            assert not applied.exists()
            await _cancel_then_confirm(screen, pilot)
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                        and not app.screen.pending)
            assert json.loads(applied.read_text()) == [
                "fixture-pool", "disable", "literal {pool} [bold]",
            ]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["status", "options", "apply"])
def test_config_command_failure_is_visible_without_unconfirmed_mutation(
    tmp_path, monkeypatch, failure,
):
    bad = [sys.executable, "-c", "import sys; print('fixture failure', file=sys.stderr); sys.exit(2)"]
    section = _section()
    if failure == "status":
        section["run"] = bad
    elif failure == "options":
        section["configure_action"]["fields"][0]["options_command"] = bad
    else:
        section["configure_action"]["run"] = bad
    _install_section(tmp_path, monkeypatch, section)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            screen = await _open(scr, pilot)
            if failure == "apply":
                assert isinstance(screen, CreateActionScreen), scr.debug
                await _fill_and_apply(screen, pilot)
                await pilot.press("left", "enter")
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                            and not app.screen.pending)
            else:
                assert screen is scr
                assert "fixture failure" in scr._config_section_states[
                    scr._config_section_key()
                ]["status"]
            if failure == "apply":
                assert "fixture failure" in scr.debug

    asyncio.run(run())


def test_workers_picker_real_override_round_trip(tmp_path, monkeypatch):
    """Opt in with a prepared dispatch interpreter; never touches host declarations."""
    dispatch_python = os.environ.get("WORKERS_CONFIG_TEST_PYTHON")
    if not dispatch_python:
        pytest.skip("set WORKERS_CONFIG_TEST_PYTHON for the cross-distribution acceptance lane")
    root = Path(__file__).resolve().parents[3]
    source = root / "plugins" / "agent-dispatch" / "src"
    assert source.is_dir() and Path(dispatch_python).is_file()
    declaration_dir = tmp_path / "declarations"
    declaration_dir.mkdir()
    declaration = declaration_dir / "fixture.json"
    pool_name = "-fixture-pool"
    declaration.write_text(
        json.dumps({"name": pool_name, "kind": "supervised-lane", "concurrency": 2}),
        encoding="utf-8",
    )
    original = declaration.read_bytes()
    registrar = tmp_path / "registrar"
    registrar.mkdir()
    (registrar / "pointers.json").write_text(json.dumps([{
        "name": "fixture", "kind": "dir", "location": str(declaration_dir), "owner": "repo:fixture",
    }]), encoding="utf-8")
    overrides = tmp_path / "overrides.json"
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(registrar))
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(overrides))
    prefix = [
        dispatch_python, "-c",
        f"import sys; sys.path.insert(0, {str(source)!r}); "
        "from agent_dispatch.__main__ import main; raise SystemExit(main())",
    ]
    # Legacy callers keep their CLI contract after Workers moves to a list pivot.
    section = _section()
    section["run"] = prefix + ["workers", "config-section"]
    action = section["configure_action"]
    action["run"] = prefix + [
        "workers", "config-section", "--toggle", "{field.action}",
        "--reason={field.reason}", "--require-declared", "--", "{field.pool}",
    ]
    pool = action["fields"][0]
    pool["options_command"] = prefix + ["workers", "pool-options"]
    _install_section(tmp_path, monkeypatch, section)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            screen = await _open(scr, pilot)
            assert isinstance(screen, CreateActionScreen), scr.debug
            assert not overrides.exists() and declaration.read_bytes() == original
            await pilot.press("escape")
            assert not overrides.exists()
            for toggle in ("disable", "enable"):
                screen = await _open(scr, pilot)
                assert isinstance(screen, CreateActionScreen), scr.debug
                await _fill_and_apply(screen, pilot, action=toggle, reason="acceptance proof")
                before = overrides.read_bytes() if overrides.exists() else None
                await pilot.press("enter")
                await _wait(pilot, lambda: not screen._confirming)
                await _wait(pilot, lambda: screen.query_one("#create-buttons", FocusGroup).has_focus)
                assert (overrides.read_bytes() if overrides.exists() else None) == before
                await pilot.press("enter")
                await _wait(pilot, lambda: bool(screen.query("#create-confirm-prompt-buttons")))
                group = screen.query_one("#create-confirm-prompt-buttons", FocusGroup)
                await _wait(pilot, lambda: group.is_mounted and group.has_focus)
                await pilot.press("left", "enter")
                await _wait(pilot, lambda: not isinstance(app.screen, CreateActionScreen))
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                            and not app.screen.pending)
                assert "failed" not in scr.debug
                state = json.loads(overrides.read_text(encoding="utf-8"))
                listed = subprocess.run(
                    prefix + ["supervise", "override", "list"],
                    capture_output=True, text=True, timeout=tasks.ACTION_TIMEOUT, check=True,
                    env=tasks._child_process_env(), creationflags=no_window_flags(),
                )
                effective = json.loads(listed.stdout)["overridden_off"]
                override_id = f"logical:repo:fixture:{pool_name}"
                if toggle == "disable":
                    assert state[override_id]["disabled"] is True
                    assert effective == [override_id]
                else:
                    assert override_id not in state
                    assert effective == []
                assert declaration.read_bytes() == original
            for stale in ("absent", "ambiguous"):
                screen = await _open(scr, pilot)
                assert isinstance(screen, CreateActionScreen), f"{stale}: {scr.debug}"
                before = overrides.read_bytes()
                if stale == "absent":
                    declaration.write_text(
                        json.dumps({"name": "replacement", "kind": "supervised-lane"}),
                        encoding="utf-8",
                    )
                else:
                    (declaration_dir / "duplicate.json").write_bytes(original)
                await _fill_and_apply(screen, pilot)
                await pilot.press("left", "enter")
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                            and not app.screen.pending)
                assert "failed" in scr.debug
                assert overrides.read_bytes() == before
                await pilot.press("escape")
                declaration.write_bytes(original)
                if stale == "ambiguous":
                    (declaration_dir / "duplicate.json").unlink()
                assert declaration.read_bytes() == original

    asyncio.run(run())
