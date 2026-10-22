"""Full contributed command results retain provider facts without interpreting them."""

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static

from test_config_actions import _wait
from test_picker_tui import _fixture_source, _wait_for_initial_setup
from worktree_manager.plugin_contracts import ContractError, parse_manifest
from worktree_manager.production_picker.picker_tui import pivots, tasks
from worktree_manager.production_picker.picker_tui.configuration_form import CommandResultScreen
from worktree_manager.production_picker.picker_tui.engine import PickerApp, PickerScreen
from worktree_manager.production_picker.picker_tui.pivot_actions import ManifestError
from worktree_manager.production_picker.picker_tui.steering import _steer_draft_path

pytestmark = pytest.mark.contract("worktree_manager.command_results")


def _manifest(action):
    return {"schema_version": 1, "label": "Examples", "list": ["provider"], "actions": [action]}


@pytest.mark.parametrize("kind", ["command", "form"])
def test_both_parsers_retain_text_result_contract(kind):
    action = {"key": "send", "label": "Send", "kind": kind, "run": ["provider"], "result_view": "text"}
    if kind == "form":
        action["fields_from"] = "card.request_input"
    manifest = _manifest(action)
    contribution = parse_manifest(
        manifest, name="example", marketplace="example", plugin="provider", source_path="fixture.json",
    )
    production = pivots.parse_manifest(manifest, name="example", source_path="fixture.json")
    assert contribution.pivot.actions[0].result_view == production.actions[0].result_view == "text"


@pytest.mark.parametrize("extra", [
    {"result_view": True}, {"result_view": []}, {"result_view": {}},
    {"result_view": "html"}, {"result_view": "text", "progress": True},
    {"result_view": "text", "kind": "card"},
    {"result_view": "text", "kind": "internal", "verb": "example"},
])
def test_unsupported_or_conflicting_result_views_fail_visibly(extra):
    manifest = _manifest({"label": "Example", "run": ["provider"], **extra})
    with pytest.raises(ContractError, match="result_view"):
        parse_manifest(
            manifest, name="example", marketplace="example", plugin="provider", source_path="fixture.json",
        )
    with pytest.raises(ManifestError, match="result_view"):
        pivots.parse_manifest(manifest, name="example", source_path="fixture.json")


@pytest.mark.parametrize("form,returncode", [(False, 0), (False, 7), (True, 0), (True, 7)])
def test_real_child_result_reaches_scrollable_screen_without_truncation(
    tmp_path, monkeypatch, form, returncode,
):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    output = json.dumps({
        "acceptance": {"request_id": "original", "note": "x" * 2000},
        "wake_status": "pending", "consumed_at": None, "literal": "[bold]not markup[/bold]",
    })
    receipt_file = tmp_path / "provider-output.json"
    receipt_file.write_text(output, encoding="utf-8")
    gate = tmp_path / "release-command"
    script = (
        "import sys,time\nfrom pathlib import Path\n"
        f"gate = Path({str(gate)!r})\n"
        "while not gate.exists(): time.sleep(0.01)\n"
        f"print(Path({str(receipt_file)!r}).read_text(encoding='utf-8'))\n"
        "print('first diagnostic\\nlast diagnostic', file=sys.stderr)\n"
        f"sys.exit({returncode})\n"
    )
    action = {
        "key": "inspect", "label": "Inspect", "run": [sys.executable, "-c", script],
        "result_view": "text",
    }
    if form:
        action.update(kind="form", fields_from="card.request_input")
    reg = pivots.parse_manifest(_manifest(action), name="example", source_path="fixture.json")
    runtime = tasks.RegisteredPivotRuntime(reg)
    monkeypatch.setattr(runtime, "repoll", lambda scope: None)
    monkeypatch.setattr(runtime, "ensure", lambda scope: None)
    draft = _steer_draft_path("sample-task")
    draft.parent.mkdir(parents=True)
    draft.write_text('{"values":{"feedback":"retained"}}', encoding="utf-8")

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test(size=(72, 24)) as pilot:
            screen = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, screen)
            underlying_screen = app.screen
            monkeypatch.setattr(screen, "_pivot_runtime", lambda selected: runtime)
            if form:
                screen._run_pivot_form_submit(
                    reg, reg.actions[0], {"task_id": "sample-task"}, "Sample", {"feedback": "exact"},
                )
            else:
                screen._run_task_action(reg, reg.actions[0], {"id": "sample-task", "title": "Sample"})
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen))
            assert app.screen.pending
            await pilot.press("escape")
            assert isinstance(app.screen, CommandResultScreen) and app.screen.pending
            gate.touch()
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            result_screen = app.screen
            body = result_screen.query_one("#command-result", Static)
            text = str(body.render())
            assert output in text
            assert "first diagnostic\nlast diagnostic" in text
            assert "[bold]not markup[/bold]" in text
            scroll = result_screen.query_one(VerticalScroll)
            await _wait(pilot, lambda: scroll.max_scroll_y > 0)
            scroll.focus()
            await pilot.pause()
            scroll.scroll_end(animate=False)
            await _wait(pilot, lambda: scroll.scroll_y > 0)
            assert ("Command failed" if returncode else "Command completed") in text
            assert "delivered" not in text
            if returncode:
                assert "exit 7" in text
                if form:
                    assert str(draft) in text and "Local draft retained" in text
            await pilot.press("escape")
            assert app.screen is underlying_screen

    asyncio.run(run())
    assert draft.exists() is (not form or returncode != 0)


