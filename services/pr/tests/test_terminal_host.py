"""Explicit standalone terminal selection and fresh-only lifecycle admission."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
else:
    from test_host import Configuration, configuration as configuration, workspace_config
from agent_pull_requests_service.config import load_config
from agent_pull_requests_service import terminal
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.generation_cache import CacheUnavailable
from agent_pull_requests.workspace_operations import WorkspaceOperations


def select(configuration: Configuration) -> Configuration:
    path, value = workspace_config(configuration)
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    return path, value


def mapping_selection(configuration: Configuration) -> Configuration:
    path, value = select(configuration)
    value["workspaces"]["terminal_mapping_reads"] = True
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize("selected", [None, 0, 1, "true", {}, []])
def test_terminal_mapping_selection_requires_boolean(
    configuration: Configuration, selected: object,
) -> None:
    path, value = mapping_selection(configuration)
    value["workspaces"]["terminal_mapping_reads"] = selected
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="JSON boolean"):
        load_config(path)
    assert not Path(value["root"]).exists()


def test_terminal_mapping_requires_terminal_reader(configuration: Configuration) -> None:
    path, value = mapping_selection(configuration)
    value["workspaces"].pop("terminal_reads")
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="requires terminal_reads"):
        load_config(path)
    assert not Path(value["root"]).exists()


@pytest.mark.parametrize("support", ["absent", "invalid", "boolean-version", "failed"])
def test_terminal_mapping_core_skew_refuses_selection_without_breaking_prior_host(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, support: str,
) -> None:
    from agent_pull_requests import workspace_terminal_mapping
    from agent_pull_requests_service import config as configuration_module

    path, value = mapping_selection(configuration)
    contract = workspace_terminal_mapping.contract()
    if support == "absent":
        import builtins
        original_import = builtins.__import__

        def absent(name: str, *args: Any, **kwargs: Any) -> Any:
            importing = args[0] if args else kwargs.get("globals")
            if name == "agent_pull_requests.workspace_terminal_mapping" and isinstance(
                importing, dict,
            ) and importing.get("__name__") == terminal.__name__:
                raise ImportError("synthetic optional provider unavailable")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", absent)
    elif support == "failed":
        def failed() -> dict[str, Any]:
            raise RuntimeError("synthetic optional provider unavailable")
        monkeypatch.setattr(workspace_terminal_mapping, "contract", failed)
    else:
        contract["terminal_comment_mapping_version" if support == "boolean-version" else "effects"] = (
            True if support == "boolean-version" else "write"
        )
        monkeypatch.setattr(workspace_terminal_mapping, "contract", lambda: contract)
    with pytest.raises(ValueError, match="core unsupported"):
        load_config(path)
    assert not Path(value["root"]).exists()
    value["workspaces"]["terminal_mapping_reads"] = False
    path.write_text(json.dumps(value))
    prior_store, prior_operations = ReplayStore, WorkspaceOperations

    def store(*args: Any, **kwargs: Any) -> ReplayStore:
        assert "terminal_comment_mapping" not in kwargs
        return prior_store(*args, **kwargs)

    def operations(*args: Any, **kwargs: Any) -> WorkspaceOperations:
        assert "terminal_mapping_reads" not in kwargs
        return prior_operations(*args, **kwargs)

    monkeypatch.setattr(configuration_module, "ReplayStore", store)
    monkeypatch.setattr(configuration_module, "WorkspaceOperations", operations)
    config = load_config(path)
    config.provision()
    assert config.workspaces is not None
    config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ].validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)


def test_terminal_mapping_fresh_adoption_restart_and_selection_rollback(
    configuration: Configuration,
) -> None:
    from agent_pull_requests.workspace_terminal_mapping import FIELD, contract

    path, value = mapping_selection(configuration)
    config = load_config(path)
    config.provision()
    before = config.store().path.read_bytes()
    document = json.loads(before)
    assert document["terminal_comment_mapping_version"] == 1
    assert document["review_comment_mapping_version"] == 1 and document["review_intent_version"] == 2
    successor = load_config(path)
    assert successor.workspaces is not None
    operations = successor.workspaces.operations(successor.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    operations.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    capabilities = operations.execute(
        "capabilities", {}, expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )
    assert capabilities[FIELD] == contract()
    assert not operations.write_available
    assert not {"prepare", "allocate", "submit", "receipt", "observe-comment"} & set(capabilities["operations"])
    operations.terminal_mapping_reads = False
    assert FIELD not in operations.execute(
        "capabilities", {}, expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )
    value["workspaces"]["terminal_mapping_reads"] = False
    path.write_text(json.dumps(value))
    with pytest.raises(ReplayStoreInvalid):
        load_config(path).store().observation_targets(expires_at=int(time.time()) + 60)
    with pytest.raises(ValueError, match="fresh dedicated root"):
        successor.provision()
    assert config.store().path.read_bytes() == before


@pytest.mark.parametrize("partial", [False, True])
def test_terminal_mapping_serve_never_adopts_or_repairs(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, partial: bool,
) -> None:
    from agent_pull_requests.terminal_comment_mapping import TerminalCommentMappingStore
    from agent_pull_requests.workspace_terminal_mapping import FIELD

    path, _ = mapping_selection(configuration)
    config = load_config(path)
    assert config.workspaces is not None
    if partial:
        def interrupted(*args: Any, **kwargs: Any) -> None:
            raise OSError("synthetic interruption before terminal mapping adoption")
        monkeypatch.setattr(TerminalCommentMappingStore, "adopt", interrupted)
        with pytest.raises(OSError):
            config.provision()
    else:
        replace(config, workspaces=replace(
            config.workspaces, terminal_mapping_reads=False,
        )).provision()
    before = config.store().path.read_bytes()
    operations = config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    if partial:
        with pytest.raises(CacheUnavailable):
            operations.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    else:
        operations.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
        assert FIELD not in operations.execute(
            "capabilities", {}, expires_at=int(time.time()) + 60, checkpoint=lambda: None,
        )
    with pytest.raises(ValueError, match="fresh dedicated root"):
        config.provision()
    assert config.store().path.read_bytes() == before


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
