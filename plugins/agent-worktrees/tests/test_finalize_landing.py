"""Landing detection across a PR's bases (``finalize_landing`` / ``finalize_ref``).

Real temporary repositories, one per case:

- a multi-commit branch squash-merged, after which upstream edits the same file
  (neither ``git cherry`` on the branch's own commits nor the blob comparison
  can see it; the whole-branch patch can);
- a PR merged into ``dev`` while the configured default branch ``main`` is
  behind it (only the PR's own base, read from the provider, holds the work);
- a worktree branch still at the commit it was created from, after the
  remote rewrote that history (nothing on it is the worktree's own work).
"""

from __future__ import annotations

import subprocess
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import finalize, finalize_landing, finalize_ref


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout.strip()


def _identity(repo: Path) -> None:
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)


def _commit(repo: Path, name: str, content: str, message: str = "") -> str:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", message or f"edit {name}", cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def env(tmp_path: Path) -> SimpleNamespace:
    """``origin.git`` with ``main`` and ``dev``; ``seed`` publishes upstream
    changes; ``clone`` is the worktree checkout on ``worktree/<id>``."""
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed), cwd=tmp_path)
    _identity(seed)
    _commit(seed, "a.txt", "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n")
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "-q", "origin", "main", "main:dev", cwd=seed)
    clone = tmp_path / "clone"
    _git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    _identity(clone)
    worktree_id = "wt-landing"
    _git("checkout", "-q", "-b", f"worktree/{worktree_id}", "origin/main", cwd=clone)
    return SimpleNamespace(tmp_path=tmp_path, origin=origin, seed=seed, clone=clone,
                           worktree_id=worktree_id, slug="pr/landing")


def _two_commit_change(clone: Path) -> str:
    _commit(clone, "a.txt", "ONE\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n", "first")
    return _commit(clone, "a.txt", "ONE\ntwo\nthree\nFOUR\nfive\nsix\nseven\neight\n", "second")


def _squash_onto(seed: Path, branch: str, content: str) -> None:
    _git("checkout", "-q", branch, cwd=seed)
    _git("pull", "-q", "--ff-only", "origin", branch, cwd=seed)
    _commit(seed, "a.txt", content, "the squashed PR")
    _git("push", "-q", "origin", branch, cwd=seed)


SQUASHED = "ONE\ntwo\nthree\nFOUR\nfive\nsix\nseven\neight\n"


def _record(env, *, head_sha: str = "", state: str = "", number=None):
    pr = SimpleNamespace(branch=env.slug, state=state, head_sha=head_sha, number=number,
                         repo="owner/repo" if number else "", provider="github", url="")
    return SimpleNamespace(worktree_id=env.worktree_id, pr=pr, prs=[pr], branch="", repo="owner/repo")


def _repo(default: str = "main"):
    return SimpleNamespace(remote="origin", default_branch=default,
                           pr=SimpleNamespace(enabled=True, provider="github", api_base=""))


@pytest.fixture
def provider(monkeypatch):
    """A provider reporting the tracked PR merged into ``base_ref``."""
    from agent_worktrees import providers
    from agent_worktrees.providers.base import PullResult

    state = SimpleNamespace(base_ref="", head_sha="", calls=0)

    class Provider:
        def get_pull(self, repo, number, **kwargs):
            state.calls += 1
            return PullResult(state="merged", merged=True, head_sha=state.head_sha,
                              base_ref=state.base_ref)

        def observe_head(self, repo, number, **kwargs):
            return PullResult(head_sha=state.head_sha)

    monkeypatch.setattr(providers, "get_provider", lambda _name: Provider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_a, **_k: None)
    return state


# -- the whole-branch squash probe ---------------------------------------------------


def test_a_multi_commit_squash_later_edited_upstream_is_landed(env):
    """Each branch commit's own patch isn't upstream, and upstream later changed the
    file again, so cherry and the blob comparison both miss it: the branch's whole
    change as one patch is the squash commit."""
    tip = _two_commit_change(env.clone)
    _squash_onto(env.seed, "main", SQUASHED)
    _commit(env.seed, "a.txt", SQUASHED.replace("eight", "EIGHT"), "a later upstream edit")
    _git("push", "-q", "origin", "main", cwd=env.seed)
    _git("fetch", "-q", "origin", cwd=env.clone)

    result = finalize_ref.landing(tip, "origin/main", str(env.clone), explain=True)

    assert (result.ancestor, result.cherry, result.blobs) == (False, False, False)
    assert result.squash is True and result.landed
    assert finalize_ref.is_content_on_upstream(tip, "origin/main", str(env.clone))
    ok, err = finalize._pr_finalize_precondition(_record(env), _repo(), str(env.clone), str(env.clone))
    assert (ok, err) == (True, None)
    assert not _git("for-each-ref", "refs/heads/pr", cwd=env.clone)  # the head branch is gone


