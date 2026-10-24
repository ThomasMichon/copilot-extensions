"""Read-only source review keeps the real renderer but cannot execute effects."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from worktree_manager import agent_plugin_runtime, engine_client, picker_review
from worktree_manager.production_picker import runner
from worktree_manager.production_picker.picker_tui import (
    capture, derive, frame_health, profiles_io, steering, tasks,
)
from worktree_manager.production_picker.picker_tui.engine import PickerScreen


@pytest.fixture
def review(monkeypatch, tmp_path):
    value = picker_review.Review(
        tmp_path / "candidate", tmp_path / "owner", "candidate-123",
        ("installed-python", "-m", "agent_worktrees"),
    )
    monkeypatch.setattr(picker_review, "_review", value)
    return value


def forbidden(*args, **kwargs):
    pytest.fail("read-only review attempted an effect")


@pytest.mark.parametrize("args", [
    ["create", "--json"], ["resolve", "--new", "--json"],
    ["repair-stale-anchor", "--json"], ["picker-reconcile-local", "--json"],
    ["stage-update", "--indicator-state", "--json"], ["backfill-sessions"],
    ["workers", "activate"], ["list", "--json"],
])
def test_engine_commands_refused_before_resolution(review, monkeypatch, args):
    monkeypatch.setattr(engine_client, "engine_base_command", forbidden)
    with pytest.raises(engine_client.EngineError, match="Read-only"):
        engine_client._run("example", args, runner=forbidden)


def test_passthroughs_cannot_bypass_refusal(review, monkeypatch):
    monkeypatch.setattr(engine_client, "engine_base_command", forbidden)
    with pytest.raises(picker_review.ReadOnlyError):
        engine_client.run_engine_passthrough("example", ["update"])
    with pytest.raises(picker_review.ReadOnlyError):
        engine_client.run_project_passthrough("example", ["new"])


def test_only_bootstrap_read_uses_complete_installed_engine(review, monkeypatch):
    monkeypatch.setattr(engine_client, "engine_base_command", forbidden)
    monkeypatch.setattr(engine_client, "installed_engine_command", forbidden)
    seen = []

    def run(argv, timeout):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    assert engine_client.run_json(
        "example", ["picker-bootstrap", "--json", "--project-config"], runner=run,
    ) == {}
    assert seen == [[
        "installed-python", "-m", "agent_worktrees", "--project", "example",
        "picker-bootstrap", "--json", "--project-config",
    ]]
    with pytest.raises(engine_client.EngineError):
        engine_client._run("example", ["picker-bootstrap", "--json", "--repair"], runner=forbidden)


@pytest.mark.parametrize("operation", ["sync", "json", "preview", "stream", "config", "worktree"])
def test_contributed_actions_refused_before_resolution(review, monkeypatch, operation):
    monkeypatch.setattr(tasks, "_resolve_command", forbidden)
    action = SimpleNamespace(run=["unexpected", "write"])
    runtime = tasks.RegisteredPivotRuntime(SimpleNamespace())
    calls = {
        "sync": lambda: runtime.run_resolved(action.run),
        "json": lambda: runtime.run_json_resolved(action.run),
        "preview": lambda: runtime.preview_resolved(action.run),
        "stream": lambda: runtime.run_action_stream(action, {}, forbidden),
        "config": lambda: tasks.run_config_section(action, {}),
        "worktree": lambda: tasks.run_worktree_action(action, {}),
    }
    ok, detail = calls[operation]()
    assert not ok and detail == picker_review.MESSAGE
    with pytest.raises(picker_review.ReadOnlyError):
        runtime._spawn_stream(action.run)


def test_unqualified_list_and_dynamic_options_cannot_spawn(review, monkeypatch):
    monkeypatch.setattr(tasks, "_resolve_command", forbidden)
    runtime = tasks.RegisteredPivotRuntime(SimpleNamespace(
        list_cmd=["custom-provider", "list"], source_owner="custom@example", stream=False,
    ))
    assert runtime._exec_list({})[0] == "error"
    assert tasks.resolve_dynamic_options(["custom-provider", "options"]) == []
    with pytest.raises(ValueError, match="Read-only"):
        tasks.resolve_dynamic_options(["custom-provider", "options"], strict=True)


def test_catalog_uses_complete_installed_owner_not_payload_shim(review, monkeypatch):
    pivot = SimpleNamespace(source_owner="agent-dispatch@example", stream=False)
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_invocation",
                        lambda plugin: (["installed-python", "-m", "agent_dispatch"], review.owner_root))
    argv = ["agent-dispatch", "workers", "catalog", "--repo", "example"]
    assert picker_review.list_command(pivot, argv) == (
        ["installed-python", "-m", "agent_dispatch", *argv[1:]],
        {"AGENT_DISPATCH_INSTALL_DIR": str(review.owner_root)},
    )
    assert tasks._child_process_env()["AGENT_DISPATCH_NO_AUTOSTART"] == "1"
    for tail in (["--activate"], ["--repo", "example", "--repo", "other"], ["--toggle", "enable"]):
        with pytest.raises(picker_review.ReadOnlyError):
            picker_review.list_command(pivot, argv[:3] + tail)


def test_qualified_catalog_uses_bounded_descendant_owner(review, monkeypatch):
    from worktree_manager import engine_process

    pivot = SimpleNamespace(
        source_owner="agent-dispatch@example", stream=False,
        list_cmd=["agent-dispatch", "workers", "catalog"],
    )
    monkeypatch.setattr(picker_review, "list_command", lambda *a: (
        ["installed", "catalog"], {"AGENT_DISPATCH_INSTALL_DIR": str(review.owner_root)},
    ))
    monkeypatch.setattr(tasks, "_resolve_command", forbidden)
    seen = []
    monkeypatch.setattr(engine_process, "run", lambda argv, **kw: (
        seen.append((argv, kw)) or SimpleNamespace(returncode=0, stdout="[]", stderr="")
    ))
    runtime = tasks.RegisteredPivotRuntime(pivot)
    assert runtime._exec_list({})[0] == "ready"
    assert seen[0][0] == ["installed", "catalog"]
    assert seen[0][1]["timeout"] == tasks.LIST_TIMEOUT
    assert seen[0][1]["env"]["AGENT_DISPATCH_NO_AUTOSTART"] == "1"
    assert "capture_output" not in seen[0][1]


def test_real_list_adapter_selects_read_feed_without_cli(review, monkeypatch):
    rows = [{"id": "example-worktree"}]
    monkeypatch.setattr(engine_client, "_run", forbidden)
    monkeypatch.setattr(picker_review, "worktree_rows",
                        lambda project, **kw: rows if project == "example" else forbidden())
    assert engine_client.list_worktree_rows("example", runner=forbidden) == rows
    with pytest.raises(engine_client.EngineError):
        engine_client.list_worktree_rows("example", refresh=True)
    with pytest.raises(engine_client.EngineError):
        engine_client.list_worktree_rows("example", current=False)


def test_feed_closes_at_first_complete_publication(review, monkeypatch):
    from worktree_manager import resident_transport, worktree_feed_contract

    closed = []

    def frames(*args, **kwargs):
        try:
            yield {"complete": False}
            yield {"complete": True}
            forbidden()
        finally:
            closed.append(True)

    monkeypatch.setattr(resident_transport, "frames", frames)
    monkeypatch.setattr(worktree_feed_contract.CurrentFeed, "receive",
                        lambda self, frame: [{"id": "row-one"}] if frame["complete"] else None)
    assert picker_review.worktree_rows("example") == [{"id": "row-one"}]
    assert closed == [True]


def test_drafts_and_diagnostics_cannot_use_live_paths(review, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WORKTREES_STEER_DRAFTS", str(tmp_path / "live-drafts"))
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", str(tmp_path / "live-trace"))
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_FRAME_HEALTH", "1")
    assert steering._steer_drafts_dir() == review.state_dir / "drafts"
    assert frame_health._launch_trace_path() is None
    assert frame_health.FrameHealthReporter.from_env() is None
    frame_health.append_launch_event("review")
    assert not (tmp_path / "live-trace").exists()
    assert profiles_io.apply_column("host", "Win", [], runner=forbidden) == (
        False, picker_review.MESSAGE,
    )


def test_session_cleans_state_and_retains_late_callback_fence(monkeypatch, tmp_path):
    monkeypatch.setattr(picker_review, "_review", None)
    slot = tmp_path / "owner" / "versions" / "1"
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_slot", lambda plugin, **kw: slot)
    with picker_review.session("test") as state:
        (state.state_dir / "draft").write_text("private test draft", encoding="utf-8")
        assert state.owner_root == slot.parent.parent
    assert not state.state_dir.exists()
    assert picker_review.active()
    with pytest.raises(picker_review.ReadOnlyError):
        picker_review.require_writable()
    with pytest.raises(RuntimeError, match="fresh process"):
        with picker_review.session("second"):
            forbidden()


def test_unavailable_owner_never_provisions(monkeypatch):
    monkeypatch.setattr(picker_review, "_review", None)
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_slot", lambda plugin, **kw: None)
    with pytest.raises(picker_review.ReadOnlyError, match="no provisioning"):
        with picker_review.session("missing"):
            forbidden()


def test_runner_uses_review_bootstrap_and_suppresses_lifecycle(review, monkeypatch):
    monkeypatch.setattr(runner.engine_client, "engine_available", lambda: True)
    monkeypatch.setattr(runner.engine_group_b, "picker_bootstrap", forbidden)
    monkeypatch.setattr(runner, "_start_anchor_heal_check", forbidden)
    monkeypatch.setattr(runner, "_start_housekeeping", forbidden)
    monkeypatch.setattr(picker_review, "bootstrap", lambda project: {
        "version": 1, "project": project, "should_switch_cwd": False,
        "cwd": None, "default_live": False,
    })
    seen = []

    def run_ui(**kwargs):
        binding = kwargs["prepare"]()
        binding.activate()
        kwargs["after_first_refresh"]()
        seen.append((binding.live, kwargs["project_label"]))
        return None

    monkeypatch.setattr(runner, "run_tui_picker", run_ui)
    assert runner.run("example", mock_mode=False) is None
    assert seen == [(False, "READ-ONLY candidate-123 · example")]


def test_production_render_and_effect_buttons(review, monkeypatch):
    local = ("example", "Win")
    source = SimpleNamespace(
        LOCAL=local, LOCAL_LABEL="example", machines=lambda: [("example", *local, True)],
        bucket=derive.bucket, for_machine=derive.for_machine, load=lambda: [],
    )
    screen = PickerScreen(source, mock_mode=False)
    assert screen.mock_mode and not screen.real_ops
    for poll in (screen._poll_update_state, screen._poll_manager_update_state,
                 screen._poll_orphan_state):
        poll()
    screen.refresh = lambda *args, **kwargs: None
    assert screen._decide({"action": "new"}) is False
    assert screen.debug == picker_review.MESSAGE
    assert screen._internal_pivot_action("open-bridge-ui", {}) == (False, picker_review.MESSAGE)
    for verb in ("Open", "Launch in new window", "Stop", "Refresh", "Cleanup"):
        screen._wt_submenu_dispatch({}, (verb, False, False))
        assert screen.debug == picker_review.MESSAGE
    rendered = asyncio.run(capture.capture_async(source))
    assert "[READ-ONLY candidate-123]" in rendered["text"]
    assert "<svg" in rendered["svg"]


def test_normal_actions_keep_existing_behavior(monkeypatch):
    monkeypatch.setattr(picker_review, "_review", None)
    monkeypatch.setattr(tasks, "_resolve_command", lambda argv: list(argv))
    seen = []
    monkeypatch.setattr(tasks.subprocess, "run", lambda argv, **kwargs: (
        seen.append(argv) or SimpleNamespace(returncode=0, stdout="done", stderr="")
    ))
    runtime = tasks.RegisteredPivotRuntime(SimpleNamespace())
    assert runtime.run_resolved(["ordinary", "action"]) == (True, "done")
    assert seen == [["ordinary", "action"]]


@pytest.mark.parametrize("verb", ["Clean up", "Sync"])
def test_bulk_confirmation_refuses_before_creating_mock_progress(review, verb):
    from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
        PickerScreenMaintenanceActionsMixin,
    )

    screen = SimpleNamespace(
        progress=None, executor=None, debug="", refresh=lambda: None,
        _scope_union=forbidden, _start_progress=forbidden, _open_progress=forbidden,
    )
    PickerScreenMaintenanceActionsMixin._confirm_cleanup(screen, {
        "verb": verb, "scope": "All", "opts": [{"label": "All eligible", "on": True}],
    })
    assert screen.progress is None and screen.executor is None
    assert screen.debug == picker_review.MESSAGE


def test_cli_discards_launch_result_instead_of_dispatching(review, monkeypatch):
    from contextlib import nullcontext
    from worktree_manager import __main__, picker_review_cli

    monkeypatch.setattr(picker_review, "session", lambda candidate: nullcontext(review))
    monkeypatch.setattr(runner, "run", lambda *a, **kw: {"action": "new"})
    monkeypatch.setattr(__main__, "_run_production_picker", forbidden)
    assert picker_review_cli.main(["example", "--candidate", "candidate-123"]) == 0


def test_cli_capture_routes_real_runner_and_explicit_output(review, monkeypatch, tmp_path):
    from contextlib import nullcontext
    from worktree_manager import __main__

    seen = []
    monkeypatch.setattr(picker_review, "session", lambda candidate: nullcontext(review))
    monkeypatch.setattr(runner, "capture", lambda project, **kwargs: (
        seen.append((project, kwargs)) or {"text": "READ-ONLY capture"}
    ))
    output = tmp_path / "capture.txt"
    assert __main__._cmd_picker([
        "review", "example", "--capture", str(output), "--format", "text", "--pivot", "Workers",
    ]) == 0
    assert output.read_text(encoding="utf-8") == "READ-ONLY capture"
    assert seen == [("example", {"pivot": "Workers", "wait_pivot": 20.0})]


@pytest.mark.parametrize("wait", ["nan", "inf", "-1"])
def test_cli_refuses_unbounded_capture_wait_before_owner_access(monkeypatch, wait):
    from worktree_manager import picker_review_cli

    monkeypatch.setattr(picker_review, "session", forbidden)
    with pytest.raises(SystemExit) as error:
        picker_review_cli.main(["example", "--wait", wait])
    assert error.value.code == 2


def test_crash_log_uses_disposable_state_only(review, monkeypatch):
    from worktree_manager.production_picker import picker_tui, project_config

    monkeypatch.setattr(project_config, "install_dir", forbidden)
    picker_tui._write_picker_crash_log(
        ValueError("review-only-test"), live=False, mock_mode=True,
    )
    assert len(list((review.state_dir / "logs").glob("picker-crash-*.log"))) == 1
    assert not review.owner_root.exists()
