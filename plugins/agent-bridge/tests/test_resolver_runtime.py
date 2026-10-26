"""Effective resolver handoff never re-discovers provider intent from files."""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import asdict

import pytest
from zdd.private_protocol import PrivateIPCError, Refusal

from agent_bridge import agent_registry
from agent_bridge.agent_registry_common import AgentConfig, NamespaceAgentInfo
from agent_bridge.agent_registry_namespace import CliNamespaceResolver, RestrictedCliNamespaceResolver
from agent_bridge.agent_registry_resolver import AgentResolver
from agent_bridge.cold_store_sources import ColdStoreManifest
from agent_bridge.provider_sources import ProviderManifest
from agent_bridge.reader_admission import ReaderAdmission
from agent_bridge.resolver_runtime import capture_resolver, restore_resolver
from agent_bridge.topology import AuthHook, MachineConfig, SshEnvironment


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    machine = MachineConfig(
        "venue", "Example venue", execution_platform="linux",
        ssh_environments=[SshEnvironment("linux", "example-ssh")],
        auth_hooks=[AuthHook("example-auth", 1234, env={"FIXTURE_SECRET": "private-hook"})],
    )
    result = AgentResolver(
        {"worker": AgentConfig("worker", host="venue", aliases=["assistant"],
                               env={"FIXTURE_SECRET": "private-agent"},
                               mcp_servers=[{"example": {"token": "private-mcp"}}])},
        {"venue": machine}, resolved_local_identity=("venue", "linux"),
    )
    manifest = ProviderManifest(
        "box", ("/example/old-provider", "--profile", "serving"), restricted=True,
    )
    namespace = RestrictedCliNamespaceResolver("box", manifest.command[0], command=list(manifest.command))
    namespace._list_cache = (time.monotonic(), [NamespaceAgentInfo("box-one", aliases=["one"])])
    namespace._list_admission = ReaderAdmission(consecutive_overruns=2, retry_at=time.monotonic() + 50)
    result.register_namespace_resolver(namespace)
    result._provider_namespaces = {"box"}
    result._provider_manifests = {"box": manifest}
    # A failed replacement may have entered the scan ledger but is NOT serving.
    result._provider_entries = {
        "/example/drop-in.json": ProviderManifest("box", ("/example/new-provider",), restricted=True),
    }
    result._provider_scan_ts = time.monotonic()
    result._apply_generation = 9
    result._last_incomplete_namespaces = ["box"]
    cold = ColdStoreManifest("session-fetch", ("/example/old-cold-provider",))
    result.cold_store._entries = {"/example/cold.json": cold}
    result.cold_store._manifests = {"session-fetch": cold}
    return result


def test_roundtrip_preserves_applied_state_and_does_not_discover(resolver, monkeypatch):
    payload = capture_resolver(resolver)

    def forbidden(*args, **kwargs):
        pytest.fail("restoration must not reload files, resolve hostnames, or run a provider")

    monkeypatch.setattr(agent_registry, "_detect_local_machine", forbidden)
    monkeypatch.setattr("agent_bridge.agent_registry_discovery.scan_provider_registry", forbidden)
    restored = restore_resolver(payload)
    assert capture_resolver(restored) == payload
    assert restored._local_machine is restored.machines["venue"]
    assert restored._local_platform == "linux"
    assert restored._apply_generation == 9
    assert restored.incomplete_namespaces == ["box"]
    assert restored._provider_entries["/example/drop-in.json"].command == ("/example/new-provider",)
    assert restored.namespace_resolvers["box"]._command[0] == "/example/old-provider"
    assert restored._provider_manifests["box"].command == resolver._provider_manifests["box"].command
    assert asdict(restored.namespace_resolvers["box"]._list_admission) == asdict(
        resolver.namespace_resolvers["box"]._list_admission,
    )
    assert restored.cold_store._manifests["session-fetch"].command == ("/example/old-cold-provider",)
    assert restored.canonical_agent_name("ASSISTANT") == "worker"


def test_payload_restoration_and_original_are_detached(resolver):
    payload = capture_resolver(resolver)
    restored = restore_resolver(payload)
    resolver.agents["worker"].env["FIXTURE_SECRET"] = "new-private-value"
    assert payload["agents"]["worker"]["env"]["FIXTURE_SECRET"] == "private-agent"
    payload["agents"]["worker"]["env"]["FIXTURE_SECRET"] = "changed-payload"
    assert restored.agents["worker"].env["FIXTURE_SECRET"] == "private-agent"
    assert restored.machines["venue"].auth_hooks[0].env["FIXTURE_SECRET"] == "private-hook"


