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


def test_anchor_bootstrap_does_not_enumerate_worktrees(monkeypatch, tmp_path, capfd):
    _bootstrap(monkeypatch, tmp_path, tmp_path / "anchor")
    monkeypatch.setattr(
        tracking, "find_worktree_id_by_cwd",
        lambda *a, **k: pytest.fail("anchor bootstrap enumerated the roster"),
    )
    assert context_cli.cmd_picker_bootstrap(SimpleNamespace(json=True)) == 0
    assert json.loads(capfd.readouterr().out)["workspace"] is None


def test_linked_bootstrap_reads_one_validated_record(monkeypatch, tmp_path, capfd):
    root = tmp_path / "workspace"
    _bootstrap(monkeypatch, tmp_path, root / "nested")
    monkeypatch.setattr(worktree_identity, "_worktree_id_from_git", lambda cwd: "exact-id")
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
    monkeypatch.setattr(worktree_identity, "_worktree_id_from_git", lambda cwd: "other-id")
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
