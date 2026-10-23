"""Standalone configuration and host controls; no production authority or credentials."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_pull_requests_service import hosting
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.config import load_config
from fleet_manager.gateway_service_handler import RequestDisconnected


@pytest.fixture
def configuration(tmp_path):
    value = {
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


def test_load_is_passive_and_provision_is_separate_and_never_replaces(configuration):
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
def test_config_refuses_ambiguous_or_ambient_configuration(configuration, change):
    path, value = configuration
    change(value)
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, TypeError)):
        load_config(path)


def test_config_refuses_duplicate_json_and_oversize(configuration):
    path, _ = configuration
    path.write_text('{"schema":"first","schema":"second"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_config(path)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="byte budget"):
        load_config(path)


def test_provision_refuses_native_state_root(configuration):
    path, _ = configuration
    config = load_config(path)
    config.root.mkdir()
    native = config.root / "watch-subscriptions.json"
    native.write_text('{"native":"preserve"}')
    assert main(["provision", "--config", str(path)]) == 1
    assert not config.store().path.exists()
    assert native.read_text() == '{"native":"preserve"}'


def test_cli_reports_operation_and_failure_class_without_echoing_references(tmp_path, capsys):
    path = tmp_path / "private-reference.json"
    assert main(["provision", "--config", str(path)]) == 1
    diagnostic = capsys.readouterr().err
    assert "provision unavailable (FileNotFoundError)" in diagnostic
    assert str(path) not in diagnostic and path.name not in diagnostic


def test_launch_scope_does_not_read_a_token_or_role_permit(monkeypatch):
    scope = hosting.LaunchScope(60)
    assert time.time() < scope.deadline() <= time.time() + 60
    scope.stopping.set()
    with pytest.raises(RequestDisconnected):
        scope.deadline()
    scope = hosting.LaunchScope(60)
    monkeypatch.setattr(hosting.os, "getpid", lambda: scope.pid + 1)
    with pytest.raises(RequestDisconnected):
        scope.check()


def test_passive_readiness_refuses_stale_future_malformed_and_oversize(tmp_path):
    path = tmp_path / "status.json"
    assert not hosting.ready(path, now=100)
    value = {
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


def test_passive_reader_does_not_stop_writer_on_windows_replace_sharing(tmp_path):
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


def test_host_owns_service_and_drains_on_passive_status_failure(configuration, monkeypatch):
    path, _ = configuration
    config = load_config(path)
    config.root.mkdir()
    key = ec.generate_private_key(ec.SECP256R1())
    config.proof_key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    config.access_token_file.write_text("synthetic-access")
    events = []

    class Service:
        def __init__(self, *_args, **kwargs):
            self.credentials = kwargs["access_token_callback"]
            self.authority = kwargs["authority"]
            self.checkpoint = kwargs["checkpoint"]
            self.held = False

        async def run(self, stop, *, drain_timeout):
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

        def status(self):
            return {"writer_held": self.held, "ready": self.held}

    monkeypatch.setattr(hosting, "WatchService", Service)

    def publish(*_args):
        assert events == ["started"]
        raise OSError("synthetic readiness storage failure")

    monkeypatch.setattr(hosting, "_publish", publish)
    with pytest.raises(OSError):
        asyncio.run(hosting.run(config))
    assert events == ["started", "drained"]


def test_preset_stop_does_not_load_credentials_or_touch_state(configuration, monkeypatch):
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


def test_host_does_not_touch_readiness_when_writer_acquisition_fails(configuration, monkeypatch):
    path, _ = configuration
    config = load_config(path)
    credentials = type("Credentials", (), {"proof_key": object(), "access_token": lambda: None})()
    monkeypatch.setattr(hosting, "load_service_credentials", lambda **_kwargs: credentials)

    class Service:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            raise RuntimeError("writer held by another owner")

        def status(self):
            return {"writer_held": False, "ready": False}

    monkeypatch.setattr(hosting, "WatchService", Service)
    monkeypatch.setattr(hosting, "_publish", lambda *_args: pytest.fail("foreign readiness write"))
    with pytest.raises(RuntimeError):
        asyncio.run(hosting.run(replace(config, drain_timeout=1)))
    assert not config.root.exists()
