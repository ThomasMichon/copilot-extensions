"""Strict optional previews and actual UI-to-owner CLI set/reset boundaries."""

import asyncio
import json
import sys
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_config_actions import _wait
from test_picker_tui import _fixture_source, _wait_for_initial_setup
from textual.widgets import Button

from worktree_manager import plugin_contracts
from worktree_manager.configuration_preview import parse_configuration_preview
from worktree_manager.production_picker import context as picker_context
from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.configuration_form import (
    ConfigurationFormScreen,
    ConfigurationResultScreen,
)
from worktree_manager.production_picker.picker_tui.configuration_preview_screen import (
    ConfigurationPreviewScreen,
)
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.pivot_actions import format_form_template
from worktree_manager.production_picker.picker_tui.pivot_registry_materialization import (
    _rewrite_manifest_commands,
)

pytestmark = pytest.mark.contract("worktree_manager.configuration_backstage")
TARGET = {"installation": "fixture-install", "machine": "fixture-local", "resource": "sample"}


def _manifest():
    return {
        "label": "Preferences", "placement": "config", "scope": "global",
        "list": [sys.executable, "-c", "print('[]')"],
        "actions": [{
            "key": "set", "label": "Set preference", "kind": "form", "confirm": True,
            "fields_from": "card.request_input", "preview": ["preview-owner"],
            "run": ["apply-owner", "{configuration_preview}"],
        }],
    }


def _parse(data):
    return pivots.parse_manifest(data, name="fixture", source_path="fixture.json")


def _contract(data):
    return plugin_contracts.parse_manifest(
        data, name="fixture", marketplace="fixture", plugin="owner", source_path="fixture.json",
    )


@pytest.mark.parametrize("parser", [_parse, _contract])
def test_optional_preview_is_additive_and_preserved(parser):
    data = _manifest()
    result = parser(data)
    action = (result.pivot if parser is _contract else result).actions[0]
    assert action.preview == ("preview-owner",)
    del data["actions"][0]["preview"]
    result = parser(data)
    assert not (result.pivot if parser is _contract else result).actions[0].preview


@pytest.mark.parametrize("change", [
    {"placement": "left"}, {"scope": "machine"}, {"confirm": False},
    {"confirm": 1}, {"kind": "command"}, {"preview": []},
    {"preview": "owner"}, {"preview": [""]}, {"preview": None},
])
@pytest.mark.parametrize("parser", [_parse, _contract])
def test_preview_declaration_restrictions_agree(parser, change):
    data = _manifest()
    for key, value in change.items():
        (data if key in ("placement", "scope") else data["actions"][0])[key] = value
    with pytest.raises(ValueError, match="preview"):
        parser(data)


@pytest.mark.parametrize("container", [
    "create_action", "view_actions", "worktree_actions", "config_sections", "configure_action",
])
@pytest.mark.parametrize("parser", [_parse, _contract])
def test_preview_cannot_escape_to_other_action_containers(parser, container):
    data = _manifest()
    action = data.pop("actions")[0]
    if container == "create_action":
        data[container] = action
    elif container == "configure_action":
        data["config_sections"] = [{"configure_action": action}]
    else:
        data[container] = [action]
    with pytest.raises(ValueError, match="preview"):
        parser(data)


def _payload():
    return {
        "version": 1, "token": "fresh-token", "target": TARGET, "scope": "machine-local",
        "operation": "set", "summary": "Set preference", "effects": ["Local override only"],
        "findings": [{"code": "warning", "severity": "warning", "message": "Future launches only"}],
    }


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"token": ""}, {"token": " "},
    {"target": {**TARGET, "machine": "another"}}, {"target": {"resource": "sample"}},
    {"scope": "repository"}, {"operation": "activate"}, {"summary": None},
    {"effects": "not-array"}, {"effects": [1]}, {"findings": {}},
    {"findings": [{"code": "x", "severity": "critical", "message": "bad"}]},
    {"extra": True},
])
def test_preview_payload_fails_closed(change):
    with pytest.raises(ValueError):
        parse_configuration_preview(json.dumps({**_payload(), **change}), TARGET)


def test_strict_json_and_findings():
    for output in ("[]", "not JSON", '{"version":1,"version":1}', json.dumps(_payload()) + "\nnoise"):
        with pytest.raises(ValueError):
            parse_configuration_preview(output, TARGET)
    data = _payload()
    preview = parse_configuration_preview(json.dumps(data), TARGET)
    assert not preview.blocked and "Future launches only" in preview.describe()
    data["findings"][0]["severity"] = "error"
    assert parse_configuration_preview(json.dumps(data), TARGET).blocked


