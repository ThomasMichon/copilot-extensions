"""Repository-configured deadlines reach real PR pushes and their locks."""

from dataclasses import replace
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, git_ops, pr_ops, pr_publish, push_timeout


@pytest.mark.parametrize("raw,expected", [({}, 180.0), ({"push_timeout_seconds": 300}, 300.0),
                                         ({"push_timeout_seconds": 0.5}, 0.5)])
def test_configured_deadline(raw, expected):
    assert cfg._parse_pr(raw).push_timeout_seconds == expected
    assert cfg.PRConfig().push_timeout_seconds == push_timeout.DEFAULT_PUSH_TIMEOUT == 180.0


@pytest.mark.parametrize("value", [
    None, True, False, 0, -1, float("inf"), float("-inf"), float("nan"),
    "", "300", "invalid", [], {}, 10 ** 400, 1e308, 1e300,
])
def test_invalid_deadline_fails_explicitly_before_publication(value, monkeypatch):
    def no_git(*args, **kwargs):
        pytest.fail("invalid deadline reached git")

    monkeypatch.setattr(git_ops, "git", no_git)
    with pytest.raises(ValueError, match=r"pr.push_timeout_seconds.*finite positive"):
        cfg._parse_pr({"push_timeout_seconds": value})
    with pytest.raises(ValueError, match=r"pr.push_timeout_seconds.*finite positive"):
        cfg.PRConfig(push_timeout_seconds=value)
    with pytest.raises(ValueError, match=r"pr.push_timeout_seconds.*finite positive"):
        pr_publish.push_checked(None, "origin", "HEAD:refs/heads/change", cwd=".", timeout=value)


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
@pytest.mark.parametrize("timeout", [180.0, 300.0])
def test_real_publication_forwards_deadline_and_lock_budgets(pr_repo, monkeypatch, scheme, timeout):
    config, wid, wt_path, _ = pr_repo
    repo = config.default_repo
    config = replace(config, repos={"ext": replace(
        repo, pr=replace(repo.pr, head_scheme=scheme, push_timeout_seconds=timeout),
    )})
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    attempts, waits, lifecycle = [], [], []
    bounded = push_timeout.run_bounded
    lock_file = pr_publish._lock_publish_file
    finalize_lock = finalize.FinalizeLock

    def run_bounded(argv, **kwargs):
        if "push" in argv:
            attempts.append(kwargs["timeout"])
            assert "-c" not in argv or "core.hooksPath=" not in " ".join(argv)
        return bounded(argv, **kwargs)

    def acquire_file(fh, *, timeout):
        waits.append(timeout)
        return lock_file(fh, timeout=timeout)

    def acquire_lifecycle(path, **kwargs):
        lifecycle.append(kwargs)
        return finalize_lock(path, **kwargs)

    monkeypatch.setattr(push_timeout, "run_bounded", run_bounded)
    monkeypatch.setattr(pr_publish, "_lock_publish_file", acquire_file)
    monkeypatch.setattr(finalize, "FinalizeLock", acquire_lifecycle)
    common = git_ops.git("rev-parse", "--git-common-dir", cwd=wt_path).stdout.strip()
    hook = Path(wt_path, common) / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    marker = hook.parent / "hook-ran"
    hook.write_text(f"#!/bin/sh\necho \"$AGENT_WORKTREES_PR_PUSH\" >> '{marker.as_posix()}'\n",
                    encoding="utf-8", newline="\n")
    hook.chmod(0o755)

    first = pr_ops.create_pr(wid, config, title="Publish deadline")
    assert first["success"], first
    git_ops.git("commit", "--allow-empty", "-m", "feedback", cwd=wt_path)
    rerun = pr_ops.create_pr(wid, config, title="Publish deadline")
    assert rerun["success"], rerun
    git_ops.git("commit", "--allow-empty", "-m", "more feedback", cwd=wt_path)
    assert finalize.push_changes(wid, config)

    assert attempts == [timeout] * 3
    assert waits and set(waits) == {2 * timeout + 30}
    budget = 4 * timeout + 180
    assert lifecycle == [{"timeout": budget, "stale_after": budget}]
    assert marker.read_text().splitlines() == ["1"] * 3


def test_auth_retry_retains_configured_bound(monkeypatch):
    import subprocess

    attempts = []
    monkeypatch.setattr(git_ops, "_auth_config_args", lambda *a, **k: ["-c", "test.auth=true"])

    def git(*args, **kwargs):
        attempts.append(kwargs)
        return subprocess.CompletedProcess(args, 1 if len(attempts) == 1 else 0, "", "denied")

    monkeypatch.setattr(git_ops, "git", git)
    assert git_ops.push("origin", "change", cwd=".", timeout=300)
    assert len(attempts) == 2
    assert all(a["timeout"] == 300 and a["kill_tree"] for a in attempts)


def test_verified_rebase_retains_configured_bound(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from agent_worktrees import git_push_transport, pr_rebase

    attempts = []
    proof = SimpleNamespace(source_head="source", branch="change", new_base="base")
    monkeypatch.setattr(pr_publish, "publish_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(pr_rebase, "verify", lambda *a, **k: proof)
    monkeypatch.setattr(pr_rebase, "_save", lambda *a, **k: None)

    def ordinary_push(*args, **kwargs):
        attempts.append(kwargs["timeout"])
        return git_ops.PushResult(ok=False, stderr="Refusing: old not an ancestor.")

    def verified_push(*args, **kwargs):
        attempts.append(kwargs["timeout"])
        assert kwargs["force_with_lease_expect"] == "old"
        return git_ops.PushResult(ok=True)

    monkeypatch.setattr(git_ops, "push", ordinary_push)
    monkeypatch.setattr(git_push_transport, "push", verified_push)
    result = pr_publish.push_checked(
        None, "origin", "HEAD:refs/heads/change", cwd=".",
        force_with_lease_expect="old", repo=object(), timeout=300,
    )
    assert result.ok and attempts == [300, 300]
    assert result.published_head_sha == "source"


def test_maximum_deadline_is_usable_by_real_subprocess(tmp_path):
    import math
    import os
    import sys

    from agent_worktrees import publication_deadline

    maximum = (publication_deadline.MAX_WAIT_SECONDS - 180) / 4
    assert publication_deadline.validate(maximum) == maximum
    with pytest.raises(ValueError, match=r"pr.push_timeout_seconds.*finite positive"):
        publication_deadline.validate(math.nextafter(maximum, math.inf))
    result = push_timeout.run_bounded(
        [sys.executable, "-c", "pass"], cwd=tmp_path, env=os.environ.copy(), timeout=maximum,
    )
    assert result.returncode == 0, result.stderr
