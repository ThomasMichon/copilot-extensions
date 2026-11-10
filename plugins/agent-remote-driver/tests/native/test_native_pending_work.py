"""Opt-in native pending-work transitions through public joined-session APIs."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from uuid import uuid4

import pytest

from agent_procutil import no_window_kwargs
from agent_remote_driver.session_host.osutil import pid_alive, process_start_time
from test_headed_extension import _logs, offline_cli as offline_cli

pytestmark = pytest.mark.portfolio_tier("T3")


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
