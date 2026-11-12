"""Explicit native process proof; not an installed-consumer or durability claim."""

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest
from agent_procutil._daemon_instance_identity import birth_identity, identity_state
from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances

from agent_mcp.ipc import request_via_socket
from agent_mcp.serve import flip_data_handle

_spec = importlib.util.spec_from_file_location(
    "generation_native_fixture", Path(__file__).with_name("generation_native_fixture.py"),
)
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("h")


@pytest.mark.timeout(180)
@pytest.mark.parametrize("keeper_start", [
    "explicit", "launcher",
    pytest.param(
        "optional", marks=pytest.mark.skipif(
            not sys.platform.startswith("linux"), reason="optional receiver requires Linux",
        ),
    ),
])
async def test_native_slots_reattach_original_conduit_without_reinitializing(
    tmp_path, monkeypatch, record_testsuite_property, keeper_start,
):
    monkeypatch.setenv("UV_OFFLINE", "1")
    cache = Path(os.environ["COPILOT_EXTENSIONS_TEST_VENV_ROOT"]) / ".uv-cache"
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    home = tmp_path / "home"
    home.mkdir()
    actor = tmp_path / "actor.py"
    actor.write_text(fixture.ACTOR, encoding="utf-8")
    upstream = tmp_path / "upstream.py"
    upstream.write_text(fixture.UPSTREAM, encoding="utf-8")
    config = home / "bridge.json"
    config.write_text(json.dumps({
        "server": {"type": "stdio", "command": [sys.executable, str(upstream)]},
        "auth": {"kind": "none"},
    }), encoding="utf-8")
    fixed = home / "current" / "serve.sock"
    keeper = fixed.parent / "custody-v1" / fixed.name
    processes, clients, handles, upstreams = [], [], [], []
    caller_identities = []
    launch_traces, backend_traces = {}, {}
    cleanup = []
    with contextlib.ExitStack() as retained:
        root, slots = fixture.build_runtimes(tmp_path, retained)
        with daemon_test_lifecycle(
            owner="native-mcp-handover", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as lifetime:
            environment = dict(
                os.environ, **lifetime, HOME=str(home), USERPROFILE=str(home),
                AGENT_MCP_HOME=str(home), AGENT_MCP_SERVE_SOCKET=str(fixed),
                AGENT_MCP_INSTALL_DIR=str(root),
            )
            for key in ("PYTHONPATH", "AGENT_MCP_CONTROL_TOKEN", "VIRTUAL_ENV"):
                environment.pop(key, None)

            async def start(label, role, handle):
                trace = f"{label}:{role}:{len(processes)}"
                traced = {
                    **environment, "COPILOT_EXTENSIONS_BOOT_TRACE": "1",
                    "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN": "agent-mcp",
                    "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS": trace,
                }
                process = await fixture.spawn(
                    slots[label][1], actor, role, root, handle, fixed, config, traced, home,
                )
                processes.append(process)
                launch_traces[process] = trace
                return process

            async def backend(label, suffix=""):
                (root / "current-version").write_text(slots[label][0], encoding="utf-8")
                handle = fixed.parent / f"{label}{suffix}.sock"
                process = await start(label, "backend", handle)
                handles.append(handle)
                reply = await fixture.eventually(lambda: request_via_socket(handle, {"op": "ping"}))
                assert reply["version"] == slots[label][0]
                assert reply["runtime_root"] == str(root)
                assert reply["custody_backend"]
                backend_traces[reply["pid"]] = launch_traces[process]
                flip_data_handle(handle, legacy_path=fixed)
                return process, handle, reply["pid"]

            async def client(label):
                role = {
                    "explicit": "conduit", "launcher": "conduit-launcher",
                    "optional": "conduit-optional",
                }[keeper_start]
                process = await start(label, role, fixed)
                clients.append(process)
                if keeper_start == "optional":
                    async def owner_record():
                        return json.loads(
                            (home / f"{process.pid}.owner.json").read_text(encoding="utf-8"),
                        )
                    owner = await fixture.eventually(owner_record)
                    assert owner["owner_pid"] == process.pid
                    assert owner["owner_birth"] == birth_identity(process.pid)
                    assert owner["conduit_pid"] != process.pid
                    caller_identities.extend([
                        (owner["owner_pid"], owner["owner_birth"]),
                        (owner["conduit_pid"], owner["conduit_birth"]),
                    ])
                await fixture.send(process, {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                })
                reply = await fixture.receive(process)
                result = reply["result"]
                upstreams.append((result["pid"], birth_identity(result["pid"])))
                return process, result

            def physical(result, label, pid):
                assert result["pipeline_code"] == label
                assert result["backend_pid"] == pid
                assert Path(result["backend_prefix"]).resolve() == slots[label][1].resolve()
                assert Path(result["backend_module"]).resolve().is_relative_to(
                    slots[label][1].resolve(),
                )
                assert result["backend_trace"] == backend_traces[pid]
                assert result["upstream_trace"] != result["backend_trace"]

            def instances():
                return {
                    row["pid"] for row in inspect_instances(tmp_path / "instances")
                    if row["state"] == "running"
                }

            try:
                old, old_handle, old_pid = await backend("old")
                old_birth = birth_identity(old_pid)
                await fixture.check_owner_capabilities(
                    slots["old"], root, environment, home, selected=True,
                )
                await fixture.check_owner_capabilities(
                    slots["new"], root, environment, home, selected=False,
                )
                if keeper_start == "explicit":
                    await start("old", "keeper", keeper)
                    await fixture.eventually(lambda: request_via_socket(keeper, {"op": "ping"}))
                held, initialization = await client("old")
                physical(initialization, "old", old_pid)
                assert len(instances()) == 2
                keepers = [
                    row for row in inspect_instances(tmp_path / "instances")
                    if row["state"] == "running" and row["service"] == "agent-mcp-custody"
                ]
                assert len(keepers) == 1
                keeper_pid, keeper_birth = int(keepers[0]["pid"]), keepers[0]["birth"]
                if keeper_start in {"launcher", "optional"}:
                    assert Path(keepers[0]["source"]).is_relative_to(slots["old"][1])
                await fixture.send(held, {
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "mutate", "arguments": {}},
                })
                assert (await fixture.receive(held))["method"] == "notifications/progress"
                assert (await fixture.receive(held))["id"] == "server-approval"
                incoming, new_handle, new_pid = await backend("new")
                assert old_pid != new_pid
                await fixture.check_owner_capabilities(
                    slots["new"], root, environment, home, selected=True,
                )
                await fixture.check_owner_capabilities(
                    slots["old"], root, environment, home, selected=False,
                )
                fresh, fresh_initialization = await client("new")
                physical(fresh_initialization, "new", new_pid)
                assert fresh_initialization["pid"] != initialization["pid"]
                assert len(instances()) == 3
                assert (await request_via_socket(keeper, {"op": "ping"}))["attached"] == 2
                await fixture.send(held, {
                    "jsonrpc": "2.0", "method": "notifications/cancelled",
                    "params": {"requestId": 2},
                })
                await fixture.send(held, {"jsonrpc": "2.0", "id": "server-approval", "result": {}})
                mutation = await fixture.receive(held)
                assert mutation["id"] == 2
                physical(mutation["result"], "old", old_pid)
                assert mutation["result"]["mutations"] == 1
                assert (await fixture.receive(held))["method"] == "notifications/tools/list_changed"
                sequence = 10

                async def inspect(process=held):
                    nonlocal sequence
                    sequence += 1
                    await fixture.send(process, {
                        "jsonrpc": "2.0", "id": sequence, "method": "tools/call",
                        "params": {"name": "inspect", "arguments": {}},
                    })
                    return (await fixture.receive(process))["result"]

                adopted = await fixture.eventually(
                    inspect, lambda value: value["pipeline_code"] == "new",
                )
                physical(adopted, "new", new_pid)
                assert adopted["pid"] == initialization["pid"]
                assert adopted["upstream_trace"] == initialization["upstream_trace"]
                assert adopted["initialized"] == adopted["mutations"] == 1
                assert adopted["cancelled"] is True
                await fixture.shutdown(old_handle)
                await asyncio.wait_for(old.wait(), 10)
                assert old.returncode == 0
                assert identity_state(old_pid, old_birth) == "dead"
                physical(await inspect(), "new", new_pid)
                assert held.returncode is None and fresh.returncode is None
                assert identity_state(keeper_pid, keeper_birth) == "matching"
                assert incoming.returncode is None
                rollback, rollback_handle, rollback_pid = await backend("old")
                await fixture.check_owner_capabilities(
                    slots["old"], root, environment, home, selected=True,
                )
                await fixture.check_owner_capabilities(
                    slots["new"], root, environment, home, selected=False,
                )
                rolled_back = await fixture.eventually(
                    inspect, lambda value: value["pipeline_code"] == "old",
                )
                physical(rolled_back, "old", rollback_pid)
                assert rolled_back["pid"] == initialization["pid"]
                assert rolled_back["upstream_trace"] == initialization["upstream_trace"]
                assert rolled_back["initialized"] == rolled_back["mutations"] == 1
                reply = await fixture.eventually(
                    lambda: inspect(fresh), lambda value: value["pipeline_code"] == "old",
                )
                physical(reply, "old", rollback_pid)
                assert reply["pid"] == fresh_initialization["pid"] and reply["initialized"] == 1
                assert reply["upstream_trace"] == fresh_initialization["upstream_trace"]
                assert rollback.returncode is None and len(instances()) == 3
                await fixture.shutdown(new_handle)
                await asyncio.wait_for(incoming.wait(), 10)
                assert incoming.returncode == 0
                replacement, _, replacement_pid = await backend("old", "-replacement")
                assert replacement_pid != rollback_pid and len(instances()) == 3
                for process, initial in ((held, initialization), (fresh, fresh_initialization)):
                    result = await fixture.eventually(
                        lambda: inspect(process),
                        lambda value: value["backend_pid"] == replacement_pid,
                    )
                    physical(result, "old", replacement_pid)
                    assert result["pid"] == initial["pid"] and result["initialized"] == 1
                    assert result["upstream_trace"] == initial["upstream_trace"]
                await fixture.shutdown(rollback_handle)
                await asyncio.wait_for(rollback.wait(), 10)
                assert rollback.returncode == 0
                physical(await inspect(), "old", replacement_pid)
                assert replacement.returncode is None
                assert identity_state(keeper_pid, keeper_birth) == "matching"
                assert all(
                    identity_state(pid, birth) == "matching"
                    for pid, birth in caller_identities
                )
                record_testsuite_property("platform", sys.platform)
                record_testsuite_property("physical_generations_" + keeper_start, json.dumps({
                    label: generation for label, (generation, _) in slots.items()
                }))
                record_testsuite_property("shared_instances_peak", 3)
                record_testsuite_property("launch_traces_varied", True)
                for process in clients:
                    process.stdin.close()
                for process, initial in ((held, initialization), (fresh, fresh_initialization)):
                    final = await fixture.receive(process)
                    assert final["method"] == "notifications/upstream-eof"
                    assert final["params"]["pid"] == initial["pid"]
                    assert final["params"]["initialized"] == 1
                    assert await asyncio.wait_for(process.stdout.read(), 10) == b""
            finally:
                for process in clients:
                    process.stdin.close()
                for process in clients:
                    await asyncio.wait_for(process.wait(), 10)
                for handle in handles:
                    with contextlib.suppress(OSError):
                        await fixture.shutdown(handle)
                for process in processes:
                    await asyncio.wait_for(process.wait(), 10)
        assert cleanup and all(result.status in {"settled", "missing"} for result in cleanup)
        assert identity_state(keeper_pid, keeper_birth) == "dead"
        assert all(identity_state(pid, birth) == "dead" for pid, birth in upstreams)
        assert all(identity_state(pid, birth) == "dead" for pid, birth in caller_identities)
