"""Owned native TCP runtime accessed only through the public Python SDK."""

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from uuid import uuid4

from copilot import CopilotClient, RuntimeConnection
from copilot.generated.rpc import (
    PermissionDecisionApproveOnce, ScheduleStopRequest, TasksCancelRequest,
    TasksRegisterRequest, TasksRemoveRequest, TasksUpdateRequest,
)


async def pending_control(session, kind, workspace, output, initial):
    observed = output.with_name("tool-observed")
    pinned = output.with_name("tool-pinned")
    snapshots = [initial.to_dict()]
    task_id = None
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        snapshot = (await session.rpc.tasks.list()).to_dict()
        if [(task["id"], task["status"]) for task in snapshot["tasks"]] != [
            (task["id"], task["status"]) for task in snapshots[-1]["tasks"]
        ]:
            snapshots.append(snapshot)
        if task_id is None:
            running = [
                task for task in snapshot["tasks"]
                if task["type"] == kind and task["status"] == "running"
            ]
            if running and (kind != "shell" or (workspace / "shell-pid").is_file()):
                task, = running
                task_id = task["id"]
                observed.write_text(task_id, encoding="utf-8")
                while not pinned.is_file():
                    assert time.monotonic() < deadline, "Tool identity was not pinned"
                    await asyncio.sleep(.025)
                (workspace / "release-shell").write_text("observed", encoding="utf-8")
        elif kind == "agent" and any(
            task["id"] == task_id and task["status"] == "idle" for task in snapshot["tasks"]
        ):
            refused = await session.rpc.tasks.remove(TasksRemoveRequest.from_dict({"id": task_id}))
            cancelled = await session.rpc.tasks.cancel(TasksCancelRequest.from_dict({"id": task_id}))
            terminal = await session.rpc.tasks.list()
            removed = await session.rpc.tasks.remove(TasksRemoveRequest.from_dict({"id": task_id}))
            snapshots.append((await session.rpc.tasks.list()).to_dict())
            return {
                "snapshots": snapshots, "refusedRemove": refused.to_dict(),
                "cancelled": cancelled.to_dict(), "terminal": terminal.to_dict(),
                "removed": removed.to_dict(),
            }
        elif kind == "shell":
            if not snapshot["tasks"]:
                return {"snapshots": snapshots}
            if any(
                task["id"] == task_id and task["status"] == "completed"
                for task in snapshot["tasks"]
            ):
                removed = await session.rpc.tasks.remove(TasksRemoveRequest.from_dict({"id": task_id}))
                snapshots.append((await session.rpc.tasks.list()).to_dict())
                return {"snapshots": snapshots, "removed": removed.to_dict()}
        await asyncio.sleep(.025)
    raise AssertionError(f"The native {kind} did not reach its settled task state: {snapshots}")


async def schedule_control(client, session, options):
    before = await session.rpc.schedule.list()
    entry, = before.entries
    result = {"before": before.to_dict()}
    try:
        assert entry.prompt == "ISOLATED_FUTURE_SCHEDULE_FIXTURE"
        result["tasksWithSchedule"] = (await session.rpc.tasks.list()).to_dict()
        other = await client.create_session(**options)
        try:
            result["otherSessionId"] = other.session_id
            result["otherSchedules"] = (await other.rpc.schedule.list()).to_dict()
        finally:
            await asyncio.wait_for(other.disconnect(), 5)
        result["afterOtherDisconnect"] = (await session.rpc.schedule.list()).to_dict()
    finally:
        result["stopped"] = (await session.rpc.schedule.stop(
            ScheduleStopRequest.from_dict({"id": entry.id}),
        )).to_dict()
        result["after"] = (await session.rpc.schedule.list()).to_dict()
    return result


async def client_task_control(client, session, options):
    registration = await session.rpc.tasks.register(TasksRegisterRequest.from_dict({
        "cancellable": False, "clientTaskId": "isolated-client-task",
        "description": "Isolated SDK inventory control", "type": "client",
    }))
    task = registration.task
    result = {
        "registration": registration.to_dict(),
        "running": (await session.rpc.tasks.list()).to_dict(),
        "runningRemoval": (await session.rpc.tasks.remove(
            TasksRemoveRequest.from_dict({"id": task.id}),
        )).to_dict(),
    }
    other = await client.create_session(**options)
    try:
        result["otherSessionId"] = other.session_id
        result["otherTasks"] = (await other.rpc.tasks.list()).to_dict()
    finally:
        await asyncio.wait_for(other.disconnect(), 5)
    result["afterOtherDisconnect"] = (await session.rpc.tasks.list()).to_dict()
    for label, update in (
        ("idle", {"kind": "progress", "status": "idle"}),
        ("completed", {"kind": "completed", "result": "ISOLATED_TASK_COMPLETE"}),
    ):
        updated = await session.rpc.tasks.update(TasksUpdateRequest.from_dict({
            "id": task.id, "sequence": task.sequence + 1, "update": update,
        }))
        task = updated.task
        result[f"{label}Update"] = updated.to_dict()
        result[label] = (await session.rpc.tasks.list()).to_dict()
        if label == "idle":
            result["idleRemoval"] = (await session.rpc.tasks.remove(
                TasksRemoveRequest.from_dict({"id": task.id}),
            )).to_dict()
    result["terminalRemoval"] = (await session.rpc.tasks.remove(
        TasksRemoveRequest.from_dict({"id": task.id}),
    )).to_dict()
    result["afterRemoval"] = (await session.rpc.tasks.list()).to_dict()
    return result


