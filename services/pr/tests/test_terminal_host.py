"""Explicit standalone terminal selection and fresh-only lifecycle admission."""

from __future__ import annotations

import json
import time
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
else:
    from test_host import Configuration, configuration as configuration, workspace_config
from agent_pull_requests_service.config import load_config
from agent_pull_requests_service import terminal
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.generation_cache import CacheUnavailable


def select(configuration: Configuration) -> Configuration:
    path, value = workspace_config(configuration)
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize("selected", [None, 1, "true", {}, []])
def test_terminal_selection_requires_explicit_boolean(
    configuration: Configuration, selected: object,
) -> None:
    path, value = select(configuration)
    value["workspaces"]["terminal_reads"] = selected
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="JSON boolean"):
        load_config(path)
    assert not Path(value["root"]).exists()


@pytest.mark.parametrize("field,value", [("preparation", {}), ("comment_mappings", True)])
def test_terminal_reader_cannot_enable_preparation_or_mapping(
    configuration: Configuration, field: str, value: object,
) -> None:
    path, document = select(configuration)
    document["workspaces"]["bindings"][0][field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_config(path)
    assert not Path(document["root"]).exists()


def test_optional_terminal_core_is_required_only_for_explicit_selection(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import workspace_terminal_read

    path, value = select(configuration)
    monkeypatch.setattr(workspace_terminal_read, "contract", lambda: {"effects": "none"})
    with pytest.raises(ValueError, match="core unsupported"):
        load_config(path)
    assert not Path(value["root"]).exists()
    value["workspaces"]["terminal_reads"] = False
    path.write_text(json.dumps(value))
    baseline = load_config(path)
    baseline.provision()
    assert "terminal_retention_version" not in json.loads(baseline.store().path.read_bytes())


def test_fresh_terminal_provision_restart_and_legacy_rollback_refusal(
    configuration: Configuration,
) -> None:
    path, value = select(configuration)
    config = load_config(path)
    assert not config.root.exists()
    config.provision()
    document = json.loads(config.store().path.read_bytes())
    assert document["version"] == 3
    assert all(document[name + "_version"] == 1 for name in (
        "terminal_retention", "terminal_recovery", "terminal_cache_retirement",
    ))
    original = config.store().path.read_bytes()
    successor = load_config(path)
    assert successor.workspaces is not None
    operations = successor.workspaces.operations(successor.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    operations.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert operations.retention_target is not None
    receipt = config.store().execute(
        config.bindings[0].scope, "register", {
            "request_id": "terminal-watch", "issued_at": time.time(),
            "number": 42, "until": ("closed",), "deadline_at": time.time() + 60,
        }, expires_at=int(time.time()) + 60,
    )
    original = config.store().path.read_bytes()
    target = operations.retention_target(
        {**operations.scope, "number": 42},
        expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )
    assert (target.account, target.repo, target.number) == ("account-a", "example/repository", 42)
    assert target.registration_ids == frozenset({receipt["registration_id"]})
    with pytest.raises(ValueError, match="fresh dedicated root"):
        successor.provision()
    value["workspaces"]["terminal_reads"] = False
    path.write_text(json.dumps(value))
    legacy = load_config(path)
    with pytest.raises(ReplayStoreInvalid):
        legacy.store().observation_targets(expires_at=int(time.time()) + 60)
    assert config.store().path.read_bytes() == original


@pytest.mark.parametrize("partial", [False, True])
def test_terminal_serve_never_adopts_or_repairs_and_failed_provision_preserves_progress(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, partial: bool,
) -> None:
    path, _ = select(configuration)
    config = load_config(path)
    if partial:
        original_adopt = terminal.adopt

        def interrupted(store: ReplayStore, checkpoint: Callable[[], None], expires_at: int) -> None:
            original_adopt(store, checkpoint, expires_at)
            raise OSError("synthetic interruption before cache provisioning")

        monkeypatch.setattr(terminal, "adopt", interrupted)
        with pytest.raises(OSError):
            config.provision()
    else:
        config.store().provision(conversations=True)
    original = config.store().path.read_bytes()
    assert config.workspaces is not None
    operations = config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    with pytest.raises((ReplayStoreInvalid, CacheUnavailable)):
        operations.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    with pytest.raises(ValueError, match="fresh dedicated root"):
        config.provision()
    assert config.store().path.read_bytes() == original
    assert not (config.root / "review-cache").exists()
