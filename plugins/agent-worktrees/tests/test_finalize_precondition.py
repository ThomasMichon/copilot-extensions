"""Tests for the PR-mode finalize precondition (issue #21).

These exercise ``_resolve_content_ref`` and ``_pr_finalize_precondition``
against real temporary git repos, focusing on the refspec head scheme where
the local ``pr/<slug>`` branch never exists (the worktree stays on
``worktree/<id>`` and ``pr/<slug>`` is only ever a *remote* push target).

Before the fix, the precondition probed the non-existent local ``pr/<slug>``
ref for the "is the content already upstream?" check; combined with a remote
feature branch auto-deleted on merge, that false-blocked finalize of an
already-merged PR.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from agent_worktrees import finalize, finalize_open_pr_gate, tracking


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", f"add {name}", cwd=repo)


def _init_identity(repo: Path) -> None:
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)


@pytest.fixture
def refspec_worktree(tmp_path: Path) -> SimpleNamespace:
    """Build a refspec-scheme worktree whose work is merged to origin/master.

    Layout:
      - ``origin.git`` bare remote with ``master`` carrying the merged work.
      - a clone checked out on ``worktree/<id>`` (never a local ``pr/<slug>``).
      - the remote has NO ``pr/<slug>`` head (auto-deleted on merge).
    """
    worktree_id = "anomalous-potato-wsl-test"
    slug = "pr/some-fix-test"

    origin = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "master", str(origin), cwd=tmp_path)

    seed = tmp_path / "seed"
    _git("init", "-b", "master", str(seed), cwd=tmp_path)
    _init_identity(seed)
    _commit(seed, "base.txt", "base\n")
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "origin", "master", cwd=seed)

    clone = tmp_path / "worktree"
    _git("clone", str(origin), str(clone), cwd=tmp_path)
    _init_identity(clone)
    # The refspec scheme keeps the worktree permanently on worktree/<id>.
    _git("checkout", "-b", f"worktree/{worktree_id}", cwd=clone)
    _commit(clone, "fix.txt", "the fix\n")

    return SimpleNamespace(
        tmp_path=tmp_path,
        origin=origin,
        clone=clone,
        seed=seed,
        worktree_id=worktree_id,
        slug=slug,
    )


def _land_on_master(env: SimpleNamespace, *, squash: bool) -> None:
    """Publish the worktree's work onto origin/master, then fetch it.

    ``squash=False`` -> the worktree commit itself becomes an ancestor of
    origin/master. ``squash=True`` -> a distinct commit with the same tree is
    pushed (worktree HEAD is NOT an ancestor; patch-id/blob strategies apply).
    """
    if squash:
        _git("checkout", "master", cwd=env.seed)
        (env.seed / "fix.txt").write_text("the fix\n")
        _git("add", "-A", cwd=env.seed)
        _git("commit", "-m", "squashed fix", cwd=env.seed)
        _git("push", "origin", "master", cwd=env.seed)
    else:
        head = _git("rev-parse", "HEAD", cwd=env.clone)
        _git("push", str(env.origin), f"{head}:refs/heads/master", cwd=env.clone)
    _git("fetch", "origin", cwd=env.clone)


def _record_and_repo(env: SimpleNamespace):
    record = SimpleNamespace(
        worktree_id=env.worktree_id,
        pr=SimpleNamespace(branch=env.slug),
    )
    repo = SimpleNamespace(
        remote="origin",
        default_branch="master",
        pr=SimpleNamespace(enabled=True, branch=env.slug),
    )
    return record, repo


def test_worktree_branch_prefers_tracked_host_branch():
    record = tracking.WorktreeRecord(
        worktree_id="app-session",
        branch="host/app-session",
        worktree_path="/tmp/app-session",
        repo="owner/repo",
        machine="host",
        platform="linux",
        started_at="2026-09-01T00:00:00",
        last_resumed_at="2026-09-01T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        checkout_managed=False,
    )

    assert finalize._worktree_branch(record, record.worktree_id) == "host/app-session"
    assert finalize._worktree_branch(None, "managed") == "worktree/managed"


def test_precondition_refreshes_missing_head_for_provider_confirmed_merge(
    refspec_worktree, monkeypatch,
):
    from agent_worktrees import providers
    from agent_worktrees.providers.base import PullResult

    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _land_on_master(env, squash=False)

    class Provider:
        def get_pull(self, repo, number, **kwargs):
            return PullResult(state="merged", merged=True)

        def observe_head(self, repo, number, **kwargs):
            return PullResult(head_sha=head_sha)

    monkeypatch.setattr(providers, "get_provider", lambda _name: Provider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_a, **_k: None)
    pr = SimpleNamespace(
        branch=env.slug, state="merged", head_sha="", number=7,
        repo="owner/repo", provider="github",
    )
    record = SimpleNamespace(
        worktree_id=env.worktree_id, pr=pr, prs=[pr], repo="owner/repo",
    )
    repo = SimpleNamespace(
        remote="origin",
        default_branch="master",
        pr=SimpleNamespace(
            enabled=True, branch=env.slug, provider="github", api_base="",
        ),
    )

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone),
    )

    assert ok is True
    assert err is None
    assert pr.head_sha == head_sha


def test_precondition_still_blocks_new_commits_after_refreshed_merged_head(
    refspec_worktree, monkeypatch,
):
    from agent_worktrees import providers
    from agent_worktrees.providers.base import PullResult

    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _land_on_master(env, squash=False)
    _commit(env.clone, "later.txt", "not part of the merged PR\n")

    class Provider:
        def get_pull(self, repo, number, **kwargs):
            return PullResult(state="merged", merged=True, head_sha=head_sha)

    monkeypatch.setattr(providers, "get_provider", lambda _name: Provider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_a, **_k: None)
    pr = SimpleNamespace(
        branch=env.slug, state="merged", head_sha="", number=7,
        repo="owner/repo", provider="github",
    )
    record = SimpleNamespace(
        worktree_id=env.worktree_id, pr=pr, prs=[pr], repo="owner/repo",
    )
    repo = SimpleNamespace(
        remote="origin",
        default_branch="master",
        pr=SimpleNamespace(
            enabled=True, branch=env.slug, provider="github", api_base="",
        ),
    )

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone),
    )

    assert ok is False
    assert err is not None
    assert "further commits" in err


def test_precondition_repairs_azure_merged_head_from_live_pr_metadata(
        refspec_worktree, monkeypatch,
):
    from agent_worktrees import providers
    from agent_worktrees.providers import azure_devops

    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _land_on_master(env, squash=False)

    monkeypatch.setattr(
        azure_devops, "run_cli",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "completed",
                "lastMergeSourceCommit": {"commitId": head_sha},
            }),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        providers, "get_provider", lambda _name: azure_devops.AzureDevOpsProvider(),
    )
    monkeypatch.setattr(
        providers, "account_token_for_slug", lambda *_a, **_k: None,
    )
    pr = SimpleNamespace(
        branch=env.slug, state="merged", head_sha="", number=7,
        repo="Project/repo", provider="azure-devops",
    )
    record = SimpleNamespace(
        worktree_id=env.worktree_id, pr=pr, prs=[pr], repo="Project/repo",
    )
    repo = SimpleNamespace(
        remote="origin",
        default_branch="master",
        pr=SimpleNamespace(
            enabled=True, branch=env.slug, provider="azure-devops",
            api_base="https://dev.azure.com/acme",
        ),
    )

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone),
    )

    assert ok is True
    assert err is None
    assert pr.head_sha == head_sha


# ---------------------------------------------------------------------------
# _resolve_content_ref
# ---------------------------------------------------------------------------

def test_resolve_prefers_local_feature_branch(refspec_worktree):
    env = refspec_worktree
    # Materialize a local pr/<slug> ref (legacy snapshot scheme).
    _git("branch", env.slug, "HEAD", cwd=env.clone)
    ref = finalize._resolve_content_ref(
        env.slug, env.worktree_id, cwd=str(env.clone)
    )
    assert ref == env.slug


def test_resolve_falls_back_to_worktree_branch(refspec_worktree):
    env = refspec_worktree
    # No local pr/<slug> ref exists (the refspec scheme never creates it).
    ref = finalize._resolve_content_ref(
        env.slug, env.worktree_id, cwd=str(env.clone)
    )
    assert ref == f"worktree/{env.worktree_id}"


def test_resolve_falls_back_to_head(refspec_worktree):
    env = refspec_worktree
    # Neither the feature branch nor a worktree/<id> branch resolves.
    ref = finalize._resolve_content_ref(
        env.slug, "nonexistent-id", cwd=str(env.clone)
    )
    assert ref == "HEAD"


# ---------------------------------------------------------------------------
# _pr_finalize_precondition -- refspec merged cases (issue #21)
# ---------------------------------------------------------------------------

def test_precondition_passes_when_merged_ancestor(refspec_worktree):
    env = refspec_worktree
    _land_on_master(env, squash=False)
    record, repo = _record_and_repo(env)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is True
    assert err is None


def test_precondition_passes_when_squash_merged(refspec_worktree):
    env = refspec_worktree
    _land_on_master(env, squash=True)
    record, repo = _record_and_repo(env)

    # Sanity: the worktree HEAD is NOT an ancestor of origin/master here.
    rc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", "origin/master"],
        cwd=str(env.clone),
    ).returncode
    assert rc != 0, "expected squash-merge to break the ancestor relationship"

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is True
    assert err is None


def _push_feature_head(env: SimpleNamespace) -> None:
    """Publish the ``pr/<slug>`` head to origin (an OPEN PR: the feature branch
    exists on the remote) WITHOUT landing the work on master."""
    head = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("push", str(env.origin), f"{head}:refs/heads/{env.slug}", cwd=env.clone)
    _git("fetch", "origin", cwd=env.clone)


def test_precondition_blocks_when_not_upstream_detach(refspec_worktree):
    env = refspec_worktree
    # Do NOT land the work on master; the remote also has no pr/<slug> head.
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)
    repo.pr.strategy = "detach"

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None
    # Detached and neither merged nor branch-on-origin -> guide to
    # create-pr; never point at a (possibly deleted) feature branch as the fix.
    assert "not upstream" in err
    assert "create-pr" in err


def test_precondition_blocks_when_not_upstream_default_is_keep_alive(refspec_worktree):
    # The safe default (unset strategy) must behave as keep-alive, not detach:
    # neither merged nor on master yet -> guide to sync (realign after merge),
    # never accept "a PR is merely open" as sufficient. Regression coverage for
    # the strategy fallback itself (config.py / finalize.py), not just the
    # explicit keep-alive case already covered by test_keepalive_ignores_feature_branch.
    env = refspec_worktree
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None
    assert "sync" in err


def test_precondition_passes_when_pr_recorded_merged(refspec_worktree):
    # The tracked PR is merged (record state) -- squash-safe, branch-independent,
    # and immune to version-file churn on the moving upstream tip. Neither the
    # content-on-master check nor a feature branch is needed, PROVIDED the
    # current worktree content is exactly that merged PR's tracked head (no
    # further local commits since).
    env = refspec_worktree
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = _git("rev-parse", "HEAD", cwd=env.clone)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is True
    assert err is None


def test_precondition_blocks_merged_pr_with_later_local_commits(refspec_worktree):
    # #4388 review finding: a commit added to the SAME keep-alive worktree
    # AFTER its tracked PR merged is real, un-PR'd work -- "an older PR from
    # this worktree merged" must never certify it safe to prune. head_sha is
    # the merged PR's tracked head (the pre-existing fix.txt commit); a later
    # local commit sits on top of it, unmerged and unrepresented anywhere
    # upstream.
    env = refspec_worktree
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _commit(env.clone, "feedback.txt", "more work after merge\n")

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None
    assert "further commits" in err
    assert "merged" in err


def test_precondition_blocks_merged_pr_snapshot_mode_stale_feature_branch(
    refspec_worktree,
):
    # #4400 review finding: under the snapshot head scheme, a local
    # feature/<slug> branch DOES exist (a frozen snapshot of the PR head at
    # push time) and _resolve_content_ref finds it BEFORE worktree/<id>. That
    # snapshot never reflects commits added later to the live worktree/<id>
    # checkout -- comparing against it (instead of worktree/<id> directly)
    # would wrongly certify newer, un-PR'd work safe. Simulate this by
    # creating a local branch literally named the tracked PR's branch,
    # frozen at the merged head, while worktree/<id> gains a further commit.
    env = refspec_worktree
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("branch", env.slug, record.pr.head_sha, cwd=env.clone)
    _commit(env.clone, "feedback.txt", "more work after merge\n")

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None
    assert "further commits" in err


class TestContentExceedsMergedHeadFailsClosed:
    """#4400 review finding: each inconclusive-lookup branch of
    ``content_exceeds_merged_head`` must independently prove it blocks
    (returns True == "exceeds") rather than silently degrading into a
    permissive fast path on a future ref/record migration."""

    def test_missing_head_sha(self, refspec_worktree):
        env = refspec_worktree
        record = SimpleNamespace(pr=SimpleNamespace(head_sha=""))
        content_ref = f"worktree/{env.worktree_id}"
        assert finalize_open_pr_gate.content_exceeds_merged_head(
            record, content_ref, "origin/master", cwd=str(env.clone),
        ) is True

    def test_none_content_ref(self, refspec_worktree):
        env = refspec_worktree
        head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
        record = SimpleNamespace(pr=SimpleNamespace(head_sha=head_sha))
        assert finalize_open_pr_gate.content_exceeds_merged_head(
            record, None, "origin/master", cwd=str(env.clone),
        ) is True

    def test_unresolvable_head_sha(self, refspec_worktree):
        env = refspec_worktree
        record = SimpleNamespace(pr=SimpleNamespace(head_sha="0" * 40))
        content_ref = f"worktree/{env.worktree_id}"
        assert finalize_open_pr_gate.content_exceeds_merged_head(
            record, content_ref, "origin/master", cwd=str(env.clone),
        ) is True

    def test_unresolvable_content_ref(self, refspec_worktree):
        env = refspec_worktree
        head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
        record = SimpleNamespace(pr=SimpleNamespace(head_sha=head_sha))
        assert finalize_open_pr_gate.content_exceeds_merged_head(
            record, "no-such-ref-at-all", "origin/master", cwd=str(env.clone),
        ) is True

    def test_failed_rev_list_unresolvable_upstream(self, refspec_worktree):
        """Distinct from the ref_exists(content_ref) short-circuit above:
        content_ref and head_sha both resolve, but `upstream` doesn't --
        `rev-list` itself errors (non-zero exit), the ``extra.returncode !=
        0`` branch, never checked in isolation elsewhere."""
        env = refspec_worktree
        head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
        record = SimpleNamespace(pr=SimpleNamespace(head_sha=head_sha))
        content_ref = f"worktree/{env.worktree_id}"
        assert finalize_open_pr_gate.content_exceeds_merged_head(
            record, content_ref, "no-such-upstream-ref-at-all", cwd=str(env.clone),
        ) is True


class TestMergedPrBlockMessageDistinguishesInconclusive:
    """#4400 eighth review round: merged_pr_block_message must never claim
    "carries further commits" for an inconclusive lookup content_exceeds_
    merged_head itself fails closed on -- an unresolvable head_sha here is
    a syntactically-present string that still fails `ref_exists`."""

    def test_unresolvable_head_sha_reports_unverifiable(self, refspec_worktree):
        env = refspec_worktree
        record = SimpleNamespace(
            worktree_id=env.worktree_id, pr=SimpleNamespace(head_sha="0" * 40), prs=[],
        )
        content_ref = f"worktree/{env.worktree_id}"
        msg = finalize_open_pr_gate.merged_pr_block_message(
            record, content_ref, "origin/master", cwd=str(env.clone),
        )
        assert "can't be verified" in msg
        assert "carries further commits" not in msg

    def test_confirmed_extra_commit_reports_further_commits(self, refspec_worktree):
        # Round 11 tightened this message to require a genuine positive
        # rev-list count (not merely that head_sha/content_ref resolve) --
        # so the fixture must actually carry a commit beyond the merged head
        # that isn't reachable from upstream either.
        env = refspec_worktree
        head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
        _land_on_master(env, squash=False)
        _commit(env.clone, "feedback.txt", "more work after merge\n")
        record = SimpleNamespace(
            worktree_id=env.worktree_id, pr=SimpleNamespace(head_sha=head_sha), prs=[],
        )
        content_ref = f"worktree/{env.worktree_id}"
        msg = finalize_open_pr_gate.merged_pr_block_message(
            record, content_ref, "origin/master", cwd=str(env.clone),
        )
        assert "carries further commits" in msg


def test_precondition_blocks_merged_pr_when_stale_snapshot_matches_upstream(
    refspec_worktree,
):
    # #4400 second review round: the stale-snapshot problem also bypasses
    # step 1's OWN upstream-content fast path, not just the merged-head
    # check -- once the merged PR's head lands on origin/master, comparing
    # the frozen local feature/<slug> snapshot (which matches that exact
    # content) against upstream returns True immediately, before the
    # merged-head check ever runs, silently pruning a LATER commit that only
    # exists on the live worktree/<id> checkout. Reproduce by landing the
    # tracked head_sha on master directly (not via the worktree checkout),
    # creating a local snapshot branch at that same content, then adding a
    # real new commit to worktree/<id> itself.
    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("push", str(env.origin), f"{head_sha}:refs/heads/master", cwd=env.clone)
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = head_sha
    _git("branch", env.slug, head_sha, cwd=env.clone)
    _commit(env.clone, "feedback.txt", "more work after merge\n")

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None
    assert "further commits" in err


def test_precondition_blocks_merged_pr_empty_commit_matches_upstream_tree(
    refspec_worktree,
):
    # #4400 third review round: step 1's upstream-content fast path is a
    # TREE comparison, not a commit-count comparison -- an empty commit (or
    # an add+revert pair) after the tracked PR merges leaves the tree
    # byte-identical to origin/<default> while still being a real, tracked
    # commit past the merged head_sha. Without gating step 1 on the
    # merged-head check too, this would return True before
    # content_exceeds_merged_head ever runs. Land head_sha directly on
    # master (no snapshot branch needed this time), then add an empty
    # commit to worktree/<id> whose tree still matches upstream exactly.
    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("push", str(env.origin), f"{head_sha}:refs/heads/master", cwd=env.clone)
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = head_sha
    _git("commit", "--allow-empty", "-m", "no-op after merge", cwd=env.clone)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None
    assert "further commits" in err


def test_precondition_blocks_legacy_merged_record_missing_head_sha(
    refspec_worktree,
):
    # #4400 fourth review round: upstream_match_is_trustworthy previously
    # treated a missing head_sha as trustworthy unconditionally -- but a
    # MERGED record with no head_sha is a legacy/incomplete record, not
    # proof there's nothing to compare against. It must fail closed (not
    # trusted) so this falls through to content_exceeds_merged_head, which
    # itself also fails closed on a missing head_sha, rather than letting
    # step 1's tree-only match wave a later empty commit through.
    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("push", str(env.origin), f"{head_sha}:refs/heads/master", cwd=env.clone)
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    _git("commit", "--allow-empty", "-m", "no-op after merge", cwd=env.clone)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_precondition_passes_after_pr_complete_realignment_past_squash(
    refspec_worktree,
):
    # #4400 fifth review round: the original head_sha..content_ref range
    # counted every commit reachable from a squash-merge-realigned
    # worktree/<id> that ISN'T an ancestor of the pre-squash head_sha --
    # including master's own unrelated history -- wrongly reporting "further
    # commits" even though pr-complete/sync correctly realigned the worktree
    # exactly onto upstream. Excluding commits also reachable from upstream
    # (not just head_sha) fixes the false positive.
    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _land_on_master(env, squash=True)
    # Simulate pr-complete's own post-squash realignment: reset worktree/<id>
    # forward to exactly match the new upstream tip.
    _git("reset", "--hard", "origin/master", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = head_sha

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is True, err
    assert err is None


def test_precondition_blocks_stale_snapshot_via_untrusted_checkout_fallback(
    refspec_worktree,
):
    # #4400 tenth review round: when the current checkout can't be trusted
    # (round 7-9's fix) and falls back to _resolve_content_ref, that fallback
    # prefers the frozen feature/<slug> snapshot BEFORE worktree/<id> --
    # reintroducing rounds 2-3's original stale-snapshot hole through this
    # new fallback path. Reproduce: the tracked PR's content is squash-merged
    # (the OLD feature snapshot patch-id-matches upstream), a LATER commit
    # lands on worktree/<id> (real, unmerged work), and an unrelated branch
    # is checked out (forcing the untrusted-checkout fallback). The fallback
    # must resolve worktree/<id> (which still has the later commit) before
    # the stale feature snapshot (which doesn't).
    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("branch", env.slug, head_sha, cwd=env.clone)  # frozen snapshot
    _land_on_master(env, squash=True)
    _git("fetch", "origin", cwd=env.clone)
    _commit(env.clone, "feedback.txt", "more work after merge\n")
    _git("checkout", "-b", "unrelated-clean-branch", "origin/master", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = head_sha

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_precondition_blocks_when_unrelated_clean_branch_checked_out(
    refspec_worktree,
):
    # #4400 seventh review round: resolve_precondition_ref must not trust
    # ANY currently-checked-out branch unconditionally -- validate_and_finalize
    # still deletes the TRACKED branch (worktree/<id>) on cleanup regardless
    # of what's checked out. Checking out an unrelated branch that happens
    # to be clean and already match upstream must never stand in for
    # worktree/<id>'s own, still-unmerged content: the current checkout is
    # only trusted when neither tracked branch name carries commits beyond
    # it; anything else falls through to validating the tracked branch
    # itself, exactly as if the checkout were gone.
    env = refspec_worktree
    _git("fetch", "origin", cwd=env.clone)
    _git("checkout", "-b", "unrelated-clean-branch", "origin/master", cwd=env.clone)
    record, repo = _record_and_repo(env)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_precondition_blocks_when_detached_head_behind_tracked_branch(
    refspec_worktree,
):
    # #4400 eighth review round: the same trust-nothing-unconditionally rule
    # applies to a genuinely detached HEAD, not just a named branch --
    # detaching to an ancestor commit (matching upstream) while worktree/<id>
    # itself still carries a later, unmerged commit must not let the
    # detached commit stand in for that later work; cleanup deletes
    # worktree/<id> regardless of what HEAD is detached to.
    env = refspec_worktree
    _git("fetch", "origin", cwd=env.clone)
    base_sha = _git("rev-parse", "origin/master", cwd=env.clone)
    _git("checkout", "--detach", base_sha, cwd=env.clone)
    record, repo = _record_and_repo(env)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_detach_accepts_open_feature_branch(refspec_worktree):
    # Detached mode finalizes BEFORE merge: a feature branch on origin (an open
    # PR) is a sufficient early ok, even with nothing on master yet.
    env = refspec_worktree
    _push_feature_head(env)
    record, repo = _record_and_repo(env)
    repo.pr.strategy = "detach"

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is True, err
    assert err is None


def test_keepalive_ignores_feature_branch(refspec_worktree):
    # keep-alive tracks ONLY alignment with origin/<default>: a feature branch on
    # origin is NOT sufficient. Guide to sync (realign after the PR merges),
    # never to the feature branch.
    env = refspec_worktree
    _push_feature_head(env)
    record, repo = _record_and_repo(env)
    repo.pr.strategy = "keep-alive"

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert "sync" in err
    assert "keep-alive" in err


def test_precondition_blocks_merged_pr_extra_commit_on_tracked_feature_branch(
    refspec_worktree,
):
    # #4400 eleventh review round: content_exceeds_merged_head (and
    # upstream_match_is_trustworthy's step-1 fast path) only validated
    # whichever ONE ref was chosen as the live-content safety ref -- but
    # validate_and_finalize's cleanup unconditionally force-deletes EVERY
    # tracked PR's local feature branch (record.prs[*].branch), not just
    # worktree/<id>. Reproduce: worktree/<id> itself matches the merged head
    # exactly (the fast path alone would certify it safe), but a separate
    # local branch tracked as this worktree's PR branch carries its own
    # later, unmerged commit -- cleanup would force-delete it, discarding
    # real work, unless BOTH refs are validated.
    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _land_on_master(env, squash=False)
    _git("branch", env.slug, head_sha, cwd=env.clone)
    _git("checkout", env.slug, cwd=env.clone)
    _commit(env.clone, "unshipped.txt", "not yet merged\n")
    _git("checkout", f"worktree/{env.worktree_id}", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = head_sha
    record.prs = [SimpleNamespace(branch=env.slug)]

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_precondition_blocks_provider_confirmed_merge_missing_head_sha(
    refspec_worktree, monkeypatch,
):
    # #4400 round 12: upstream_match_is_trustworthy previously asked only the
    # LOCAL record.pr.state -- but finalize._pr_is_merged's authoritative
    # check can confirm a merge via the provider even while the local state
    # is still "open" (a stale/failed reconcile). A missing head_sha in that
    # case must still fail closed at the fast path (never certify "safe"
    # merely because the local state hadn't caught up), not slip through
    # unvalidated because state != "merged" locally.
    env = refspec_worktree
    _land_on_master(env, squash=False)  # content_ref matches upstream exactly
    record, repo = _record_and_repo(env)
    record.pr.state = "open"
    record.pr.head_sha = ""
    record.pr.number = 99
    record.pr.repo = "owner/repo"
    monkeypatch.setattr(finalize_open_pr_gate, "pr_merge_status", lambda rec, rp: True)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_precondition_blocks_indeterminate_merge_status_missing_head_sha(
    refspec_worktree, monkeypatch,
):
    # #4400 round 13: a missing head_sha's fail-closed check must distinguish
    # "confirmed NOT merged" (trustworthy -- nothing to compare against) from
    # "couldn't determine" (a provider/network error on a genuinely TRACKED
    # PR) -- the latter must fail closed too. Round 12's fix used
    # `not _pr_is_merged(...)`, which collapses BOTH a confirmed-unmerged PR
    # and a provider error to the same permissive `True` (trustworthy);
    # `pr_merge_status`'s tri-state return distinguishes them.
    env = refspec_worktree
    _land_on_master(env, squash=False)  # content_ref matches upstream exactly
    record, repo = _record_and_repo(env)
    record.pr.state = "open"
    record.pr.head_sha = ""
    record.pr.number = 99
    record.pr.repo = "owner/repo"
    monkeypatch.setattr(finalize_open_pr_gate, "pr_merge_status", lambda rec, rp: None)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_precondition_blocks_merged_pr_extra_commit_on_legacy_record_branch(
    refspec_worktree,
):
    # #4400 round 13: `_cleanup_branch_refs` previously checked only
    # `worktree/<id>` plus tracked PR feature branches -- but
    # `validate_and_finalize` deletes the TRACKED branch via
    # `_worktree_branch(record, worktree_id)`, which prefers `record.branch`
    # (a legacy/non-canonical name) over `worktree/<id>` when set. A commit
    # that exists only on that legacy `record.branch` must still block
    # finalize even when `worktree/<id>` itself matches the merged head.
    env = refspec_worktree
    head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _land_on_master(env, squash=False)
    legacy_branch = "legacy-tracked-branch"
    _git("branch", legacy_branch, head_sha, cwd=env.clone)
    _git("checkout", legacy_branch, cwd=env.clone)
    _commit(env.clone, "unshipped.txt", "not yet merged\n")
    _git("checkout", f"worktree/{env.worktree_id}", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = head_sha
    record.branch = legacy_branch
    record.prs = []

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


class TestPrMergeStatusIndeterminateVsUnmerged:
    """#4400 round 14: pr_merge_status must distinguish "a PR was never even
    opened" (confirmed NOT merged, False) from "a PR record exists but is
    only partially populated" (indeterminate, None) -- collapsing the
    latter into False let a tree-only upstream match certify and prune a
    record whose merge boundary genuinely can't be checked."""

    def test_a_project_name_repo_is_queried_by_the_slug_its_pr_url_names(self):
        """Older records store the project name ("my-project"), not the
        hosting owner/name: the provider must be asked for the URL's repo, or a
        merged, aligned worktree can never finalize."""
        from agent_worktrees import providers
        from agent_worktrees.providers.github import GitHubProvider

        asked = []

        class _Merged(GitHubProvider):
            def get_pull(self, slug, number, **_kwargs):
                asked.append((slug, number))
                return SimpleNamespace(merged=True, state="merged", head_sha="abc123")

        with mock.patch.object(providers, "get_provider", lambda _name: _Merged()), \
                mock.patch.object(providers, "account_token_for_slug", lambda *_a: None):
            pr = SimpleNamespace(
                branch="user/x/fix", repo="my-project", number=584, provider="github",
                state="merged", head_sha="", url="https://github.com/octo/my-project/pull/584",
            )
            repo = SimpleNamespace(pr=SimpleNamespace(provider="github", api_base=""))
            assert finalize_open_pr_gate.pr_merge_status(SimpleNamespace(pr=pr, prs=[pr]), repo) is True
        assert asked[0] == ("octo/my-project", 584)
        assert pr.head_sha == "abc123"  # repaired in place for the boundary check

    def test_no_pr_at_all_is_confirmed_unmerged(self):
        record = SimpleNamespace(pr=None)
        assert finalize_open_pr_gate.pr_merge_status(record, repo=None) is False

    def test_pr_with_neither_number_nor_repo_is_confirmed_unmerged(self):
        # Never even reached create-pr -- nothing could have merged.
        record = SimpleNamespace(pr=SimpleNamespace(branch="pr/some-fix"))
        assert finalize_open_pr_gate.pr_merge_status(record, repo=None) is False

    def test_pr_with_number_but_no_repo_is_indeterminate(self):
        record = SimpleNamespace(pr=SimpleNamespace(branch="pr/some-fix", number=42))
        assert finalize_open_pr_gate.pr_merge_status(record, repo=None) is None

    def test_pr_with_repo_but_no_number_is_indeterminate(self):
        record = SimpleNamespace(
            pr=SimpleNamespace(branch="pr/some-fix", repo="owner/repo"),
        )
        assert finalize_open_pr_gate.pr_merge_status(record, repo=None) is None

    def test_mismatched_tracked_provider_is_indeterminate_not_queried(self):
        # A legacy record can retain a different provider than the repo is
        # now configured for. Querying the configured provider with the
        # same slug/number could confirm an unrelated merged PR and hand
        # back the wrong head -- fail closed instead of ever calling it.
        pr = SimpleNamespace(
            branch="pr/some-fix", repo="owner/repo", number=7,
            provider="azure-devops", state="open", head_sha="",
        )
        record = SimpleNamespace(pr=pr)
        repo = SimpleNamespace(
            pr=SimpleNamespace(provider="github", api_base=""),
        )
        assert finalize_open_pr_gate.pr_merge_status(record, repo) is None

    def test_mismatched_tracked_authority_is_indeterminate_not_queried(self):
        # Same provider KIND, but the tracked PR's own URL names a host that
        # no longer matches the repo's currently configured authority (e.g.
        # the repo moved to a different GitHub Enterprise instance). Querying
        # the NEW authority with the OLD slug/number could confirm an
        # unrelated merged PR there and hand back the wrong head -- fail
        # closed instead of ever calling get_pull().
        from agent_worktrees import providers
        from agent_worktrees.providers.github import GitHubProvider

        class _BoomOnQuery(GitHubProvider):
            def get_pull(self, *_args, **_kwargs):
                raise AssertionError("must not query a provider across a host change")

        with mock.patch.object(
            providers, "get_provider", lambda _name: _BoomOnQuery(),
        ):
            pr = SimpleNamespace(
                branch="pr/some-fix", repo="owner/repo", number=7,
                provider="github", state="open", head_sha="",
                url="https://old-host.example.com/owner/repo/pull/7",
            )
            record = SimpleNamespace(pr=pr)
            repo = SimpleNamespace(
                pr=SimpleNamespace(
                    provider="github", api_base="https://new-host.example.com",
                ),
            )
            assert finalize_open_pr_gate.pr_merge_status(record, repo) is None

    def test_mismatched_azure_devops_organization_is_indeterminate_not_queried(self):
        # Same host (``dev.azure.com``), but the tracked PR's own URL names
        # a DIFFERENT organization segment than the repo's currently
        # configured ``api_base``. Azure DevOps' ``authority_endpoint()``
        # returns the full ``.../<org>`` URL -- host[:port] equality alone
        # would miss this (two different orgs share the same host), letting
        # a stale tracked PR be re-queried against the wrong org and
        # confirm an unrelated merge there. Fail closed instead.
        from agent_worktrees import providers
        from agent_worktrees.providers.azure_devops import AzureDevOpsProvider

        class _BoomOnQuery(AzureDevOpsProvider):
            def get_pull(self, *_args, **_kwargs):
                raise AssertionError("must not query a provider across an org change")

        with mock.patch.object(
            providers, "get_provider", lambda _name: _BoomOnQuery(),
        ):
            pr = SimpleNamespace(
                branch="pr/some-fix", repo="project/repo", number=7,
                provider="azure-devops", state="open", head_sha="",
                url=(
                    "https://dev.azure.com/old-org/project/_git/repo"
                    "/pullrequest/7"
                ),
            )
            record = SimpleNamespace(pr=pr)
            repo = SimpleNamespace(
                pr=SimpleNamespace(
                    provider="azure-devops",
                    api_base="https://dev.azure.com/new-org",
                ),
            )
            assert finalize_open_pr_gate.pr_merge_status(record, repo) is None

    def test_mismatched_gitea_root_path_is_indeterminate_not_queried(self):
        # Same host, but the tracked PR's own URL sits under a DIFFERENT
        # root path than the repo's currently configured path-hosted
        # ``api_base`` (e.g. a different Gitea instance sharing the host).
        # Host[:port] equality alone would miss this -- Gitea's
        # ``authority_endpoint()`` returns the full ``api_base`` URL
        # including its root path, which host-only comparison discards.
        # Fail closed instead of querying across instances.
        from agent_worktrees import providers
        from agent_worktrees.providers.gitea import GiteaProvider

        class _BoomOnQuery(GiteaProvider):
            def get_pull(self, *_args, **_kwargs):
                raise AssertionError(
                    "must not query a provider across a root-path change",
                )

        with mock.patch.object(
            providers, "get_provider", lambda _name: _BoomOnQuery(),
        ):
            pr = SimpleNamespace(
                branch="pr/some-fix", repo="owner/project", number=12,
                provider="gitea", state="open", head_sha="",
                url="https://forge.example/other-app/owner/project/pulls/12",
            )
            record = SimpleNamespace(pr=pr)
            repo = SimpleNamespace(
                pr=SimpleNamespace(
                    provider="gitea", api_base="https://forge.example/gitea",
                ),
            )
            assert finalize_open_pr_gate.pr_merge_status(record, repo) is None

    def test_mismatched_gitea_scheme_is_indeterminate_not_queried(self):
        # Same host and path root, but the tracked PR's own URL uses a
        # DIFFERENT scheme (http) than the repo's currently configured
        # https api_base. Comparing host[:port] alone would collapse these
        # onto the same value and treat them as the same authority --
        # querying the https instance with a PR identity tracked against
        # a DIFFERENT (http) origin could confirm an unrelated merge there.
        from agent_worktrees import providers
        from agent_worktrees.providers.gitea import GiteaProvider

        class _BoomOnQuery(GiteaProvider):
            def get_pull(self, *_args, **_kwargs):
                raise AssertionError(
                    "must not query a provider across a scheme change",
                )

        with mock.patch.object(
            providers, "get_provider", lambda _name: _BoomOnQuery(),
        ):
            pr = SimpleNamespace(
                branch="pr/some-fix", repo="owner/project", number=12,
                provider="gitea", state="open", head_sha="",
                url="http://forge.example/gitea/owner/project/pulls/12",
            )
            record = SimpleNamespace(pr=pr)
            repo = SimpleNamespace(
                pr=SimpleNamespace(
                    provider="gitea", api_base="https://forge.example/gitea",
                ),
            )
            assert finalize_open_pr_gate.pr_merge_status(record, repo) is None

    def test_unconfigured_gitea_authority_is_indeterminate_not_queried(self):
        # Gitea's authority_endpoint() returns the bare (stripped) api_base
        # verbatim -- an empty/unconfigured api_base yields an empty
        # string, which must NOT be treated as "no authority to check,
        # proceed anyway". An empty configured endpoint can never be
        # proven to match the tracked PR's own origin, so this must fail
        # closed (indeterminate) rather than silently skip validation and
        # query an arbitrary tracked URL unchecked.
        from agent_worktrees import providers
        from agent_worktrees.providers.gitea import GiteaProvider

        class _BoomOnQuery(GiteaProvider):
            def get_pull(self, *_args, **_kwargs):
                raise AssertionError(
                    "must not query with an unconfigured/unverifiable authority",
                )

        with mock.patch.object(
            providers, "get_provider", lambda _name: _BoomOnQuery(),
        ):
            pr = SimpleNamespace(
                branch="pr/some-fix", repo="owner/project", number=12,
                provider="gitea", state="open", head_sha="",
                url="https://forge.example/gitea/owner/project/pulls/12",
            )
            record = SimpleNamespace(pr=pr)
            repo = SimpleNamespace(
                pr=SimpleNamespace(provider="gitea", api_base=""),
            )
            assert finalize_open_pr_gate.pr_merge_status(record, repo) is None


