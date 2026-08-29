"""Tests for the Picker's Profiles column IO (load/apply, own-column model)."""
from __future__ import annotations

import json

from agent_worktrees import profiles as profiles_mod
from worktree_manager.production_picker.picker_tui import profiles_io
from agent_worktrees.profiles import TargetSel


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def test_load_local_column_reads_config(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    profiles_mod.save_selection(
        cfg_path, [TargetSel("Emancipation-Cube", "Win", "shell")],
        self_machine="Anomalous-Potato", self_env="Win")
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        "agent_worktrees.config.default_config_path", lambda: cfg_path)

    col = profiles_io.load_column("Anomalous-Potato", "Win")
    assert TargetSel("Anomalous-Potato", "Win", "agent") in col   # self, locked
    assert TargetSel("Emancipation-Cube", "Win", "shell") in col


def test_load_local_unmanaged_returns_none(monkeypatch, tmp_path):
    """A config with no terminal_profiles key is unmanaged -> None (the caller
    renders the default column)."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("machine: anomalous-potato\n", encoding="utf-8")
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        "agent_worktrees.config.default_config_path", lambda: cfg_path)

    assert profiles_io.load_column("Anomalous-Potato", "Win") is None


def test_load_remote_unmanaged_returns_none(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "emancipation-cube", "..."])

    payload = json.dumps({"version": 1, "managed": False, "targets": []})

    def fake_runner(argv, timeout):
        return _Proc(0, stdout=payload)

    assert profiles_io.load_column("Emancipation-Cube", "Win", runner=fake_runner) is None


def test_load_remote_column_parses_ssh_json(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "emancipation-cube", "..."])

    payload = json.dumps({"version": 1, "machine": "Emancipation-Cube", "env": "Win",
                          "managed": True,
                          "targets": [{"machine": "Emancipation-Cube", "env": "Win",
                                       "kind": "agent"},
                                      {"machine": "Mantis-Counter", "env": "Linux",
                                       "kind": "shell"}]})

    def fake_runner(argv, timeout):
        return _Proc(0, stdout="banner noise\n" + payload)

    col = profiles_io.load_column("Emancipation-Cube", "Win", runner=fake_runner)
    assert TargetSel("Mantis-Counter", "Linux", "shell") in col
    assert TargetSel("Emancipation-Cube", "Win", "agent") in col   # self diagonal forced


def test_load_remote_failure_marks_unavailable(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "emancipation-cube", "..."])

    def boom(argv, timeout):
        raise OSError("ssh down")

    # An SSH failure means we can't read the remote's real column, so the column
    # is UNAVAILABLE (read-only) -- never a fabricated selection we'd try to
    # write back and fail (#1370).
    assert profiles_io.load_column("Emancipation-Cube", "Win", runner=boom) \
        is profiles_io.UNAVAILABLE


def test_load_remote_nonzero_marks_unavailable(monkeypatch):
    """An older remote without the ``profiles`` subcommand exits nonzero -> the
    column is unavailable/read-only, not a fabricated selection (#1370)."""
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "emancipation-cube", "..."])

    def old_remote(argv, timeout):
        return _Proc(2, stderr="error: unrecognized command 'profiles'")

    assert profiles_io.load_column("Emancipation-Cube", "Win", runner=old_remote) \
        is profiles_io.UNAVAILABLE


def test_load_remote_no_argv_marks_unavailable(monkeypatch):
    """No SSH argv (unreachable / not-ready host) -> unavailable, read-only."""
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv", lambda m, e, **k: None)

    assert profiles_io.load_column("Emancipation-Cube", "Win") is profiles_io.UNAVAILABLE


def test_apply_local_writes_config(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        "agent_worktrees.config.default_config_path", lambda: cfg_path)

    ok, _detail = profiles_io.apply_column(
        "Anomalous-Potato", "Win",
        [TargetSel("Emancipation-Cube", "WSL", "agent")], mirror=False)
    assert ok
    loaded = profiles_mod.load_selection(cfg_path)
    assert TargetSel("Emancipation-Cube", "WSL", "agent") in loaded
    assert TargetSel("Anomalous-Potato", "Win", "agent") in loaded


def test_apply_remote_sends_ssh(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    seen = {}

    def fake_argv(m, e, *, action, set_json=None, no_mirror=False):
        seen["set_json"] = set_json
        return ["ssh", "emancipation-cube", "..."]

    monkeypatch.setattr(profiles_io.data_ssh, "profiles_argv", fake_argv)

    def fake_runner(argv, timeout):
        return _Proc(0, stdout='{"version":1,"targets":[]}')

    ok, detail = profiles_io.apply_column(
        "Emancipation-Cube", "Win", [TargetSel("Emancipation-Cube", "Win", "agent")],
        runner=fake_runner)
    assert ok
    assert detail == "pushed"
    assert "Emancipation-Cube" in seen["set_json"]


def test_apply_remote_failure_reports(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Anomalous-Potato", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "emancipation-cube", "..."])

    def fake_runner(argv, timeout):
        return _Proc(1, stderr="boom")

    ok, detail = profiles_io.apply_column(
        "Emancipation-Cube", "Win", [], runner=fake_runner)
    assert not ok
    assert "boom" in detail
