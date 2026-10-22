"""Fresh graph evidence gates creation-history reads without weakening resets."""

from types import SimpleNamespace

import pytest
import test_finalize_landing as landing_support

from agent_worktrees import finalize_open_pr_gate, finalize_ref, git_ops

_landing_seed = landing_support._landing_seed
_git = landing_support._git


@pytest.fixture
def env(tmp_path, _landing_seed):
    return landing_support._copy_landing_env(_landing_seed, tmp_path)


@pytest.mark.parametrize("feature", ["", "worktree/task"])
def test_a_tracked_ref_cannot_have_commits_beyond_the_identical_current_ref(monkeypatch, feature):
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: pytest.fail("identical refs need no Git probe"),
    )
    assert not finalize_open_pr_gate._tracked_branch_ahead(
        feature, "task", "worktree/task", cwd="unused",
    )


def test_a_different_tracked_branch_still_blocks_unpublished_work(env):
    landing_support._commit(env.clone, "a.txt", "unpublished\n")
    _git("checkout", "-q", "-b", "current", "origin/main", cwd=env.clone)
    assert finalize_open_pr_gate._tracked_branch_ahead(
        "", env.worktree_id, "current", cwd=str(env.clone),
    )


def test_zero_extra_commits_use_real_graph_evidence_without_creation_history(env, monkeypatch):
    head = _git("rev-parse", "HEAD", cwd=env.clone)
    monkeypatch.setattr(
        finalize_ref, "creation_point",
        lambda *args, **kwargs: pytest.fail("the real graph already proves no extra commits"),
    )
    assert finalize_open_pr_gate._extra_commit_count(
        f"worktree/{env.worktree_id}", head, "origin/main", cwd=str(env.clone),
    ) == 0
    _git("checkout", "-q", "-b", "current", "origin/main", cwd=env.clone)
    assert not finalize_open_pr_gate._tracked_branch_ahead(
        "", env.worktree_id, "current", cwd=str(env.clone),
    )


@pytest.mark.parametrize(
    "returncode,stdout",
    [(1, ""), (0, ""), (0, "unknown"), (0, "-1"), (0, "1\n2"), (0, "\u00b2")],
)
def test_unreadable_extra_commit_counts_never_authorize_finalization(
    monkeypatch, returncode, stdout,
):
    monkeypatch.setattr(git_ops, "ref_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        git_ops, "git",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    assert finalize_open_pr_gate._extra_commit_count(
        "tracked", "published", "upstream", cwd="unused",
    ) is None
    assert finalize_open_pr_gate._tracked_branch_ahead(
        "", "task", "other", cwd="unused",
    )


@pytest.mark.parametrize("later", [[], ["later\tcommit: new work"]])
def test_creation_point_without_a_later_reset_needs_no_source_tip_reread(monkeypatch, later):
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: pytest.fail("there is no reset to validate"),
    )
    assert finalize_ref._later_resets_to_source(
        [*later, "created\tbranch: Created from origin/main"], "created", "origin/main", (), "unused",
    ) == "created"


def test_a_reset_after_earlier_local_work_still_reads_qualified_tips(monkeypatch):
    calls = []

    def evidence(*args, **kwargs):
        calls.append(args)
        if args[0] == "rev-parse":
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="published\n")

    monkeypatch.setattr(git_ops, "git", evidence)
    assert finalize_ref._later_resets_to_source(
        [
            "published\treset: moving to origin/main",
            "local\tcommit: earlier work",
            "created\tbranch: Created from origin/dev",
        ],
        "created", "origin/dev", ("refs/remotes/origin/main",), "unused",
    ) == "published"
    assert any(args[:4] == ("reflog", "show", "--format=%H", "refs/remotes/origin/main")
               for args in calls)


@pytest.mark.parametrize("stdout", ["", "unknown", "-1", "1\n2", "\u00b2"])
def test_bad_counts_after_creation_exclusion_remain_unknown(monkeypatch, stdout):
    monkeypatch.setattr(git_ops, "ref_exists", lambda *args, **kwargs: True)
    calls = []

    def count(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="1" if len(calls) == 1 else stdout)

    def created(ref, cwd, *, published_bases):
        assert published_bases == ("refs/remotes/origin/main", "refs/remotes/origin/dev")
        return "created"

    monkeypatch.setattr(git_ops, "git", count)
    monkeypatch.setattr(finalize_ref, "creation_point", created)
    assert finalize_open_pr_gate._extra_commit_count(
        "tracked", "head", ("refs/remotes/origin/main", "refs/remotes/origin/dev"), cwd="unused",
    ) is None
    assert calls == [
        ("rev-list", "--count", "tracked", "^head", "^refs/remotes/origin/main", "^refs/remotes/origin/dev"),
        ("rev-list", "--count", "tracked", "^head", "^refs/remotes/origin/main", "^refs/remotes/origin/dev", "^created"),
    ]


@pytest.mark.parametrize(
    "width,kind",
    [(40, "valid"), (64, "valid"), (40, "failure"), (40, "short"), (40, "missing-tab"), (40, "shadow")],
)
def test_creation_tip_batch_requires_full_ids_and_no_local_shadow(monkeypatch, width, kind):
    sha = "a" * width
    row = f"refs/remotes/origin/main\t{sha}\n"
    if kind == "short":
        row = "refs/remotes/origin/main\tabc\n"
    elif kind == "missing-tab":
        row = sha + "\n"
    elif kind == "shadow":
        row += f"refs/heads/origin/main\t{sha}\n"
    calls = []

    def evidence(*args, **kwargs):
        calls.append(args)
        if args[0] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="refs/heads/task\n")
        if args[0] == "remote":
            return SimpleNamespace(returncode=0, stdout="origin\n")
        if args[0] == "reflog":
            assert args[3] == "refs/heads/task"
            return SimpleNamespace(returncode=0, stdout=f"{sha}\tbranch: Created from origin/main\n")
        assert args == (
            "for-each-ref", "--format=%(refname)%09%(objectname)",
            "refs/heads/origin/main", "refs/remotes/origin/",
        )
        return SimpleNamespace(returncode=1 if kind == "failure" else 0, stdout=row)

    monkeypatch.setattr(git_ops, "git", evidence)
    assert finalize_ref.creation_point("task", "unused") == (sha if kind == "valid" else "")
    assert sum(args[0] == "for-each-ref" for args in calls) == 1
