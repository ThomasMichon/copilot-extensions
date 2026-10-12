"""Actual-account transitions must never reuse another routed identity's token."""

from __future__ import annotations

import copy
import json

import pytest

from agent_mcp.auth.identity import resolve_entra_identity
from agent_mcp.client import OneShotSession, UpstreamError
from agent_mcp.config import AuthSpec, parse_config
from agent_mcp.routing_errors import RoutingError

from .test_routing import MockUpstream, TOOLS, definition


@pytest.fixture
def identity_backend(monkeypatch, tmp_path):
    import agent_mcp.auth.identity as identity
    import agent_mcp.client as client

    state = {"principal": "alpha", "tenant": "tenant", "subscription": "subscription",
             "minted": [], "probes": [], "started": [], "backend": [],
             "tools": {key: copy.deepcopy(TOOLS) for key in ("alpha", "beta")}}
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    original_which = identity.shutil.which
    monkeypatch.setattr(identity.shutil, "which", lambda name: (
        None if name == "ado-auth-helper" else original_which(name)))

    async def azure_json(args, *, timeout):
        state["probes"].append(list(args))
        if args[:2] == ["account", "show"]:
            return {"user": {"name": state["principal"], "type": "user"},
                    "tenantId": state["tenant"], "id": state["subscription"],
                    "environmentName": "AzureCloud"}
        if args[:2] == ["account", "list"]:
            return state.get("accounts", [])
        assert args[:2] == ["account", "get-access-token"]
        assert args[args.index("--subscription") + 1] == state["subscription"]
        state["minted"].append(state["principal"])
        token = state["principal"]
        if state.get("switch_during_mint"):
            state["principal"] = "beta"
            token = "beta"
        return {"accessToken": token}

    class AuthenticatedMock(MockUpstream):
        async def send(self, msg):
            # Authentication occurs before payload delivery, like real HTTP.
            if msg["method"] == "tools/call":
                await self.injector.headers()
                state["backend"].append((self.key, copy.deepcopy(msg)))
            await super().send(msg)

    monkeypatch.setattr(identity, "azure_json", azure_json)
    monkeypatch.setattr(client, "build_transport", lambda cfg, injector: (
        AuthenticatedMock(cfg, injector, state)))
    return state


def entra_definition():
    data = definition()
    for target in data["routing"]["targets"].values():
        target["auth"] = {
            "kind": "entra", "resource": "resource", "format": "{token}",
            "cache": {"scope": "shared", "key": "same", "ttl": 600},
        }
    return data


async def test_actual_identity_change_same_endpoint_tenant_resource_reopens(identity_backend):
    state = identity_backend
    cfg = parse_config(entra_definition())
    async with OneShotSession(cfg) as session:
        first = await session.call_tool("read", {"organization": "alpha"})
        assert first["structuredContent"]["credential"] == "alpha"
        old = state["started"][0]
        old_key = old.cfg.auth.cache.key
        state["principal"] = "beta"
        second = await session.call_tool("read", {"organization": "alpha"})
        assert second["structuredContent"]["credential"] == "beta"
        assert old.closed
        replacement = state["started"][-1]
        assert replacement.cfg.auth.cache.key != old_key
        assert replacement.cfg.auth.identity_context["principal"] == "beta"
        assert replacement.cfg.auth.identity_context["tenant"] == "tenant"
        assert state["minted"] == ["alpha", "beta"]
    assert all(t.closed for t in state["started"])
    assert cfg.routing.targets["alpha"].auth.identity_context is None


async def test_new_session_after_account_switch_cannot_hit_old_disk_cache(identity_backend):
    state = identity_backend
    cfg = parse_config(entra_definition())
    for principal in ("alpha", "beta"):
        state["principal"] = principal
        async with OneShotSession(cfg) as session:
            result = await session.call_tool("read", {"organization": "alpha"})
            assert result["structuredContent"]["credential"] == principal
    assert state["minted"] == ["alpha", "beta"]


async def test_routed_entra_never_uses_unqualified_source_cache(identity_backend, monkeypatch):
    from agent_mcp.auth.injectors import EntraInjector

    class StaleSource:
        async def resolve(self, *args, **kwargs):
            raise AssertionError("unqualified source cache must not be queried")

    monkeypatch.setattr(EntraInjector, "_new_source", staticmethod(lambda: StaleSource()))
    async with OneShotSession(parse_config(entra_definition())) as session:
        result = await session.call_tool("read", {"organization": "alpha"})
        assert result["structuredContent"]["credential"] == "alpha"


async def test_identity_switch_during_mint_fails_before_backend_and_cache(identity_backend, tmp_path):
    state = identity_backend
    state["switch_during_mint"] = True
    async with OneShotSession(parse_config(entra_definition())) as session:
        with pytest.raises(UpstreamError, match="identity changed"):
            await session.call_tool("read", {"organization": "alpha"})
    assert state["backend"] == []
    assert not list((tmp_path / "token-cache").glob("*.json"))


async def test_warm_injector_guard_catches_identity_switch_without_router(identity_backend):
    state = identity_backend
    async with OneShotSession(parse_config(entra_definition())) as session:
        await session.call_tool("read", {"organization": "alpha"})
        injector = state["started"][0].injector
        state["principal"] = "beta"
        with pytest.raises(RuntimeError, match="identity changed"):
            await injector.headers()
    assert len(state["backend"]) == 1


