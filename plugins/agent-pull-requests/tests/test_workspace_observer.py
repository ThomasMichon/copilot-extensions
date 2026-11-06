"""Strict profile reads and real finite CLI/TLS/native-reader composition."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

from agent_pull_requests import workspace_client_profile as profiles
from agent_pull_requests import workspace_observer as observer
from agent_pull_requests.watch_replay_binding import ReplayBinding
from test_native_read import native as native
from test_native_read import update
from test_review_workspace import evidence as evidence


@pytest.fixture(autouse=True)
def application_context(monkeypatch):
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT"):
        monkeypatch.delenv(key, raising=False)


def connection(root):
    return {
        "gateway_origin": "https://gateway.example", "route_prefix": "/api",
        "proof_key_file": str(root / "proof.pem"), "access_token_file": str(root / "access"),
        "gateway_ca": None,
        "binding": ReplayBinding(
            "fleet-a", "gateway-a", "service-a", "namespace", "caller-a",
            "project", "consumer-a", "account-a", "project/repository",
        ).document(),
        "scope": {
            "provider": "azure-devops", "endpoint": "https://dev.azure.com/example",
            "project_id": "project-id", "repository_id": "repository-id",
            "principal_id": "reviewer-id",
        },
    }


def write_profiles(root, selected, owner=None):
    root.mkdir(exist_ok=True, parents=True)
    document = {
        "schema": profiles.SCHEMA, "version": 1,
        "configuration_owner": owner or {"mode": "application", "plugin": "agent-pull-requests"},
        "profiles": {"selected": selected},
    }
    (root / profiles.PROFILE_FILE).write_text(json.dumps(document), encoding="utf-8")
    return document


@pytest.mark.parametrize("name", ["../selected", "a/b", "a\\b", "", "a.py", "selected "])
def test_profile_name_is_not_a_path_and_refuses_before_configuration(name, monkeypatch):
    monkeypatch.setattr(profiles, "configuration_owner", lambda: pytest.fail("configuration accessed"))
    with pytest.raises(ValueError):
        profiles.read_profile(name, lambda: 10)


@pytest.mark.parametrize("change", ["foreign", "version", "extra", "duplicate", "missing", "secret", "scope", "relative"])
def test_invalid_profile_refuses_without_reading_credentials(tmp_path, monkeypatch, change):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    selected = connection(tmp_path)
    document = write_profiles(tmp_path, selected)
    if change == "foreign":
        document["configuration_owner"]["mode"] = "namespaced"
    elif change == "version":
        document["version"] = True
    elif change == "extra":
        document["factory"] = "not-a-loader"
    elif change == "missing":
        document["profiles"] = {}
    elif change == "secret":
        selected["access_token"] = "not-a-credential-reference"
    elif change == "scope":
        selected["scope"]["account"] = "not-a-scope-field"
    elif change == "relative":
        selected["proof_key_file"] = "relative.pem"
    path = tmp_path / profiles.PROFILE_FILE
    path.write_text(
        '{"version":1,"version":1}' if change == "duplicate" else json.dumps(document),
        encoding="utf-8",
    )
    with pytest.raises((ValueError, PermissionError)):
        profiles.read_profile("selected", lambda: 10)
    assert sorted(item.name for item in tmp_path.iterdir()) == [profiles.PROFILE_FILE]


@pytest.mark.parametrize("suffix", ["", "/"])
def test_application_profile_is_read_only_configuration_not_a_credential_loader(tmp_path, monkeypatch, suffix):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    selected = connection(tmp_path)
    selected["scope"]["endpoint"] += suffix
    write_profiles(tmp_path, selected)
    value, binding, scope = profiles.read_profile("selected", lambda: 10)
    assert value == selected and binding.document() == selected["binding"]
    assert scope == {**selected["scope"], "endpoint": selected["scope"]["endpoint"].rstrip("/")}
    assert not (tmp_path / "proof.pem").exists()


def test_missing_profile_stops_before_any_credential_read(tmp_path, monkeypatch):
    import fleet_manager.credential_references as credentials
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    monkeypatch.setattr(credentials, "load_service_credentials", lambda **k: pytest.fail("credential accessed"))
    code, value = observer._execute({
        "command": "capabilities", "profile": "missing", "request": None, "owner_conflict": False,
    }, lambda: 10)
    assert code == 1 and value["effect_completion"] == "not_inferred"
    assert list(tmp_path.iterdir()) == []


def test_unattributed_default_legacy_uses_existing_entrypoint_selection(tmp_path, monkeypatch):
    from agent_pull_requests import _installation_context as api
    payload = tmp_path / "payload"
    payload.mkdir()
    root = tmp_path / "application"
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(root))
    selected = connection(root)
    write_profiles(root, selected)
    assert profiles.read_profile("selected", lambda: 10)[0] == selected
    monkeypatch.setattr(api, "probe_legacy_entrypoint", lambda **k: {
        "allowMutation": True, "probeReason": "migration-required",
    })
    with pytest.raises(PermissionError):
        profiles.read_profile("selected", lambda: 10)


def test_cell_profile_uses_canonical_namespace_not_legacy_watch_or_runtime_capture(tmp_path, monkeypatch):
    from agent_pull_requests import _installation_context as api
    from agent_pull_requests import watch_invocation
    from test_watch_installation import _installation, _policy

    profile = tmp_path / "profile"
    profile.mkdir()
    current = api._current_environment
    def environment(**kwargs):
        return current(**{**kwargs, "os_profile": profile})
    monkeypatch.setattr(api, "_current_environment", environment)
    installation = _installation(tmp_path)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(installation.receipt))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(installation.payload_root))
    monkeypatch.setenv("AGENT_RT_ROOT", str(installation.plugin_root))
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(installation.state_root))
    monkeypatch.setattr(watch_invocation.WatchInvocation, "capture",
                        lambda **k: pytest.fail("watch runtime captured"))
    root, owner = profiles.configuration_owner()
    assert root == installation.state_root
    assert owner == {
        "mode": "namespaced", "plugin": "agent-pull-requests",
        "marketplace": installation.marketplace_id,
    }
    selected = connection(root)
    write_profiles(root, selected, owner)
    assert profiles.read_profile("selected", lambda: 10)[0] == selected
    write_profiles(root, selected, {**owner, "marketplace": "foreign"})
    with pytest.raises(ValueError):
        profiles.read_profile("selected", lambda: 10)
    write_profiles(root, selected, owner)
    other = tmp_path / "legacy"
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(other))
    with pytest.raises(PermissionError):
        profiles.read_profile("selected", lambda: 10)
    assert not other.exists()
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(root))
    _policy(installation.os_profile, False)
    with pytest.raises(PermissionError):
        profiles.read_profile("selected", lambda: 10)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT")
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT")
    with pytest.raises(PermissionError):
        profiles.read_profile("selected", lambda: 10)


@pytest.mark.parametrize("unsupported", [False, True])
def test_optional_events_and_writable_server_operations_are_not_fabricated(unsupported):
    from fleet_manager.gateway_service_caller import ServiceResponse
    from agent_pull_requests.workspace_client import WorkspaceClient
    from agent_pull_requests.workspace_operations import CHUNK_BYTES, MAX_GENERATION_BYTES, PROTOCOL
    selected = connection(Path.cwd())
    binding = ReplayBinding.from_document(selected["binding"])
    calls = []
    class Caller:
        async def request(self, *args, **kwargs):
            calls.append(args[1])
            return ServiceResponse(200, (("content-type", "application/json"),), json.dumps({
                "binding": binding.document(), "protocol": PROTOCOL,
                "generation_versions": [2] if unsupported else [1], "owner_store_versions": [2],
                "chunk_bytes": CHUNK_BYTES, "max_generation_bytes": MAX_GENERATION_BYTES,
                "operations": ["load", "sync", "lookup", "chunk", "submit", "inspect", "events"],
            }).encode())
    client = WorkspaceClient(Caller(), route_prefix="/api", binding=binding, scope=selected["scope"])
    if unsupported:
        from agent_pull_requests.workspace_client import WorkspaceClientError
        with pytest.raises(WorkspaceClientError, match="unsupported_workspace_contract"):
            asyncio.run(observer._ObserverTools(client).invoke("load", {
                "number": 42, "expected_current": None, "budget_seconds": 10,
            }))
        assert calls == ["/api/v2/review/project/capabilities"]
        return
    value = asyncio.run(observer._ObserverTools(client).capabilities())
    assert set(value["operations"]) == {"capabilities", "load", "sync", "materialize"}
    assert not {"submit", "inspect", "events"} & set(value["backend_contract"]["operations"])


@pytest.mark.parametrize("command", ["submit", "inspect", "source", "source-read"])
def test_read_only_selection_refuses_before_request_or_profile_io(command, monkeypatch):
    monkeypatch.setattr(observer, "_observe", lambda *a: pytest.fail("profile accessed"))
    code, value = observer._execute({
        "command": command, "profile": "selected", "request": "missing", "owner_conflict": False,
    }, lambda: 10)
    assert code == 4 and value["effect_completion"] == "not_inferred"


def test_embedding_and_profile_are_ambiguous_without_replacing_owner(monkeypatch):
    monkeypatch.setattr(observer, "_observe", lambda *a: pytest.fail("profile accessed"))
    code, value = observer._execute({
        "command": "capabilities", "profile": "selected", "request": None, "owner_conflict": True,
    }, lambda: 10)
    assert code == 4 and value["error"] == "workspace_owner_refused"


def test_actual_frontdoor_rejects_profile_and_embedding_before_process_or_stdio(monkeypatch, capsys):
    from agent_pull_requests.__main__ import main
    monkeypatch.setattr(observer, "run_caller_owned", lambda *a, **k: pytest.fail("worker launched"))
    assert main(
        ["workspace", "--client-profile", "selected", "capabilities"],
        workspace_host=observer.WorkspaceTools(),
    ) == 4
    assert capsys.readouterr().out == ""


def test_unknown_request_fields_refuse_before_profile_or_credentials(tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    request.write_text('{"number":42,"account":"override"}', encoding="utf-8")
    monkeypatch.setattr(observer, "_observe", lambda *a: pytest.fail("profile accessed"))
    code, _ = observer._execute({
        "command": "load", "profile": "selected", "request": str(request), "owner_conflict": False,
    }, lambda: 10)
    assert code == 1


def test_failed_physical_cleanup_retains_original_owner(monkeypatch, tmp_path):
    failure = OwnedCleanupError(object(), cleanup_resources=(("original", object()),))
    async def fail(*args):
        raise failure
    monkeypatch.setattr(observer, "_observe", fail)
    with pytest.raises(OwnedCleanupError) as caught:
        observer._execute({
            "command": "capabilities", "profile": "selected", "request": None, "owner_conflict": False,
        }, lambda: 10)
    assert caught.value is failure


def test_profile_invocation_carries_one_absolute_deadline_and_borrowed_streams(monkeypatch):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(observer, "run_caller_owned", run)
    monkeypatch.setattr(observer.sys, "stdin", SimpleNamespace(buffer=object()))
    monkeypatch.setattr(observer.sys, "stdout", SimpleNamespace(buffer=object()))
    started = time.monotonic()
    assert observer.run(SimpleNamespace(
        client_profile="selected", workspace_command="sync", request="-",
    )) == 7
    argv, options = calls[0]
    assert started < options["budget"].deadline <= time.monotonic() + 60
    assert float(argv[3]) == options["budget"].execution_deadline
    assert options["stdin_stream"] is observer.sys.stdin.buffer
    assert options["stdout_stream"] is observer.sys.stdout.buffer
    assert options["capture"] is False


def _fleet_fixtures():
    root = Path(__file__).resolve().parents[3]
    path = str(root / "fleet-manager" / "tests")
    if path not in sys.path:
        sys.path.insert(0, path)
    from gateway_service_transport_fixtures import Rig
    return Rig


@pytest.mark.parametrize("change", ["unchanged", "dialogue", "target", "head", "partial", "refusals"])
@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.timeout(90)
def test_fresh_cli_processes_use_authenticated_native_owner_cache_and_events(
    native, tmp_path, monkeypatch, change, record_property,
):
    from cryptography.hazmat.primitives import serialization
    from fleet_manager.gateway_local_handler import LocalHandlerBinding
    from fleet_manager.gateway_manifest import GatewayManifest
    from fleet_manager.planning import canonical
    from agent_pull_requests.generation_cache import GenerationCache
    from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
    from agent_pull_requests.watch_replay import ConsumerScope
    from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore
    from agent_pull_requests.workspace_operations import WorkspaceOperations

    Rig = _fleet_fixtures()
    reader, _, _, evidence_document = native
    root = tmp_path / "client"
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(root))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    store = ReplayStore(
        tmp_path / "owner", "namespace",
        service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a"),
    )
    store.provision(conversations=True)
    cache = GenerationCache(store.root, store.namespace, max_bytes=4 * 1024 * 1024,
                            max_generations=20, service_identity=store.service_identity)
    cache.provision()
    workspace = WorkspaceOperations(
        store, cache, scope=dict(reader.scope), read_owner=reader, binding_ref="selected",
        checkpoint=lambda: None, max_work_seconds=10, conversations=True,
    )
    gateway = WatchGateway(
        store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        bindings=(WatchScopeBinding(
            "caller-a", "project", ConsumerScope("consumer-a", "account-a", "project/repository"),
        ),), workspaces={("caller-a", "project"): workspace},
    )

    async def scenario():
        tls = tmp_path / "tls"
        tls.mkdir()
        rig = Rig(tls)
        requests = []
        samples = []
        async def handle(local):
            local.checkpoint()
            requests.append(local.request.target)
            result = await gateway.handle(local.request)
            local.checkpoint()
            return result
        rig.h.document["manifest"]["slots"][0]["routes"][0]["capabilities"] = ["http"]
        manifest = GatewayManifest.parse(canonical(rig.h.document["manifest"]))
        try:
            await rig.start(local_handlers=(LocalHandlerBinding(manifest.slots[0], handle),))
            selected = connection(root)
            selected.update(gateway_origin=rig.origin, gateway_ca=rig.ca)
            write_profiles(root, selected)
            (root / "proof.pem").write_bytes(rig.h.keys[3].get_op_key("sign").private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            ))
            (root / "access").write_text(rig.access("caller-a", 3), encoding="utf-8")

            async def cli(command, request=None):
                rig.h.clock.value = int(time.time())
                (root / "access").write_text(rig.access("caller-a", 3), encoding="utf-8")
                args = [sys.executable, "-m", "agent_pull_requests", "workspace",
                        "--client-profile", "selected", command]
                if request is not None:
                    args += ["--request", "-"]
                started = time.monotonic()
                before = len(requests)
                result = await asyncio.to_thread(
                    run_caller_owned, args, timeout=60, max_stdout_bytes=observer.OUTPUT_BYTES,
                    max_stderr_bytes=65536,
                    input_text=json.dumps(request) if request is not None else None,
                    env=dict(os.environ),
                )
                samples.append({
                    "command": command, "duration_ms": round((time.monotonic() - started) * 1000),
                    "workspace_requests": len(requests) - before,
                })
                assert result.stderr == ""
                return result.returncode, json.loads(result.stdout)

            if change == "unchanged":
                code, capabilities = await cli("capabilities")
                assert code == 0
                assert set(capabilities["operations"]) == set(observer.READ_COMMANDS)
                from agent_pull_requests.native_read import PROFILE
                assert capabilities["backend_contract"]["read_acquisition"] == PROFILE
            loaded_code, loaded = await cli("load", {
                "number": 42, "expected_current": None, "budget_seconds": 10,
            })
            assert loaded_code == 0, loaded
            identity = loaded["result"]["descriptor"]["id"]
            if change != "refusals":
                value = evidence_document["evidence"]
                if change == "dialogue":
                    value["sections"]["discussions"]["data"][0]["comments"][0]["content"] = "Changed"
                elif change == "target":
                    value["sections"]["pull_request"]["data"]["lastMergeTargetCommit"]["commitId"] = "f" * 40
                elif change == "head":
                    value["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] = "e" * 40
                    value["sections"]["iterations"]["data"][-1]["sourceRefCommit"]["commitId"] = "e" * 40
                elif change == "partial":
                    value["sections"]["policies"].update(complete=False, errors=["unavailable"])
                for section in value["sections"].values():
                    section.update(observed_from="2026-10-30T00:00:02Z",
                                   observed_to="2026-10-30T00:00:03Z")
                update(native)
                code, synced = await cli("sync", {
                    "number": 42, "expected_current": identity, "previous_id": identity, "budget_seconds": 10,
                })
                assert code == 0, synced
                changed = synced["result"]["comparison"]["changed_sections"]
                if change == "unchanged":
                    assert changed == []
                else:
                    assert ("discussions" if change == "dialogue" else (
                        "policies" if change == "partial" else "pull_request"
                    )) in changed
                identity = synced["result"]["descriptor"]["id"]
                code, generation = await cli("materialize", {"number": 42, "generation_id": identity})
                assert code == 0 and generation["id"] == identity
                if change == "partial":
                    assert not generation["complete"]
                    assert generation["sections"]["policies"]["errors"] == ["unavailable"]
                if change in ("unchanged", "dialogue"):
                    code, events = await cli("events", {"number": 42, "after": None, "limit": 16})
                    assert code == 0 and events["result"]["high_water"]["sequence"] >= 1
                    assert "Changed" not in json.dumps(events)
                return
            before = len(requests)
            for command in ("submit", "inspect", "source", "source-read"):
                code, refused = await cli(command, {})
                assert code == 4 and refused["effect_completion"] == "not_inferred"
            assert len(requests) == before
            provider_calls = native[2].with_suffix(".calls").read_bytes()
            for field in ("account", "principal", "target"):
                current = selected["binding"][field]
                selected["binding"][field] = "unselected"
                write_profiles(root, selected)
                code, _ = await cli("load", {"number": 42, "expected_current": identity, "budget_seconds": 10})
                assert code != 0
                assert native[2].with_suffix(".calls").read_bytes() == provider_calls
                selected["binding"][field] = current
                write_profiles(root, selected)
            rig.h.document["callers"] = []
            await rig.update()
            before = len(requests)
            code, _ = await cli("capabilities")
            assert code != 0 and len(requests) == before
        finally:
            record_property("observer_cli_samples", json.dumps(samples))
            await gateway.drain(timeout=2)
            await rig.close()
    asyncio.run(scenario())
