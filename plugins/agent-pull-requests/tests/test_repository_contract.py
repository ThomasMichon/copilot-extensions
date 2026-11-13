"""Full backend identity cannot collapse into an account/repository label."""

from dataclasses import replace

import pytest

from agent_pull_requests.repository_contract import RepositoryIdentity


def test_full_key_distinguishes_all_backend_dimensions():
    identity = RepositoryIdentity("namespace", "github", "https://api.github.com", "reader", "a/b")
    peers = [
        replace(identity, namespace="other"),
        replace(identity, provider="gitea"),
        replace(identity, endpoint="https://forge.example"),
        replace(identity, account="other"),
        replace(identity, repo="a/c"),
    ]
    assert len({identity.key, *(peer.key for peer in peers)}) == 6
    assert RepositoryIdentity.parse(identity.document()) == identity


@pytest.mark.parametrize("change", [
    {"provider": "unknown"}, {"endpoint": "http://forge.example"},
    {"endpoint": "https://user:secret@forge.example"}, {"endpoint": "https://forge.example/"},
    {"endpoint": "https://forge.example?q=1"}, {"repo": "../b"}, {"account": ""},
])
def test_invalid_identity_is_not_admitted(change):
    value = RepositoryIdentity(
        "namespace", "github", "https://api.github.com", "reader", "a/b",
    ).document() | change
    with pytest.raises(ValueError):
        RepositoryIdentity.parse(value)


@pytest.mark.parametrize("repo", ["My Project/repository", "Project/répertoire", "Project/repo name"])
def test_ado_repository_names_preserve_native_scope_without_relaxing_other_drivers(repo):
    identity = RepositoryIdentity("namespace", "azure-devops", "https://dev.azure.com/example", "reader", repo)
    assert RepositoryIdentity.parse(identity.document()) == identity
    for provider in ("github", "gitea"):
        with pytest.raises(ValueError):
            replace(identity, provider=provider)


@pytest.mark.parametrize("repo", ["Project/..", "/repo", "a/b/c", "Project/repo\x7f", "a/" + "b" * 255])
def test_ado_repository_scope_rejects_native_invalid_names(repo):
    with pytest.raises(ValueError):
        RepositoryIdentity("namespace", "azure-devops", "https://dev.azure.com/example", "reader", repo)
