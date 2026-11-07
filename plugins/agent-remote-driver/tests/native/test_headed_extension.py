"""Opt-in native SDK loading with isolated state and no tool permission grant."""

from __future__ import annotations

import http.server
import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

from agent_procutil import no_window_kwargs
import pytest
from websockets.asyncio.client import connect

from agent_remote_driver.headed.backend import HeadedBackend
from agent_remote_driver.headed.staging import stage_extension
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.host import SessionHost
from agent_remote_driver.session_host.ws_transport import WebSocketTransport
from agent_remote_driver.session_host.osutil import pid_alive


@pytest.fixture
def offline_cli(tmp_path):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select an existing native Copilot executable for the offline acceptance lane")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    requests = []

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            body = {
                "id": "fixture", "object": "chat.completion.chunk", "model": "gpt-4.1",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "FIXTURE"},
                             "finish_reason": "stop"}],
            }
            self.wfile.write(("data: " + json.dumps(body) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()

    provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    provider.daemon_threads = True
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    home, workspace = (tmp_path / name for name in ("home", "workspace"))
    for path in (home, workspace):
        path.mkdir(parents=True)
    environment = {
        key: value for key, value in os.environ.items()
        if (key.startswith("COPILOT_EXTENSIONS_")
            or not key.startswith(("COPILOT_", "AGENT_", "WORKTREE_", "OTEL_")))
        and key not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE", "PSMUX_PANE",
            "WT_SESSION", "TERM_PROGRAM",
        }
    }
    environment.update(
        COPILOT_HOME=str(home), COPILOT_OFFLINE="true",
        COPILOT_PROVIDER_BASE_URL=f"http://127.0.0.1:{provider.server_port}",
        COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_MODEL_ID="gpt-4.1",
        COPILOT_MODEL="gpt-4.1", USE_TGREP="false",
    )
    try:
        yield SimpleNamespace(
            executable=executable, env=environment, home=home, workspace=workspace,
            requests=requests, logs=tmp_path / "logs",
        )
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


def _logs(directory):
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")[-6000:]
        for path in directory.rglob("*.log")
    )


def test_native_launch_scoped_observer_needs_no_privileged_permission(tmp_path, offline_cli):
    plugin = tmp_path / "plugin"
    (plugin / "extensions" / "driver-fixture").mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "driver-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (plugin / "extensions" / "driver-fixture" / "extension.mjs").write_text("""