class TestRepairOtherTrackedPrHeads:
    """#4751 round 12: content_exceeds_merged_head_any's per-branch check
    (_cleanup_branch_refs) fails closed on ANY record.prs[*] entry missing
    its own head_sha -- but only record.pr (the active entry) ever got a
    repair attempt via pr_merge_status. A worktree carrying more than one
    merged PR could never finalize even when every entry is confirmable and
    repairable via the provider. repair_other_tracked_pr_heads (and its
    wiring into content_exceeds_merged_head_any's optional `repo` param)
    closes that gap.
    """

    def test_repairs_missing_head_sha_on_non_active_merged_pr(self):
        from agent_worktrees import providers
        from agent_worktrees.providers.github import GitHubProvider

        class _Confirms(GitHubProvider):
            def get_pull(self, repo_slug, number, *, api_base="", token=""):
                assert (repo_slug, number) == ("owner/repo", 9)
                return SimpleNamespace(
                    merged=True, state="merged", head_sha="c" * 40,
                )

        active = SimpleNamespace(
            branch="pr/active", repo="owner/repo", number=1,
            provider="github", state="open", head_sha="a" * 40,
        )
        other = SimpleNamespace(
            branch="pr/other", repo="owner/repo", number=9,
            provider="github", state="open", head_sha="",
        )
        record = SimpleNamespace(pr=active, prs=[active, other])
        repo = SimpleNamespace(pr=SimpleNamespace(provider="github", api_base=""))

        with mock.patch.object(
            providers, "get_provider", lambda _name: _Confirms(),
        ):
            finalize_open_pr_gate.repair_other_tracked_pr_heads(record, repo)

        assert other.head_sha == "c" * 40
        assert other.state == "merged"
        # The active entry is untouched by this helper -- pr_merge_status
        # (called separately by upstream_match_is_trustworthy) owns it.
        assert active.head_sha == "a" * 40

    def test_does_not_touch_entries_that_already_have_a_head_sha(self):
        from agent_worktrees import providers
        from agent_worktrees.providers.github import GitHubProvider

        class _BoomOnQuery(GitHubProvider):
            def get_pull(self, *_args, **_kwargs):
                raise AssertionError("must not re-query an already-populated entry")

        active = SimpleNamespace(
            branch="pr/active", repo="owner/repo", number=1,
            provider="github", state="open", head_sha="a" * 40,
        )
        other = SimpleNamespace(
            branch="pr/other", repo="owner/repo", number=9,
            provider="github", state="merged", head_sha="b" * 40,
        )
        record = SimpleNamespace(pr=active, prs=[active, other])
        repo = SimpleNamespace(pr=SimpleNamespace(provider="github", api_base=""))

        with mock.patch.object(
            providers, "get_provider", lambda _name: _BoomOnQuery(),
        ):
            finalize_open_pr_gate.repair_other_tracked_pr_heads(record, repo)

        assert other.head_sha == "b" * 40

    def test_content_exceeds_merged_head_any_repairs_before_the_boundary_check(
        self, refspec_worktree, monkeypatch,
    ):
        from agent_worktrees import providers
        from agent_worktrees.providers.github import GitHubProvider

        env = refspec_worktree
        other_branch = "pr/other-parallel-fix"
        _git("branch", other_branch, cwd=env.clone)
        record, repo = _record_and_repo(env)
        repo.pr.provider = "github"
        repo.pr.api_base = ""
        active_head_sha = "a" * 40
        record.pr.head_sha = active_head_sha
        record.pr.number = None
        other = SimpleNamespace(
            branch=other_branch, repo="owner/repo", number=9,
            provider="github", state="open", head_sha="",
        )
        record.prs = [record.pr, other]

        class _Confirms(GitHubProvider):
            def get_pull(self, repo_slug, number, *, api_base="", token=""):
                return SimpleNamespace(
                    merged=True, state="merged", head_sha="b" * 40,
                )

        monkeypatch.setattr(
            finalize_open_pr_gate, "content_exceeds_merged_head", lambda *a, **k: False,
        )
        with mock.patch.object(
            providers, "get_provider", lambda _name: _Confirms(),
        ):
            content_ref = f"worktree/{env.worktree_id}"
            finalize_open_pr_gate.content_exceeds_merged_head_any(
                record, content_ref, "origin/master", cwd=str(env.clone), repo=repo,
            )

        assert other.head_sha == "b" * 40


