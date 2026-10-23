"""Generic form drafts retain exact provider and card provenance."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from textual.app import App
from textual.widgets import Input

from worktree_manager.form_drafts import draft_ack, form_binding, shared_values
from worktree_manager.plugin_contracts import ContractError, parse_manifest
from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.engine_form_drafts import PivotFormDraftActionsMixin
from worktree_manager.production_picker.picker_tui.steering import PivotFormScreen, _steer_draft_path

pytestmark = pytest.mark.contract("worktree_manager.form_drafts")

SPEC = {
    "save": ["provider", "save", "{task_id}", "{fields}", "{draft_scope.queue}", "{revision}"],
    "clear": ["provider", "clear", "{task_id}", "{draft_scope.queue}", "{revision}"],
    "scope_from": "draft_scope", "revision_from": "revision", "version_from": "version",
    "saved_version_from": "draft.version", "values_from": "draft.fields",
}
FIELDS = [{"name": "answer", "type": "text"}]
CARD = {"title": "A question", "body": "Read this", "status": "Waiting"}


def manifest(spec=SPEC):
    return {
        "schema_version": 1, "label": "Examples", "list": ["provider", "list"],
        "actions": [{
            "key": "answer", "label": "Answer", "kind": "form", "fields_from": "card.fields",
            "run": ["provider", "answer", "{fields}"], "draft": deepcopy(spec),
        }],
    }


def record():
    return {
        "task_id": "same-id", "version": "card-v1", "revision": "draft-v1",
        "draft_scope": {"queue": "queue-one", "installation": "install-one", "machine": "host"},
        "machine": "host",
        "draft": {"version": "card-v1", "fields": {"answer": "shared response"}},
    }


def registered(data=None):
    return pivots.parse_manifest(data or manifest(), name="examples", source_path="owned.json")


def binding(reg=None, rec=None, fields=FIELDS, card=CARD):
    reg, rec = reg or registered(), rec or record()
    return form_binding(reg, reg.actions[0], rec, rec, fields, card)


def test_both_parsers_retain_the_generic_draft_contract():
    raw = manifest()
    generic = parse_manifest(raw, name="examples", marketplace="demo", plugin="provider", source_path="owned.json")
    assert generic.pivot.actions[0].form["draft"] == registered(raw).actions[0].form["draft"]


@pytest.mark.parametrize("mutation", ["save", "clear", "scope_from", "unknown", "nonform"])
def test_malformed_drafts_are_visible_in_both_parsers(mutation):
    raw = manifest()
    action = raw["actions"][0]
    if mutation == "unknown":
        action["draft"]["extra"] = "unsupported"
    elif mutation == "nonform":
        action["kind"] = "command"
    else:
        action["draft"][mutation] = []
    with pytest.raises(ContractError, match="draft"):
        parse_manifest(raw, name="examples", marketplace="demo", plugin="provider", source_path="owned.json")
    with pytest.raises(ValueError, match="draft"):
        registered(raw)


@pytest.mark.parametrize("path", [".scope", "scope.", "scope..queue", "{scope}", "scope queue", "scope\0queue"])
def test_malformed_dotted_selectors_are_not_silently_accepted(path):
    raw = manifest()
    raw["actions"][0]["draft"]["scope_from"] = path
    with pytest.raises(ValueError, match="dotted path"):
        registered(raw)


def test_nested_draft_commands_use_the_contributing_payload_not_ambient_path(tmp_path, monkeypatch):
    import os
    from worktree_manager.production_picker.picker_tui.pivot_registry_materialization import (
        _rewrite_manifest_commands,
    )
    root = tmp_path / "owned"
    binary = root / "bin" / ("provider.cmd" if os.name == "nt" else "provider")
    binary.parent.mkdir(parents=True)
    binary.write_text("owned command", encoding="utf-8")
    binary.chmod(0o755)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    other = foreign / binary.name
    other.write_text("foreign command", encoding="utf-8")
    other.chmod(0o755)
    monkeypatch.setenv("PATH", str(foreign))
    rewritten = _rewrite_manifest_commands(manifest(), root=root, require_targets=True)
    draft = rewritten["actions"][0]["draft"]
    assert draft["save"][0] == draft["clear"][0] == str(binary)
    binary.unlink()
    (root / "payload-invocation.json").write_text('{"command":"provider"}', encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        _rewrite_manifest_commands(manifest(), root=root, require_targets=True)


@pytest.mark.parametrize("field", ["version", "revision", "draft_scope"])
def test_missing_owner_provenance_cannot_open_a_bound_form(field):
    rec = record()
    del rec[field]
    with pytest.raises(ValueError, match="provenance unavailable"):
        binding(rec=rec)


@pytest.mark.parametrize("field", ["version", "revision", "draft_scope", "machine", "task_id"])
def test_row_identity_changes_cannot_reuse_local_answers(field, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    first = binding()
    changed = record()
    changed[field] = {"queue": "different"} if field == "draft_scope" else "different"
    assert _steer_draft_path("same-id", first) != _steer_draft_path("same-id", binding(rec=changed))


def test_provider_installation_action_schema_and_card_bind_local_answers(tmp_path, monkeypatch):
    from dataclasses import replace
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    reg = registered()
    original = binding(reg)
    variants = [
        binding(replace(reg, source_path="other.json")),
        binding(replace(reg, list_cmd=("other-installation", "list"))),
        binding(replace(reg, actions=(replace(reg.actions[0], key="different"),))),
        binding(fields=[{"name": "answer", "type": "textarea"}]),
        binding(card={**CARD, "body": "Replacement question"}),
    ]
    assert all(value != original for value in variants)
    assert len({_steer_draft_path("same-id", value) for value in [original, *variants]}) == 6


def test_only_bound_shared_answers_restore_and_multichoice_decodes_once():
    rec = record()
    rec["draft"]["fields"]["choices"] = '["one","two,three"]'
    assert shared_values(SPEC, rec, [{"name": "choices", "type": "multichoice"}]) == {
        "answer": "shared response", "choices": ["one", "two,three"],
    }
    rec["draft"].pop("version")
    assert shared_values(SPEC, rec) == {}


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("corruption", ["version", "revision", "draft_scope", "draft"])
def test_incomplete_or_stale_acknowledgements_are_not_success(clear, corruption):
    ctx = record()
    result = {**record(), "revision": "draft-v2", "draft": None if clear else record()["draft"]}
    result.pop(corruption)
    with pytest.raises((ValueError, TypeError)):
        draft_ack(SPEC, ctx, json.dumps(result), clear=clear)


class Host(App):
    def __init__(self, form):
        super().__init__()
        self.form = form
        self.result = None

    def on_mount(self):
        self.push_screen(self.form, lambda result: setattr(self, "result", result))


def test_bound_restore_ignores_legacy_or_tampered_local_file_and_prefers_exact_local(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    identity = binding()
    legacy = _steer_draft_path("same-id")
    legacy.write_text('{"values":{"answer":"unbound"}}', encoding="utf-8")
    path = _steer_draft_path("same-id", identity)
    path.write_text(json.dumps({"binding": {"wrong": True}, "values": {"answer": "tampered"}}))

    async def run():
        first = PivotFormScreen(CARD, FIELDS, "Answer", task_id="same-id",
                                draft_binding=identity, draft_values={"answer": "shared"})
        async with Host(first).run_test() as pilot:
            assert first.query_one(Input).value == "shared"
            first.query_one(Input).value = "local {task_id}"
            first.action_save()
            await pilot.pause()
        second = PivotFormScreen(CARD, FIELDS, "Answer", task_id="same-id",
                                 draft_binding=identity, draft_values={"answer": "shared"})
        async with Host(second).run_test():
            assert second.query_one(Input).value == "local {task_id}"
    asyncio.run(run())
    assert legacy.exists()


def test_reset_waits_for_ack_and_preserves_answer_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    calls = []
    form = PivotFormScreen(CARD, FIELDS, "Answer", task_id="same-id",
                           draft_binding=binding(), on_clear_draft=lambda: calls.append("clear"))

    async def run():
        app = Host(form)
        async with app.run_test() as pilot:
            form.query_one(Input).value = "keep until acknowledged"
            form._perform_reset()
            form._confirm()
            form.action_save()
            form.action_escape_close()
            assert app.screen is form and form._draft_pending
            assert form.query_one(Input).value == "keep until acknowledged"
            assert form._draft_path().exists() and calls == ["clear"]
            form.draft_reset_complete(False)
            assert form.query_one(Input).value == "keep until acknowledged"
            assert form._draft_path().exists()
            form._perform_reset()
            old = form._draft_path()
            form.draft_reset_complete(True, "absent")
            await pilot.pause()
            assert app.screen is form and not form._draft_pending
            assert form.query_one(Input).value == ""
            assert form._draft_binding["revision_from"] == "absent"
            assert not old.exists()
    asyncio.run(run())


@pytest.mark.parametrize("clear", [False, True])
def test_real_provider_subprocess_uses_declared_argv_and_exact_observed_revision(
    tmp_path, monkeypatch, clear,
):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    rec = record()
    captured = tmp_path / "received.json"
    response = {**rec, "revision": "absent" if clear else "draft-v2",
                "draft": None if clear else {
                    "version": "card-v1", "fields": {"answer": "literal {revision}"},
                }}
    script = (
        "import json,sys;from pathlib import Path;"
        f"Path({str(captured)!r}).write_text(json.dumps(sys.argv[1:]));"
        f"print({json.dumps(response)!r});"
        "print('provider diagnostic',file=sys.stderr)"
    )
    script_path = tmp_path / "provider.py"
    script_path.write_text(script, encoding="utf-8")
    raw = manifest()
    for verb in ("save", "clear"):
        raw["actions"][0]["draft"][verb] = [
            sys.executable, str(script_path), "{task_id}", "{draft_scope.queue}", "{revision}", "{fields}",
        ]
    reg = registered(raw)
    runtime = tasks.RegisteredPivotRuntime(reg)
    refreshes = []
    monkeypatch.setattr(runtime, "ensure", lambda scope: refreshes.append(scope))
    form = PivotFormScreen(CARD, FIELDS, "Answer", task_id="same-id", draft_binding=binding(reg))
    resets = []
    monkeypatch.setattr(form, "draft_reset_complete", lambda ok, revision=None: resets.append((ok, revision)))

    class Picker(PivotFormDraftActionsMixin):
        app = type("NoErrors", (), {"push_screen": lambda *args: pytest.fail("unexpected error")})()
        def _pivot_runtime(self, value):
            assert value is reg
            return runtime
        def _pivot_scope_key(self):
            return "host"
        def _run_bg(self, label, work, done, **kwargs):
            done(work())
        def refresh(self):
            pass

    Picker()._run_pivot_form_draft(
        reg, reg.actions[0], rec, "Example", {"answer": "literal {revision}"}, form, clear=clear,
    )
    assert json.loads(captured.read_text()) == [
        "same-id", "queue-one", "draft-v1", "--field", "answer=literal {revision}",
    ]
    assert refreshes == ["host"]
    assert resets == ([(True, "absent")] if clear else [])
    assert rec["revision"] == ("absent" if clear else "draft-v1")


@pytest.mark.parametrize("fails", [False, True])
def test_native_open_restores_shared_answer_and_reset_rebases_only_its_draft_revision(
    tmp_path, monkeypatch, fails,
):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.steering import SubmitErrorScreen

    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    reg = registered()
    rec = {**record(), "id": "same-id", "title": "Example", "card": {**CARD, "fields": FIELDS}}
    calls = []

    class Runtime:
        def run_json_resolved(self, argv):
            calls.append(argv)
            if fails:
                return False, "card or shared draft changed; refresh"
            return True, json.dumps({**rec, "revision": "absent", "draft": None})
        def invalidate(self):
            pass
        def ensure(self, scope):
            assert scope == "host"

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            monkeypatch.setattr(picker, "_pivot_runtime", lambda value: Runtime())
            monkeypatch.setattr(picker, "_pivot_scope_key", lambda: "host")
            picker._open_pivot_form(reg, reg.actions[0], rec, rec, "Example")
            await _wait(pilot, lambda: isinstance(app.screen, PivotFormScreen))
            form = app.screen
            assert form.query_one(Input).value == "shared response"
            form.query_one(Input).value = "not yet submitted"
            form._perform_reset()
            await _wait(pilot, lambda: not form._draft_pending)
            assert calls == [["provider", "clear", "same-id", "queue-one", "draft-v1"]]
            if fails:
                assert isinstance(app.screen, SubmitErrorScreen)
                assert form.query_one(Input).value == "not yet submitted"
                assert form._draft_path().exists()
                assert form._draft_binding["revision_from"] == "draft-v1"
            else:
                assert app.screen is form and form.query_one(Input).value == ""
                assert form._draft_binding["revision_from"] == "absent"
            assert rec["revision"] == "draft-v1"
    asyncio.run(run())


@pytest.mark.parametrize("action", ["save", "confirm", "escape", "reset"])
def test_local_persistence_failure_never_dismisses_or_invokes_provider(tmp_path, monkeypatch, action):
    from worktree_manager import form_draft_storage
    from worktree_manager.production_picker.picker_tui.steering import SubmitErrorScreen

    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    monkeypatch.setattr(form_draft_storage.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("write denied")))
    calls = []
    form = PivotFormScreen(CARD, FIELDS, "Answer", task_id="same-id", draft_binding=binding(),
                           on_clear_draft=lambda: calls.append("must not run"))

    async def run():
        app = Host(form)
        async with app.run_test() as pilot:
            form.query_one(Input).value = "still here"
            method = {
                "save": form.action_save, "confirm": form._confirm,
                "escape": form.action_escape_close, "reset": form._perform_reset,
            }[action]
            method()
            await pilot.pause()
            assert isinstance(app.screen, SubmitErrorScreen) and app.result is None
            assert form.query_one(Input).value == "still here"
            assert not form._draft_pending and calls == []
    asyncio.run(run())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_verified_save_then_two_resets_and_restart_never_revive_cleared_answers(
    tmp_path, monkeypatch, cleanup_fails,
):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.steering import SubmitErrorScreen

    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    reg = registered()
    owner = {**record(), "id": "same-id", "title": "Example", "card": {**CARD, "fields": FIELDS},
             "revision": "absent", "draft": None}
    calls = []
    failed_path = []
    unlink = Path.unlink

    def fail_first_unlink(path, *args, **kwargs):
        if cleanup_fails and failed_path and path == failed_path[0]:
            failed_path.clear()
            raise OSError("first cleanup denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_unlink)

    class Runtime:
        def run_json_resolved(self, argv):
            assert argv[-1] == owner["revision"]
            calls.append(argv[1])
            if argv[1] == "save":
                value = argv[argv.index("--field") + 1].partition("=")[2]
                owner.update(revision="draft-v2", draft={"version": "card-v1", "fields": {"answer": value}})
            else:
                owner.update(revision="absent", draft=None)
            return True, json.dumps(owner)
        def invalidate(self):
            pass
        def ensure(self, scope):
            pass

    async def open_form(app, pilot):
        picker = app.query_one(PickerScreen)
        await _wait_for_initial_setup(pilot, picker)
        monkeypatch.setattr(picker, "_pivot_runtime", lambda value: Runtime())
        monkeypatch.setattr(picker, "_pivot_scope_key", lambda: "host")
        picker._open_pivot_form(reg, reg.actions[0], deepcopy(owner), deepcopy(owner), "Example")
        await _wait(pilot, lambda: isinstance(app.screen, PivotFormScreen))
        return app.screen

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            saved = await open_form(app, pilot)
            saved.query_one(Input).value = "must not return after Reset"
            saved.action_save()
            empty_path = saved._draft_path()
            failed_path.append(empty_path)
            picker = app.query_one(PickerScreen)
            await _wait(pilot, lambda: "save" in calls and (
                picker.debug.startswith("Save") or picker.debug.startswith("Shared Save")
            ))
            if cleanup_fails:
                assert empty_path.exists() and isinstance(app.screen, SubmitErrorScreen)
                app.screen.action_ack()
                await pilot.pause()
            else:
                assert not empty_path.exists()
            reset = await open_form(app, pilot)
            assert reset.query_one(Input).value == "must not return after Reset"
            reset._perform_reset()
            await _wait(pilot, lambda: calls.count("clear") == 1 and not reset._draft_pending)
            assert reset.query_one(Input).value == "" and owner["draft"] is None
            reset.query_one(Input).value = "new unsaved edits"
            reset._perform_reset()
            await _wait(pilot, lambda: calls.count("clear") == 2 and not reset._draft_pending)
            assert app.screen is reset and reset.query_one(Input).value == ""
            assert reset._draft_binding["version_from"] == "card-v1"
        restarted = PickerApp(_fixture_source(), live=False)
        async with restarted.run_test() as pilot:
            reopened = await open_form(restarted, pilot)
            assert reopened.query_one(Input).value == ""
            assert owner["revision"] == "absent" and owner["draft"] is None
            if cleanup_fails:
                assert empty_path.exists() and reopened._local_recoveries
                reopened.action_restore_local()
                assert reopened.query_one(Input).value == "must not return after Reset"
            else:
                assert not empty_path.exists()
    asyncio.run(run())
    assert calls == ["save", "clear", "clear"]


@pytest.mark.parametrize("clear", [False, True])
@pytest.mark.parametrize("shared", [False, True])
def test_delayed_acknowledgement_cannot_delete_a_newer_process_write(tmp_path, monkeypatch, clear, shared):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    identity = binding()
    form = PivotFormScreen(CARD, FIELDS, "Answer", task_id="same-id", draft_binding=identity,
                           on_clear_draft=(lambda: None) if shared else None)
    newer_path = []

    async def run():
        async with Host(form).run_test():
            form.query_one(Input).value = "first attempt"
            assert form._write_draft()
            acknowledged = form._draft_path()
            base = _steer_draft_path("same-id", identity)
            program = (
                "import json,sys;from pathlib import Path;"
                "from worktree_manager.form_draft_storage import write_draft;"
                "print(write_draft(Path(sys.argv[1]),json.loads(sys.argv[2]),'same-id',"
                f"{{'answer':'newer process response'}},pending={shared!r}))"
            )
            child = subprocess.run(
                [sys.executable, "-c", program, str(base), json.dumps(identity)],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert child.returncode == 0, child.stderr
            newer = Path(child.stdout.strip())
            newer_path.append(newer)
            assert newer != acknowledged
            if clear:
                form.draft_reset_complete(True, "absent" if shared else None)
            else:
                assert form._delete_draft()
            assert not acknowledged.exists() and newer.exists()
        restored = PivotFormScreen(CARD, FIELDS, "Answer", task_id="same-id", draft_binding=identity,
                                   on_clear_draft=(lambda: None) if shared else None)
        async with Host(restored).run_test():
            if shared:
                assert restored.query_one(Input).value == ""
                restored.action_restore_local()
            assert restored.query_one(Input).value == "newer process response"
    asyncio.run(run())
    assert newer_path[0].exists()


@pytest.mark.parametrize("confirm", [False, True])
def test_local_only_repeated_save_then_reset_or_confirm_retires_all_observed_copies(
    tmp_path, monkeypatch, confirm,
):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen

    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    script = tmp_path / "submit.py"
    script.write_text("print('provider command result')", encoding="utf-8")
    raw = manifest()
    raw["actions"][0].pop("draft")
    raw["actions"][0].update(run=[sys.executable, str(script), "{fields}"], result_view="text")
    reg = registered(raw)
    runtime = tasks.RegisteredPivotRuntime(reg)
    monkeypatch.setattr(runtime, "ensure", lambda scope: None)
    rec = {"id": "same-id", "title": "Example", "card": {**CARD, "fields": FIELDS}}
    saved_paths = []

    async def open_form(app, pilot):
        picker = app.query_one(PickerScreen)
        await _wait_for_initial_setup(pilot, picker)
        monkeypatch.setattr(picker, "_pivot_runtime", lambda value: runtime)
        picker._open_pivot_form(reg, reg.actions[0], rec, rec, "Example")
        await _wait(pilot, lambda: isinstance(app.screen, PivotFormScreen))
        return app.screen

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            for value in ("first response", "second response"):
                form = await open_form(app, pilot)
                form.query_one(Input).value = value
                form.action_save()
                saved_paths.append(form._draft_path())
                await _wait(pilot, lambda: app.screen is not form)
            form = await open_form(app, pilot)
            assert form.query_one(Input).value == "second response"
            assert set(saved_paths) <= set(form._draft_files)
            if confirm:
                form._confirm()
                saved_paths.append(form._draft_path())
                await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            else:
                form._perform_reset()
                assert form.query_one(Input).value == ""
            assert not any(path.exists() for path in saved_paths)
        restarted = PickerApp(_fixture_source(), live=False)
        async with restarted.run_test() as pilot:
            form = await open_form(restarted, pilot)
            assert form.query_one(Input).value == ""
    asyncio.run(run())


def test_native_confirm_retires_its_exact_record_without_deleting_a_newer_copy(tmp_path, monkeypatch):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.form_draft_storage import write_draft
    from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen

    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    gate = tmp_path / "release"
    script = tmp_path / "submit.py"
    script.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        f"while not Path({str(gate)!r}).exists(): time.sleep(0.01)\n"
        "print('provider command result')\n", encoding="utf-8",
    )
    raw = manifest()
    raw["actions"][0].update(run=[sys.executable, str(script), "{fields}"], result_view="text")
    reg = registered(raw)
    runtime = tasks.RegisteredPivotRuntime(reg)
    monkeypatch.setattr(runtime, "ensure", lambda scope: None)
    rec = {**record(), "id": "same-id", "title": "Example", "card": {**CARD, "fields": FIELDS}}

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            picker = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, picker)
            monkeypatch.setattr(picker, "_pivot_runtime", lambda value: runtime)
            picker._open_pivot_form(reg, reg.actions[0], rec, rec, "Example")
            await _wait(pilot, lambda: isinstance(app.screen, PivotFormScreen))
            form = app.screen
            form.query_one(Input).value = "original submitted response"
            form._confirm()
            original = form._draft_path()
            assert original.exists()
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen))
            newer = write_draft(
                _steer_draft_path("same-id", form._draft_binding), form._draft_binding,
                "same-id", {"answer": "newer local response"}, pending=True,
            )
            gate.touch()
            await _wait(pilot, lambda: not app.screen.pending)
            assert not original.exists() and newer.exists()
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["before-submit", "after-submit"])
def test_local_confirm_cleanup_failure_never_reapplies_an_accepted_response(tmp_path, monkeypatch, phase):
    from test_config_actions import _wait
    from test_picker_tui import _fixture_source, _wait_for_initial_setup
    from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
    from worktree_manager.production_picker.picker_tui.steering import SubmitErrorScreen

    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    invoked = tmp_path / "invoked"
    script = tmp_path / "submit.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(invoked)!r}).write_text('yes')\nprint('completed')",
        encoding="utf-8",
    )
    raw = manifest()
    raw["actions"][0].pop("draft")
    raw["actions"][0].update(run=[sys.executable, str(script), "{fields}"], result_view="text")
    reg = registered(raw)
    runtime = tasks.RegisteredPivotRuntime(reg)
    monkeypatch.setattr(runtime, "ensure", lambda scope: None)
    rec = {"task_id": "same-id", "id": "same-id", "title": "Example", "card": {**CARD, "fields": FIELDS}}
    failed_path = []
    unlink = Path.unlink

    def fail_once(path, *args, **kwargs):
        if failed_path and path == failed_path[0]:
            failed_path.clear()
            raise OSError("cleanup denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)

    async def open_form(app, pilot):
        picker = app.query_one(PickerScreen)
        await _wait_for_initial_setup(pilot, picker)
        monkeypatch.setattr(picker, "_pivot_runtime", lambda value: runtime)
        picker._open_pivot_form(reg, reg.actions[0], rec, rec, "Example")
        await _wait(pilot, lambda: isinstance(app.screen, PivotFormScreen))
        return app.screen

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            form = await open_form(app, pilot)
            form.query_one(Input).value = "older automatic response"
            form.action_save()
            automatic = form._draft_path()
            await _wait(pilot, lambda: app.screen is not form)
            form = await open_form(app, pilot)
            form.query_one(Input).value = "current submitted response"
            if phase == "before-submit":
                failed_path.append(automatic)
            form._confirm()
            preserved = form._draft_path()
            if phase == "after-submit":
                failed_path.append(preserved)
            await _wait(pilot, lambda: isinstance(app.screen, SubmitErrorScreen))
            assert preserved.exists()
            if phase == "before-submit":
                assert form.is_mounted and form.query_one(Input).value == "current submitted response"
                assert not invoked.exists()
            else:
                assert invoked.exists() and not automatic.exists()
        if phase == "after-submit":
            restarted = PickerApp(_fixture_source(), live=False)
            async with restarted.run_test() as pilot:
                form = await open_form(restarted, pilot)
                assert form.query_one(Input).value == ""
                form.action_restore_local()
                assert form.query_one(Input).value == "current submitted response"
    asyncio.run(run())


@pytest.mark.parametrize("close", ["save", "confirm", "escape"])
def test_untouched_shared_multichoice_preserves_custom_boundaries_and_order(tmp_path, monkeypatch, close):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    fields = [{"name": "answer", "type": "multichoice", "options": ["one", "two"], "allow_other": True}]
    original = ["custom,one", "one", "custom\ntwo"]
    form = PivotFormScreen(CARD, fields, "Answer", task_id="same-id", draft_binding=binding(fields=fields),
                           draft_values={"answer": original}, on_clear_draft=lambda: None)

    async def run():
        app = Host(form)
        async with app.run_test() as pilot:
            assert form._collect() == {"answer": original}
            {"save": form.action_save, "confirm": form._confirm, "escape": form.action_escape_close}[close]()
            await pilot.pause()
            assert app.result["values"] == {"answer": original}
            assert json.loads(form._draft_path().read_text())["values"] == {"answer": original}
    asyncio.run(run())


def test_editing_other_keeps_existing_single_freeform_semantics(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    fields = [{"name": "answer", "type": "multichoice", "options": ["one", "two"], "allow_other": True}]
    form = PivotFormScreen(CARD, fields, "Answer", task_id="same-id", draft_binding=binding(fields=fields),
                           draft_values={"answer": ["custom,one", "one", "custom\ntwo"]})

    async def run():
        async with Host(form).run_test() as pilot:
            controls = form._q[0]
            controls["primary"].select("two")
            await pilot.pause()
            assert form._collect()["answer"] == ["one", "two", "custom,one", "custom\ntwo"]
            controls["other"].text = "one new, freeform\ncustom answer"
            assert form._collect()["answer"] == ["one", "two", "one new, freeform\ncustom answer"]
    asyncio.run(run())


def test_strict_multichoice_cannot_confirm_invisible_or_duplicate_members(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    fields = [{"name": "answer", "type": "multichoice", "options": ["perf", "other"], "allow_other": False}]
    form = PivotFormScreen(
        CARD, fields, "Answer", task_id="same-id", draft_binding=binding(fields=fields),
        draft_values={"answer": ["perf", "not-an-option", "perf"]},
    )

    async def run():
        app = Host(form)
        async with app.run_test() as pilot:
            assert form._q[0]["primary"].selected == ["perf"]
            assert form._q[0]["other"] is None
            form._confirm()
            await pilot.pause()
            assert app.result["values"] == {"answer": ["perf"]}
            assert json.loads(form._draft_path().read_text())["values"] == {"answer": ["perf"]}
    asyncio.run(run())
