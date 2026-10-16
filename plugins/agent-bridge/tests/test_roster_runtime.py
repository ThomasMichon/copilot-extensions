"""Captured readiness never substitutes an unscanned or timeless roster."""

import asyncio
import copy
import time

import pytest
from zdd.private_protocol import PrivateIPCError

from agent_bridge.agent_registry_cache import AgentRosterCache, _NamespaceEntry, _NamespaceState
from agent_bridge.agent_registry_namespace import CliNamespaceResolver
from agent_bridge.effective_runtime import ServingRuntimeCapture
from agent_bridge.roster_runtime import capture_roster, restore_roster
from agent_bridge.runtime_restore import restore_runtime
from test_runtime_restore import captured as captured


def warm_roster(app):
    resolver = app.state.resolver
    cache = AgentRosterCache(resolver)
    cache._discovery_ever_ok = True
    cache._discovery_ok_at = time.monotonic()
    app.state.agent_roster_cache = cache
    return cache


def roster_capture(captured):
    app, origins, _capture = captured
    warm_roster(app)
    capture = ServingRuntimeCapture(
        app, origins, include_resolver=True, include_relay=True, include_roster=True,
    )
    return capture


def test_roster_transfer_is_private_detached_and_never_discovers(captured, monkeypatch):
    app, _origins, _capture = captured
    capture = roster_capture(captured)
    payload = capture.capture().private_payload()
    assert payload["runtime"]["coverage"] == "config-manager-resolver-relay-roster-v1"

    def forbidden(*args, **kwargs):
        pytest.fail("restored roster rediscovered or started a provider")

    monkeypatch.setattr(type(app.state.resolver), "refresh_provider_resolvers", forbidden)
    monkeypatch.setattr(AgentRosterCache, "start", forbidden)
    restored = restore_runtime(payload)
    assert restored.roster.resolver is restored.resolver
    assert restored.roster.ready()
    assert "private-fixture" not in repr(restored.roster)
    snapshot = asyncio.run(restored.roster.get_snapshot())
    assert snapshot.complete and snapshot.incomplete_namespaces == []
    assert snapshot.rows[0]["name"] == "actual"
    snapshot.rows.clear()
    assert asyncio.run(restored.roster.get_snapshot()).rows
    assert not asyncio.run(restored.roster.get_snapshot(force_refresh=True)).complete
    app.state.agent_roster_cache._discovery_ok_at -= 1
    assert restored.roster.private_payload() == payload["runtime"]["roster"]


@pytest.mark.parametrize("mutation", [
    "missing", "unscanned", "stopping", "cycle", "uninitialized", "different-provider",
    "missing-namespace", "future", "bad-type",
])
def test_roster_capture_refuses_incomplete_native_observations(captured, mutation):
    app, _origins, _capture = captured
    cache = warm_roster(app)
    namespace = CliNamespaceResolver("fixture", "fixture-tool")
    app.state.resolver._namespace_resolvers["fixture"] = namespace
    entry = _NamespaceEntry(namespace, _NamespaceState.OK, [], time.monotonic())
    cache._entries["fixture"] = entry
    if mutation == "missing":
        cache = object()
    elif mutation == "unscanned":
        cache._discovery_ever_ok = False
    elif mutation == "stopping":
        cache._stopping = True
    elif mutation == "cycle":
        cache._cycle_started_at = time.monotonic()
    elif mutation == "uninitialized":
        entry.state = _NamespaceState.UNINITIALIZED
    elif mutation == "different-provider":
        entry.provider_ref = object()
    elif mutation == "missing-namespace":
        cache._entries.clear()
    elif mutation == "future":
        cache._discovery_ok_at = time.monotonic() + 100
    else:
        cache._freshness_deadline = True
    with pytest.raises(PrivateIPCError):
        capture_roster(cache, app.state.resolver)


@pytest.mark.parametrize("mutation", [
    "extra", "missing", "coercion", "namespace", "future", "null", "boolean-schema",
])
def test_roster_restore_rejects_unknown_or_incomplete_coverage(captured, mutation):
    payload = roster_capture(captured).capture().private_payload()
    payload = copy.deepcopy(payload)
    roster = payload["runtime"]["roster"]
    if mutation == "extra":
        roster["unknown"] = "private-fixture-sensitive"
    elif mutation == "missing":
        del roster["discovery_at"]
    elif mutation == "coercion":
        roster["freshness_seconds"] = "36"
    elif mutation == "namespace":
        roster["entries"]["unknown"] = {"state": "ok", "rows": [], "refreshed_at": 1}
    elif mutation == "future":
        roster["discovery_at"] = time.monotonic() + 100
    elif mutation == "boolean-schema":
        roster["schema"] = True
    else:
        payload["runtime"]["roster"] = None
    with pytest.raises(PrivateIPCError) as caught:
        restore_runtime(payload)
    assert "private-fixture" not in str(caught.value)


def test_roster_age_and_failed_namespaces_remain_visible(captured, monkeypatch):
    app, _origins, _capture = captured
    cache = warm_roster(app)
    resolver = app.state.resolver
    now = time.monotonic()
    for prefix, status in [("fresh", _NamespaceState.OK), ("failed", _NamespaceState.FAILED)]:
        provider = CliNamespaceResolver(prefix, "fixture-tool")
        resolver._namespace_resolvers[prefix] = provider
        cache._entries[prefix] = _NamespaceEntry(
            provider, status, [{"name": prefix + ":agent"}], now,
        )
    restored = restore_roster(capture_roster(cache, resolver), resolver)
    snapshot = asyncio.run(restored.get_snapshot())
    assert not snapshot.complete and snapshot.incomplete_namespaces == ["failed"]
    assert {row["name"] for row in snapshot.rows} == {"actual", "fresh:agent"}
    monkeypatch.setattr("agent_bridge.roster_runtime.time.monotonic", lambda: now + 100)
    assert not restored.ready()
    stale = asyncio.run(restored.get_snapshot())
    assert stale.incomplete_namespaces == ["failed", "fresh"]
    assert not stale.complete and {row["name"] for row in stale.rows} == {"actual"}