def test_missing_preview_disables_only_affected_contract_action(tmp_path):
    data = _manifest()
    data["actions"][0]["run"] = [sys.executable]
    data["actions"].append({"label": "Peer", "run": [sys.executable]})
    resolved = _rewrite_manifest_commands(data, root=tmp_path, require_targets=True)
    assert resolved["actions"][0]["preview"] == ["preview-owner"]
    assert _parse(resolved).actions[0].preview_error
    contract, missing = plugin_contracts._probe_commands(_contract(resolved))
    assert contract.command_available
    assert not contract.pivot.actions[0].available and contract.pivot.actions[1].available
    assert missing == ["action set preview: preview-owner"]
    data["actions"][0]["preview"] = ["bin/missing-preview"]
    resolved = _rewrite_manifest_commands(data, root=tmp_path, require_targets=True)
    assert resolved["actions"][0]["preview"][0] == str(tmp_path / "bin/missing-preview")
    assert _parse(resolved).actions[0].preview_error


def test_unusable_preview_is_not_retried_through_an_unattributed_runtime_path(tmp_path):
    data = _manifest()
    data["actions"][0]["run"] = [sys.executable]
    data["actions"][0]["preview"] = ["../outside-preview"]
    resolved = _rewrite_manifest_commands(data, root=tmp_path, require_targets=True)
    assert _parse(resolved).actions[0].preview_error


def _owner(tmp_path, monkeypatch):
    monkeypatch.setattr(picker_context, "_project", "fixture-project")
    monkeypatch.setattr(picker_context, "_bootstrap", None)
    root = tmp_path / "owner"
    for directory in ("repository", "knowledge", "plugin", "preset"):
        folder = root / directory
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(
            json.dumps({"mode": "inherited", "secret": "SENTINEL-DO-NOT-RENDER"}),
        )
    (root / "local").mkdir()
    (root / "coordination.lock").write_bytes(b"0")
    fixture = Path(__file__).parent / "fixtures" / "configuration_owner.py"
    prefix = [sys.executable, "-I", str(fixture)]
    data = _manifest()
    data["list"] = [*prefix, "list", str(root), "set", "unused", "unused"]
    for operation in ("set", "reset"):
        action = deepcopy(data["actions"][0])
        action.update(key=operation, label=f"{operation} preference")
        action["preview"] = [
            *prefix, "preview", str(root), operation, "{field.value}", "{configuration_preview}",
        ]
        action["run"] = [
            *prefix, "apply", str(root), operation, "{field.value}", "{configuration_preview}",
        ]
        if operation == "set":
            data["actions"][0] = action
        else:
            data["actions"].append(action)
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "fixture.json").write_text(json.dumps(data))
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(registry))
    monkeypatch.setenv("AGENT_WORKTREES_PLUGINS_DIR", str(plugins))
    return root


def _tree(root):
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


async def _ready(app, pilot):
    engine = app.query_one(PickerScreen)
    await _wait_for_initial_setup(pilot, engine)
    assert "Preferences" in engine.htabs, engine.htabs
    engine._open_cfgmenu()
    while engine.htabs[engine.htab] != "Preferences":
        engine._rotate_machine(1)
        await pilot.pause()
    await _wait(pilot, lambda: engine._task_state()[0] in ("ready", "error"))
    assert engine._task_state()[0] == "ready", engine._task_state()
    return engine


def _edit(engine, operation="set"):
    reg = engine._reg_pivot()
    row = engine._task_rows()[0]
    action = next(action for action in reg.actions if action.key == operation)
    engine._open_pivot_form(reg, action, row, engine._task_action_ctx(reg, row), row["title"])


async def _preview(app, pilot, value="chosen"):
    await pilot.pause()
    assert isinstance(app.screen, ConfigurationFormScreen)
    editor = app.screen
    editor._q[0]["primary"].value = value
    editor.dismiss(editor._collect())
    await _wait(pilot, lambda: isinstance(app.screen, ConfigurationPreviewScreen)
                and not app.screen.query_one("#config-preview-retry", Button).disabled)
    return app.screen


