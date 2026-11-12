"""Passive local profile selection cannot synthesize Gateway or forge authority."""

import sys
from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests_service.repository_config import RepositoryConfig


def selection(root: Path) -> dict[str, Any]:
    return {
        "protocol": "agent-pull-requests.repository-cache/v1",
        "socket": str(root / "local.sock"),
        "peers": [{"uid": 1000, "principal": "reader"}],
        "repositories": [{
            "target": "repo", "identity": {
                "namespace": "test", "provider": "gitea", "endpoint": "https://forge.example",
                "account": "reader", "repo": "owner/repo",
            },
            "prefix": [sys.executable], "credential_command": [sys.executable, "-m", "fixture"],
            "source_config": str(root / "native.yaml"),
        }],
        "bindings": [{"principal": "reader", "target": "repo", "consumer": "consumer"}],
    }


@pytest.mark.skipif(sys.platform != "linux", reason="Linux peer credential profile")
def test_local_selection_is_passive_and_uses_full_identity(tmp_path: Path) -> None:
    root = tmp_path / "state"
    config = RepositoryConfig.parse(selection(root), "test", root)
    assert not root.exists()
    identity = config.identities[0][1]
    assert config.observations[0].account == identity.key
    assert config.bindings[0].scope.account == identity.key
    assert config.peers == ((1000, "reader"),)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux peer credential profile")
@pytest.mark.parametrize("field,value", [
    ("protocol", "unknown"), ("socket", "relative"), ("peers", []),
    ("repositories", []), ("bindings", []), ("gateway", "forged"),
])
def test_local_selection_strict_fields(tmp_path: Path, field: str, value: object) -> None:
    root = tmp_path / "state"
    raw = selection(root)
    raw[field] = value
    with pytest.raises((ValueError, TypeError)):
        RepositoryConfig.parse(raw, "test", root)
    assert not root.exists()