def test_content_exceeds_merged_head_any_checks_each_pr_branch_against_own_head(
    refspec_worktree, monkeypatch,
):
    # #4400 round 14 (deterministic unit-level proof, independent of git
    # ancestor topology): each cleanup branch must be validated against ITS
    # OWN PR's head_sha, never universally against the active PR's -- a
    # shared boundary can incorrectly exclude a historical/parallel PR's
    # real content via unrelated ancestry. Spies on `_extra_commit_count`'s
    # call arguments to prove the correct (ref, head_sha) pairing directly.
    env = refspec_worktree
    other_branch = "pr/other-parallel-fix"
    _git("branch", other_branch, cwd=env.clone)
    record, repo = _record_and_repo(env)
    active_head_sha = "a" * 40
    other_head_sha = "b" * 40
    record.pr.head_sha = active_head_sha
    record.prs = [SimpleNamespace(branch=other_branch, head_sha=other_head_sha, state="merged")]

    seen_calls: list[tuple[str, str]] = []

    def spy_count(ref, head_sha, upstream, *, cwd):
        seen_calls.append((ref, head_sha))
        return 0

    monkeypatch.setattr(finalize_open_pr_gate, "_extra_commit_count", spy_count)
    monkeypatch.setattr(
        finalize_open_pr_gate, "content_exceeds_merged_head", lambda *a, **k: False,
    )

    content_ref = f"worktree/{env.worktree_id}"
    finalize_open_pr_gate.content_exceeds_merged_head_any(
        record, content_ref, "origin/master", cwd=str(env.clone), repo=repo,
    )

    other_calls = [(r, h) for r, h in seen_calls if r == other_branch]
    assert other_calls, "expected other_branch to be checked"
    assert all(h == other_head_sha for _, h in other_calls), (
        f"other_branch must be checked against its OWN head_sha, got {other_calls}"
    )


