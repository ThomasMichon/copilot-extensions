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


def test_cmd_daemons_status_text_mode_flags_pre_upgrade_daemon_telemetry(monkeypatch, capsys):
    report = [
        {"pid": 1, "port": 9000, "active": False, "status": "ready", "telemetry": "unsupported"},
    ]
    monkeypatch.setattr(
        "worktree_manager.daemons_status.daemon_statuses", lambda: report
    )

    rc = daemons_cli.cmd_daemons(["status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "telemetry unavailable (pre-upgrade daemon)" in out


def test_cmd_daemons_mappings_json_prints_the_report(monkeypatch, capsys):
    report = [
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "mux_session": "wt-1",
            "live": True,
            "attached_clients": 2,
            "observed_at": "2026-09-25T00:00:00Z",
        }
    ]
    monkeypatch.setattr(
        "worktree_manager.daemons_status.mapping_statuses", lambda: report
    )

    rc = daemons_cli.cmd_daemons(["mappings", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == report


def test_cmd_daemons_mappings_text_mode_reports_none_known(monkeypatch, capsys):
    monkeypatch.setattr(
        "worktree_manager.daemons_status.mapping_statuses", lambda: []
    )

    rc = daemons_cli.cmd_daemons(["mappings"])

    assert rc == 0
    assert "no known mux-session mappings" in capsys.readouterr().out


def test_cmd_daemons_mappings_text_mode_renders_live_marker_and_fields(monkeypatch, capsys):
    report = [
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "mux_session": "wt-1",
            "live": True,
            "attached_clients": 2,
            "observed_at": "2026-09-25T00:00:00Z",
        },
        {
            "project": "proj",
            "worktree_id": "wt-2",
            "mux_session": "wt-2",
            "live": False,
            "attached_clients": 0,
            "observed_at": "2026-09-25T00:00:00Z",
        },
    ]
    monkeypatch.setattr(
        "worktree_manager.daemons_status.mapping_statuses", lambda: report
    )

    rc = daemons_cli.cmd_daemons(["mappings"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "* proj/wt-1" in out
    assert "session wt-1" in out
    assert "attached 2" in out
    assert "  proj/wt-2" in out


def test_cmd_daemons_rejects_unknown_action():
    assert daemons_cli.cmd_daemons(["bogus"]) == 2


def test_cmd_daemons_requires_an_action():
    assert daemons_cli.cmd_daemons([]) == 2
