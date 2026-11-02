"""Complete provider enumeration without enabling bulk completion."""

import json
import subprocess

import pytest

from agent_worktrees.providers import repository_inventory as inventory
from agent_worktrees.providers.base import ProviderError
from agent_worktrees.providers.github import GitHubProvider


def row(number=1, **changes):
    return {
        "number": number, "url": f"https://github.com/example/widgets/pull/{number}",
        "state": "open", "title": f"Change {number}", "created_at": "2026-01-01T00:00:00Z",
        "author": "author-a", "head_sha": "a" * 40, "base_repo": "example/widgets", **changes,
    }


def response(rows, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(rows), "later page unavailable" if code else "")


def test_complete_listing_reads_beyond_one_full_page(monkeypatch):
    calls = []

    def run(argv, *, env):
        calls.append((argv, env))
        pages = json.dumps([row(n) for n in range(1, 101)]) + "\n" + json.dumps([row(101)])
        return subprocess.CompletedProcess(argv, 0, pages, "")

    monkeypatch.setattr(inventory, "run_cli", run)
    result = GitHubProvider().list_open_pull_records("example/widgets", token="scoped-token")
    assert len(result) == 101
    assert len(calls) == 1 and "--paginate" in calls[0][0]
    assert all(env["GH_TOKEN"] == "scoped-token" for _, env in calls)
    assert all("--hostname" in argv and "github.com" in argv for argv, _ in calls)
    assert result[-1]["number"] == 101 and "base_repo" not in result[-1]


def test_later_page_failure_never_returns_partial_success(monkeypatch):
    monkeypatch.setattr(inventory, "run_cli", lambda *_a, **_k: response([row(n) for n in range(1, 101)], 1))
    with pytest.raises(ProviderError, match="did not complete"):
        GitHubProvider().list_open_pull_records("example/widgets")


@pytest.mark.parametrize("payload", [
    {}, [None], [row(number=True)], [row(state="closed")], [row(author=42)],
    [row(base_repo="example/other")], [row(url="https://foreign.example/example/widgets/pull/1")],
    [row(url="https://github.com/example/other/pull/1")],
    [row(created_at="not-time")], [row(created_at="2026-01-01T00:00:00")],
])
def test_malformed_or_foreign_provider_data_refuses(monkeypatch, payload):
    monkeypatch.setattr(inventory, "run_cli", lambda *_a, **_k: response(payload))
    with pytest.raises(ProviderError):
        GitHubProvider().list_open_pull_records("example/widgets")


def test_repeated_page_is_incomplete_not_a_false_total(monkeypatch):
    pages = json.dumps([row(1)]) + "\n" + json.dumps([row(1)])
    monkeypatch.setattr(inventory, "run_cli", lambda *_a, **_k:
                        subprocess.CompletedProcess([], 0, pages, ""))
    with pytest.raises(ProviderError, match="repeated"):
        GitHubProvider().list_open_pull_records("example/widgets")


def test_short_nonterminal_page_still_reads_native_pager_output(monkeypatch):
    pages = json.dumps([row(1)]) + "\n" + json.dumps([row(2)])
    monkeypatch.setattr(inventory, "run_cli", lambda *_a, **_k:
                        subprocess.CompletedProcess([], 0, pages, ""))
    assert len(GitHubProvider().list_open_pull_records("example/widgets")) == 2


def test_truncated_pager_stream_cannot_be_complete(monkeypatch):
    pages = json.dumps([row(1)]) + "\n["
    monkeypatch.setattr(inventory, "run_cli", lambda *_a, **_k:
                        subprocess.CompletedProcess([], 0, pages, ""))
    with pytest.raises(ProviderError, match="page 2"):
        GitHubProvider().list_open_pull_records("example/widgets")


def test_empty_provider_inventory_is_valid(monkeypatch):
    monkeypatch.setattr(inventory, "run_cli", lambda *_a, **_k: response([]))
    assert GitHubProvider().list_open_pull_records("example/widgets") == ()


def test_nullable_metadata_does_not_erase_a_known_open_pr(monkeypatch):
    monkeypatch.setattr(inventory, "run_cli", lambda *_a, **_k:
                        response([row(author=None, head_sha=None, title=None)]))
    result = GitHubProvider().list_open_pull_records("example/widgets")
    assert result[0]["number"] == 1 and result[0]["author"] is None
    assert set(result[0]["metadata_unknown"]) == {"title", "author", "head_sha"}


def test_wrong_ambient_identity_never_reaches_repository_read(monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "another-account\n", "")

    monkeypatch.setattr(inventory, "run_cli", run)
    with pytest.raises(ProviderError, match="identity"):
        GitHubProvider().list_open_pull_records("example/widgets", expected_login="expected-account")
    assert len(calls) == 1 and calls[0][-3] == "https://api.github.com/user"


def test_explicit_provider_host_is_pinned_to_absolute_endpoint(monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return response([])

    monkeypatch.setattr(inventory, "run_cli", run)
    GitHubProvider().list_open_pull_records("example/widgets", api_base="https://forge.example/api/v3")
    assert calls[0][4].startswith("https://forge.example/api/v3/repos/example/widgets/")


@pytest.mark.parametrize("actual", ["expected-account", "another-account"])
def test_enterprise_token_aliases_and_explicit_identity_are_enforced(monkeypatch, actual):
    calls = []
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        monkeypatch.setenv(key, "unrelated-inherited-token")

    def run(argv, *, env):
        calls.append((argv, env))
        if argv[-1] == ".login":
            return subprocess.CompletedProcess(argv, 0, actual, "")
        return response([])

    monkeypatch.setattr(inventory, "run_cli", run)
    read = lambda: GitHubProvider().list_open_pull_records(
        "example/widgets", api_base="https://forge.example/api/v3",
        token="selected-repository-token", expected_login="expected-account",
    )
    if actual == "expected-account":
        assert read() == () and len(calls) == 2
    else:
        with pytest.raises(ProviderError, match="identity"):
            read()
        assert len(calls) == 1
    assert all(
        env[key] == "selected-repository-token"
        for _, env in calls
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
    )


def test_read_only_inventory_does_not_unlock_existing_bulk_merge():
    with pytest.raises(ProviderError, match="gitea-only"):
        GitHubProvider().list_open_pulls("example/widgets")