async def main():
    executable, workspace, logs, identity_path, pinned_path, output_path, mode = sys.argv[1:]
    connection_token = uuid4().hex + uuid4().hex
    process = subprocess.Popen(
        [
            executable, "--headless", "--no-auto-update", "--no-auto-login",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color",
            "--no-ask-user", "--log-dir", logs,
        ],
        cwd=workspace, env={**os.environ, "COPILOT_CONNECTION_TOKEN": connection_token},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    errors = []
    stderr_reader = stdout_reader = None
    client = session = None
    try:
        identity = Path(identity_path)
        temporary = identity.with_suffix(".pending")
        temporary.write_text(json.dumps({
            "pid": process.pid, "helperPid": os.getpid(),
        }), encoding="utf-8")
        temporary.replace(identity)
        stderr_reader = threading.Thread(
            target=lambda: errors.append(process.stderr.read()), daemon=True,
        )
        stderr_reader.start()
        deadline = time.monotonic() + 15
        while not Path(pinned_path).is_file():
            assert time.monotonic() < deadline, "Native runtime identity was not pinned"
            await asyncio.sleep(.025)
        while True:
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 15)
            assert line, b"".join(errors).decode("utf-8", "replace")
            match = re.search(rb"listening on port (\d+)", line, re.IGNORECASE)
            if match:
                port = int(match.group(1))
                break
        stdout_reader = threading.Thread(target=process.stdout.read, daemon=True)
        stdout_reader.start()
        client = CopilotClient(
            connection=RuntimeConnection.for_uri(
                f"127.0.0.1:{port}", connection_token=connection_token,
            ),
            working_directory=workspace, env=os.environ.copy(), use_logged_in_user=False,
        )
        await client.start()
        status = await client.get_status()
        options = dict(
            model="gpt-4.1", working_directory=workspace, skip_custom_instructions=True,
            enable_config_discovery=False, enable_file_hooks=False, enable_skills=False,
            mcp_servers={}, request_extensions=False, streaming=True,
            provider={
                "type": "openai", "base_url": os.environ["COPILOT_PROVIDER_BASE_URL"],
                "wire_api": "completions",
            },
        )
        if mode == "schedule":
            options["manage_schedule_enabled"] = True
        kind = mode.split("-")[0] if mode.startswith(("shell-", "agent-")) else None
        permissions = []
        if kind == "shell":
            def permit_fixture_shell(request, invocation):
                assert not invocation.get("managed_settings_enabled", False)
                assert not getattr(request, "managed_approval_required", False)
                assert request.kind == "shell" and invocation["session_id"] == session.session_id
                assert getattr(request, "full_command_text", None) == os.environ["DRIVER_FIXTURE_SHELL_COMMAND"]
                permissions.append("fixture-shell")
                return PermissionDecisionApproveOnce()
            options["on_permission_request"] = permit_fixture_shell
        session = await client.create_session(**options)
        before_tasks = await session.rpc.tasks.list()
        before_schedules = await session.rpc.schedule.list()
        control = await client_task_control(client, session, options) if mode == "client-tasks" else None
        turn = asyncio.create_task(session.send_and_wait("ISOLATED_HEADLESS_SDK_INVENTORY_FIXTURE", timeout=30))
        try:
            pending = await pending_control(
                session, kind, Path(workspace), Path(output_path), before_tasks,
            ) if kind else None
            response = await turn
            if kind:
                await session.rpc.tasks.wait_for_pending(timeout=10)
                pending["afterPendingWait"] = (await session.rpc.tasks.list()).to_dict()
        finally:
            if not turn.done():
                turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        schedule = await schedule_control(client, session, options) if mode == "schedule" else None
        after_tasks = await session.rpc.tasks.list()
        after_schedules = await session.rpc.schedule.list()
        result = {
            "sessionId": session.session_id, "status": status.to_dict(),
            "beforeTasks": before_tasks.to_dict(), "beforeSchedules": before_schedules.to_dict(),
            "afterTasks": after_tasks.to_dict(), "afterSchedules": after_schedules.to_dict(),
            "responseType": response.type.value if response else None,
            "responseText": getattr(response.data, "content", None) if response else None,
            "clientTaskControl": control,
            "scheduleControl": schedule,
            "pendingControl": pending, "permissions": permissions,
        }
    finally:
        try:
            try:
                if session is not None:
                    await asyncio.wait_for(session.disconnect(), 5)
            finally:
                if client is not None:
                    await asyncio.wait_for(client.stop(), 5)
        finally:
            if process.poll() is None:
                process.terminate()
            await asyncio.to_thread(process.wait, 10)
            if stderr_reader is not None and stderr_reader.ident is not None:
                stderr_reader.join(5)
                assert not stderr_reader.is_alive(), "Native stderr remained open after retirement"
            if stdout_reader is not None and stdout_reader.ident is not None:
                stdout_reader.join(5)
                assert not stdout_reader.is_alive(), "Native stdout remained open after retirement"
    Path(output_path).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
