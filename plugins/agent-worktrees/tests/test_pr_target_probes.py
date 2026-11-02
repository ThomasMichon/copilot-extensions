"""Recorded-target reads retain fresh authority without redundant probes."""

import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_publish, tracking
from agent_worktrees.config import PRConfig


@pytest.mark.parametrize("slug,identity,accepted", [
    ("owner/repo", "first.invalid/owner/repo", True),
    ("", "", False),
    ("another/repo", "first.invalid/another/repo", False),
    ("owner/repo", "second.invalid/owner/repo", False),
])
def test_recorded_target_is_decided_by_its_fresh_url_snapshot(
    monkeypatch, slug, identity, accepted,
):
    repo = SimpleNamespace(remote="origin", pr=PRConfig())
    pr = tracking.PRRecord(
        remote="fork", head_repo="owner/repo",
        head_identity="first.invalid/owner/repo",
    )
    calls = []

    def snapshot(remote, *, cwd):
        calls.append((remote, cwd))
        result = subprocess.CompletedProcess([], 0, "", "")
        return result, result, pr_publish.PushTarget(remote, slug, identity)

    monkeypatch.setattr(pr_publish, "_remote_snapshot", snapshot)
    monkeypatch.setattr(
        git_ops, "has_remote",
        lambda *a, **k: pytest.fail("fresh URL authority was preceded by a redundant remote list"),
    )
    target = pr_publish._select_push_target(repo, pr, "checkout")
    assert calls == [("fork", "checkout")]
    assert target == (pr_publish.PushTarget("fork", slug, identity) if accepted else None)


@pytest.mark.parametrize("has_target,tip,gone", [
    (True, "", True),
    (True, None, False),
    (True, "published-head", False),
    (False, "", False),
])
def test_head_absence_keeps_selection_and_probe_inside_one_lock(
    monkeypatch, has_target, tip, gone,
):
    repo = SimpleNamespace(remote="origin", pr=PRConfig())
    pr = tracking.PRRecord(branch="feature/change", remote="fork")
    calls = []
    held = False

    @contextmanager
    def lock(cwd, **kwargs):
        nonlocal held
        assert cwd == "checkout" and not held
        assert kwargs == {"push_timeout_seconds": repo.pr.push_timeout_seconds}
        calls.append("lock")
        held = True
        try:
            yield
        finally:
            held = False
            calls.append("unlock")

    def select(got_repo, got_pr, cwd):
        assert held and (got_repo, got_pr, cwd) == (repo, pr, "checkout")
        calls.append("select")
        return pr_publish.PushTarget("fork") if has_target else None

    def probe(remote, branch, cwd):
        assert held and (remote, branch, cwd) == ("fork", pr.branch, "checkout")
        calls.append("probe")
        return tip

    monkeypatch.setattr(pr_publish, "publish_lock", lock)
    monkeypatch.setattr(pr_publish, "_select_push_target", select)
    monkeypatch.setattr(pr_publish, "_tip", probe)
    monkeypatch.setattr(
        pr_publish, "push_remote",
        lambda *a, **k: pytest.fail("already-locked selection reacquired the publication lock"),
    )
    assert pr_publish.head_branch_gone(repo, pr, "checkout") is gone
    assert not held
    assert calls == ["lock", "select", *(["probe"] if has_target else []), "unlock"]


def test_head_absence_cannot_infer_deletion_without_the_lock(monkeypatch):
    @contextmanager
    def refused(*args, **kwargs):
        raise pr_publish.PublishLockTimeout("controlled contention")
        yield

    monkeypatch.setattr(pr_publish, "publish_lock", refused)
    monkeypatch.setattr(
        pr_publish, "_select_push_target",
        lambda *a, **k: pytest.fail("unlocked target selection"),
    )
    repo = SimpleNamespace(pr=PRConfig())
    assert pr_publish.head_branch_gone(repo, tracking.PRRecord(branch="change"), "checkout") is False
