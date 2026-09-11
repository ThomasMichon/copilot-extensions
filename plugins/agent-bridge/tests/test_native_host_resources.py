"""Synthetic host-resource contract tests; no external venue or browser."""

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge import native_resource_cli, native_runtime
from agent_bridge.native_manager import NativeManager, ProviderTransport, validate_request
from agent_bridge.native_resources import (
    CAPABILITY, DEFINITIONS_SCHEMA, DESCRIPTOR_SCHEMA, EMPTY_SCHEMA, HostResources,
    ResourceMailbox, descriptor, public_definitions, resource_result,
    validate_definitions, validate_input, validate_schema,
)
from agent_bridge.native_store import NativeError, NativeStore


@pytest.fixture
def definition(tmp_path):
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import json,pathlib,sys,time\n"
        "q=json.load(sys.stdin); p=pathlib.Path(q['stateDir'])/'owned.json'\n"
        "old=json.loads(p.read_text()) if p.exists() else None\n"
        "r={k:q[k] for k in ('schema','version','executionId','generation','resource','operationId')}\n"
        "r['ok']=True\n"
        "if q['operation']=='ensure':\n"
        " time.sleep(.03)\n"
        " data=old or {'operationId':q['operationId'],'creates':1,'ensures':0}\n"
        " assert data['operationId']==q['operationId']\n"
        " data['ensures']+=1;p.write_text(json.dumps(data))\n"
        " if q['config'].get('failOnce') and data['ensures']==1: sys.exit(3)\n"
        " r.update(owned=True,value={'marker':'RESOURCE_READY'},receipt={'scope':q['operationId']})\n"
        "else:\n"
        " if old: assert old['operationId']==q['operationId'];p.unlink()\n"
        " r['released']=True\n"
        "print(json.dumps(r))\n",
        encoding="utf-8",
    )
    return {"schema": DEFINITIONS_SCHEMA, "version": 1, "resources": {
        "preview": {"argv": [sys.executable, str(provider)], "config": {"privatePolicy": "local-only"}}
    }}


def host_store(tmp_path, definition):
    store = NativeStore(tmp_path / "controller")
    store.reserve("execution", "generation", "launch", "example", str(tmp_path), "hash",
                  {"spec": {"hostResources": definition}, "sessionId": "session"})
    store.update("execution", "generation", state="ready", represented=True)
    return store


def resource_request(request_id="request", **changes):
    return {"executionId": "execution", "generation": "generation", "sessionId": "session",
            "resource": "preview", "requestId": request_id, "input": {}, **changes}


def resource_directory(store):
    return store.root / "host-resources" / "execution" / "generation" / "preview"


def test_registration_is_inert_and_public_capability_has_no_local_policy(tmp_path, definition):
    assert validate_definitions(definition) == definition
    request = {"codespace": "example", "owner": str(tmp_path), "cwd": "/workspaces/example",
               "command": "exec copilot", "requestId": "launch"}
    assert "hostResources" not in validate_request(request)
    validated = validate_request({**request, "hostResources": definition})
    store = host_store(tmp_path, validated["hostResources"])
    HostResources(store, lambda: True)
    assert not (store.root / "host-resources").exists()
    public = public_definitions(definition)
    assert public == {"preview": EMPTY_SCHEMA}
    assert "privatePolicy" not in json.dumps(public)


@pytest.mark.guard
def test_host_resource_descriptor_and_result_match_captured_contract():
    path = Path(__file__).parents[1] / "contract" / "fixtures" / "http" / "current" / "native-resource-protocol.json"
    captured = json.loads(path.read_text())
    public = descriptor("execution-example", "generation-example", {"preview": EMPTY_SCHEMA})
    public["resources"]["preview"]["ensureCommand"][0] = "<official-remote-python>"
    assert public == captured["descriptor"]
    request = {
        "executionId": "execution-example", "generation": "generation-example",
        "sessionId": "session-example", "requestId": "request-example", "resource": "preview", "input": {},
    }
    assert resource_result(request, value={"endpoint": "http://127.0.0.1:45123"}) == captured["result"]
    assert EMPTY_SCHEMA == captured["empty_input_schema"]


