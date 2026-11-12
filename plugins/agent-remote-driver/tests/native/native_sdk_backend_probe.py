"""Exercise an already-built optional application; never install a runtime or SDK."""

import asyncio
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

from copilot import CopilotClient, RuntimeConnection
from copilot.generated.rpc import (
    TasksCancelRequest, TasksRegisterRequest, TasksRemoveRequest, TasksUpdateRequest,
)

from agent_remote_driver.session_host import launcher, sdk_backend
from agent_remote_driver.session_host.client import SessionHostClient


async def main():
    executable, workspace, logs, output_directory, mode = sys.argv[1:]
    output = Path(output_directory)
    sessions = []
    clients = []

    class FixtureClient(CopilotClient):
        async def create_session(self, **options):
            session = await super().create_session(**fixture_options(options))
            sessions.append(session)
            return session

        async def resume_session(self, session_id, **options):
            session = await super().resume_session(session_id, **fixture_options(options))
            sessions.append(session)
            return session

        def __init__(self, **options):
            super().__init__(**options)
            clients.append(self)

    def fixture_options(options):
        return {
            **options, "model": "gpt-4.1", "skip_custom_instructions": True,
            "enable_config_discovery": False, "enable_file_hooks": False, "enable_skills": False,
            "mcp_servers": {}, "request_extensions": False,
            **({"manage_schedule_enabled": True} if mode == "schedule" else {}),
            "provider": {
                "type": "openai", "base_url": os.environ["COPILOT_PROVIDER_BASE_URL"],
                "wire_api": "completions",
            },
        }

    # Only public session configuration is fixture-specific. The artifact's
    # native spawn, SDK connection, permission adapter and broker are unchanged.
    sdk_backend.require_sdk = lambda: (FixtureClient, RuntimeConnection)
    native_id = None
    facts = []
    runtime_statuses = []
    permissions = []
    observations = []
    for incarnation in range(2 if mode == "plain" else 1):
        state_file = output / f"host-{incarnation}.json"
        ready = asyncio.Event()
        host = asyncio.create_task(launcher.run_host(
            [
                executable, "--headless", "--no-auto-update", "--no-auto-login",
                "--disable-builtin-mcps", "--no-custom-instructions", "--no-color",
                "--no-ask-user", "--log-dir", logs,
            ],
            backend="sdk", cwd=workspace, state_file=state_file, ready=ready,
            local_birth_evidence=True,
        ))
        front = None
        try:
            waiting = asyncio.create_task(ready.wait())
            done, _ = await asyncio.wait(
                (waiting, host), timeout=20, return_when=asyncio.FIRST_COMPLETED,
            )
            if host in done:
                await host
                raise AssertionError("Native SDK Host exited before readiness")
            assert waiting in done, "Native SDK Host readiness timed out"
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            facts.append(state)
            runtime_statuses.append((await clients[-1].get_status()).to_dict())
            (output / f"native-{incarnation}.json").write_text(json.dumps({
                "pid": state["child_pid"], "helperPid": os.getpid(),
            }), encoding="utf-8")
            async with asyncio.timeout(10):
                while not (output / f"pinned-{incarnation}").exists():
                    await asyncio.sleep(.025)
            front = await SessionHostClient.connect(port=state["port"])
            await front.attach(nonce=state["nonce"].encode())
            cursor = 0

            async def write(identifier, method, params):
                await front.write((json.dumps({
                    "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
                }) + "\n").encode())

            async def receive():
                nonlocal cursor
                cursor, frame = await asyncio.wait_for(anext(front.frames()), 30)
                await front.ack(cursor)
                message = json.loads(frame)
                if message.get("method") == "session/request_permission":
                    raw = message["params"]["toolCall"]["rawInput"]
                    assert raw["kind"] == "shell", raw
                    assert raw["fullCommandText"] == os.environ["DRIVER_FIXTURE_SHELL_COMMAND"], raw
                    permissions.append(raw["kind"])
                    await front.write((json.dumps({
                        "jsonrpc": "2.0", "id": message["id"], "result": {
                            "outcome": {"outcome": "selected", "optionId": "allow-once"},
                        },
                    }) + "\n").encode())
                return message

            async def rpc(identifier, method, params):
                await write(identifier, method, params)
                updates = []
                while True:
                    message = await receive()
                    if message.get("id") == identifier and "method" not in message:
                        assert "error" not in message, message
                        return message["result"], updates
                    updates.append(message)

            async def inventory(identifier):
                for attempt in range(10):
                    state, _ = await rpc(
                        identifier + attempt, "copilot-driver/activity", {"sessionId": native_id},
                    )
                    if state["tasks"]["known"] and state["schedules"]["known"]:
                        return state
                    await asyncio.sleep(.025)
                raise AssertionError(f"Native inventory did not stabilize: {state}")

            initialized, _ = await rpc(1, "initialize", {"protocolVersion": 1})
            assert initialized["_meta"]["automaticIdleRetirement"] == "unavailable"
            restored, history = await rpc(
                2, "session/new" if incarnation == 0 else "session/load",
                {"cwd": workspace, "mcpServers": [], **(
                    {"sessionId": native_id} if incarnation else {}
                )},
            )
            if incarnation == 0:
                native_id = restored["sessionId"]
            else:
                text = [
                    item["params"]["update"]["content"]["text"] for item in history
                    if item.get("method") == "session/update"
                    and "content" in item["params"]["update"]
                ]
                assert "SDK_FIRST_LITERAL_INPUT" in text and "FIXTURE" in text, text
            assert sessions[-1].session_id == native_id
            params = {"sessionId": native_id}
            if mode == "cancel":
                await write(3, "session/prompt", {
                    **params, "prompt": [{"type": "text", "text": "SDK_CANCEL_INPUT"}],
                })
                await write(4, "session/cancel", params)
                replies = {}
                while len(replies) < 2:
                    message = await receive()
                    if message.get("id") in {3, 4} and "method" not in message:
                        assert "error" not in message, message
                        replies[message["id"]] = message["result"]
                assert replies[3] == {"stopReason": "cancelled"}, replies
                observations.append({"cancel": replies})
            else:
                prompt = {
                    **params, "prompt": [{"type": "text", "text": (
                        "SDK_FIRST_LITERAL_INPUT" if incarnation == 0 else "SDK_RESTORED_LITERAL_INPUT"
                    )}],
                }
                pending = None
                if mode.endswith("-pending"):
                    await write(3, "session/prompt", prompt)
                    early = []
                    async with asyncio.timeout(20):
                        attempt = 0
                        while pending is None:
                            state, updates = await rpc(1_000 + attempt, "copilot-driver/activity", params)
                            early.extend(updates)
                            if state["tasks"]["known"]:
                                pending = next((
                                    item for item in state["tasks"]["items"]
                                    if item["type"] == mode.split("-")[0] and item["status"] == "running"
                                ), None)
                                if mode == "shell-pending" and not (Path(workspace) / "shell-pid").is_file():
                                    pending = None
                            attempt += 1
                            await asyncio.sleep(.025)
                        (output / "tool-observed").write_text(pending["id"], encoding="utf-8")
                        while not (output / "tool-pinned").exists():
                            await asyncio.sleep(.025)
                        if mode == "shell-pending":
                            (Path(workspace) / "release-shell").write_text("observed", encoding="utf-8")
                    updates = early
                    while True:
                        message = await receive()
                        if message.get("id") == 3 and "method" not in message:
                            assert "error" not in message, message
                            completed = message["result"]
                            break
                        updates.append(message)
                    await sessions[-1].rpc.tasks.wait_for_pending(timeout=10)
                    terminal = (await sessions[-1].rpc.tasks.list()).tasks
                    for task in terminal:
                        if task.id == pending["id"]:
                            if mode == "agent-pending":
                                await sessions[-1].rpc.tasks.cancel(TasksCancelRequest.from_dict({"id": task.id}))
                            await sessions[-1].rpc.tasks.remove(TasksRemoveRequest.from_dict({"id": task.id}))
                else:
                    completed, updates = await rpc(3, "session/prompt", prompt)
                assert completed == {"stopReason": "end_turn"}
                text = "".join(
                    item["params"]["update"]["content"]["text"] for item in updates
                    if item.get("method") == "session/update"
                    and item["params"]["update"].get("sessionUpdate") == "agent_message_chunk"
                )
                assert text == "FIXTURE", text
                activity = await inventory(100)
                assert activity["sessionId"] == native_id
                assert activity["foreground"] == {"known": True, "state": "idle"}, activity
                assert activity["tasks"] == {"known": True, "items": []}, activity
                assert activity["schedules"]["known"], activity
                observations.append({"inventory": activity, "history": len(history)})
                if pending is not None:
                    observations[-1]["pending"] = pending
                if mode == "plain" and incarnation == 0:
                    task = (await sessions[-1].rpc.tasks.register(TasksRegisterRequest.from_dict({
                        "cancellable": False, "clientTaskId": "sdk-adapter-control",
                        "description": "Native adapter inventory control", "type": "client",
                    }))).task
                    active = await inventory(200)
                    assert active["tasks"]["items"][0]["id"] == task.id, active
                    task = (await sessions[-1].rpc.tasks.update(TasksUpdateRequest.from_dict({
                        "id": task.id, "sequence": task.sequence + 1,
                        "update": {"kind": "completed", "result": "SDK_TASK_COMPLETE"},
                    }))).task
                    await sessions[-1].rpc.tasks.remove(TasksRemoveRequest.from_dict({"id": task.id}))
                    observations[-1]["nonempty"] = active
                    await front.close()
                    front = await SessionHostClient.connect(port=state["port"])
                    hello = await front.attach(last_acked=cursor, nonce=state["nonce"].encode())
                    assert hello.child_pid == state["child_pid"]
                    continued, _ = await rpc(6, "session/prompt", {
                        **params, "prompt": [{"type": "text", "text": "SDK_RECONNECTED_LITERAL_INPUT"}],
                    })
                    assert continued == {"stopReason": "end_turn"}
                if mode == "schedule":
                    entry, = activity["schedules"]["items"]
                    assert entry["prompt"] == "ISOLATED_FUTURE_SCHEDULE_FIXTURE"
                    from copilot.generated.rpc import ScheduleStopRequest
                    await sessions[-1].rpc.schedule.stop(ScheduleStopRequest.from_dict({"id": entry["id"]}))
            await front.terminate()
            await asyncio.wait_for(host, 15)
        finally:
            if front is not None:
                await front.close()
            if not host.done():
                host.cancel()
            await asyncio.gather(host, return_exceptions=True)
    assert len(clients) == len(sessions) == len(facts)
    (output / "result.json").write_text(json.dumps({
        "facts": facts, "sessionId": native_id, "permissions": permissions,
        "runtimeStatuses": runtime_statuses, "sdkVersion": version("github-copilot-sdk"),
        "observations": observations,
    }), encoding="utf-8")


asyncio.run(main())
