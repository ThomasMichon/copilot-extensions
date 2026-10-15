"""Final local repair uses the fresh payload and retains real blockers."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from agent_worktrees import maintenance_cli, picker_profiles_cli, update_integration as integration


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
