"""Independent HTTP target and owner expectations, with no live endpoint or secrets."""

from __future__ import annotations

import copy
import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from agent_bridge.agent_registry import AgentResolver
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.remote_acp_auth import RemoteCredentialPolicy
from agent_bridge.remote_acp_catalog import RemoteAcpCatalog
from agent_bridge.remote_acp_config import (
    RemoteAcpBinding,
    RemoteAcpTarget,
    RemoteAuthApproval,
)
from agent_bridge.remote_acp_sources import RemoteContributionReport
from agent_bridge.routes.agents import get_agent, list_agents
from agent_bridge.routes import health as health_route

pytestmark = pytest.mark.guard
CONTRACT = Path(__file__).resolve().parents[1] / "contract"
REPO = Path(__file__).resolve().parents[3]
def _targets_vector(generation):
    return json.loads(
        (CONTRACT / f"fixtures/http/current/remote-acp-targets-generation-{generation}.json").read_text(
            encoding="utf-8",
        )
    )


VECTOR = _targets_vector(45)


def _request(**state):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def _target():
    return RemoteAcpTarget(
        endpoint=VECTOR["client"]["url"], transport="streamable-http",
        auth={"source": "git-credential", "identity": "synthetic"},
    )


@pytest.mark.asyncio
async def test_health_retains_historical_target_capabilities(monkeypatch):
    monkeypatch.setattr(
        health_route, "get_default_manager",
        lambda: SimpleNamespace(carrier_diagnostics=lambda: {}),
    )
    monkeypatch.setattr(health_route, "slot_descriptor", lambda *args, **kwargs: {})
    monkeypatch.setattr(health_route, "config_dir", lambda: Path("synthetic-unused"))
    body = await health_route.health(_request())
    expected = {**VECTOR["health"], "protocol_version": HTTP_PROTOCOL_VERSION}
    assert {key: body[key] for key in expected} == expected


@pytest.mark.parametrize("generation", range(37, HTTP_PROTOCOL_VERSION + 1))
@pytest.mark.parametrize("address", ["url", "named"])
def test_client_target_negotiation_precedes_request(generation, address):
    client = BridgeClient.__new__(BridgeClient)
    client._daemon_proto = (generation, 1)
    request = Mock(return_value={})
    client._request = request
    minimum = VECTOR["capability"]["remote_acp_targets" if address == "url" else "remote_acp"]
    if generation < minimum:
        with pytest.raises(BridgeClientError) as refused:
            client.start_session(agent=VECTOR["client"][address])
        assert refused.value.status == VECTOR["capability"]["unsupported_status"]
        assert request.call_count == VECTOR["client"]["refusal_requests"]
    else:
        client.start_session(agent=VECTOR["client"][address])
        expected = VECTOR["client"]["accepted_request"]
        assert request.call_count == 1
        assert request.call_args.args == (
            expected["method"], expected["path"],
            {
                "agent": VECTOR["client"][address],
                "protocol_version": HTTP_PROTOCOL_VERSION,
            },
        )


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    from agent_bridge import remote_acp_sources

    root = tmp_path / "payload"
    root.mkdir()
    (root / "providers").mkdir()
    (root / "plugin.json").write_text(json.dumps({
        "name": "synthetic", "bridgeConfig": "config.json",
        "bridgeProviders": "providers",
    }), encoding="utf-8")
    (root / "config.json").write_text(json.dumps({"remote_acp": {
        "assistant": {"transport": "streamable-http"},
        "broken": {"transport": "streamable-http", "endpoint": VECTOR["client"]["url"]},
    }}), encoding="utf-8")
    (root / "providers" / "assistant.json").write_text(json.dumps({
        "schema_version": 1, "kind": "remote-acp", "name": "assistant", "target": "assistant",
    }), encoding="utf-8")
    plugin = ActivePlugin(
        source="synthetic@example", name="synthetic", marketplace="example",
        root=root, scopes=("project:demo",),
    )
    report = ActivationReport(
        ScanAuthority.COMPLETE, {plugin.source: EntryDecision.active(plugin)},
    )
    monkeypatch.setattr(remote_acp_sources, "resolve_active_plugins", lambda: report)
    return RemoteAcpCatalog({"native": _target()}, project_bindings=lambda project: {})