def test_both_native_categories_opt_in_to_full_result_presentation():
    root = Path(__file__).resolve().parents[3]
    for name in ("agent-dispatch", "agent-dispatch-maintenance"):
        path = root / "plugins" / "agent-dispatch" / "pivots" / f"{name}.json"
        reg = pivots.parse_manifest(json.loads(path.read_text()), name=name, source_path=str(path))
        actions = {action.key: action for action in reg.actions}
        assert actions["steer"].result_view == actions["inspect-evidence"].result_view == "text"


@pytest.mark.parametrize("form", [False, True])
def test_timed_out_child_retains_captured_receipt_and_diagnostics(
    tmp_path, monkeypatch, form,
):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "drafts"))
    monkeypatch.setattr(tasks, "ACTION_TIMEOUT", 2)
    receipt = "accepted-original-request-" + "x" * 500
    script = (
        "import sys,time;"
        f"print({receipt!r}, flush=True);"
        "print('original diagnostic', file=sys.stderr, flush=True);"
        "time.sleep(60)"
    )
    action = {
        "label": "Inspect", "run": [sys.executable, "-c", script], "result_view": "text",
    }
    if form:
        action.update(kind="form", fields_from="card.request_input")
    reg = pivots.parse_manifest(_manifest(action), name="example", source_path="fixture.json")
    runtime = tasks.RegisteredPivotRuntime(reg)
    monkeypatch.setattr(runtime, "repoll", lambda scope: None)
    draft = _steer_draft_path("timeout-task")
    draft.parent.mkdir(parents=True)
    draft.write_text('{"values":{"answer":"retained"}}', encoding="utf-8")

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            screen = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, screen)
            monkeypatch.setattr(screen, "_pivot_runtime", lambda selected: runtime)
            if form:
                screen._run_pivot_form_submit(
                    reg, reg.actions[0], {"task_id": "timeout-task"}, "Sample", {},
                )
            else:
                screen._run_task_action(reg, reg.actions[0], {"id": "timeout-task"})
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            text = str(app.screen.query_one("#command-result", Static).render())
            assert receipt in text and "original diagnostic" in text
            assert "Command failed" in text and "Command timed out after 2s" in text
            assert draft.exists()

    asyncio.run(run())


def test_ordinary_command_timeout_retains_terse_compatibility(monkeypatch):
    monkeypatch.setattr(tasks, "ACTION_TIMEOUT", 2)
    reg = pivots.parse_manifest(_manifest({"label": "Read", "run": ["provider"]}),
                                name="example", source_path="fixture.json")
    argv = [
        sys.executable, "-c", "import time;print('partial',flush=True);time.sleep(60)",
    ]
    ok, message = tasks.RegisteredPivotRuntime(reg).run_resolved(argv)
    assert not ok and message == str(subprocess.TimeoutExpired(argv, 2))[:200]


@pytest.mark.parametrize("form", [False, True])
def test_completed_text_action_invalidates_an_older_inflight_list(
    tmp_path, monkeypatch, form,
):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path))
    action = {
        "label": "Change", "run": [sys.executable, "-c", "print('accepted')"],
        "result_view": "text",
    }
    if form:
        action.update(kind="form", fields_from="card.request_input")
    reg = pivots.parse_manifest(_manifest(action), name="example", source_path="fixture.json")
    runtime = tasks.RegisteredPivotRuntime(reg)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def listing(context):
        calls.append(context["machine"])
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            finished.set()
            return "ready", [{"id": "before-command"}], "", {}
        return "ready", [{"id": "after-command"}], "", {}

    monkeypatch.setattr(runtime, "_exec_list", listing)

    async def run():
        app = PickerApp(_fixture_source(), live=False)
        async with app.run_test() as pilot:
            screen = app.query_one(PickerScreen)
            await _wait_for_initial_setup(pilot, screen)
            monkeypatch.setattr(screen, "_pivot_runtime", lambda selected: runtime)
            monkeypatch.setattr(screen, "_pivot_scope_key", lambda: "original-scope")
            runtime.ensure("original-scope")
            await _wait(pilot, entered.is_set)
            generation = runtime._gen
            if form:
                screen._run_pivot_form_submit(
                    reg, reg.actions[0], {"task_id": "sample-task"}, "Sample", {},
                )
            else:
                screen._run_task_action(reg, reg.actions[0], {"id": "sample-task"})
            monkeypatch.setattr(screen, "_pivot_scope_key", lambda: "other-scope")
            await _wait(pilot, lambda: isinstance(app.screen, CommandResultScreen) and not app.screen.pending)
            assert runtime._gen > generation
            release.set()
            await _wait(pilot, lambda: finished.is_set() and runtime.get("original-scope")[0] == "idle")
            runtime.ensure("original-scope")
            await _wait(pilot, lambda: runtime.get("original-scope")[0] == "ready")
            assert runtime.get("original-scope")[1] == [{"id": "after-command"}]
            assert calls == ["original-scope", "original-scope"]

    try:
        asyncio.run(run())
    finally:
        release.set()
        runtime.close()