def test_cleanup_branch_refs_keeps_latest_entry_for_a_reused_branch_name():
    # #4699: a legacy flow can reuse the SAME local branch name across
    # sequential PRs on one long-lived worktree
    # (`tracking._merge_pr_attribution_state` documents this exact reuse
    # pattern for `pr_id`-less records). The live git ref for that name can
    # only ever point at the MOST RECENT push -- never an earlier one -- so
    # `_cleanup_branch_refs` must pair a reused branch with its LATEST
    # `record.prs` entry, not the first one encountered in append order.
    record = SimpleNamespace(
        worktree_id="some-worktree",
        branch="",
        pr=SimpleNamespace(head_sha="active" * 8),
        prs=[
            SimpleNamespace(branch="reused-branch", head_sha="old-head-sha"),
            SimpleNamespace(branch="reused-branch", head_sha="new-head-sha"),
            SimpleNamespace(branch="unrelated-branch", head_sha="unrelated-sha"),
        ],
    )

    pairs = finalize_open_pr_gate._cleanup_branch_refs(record)

    assert ("reused-branch", "new-head-sha") in pairs
    assert ("reused-branch", "old-head-sha") not in pairs
    assert ("unrelated-branch", "unrelated-sha") in pairs
    reused_count = sum(1 for ref, _ in pairs if ref == "reused-branch")
    assert reused_count == 1, "a reused branch name must appear only once"