def test_the_probe_moves_no_ref(env):
    tip = _two_commit_change(env.clone)
    _squash_onto(env.seed, "main", SQUASHED)
    _git("fetch", "-q", "origin", cwd=env.clone)
    refs = _git("for-each-ref", "--format=%(refname) %(objectname)", cwd=env.clone)
    assert finalize_ref.squash_landed(tip, "origin/main", str(env.clone)) is True
    assert _git("for-each-ref", "--format=%(refname) %(objectname)", cwd=env.clone) == refs


def test_a_different_change_is_not_squash_landed(env):
    tip = _two_commit_change(env.clone)
    _squash_onto(env.seed, "main", SQUASHED.replace("FOUR", "four (another fix)"))
    _git("fetch", "-q", "origin", cwd=env.clone)
    result = finalize_ref.landing(tip, "origin/main", str(env.clone), explain=True)
    assert result.squash is False and not result.landed


def test_an_unreadable_upstream_is_never_landed(env):
    tip = _two_commit_change(env.clone)
    assert finalize_ref.squash_landed(tip, "origin/no-such-branch", str(env.clone)) is None
    assert not finalize_ref.is_content_on_upstream(tip, "origin/no-such-branch", str(env.clone))


# -- the PR's own base ---------------------------------------------------------------


def _merge_into_dev_and_pull_forward(env) -> str:
    """The PR squash-merges into dev (main stays behind); the worktree then pulls
    forward onto dev, so its branch now carries dev's commits, not main's."""
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _commit(env.seed, "b.txt", "another PR on dev\n", "another dev PR")
    _git("push", "-q", "origin", "dev", cwd=env.seed)
    _git("fetch", "-q", "origin", cwd=env.clone)
    _git("reset", "-q", "--hard", "origin/dev", cwd=env.clone)
    return head


def test_a_pr_merged_into_dev_while_main_is_behind_is_landed_on_its_base(env, provider):
    head = _merge_into_dev_and_pull_forward(env)
    provider.head_sha, provider.base_ref = head, "dev"
    record = _record(env, head_sha=head, state="merged", number=7)

    ok, err = finalize._pr_finalize_precondition(record, _repo(), str(env.clone), str(env.clone))

    assert (ok, err) == (True, None)


def test_without_the_pr_base_a_dev_merge_is_refused_with_its_evidence(env, provider):
    """The provider doesn't say where the PR went: only the default branch is
    tested, and the refusal names what was checked."""
    head = _merge_into_dev_and_pull_forward(env)
    provider.head_sha, provider.base_ref = head, ""
    record = _record(env, head_sha=head, state="merged", number=7)

    ok, err = finalize._pr_finalize_precondition(record, _repo(), str(env.clone), str(env.clone))

    assert ok is False
    assert "further commits" in err
    assert f"Landing checks: worktree/{env.worktree_id} on origin/main: ancestor no" in err
    assert "origin/dev" not in err
    assert provider.calls == 1  # the base lookup and the merged-head refresh share one read


def test_the_pr_base_is_asked_only_when_the_default_branch_lacks_the_work(env, provider):
    tip = _two_commit_change(env.clone)
    _git("push", "-q", "origin", f"worktree/{env.worktree_id}:main", cwd=env.clone)
    _git("fetch", "-q", "origin", cwd=env.clone)
    record = _record(env, head_sha=tip, state="open", number=7)
    ok, _err = finalize._pr_finalize_precondition(record, _repo(), str(env.clone), str(env.clone))
    assert ok is True and provider.calls == 0


def test_explain_reports_every_base_and_check(env, provider):
    head = _merge_into_dev_and_pull_forward(env)
    provider.head_sha, provider.base_ref = head, "dev"
    report = finalize_landing.explain(
        _record(env, head_sha=head, state="merged", number=7), _repo(), str(env.clone), str(env.clone))
    assert report["bases"] == ["origin/main", "origin/dev"]
    assert [c["landed"] for c in report["checks"]] == [False, True]
    assert report["checks"][1]["ancestor"] is True
    assert report["landed"] is True and report["content_ref"] == f"worktree/{env.worktree_id}"