@pytest.mark.parametrize("stage", ["cancel", "set", "reset"])
def test_production_ui_real_owner_set_reset_cancel_and_unchanged_sources(tmp_path, monkeypatch, stage):
    root = _owner(tmp_path, monkeypatch)
    override = str(Path("local") / "override.json")
    if stage == "reset":
        (root / override).write_text(json.dumps({"mode": "literal-{configuration_preview}"}))
    original = _tree(root)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(118, 40)) as pilot:
            engine = await _ready(app, pilot)
            if stage == "cancel":
                engine._pivot_runtime(engine._reg_pivot()).repoll("")
                await _wait(pilot, lambda: engine._task_state()[0] == "ready")
            assert _tree(root) == original
            if stage == "reset":
                source = root / "preset" / "config.json"
                source.write_text(json.dumps({"mode": "changed-inheritance", "secret": "SENTINEL-DO-NOT-RENDER"}))
                expected = _tree(root)
                expected.pop(override)
                _edit(engine, "reset")
                await _preview(app, pilot)
                await pilot.click("#config-preview-apply")
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                            and not app.screen.pending)
                assert _tree(root) == expected
                app.screen.action_close()
                await _wait(pilot, lambda: engine._task_rows()[0]["effective"] == "changed-inheritance")
                return
            _edit(engine)
            preview = await _preview(app, pilot)
            assert preview.preview and not preview.preview.blocked
            assert app.focused.id == "config-preview-cancel"
            assert "Existing sessions are unchanged" in preview.preview.describe()
            assert "chosen" in str(preview.query_one("#config-preview").render())
            assert "SENTINEL" not in str(preview.query_one("#config-preview").render())
            assert _tree(root) == original
            if stage == "cancel":
                await pilot.press("enter")
                await pilot.pause()
                assert not isinstance(app.screen, ConfigurationPreviewScreen)
                assert _tree(root) == original
                return
            await pilot.click("#config-preview-edit")
            await pilot.pause()
            assert app.screen._collect()["value"] == "chosen"
            preview = await _preview(app, pilot, "literal-{configuration_preview}")
            await pilot.click("#config-preview-apply")
            await _wait(pilot, lambda: isinstance(app.screen, ConfigurationResultScreen)
                        and not app.screen.pending)
            receipt = str(app.screen.query_one("#config-result").render())
            assert "set completed" in receipt and "owner diagnostic" in receipt
            assert "SENTINEL" not in receipt
            after_set = _tree(root)
            assert after_set == {**original, override: after_set[override]}
            app.screen.action_close()
            await _wait(pilot, lambda: engine._task_rows()[0]["effective"] == "literal-{configuration_preview}")

    asyncio.run(run())


@pytest.mark.parametrize("changed", ["repository", "knowledge", "plugin", "preset", "local"])
def test_provider_revalidates_stale_preview_and_legacy_blank_token(tmp_path, monkeypatch, changed):
    root = _owner(tmp_path, monkeypatch)
    reg = _parse(json.loads((tmp_path / "registry" / "fixture.json").read_text()))
    runtime = tasks.RegisteredPivotRuntime(reg)
    action = reg.actions[0]
    ok, output = runtime.preview_resolved(format_form_template(action.preview, {}, {"value": "chosen"}))
    assert ok
    token = parse_configuration_preview(output, TARGET).token
    target = root / changed / ("override.json" if changed == "local" else "config.json")
    target.write_text(json.dumps({"mode": "concurrent"}))
    baseline = _tree(root)
    for context in ({}, {"configuration_preview": token}, {"configuration_preview": "forged"}):
        ok, message = runtime.run_resolved(
            format_form_template(action.run, context, {"value": "chosen"}), preserve_output=True,
        )
        assert not ok and "Preview conflict" in message and _tree(root) == baseline


def test_owner_serializes_competing_applies_and_redacts_actual_outputs_and_argv(tmp_path, monkeypatch):
    root = _owner(tmp_path, monkeypatch)
    reg = _parse(json.loads((tmp_path / "registry" / "fixture.json").read_text()))
    runtime = tasks.RegisteredPivotRuntime(reg)
    state, rows, error, _ = runtime._exec_list({})
    assert state == "ready", error
    assert "SENTINEL" not in json.dumps(rows)
    values = {"value": "chosen", "configuration_preview": "field-spoof"}
    preview_argv = format_form_template(reg.actions[0].preview, {}, values)
    ok, output = runtime.preview_resolved(preview_argv)
    assert ok and "SENTINEL" not in output
    token = parse_configuration_preview(output, TARGET).token
    argv = format_form_template(reg.actions[0].run, {"configuration_preview": token}, values)
    assert argv[-1] == token and "SENTINEL" not in json.dumps([preview_argv, argv])
    before = _tree(root)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: runtime.run_resolved(argv, preserve_output=True), range(2)))
    assert sorted(ok for ok, _ in results) == [False, True]
    assert any("Preview conflict" in message for _, message in results)
    assert all("SENTINEL" not in message for _, message in results)
    after = _tree(root)
    override = str(Path("local") / "override.json")
    assert after == {**before, override: after[override]}


