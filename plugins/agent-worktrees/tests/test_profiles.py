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


def test_no_agent_config_seeds_empty_managed_selection(tmp_path):
    """`register --no-agent` seeds `terminal_profiles: []` -> a *managed* empty
    selection (has_selection True, load_selection []), so the WT generator emits
    NO profile -- distinct from an absent key (unmanaged = the default column)."""
    from pathlib import Path

    from agent_worktrees import __main__ as m

    cfg_path = tmp_path / "config.yaml"
    m._write_config(
        cfg_path, Path("D:/Src/example-marketplace"), "host-dev6", "windows",
        "example-marketplace", "main", no_terminal_profile=True)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["terminal_profiles"] == []
    # Managed (key present) + empty -> no profiles, NOT the default column.
    assert profiles.has_selection(cfg_path) is True
    assert profiles.load_selection(cfg_path) == []


def test_default_config_omits_terminal_profiles_key(tmp_path):
    """Without --no-agent, the key is absent -> unmanaged (has_selection False)."""
    from pathlib import Path

    from agent_worktrees import __main__ as m

    cfg_path = tmp_path / "config.yaml"
    m._write_config(
        cfg_path, Path("D:/Src/x"), "host-dev6", "windows", "x", "main")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert profiles.CONFIG_KEY not in data
    assert profiles.has_selection(cfg_path) is False


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


# ---- Default column (unmanaged): minimal per-agent + bare cross-machine ----

def test_is_default_on_minimal_per_agent():
    """agent kind is ON only for the self diagonal (this host, native env)."""
    assert profiles.is_default_on(
        TargetSel("Lambda-Core", "Win", "agent"), "Lambda-Core", "Win")
    # remote agent-launch combo -> OFF
    assert not profiles.is_default_on(
        TargetSel("Borealis", "Win", "agent"), "Lambda-Core", "Win")
    # local WSL launcher -> OFF (no local WSL agent by default)
    assert not profiles.is_default_on(
        TargetSel("Lambda-Core", "WSL", "agent"), "Lambda-Core", "Win")


def test_is_default_on_bare_cross_machine():
    """shell kind is ON for every OTHER machine (any env); local shells OFF."""
    assert profiles.is_default_on(
        TargetSel("Borealis", "Win", "shell"), "Lambda-Core", "Win")
    assert profiles.is_default_on(
        TargetSel("Borealis", "WSL", "shell"), "Lambda-Core", "Win")
    assert not profiles.is_default_on(
        TargetSel("Lambda-Core", "Win", "shell"), "Lambda-Core", "Win")
    assert not profiles.is_default_on(
        TargetSel("Lambda-Core", "WSL", "shell"), "Lambda-Core", "Win")


def test_default_selection_minimal_plus_bare():
    machines_envs = [("Lambda-Core", "Win"), ("Lambda-Core", "WSL"),
                     ("Borealis", "Win"), ("Borealis", "WSL")]
    candidates = [TargetSel(m, e, k) for (m, e) in machines_envs
                  for k in ("agent", "shell")]
    sel = profiles.default_selection(candidates, "Lambda-Core", "Win")
    # self·agent first (locked)
    assert sel[0] == TargetSel("Lambda-Core", "Win", "agent")
    # bare cross-machine shells present
    assert TargetSel("Borealis", "Win", "shell") in sel
    assert TargetSel("Borealis", "WSL", "shell") in sel
    # excluded: remote agent combos, local shells, local WSL agent
    assert TargetSel("Borealis", "Win", "agent") not in sel
    assert TargetSel("Lambda-Core", "Win", "shell") not in sel
    assert TargetSel("Lambda-Core", "WSL", "agent") not in sel


def test_default_selection_self_only_without_roster():
    """No remote candidates (project without machines.yaml) -> self only."""
    assert profiles.default_selection([], "Lambda-Core", "Win") == [
        TargetSel("Lambda-Core", "Win", "agent")]
