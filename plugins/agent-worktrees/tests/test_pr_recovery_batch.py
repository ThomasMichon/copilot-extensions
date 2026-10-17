"""Fresh native recovery-object reads and all-or-nothing retention."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_worktrees import git_ops, pr_rebase, pr_recovery


def test_recovery_commit_batch_is_fresh_and_isolated(pr_repo, monkeypatch):
    _, _, path, remote = pr_repo
    head = git_ops.git("rev-parse", "HEAD", cwd=path).stdout.strip()
    parent = git_ops.git("rev-parse", "HEAD^", cwd=path).stdout.strip()
    calls = []
    original = pr_rebase._git_bytes

    def observed(*args, **kwargs):
        calls.append((args, kwargs["stdin"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(pr_rebase, "_git_bytes", observed)
    git_ops.git("update-ref", "refs/recovery-test/tip", head, cwd=path)
    with monkeypatch.context() as patch:
        patch.setenv("GIT_DIR", str(remote))
        patch.setenv("GIT_WORK_TREE", str(remote.parent))
        assert pr_recovery._commits(("HEAD", "refs/recovery-test/tip"), str(path)) == (
            head, head,
        )
    git_ops.git("update-ref", "refs/recovery-test/tip", parent, head, cwd=path)
    assert pr_recovery._commits(("HEAD", "refs/recovery-test/tip"), str(path)) == (
        head, parent,
    )
    assert len(calls) == 2
    assert all(args == ("cat-file", "--batch-check=%(objectname) %(objecttype)", "-z")
               for args, _ in calls)
    assert all(data == b"HEAD^{commit}\0refs/recovery-test/tip^{commit}\0"
               for _, data in calls)


@pytest.mark.parametrize("ref", ["refs/recovery-test/missing", "HEAD^{tree}"])
def test_recovery_commit_batch_refuses_missing_or_noncommit(pr_repo, ref):
    _, _, path, _ = pr_repo
    with pytest.raises(ValueError, match="missing, non-commit, or malformed"):
        pr_recovery._commits(("HEAD", ref), str(path))


@pytest.mark.parametrize("refs", [(), ("",), ("HEAD\0HEAD",), (None,), (True,)])
def test_recovery_commit_batch_refuses_invalid_input(monkeypatch, refs):
    monkeypatch.setattr(
        pr_rebase, "_git_bytes", lambda *a, **k: pytest.fail("invalid input reached Git"),
    )
    with pytest.raises(ValueError, match="nonempty and NUL-free"):
        pr_recovery._commits(refs, ".")


@pytest.mark.parametrize("output", [
    b"", b"a" * 40 + b" commit\nextra\n", b"a" * 41 + b" commit\n",
    b"a" * 40 + b" tree\n", b"a" * 40 + b" commit",
])
def test_recovery_commit_batch_refuses_malformed_output(monkeypatch, output):
    monkeypatch.setattr(pr_rebase, "_git_bytes", lambda *a, **k: output)
    with pytest.raises(ValueError, match="missing, non-commit, or malformed"):
        pr_recovery._commits(("HEAD",), ".")


@pytest.mark.parametrize("width", [40, 64])
def test_recovery_commit_batch_accepts_complete_object_ids(monkeypatch, width):
    monkeypatch.setattr(
        pr_rebase, "_git_bytes", lambda *a, **k: b"a" * width + b" commit\n",
    )
    assert pr_recovery._commits(("HEAD",), ".") == ("a" * width,)


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_recovery_batch_uses_native_object_format(tmp_path, object_format):
    git_ops.git(
        "init", "--template=", f"--object-format={object_format}", "-b", "main",
        str(tmp_path),
    )
    (tmp_path / "source.txt").write_text("native recovery objects\n", encoding="utf-8")
    git_ops.git("add", "source.txt", cwd=tmp_path)
    git_ops.git(
        "-c", "user.name=Test", "-c", "user.email=t@example.com",
        "commit", "-m", "native recovery", cwd=tmp_path,
    )
    head = git_ops.git("rev-parse", "HEAD", cwd=tmp_path).stdout.strip()
    assert len(head) == (40 if object_format == "sha1" else 64)
    point = pr_recovery.RecoveryPoint(
        version=1, worktree_id="task", branch="main", local_head=head,
        local_ref="refs/recovery-test/native", target_head=head,
    )
    pr_recovery._retain_refs(point, cwd=str(tmp_path))
    assert pr_recovery._commits(("HEAD", point.local_ref), str(tmp_path)) == (head, head)


@pytest.mark.parametrize("collision", [False, True])
def test_recovery_retention_is_one_create_only_transaction(pr_repo, monkeypatch, collision):
    _, wid, path, _ = pr_repo
    head = git_ops.git("rev-parse", "HEAD", cwd=path).stdout.strip()
    parent = git_ops.git("rev-parse", "HEAD^", cwd=path).stdout.strip()
    root = "refs/recovery-test/atomic"
    point = pr_recovery.RecoveryPoint(
        version=1, worktree_id=wid, branch=f"worktree/{wid}",
        local_head=head, local_ref=f"{root}/local", target_head=parent,
        published_head=parent, published_ref=f"{root}/published",
        lineage_head=head, lineage_ref=f"{root}/lineage",
    )
    if collision:
        git_ops.git("update-ref", point.published_ref, head, cwd=path)
    calls = []
    original = pr_rebase._git_bytes

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(pr_rebase, "_git_bytes", observed)
    if collision:
        with pytest.raises(git_ops.GitError, match="reference already exists"):
            pr_recovery._retain_refs(point, cwd=str(path))
        assert git_ops.git("show-ref", "--verify", point.local_ref, cwd=path,
                           check=False).returncode != 0
        assert git_ops.git("show-ref", "--verify", point.lineage_ref, cwd=path,
                           check=False).returncode != 0
        assert git_ops.git("rev-parse", point.published_ref, cwd=path).stdout.strip() == head
    else:
        pr_recovery._retain_refs(point, cwd=str(path))
        assert pr_recovery._commits(
            (point.local_ref, point.published_ref, point.lineage_ref), str(path),
        ) == (head, parent, head)
    assert calls[0] == ("update-ref", "--stdin", "-z")
    assert sum(args[:1] == ("update-ref",) for args in calls) == 1
    with pytest.raises(ValueError, match="complete object IDs"):
        pr_recovery._retain_refs(replace(point, local_head="a" * 41), cwd=str(path))