@pytest.mark.parametrize("value", [
    {"port": 1234}, {"argv": ["unapproved"]}, {"profile": "other"}, [], {"x": float("nan")},
])
def test_remote_cannot_select_host_policy(value):
    with pytest.raises(NativeError):
        validate_input(value, EMPTY_SCHEMA)


def test_schema_is_closed_primitive_and_bounded():
    schema = {"type": "object", "additionalProperties": False, "properties": {
        "kind": {"type": "string", "enum": ["preview"]},
        "count": {"type": "integer"}, "show": {"type": "boolean"},
    }, "required": ["kind"]}
    assert validate_schema(schema) == schema
    assert validate_input({"kind": "preview", "count": 2, "show": False}, schema)
    for invalid in ({"kind": "other"}, {"count": 1}, {"kind": "preview", "count": True}):
        with pytest.raises(NativeError):
            validate_input(invalid, schema)
    with pytest.raises(NativeError):
        validate_schema({**schema, "additionalProperties": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("required,advertised", [(False, False), (True, False), (True, True)])
async def test_resource_capability_negotiation_reuses_existing_transport_argv(required, advertised):
    stream = asyncio.StreamReader()
    frame = {"event": "ready", "capability": "codespace-native-transport-v1", "version": 1}
    if advertised:
        frame["hostResources"] = CAPABILITY
    stream.feed_data(json.dumps(frame).encode() + b"\n")
    stream.feed_eof()
    transport = ProviderTransport([], {
        "id": "execution", "generation": "generation",
        "data": {"spec": {"hostResources": {}} if required else {}},
    }, lambda: None)
    transport.process = SimpleNamespace(stdout=stream)
    transport.ready = asyncio.get_running_loop().create_future()
    await transport._read()
    if required and not advertised:
        with pytest.raises(NativeError) as exc:
            await transport.ready
        assert exc.value.code == "resource_capability_unavailable"
    else:
        assert await transport.ready is True


@pytest.mark.asyncio
async def test_real_worker_coalesces_concurrent_ensure_and_releases_once(tmp_path, definition):
    store = host_store(tmp_path, definition)
    resources = HostResources(store, lambda: True)
    assert not resource_directory(store).exists()
    replies = await asyncio.gather(*[
        resources.ensure("execution", "generation", resource_request(str(i))) for i in range(2)
    ])
    assert all(reply["state"] == "ready" for reply in replies)
    state = json.loads((resource_directory(store) / "owned.json").read_text())
    assert state["creates"] == 1 and state["ensures"] == 2
    persisted = store.get("execution")["data"]["_host_resource_preview"]
    assert persisted["owned"] and persisted["attempted"]
    store.update("execution", "generation", state="stopping")
    await asyncio.gather(resources.cleanup("execution", "generation"), resources.cleanup("execution", "generation"))
    assert not (resource_directory(store) / "owned.json").exists()
    assert store.get("execution")["data"]["_host_resource_preview"]["released"]


@pytest.mark.asyncio
async def test_lost_ensure_reply_reconciles_same_scope_after_controller_restart(tmp_path, definition):
    definition["resources"]["preview"]["config"]["failOnce"] = True
    store = host_store(tmp_path, definition)
    first = HostResources(store, lambda: True)
    with pytest.raises(NativeError):
        await first.ensure("execution", "generation", resource_request())
    assert store.get("execution")["data"]["_host_resource_preview"]["attempted"]
    second = HostResources(NativeStore(store.root), lambda: True)
    reply = await second.ensure("execution", "generation", resource_request())
    assert reply["state"] == "ready"
    state = json.loads((resource_directory(store) / "owned.json").read_text())
    assert state["creates"] == 1 and state["ensures"] == 2
    store.update("execution", "generation", state="stopping")
    await second.cleanup("execution", "generation")


@pytest.mark.asyncio
@pytest.mark.parametrize("attempted", [False, True])
async def test_cleanup_never_requested_or_unowned_is_noop(tmp_path, definition, attempted):
    store = host_store(tmp_path, definition)
    invoke = AsyncMock(side_effect=AssertionError("unowned resource was invoked"))
    if attempted:
        store.update("execution", "generation", _host_resource_preview={"attempted": True, "owned": False})
    store.update("execution", "generation", state="stopping")
    await HostResources(store, lambda: True, invoke=invoke).cleanup("execution", "generation")
    invoke.assert_not_awaited()
    assert not resource_directory(store).exists()


@pytest.mark.asyncio
async def test_failed_ensure_still_has_created_only_cleanup_obligation(tmp_path, definition):
    definition["resources"]["preview"]["config"]["failOnce"] = True
    store = host_store(tmp_path, definition)
    resources = HostResources(store, lambda: True)
    with pytest.raises(NativeError):
        await resources.ensure("execution", "generation", resource_request())
    assert (resource_directory(store) / "owned.json").exists()
    store.update("execution", "generation", state="stopping")
    await resources.cleanup("execution", "generation")
    assert not (resource_directory(store) / "owned.json").exists()


@pytest.mark.asyncio
async def test_worker_lock_survives_controller_generation_boundary(tmp_path, definition):
    from single_instance_lease import SingleInstance

    store = host_store(tmp_path, definition)
    previous_worker_lock = SingleInstance(resource_directory(store), service="native-host-resource")
    with previous_worker_lock:
        with pytest.raises(NativeError) as exc:
            await HostResources(store, lambda: True).ensure("execution", "generation", resource_request())
        assert exc.value.code == "resource_busy"
        assert not (resource_directory(store) / "owned.json").exists()
    successor = HostResources(store, lambda: True)
    assert (await successor.ensure("execution", "generation", resource_request()))["state"] == "ready"
    store.update("execution", "generation", state="stopping")
    await successor.cleanup("execution", "generation")


@pytest.mark.asyncio
async def test_retired_native_keeps_cleanup_obligation_until_provider_confirms(tmp_path, definition):
    manager = NativeManager(tmp_path / "controller", lambda: pytest.fail("retirement proof already persisted"))
    store = manager.store(create=True)
    store.reserve("execution", "generation", "launch", "example", str(tmp_path), "hash", {
        "spec": {"hostResources": definition}, "launchRequested": True, "retired": True,
        "_host_resource_preview": {"attempted": True},
    })
    store.update("execution", "generation", state="stopping")
    calls = []

    async def invoke(definition, payload, owner):
        calls.append(payload)
        if len(calls) == 1:
            raise NativeError("resource_cleanup_unconfirmed", "Synthetic lost release acknowledgement")
        return {key: payload[key] for key in (
            "schema", "version", "executionId", "generation", "resource", "operationId",
        )} | {"ok": True, "released": True}

    manager._host_resources = HostResources(store, lambda: True, invoke=invoke)
    with pytest.raises(NativeError, match="lost release"):
        await manager.stop("execution", "generation")
    assert store.get("execution")["state"] == "stopping"
    assert store.get("execution")["data"]["retired"]
    assert (await manager.stop("execution", "generation"))["state"] == "stopped"
    assert calls[0] == calls[1]
    await manager.stop("execution", "generation")
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"generation": "foreign"}, {"executionId": "foreign"}, {"sessionId": "foreign"},
    {"resource": "unapproved"}, {"input": {"port": 9222}}, {"argv": ["unapproved"]},
])
async def test_foreign_and_invalid_requests_have_no_host_effect(tmp_path, definition, changes):
    store = host_store(tmp_path, definition)
    invoke = AsyncMock(side_effect=AssertionError("unauthorized host effect"))
    with pytest.raises(NativeError):
        await HostResources(store, lambda: True, invoke=invoke).ensure(
            "execution", "generation", resource_request(**changes),
        )
    invoke.assert_not_awaited()
    assert not resource_directory(store).exists()


