"""Production registration, provenance and refusal for HTTP credential sources."""

from __future__ import annotations

import json
import subprocess

import pytest
from credential_relay import RelayBuilder
from credential_relay.http_auth import HttpAuthContext
from dropin_registry import ScanAuthority
from http_credential_provider import configure
from plugin_activation import ActivationReport

from agent_bridge import http_provider_sources, provider_sources
from agent_bridge.agent_registry_relay import register_credential_sources
from agent_bridge.provider_sources import ManifestError, parse_manifest
from agent_bridge.remote_acp_auth import RemoteCredentialPolicy
from agent_bridge.remote_acp_config import RemoteAcpTarget, RemoteAuthApproval


@pytest.mark.asyncio
async def test_real_startup_registers_approved_http_source_with_tcp_listener_disabled(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from agent_bridge import app as app_module
    from agent_bridge.agent_registry import AgentResolver
    from agent_bridge.models import ServiceConfig

    calls, _ = configure(monkeypatch, tmp_path)
    tcp_start = AsyncMock(side_effect=AssertionError("TCP relay must remain disabled"))
    monkeypatch.setattr("credential_relay.server.CredentialRelayServer.start", tcp_start)
    monkeypatch.setattr(app_module, "daemon_resolver", lambda cfg: AgentResolver({}, {}))
    target = RemoteAcpTarget(
        endpoint="https://example.com/acp", transport="streamable-http",
        auth={
            "source": "injected", "identity": "synthetic", "scheme": "Example",
            "credential_source": "synthetic@example/grant",
            "resource": "synthetic-resource", "scope": "read",
        },
    )
    app = app_module.create_app(config=ServiceConfig(
        port=0, db_path=str(tmp_path / "bridge.db"), enable_credential_relay=False,
        remote_acp={"peer": target}, remote_acp_auth_sources={
            "synthetic@example/grant": RemoteAuthApproval(
                origin=target.origin, identity="synthetic", scheme="Example",
                resource="synthetic-resource", scope="read",
            ),
        },
    ), token="synthetic-control")
    app.state.publish_on_ready = False
    app.state.passive = True
    async with app.router.lifespan_context(app):
        assert app.state.ready and not calls.exists()
        policy = app.state.session_manager._remote_credential_policy
        context = HttpAuthContext(target.origin, "synthetic", "test", "synthetic-resource", "read")
        headers = await policy.bind(target)(context).headers(context)
        assert headers["Authorization"].split(" ", 1) == ["Example", "synthetic-credential"]
        assert calls.exists() and not app.state.credential_relay_ready
        tcp_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_provider_process_registers_then_acquires_only_after_approval(monkeypatch, tmp_path):
    calls, _ = configure(monkeypatch, tmp_path)
    builder = RelayBuilder()
    register_credential_sources(builder)
    assert not calls.exists() and builder.empty
    target = RemoteAcpTarget(
        endpoint="https://example.com/acp", transport="streamable-http",
        auth={
            "source": "injected", "identity": "synthetic", "scheme": "Example",
            "credential_source": "synthetic@example/grant",
            "resource": "synthetic-resource", "scope": "read",
        },
    )
    policy = RemoteCredentialPolicy()
    policy.register_sources(builder.http_sources)
    with pytest.raises(PermissionError, match="approval"):
        policy.bind(target)
    assert not calls.exists()
    policy.approvals["synthetic@example/grant"] = RemoteAuthApproval(
        origin=target.origin, identity="synthetic", scheme="Example",
        resource="synthetic-resource", scope="read",
    )
    factory = policy.bind(target)
    assert not calls.exists()
    context = HttpAuthContext(target.origin, "synthetic", "test", "synthetic-resource", "read")
    headers = await factory(context).headers(context)
    assert headers["Authorization"].split(" ", 1) == ["Example", "synthetic-credential"]
    assert json.loads(calls.read_text()) == {
        "protocol": "https", "host": "example.com", "path": "acp",
        "username": "synthetic", "resource": "synthetic-resource", "scope": "read",
    }


@pytest.mark.parametrize("state", ["disabled", "unknown", "legacy", "opt-out"])
def test_unattributed_inactive_or_nonparticipating_provider_cannot_register(monkeypatch, tmp_path, state):
    calls, registry = configure(monkeypatch, tmp_path)
    if state in {"disabled", "unknown"}:
        authority = ScanAuthority.COMPLETE if state == "disabled" else ScanAuthority.INDETERMINATE
        monkeypatch.setattr(provider_sources, "resolve_active_plugins", lambda: ActivationReport(authority, {}))
    else:
        path = registry / "credentials.json"
        data = json.loads(path.read_text())
        data.pop("http_credentials_version")
        if state == "legacy":
            for key in ("schema_version", "plugin", "plugin_root"):
                data.pop(key)
        path.write_text(json.dumps(data))
    builder = RelayBuilder()
    register_credential_sources(builder)
    with pytest.raises(PermissionError, match="unavailable"):
        builder.http_sources.get("synthetic@example/grant")
    assert not calls.exists()


@pytest.mark.parametrize("version", [True, "1", -1, 2])
def test_http_provider_manifest_requires_supported_integer_version(tmp_path, version):
    with pytest.raises(ManifestError, match="http_credentials_version"):
        parse_manifest({
            "schema_version": 1, "plugin": "synthetic@example", "plugin_root": str(tmp_path),
            "namespace": "synthetic", "command": ["unused"], "http_credentials_version": version,
        })


def test_http_provider_manifest_rejects_legacy_opt_in():
    with pytest.raises(ManifestError, match="schema_version 1"):
        parse_manifest({
            "namespace": "synthetic", "command": ["unused"], "http_credentials_version": 1,
        })


@pytest.mark.parametrize("entries", [
    None, {}, [{"id": "foreign/source", "scoped": True}],
    [{"id": "grant", "scoped": "true"}],
    [{"id": "grant", "scoped": True, "command": ["untrusted"]}],
    [{"id": "grant", "scoped": True}, {"id": "broken"}],
])
def test_invalid_profile_does_not_partially_register_or_disable_healthy_source(monkeypatch, tmp_path, entries):
    calls, _ = configure(monkeypatch, tmp_path)
    monkeypatch.setattr(http_provider_sources, "run_captured", lambda *args, **kwargs: (
        subprocess.CompletedProcess(args, 0, json.dumps({"http_sources": entries}), "")
    ))
    builder = RelayBuilder()
    healthy = object()
    builder.add_http_source("healthy@example/source", healthy)
    register_credential_sources(builder)
    assert builder.http_sources.get("healthy@example/source").source is healthy
    with pytest.raises(PermissionError, match="unavailable"):
        builder.http_sources.get("synthetic@example/grant")
    assert not calls.exists()


def test_duplicate_advertisements_refuse_reference_without_affecting_peers(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setattr(http_provider_sources, "run_captured", lambda *args, **kwargs: (
        subprocess.CompletedProcess(args, 0, json.dumps({"http_sources": [
            {"id": "grant", "scoped": True}, {"id": "grant", "scoped": True},
            {"id": "other", "scoped": False},
        ]}), "")
    ))
    builder = RelayBuilder()
    register_credential_sources(builder)
    with pytest.raises(PermissionError, match="ambiguous"):
        builder.http_sources.get("synthetic@example/grant")
    assert not builder.http_sources.get("synthetic@example/other").scoped


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exit", "timeout", "decode"])
async def test_provider_failures_never_expose_backend_output(monkeypatch, failure):
    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["synthetic"], 1, stderr="private-detail")
        if failure == "decode":
            raise UnicodeError("private-detail")
        return subprocess.CompletedProcess(args, 1, "private-detail", "private-detail")
    monkeypatch.setattr(http_provider_sources, "run_captured", run)
    source = http_provider_sources.ProviderHttpSource(("synthetic",), "grant")
    with pytest.raises(PermissionError) as error:
        await source.resolve("get", {"scope": "read"})
    assert "private-detail" not in str(error.value)


@pytest.mark.asyncio
async def test_provider_request_rejects_injected_wire_fields_before_execution(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid request must not execute a provider")
    monkeypatch.setattr(http_provider_sources, "run_captured", unexpected)
    source = http_provider_sources.ProviderHttpSource(("synthetic",), "grant")
    for action, fields in (("store", {}), ("get", {"scope": "read\nwrite"}), ("get", {"x=y": "z"})):
        with pytest.raises(PermissionError, match="refused"):
            await source.resolve(action, fields)
