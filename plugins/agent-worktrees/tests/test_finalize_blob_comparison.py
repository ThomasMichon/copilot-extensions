"""Real object evidence for finalize's batched changed-path comparison."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import test_finalize_landing as landing_support

from agent_worktrees import finalize_open_pr_gate, finalize_ref, git_ops

_landing_seed = landing_support._landing_seed
_git = landing_support._git


@pytest.fixture
def env(tmp_path, _landing_seed):
    return landing_support._copy_landing_env(_landing_seed, tmp_path)


def _change(repo: Path, operation: str) -> None:
    path = repo / "a.txt"
    if operation == "delete":
        path.unlink()
    elif operation == "directory":
        path.unlink()
        path.mkdir()
        (path / "child.txt").write_text("new content\n")
    else:
        path.write_text(operation + "\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", operation, cwd=repo)


@pytest.mark.parametrize(
    "branch_change,upstream_change,expected",
    [
        ("same", "same", True),
        ("mine", "different", False),
        ("delete", "delete", True),
        ("delete", "different", False),
        ("directory", "directory", True),
        ("directory", "different", False),
        ("different", "directory", False),
    ],
)
def test_changed_objects_cover_files_deletions_and_directory_replacements(
    env, branch_change, upstream_change, expected,
):
    branch = f"worktree/{env.worktree_id}"
    _change(env.clone, branch_change)
    _git("checkout", "-q", "-b", "upstream", "origin/main", cwd=env.clone)
    _change(env.clone, upstream_change)
    refs = _git("show-ref", cwd=env.clone)

    assert finalize_ref._blobs_on_upstream(branch, "upstream", str(env.clone)) is expected
    assert _git("show-ref", cwd=env.clone) == refs


@pytest.mark.parametrize("different", [False, True])
def test_many_literal_changed_paths_use_a_bounded_number_of_real_git_calls(
    env, monkeypatch, different,
):
    branch = f"worktree/{env.worktree_id}"
    paths = [f"file {index} [literal] $.txt" for index in range(16)]
    for path in paths:
        (env.clone / path).write_text("branch\n")
    _git("add", "-A", cwd=env.clone)
    _git("commit", "-q", "-m", "branch changes", cwd=env.clone)
    _git("checkout", "-q", "-b", "upstream", "origin/main", cwd=env.clone)
    for path in paths:
        (env.clone / path).write_text("branch\n")
    if different:
        (env.clone / paths[-1]).write_text("unpublished branch content differs\n")
    (env.clone / "upstream-only.txt").write_text("unrelated\n")
    _git("add", "-A", cwd=env.clone)
    _git("commit", "-q", "-m", "upstream changes", cwd=env.clone)
    original = git_ops.git
    calls = []

    def measured(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", measured)
    assert finalize_ref._blobs_on_upstream(branch, "upstream", str(env.clone)) is not different
    assert len(calls) == 3
    assert [args[0] for args in calls] == ["merge-base", "diff", "ls-tree"]


@pytest.mark.parametrize("failed_command", ["diff", "ls-tree"])
def test_failed_object_evidence_is_not_reported_as_landed(
    env, monkeypatch, capsys, failed_command,
):
    branch = f"worktree/{env.worktree_id}"
    _change(env.clone, "unpublished")
    original = git_ops.git

    def fail(*args, **kwargs):
        if args[0] == failed_command:
            return SimpleNamespace(returncode=1, stdout="", stderr="object evidence unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", fail)
    assert not finalize_ref._blobs_on_upstream(branch, "origin/main", str(env.clone))
    assert "Cannot verify changed files on upstream" in capsys.readouterr().out


@pytest.mark.parametrize("feature", ["", "worktree/task"])
def test_a_tracked_ref_cannot_have_commits_beyond_the_identical_current_ref(monkeypatch, feature):
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: pytest.fail("identical refs need no Git probe"),
    )
    assert not finalize_open_pr_gate._tracked_branch_ahead(
        feature, "task", "worktree/task", cwd="unused",
    )


def test_a_different_tracked_branch_still_blocks_unpublished_work(env):
    _change(env.clone, "unpublished")
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


@pytest.mark.parametrize("returncode,stdout", [(1, ""), (0, ""), (0, "unknown")])
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


def test_landing_compares_object_content_without_turning_modes_into_unpublished_work(env):
    _git("update-index", "--chmod=+x", "a.txt", cwd=env.clone)
    _git("commit", "-q", "-m", "mode-only change", cwd=env.clone)
    assert finalize_ref._blobs_on_upstream(
        f"worktree/{env.worktree_id}", "origin/main", str(env.clone),
    )


@pytest.mark.parametrize("later", [[], ["later\tcommit: new work"]])
def test_creation_point_without_a_later_reset_needs_no_source_tip_reread(monkeypatch, later):
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: pytest.fail("there is no reset to validate"),
    )
    assert finalize_ref._later_resets_to_source(
        [*later, "created\tbranch: Created from origin/main"], "created", "origin/main", "unused",
    ) == "created"
