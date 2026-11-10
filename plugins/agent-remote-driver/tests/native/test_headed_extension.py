"""Opt-in native SDK loading with isolated state and no tool permission grant."""

from __future__ import annotations

import http.server
import asyncio
import contextlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
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
    pause_response, response_started, release_response = (threading.Event() for _ in range(3))

    class Provider(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if pause_response.is_set():
                response_started.set()
                if not release_response.wait(30):
                    self.send_error(504)
                    return
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
            pause_response=pause_response, response_started=response_started,
            release_response=release_response,
        )
    finally:
        release_response.set()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


def _logs(directory):
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")[-6000:]
        for path in directory.rglob("*.log")
    )


@pytest.mark.parametrize("configured", [False, True])
def test_native_private_mux_executes_the_requested_pane(tmp_path, monkeypatch, configured):
    if os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Explicitly select the isolated Windows PSMux lane")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from headed_mux_fixture import FixtureMux
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    assert binary
    pane_python = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert pane_python
    home = tmp_path / "home"
    home.mkdir()
    (home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    marker, release = tmp_path / "started.txt", tmp_path / "release.txt"
    pane = tmp_path / "pane.py"
    pane.write_text(
        "import os,time\nfrom pathlib import Path\n"
        "marker=Path(os.environ['DRIVER_FIXTURE_MARKER'])\n"
        "temporary=marker.with_suffix('.tmp')\n"
        "temporary.write_text(str(os.getpid()),encoding='utf-8')\n"
        "temporary.replace(marker)\n"
        "while not Path(os.environ['DRIVER_FIXTURE_RELEASE']).exists(): time.sleep(0.05)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PSMUX_CONFIG_FILE", str(tmp_path / "unused-caller.conf"))
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path / "unused-caller-data"))
    fixture = FixtureMux(binary, home, {
        **{key: value for key, value in os.environ.items()
           if key not in {"PSMUX_CONFIG_FILE", "PSMUX_DATA_DIR"}},
        "HOME": str(home), "USERPROFILE": str(home), "PSMUX_NO_WARM": "1",
        "PSMUX_DATA_DIR": str(tmp_path / "psmux-data"),
        "DRIVER_FIXTURE_MARKER": str(marker), "DRIVER_FIXTURE_RELEASE": str(release),
    })
    if configured:
        fixture.environment.update(
            PSMUX_CONFIG_FILE=os.devnull, PSMUX_DATA_DIR=str(tmp_path / "psmux-data"),
        )
    else:
        assert "PSMUX_CONFIG_FILE" not in fixture.environment
    assert fixture.environment["PSMUX_DATA_DIR"] == str(tmp_path / "psmux-data")
    identity = None
    try:
        assert fixture.query("has-session", "-t", fixture.target).returncode == 1
        launched = fixture.query(
            "new-session", "-d", "-s", fixture.target, "-c", str(tmp_path), "--",
            pane_python, "-I", "-B", str(pane),
        )
        assert launched.returncode == 0, launched.stderr
        observed = fixture.query(
            "display-message", "-p", "-t", fixture.target, "#{session_id}:#{session_created}|#{pane_id}",
        )
        assert observed.returncode == 0 and "|" in observed.stdout
        identity = observed.stdout.strip()
        deadline = time.monotonic() + 10
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not marker.is_file():
            observed_pid = fixture.query("display-message", "-p", "-t", fixture.target, "#{pane_pid}")
            assert observed_pid.returncode == 0 and observed_pid.stdout.strip().isdigit()
            pid = int(observed_pid.stdout.strip())
            detail = subprocess.run(
                [shutil.which("pwsh"), "-NoProfile", "-Command",
                 f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
                 "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
                env=fixture.environment, capture_output=True, text=True, encoding="utf-8",
                timeout=10, **no_window_kwargs(),
            )
            pytest.fail(detail.stdout + detail.stderr)
        pid = int(marker.read_text(encoding="utf-8"))
        assert pid_alive(pid)
        release.write_text("release", encoding="utf-8")
        deadline = time.monotonic() + 10
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not pid_alive(pid)
    finally:
        if fixture.query("has-session", "-t", fixture.target).returncode == 0:
            observed = fixture.query(
                "display-message", "-p", "-t", fixture.target, "#{session_id}:#{session_created}|#{pane_id}",
            )
            if observed.returncode == 1:
                assert fixture.query("has-session", "-t", fixture.target).returncode == 1
            else:
                assert identity is not None and observed.returncode == 0 and observed.stdout.strip() == identity
                assert fixture.query("kill-session", "-t", fixture.target).returncode == 0


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


def test_native_joined_session_reports_pending_read_support_without_permission_grants(tmp_path, offline_cli):
    plugin = tmp_path / "pending-plugin"
    extension = plugin / "extensions" / "pending-fixture"
    extension.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "pending-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (extension / "extension.mjs").write_text("""
import { writeFileSync } from "node:fs";
import { joinSession } from "@github/copilot-sdk/extension";
const session = await joinSession();
const observations = {};
for (const [name, field] of [["tasks", "tasks"], ["schedule", "entries"]]) {
    const api = session.rpc?.[name];
    if (typeof api?.list !== "function") {
        observations[name] = { state: "unavailable", reason: "missing-method" };
        continue;
    }
    try {
        const result = await api.list();
        observations[name] = Array.isArray(result?.[field])
            ? { state: "available", field, items: result[field] }
            : { state: "unavailable", reason: "invalid-shape" };
    } catch {
        observations[name] = { state: "unavailable", reason: "read-failed" };
    }
}
writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
    sessionId: session.sessionId, observations,
}));
""", encoding="utf-8")
    output = tmp_path / "pending-reads.json"
    result = subprocess.run(
        [
            offline_cli.executable, "-p", "ISOLATED_PENDING_READ_FIXTURE", "--trust-folder",
            "--experimental", "--plugin-dir", str(plugin), "--no-auto-update",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
            "--log-dir", str(offline_cli.logs),
        ],
        cwd=offline_cli.workspace, env={**offline_cli.env, "DRIVER_FIXTURE_OUTPUT": str(output)},
        capture_output=True, timeout=60, text=True, encoding="utf-8", **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr + _logs(offline_cli.logs)
    assert output.is_file(), result.stderr + _logs(offline_cli.logs)
    observed = json.loads(output.read_text(encoding="utf-8"))
    for name, field in (("tasks", "tasks"), ("schedule", "entries")):
        snapshot = observed["observations"][name]
        assert snapshot["state"] == "available", {name: snapshot}
        assert snapshot["field"] == field and isinstance(snapshot["items"], list)
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    assert not any(
        event.get("data", {}).get("permissionRequest", {}).get("kind") == "extension-permission-access"
        for event in events
    )
    assert len(offline_cli.requests) == 1
    assert not (offline_cli.home / "permissions-config.json").exists()


def test_native_joined_observer_close_returns_no_attributed_outcome(tmp_path, offline_cli):
    plugin = tmp_path / "observer-plugin"
    extension = plugin / "extensions" / "observer-fixture"
    extension.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "observer-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (extension / "extension.mjs").write_text("""
