"""Real object evidence for finalize's batched changed-path comparison."""

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import test_finalize_landing as landing_support
from agent_procutil import no_window_kwargs

from agent_worktrees import finalize_ref, git_ops

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
@pytest.mark.parametrize("malformed", [False, True])
def test_failed_object_evidence_is_not_reported_as_landed(
    env, monkeypatch, capsys, failed_command, malformed,
):
    branch = f"worktree/{env.worktree_id}"
    _change(env.clone, "unpublished")
    original = git_ops.git

    def fail(*args, **kwargs):
        if args[0] == failed_command:
            return SimpleNamespace(
                returncode=0 if malformed else 1,
                stdout="incomplete object evidence" if malformed else "",
                stderr="object evidence unavailable",
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", fail)
    assert not finalize_ref._blobs_on_upstream(branch, "origin/main", str(env.clone))
    assert "Cannot verify changed files on upstream" in capsys.readouterr().out


def test_landing_compares_object_content_without_turning_modes_into_unpublished_work(env):
    _git("update-index", "--chmod=+x", "a.txt", cwd=env.clone)
    _git("commit", "-q", "-m", "mode-only change", cwd=env.clone)
    assert finalize_ref._blobs_on_upstream(
        f"worktree/{env.worktree_id}", "origin/main", str(env.clone),
    )


@pytest.mark.parametrize("raw_path", [b"kept\rname.txt", b"kept\xffname.txt"])
def test_lossy_path_evidence_never_certifies_an_unpublished_deletion(env, capsys, raw_path):
    branch = f"worktree/{env.worktree_id}"
    original_tree = _git("rev-parse", "HEAD^{tree}", cwd=env.clone)
    blob = _git("rev-parse", "HEAD:a.txt", cwd=env.clone).encode("ascii")
    entries = b"100644 blob " + blob + b"\ta.txt\0"
    entries += b"100644 blob " + blob + b"\t" + raw_path + b"\0"
    # Object-only trees exercise POSIX-only names on every host, without checkout.
    tree = subprocess.run(
        ["git", "mktree", "-z"], input=entries, cwd=env.clone,
        capture_output=True, check=True, timeout=5, **no_window_kwargs(),
    ).stdout.decode("ascii").strip()
    base = _git("commit-tree", tree, "-p", "HEAD", "-m", "unusual path", cwd=env.clone)
    deleted = _git(
        "commit-tree", original_tree, "-p", base, "-m", "unpublished deletion", cwd=env.clone,
    )
    _git("update-ref", "refs/heads/upstream", base, cwd=env.clone)
    _git("update-ref", f"refs/heads/{branch}", deleted, cwd=env.clone)
    refs = _git("show-ref", cwd=env.clone)

    assert not finalize_ref._blobs_on_upstream(branch, "upstream", str(env.clone))
    assert "Cannot verify changed files on upstream" in capsys.readouterr().out
    assert _git("show-ref", cwd=env.clone) == refs


@pytest.mark.parametrize(
    "setting",
    ["GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"],
)
def test_inherited_pathspec_modes_never_certify_an_unpublished_deletion(
    env, monkeypatch, capsys, setting,
):
    branch = f"worktree/{env.worktree_id}"
    _change(env.clone, "delete")
    refs = _git("show-ref", cwd=env.clone)
    monkeypatch.setenv(setting, "1")

    assert not finalize_ref._blobs_on_upstream(branch, "origin/main", str(env.clone))
    assert "Cannot verify changed files on upstream" in capsys.readouterr().out
    assert _git("show-ref", cwd=env.clone) == refs
