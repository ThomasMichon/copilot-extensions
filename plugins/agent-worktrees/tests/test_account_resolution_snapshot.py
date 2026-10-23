"""Bulk account metadata retains the canonical per-target lookup policy."""
from __future__ import annotations

import pytest
import yaml

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


@pytest.mark.parametrize("account_map", [
    ["Example", "login"], None, {"Example": ["login"]}, {"Example": 12},
    {"": "login"}, {"Example": ""}, {12: "login"},
])
def test_bulk_command_rejects_malformed_stored_account_map(tmp_path, monkeypatch, account_map):
    registry_path = tmp_path / "repos.yaml"
    registry_path.write_text(
        yaml.safe_dump({"repos": {}, "account_map": account_map}), encoding="utf-8",
    )
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry_path)
    errors = []
    payloads = []
    monkeypatch.setattr(repos_cli.output, "_json_output", payloads.append)
    monkeypatch.setattr(
        repos_cli.output, "_json_error",
        lambda message, exit_code: errors.append(message) or exit_code,
    )
    assert repos_cli.cmd_repos_dispatch(["account", "list", "--json", "--with-resolutions"]) == 2
    assert "account_map" in errors[0]
    assert payloads == []


@pytest.mark.parametrize("count", [200, 400, 800])
def test_bulk_resolution_parses_slugs_linearly(monkeypatch, count):
    registry = repos.ReposRegistry(repos={
        f"alias-{i}": repos.RepoEntry(
            f"alias-{i}", remote=f"https://github.com/Owner-{i}/repo",
            account=f"explicit-{i}" if i % 2 else "",
        ) for i in range(count)
    }, account_map={f"owner-{i}": f"mapped-{i}" for i in range(count)})
    monkeypatch.setattr(repos, "read_registry", lambda **_: registry)
    parse = repos.github_slug
    calls = 0

    def counted(remote):
        nonlocal calls
        calls += 1
        return parse(remote)

    monkeypatch.setattr(repos, "github_slug", counted)
    snapshot = repo_accounts.resolution_snapshot()
    assert calls <= 2 * count
    assert len(snapshot["repository_accounts"]) == 2 * count
    for i in range(count):
        login = f"explicit-{i}" if i % 2 else f"mapped-{i}"
        assert snapshot["repository_accounts"][f"owner-{i}/repo"] == login
        assert snapshot["repository_accounts"][f"alias-{i}"] == login
        assert snapshot["owner_accounts"][f"owner-{i}"] == login


def test_index_preserves_registry_first_match_and_alias_case(monkeypatch):
    registry = repos.ReposRegistry(
        account_map={"Example": "mapped"},
        repos={
            "Alias": repos.RepoEntry("Alias", remote="https://github.com/Example/repo"),
            "alias": repos.RepoEntry(
                "alias", remote="git@github.com:example/REPO", account="second",
            ),
            "third": repos.RepoEntry(
                "third", remote="https://github.com/EXAMPLE/other", account="third",
            ),
            "non-github": repos.RepoEntry("non-github", remote="https://example.com/repo"),
        },
    )
    monkeypatch.setattr(repos, "read_registry", lambda **_: registry)
    snapshot = repo_accounts.resolution_snapshot()
    for target in ("Alias", "alias", "third", "non-github", "Example/repo", "EXAMPLE/other"):
        key = target.casefold() if "/" in target else target
        assert snapshot["repository_accounts"][key] == repos.account_for_github_slug(
            target, registry=registry,
        )
    assert snapshot["owner_accounts"]["example"] == repos.account_for_github_owner(
        "Example", registry=registry,
    )