import { writeFileSync } from "node:fs";
import { joinSession } from "@github/copilot-sdk/extension";
const session = await joinSession();
const unsubscribe = session.on(() => {});
const unsubscribeResult = unsubscribe();
const disconnect = session.disconnect();
const disconnectIsPromise = typeof disconnect?.then === "function";
const disconnectResult = await disconnect;
const repeatedResult = await session.disconnect();
writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
    sessionId: session.sessionId,
    unsubscribeResult: typeof unsubscribeResult,
    disconnectIsPromise,
    disconnectResult: typeof disconnectResult,
    repeatedResult: typeof repeatedResult,
}));
""", encoding="utf-8")
    output = tmp_path / "observer-close.json"
    result = subprocess.run(
        [
            offline_cli.executable, "-p", "ISOLATED_OBSERVER_CLOSE_FIXTURE", "--trust-folder",
            "--experimental", "--plugin-dir", str(plugin), "--no-auto-update",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
            "--log-dir", str(offline_cli.logs),
        ],
        cwd=offline_cli.workspace, env={**offline_cli.env, "DRIVER_FIXTURE_OUTPUT": str(output)},
        capture_output=True, timeout=60, text=True, encoding="utf-8", **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr + _logs(offline_cli.logs)
    assert output.is_file(), result.stderr + _logs(offline_cli.logs)
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert observed == {
        "sessionId": observed["sessionId"], "unsubscribeResult": "undefined",
        "disconnectIsPromise": True, "disconnectResult": "undefined", "repeatedResult": "undefined",
    }
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    assert not any(
        event.get("data", {}).get("permissionRequest", {}).get("kind") == "extension-permission-access"
        for event in events
    )
    assert len(offline_cli.requests) == 1
    assert not (offline_cli.home / "permissions-config.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("pane_owner", ["direct", "manager-wrapper"])
async def test_native_headed_sdk_uses_common_ws_tcp_broker_and_preserves_identity(
    tmp_path, offline_cli, monkeypatch, pane_owner,
):
    if os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Explicitly select the isolated native headed mux lane")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from headed_mux_fixture import FixtureMux
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    if os.name != "nt" or not binary:
        pytest.skip("This native headed fixture selects an existing Windows PSMux")
    (offline_cli.home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(offline_cli.workspace)]}), encoding="utf-8",
    )
    (offline_cli.home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    class Owner:
        pid = None
        pane_pid = None
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
    native_argv = [
        offline_cli.executable, "-i", prompts[0], "--session-id", native, "--experimental",
        "--screen-reader", "--plugin-dir", str(plugin), "--no-auto-update", "--disable-builtin-mcps",
        "--no-custom-instructions", "--no-color", "--no-ask-user", "--log-dir", str(offline_cli.logs),
    ]
    pane_argv = native_argv
    if pane_owner == "manager-wrapper":
        pwsh = shutil.which("pwsh")
        assert pwsh, "PowerShell 7 is required for the native Manager wrapper lane"
        wrapper = Path(__file__).resolve().parents[4] / "worktree-manager" / "bin" / "pane-wrapper.ps1"
        assert wrapper.is_file()
        assert not (offline_cli.home / ".agent-worktrees").exists()
        pane_argv = [pwsh, "-NoProfile", "-NonInteractive", "-File", str(wrapper), *native_argv]
    fixture = FixtureMux(binary, offline_cli.home, environment)
    target, query = fixture.target, fixture.query

    async def launch():
        absent = await asyncio.to_thread(query, "has-session", "-t", target)
        assert absent.returncode == 1, "The fixture target must be absent before creation"
        result = await asyncio.to_thread(
            query, "new-session", "-d", "-s", target, "-c", str(offline_cli.workspace),
            "--", *pane_argv,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        observed = await asyncio.to_thread(
            query, "display-message", "-p", "-t", target, "#{session_id}:#{session_created}|#{pane_id}",
        )
        assert observed.returncode == 0 and "|" in observed.stdout
        owner.identity = observed.stdout.strip()
        pane = await asyncio.to_thread(query, "display-message", "-p", "-t", target, "#{pane_pid}")
        assert pane.returncode == 0, pane.stderr
        owner.pane_pid = int(pane.stdout.strip())
        assert pid_alive(owner.pane_pid)
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
                + (await asyncio.to_thread(query, "capture-pane", "-p", "-t", target)).stdout
            )
            assert backend.native_pid and pid_alive(backend.native_pid)
            assert backend.extension_pid and pid_alive(backend.extension_pid)
            if pane_owner == "manager-wrapper":
                assert owner.pane_pid != backend.native_pid
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
                if all(not pid_alive(pid) for pid in (owner.pane_pid, backend.native_pid, backend.extension_pid)):
                    break
                await asyncio.sleep(0.05)
            assert not pid_alive(owner.pane_pid)
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
    await _native_coordinator_flow(tmp_path, offline_cli, monkeypatch, delivery)


@pytest.mark.asyncio
async def test_native_headed_startup_seed_observation_precedes_controlled_send(
    tmp_path, offline_cli, monkeypatch,
):
    await asyncio.wait_for(
        _native_coordinator_flow(tmp_path, offline_cli, monkeypatch, "startup-seed"), 120,
    )


@pytest.mark.asyncio
async def test_native_headed_startup_no_seed_is_not_an_idle_claim(
    tmp_path, offline_cli, monkeypatch,
):
    await asyncio.wait_for(
        _native_coordinator_flow(tmp_path, offline_cli, monkeypatch, "startup-no-seed"), 120,
    )


@pytest.mark.asyncio
async def test_native_headed_startup_cold_resume_preserves_history(
    tmp_path, offline_cli, monkeypatch,
):
    await asyncio.wait_for(
        _native_coordinator_flow(tmp_path, offline_cli, monkeypatch, "startup-cold-resume"), 120,
    )


def _startup_observation_stage(attempt_directory):
    from agent_remote_driver.headed.staging import stage_extension

    plugin = stage_extension(attempt_directory)
    extension = plugin / "extensions" / "copilot-driver-headed" / "extension.mjs"
    source = extension.read_text(encoding="utf-8")
    joined = "const session = await joinSession();"
    requests = "let buffer = Buffer.alloc(0);"
    assert source.count(joined) == source.count(requests) == 1
    observer = r"""
