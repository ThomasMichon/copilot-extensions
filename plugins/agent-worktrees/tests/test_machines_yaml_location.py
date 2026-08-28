"""Tests for machines.yaml location resolution (.agent-worktrees/ vs repo root)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import config as cfg

_MIN = "machines:\n  m1:\n    display_name: M1\n"


def _write(p: Path, text: str = _MIN) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_prefers_canonical_over_root(tmp_path: Path):
    _write(tmp_path / ".agent-worktrees" / "machines.yaml")
    _write(tmp_path / "machines.yaml")
    assert cfg.machines_yaml_path(tmp_path) == tmp_path / ".agent-worktrees" / "machines.yaml"


def test_falls_back_to_root(tmp_path: Path):
    _write(tmp_path / "machines.yaml")
    assert cfg.machines_yaml_path(tmp_path) == tmp_path / "machines.yaml"


def test_missing_reports_canonical(tmp_path: Path):
    # Neither exists -> the canonical path is what errors should name.
    assert cfg.machines_yaml_path(tmp_path) == tmp_path / ".agent-worktrees" / "machines.yaml"


def test_load_reads_canonical(tmp_path: Path):
    _write(tmp_path / ".agent-worktrees" / "machines.yaml")
    entries = cfg.load_machines_yaml(tmp_path)
    assert "m1" in entries


def test_load_reads_legacy_root(tmp_path: Path):
    _write(tmp_path / "machines.yaml")
    entries = cfg.load_machines_yaml(tmp_path)
    assert "m1" in entries


def test_load_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        cfg.load_machines_yaml(tmp_path)


def test_load_normalizes_machine_metadata(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        """machines:
  m1:
    display_name: M1
    role: worker
    description: "  General-purpose worker.  "
    capabilities: [" builds ", "", builds, tests, " tests "]
""",
    )
    entry = cfg.load_machines_yaml(tmp_path)["m1"]
    assert entry.description == "General-purpose worker."
    assert entry.capabilities == ["builds", "tests"]


def test_load_machine_metadata_defaults_empty(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  m1:\n    description:\n    capabilities:\n",
    )
    entry = cfg.load_machines_yaml(tmp_path)["m1"]
    assert entry.description == ""
    assert entry.capabilities == []


@pytest.mark.parametrize("value", ["builds", "{name: builds}", "123"])
def test_load_rejects_non_list_capabilities(tmp_path: Path, value: str):
    _write(
        tmp_path / "machines.yaml",
        f"machines:\n  m1:\n    capabilities: {value}\n",
    )
    with pytest.raises(ValueError, match="capabilities must be a list"):
        cfg.load_machines_yaml(tmp_path)


def test_load_rejects_non_string_description(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  m1:\n    description: [not, a, string]\n",
    )
    with pytest.raises(ValueError, match="description must be a string"):
        cfg.load_machines_yaml(tmp_path)


def test_load_rejects_non_mapping_ssh(tmp_path: Path):
    _write(
        tmp_path / "machines.yaml",
        "machines:\n  m1:\n    ssh: not-a-mapping\n",
    )
    with pytest.raises(ValueError, match="ssh must be a mapping"):
        cfg.load_machines_yaml(tmp_path)


# ---------------------------------------------------------------------------
# State-root config-overlay (E1e): a stateless harness with no machines.yaml of
# its own redirects to the bound knowledge repo's machines.yaml.
# ---------------------------------------------------------------------------

def _stateless_config(harness_anchor, *, knowledge_repo="knowledge", stateless=True):
    return cfg.Config(
        srcroot="/src", machine="test", platform="linux",
        repo_name="harness", knowledge_repo=knowledge_repo,
        repos={"harness": cfg.RepoConfig(
            anchor=str(harness_anchor),
            worktree_root=str(harness_anchor) + ".wt",
            default_branch="main", remote="origin", stateless=stateless)},
    )


def _bind(monkeypatch, harness, knowledge, *, stateless=True):
    from agent_worktrees import state_root as sr
    monkeypatch.setattr(
        cfg, "load_config",
        lambda: _stateless_config(harness, stateless=stateless))
    monkeypatch.setattr(
        sr, "_checkout_path",
        lambda name: str(knowledge) if name == "knowledge" else None)


def test_overlay_redirects_to_knowledge(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(harness) == (
        knowledge / ".agent-worktrees" / "machines.yaml"
    )
    # ...and the loader follows the redirect.
    assert "m1" in cfg.load_machines_yaml(harness)


def test_overlay_redirects_to_knowledge_legacy_root(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / "machines.yaml")  # legacy root location in the overlay
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(harness) == knowledge / "machines.yaml"


def test_overlay_not_used_when_harness_has_own(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    _write(harness / ".agent-worktrees" / "machines.yaml")
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(harness) == (
        harness / ".agent-worktrees" / "machines.yaml"
    )


def test_overlay_only_for_launch_repo(tmp_path: Path, monkeypatch):
    # A non-default repo without machines.yaml must NOT be redirected.
    harness = tmp_path / "harness"
    harness.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge)
    assert cfg.machines_yaml_path(other) == (
        other / ".agent-worktrees" / "machines.yaml"
    )


def test_overlay_noop_when_not_stateless(tmp_path: Path, monkeypatch):
    harness = tmp_path / "harness"
    harness.mkdir()
    knowledge = tmp_path / "knowledge"
    _write(knowledge / ".agent-worktrees" / "machines.yaml")
    _bind(monkeypatch, harness, knowledge, stateless=False)
    # not require-external -> report own canonical, no redirect
    assert cfg.machines_yaml_path(harness) == (
        harness / ".agent-worktrees" / "machines.yaml"
    )
