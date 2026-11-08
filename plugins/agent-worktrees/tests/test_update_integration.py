"""Final local repair uses the fresh payload and retains real blockers."""
from __future__ import annotations

import os
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import maintenance_cli, picker_profiles_cli, update_integration as integration


@pytest.fixture(autouse=True)
def healthy_adopted_manager(monkeypatch):
    from agent_worktrees import manager_recovery

    monkeypatch.setattr(manager_recovery, "recover", lambda: {"status": "current"})


def test_finish_pins_selected_payload_and_restores_caller(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", "old-snapshot")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "caller-context")
    observed = []

    def reconcile():
        observed.append((os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"],
                         os.environ.get("COPILOT_EXTENSIONS_CONTEXT")))
        return {"registered": ["demo"], "preserved": [], "blocked": []}

    monkeypatch.setattr(integration.installer, "reconcile_binstubs", reconcile)
    assert integration.finish(tmp_path, "linux", {}) is True
    assert observed == [(str(tmp_path), None)]
    assert os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] == "old-snapshot"
    assert os.environ["COPILOT_EXTENSIONS_CONTEXT"] == "caller-context"


def test_selected_cell_is_preserved(monkeypatch, tmp_path):
    observed = []
    monkeypatch.setattr(
        integration.installer, "reconcile_binstubs",
        lambda: observed.append(os.environ["COPILOT_EXTENSIONS_CONTEXT"]) or {},
    )
    assert integration.finish(
        tmp_path, "linux", {"COPILOT_EXTENSIONS_CONTEXT": "selected-context"},
    ) is True
    assert observed == ["selected-context"]


@pytest.mark.parametrize("selected_context", [None, "selected-context"])
@pytest.mark.parametrize("success", [False, True])
def test_windows_profile_finishing_resolves_selected_state_and_restores_caller(
    monkeypatch, tmp_path, selected_context, success,
):
    from agent_worktrees import manager_launch_cli, registry_paths

    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", "inherited-payload")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "inherited-context")
    monkeypatch.setattr(integration.installer, "reconcile_binstubs", lambda: {})
    # This unit isolates selection/restoration; real receipt transport is
    # exercised by test_terminal_refresh's cell fixture.
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(picker_profiles_cli.cfg, "project_name", lambda: "demo")
    monkeypatch.setattr(
        manager_launch_cli, "_discover_control_plane_provider_manifests",
        lambda **kwargs: {"worktree-manager": SimpleNamespace(command=("manager",), provider_root="")},
    )
    selected, inherited = tmp_path / "selected", tmp_path / "inherited"
    for path, machine in [(selected, "selected-host"), (inherited, "wrong-host")]:
        path.mkdir()
        (path / "config.yaml").write_text(machine, encoding="utf-8")
    observed = []

    def project_dir(project):
        assert project == "demo"
        observed.append((os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"],
                         os.environ.get("COPILOT_EXTENSIONS_CONTEXT")))
        return selected if os.environ.get("COPILOT_EXTENSIONS_CONTEXT") == selected_context else inherited

    monkeypatch.setattr(picker_profiles_cli.cfg, "project_dir", project_dir)
    monkeypatch.setattr(
        picker_profiles_cli.cfg, "load_config",
        lambda path, **kwargs: SimpleNamespace(machine=path.read_text(encoding="utf-8")),
    )
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0 if success else 1, stderr="", stdout=json.dumps({
            "schema": "copilot-extensions.terminal-profile-refresh",
            "version": 1, "status": "verified_current" if success else "blocked",
        }))

    monkeypatch.setattr(picker_profiles_cli.subprocess, "run", run)
    environment = {"COPILOT_EXTENSIONS_CONTEXT": selected_context} if selected_context else {}
    assert integration.finish(tmp_path, "windows", environment) is success
    assert observed == [(str(tmp_path), selected_context)]
    assert calls == [["manager", "refresh-profiles", "demo", "--machine", "selected-host"]]
    assert os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] == "inherited-payload"
    assert os.environ["COPILOT_EXTENSIONS_CONTEXT"] == "inherited-context"


