"""Configuration paints before provider I/O and retains normal pivot semantics."""

import asyncio
import html
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_config_actions import _install_section, _section, _wait
from test_picker_tui import _fixture_source, _wait_for_initial_setup
from textual.screen import ModalScreen

from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.configuration_form import (
    ConfigurationFormScreen,
    ConfigurationResultScreen,
)
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen

pytestmark = pytest.mark.contract("worktree_manager.configuration_backstage")


@pytest.mark.parametrize("scenario", [
    "profiles", "profiles-confirm", "loading", "catalog", "actions", "details",
    "instantiate", "declaration", "edit", "confirm", "receipt", "error", "compact",
])
def test_illustrated_configuration_scenarios_use_production_compositor(monkeypatch, scenario):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "worktree-manager" / "scripts" / "picker-snapshot"))
    from configuration import capture_configuration
    from render import _demo_source

    svg = capture_configuration(_demo_source(), scenario)
    assert "<svg" in svg
    assert f"Configuration - {scenario}" in html.unescape(svg).replace("\xa0", " ")
    asset = root / "docs" / "assets" / "configuration-workbench" / f"{scenario}.png"
    assert asset.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_workers_table_keeps_status_statistics_description_and_source_at_standard_width():
    from worktree_manager.production_picker.picker_tui.engine_views import TasksView
    manifest_path = Path(__file__).resolve().parents[3] / "plugins" / "agent-dispatch" / "pivots" / "workers-config.json"
    reg = pivots.parse_manifest(
        json.loads(manifest_path.read_text()), name="workers", source_path=str(manifest_path),
    )
    fitted = TasksView(None)._fitted_columns(reg, 118)
    assert {column.key for column in fitted} == {
        "title", "enabled", "activity", "lanes", "provenance", "description",
    }
    wide = TasksView(None)._fitted_columns(reg, 150)
    assert {column.key for column in wide} == {
        "title", "kind", "enabled", "activity", "lanes", "provenance", "description",
    }


@pytest.mark.parametrize("error", [False, True])
def test_inert_provider_template_catalog_records_offer_only_details(error):
    manifest_path = Path(__file__).resolve().parents[3] / "plugins" / "agent-dispatch" / "pivots" / "workers-config.json"
    contribution = pivots.parse_manifest(
        json.loads(manifest_path.read_text()), name="workers", source_path=str(manifest_path),
    )
    row = {
        "id": "template:provider:0123456789abcdef0123456789abcdef:record-source",
        "title": "Record source", "group": "Available templates", "kind": "emitter",
        "validity": "fragment", "enabled": "not-instantiated", "activity": "not-applicable",
        "editable": "false", "instantiable": "false", "resolution": "discovered-only",
        "unsupported_reason": "Safe local instantiation is not supported.",
        "card": {"title": "Record source", "body": "Declared requirements and provider provenance."},
    }
    if error:
        row.update(
            id="template-error:0", group="Discovery errors", kind="error",
            finding_scope="templates", validity="error", enabled="unknown",
        )
    records, summary = pivots.parse_list_payload({
        "entries": [row], "summary": {
            "errors": [], "template_discovery": {"state": "partial" if error else "complete"},
        },
    })
    assert records == [row] and summary["errors"] == []
    assert [
        action.key for action in contribution.actions if pivots.entry_matches(action.when, records[0])
    ] == ["details"]
    assert records[0]["card"] == row["card"]


def test_configuration_placement_is_additive_and_strict():
    base = {"label": "Resources", "list": ["provider", "catalog"]}
    ordinary = pivots.parse_manifest(base, name="ordinary", source_path="fixture.json")
    config = pivots.parse_manifest(
        {**base, "placement": "config", "scope": "global"},
        name="config", source_path="fixture.json",
    )
    assert ordinary.placement == "left"
    assert config.placement == "config" and config.account_scoped
    account = pivots.parse_manifest(
        {**base, "placement": "config", "scope": "account"},
        name="account", source_path="fixture.json",
    )
    assert account.account_scoped
    for extra in ({}, {"scope": "machine"}):
        with pytest.raises(pivots.ManifestError, match="account/global"):
            pivots.parse_manifest(
                {**base, "placement": "config", **extra},
                name="hidden-target", source_path="fixture.json",
            )
    for value in ([], "dialog", None):
        with pytest.raises(pivots.ManifestError, match="placement"):
            pivots.parse_manifest(
                {**base, "placement": value}, name="bad", source_path="fixture.json",
            )