def test_cleanup_branch_refs_uses_opened_at_recency_not_raw_list_order():
    # #4699: `record.prs` is not guaranteed to be chronological --
    # `tracking.WorktreeRecord.active_pr` defines recency by `opened_at`,
    # only falling back to list position as a tie-breaker; concurrent-save
    # reconciliation can append an OLDER unmatched on-disk entry after a
    # NEWER in-memory one. A reused branch name must therefore be paired
    # with whichever entry has the LATEST `opened_at`, never simply the
    # last one in raw list order.
    record = SimpleNamespace(
        worktree_id="some-worktree",
        branch="",
        pr=SimpleNamespace(head_sha="active" * 8),
        prs=[
            # Chronologically LATER (opened_at), but appears FIRST in the
            # list -- e.g. reconciliation appended the older entry after it.
            SimpleNamespace(
                branch="reused-branch", head_sha="new-head-sha",
                opened_at="2026-02-01T00:00:00Z", state="merged",
            ),
            SimpleNamespace(
                branch="reused-branch", head_sha="old-head-sha",
                opened_at="2026-01-01T00:00:00Z", state="merged",
            ),
        ],
    )

    pairs = finalize_open_pr_gate._cleanup_branch_refs(record)

    assert ("reused-branch", "new-head-sha") in pairs
    assert ("reused-branch", "old-head-sha") not in pairs


