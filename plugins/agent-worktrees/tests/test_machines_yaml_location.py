"""Tests for machines.yaml location resolution (.agent-worktrees/ vs repo root)."""

from __future__ import annotations

from pathlib import Path

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
    import pytest
    with pytest.raises(FileNotFoundError):
        cfg.load_machines_yaml(tmp_path)