@pytest.mark.parametrize("size", [(118, 40), (80, 24)])
def test_backstage_renders_before_blocked_provider_and_navigation_stays_responsive(
    tmp_path, monkeypatch, size,
):
    _install_section(tmp_path, monkeypatch, _section())
    entered, release = threading.Event(), threading.Event()

    def slow_read(section, context):
        entered.set()
        assert release.wait(10), "test did not release its provider"
        return True, "Fixture ready"

    monkeypatch.setattr(tasks, "run_config_section", slow_read)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=size) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            original = (scr.htab, scr.sel, scr.top, scr.btn_idx)
            scr._open_cfgmenu()
            assert scr._in_configuration()
            while scr._kind() != "config_section":
                scr._rotate_machine(1)
            try:
                await _wait(pilot, entered.is_set)
                assert not isinstance(app.screen, ModalScreen)
                chrome = scr.tab_bar(size[0], True).plain
                assert "Sections" in chrome and "Local controls" in chrome
                assert "All" not in chrome
                body = "\n".join(row.text.plain for row in scr.build_body(size[0]))
                assert "Loading Local controls" in body
                scr._rotate_machine(1)
                assert scr._kind() == "profiles"
                assert "Profiles" in scr.tab_bar(size[0], True).plain
                scr._dispatch_key("escape")
                assert (scr.htab, scr.sel, scr.top, scr.btn_idx) == original
                release.set()
                await pilot.pause()
                assert not scr._in_configuration()
            finally:
                release.set()

    asyncio.run(run())


def test_one_shot_legacy_section_is_inert_until_explicit_action(tmp_path, monkeypatch):
    _install_section(tmp_path, monkeypatch, {
        "label": "Legacy", "run": [sys.executable, "-c", "print('fixture')"],
    })
    calls = []
    monkeypatch.setattr(tasks, "run_config_section", lambda *args: calls.append(args) or (True, "done"))

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            scr._open_cfgmenu()
            while scr._kind() != "config_section":
                scr._rotate_machine(1)
            await pilot.pause()
            assert not calls
            scr._activate_config_section()
            await _wait(pilot, lambda: bool(calls))
            assert len(calls) == 1

    asyncio.run(run())


def test_legacy_configuration_error_is_retryable_and_late_read_is_fenced(tmp_path, monkeypatch):
    _install_section(tmp_path, monkeypatch, _section())
    release = threading.Event()
    calls = []

    def read(section, context):
        calls.append(1)
        if len(calls) == 1:
            return False, "Discovery failed"
        if len(calls) == 2:
            assert release.wait(10)
            return True, "Stale older response"
        return True, "Fresh replacement"

    monkeypatch.setattr(tasks, "run_config_section", read)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            scr._open_cfgmenu()
            scr._rotate_machine(1)
            try:
                key = scr._config_section_key()
                await _wait(pilot, lambda: scr._config_section_states.get(key, {}).get("state") == "error")
                scr._activate_config_section()
                await _wait(pilot, lambda: len(calls) == 2)
                scr._load_current_config_section(force=True)
                await _wait(pilot, lambda: scr._config_section_states[key]["state"] == "ready")
                assert scr._config_section_states[key]["status"] == "Fresh replacement"
                release.set()
                await pilot.pause()
                assert scr._config_section_states[key]["status"] == "Fresh replacement"
            finally:
                release.set()

    asyncio.run(run())


def test_configuration_editor_restores_values_and_cancel_has_no_side_effects():
    fields = [
        {"name": "name", "type": "text", "default": "fixture-worker"},
        {"name": "mode", "type": "choice", "options": ["save", "activate"], "default": "save"},
        {"name": "labels", "type": "multichoice", "options": ["one", "two"], "default": ["two"]},
    ]
    results = []

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            form = ConfigurationFormScreen("Edit instance", fields, confirm=True)
            app.push_screen(form, results.append)
            await pilot.pause()
            assert not isinstance(form, ModalScreen)
            assert form._collect() == {"name": "fixture-worker", "mode": "save", "labels": ["two"]}
            await pilot.press("escape")
            await pilot.pause()
            assert results == [None]

    asyncio.run(run())


