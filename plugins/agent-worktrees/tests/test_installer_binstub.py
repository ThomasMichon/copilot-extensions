"""Tests for project-binstub generation (#25 cross-project WORKTREE_ID)."""

from __future__ import annotations

import platform
from pathlib import Path

from agent_worktrees import installer as inst


def _project_binstub(lb: Path, project: str) -> str:
    name = f"{project}.cmd" if platform.system() == "Windows" else project
    return (lb / name).read_text()


def test_project_binstub_clears_inherited_worktree_id(monkeypatch, tmp_path: Path):
    """A project binstub is a cross-project entry point, so it must drop any
    inherited WORKTREE_ID / APERTURE_WORKTREE_ID before routing to the CLI."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    content = _project_binstub(lb, "demoproj")
    assert "WORKTREE_PROJECT" in content
    if platform.system() == "Windows":
        assert 'set "WORKTREE_ID="' in content
        assert 'set "APERTURE_WORKTREE_ID="' in content
    else:
        assert "unset WORKTREE_ID APERTURE_WORKTREE_ID" in content


def test_global_stub_does_not_clear_worktree_id(monkeypatch, tmp_path: Path):
    """The global `agent-worktrees` stub is the 'inherit my worktree' path and
    must NOT blank WORKTREE_ID (only project binstubs do)."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    name = "agent-worktrees.cmd" if platform.system() == "Windows" else "agent-worktrees"
    global_stub = (lb / name).read_text()
    assert "WORKTREE_ID" not in global_stub
