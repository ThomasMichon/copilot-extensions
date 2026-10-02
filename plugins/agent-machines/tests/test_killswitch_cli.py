from __future__ import annotations

import json
import subprocess

from agent_machines import __main__ as cli


def _run(argv: list[str]) -> int:
    return cli.main(argv)


def test_status_reports_inactive_when_no_state_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(tmp_path / "ks.json"))
    rc = _run(["bootstrap-killswitch", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inactive" in out


def test_on_writes_state_and_status_reports_active(tmp_path, monkeypatch, capsys) -> None:
    state_file = tmp_path / "nested" / "ks.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(state_file))

    rc = _run(["bootstrap-killswitch", "on", "hand-diagnosing a venv"])
    assert rc == 0
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["active"] is True
    assert data["reason"] == "hand-diagnosing a venv"
    assert data["set_by"]
    assert data["set_at"]

    capsys.readouterr()
    rc = _run(["bootstrap-killswitch", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ACTIVE" in out
    assert "hand-diagnosing a venv" in out


def test_off_clears_state(tmp_path, monkeypatch, capsys) -> None:
    state_file = tmp_path / "ks.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(state_file))
    _run(["bootstrap-killswitch", "on", "temporary"])
    assert state_file.exists()

    capsys.readouterr()
    rc = _run(["bootstrap-killswitch", "off"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cleared" in out
    assert not state_file.exists()

    capsys.readouterr()
    rc = _run(["bootstrap-killswitch", "off"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already inactive" in out


def test_on_off_json_mode(tmp_path, monkeypatch, capsys) -> None:
    state_file = tmp_path / "ks.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(state_file))

    _run(["bootstrap-killswitch", "on", "ci check", "--json"])
    on_payload = json.loads(capsys.readouterr().out)
    assert on_payload["ok"] is True
    assert on_payload["active"] is True
    assert on_payload["reason"] == "ci check"

    _run(["bootstrap-killswitch", "status", "--json"])
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["active"] is True

    _run(["bootstrap-killswitch", "off", "--json"])
    off_payload = json.loads(capsys.readouterr().out)
    assert off_payload["ok"] is True
    assert off_payload["cleared"] is True


def test_malformed_state_file_fails_open_to_inactive(tmp_path, monkeypatch, capsys) -> None:
    state_file = tmp_path / "ks.json"
    state_file.write_text("not json at all")
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(state_file))

    rc = _run(["bootstrap-killswitch", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inactive" in out


def test_malformed_state_file_surfaces_corruption_not_silently(tmp_path, monkeypatch, capsys) -> None:
    """Fails open to inactive (reconcile proceeds), but a corrupt file --
    one that exists but can't be read -- must remain discoverable, not
    indistinguishable from "never activated"."""
    state_file = tmp_path / "ks.json"
    state_file.write_text("not json at all")
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(state_file))

    rc = _run(["bootstrap-killswitch", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "corrupt" in out

    capsys.readouterr()
    rc = _run(["bootstrap-killswitch", "status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["active"] is False
    assert "error" in payload


def test_missing_state_file_has_no_error_in_json(tmp_path, monkeypatch, capsys) -> None:
    """The normal "never activated" case (no file at all) is not an error
    -- only an existing-but-unreadable file is."""
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(tmp_path / "ks.json"))
    _run(["bootstrap-killswitch", "status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["active"] is False
    assert "error" not in payload


def test_bare_subcommand_defaults_to_status(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(tmp_path / "ks.json"))
    rc = _run(["bootstrap-killswitch"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "inactive" in out


def test_on_detects_live_in_flight_reconcile(tmp_path, monkeypatch, capsys) -> None:
    """The switch only prevents a NEW reconcile; it warns, rather than
    silently overclaiming success, when one is already running."""
    fake_home = tmp_path / "home"
    lock_dir = fake_home / ".fake-plugin"
    lock_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(fake_home / "ks.json"))

    proc = subprocess.Popen(["sleep", "30"])
    try:
        (lock_dir / "reconcile.lock").write_text(str(proc.pid), encoding="utf-8")
        rc = _run(["bootstrap-killswitch", "on", "test"])
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARNING" in out
    assert "fake-plugin" in out


def test_on_reports_no_warning_when_no_lock_present(tmp_path, monkeypatch, capsys) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(fake_home / "ks.json"))

    rc = _run(["bootstrap-killswitch", "on", "test"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARNING" not in out


def test_on_ignores_stale_lock_with_dead_pid(tmp_path, monkeypatch, capsys) -> None:
    fake_home = tmp_path / "home"
    lock_dir = fake_home / ".fake-plugin"
    lock_dir.mkdir(parents=True)
    # A PID astronomically unlikely to be alive right now.
    (lock_dir / "reconcile.lock").write_text("999999999", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(fake_home / "ks.json"))

    rc = _run(["bootstrap-killswitch", "on", "test"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARNING" not in out


def test_on_json_mode_includes_in_flight_reconciles(tmp_path, monkeypatch, capsys) -> None:
    fake_home = tmp_path / "home"
    lock_dir = fake_home / ".fake-plugin"
    lock_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(fake_home / "ks.json"))

    proc = subprocess.Popen(["sleep", "30"])
    try:
        (lock_dir / "reconcile.lock").write_text(str(proc.pid), encoding="utf-8")
        rc = _run(["bootstrap-killswitch", "on", "test", "--json"])
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload.get("in_flight_reconciles") == ["fake-plugin"]
