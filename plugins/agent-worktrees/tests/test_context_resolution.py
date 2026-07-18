"""Tests for git-like CWD-based context resolution.

Covers the resolver that discovers the active project + worktree from the
current directory (or an explicit ``--project``), and the core anti-contamination
guarantee: ambient ``WORKTREE_PROJECT`` / ``WORKTREE_ID`` / ``WORKTREE_REPO`` are
never trusted for identity when the directory is authoritative.
"""

from __future__ import annotations

import dataclasses
import os
import types
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import git_ops
from agent_worktrees import installer as inst


def _git(*args: str, cwd) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


@pytest.fixture
def adopted_repo(tmp_path: Path, monkeypatch):
    """A real anchor repo + one worktree, adopted as project ``myproj``.

    Returns ``(anchor, wt_root, wt_path, wt_id, config)``. Stubs the projects
    registry, repos registry, tracking dir, and ``load_config`` so resolution is
    hermetic.
    """
    anchor = tmp_path / "myrepo"
    wt_root = tmp_path / "myrepo.worktrees"

    git_ops.git("init", "-b", "master", str(anchor))
    _git("config", "user.email", "t@example.com", cwd=anchor)
    _git("config", "user.name", "Test", cwd=anchor)
    (anchor / "f.txt").write_text("x\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "init", cwd=anchor)

    wt_root.mkdir()
    wt_id = "myrepo-wt-001"
    wt_path = wt_root / wt_id
    git_ops.git(
        "worktree", "add", str(wt_path), "-b", f"worktree/{wt_id}", "master",
        cwd=str(anchor),
    )

    monkeypatch.setattr(
        inst, "read_projects_registry",
        lambda: {"projects": {"myproj": {"anchor": str(anchor)}}},
    )
    monkeypatch.setattr(
        "agent_worktrees.repos.read_registry",
        lambda: types.SimpleNamespace(repos={}),
    )

    tdir = tmp_path / "tracking"
    tdir.mkdir()
    (tdir / f"{wt_id}.yaml").write_text("id: x\n")
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tdir)

    conf = cfg.Config(
        srcroot=str(tmp_path), machine="t", platform="linux", repo_name="myproj",
        repos={"myproj": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(wt_root),
            default_branch="master", remote="origin",
        )},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: conf)

    return anchor, wt_root, wt_path, wt_id, conf


# ---------------------------------------------------------------------------
# Reverse lookup + project resolution
# ---------------------------------------------------------------------------

def test_reverse_lookup_from_anchor(adopted_repo):
    anchor, *_ = adopted_repo
    assert m._reverse_lookup_project(anchor) == "myproj"


def test_resolve_from_anchor_cwd(adopted_repo, monkeypatch):
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(anchor)
    project, assumed = m._resolve_active_project(None)
    assert project == "myproj"
    assert assumed is None  # assumed CWD stays the real CWD


def test_resolve_from_worktree_cwd(adopted_repo, monkeypatch):
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    project, assumed = m._resolve_active_project(None)
    assert project == "myproj"
    assert assumed is None


def test_project_override_reports_anchor(adopted_repo):
    anchor, *_ = adopted_repo
    project, reported = m._resolve_active_project("myproj")
    assert project == "myproj"
    # The resolver reports the anchor; main() decides whether to chdir to it.
    assert Path(reported).resolve() == anchor.resolve()


def test_cwd_is_inside_project(adopted_repo, monkeypatch):
    anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    assert m._cwd_is_inside_project(anchor) is True
    monkeypatch.chdir(anchor)
    assert m._cwd_is_inside_project(anchor) is True


def test_cwd_not_inside_other_project(adopted_repo, monkeypatch, tmp_path):
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    assert m._cwd_is_inside_project(_anchor) is False


