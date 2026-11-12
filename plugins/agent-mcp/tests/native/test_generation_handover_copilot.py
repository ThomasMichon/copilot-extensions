"""Actual offline Copilot discovery across private physical runtime handovers."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Callable

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity, identity_state
from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances

from agent_mcp.ipc import request_via_socket
from agent_mcp.serve import flip_data_handle

_spec = importlib.util.spec_from_file_location(
    "copilot_handover_fixture", Path(__file__).parents[1] / "generation_native_fixture.py",
)
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux exec"),
]

UPSTREAM = r"""
import json, os, sys, threading
from pathlib import Path
trigger, receipt = map(Path, sys.argv[1:])
lock = threading.Lock()
stopped = threading.Event()
initialized = revision = 0
def emit(message):
    with lock:
        print(json.dumps(message), flush=True)
def notify():
    global revision
    while not stopped.wait(.02):
        try:
            current = int(trigger.read_text())
        except (OSError, ValueError):
            continue
        if current != revision:
            revision = current
            emit({"jsonrpc":"2.0","method":"notifications/tools/list_changed"})
thread = threading.Thread(target=notify, daemon=True)
thread.start()
try:
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message.get("method")
        result = {"pid":os.getpid(),"initialized":initialized,"revision":revision}
        if method == "initialize":
            initialized += 1
            result.update(
                initialized=initialized,
                protocolVersion=message["params"]["protocolVersion"],
                capabilities={"tools":{"listChanged":True}},
                serverInfo={"name":"physical-owner-fixture","version":"1"},
            )
        elif method == "tools/list":
            result["tools"] = [{
                "name":"fixture_"+str(revision),"description":"Offline discovery fixture",
                "inputSchema":{"type":"object","properties":{}},
            }]
        emit({"jsonrpc":"2.0","id":message["id"],"result":result})
finally:
    stopped.set()
    thread.join(1)
    receipt.write_text(json.dumps({"pid":os.getpid(),"initialized":initialized}))
