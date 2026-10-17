"""Fresh, raw tree evidence for one source-owned replay decision."""

import subprocess

import pytest

from agent_worktrees import git_ops, pr_rebase


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_replay_trees_use_one_fresh_raw_probe(tmp_path, monkeypatch, object_format):
    def git(*args):
        return git_ops.git(*args, cwd=tmp_path).stdout.strip()

    git("init", "--template=", f"--object-format={object_format}")
    git("config", "user.name", "Developer")
    git("config", "user.email", "developer@example.com")
    commits, trees = [], []
    for text in ("original\n", "changed\n"):
        (tmp_path / "work.txt").write_text(text)
        git("add", "work.txt")
        git("commit", "-m", "source work")
        commits.append(git("rev-parse", "HEAD"))
        trees.append(git("rev-parse", "HEAD^{tree}"))
    git("notes", "add", "-m", "Annotation is not tree evidence", commits[0])
    git("config", "log.showSignature", "true")
    git("config", "color.ui", "always")
    git("replace", commits[0], commits[1])
    assert git("rev-parse", f"{commits[0]}^{{tree}}") == trees[1]
    foreign = tmp_path / "foreign.git"
    git("init", "--bare", "--template=", str(foreign))
    original = pr_rebase._git_bytes
    calls = []

    def observed(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(pr_rebase, "_git_bytes", observed)
    monkeypatch.setenv("GIT_DIR", str(foreign))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    assert pr_rebase._tree_ids((commits[0], commits[1], commits[0]), str(tmp_path)) == dict(
        zip(commits, trees),
    )
    assert len(calls) == 1
    assert calls[0][0] == (
        "log", "--stdin", "--no-walk=unsorted", "--format=%H %T",
        "--no-notes", "--no-show-signature", "--no-color",
    )
    assert calls[0][1]["stdin"] == f"{commits[0]}\n{commits[1]}\n".encode("ascii")
    assert pr_rebase._tree_ids((commits[1],), str(tmp_path)) == {commits[1]: trees[1]}
    assert len(calls) == 2
    assert calls[1][1]["stdin"] == f"{commits[1]}\n".encode("ascii")


@pytest.mark.parametrize("output", [
    b"",
    b"a" * 40 + b"\n",
    b"a" * 40 + b" tree\n",
    b"a" * 40 + b" " + b"b" * 39 + b"\n",
    b"a" * 40 + b" " + b"b" * 41 + b"\n",
    b"a" * 40 + b" " + b"b" * 40 + b" extra\n",
    b"a" * 40 + b" " + b"b" * 40,
    b"a" * 40 + b"\t" + b"b" * 40 + b"\n",
    b"a" * 40 + b"  " + b"b" * 40 + b"\n",
    b"c" * 40 + b" " + b"b" * 40 + b"\n",
    (b"a" * 40 + b" " + b"b" * 40 + b"\n") * 2,
])
def test_replay_trees_refuse_missing_malformed_or_substituted_rows(monkeypatch, output):
    monkeypatch.setattr(pr_rebase, "_git_bytes", lambda *args, **kwargs: output)
    assert pr_rebase._tree_ids(("a" * 40,), ".") is None


@pytest.mark.parametrize("heads", [(), ("HEAD",), ("a" * 41,), (None,), (3,)])
def test_replay_trees_require_pinned_complete_commit_ids(monkeypatch, heads):
    monkeypatch.setattr(
        pr_rebase, "_git_bytes", lambda *args, **kwargs: pytest.fail("invalid refs reached Git"),
    )
    assert pr_rebase._tree_ids(heads, ".") is None


def test_replay_tree_probe_timeout_retains_existing_error_contract(monkeypatch):
    def stalled(command, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(pr_rebase.subprocess, "run", stalled)
    with pytest.raises(git_ops.GitError, match="timed out after 30 seconds"):
        pr_rebase._tree_ids(("a" * 40,), ".")