@pytest.mark.parametrize("result", [
    {"blocked": ["demo"]},
    {"registered": ["demo"], "preserved": ["demo"]},
    None,
])
def test_unresolved_required_commands_fail_finish(monkeypatch, tmp_path, result):
    monkeypatch.setattr(integration.installer, "reconcile_binstubs", lambda: result)
    assert integration.finish(tmp_path, "linux", {}) is False


def test_unrelated_foreign_inventory_does_not_block(monkeypatch, tmp_path):
    monkeypatch.setattr(
        integration.installer, "reconcile_binstubs",
        lambda: {"registered": ["demo"], "preserved": ["other-owner"], "blocked": []},
    )
    assert integration.finish(tmp_path, "linux", {}) is True


def test_profile_failure_is_required_and_attempted_once(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(integration.installer, "reconcile_binstubs", lambda: {})
    monkeypatch.setattr(
        integration.picker_profiles_cli, "_refresh_terminal_profiles",
        lambda: calls.append("profiles") or False,
    )
    assert integration.finish(tmp_path, "windows", {}) is False
    assert calls == ["profiles"]


def test_unadopted_manager_does_not_install_or_refresh_profiles(monkeypatch, tmp_path):
    from agent_worktrees import manager_recovery

    monkeypatch.setattr(manager_recovery, "recover", lambda: {"status": "not-adopted"})
    monkeypatch.setattr(integration.installer, "reconcile_binstubs", lambda: {})
    monkeypatch.setattr(
        integration.picker_profiles_cli, "_refresh_terminal_profiles",
        lambda: pytest.fail("unadopted Manager profile integration was invoked"),
    )
    assert integration.finish(tmp_path, "windows", {})


@pytest.mark.parametrize("status", ["current", "not-adopted", "recovered"])
def test_successful_recovery_preserves_the_final_update_exit(monkeypatch, tmp_path, status):
    from agent_worktrees import manager_recovery, update_events

    monkeypatch.setattr(manager_recovery, "recover", lambda: {"status": status})
    monkeypatch.setattr(integration.installer, "reconcile_binstubs", lambda: {})
    update_events.start("engine")
    try:
        assert integration.finish(tmp_path, "linux", {})
        assert update_events.finish(0) == 0
    finally:
        update_events.finish(None)


def test_failed_manager_recovery_stops_integration_visibly(monkeypatch, tmp_path, capsys):
    from agent_worktrees import manager_recovery

    def refused():
        raise manager_recovery.RecoveryRefused("selected pin cannot recover")

    monkeypatch.setattr(manager_recovery, "recover", refused)
    monkeypatch.setattr(
        integration.installer, "reconcile_binstubs",
        lambda: pytest.fail("integration continued after failed recovery"),
    )
    assert not integration.finish(tmp_path, "windows", {})
    assert "selected pin cannot recover" in capsys.readouterr().out


def test_reconcile_exception_restores_context(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", "original")

    def denied():
        raise PermissionError("command denied")

    monkeypatch.setattr(integration.installer, "reconcile_binstubs", denied)
    assert integration.finish(tmp_path, "linux", {}) is False
    assert os.environ["AGENT_WORKTREES_PAYLOAD_ROOT"] == "original"


@pytest.mark.parametrize(("blocked", "expected"), [([], 0), (["demo"], 1)])
def test_explicit_reconcile_and_repair_propagate_required_blockers(monkeypatch, blocked, expected):
    monkeypatch.setattr(
        integration.installer, "reconcile_binstubs", lambda: {"blocked": blocked},
    )
    assert maintenance_cli.cmd_reconcile_binstubs(SimpleNamespace()) == expected
    assert picker_profiles_cli.cmd_repair(SimpleNamespace()) == expected
