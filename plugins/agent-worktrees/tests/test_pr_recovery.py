"""Backup-first synchronization and publication against disposable Git remotes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, git_collab, git_ops, pr_ops, pr_rebase, pr_recovery, tracking

pytestmark = pytest.mark.timeout(180)


def _git(*args, cwd):
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def _record(wid):
    return tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")


def _prepare(pr_repo, *, legacy=False):
    config, wid, path, remote = pr_repo
    branch = f"user/developer/topic-{git_ops.worktree_suffix(wid)}" if legacy else None
    first = pr_ops.create_pr(wid, config, title="Owned change", branch=branch)
    assert first["success"], first
    if legacy:
        record = _record(wid)
        record.branch = wid
        record.pr.base_sha = record.pr.patch_id = ""
        tracking.save_record(record)
    anchor = Path(config.default_repo.anchor)
    (anchor / "upstream.txt").write_text("new upstream work\n")
    _git("add", "upstream.txt", cwd=anchor)
    _git("commit", "-m", "advance upstream", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    return config, wid, path, remote, first["branch"], first["head_sha"]


def _point(path, *, pending=False):
    checkpoint = pr_recovery._pending_path(str(path)) if pending else pr_recovery._path(str(path))
    data = json.loads(checkpoint.read_text(encoding="utf-8"))
    return data["checkpoints"][data["latest"]] if data.get("version") == 2 else data


@pytest.mark.guard
def test_recovery_selects_only_the_live_pr_for_the_current_branch():
    older = tracking.PRRecord(state="open", branch="pr/older")
    newer = tracking.PRRecord(state="open", branch="pr/newer")
    finished = tracking.PRRecord(state="merged", branch="pr/finished")
    record = SimpleNamespace(prs=[older, finished, newer], active_pr=lambda: newer)
    assert pr_recovery.selected_pr("task", "worktree/task", record) is newer
    assert pr_recovery.selected_pr("task", older.branch, record) is older
    assert pr_recovery.selected_pr("task", "unrelated", record) is None
    assert pr_recovery.selected_pr("task", finished.branch, record) is None
    record = SimpleNamespace(prs=[finished], active_pr=lambda: finished)
    assert pr_recovery.selected_pr("task", "worktree/task", record) is None
    assert pr_recovery.selected_pr("task", "worktree/task", None) is None


@pytest.mark.guard
def test_backup_failure_prevents_rebase_contract(tmp_path, monkeypatch, capsys):
    config = SimpleNamespace(default_repo=SimpleNamespace(
        remote="origin", default_branch="main", worktree_root=str(tmp_path),
    ))
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(tracking, "load_record", lambda *_: None)
    monkeypatch.setattr(tracking, "resolve_worktree_path", lambda *_: str(tmp_path))
    monkeypatch.setattr(git_ops, "_get_current_branch_safe", lambda *_: "worktree/task")
    monkeypatch.setattr(git_ops, "is_clean", lambda **_: True)
    monkeypatch.setattr(git_ops, "fetch", lambda *_, **__: None)
    monkeypatch.setattr(git_ops, "ref_exists", lambda *_, **__: True)
    monkeypatch.setattr(git_ops, "rebase", lambda *_, **__: pytest.fail("must not rebase"))

    def fail(*_, **__):
        raise OSError("backup unavailable")

    monkeypatch.setattr(pr_recovery, "prepare", fail)
    assert not git_collab.sync_forward("task", config)
    assert "nothing rebased: backup unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("checkout_head", [False, True], ids=["worktree", "private-head"])
def test_backed_sync_publishes_legacy_pr_without_replay_journals(
    pr_repo, monkeypatch, capsys, checkout_head,
):
    config, wid, path, remote, branch, old = _prepare(pr_repo, legacy=True)
    if checkout_head:
        _git("checkout", "-B", branch, old, cwd=path)
        record = _record(wid)
        record.prs.append(tracking.PRRecord(
            state="open", branch=f"pr/newer-{git_ops.worktree_suffix(wid)}",
            head_sha="1" * 40, pr_id="newer", opened_at="2099-01-01T00:00:00",
        ))
        tracking.save_record(record)
    (path / "feedback.txt").write_text("unpublished source work\n")
    _git("add", "feedback.txt", cwd=path)
    _git("commit", "-m", "source feedback", cwd=path)
    original = _git("rev-parse", "HEAD", cwd=path)
    assert git_collab.sync_forward(wid, config)
    point = _point(path)
    assert point["local_head"] == original
    assert point["published_head"] == old
    assert f"git switch --detach {original}" in capsys.readouterr().out
    _git("reflog", "expire", "--expire=now", "--all", cwd=path)
    _git("gc", "--prune=now", cwd=path)
    assert _git("rev-parse", point["local_ref"], cwd=path) == original
    assert _git("rev-parse", point["published_ref"], cwd=path) == old
    assert _git("show", f'{point["local_ref"]}:feedback.txt', cwd=path) == "unpublished source work"
    monkeypatch.setattr(pr_rebase, "_replay", lambda *_: pytest.fail("backup must replace journal gate"))
    tip = _git("rev-parse", "HEAD", cwd=path)
    if checkout_head:
        result = pr_ops.create_pr(wid, config, title="Owned change", branch=branch)
        assert result["success"], result
    else:
        assert finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    published = next(pr for pr in _record(wid).prs if pr.branch == branch)
    assert published.base_sha == point["target_head"]
    assert published.patch_id


def test_sync_aborts_before_rebase_when_backup_cannot_be_written(pr_repo, monkeypatch):
    config, wid, path, _, _, old = _prepare(pr_repo)
    original = git_ops.git

    def fail_backup(*args, **kwargs):
        if args[:1] == ("update-ref",):
            raise git_ops.GitError(["git", *args], 1, "backup storage unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", fail_backup)
    assert not git_collab.sync_forward(wid, config)
    assert _git("rev-parse", "HEAD", cwd=path) == old
    assert not pr_recovery._path(str(path)).exists()


def test_recovery_point_survives_abort_and_distinguishes_repeated_syncs(pr_repo):
    config, wid, path, _, _, old = _prepare(pr_repo)
    first = pr_recovery.prepare(
        wid, f"worktree/{wid}", "origin/master", _record(wid), cwd=str(path),
    )
    second = pr_recovery.prepare(
        wid, f"worktree/{wid}", "origin/master", _record(wid), cwd=str(path),
    )
    assert first.local_ref != second.local_ref
    assert _git("rev-parse", first.local_ref, cwd=path) == old
    assert _git("rev-parse", second.local_ref, cwd=path) == old
    assert pr_recovery.synced(_record(wid), _record(wid).pr, old, old, cwd=str(path)) is None
    assert git_collab.sync_forward(wid, config)
    initial = _point(path)
    assert git_collab.sync_forward(wid, config)
    repeated = _point(path)
    assert initial["local_ref"] != repeated["local_ref"]
    assert _git("rev-parse", initial["local_ref"], cwd=path) == old
    assert repeated["published_head"] == old
    assert finalize.push_changes(wid, config)


def test_recovery_ref_writes_ignore_inherited_foreign_git_context(pr_repo, monkeypatch):
    config, wid, path, remote, _, old = _prepare(pr_repo)
    record = _record(wid)
    foreign = remote.parent / "foreign.git"
    _git("clone", "--bare", str(path), str(foreign), cwd=remote.parent)
    with monkeypatch.context() as patch:
        patch.setenv("GIT_DIR", str(foreign))
        patch.setenv("GIT_WORK_TREE", str(remote.parent))
        patch.setenv("GIT_INDEX_FILE", str(foreign / "index"))
        point = pr_recovery.prepare(
            wid, f"worktree/{wid}", "origin/master", record, cwd=str(path),
        )
    for ref in (point.local_ref, point.published_ref, point.lineage_ref):
        assert _git("rev-parse", ref, cwd=path) == old
    assert not _git(
        "--git-dir", str(foreign), "for-each-ref", "--format=%(refname)",
        "refs/agent-worktrees/recovery", cwd=path,
    )


def test_unrelated_branch_sync_keeps_completed_private_pr_authority(pr_repo):
    config, wid, path, remote, branch, _ = _prepare(pr_repo)
    assert git_collab.sync_forward(wid, config)
    completed = _point(path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    _git("reflog", "expire", "--expire=now", "--all", cwd=path)
    _git("checkout", "-b", "unrelated", "origin/master", cwd=path)
    assert git_collab.sync_forward(wid, config)
    assert _point(path) == completed
    assert not pr_recovery._pending_path(str(path)).exists()
    _git("checkout", f"worktree/{wid}", cwd=path)
    assert finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip


def test_another_live_pr_checkpoint_does_not_replace_older_publication_authority(pr_repo):
    config, wid, path, remote, branch, _ = _prepare(pr_repo)
    assert git_collab.sync_forward(wid, config)
    completed = _point(path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    record = _record(wid)
    record.prs.append(tracking.PRRecord(
        state="open", branch=f"pr/another-{git_ops.worktree_suffix(wid)}",
        head_sha=tip, pr_id="another", opened_at="2099-01-01T00:00:00",
    ))
    tracking.save_record(record)
    assert git_collab.sync_forward(wid, config)
    assert pr_recovery._points(str(path))[completed["pr_id"]] == completed
    assert _point(path)["pr_id"] == "another"
    _git("reflog", "expire", "--expire=now", "--all", cwd=path)
    _git("checkout", "-B", branch, tip, cwd=path)
    result = pr_ops.create_pr(wid, config, title="Owned change", branch=branch)
    assert result["success"], result
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip


def test_backup_never_refreshes_concurrent_remote_lease(pr_repo):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    assert git_collab.sync_forward(wid, config)
    other = remote.parent / "reviewer"
    _git("clone", str(remote), str(other), cwd=remote.parent)
    _git("config", "user.email", "reviewer@example.com", cwd=other)
    _git("config", "user.name", "Reviewer", cwd=other)
    _git("checkout", "-B", branch, f"origin/{branch}", cwd=other)
    (other / "reviewer.txt").write_text("concurrent change\n")
    _git("add", "reviewer.txt", cwd=other)
    _git("commit", "-m", "reviewer change", cwd=other)
    concurrent = _git("rev-parse", "HEAD", cwd=other)
    _git("push", "origin", branch, cwd=other)
    assert not finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == concurrent
    assert _record(wid).pr.head_sha == old
    assert _point(path)["published_head"] == old


def test_pre_sync_reset_cannot_launder_unrelated_source_into_rewrite_authority(pr_repo):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    _git("reset", "--hard", _record(wid).pr.base_sha, cwd=path)
    (path / "unrelated.txt").write_text("unrelated replacement\n")
    _git("add", "unrelated.txt", cwd=path)
    _git("commit", "-m", "unrelated replacement", cwd=path)
    unrelated = _git("rev-parse", "HEAD", cwd=path)
    assert not git_collab.sync_forward(wid, config)
    point = _point(path, pending=True)
    assert _git("rev-parse", "HEAD", cwd=path) == unrelated
    assert _git("rev-parse", point["local_ref"], cwd=path) == unrelated
    assert _git("rev-parse", point["published_ref"], cwd=path) == old
    assert not finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old


@pytest.mark.parametrize("failure", ["conflict", "checkpoint-write"])
def test_failed_repeated_sync_preserves_completed_authority_after_reflog_expiry(
    pr_repo, monkeypatch, failure,
):
    config, wid, path, remote, branch, _ = _prepare(pr_repo)
    assert git_collab.sync_forward(wid, config)
    completed = _point(path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    _git("reflog", "expire", "--expire=now", "--all", cwd=path)
    if failure == "conflict":
        anchor = Path(config.default_repo.anchor)
        (anchor / "a.txt").write_text("conflicting upstream addition\n")
        _git("add", "a.txt", cwd=anchor)
        _git("commit", "-m", "conflicting upstream addition", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)
    else:
        original = tracking._atomic_write

        def fail_completed(path, data):
            if path.name == "agent-worktrees-pr-recovery.json":
                raise OSError("completed checkpoint write failed")
            return original(path, data)

        monkeypatch.setattr(tracking, "_atomic_write", fail_completed)
    assert not git_collab.sync_forward(wid, config)
    assert _point(path) == completed
    assert _git("rev-parse", "HEAD", cwd=path) == tip
    _git("reflog", "expire", "--expire=now", "--all", cwd=path)
    monkeypatch.setattr(pr_rebase, "_replay", lambda *_: pytest.fail("must retain completed authority"))
    assert finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip


def test_backup_is_not_authority_for_another_association_or_unrelated_tip(pr_repo):
    config, wid, path, _, branch, old = _prepare(pr_repo)
    assert git_collab.sync_forward(wid, config)
    record = _record(wid)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert pr_recovery.synced(record, record.pr, old, tip, cwd=str(path))
    record.pr.number = 999
    assert pr_recovery.synced(record, record.pr, old, tip, cwd=str(path)) is None
    record = _record(wid)
    assert pr_recovery.synced(record, record.pr, old, old, cwd=str(path)) is None
    for destination in ("master", "feature/shared", "user/developer/another-topic"):
        record.pr.branch = destination
        assert pr_rebase.verify(
            record, config.default_repo, "origin",
            f"worktree/{wid}:refs/heads/{destination}", old, cwd=str(path),
        ) is None
    assert _git("rev-parse", f"origin/{branch}", cwd=path) == old


def test_corrupt_checkpoint_is_reported_without_publication(pr_repo, capsys):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    assert git_collab.sync_forward(wid, config)
    pr_recovery._path(str(path)).write_text('{"version": 1, "local_head": []}', encoding="utf-8")
    assert not finalize.push_changes(wid, config)
    assert "Invalid PR recovery checkpoint" in capsys.readouterr().out
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old
