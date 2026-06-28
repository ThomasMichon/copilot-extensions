"""Tests for the Picker's Profiles column IO (load/apply, own-column model)."""
from __future__ import annotations

import json

from agent_worktrees import profiles as profiles_mod
from agent_worktrees.picker_tui import profiles_io
from agent_worktrees.profiles import TargetSel


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def test_load_local_column_reads_config(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    profiles_mod.save_selection(
        cfg_path, [TargetSel("Borealis", "Win", "shell")],
        self_machine="Lambda-Core", self_env="Win")
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    monkeypatch.setattr(
        "agent_worktrees.config.default_config_path", lambda: cfg_path)

    col = profiles_io.load_column("Lambda-Core", "Win")
    assert TargetSel("Lambda-Core", "Win", "agent") in col   # self, locked
    assert TargetSel("Borealis", "Win", "shell") in col


def test_load_local_legacy_returns_none(monkeypatch, tmp_path):
    """A config with no terminal_profiles key is legacy -> None (all-on)."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("machine: lambda-core\n", encoding="utf-8")
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    monkeypatch.setattr(
        "agent_worktrees.config.default_config_path", lambda: cfg_path)

    assert profiles_io.load_column("Lambda-Core", "Win") is None


def test_load_remote_unmanaged_returns_none(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "borealis", "..."])

    payload = json.dumps({"version": 1, "managed": False, "targets": []})

    def fake_runner(argv, timeout):
        return _Proc(0, stdout=payload)

    assert profiles_io.load_column("Borealis", "Win", runner=fake_runner) is None


def test_load_remote_column_parses_ssh_json(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "borealis", "..."])

    payload = json.dumps({"version": 1, "machine": "Borealis", "env": "Win",
                          "managed": True,
                          "targets": [{"machine": "Borealis", "env": "Win",
                                       "kind": "agent"},
                                      {"machine": "Wheatley", "env": "Linux",
                                       "kind": "shell"}]})

    def fake_runner(argv, timeout):
        return _Proc(0, stdout="banner noise\n" + payload)

    col = profiles_io.load_column("Borealis", "Win", runner=fake_runner)
    assert TargetSel("Wheatley", "Linux", "shell") in col
    assert TargetSel("Borealis", "Win", "agent") in col   # self diagonal forced


def test_load_remote_failure_degrades_to_legacy(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "borealis", "..."])

    def boom(argv, timeout):
        raise OSError("ssh down")

    # A transient SSH failure degrades to legacy (None), never an empty grid.
    assert profiles_io.load_column("Borealis", "Win", runner=boom) is None


def test_apply_local_writes_config(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    monkeypatch.setattr(
        "agent_worktrees.config.default_config_path", lambda: cfg_path)

    ok, _detail = profiles_io.apply_column(
        "Lambda-Core", "Win",
        [TargetSel("Borealis", "WSL", "agent")], mirror=False)
    assert ok
    loaded = profiles_mod.load_selection(cfg_path)
    assert TargetSel("Borealis", "WSL", "agent") in loaded
    assert TargetSel("Lambda-Core", "Win", "agent") in loaded


def test_apply_remote_sends_ssh(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    seen = {}

    def fake_argv(m, e, *, action, set_json=None, no_mirror=False):
        seen["set_json"] = set_json
        return ["ssh", "borealis", "..."]

    monkeypatch.setattr(profiles_io.data_ssh, "profiles_argv", fake_argv)

    def fake_runner(argv, timeout):
        return _Proc(0, stdout='{"version":1,"targets":[]}')

    ok, detail = profiles_io.apply_column(
        "Borealis", "Win", [TargetSel("Borealis", "Win", "agent")],
        runner=fake_runner)
    assert ok
    assert detail == "pushed"
    assert "Borealis" in seen["set_json"]


def test_apply_remote_failure_reports(monkeypatch):
    monkeypatch.setattr(profiles_io, "_local_key", lambda: ("Lambda-Core", "Win"))
    monkeypatch.setattr(
        profiles_io.data_ssh, "profiles_argv",
        lambda m, e, **k: ["ssh", "borealis", "..."])

    def fake_runner(argv, timeout):
        return _Proc(1, stderr="boom")

    ok, detail = profiles_io.apply_column(
        "Borealis", "Win", [], runner=fake_runner)
    assert not ok
    assert "boom" in detail
