"""Authenticated remote-head reads share bounded, output-aware acquisition."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from agent_worktrees import git_acquisition_budget, git_fetch, git_ops, git_push_transport, pr_publish
from agent_worktrees.config import RepoConfig
from agent_worktrees.tracking import PRRecord


@pytest.mark.parametrize("requested,expected", [(None, None), (300, 300), (5, 5)])
def test_foreground_creation_fetch_has_no_default_completion_deadline(
    monkeypatch, requested, expected,
):
    from agent_worktrees import tracking_write

    calls = []

    def run(*args, **kwargs):
        calls.append((kwargs["timeout"], git_acquisition_budget.remaining_timeout(None)))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(git_ops, "_auth_config_args", lambda *a, **k: [])
    monkeypatch.setattr(git_ops, "git", run)
    with tracking_write.foreground_completion():
        git_ops.fetch("origin", cwd=".", timeout=requested)
    assert calls[0][0] == expected
    assert (calls[0][1] is None) is (expected is None)


def test_unpinned_head_probe_uses_soft_and_hard_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    ref = "refs/heads/topic"

    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        assert args == ("ls-remote", "origin", ref)
        return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\t{ref}\n", "")

    monkeypatch.setattr(git_push_transport, "captured_urls", lambda remote: ())
    monkeypatch.setattr(git_ops, "_auth_config_args", lambda remote, **kwargs: [])
    monkeypatch.setattr(git_ops, "git", run)
    assert pr_publish._tip("origin", "topic", ".") == "a" * 40
    assert calls[0]["timeout"] == 300
    assert calls[0]["soft_timeout"] == 30
    assert calls[0]["kill_tree"] is True


def test_expired_head_probe_is_unknown_not_confirmed_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def expire(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 300)

    monkeypatch.setattr(git_push_transport, "captured_urls", lambda remote: ())
    monkeypatch.setattr(git_ops, "_auth_config_args", lambda remote, **kwargs: [])
    monkeypatch.setattr(git_ops, "git", expire)
    assert pr_publish._tip("origin", "topic", ".") is None


@pytest.mark.parametrize("outer,expected", [(None, [295, 290]), (20.0, [15, 10])])
def test_pinned_head_retries_share_the_original_budget(
    monkeypatch: pytest.MonkeyPatch, outer: float | None, expected: list[int],
) -> None:
    clock = SimpleNamespace(now=100.0)
    time_source = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(git_acquisition_budget, "time", time_source)
    monkeypatch.setattr(git_push_transport, "time", time_source)
    observed: list[float | None] = []
    target = "refs/heads/topic"

    @contextmanager
    def aliases(pairs: Sequence[tuple[str, str]], **kwargs: object) -> Iterator[list[str]]:
        yield []

    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 300
        assert kwargs["soft_timeout"] == 30
        observed.append(git_acquisition_budget.remaining_timeout(300))
        clock.now += 5
        return subprocess.CompletedProcess(
            args, 1 if len(observed) == 1 else 0,
            "" if len(observed) == 1 else f"{'a' * 40}\t{target}\n", "",
        )

    def prepare_auth(destination: str) -> list[str]:
        assert git_acquisition_budget.remaining_timeout(300) == (300 if outer is None else outer)
        clock.now += 5
        return []

    monkeypatch.setattr(git_push_transport, "_url_aliases", aliases)
    monkeypatch.setattr(git_push_transport, "_auth_attempts", lambda auth: [[], []])
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", prepare_auth)
    monkeypatch.setattr(git_ops, "git", run)
    with (
        git_acquisition_budget.acquisition_budget(clock.now + outer)
        if outer is not None else nullcontext()
    ):
        assert git_push_transport.observed_ref(
            "origin", ("https://example.com/owner/project.git",), target, ".",
        ) == "a" * 40
    assert observed == expected


@pytest.mark.parametrize("pinned", [False, True])
def test_head_probe_credential_preparation_cannot_renew_expired_acquisition(
    monkeypatch: pytest.MonkeyPatch, pinned: bool,
) -> None:
    clock = SimpleNamespace(now=100.0)
    time_source = SimpleNamespace(monotonic=lambda: clock.now)
    for module in (git_acquisition_budget, git_push_transport, pr_publish):
        monkeypatch.setattr(module, "time", time_source)
    observed: list[float | None] = []
    destination = "https://example.com/owner/project.git"

    def prepare_auth(*args: object, **kwargs: object) -> list[str]:
        observed.append(git_acquisition_budget.remaining_timeout(300))
        clock.now += 301
        return []

    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        git_acquisition_budget.remaining_timeout(300)
        pytest.fail("expired credential acquisition reached the remote")

    monkeypatch.setattr(git_push_transport, "captured_urls", lambda remote: (destination,) if pinned else ())
    monkeypatch.setattr(git_push_transport, "_url_aliases", lambda *args, **kwargs: nullcontext([]))
    monkeypatch.setattr(git_ops, "_auth_config_args", prepare_auth)
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", prepare_auth)
    monkeypatch.setattr(git_ops, "git", run)
    assert pr_publish._tip("origin", "topic", ".") is None
    assert observed == [300]
    assert git_acquisition_budget.remaining_timeout(None) is None


@pytest.mark.parametrize("outer", [None, 20.0])
def test_legacy_fork_discovery_shares_one_budget_across_all_candidates(
    monkeypatch: pytest.MonkeyPatch, outer: float | None,
) -> None:
    clock = SimpleNamespace(now=100.0)
    time_source = SimpleNamespace(monotonic=lambda: clock.now)
    for module in (git_acquisition_budget, git_push_transport, pr_publish):
        monkeypatch.setattr(module, "time", time_source)
    observed: list[float] = []
    forks = [f"fork-{index}" for index in range(7)]

    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        remaining = git_acquisition_budget.remaining_timeout(300)
        assert remaining is not None
        observed.append(remaining)
        clock.now += min(299, remaining)
        git_acquisition_budget.remaining_timeout(300)
        return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\trefs/heads/topic\n", "")

    monkeypatch.setattr(pr_publish, "_fork_remotes", lambda repo: forks)
    monkeypatch.setattr(pr_publish, "_provider_head", lambda *args: "a" * 40)
    monkeypatch.setattr(git_ops, "has_remote", lambda *args, **kwargs: True)
    monkeypatch.setattr(git_push_transport, "captured_urls", lambda remote: ())
    monkeypatch.setattr(git_ops, "_auth_config_args", lambda *args, **kwargs: [])
    monkeypatch.setattr(git_ops, "git", run)
    with (
        git_acquisition_budget.acquisition_budget(clock.now + outer)
        if outer is not None else nullcontext()
    ):
        assert pr_publish._select_push_target(RepoConfig(".", "."), PRRecord(branch="topic"), ".") is None
    assert observed == ([300, 1] if outer is None else [20])
    assert clock.now - 100 == (300 if outer is None else outer)
    assert git_acquisition_budget.remaining_timeout(None) is None


@pytest.mark.parametrize("requested,outer,expected", [(None, None, 297), (5, None, 2), (300, 20, 17)])
def test_fetch_credential_preparation_shares_caller_and_enclosing_deadlines(
    monkeypatch: pytest.MonkeyPatch, requested: float | None, outer: float | None, expected: float,
) -> None:
    clock = SimpleNamespace(now=100.0)
    time_source = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(git_acquisition_budget, "time", time_source)
    monkeypatch.setattr(git_fetch, "time", time_source)
    observed: list[float | None] = []

    def prepare_auth(*args: object, **kwargs: object) -> list[str]:
        assert git_acquisition_budget.remaining_timeout(None) is not None
        clock.now += 3
        return []

    def run(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append(git_acquisition_budget.remaining_timeout(300))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(git_ops, "_auth_config_args", prepare_auth)
    monkeypatch.setattr(git_ops, "git", run)
    with (
        git_acquisition_budget.acquisition_budget(clock.now + outer)
        if outer is not None else nullcontext()
    ):
        git_ops.fetch("origin", cwd=".", timeout=requested)
    assert observed == [expected]
    assert git_acquisition_budget.remaining_timeout(None) is None


@pytest.mark.parametrize("requested,outer", [(None, None), (5, None), (300, 20)])
def test_fetch_preparation_expiry_uses_the_execution_timeout_error_contract(
    monkeypatch: pytest.MonkeyPatch, requested: float | None, outer: float | None,
) -> None:
    clock = SimpleNamespace(now=100.0)
    time_source = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(git_acquisition_budget, "time", time_source)
    monkeypatch.setattr(git_fetch, "time", time_source)

    def prepare_auth(*args: object, **kwargs: object) -> list[str]:
        remaining = git_acquisition_budget.remaining_timeout(None)
        assert remaining is not None
        clock.now += remaining + 1
        git_acquisition_budget.remaining_timeout(None)
        pytest.fail("expired credential preparation returned")

    monkeypatch.setattr(git_ops, "_auth_config_args", prepare_auth)
    monkeypatch.setattr(git_ops, "git", lambda *args, **kwargs: pytest.fail("remote launched after expiry"))
    with (
        git_acquisition_budget.acquisition_budget(clock.now + outer)
        if outer is not None else nullcontext()
    ), pytest.raises(git_ops.GitError, match="hard timeout") as failure:
        git_ops.fetch("origin", cwd=".", timeout=requested)
    assert failure.value.returncode == 124
    assert isinstance(failure.value.__cause__, TimeoutError)
    assert git_acquisition_budget.remaining_timeout(None) is None
