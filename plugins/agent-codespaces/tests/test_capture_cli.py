"""CLI-dispatch tests for ``sync-sessions`` (agent-codespaces).

Covers ``capture_cli.cmd_sync_sessions`` -- text and ``--json`` output
shapes, and the deferred exit code (mirroring agent-containers'
``test_rescue_capture_cli.py`` shape).
"""
from __future__ import annotations

import argparse
import json

from agent_codespaces import capture_cli as ccli


def test_sync_sessions_json_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        ccli, "capture_codespace_sessions",
        lambda name, account=None, timeout=300.0, verbose=False: (
            {"ok": True, "deferred": False, "session_count": 2, "detail": "pushed"}
        ),
    )
    args = argparse.Namespace(
        name="cs-a", account=None, timeout=300.0, verbose=False, json=True,
    )

    rc = ccli.cmd_sync_sessions(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["session_count"] == 2


def test_sync_sessions_json_deferred_is_busy_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        ccli, "capture_codespace_sessions",
        lambda name, account=None, timeout=300.0, verbose=False: (
            {"ok": False, "deferred": True, "session_count": 0,
             "detail": "active Copilot session-state lock present"}
        ),
    )
    args = argparse.Namespace(
        name="cs-a", account=None, timeout=300.0, verbose=False, json=True,
    )

    rc = ccli.cmd_sync_sessions(args)

    assert rc == 75
    payload = json.loads(capsys.readouterr().out)
    assert payload["deferred"] is True


def test_sync_sessions_failure_non_deferred_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(
        ccli, "capture_codespace_sessions",
        lambda name, account=None, timeout=300.0, verbose=False: (
            {"ok": False, "deferred": False, "session_count": 0, "detail": "boom"}
        ),
    )
    args = argparse.Namespace(
        name="cs-a", account=None, timeout=300.0, verbose=False, json=False,
    )

    rc = ccli.cmd_sync_sessions(args)

    assert rc == 1
    assert "Failed" in capsys.readouterr().err


def test_sync_sessions_text_summary_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        ccli, "capture_codespace_sessions",
        lambda name, account=None, timeout=300.0, verbose=False: (
            {"ok": True, "deferred": False, "session_count": 1, "detail": "pushed"}
        ),
    )
    args = argparse.Namespace(
        name="cs-a", account=None, timeout=300.0, verbose=False, json=False,
    )

    rc = ccli.cmd_sync_sessions(args)

    assert rc == 0
    assert "[OK]" in capsys.readouterr().out