async def test_invalid_selector_and_action_gate_do_not_probe_identity(identity_backend):
    data = entra_definition()
    data["routing"]["targets"]["beta"]["decorators"] = [{
        "type": "input_gate", "match_tools": ["action"],
        "deny_when": {"path": "action", "not_in": ["get"]},
    }]
    async with OneShotSession(parse_config(data)) as session:
        with pytest.raises(UpstreamError, match="required routing selector"):
            await session.call_tool("read", {})
        with pytest.raises(UpstreamError, match="denied by input policy"):
            await session.call_tool("action", {"organization": "beta", "action": "write"})
    assert identity_backend["probes"] == []
    assert identity_backend["started"] == []


async def test_relay_without_identity_binding_fails_closed(identity_backend, monkeypatch):
    import agent_mcp.auth.identity as identity

    monkeypatch.setattr(identity.shutil, "which", lambda _name: "relay-helper")
    async with OneShotSession(parse_config(entra_definition())) as session:
        with pytest.raises(UpstreamError, match="relay cannot bind identity"):
            await session.call_tool("read", {"organization": "alpha"})
    assert identity_backend["probes"] == []
    assert identity_backend["started"] == []


async def test_explicit_tenant_selects_unique_principal_without_switch(identity_backend):
    state = identity_backend
    state["accounts"] = [
        {"tenantId": "other", "id": "other-subscription",
         "user": {"name": "beta", "type": "user"}, "environmentName": "AzureCloud"}]
    context = await resolve_entra_identity(AuthSpec(kind="entra", tenant="other"), timeout=1)
    assert context["principal"] == "beta"
    assert context["subscription"] == "other-subscription"
    assert state["principal"] == "alpha"
    state["accounts"].append(
        {"tenantId": "other", "id": "another-subscription",
         "user": {"name": "alpha", "type": "user"}})
    with pytest.raises(RuntimeError, match="ambiguous principals"):
        await resolve_entra_identity(AuthSpec(kind="entra", tenant="other"), timeout=1)


async def test_unknown_or_malformed_identity_fails_before_backend(identity_backend):
    identity_backend["principal"] = ""
    async with OneShotSession(parse_config(entra_definition())) as session:
        with pytest.raises(UpstreamError, match="incomplete Azure account"):
            await session.call_tool("read", {"organization": "alpha"})
    assert identity_backend["started"] == []


@pytest.mark.parametrize("user", [
    {"name": "alpha", "type": "unknown"},
    {"name": "systemAssignedIdentity", "type": "servicePrincipal"},
])
def test_generic_principal_markers_are_not_verified_identities(user):
    from agent_mcp.auth.identity import _account_identity

    with pytest.raises(RuntimeError, match="auth identity unavailable"):
        _account_identity({"user": user, "tenantId": "tenant", "id": "subscription"})


@pytest.mark.parametrize("legacy_asyncio", [False, True])
async def test_azure_probe_timeout_uses_owned_tree_teardown(monkeypatch, legacy_asyncio):
    import agent_mcp.auth.identity as identity
    import agent_mcp.auth.injectors as injectors
    import credential_relay.sources.az_login as source

    timeout_type = TimeoutError
    if legacy_asyncio:
        timeout_type = type("LegacyAsyncioTimeoutError", (Exception,), {})
        monkeypatch.setattr(identity.asyncio, "TimeoutError", timeout_type)

    class Process:
        async def communicate(self):
            raise timeout_type()

    proc = Process()
    closed = []

    async def spawn(*args, **kwargs):
        assert kwargs["stdout"] == identity.asyncio.subprocess.PIPE
        assert kwargs["stdin"] == identity.asyncio.subprocess.DEVNULL
        return proc

    async def close(value):
        closed.append(value)

    monkeypatch.setattr(source, "_az_argv", lambda args: ["az", *args])
    monkeypatch.setattr(identity.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(injectors, "_terminate_tree", close)
    with pytest.raises(RoutingError, match="probe failed"):
        await identity.azure_json(["account", "show"], timeout=1)
    assert closed == [proc]


async def test_azure_probe_fails_sanitized_on_bad_output(monkeypatch):
    import agent_mcp.auth.identity as identity
    import agent_mcp.auth.injectors as injectors
    import credential_relay.sources.az_login as source

    class Process:
        returncode = 1

        async def communicate(self):
            return b"", b"private account diagnostic"

    async def spawn(*args, **kwargs):
        return Process()

    async def close(_value):
        pass

    monkeypatch.setattr(source, "_az_argv", lambda args: ["az", *args])
    monkeypatch.setattr(identity.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(injectors, "_terminate_tree", close)
    with pytest.raises(RuntimeError, match="Azure CLI command failed") as error:
        await identity.azure_json(["account", "show"], timeout=1)
    assert "private account" not in str(error.value)


def test_validate_reports_each_routed_target_without_auth_probe(identity_backend, tmp_path, capsys):
    from agent_mcp.__main__ import main

    cfg = tmp_path / "routed.json"
    cfg.write_text(json.dumps(entra_definition()), encoding="utf-8")
    assert main(["validate", str(cfg)]) == 0
    output = capsys.readouterr().out
    assert "catalog=alpha (discovery only)" in output
    assert "alpha: http" in output and "beta: http" in output
    assert output.count("auth: entra") == 2
    assert "connectivity not checked" in output
    assert identity_backend["probes"] == []


async def test_diagnose_does_not_claim_unchecked_routed_auth(identity_backend, tmp_path):
    from agent_mcp.diagnose import diagnose

    cfg = tmp_path / "routed.json"
    cfg.write_text(json.dumps(entra_definition()), encoding="utf-8")
    report = await diagnose(str(cfg), printer=lambda _line: None)
    assert not report.ok
    assert report.failed_stage.name == "routing-context"
    assert "no execution target selected" in report.failed_stage.detail
    assert identity_backend["probes"] == []
    assert identity_backend["started"] == []
