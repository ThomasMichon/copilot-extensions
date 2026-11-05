"""Standalone configuration and host controls; no production authority or credentials."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_pull_requests_service import hosting
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.config import load_config
from fleet_manager.gateway_service_handler import RequestDisconnected
from agent_pull_requests.generation_cache import CacheUnavailable
from agent_pull_requests.native_read import NativeReadHandler
from agent_pull_requests.watch_replay_store import ReplayStoreInvalid

Configuration = tuple[Path, dict[str, Any]]

@pytest.fixture
def configuration(tmp_path: Path) -> Configuration:
    value: dict[str, Any] = {
        "schema": "agent-pull-requests.service", "version": 1,
        "root": str(tmp_path / "state"), "namespace": "installation-a",
        "fleet": "fleet-a", "gateway": "gateway-a", "slot": "pr-watch",
        "gateway_origin": "https://localhost:9443",
        "proof_key_file": str(tmp_path / "proof.pem"),
        "access_token_file": str(tmp_path / "access"),
        "bindings": [{
            "principal": "caller-a", "target": "repository-a", "consumer": "dispatch-a",
            "account": "account-a", "repo": "example/repository",
        }],
        "observations": [{
            "account": "account-a", "repo": "example/repository", "command": sys.executable,
            "prefix": [sys.executable, "-I", "-m", "agent_worktrees"],
            "credential_command": [
                sys.executable, "-I", "-m", "fleet_manager", "credential",
                "--config", str(tmp_path / "worker.json"),
            ],
        }],
    }
    path = tmp_path / "host.json"
    path.write_text(json.dumps(value))
    return path, value


def test_load_is_passive_and_provision_is_separate_and_never_replaces(
    configuration: Configuration,
) -> None:
    path, _ = configuration
    config = load_config(path)
    assert not config.root.exists()
    assert main(["provision", "--config", str(path)]) == 0
    original = config.store().path.read_bytes()
    assert json.loads(original)["service"] == {
        "fleet": config.fleet, "gateway": config.gateway, "slot": config.slot,
    }
    assert main(["provision", "--config", str(path)]) == 1
    assert config.store().path.read_bytes() == original
    assert not config.status_path.exists()


@pytest.mark.parametrize("change", [
    lambda value: value.update(version=True),
    lambda value: value.update(token="must-not-be-accepted"),
    lambda value: value.update(root="relative"),
    lambda value: value.update(interval=0),
    lambda value: value.update(operation_seconds=301),
    lambda value: value.update(drain_timeout=True),
    lambda value: value["observations"][0].pop("credential_command"),
    lambda value: value["observations"][0].update(credential_command=[]),
    lambda value: value["bindings"].append(value["bindings"][0]),
    lambda value: value["observations"].append(value["observations"][0]),
    lambda value: value["observations"][0].update(account="other-account"),
])
def test_config_refuses_ambiguous_or_ambient_configuration(
    configuration: Configuration, change: Callable[[dict[str, Any]], object],
) -> None:
    path, value = configuration
    change(value)
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, TypeError)):
        load_config(path)


def test_config_refuses_duplicate_json_and_oversize(configuration: Configuration) -> None:
    path, _ = configuration
    path.write_text('{"schema":"first","schema":"second"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_config(path)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="byte budget"):
        load_config(path)


def workspace_config(configuration: Configuration) -> Configuration:
    path, value = configuration
    value["workspaces"] = {
        "bindings": [{
            "principal": "caller-a", "target": "repository-a",
            "command": [sys.executable, "-I", "-m", "agent_worktrees.pr_review_evidence_cli"],
            "scope": {
                "provider": "azure-devops", "endpoint": "https://dev.azure.com/example",
                "project_id": "project-id", "repository_id": "repository-id",
                "principal_id": "reviewer-id",
            },
        }],
        "cache_bytes": 4 * 1024 * 1024, "cache_generations": 20,
    }
    path.write_text(json.dumps(value))
    return path, value


def test_optional_workspace_fresh_provision_and_restart_preserve_layout(
    configuration: Configuration,
) -> None:
    path, _ = workspace_config(configuration)
    config = load_config(path)
    assert config.workspaces is not None
    assert not config.root.exists()
    assert main(["provision", "--config", str(path)]) == 0
    store = config.store()
    document = json.loads(store.path.read_bytes())
    assert document["version"] == 3 and document["schema"] == "agent-pull-requests.owner-store"
    original = store.path.read_bytes()
    operations = config.workspaces.operations(store, lambda: None, config.operation_seconds)
    selected = operations[("caller-a", "repository-a")]
    selected.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert selected.write_context is None
    assert isinstance(selected.read_owner, NativeReadHandler)
    assert selected.read_owner.credential_command == config.observations[0].credential_command
    assert selected.read_owner.repo == config.bindings[0].scope.repo
    assert main(["provision", "--config", str(path)]) == 1
    assert config.store().path.read_bytes() == original


def test_optional_workspace_never_migrates_watch_volume_or_repairs_missing_cache(
    configuration: Configuration,
) -> None:
    path, _ = configuration
    assert main(["provision", "--config", str(path)]) == 0
    original = load_config(path).store().path.read_bytes()
    workspace_config(configuration)
    config = load_config(path)
    assert config.workspaces is not None
    selected = config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    with pytest.raises(ReplayStoreInvalid, match="adoption"):
        selected.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert main(["provision", "--config", str(path)]) == 1
    assert config.store().path.read_bytes() == original
    assert not (config.root / "review-cache").exists()

    config = replace(config, root=config.root.with_name("fresh-workspace"))
    assert config.workspaces is not None
    config.store().provision(conversations=True)
    original = config.store().path.read_bytes()
    selected = config.workspaces.operations(config.store(), lambda: None, 60)[
        ("caller-a", "repository-a")
    ]
    with pytest.raises(CacheUnavailable):
        selected.validate_storage(expires_at=int(time.time()) + 60, checkpoint=lambda: None)
    assert config.store().path.read_bytes() == original
    assert not (config.root / "review-cache").exists()


@pytest.mark.parametrize("change", [
    lambda value: value["workspaces"].update(cache_bytes=True),
    lambda value: value["workspaces"].update(cache_generations=0),
    lambda value: value["workspaces"].update(write_context="not-an-owner"),
    lambda value: value["workspaces"]["bindings"][0].update(principal="unadmitted"),
    lambda value: value["workspaces"]["bindings"][0].update(target="unadmitted"),
    lambda value: value["workspaces"]["bindings"][0].update(credential_command=[sys.executable]),
    lambda value: value["workspaces"]["bindings"][0].update(command=["relative"]),
    lambda value: value["workspaces"]["bindings"][0]["scope"].update(provider="github"),
    lambda value: value["workspaces"]["bindings"][0]["scope"].update(number=42),
    lambda value: value["workspaces"]["bindings"].append(value["workspaces"]["bindings"][0]),
])
def test_optional_workspace_config_is_strict_and_bound_to_existing_route(
    configuration: Configuration, change: Callable[[dict[str, Any]], object],
) -> None:
    path, value = workspace_config(configuration)
    change(value)
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, TypeError)):
        load_config(path)


@pytest.mark.parametrize("conversations", [False, True])
def test_actual_host_refuses_incompatible_storage_before_connector_admission(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, conversations: bool,
) -> None:
    from types import SimpleNamespace

    path, _ = workspace_config(configuration)
    config = load_config(path)
    store = config.store()
    store.provision(conversations=conversations)
    original = store.path.read_bytes()

    async def access_token() -> str:
        pytest.fail("connector admitted before workspace storage validation")

    monkeypatch.setattr(hosting, "load_service_credentials", lambda **_kwargs: SimpleNamespace(
        proof_key=object(), access_token=access_token,
    ))
    with pytest.raises((ReplayStoreInvalid, CacheUnavailable)):
        asyncio.run(hosting.run(config))
    assert store.path.read_bytes() == original
    assert not (config.root / "review-cache").exists()


def test_provision_refuses_native_state_root(configuration: Configuration) -> None:
    path, _ = configuration
    config = load_config(path)
    config.root.mkdir()
    native = config.root / "watch-subscriptions.json"
    native.write_text('{"native":"preserve"}')
    assert main(["provision", "--config", str(path)]) == 1
    assert not config.store().path.exists()
    assert native.read_text() == '{"native":"preserve"}'


def test_cli_reports_operation_and_failure_class_without_echoing_references(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "private-reference.json"
    assert main(["provision", "--config", str(path)]) == 1
    diagnostic = capsys.readouterr().err
    assert "provision unavailable (FileNotFoundError)" in diagnostic
    assert str(path) not in diagnostic and path.name not in diagnostic


def test_launch_scope_does_not_read_a_token_or_role_permit(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = hosting.LaunchScope(60)
    assert time.time() < scope.deadline() <= time.time() + 60
    scope.stopping.set()
    with pytest.raises(RequestDisconnected):
        scope.deadline()
    scope = hosting.LaunchScope(60)
    monkeypatch.setattr(os, "getpid", lambda: scope.pid + 1)
    with pytest.raises(RequestDisconnected):
        scope.check()


def test_passive_readiness_refuses_stale_future_malformed_and_oversize(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    assert not hosting.ready(path, now=100)
    value: dict[str, Any] = {
        "schema": "agent-pull-requests.service-status", "version": 1,
        "ready": True, "observed_at": 100, "expires_at": 103,
    }
    hosting._publish(path, value)
    assert hosting.ready(path, now=101)
    assert not hosting.ready(path, now=103)
    assert not hosting.ready(path, now=99)
    value["expires_at"] = 1000
    hosting._publish(path, value)
    assert not hosting.ready(path, now=101)
    path.write_bytes(b"x" * 4097)
    assert not hosting.ready(path)
    assert main(["ready", "--status", str(path)]) == 1


def test_passive_reader_does_not_stop_writer_on_windows_replace_sharing(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    value = {
        "schema": "agent-pull-requests.service-status", "version": 1,
        "ready": True, "observed_at": 100, "expires_at": 103,
    }
    assert hosting._publish(path, value)
    with path.open("rb") as held:
        replaced = hosting._publish(path, dict(value, observed_at=200, expires_at=203))
        assert json.load(held) == value
        assert replaced is (os.name != "nt")
        assert hosting.ready(path, now=201) is (os.name != "nt")
    assert hosting._publish(path, dict(value, observed_at=200, expires_at=203))
    assert hosting.ready(path, now=201)
    assert list(tmp_path.glob("*.tmp")) == []


def test_host_owns_service_and_drains_on_passive_status_failure(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = configuration
    config = load_config(path)
    config.root.mkdir()
    key = ec.generate_private_key(ec.SECP256R1())
    config.proof_key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    config.access_token_file.write_text("synthetic-access")
    events: list[str] = []

    class Service:
        def __init__(self, *_args: object, **kwargs: Any) -> None:
            self.credentials = cast(Callable[[], Awaitable[str]], kwargs["access_token_callback"])
            self.authority = cast(Callable[[], int], kwargs["authority"])
            self.checkpoint = cast(Callable[[], None], kwargs["checkpoint"])
            self.held = False

        async def run(self, stop: asyncio.Event, *, drain_timeout: float) -> None:
            assert drain_timeout == 60
            assert await self.credentials() == "synthetic-access"
            self.checkpoint()
            assert self.authority() > time.time()
            self.held = True
            events.append("started")
            await stop.wait()
            with pytest.raises(RequestDisconnected):
                self.checkpoint()
            events.append("drained")
            self.held = False

        def status(self) -> dict[str, bool]:
            return {"writer_held": self.held, "ready": self.held}

    monkeypatch.setattr(hosting, "WatchService", Service)

    def publish(*_args: object) -> bool:
        assert events == ["started"]
        raise OSError("synthetic readiness storage failure")

    monkeypatch.setattr(hosting, "_publish", publish)
    with pytest.raises(OSError):
        asyncio.run(hosting.run(config))
    assert events == ["started", "drained"]


def test_preset_stop_does_not_load_credentials_or_touch_state(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = configuration
    config = load_config(path)
    monkeypatch.setattr(
        hosting, "load_service_credentials",
        lambda **_kwargs: pytest.fail("credential lookup after known stop"),
    )
    stop = asyncio.Event()
    stop.set()
    asyncio.run(hosting.run(config, stop=stop))
    assert not config.root.exists()


def test_host_does_not_touch_readiness_when_writer_acquisition_fails(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = configuration
    config = load_config(path)
    credentials = type("Credentials", (), {"proof_key": object(), "access_token": lambda: None})()
    monkeypatch.setattr(hosting, "load_service_credentials", lambda **_kwargs: credentials)

    class Service:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("writer held by another owner")

        def status(self) -> dict[str, bool]:
            return {"writer_held": False, "ready": False}

    monkeypatch.setattr(hosting, "WatchService", Service)
    monkeypatch.setattr(hosting, "_publish", lambda *_args: pytest.fail("foreign readiness write"))
    with pytest.raises(RuntimeError):
        asyncio.run(hosting.run(replace(config, drain_timeout=1)))
    assert not config.root.exists()
