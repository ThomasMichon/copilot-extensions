"""Attested custom heads preserve real rebase ownership without suffix bypass."""

from __future__ import annotations

from types import SimpleNamespace
import copy

import pytest

from agent_worktrees import git_collab, git_ops, pr_custom_head, pr_publish, tracking
from tests.test_pr_rebase import _advance, _git, _prepare, _record

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
    )
    monkeypatch.setattr(git_ops, "remote_slug", lambda *a, **k: "example/project")
    monkeypatch.setattr(git_ops, "_auth_config_args", lambda *a, **k: [])
    monkeypatch.setattr(pr_publish, "push_identity", lambda *a, **k: identity)
    monkeypatch.setattr(pr_custom_head.pr_authority, "assert_exclusive", lambda *a, **k: ())
    monkeypatch.setattr(pr_custom_head.providers, "account_token_for_slug", lambda *a: "")
    monkeypatch.setattr(pr_custom_head.providers, "get_provider", lambda *a: SimpleNamespace(get_pull=lambda *a, **k: observed))
    return config, record, path, remote, old, observed


@pytest.mark.guard
def test_attested_custom_branch_proves_source_owned_sync(pr_repo, monkeypatch):
    from agent_worktrees import pr_rebase

    config, record, path, remote, old, observed = _attested(pr_repo, monkeypatch)
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


@pytest.mark.guard
def test_custom_head_rejects_unproven_or_stale_authority(pr_repo, monkeypatch):
    config, record, path, remote, old, observed = _attested(pr_repo, monkeypatch)
    baseline = copy.deepcopy(record)
    saved_observation = vars(observed).copy()
    for mutation in ("unattested", "foreign-owner", "destination", "stale-head", "foreign-pr",
                     "foreign-branch", "foreign-repo", "closed", "nonexclusive", "missing-provider"):
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
            elif mutation == "nonexclusive":
                patch.setattr(pr_custom_head.pr_authority, "assert_exclusive",
                              lambda *a, **k: (_ for _ in ()).throw(ValueError("foreign ledger")))
            else:
                patch.setattr(pr_custom_head.providers, "get_provider",
                              lambda *a: (_ for _ in ()).throw(RuntimeError("unavailable")))
            assert not pr_custom_head.verify_attested(record, config.default_repo, record.pr, "origin", old, cwd=str(path)), mutation