def test_mailbox_idempotency_reply_binding_and_stop_refusal(tmp_path):
    store = NativeStore(tmp_path)
    store.reserve("execution", "generation", "launch", "example", "owner", "hash",
                  {"resourceDefinitions": {"preview": EMPTY_SCHEMA}, "sessionId": "session"})
    store.update("execution", "generation", state="ready")
    mailbox = ResourceMailbox(store)
    request = resource_request()
    mailbox.submit(request)
    mailbox.submit(request)
    assert mailbox.pending("execution", "generation") == [request]
    with pytest.raises(NativeError):
        mailbox.submit({**request, "sessionId": "foreign"})
    result = resource_result(request, value={"ready": True})
    with pytest.raises(NativeError):
        mailbox.complete("execution", "generation", {**result, "sessionId": "foreign"})
    assert mailbox.complete("execution", "generation", result)["recorded"]
    assert mailbox.complete("execution", "generation", result)["recorded"]
    assert mailbox.result("execution", "generation", "request") == result
    assert not mailbox.pending("execution", "generation")
    store.update("execution", "generation", state="stopping")
    with pytest.raises(NativeError):
        mailbox.submit(resource_request("next"))


@pytest.mark.asyncio
async def test_native_status_carries_real_mailbox_request_after_frontend_detach(tmp_path, definition, monkeypatch):
    remote = NativeStore(tmp_path / "venue")
    observed = {"launches": 0, "replyLost": True}
    mailbox = ResourceMailbox(remote)

    class Transport:
        def __init__(self, prefix, row, reserved, on_progress=None):
            self.row, self.reserved = row, reserved

        async def start(self, **kwargs):
            self.reserved()

        async def request(self, method, params=None):
            execution, generation = self.row["id"], self.row["generation"]
            if method == "launch":
                observed["launches"] += 1
                assert params["hostResources"] == {"preview": EMPTY_SCHEMA}
                assert "privatePolicy" not in json.dumps(params)
                remote.reserve(execution, generation, execution, "example", "owner", "hash",
                               {"resourceDefinitions": params["hostResources"], "sessionId": "session"})
                remote.update(execution, generation, state="ready")
            if method == "resource-complete":
                if observed["replyLost"]:
                    observed["replyLost"] = False
                    raise ConnectionError("synthetic lost acknowledgement")
                return mailbox.complete(execution, generation, params)
            if method == "stop":
                return {"retired": True, "state": "stopped", "recovery": {"ok": False}}
            return {"state": "ready", "sessionId": "session", "represented": True,
                    "activated": True, "resourceRequests": mailbox.pending(execution, generation)}

        async def close(self):
            pass

    def manager():
        return NativeManager(tmp_path / "controller", lambda: ["provider"], transport_factory=Transport)

    first = manager()
    receipt = await first.start({"requestId": "launch", "codespace": "example", "owner": str(tmp_path),
                                "cwd": "/workspaces/example", "command": "exec copilot", "hostResources": definition})
    await asyncio.gather(*list(first.tasks.values()))
    assert not (first.root / "host-resources").exists()
    await first.shutdown()  # presentation/controller loss never stops the venue
    second = manager()
    execution, generation = receipt["executionId"], receipt["generation"]
    request = resource_request(executionId=execution, generation=generation)
    mailbox.submit(request)
    await second.status(execution, generation)
    await asyncio.gather(*list(second.tasks.values()))
    await asyncio.gather(*list(second.resource_tasks.values()))
    assert mailbox.result(execution, generation, "request") is None
    await second.status(execution, generation)
    await asyncio.gather(*list(second.resource_tasks.values()))
    result = mailbox.result(execution, generation, "request")
    assert result["state"] == "ready" and result["sessionId"] == "session"
    assert result["executionId"] == execution and result["generation"] == generation
    assert observed["launches"] == 1
    directory = first.root / "host-resources" / execution / generation / "preview"
    assert json.loads((directory / "owned.json").read_text())["creates"] == 1
    stopped = await second.stop(execution, generation)
    assert stopped["state"] == "stopped" and not (directory / "owned.json").exists()
    await second.shutdown()


