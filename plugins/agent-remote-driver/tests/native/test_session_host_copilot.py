"""Opt-in real Copilot ACP; private homes and an offline synthetic provider."""

from __future__ import annotations

import asyncio
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from agent_procutil import direct_python, no_window_kwargs
import pytest
from websockets.asyncio.client import connect

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.osutil import pid_alive


@pytest.mark.asyncio
async def test_native_copilot_new_ws_reconnect_and_cold_load(tmp_path):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select an existing native Copilot executable for the offline acceptance lane")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    interpreter, interpreter_env = direct_python(sys.executable)
    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            assert request.get("stream") is True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (
                ({"role": "assistant", "content": "SYNTHETIC_ACP_REPLY"}, None),
                ({}, "stop"),
            ):
                body = {
                    "id": "fixture", "object": "chat.completion.chunk", "model": "gpt-4.1",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                self.wfile.write(("data: " + json.dumps(body) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.daemon_threads = True
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    home, workspace = tmp_path / "cli-home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_BRIDGE", "AGENT_WORKTREES", "WORKTREE_", "OTEL_"))
        and key not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE", "PSMUX_PANE",
            "WT_SESSION", "TERM_PROGRAM",
        }
    }
    environment.update(
        COPILOT_HOME=str(home), COPILOT_OFFLINE="true",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{provider.server_port}",
        COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_MODEL_ID="gpt-4.1",
        COPILOT_MODEL="gpt-4.1", USE_TGREP="false", PYTHONDONTWRITEBYTECODE="1",
    )
    environment.update(interpreter_env)
    prompts = ['FIRST_ACP\n"quote" & literal $value \u03a9', "SECOND_ACP same live native session",
               "THIRD_ACP preserve prior native history"]
    native = None
    child_pids = []
    try:
        for incarnation in range(2):
            state = tmp_path / f"host-{incarnation}.json"
            proc = subprocess.Popen(
                [
                    interpreter, "-I", "-m", "agent_remote_driver.session_host",
                    "--cwd", str(workspace),
                    "--state-file", str(state), "--ws-port", "0", "--", executable,
                    "--acp", "--stdio", "--no-auto-update", "--disable-builtin-mcps",
                    "--no-custom-instructions", "--no-color", "--no-ask-user",
                    "--log-dir", str(tmp_path / "logs"),
                ],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **no_window_kwargs(),
            )
            client = None
            try:
                for _ in range(400):
                    if state.exists():
                        try:
                            facts = json.loads(state.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            pass
                        else:
                            break
                    if proc.poll() is not None:
                        pytest.fail("Native Host exited before readiness: " +
                                    proc.communicate()[1].decode("utf-8", "replace"))
                    await asyncio.sleep(0.025)
                else:
                    pytest.fail("Native Host readiness timeout")
                assert facts["pid"] == proc.pid
                child_pids.append(facts["child_pid"])
                client = await SessionHostClient.connect(port=facts["port"])
                hello = await client.attach(nonce=facts["nonce"].encode())
                assert hello.child_pid == facts["child_pid"]
                cursor = 0

                async def rpc(identifier, method, params):
                    nonlocal cursor
                    await client.write((json.dumps({
                        "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
                    }) + "\n").encode())
                    while True:
                        cursor, frame = await asyncio.wait_for(anext(client.frames()), 30)
                        message = json.loads(frame)
                        await client.ack(cursor)
                        if message.get("id") == identifier and "method" not in message:
                            assert "error" not in message, message
                            return message["result"]

                initialized = await rpc(1, "initialize", {
                    "protocolVersion": 1, "clientCapabilities": {},
                    "clientInfo": {"name": "isolated-driver-fixture", "version": "1"},
                })
                assert initialized["protocolVersion"] == 1
                if incarnation == 0:
                    created = await rpc(2, "session/new", {"cwd": str(workspace), "mcpServers": []})
                    native = created["sessionId"]
                    assert isinstance(native, str) and native
                else:
                    assert initialized["agentCapabilities"]["loadSession"] is True
                    await rpc(2, "session/load", {
                        "sessionId": native, "cwd": str(workspace), "mcpServers": [],
                    })
                prompt = prompts[0 if incarnation == 0 else 2]
                result = await rpc(3, "session/prompt", {
                    "sessionId": native, "prompt": [{"type": "text", "text": prompt}],
                })
                assert result["stopReason"] == "end_turn"
                if incarnation == 1:
                    await client.terminate()
                else:
                    await client.close()
                    client = None
                    websocket = facts["websocket"]
                    assert websocket["contract"] == "acp.v1"
                    async with connect(
                        websocket["url"], proxy=None,
                        subprotocols=["acp.v1", "bearer." + websocket["nonce"]],
                    ) as connection:
                        # Re-initialize over a normal ACP WS connection,
                        # without a driver-specific attach or frame envelope.
                        await connection.send(json.dumps({
                            "jsonrpc": "2.0", "id": 4, "method": "initialize",
                            "params": {"protocolVersion": 1, "clientCapabilities": {}},
                        }))
                        initialized = json.loads(await asyncio.wait_for(connection.recv(), 30))
                        assert initialized["id"] == 4 and "error" not in initialized
                        await connection.send(json.dumps({
                            "jsonrpc": "2.0", "id": 5, "method": "session/prompt",
                            "params": {"sessionId": native,
                                       "prompt": [{"type": "text", "text": prompts[1]}]},
                        }))
                        while True:
                            message = json.loads(await asyncio.wait_for(connection.recv(), 30))
                            if message.get("id") == 5 and "method" not in message:
                                assert message["result"]["stopReason"] == "end_turn"
                                break
                    # WS disconnect releases control, never stops Copilot.
                    assert proc.poll() is None and pid_alive(hello.child_pid)
                    for _ in range(100):
                        client = await SessionHostClient.connect(port=facts["port"])
                        try:
                            restored = await client.attach(nonce=facts["nonce"].encode())
                        except ConnectionError:
                            await client.close()
                            await asyncio.sleep(0.01)
                        else:
                            break
                    else:
                        pytest.fail("WS failed to release the existing native controller")
                    assert restored.child_pid == hello.child_pid
                    await client.terminate()
                await asyncio.to_thread(proc.wait, timeout=15)
                assert proc.returncode == 0 and not pid_alive(hello.child_pid)
            except Exception as exc:
                logs = "\n".join(
                    path.read_text(encoding="utf-8", errors="replace")[-6000:]
                    for path in sorted((tmp_path / "logs").glob("*.log"))[-2:]
                )
                raise AssertionError(
                    f"Native incarnation {incarnation} failed; isolated CLI log tail:\n{logs}"
                ) from exc
            finally:
                if client is not None:
                    await client.close()
                if proc.poll() is None:
                    proc.kill()
                    await asyncio.to_thread(proc.wait, timeout=5)
                proc.communicate(timeout=5)
        assert len(requests) == 3
        files = list((home / "session-state").glob("*/events.jsonl"))
        assert [path.parent.name for path in files] == [native]
        events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
        assert [event["data"]["content"] for event in events if event["type"] == "user.message"] == prompts
        assert sum(event["type"] == "assistant.turn_end" for event in events) == 3
        assert all(not pid_alive(pid) for pid in child_pids)
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
