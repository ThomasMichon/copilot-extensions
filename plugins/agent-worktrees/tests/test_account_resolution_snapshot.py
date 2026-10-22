"""Bulk account metadata retains the canonical per-target lookup policy."""
from __future__ import annotations

from agent_worktrees import repo_accounts, repos, repos_cli


def test_snapshot_matches_exact_repo_alias_and_owner_policy(monkeypatch):
    registry = repos.ReposRegistry(
        account_map={"Example": "mapped", "Other": "other"},
        repos={
            "first": repos.RepoEntry(
                "first", remote="https://github.com/Example/first.git", account="first-user",
            ),
            "second": repos.RepoEntry(
                "second", remote="git@github.com:Example/second.git", account="second-user",
            ),
            "derived": repos.RepoEntry(
                "derived", remote="https://github.com/Other/derived.git",
            ),
            "non-github": repos.RepoEntry("non-github", remote="https://example.com/repo"),
        },
    )
    monkeypatch.setattr(repos, "read_registry", lambda **_: registry)
    monkeypatch.setattr(repos, "find_repo", lambda target: registry.repos.get(target))
    snapshot = repo_accounts.resolution_snapshot()
    assert snapshot["resolution_version"] == 1
    assert snapshot["repository_accounts"] == {
        "first": "first-user", "example/first": "first-user",
        "second": "second-user", "example/second": "second-user",
        "derived": "other", "other/derived": "other",
        "non-github": None,
    }
    assert snapshot["owner_accounts"] == {"example": "first-user", "other": "other"}
    payloads = []
    monkeypatch.setattr(repos_cli.output, "_json_output", payloads.append)
    assert repos_cli.cmd_repos_dispatch(["account", "list", "--json", "--with-resolutions"]) == 0
    payload = payloads[0]
    assert payload["account_map"] == registry.account_map
    assert all(payload[key] == value for key, value in snapshot.items())


def test_snapshot_uses_one_strict_registry_read(monkeypatch):
    reads = []
    registry = repos.ReposRegistry(
        repos={"Alias": repos.RepoEntry("Alias", remote="https://github.com/Example/repo")},
    )

    def read(**kwargs):
        reads.append(kwargs)
        return registry

    monkeypatch.setattr(repos, "read_registry", read)
    snapshot = repo_accounts.resolution_snapshot()
    assert reads == [{"strict": True}]
    assert snapshot["repository_accounts"]["Alias"] == "Example"
    assert "alias" not in snapshot["repository_accounts"]
    assert snapshot["unmapped_owner_fallback"] is True


def test_bulk_resolution_refuses_an_unreadable_registry(monkeypatch):
    errors = []
    monkeypatch.setattr(repos, "read_registry", lambda **_: repos.ReposRegistry())

    def unavailable():
        raise repos.RepositoryResolutionError("unreadable")

    monkeypatch.setattr(repo_accounts, "resolution_snapshot", unavailable)
    monkeypatch.setattr(
        repos_cli.output, "_json_error",
        lambda message, exit_code: errors.append(message) or exit_code,
    )
    assert repos_cli.cmd_repos_dispatch(["account", "list", "--json", "--with-resolutions"]) == 2
    assert "unreadable" in errors[0]
