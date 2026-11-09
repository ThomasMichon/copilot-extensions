"""Bulk account metadata retains the canonical per-target lookup policy."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import repo_accounts, repos, repos_cli


@pytest.mark.parametrize("owner", ["ExampleOrganization", "UnrelatedPublisher"])
def test_repository_namespace_is_not_an_operator_login(monkeypatch, owner):
    registry = repos.ReposRegistry(repos={
        "dependency": repos.RepoEntry(
            "dependency", remote=f"https://github.com/{owner}/dependency",
        ),
    })
    monkeypatch.setattr(repos, "read_registry", lambda **_: registry)
    monkeypatch.setattr(repos, "find_repo", lambda name: registry.repos.get(name))
    assert repos.resolve_account(registry.repos["dependency"]) is None
    assert repos.account_for_github_slug(f"{owner}/dependency") is None
    assert repos.account_for_github_owner(owner) is None
    assert repo_accounts.resolution_snapshot()["unmapped_owner_fallback"] is False


def test_preferred_account_keeps_repository_exceptions_local(monkeypatch):
    registry = repos.ReposRegistry(
        account_map={"github.com:*": "operator-work", "ServiceOrg": "service-account"},
        repos={
            "dependency": repos.RepoEntry(
                "dependency", remote="https://github.com/Publisher/dependency",
                account="operator-public",
            ),
            "sibling": repos.RepoEntry(
                "sibling", remote="https://github.com/Publisher/other",
            ),
            "work": repos.RepoEntry(
                "work", remote="https://github.com/WorkOrg/product",
            ),
        },
    )
    monkeypatch.setattr(repos, "read_registry", lambda **_: registry)
    monkeypatch.setattr(repos, "find_repo", lambda name: registry.repos.get(name))
    assert repos.account_for_github_slug("Publisher/dependency") == "operator-public"
    assert repos.account_for_github_slug("Publisher/other") == "operator-work"
    assert repos.account_for_github_owner("Publisher") == "operator-work"
    assert repos.account_for_github_slug("WorkOrg/product") == "operator-work"
    assert repos.account_for_github_slug("ServiceOrg/tool") == "service-account"
    assert repos.account_for_github_slug(
        "WorkOrg/product", configured_only=True,
    ) == "operator-work"
    snapshot = repo_accounts.resolution_snapshot()
    assert snapshot["default_account"] == "operator-work"
    assert snapshot["owner_accounts"]["publisher"] == "operator-work"
    assert snapshot["repository_accounts"]["publisher/dependency"] == "operator-public"
    assert snapshot["repository_accounts"]["publisher/other"] == "operator-work"


def test_default_account_command_roundtrips_without_owner_inference(tmp_path, monkeypatch):
    registry_path = tmp_path / "repos.yaml"
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry_path)
    assert repos_cli.cmd_repos_dispatch(["account", "default", "operator-work"]) == 0
    assert repos.read_registry(strict=True).default_account == "operator-work"
    assert repos.account_for_github_slug("Publisher/dependency") == "operator-work"
    repos.set_account_map("GITHUB.COM:*", "operator-work")
    assert repos.read_registry(strict=True).default_account == "operator-work"
    assert repos_cli.cmd_repos_dispatch(["account", "default", "--unset"]) == 0
    assert repos.read_registry(strict=True).default_account == ""
    assert repos.account_for_github_slug("Publisher/dependency") is None


@pytest.mark.parametrize("login", ["1234", "true", "null", "yes"])
def test_scalar_shaped_accounts_roundtrip_as_strings(tmp_path, monkeypatch, login):
    registry_path = tmp_path / "repos.yaml"
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry_path)
    assert repos_cli.cmd_repos_dispatch(["account", "default", login]) == 0
    registry = repos.read_registry(strict=True)
    assert registry.default_account == login
    registry.account_map["1234"] = login
    registry.repos["exception"] = repos.RepoEntry(
        "exception", remote="https://github.com/Publisher/tool",
        account=login, copilot_account=login,
    )
    repos.write_registry(registry)
    loaded = repos.read_registry(strict=True)
    assert loaded.account_map["1234"] == login
    assert loaded.repos["exception"].account == login
    assert loaded.repos["exception"].copilot_account == login
    assert repos_cli.cmd_repos_dispatch(["account", "default", "--unset"]) == 0
    assert repos.read_registry(strict=True).default_account == ""


def test_conflicting_preferred_key_case_variants_refuse_resolution(tmp_path, monkeypatch):
    registry_path = tmp_path / "repos.yaml"
    registry_path.write_text(yaml.safe_dump({"account_map": {
        "github.com:*": "first-operator", "GITHUB.COM:*": "other-operator",
    }}), encoding="utf-8")
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry_path)
    with pytest.raises(repos.RepositoryResolutionError, match="conflicting preferred"):
        repos.account_for_github_slug("Publisher/dependency")


def test_remote_account_does_not_mix_entry_and_preference_snapshots(monkeypatch):
    first = repos.ReposRegistry(
        account_map={"github.com:*": "first-operator"},
        repos={"dependency": repos.RepoEntry(
            "dependency", remote="https://github.com/Publisher/tool",
        )},
    )
    second = repos.ReposRegistry(
        account_map={"github.com:*": "later-operator"},
        repos={"dependency": repos.RepoEntry(
            "dependency", remote="https://github.com/Publisher/tool", account="repo-exception",
        )},
    )
    reads = []

    def read(**kwargs):
        reads.append(kwargs)
        return first if len(reads) == 1 else second

    monkeypatch.setattr(repos, "read_registry", read)
    assert repo_accounts.account_for_remote("https://github.com/Publisher/tool") == "first-operator"
    assert reads == [{"strict": True}]


@pytest.mark.parametrize("preferred", [12, [], "two accounts"])
def test_invalid_preferred_policy_is_not_an_ambient_fallback(tmp_path, monkeypatch, preferred):
    registry_path = tmp_path / "repos.yaml"
    registry_path.write_text(yaml.safe_dump({
        "repos": {}, "account_map": {"github.com:*": preferred},
    }), encoding="utf-8")
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: registry_path)
    with pytest.raises(repos.RepositoryResolutionError):
        repos.account_for_github_slug("Publisher/dependency")


def test_preferred_policy_crosses_the_actual_cli_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    path = repos._repos_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"repos": {
        "work": {"remote": "https://github.com/WorkOrg/product"},
        "dependency": {
            "remote": "https://github.com/Publisher/tool", "account": "operator-public",
        },
        "sibling": {"remote": "https://github.com/Publisher/other"},
    }}), encoding="utf-8")
    for args in (
        ["repos", "account", "default", "operator-work"],
        ["repos", "account", "list", "--json", "--with-resolutions"],
    ):
        # Match the metadata consumer; the test runner contains these CLI children.
        result = subprocess.run(
            [sys.executable, "-m", "agent_worktrees", *args], timeout=10,
            capture_output=True, text=True, **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["default_account"] == "operator-work"
    assert snapshot["repository_accounts"]["publisher/tool"] == "operator-public"
    assert snapshot["repository_accounts"]["publisher/other"] == "operator-work"
    assert snapshot["repository_accounts"]["workorg/product"] == "operator-work"
    assert snapshot["unmapped_owner_fallback"] is False
    assert snapshot["account_map"]["github.com:*"] == "operator-work"


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
    assert snapshot["owner_accounts"] == {"example": "mapped", "other": "other"}
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
    assert snapshot["repository_accounts"]["Alias"] is None
    assert "alias" not in snapshot["repository_accounts"]
    assert snapshot["unmapped_owner_fallback"] is False


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
        assert snapshot["owner_accounts"][f"owner-{i}"] == f"mapped-{i}"


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
