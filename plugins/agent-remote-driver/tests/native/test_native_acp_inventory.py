"""Opt-in joined inventory reads in an actual native ACP session."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import run_caller_owned
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


@pytest.mark.parametrize("mode,timeout_helper", [
    ("empty", False), ("empty", True), ("client-tasks", False), ("schedule", False),
    ("shell-sync", False), ("shell-background", False),
    ("agent-sync", False), ("agent-background", False),
])
def test_native_public_sdk_headless_inventory(tmp_path, offline_cli, mode, timeout_helper):
    kind = mode.split("-")[0] if mode.startswith(("shell-", "agent-")) else None
    if kind == "shell" and os.name != "nt":
        pytest.skip("Native PowerShell tool control requires Windows")
    interpreter = os.environ.get("COPILOT_NATIVE_TEST_SDK_PYTHON")
    if not interpreter:
        pytest.skip("Select an isolated interpreter with a seasoned public Python SDK")
    assert Path(interpreter).is_absolute() and Path(interpreter).is_file()
    identity, pinned, output = (tmp_path / name for name in ("native.json", "pinned", "sdk.json"))
    arguments = [
        interpreter, str(Path(__file__).with_name("native_sdk_inventory_probe.py")),
        offline_cli.executable, str(offline_cli.workspace), str(offline_cli.logs),
        str(identity), str(pinned), str(output), mode,
    ]
    settled = threading.Event()
    if timeout_helper:
        offline_cli.pause_response.set()
    if mode == "schedule":
        offline_cli.responses.append({
            "delta": {"role": "assistant", "tool_calls": [{
                "index": 0, "id": "fixture-schedule-tool", "type": "function",
                "function": {
                    "name": "manage_schedule", "arguments": json.dumps({
                        "action": "create", "interval": "2h",
                        "prompt": "ISOLATED_FUTURE_SCHEDULE_FIXTURE",
                    }),
                },
            }]}, "finish_reason": "tool_calls",
        })
    if kind:
        command = (
            f"[System.IO.File]::WriteAllText('{offline_cli.workspace / 'shell-pid'}.pending', [string]$PID); "
            f"[System.IO.File]::Move('{offline_cli.workspace / 'shell-pid'}.pending', '{offline_cli.workspace / 'shell-pid'}'); "
            "$deadline = [DateTime]::UtcNow.AddSeconds(15); "
            f"while (-not (Test-Path -LiteralPath '{offline_cli.workspace / 'release-shell'}')) {{ "
            "if ([DateTime]::UtcNow -gt $deadline) { throw 'Pending shell was not observed' }; "
            "Start-Sleep -Milliseconds 50 }; Write-Output 'ISOLATED_SHELL_SETTLED'"
        )
        offline_cli.env["DRIVER_FIXTURE_SHELL_COMMAND"] = command
        tool_arguments = {
            "command": command, "description": "Observe isolated pending shell",
            "mode": "sync" if mode.endswith("-sync") else "async",
            "initial_wait": 30, "shellId": "fixture-pending-shell",
        } if kind == "shell" else {
            "name": "pending-fixture", "description": "Observe isolated pending agent",
            "prompt": "Return ISOLATED_AGENT_SETTLED without using any tools.",
            "agent_type": "explore", "mode": mode.split("-")[1],
        }
        offline_cli.responses.append({
            "delta": {"role": "assistant", "tool_calls": [{
                "index": 0, "id": "fixture-pending-tool", "type": "function",
                "function": {
                    "name": "powershell" if kind == "shell" else "task",
                    "arguments": json.dumps(tool_arguments),
                },
            }]}, "finish_reason": "tool_calls",
        })
        if kind == "agent":
            offline_cli.response_gate.select = lambda request: (
                offline_cli.release_response if any(
                    message["role"] == "user" and "ISOLATED_AGENT_SETTLED" in str(message.get("content", ""))
                    for message in request["messages"]
                ) else None
            )
    identities = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        execution = executor.submit(
            run_caller_owned, arguments, timeout=20 if timeout_helper else 50,
            max_stdout_bytes=128_000, max_stderr_bytes=128_000,
            cwd=offline_cli.workspace, env=offline_cli.env, on_settled=settled.set,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not execution.done():
                try:
                    native = json.loads(identity.read_text(encoding="utf-8"))
                    for pid in (native["helperPid"], native["pid"]):
                        birth = process_start_time(pid)
                        assert birth and pid_alive(pid)
                        identities.append((pid, birth))
                    pinned.write_text("pinned", encoding="utf-8")
                    break
                except (FileNotFoundError, json.JSONDecodeError):
                    time.sleep(.025)
            if kind:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not execution.done():
                    if (tmp_path / "tool-observed").is_file():
                        if kind == "shell":
                            pid = int((offline_cli.workspace / "shell-pid").read_text(encoding="utf-8"))
                            birth = process_start_time(pid)
                            assert birth and pid_alive(pid)
                            identities.append((pid, birth))
                        (tmp_path / "tool-pinned").write_text("pinned", encoding="utf-8")
                        offline_cli.release_response.set()
                        break
                    time.sleep(.025)
            if timeout_helper:
                assert offline_cli.response_started.wait(10), "SDK model request did not begin"
                with pytest.raises(subprocess.TimeoutExpired):
                    execution.result(timeout=60)
            else:
                result = execution.result(timeout=65)
        finally:
            offline_cli.release_response.set()
            try:
                execution.result(timeout=65)
            except subprocess.TimeoutExpired:
                pass
            assert settled.is_set(), "The owned native process scope did not settle"
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and any(
                pid_alive(pid) and process_start_time(pid) == birth for pid, birth in identities
            ):
                time.sleep(.05)
            assert all(
                not pid_alive(pid) or (
                    (current := process_start_time(pid)) is not None and current != birth
                ) for pid, birth in identities
            )
    assert len(identities) == (3 if kind == "shell" else 2)
    assert len({pid for pid, _ in identities}) == len(identities)
    if mode == "schedule":
        tools = [
            tool for request in offline_cli.requests for tool in request.get("tools", [])
            if tool.get("function", {}).get("name") == "manage_schedule"
        ]
        assert tools, "The selected SDK session did not offer manage_schedule"
    expected_requests = 3 if kind == "agent" else 2 if kind == "shell" or mode == "schedule" else 1
    if mode == "agent-background":
        assert len(offline_cli.requests) in (3, 4)
    else:
        assert len(offline_cli.requests) == expected_requests
    if timeout_helper:
        assert not output.exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr + _logs(offline_cli.logs)
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert observed["beforeTasks"]["tasks"] == observed["afterTasks"]["tasks"] == []
    assert observed["beforeSchedules"]["entries"] == observed["afterSchedules"]["entries"] == []
    assert observed["responseType"] == "assistant.message"
    assert observed["responseText"] == "FIXTURE", observed
    assert observed["status"]["version"] and observed["status"]["protocolVersion"] >= 1
    schedule = observed["scheduleControl"]
    if mode == "schedule":
        entry, = schedule["before"]["entries"]
        assert entry["prompt"] == "ISOLATED_FUTURE_SCHEDULE_FIXTURE"
        assert entry["intervalMs"] == 7_200_000 and entry["recurring"]
        assert schedule["tasksWithSchedule"]["tasks"] == []
        assert schedule["otherSessionId"] != observed["sessionId"]
        assert schedule["otherSchedules"]["entries"] == []
        assert schedule["afterOtherDisconnect"]["entries"] == [entry]
        assert schedule["stopped"]["entry"]["id"] == entry["id"]
        assert schedule["after"]["entries"] == []
    else:
        assert schedule is None
    pending = observed["pendingControl"]
    if kind:
        assert pending["snapshots"][0]["tasks"] == pending["snapshots"][-1]["tasks"] == []
        assert pending["afterPendingWait"]["tasks"] == []
        active = [
            task for snapshot in pending["snapshots"] for task in snapshot["tasks"]
            if task["type"] == kind and task["status"] == "running"
        ]
        assert active and len({task["id"] for task in active}) == 1, pending
        task_id = active[0]["id"]
        assert all(
            task["id"] == task_id and task["type"] == kind
            for snapshot in pending["snapshots"] for task in snapshot["tasks"]
        ), pending
        assert all(task["executionMode"] == mode.split("-")[1] for task in active), pending
        if kind == "shell":
            assert all(task["command"] == command for task in active)
            assert observed["permissions"] == ["fixture-shell"]
        else:
            assert observed["permissions"] == []
            assert not pending["refusedRemove"]["removed"]
            assert pending["cancelled"]["cancelled"]
            terminal, = pending["terminal"]["tasks"]
            assert terminal["id"] == task_id and terminal["status"] == "cancelled"
            if len(offline_cli.requests) == 4:
                assert task_id in str(offline_cli.requests[-1]["messages"])
                assert "has finished processing and is now idle." in str(
                    offline_cli.requests[-1]["messages"],
                )
        if "removed" in pending:
            assert pending["removed"]["removed"]
        tool_results = [
            message for request in offline_cli.requests for message in request["messages"]
            if message["role"] == "tool" and message.get("tool_call_id") == "fixture-pending-tool"
        ]
        tool_result, = {json.dumps(message, sort_keys=True) for message in tool_results}
        tool_result = json.loads(tool_result)
        assert tool_result["content"] and "Permission denied" not in str(tool_result["content"])
    else:
        assert pending is None and observed["permissions"] == []
    control = observed["clientTaskControl"]
    if mode == "client-tasks":
        registered = control["registration"]
        assert registered["created"] and not registered["reclaimed"]
        task_id = registered["task"]["id"]
        assert control["otherSessionId"] != observed["sessionId"]
        assert control["otherTasks"]["tasks"] == []
        assert control["runningRemoval"]["removed"] is False
        assert control["idleRemoval"]["removed"] is False
        for label, status in (
            ("running", "running"), ("afterOtherDisconnect", "running"),
            ("idle", "idle"), ("completed", "completed"),
        ):
            tasks = control[label]["tasks"]
            assert len(tasks) == 1 and tasks[0]["id"] == task_id, control
            assert tasks[0]["type"] == "client" and tasks[0]["status"] == status, control
        for label in ("idle", "completed"):
            assert control[f"{label}Update"]["applied"]
            assert not control[f"{label}Update"]["duplicate"]
        assert control["completed"]["tasks"][0]["result"] == "ISOLATED_TASK_COMPLETE"
        assert control["terminalRemoval"]["removed"]
        assert control["afterRemoval"]["tasks"] == []
    else:
        assert control is None
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    if kind:
        assert len([
            event for event in events if event["type"] == "tool.execution_start"
            and event["data"].get("toolCallId") == "fixture-pending-tool"
        ]) == 1
    inputs = [
        event["data"]["content"] for event in events if event["type"] == "user.message"
    ]
    assert inputs == ["ISOLATED_HEADLESS_SDK_INVENTORY_FIXTURE"] + (
        ["Return ISOLATED_AGENT_SETTLED without using any tools."] if kind == "agent" else []
    )
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
