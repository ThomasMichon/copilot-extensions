"""Opt-in joined inventory reads in an actual native ACP session."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from agent_procutil import no_window_kwargs
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time
from test_headed_extension import _logs, offline_cli as offline_cli

pytestmark = pytest.mark.portfolio_tier("T3")


@pytest.mark.asyncio
async def test_native_acp_missing_inventory_is_not_an_empty_snapshot(tmp_path, offline_cli):
    plugin = tmp_path / "acp-inventory-plugin"
    extension = plugin / "extensions" / "acp-inventory-fixture"
    extension.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "acp-inventory-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (extension / "extension.mjs").write_text("""
import { writeFileSync } from "node:fs";
import { joinSession } from "@github/copilot-sdk/extension";
writeFileSync(process.env.DRIVER_EXTENSION_STARTED, "started");
const session = await joinSession();
const tasks = await session.rpc.tasks.list();
const schedules = await session.rpc.schedule.list();
writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
    sessionId: session.sessionId, pid: process.pid, tasks, schedules,
}));
""", encoding="utf-8")
    output, started = tmp_path / "acp-inventory.json", tmp_path / "extension-started"
    process = await asyncio.create_subprocess_exec(
        offline_cli.executable, "--acp", "--stdio", "--experimental",
        "--plugin-dir", str(plugin), "--no-auto-update", "--disable-builtin-mcps",
        "--no-custom-instructions", "--no-color", "--no-ask-user",
        "--log-dir", str(offline_cli.logs),
        cwd=offline_cli.workspace,
        env={
            **offline_cli.env, "DRIVER_FIXTURE_OUTPUT": str(output),
            "DRIVER_EXTENSION_STARTED": str(started),
        },
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    stderr_task = asyncio.create_task(process.stderr.read())
    identities = []

    def retain(pid):
        birth = process_start_time(pid)
        assert birth and pid_alive(pid)
        identities.append((pid, birth))

    async def rpc(identifier, method, params, *, allow_error=False):
        process.stdin.write((json.dumps({
            "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
        }) + "\n").encode())
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), 30)
            assert line, (await asyncio.wait_for(stderr_task, 5)).decode("utf-8", "replace")
            message = json.loads(line)
            if message.get("id") == identifier and "method" not in message:
                if allow_error:
                    return message
                assert "error" not in message, message
                return message["result"]

    try:
        retain(process.pid)
        initialized = await rpc(1, "initialize", {
            "protocolVersion": 1, "clientCapabilities": {},
            "clientInfo": {"name": "isolated-inventory-fixture", "version": "1"},
        })
        assert initialized["protocolVersion"] == 1
        created = await rpc(2, "session/new", {
            "cwd": str(offline_cli.workspace), "mcpServers": [],
        })
        result = await rpc(3, "session/prompt", {
            "sessionId": created["sessionId"],
            "prompt": [{"type": "text", "text": "ISOLATED_ACP_INVENTORY_FIXTURE"}],
        })
        assert result["stopReason"] == "end_turn"
        direct_reads = {
            "tasks": await rpc(4, "session.tasks.list", {
                "sessionId": created["sessionId"],
            }, allow_error=True),
            "schedules": await rpc(5, "session.schedule.list", {
                "sessionId": created["sessionId"],
            }, allow_error=True),
        }
        assert all(
            read.get("error", {}).get("code") == -32601 for read in direct_reads.values()
        ), direct_reads
        assert not started.exists() and not output.exists(), json.dumps({
            "started": started.exists(), "capabilities": initialized["agentCapabilities"],
            "providerRequests": len(offline_cli.requests), "directReads": direct_reads,
        })
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)
        stderr = await asyncio.wait_for(stderr_task, 5)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
            pid_alive(pid) and process_start_time(pid) == birth for pid, birth in identities
        ):
            await asyncio.sleep(.05)
        assert all(
            not pid_alive(pid) or (
                (current := process_start_time(pid)) is not None and current != birth
            ) for pid, birth in identities
        ), stderr.decode("utf-8", "replace")
    assert len(identities) == 1
    assert len(offline_cli.requests) == 1


def test_native_public_sdk_headless_inventory(tmp_path, offline_cli):
    interpreter = os.environ.get("COPILOT_NATIVE_TEST_SDK_PYTHON")
    if not interpreter:
        pytest.skip("Select an isolated interpreter with a seasoned public Python SDK")
    assert Path(interpreter).is_absolute() and Path(interpreter).is_file()
    identity, pinned, output = (tmp_path / name for name in ("native.json", "pinned", "sdk.json"))
    process = subprocess.Popen(
        [
            interpreter, str(Path(__file__).with_name("native_sdk_inventory_probe.py")),
            offline_cli.executable, str(offline_cli.workspace), str(offline_cli.logs),
            str(identity), str(pinned), str(output),
        ],
        cwd=offline_cli.workspace, env=offline_cli.env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", **no_window_kwargs(),
    )
    identities = []
    try:
        birth = process_start_time(process.pid)
        assert birth and pid_alive(process.pid)
        identities.append((process.pid, birth))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            try:
                native = json.loads(identity.read_text(encoding="utf-8"))
                birth = process_start_time(native["pid"])
                assert birth and pid_alive(native["pid"])
                identities.append((native["pid"], birth))
                pinned.write_text("pinned", encoding="utf-8")
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(.025)
        stdout, stderr = process.communicate(timeout=50)
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and any(
            pid_alive(pid) and process_start_time(pid) == birth for pid, birth in identities
        ):
            time.sleep(.05)
        assert all(
            not pid_alive(pid) or (
                (current := process_start_time(pid)) is not None and current != birth
            ) for pid, birth in identities
        )
    assert process.returncode == 0, stdout + stderr + _logs(offline_cli.logs)
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert len(identities) == 2 and identities[0][0] != identities[1][0]
    assert observed["beforeTasks"]["tasks"] == observed["afterTasks"]["tasks"] == []
    assert observed["beforeSchedules"]["entries"] == observed["afterSchedules"]["entries"] == []
    assert observed["responseType"] == "assistant.message"
    assert observed["responseText"] == "FIXTURE", observed
    assert observed["status"]["version"] and observed["status"]["protocolVersion"] >= 1
    assert len(offline_cli.requests) == 1
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    assert not any(
        event.get("data", {}).get("permissionRequest", {}).get("kind") == "extension-permission-access"
        for event in events
    )
    assert not (offline_cli.home / "permissions-config.json").exists()


def test_native_prompt_mode_loads_the_same_inventory_plugin(tmp_path, offline_cli):
    plugin = tmp_path / "inventory-plugin"
    extension = plugin / "extensions" / "inventory-fixture"
    extension.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "inventory-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (extension / "extension.mjs").write_text("""
