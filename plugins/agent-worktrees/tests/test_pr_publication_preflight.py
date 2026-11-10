"""Managed publication must run configured built-ins before remote/provider mutation."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_worktrees import finalize, git_ops, pr_foreign_create, pr_ops, pr_publish, pr_config
from agent_worktrees import pr_publication_checks as checks
from agent_worktrees.pr_publication_config import PublicationChecksConfig


def enable(config):
    repo = config.default_repo
    config.repos[config.repo_name] = replace(
        repo, pr=replace(repo.pr, publication_checks=PublicationChecksConfig()),
    )


def test_unconfigured_create_preserves_existing_publication_defaults(pr_repo):
    config, worktree_id, _root, _remote = pr_repo
    assert config.default_repo.pr.publication_checks is None
    result = pr_ops.create_pr(worktree_id, config, title="Existing behavior")
    assert result["success"], result


def test_create_blocks_before_push_or_provider_then_publishes_reviewed_diff(pr_repo, monkeypatch):
    config, worktree_id, root, _remote = pr_repo
    enable(config)
    original_push = git_ops.push
    calls = []

    def push(*args, **kwargs):
        calls.append(args)
        return original_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", push)
    result = pr_ops.create_pr(worktree_id, config, title="Reviewed change")
    assert not result["success"] and not calls
    checks.record_self_review(root, "origin/master", config.default_repo.pr.publication_checks,
                              lenses=["runtime"], summary="checked")
    result = pr_ops.create_pr(worktree_id, config, title="Reviewed change")
    assert result["success"], result
    assert calls
    assert len(calls[-1][1].partition(":")[0]) == 40  # pinned candidate, not a movable branch


def test_push_changes_preflight_precedes_recovery_and_provider_mutation(pr_repo, monkeypatch):
    config, worktree_id, _root, _remote = pr_repo
    enable(config)
    from agent_worktrees import pr_break_glass
    monkeypatch.setattr(pr_break_glass, "publish_recovery",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mutation reached")))
    assert not finalize.push_changes(worktree_id, config)


def test_last_mile_preflight_runs_before_destination_or_transport(pr_repo, monkeypatch):
    config, _worktree_id, root, _remote = pr_repo
    enable(config)
    monkeypatch.setattr(pr_publish, "publish_lock",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("transport reached")))
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/change",
                                    cwd=str(root), repo=config.default_repo)
    assert not result
    assert "review" in result.stderr


def test_publish_records_checked_head_when_local_ref_advances_during_lock(pr_repo, monkeypatch):
    config, _worktree_id, root, remote = pr_repo
    enable(config)
    candidate = git_ops.git("rev-parse", "HEAD", cwd=root).stdout.strip()
    checks.record_self_review(root, "origin/master", config.default_repo.pr.publication_checks,
                              lenses=["runtime"], summary="checked candidate")
    original_lock = pr_publish.publish_lock

    @contextmanager
    def admission(*args, **kwargs):
        with original_lock(*args, **kwargs):
            (root / "a.txt").write_text("later unpublished candidate\n", encoding="utf-8")
            git_ops.git("add", "a.txt", cwd=root)
            git_ops.git("commit", "-qm", "advance local ref during lock admission", cwd=root)
            yield

    monkeypatch.setattr(pr_publish, "publish_lock", admission)
    result = pr_publish.push_checked(None, "origin", "HEAD:refs/heads/pinned",
                                    cwd=str(root), repo=config.default_repo)
    assert result, result.stderr
    assert git_ops.git("rev-parse", "HEAD", cwd=root).stdout.strip() != candidate
    assert git_ops.git("--git-dir", str(remote), "rev-parse", "pinned",
                       cwd=root, isolated_repository=True).stdout.strip() == candidate
    assert result.published_head_sha == candidate


def test_foreign_creation_with_local_receipt_requirement_refuses_before_provider(monkeypatch):
    config = SimpleNamespace()
    repo = SimpleNamespace(default_branch="main",
                           pr=SimpleNamespace(enabled=True, publication_checks=PublicationChecksConfig()))
    monkeypatch.setattr(pr_config, "resolve_repo_config_for_slug",
                        lambda *args: SimpleNamespace(resolved=True, same_as_active=False, repo_config=repo))
    result = pr_foreign_create.create_foreign_pr_from_branch(
        "owner", config, target_repo="example/project", from_branch="change", title="Change",
    )
    assert "source worktree" in result["error"]


@pytest.mark.parametrize("check", ["self_review", "scope_report"])
def test_rewrite_rechecks_candidate_advanced_while_waiting_for_admission(pr_repo, monkeypatch, check):
    from agent_worktrees import config as cfg, tracking

    config, worktree_id, root, remote = pr_repo
    assert pr_ops.create_pr(worktree_id, config, title="Initial publication")["success"]
    record = tracking.load_record(cfg.tracking_dir() / f"{worktree_id}.yaml")
    record.pr.state = "open"
    tracking.save_record(record)
    original_head = git_ops.git("rev-parse", "HEAD", cwd=root).stdout.strip()
    policy = PublicationChecksConfig(
        self_review=check == "self_review",
        allowed_paths=("a.txt", "b.txt") if check == "scope_report" else None,
    )
    repo = config.default_repo
    config.repos[config.repo_name] = replace(repo, pr=replace(repo.pr, publication_checks=policy))
    if policy.self_review:
        checks.record_self_review(root, "origin/master", policy, lenses=["runtime"], summary="checked A")
    acquire = finalize.FinalizeLock.acquire
    advanced = []

    def admission(lock):
        acquire(lock)
        if not advanced:
            path = root / ("outside.txt" if check == "scope_report" else "a.txt")
            path.write_text("unreviewed candidate B\n", "utf-8")
            git_ops.git("add", "-A", cwd=root)
            git_ops.git("commit", "-qm", "advance during admission", cwd=root)
            advanced.append(git_ops.git("rev-parse", "HEAD", cwd=root).stdout.strip())

    monkeypatch.setattr(finalize.FinalizeLock, "acquire", admission)
    monkeypatch.setattr(git_ops, "push",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unchecked transport")))
    checked = pr_publish.push_checked
    seen = []

    def boundary(*args, **kwargs):
        seen.append(kwargs.get("repo"))
        return checked(*args, **kwargs)

    monkeypatch.setattr(pr_publish, "push_checked", boundary)
    assert not finalize.push_changes(worktree_id, config, rewrite_pr=True)
    assert advanced and advanced[0] != original_head
    assert seen == [config.default_repo]
    assert git_ops.git("--git-dir", str(remote), "rev-parse", record.pr.branch,
                       cwd=root, isolated_repository=True, timeout=15, kill_tree=True).stdout.strip() == original_head
