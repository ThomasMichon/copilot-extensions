"""Failed pre-publication attempts retain exactly one recoverable association."""

from __future__ import annotations

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, pr_publication_state, tracking

pytestmark = pytest.mark.contract("agent_worktrees.pr_publish.publication_state")


@pytest.mark.parametrize("new", [False, True])
def test_rebase_failure_retry_reuses_unpublished_record(
    pr_repo: tuple, monkeypatch: pytest.MonkeyPatch, new: bool,
) -> None:
    config, wid, _wt, remote = pr_repo
    real_rebase = git_ops.rebase
    monkeypatch.setattr(git_ops, "rebase", lambda *_args, **_kwargs: False)
    failed = pr_ops.create_pr(wid, config, title="Retry example", new=True, open_pr=False)
    assert not failed["success"]
    assert "genuine conflicts" in failed["error"]
    path = cfg.tracking_dir() / f"{wid}.yaml"
    before = tracking.load_record(path)
    assert len(before.prs) == 1
    assert pr_publication_state.unpublished(before.pr)
    original = before.pr

    monkeypatch.setattr(git_ops, "rebase", real_rebase)
    result = pr_ops.create_pr(wid, config, title="Retry example", new=new, open_pr=False)
    assert result["success"], result
    after = tracking.load_record(path)
    assert len(after.prs) == 1
    assert after.pr.pr_id == original.pr_id
    assert after.pr.attribution_mode == original.attribution_mode
    assert after.pr.attribution_explicit == original.attribution_explicit
    assert after.pr.head_sha == git_ops.git(
        "--git-dir", str(remote), "rev-parse", f"refs/heads/{result['branch']}",
        cwd=str(_wt),
    ).stdout.strip()
    assert after.pr.head_sha == result["head_sha"]


@pytest.mark.guard
@pytest.mark.parametrize("field", [
    "url", "number", "base_sha", "head_sha", "patch_id", "rewrite_owner",
    "rewrite_identity", "remote", "head_repo", "head_identity", "head_owner",
    "head_observed_at", "head_observed_api_base", "attribution_head", "closed_at",
])
def test_publication_evidence_prevents_provisional_retry(field: str) -> None:
    record = tracking.PRRecord(state="creating")
    setattr(record, field, 7 if field == "number" else "recorded-evidence")
    assert not pr_publication_state.unpublished(record)


@pytest.mark.guard
@pytest.mark.parametrize("state", ["", "open", "active", "closed", "merged"])
def test_only_creating_entries_are_unpublished(state: str) -> None:
    assert not pr_publication_state.unpublished(tracking.PRRecord(state=state))
    assert not pr_publication_state.unpublished(None)


def test_different_new_head_preserves_unrelated_provisional(
    pr_repo: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, _wt, _remote = pr_repo
    real_rebase = git_ops.rebase
    monkeypatch.setattr(git_ops, "rebase", lambda *_args, **_kwargs: False)
    assert not pr_ops.create_pr(
        wid, config, title="First example", new=True, open_pr=False,
    )["success"]
    path = cfg.tracking_dir() / f"{wid}.yaml"
    original = tracking.load_record(path).pr
    monkeypatch.setattr(git_ops, "rebase", real_rebase)
    result = pr_ops.create_pr(wid, config, title="Other example", new=True, open_pr=False)
    assert result["success"], result
    after = tracking.load_record(path)
    assert len(after.prs) == 2
    assert after.prs[0].pr_id == original.pr_id
    assert pr_publication_state.unpublished(after.prs[0])
    assert after.prs[0].branch != after.prs[1].branch