def test_not_in_repo_resolves_nothing(adopted_repo, monkeypatch, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert m._resolve_active_project(None) == (None, None)


# ---------------------------------------------------------------------------
# Worktree-id resolution is CWD-only
# ---------------------------------------------------------------------------

def test_worktree_id_from_worktree_cwd(adopted_repo, monkeypatch):
    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    monkeypatch.chdir(wt_path)
    assert m._infer_worktree_id(None, conf) == wt_id


def test_worktree_id_none_at_anchor(adopted_repo, monkeypatch):
    anchor, _wt_root, _wt_path, _wt_id, conf = adopted_repo
    monkeypatch.chdir(anchor)
    # The anchor is not under worktree_root -> no worktree id.
    assert m._infer_worktree_id(None, conf) is None


def test_worktree_id_resolves_under_foreign_worktree_root(adopted_repo, monkeypatch):
    """Regression for copilot-extensions#59: a real, git-registered worktree must
    resolve from its CWD even when the config's ``worktree_root`` points somewhere
    else entirely (the state left behind by a worktree-root layout migration).

    The legacy single-root scan returned None here; git-based identity does not.
    """
    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    # Point worktree_root at a bogus, unrelated location the worktree is NOT under.
    foreign = conf.default_repo.anchor + ".SOMEWHERE_ELSE.worktrees"
    bad_conf = dataclasses.replace(
        conf,
        repos={
            conf.repo_name: dataclasses.replace(
                conf.default_repo, worktree_root=foreign
            )
        },
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: bad_conf)
    monkeypatch.chdir(wt_path)
    # Legacy root scan would fail (cwd not under foreign root); git identity wins.
    assert m._infer_worktree_id_from_worktree_root(bad_conf, Path(wt_path)) is None
    assert m._infer_worktree_id(None, bad_conf) == wt_id


def test_project_override_yields_no_worktree_id_at_anchor(adopted_repo, monkeypatch):
    anchor, _wt_root, _wt_path, _wt_id, conf = adopted_repo
    # After main() chdir's to the anchor for a cross-project --project call, the
    # CWD is the anchor (not under worktree_root) -> no worktree id.
    monkeypatch.chdir(anchor)
    assert m._infer_worktree_id(None, conf) is None


# ---------------------------------------------------------------------------
# Anti-contamination: ambient env is ignored when CWD is authoritative
# ---------------------------------------------------------------------------

def test_worktree_id_ignores_wrong_env(adopted_repo, monkeypatch):
    """WORKTREE_ID / WORKTREE_REPO set to WRONG values must not override the
    worktree id resolved from the current directory."""
    _anchor, _wt_root, wt_path, wt_id, conf = adopted_repo
    monkeypatch.setenv("WORKTREE_ID", "some-other-worktree")
    monkeypatch.setenv("APERTURE_WORKTREE_ID", "some-other-worktree")
    monkeypatch.setenv("WORKTREE_REPO", "/nonexistent/other/repo")
    monkeypatch.chdir(wt_path)
    assert m._infer_worktree_id(None, conf) == wt_id


def test_project_resolution_ignores_wrong_env(adopted_repo, monkeypatch):
    """A stale WORKTREE_PROJECT in the env must not steer resolution when the
    directory identifies a different (correct) project."""
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.setenv("WORKTREE_PROJECT", "some-other-project")
    monkeypatch.chdir(wt_path)
    project, _assumed = m._resolve_active_project(None)
    assert project == "myproj"


# ---------------------------------------------------------------------------
# `main()` chdir behavior for --project (git `-C` semantics)
# ---------------------------------------------------------------------------

def test_project_binstub_from_within_worktree_keeps_worktree(adopted_repo, monkeypatch):
    """Regression (the note): `<project> <cmd>` run from inside one of the
    project's own worktrees must act on THAT worktree -- NOT chdir to the anchor
    and lose it. This is the common sign-off case (`<project> push-changes`)."""
    _anchor, _wt_root, wt_path, wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)

    captured = {}

    def fake_status(args):
        captured["cwd"] = Path.cwd().resolve()
        captured["wt"] = m._infer_worktree_id_from_cwd()
        return 0

    monkeypatch.setitem(m.COMMAND_MAP, "status", fake_status)
    orig = Path.cwd()
    try:
        rc = m.main(["--project", "myproj", "status"])
    finally:
        os.chdir(orig)
    assert rc == 0
    assert captured["cwd"] == wt_path.resolve()  # did NOT chdir away
    assert captured["wt"] == wt_id               # acts on the current worktree


def test_project_binstub_from_outside_chdirs_to_anchor(adopted_repo, monkeypatch, tmp_path):
    """`<project> <cmd>` run from an unrelated directory chdir's to the
    project's anchor (git `-C`), so it cleanly targets its own project."""
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)

    captured = {}

    def fake_status(args):
        captured["cwd"] = Path.cwd().resolve()
        return 0

    monkeypatch.setitem(m.COMMAND_MAP, "status", fake_status)
    orig = Path.cwd()
    try:
        rc = m.main(["--project", "myproj", "status"])
    finally:
        os.chdir(orig)
    assert rc == 0
    assert captured["cwd"] == anchor.resolve()  # chdir'd to the anchor


# ---------------------------------------------------------------------------
# `get` keys: the rename-swap (worktree-dir = CURRENT worktree; worktrees-root
# = the parent directory that holds all worktrees). See the
# agent-worktrees-normalized-launch effort, Phase 1.
# ---------------------------------------------------------------------------

@pytest.fixture
def active_myproj(monkeypatch):
    """Set the module-level active project the way main() does, then restore."""
    cfg.set_active_project("myproj")
    yield
    cfg.set_active_project(None)


def test_get_worktree_dir_is_current_worktree(adopted_repo, active_myproj, monkeypatch, capsys):
    """`get worktree-dir` from inside a worktree yields THAT worktree's root."""
    _anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(types.SimpleNamespace(key="worktree-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == wt_path.resolve()


def test_get_worktree_dir_empty_at_anchor(adopted_repo, active_myproj, monkeypatch, capsys):
    """At the anchor (not inside a worktree) `get worktree-dir` is empty."""
    anchor, _wt_root, _wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(anchor)
    rc = m.cmd_get(types.SimpleNamespace(key="worktree-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == ""


def test_get_worktree_dir_empty_outside_repo(adopted_repo, active_myproj, monkeypatch, tmp_path, capsys):
    """Outside any managed repo/worktree `get worktree-dir` is empty."""
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    rc = m.cmd_get(types.SimpleNamespace(key="worktree-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == ""


def test_get_worktrees_root_is_parent(adopted_repo, active_myproj, monkeypatch, capsys):
    """`get worktrees-root` yields the parent dir that holds all worktrees --
    the OLD meaning of `worktree-dir` -- regardless of CWD."""
    _anchor, wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(types.SimpleNamespace(key="worktrees-root"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == wt_root.resolve()


def test_get_repo_dir_is_anchor(adopted_repo, active_myproj, monkeypatch, capsys):
    """`get repo-dir` still yields the anchor repo, from inside a worktree."""
    anchor, _wt_root, wt_path, _wt_id, _conf = adopted_repo
    monkeypatch.chdir(wt_path)
    rc = m.cmd_get(types.SimpleNamespace(key="repo-dir"))
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert Path(out).resolve() == anchor.resolve()


def test_get_keys_lists_swapped_keys(adopted_repo, capsys):
    """`get keys` advertises both the repointed worktree-dir and worktrees-root."""
    rc = m.cmd_get(types.SimpleNamespace(key="keys"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "worktree-dir" in out
    assert "worktrees-root" in out