const fixtureFs = await import("node:fs");
const fixtureOn = session.on.bind(session);
session.on = (callback) => {
  let delayed = false, released = false;
  const queue = [];
  const unsubscribe = fixtureOn((event) => {
    const root = event?.type === "user.message" &&
      ![event.agentId, event.data?.agentId, event.data?.parentToolCallId].some(Boolean);
    if (!released && (delayed || (root && event.data?.content === binding.startupPrompt))) {
      if (!delayed) {
        fixtureFs.writeFileSync(process.env.DRIVER_STARTUP_HELD + ".tmp", JSON.stringify({
          sessionId: session.sessionId, nativePid: process.ppid, extensionPid: process.pid,
          eventId: event.id,
        }));
        fixtureFs.renameSync(process.env.DRIVER_STARTUP_HELD + ".tmp", process.env.DRIVER_STARTUP_HELD);
      }
      delayed = true;
      if (queue.length >= 2_000) throw Error("Fixture observation bound exceeded");
      queue.push(event);
    } else callback(event);
  });
  const timer = setInterval(() => {
    if (!fixtureFs.existsSync(process.env.DRIVER_STARTUP_RELEASE)) return;
    clearInterval(timer);
    released = true;
    for (const event of queue.splice(0)) callback(event);
  }, 10);
  timer.unref();
  return () => { clearInterval(timer); unsubscribe(); };
};
"""
    status = r"""