@pytest.mark.parametrize("descendant", [False, True])
def test_remote_helper_requires_native_ancestry_and_returns_only_capability(tmp_path, monkeypatch, descendant):
    store = NativeStore(tmp_path / "venue")
    store.reserve("execution", "generation", "launch", "example", "owner", "hash",
                  {"resourceDefinitions": {"preview": EMPTY_SCHEMA}, "sessionId": "session"})
    store.update("execution", "generation", state="ready")
    descriptor = {"schema": DESCRIPTOR_SCHEMA, "version": 1, "capability": CAPABILITY,
                  "executionId": "execution", "generation": "generation",
                  "resources": {"preview": {"inputSchema": EMPTY_SCHEMA, "ensureCommand": [
                      sys.executable, "-m", "agent_bridge", "native", "resource", "ensure", "preview", "--json",
                  ]}}}
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(descriptor))
    monkeypatch.setenv("AGENT_BRIDGE_NATIVE_RESOURCES", str(path))
    monkeypatch.setenv("AGENT_BRIDGE_NATIVE_EXECUTION_ID", "execution")
    monkeypatch.setenv("AGENT_BRIDGE_NATIVE_GENERATION", "generation")
    runtime = SimpleNamespace(store=store, status=lambda *a, **k: {
        "state": "ready", "represented": True, "sessionId": "session", "activated": True,
        "host": {"child_pid": 1234, "child_start_ticks": "ticks", "boot_id": "boot"},
    })
    monkeypatch.setattr(native_runtime, "NativeRuntime", lambda: runtime)
    monkeypatch.setattr(native_runtime, "descendant_of", lambda *a: descendant)
    monkeypatch.setattr(native_runtime, "process_matches", lambda *a: True)
    monkeypatch.setattr("agent_bridge.client.BridgeClient.from_config", lambda: object())
    args = SimpleNamespace(resource_action="descriptor")
    if descendant:
        assert native_resource_cli.command(args) == descriptor
    else:
        with pytest.raises(NativeError, match="registered native child"):
            native_resource_cli.command(args)


