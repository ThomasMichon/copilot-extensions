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

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import finalize


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
    worktree_id = "lambda-core-wsl-test"
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


def test_precondition_blocks_when_not_upstream(refspec_worktree):
    env = refspec_worktree
    # Do NOT land the work on master; the remote also has no pr/<slug> head.
    _git("fetch", "origin", cwd=env.clone)
    record, repo = _record_and_repo(env)

    ok, err = finalize._pr_finalize_precondition(
        record, repo, str(env.clone), str(env.clone)
    )
    assert ok is False
    assert err is not None
    assert "is not on" in err
