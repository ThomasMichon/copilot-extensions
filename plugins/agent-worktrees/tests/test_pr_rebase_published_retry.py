"""Recover an accepted rebased publication without forcing the remote again."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, git_collab, git_ops, git_push_transport
from agent_worktrees import pr_ops, pr_publish, tracking

pytestmark = pytest.mark.timeout(180)


def _git(path: Path, *args: str) -> str:
    return git_ops.git(*args, cwd=str(path)).stdout.strip()


def _accepted_rebase(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> tuple[cfg.Config, str, Path, Path, str, str, str]:
    config, wid, path, remote = pr_repo
    repo = dataclasses.replace(
        config.default_repo,
        pr=dataclasses.replace(config.default_repo.pr, head_scheme="refspec"),
    )
    config = dataclasses.replace(config, repos={"ext": repo})
    created = pr_ops.create_pr(wid, config, title="Owned change")
    assert created["success"], created
    old = created["head_sha"]
    anchor = Path(repo.anchor)
    (anchor / "upstream.txt").write_text("upstream advance\n")
    _git(anchor, "add", "upstream.txt")
    _git(anchor, "commit", "-m", "Advance base")
    _git(anchor, "push", "origin", "master")
    assert git_collab.sync_forward(wid, config)
    accepted = _git(path, "rev-parse", "HEAD")
    assert not git_ops.is_commit_ancestor(old, accepted, cwd=path)
    transport = git_push_transport.push

    def lose_ack(*args: Any, **kwargs: Any) -> git_ops.PushResult:
        result = transport(*args, **kwargs)
        assert result, result.stderr
        return git_ops.PushResult(ok=False, stderr="transport acknowledgement lost")

    with monkeypatch.context() as patch:
        patch.setattr(git_push_transport, "push", lose_ack)
        assert not finalize.push_changes(wid, config)
    branch = created["branch"]
    assert _git(path, "--git-dir", str(remote), "rev-parse", branch) == accepted
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").pr.head_sha == old
    (path / "later.txt").write_text("retained later source work\n")
    _git(path, "add", "later.txt")
    _git(path, "commit", "-m", "Later source work")
    return config, wid, path, remote, branch, old, accepted


@pytest.mark.parametrize("command", ["push-changes", "create-pr"])
def test_accepted_rebase_retry_is_pinned_nonforcing_and_refreshes_tracking(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    config, wid, path, remote, branch, old, accepted = _accepted_rebase(pr_repo, monkeypatch)
    source = _git(path, "rev-parse", "HEAD")
    monkeypatch.setattr(pr_publish, "_provider_head", lambda *_a, **_k: accepted)
    transport = git_push_transport.push
    calls = []

    def capture(*args: Any, **kwargs: Any) -> git_ops.PushResult:
        calls.append((args, kwargs))
        return transport(*args, **kwargs)

    monkeypatch.setattr(git_push_transport, "push", capture)
    if command == "push-changes":
        assert finalize.push_changes(wid, config)
    else:
        result = pr_ops.create_pr(wid, config, title="Owned change")
        assert result.get("success"), result
    assert _git(path, "--git-dir", str(remote), "rev-parse", branch) == source
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    assert record.pr.head_sha == source
    assert record.pr.base_sha == _git(path, "rev-parse", "origin/master")
    assert record.pr.patch_id == pr_ops._patch_id(record.pr.base_sha, source, cwd=str(path))
    assert calls[-1][0] == ("origin", f"{source}:refs/heads/{branch}")
    assert calls[-1][1]["force_with_lease_expect"] is None
    assert not calls[-1][1].get("force_with_lease", False)
    assert calls[-1][1]["timeout"] == config.default_repo.pr.push_timeout_seconds
    assert (path / "later.txt").read_text() == "retained later source work\n"
    assert not git_ops.is_commit_ancestor(old, accepted, cwd=path)


@pytest.mark.parametrize("observation", ["missing", "different", "foreign", "racing"])
def test_accepted_rebase_retry_refuses_unknown_or_concurrently_changed_heads(
    pr_repo: tuple[cfg.Config, str, Path, Path], monkeypatch: pytest.MonkeyPatch, observation: str,
) -> None:
    config, wid, path, remote, branch, old, accepted = _accepted_rebase(pr_repo, monkeypatch)
    remote_head = accepted
    if observation in ("foreign", "racing"):
        other = remote.parent / "concurrent"
        _git(path, "clone", str(remote), str(other))
        _git(other, "config", "user.email", "reviewer@example.com")
        _git(other, "config", "user.name", "Reviewer")
        _git(other, "checkout", "-B", branch, f"origin/{branch}")
        (other / "reviewer.txt").write_text("preserve concurrent work\n")
        _git(other, "add", "reviewer.txt")
        _git(other, "commit", "-m", "Concurrent work")
        remote_head = _git(other, "rev-parse", "HEAD")
        if observation == "foreign":
            _git(other, "push", "origin", branch)

    def observe(*_args: Any, **_kwargs: Any) -> str:
        if observation == "racing":
            _git(other, "push", "origin", branch)
            return accepted
        return "" if observation == "missing" else "0" * 40 if observation == "different" else remote_head

    monkeypatch.setattr(pr_publish, "_provider_head", observe)
    assert not finalize.push_changes(wid, config)
    assert _git(path, "--git-dir", str(remote), "rev-parse", branch) == remote_head
    assert tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml").pr.head_sha == old