def test_configuration_editor_restores_custom_choice_and_multichoice_defaults():
    from worktree_manager.production_picker.picker_tui.pivot_create_action import _parse_create_fields

    fields = [
        {"name": "host", "type": "choice", "options": ["listed"],
         "allow_other": True, "default": "custom-host"},
        {"name": "labels", "type": "multichoice", "options": ["listed", "unused"],
         "allow_other": True, "default": ["listed", "custom-one", "custom-two"]},
    ]
    parsed = _parse_create_fields(fields, where="fixture.fields")
    for field in fields:
        with pytest.raises(pivots.ManifestError, match="default"):
            _parse_create_fields([{**field, "allow_other": False}], where="fixture.fields")

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            form = ConfigurationFormScreen("Edit custom values", parsed)
            app.push_screen(form)
            await pilot.pause()
            assert form._collect() == {
                "host": "custom-host", "labels": ["listed", "custom-one", "custom-two"],
            }
            assert all(record["other"].display for record in form._q)
            form._q[1]["other"].text = "new custom\ncomma, member"
            assert form._collect()["labels"] == ["listed", "new custom", "comma, member"]
            await pilot.press("escape")

    asyncio.run(run())


@pytest.mark.parametrize("returncode", [0, 1])
def test_configuration_receipt_preserves_real_child_output_even_on_partial_failure(returncode):
    receipt = json.dumps({
        "persisted": True,
        "activation": "pending" if returncode == 0 else "failed",
        "effects": "complete-effects-" + "x" * 500,
    })
    script = f"import sys; print({receipt!r}); print('provider diagnostic', file=sys.stderr); sys.exit({returncode})"
    reg = pivots.parse_manifest(
        {"label": "Fixture", "placement": "config", "scope": "global", "list": ["fixture"]},
        name="fixture", source_path="fixture.json",
    )
    runtime = tasks.RegisteredPivotRuntime(reg)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            scr._run_configuration_command(
                "Save and Activate",
                lambda: runtime.run_resolved(
                    [sys.executable, "-c", script], preserve_output=True,
                ),
                lambda: None,
            )
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                        and not app.screen.pending)
            text = str(app.screen.query_one("#config-result").render())
            assert receipt in text and "provider diagnostic" in text
            assert ("Failed" if returncode else "Completed") in text
            if returncode:
                assert "exit 1" in text

    asyncio.run(run())


def test_configuration_context_never_inherits_the_operational_machine():
    reg = pivots.parse_manifest(
        {"label": "Fixture", "placement": "config", "scope": "global", "list": ["fixture"]},
        name="fixture", source_path="fixture.json",
    )
    scr = PickerScreen(_fixture_source(), live=False)
    scr._source_local = ("fixture-local", "Win")
    scr._pivot_machine_id = lambda: "previously-selected-remote"
    ctx = scr._task_action_ctx(reg, {"id": "entry", "title": "Entry"})
    assert ctx["machine"] == ""
    assert scr._config_section_ctx()["machine"] == "fixture-local"


def test_post_submit_refresh_keeps_rows_and_selected_identity_until_swap(monkeypatch):
    reg = pivots.parse_manifest({
        "label": "Resources", "placement": "config", "scope": "global",
        "list": ["fixture"], "actions": [{"key": "save", "label": "Save", "run": ["fixture"]}],
    }, name="fixture", source_path="fixture.json")
    monkeypatch.setattr(pivots, "scan_pivot_registry", lambda: SimpleNamespace(
        pivots=[reg], worktree_actions=[], config_sections=[], findings=(),
    ))
    entered, release = threading.Event(), threading.Event()
    rows = [{"id": "one", "title": "One"}, {"id": "two", "title": "Two"}]
    calls = []

    def read(runtime, context):
        calls.append(1)
        if len(calls) > 1:
            entered.set()
            assert release.wait(10), "test did not release refresh"
            return "ready", list(reversed(rows)), "", {}
        return "ready", rows, "", {}

    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "_exec_list", read)
    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "run_resolved", lambda *a, **k: (True, "saved"))

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            scr._open_cfgmenu()
            while scr.htabs[scr.htab] != "Resources":
                scr._rotate_machine(1)
                await pilot.pause()
            await _wait(pilot, lambda: scr._task_state()[0] == "ready")
            scr._task_rows()
            scr.sel = ("T", 1)
            try:
                scr._submit_configuration_form(reg, reg.actions[0], {"id": "two"}, {})
                await _wait(pilot, lambda: entered.is_set()
                            and isinstance(app.screen, ConfigurationResultScreen)
                            and not app.screen.pending)
                app.screen.action_close()
                await pilot.pause()
                assert scr._task_rows() == rows and scr.sel == ("T", 1)
                release.set()
                await _wait(pilot, lambda: scr._task_rows()[0]["id"] == "two")
                assert scr.sel == ("T", 0)
            finally:
                release.set()

    asyncio.run(run())


