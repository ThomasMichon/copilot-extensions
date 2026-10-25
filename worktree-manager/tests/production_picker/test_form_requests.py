"""Original requests survive row/card loss without granting saved argv authority."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import sys

import pytest
from textual.widgets import Input, OptionList, Static

from worktree_manager.form_requests import (
    prepared_record, recovery_records, recovery_text, request_contract,
    resolve_request, retain_request,
)
from worktree_manager.plugin_contracts import ContractError, parse_manifest
from worktree_manager.production_picker.picker_tui import pivots
from worktree_manager.production_picker.picker_tui import engine_form_requests as requests

pytestmark = pytest.mark.contract("worktree_manager.form_requests")

SPEC = {
    "prepare": ["provider", "prepare", "{task_id}", "{request_id}", "{json.owner_scope}", "{fields}"],
    "receipt": ["provider", "receipt", "{json.prepared_request}"],
    "scope_from": "owner_scope", "prepared_scope_from": "scope", "identity_from": "id",
}


def manifest():
    return {
        "schema_version": 1, "label": "Examples", "list": ["provider", "list", "--machine", "{machine}"],
        "view_actions": [{
            "key": "recover", "label": "Retained responses", "kind": "internal", "verb": "recover-forms",
        }],
        "actions": [{
            "key": "answer", "label": "Answer", "kind": "form", "fields_from": "card.fields",
            "request": deepcopy(SPEC), "run": ["provider", "submit", "{json.prepared_request}"],
        }],
    }


def registered(raw=None):
    return pivots.parse_manifest(raw or manifest(), name="examples", source_path="owned.json")


def form_action(reg):
    return next(action for action in (*reg.actions, *reg.view_actions) if action.form)


def context():
    return {
        "request_id": "original/id", "task_id": "task-one", "machine": "host",
        "owner_scope": {"installation": "install-one", "queue": "queue-one", "machine": "host"},
        "unrelated_result": "not retained",
    }


def original(reg=None):
    reg = reg or registered()
    ctx = context()
    return prepared_record(
        reg, form_action(reg), ctx, {"answer": "literal {task_id}\n[red]not markup"},
        {"card": "original", "revision": "original-revision"},
        {"id": ctx["request_id"], "scope": ctx["owner_scope"], "wire": "original"},
    )


@pytest.mark.parametrize("view", [False, True])
def test_both_parsers_preserve_request_abi_and_json_tokens_are_single_pass(view):
    raw = manifest()
    if view:
        raw["view_actions"].append(raw["actions"].pop())
    general = parse_manifest(raw, name="examples", marketplace="demo", plugin="provider", source_path="owned.json")
    reg = registered(raw)
    assert form_action(general.pivot).form == form_action(reg).form
    ctx = context()
    assert json.loads(pivots.format_form_template(
        ["{json.owner_scope}"], ctx, {},
    )[0]) == ctx["owner_scope"]
    ctx["prepared_request"] = {"answer": "{task_id}"}
    assert json.loads(pivots.format_form_template(
        ["{json.prepared_request}"], ctx, {},
    )[0]) == {"answer": "{task_id}"}


@pytest.mark.parametrize("mutation", ["prepare", "receipt", "scope_from", "unknown", "nonform"])
def test_unknown_or_malformed_request_contract_is_incompatible(mutation):
    raw = manifest()
    action = raw["actions"][0]
    if mutation == "unknown":
        action["request"]["future"] = True
    elif mutation == "nonform":
        action["kind"] = "command"
    else:
        action["request"][mutation] = []
    with pytest.raises(ContractError, match="request"):
        parse_manifest(raw, name="examples", marketplace="demo", plugin="provider", source_path="owned.json")
    with pytest.raises(ValueError, match="request"):
        registered(raw)


@pytest.mark.parametrize("view", [False, True])
def test_configuration_never_silently_ignores_an_operational_request_contract(view):
    raw = manifest()
    raw.update(placement="config", scope="global")
    if view:
        raw["view_actions"].append(raw["actions"].pop())
    with pytest.raises(ContractError, match="request forms require an operational pivot"):
        parse_manifest(raw, name="examples", marketplace="demo", plugin="provider", source_path="owned.json")
    with pytest.raises(ValueError, match="request forms require an operational pivot"):
        registered(raw)


def test_prepare_and_receipt_commands_are_rewritten_to_current_payload(tmp_path, monkeypatch):
    import os
    from worktree_manager.production_picker.picker_tui.pivot_registry_materialization import _rewrite_manifest_commands
    root = tmp_path / "provider"
    binary = root / "bin" / ("provider.cmd" if os.name == "nt" else "provider")
    binary.parent.mkdir(parents=True)
    binary.write_text("provider", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", "")
    rewritten = _rewrite_manifest_commands(manifest(), root=root, require_targets=True)
    spec = rewritten["actions"][0]["request"]
    assert spec["prepare"][0] == spec["receipt"][0] == str(binary)
    binary.unlink()
    (root / "payload-invocation.json").write_text('{"command":"provider"}', encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        _rewrite_manifest_commands(manifest(), root=root, require_targets=True)


def test_recovery_rescans_current_attributed_template_and_refuses_removed_or_manual_provider(
    tmp_path, monkeypatch,
):
    from test_pivot_registry import _active_report, _command
    from worktree_manager.production_picker.picker_tui import pivot_registry_scan
    root, registry = tmp_path / "provider", tmp_path / "pivots"
    _command(root, "provider")
    template = root / "pivots" / "examples.json"
    template.parent.mkdir(parents=True)
    template.write_text(json.dumps(manifest()), encoding="utf-8")
    activation = _active_report("provider@example-marketplace", root)
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(registry))
    monkeypatch.setattr(pivot_registry_scan, "resolve_active_plugins", lambda: activation)
    report = pivots.scan_pivot_registry(registry, activation_report=activation)
    reg = report.pivots[0]
    record = original(reg)
    assert resolve_request(record, requests.current_request_registrations())[0] == reg
    raw = manifest()
    raw["actions"][0]["request"]["prepare"].append("changed-contract")
    template.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unavailable or changed"):
        resolve_request(record, requests.current_request_registrations())
    template.unlink()
    assert requests.current_request_registrations() == []
    manual = registry / "manual.json"
    manual.write_text(json.dumps(manifest()), encoding="utf-8")
    assert requests.current_request_registrations() == []


@pytest.mark.parametrize("uncertainty", ["registry", "entry", "template", "activation"])
def test_indeterminate_reads_never_reuse_display_cache_as_execution_authority(
    tmp_path, monkeypatch, uncertainty,
):
    from test_pivot_registry import _active_report, _command
    from dropin_registry import ScanAuthority, ScanSnapshot
    from plugin_activation import ActivationReport
    from worktree_manager.production_picker.picker_tui import pivot_registry_scan, tasks
    root, registry = tmp_path / "provider", tmp_path / "pivots"
    _command(root, "provider")
    template = root / "pivots" / "examples.json"
    template.parent.mkdir(parents=True)
    template.write_text(json.dumps(manifest()), encoding="utf-8")
    activation = _active_report("provider@example-marketplace", root)
    monkeypatch.setenv("AGENT_WORKTREES_PIVOTS_DIR", str(registry))
    monkeypatch.setattr(pivot_registry_scan, "resolve_active_plugins", lambda: activation)
    reg = pivots.scan_pivot_registry(registry, activation_report=activation).pivots[0]
    record = original(reg)
    path = retain_request(tmp_path / "drafts", record)
    cached = dict(pivot_registry_scan._LAST_KNOWN[str(registry.resolve())])
    if uncertainty == "registry":
        monkeypatch.setattr(
            pivot_registry_scan, "scan_directory",
            lambda *_args, **_kwargs: ScanSnapshot("pivots", ScanAuthority.INDETERMINATE),
        )
    elif uncertainty == "entry":
        read = pivot_registry_scan._read_json

        def unreadable(path):
            if path == registry / "examples.json":
                raise PermissionError("entry temporarily unreadable")
            return read(path)

        monkeypatch.setattr(pivot_registry_scan, "_read_json", unreadable)
    elif uncertainty == "template":
        def unreadable_template(*_args):
            raise PermissionError("template temporarily unreadable")

        monkeypatch.setattr(pivot_registry_scan, "_read_verified_template", unreadable_template)
    else:
        activation = ActivationReport(ScanAuthority.INDETERMINATE, {})

    def unexpected(*_args, **_kwargs):
        pytest.fail("uncertain attribution must not invoke any provider command")

    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "run_resolved", unexpected)
    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "run_json_resolved", unexpected)
    assert pivots.scan_pivot_registry(registry, materialize=False).pivots == [reg]
    with pytest.raises(ValueError, match="unavailable or changed"):
        requests.current_request_action(record["contract"])
    for receipt in (False, True):
        with pytest.raises(ValueError, match="unavailable or changed"):
            requests.run_retained_request(path, record, receipt=receipt)
    assert pivot_registry_scan._LAST_KNOWN[str(registry.resolve())] == cached
    assert path.exists()


def test_retained_request_is_detached_and_never_captures_unreferenced_task_output(tmp_path):
    reg = registered()
    ctx = context()
    prepared = {"id": ctx["request_id"], "scope": ctx["owner_scope"], "wire": ["original"]}
    record = prepared_record(reg, reg.actions[0], ctx, {"answer": "one"}, {"card": "original"}, prepared)
    path = retain_request(tmp_path, record)
    prepared["wire"].append("changed")
    ctx["owner_scope"]["queue"] = "changed"
    assert record["prepared_request"]["wire"] == ["original"]
    assert record["context"]["owner_scope"]["queue"] == "queue-one"
    assert "unrelated_result" not in record["context"]
    restored = recovery_records(tmp_path)
    assert restored == [(path, record, "")]
    assert resolve_request(restored[0][1], [reg]) == (reg, reg.actions[0])


@pytest.mark.parametrize("part", ["id", "scope", "nan"])
def test_prepare_rejects_unbound_nonfinite_or_wrong_identity(part):
    reg, ctx = registered(), context()
    prepared = {"id": ctx["request_id"], "scope": ctx["owner_scope"]}
    if part == "nan":
        prepared["number"] = float("nan")
    else:
        prepared[part] = "changed"
    with pytest.raises(ValueError):
        prepared_record(reg, reg.actions[0], ctx, {}, {}, prepared)


def test_previous_legacy_wrong_scope_and_corrupt_values_stay_visible_read_only(tmp_path):
    (tmp_path / "old.json").write_text('{"values":{"answer":"old"}}', encoding="utf-8")
    (tmp_path / "corrupt.json").write_text("{", encoding="utf-8")
    (tmp_path / "scalar.json").write_text("[]", encoding="utf-8")
    reg = registered()
    record = original(reg)
    record["prepared_request"]["scope"]["queue"] = "wrong"
    path = retain_request(tmp_path, record)
    records = recovery_records(tmp_path)
    assert len(records) == 4 and sum(bool(item[2]) for item in records) == 3
    assert next(item[1] for item in records if item[0].name == "corrupt.json") == {"unparsed_text": "{"}
    with pytest.raises(ValueError, match="scope"):
        resolve_request(record, [reg])
    assert "wrong" in recovery_text(path, record)
    assert len(list(tmp_path.glob("*.json"))) == 4


@pytest.mark.parametrize("change", [
    "provider", "installation", "action", "form", "unavailable", "source_owner", "scope", "confirm",
])
def test_changed_current_provider_or_contract_is_not_saved_execution_authority(change):
    reg = registered()
    record = original(reg)
    if change == "provider":
        current = replace(reg, source_path="another.json")
    elif change == "installation":
        current = replace(reg, list_cmd=("other-installation", "list"))
    elif change == "action":
        current = replace(reg, actions=(replace(reg.actions[0], run=("foreign",)),))
    elif change == "form":
        current = replace(reg, actions=(replace(reg.actions[0], form={"fields_from": "different"}),))
    elif change == "source_owner":
        current = replace(reg, source_owner="another@marketplace")
    elif change == "scope":
        current = replace(reg, scope="global")
    elif change == "confirm":
        current = replace(reg, actions=(replace(reg.actions[0], confirm=True),))
    else:
        current = replace(reg, actions=())
    with pytest.raises(ValueError, match="unavailable or changed"):
        resolve_request(record, [current])
    assert record == original(reg)


def provider(tmp_path, *, view=False):
    script = tmp_path / "provider.py"
    script.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "root=Path(sys.argv[1]);op=sys.argv[2]\n"
        "if op=='prepare':\n"
        " print(json.dumps({'id':sys.argv[4],'scope':json.loads(sys.argv[5]),"
        "'fields':sys.argv[6:],'sender':'original sender','wake':False}));sys.exit(0)\n"
        "request=json.loads(sys.argv[3]);path=root/'accepted.json'\n"
        "if op=='submit' and not path.exists():\n"
        " path.write_text(json.dumps(request));print('accepted response lost',file=sys.stderr);sys.exit(7)\n"
        "if not path.exists():\n"
        " print('receipt missing; not permission to send',file=sys.stderr);sys.exit(4)\n"
        "assert json.loads(path.read_text())==request\n"
        "print('original receipt '+json.dumps(request)+'\\n'+'evidence '*400)\n",
        encoding="utf-8",
    )
    raw = manifest()
    action = raw["actions"][0]
    for verb in ("prepare", "receipt"):
        action["request"][verb][:1] = [sys.executable, str(script), str(tmp_path)]
    action["run"][:1] = [sys.executable, str(script), str(tmp_path)]
    if view:
        raw["view_actions"].append(raw["actions"].pop())
    return registered(raw)


@pytest.mark.parametrize("view", [False, True])
def test_actual_runtime_replay_after_response_loss_and_restart_keeps_original_wire(tmp_path, monkeypatch, view):
    reg = provider(tmp_path, view=view)
    action = form_action(reg)
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [reg])
    assert requests.current_request_action(request_contract(reg, action)) == (reg, action)
    ctx = context()
    from worktree_manager.production_picker.picker_tui import tasks
    argv = pivots.format_form_template(action.form["request"]["prepare"], ctx, {"answer": "{task_id}"})
    ok, output = tasks.RegisteredPivotRuntime(reg).run_json_resolved(argv)
    assert ok, output
    record = prepared_record(reg, action, ctx, {"answer": "{task_id}"}, {}, json.loads(output))
    path = retain_request(tmp_path / "drafts", record)
    ok, output = requests.run_retained_request(path, record)
    assert not ok and "accepted response lost" in output and path.exists()
    restarted = recovery_records(path.parent)[0][1]
    for receipt in (True, False):
        ok, output = requests.run_retained_request(path, restarted, receipt=receipt)
        assert ok and len(output) > 2000 and "original sender" in output
    accepted = json.loads((tmp_path / "accepted.json").read_text())
    assert accepted == record["prepared_request"] and accepted["wake"] is False
    assert len(list(path.parent.glob("*.evidence-*.json"))) == 3
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [])
    with pytest.raises(ValueError, match="unavailable"):
        requests.run_retained_request(path, restarted)
    assert path.exists()


@pytest.mark.parametrize("view", [False, True])
def test_native_prepare_persists_before_submit_and_global_recovery_cancel_never_erases(
    tmp_path, monkeypatch, view,
):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
    from worktree_manager.production_picker.picker_tui.engine_action_confirm import ActionConfirmScreen
    from worktree_manager.production_picker.picker_tui.engine_dialogs import TaskMenuScreen
    from worktree_manager.production_picker.picker_tui.steering import PivotFormScreen

    directory = tmp_path / "drafts"
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(directory))
    reg = provider(tmp_path, view=view)
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [reg])

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            runtime = picker._pivot_runtime(reg)
            monkeypatch.setattr(runtime, "ensure", lambda *_args: None)
            rec = {**context(), "card": {
                "title": "Original question", "body": "Read before confirming",
                "fields": [{"name": "answer", "type": "text"}],
            }}
            picker._open_pivot_form(reg, form_action(reg), rec, rec, "Original question")
            await _wait(pilot, lambda: isinstance(app.screen, PivotFormScreen))
            form = app.screen
            form.query_one(Input).value = "literal {task_id} [red]plain"
            form._confirm()
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            assert "accepted response lost" in str(app.screen.query_one("#command-result", Static).content)
            record_path = next(path for path, _record, diagnostic in recovery_records(directory) if not diagnostic)
            assert record_path.exists() and form._draft_path().exists()
            app.screen.action_close()
            await pilot.pause()
            # No registered provider pane or board row is needed for global recovery.
            await pilot.press("ctrl+shift+r")
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
            app.screen.dismiss(0)
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen)
                        and len(app.screen.query_one(OptionList)._options) == 3)
            app.screen.dismiss(2)
            await _wait(pilot, lambda: isinstance(app.screen, ActionConfirmScreen))
            await pilot.press("escape")
            await _wait(pilot, lambda: not isinstance(app.screen, ActionConfirmScreen))
            assert record_path.exists()
            assert len(list(directory.glob("*.evidence-*.json"))) == 1
            record = json.loads(record_path.read_text())
            picker._run_form_recovery(record_path, record, receipt=True)
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            result = app.screen.query_one("#command-result", Static)
            assert "original receipt" in str(result.content) and "[red]plain" in str(result.content)
            assert record_path.exists()
            app.screen.action_close()
            await pilot.pause()
            monkeypatch.setattr(requests, "current_request_registrations", lambda: [])
            picker._open_form_recovery(record_path, record, "")
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
            assert app.screen.query_one(OptionList).option_count == 1
            app.screen.dismiss(0)
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            assert "unavailable or changed" in str(app.screen.query_one("#command-result", Static).content)
            assert len(list(directory.glob("*.evidence-*.json"))) == 2
            assert record_path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["write", "publication", "directory"])
def test_local_request_write_failure_prevents_any_submit(tmp_path, monkeypatch, failure):
    from worktree_manager import form_draft_storage as storage
    from worktree_manager.production_picker.picker_tui.engine_form_requests import PivotFormRequestActionsMixin
    reg = provider(tmp_path)
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [reg])
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "new" / "drafts"))

    def denied(*_args, **_kwargs):
        raise OSError("durability barrier failed")

    if failure == "write":
        monkeypatch.setattr(storage.os, "fsync", denied)
    elif storage.os.name == "nt":
        move = storage._windows_move

        def fail_barrier(source, destination, *, replace):
            if replace == (failure == "publication"):
                denied()
            move(source, destination, replace=replace)

        monkeypatch.setattr(storage, "_windows_move", fail_barrier)
    else:
        monkeypatch.setattr(storage.os if failure == "publication" else storage,
                            "replace" if failure == "publication" else "_sync_directory", denied)
    screens = []

    class Picker(PivotFormRequestActionsMixin):
        app = type("Screens", (), {"push_screen": lambda _self, screen: screens.append(screen)})()

        def _run_bg(self, label, work, done, **_kwargs):
            done(work())

        def _run_pivot_form_submit(self, *_args, **_kwargs):
            pytest.fail("submit must not leave the client before durable retention")

    Picker()._prepare_pivot_form_submit(
        reg, reg.actions[0], context(), "Example", {"answer": "keep"},
        draft_binding={"card": "original"}, draft_path=tmp_path / "draft.json",
    )
    assert screens and not (tmp_path / "accepted.json").exists()


def test_corrupt_observation_does_not_hide_original_or_other_observations(tmp_path):
    from worktree_manager.form_requests import observe_request
    record = original()
    path = retain_request(tmp_path, record)
    bad = path.with_name(f"{path.stem}.evidence-a.json")
    bad.write_bytes(b"\xff")
    observe_request(path, operation="receipt", ok=False, output="owner unavailable")
    text = recovery_text(path, record)
    assert bad.name in text and "UnicodeDecodeError" in text
    assert "owner unavailable" in text and record["prepared_request"]["id"] in text
    assert json.loads(path.read_text(encoding="utf-8")) == record


def test_recovered_retry_reestablishes_durability_and_still_allows_receipt_reads(
    tmp_path, monkeypatch,
):
    import os
    from types import SimpleNamespace
    from worktree_manager import form_draft_storage as storage
    from worktree_manager.production_picker.picker_tui import tasks
    reg, record = registered(), original()
    directory = tmp_path / "new-parent" / "new-child"
    directory.mkdir(parents=True)
    calls = []

    def denied(_path):
        raise OSError("directory publication failed")

    monkeypatch.setattr(storage, "os", SimpleNamespace(
        name="posix", fsync=os.fsync, replace=os.replace, unlink=os.unlink,
    ))
    monkeypatch.setattr(storage, "_sync_directory", denied)
    with pytest.raises(OSError, match="directory publication failed"):
        retain_request(directory, record)
    path, restored, diagnostic = recovery_records(directory)[0]
    assert restored == record and not diagnostic
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [reg])

    def run(_runtime, argv, **_kwargs):
        calls.append(argv)
        return True, "original owner receipt"

    monkeypatch.setattr(tasks.RegisteredPivotRuntime, "run_resolved", run)
    for _ in range(2):
        with pytest.raises(OSError, match="directory publication failed"):
            requests.run_retained_request(path, restored)
        assert calls == []
        assert json.loads(path.read_text(encoding="utf-8")) == record
    # A read is still allowed even if its separate local observation cannot flush.
    ok, output = requests.run_retained_request(path, restored, receipt=True)
    assert not ok and "original owner receipt" in output and len(calls) == 1
    assert calls[0][1] == "receipt"
    monkeypatch.setattr(storage, "_sync_directory", lambda _path: None)
    assert requests.run_retained_request(path, restored) == (True, "original owner receipt")
    assert len(calls) == 2 and calls[-1][1] == "submit"


@pytest.mark.parametrize("failure", [False, True])
def test_inspection_keeps_ui_responsive_while_observation_read_is_held(tmp_path, monkeypatch, failure):
    import threading
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
    from worktree_manager.production_picker.picker_tui.engine_dialogs import TaskMenuScreen

    entered, release = threading.Event(), threading.Event()
    ui_thread = threading.get_ident()
    path = retain_request(tmp_path, original())

    def held_read(*_args):
        assert threading.get_ident() != ui_thread, "inspection I/O must not run on UI thread"
        entered.set()
        assert release.wait(5), "test must release worker while UI remains responsive"
        if failure:
            raise OSError("observation directory unreadable")
        return "complete original observation"

    monkeypatch.setattr(requests, "recovery_text", held_read)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            picker._show_form_recovery(path, original(), "Provider unavailable; read-only")
            await _wait(pilot, lambda: isinstance(app.screen, TaskMenuScreen))
            app.screen.dismiss(0)
            try:
                await _wait(pilot, lambda: entered.is_set())
                assert isinstance(app.screen, CommandResultScreen) and app.screen.pending
                completed = asyncio.Event()
                app.call_later(completed.set)
                await asyncio.wait_for(completed.wait(), 1)
                assert not release.is_set() and app.screen.pending
            finally:
                release.set()
            await _wait(pilot, lambda: not app.screen.pending)
            text = str(app.screen.query_one("#command-result", Static).content)
            assert ("observation directory unreadable" if failure else "complete original observation") in text
            assert path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_prepared_submit_refreshes_original_scope_and_retains_path_even_on_resolution_error(
    tmp_path, monkeypatch, failure,
):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
    reg = registered()
    record = original(reg)
    path = retain_request(tmp_path, record)
    refreshed = []

    def execute(*_args):
        if failure:
            raise ValueError("provider changed")
        return True, "original receipt"

    monkeypatch.setattr(requests, "run_retained_request", execute)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            runtime = picker._pivot_runtime(reg)
            monkeypatch.setattr(runtime, "ensure", lambda scope: refreshed.append(scope))
            monkeypatch.setattr(picker, "_pivot_scope_key", lambda: "different-selected-machine")
            picker._run_pivot_form_submit(
                reg, reg.actions[0], record["context"], "Original question", record["values"],
                retained_request=(path, record),
            )
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            text = str(app.screen.query_one("#command-result", Static).content)
            assert str(path) in text and path.exists()
            assert refreshed == ([] if failure else ["host"])
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["success", "uncertain", "blocked", "receipt"])
@pytest.mark.parametrize("account_scoped", [False, True])
def test_recovered_submit_refreshes_original_scope_without_polling(
    tmp_path, monkeypatch, outcome, account_scoped,
):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
    reg = replace(registered(), scope="account" if account_scoped else "machine")
    record = original(reg)
    path = retain_request(tmp_path, record)
    invalidated, refreshed = [], []
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [reg])

    def execute(*_args, **_kwargs):
        if outcome == "blocked":
            raise OSError("durable retention failed; no command")
        return outcome != "uncertain", "original receipt or uncertain command output"

    monkeypatch.setattr(requests, "run_retained_request", execute)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            runtime = picker._pivot_runtime(reg)
            monkeypatch.setattr(runtime, "invalidate", invalidated.append)
            monkeypatch.setattr(runtime, "ensure", lambda scope: refreshed.append(scope))
            monkeypatch.setattr(picker, "_pivot_scope_key", lambda: "unrelated-current-machine")
            picker._run_form_recovery(path, record, receipt=outcome == "receipt")
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            mutated = outcome in ("success", "uncertain")
            assert invalidated == (["" if account_scoped else "host"] if mutated else [])
            assert refreshed == (["" if account_scoped else "host"] if mutated else [])
            assert path.exists() and str(path) in str(app.screen.query_one("#command-result", Static).content)
    asyncio.run(run())


def test_recovered_uncertain_submit_replaces_real_cached_rows_without_polling(tmp_path, monkeypatch):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
    reg = replace(provider(tmp_path), list_cmd=(
        sys.executable, "-c",
        "import json,sys;from pathlib import Path;"
        "print(json.dumps([dict(id='task-one',title='Answered' if Path(sys.argv[1]).exists() else 'Waiting')]))",
        str(tmp_path / "accepted.json"), "--machine", "{machine}",
    ))
    record = original(reg)
    path = retain_request(tmp_path / "drafts", record)
    monkeypatch.setattr(requests, "current_request_registrations", lambda: [reg])

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            runtime = picker._pivot_runtime(reg)
            runtime.ensure("host")
            await _wait(pilot, lambda: runtime.get("host")[0] == "ready")
            assert runtime.get("host")[1][0]["title"] == "Waiting"
            monkeypatch.setattr(picker, "_pivot_scope_key", lambda: "unrelated-current-machine")
            picker._run_form_recovery(path, record)
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            assert "accepted response lost" in str(app.screen.query_one("#command-result", Static).content)
            await _wait(pilot, lambda: runtime.get("host")[0] == "ready"
                        and runtime.get("host")[1][0]["title"] == "Answered")
            assert runtime.get("unrelated-current-machine")[0] == "idle"
            assert path.exists()
    asyncio.run(run())


def test_retained_response_menu_scrolls_without_growing_beyond_terminal(tmp_path, monkeypatch):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.engine_form_requests import RetainedResponsesMenu
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    for _ in range(40):
        retain_request(tmp_path, original())

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(80, 24)) as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            picker.action_recover_forms()
            await _wait(pilot, lambda: isinstance(app.screen, RetainedResponsesMenu))
            menu = app.screen
            options = menu.query_one(OptionList)
            assert options.option_count == 40
            options.highlighted = 39
            await pilot.pause()
            assert options.scroll_y > 0
            assert menu.query_one("#task-frame").region.bottom <= 24
            await pilot.press("escape")
            assert len(recovery_records(tmp_path)) == 40
    asyncio.run(run())


def test_reset_and_draft_cleanup_never_erase_uncertain_request_or_observations(tmp_path, monkeypatch):
    from test_form_drafts import Host, FIELDS, CARD, binding
    from worktree_manager.form_requests import observe_request
    from worktree_manager.production_picker.picker_tui.steering import PivotFormScreen
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    record = original()
    path = retain_request(tmp_path, record)
    observe_request(path, operation="submit", ok=False, output="response lost")

    async def run():
        form = PivotFormScreen(CARD, FIELDS, "Answer", task_id="task-one", draft_binding=binding())
        async with Host(form).run_test():
            form.query_one(Input).value = "new editable answer"
            assert form._write_draft()
            form._perform_reset()
            assert path.exists() and form.query_one(Input).value == ""
            assert len(list(tmp_path.glob("*.evidence-*.json"))) == 1
            assert json.loads(path.read_text()) == record
    asyncio.run(run())
