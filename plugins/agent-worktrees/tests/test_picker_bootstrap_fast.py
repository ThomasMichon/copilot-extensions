"""Bootstrap keeps exact local identity reads off the full-roster scan."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_worktrees import context_cli, manager_launch_cli, tracking, worktree_identity


def _bootstrap(monkeypatch, tmp_path, cwd):
    anchor = tmp_path / "anchor"
    monkeypatch.setattr(context_cli.cfg, "project_name", lambda: "project")
    monkeypatch.setattr(context_cli.cfg, "project_dir", lambda project: tmp_path)
    monkeypatch.setattr(context_cli, "_resolve_active_project", lambda project: (project, anchor))
    monkeypatch.setattr(context_cli, "_cwd_is_inside_project", lambda path: True)
    monkeypatch.setattr(context_cli, "_in_ssh_session", lambda: False)
    monkeypatch.setattr(context_cli, "_core", lambda: SimpleNamespace(_INVOCATION_CWD=cwd))
    return anchor


@pytest.mark.parametrize("nested", [False, True])
def test_anchor_bootstrap_does_not_enumerate_worktrees(monkeypatch, tmp_path, capfd, nested):
    cwd = tmp_path / "anchor"
    _bootstrap(monkeypatch, tmp_path, cwd / "src" if nested else cwd)
    monkeypatch.setattr(worktree_identity, "_worktree_id_from_git", lambda cwd, **kw: None)
    monkeypatch.setattr(
        worktree_identity.git_ops, "git",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=f"false\n{cwd / '.git'}\n"),
    )
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_cwd",
        lambda *a, **k: pytest.fail("anchor bootstrap enumerated the roster"),
    )
    assert context_cli.cmd_picker_bootstrap(SimpleNamespace(json=True)) == 0
    assert json.loads(capfd.readouterr().out)["workspace"] is None


@pytest.mark.parametrize("nested", [False, True])
def test_linked_bootstrap_reads_one_validated_record(monkeypatch, tmp_path, capfd, nested):
    root = tmp_path / "anchor" / "workspace" if nested else tmp_path / "workspace"
    _bootstrap(monkeypatch, tmp_path, root / "nested")
    monkeypatch.setattr(worktree_identity, "_worktree_id_from_git", lambda cwd, **kw: "exact-id")
    reads = []

    def load(worktree_id, **kwargs):
        reads.append(worktree_id)
        return SimpleNamespace(repo="project", worktree_path=str(root))

    monkeypatch.setattr(tracking, "load_record_by_id", load)
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_cwd",
        lambda *a, **k: pytest.fail("linked bootstrap enumerated the roster"),
    )
    assert context_cli.cmd_picker_bootstrap(SimpleNamespace(json=True)) == 0
    assert json.loads(capfd.readouterr().out)["workspace"] == str(root)
    assert reads == ["exact-id"]


@pytest.mark.parametrize("foreign", ["repo", "path"])
def test_unconfirmed_git_identity_preserves_tracked_path_fallback(
    monkeypatch, tmp_path, capfd, foreign,
):
    root = tmp_path / "workspace"
    _bootstrap(monkeypatch, tmp_path, root / "nested")
    monkeypatch.setattr(worktree_identity, "_worktree_id_from_git", lambda cwd, **kw: "other-id")
    records = {
        "other-id": SimpleNamespace(
            repo="other" if foreign == "repo" else "project",
            worktree_path=str(tmp_path / "workspace-other"),
        ),
        "tracked": SimpleNamespace(repo="project", worktree_path=str(root)),
    }
    monkeypatch.setattr(tracking, "load_record_by_id", lambda wid, **kwargs: records[wid])
    monkeypatch.setattr(tracking, "find_worktree_id_by_cwd", lambda *a, **k: "tracked")
    assert context_cli.cmd_picker_bootstrap(SimpleNamespace(json=True)) == 0
    assert json.loads(capfd.readouterr().out)["workspace"] == str(root)


@pytest.mark.parametrize("headless", [True, False])
def test_bare_headless_check_does_not_resolve_unrelated_pr_topology(monkeypatch, headless):
    def load(**kwargs):
        assert kwargs == {"include_control_plane_related_pr": False}
        return SimpleNamespace(headless=headless)

    monkeypatch.setattr(manager_launch_cli.cfg, "load_config", load)
    assert manager_launch_cli._is_headless_project() is headless


def test_failed_git_probe_keeps_nested_tracking_fallback(monkeypatch, tmp_path, capfd):
    root = tmp_path / "anchor" / "workspace"
    _bootstrap(monkeypatch, tmp_path, root / "nested")
    monkeypatch.setattr(worktree_identity, "_worktree_id_from_git", lambda cwd, **kw: None)

    def failed_probe(*args, **kwargs):
        raise OSError("fixture Git unavailable")

    monkeypatch.setattr(worktree_identity.git_ops, "git", failed_probe)
    monkeypatch.setattr(tracking, "find_worktree_id_by_cwd", lambda *a, **k: "tracked")
    monkeypatch.setattr(
        tracking, "load_record_by_id",
        lambda *a, **k: SimpleNamespace(repo="project", worktree_path=str(root)),
    )
    assert context_cli.cmd_picker_bootstrap(SimpleNamespace(json=True)) == 0
    assert json.loads(capfd.readouterr().out)["workspace"] == str(root)


def test_anchor_confirmation_ignores_inherited_git_selector(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    worktree_identity.git_ops.git("init", cwd=anchor, isolated_repository=True)
    worktree_identity.git_ops.git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture",
        cwd=anchor, isolated_repository=True, no_hooks=True,
    )
    nested = anchor / "nested-linked"
    worktree_identity.git_ops.git(
        "worktree", "add", "-b", "nested-fixture", str(nested), cwd=anchor,
        isolated_repository=True,
    )
    git_dir = worktree_identity.git_ops.git(
        "rev-parse", "--absolute-git-dir", cwd=anchor, isolated_repository=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", git_dir)
    assert worktree_identity._confirmed_anchor_checkout(nested, anchor) is False


def test_fast_record_ignores_an_enclosing_worktree_git_selector(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    git = worktree_identity.git_ops.git
    git("init", cwd=anchor, isolated_repository=True)
    git(
        "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture",
        cwd=anchor, isolated_repository=True, no_hooks=True,
    )
    outer = tmp_path / "outer"
    nested = outer / "nested"
    for path, branch in ((outer, "outer"), (nested, "nested")):
        git(
            "worktree", "add", "-b", branch, str(path), cwd=anchor,
            isolated_repository=True,
        )
    outer_git_dir = git(
        "rev-parse", "--absolute-git-dir", cwd=outer, isolated_repository=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", outer_git_dir)
    monkeypatch.setattr(worktree_identity.cfg, "project_dir", lambda project: tmp_path)
    records = {
        path.name: SimpleNamespace(repo="project", worktree_path=str(path))
        for path in (outer, nested)
    }
    monkeypatch.setattr(tracking, "load_record_by_id", lambda wid, **kwargs: records[wid])
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_cwd",
        lambda *a, **k: pytest.fail("validated nested identity enumerated the roster"),
    )
    assert worktree_identity.tracked_invocation_record(
        nested, project="project", anchor=anchor,
    ) is records["nested"]


@pytest.mark.parametrize("layout", ["ordinary", "converted-bare", "pure-bare"])
def test_real_anchor_subdirectory_does_not_enumerate(tmp_path, monkeypatch, layout):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    git = worktree_identity.git_ops.git
    git(
        "init", *(["--bare"] if layout == "pure-bare" else []),
        cwd=anchor, isolated_repository=True,
    )
    if layout == "converted-bare":
        git("config", "core.bare", "true", cwd=anchor, isolated_repository=True)
    nested = anchor / "src"
    nested.mkdir()
    monkeypatch.setattr(worktree_identity.cfg, "project_dir", lambda project: tmp_path)
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_cwd",
        lambda *a, **k: pytest.fail("confirmed anchor enumerated the roster"),
    )
    assert worktree_identity.tracked_invocation_record(
        nested, project="project", anchor=anchor,
    ) is None
