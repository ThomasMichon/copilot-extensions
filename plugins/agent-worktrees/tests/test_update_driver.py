"""The public update boundary queues instead of running an installer in its caller."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from agent_worktrees import update_cli, update_driver_cli as boundary


def _args(**values):
    fields = dict(
        force=False, no_anchor_sync=False, no_prune_pivots=False,
        recreate_venv=False, skip_modules=None, no_manager=False,
        detach=False, wait=False, supervised_debug=False, request_status=None,
        auth_report=False, json=False, force_runtime=None,
    )
    return argparse.Namespace(**{**fields, **values})


@pytest.fixture
def queued(monkeypatch, tmp_path):
    import update_driver

    seen = {}
    request = SimpleNamespace(
        id="synthetic-request", state="pending", exit_code=None, error=None,
        log_path=tmp_path / "update.log",
    )

    def enqueue(spec, *, runner):
        seen["spec"], seen["runner"] = spec, runner
        request.spec = spec
        return request

    def wait(request_id, **kwargs):
        seen["wait"] = request_id
        return SimpleNamespace(**{**vars(request), "state": "completed", "exit_code": 4})

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary caller entered the update executor")

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (str(tmp_path / "python.exe"), "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: seen.update(worker=args))
    monkeypatch.setattr(update_driver, "wait", wait)
    monkeypatch.setattr(boundary.cfg, "active_project", lambda: "example")
    monkeypatch.setattr(boundary.cfg, "install_dir", lambda: tmp_path)
    from agent_worktrees import __main__ as core
    monkeypatch.setattr(core, "_INVOCATION_CWD", None)
    monkeypatch.setattr(update_cli, "cmd_update", forbidden)
    return seen, request


def test_ordinary_update_queues_before_manager_discovery(queued):
    seen, request = queued
    assert boundary.cmd_update_request(_args(force=True)) == 4
    assert seen["spec"].argv == ("--force",)
    assert seen["spec"].project == "example"
    assert seen["runner"].argv[-4:] == ("--project", "example", "update", "--force")
    assert seen["wait"] == request.id


def test_no_manager_does_not_bypass_the_queue(queued):
    seen, _request = queued
    assert boundary.cmd_update_request(_args(no_manager=True)) == 4
    assert seen["spec"].argv == ("--no-manager",)
    assert seen["runner"].argv[-2:] == ("update", "--no-manager")


def test_queued_update_keeps_original_context_after_project_chdir(queued, tmp_path, monkeypatch):
    from agent_worktrees import __main__ as core

    caller, anchor = tmp_path / "caller", tmp_path / "anchor"
    caller.mkdir()
    anchor.mkdir()
    monkeypatch.chdir(anchor)
    monkeypatch.setattr(core, "_INVOCATION_CWD", caller)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "selected-installation"))
    seen, _request = queued
    assert boundary.cmd_update_request(_args()) == 4
    assert seen["spec"].cwd == str(caller)
    assert seen["runner"].cwd == str(caller)
    assert dict(seen["spec"].context)["AGENT_HOME"] == str(tmp_path / "selected-installation")
    assert seen["runner"].context_keys == boundary._CONTEXT_KEYS


@pytest.mark.parametrize("with_manager", [False, True])
def test_actual_queued_engine_keeps_each_scope_and_original_invocation(tmp_path, monkeypatch, with_manager):
    import json
    import importlib.util
    import os
    import subprocess
    import sys
    from contextlib import nullcontext
    from pathlib import Path
    from agent_procutil.owned_process import run_owned

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for key in boundary._CONTEXT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UPDATE_DRIVER_TEST_MANAGER", "1" if with_manager else "0")
    fixture = Path(__file__).parent / "fixtures" / "update_driver_context.py"
    scopes = ("first",) if with_manager else ("first", "second")
    for name in scopes:
        scope = tmp_path / name
        (scope / "anchor").mkdir(parents=True)
        anchor_settings = scope / "anchor" / ".github" / "copilot" / "settings.json"
        anchor_settings.parent.mkdir(parents=True)
        anchor_settings.write_text("{}")
        invocation = scope / "invocation"
        settings = invocation / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
    observer = nullcontext()
    if with_manager and os.name == "nt":
        helper_path = Path(__file__).resolve().parents[3] / "libs/update-driver/tests/test_native_lifetime.py"
        specification = importlib.util.spec_from_file_location("_queued_engine_window_probe", helper_path)
        helper = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(helper)
        observer = helper.window_observer()
    with observer as windows:
        for _cycle in range(2 if with_manager else 1):
            for name in scopes:
                scope = tmp_path / name
                monkeypatch.setenv("AGENT_HOME", str(scope))
                monkeypatch.chdir(scope / "invocation")
                result = run_owned([sys.executable, str(fixture), "submit"], timeout=20)
                assert result.returncode == 0, result.stderr
            monkeypatch.setenv("AGENT_HOME", str(tmp_path / "first"))
            try:
                result = run_owned([sys.executable, "-m", "update_driver", "worker"], timeout=20)
            except subprocess.TimeoutExpired as error:
                logs = home / ".copilot-extensions" / "update-driver"
                pytest.fail(f"worker timeout: {[p.read_text() for p in logs.glob('*.log')]}: {error}")
            assert result.returncode == 0, result.stderr
    if windows is not None:
        assert windows.surfaced == {}, windows.surfaced
        assert windows.focus_changes == {}, windows.focus_changes
    for name in scopes:
        scope = tmp_path / name
        assert json.loads((scope / "result.json").read_text()) == {
            "anchor": str(scope / "anchor"),
            "invocation": str(scope / ("anchor" if with_manager else "invocation")),
            "update_context": str(scope / "invocation"),
        }
        if with_manager:
            assert (scope / "manager-entered").read_text() == str(scope / "invocation")


def test_engine_request_identity_keeps_explicit_provider_override(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_CMD", "synthetic-first-provider")
    assert boundary.cmd_update_request(_args()) == 4
    first = seen["spec"].context
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_CMD", "synthetic-second-provider")
    assert boundary.cmd_update_request(_args()) == 4
    assert first != seen["spec"].context
    assert dict(first)["WORKTREE_MANAGER_ENGINE_CMD"] == "synthetic-first-provider"
    assert "WORKTREE_MANAGER_ENGINE_CMD" in seen["runner"].context_keys


def test_detach_receipt_is_not_a_successful_installation(queued, capsys):
    import json

    seen, request = queued
    assert boundary.cmd_update_request(_args(detach=True, json=True)) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["request_id"] == request.id
    assert receipt["state"] == "pending"
    assert receipt["exit_code"] is None
    assert "wait" not in seen


def test_spawn_failure_never_uses_foreground_fallback(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail(*args, **kwargs):
        raise update_driver.SpawnError("fixture spawn refused")

    monkeypatch.setattr(update_driver, "ensure_worker", fail)
    assert boundary.cmd_update_request(_args()) == 1
    assert "wait" not in seen


def test_invalid_participant_never_becomes_new_work(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail():
        raise update_driver.ParticipantError("fixture witness expired")

    monkeypatch.setattr(update_driver, "borrowed_custody", fail)
    assert boundary.cmd_update_request(_args(no_manager=True)) == 1
    assert "spec" not in seen


def test_supervised_debug_uses_same_queue_and_drain_authority(queued, monkeypatch):
    import update_driver

    seen, _request = queued
    monkeypatch.setattr(update_driver, "drain", lambda: seen.update(debug=True))
    assert boundary.cmd_update_request(_args(supervised_debug=True)) == 4
    assert seen["debug"]
    assert "worker" not in seen
    assert "spec" in seen


def test_status_does_not_resolve_installation_or_enqueue(queued, monkeypatch):
    import update_driver

    seen, request = queued
    monkeypatch.setattr(update_driver, "status", lambda identifier: request)
    assert boundary.cmd_update_request(_args(request_status=request.id, json=True)) == 0
    assert seen == {}


def test_status_exposes_settlement_kind_without_executing_installation(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.state, request.error = "interrupted", "native scope settled after failure"
    request.settled_at, request.settlement_kind = 1.0, "native_scope"
    monkeypatch.setattr(update_driver, "status", lambda identifier: request)
    assert boundary.cmd_update_request(_args(request_status=request.id, json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "interrupted" and result["exit_code"] is None
    assert result["settlement_kind"] == "native_scope" and result["settled_at"] == 1.0
    assert seen == {}


def test_auth_report_stays_read_only_before_custody_resolution(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setattr(update_cli, "cmd_update", lambda args: 4)
    assert boundary.cmd_update_request(_args(auth_report=True, json=True)) == 4
    assert seen == {}


def test_driver_controls_are_not_forwarded_to_transaction_runner():
    args = _args(detach=True, json=True, force=True)
    assert boundary._flags(args) == ["--force"]


def test_request_status_does_not_require_a_project():
    from agent_worktrees.project_routing import _is_no_project_invocation

    assert _is_no_project_invocation(["update", "--request-status", "synthetic-request", "--json"])
    assert _is_no_project_invocation(["update", "--request-status=synthetic-request"])
    assert not _is_no_project_invocation(["update", "--wait"])


@pytest.mark.parametrize("mode", ["detach", "supervised_debug", "request_status"])
def test_authentication_report_cannot_select_installation_mode(mode):
    with pytest.raises(SystemExit) as error:
        boundary.cmd_update_request(_args(auth_report=True, **{mode: True}))
    assert error.value.code == 2


def test_recovery_controls_work_without_resolving_or_executing_installation(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.settled_at = 1.0
    monkeypatch.setattr(update_driver, "recovery_status", lambda: SimpleNamespace(
        eligible_ids=(request.id,), refused=(), error=None,
    ))
    assert boundary.cmd_update_request(_args(recovery_status=True, json=True)) == 0
    assert json.loads(capsys.readouterr().out)["eligible_requests"] == [request.id]
    monkeypatch.setattr(update_driver, "recover_after_restart", lambda: (request,))
    assert boundary.cmd_update_request(_args(recover_after_restart=True, json=True)) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is False
    assert seen == {}


def test_operator_repair_is_explicit_and_does_not_execute_installation(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.state = "interrupted"
    request.recovery_authority, request.released_at = "operator_repair", 2.0
    request.settled_at = request.settlement_kind = None
    def recover(*, authorized):
        assert authorized is True
        return (request,)

    monkeypatch.setattr(update_driver, "recover_for_repair", recover)
    assert boundary.cmd_update_request(_args(recover_for_repair=True, json=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["replayed"] is False
    assert result["released"][0]["recovery_authority"] == "operator_repair"
    assert result["released"][0]["settlement_kind"] is None
    assert seen == {}


@pytest.mark.parametrize("mode", ["auth_report", "force"])
def test_operator_repair_rejects_installation_and_auth_options(queued, mode):
    if mode == "auth_report":
        with pytest.raises(SystemExit):
            boundary.cmd_update_request(_args(recover_for_repair=True, auth_report=True))
    else:
        assert boundary.cmd_update_request(_args(recover_for_repair=True, force=True)) == 1
    assert queued[0] == {}


def test_operator_repair_routing_needs_no_project():
    from agent_worktrees.project_routing import _is_no_project_invocation

    assert _is_no_project_invocation(["update", "--recover-for-repair", "--json"])


def test_manager_update_handoff_carries_only_explicit_scoped_capability(monkeypatch):
    import agent_procutil
    import update_driver
    from agent_worktrees import manager_launch_cli

    seen = {}
    monkeypatch.delenv("UPDATE_DRIVER_WITNESS", raising=False)
    monkeypatch.setattr(update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "fixture"})
    monkeypatch.setattr(manager_launch_cli.platform, "system", lambda: "Windows")

    def spawn(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(wait=lambda: 0, kill=lambda: None), None

    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    with pytest.raises(SystemExit) as result:
        manager_launch_cli._exec_worktree_manager(["fixture-manager"], None, subcommand=["update"])
    assert result.value.code == 0
    assert seen["env"]["UPDATE_DRIVER_WITNESS"] == "fixture"
    with pytest.raises(SystemExit):
        manager_launch_cli._exec_worktree_manager(["fixture-manager"], None, subcommand=["picker"])
    assert "UPDATE_DRIVER_WITNESS" not in seen["env"]