def test_cleanup_branch_refs_prefers_merged_entry_over_a_later_rejected_reuse():
    # #4699: the LATEST tracked entry for a reused branch
    # name is not necessarily the one that merged -- a rejected (closed)
    # reuse can be the most recent. `_cleanup_branch_refs` must not let a
    # confirmed-terminal-non-merge entry overwrite an earlier entry that
    # could plausibly represent real landed work.
    record = SimpleNamespace(
        worktree_id="some-worktree",
        branch="",
        pr=SimpleNamespace(head_sha="active" * 8),
        prs=[
            SimpleNamespace(branch="reused-branch", head_sha="merged-sha", state="merged"),
            SimpleNamespace(branch="reused-branch", head_sha="rejected-sha", state="closed"),
        ],
    )

    pairs = finalize_open_pr_gate._cleanup_branch_refs(record)

    assert ("reused-branch", "merged-sha") in pairs
    assert ("reused-branch", "rejected-sha") not in pairs


def test_cleanup_branch_refs_uses_a_later_merge_after_an_earlier_rejection():
    # Counterpart: once a LATER reuse of the same branch name is itself
    # merged, it must win over an earlier rejected attempt.
    record = SimpleNamespace(
        worktree_id="some-worktree",
        branch="",
        pr=SimpleNamespace(head_sha="active" * 8),
        prs=[
            SimpleNamespace(branch="reused-branch", head_sha="rejected-sha", state="closed"),
            SimpleNamespace(branch="reused-branch", head_sha="merged-sha", state="merged"),
        ],
    )

    pairs = finalize_open_pr_gate._cleanup_branch_refs(record)

    assert ("reused-branch", "merged-sha") in pairs
    assert ("reused-branch", "rejected-sha") not in pairs


