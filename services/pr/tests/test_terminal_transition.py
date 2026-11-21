"""Retained reader adoption preserves data and excludes the existing service."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from single_instance_lease import SingleInstance

from agent_pull_requests.generation_cache import CacheInvalid
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests.watch_replay_store import MAX_OWNER_BYTES, ReplayStore, ReplayStoreInvalid
from agent_pull_requests_service import terminal, terminal_transition
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.config import HostConfig, load_config

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration, workspace_config
else:
    from test_host import Configuration, configuration as configuration, workspace_config


def retained(configuration: Configuration) -> tuple[Path, dict[str, Any], HostConfig]:
    path, value = workspace_config(configuration)
    baseline = load_config(path)
    baseline.provision()
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    return path, value, load_config(path)


def cache_bytes(config: HostConfig) -> dict[str, bytes]:
    assert config.workspaces is not None
    root = config.workspaces.cache(config.store()).root
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file() and path.name != "cache.lock"
    }


def command(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_pull_requests_service", "adopt-terminal", "--config", str(path)],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )


def generation(title: str | None = None) -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[3]
        / "plugins/agent-pull-requests/docs/review-evidence.example.json"
    )
    evidence = json.loads(path.read_text())
    if title is not None:
        evidence["sections"]["pull_request"]["data"]["title"] = title
    return cast(dict[str, Any], build_generation(evidence)["generation"])


def test_actual_cli_preserves_empty_cache_and_pending_replay(
    configuration: Configuration,
) -> None:
    path, _, selected = retained(configuration)
    assert selected.workspaces is not None
    baseline = load_config(configuration[0])
    store = baseline.store()
    cache = selected.workspaces.cache(store)
    expires_at = int(time.time()) + 60
    scope = selected.bindings[0].scope
    receipt = store.execute(scope, "register", {
        "request_id": "retained-reader", "issued_at": time.time(),
        "number": 42, "until": ("head_changed",), "deadline_at": time.time() + 60,
    }, expires_at=expires_at)
    target = ObservationTarget(
        scope.account, scope.repo, 42, frozenset({receipt["registration_id"]}),
    )
    store.observe(target, PRSnapshot(head_sha="a" * 40), expires_at=expires_at)
    store.observe(target, PRSnapshot(head_sha="b" * 40), expires_at=expires_at)
    pending = store.execute(scope, "pending", {}, expires_at=expires_at)
    assert len(pending["events"]) == 1
    before = json.loads(store.path.read_bytes())
    objects = cache_bytes(selected)
    result = command(path)
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr
    reopened = load_config(path)
    after = json.loads(reopened.store().path.read_bytes())
    for field in ("namespace", "service", "replay", "reviews", "conversations"):
        assert after[field] == before[field]
    assert reopened.store().execute(scope, "pending", {}, expires_at=expires_at) == pending
    assert cache_bytes(reopened) == objects
    assert not list(cache.generations.iterdir()) and not list(cache.targets.iterdir())
    assert all(after[field + "_version"] == 1 for field in (
        "terminal_retention", "terminal_recovery", "terminal_cache_retirement",
    ))
    assert reopened.workspaces is not None
    operations = reopened.workspaces.operations(reopened.store(), lambda: None, 60)
    operations[("caller-a", "repository-a")].validate_storage(
        expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )
    assert command(path).returncode == 0
    assert json.loads(reopened.store().path.read_bytes()) == after
    assert main(["provision", "--config", str(path)]) == 1


def test_unknown_active_operation_refuses_and_preserves_original_workspace_inspection(
    configuration: Configuration,
) -> None:
    path, value, _ = retained(configuration)
    selected_generation = generation()
    value["workspaces"]["terminal_reads"] = False
    value["workspaces"]["bindings"][0]["scope"] = {
        name: selected for name, selected in selected_generation["scope"].items() if name != "number"
    }
    path.write_text(json.dumps(value))
    baseline = load_config(path)
    store = baseline.store()
    expires_at = int(time.time()) + 60
    intent = {
        "schema": "agent-pull-requests.review-intent", "version": 1,
        "operation_id": "original-unknown-verdict", "scope": selected_generation["scope"],
        "generation_id": selected_generation["id"], "source_id": "a" * 64,
        "revision": {
            name: selected_generation["revision"][name] for name in ("head_sha", "common_sha", "iteration")
        },
        "actions": [{"id": "verdict", "kind": "verdict", "verdict": "APPROVED"}],
    }
    store.admit_review(intent, expires_at=expires_at)
    store.begin_review_action(intent["operation_id"], intent["scope"], "verdict", expires_at=expires_at)
    assert baseline.workspaces is not None
    operations = baseline.workspaces.operations(store, lambda: None, 60)[("caller-a", "repository-a")]
    payload = {"number": intent["scope"]["number"], "operation_id": intent["operation_id"]}
    original = operations.execute("inspect", payload, expires_at=expires_at, checkpoint=lambda: None)
    before = store.path.read_bytes(), cache_bytes(baseline)
    assert json.loads(before[0])["reviews"][0]["actions"][0]["state"] == "unknown"
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    assert command(path).returncode == 1
    assert (store.path.read_bytes(), cache_bytes(baseline)) == before
    assert operations.execute("inspect", payload, expires_at=expires_at, checkpoint=lambda: None) == original


def test_actual_cli_refuses_competing_service_lease_without_mutation(
    configuration: Configuration,
) -> None:
    path, _, config = retained(configuration)
    before = config.store().path.read_bytes(), cache_bytes(config)
    with SingleInstance(config.root, service="agent-pull-requests-watch") as lease:
        result = command(path)
        assert result.returncode == 1 and lease.held
        assert "state retained" in result.stderr and str(config.root) not in result.stderr
        assert (config.store().path.read_bytes(), cache_bytes(config)) == before
    assert command(path).returncode == 0


def test_actual_service_failed_drain_keeps_transition_excluded(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_service

    path, _, config = retained(configuration)
    entered, release = threading.Event(), threading.Event()

    class Connector:
        def __init__(self, **kwargs: Any) -> None:
            self.connected = asyncio.Event()

        async def run(self, stop: asyncio.Event) -> None:
            self.connected.set()
            try:
                await stop.wait()
            finally:
                self.connected.clear()

    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)

    async def scenario() -> None:
        async def access() -> str:
            return "synthetic"
        store = config.store()
        service = watch_service.WatchService(
            store, fleet=config.fleet, gateway=config.gateway, slot=config.slot,
            bindings=config.bindings, observations=config.observations,
            gateway_origin=config.gateway_origin, access_token_callback=access,
            proof_key=object(), authority=lambda: int(time.time()) + 60,
            checkpoint=lambda: None, interval=0.01,
        )
        original = store.observation_targets

        def blocked(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            assert release.wait(3), "test must release admitted disk work"
            return original(*args, **kwargs)

        await service.start()
        monkeypatch.setattr(store, "observation_targets", blocked)
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            with pytest.raises(TimeoutError):
                await service.close(timeout=0.01)
            assert service.status()["writer_held"]
            before = store.path.read_bytes()
            result = await asyncio.to_thread(command, path)
            assert result.returncode == 1
            assert store.path.read_bytes() == before and service.status()["writer_held"]
        finally:
            release.set()
            await service.close(timeout=3)
        assert not service.status()["writer_held"]
        assert (await asyncio.to_thread(command, path)).returncode == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["binding", "cache", "capacity", "mapping"])
def test_preflight_refusals_do_not_partially_adopt(
    configuration: Configuration, failure: str,
) -> None:
    path, value, config = retained(configuration)
    assert config.workspaces is not None
    if failure == "binding":
        value["namespace"] = "another-installation"
    elif failure == "cache":
        config.workspaces.cache(config.store()).descriptor.unlink()
    elif failure == "capacity":
        def reserve(document: dict[str, Any]) -> None:
            capacities = document["capacities"]
            capacities["replay_bytes"] = (
                MAX_OWNER_BYTES - 4096 - capacities["review_bytes"] - capacities["conversation_bytes"]
            )
        config.store()._review_transaction(reserve, expires_at=int(time.time()) + 60)
    else:
        value["workspaces"]["terminal_mapping_reads"] = True
    path.write_text(json.dumps(value))
    before = config.store().path.read_bytes(), cache_bytes(config)
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert (config.store().path.read_bytes(), cache_bytes(config)) == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_interrupted_adoption_retains_complete_progress_and_explicit_retry(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    from agent_pull_requests import watch_replay_store

    path, _, config = retained(configuration)
    before = json.loads(config.store().path.read_bytes())
    objects = cache_bytes(config)
    with monkeypatch.context() as patch:
        if uncertain:
            original = watch_replay_store._atomic_write_json

            def lost_reply(*args: Any, **kwargs: Any) -> None:
                original(*args, **kwargs)
                raise OSError("synthetic lost replace result")
            patch.setattr(watch_replay_store, "_atomic_write_json", lost_reply)
        else:
            def interrupted(*args: Any, **kwargs: Any) -> None:
                raise OSError("synthetic interruption before recovery adoption")
            patch.setattr(TerminalRecoveryStore, "adopt", interrupted)
        assert main(["adopt-terminal", "--config", str(path)]) == 1
    partial = json.loads(config.store().path.read_bytes())
    assert partial["terminal_retention_version"] == 1
    assert "terminal_recovery_version" not in partial
    for field in ("replay", "reviews", "conversations"):
        assert partial[field] == before[field]
    assert cache_bytes(config) == objects
    with pytest.raises(ReplayStoreInvalid):
        ReplayStore(config.root, config.namespace, service_identity=config.store().service_identity).observation_targets(
            expires_at=int(time.time()) + 60,
        )
    assert main(["adopt-terminal", "--config", str(path)]) == 0
    assert config.store().observation_targets(expires_at=int(time.time()) + 60) == ()
    assert cache_bytes(config) == objects


def test_expired_lifecycle_refuses_before_adoption(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _, config = retained(configuration)
    before = config.store().path.read_bytes()
    instants = iter((0.0, float(config.operation_seconds + 1)))
    monkeypatch.setattr(terminal_transition, "time", SimpleNamespace(
        monotonic=lambda: next(instants), time=time.time,
    ))
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert config.store().path.read_bytes() == before


def test_retained_mapping_requires_and_preserves_existing_mapping_prerequisites(
    configuration: Configuration,
) -> None:
    from agent_pull_requests.review_comment_mapping import CommentMappingStore

    path, value, config = retained(configuration)
    expires_at = int(time.time()) + 60
    store = config.store()
    store.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires_at)
    CommentMappingStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires_at)
    value["workspaces"]["terminal_mapping_reads"] = True
    path.write_text(json.dumps(value))
    assert main(["adopt-terminal", "--config", str(path)]) == 0
    after = json.loads(load_config(path).store().path.read_bytes())
    assert after["terminal_comment_mapping_version"] == 1
    assert after["review_intent_version"] == 2 and after["review_comment_mapping_version"] == 1
    assert after["reviews"] == []
    before_rollback = store.path.read_bytes()
    value["workspaces"]["terminal_mapping_reads"] = False
    path.write_text(json.dumps(value))
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert store.path.read_bytes() == before_rollback


def test_missing_volume_and_unselected_profile_never_provision(configuration: Configuration) -> None:
    path, value = workspace_config(configuration)
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    root = Path(value["root"])
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert not root.exists()
    value["workspaces"]["terminal_reads"] = False
    path.write_text(json.dumps(value))
    config = load_config(path)
    config.provision()
    before = config.store().path.read_bytes()
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert config.store().path.read_bytes() == before


@pytest.mark.parametrize("linked", ["symbolic", "hard"])
def test_unsafe_service_lock_never_overwrites_retained_bytes(
    configuration: Configuration, linked: str,
) -> None:
    path, _, config = retained(configuration)
    store = config.store()
    lease = SingleInstance(config.root, service="agent-pull-requests-watch")
    before = store.path.read_bytes()
    try:
        if linked == "symbolic":
            lease.lock_path.symlink_to(store.path)
        else:
            os.link(store.path, lease.lock_path)
    except OSError as error:
        pytest.skip(f"native link creation unavailable: {type(error).__name__}")
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert store.path.read_bytes() == before


def test_native_state_coexistence_refuses_before_adoption(configuration: Configuration) -> None:
    path, _, config = retained(configuration)
    native = config.root / "watch-subscriptions.json"
    native.write_text("retained native state")
    before = config.store().path.read_bytes(), native.read_bytes()
    assert main(["adopt-terminal", "--config", str(path)]) == 1
    assert (config.store().path.read_bytes(), native.read_bytes()) == before


@pytest.mark.parametrize("contents", ["generation", "pointer"])
def test_populated_cache_refuses_without_excluding_original_workspace_reads(
    configuration: Configuration, contents: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal, "retained_cache_supported", lambda: False)
    path, value, _ = retained(configuration)
    selected = generation()
    value["workspaces"]["terminal_reads"] = False
    value["workspaces"]["bindings"][0]["scope"] = {
        name: selected_value for name, selected_value in selected["scope"].items() if name != "number"
    }
    path.write_text(json.dumps(value))
    baseline = load_config(path)
    assert baseline.workspaces is not None
    cache = baseline.workspaces.cache(baseline.store())
    cache.publish(selected, expected_current=None)
    operations = baseline.workspaces.operations(baseline.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    payload = {"number": selected["scope"]["number"]}
    original = operations.execute(
        "lookup", payload, expires_at=int(time.time()) + 60, checkpoint=lambda: None,
    )
    if contents == "pointer":
        (cache.generations / (selected["id"] + ".json")).unlink()
    value["workspaces"]["terminal_reads"] = True
    path.write_text(json.dumps(value))
    config = load_config(path)
    before = config.store().path.read_bytes(), cache_bytes(config)
    with pytest.raises(CacheInvalid, match="populated cache"):
        terminal_transition.adopt(config)
    assert (config.store().path.read_bytes(), cache_bytes(config)) == before
    if contents == "generation":
        assert operations.execute(
            "lookup", payload, expires_at=int(time.time()) + 60, checkpoint=lambda: None,
        ) == original


def test_deadline_expires_during_cache_scan_without_visiting_later_entries(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal, "retained_cache_supported", lambda: False)
    _, _, config = retained(configuration)
    assert config.workspaces is not None
    cache = config.workspaces.cache(config.store())
    before = config.store().path.read_bytes(), cache_bytes(config)
    clock = [0.0]
    visited: list[Path] = []
    original = os.scandir

    def scan(path: Path) -> Any:
        if path == cache.generations:
            visited.append(path)
            clock[0] = config.operation_seconds + 1.0
        return original(path)

    monkeypatch.setattr(terminal_transition, "os", SimpleNamespace(getpid=os.getpid, scandir=scan))
    monkeypatch.setattr(terminal_transition, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=time.time,
    ))
    with pytest.raises(ValueError, match="lifecycle"):
        terminal_transition.adopt(config)
    assert len(visited) == 1
    assert (config.store().path.read_bytes(), cache_bytes(config)) == before
