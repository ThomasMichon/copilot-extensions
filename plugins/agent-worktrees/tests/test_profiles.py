"""Tests for the terminal-profile selection model (own-column persistence)."""
from __future__ import annotations

import yaml

from agent_worktrees import profiles
from agent_worktrees.profiles import TargetSel


def test_seed_is_self_agent_diagonal_only():
    seed = profiles.seed_selection("Anomalous-Potato", "Win")
    assert seed == [TargetSel("Anomalous-Potato", "Win", "agent")]


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
    cfg_path = tmp_path / ".test-chamber" / "config.yaml"
    sels = [
        TargetSel("Anomalous-Potato", "Win", "agent"),
        TargetSel("Emancipation-Cube", "WSL", "shell"),
    ]
    written = profiles.save_selection(
        cfg_path, sels, self_machine="Anomalous-Potato", self_env="Win")
    assert TargetSel("Anomalous-Potato", "Win", "agent") in written
    loaded = profiles.load_selection(cfg_path)
    assert loaded == written
    assert TargetSel("Emancipation-Cube", "WSL", "shell") in loaded


def test_save_preserves_other_keys(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "repo_name: test-chamber\nmachine: anomalous-potato\n", encoding="utf-8")
    profiles.save_selection(
        cfg_path, [TargetSel("Emancipation-Cube", "Win", "agent")],
        self_machine="Anomalous-Potato", self_env="Win")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["repo_name"] == "test-chamber"
    assert data["machine"] == "anomalous-potato"
    assert profiles.CONFIG_KEY in data


def test_self_diagonal_always_present_and_first(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    # Caller omits the self diagonal -- normalize must inject it at the front.
    written = profiles.save_selection(
        cfg_path, [TargetSel("Emancipation-Cube", "Win", "shell")],
        self_machine="Anomalous-Potato", self_env="Win")
    assert written[0] == TargetSel("Anomalous-Potato", "Win", "agent")


def test_dedup_and_bad_kind_normalized(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "terminal_profiles:\n"
        "  - {machine: Emancipation-Cube, env: Win, kind: agent}\n"
        "  - {machine: Emancipation-Cube, env: Win, kind: agent}\n"   # dup
        "  - {machine: Mantis-Counter, env: Linux, kind: bogus}\n"  # bad kind -> agent
        "  - {machine: '', env: Win, kind: agent}\n",          # invalid -> skip
        encoding="utf-8")
    loaded = profiles.load_selection(cfg_path)
    assert loaded.count(TargetSel("Emancipation-Cube", "Win", "agent")) == 1
    assert TargetSel("Mantis-Counter", "Linux", "agent") in loaded
    assert all(s.machine for s in loaded)


def test_normalize_dedupes_self():
    sels = [TargetSel("Anomalous-Potato", "Win", "agent"),
            TargetSel("Anomalous-Potato", "Win", "agent")]
    out = profiles.normalize_selection(sels, "Anomalous-Potato", "Win")
    assert out == [TargetSel("Anomalous-Potato", "Win", "agent")]


def test_has_selection_distinguishes_legacy_from_managed(tmp_path):
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("machine: anomalous-potato\n", encoding="utf-8")
    assert profiles.has_selection(legacy) is False
    assert profiles.has_selection(tmp_path / "missing.yaml") is False

    managed = tmp_path / "managed.yaml"
    profiles.save_selection(managed, [], self_machine="Anomalous-Potato",
                            self_env="Win")
    assert profiles.has_selection(managed) is True


# ---- Default column (unmanaged): minimal per-agent + bare cross-machine ----

def test_is_default_on_minimal_per_agent():
    """agent kind is ON only for the self diagonal (this host, native env)."""
    assert profiles.is_default_on(
        TargetSel("Anomalous-Potato", "Win", "agent"), "Anomalous-Potato", "Win")
    # remote agent-launch combo -> OFF
    assert not profiles.is_default_on(
        TargetSel("Emancipation-Cube", "Win", "agent"), "Anomalous-Potato", "Win")
    # local WSL launcher -> OFF (no local WSL agent by default)
    assert not profiles.is_default_on(
        TargetSel("Anomalous-Potato", "WSL", "agent"), "Anomalous-Potato", "Win")


def test_is_default_on_bare_cross_machine():
    """shell kind is ON for every OTHER machine (any env); local shells OFF."""
    assert profiles.is_default_on(
        TargetSel("Emancipation-Cube", "Win", "shell"), "Anomalous-Potato", "Win")
    assert profiles.is_default_on(
        TargetSel("Emancipation-Cube", "WSL", "shell"), "Anomalous-Potato", "Win")
    assert not profiles.is_default_on(
        TargetSel("Anomalous-Potato", "Win", "shell"), "Anomalous-Potato", "Win")
    assert not profiles.is_default_on(
        TargetSel("Anomalous-Potato", "WSL", "shell"), "Anomalous-Potato", "Win")


def test_default_selection_minimal_plus_bare():
    machines_envs = [("Anomalous-Potato", "Win"), ("Anomalous-Potato", "WSL"),
                     ("Emancipation-Cube", "Win"), ("Emancipation-Cube", "WSL")]
    candidates = [TargetSel(m, e, k) for (m, e) in machines_envs
                  for k in ("agent", "shell")]
    sel = profiles.default_selection(candidates, "Anomalous-Potato", "Win")
    # self·agent first (locked)
    assert sel[0] == TargetSel("Anomalous-Potato", "Win", "agent")
    # bare cross-machine shells present
    assert TargetSel("Emancipation-Cube", "Win", "shell") in sel
    assert TargetSel("Emancipation-Cube", "WSL", "shell") in sel
    # excluded: remote agent combos, local shells, local WSL agent
    assert TargetSel("Emancipation-Cube", "Win", "agent") not in sel
    assert TargetSel("Anomalous-Potato", "Win", "shell") not in sel
    assert TargetSel("Anomalous-Potato", "WSL", "agent") not in sel


def test_default_selection_self_only_without_roster():
    """No remote candidates (project without machines.yaml) -> self only."""
    assert profiles.default_selection([], "Anomalous-Potato", "Win") == [
        TargetSel("Anomalous-Potato", "Win", "agent")]