# -- the creation point ----------------------------------------------------------------


def _rewrite_main(env) -> None:
    """The remote rewrites main's history (a re-rooted release branch)."""
    _git("checkout", "-q", "--orphan", "rewritten", cwd=env.seed)
    _commit(env.seed, "a.txt", "rewritten history\n", "re-rooted")
    _git("push", "-q", "-f", "origin", "rewritten:main", cwd=env.seed)


def test_a_branch_still_at_its_creation_point_carries_no_work(env, provider):
    """The worktree branch was created from origin/main and never moved; main was
    then rewritten. The work was done on another branch, squash-merged into dev:
    the creation point is published history, never the worktree's own work."""
    _git("checkout", "-q", "-b", env.slug, "origin/dev", cwd=env.clone)
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _rewrite_main(env)
    _git("fetch", "-q", "-f", "origin", cwd=env.clone)
    created = finalize_ref.creation_point(f"worktree/{env.worktree_id}", str(env.clone))
    assert created == _git("rev-parse", f"worktree/{env.worktree_id}", cwd=env.clone)
    provider.head_sha, provider.base_ref = head, "dev"
    record = _record(env, number=7, state="merged")

    ok, err = finalize._pr_finalize_precondition(record, _repo(), str(env.clone), str(env.clone))

    assert (ok, err) == (True, None)


def _sync_worktree_branch_to_a_newer_main(env) -> str:
    """origin/main advances; the worktree branch is then reset to that new tip (a
    sync), with no commit of its own. Returns the new tip."""
    _git("checkout", "-q", "main", cwd=env.seed)
    tip = _commit(env.seed, "c.txt", "a release on main\n", "release: promote dev to main")
    _git("push", "-q", "origin", "main", cwd=env.seed)
    _git("fetch", "-q", "origin", cwd=env.clone)
    _git("branch", "-f", f"worktree/{env.worktree_id}", "origin/main", cwd=env.clone)
    return tip


def test_a_branch_only_synced_to_newer_remote_tips_carries_no_work(env, provider):
    """Like the creation point, a sync that only reset the branch to a later tip of
    its source (named in that source's own reflog) is published history; main is
    rewritten afterwards, so only the reflog still proves it."""
    _git("checkout", "-q", "-b", env.slug, "origin/dev", cwd=env.clone)
    tip = _sync_worktree_branch_to_a_newer_main(env)
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _rewrite_main(env)
    _git("fetch", "-q", "-f", "origin", cwd=env.clone)
    assert finalize_ref.creation_point(f"worktree/{env.worktree_id}", str(env.clone)) == tip
    provider.head_sha, provider.base_ref = head, "dev"

    ok, err = finalize._pr_finalize_precondition(
        _record(env, number=7, state="merged"), _repo(), str(env.clone), str(env.clone))

    assert (ok, err) == (True, None)


def test_a_commit_after_a_sync_still_counts(env, provider):
    _git("checkout", "-q", "--detach", cwd=env.clone)
    tip = _sync_worktree_branch_to_a_newer_main(env)
    _git("checkout", "-q", f"worktree/{env.worktree_id}", cwd=env.clone)
    _commit(env.clone, "mine.txt", "real local work\n", "never published")
    _git("checkout", "-q", "-b", env.slug, "origin/dev", cwd=env.clone)
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _rewrite_main(env)
    _git("fetch", "-q", "-f", "origin", cwd=env.clone)
    assert finalize_ref.creation_point(f"worktree/{env.worktree_id}", str(env.clone)) == tip
    provider.head_sha, provider.base_ref = head, "dev"

    ok, err = finalize._pr_finalize_precondition(
        _record(env, number=7, state="merged"), _repo(), str(env.clone), str(env.clone))

    assert ok is False and "further commits" in err


def test_work_past_the_creation_point_still_counts(env, provider):
    _commit(env.clone, "mine.txt", "real local work\n", "never published")
    _git("checkout", "-q", "-b", env.slug, "origin/dev", cwd=env.clone)
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _rewrite_main(env)
    _git("fetch", "-q", "-f", "origin", cwd=env.clone)
    provider.head_sha, provider.base_ref = head, "dev"

    ok, err = finalize._pr_finalize_precondition(
        _record(env, number=7, state="merged"), _repo(), str(env.clone), str(env.clone))

    assert ok is False and "further commits" in err


