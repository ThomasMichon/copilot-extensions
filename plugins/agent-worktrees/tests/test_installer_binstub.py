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


def test_deploy_binstubs_writes_ps1_on_windows(monkeypatch, tmp_path: Path):
    """On Windows ``register``/``deploy_binstubs`` must emit the ``.ps1`` primary
    (pwsh prefers it), not just the ``.cmd`` fallback -- the omission was the
    root cause of the example-ai-hub launcher misbehaving."""
    if platform.system() != "Windows":
        import pytest
        pytest.skip("Windows-only .ps1 primary")
    lb = tmp_path / "bin"
    monkeypatch.setattr(inst, "local_bin", lambda: lb)

    assert inst.deploy_binstubs(repo_dir=tmp_path, project="demoproj") is True

    ps1 = lb / "demoproj.ps1"
    assert ps1.exists()
    content = ps1.read_text()
    assert "-m agent_worktrees --project 'demoproj'" in content


def _reg(monkeypatch, names: list[str]) -> None:
    monkeypatch.setattr(
        inst, "read_projects_registry",
        lambda: {"projects": {n: {} for n in names}},
    )


def test_reconcile_adds_registered_and_removes_stale(monkeypatch, tmp_path: Path):
    """Reconcile deploys a complete set for every registered project and removes
    signature-matched stubs for deregistered ones."""
    lb = tmp_path / "bin"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    _reg(monkeypatch, ["keepproj"])

    # Pre-seed a stale *ours* stub for a project no longer registered.
    for p, c in inst._project_binstub_specs("staleproj"):
        p.write_text(c, newline="")
    # And a foreign stub from another tool (no WORKTREE_PROJECT / --project marker).
    foreign = lb / ("othertool.cmd" if platform.system() == "Windows" else "othertool")
    foreign.write_text("@echo off\r\necho not ours\r\n", newline="")

    result = inst.reconcile_binstubs()

    # Registered project deployed (all platform files present).
    for p, _ in inst._project_binstub_specs("keepproj"):
        assert p.exists()
    # Stale ours removed.
    for p, _ in inst._project_binstub_specs("staleproj"):
        assert not p.exists()
    # Foreign spared.
    assert foreign.exists()
    assert "keepproj" in result["registered"]
    assert any("staleproj" in r for r in result["removed"])


def test_reconcile_never_touches_reserved_global_name(monkeypatch, tmp_path: Path):
    """A project accidentally registered as ``agent-worktrees`` (e.g. install run
    from the plugin checkout) must never be deployed as a project stub -- that
    would clobber the global launcher."""
    lb = tmp_path / "bin"
    lb.mkdir()
    monkeypatch.setattr(inst, "local_bin", lambda: lb)
    _reg(monkeypatch, ["agent-worktrees", "realproj"])

    inst.reconcile_binstubs()

    # Real project deployed; reserved name NOT written as a project stub.
    for p, _ in inst._project_binstub_specs("realproj"):
        assert p.exists()
    for p, _ in inst._project_binstub_specs("agent-worktrees"):
        assert not p.exists()
