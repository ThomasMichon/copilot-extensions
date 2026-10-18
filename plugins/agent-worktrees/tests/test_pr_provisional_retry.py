"""Failed pre-publication attempts retain exactly one recoverable association."""

from __future__ import annotations

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, git_ops, pr_ops, pr_publication_state, pr_publish, tracking

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


@pytest.mark.guard
@pytest.mark.parametrize("field", ["branch", "provider", "repo"])
def test_provisional_retry_requires_same_destination(field: str) -> None:
    record = tracking.PRRecord(
        state="creating", branch="pr/example", provider="github", repo="example/repo",
    )
    assert pr_publication_state.unpublished_retry(
        record, "pr/example", "github", "example/repo",
    )
    setattr(record, field, "different")
    if field == "branch":
        assert not pr_publication_state.unpublished_retry(
            record, "pr/example", "github", "example/repo",
        )
    else:
        with pytest.raises(ValueError, match="destination changed"):
            pr_publication_state.unpublished_retry(
                record, "pr/example", "github", "example/repo",
            )


def test_unpublished_retry_preserves_existing_local_branch(
    pr_repo: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, wid, wt, _remote = pr_repo
    monkeypatch.setattr(git_ops, "rebase", lambda *_args, **_kwargs: False)
    failed = pr_ops.create_pr(wid, config, title="Retry example", new=True, open_pr=False)
    assert not failed["success"]
    path = cfg.tracking_dir() / f"{wid}.yaml"
    before = tracking.load_record(path)
    branch = before.pr.branch
    original = git_ops.git("rev-parse", "HEAD", cwd=str(wt)).stdout.strip()
    git_ops.git("branch", branch, cwd=str(wt))
    retry = pr_ops.create_pr(wid, config, title="Retry example", new=True, open_pr=False)
    assert not retry["success"]
    assert "already exists" in retry["error"]
    assert git_ops.git("rev-parse", branch, cwd=str(wt)).stdout.strip() == original
    assert len(tracking.load_record(path).prs) == 1


@pytest.mark.parametrize("new", [False, True])
def test_same_head_repository_change_refuses_without_mutation(
    pr_repo: tuple, monkeypatch: pytest.MonkeyPatch, new: bool,
) -> None:
    config, wid, wt, _remote = pr_repo
    monkeypatch.setattr(git_ops, "rebase", lambda *_args, **_kwargs: False)
    assert not pr_ops.create_pr(
        wid, config, title="Retry example", new=True, open_pr=False,
    )["success"]
    path = cfg.tracking_dir() / f"{wid}.yaml"
    before = tracking.load_record(path)
    branch = before.pr.branch
    git_ops.git("branch", branch, cwd=str(wt))
    original = git_ops.git("rev-parse", branch, cwd=str(wt)).stdout.strip()
    result = pr_ops.create_pr(
        wid, config, title="Retry example", target_repo="other/repository",
        new=new, open_pr=False,
    )
    assert not result["success"]
    assert "destination changed" in result["error"]
    after = tracking.load_record(path)
    assert after.prs == before.prs
    assert git_ops.git("rev-parse", branch, cwd=str(wt)).stdout.strip() == original


@pytest.mark.parametrize("explicit_branch", [False, True])
def test_interleaved_retry_reuses_original_attempt(
    pr_repo: tuple, monkeypatch: pytest.MonkeyPatch, explicit_branch: bool,
) -> None:
    config, wid, _wt, _remote = pr_repo
    real_rebase = git_ops.rebase
    monkeypatch.setattr(git_ops, "rebase", lambda *_args, **_kwargs: False)
    for title in ("First attempt", "Other attempt"):
        assert not pr_ops.create_pr(
            wid, config, title=title, new=True, open_pr=False,
        )["success"]
    path = cfg.tracking_dir() / f"{wid}.yaml"
    before = tracking.load_record(path)
    first, other = before.prs
    monkeypatch.setattr(git_ops, "rebase", real_rebase)
    result = pr_ops.create_pr(
        wid, config, title="First attempt", new=not explicit_branch,
        branch=first.branch if explicit_branch else None, open_pr=False,
    )
    assert result["success"], result
    after = tracking.load_record(path)
    assert len(after.prs) == 2
    assert after.prs[0].pr_id == first.pr_id
    assert after.prs[0].head_sha == result["head_sha"]
    assert after.prs[1] == other
    assert sum(not tracking._pr_is_terminal(pr) and pr.branch == first.branch
               for pr in after.prs) == 1
    assert after.active_pr().pr_id == first.pr_id
    assert after.prs[0].attribution_mode == first.attribution_mode
    assert after.prs[0].attribution_explicit == first.attribution_explicit
    (_wt / "followup.txt").write_text("followup\n", encoding="utf-8")
    git_ops.git("add", "followup.txt", cwd=str(_wt))
    git_ops.git("commit", "-m", "followup", cwd=str(_wt))
    assert finalize.push_changes(wid, config)
    updated = tracking.load_record(path)
    assert updated.active_pr().pr_id == first.pr_id
    assert updated.prs[1] == other
    assert updated.prs[0].head_sha == git_ops.git(
        "--git-dir", str(_remote), "rev-parse", f"refs/heads/{first.branch}",
        cwd=str(_wt),
    ).stdout.strip()


def _bare_record(**prs_kwargs) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt-001", branch="worktree/wt-001", worktree_path="/tmp/wt",
        repo="test-repo", machine="test-machine", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        sessions=None, **prs_kwargs,
    )


