from __future__ import annotations

import json

from worktree_manager import daemons_cli


def test_cmd_daemons_status_json_prints_the_report(monkeypatch, capsys):
    report = [
        {"pid": 1, "port": 9000, "active": True, "status": "ready"},
    ]
    monkeypatch.setattr(
        "worktree_manager.daemons_status.daemon_statuses", lambda: report
    )

    rc = daemons_cli.cmd_daemons(["status", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == report


def test_cmd_daemons_status_text_mode_reports_no_resident_daemons(monkeypatch, capsys):
    monkeypatch.setattr(
        "worktree_manager.daemons_status.daemon_statuses", lambda: []
    )

    rc = daemons_cli.cmd_daemons(["status"])

    assert rc == 0
    assert "no resident mux-daemons" in capsys.readouterr().out


def test_cmd_daemons_status_text_mode_renders_active_marker_and_fields(monkeypatch, capsys):
    report = [
        {
            "pid": 1,
            "port": 9000,
            "active": True,
            "status": "ready",
            "version": "0.1.0-dev1",
            "attached_clients": 3,
            "busy": True,
        },
        {"pid": 2, "port": None, "active": False, "status": "unknown"},
    ]
    monkeypatch.setattr(
        "worktree_manager.daemons_status.daemon_statuses", lambda: report
    )

    rc = daemons_cli.cmd_daemons(["status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "* pid 1" in out
    assert "version 0.1.0-dev1" in out
    assert "busy" in out
    assert "pid 2" in out and "port ?" in out


def test_cmd_daemons_rejects_unknown_action():
    assert daemons_cli.cmd_daemons(["bogus"]) == 2


def test_cmd_daemons_requires_an_action():
    assert daemons_cli.cmd_daemons([]) == 2
