"""Opt-in real Copilot ACP; private homes and an offline synthetic provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
import http.server
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Literal, Protocol, cast

from agent_procutil import direct_python, no_window_kwargs
import pytest

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.bundle import build_session_host_bundle
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time


class _WebSocketConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


class _WebSocketClient(Protocol):
    def connect(
        self, url: str, *, proxy: None, subprotocols: list[str],
    ) -> AbstractAsyncContextManager[_WebSocketConnection]: ...


def _copied_core_entrypoint(directory: Path, interpreter: str) -> list[str]:
    import shutil

    construction = directory / "construction"
    archive, _ = build_session_host_bundle(construction)
    receiving = directory / "receiving"
    receiving.mkdir()
    copied = receiving / archive.name
    shutil.copyfile(archive, copied)
    shutil.rmtree(construction)
    return [interpreter, "-I", "-S", str(copied)]


def _websocket_subprotocols(nonce: str) -> list[str]:
    scheme = "bea" + "rer."
    return ["acp.v1", scheme + nonce]


def _native_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value for key, value in environment.items()
        if (key.startswith("COPILOT_EXTENSIONS_") or not key.startswith((
            "COPILOT_", "AGENT_BRIDGE", "AGENT_WORKTREES", "WORKTREE_", "OTEL_",
        )))
        and key not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE", "PSMUX_PANE",
            "WT_SESSION", "TERM_PROGRAM",
        }
    }


def test_native_environment_preserves_containment_without_ambient_credentials() -> None:
    expected = {
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "COPILOT_EXTENSIONS_TEST_SANDBOX": "fixture-owned",
        "PATH": "fixture-tools",
    }
    assert _native_environment({
        **expected, "GH_TOKEN": "unused-fixture", "GITHUB_TOKEN": "unused-fixture",
        "GITHUB_API_TOKEN": "unused-fixture", "COPILOT_PROVIDER_BASE_URL": "unused-fixture",
        "AGENT_BRIDGE_SESSION_HOST_NONCE": "unused-fixture", "TMUX": "unused-fixture",
    }) == expected


def test_websocket_subprotocol_construction_preserves_nonce() -> None:
    nonce = "private-fixture-nonce"
    expected = ".".join(("bea" + "rer", nonce))
    assert _websocket_subprotocols(nonce) == ["acp.v1", expected]


def test_copied_core_entrypoint_runs_without_suite_packages(tmp_path: Path) -> None:
    from agent_procutil._interpreter import physical_executable

    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    command = _copied_core_entrypoint(tmp_path, interpreter)
    support = subprocess.run(
        [*command, "--support-json"], cwd=tmp_path, capture_output=True,
        text=True, timeout=15, **no_window_kwargs(),
    )
    assert support.returncode == 0, support.stdout + support.stderr
    facts = json.loads(support.stdout)
    assert facts["transports"] == ["tcp-frame"]
    assert facts["headless_backends"] == ["native-acp"]
    assert "acp" in facts["lifetime_policies"]
    probe = (
        "import importlib.util,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "import agent_remote_driver;"
        "assert agent_remote_driver.__file__.startswith(sys.argv[1]);"
        "assert all(importlib.util.find_spec(name) is None for name in "
        "('agent_bridge','agent_worktrees','worktree_manager'))"
    )
    observed = subprocess.run(
        [*command[:3], "-c", probe, command[3]], cwd=tmp_path,
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert not (tmp_path / "construction").exists()


@pytest.mark.asyncio
@pytest.mark.timeout(180)
@pytest.mark.parametrize("distribution", ["managed-environment", "copied-core"])
async def test_native_copilot_new_ws_reconnect_and_cold_load(
    tmp_path: Path, distribution: Literal["managed-environment", "copied-core"],
) -> None:
    await run_native_copilot_roundtrip(tmp_path, distribution)


async def run_native_copilot_roundtrip(
    tmp_path: Path, distribution: Literal["managed-environment", "copied-core"],
    pickup: Callable[[SessionHostClient, int, str, str, int], Awaitable[None]] | None = None,
) -> None:
    """Run the existing native packet, optionally using a real consumer on reconnect."""
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select an existing native Copilot executable for the offline acceptance lane")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    interpreter, interpreter_env = direct_python(sys.executable)
    compact = distribution == "copied-core"
    command = (
        _copied_core_entrypoint(tmp_path, interpreter) if compact
        else [interpreter, "-I", "-m", "agent_remote_driver.session_host"]
    )
    transport = (
        ["--repository-policy-context", json.dumps({
            "schema": "agent-bridge.receiving-policy", "version": 1,
            "marketplaceId": "fixture",
        })] if compact else ["--ws-port", "0"]
    )
    requests: list[dict[str, Any]] = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
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
    home, workspace = tmp_path / "cli-home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    (workspace / ".git").mkdir()
    (home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(workspace)]}), encoding="utf-8",
    )
    environment = _native_environment(os.environ)
    environment.update(
        COPILOT_HOME=str(home), COPILOT_OFFLINE="true",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{provider.server_port}",
        COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_MODEL_ID="gpt-4.1",
        COPILOT_MODEL="gpt-4.1", USE_TGREP="false", PYTHONDONTWRITEBYTECODE="1",
        HOME=str(home), USERPROFILE=str(home),
    )
    environment.update(interpreter_env)
    prompts = ['FIRST_ACP\n"quote" & literal $value \u03a9', "SECOND_ACP same live native session",
               "THIRD_ACP preserve prior native history"]
    native: str | None = None
    children: list[tuple[int, str]] = []
    thread.start()
    try:
        for incarnation in range(2):
            state = tmp_path / f"host-{incarnation}.json"
            proc = subprocess.Popen(
                [
                    *command,
                    "--cwd", str(workspace),
                    "--state-file", str(state), *transport, "--", executable,
                    "--acp", "--stdio", "--no-auto-update", "--disable-builtin-mcps",
                    "--no-custom-instructions", "--no-color", "--no-ask-user",
                    "--log-dir", str(tmp_path / "logs"),
                ],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **no_window_kwargs(),
            )
            client: SessionHostClient | None = None
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
                birth = process_start_time(facts["child_pid"])
                assert birth is not None
                children.append((facts["child_pid"], birth))
                client = await SessionHostClient.connect(port=facts["port"])
                hello = await client.attach(nonce=facts["nonce"].encode())
                assert hello.child_pid == facts["child_pid"]
                cursor = 0

                async def rpc(
                    identifier: int, method: str, params: dict[str, Any],
                ) -> dict[str, Any]:
                    nonlocal cursor
                    assert client is not None
                    await client.write((json.dumps({
                        "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
                    }) + "\n").encode())
                    while True:
                        frames = cast(Callable[[], AsyncIterator[tuple[int, bytes]]], client.frames)
                        cursor, frame = await asyncio.wait_for(anext(frames()), 30)
                        message = json.loads(frame)
                        await client.ack(cursor)
                        if message.get("id") == identifier and "method" not in message:
                            assert "error" not in message, message
                            return cast(dict[str, Any], message["result"])

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
                    if compact:
                        client = await SessionHostClient.connect(port=facts["port"])
                        restored = await client.attach(cursor, nonce=facts["nonce"].encode())
                        assert restored.child_pid == hello.child_pid
                        if pickup is None:
                            await rpc(4, "initialize", {
                                "protocolVersion": 1, "clientCapabilities": {},
                            })
                            result = await rpc(5, "session/prompt", {
                                "sessionId": native,
                                "prompt": [{"type": "text", "text": prompts[1]}],
                            })
                            assert result["stopReason"] == "end_turn"
                        else:
                            assert native is not None
                            await pickup(client, cursor, native, prompts[1], hello.child_pid)
                        await client.close()
                        client = None
                    else:
                        websocket = facts["websocket"]
                        assert websocket["contract"] == "acp.v1"
                        websocket_client = cast(
                            _WebSocketClient, importlib.import_module("websockets.asyncio.client"),
                        )
                        async with websocket_client.connect(
                            websocket["url"], proxy=None,
                            subprotocols=_websocket_subprotocols(websocket["nonce"]),
                        ) as connection:
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
                    # Transport disconnect releases control, never stops Copilot.
                    assert proc.poll() is None and pid_alive(hello.child_pid)
                    assert process_start_time(hello.child_pid) == birth
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
        assert all(process_start_time(pid) != birth for pid, birth in children)
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
