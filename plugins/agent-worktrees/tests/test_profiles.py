"""Tests for the terminal-profile selection model (own-column persistence)."""
from __future__ import annotations

import yaml

from agent_worktrees import profiles
from agent_worktrees.profiles import TargetSel


def test_seed_is_self_agent_diagonal_only():
    seed = profiles.seed_selection("Lambda-Core", "Win")
    assert seed == [TargetSel("Lambda-Core", "Win", "agent")]


def test_load_missing_file_returns_empty(tmp_path):
    assert profiles.load_selection(tmp_path / "nope.yaml") == []


def test_save_then_load_roundtrip(tmp_path):
    cfg_path = tmp_path / ".aperture-labs" / "config.yaml"
    sels = [
        TargetSel("Lambda-Core", "Win", "agent"),
        TargetSel("Borealis", "WSL", "shell"),
    ]
    written = profiles.save_selection(
        cfg_path, sels, self_machine="Lambda-Core", self_env="Win")
    assert TargetSel("Lambda-Core", "Win", "agent") in written
    loaded = profiles.load_selection(cfg_path)
    assert loaded == written
    assert TargetSel("Borealis", "WSL", "shell") in loaded


def test_save_preserves_other_keys(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "repo_name: aperture-labs\nmachine: lambda-core\n", encoding="utf-8")
    profiles.save_selection(
        cfg_path, [TargetSel("Borealis", "Win", "agent")],
        self_machine="Lambda-Core", self_env="Win")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["repo_name"] == "aperture-labs"
    assert data["machine"] == "lambda-core"
    assert profiles.CONFIG_KEY in data


def test_self_diagonal_always_present_and_first(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    # Caller omits the self diagonal -- normalize must inject it at the front.
    written = profiles.save_selection(
        cfg_path, [TargetSel("Borealis", "Win", "shell")],
        self_machine="Lambda-Core", self_env="Win")
    assert written[0] == TargetSel("Lambda-Core", "Win", "agent")


def test_dedup_and_bad_kind_normalized(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "terminal_profiles:\n"
        "  - {machine: Borealis, env: Win, kind: agent}\n"
        "  - {machine: Borealis, env: Win, kind: agent}\n"   # dup
        "  - {machine: Wheatley, env: Linux, kind: bogus}\n"  # bad kind -> agent
        "  - {machine: '', env: Win, kind: agent}\n",          # invalid -> skip
        encoding="utf-8")
    loaded = profiles.load_selection(cfg_path)
    assert loaded.count(TargetSel("Borealis", "Win", "agent")) == 1
    assert TargetSel("Wheatley", "Linux", "agent") in loaded
    assert all(s.machine for s in loaded)


def test_normalize_dedupes_self():
    sels = [TargetSel("Lambda-Core", "Win", "agent"),
            TargetSel("Lambda-Core", "Win", "agent")]
    out = profiles.normalize_selection(sels, "Lambda-Core", "Win")
    assert out == [TargetSel("Lambda-Core", "Win", "agent")]


def test_has_selection_distinguishes_legacy_from_managed(tmp_path):
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("machine: lambda-core\n", encoding="utf-8")
    assert profiles.has_selection(legacy) is False
    assert profiles.has_selection(tmp_path / "missing.yaml") is False

    managed = tmp_path / "managed.yaml"
    profiles.save_selection(managed, [], self_machine="Lambda-Core",
                            self_env="Win")
    assert profiles.has_selection(managed) is True
