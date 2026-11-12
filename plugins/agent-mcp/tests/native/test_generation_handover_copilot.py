"""Actual offline Copilot protocol traffic across private physical handovers."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import signal
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
_spec = importlib.util.spec_from_file_location(
    "copilot_canned_provider", Path(__file__).with_name("generation_copilot_provider.py"),
)
provider = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(provider)

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux exec"),
]

UPSTREAM = r"""
import json, os, signal, sys, threading, time
from pathlib import Path
trigger, receipt, replies, calls, hold, release = map(Path, sys.argv[1:])
lock = threading.Lock()
stopped = threading.Event()
initialized = revision = 0
boundary = "interrupted"
def terminate(number, _frame):
    global boundary
    boundary = "signal-" + str(number)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, terminate)
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
            emit({"jsonrpc":"2.0","id":"fixture-ping-"+str(revision),"method":"ping"})
thread = threading.Thread(target=notify, daemon=True)
thread.start()
try:
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        if "method" not in message:
            with replies.open("a") as output:
                output.write(json.dumps(message)+"\n")
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
            cursor = message.get("params",{}).get("cursor")
            if cursor is not None:
                result.update(tools=[], acknowledged=cursor)
            else:
                result.update(nextCursor=str(revision), tools=[{
                    "name":"fixture_"+str(revision),"description":"Offline discovery fixture",
                    "inputSchema":{"type":"object","properties":{"nonce":{"type":"string"}},
                                   "required":["nonce"]},
                }])
        elif method == "tools/call":
            assert message["params"]["name"] == "fixture_0"
            nonce = message["params"]["arguments"]["nonce"]
            with calls.open("a") as output:
                output.write(json.dumps({"nonce":nonce,"pid":os.getpid()})+"\n")
            if hold.is_file() and hold.read_text() == nonce:
                deadline = time.monotonic() + 10
                while not release.is_file() or release.read_text() != nonce:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("fixture tool release deadline expired")
                    time.sleep(.02)
            result.update(content=[{"type":"text","text":"fixture-result:"+nonce}])
        emit({"jsonrpc":"2.0","id":message["id"],"result":result})
    boundary = "stdin-eof"
finally:
    stopped.set()
    thread.join(1)
    receipt.write_text(json.dumps({
        "pid":os.getpid(),"initialized":initialized,"boundary":boundary,
    }))
