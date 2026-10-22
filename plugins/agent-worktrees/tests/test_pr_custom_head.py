"""Attested custom heads preserve real rebase ownership without suffix bypass."""

from __future__ import annotations

from types import SimpleNamespace
import copy

import pytest

from pr_test_helpers import _stub_remote_target

from agent_worktrees import git_collab, git_ops, pr_custom_head, pr_publish, tracking
from test_pr_rebase import _advance, _git, _prepare, _record

pytestmark = pytest.mark.timeout(180)


def _attested(pr_repo, monkeypatch):
    config, wid, path, remote, branch, old = _prepare(
        pr_repo, "refspec", branch="pr/portable-repair",
    )
    record = _record(wid)
    pr = record.pr
    pr.number, pr.repo, pr.url, pr.state = 42, "example/project", "https://example.com/example/project/pulls/42", "open"
    tracking.save_record(record)
    identity = "example.com/example/project"
    assert not pr_publish.record_rewrite_ownership(config, record, pr, branch, old, identity)
    record = _record(wid)
    observed = SimpleNamespace(
        number=42, state="open", merged=False, head_sha=old,
        head_ref=branch, head_url="https://example.com/example/project",
        url=pr.url,
    )
    monkeypatch.setattr(git_ops, "remote_slug", lambda *a, **k: "example/project")
    monkeypatch.setattr(git_ops, "_auth_config_args", lambda *a, **k: [])
    _stub_remote_target(monkeypatch, None, "example/project", identity)
    monkeypatch.setattr(pr_custom_head.pr_authority, "assert_exclusive", lambda *a, **k: ())
    monkeypatch.setattr(pr_custom_head.providers, "account_token_for_slug", lambda *a: "")
    monkeypatch.setattr(pr_custom_head.providers, "get_provider", lambda *a: SimpleNamespace(get_pull=lambda *a, **k: observed))
    return config, record, path, remote, old, observed


def test_attested_custom_branch_proves_source_owned_sync(pr_repo, monkeypatch):
    from agent_worktrees import pr_rebase

    config, record, path, remote, old, observed = _attested(pr_repo, monkeypatch)
    def full_config_authority(actual_config, *args, **kwargs):
        assert actual_config.default_repo.remote == config.default_repo.remote
        assert actual_config.repo_name == config.repo_name
        return ()
    monkeypatch.setattr(pr_custom_head.pr_authority, "assert_exclusive", full_config_authority)
    _advance(config, path)
    assert git_collab.sync_forward(record.worktree_id, config)
    refreshed = _record(record.worktree_id)
    reasons = []
    proof = pr_rebase.verify(
        refreshed, config.default_repo, "origin",
        f"{record.branch}:refs/heads/{record.pr.branch}", old, cwd=str(path), reasons=reasons,
    )
    assert proof is not None, reasons
    assert proof.branch == "pr/portable-repair" and proof.expected_head == old
    assert _git("--git-dir", str(remote), "rev-parse", observed.head_ref, cwd=path) == old
    result = pr_rebase.push(
        refreshed, config.default_repo, "origin",
        f"{record.branch}:refs/heads/{record.pr.branch}", old, cwd=str(path),
    )
    assert result, result.stderr
    assert _git("--git-dir", str(remote), "rev-parse", observed.head_ref, cwd=path) == proof.source_head


