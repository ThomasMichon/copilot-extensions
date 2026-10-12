"""Tests for the Phase 10 item 3 poll-fallback cycle."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_dispatch.github_provider_adapter import PRObservation
from agent_dispatch.pr_observation_store import PRObservationStore
from agent_dispatch.pr_polling_policy import RepoTier
from agent_dispatch.pr_review_poll_loop import build_provider_observer, run_poll_cycle
from agent_dispatch.provider_state_machine import ApprovalStatus, Mergeability, Revision


def _observation(diff_hash: str = "diff-1") -> PRObservation:
    return PRObservation(
        number=1,
        approval_status=ApprovalStatus.APPROVED,
        mergeability=Mergeability.CLEAN,
        holds=frozenset(),
        revision=Revision(diff_hash=diff_hash, base_sha="base-1"),
        last_commit_at=1234.0,
    )


def test_a_pr_not_yet_due_is_not_refreshed(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    store.put("example/owned-private", 1, _observation(), observed_at=1000.0)
    calls: list[tuple[str, int]] = []

    def observe(repo: str, number: int) -> PRObservation:
        calls.append((repo, number))
        return _observation("diff-2")

    tiers = {"example/owned-private": RepoTier.OWNED_PRIVATE}
    refreshed = run_poll_cycle(
        store,
        observe,
        tiers,
        now=1000.0 + 299,  # < 5-minute OWNED_PRIVATE interval
    )

    assert refreshed == []
    assert calls == []


def test_a_due_pr_is_refreshed_and_persisted(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    store.put("example/owned-private", 1, _observation(), observed_at=1000.0)

    def observe(repo: str, number: int) -> PRObservation:
        return _observation("diff-2")

    tiers = {"example/owned-private": RepoTier.OWNED_PRIVATE}
    now = 1000.0 + 300  # exactly the 5-minute OWNED_PRIVATE interval
    refreshed = run_poll_cycle(store, observe, tiers, now=now)

    assert len(refreshed) == 1
    repo, number, observation = refreshed[0]
    assert (repo, number) == ("example/owned-private", 1)
    assert observation.revision.diff_hash == "diff-2"
    assert store.last_observed_at("example/owned-private", 1) == now


def test_multiple_tracked_prs_are_each_evaluated_independently(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    store.put("example/owned-private", 1, _observation(), observed_at=1000.0)
    store.put("example/other-private", 2, _observation(), observed_at=1000.0 + 299)

    def observe(repo: str, number: int) -> PRObservation:
        return _observation("diff-2")

    tiers = {
        "example/owned-private": RepoTier.OWNED_PRIVATE,
        "example/other-private": RepoTier.OWNED_PRIVATE,
    }
    now = 1000.0 + 300
    refreshed = run_poll_cycle(store, observe, tiers, now=now)

    # example/owned-private (300s elapsed) is due; example/other-private
    # (1s elapsed) is not.
    assert [(repo, number) for repo, number, _ in refreshed] == [("example/owned-private", 1)]


def test_untracked_store_produces_an_empty_cycle(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    refreshed = run_poll_cycle(store, lambda repo, number: _observation(), now=1000.0)
    assert refreshed == []


def test_a_pr_with_no_prior_last_observed_at_is_treated_as_due(tmp_path: Path, monkeypatch):
    store = PRObservationStore(tmp_path / "pr.db")
    store.put("example/a", 1, _observation(), observed_at=1000.0)
    # Simulate a row somehow missing last_observed_at by monkeypatching the
    # accessor -- put() always sets it, so this exercises the defensive branch.
    monkeypatch.setattr(store, "last_observed_at", lambda repo, number: None)

    calls: list[tuple[str, int]] = []

    def observe(repo: str, number: int) -> PRObservation:
        calls.append((repo, number))
        return _observation("diff-2")

    refreshed = run_poll_cycle(store, observe, now=1000.0)

    assert calls == [("example/a", 1)]
    assert len(refreshed) == 1


def test_build_provider_observer_routes_provider_tagged_store_keys(monkeypatch):
    calls = []

    class _GitHub:
        def __init__(self, expected_login, runner=None):
            self.expected_login = expected_login

        def observe(self, repo, number):
            calls.append(("github", repo, number, self.expected_login))
            return _observation("github-diff")

    class _AzureDevOps:
        def __init__(self, expected_login, runner=None):
            self.expected_login = expected_login

        def observe(self, repo, number):
            calls.append(("azure-devops", repo, number, self.expected_login))
            return _observation("ado-diff")

    class _Gitea:
        def __init__(self, expected_login, runner=None, api_bases=None):
            self.expected_login = expected_login
            self.api_bases = api_bases

        def observe(self, repo, number):
            calls.append(("gitea", repo, number, self.expected_login, self.api_bases))
            return _observation("gitea-diff")

    monkeypatch.setattr("agent_dispatch.pr_review_poll_loop.GitHubPRAdapter", _GitHub)
    monkeypatch.setattr("agent_dispatch.pr_review_poll_loop.AzureDevOpsPRAdapter", _AzureDevOps)
    monkeypatch.setattr("agent_dispatch.pr_review_poll_loop.GiteaPRAdapter", _Gitea)

    observe = build_provider_observer(
        {"github": "gh-bot", "azure-devops": "ado-bot", "gitea": "gitea-bot"},
        runner=lambda *_args, **_kwargs: SimpleNamespace(),
        gitea_api_bases={"gitea.example.com": "https://gitea.example.com"},
    )

    github = observe("example/project", 7)
    azure = observe("azure-devops:example-org/example-project/example-repo", 9)
    gitea = observe("gitea:gitea.example.com/example-org/example-repo", 11)

    assert github.revision.diff_hash == "github-diff"
    assert azure.revision.diff_hash == "ado-diff"
    assert gitea.revision.diff_hash == "gitea-diff"
    assert calls == [
        ("github", "example/project", 7, "gh-bot"),
        ("azure-devops", "example-org/example-project/example-repo", 9, "ado-bot"),
        (
            "gitea", "gitea.example.com/example-org/example-repo", 11, "gitea-bot",
            {"gitea.example.com": "https://gitea.example.com"},
        ),
    ]

