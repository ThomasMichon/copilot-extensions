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


def test_dial_log_stays_usable_when_the_installation_context_is_refused(tmp_path, monkeypatch, capsys):
    """A read-only diagnostic: it must answer exactly when the context is refused (a
    broken install is when the dial history is wanted)."""
    from ssh_manager import dial_log

    from agent_codespaces import __main__ as cli

    def refused():
        raise cli.ContextRefused("generation changed")

    monkeypatch.setattr(cli, "validate_context", refused)
    monkeypatch.setenv(dial_log.DIAL_LOG_ENV, str(tmp_path))
    dial_log.record("cs-r", kind="reconnect", outcome="error", elapsed_s=1.0)
    assert main(["dial-log", "cs-r", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["last_failure"]["kind"] == "reconnect"


def test_text_output_never_replays_control_sequences(tmp_path, monkeypatch, capsys):
    """A remote's stderr can carry ESC/OSC sequences: text mode shows them escaped."""
    from ssh_manager import dial_log

    monkeypatch.setenv(dial_log.DIAL_LOG_ENV, str(tmp_path))
    dial_log.record("cs-esc", kind="direct_exec", outcome="transient", elapsed_s=1.0,
                    reason="exit 255 \x1b]0;owned\x07", stderr="\x1b[2J\x1b]8;;http://x\x07click")
    assert main(["dial-log", "cs-esc"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "\\x1b[2J" in out and "\\x1b]0;owned\\x07" in out


def test_the_header_escapes_the_target_name_too(tmp_path, monkeypatch, capsys):
    from ssh_manager import dial_log

    monkeypatch.setenv(dial_log.DIAL_LOG_ENV, str(tmp_path))
    assert main(["dial-log", "cs\x1b]0;owned\x07"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "cs\\x1b]0;owned\\x07" in out

