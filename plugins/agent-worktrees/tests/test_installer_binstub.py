"""Tests for project-binstub generation (#25 cross-project WORKTREE_ID)."""

from __future__ import annotations

import platform
from pathlib import Path

from agent_worktrees import installer as inst


def _project_binstub(lb: Path, project: str) -> str:
    name = f"{project}.cmd" if platform.system() == "Windows" else project
    return (lb / name).read_text()


def test_project_binstub_uses_project_flag(monkeypatch, tmp_path: Path):
    """A project binstub names its project via ``--project`` (context otherwise
    resolves from CWD, git-like). It must NOT set an ambient WORKTREE_PROJECT on
    the primary path, nor scrub WORKTREE_ID (identity now comes purely from CWD)."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    content = _project_binstub(lb, "demoproj")
    # Primary path routes through the CLI with an explicit --project.
    assert "--project demoproj" in content
    # No longer scrubs the inherited worktree id -- it is simply ignored.
    assert "WORKTREE_ID" not in content
    assert "APERTURE_WORKTREE_ID" not in content
    # WORKTREE_PROJECT survives ONLY in the recovery (venv-missing) branch,
    # never on the primary CLI path.
    if platform.system() == "Windows":
        assert '"%_PY%" -m agent_worktrees --project demoproj' in content
        assert 'set "WORKTREE_PROJECT=demoproj"' in content  # recovery only
    else:
        assert 'exec "$_AW" --project demoproj' in content
        assert 'export WORKTREE_PROJECT="demoproj"' in content  # recovery only


def test_global_stub_does_not_clear_worktree_id(monkeypatch, tmp_path: Path):
    """The global `agent-worktrees` stub is the 'inherit my worktree' path and
    must NOT blank WORKTREE_ID (only project binstubs do)."""
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    name = "agent-worktrees.cmd" if platform.system() == "Windows" else "agent-worktrees"
    global_stub = (lb / name).read_text()
    assert "WORKTREE_ID" not in global_stub


def test_windows_binstubs_avoid_unsigned_trampoline(monkeypatch, tmp_path: Path):
    """Smart App Control hard-blocks the unsigned uv console-script trampoline
    (`agent-worktrees.exe`). On Windows the binstubs must launch via the venv's
    signed python.exe with `-m agent_worktrees`, never the .exe trampoline."""
    if platform.system() != "Windows":
        import pytest
        pytest.skip("Windows-only binstub content")
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    for name in ("agent-worktrees.cmd", "demoproj.cmd"):
        content = (lb / name).read_text()
        assert "\\Scripts\\python.exe" in content
        assert "-m agent_worktrees" in content
        assert "agent-worktrees.exe" not in content