def test_workers_catalog_picker_instance_round_trip(tmp_path, monkeypatch):
    """Exercise the shipped list/form CLI boundary against isolated provider state."""
    dispatch_python = os.environ.get("WORKERS_CONFIG_TEST_PYTHON")
    if not dispatch_python:
        pytest.skip("set WORKERS_CONFIG_TEST_PYTHON for the cross-distribution lane")
    root = Path(__file__).resolve().parents[3]
    source = root / "plugins" / "agent-dispatch" / "src"
    manifest_path = root / "plugins" / "agent-dispatch" / "pivots" / "workers-config.json"
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    prefix = [
        dispatch_python, "-c",
        (f"import sys; sys.path.insert(0, {str(source)!r}); "
         "from agent_dispatch.__main__ import main; raise SystemExit(main())"),
    ]
    manifest["list"] = prefix + manifest["list"][1:]
    for action in manifest["actions"]:
        if action.get("run"):
            action["run"] = prefix + action["run"][1:]
    registry = tmp_path / "pivots"
    registry.mkdir()
    (registry / "workers.json").write_text(json.dumps(manifest), encoding="utf-8")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    registrar = tmp_path / "registrar"
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(registry))
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugins))
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(registrar))
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DROPINS_DIR", str(tmp_path / "dropins"))
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(tmp_path / "overrides.json"))
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "install"))

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            scr = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, scr)
            scr._open_cfgmenu()
            while scr.htabs[scr.htab] != "Workers":
                scr._rotate_machine(1)
                await pilot.pause()
            await _wait(pilot, lambda: scr._task_state()[0] in ("ready", "error"))
            assert scr._task_state()[0] == "ready", scr._task_state()
            reg = scr._reg_pivot()
            row = next(row for row in scr._task_rows() if row["id"] == "template:worker-pool")
            action = next(action for action in reg.actions if action.key == "instantiate-save")
            context = scr._task_action_ctx(reg, row)
            scr._open_pivot_form(reg, action, row, context, row["title"])
            await pilot.pause()
            editor = app.screen
            assert isinstance(editor, ConfigurationFormScreen)
            assert editor._sections == ("Profiles", "Workers")
            assert json.loads(editor._collect()["declaration"])["concurrency"] == 1
            await pilot.press("escape")
            assert not registrar.exists()
            scr._open_pivot_form(reg, action, row, context, row["title"])
            await pilot.pause()
            editor = app.screen
            editor._q[0]["primary"].value = "fixture-worker"
            editor._show_confirm_prompt()
            await pilot.pause()
            assert not registrar.exists()
            await pilot.press("left", "enter")
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                        and not app.screen.pending)
            assert "persisted" in str(app.screen.query_one("#config-result").render())
            assert (registrar / "worker-instances" / "fixture-worker.json").is_file()
            assert not (registrar / "pointers.json").exists()
            app.screen.action_close()
            await _wait(pilot, lambda: any(
                row["id"] == "instance:fixture-worker" for row in scr._task_rows()
            ))
            instance = next(row for row in scr._task_rows() if row["id"] == "instance:fixture-worker")
            edit = next(action for action in reg.actions if action.key == "edit-activate")
            scr._open_pivot_form(
                reg, edit, instance, scr._task_action_ctx(reg, instance), instance["title"],
            )
            await pilot.pause()
            editor = app.screen
            assert editor._collect()["name"] == "fixture-worker"
            document = json.loads(editor._collect()["declaration"])
            document["concurrency"] = 2
            editor._q[1]["primary"].text = json.dumps(document)
            editor._show_confirm_prompt()
            await pilot.pause()
            assert not (registrar / "pointers.json").exists()
            assert "create agentic tasks" in str(editor.query_one("#create-confirm-prompt").render())
            # Drive the real default-Cancel confirmation rather than invoking a CLI shortcut.
            await pilot.press("left", "enter")
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                        and not app.screen.pending)
            receipt = str(app.screen.query_one("#config-result").render())
            assert '"activation": "pending"' in receipt
            assert len(list((registrar / "worker-instances").glob("*.json"))) == 1
            active = json.loads((registrar / "worker-instances-active" / "fixture-worker.json").read_text())
            assert active["concurrency"] == 2
            assert manifest_path.read_bytes() == original

    asyncio.run(run())