@pytest.mark.asyncio
async def test_ordinary_roster_and_detail_match_binding_vectors(catalog, monkeypatch):
    network = Mock(side_effect=AssertionError("discovery must remain local"))
    monkeypatch.setattr(socket.socket, "connect", network)
    monkeypatch.setattr(socket, "create_connection", network)
    resolver = AgentResolver({}, {})
    resolver.remote_acp_catalog = catalog
    request = _request(resolver=resolver, ready=True)
    rows = (await list_agents(request))["agents"]
    expected_rows = VECTOR["roster"]["rows"]
    assert {row["name"] for row in rows} == {row["name"] for row in expected_rows}
    for expected in expected_rows:
        row = next(row for row in rows if row["name"] == expected["name"])
        assert {key: row[key] for key in expected} == expected
        assert await get_agent(row["name"], request) == row
        assert "endpoint" not in row and "auth" not in row
    binding = {
        "plugin": "synthetic@example", "endpoint": VECTOR["client"]["url"],
        "identity": "synthetic",
    }
    catalog.bindings["assistant"] = RemoteAcpBinding(**binding)
    row = await get_agent("remote-acp:assistant", request, project="demo")
    expected = VECTOR["roster"]["bound_contribution"]
    assert {key: row[key] for key in expected} == expected
    assert catalog.resolve("remote-acp:assistant", project="demo")[0] == "assistant"
    with pytest.raises(ValueError, match="project"):
        catalog.resolve("remote-acp:assistant", project="other")
    catalog.bindings["assistant"] = RemoteAcpBinding(**{**binding, "plugin": "other@example"})
    row = await get_agent("remote-acp:assistant", request, project="demo")
    expected = VECTOR["roster"]["wrong_owner"]
    assert {key: row[key] for key in expected} == expected
    with pytest.raises(ValueError):
        catalog.resolve("remote-acp:assistant", project="demo")
    assert network.call_count == VECTOR["endpoint_policy"]["discovery_network_requests"]


def test_url_uses_one_exact_locally_configured_policy():
    catalog = RemoteAcpCatalog({"native": _target()}, discover=RemoteContributionReport)
    assert catalog.resolve(VECTOR["endpoint_policy"]["canonical_match"]) == ("native", _target())
    for address in VECTOR["endpoint_policy"]["rejected"]:
        with pytest.raises(ValueError):
            catalog.resolve(address)
    catalog.targets["duplicate"] = _target()
    assert len(catalog.targets) == VECTOR["endpoint_policy"]["ambiguous_matching_targets"]
    with pytest.raises(ValueError, match="unique"):
        catalog.resolve(VECTOR["client"]["url"])


def test_source_approval_matches_exact_policy_without_resolving_secrets():
    reference = "synthetic@example/grant"
    target = RemoteAcpTarget(
        endpoint=VECTOR["client"]["url"], transport="streamable-http",
        auth={"source": "injected", "identity": "synthetic", "credential_source": reference},
    )
    policy = RemoteCredentialPolicy()
    source = SimpleNamespace(resolve=Mock(side_effect=AssertionError("secret read")))
    policy.sources.register(reference, source)
    catalog = RemoteAcpCatalog(
        {"native": target}, discover=RemoteContributionReport, check_auth=policy.bind,
    )
    row = catalog.rows()[0]
    expected = VECTOR["roster"]["source_unapproved"]
    assert {key: row[key] for key in expected} == expected
    approval = {"origin": "https://example.com", "identity": "synthetic"}
    policy.approvals[reference] = RemoteAuthApproval(**approval)
    assert catalog.resolve(VECTOR["client"]["url"]) == ("native", target)
    for changes in (
        {"origin": "https://other.example.com"}, {"identity": "different"},
        {"resource": "other"}, {"scope": "other"}, {"scheme": "Other"},
    ):
        policy.approvals[reference] = RemoteAuthApproval(**{**approval, **changes})
        with pytest.raises(ValueError):
            catalog.resolve(VECTOR["client"]["url"])
    assert source.resolve.call_count == VECTOR["endpoint_policy"]["discovery_secret_reads"]


def _wire_generation(value, generation):
    if isinstance(value, dict):
        return {
            key: generation if key == "protocol_version" and item == generation - 1
            else _wire_generation(item, generation)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_wire_generation(item, generation) for item in value]
    return value


