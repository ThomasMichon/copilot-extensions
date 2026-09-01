"""Detached reconciliation status reporting."""

from __future__ import annotations

import argparse
import json

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
