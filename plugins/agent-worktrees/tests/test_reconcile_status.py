"""Detached reconciliation status reporting."""

from __future__ import annotations

import argparse
import json

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import reconcile


def _args(repo, status):
    return argparse.Namespace(
        repo=str(repo),
        status=str(status),
        apply=True,
        machine=None,
        with_payload_refresh=False,
    )


def test_failed_apply_writes_status_and_exits_nonzero(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    status = tmp_path / "provision-status.json"
    monkeypatch.setattr(
        reconcile,
        "apply_plan",
        lambda *args, **kwargs: {
            "action": "reconcile",
            "passes": 2,
            "executed": [
                {
                    "service": "agent-example",
                    "argv": ["installer"],
                    "ok": False,
                    "returncode": 7,
                }
            ],
        },
    )

    rc = cli.cmd_reconcile_plugins(_args(repo, status))

    assert rc == 1
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["repo"] == str(repo.resolve())
    assert payload["failed"][0]["service"] == "agent-example"


def test_successful_apply_clears_previous_failure(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    status = tmp_path / "provision-status.json"
    status.write_text('{"ok": false}\n', encoding="utf-8")
    monkeypatch.setattr(
        reconcile,
        "apply_plan",
        lambda *args, **kwargs: {
            "action": "continue",
            "passes": 2,
            "executed": [],
        },
    )

    rc = cli.cmd_reconcile_plugins(_args(repo, status))

    assert rc == 0
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["failed"] == []


@pytest.mark.parametrize("blocked", ["diagnostic", "copilot-unavailable"])
def test_incomplete_apply_cannot_publish_success(tmp_path, monkeypatch, capsys, blocked):
    status = tmp_path / "status.json"
    diagnostic = {
        "service": "agent-example",
        "phase": "runtime",
        "reason": "installation-context-invalid",
        "message": "Selected installation is unavailable",
    }
    plan = (
        {"action": "continue", "diagnostics": [diagnostic]}
        if blocked == "diagnostic" else {
            "action": "reconcile",
            "updates": [{
                "service": "agent-example",
                "phase": "payload",
                "reason": "payload-missing",
                "argv": ["copilot", "plugin", "install", "agent-example@copilot-extensions"],
            }],
        }
    )
    monkeypatch.setattr(reconcile, "build_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(reconcile, "resolve_copilot", lambda: None)

    assert cli.cmd_reconcile_plugins(_args(tmp_path, status)) == 1

    summary = json.loads(capsys.readouterr().out)
    recorded = json.loads(status.read_text(encoding="utf-8"))
    assert recorded["ok"] is False
    assert summary["executed"] == []
    if blocked == "diagnostic":
        assert summary["diagnostics"] == [diagnostic]
        assert recorded["diagnostics"] == [diagnostic]
    else:
        assert summary["skipped"][0]["service"] == "agent-example"
        assert summary["skipped"][0]["reason"] == "copilot-unavailable"
        assert recorded["skipped"] == summary["skipped"]


@pytest.mark.parametrize("resolved", [False, True])
def test_diagnostics_preserve_independent_work_and_terminal_outcome(
    tmp_path, monkeypatch, capsys, resolved,
):
    status = tmp_path / "status.json"
    diagnostic = {
        "service": "agent-blocked",
        "phase": "runtime",
        "reason": "installation-context-invalid",
        "message": "Selected installation is unavailable",
    }
    plans = iter([
        {
            "action": "reconcile",
            "diagnostics": [diagnostic],
            "updates": [{
                "service": "agent-independent",
                "phase": "runtime",
                "reason": "runtime-missing",
                "argv": ["fixture-installer"],
            }],
        },
        {"action": "continue", **({} if resolved else {"diagnostics": [diagnostic]})},
    ])
    calls = []
    monkeypatch.setattr(reconcile, "build_plan", lambda *args, **kwargs: next(plans))
    monkeypatch.setattr(reconcile, "execution_diagnostic", lambda _update: None)
    monkeypatch.setattr(
        reconcile, "_run_plan_step",
        lambda argv, _update, _log: calls.append(list(argv)) or 0,
    )

    assert cli.cmd_reconcile_plugins(_args(tmp_path, status)) == (0 if resolved else 1)

    summary = json.loads(capsys.readouterr().out)
    recorded = json.loads(status.read_text(encoding="utf-8"))
    assert calls == [["fixture-installer"]]
    assert summary["executed"][0]["ok"] is True
    assert recorded["ok"] is resolved
    if resolved:
        assert not summary.get("diagnostics")
    else:
        assert summary["diagnostics"] == [diagnostic]
        assert recorded["diagnostics"] == [diagnostic]


@pytest.mark.parametrize("retried", [False, True])
def test_skipped_payload_requires_actual_success_not_a_later_empty_plan(
    tmp_path, monkeypatch, capsys, retried,
):
    status = tmp_path / "status.json"
    payload_plan = {
        "action": "reconcile",
        "updates": [{
            "service": "agent-example",
            "phase": "payload",
            "reason": "payload-refresh",
            "argv": ["copilot", "plugin", "update", "agent-example@copilot-extensions"],
        }],
    }
    plans = iter([payload_plan, payload_plan if retried else {"action": "continue"}])
    commands = iter([None, "fixture-copilot"])
    calls = []
    monkeypatch.setattr(reconcile, "build_plan", lambda *args, **kwargs: next(plans))
    monkeypatch.setattr(reconcile, "resolve_copilot", lambda: next(commands))
    monkeypatch.setattr(reconcile, "execution_diagnostic", lambda _update: None)
    monkeypatch.setattr(
        reconcile, "_run_plan_step",
        lambda argv, _update, _log: calls.append(list(argv)) or 0,
    )

    assert cli.cmd_reconcile_plugins(_args(tmp_path, status)) == (0 if retried else 1)

    summary = json.loads(capsys.readouterr().out)
    assert json.loads(status.read_text(encoding="utf-8"))["ok"] is retried
    if retried:
        assert calls == [[
            "fixture-copilot", "plugin", "update", "agent-example@copilot-extensions",
        ]]
        assert summary["executed"][0]["ok"] is True
        assert not summary.get("skipped")
    else:
        assert calls == []
        assert summary["executed"] == []
        assert summary["skipped"][0]["reason"] == "copilot-unavailable"


@pytest.mark.parametrize("resolved", [False, True])
def test_final_runtime_repair_rechecks_diagnostics_without_another_apply(
    tmp_path, monkeypatch, capsys, resolved,
):
    status = tmp_path / "status.json"
    diagnostic = {
        "service": "agent-example",
        "phase": "runtime",
        "reason": "runtime-running-generation-drift",
        "message": "The live process differs from the installed build",
    }
    plans = iter([
        {
            "action": "reconcile",
            "updates": [{
                "service": "agent-example", "phase": "payload", "reason": "payload-refresh",
                "argv": ["copilot", "plugin", "update", "agent-example@copilot-extensions"],
            }],
        },
        {
            "action": "reconcile",
            "diagnostics": [diagnostic],
            "updates": [{
                "service": "agent-example", "phase": "runtime", "reason": "runtime-running-drift",
                "argv": ["fixture-runtime-installer"],
            }],
        },
        {"action": "continue", **({} if resolved else {"diagnostics": [diagnostic]})},
    ])
    observations, executions = [], []

    def build_plan(repo, **kwargs):
        assert repo == tmp_path
        observations.append(kwargs)
        return next(plans)

    monkeypatch.setattr(reconcile, "build_plan", build_plan)
    monkeypatch.setattr(reconcile, "resolve_copilot", lambda: "fixture-copilot")
    monkeypatch.setattr(reconcile, "execution_diagnostic", lambda _update: None)
    monkeypatch.setattr(
        reconcile, "_run_plan_step",
        lambda argv, _update, _log: executions.append(list(argv)) or 0,
    )
    args = _args(tmp_path, status)
    args.with_payload_refresh = True

    assert cli.cmd_reconcile_plugins(args) == (0 if resolved else 1)

    summary = json.loads(capsys.readouterr().out)
    assert len(executions) == 2
    assert executions[-1] == ["fixture-runtime-installer"]
    assert len(summary["executed"]) == 2
    assert observations == [
        {"machine": None, "include_payload_refresh": True},
        {"machine": None, "include_payload_refresh": True},
        {"machine": None, "include_payload_refresh": False, "save": False},
    ]
    assert json.loads(status.read_text(encoding="utf-8"))["ok"] is resolved
    assert summary.get("diagnostics", []) == ([] if resolved else [diagnostic])


def test_handled_apply_error_reports_json_and_preserves_failed_status(tmp_path, monkeypatch, capsys):
    status = tmp_path / "status.json"

    def failed(*args, **kwargs):
        raise ValueError("synthetic plugin failure")

    monkeypatch.setattr(reconcile, "apply_plan", failed)
    assert cli.cmd_reconcile_plugins(_args(tmp_path, status)) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["action"] == "reconcile" and result["ok"] is False
    assert result["reason"] == "synthetic plugin failure" and result["executed"] == []
    assert "provision: error: synthetic plugin failure" in output.err
    recorded = json.loads(status.read_text(encoding="utf-8"))
    assert recorded["ok"] is False and recorded["reason"] == result["reason"]


@pytest.mark.parametrize("kind", ["participant", "driver", "cleanup"])
def test_unconfirmed_execution_is_not_a_handled_plugin_failure(tmp_path, monkeypatch, capsys, kind):
    from agent_procutil.owned_process import OwnedCleanupError
    from update_driver import ParticipantError, StateError

    errors = {
        "participant": ParticipantError("synthetic custody loss"),
        "driver": StateError("synthetic execution-state refusal"),
        "cleanup": OwnedCleanupError(object()),
    }
    error = errors[kind]
    status = tmp_path / "status.json"

    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(reconcile, "apply_plan", failed)
    with pytest.raises(type(error)):
        cli.cmd_reconcile_plugins(_args(tmp_path, status))
    assert capsys.readouterr().out == ""
    assert not status.exists()