def test_precondition_passes_with_reused_branch_name_across_sequential_prs(
    refspec_worktree,
):
    # #4699 end-to-end regression: a reused branch name must be paired with
    # its LATEST (most recent) `record.prs` entry, never its FIRST -- the
    # live git ref for a reused name can only ever point at the most recent
    # push. Pairing it with an earlier, stale `head_sha` instead would read
    # every commit made for a LATER reuse of that same branch name as
    # "extra" commits beyond that stale boundary (a squash merge always
    # breaks ancestry, so those later commits are never reachable from
    # `upstream` by SHA either), falsely blocking finalize even though BOTH
    # reuses had already landed on master via squash merge.
    env = refspec_worktree
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = _git("rev-parse", "HEAD", cwd=env.clone)

    reused_branch = "legacy-reused-branch"
    _git("branch", reused_branch, cwd=env.clone)
    _git("checkout", reused_branch, cwd=env.clone)

    # First reuse ("PR #1"): its own commit, squash-merged onto master.
    _commit(env.clone, "legacy_v1.txt", "legacy reuse #1\n")
    first_head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("checkout", "master", cwd=env.seed)
    (env.seed / "legacy_v1.txt").write_text("legacy reuse #1\n")
    _git("add", "-A", cwd=env.seed)
    _git("commit", "-m", "squashed legacy PR #1", cwd=env.seed)
    _git("push", "origin", "master", cwd=env.seed)

    # Second reuse ("PR #2"): the SAME local branch name advanced further
    # with distinct content, ALSO squash-merged onto master.
    _commit(env.clone, "legacy_v2.txt", "legacy reuse #2\n")
    second_head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    (env.seed / "legacy_v2.txt").write_text("legacy reuse #2\n")
    _git("add", "-A", cwd=env.seed)
    _git("commit", "-m", "squashed legacy PR #2", cwd=env.seed)
    _git("push", "origin", "master", cwd=env.seed)

    _git("checkout", f"worktree/{env.worktree_id}", cwd=env.clone)
    _git("fetch", "origin", cwd=env.clone)

    record.prs = [
        SimpleNamespace(branch=reused_branch, head_sha=first_head_sha, state="merged"),
        SimpleNamespace(branch=reused_branch, head_sha=second_head_sha, state="merged"),
    ]

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is True, f"expected no false-positive block, got: {err}"
    assert err is None


