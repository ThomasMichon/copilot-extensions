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