import { writeFileSync } from "node:fs";
import { joinSession } from "@github/copilot-sdk/extension";
const session = await joinSession();
writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
    sessionId: session.sessionId,
    send: typeof session.send,
    sendAndWait: typeof session.sendAndWait,
    abort: typeof session.abort,
    events: typeof session.on,
}));
""", encoding="utf-8")
    output = tmp_path / "joined.json"
    result = subprocess.run(
        [
            offline_cli.executable, "-p", "ISOLATED_EXTENSION_FIXTURE", "--trust-folder", "--experimental",
            "--plugin-dir", str(plugin), "--no-auto-update", "--disable-builtin-mcps",
            "--no-custom-instructions", "--no-color", "--no-ask-user",
            "--log-dir", str(offline_cli.logs),
        ],
        cwd=offline_cli.workspace, env={**offline_cli.env, "DRIVER_FIXTURE_OUTPUT": str(output)},
        capture_output=True, timeout=60, text=True, encoding="utf-8", **no_window_kwargs(),
    )
    logs = _logs(offline_cli.logs)
    assert result.returncode == 0, result.stderr + logs
    assert output.exists(), result.stderr + logs
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert all(observed[name] == "function" for name in ("send", "sendAndWait", "abort", "events"))
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    events = [
        json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert not any(
        event.get("data", {}).get("permissionRequest", {}).get("kind") == "extension-permission-access"
        for event in events
    )
    assert len(offline_cli.requests) == 1
    assert not (offline_cli.home / "permissions-config.json").exists()


@pytest.mark.asyncio
async def test_native_headed_sdk_uses_common_ws_tcp_broker_and_preserves_identity(
    tmp_path, offline_cli, monkeypatch,
):
    if os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Explicitly select the isolated native headed mux lane")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from headed_mux_fixture import FixtureMux
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    shell = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if os.name != "nt" or not binary or not shell:
        pytest.skip("This native headed fixture selects an existing Windows PSMux and PowerShell")
    (offline_cli.home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(offline_cli.workspace)]}), encoding="utf-8",
    )
    (offline_cli.home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    class Owner:
        pid = None
        stops = 0
        returncode = None

        def __init__(self):
            self.started = asyncio.Event()
            self.identity = None

        async def wait(self):
            await self.started.wait()
            while self.returncode is None:
                result = await asyncio.to_thread(query, "has-session", "-t", target)
                if result.returncode == 1:
                    self.returncode = 0
                    break
                assert result.returncode == 0, result.stderr
                await asyncio.sleep(0.2)
            return self.returncode

        async def terminate(self):
            if self.identity is not None and self.returncode is None:
                observed = await asyncio.to_thread(
                    query, "display-message", "-p", "-t", target, "#{session_id}:#{session_created}|#{pane_id}",
                )
                assert observed.returncode == 0 and observed.stdout.strip() == self.identity
                self.stops += 1
                stopped = await asyncio.to_thread(query, "kill-session", "-t", target)
                assert stopped.returncode == 0, stopped.stderr
                await asyncio.wait_for(self.wait(), 10)

    native = str(uuid4())
    owner = Owner()
    prompts = ['HEADed literal\n"quote" & $value', "SECOND headed native prompt",
               "THIRD headed native prompt"]
    backend = HeadedBackend(
        owner, session_id=native, nonce="fixture-" * 8, startup_prompt=prompts[0],
    )
    await backend.serve()
    plugin = stage_extension(tmp_path / "original-attempt")
    host, websocket, client = None, None, None
    environment = {
        **offline_cli.env, "HOME": str(offline_cli.home), "USERPROFILE": str(offline_cli.home),
        "PSMUX_DATA_DIR": str(tmp_path / "psmux-data"), "PSMUX_CONFIG_FILE": os.devnull,
        "PSMUX_NO_WARM": "1", "WORKTREE_PANE_MIN_RUNTIME": "0",
        "COPILOT_DRIVER_HEADED_BINDING": json.dumps(backend.declaration()),
    }
    root = Path(__file__).resolve().parents[4]
    wrapper = root / "worktree-manager" / "bin" / "pane-wrapper.ps1"
    native_argv = tmp_path / "native-argv.json"
    native_argv.write_text(json.dumps([
        offline_cli.executable, "-i", prompts[0], "--session-id", native, "--experimental",
        "--screen-reader", "--plugin-dir", str(plugin), "--no-auto-update", "--disable-builtin-mcps",
        "--no-custom-instructions", "--no-color", "--no-ask-user", "--log-dir", str(offline_cli.logs),
    ]), encoding="utf-8")
    pane = tmp_path / "fixture-pane.ps1"
    pane.write_text(
        "$ErrorActionPreference='Stop'\n"
        "try {\n"
        "  $nativeArgs = @(Get-Content -Raw -LiteralPath $env:DRIVER_FIXTURE_ARGV | ConvertFrom-Json)\n"
        "  & $env:DRIVER_FIXTURE_WRAPPER @nativeArgs\n"
        "  [IO.File]::WriteAllText($env:DRIVER_FIXTURE_LOG, \"pane exit: $LASTEXITCODE\")\n"
        "  exit $LASTEXITCODE\n"
        "} catch {\n"
        "  [IO.File]::WriteAllText($env:DRIVER_FIXTURE_LOG, $_.ToString())\n"
        "  exit 3\n"
        "}\n", encoding="utf-8",
    )
    pane_log = tmp_path / "pane-result.txt"
    assert wrapper.is_file(), "The real pane wrapper must exist before native creation"
    environment.update(DRIVER_FIXTURE_ARGV=str(native_argv), DRIVER_FIXTURE_WRAPPER=str(wrapper),
                       DRIVER_FIXTURE_LOG=str(pane_log))
    fixture = FixtureMux(binary, offline_cli.home, environment)
    target, query = fixture.target, fixture.query

    async def launch():
        absent = await asyncio.to_thread(query, "has-session", "-t", target)
        assert absent.returncode == 1, "The fixture target must be absent before creation"
        # PSMux joins pane argv; match the caller's existing single-quoted file boundary.
        result = await asyncio.to_thread(
            query, "new-session", "-d", "-s", target, "-c", str(offline_cli.workspace),
            Path(shell).name, "-NoProfile", "-File", "'" + str(pane).replace("'", "''") + "'",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        observed = await asyncio.to_thread(
            query, "display-message", "-p", "-t", target, "#{session_id}:#{session_created}|#{pane_id}",
        )
        assert observed.returncode == 0 and "|" in observed.stdout
        owner.identity = observed.stdout.strip()
        owner.started.set()

    try:
        await launch()
        try:
            for _ in range(600):
                if backend.bound.is_set() or owner.returncode is not None:
                    break
                await asyncio.sleep(0.05)
            assert backend.bound.is_set(), (
                _logs(offline_cli.logs)
                + (pane_log.read_text(encoding="utf-8") if pane_log.is_file() else "Pane did not report an exit")
                + (await asyncio.to_thread(query, "capture-pane", "-p", "-t", target)).stdout
            )
            assert backend.native_pid and pid_alive(backend.native_pid)
            assert backend.extension_pid and pid_alive(backend.extension_pid)
            for _ in range(600):
                event_file = offline_cli.home / "session-state" / native / "events.jsonl"
                if event_file.is_file():
                    recorded = event_file.read_text(encoding="utf-8")
                    if '"assistant.turn_end"' in recorded:
                        break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Original native seed did not complete: " + _logs(offline_cli.logs))
            host = SessionHost(backend, nonce="front-private", lifetime_policy="cli")
            port = await host.serve()
            websocket = WebSocketTransport(port, "front-private")
            await websocket.serve()
            async with connect(
                f"ws://127.0.0.1:{websocket.port}/acp", proxy=None,
                subprotocols=["acp.v1", "bearer.front-private"],
            ) as connection:
                await connection.send(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": 1},
                }))
                initialized = json.loads(await asyncio.wait_for(connection.recv(), 30))
                assert initialized["result"]["_meta"]["nativeSessionId"] == native
                await connection.send(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
                    "params": {"sessionId": native, "prompt": [{"type": "text", "text": prompts[1]}]},
                }))
                while True:
                    message = json.loads(await asyncio.wait_for(connection.recv(), 30))
                    if message.get("id") == 2:
                        assert message["result"]["stopReason"] == "end_turn", message
                        break
            assert owner.returncode is None and pid_alive(backend.native_pid)
            for _ in range(100):
                if host._front is None:
                    break
                await asyncio.sleep(0.01)
            client = await SessionHostClient.connect(port=port)
            hello = await client.attach(nonce=b"front-private")
            assert hello.child_pid == (owner.pid or 0)
            await client.write((json.dumps({
                "jsonrpc": "2.0", "id": 3, "method": "session/prompt",
                "params": {"sessionId": native, "prompt": [{"type": "text", "text": prompts[2]}]},
            }) + "\n").encode())
            while True:
                cursor, frame = await asyncio.wait_for(anext(client.frames()), 30)
                message = json.loads(frame)
                await client.ack(cursor)
                if message.get("id") == 3:
                    assert message["result"]["stopReason"] == "end_turn", message
                    break
            await client.terminate()
            await asyncio.wait_for(host.wait_child(), 15)
            assert owner.returncode is not None and owner.stops == 1
            for _ in range(100):
                if not pid_alive(backend.native_pid) and not pid_alive(backend.extension_pid):
                    break
                await asyncio.sleep(0.05)
            assert not pid_alive(backend.native_pid) and not pid_alive(backend.extension_pid)
            session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
            assert [path.parent.name for path in session_files] == [native]
            events = [
                json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()
            ]
            assert [event["data"]["content"] for event in events if event["type"] == "user.message"] == prompts
            assert len(offline_cli.requests) == 3
            assert not (offline_cli.home / "permissions-config.json").exists()
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.close()
            if websocket is not None:
                await websocket.close()
            if host is not None:
                await host.close()
            await backend.close()
            await owner.terminate()
            assert owner.returncode is not None
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["ordinary", "receiving", "original-owner", "bridge-client"])
async def test_native_headed_coordinator_owns_the_common_broker_in_the_original_pane(
    tmp_path, offline_cli, monkeypatch, delivery,
):
    if os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Explicitly select the isolated native headed mux lane")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from headed_mux_fixture import FixtureMux
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    shell = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if os.name != "nt" or not binary or not shell:
        pytest.skip("The native coordinator fixture requires existing Windows PSMux and PowerShell")
    if delivery == "bridge-client" and not shutil.which("node"):
        pytest.skip("Node is required for the retained Bridge client")
    (offline_cli.home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(offline_cli.workspace)]}), encoding="utf-8",
    )
    (offline_cli.home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    session_id = str(uuid4())
    prompts = ['coordinator original\n"quotes" & $literal', "coordinator second"]
    receipt_nonce = uuid4().hex + uuid4().hex
    state_file = tmp_path / "host.json"
    arguments = tmp_path / "coordinator-argv.json"
    coordinator_argv = [
        sys.executable,
        "-m", "agent_remote_driver.session_host", "--mode", "headed",
        "--attempt-directory", str(tmp_path / "original-attempt"),
        "--state-file", str(state_file), "--session-id", session_id, "--ws-port", "0",
        "--cwd", str(offline_cli.workspace), "--",
        offline_cli.executable, "-i", prompts[0], "--session-id", session_id,
        "--screen-reader", "--no-auto-update", "--disable-builtin-mcps",
        "--no-custom-instructions", "--no-color", "--no-ask-user",
        "--log-dir", str(offline_cli.logs),
    ]
    if delivery in {"receiving", "bridge-client"}:
        from agent_remote_driver.headed.launcher import prepare_headed_command

        coordinator_argv = prepare_headed_command(
            coordinator_argv[coordinator_argv.index("--") + 1:],
            session_id=session_id, attempt_directory=tmp_path / "original-attempt",
            state_file=state_file, cwd=str(offline_cli.workspace),
        )
    owner_receipt = tmp_path / "original-owner-receipt.json"
    if delivery == "original-owner":
        manifest = tmp_path / "original-owner.json"
        manifest.write_text(json.dumps({
            "argv": coordinator_argv[coordinator_argv.index("--") + 1:],
            "session": session_id, "attempt": str(tmp_path / "original-attempt"),
            "state": str(state_file), "cwd": str(offline_cli.workspace),
            "receipt": str(owner_receipt),
        }), encoding="utf-8")
        coordinator_argv = [
            sys.executable, "-c",
            "import json,os,sys;from pathlib import Path;"
            "from agent_remote_driver.headed.caller import execute;"
            "m=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));"
            "seen=[];"
            "admit=lambda:seen.append('admitted');"
            "spawned=lambda pid,birth:(Path(m['receipt']+'.tmp').write_text("
            "json.dumps({'pid':pid,'birth':birth,'admission':seen,'owner_pid':os.getpid()}),"
            "encoding='utf-8'),Path(m['receipt']+'.tmp').replace(m['receipt']));"
            "code,certain=execute(m['argv'],session_id=m['session'],"
            "attempt_directory=Path(m['attempt']),state_file=Path(m['state']),"
            "cwd=m['cwd'],environment=os.environ.copy(),admit=admit,"
            "spawned=spawned,interactive=True);"
            "sys.exit(code if code or certain else 3)",
            str(manifest),
        ]
    arguments.write_text(json.dumps(coordinator_argv[1:]), encoding="utf-8")
    pane = tmp_path / "coordinator-pane.ps1"
    pane.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$childArgs = @(Get-Content -Raw -LiteralPath $env:DRIVER_FIXTURE_ARGV | ConvertFrom-Json)\n"
        "& $env:DRIVER_FIXTURE_PYTHON @childArgs\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    fixture = FixtureMux(binary, offline_cli.home, {
        **offline_cli.env, "HOME": str(offline_cli.home), "USERPROFILE": str(offline_cli.home),
        "PSMUX_DATA_DIR": str(tmp_path / "psmux-data"), "PSMUX_CONFIG_FILE": os.devnull,
        "PSMUX_NO_WARM": "1", "DRIVER_FIXTURE_ARGV": str(arguments),
        "DRIVER_FIXTURE_PYTHON": coordinator_argv[0],
        "AGENT_BRIDGE_SESSION_HOST_NONCE": receipt_nonce,
    })
    target, query = fixture.target, fixture.query
    identity, state, owner_pid = None, None, None
    try:
        absent = await asyncio.to_thread(query, "has-session", "-t", target)
        assert absent.returncode == 1
        launched = await asyncio.to_thread(
            query, "new-session", "-d", "-s", target, "-c", str(offline_cli.workspace),
            Path(shell).name, "-NoProfile", "-File", "'" + str(pane).replace("'", "''") + "'",
        )
        assert launched.returncode == 0, launched.stderr
        observed = await asyncio.to_thread(
            query, "display-message", "-p", "-t", target, "#{session_id}:#{session_created}|#{pane_id}",
        )
        assert observed.returncode == 0 and "|" in observed.stdout
        identity = observed.stdout.strip()
        for _ in range(600):
            if state_file.is_file():
                state = json.loads(state_file.read_text())
                if state["state"] == "running":
                    break
            present = await asyncio.to_thread(query, "has-session", "-t", target)
            if present.returncode == 1:
                pytest.fail("Coordinator pane exited before binding: " + _logs(offline_cli.logs))
            await asyncio.sleep(0.05)
        else:
            pane_output = await asyncio.to_thread(query, "capture-pane", "-p", "-t", target)
            pytest.fail(
                f"Coordinator did not bind; retained state={state!r}; "
                f"fixture pane={pane_output.stdout[-8192:]!r}; logs=" + _logs(offline_cli.logs)
            )
        assert state["child_pid"] > 0 and state["extension_pid"] > 0
        assert pid_alive(state["pid"]) and pid_alive(state["child_pid"])
        from agent_remote_driver.headed.receipt import native_child
        from agent_remote_driver.session_host.osutil import process_start_time

        if delivery == "original-owner":
            for _ in range(100):
                if owner_receipt.is_file():
                    break
                await asyncio.sleep(0.02)
            actual_receipt = json.loads(owner_receipt.read_text(encoding="utf-8"))
            owner_pid = actual_receipt["owner_pid"]
            assert actual_receipt == {
                "pid": state["child_pid"], "birth": process_start_time(state["child_pid"]),
                "admission": ["admitted"], "owner_pid": owner_pid,
            }
            assert owner_pid != state["pid"] and pid_alive(owner_pid)
        else:
            assert native_child(
                state_file, coordinator_pid=state["pid"],
                coordinator_birth=process_start_time(state["pid"]),
                session_id=session_id, nonce=receipt_nonce,
            ) == (state["child_pid"], process_start_time(state["child_pid"]))
        event_file = offline_cli.home / "session-state" / session_id / "events.jsonl"
        for _ in range(600):
            if event_file.is_file() and '"assistant.turn_end"' in event_file.read_text():
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("Original coordinator seed did not finish")
        requests = (
            (1, "initialize", {"protocolVersion": 1}),
            (2, "session/prompt", {
                "sessionId": session_id, "prompt": [{"type": "text", "text": prompts[1]}],
            }),
        )
        if delivery == "ordinary":
            async with connect(
                state["websocket"]["url"], proxy=None,
                subprotocols=["acp.v1", "bearer." + state["nonce"]],
            ) as connection:
                for request_id, method, params in requests:
                    await connection.send(json.dumps({
                        "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                    }))
                    while True:
                        response = json.loads(await asyncio.wait_for(connection.recv(), 30))
                        if response.get("id") == request_id:
                            assert "result" in response, response
                            break
        elif delivery == "bridge-client":
            from agent_procutil.owned_process import run_caller_owned

            wrapper = (
                Path(__file__).resolve().parents[4] / "plugins" / "agent-bridge" /
                "extensions" / "agent-bridge" / "remote-driver.mjs"
            )
            declaration = {
                "contract": "copilot-driver.headed-client", "version": 1,
                "sessionId": session_id, "cwd": str(offline_cli.workspace), "stateFile": str(state_file),
                "nonce": state["nonce"], "hostPid": state["host_pid"],
                "client": str(Path(state["extension_assets"]) / "extensions" /
                              "copilot-driver-headed" / "host-client.mjs"),
            }
            script = (
                "const {createHeadedDriverSession}=await import(" + json.dumps(wrapper.as_uri()) + ");\n"
                "const native={sessionId:" + json.dumps(session_id)
                + ",send(){throw Error('raw SDK forbidden')}};\n"
                "const client=await createHeadedDriverSession(native,{declaration:"
                + json.dumps(json.dumps(declaration)) + ",cwd:" + json.dumps(str(offline_cli.workspace))
                + ",nativePid:" + str(state["child_pid"]) + "});\n"
                "try {console.log(JSON.stringify({messageId:await client.send({prompt:"
                + json.dumps(prompts[1]) + ",mode:'immediate',source:'agent-bridge'})}));}"
                "finally {client.close();}\n"
            )
            accepted = await asyncio.to_thread(
                run_caller_owned, [shutil.which("node"), "--input-type=module", "-e", script],
                timeout=20, max_stdout_bytes=4096, max_stderr_bytes=4096,
            )
            assert accepted.returncode == 0, accepted.stderr
            assert not accepted.stderr
            assert isinstance(json.loads(accepted.stdout)["messageId"], str)
            for _ in range(600):
                events = [json.loads(line) for line in event_file.read_text().splitlines()]
                if sum(event["type"] == "assistant.turn_end" for event in events) >= 2:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Bridge broker delivery was accepted but did not complete")
        else:
            assert "websocket" not in state
            client = await SessionHostClient.connect(port=state["port"])
            try:
                await client.attach(nonce=state["nonce"].encode())
                for request_id, method, params in requests:
                    await client.write((json.dumps({
                        "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                    }) + "\n").encode())
                    while True:
                        cursor, frame = await asyncio.wait_for(anext(client.frames()), 30)
                        response = json.loads(frame)
                        await client.ack(cursor)
                        if response.get("id") == request_id:
                            assert "result" in response, response
                            break
                await client.detach(reapable=True)
            finally:
                await client.close()
        await asyncio.sleep(0.1)
        client = await SessionHostClient.connect(port=state["port"])
        try:
            hello = await client.attach(nonce=state["nonce"].encode())
            assert hello.child_pid == state["child_pid"]
            await client.detach(reapable=True)
            await client.close()
            await asyncio.sleep(0.1)
            assert pid_alive(state["child_pid"]) and pid_alive(state["pid"])
            client = await SessionHostClient.connect(port=state["port"])
            await client.attach(nonce=state["nonce"].encode())
            await client.terminate()
        finally:
            await client.close()
        owned_pids = [state[key] for key in ("pid", "child_pid", "extension_pid")]
        if owner_pid is not None:
            owned_pids.append(owner_pid)
        for _ in range(300):
            if not any(pid_alive(pid) for pid in owned_pids):
                break
            await asyncio.sleep(0.05)
        assert not any(pid_alive(pid) for pid in owned_pids)
        events = [json.loads(line) for line in event_file.read_text().splitlines()]
        assert [event["data"]["content"] for event in events if event["type"] == "user.message"] == prompts
        assert len(offline_cli.requests) == 2
        assert not (offline_cli.home / "permissions-config.json").exists()
    finally:
        for _ in range(100):
            present = await asyncio.to_thread(query, "has-session", "-t", target)
            if present.returncode == 1:
                break
            assert present.returncode == 0, present.stderr
            observed = await asyncio.to_thread(
                query, "display-message", "-p", "-t", target,
                "#{session_id}:#{session_created}|#{pane_id}",
            )
            if not observed.stdout.strip():
                await asyncio.sleep(0.05)
                continue
            assert identity is not None and observed.returncode == 0 and observed.stdout.strip() == identity
            stopped = await asyncio.to_thread(query, "kill-session", "-t", target)
            assert stopped.returncode == 0, stopped.stderr
            break
        else:
            pytest.fail("Fixture session retirement could not be confirmed")