def test_precondition_blocks_reused_branch_whose_latest_entry_is_unmerged(
    refspec_worktree,
):
    # #4699: the LATEST tracked entry for a reused branch
    # name is not necessarily the one that merged. A sequence of a merged
    # reuse followed by a REJECTED (closed, never merged) reuse of the SAME
    # branch name leaves that rejected PR's own commit as the branch's
    # current tip, with its own `head_sha` matching that tip exactly --
    # trusting it unconditionally as a merge boundary would read "zero
    # extra commits" and let cleanup force-delete a genuinely unmerged
    # commit. Only a reused entry that is ITSELF independently confirmed
    # merged may be trusted as a boundary.
    env = refspec_worktree
    record, repo = _record_and_repo(env)
    record.pr.state = "merged"
    record.pr.head_sha = _git("rev-parse", "HEAD", cwd=env.clone)

    reused_branch = "legacy-reused-branch-rejected-tail"
    _git("branch", reused_branch, cwd=env.clone)
    _git("checkout", reused_branch, cwd=env.clone)

    # First reuse ("PR #1"): merged via squash.
    _commit(env.clone, "merged_reuse.txt", "the part that merged\n")
    first_head_sha = _git("rev-parse", "HEAD", cwd=env.clone)
    _git("checkout", "master", cwd=env.seed)
    (env.seed / "merged_reuse.txt").write_text("the part that merged\n")
    _git("add", "-A", cwd=env.seed)
    _git("commit", "-m", "squashed merged reuse", cwd=env.seed)
    _git("push", "origin", "master", cwd=env.seed)

    # Second reuse ("PR #2"): the SAME branch name, rejected -- never merged,
    # never pushed anywhere. Its own `head_sha` is the branch's current tip.
    _git("checkout", reused_branch, cwd=env.clone)
    _commit(env.clone, "rejected_reuse.txt", "never merged, never landed\n")
    second_head_sha = _git("rev-parse", "HEAD", cwd=env.clone)

    _git("checkout", f"worktree/{env.worktree_id}", cwd=env.clone)
    _git("fetch", "origin", cwd=env.clone)

    record.prs = [
        SimpleNamespace(branch=reused_branch, head_sha=first_head_sha, state="merged"),
        SimpleNamespace(branch=reused_branch, head_sha=second_head_sha, state="closed"),
    ]

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


def test_precondition_blocks_closed_pr_with_unmerged_commits_on_other_tracked_branch(
    refspec_worktree,
):
    # #4400 round 15: `assert_no_live_pr` permits a terminal CLOSED
    # (confirmed-NOT-merged, never "live") PR -- but cleanup still
    # force-deletes every tracked PR's own feature branch regardless of
    # merge outcome. A missing head_sha's fast path must not blanket-trust
    # a confirmed-unmerged status without checking whether some OTHER
    # tracked PR branch still carries commits that never reached upstream.
    env = refspec_worktree
    _land_on_master(env, squash=False)  # worktree/<id> itself matches upstream
    other_branch = "pr/closed-rejected-fix"
    _git("branch", other_branch, cwd=env.clone)
    _git("checkout", other_branch, cwd=env.clone)
    _commit(env.clone, "rejected_work.txt", "never reached upstream\n")
    _git("checkout", f"worktree/{env.worktree_id}", cwd=env.clone)
    record, repo = _record_and_repo(env)
    record.pr.state = "closed"
    record.pr.head_sha = ""
    record.prs = [SimpleNamespace(branch=other_branch)]

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None