def test_actual_alias_indexes_are_not_recomputed(resolver):
    # Public agent maps can change while the actual serving alias index stays put.
    resolver.agents["worker"].aliases = ["new-alias"]
    payload = capture_resolver(resolver)
    restored = restore_resolver(payload)
    assert restored.canonical_agent_name("assistant") == "worker"
    assert restored.canonical_agent_name("new-alias") is None


@pytest.mark.parametrize("opaque", [object(), CliNamespaceResolver("box", "provider", fallback=object())])
def test_opaque_namespace_or_callback_refuses(resolver, opaque):
    resolver._namespace_resolvers["box"] = opaque
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture_resolver(resolver)


def test_inflight_discovery_or_contended_lock_refuses_without_wait(resolver):
    resolver._scan_attempts_in_flight = 1
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        capture_resolver(resolver)
    resolver._scan_attempts_in_flight = 0
    with resolver._scan_lock:
        with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
            capture_resolver(resolver)
    assert capture_resolver(resolver)["apply_generation"] == 9


@pytest.mark.asyncio
async def test_inflight_namespace_enumeration_refuses_and_retains_task(resolver):
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    resolver.namespace_resolvers["box"]._list_task = task
    try:
        with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
            capture_resolver(resolver)
        assert not task.cancelled()
    finally:
        blocker.set()
        await task


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema=2),
    lambda p: p.update(unknown="private-secret"),
    lambda p: p["agents"]["worker"].update(unknown="private-secret"),
    lambda p: p["agents"]["worker"].update(managed="false"),
    lambda p: p.update(local_key="missing"),
    lambda p: p.update(provider_namespaces=[]),
    lambda p: p["namespaces"]["box"].update(command=["/other/provider"]),
    lambda p: p["namespaces"]["box"].update(kind="cli"),
    lambda p: p["namespaces"]["box"]["admission"].update(probing=True),
    lambda p: p["ssh_aliases"].update({"example-ssh": ["venue", -1]}),
    lambda p: p.update(provider_scan_ttl=float("nan")),
])
def test_invalid_private_state_only_reports_fixed_refusal(resolver, mutation):
    payload = copy.deepcopy(capture_resolver(resolver))
    mutation(payload)
    with pytest.raises(PrivateIPCError) as caught:
        restore_resolver(payload)
    assert str(caught.value) == Refusal.AUTHORITY.value
    assert caught.value.__cause__ is None
    assert "private-secret" not in repr(caught.value)


def test_restore_different_execution_platform_refuses(resolver, monkeypatch):
    payload = capture_resolver(resolver)
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "windows")
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        restore_resolver(payload)


@pytest.mark.parametrize("kind", ["unmatched", "foreign-platform", "ssh-alias"])
@pytest.mark.parametrize("legacy", [False, True])
def test_remote_only_scoped_state_roundtrips_without_local_authority(monkeypatch, kind, legacy):
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    monkeypatch.setattr("socket.gethostname", lambda: "workstation")
    machine = MachineConfig(
        "remote", "Remote", hostname="another-host", physical_host="another-host",
        execution_platform="linux",
        ssh_environments=[SshEnvironment("linux", "remote-ssh")],
    )
    if kind == "foreign-platform":
        machine.hostname = "workstation"
        machine.execution_platform = "windows"
        machine.ssh_environments = [SshEnvironment("windows", "remote-ssh")]
    elif kind == "ssh-alias":
        machine.ssh_environments[0].alias = "workstation"
    machine.legacy_key = machine.key if legacy else ""
    original = AgentResolver({}, {machine.key: machine})
    assert original._local_machine is None
    payload = capture_resolver(original)

    def forbidden(*args, **kwargs):
        pytest.fail("remote-only restoration must not rediscover or acquire local authority")

    monkeypatch.setattr(agent_registry, "_detect_local_machine", forbidden)
    restored = restore_resolver(payload)
    assert restored._local_machine is None
    assert restored._local_platform == "linux"
    assert not restored.is_local_transport("remote-ssh", execution_space_key="remote")
    assert not restored.is_local_transport("workstation")
    assert capture_resolver(restored) == payload
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "windows")
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        restore_resolver(payload)
