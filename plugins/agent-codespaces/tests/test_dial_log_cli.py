"""``agent-codespaces dial-log``: reads the shared ssh-manager dial log, never dials."""

from __future__ import annotations

import json

from agent_codespaces.__main__ import main


def test_dial_log_reports_counts_and_recent_entries(tmp_path, monkeypatch, capsys):
    from ssh_manager import dial_log

    monkeypatch.setenv(dial_log.DIAL_LOG_ENV, str(tmp_path))
    dial_log.record("cs-x", kind="config_fetch", outcome="timeout", elapsed_s=30.0, attempt=1,
                    account="pinned")
    dial_log.record("cs-x", kind="config_fetch", outcome="ok", elapsed_s=4.2, attempt=2,
                    account="pinned")

    assert main(["dial-log", "cs-x", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["last_10m"] == {"dials": 2, "by_outcome": {"ok": 1, "timeout": 1}}
    assert [e["outcome"] for e in out["recent"]] == ["timeout", "ok"]
    assert out["last_failure"]["outcome"] == "timeout"

    assert main(["dial-log", "cs-x"]) == 0
    text = capsys.readouterr().out
    assert "2 dial(s) (ok 1, timeout 1)" in text and "config_fetch" in text


def test_dial_log_for_a_target_never_dialed_is_empty(tmp_path, monkeypatch, capsys):
    from ssh_manager import dial_log

    monkeypatch.setenv(dial_log.DIAL_LOG_ENV, str(tmp_path))
    assert main(["dial-log", "cs-none", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["entries"] == 0 and out["recent"] == [] and out["last_failure"] is None
