"""Opt-in native pending-work transitions through public joined-session APIs."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from uuid import uuid4

import pytest

from agent_procutil import no_window_kwargs
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time
from test_headed_extension import _logs, offline_cli as offline_cli

pytestmark = pytest.mark.portfolio_tier("T3")


@pytest.mark.parametrize("kind", ["shell", "agent"])
@pytest.mark.parametrize("execution", ["sync", "background"])
@pytest.mark.skipif(os.name != "nt", reason="Native PowerShell tool control requires Windows")
def test_native_joined_pending_work_observes_real_tool(tmp_path, offline_cli, kind, execution):
    plugin = tmp_path / "native-task-plugin"
    extension = plugin / "extensions" / "native-task-fixture"
    extension.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "native-task-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (extension / "extension.mjs").write_text("""
import { existsSync, writeFileSync } from "node:fs";
import { joinSession } from "@github/copilot-sdk/extension";
const session = await joinSession();
const snapshots = [];
let taskId;
let actions;
let settled = false;
const save = () => writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
    sessionId: session.sessionId, snapshots, actions, settled,
}));
writeFileSync(process.env.DRIVER_OBSERVER_READY, JSON.stringify({
    sessionId: session.sessionId, pid: process.pid,
}));
let checking = false;
const timer = setInterval(async () => {
    if (checking) return;
    checking = true;
    try {
        const snapshot = await session.rpc.tasks.list();
        const encoded = JSON.stringify(snapshot);
        if (encoded !== JSON.stringify(snapshots.at(-1))) {
            snapshots.push(snapshot);
            save();
        }
        if (snapshot.tasks.some(task =>
            task.type === process.env.DRIVER_PENDING_KIND && task.status === "running"
        ) && existsSync(process.env.DRIVER_IDENTITIES_PINNED)) {
            taskId = snapshot.tasks.find(task =>
                task.type === process.env.DRIVER_PENDING_KIND && task.status === "running"
            ).id;
            writeFileSync(process.env.DRIVER_RELEASE_SHELL, "observed");
        }
        if (taskId && process.env.DRIVER_PENDING_KIND === "agent" &&
            snapshot.tasks.some(task => task.id === taskId && task.status === "idle")) {
            const refusedRemove = await session.rpc.tasks.remove({ id: taskId });
            const cancelled = await session.rpc.tasks.cancel({ id: taskId });
            const terminal = await session.rpc.tasks.list();
            const removed = await session.rpc.tasks.remove({ id: taskId });
            actions = { refusedRemove, cancelled, terminal, removed };
            snapshots.push(await session.rpc.tasks.list());
            settled = true;
        } else if (taskId && process.env.DRIVER_PENDING_KIND === "shell" &&
                   snapshot.tasks.length === 0) {
            settled = true;
        } else if (taskId && process.env.DRIVER_PENDING_KIND === "shell" &&
                   snapshot.tasks.some(task => task.id === taskId && task.status === "completed")) {
            actions = { removed: await session.rpc.tasks.remove({ id: taskId }) };
            snapshots.push(await session.rpc.tasks.list());
            settled = true;
        }
        if (settled) { clearInterval(timer); save(); }
    } catch (error) {
        clearInterval(timer);
        writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
            sessionId: session.sessionId, snapshots, error: String(error),
        }));
    } finally {
        checking = false;
    }
}, 25);
session.on("session.idle", () => { clearInterval(timer); });
""", encoding="utf-8")
    output, ready = tmp_path / "pending.json", tmp_path / "observer-ready.json"
    release = offline_cli.workspace / "release-shell"
    shell_pid = offline_cli.workspace / "shell-pid"
    pinned = tmp_path / "identities-pinned"
    command = (
        f"Set-Content -LiteralPath '{shell_pid}' -Value $PID; "
        "$deadline = [DateTime]::UtcNow.AddSeconds(15); "
        f"while (-not (Test-Path -LiteralPath '{release}')) {{ "
        "if ([DateTime]::UtcNow -gt $deadline) { throw 'Pending shell was not observed' }; "
        "Start-Sleep -Milliseconds 50 }; Write-Output 'ISOLATED_SHELL_SETTLED'"
    )
    arguments = {
        "command": command, "description": "Observe isolated pending shell",
        "mode": "sync" if execution == "sync" else "async",
        "initial_wait": 30, "shellId": "fixture-pending-shell",
    } if kind == "shell" else {
        "name": "pending-fixture", "description": "Observe isolated pending agent",
        "prompt": "Return ISOLATED_AGENT_SETTLED without using any tools.",
        "agent_type": "explore", "mode": execution,
    }
    offline_cli.responses.append({
        "delta": {"role": "assistant", "tool_calls": [{
            "index": 0, "id": "fixture-pending-tool", "type": "function",
            "function": {
                "name": "powershell" if kind == "shell" else "task",
                "arguments": json.dumps(arguments),
            },
        }]}, "finish_reason": "tool_calls",
    })
    settled_response = threading.Event()

    def gate_response(request):
        if any(
            message["role"] == "user" and "ISOLATED_AGENT_SETTLED" in str(message.get("content", ""))
            for message in request["messages"]
        ):
            return offline_cli.release_response
        if any(message["role"] == "tool" for message in request["messages"]):
            return settled_response
        return None

    offline_cli.response_gate.select = gate_response
    identities = []
    process = subprocess.Popen(
        [
            offline_cli.executable, "-p", "ISOLATED_NATIVE_PENDING_FIXTURE", "--trust-folder",
            "--experimental", "--plugin-dir", str(plugin),
            "--allow-tool", "shell" if kind == "shell" else "task",
            "--no-auto-update", "--disable-builtin-mcps", "--no-custom-instructions",
            "--no-color", "--no-ask-user", "--log-dir", str(offline_cli.logs),
        ],
        cwd=offline_cli.workspace, env={
            **offline_cli.env, "DRIVER_FIXTURE_OUTPUT": str(output),
            "DRIVER_RELEASE_SHELL": str(release),
            "DRIVER_OBSERVER_READY": str(ready), "DRIVER_IDENTITIES_PINNED": str(pinned),
            "DRIVER_PENDING_KIND": kind,
        }, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        **no_window_kwargs(),
    )

    def retain(pid):
        birth = process_start_time(pid)
        assert birth and pid_alive(pid)
        identities.append((pid, birth))

    try:
        retain(process.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            try:
                observer = json.loads(ready.read_text(encoding="utf-8"))
                observed = json.loads(output.read_text(encoding="utf-8"))
                active = [
                    task for snapshot in observed["snapshots"] for task in snapshot["tasks"]
                    if task["type"] == kind and task["status"] == "running"
                ]
                if active and (kind != "shell" or shell_pid.is_file()):
                    retain(observer["pid"])
                    if kind == "shell":
                        retain(int(shell_pid.read_text(encoding="utf-8").strip()))
                    pinned.write_text("pinned", encoding="utf-8")
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(.025)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            try:
                if release.is_file():
                    offline_cli.release_response.set()
                current = json.loads(output.read_text(encoding="utf-8"))
                if current.get("settled") or "error" in current:
                    settled_response.set()
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(.025)
        stdout, stderr = process.communicate(timeout=30)
    finally:
        offline_cli.release_response.set()
        settled_response.set()
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and any(
            pid_alive(pid) and process_start_time(pid) == birth for pid, birth in identities
        ):
            time.sleep(.05)
        assert all(
            not pid_alive(pid) or (
                (current := process_start_time(pid)) is not None and current != birth
            ) for pid, birth in identities
        )
    assert process.returncode == 0, stderr + _logs(offline_cli.logs)
    assert output.is_file(), stderr + _logs(offline_cli.logs)
    observed = json.loads(output.read_text(encoding="utf-8"))
    assert "error" not in observed and observed.get("settled"), observed
    assert release.is_file(), json.dumps({
        "observed": observed,
        "tools": [
            message for request in offline_cli.requests for message in request["messages"]
            if message["role"] == "tool"
        ],
        "output": stdout + stderr,
        "permissions": [
            json.loads(line) for path in (offline_cli.home / "session-state").glob("*/events.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
            if "permission" in line
        ],
    })
    assert len(identities) == (3 if kind == "shell" else 2), observed
    assert len({pid for pid, _ in identities}) == len(identities)
    assert len(offline_cli.requests) == (2 if kind == "shell" else 3), observed
    tool_results = [
        message for request in offline_cli.requests for message in request["messages"]
        if message["role"] == "tool" and message.get("tool_call_id") == "fixture-pending-tool"
    ]
    tool_result, = tool_results
    assert tool_result["content"] and "Permission denied" not in str(tool_result["content"])
    if execution == "sync":
        assert ("ISOLATED_SHELL_SETTLED" if kind == "shell" else "FIXTURE") in str(
            tool_result["content"],
        ), tool_result
    assert len({task["id"] for task in active}) == 1
    assert all(task["executionMode"] == execution for task in active), active
    if kind == "shell":
        assert all(task["command"] == command for task in active), active
    else:
        assert all(task["toolCallId"] == "fixture-pending-tool" for task in active), active
    task_id = active[0]["id"]
    assert observed["snapshots"][0]["tasks"] == []
    assert all(
        task["id"] == task_id and task["type"] == kind
        for snapshot in observed["snapshots"] for task in snapshot["tasks"]
    ), observed
    assert observed["snapshots"][-1]["tasks"] == [], json.dumps(observed)
    if kind == "agent":
        actions = observed["actions"]
        assert not actions["refusedRemove"]["removed"]
        assert actions["cancelled"]["cancelled"] and actions["removed"]["removed"]
        terminal, = actions["terminal"]["tasks"]
        assert terminal["id"] == task_id and terminal["status"] == "cancelled", terminal
    elif observed.get("actions"):
        assert observed["actions"]["removed"]["removed"]
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    expected_users = ["ISOLATED_NATIVE_PENDING_FIXTURE"]
    if kind == "agent":
        expected_users.append(arguments["prompt"])
    assert [
        event["data"]["content"] for event in events if event["type"] == "user.message"
    ] == expected_users
    assert not any(
        event.get("data", {}).get("permissionRequest", {}).get("kind") == "extension-permission-access"
        for event in events
    )
    assert not (offline_cli.home / "permissions-config.json").exists()


def test_native_joined_client_task_transitions_remain_visible_until_removed(tmp_path, offline_cli):
    plugin = tmp_path / "pending-work-plugin"
    extension = plugin / "extensions" / "pending-work-fixture"
    extension.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "pending-work-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (extension / "extension.mjs").write_text("""
import { writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { joinSession } from "@github/copilot-sdk/extension";
const session = await joinSession();
const api = session.rpc.tasks;
const initial = await api.list();
const registered = await api.register({
    type: "client", clientTaskId: randomUUID(),
    description: "Isolated pending-work fixture", cancellable: false,
});
const running = await api.list();
const idle = await api.update({
    id: registered.task.id, sequence: registered.task.sequence + 1,
    update: { kind: "progress", status: "idle" },
});
const waiting = await api.list();
const completed = await api.update({
    id: idle.task.id, sequence: idle.task.sequence + 1,
    update: { kind: "completed", result: { fixture: true } },
});
const settled = await api.list();
const removed = await api.remove({ id: completed.task.id });
const final = await api.list();
writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
    sessionId: session.sessionId, initial, registered, running, idle, waiting,
    completed, settled, removed, final,
}));
""", encoding="utf-8")
    output = tmp_path / "pending-work.json"
    result = subprocess.run(
        [
            offline_cli.executable, "-p", "ISOLATED_PENDING_WORK_FIXTURE", "--trust-folder",
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
    assert observed["initial"]["tasks"] == observed["final"]["tasks"] == []
    assert observed["registered"]["created"] and not observed["registered"]["reclaimed"]
    task_id = observed["registered"]["task"]["id"]
    for snapshot, status in (("running", "running"), ("waiting", "idle"), ("settled", "completed")):
        tasks = observed[snapshot]["tasks"]
        assert len(tasks) == 1 and tasks[0]["id"] == task_id
        assert tasks[0]["type"] == "client" and tasks[0]["status"] == status
        assert tasks[0]["executionMode"] == "background"
    assert observed["idle"]["applied"] and observed["completed"]["applied"]
    assert observed["completed"]["task"]["result"] == {"fixture": True}
    assert observed["removed"]["removed"]
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    events = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    assert not any(
        event.get("data", {}).get("permissionRequest", {}).get("kind") == "extension-permission-access"
        for event in events
    )
    assert len(offline_cli.requests) == 1
    assert not (offline_cli.home / "permissions-config.json").exists()


def test_native_joined_schedule_list_and_exact_stop_observe_real_command(
    tmp_path, offline_cli, monkeypatch,
):
    if os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Explicitly select the isolated native Windows terminal lane")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from headed_mux_fixture import FixtureMux

    binary = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux.exe")
    assert binary, "Select an existing native PSMux"
    plugin = tmp_path / "schedule-plugin"
    extension = plugin / "extensions" / "schedule-fixture"
    extension.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "schedule-fixture", "version": "1.0.0"}), encoding="utf-8",
    )
    (extension / "extension.mjs").write_text("""