@pytest.mark.parametrize(("generation", "reattested"), [(41, 40), (42, 42), (43, 42), (44, 45)])
def test_reattestations_retain_independent_historical_expectations(generation, reattested):
    registry = json.loads((CONTRACT / "registry.json").read_text(encoding="utf-8"))
    contract = next(item for item in registry["contracts"] if item["id"] == "agent-bridge.http-wire")
    count = 0
    for registered in contract["fixtures"]:
        if registered["generation"] != generation:
            continue
        current = json.loads((REPO / registered["path"]).read_text(encoding="utf-8"))
        if "reattested_from" not in current:
            continue
        count += 1
        previous_path = REPO / current["reattested_from"]["path"]
        previous_bytes = previous_path.read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(previous_bytes).hexdigest() == current["reattested_from"]["sha256"]
        previous = json.loads(previous_bytes)
        expected = _wire_generation(previous, generation)
        actual = copy.deepcopy(current)
        actual.pop("captured_from")
        actual.pop("reattested_from")
        expected.pop("captured_from")
        expected.pop("reattested_from", None)
        assert actual == expected
    assert count == reattested


def test_generation_45_reattestations_preserve_independent_previous_vectors():
    registry = json.loads((CONTRACT / "registry.json").read_text(encoding="utf-8"))
    contract = next(item for item in registry["contracts"] if item["id"] == "agent-bridge.http-wire")
    count = 0
    for registered in contract["fixtures"]:
        if registered["generation"] != 45:
            continue
        current = json.loads((REPO / registered["path"]).read_text(encoding="utf-8"))
        if "reattested_from" not in current:
            continue
        count += 1
        previous_bytes = (REPO / current["reattested_from"]["path"]).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(previous_bytes).hexdigest() == current["reattested_from"]["sha256"]
        previous = json.loads(previous_bytes)
        expected = _wire_generation(previous, 45)
        actual = copy.deepcopy(current)
        for data in (expected, actual):
            data.pop("captured_from")
            data.pop("reattested_from", None)
        assert actual == expected
    assert count > 0


OWNED = json.loads(
    (CONTRACT / "fixtures/http/current/owned-remote-generation-45.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("generation", range(38, HTTP_PROTOCOL_VERSION + 1))
@pytest.mark.parametrize("address", ["remote-acp:example-agent", "https://example.com/acp"])
def test_owned_remote_client_refuses_before_sending_to_older_daemon(generation, address):
    from agent_bridge import protocol

    assert protocol.REMOTE_ACP_OWNERSHIP_PROTOCOL_VERSION == OWNED["capability"]["remote_acp_ownership"]
    client = BridgeClient.__new__(BridgeClient)
    client._daemon_proto = (generation, 1)
    client._request = Mock(return_value={})
    payload = {**OWNED["request"]["json"], "agent": address}
    payload.pop("protocol_version")
    if generation < OWNED["capability"]["remote_acp_ownership"]:
        with pytest.raises(BridgeClientError) as refused:
            client.start_session(**payload)
        assert refused.value.status == OWNED["capability"]["unsupported_status"]
        client._request.assert_not_called()
    else:
        client.start_session(**payload)
        client._request.assert_called_once_with(
            OWNED["request"]["method"], OWNED["request"]["path"],
            {
                **OWNED["request"]["json"], "agent": address,
                "protocol_version": HTTP_PROTOCOL_VERSION,
            },
            request_timeout=None,
        )


@pytest.mark.parametrize("phase", ["bound", "attempting", "retired", "closed"])
def test_owned_remote_inspection_preserves_retained_terminal_identity(phase):
    from agent_bridge.remote_relationships import inspect_row

    row = {**OWNED["journal"], "phase": phase}
    expected = {**OWNED["inspection"], "ownership": "owned" if phase == "bound" else phase}
    assert inspect_row(row) == expected


def test_unreserved_retirement_reports_its_confirmed_absence():
    from agent_bridge.relationship_authority import confirm_absence
    from agent_bridge.remote_relationships import inspect_row

    expected = OWNED["unreserved_retirement"]
    row = {
        **OWNED["journal"], "phase": "closed", "claim": None, "native_id": None,
        "absence": expected["authority"],
    }
    assert confirm_absence(row["absence"], owner=row["owner"], request_id=row["id"])
    assert inspect_row(row) == expected["inspection"]