def test_a_branch_created_from_a_local_branch_has_no_creation_point(env):
    _git("branch", "local-base", cwd=env.clone)
    _git("branch", "from-local", "local-base", cwd=env.clone)
    assert finalize_ref.creation_point("from-local", str(env.clone)) == ""
    _git("reflog", "expire", "--expire=now", "--all", cwd=env.clone)
    assert finalize_ref.creation_point(f"worktree/{env.worktree_id}", str(env.clone)) == ""
    assert finalize_ref.creation_point("HEAD~0", str(env.clone)) == ""


def test_a_local_branch_named_like_a_remote_one_is_no_creation_point(env):
    """'Created from origin/local-only' names a local branch holding unpublished work:
    the remote-tracking ref never held that commit, so it is no creation point --
    while the local branch exists, and after it is deleted."""
    _git("checkout", "-q", "-b", "origin/local-only", cwd=env.clone)
    _commit(env.clone, "unpublished.txt", "never pushed\n")
    _git("checkout", "-q", "-b", "wt-2", "origin/local-only", cwd=env.clone)
    assert "Created from origin/local-only" in _git("reflog", "show", "wt-2", cwd=env.clone)
    assert finalize_ref.creation_point("wt-2", str(env.clone)) == ""
    _git("branch", "-D", "origin/local-only", cwd=env.clone)
    assert finalize_ref.creation_point("wt-2", str(env.clone)) == ""


def test_a_local_branch_shadowing_the_pr_base_never_certifies_its_commits(env, provider):
    """A local branch named 'origin/dev' (which an abbreviated 'origin/dev' resolves to
    first) holds a commit made after the merge that was never published: the PR's base
    is checked as the remote-tracking ref, so the commit still blocks."""
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _git("fetch", "-q", "origin", cwd=env.clone)
    _commit(env.clone, "after.txt", "made after the merge, never pushed\n", "post-merge work")
    _git("branch", "origin/dev", "HEAD", cwd=env.clone)
    provider.head_sha, provider.base_ref = head, "dev"

    ok, err = finalize._pr_finalize_precondition(
        _record(env, head_sha=head, state="merged", number=7), _repo(), str(env.clone), str(env.clone))

    assert ok is False and "further commits" in err
    assert "on origin/dev: ancestor no" in err


# -- pr-status pull-forward advice -------------------------------------------------------


def test_pull_forward_targets_the_base_a_pr_merged_into(env, provider):
    from agent_worktrees import pr_pull_forward
    from agent_worktrees.tracking import PRRecord

    head = _merge_into_dev_and_pull_forward(env)
    _git("reset", "-q", "--hard", head, cwd=env.clone)
    provider.head_sha, provider.base_ref = head, "dev"
    record = SimpleNamespace(worktree_path=str(env.clone), repo="")
    active = PRRecord(state="merged", number=7, repo="owner/repo", provider="github")
    config = SimpleNamespace(default_repo=_repo())

    rec = pr_pull_forward.pull_forward_recommendation(record, active, config, live=True)
    assert rec["pull_forward_argv"] == ["git", "-C", str(env.clone), "rebase", "--onto",
                                        "refs/remotes/origin/dev", head]
    assert rec["pull_forward_command"] == " ".join(shlex.quote(a) for a in rec["pull_forward_argv"])
    assert rec["pull_forward_base"] == "origin/dev" and rec["behind"] == 2
    assert "not the configured default branch origin/main" in rec["next_action"]

    provider.base_ref = "dev;id"  # a legal branch name that is shell syntax
    _git("update-ref", "refs/remotes/origin/dev;id", "refs/remotes/origin/dev", cwd=env.clone)
    rec = pr_pull_forward.pull_forward_recommendation(record, active, config, live=True)
    assert rec["pull_forward_command"] == ""
    assert rec["pull_forward_argv"][-2] == "refs/remotes/origin/dev;id"
    assert "dev;id" not in rec["next_action"] and "pull_forward_argv" in rec["next_action"]

    provider.base_ref = "main"
    rec = pr_pull_forward.pull_forward_recommendation(record, active, config, live=True)
    assert rec is None or rec["pull_forward_command"] == "agent-worktrees git sync"
    calls = provider.calls
    assert "pull_forward_base" not in (pr_pull_forward.pull_forward_recommendation(
        record, active, config) or {})
    assert provider.calls == calls  # not live: the provider isn't asked