import { writeFileSync } from "node:fs";
import { joinSession } from "@github/copilot-sdk/extension";
const session = await joinSession();
writeFileSync(process.env.DRIVER_OBSERVER_READY, JSON.stringify({
    sessionId: session.sessionId, pid: process.pid,
}));
let checking = false;
const timer = setInterval(async () => {
    if (checking) return;
    checking = true;
    try {
        const before = await session.rpc.schedule.list();
        if (before.entries?.length === 0) return;
        if (before.entries.length !== 1 ||
            before.entries[0].prompt !== "ISOLATED_FUTURE_SCHEDULE_FIXTURE") {
            throw new Error("The fixture's single schedule is not identified");
        }
        clearInterval(timer);
        const stopped = await session.rpc.schedule.stop({ id: before.entries[0].id });
        const after = await session.rpc.schedule.list();
        writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({
            sessionId: session.sessionId, before, stopped, after,
        }));
    } catch (error) {
        clearInterval(timer);
        writeFileSync(process.env.DRIVER_FIXTURE_OUTPUT, JSON.stringify({ error: error.name }));
    } finally {
        checking = false;
    }
}, 50);
""", encoding="utf-8")
    output, ready = tmp_path / "schedule.json", tmp_path / "observer-ready.json"
    native = str(uuid4())
    history = offline_cli.home / "session-state" / native / "events.jsonl"
    (offline_cli.home / "config.json").write_text(
        json.dumps({"trustedFolders": [str(offline_cli.workspace)]}), encoding="utf-8",
    )
    (offline_cli.home / ".psmux.conf").write_text("set -g warm off\n", encoding="utf-8")
    environment = {
        **offline_cli.env, "HOME": str(offline_cli.home), "USERPROFILE": str(offline_cli.home),
        "PSMUX_DATA_DIR": str(tmp_path / "mux-data"), "PSMUX_CONFIG_FILE": os.devnull,
        "PSMUX_NO_WARM": "1", "DRIVER_FIXTURE_OUTPUT": str(output),
        "DRIVER_OBSERVER_READY": str(ready),
    }
    mux = FixtureMux(binary, offline_cli.home, environment)
    identities = []
    incarnation = None

    def until(check, label):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                value = check()
                if value:
                    return value
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(.05)
        pytest.fail(
            label + "\n" + _logs(offline_cli.logs) + "\n"
            + mux.query("capture-pane", "-p", "-t", mux.target).stdout[-8192:],
        )

    def events():
        return [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]

    try:
        launched = mux.query(
            "new-session", "-d", "-s", mux.target, "-c", str(offline_cli.workspace),
            "-P", "-F", "#{session_id}:#{session_created}|#{pane_id}", "--",
            offline_cli.executable, "-i", "ISOLATED_SCHEDULE_FIXTURE", "--session-id", native,
            "--experimental", "--screen-reader", "--plugin-dir", str(plugin), "--no-auto-update",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
            "--log-dir", str(offline_cli.logs),
        )
        assert launched.returncode == 0, launched.stdout + launched.stderr
        incarnation = launched.stdout.strip()
        assert re.fullmatch(r"\$[0-9]+:[0-9]+\|%[0-9]+", incarnation), incarnation
        pane = mux.query("display-message", "-p", "-t", mux.target, "#{pane_pid}")
        assert pane.returncode == 0 and pane.stdout.strip().isdigit(), pane.stdout + pane.stderr
        native_pid = int(pane.stdout.strip())
        birth = process_start_time(native_pid)
        assert pid_alive(native_pid) and birth
        identities.append((native_pid, birth))
        observer = until(lambda: json.loads(ready.read_text(encoding="utf-8")), "Observer did not join")
        assert observer["sessionId"] == native
        assert observer["pid"] != native_pid
        birth = process_start_time(observer["pid"])
        assert pid_alive(observer["pid"]) and birth
        identities.append((observer["pid"], birth))
        until(
            lambda: any(event["type"] == "assistant.turn_end" for event in events()),
            "Original native turn did not finish",
        )
        assert mux.query(
            "send-keys", "-t", mux.target, "-l", "--",
            "/every 2h ISOLATED_FUTURE_SCHEDULE_FIXTURE",
        ).returncode == 0
        assert mux.query("send-keys", "-t", mux.target, "Enter").returncode == 0
        observed = until(
            lambda: json.loads(output.read_text(encoding="utf-8")),
            "The native command did not produce an observed, exactly stopped schedule",
        )
    finally:
        present = mux.query("has-session", "-t", mux.target)
        assert present.returncode in (0, 1), present.stderr
        if present.returncode == 0:
            current = mux.query(
                "display-message", "-p", "-t", mux.target,
                "#{session_id}:#{session_created}|#{pane_id}",
            )
            assert incarnation and current.returncode == 0 and current.stdout.strip() == incarnation
            assert mux.query("kill-session", "-t", mux.target).returncode == 0
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
    assert "error" not in observed, observed
    assert len(identities) == 2
    entry, = observed["before"]["entries"]
    assert observed["sessionId"] == native
    assert entry["prompt"] == "ISOLATED_FUTURE_SCHEDULE_FIXTURE"
    assert entry["intervalMs"] == 7_200_000 and entry["recurring"]
    assert observed["stopped"]["entry"]["id"] == entry["id"]
    assert observed["after"]["entries"] == []
    session_files = list((offline_cli.home / "session-state").glob("*/events.jsonl"))
    assert [path.parent.name for path in session_files] == [observed["sessionId"]]
    recorded = events()
    assert [
        event["data"]["content"] for event in recorded if event["type"] == "user.message"
    ] == ["ISOLATED_SCHEDULE_FIXTURE"]
    assert not any(
        event.get("data", {}).get("permissionRequest", {}).get("kind") == "extension-permission-access"
        for event in recorded
    )
    assert len(offline_cli.requests) == 1
    assert not (offline_cli.home / "permissions-config.json").exists()
