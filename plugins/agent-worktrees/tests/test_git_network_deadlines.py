"""Authenticated remote-head reads share bounded, output-aware acquisition."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from agent_worktrees import git_acquisition_budget, git_ops, git_push_transport, pr_publish


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


@pytest.mark.parametrize("outer,expected", [(None, [300, 295]), (20.0, [20, 15])])
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

    monkeypatch.setattr(git_push_transport, "_url_aliases", aliases)
    monkeypatch.setattr(git_push_transport, "_auth_attempts", lambda auth: [[], []])
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", lambda destination: [])
    monkeypatch.setattr(git_ops, "git", run)
    with (
        git_acquisition_budget.acquisition_budget(clock.now + outer)
        if outer is not None else nullcontext()
    ):
        assert git_push_transport.observed_ref(
            "origin", ("https://example.com/owner/project.git",), target, ".",
        ) == "a" * 40
    assert observed == expected