"""


async def rpc(
    process: asyncio.subprocess.Process, stderr: asyncio.Task[bytes], identifier: int,
    method: str, params: dict,
) -> dict:
    process.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
    }) + "\n").encode())
    await process.stdin.drain()
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), 30)
        assert line, (await asyncio.wait_for(stderr, 5)).decode("utf-8", "replace")
        message = json.loads(line)
        if message.get("id") == identifier and "method" not in message:
            assert "error" not in message, message
            return message["result"]


@pytest.mark.timeout(180)
async def test_actual_native_client_rediscovers_through_replacement_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select the pinned native Copilot asset on a disposable runner")
    executable = Path(executable)
    assert executable.is_absolute() and executable.name == "copilot" and executable.is_file()
    monkeypatch.setenv("UV_OFFLINE", "1")
    cache = Path(os.environ["COPILOT_EXTENSIONS_TEST_VENV_ROOT"]) / ".uv-cache"
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    actor, upstream = tmp_path / "actor.py", tmp_path / "upstream.py"
    actor.write_text(fixture.ACTOR, encoding="utf-8")
    upstream.write_text(UPSTREAM, encoding="utf-8")
    trigger, receipt = tmp_path / "refresh", tmp_path / "upstream-eof.json"
    observations = tmp_path / "responses.jsonl"
    config = home / "bridge.json"
    config.write_text(json.dumps({
        "server": {"type": "stdio", "command": [
            sys.executable, str(upstream), str(trigger), str(receipt),
        ]}, "auth": {"kind": "none"},
    }), encoding="utf-8")
    fixed = workspace / "current" / "serve.sock"
    processes, handles, cleanup = [], [], []
    client = stderr = None
    identities = []
    sequence = 0
    with contextlib.ExitStack() as retained:
        root, slots = fixture.build_runtimes(tmp_path, retained, observations=observations)
        with daemon_test_lifecycle(
            owner="native-copilot-mcp-handover", registry_root=tmp_path / "instances",
            cleanup_results=cleanup,
        ) as lifetime:
            environment = {
                key: value for key, value in os.environ.items()
                if not key.startswith(("COPILOT_", "AGENT_", "WORKTREE_", "OTEL_"))
                and key not in {
                    "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE",
                    "PYTHONPATH", "VIRTUAL_ENV",
                }
            }
            environment.update(
                **lifetime, HOME=str(home), USERPROFILE=str(home),
                COPILOT_HOME=str(home / ".copilot"), COPILOT_OFFLINE="true",
                COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:1",
                COPILOT_PROVIDER_MODEL_ID="gpt-4.1", COPILOT_MODEL="gpt-4.1", USE_TGREP="false",
                AGENT_MCP_HOME=str(workspace), AGENT_MCP_SERVE_SOCKET=str(fixed),
                AGENT_MCP_INSTALL_DIR=str(root),
                COPILOT_EXTENSIONS_BOOT_TRACE="1",
                COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN="agent-mcp",
                COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS="original-native-client",
            )

            async def backend(label: str) -> tuple[asyncio.subprocess.Process, Path, int]:
                (root / "current-version").write_text(slots[label][0], encoding="utf-8")
                handle = fixed.parent / f"backend-{len(processes)}.sock"
                traced = {
                    **environment, "COPILOT_EXTENSIONS_BOOT_TRACE": "1",
                    "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN": "agent-mcp",
                    "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS": str(handle),
                }
                process = await fixture.spawn(
                    slots[label][1], actor, "backend", root, handle, fixed, config, traced, workspace,
                )
                processes.append(process)
                handles.append(handle)
                reply = await fixture.eventually(lambda: request_via_socket(handle, {"op": "ping"}))
                assert reply["version"] == slots[label][0] and reply["custody_backend"]
                identities.append((reply["pid"], birth_identity(reply["pid"])))
                flip_data_handle(handle, legacy_path=fixed)
                return process, handle, reply["pid"]

            async def discovery(label: str, pid: int, *, refresh: bool = True) -> dict:
                nonlocal sequence

                async def observe() -> dict:
                    nonlocal sequence
                    if refresh:
                        sequence += 1
                        trigger.write_text(str(sequence), encoding="utf-8")
                        await asyncio.sleep(.1)
                    rows = observations.read_text(encoding="utf-8").splitlines() if observations.exists() else []
                    results = [
                        row["response"]["result"] for row in map(json.loads, rows)
                        if row["method"] == "tools/list"
                    ]
                    return results[-1] if results else {}

                result = await fixture.eventually(
                    observe, lambda value: value.get("backend_pid") == pid,
                )
                assert result["pipeline_code"] == label
                assert Path(result["backend_prefix"]).resolve() == slots[label][1].resolve()
                assert Path(result["backend_module"]).resolve().is_relative_to(slots[label][1].resolve())
                assert result["initialized"] == 1
                assert result["backend_trace"] == str(handles[-1])
                return result

            try:
                old, old_handle, old_pid = await backend("old")
                python = slots["old"][1] / "bin" / "python"
                entries = home / "entries.json"
                entries.write_text(json.dumps({"mcpServers": {"physical-fixture": {
                    "type": "local", "command": str(python),
                    "args": ["-I", "-m", "agent_mcp", "bridge", "--config", str(config)],
                    "tools": ["*"],
                }}}), encoding="utf-8")
                client = await asyncio.create_subprocess_exec(
                    str(python), "-I", "-m", "agent_mcp", "exec-client",
                    "--mcp-config", str(entries), "--server", "physical-fixture", "--",
                    str(executable), "--acp", "--stdio", "--no-auto-update",
                    "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
                    "--log-dir", str(tmp_path / "logs"),
                    cwd=workspace, env=environment, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **no_window_kwargs(),
                )
                stderr = asyncio.create_task(client.stderr.read())
                client_birth = birth_identity(client.pid)
                initialized = await rpc(client, stderr, 1, "initialize", {
                    "protocolVersion": 1, "clientCapabilities": {},
                    "clientInfo": {"name": "physical-owner-fixture", "version": "1"},
                })
                assert initialized["protocolVersion"] == 1
                await rpc(client, stderr, 2, "session/new", {"cwd": str(workspace), "mcpServers": []})
                first = await discovery("old", old_pid, refresh=False)
                upstream_identity = first["pid"], birth_identity(first["pid"])
                owner = json.loads(observations.with_suffix(".owner.json").read_text(encoding="utf-8"))
                assert (owner["owner_pid"], owner["owner_birth"]) == (client.pid, client_birth)
                conduit_identity = owner["conduit_pid"], owner["conduit_birth"]
                assert conduit_identity[0] != client.pid
                keepers = [
                    row for row in inspect_instances(tmp_path / "instances")
                    if row["state"] == "running" and row["service"] == "agent-mcp-custody"
                ]
                assert len(keepers) == 1
                keeper_identity = keepers[0]["pid"], keepers[0]["birth"]
                for label in ("new", "old", "old"):
                    incoming, _, incoming_pid = await backend(label)
                    result = await discovery(label, incoming_pid)
                    assert result["pid"] == upstream_identity[0]
                    assert client.returncode is None
                    assert identity_state(client.pid, client_birth) == "matching"
                    assert identity_state(*keeper_identity) == "matching"
                    assert identity_state(*conduit_identity) == "matching"
                    await fixture.shutdown(old_handle)
                    await asyncio.wait_for(old.wait(), 10)
                    assert old.returncode == 0
                    old, old_handle = incoming, handles[-1]
                record_testsuite_property("actual_native_client", str(executable.name))
                record_testsuite_property("physical_generations", json.dumps({
                    label: generation for label, (generation, _) in slots.items()
                }))
                record_testsuite_property("discovery_after_handover", 3)
            finally:
                if client is not None:
                    if client.returncode is None:
                        client.terminate()
                    await asyncio.wait_for(client.wait(), 10)
                    await asyncio.wait_for(stderr, 5)
                for handle in handles:
                    with contextlib.suppress(OSError):
                        await fixture.shutdown(handle)
                for process in processes:
                    await asyncio.wait_for(process.wait(), 10)
        assert cleanup and all(result.status in {"settled", "missing"} for result in cleanup)
        assert identity_state(client.pid, client_birth) == "dead"
        assert identity_state(*upstream_identity) == "dead"
        assert identity_state(*keeper_identity) == "dead"
        assert identity_state(*conduit_identity) == "dead"
        assert all(identity_state(pid, birth) == "dead" for pid, birth in identities)
        assert json.loads(receipt.read_text(encoding="utf-8")) == {
            "pid": upstream_identity[0], "initialized": 1,
        }