def test_fresh_publish_outranks_an_earlier_same_second_resumed_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed retry's bumped ``opened_at`` must not permanently outrank a
    genuinely later fresh publish that lands in the same whole-second clock
    tick (#7114 follow-up): both settle through the same monotonic-ordering
    policy, so whichever PR actually publishes last always sorts last.
    """
    frozen = "2026-06-01T10:00:51"
    monkeypatch.setattr(tracking, "_now_iso", lambda: frozen)

    # A: an unpublished attempt resumes and publishes first.
    resumed_pr = tracking.PRRecord(state="creating", branch="pr/a", opened_at=frozen)
    record = _bare_record(prs=[resumed_pr])
    pr_publication_state.settle_creation(record, resumed_pr)
    assert record.active_pr().branch == "pr/a"

    # C: an unrelated, entirely fresh PR publishes moments later -- still
    # within the same whole-second tick that _now_iso() can represent.
    fresh_pr = tracking.PRRecord(state="creating", branch="pr/c", opened_at=frozen)
    record.prs.append(fresh_pr)
    pr_publication_state.settle_creation(record, fresh_pr)

    assert record.active_pr().branch == "pr/c"


def test_failed_retry_allocation_outranks_an_earlier_same_second_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provisional attempt allocated after an earlier settle in the same
    whole-second clock tick must still sort later than that settle (#7114
    follow-up): successful A -> failed B -> a no-selector retry must resume
    B, not fall back to the earlier, already-settled A.
    """
    frozen = "2026-06-01T10:00:51"
    monkeypatch.setattr(tracking, "_now_iso", lambda: frozen)

    # A: an attempt settles (publishes) successfully.
    settled_pr = tracking.PRRecord(state="creating", branch="pr/a", opened_at=frozen)
    record = _bare_record(prs=[settled_pr])
    pr_publication_state.settle_creation(record, settled_pr)
    assert record.active_pr().branch == "pr/a"

    # B: a distinct --new attempt is allocated moments later -- still within
    # the same whole-second tick -- then fails before ever settling (e.g. a
    # rebase conflict), leaving it unpublished.
    failed_pr = tracking.PRRecord(
        state="creating", branch="pr/b",
        opened_at=pr_publication_state.monotonic_opened_at(record),
    )
    record.prs.append(failed_pr)
    assert pr_publication_state.unpublished(failed_pr)

    # A no-selector retry must target B (the most recently attempted PR),
    # not fall back to the earlier, already-settled A.
    assert record.active_pr().branch == "pr/b"


def test_legacy_feature_branch_allocation_outranks_an_earlier_same_second_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy feature-branch publication path (``prepare_existing_publication``,
    used by the already-on-the-feature-branch re-run path) must share the same
    monotonic-ordering policy as fresh/retry allocation and settlement (#7114
    follow-up) -- it was still stamping a plain ``_now_iso()`` at allocation,
    letting it sort *before* an unrelated sibling settled moments earlier in
    the same whole-second clock tick.
    """
    frozen = "2026-06-01T10:00:51"
    monkeypatch.setattr(tracking, "_now_iso", lambda: frozen)

    settled_pr = tracking.PRRecord(state="creating", branch="pr/a", opened_at=frozen)
    record = _bare_record(prs=[settled_pr])
    pr_publication_state.settle_creation(record, settled_pr)
    assert record.active_pr().branch == "pr/a"

    legacy_pr = pr_publish.prepare_existing_publication(
        record, None, "pr/legacy", cfg.PRConfig(), None,
    )
    record.prs.append(legacy_pr)

    assert record.active_pr().branch == "pr/legacy"