import { writeFileSync } from "node:fs";
import { joinSession } from "@github/copilot-sdk/extension";
writeFileSync(process.env.DRIVER_EXTENSION_STARTED, "started");
const session = await joinSession();
const tasks = await session.rpc.tasks.list();
const schedules = await session.rpc.schedule.list();
writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
    sessionId: session.sessionId, pid: process.pid, tasks, schedules,
}));
""", encoding="utf-8")
    output, started = tmp_path / "inventory.json", tmp_path / "extension-started"
    result = subprocess.run(
        [
            offline_cli.executable, "-p", "ISOLATED_INVENTORY_FIXTURE", "--trust-folder",
            "--experimental", "--plugin-dir", str(plugin), "--no-auto-update",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
            "--log-dir", str(offline_cli.logs),
        ],
        cwd=offline_cli.workspace, env={
            **offline_cli.env, "DRIVER_FIXTURE_OUTPUT": str(output),
            "DRIVER_EXTENSION_STARTED": str(started),
        }, capture_output=True, timeout=60, text=True, encoding="utf-8", **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr + _logs(offline_cli.logs)
    assert started.is_file() and output.is_file(), _logs(offline_cli.logs)
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert observed["tasks"]["tasks"] == observed["schedules"]["entries"] == []
    assert len(offline_cli.requests) == 1