"""


@pytest.mark.timeout(180)
async def test_actual_native_client_responds_through_replacement_and_rollback(
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
    trigger, receipt = tmp_path / "refresh", tmp_path / "upstream-exit.json"
    replies = tmp_path / "client-replies.jsonl"
    calls = tmp_path / "tool-calls.jsonl"
    hold, release = tmp_path / "hold-tool", tmp_path / "release-tool"
    observations = tmp_path / "responses.jsonl"
    caller_context = tmp_path / "caller-context.json"
    config = home / "bridge.json"
    config.write_text(json.dumps({
        "server": {"type": "stdio", "command": [
            sys.executable, str(upstream), str(trigger), str(receipt), str(replies),
            str(calls), str(hold), str(release),
        ]}, "auth": {"kind": "none"},
    }), encoding="utf-8")
    fixed = workspace / "current" / "serve.sock"
    processes, handles, cleanup = [], [], []
    client = stderr = session = None
    pending = None
    identities = []
    sequence = 0
    model = provider.CannedProvider()
    with contextlib.ExitStack() as retained:
        root, slots = fixture.build_runtimes(
            tmp_path, retained, observations=observations, caller_context=caller_context,
        )
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
                COPILOT_EXTENSIONS_TEST_CONTAINED=os.environ["COPILOT_EXTENSIONS_TEST_CONTAINED"],
                PWD=str(workspace), COPILOT_DISABLE_LOGIN_SHELL_ENV="1",
                COPILOT_HOME=str(home / ".copilot"), COPILOT_OFFLINE="true",
                COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:1",
                COPILOT_PROVIDER_MODEL_ID="gpt-4.1", COPILOT_MODEL="gpt-4.1", USE_TGREP="false",
                AGENT_MCP_HOME=str(workspace), AGENT_MCP_SERVE_SOCKET=str(fixed),
                AGENT_MCP_INSTALL_DIR=str(root),
                COPILOT_EXTENSIONS_BOOT_TRACE="1",
                COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN="agent-mcp",
                COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS="original-native-client",
            )

            backend_environment = environment

            async def backend(label: str) -> tuple[asyncio.subprocess.Process, Path, int]:
                (root / "current-version").write_text(slots[label][0], encoding="utf-8")
                handle = fixed.parent / f"backend-{len(processes)}.sock"
                traced = {
                    **backend_environment, "COPILOT_EXTENSIONS_BOOT_TRACE": "1",
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

            async def exchange(label: str, pid: int, *, refresh: bool = True) -> dict:
                nonlocal sequence
                previous = sequence if refresh else -1

                def rows() -> list[dict]:
                    lines = observations.read_text(encoding="utf-8").splitlines() if observations.exists() else []
                    return list(map(json.loads, lines))

                async def observe() -> dict:
                    nonlocal sequence
                    if refresh:
                        sequence += 1
                        trigger.write_text(str(sequence), encoding="utf-8")
                        await asyncio.sleep(.1)
                    observed = rows()
                    for row in reversed(observed):
                        result = row["response"]["result"]
                        if refresh:
                            reply_id = row["request"].get("id")
                            if (
                                row["method"] == "client/reply"
                                and isinstance(reply_id, str)
                                and reply_id.startswith("fixture-ping-")
                                and int(reply_id.removeprefix("fixture-ping-")) > previous
                                and result["backend_pid"] == pid
                                and replies.is_file()
                                and any(
                                    reply.get("id") == reply_id and reply.get("result") == {}
                                    for reply in map(json.loads, replies.read_text().splitlines())
                                )
                            ):
                                return result
                            continue
                        if row["method"] != "tools/list":
                            continue
                        token = result.get("nextCursor")
                        if token is None or int(token) <= previous or result.get("backend_pid") != pid:
                            continue
                        if any(
                            ack["request"].get("params", {}).get("cursor") == token
                            and ack["response"]["result"].get("backend_pid") == pid
                            for ack in observed
                        ):
                            return result
                    return {}

                result = await fixture.eventually(
                    observe, lambda value: value.get("backend_pid") == pid,
                )
                assert result["pipeline_code"] == label
                assert Path(result["backend_prefix"]).resolve() == slots[label][1].resolve()
                assert Path(result["backend_module"]).resolve().is_relative_to(slots[label][1].resolve())
                if not refresh:
                    assert result["initialized"] == 1
                assert result["backend_trace"] == str(handles[-1])
                return result

            try:
                await model.start()
                environment.update(
                    COPILOT_PROVIDER_BASE_URL=model.url,
                    COPILOT_PROVIDER_WIRE_API="completions",
                )
                (root / "current-version").write_text(slots["old"][0], encoding="utf-8")
                python = slots["old"][1] / "bin" / "python"
                entries = home / "entries.json"
                entries.write_text(json.dumps({"mcpServers": {"physical-fixture": {
                    "type": "local", "command": str(python),
                    "cwd": str(workspace),
                    "args": ["-I", "-m", "agent_mcp", "bridge", "--config", str(config)],
                    "tools": ["*"],
                }}}), encoding="utf-8")
                client = await asyncio.create_subprocess_exec(
                    str(python), "-I", "-m", "agent_mcp", "exec-client",
                    "--mcp-config", str(entries), "--server", "physical-fixture", "--",
                    str(executable), "-C", str(workspace), "--acp", "--stdio", "--no-auto-update",
                    "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
                    "--allow-tool=physical-fixture(fixture_0)",
                    "--log-dir", str(tmp_path / "logs"),
                    cwd=workspace, env=environment, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **no_window_kwargs(),
                )
                stderr = asyncio.create_task(client.stderr.read())
                client_birth = birth_identity(client.pid)
                initialized, _ = await provider.rpc(client, stderr, 1, "initialize", {
                    "protocolVersion": 1, "clientCapabilities": {},
                    "clientInfo": {"name": "physical-owner-fixture", "version": "1"},
                })
                assert initialized["protocolVersion"] == 1
                session = asyncio.create_task(provider.rpc(
                    client, stderr, 2, "session/new", {"cwd": str(workspace), "mcpServers": []},
                ))

                async def prepared_context() -> dict:
                    if not caller_context.is_file():
                        return {}
                    return json.loads(caller_context.read_text(encoding="utf-8"))

                # The native client adds its own semantic environment before MCP exec.
                # Prepare the private backend from that exact pre-effect caller context.
                backend_environment = await fixture.eventually(prepared_context)
                old, old_handle, old_pid = await backend("old")
                caller_context.with_suffix(".ready").write_text("ready", encoding="utf-8")
                opened, _ = await session
                first = await exchange("old", old_pid, refresh=False)
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
                keeper_identity = int(keepers[0]["pid"]), keepers[0]["birth"]

                def retained_identities() -> None:
                    assert client.returncode is None
                    assert identity_state(client.pid, client_birth) == "matching"
                    assert identity_state(*keeper_identity) == "matching"
                    assert identity_state(*conduit_identity) == "matching"
                    assert identity_state(*upstream_identity) == "matching"

                async def tool_call(label: str, pid: int) -> None:
                    nonce = "native-fixture-" + str(len(model.calls) + 1)
                    model.nonce = nonce
                    result, text = await provider.rpc(client, stderr, 10 + len(model.calls), "session/prompt", {
                        "sessionId": opened["sessionId"],
                        "prompt": [{"type": "text", "text": "Run the fixture tool with nonce " + nonce}],
                    })
                    assert not model.errors, model.errors
                    assert result["stopReason"] == "end_turn", result
                    assert "fixture-complete:" + nonce in text
                    assert nonce in model.results
                    accepted = list(map(json.loads, calls.read_text().splitlines()))
                    assert [row["nonce"] for row in accepted] == model.calls
                    assert all(row["pid"] == upstream_identity[0] for row in accepted)
                    witnessed = [
                        json.loads(line) for line in observations.read_text().splitlines()
                        if json.loads(line)["method"] == "tools/call"
                        and json.loads(line)["request"]["params"]["arguments"]["nonce"] == nonce
                    ]
                    assert len(witnessed) == 1
                    response = witnessed[0]["response"]["result"]
                    assert response["backend_pid"] == pid and response["pipeline_code"] == label
                    assert response["content"] == [{"type": "text", "text": "fixture-result:" + nonce}]
                    retained_identities()

                await tool_call("old", old_pid)
                previous_label = "old"
                for label in ("new", "old", "old"):
                    nonce = "native-fixture-" + str(len(model.calls) + 1)
                    hold.write_text(nonce, encoding="utf-8")
                    pending = asyncio.create_task(tool_call(previous_label, old.pid))

                    async def accepted_call() -> bool:
                        return any(
                            row["nonce"] == nonce
                            for row in map(json.loads, calls.read_text().splitlines())
                        )

                    await fixture.eventually(accepted_call)
                    incoming, _, incoming_pid = await backend(label)

                    async def quiescing_call() -> bool:
                        path = observations.with_suffix(".quiescing")
                        return path.is_file() and any(
                            row["backend_pid"] == old.pid and row["accepted"] > 0
                            and row["settlers"] > 0
                            for row in map(json.loads, path.read_text().splitlines())
                        )

                    await fixture.eventually(quiescing_call)
                    assert not pending.done() and nonce not in model.results
                    assert model.calls.count(nonce) == 1
                    retained_identities()
                    release.write_text(nonce, encoding="utf-8")
                    await pending
                    await exchange(label, incoming_pid)
                    retained_identities()
                    await fixture.shutdown(old_handle)
                    await asyncio.wait_for(old.wait(), 10)
                    assert old.returncode == 0
                    await exchange(label, incoming_pid)
                    retained_identities()
                    await tool_call(label, incoming_pid)
                    old, old_handle = incoming, handles[-1]
                    previous_label = label
                sequence += 1
                dropped = "fixture-ping-" + str(sequence)
                observations.with_suffix(".drop").write_text(dropped, encoding="utf-8")
                trigger.write_text(str(sequence), encoding="utf-8")

                async def dropped_request() -> bool:
                    path = observations.with_suffix(".dropped")
                    return path.is_file() and path.read_text(encoding="utf-8") == dropped

                await fixture.eventually(dropped_request)
                await asyncio.sleep(1)
                assert not any(
                    json.loads(line).get("id") == dropped
                    for line in replies.read_text(encoding="utf-8").splitlines()
                )
                retained_identities()
                observations.with_suffix(".release").write_text("release", encoding="utf-8")

                async def released_reply() -> bool:
                    return any(
                        reply.get("id") == dropped and reply.get("result") == {}
                        for reply in map(json.loads, replies.read_text(encoding="utf-8").splitlines())
                    )

                await fixture.eventually(released_reply)
                nonce = "native-fixture-unknown"
                model.nonce = model.unknown_nonce = nonce
                hold.write_text(nonce, encoding="utf-8")
                pending = asyncio.create_task(provider.rpc(
                    client, stderr, 99, "session/prompt", {
                        "sessionId": opened["sessionId"],
                        "prompt": [{"type": "text", "text": "Run the fixture tool with nonce " + nonce}],
                    },
                ))
                await fixture.eventually(accepted_call)
                incoming, _, incoming_pid = await backend("new")
                await fixture.eventually(quiescing_call)
                assert not pending.done() and nonce not in model.results
                retained_identities()
                observations.with_suffix(".lose-tool").write_text(nonce, encoding="utf-8")
                release.write_text(nonce, encoding="utf-8")

                async def uncertain_call() -> dict:
                    loss = observations.with_suffix(".lost-tool")
                    unknown = observations.with_suffix(".unknown")
                    if not loss.is_file() or not unknown.is_file():
                        return {}
                    dropped = json.loads(loss.read_text())
                    assert dropped["backend_pid"] == old.pid and dropped["nonce"] == nonce
                    for row in map(json.loads, unknown.read_text().splitlines()):
                        if any(
                            message.get("params", {}).get("arguments", {}).get("nonce") == nonce
                            for message in row["accepted"]
                        ):
                            assert row["keeper_pid"] == keeper_identity[0]
                            assert row["uncertain"] and row["fenced"]
                            return row
                    return {}

                await fixture.eventually(uncertain_call)
                await asyncio.sleep(1)
                assert nonce not in model.results
                assert model.calls.count(nonce) == 1
                assert not model.errors, model.errors
                if pending.done():
                    result, text = await pending
                    assert result["stopReason"] == "end_turn", result
                    assert "fixture-unknown:" + nonce in text
                    assert "fixture-complete:" + nonce not in text
                async def failed_stream_closed() -> bool:
                    return identity_state(*conduit_identity) == "dead"

                await fixture.eventually(failed_stream_closed)
                assert client.returncode is None
                assert identity_state(client.pid, client_birth) == "matching"
                record_testsuite_property("actual_native_unknown_delivery_calls", 1)
                record_testsuite_property("unknown_delivery_conduit_exit", "dead")
                record_testsuite_property("actual_native_client", str(executable.name))
                record_testsuite_property("physical_generations", json.dumps({
                    label: generation for label, (generation, _) in slots.items()
                }))
                record_testsuite_property("native_ping_roundtrips_after_handover", 3)
                record_testsuite_property("actual_native_tool_calls_consumed", len(model.results))
                record_testsuite_property("actual_native_inflight_handover_calls", 3)
            finally:
                await model.close()
                if pending is not None and not pending.done():
                    pending.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pending
                if session is not None and not session.done():
                    session.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await session
                if client is not None:
                    if client.returncode is None:
                        client.stdin.close()
                    await asyncio.wait_for(client.wait(), 10)
                    diagnostic = await asyncio.wait_for(stderr, 5)
                    if sys.exc_info()[0] is not None:
                        print("Native client stderr:", diagnostic.decode("utf-8", "replace")[-8000:])
                        for path in [
                            observations, replies, observations.with_suffix(".owner.json"),
                            observations.with_suffix(".refusal"), observations.with_suffix(".capabilities"),
                            *(tmp_path / "logs").glob("**/*.log"),
                            *workspace.glob("**/*.log"),
                        ]:
                            if path.is_file():
                                print(path.name + ":", path.read_text(
                                    encoding="utf-8", errors="replace",
                                )[-8000:])
                    else:
                        async def upstream_settled() -> bool:
                            return receipt.is_file()

                        try:
                            await fixture.eventually(upstream_settled)
                        except TimeoutError:
                            print("Native exit identities:", {
                                name: identity_state(*pair)
                                for name, pair in {
                                    "conduit": conduit_identity, "keeper": keeper_identity,
                                    "upstream": upstream_identity,
                                }.items()
                            })
                            for path in workspace.glob("**/*.log"):
                                print(path.name + ":", path.read_text(
                                    encoding="utf-8", errors="replace",
                                )[-8000:])
                            raise
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
        exit_receipt = json.loads(receipt.read_text(encoding="utf-8"))
        assert exit_receipt == {
            "pid": upstream_identity[0], "initialized": 1,
            "boundary": f"signal-{signal.SIGTERM}",
        }
        accepted = list(map(json.loads, calls.read_text().splitlines()))
        assert [row["nonce"] for row in accepted] == model.calls
        assert all(row["pid"] == upstream_identity[0] for row in accepted)
        assert model.calls.count("native-fixture-unknown") == 1
        assert "native-fixture-unknown" not in model.results
        record_testsuite_property("contained_native_upstream_exit", exit_receipt["boundary"])
