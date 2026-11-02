"""Branch-named completion backups in a shared linked-worktree repository."""

from pathlib import Path
import shlex
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_complete, pr_state_cli
from test_pr_complete import _git, _squash_merge_upstream


def _refs(path):
    return _git(
        "for-each-ref", "--format=%(refname)",
        "refs/pre-complete-backups/", "refs/pre-complete-backup", cwd=path,
    )


def test_linked_worktrees_retain_branch_named_backups_and_legacy_ref(pr_repo):
    config, wid, path, _ = pr_repo
    anchor = Path(config.default_repo.anchor)
    original = _git("rev-parse", "HEAD", cwd=path)
    legacy = _git("rev-parse", "origin/master", cwd=path)
    _git("update-ref", "refs/pre-complete-backup", legacy, cwd=path)
    other_id = "other-worktree"
    other = Path(config.default_repo.worktree_root) / other_id
    _git("worktree", "add", "-b", "feature/nested/topic", str(other), "HEAD", cwd=path)
    (other / "other.txt").write_text("other branch follow-up\n")
    _git("add", "other.txt", cwd=other)
    _git("commit", "-m", "other branch follow-up", cwd=other)
    other_tip = _git("rev-parse", "HEAD", cwd=other)
    _squash_merge_upstream(
        anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )

    first = pr_complete.complete_worktree(wid, config)
    second = pr_complete.complete_worktree(other_id, config)

    assert first["success"], first
    assert second["success"], second
    assert first["backup_ref"].startswith(f"refs/pre-complete-backups/worktree/{wid}/")
    assert second["backup_ref"].startswith("refs/pre-complete-backups/feature/nested/topic/")
    assert _git("rev-parse", first["backup_ref"], cwd=path) == original
    assert _git("rev-parse", second["backup_ref"], cwd=path) == other_tip
    assert _git("rev-parse", "refs/pre-complete-backup", cwd=path) == legacy
    assert (other / "other.txt").read_text() == "other branch follow-up\n"


def test_repeated_completion_retains_each_original_tip(pr_repo):
    config, wid, path, _ = pr_repo
    anchor = Path(config.default_repo.anchor)
    original = _git("rev-parse", "HEAD", cwd=path)
    _squash_merge_upstream(
        anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )
    first = pr_complete.complete_worktree(wid, config)
    assert first["success"], first
    (path / "local.txt").write_text("later local work\n")
    _git("add", "local.txt", cwd=path)
    _git("commit", "-m", "later local work", cwd=path)
    later = _git("rev-parse", "HEAD", cwd=path)
    _squash_merge_upstream(anchor, files={"upstream.txt": "advance\n"}, msg="advance")

    second = pr_complete.complete_worktree(wid, config)

    assert second["success"], second
    assert first["backup_ref"] != second["backup_ref"]
    assert _git("rev-parse", first["backup_ref"], cwd=path) == original
    assert _git("rev-parse", second["backup_ref"], cwd=path) == later
    assert not git_ops.ref_exists("refs/pre-complete-backup", cwd=path)
    assert (path / "local.txt").read_text() == "later local work\n"


def test_dry_run_and_noop_do_not_create_backups(pr_repo):
    config, wid, path, _ = pr_repo
    anchor = Path(config.default_repo.anchor)
    legacy = _git("rev-parse", "origin/master", cwd=path)
    _git("update-ref", "refs/pre-complete-backup", legacy, cwd=path)
    _squash_merge_upstream(
        anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )
    before = _refs(path)
    assert before == "refs/pre-complete-backup"
    preview = pr_complete.complete_worktree(wid, config, dry_run=True)
    assert preview["success"], preview
    assert "backup_ref" not in preview
    assert _refs(path) == before
    result = pr_complete.complete_worktree(wid, config)
    assert result["success"], result
    retained = _refs(path)
    assert set(retained.splitlines()) == {"refs/pre-complete-backup", result["backup_ref"]}
    noop = pr_complete.complete_worktree(wid, config)
    assert noop["success"] and noop["action"] == "up-to-date", noop
    assert "backup_ref" not in noop
    assert _refs(path) == retained


def test_existing_backup_collision_refuses_without_replacing_any_tip(pr_repo, monkeypatch):
    config, wid, path, _ = pr_repo
    anchor = Path(config.default_repo.anchor)
    before = _git("rev-parse", "HEAD", cwd=path)
    existing = _git("rev-parse", "origin/master", cwd=path)
    ref = f"refs/pre-complete-backups/worktree/{wid}/collision"
    _git("update-ref", ref, existing, cwd=path)
    monkeypatch.setattr(pr_complete.uuid, "uuid4", lambda: SimpleNamespace(hex="collision"))
    _squash_merge_upstream(
        anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash",
    )

    result = pr_complete.complete_worktree(wid, config)

    assert not result["success"], result
    assert "Could not create recovery ref" in result["error"]
    assert _git("rev-parse", "HEAD", cwd=path) == before
    assert _git("rev-parse", ref, cwd=path) == existing


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("json_output", [True, False])
def test_cli_reports_exact_retained_backup_on_success_and_failure(
    pr_repo, monkeypatch, capfd, success, json_output,
):
    _config, wid, _path, _ = pr_repo
    ref = "refs/pre-complete-backups/feature/$(example)/operation"
    result = {
        "success": success, "action": "rebased" if success else "error",
        "message": "replayed", "error": "conflict", "backup_ref": ref,
    }
    monkeypatch.setattr(pr_complete, "complete_worktree", lambda *a, **k: result)
    args = SimpleNamespace(config=None, worktree_id=wid, json=json_output, dry_run=False)
    assert pr_state_cli.cmd_pr_complete(args) == (0 if success else 1)
    captured = capfd.readouterr()
    if json_output:
        import json

        assert json.loads(captured.out)["backup_ref"] == ref
    else:
        assert f"git switch --detach {shlex.quote(ref)}" in captured.out
