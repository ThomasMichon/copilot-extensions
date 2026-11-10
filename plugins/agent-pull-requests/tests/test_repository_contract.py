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