const fixtureHandle = adapter.handle.bind(adapter);
adapter.handle = async (request) => {
  const response = await fixtureHandle(request);
  if (request?.method === "copilot-driver/input-status") {
    fixtureFs.appendFileSync(process.env.DRIVER_STARTUP_STATUS, JSON.stringify(response) + "\n");
  }
  return response;
};
"""
    extension.write_text(
        source.replace(joined, joined + observer).replace(requests, status + requests),
        encoding="utf-8",
    )
    return plugin


async def _native_coordinator_flow(tmp_path, offline_cli, monkeypatch, delivery):
    if os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Explicitly select the isolated native headed mux lane")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from headed_mux_fixture import FixtureMux
    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    if os.name != "nt" or not binary:
        pytest.skip("The native coordinator fixture requires existing Windows PSMux")
    startup = delivery.startswith("startup-")
    no_seed, cold_resume = delivery == "startup-no-seed", delivery == "startup-cold-resume"
    if (delivery == "bridge-client" or startup) and not shutil.which("node"):
        pytest.skip("Node is required for the retained Bridge client")
    (offline_cli.home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(offline_cli.workspace)]}), encoding="utf-8",
    )
    (offline_cli.home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    session_id = str(uuid4())
    prompts = ['coordinator original\n"quotes" & $literal', "coordinator second"]
    if no_seed:
        prompts = prompts[1:]
    if cold_resume:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                offline_cli.executable, "--session-id", session_id, "-p", prompts[0],
                "--no-auto-update", "--disable-builtin-mcps", "--no-custom-instructions",
                "--no-color", "--no-ask-user", "--log-dir", str(offline_cli.logs),
            ],
            cwd=offline_cli.workspace, env=offline_cli.env, capture_output=True,
            timeout=60, text=True, encoding="utf-8", **no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr + _logs(offline_cli.logs)
        assert len(offline_cli.requests) == 1
    if delivery == "startup-seed":
        offline_cli.pause_response.set()
    receipt_nonce = uuid4().hex + uuid4().hex
    state_file = tmp_path / "host.json"
    coordinator_argv = [
        sys.executable,
        "-m", "agent_remote_driver.session_host", "--mode", "headed",
        "--attempt-directory", str(tmp_path / "original-attempt"),
        "--state-file", str(state_file), "--session-id", session_id, "--ws-port", "0",
        "--cwd", str(offline_cli.workspace), "--",
        offline_cli.executable,
        *(["-i", prompts[0]] if not (no_seed or cold_resume) else []),
        "--resume" if cold_resume else "--session-id", session_id,
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
    held_file, release_file, status_file = (
        tmp_path / name for name in ("startup-held.json", "startup-release", "startup-status.jsonl")
    )
    if startup:
        script = (
            inspect.getsource(_startup_observation_stage)
            + "\nimport agent_remote_driver.headed.launcher as headed\n"
            "from agent_remote_driver.session_host.launcher import main\n"
            "headed.stage_extension = _startup_observation_stage\n"
            "raise SystemExit(main())\n"
        )
        compile(script, "<fixture-startup-observation>", "exec")
        coordinator_argv = [
            sys.executable, "-c", script, *coordinator_argv[3:],
        ]
    fixture = FixtureMux(binary, offline_cli.home, {
        **offline_cli.env, "HOME": str(offline_cli.home), "USERPROFILE": str(offline_cli.home),
        "PSMUX_DATA_DIR": str(tmp_path / "psmux-data"), "PSMUX_CONFIG_FILE": os.devnull,
        "PSMUX_NO_WARM": "1",
        "AGENT_BRIDGE_SESSION_HOST_NONCE": receipt_nonce,
        **({
            "DRIVER_STARTUP_HELD": str(held_file), "DRIVER_STARTUP_RELEASE": str(release_file),
            "DRIVER_STARTUP_STATUS": str(status_file),
        } if startup else {}),
    })
    target, query = fixture.target, fixture.query
    identity, state, owner_pid, client_operation = None, None, None, None
    try:
        absent = await asyncio.to_thread(query, "has-session", "-t", target)
        assert absent.returncode == 1
        launched = await asyncio.to_thread(
            query, "new-session", "-d", "-s", target, "-c", str(offline_cli.workspace),
            "--", *coordinator_argv,
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
        if not startup:
            for _ in range(600):
                if event_file.is_file() and '"assistant.turn_end"' in event_file.read_text():
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Original coordinator seed did not finish")
        requests = (
            (1, "initialize", {"protocolVersion": 1}),
            (2, "session/prompt", {
                "sessionId": session_id, "prompt": [{"type": "text", "text": prompts[-1]}],
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
        elif delivery == "bridge-client" or startup:
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
                + json.dumps(prompts[-1]) + ",mode:"
                + json.dumps("enqueue" if startup else "immediate")
                + ",source:'agent-bridge'})}));}"
                "finally {client.close();}\n"
            )
            client_operation = asyncio.create_task(asyncio.to_thread(
                run_caller_owned, [shutil.which("node"), "--input-type=module", "-e", script],
                timeout=20, max_stdout_bytes=4096, max_stderr_bytes=4096,
            ))
            if delivery == "startup-seed":
                while not (
                    status_file.is_file() and "\n" in status_file.read_text(encoding="utf-8")
                ) and not client_operation.done():
                    await asyncio.sleep(.02)
                if not status_file.is_file() or "\n" not in status_file.read_text(encoding="utf-8"):
                    outcome = await client_operation
                    pytest.fail(
                        "The real Bridge client exited before startup readiness: "
                        f"exit={outcome.returncode}; stderr={outcome.stderr[-2048:]!r}"
                    )
                statuses = [
                    json.loads(line)["result"]
                    for line in status_file.read_text(encoding="utf-8").split("\n")[:-1]
                ]
                assert statuses[0]["startupSeed"] == "pending"
                assert statuses[0]["nativeSend"] == statuses[0]["prompt"] == "pending"
                for _ in range(100):
                    if held_file.is_file() and offline_cli.response_started.is_set():
                        break
                    await asyncio.sleep(.02)
                else:
                    pytest.fail("The actual SDK startup event and original model request were not held")
                held = json.loads(held_file.read_text(encoding="utf-8"))
                assert held["sessionId"] == session_id and held["eventId"]
                assert held["nativePid"] == state["child_pid"]
                assert held["extensionPid"] == state["extension_pid"]
                assert len(offline_cli.requests) == 1
                assert not client_operation.done()
                release_file.write_text("release original SDK observations", encoding="utf-8")
            accepted = await client_operation
            assert accepted.returncode == 0, accepted.stderr
            assert not accepted.stderr
            assert isinstance(json.loads(accepted.stdout)["messageId"], str)
            if startup:
                statuses = [
                    json.loads(line)["result"]
                    for line in status_file.read_text(encoding="utf-8").splitlines()
                ]
                assert all(value["sessionId"] == session_id for value in statuses)
                ready = [value for value in statuses if value["nativeSend"] == "ready"]
                assert ready
                if delivery == "startup-seed":
                    assert ready[-1]["startupSeed"] == "observed"
                    assert ready[-1]["startupTurn"] == "active" and ready[-1]["prompt"] == "pending"
                    assert len(offline_cli.requests) == 1
                    offline_cli.release_response.set()
                else:
                    assert ready[-1]["startupSeed"] == ready[-1]["startupTurn"] == "not-requested"
                    assert not held_file.exists()
            for _ in range(600):
                events = (
                    [json.loads(line) for line in event_file.read_text().splitlines()]
                    if event_file.is_file() else []
                )
                if sum(event["type"] == "assistant.turn_end" for event in events) >= len(prompts):
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
        assert len(offline_cli.requests) == len(prompts)
        if delivery == "startup-seed":
            assert next(event["id"] for event in events if event["type"] == "user.message") == held["eventId"]
        if startup:
            assert [path.parent.name for path in (offline_cli.home / "session-state").glob("*/events.jsonl")] == [session_id]
        assert not (offline_cli.home / "permissions-config.json").exists()
    finally:
        offline_cli.release_response.set()
        if client_operation is not None:
            await asyncio.gather(client_operation, return_exceptions=True)
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