def test_the_pull_forward_advice_runs_cleanly_past_a_squash_that_a_plain_rebase_conflicts_on(env, provider):
    """Two PR commits edit the same line; the PR squash-merges into dev, which then
    edits that line again; one commit is made locally after the merge. Replaying the
    PR's own commits would conflict although all of them landed: the advice replays
    only the post-merge commit, and actually running it succeeds."""
    from agent_worktrees import pr_pull_forward
    from agent_worktrees.tracking import PRRecord

    first = _commit(env.clone, "a.txt", SQUASHED.replace("ONE", "one!"), "first")
    head = _commit(env.clone, "a.txt", SQUASHED.replace("ONE", "ONE!"), "second")
    assert first != head
    _squash_onto(env.seed, "dev", SQUASHED.replace("ONE", "ONE!"))
    _commit(env.seed, "a.txt", SQUASHED.replace("ONE", "ONE!!"), "a later dev edit to the same line")
    _git("push", "-q", "origin", "dev", cwd=env.seed)
    _git("fetch", "-q", "origin", cwd=env.clone)
    _commit(env.clone, "post.txt", "made after the merge\n", "post-merge work")
    provider.head_sha, provider.base_ref = head, "dev"
    record = SimpleNamespace(worktree_path=str(env.clone), repo="")
    active = PRRecord(state="merged", number=7, repo="owner/repo", provider="github")

    rec = pr_pull_forward.pull_forward_recommendation(
        record, active, SimpleNamespace(default_repo=_repo()), live=True)
    done = subprocess.run(rec["pull_forward_argv"], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert (env.clone / "post.txt").exists()
    assert (env.clone / "a.txt").read_text().startswith("ONE!!")
    assert subprocess.run(["git", "merge-base", "--is-ancestor", "origin/dev", "HEAD"],
                          cwd=str(env.clone)).returncode == 0


def test_no_command_is_generated_without_a_verified_merge_boundary(env, provider):
    from agent_worktrees import pr_pull_forward
    from agent_worktrees.tracking import PRRecord

    _merge_into_dev_and_pull_forward(env)
    provider.head_sha, provider.base_ref = "f" * 40, "dev"  # not in this checkout's history
    _git("reset", "-q", "--hard", "HEAD~1", cwd=env.clone)
    rec = pr_pull_forward.pull_forward_recommendation(
        SimpleNamespace(worktree_path=str(env.clone), repo=""),
        PRRecord(state="merged", number=7, repo="owner/repo", provider="github"),
        SimpleNamespace(default_repo=_repo()), live=True)
    assert rec["pull_forward_argv"] == [] and rec["pull_forward_command"] == ""
    assert "none is generated" in rec["next_action"]


def test_explain_with_an_empty_worktree_path_reads_the_anchor(env, provider):
    """A tracking record can carry an empty path: the report comes from the anchor
    instead of failing on a git call with an empty working directory."""
    report = finalize_landing.explain(_record(env), _repo(), "", str(env.clone))
    assert report["content_ref"] and report["bases"][0] == "origin/main"


def test_a_pr_tracked_without_its_branch_finalizes_where_explain_says_it_landed(
        env, provider, monkeypatch):
    """The work was committed on a hand-made branch (the tracked ``worktree/<id>``
    never moved) and the PR recorded by URL and number only, so its ``branch`` is
    empty; it squash-merged into dev while main is behind. ``--explain-landing``
    reads it as a PR and finds it landed on dev; finalize must decide it the same
    way, not as a direct-push worktree checked against main alone."""
    from agent_worktrees import config as cfg
    from agent_worktrees import tracking

    tracking_d = env.tmp_path / "tracking"
    tracking_d.mkdir()
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tracking_d)
    _git("checkout", "-q", "-b", "pr/hand-made", cwd=env.clone)
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _git("checkout", "-q", "main", cwd=env.seed)
    _git("fetch", "-q", "origin", cwd=env.clone)
    provider.head_sha, provider.base_ref = head, "dev"

    pr = tracking.PRRecord(state="merged", number=7, repo="owner/repo", provider="github")
    record = tracking.WorktreeRecord(
        worktree_id=env.worktree_id, branch=f"worktree/{env.worktree_id}",
        worktree_path=str(env.clone), repo="repo", machine="test", platform="linux",
        started_at="2026-10-01T00:00:00", last_resumed_at="2026-10-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None, prs=[pr])
    tracking.save_record(record, tracking_d / f"{env.worktree_id}.yaml")
    repo_cfg = cfg.RepoConfig(anchor=str(env.seed), worktree_root=str(env.tmp_path),
                              default_branch="main", remote="origin",
                              pr=cfg.PRConfig(enabled=True, required=True, provider="github"))
    config = cfg.Config(srcroot=str(env.tmp_path), machine="test", platform="linux",
                        repo_name="repo", repos={"repo": repo_cfg})

    assert finalize_landing.tracks_pr(record, repo_cfg)
    report = finalize_landing.explain(record, repo_cfg, str(env.clone), str(env.seed))
    assert (report["content_ref"], report["landed"]) == ("pr/hand-made", True)

    assert finalize.validate_and_finalize(env.worktree_id, config) is True


def test_a_pr_is_tracked_by_its_branch_or_its_number_and_only_with_prs_enabled(env):
    repo = _repo()
    assert finalize_landing.tracks_pr(_record(env), repo)                    # branch only
    nobranch = _record(env, number=7)
    nobranch.pr.branch = ""
    assert finalize_landing.tracks_pr(nobranch, repo)                        # number only
    nobranch.pr.number = None
    assert not finalize_landing.tracks_pr(nobranch, repo)                    # neither
    assert not finalize_landing.tracks_pr(SimpleNamespace(pr=None), repo)    # no PR
    repo.pr.enabled = False
    assert not finalize_landing.tracks_pr(_record(env), repo)                # PRs off


def test_branches_pulled_forward_onto_different_bases_are_all_published(env, provider):
    """An older merged PR's branch was pulled forward onto main, the current checkout
    onto dev; each carries a commit only the other base lacks. Every commit is on some
    published base, so the work is landed."""
    _git("checkout", "-q", "-b", "pr/old", "origin/main", cwd=env.clone)
    old_head = _commit(env.clone, "old.txt", "the older PR\n", "older PR")
    _git("checkout", "-q", "main", cwd=env.seed)
    _commit(env.seed, "old.txt", "the older PR\n", "the older PR, squashed into main")
    _git("push", "-q", "origin", "main", cwd=env.seed)
    _git("checkout", "-q", f"worktree/{env.worktree_id}", cwd=env.clone)
    head = _two_commit_change(env.clone)
    _squash_onto(env.seed, "dev", SQUASHED)
    _git("fetch", "-q", "origin", cwd=env.clone)
    _git("branch", "-f", "pr/old", "origin/main", cwd=env.clone)       # pulled forward onto main
    _git("reset", "-q", "--hard", "origin/dev", cwd=env.clone)          # pulled forward onto dev
    provider.head_sha, provider.base_ref = head, "dev"
    old = SimpleNamespace(branch="pr/old", state="merged", head_sha=old_head, number=6,
                          repo="owner/repo", provider="github", url="", opened_at="2026-10-01")
    current = SimpleNamespace(branch=env.slug, state="merged", head_sha=head, number=7,
                              repo="owner/repo", provider="github", url="", opened_at="2026-10-05")
    record = SimpleNamespace(worktree_id=env.worktree_id, pr=current, prs=[old, current],
                             branch="", repo="owner/repo")

    ok, err = finalize._pr_finalize_precondition(record, _repo(), str(env.clone), str(env.clone))

    assert (ok, err) == (True, None)


def test_no_pull_forward_command_onto_a_stale_base_after_a_failed_fetch(env, provider, monkeypatch):
    """The fetch fails and this checkout's copy of dev predates the merge (it holds an
    unrelated commit instead): moving onto it would drop the PR's work, so no command."""
    from agent_worktrees import git_ops, pr_pull_forward
    from agent_worktrees.tracking import PRRecord

    head = _two_commit_change(env.clone)
    _git("checkout", "-q", "dev", cwd=env.seed)
    _commit(env.seed, "b.txt", "unrelated\n", "an unrelated dev commit")
    _git("push", "-q", "origin", "dev", cwd=env.seed)
    _git("fetch", "-q", "origin", cwd=env.clone)                         # dev has b.txt, not the PR
    _squash_onto(env.seed, "dev", SQUASHED)                              # merged later, never fetched

    def no_fetch(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(git_ops, "fetch", no_fetch)
    provider.head_sha, provider.base_ref = head, "dev"
    rec = pr_pull_forward.pull_forward_recommendation(
        SimpleNamespace(worktree_path=str(env.clone), repo=""),
        PRRecord(state="merged", number=7, repo="owner/repo", provider="github"),
        SimpleNamespace(default_repo=_repo()), live=True)
    assert rec["pull_forward_argv"] == [] and rec["pull_forward_command"] == ""
    assert "fetch" in rec["next_action"]