def test_cli_missing_native_descriptor_never_ensures_a_local_daemon(monkeypatch, capsys):
    from agent_bridge import __main__ as cli
    from agent_bridge.native_cli import command

    monkeypatch.delenv("AGENT_BRIDGE_NATIVE_RESOURCES", raising=False)
    monkeypatch.setattr(cli, "_get_client", lambda **_: pytest.fail("resource helper started a local daemon"))
    args = cli.build_parser().parse_args(["native", "resource", "ensure", "preview", "--json"])
    with pytest.raises(SystemExit) as exc:
        command(args)
    assert exc.value.code == 69
    assert json.loads(capsys.readouterr().out)["error"] == "resource_capability_unavailable"


def test_invalid_local_registration_is_rejected_before_controller_ensure(tmp_path, monkeypatch, capsys):
    from agent_bridge import __main__ as cli
    from agent_bridge.native_cli import command

    program = tmp_path / "command.txt"
    program.write_text("exec copilot")
    registration = tmp_path / "resources.json"
    registration.write_text(json.dumps({"schema": "wrong"}))
    monkeypatch.setattr(cli, "_get_client", lambda **_: pytest.fail("invalid resources reached controller"))
    args = cli.build_parser().parse_args([
        "native", "start", "--codespace", "example", "--owner", str(tmp_path),
        "--cwd", "/workspaces/example", "--request-id", "launch", "--command-file", str(program),
        "--host-resources-file", str(registration), "--json",
    ])
    with pytest.raises(SystemExit):
        command(args)
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_resource"
