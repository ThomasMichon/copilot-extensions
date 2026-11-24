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


@pytest.mark.skipif(sys.platform != "linux", reason="Linux peer credential profile")
@pytest.mark.parametrize("protocol", [None, False, True, "", "unknown"])
def test_batch_selection_is_not_a_permissive_boolean_or_ambient_provider(
    tmp_path: Path, protocol: object,
) -> None:
    root = tmp_path / "state"
    raw = selection(root)
    raw["repositories"][0]["snapshot_batch"] = protocol
    with pytest.raises(ValueError):
        RepositoryConfig.parse(raw, "test", root)
    assert not root.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux peer credential profile")
def test_batch_selection_retains_explicit_repository_and_credential_binding(tmp_path: Path) -> None:
    root = tmp_path / "state"
    raw = selection(root)
    entry = raw["repositories"][0]
    entry["identity"].update(provider="github", endpoint="https://api.github.com")
    entry["snapshot_batch"] = "agent-worktrees.pr-snapshot-batch/v1"
    configured = RepositoryConfig.parse(raw, "test", root).observations[0]
    assert configured.snapshot_batch == entry["snapshot_batch"]
    assert configured.inventory_config == entry["source_config"]
    assert configured.credential_command == tuple(entry["credential_command"])
    assert not root.exists()
