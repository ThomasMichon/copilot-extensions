"""Private relay restoration preserves gates without querying credential stores."""

from __future__ import annotations

import copy
import time

import pytest
from credential_relay.registry import RelayBuilder, TokenRegistry
from credential_relay.server import CredentialRelayServer, ScopeDenied
from credential_relay.sources.az_login import AzLoginSource
from credential_relay.sources.gh_auth import GhAuthSource
from credential_relay.sources.git_credential import GitCredentialSource
from credential_relay.sources.injected_token import InjectedTokenSource
from credential_relay.token_gate import RelayTokenGate
from zdd.private_protocol import PrivateIPCError, Refusal

from agent_bridge.agent_registry_relay import FileTokenAuthorizer, FileTokenValidator
from agent_bridge.relay_runtime import capture_relay, restore_relay


@pytest.fixture
def relay(tmp_path):
    registry = TokenRegistry()
    registry.add("fixture-memory-token")
    builder = RelayBuilder()
    git = GitCredentialSource(123, github_username="example-account", github_hosts=["example.test"])
    git._cache[("https", "example.test", "example-account")] = (
        "fixture-private-cached-credential", time.time() + 120,
    )
    builder.add_source(git)
    builder.add_source(GhAuthSource(account="example-account", github_hosts=["example.test"]))
    azure = AzLoginSource(["https://example.test"], cache_ttl_override=120)
    azure._cache[("https://example.test", "example-tenant")] = (
        "fixture-private-azure-credential", time.time() + 60,
    )
    builder.add_source(azure)
    builder.add_source(InjectedTokenSource(["https://example.test"], env_var="EXAMPLE_INJECTED_TOKEN"))
    builder.require_token(["get-azure-token"], registry.validate)
    builder.require_token(["get-azure-token"], FileTokenValidator(tmp_path / "validator.json"))
    builder.authorize_token(
        ["get-azure-token"], FileTokenAuthorizer(tmp_path / "authorizer.json", ["https://example.test"]),
    )
    builder.azure_resources.add("https://example.test")
    builder.set_port(12345)
    builder.set_ado_host("example.test")
    return builder.build()


def test_effective_relay_roundtrip_uses_no_files_and_does_not_start(relay, monkeypatch):
    saved = capture_relay(relay)

    def forbidden(*args, **kwargs):
        pytest.fail("private restoration must not read a store or start a listener")

    monkeypatch.setattr("pathlib.Path.read_text", forbidden)
    monkeypatch.setattr(CredentialRelayServer, "start", forbidden)
    restored = restore_relay(saved)
    assert not restored.running
    assert capture_relay(restored) == saved
    gate = restored.token_authorizer.__self__
    assert type(gate) is RelayTokenGate
    assert gate.validators[0]("fixture-memory-token")
    assert not gate.validators[0]("wrong-token")
    assert gate.static_resources == frozenset({"https://example.test"})
    assert restored.sources[0]._cache == relay.sources[0]._cache
    assert restored.sources[2]._cache == relay.sources[2]._cache
    saved["sources"][0]["cache"][0][1] = "changed-private-payload"
    assert restored.sources[0]._cache[("https", "example.test", "example-account")][0] == (
        "fixture-private-cached-credential"
    )
    assert "fixture-memory-token" not in repr(gate)


def test_restored_authorization_keeps_any_match_scope_denial_and_unknown_silence(relay, monkeypatch):
    restored = restore_relay(capture_relay(relay))
    monkeypatch.setattr(FileTokenValidator, "__call__", lambda self, token: False)
    monkeypatch.setattr(FileTokenAuthorizer, "__call__", lambda self, token, action, fields: False)
    assert restored.token_authorizer(
        "fixture-memory-token", "get-azure-token", {"scope": "https://example.test/.default"},
    )
    with pytest.raises(ScopeDenied):
        restored.token_authorizer(
            "fixture-memory-token", "get-azure-token", {"scope": "https://denied.test/.default"},
        )
    assert not restored.token_authorizer("unknown", "get-azure-token", {"scope": "https://denied.test"})


def test_validator_only_gate_roundtrip():
    registry = TokenRegistry()
    registry.add("fixture-token")
    builder = RelayBuilder()
    builder.require_token(["get-azure-token"], registry.validate)
    restored = restore_relay(capture_relay(builder.build()))
    assert restored.token_validator("fixture-token")
    assert not restored.token_validator("wrong")
    assert restored.token_authorizer is None


def test_none_host_does_not_reload_successor_environment(monkeypatch):
    monkeypatch.delenv("CODESPACES_ADO_HOST", raising=False)
    saved = capture_relay(CredentialRelayServer())
    monkeypatch.setenv("CODESPACES_ADO_HOST", "unexpected-successor.test")
    assert restore_relay(saved).ado_host is None


@pytest.mark.parametrize("mutation", [
    lambda server: setattr(server, "token_authorizer", lambda *args: True),
    lambda server: server.sources.append(object()),
    lambda server: setattr(server.stats, "active_connections", 1),
    lambda server: setattr(server.sources[0], "_username_resolver", lambda *args: "other-account"),
    lambda server: server.sources[0]._inflight.update({"pending": object()}),
    lambda server: setattr(server.sources[0], "resolve", lambda *args: "unrecorded-credential"),
])
def test_opaque_or_inflight_state_refuses(relay, mutation):
    mutation(relay)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture_relay(relay)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema=True),
    lambda p: p.update(extra="fixture-secret"),
    lambda p: p.update(port=-1),
    lambda p: p.update(gate="none"),
    lambda p: p["sources"][0].update(kind="unknown"),
    lambda p: p["sources"][0].update(ttl=float("nan")),
    lambda p: p["sources"][0].update(env_var="unexpected"),
    lambda p: p["validators"][0].update(tokens=["duplicate", "duplicate"]),
    lambda p: p["authorizers"][0].update(tokens=["extra-privilege"]),
])
def test_malformed_secret_payload_only_exposes_fixed_reason(relay, mutation):
    payload = copy.deepcopy(capture_relay(relay))
    mutation(payload)
    with pytest.raises(PrivateIPCError) as caught:
        restore_relay(payload)
    assert str(caught.value) == Refusal.AUTHORITY.value
    assert caught.value.__cause__ is None
    assert "fixture-secret" not in repr(caught.value)


def test_contended_token_registry_capture_never_waits(relay):
    registry = relay.token_authorizer.__self__.validators[0].__self__
    with registry._lock:
        with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
            capture_relay(relay)