def test_pending_preview_cancel_and_late_reply_cannot_authorize_new_context(tmp_path, monkeypatch):
    root = _owner(tmp_path, monkeypatch)
    original = _tree(root)
    entered, release = threading.Event(), threading.Event()
    actual = tasks.RegisteredPivotRuntime.preview_resolved

    def slow(runtime, argv):
        entered.set()
        assert release.wait(10)
        return actual(runtime, argv)

    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "preview_resolved", slow)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            engine = await _ready(app, pilot)
            _edit(engine)
            await pilot.pause()
            app.screen.dismiss(app.screen._collect())
            try:
                await _wait(pilot, entered.is_set)
                old = app.screen
                assert isinstance(old, ConfigurationPreviewScreen)
                assert old.query_one("#config-preview-apply", Button).disabled
                await pilot.press("escape")
                _edit(engine)
                await pilot.pause()
                assert isinstance(app.screen, ConfigurationFormScreen)
                release.set()
                await _wait(pilot, lambda: not any(t.is_alive() for t in engine._bg_threads))
                assert isinstance(app.screen, ConfigurationFormScreen) and old.preview is None
                assert _tree(root) == original
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize("changed", ["section", "project", "unbound", "row", "action"])
def test_context_changes_after_preview_block_explicit_apply(tmp_path, monkeypatch, changed):
    root = _owner(tmp_path, monkeypatch)
    original = _tree(root)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            engine = await _ready(app, pilot)
            _edit(engine)
            await _preview(app, pilot)
            if changed == "section":
                engine._rotate_config_section(1)
                engine._rotate_config_section(-1)
            elif changed == "project":
                picker_context.set_project("another-project")
            elif changed == "unbound":
                picker_context.reset()
            elif changed == "row":
                engine._task_rows()[0]["configuration"]["target"]["resource"] = "another"
            elif changed == "action":
                engine._configuration_preview_generation = object()
            await pilot.click("#config-preview-apply")
            await pilot.pause()
            assert not isinstance(app.screen, ConfigurationResultScreen)
            assert "context changed" in engine.debug
            assert _tree(root) == original

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["malformed", "blocking", "target", "nonzero", "timeout", "context"])
def test_preview_errors_block_apply_and_are_retryable(tmp_path, monkeypatch, failure):
    root = _owner(tmp_path, monkeypatch)
    original = _tree(root)
    actual = tasks.RegisteredPivotRuntime.preview_resolved

    def failed(runtime, argv):
        if failure == "malformed":
            return actual(runtime, [sys.executable, "-c", "print('invalid JSON')"])
        if failure in ("blocking", "target"):
            data = _payload()
            if failure == "blocking":
                data["findings"][0]["severity"] = "error"
            else:
                data["target"] = {**TARGET, "resource": "another"}
            return actual(runtime, [sys.executable, "-c", f"print({json.dumps(data)!r})"])
        if failure == "nonzero":
            return actual(runtime, [
                sys.executable, "-c", "import sys; print('owner failure', file=sys.stderr); sys.exit(1)",
            ])
        if failure == "timeout":
            return actual(runtime, [sys.executable, "-c", "import time; time.sleep(10)"])
        return actual(runtime, argv)

    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "preview_resolved", failed)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            engine = await _ready(app, pilot)
            if failure == "timeout":
                monkeypatch.setattr(tasks, "LIST_TIMEOUT", .1)
            _edit(engine)
            if failure == "context":
                engine._configuration_preview_generation = object()
            preview = await _preview(app, pilot)
            assert preview.query_one("#config-preview-apply", Button).disabled
            assert _tree(root) == original
            if failure != "context":
                monkeypatch.setattr(tasks.RegisteredPivotRuntime, "preview_resolved", actual)
                monkeypatch.setattr(tasks, "LIST_TIMEOUT", 20)
                await pilot.click("#config-preview-retry")
                await _wait(pilot, lambda: isinstance(app.screen, ConfigurationPreviewScreen)
                            and app.screen.preview is not None)
                assert not app.screen.query_one("#config-preview-apply", Button).disabled
                assert _tree(root) == original

    asyncio.run(run())