def test_custom_head_rejects_unproven_or_stale_authority(pr_repo, monkeypatch):
    config, record, path, remote, old, observed = _attested(pr_repo, monkeypatch)
    baseline = copy.deepcopy(record)
    saved_observation = vars(observed).copy()
    for mutation in ("unattested", "foreign-owner", "destination", "stale-head", "foreign-pr",
                     "foreign-branch", "foreign-repo", "closed", "nonexclusive", "missing-provider", "unrecorded-provider", "shared-feature", "endpoint-drift"):
        record = copy.deepcopy(baseline)
        vars(observed).update(saved_observation)
        with monkeypatch.context() as patch:
            if mutation == "unattested":
                record.pr.rewrite_owner = ""
            elif mutation == "foreign-owner":
                record.pr.rewrite_owner = "other-worktree:" + record.pr.branch
            elif mutation == "destination":
                record.pr.rewrite_identity = "other.example/example/project"
            elif mutation == "stale-head":
                observed.head_sha = "a" * 40
            elif mutation == "foreign-pr":
                observed.number = 43
            elif mutation == "foreign-branch":
                observed.head_ref = "pr/another"
            elif mutation == "foreign-repo":
                observed.head_url = "https://example.com/other/project"
            elif mutation == "closed":
                observed.state = "closed"
            elif mutation == "unrecorded-provider":
                record.pr.provider = ""
            elif mutation == "shared-feature":
                record.pr.branch = observed.head_ref = "feature/shared"
                record.pr.rewrite_owner = f"{record.worktree_id}:feature/shared"
            elif mutation == "endpoint-drift":
                observed.url = "https://other.example/example/project/pulls/42"
            elif mutation == "nonexclusive":
                patch.setattr(pr_custom_head.pr_authority, "assert_exclusive",
                              lambda *a, **k: (_ for _ in ()).throw(ValueError("foreign ledger")))
            else:
                patch.setattr(pr_custom_head.providers, "get_provider",
                              lambda *a: (_ for _ in ()).throw(RuntimeError("unavailable")))
            assert not pr_custom_head.verify_attested(record, config.default_repo, record.pr, "origin", old, cwd=str(path)), mutation


def test_public_explicit_rewrite_round_trip_and_stale_live_head(pr_repo, monkeypatch):
    from agent_worktrees import finalize, pr_ops, pr_rewrite

    config, record, path, remote, old, observed = _attested(pr_repo, monkeypatch)
    monkeypatch.setattr(pr_publish, "push_target", lambda *a, **k: pr_publish.PushTarget("origin"))
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: observed)
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    (path / "feedback.txt").write_text("reviewed feedback\n")
    _git("add", "feedback.txt", cwd=path)
    _git("commit", "-m", "feedback", cwd=path)
    assert finalize.push_changes(record.worktree_id, config, rewrite_pr=True)
    published = _git("--git-dir", str(remote), "rev-parse", observed.head_ref, cwd=path)
    assert published == _git("rev-parse", "HEAD", cwd=path)
    assert _record(record.worktree_id).pr.head_sha == published
    observed.head_sha = "a" * 40
    (path / "later.txt").write_text("later source\n")
    _git("add", "later.txt", cwd=path)
    _git("commit", "-m", "later", cwd=path)
    assert not finalize.push_changes(record.worktree_id, config, rewrite_pr=True)
    assert _git("--git-dir", str(remote), "rev-parse", observed.head_ref, cwd=path) == published


def test_fork_head_identity_is_distinct_from_base_pr_repository(pr_repo, monkeypatch):
    config, record, path, remote, old, observed = _attested(pr_repo, monkeypatch)
    record.pr.rewrite_identity = "example.com/contributor/project"
    observed.head_url = "https://example.com/contributor/project"
    monkeypatch.setattr(pr_publish, "push_identity", lambda *a, **k: record.pr.rewrite_identity)
    monkeypatch.setattr(git_ops, "remote_slug",
                        lambda remote, **k: "example/project" if remote == "origin" else "contributor/project")
    monkeypatch.setattr(pr_publish, "_tip", lambda *a: old)
    assert pr_custom_head.verify_attested(record, config.default_repo, record.pr, "fork", old, cwd=str(path),
                                          config=config, observed=observed, remote_tip=old)
    record.pr.repo = "contributor/project"
    assert not pr_custom_head.verify_attested(record, config.default_repo, record.pr, "fork", old, cwd=str(path),
                                              config=config, observed=observed, remote_tip=old)
