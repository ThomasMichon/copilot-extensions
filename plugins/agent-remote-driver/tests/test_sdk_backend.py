"""Public-SDK adaptation through the real Host; no native runtime is downloaded."""

import asyncio
from contextlib import asynccontextmanager
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.host import SessionHost
from agent_remote_driver.session_host import launcher, sdk_backend


@pytest.fixture(autouse=True)
def sdk_source_type(monkeypatch):
    monkeypatch.setitem(sys.modules, "copilot.session", SimpleNamespace(
        AgentMessageSource=lambda **values: SimpleNamespace(**values),
    ))


class Event:
    def __init__(self, kind, **data):
        self.value = {"id": str(uuid4()), "type": kind, "data": data}

    def to_dict(self):
        return self.value


class Snapshot:
    def __init__(self, **value):
        self.value = value

    def to_dict(self):
        return self.value


class Owner:
    pid = None
    returncode = None

    def __init__(self):
        self.done = asyncio.Event()

    async def wait(self):
        await self.done.wait()
        return self.returncode

    def kill(self):
        self.returncode = 0
        self.done.set()


class Session:
    session_id = "native-fixture"

    def __init__(self):
        self.rpc = SimpleNamespace(
            tasks=SimpleNamespace(list=AsyncMock(return_value=Snapshot(tasks=[]))),
            schedule=SimpleNamespace(list=AsyncMock(return_value=Snapshot(entries=[]))),
        )
        self.disconnect = AsyncMock()
        self.get_events = AsyncMock(return_value=[
            Event("user.message", content="prior literal input"),
            Event("assistant.message", content="prior native reply", messageId="old"),
        ])
        self.release = asyncio.Event()
        self.inputs = []
        self.abort_calls = 0

    def emit(self, kind, **data):
        self.options["on_event"](Event(kind, **data))

    async def send_and_wait(self, text, **kwargs):
        self.inputs.append(text)
        self.emit("user.message", content=text, source="agent-" + kwargs["source"].agent_id)
        self.emit("assistant.message_delta", deltaContent="native reply", messageId="one")
        await self.release.wait()
        self.emit("assistant.message", content="native reply", messageId="one")
        self.emit("session.idle", aborted=self.abort_calls > 0)
        if self.abort_calls:
            raise RuntimeError("Session aborted before response completed")

    async def abort(self):
        self.abort_calls += 1
        self.emit("abort")
        self.release.set()


class Client:
    def __init__(self):
        self.session = Session()
        self.created = []
        self.resumed = []
        self.stop = AsyncMock()
        self.force_stop = AsyncMock()

    async def create_session(self, **options):
        self.created.append(options)
        self.session.options = options
        return self.session

    async def resume_session(self, session_id, **options):
        self.resumed.append(session_id)
        self.session.options = options
        return self.session


@asynccontextmanager
async def broker():
    client, owner = Client(), Owner()
    backend = sdk_backend.SdkBackend(owner, client, cwd="fixture")
    host = SessionHost(backend, nonce="private-fixture", lifetime_policy="acp")
    port = await host.serve()
    front = await SessionHostClient.connect(port=port)
    await front.attach(nonce=b"private-fixture")
    try:
        yield client, backend, host, front, port
    finally:
        await front.close()
        await host.close()
        owner.kill()
        await backend.close()


async def send(front, identifier, method, params):
    await front.write((json.dumps({
        "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
    }) + "\n").encode())


async def until(front, identifier):
    updates = []
    while True:
        seq, frame = await asyncio.wait_for(anext(front.frames()), 2)
        message = json.loads(frame)
        await front.ack(seq)
        if message.get("id") == identifier and "method" not in message:
            return message, updates, seq
        updates.append(message)


async def bind(front):
    await send(front, 1, "session/new", {"cwd": "fixture", "mcpServers": []})
    result, _, _ = await until(front, 1)
    assert result["result"]["sessionId"] == "native-fixture"


@pytest.mark.asyncio
async def test_sdk_prompt_survives_front_loss_with_one_native_binding_and_no_duplicate_output():
    async with broker() as (client, backend, host, front, port):
        await bind(front)
        text = 'literal\n"quotes" & $value \u03a9'
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": text}],
        })
        seq, frame = await asyncio.wait_for(anext(front.frames()), 2)
        assert json.loads(frame)["params"]["update"]["content"]["text"] == "native reply"
        await front.ack(seq)
        await front.close()
        client.session.release.set()
        replacement = await SessionHostClient.connect(port=port)
        try:
            await replacement.attach(nonce=b"private-fixture", last_acked=seq)
            reply, updates, _ = await until(replacement, 2)
            assert reply["result"] == {"stopReason": "end_turn"}
            assert not updates
            assert client.session.inputs == [text] and len(client.created) == 1
            assert host.child_alive and backend.activity()["automaticIdleRetirement"] == "unavailable"
        finally:
            await replacement.close()


@pytest.mark.asyncio
async def test_sdk_cancel_waits_for_admission_and_reports_observed_native_abort():
    async with broker() as (client, _backend, _host, front, _port):
        await bind(front)
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "cancel me"}],
        })
        await send(front, 3, "session/cancel", {"sessionId": "native-fixture"})
        replies = {}
        while len(replies) < 2:
            seq, frame = await asyncio.wait_for(anext(front.frames()), 2)
            message = json.loads(frame)
            await front.ack(seq)
            if "result" in message:
                replies[message["id"]] = message["result"]
        assert replies == {2: {"stopReason": "cancelled"}, 3: {}}
        assert client.session.abort_calls == 1


@pytest.mark.asyncio
async def test_sdk_cancel_refuses_control_lost_in_the_admission_batch(monkeypatch):
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        entered, admit, cancelling = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def held_send(text, **kwargs):
            client.session.inputs.append(text)
            entered.set()
            await admit.wait()
            client.session.emit("user.message", content=text, source="agent-" + kwargs["source"].agent_id)
            client.session.emit("user.message", content="foreign input", source="user")
            await client.session.release.wait()

        started_wait = backend._started.wait

        async def wait_started():
            cancelling.set()
            await started_wait()

        monkeypatch.setattr(backend._started, "wait", wait_started)
        client.session.send_and_wait = held_send
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "owned input"}],
        })
        await asyncio.wait_for(entered.wait(), 2)
        await send(front, 3, "session/cancel", {"sessionId": "native-fixture"})
        await asyncio.wait_for(cancelling.wait(), 2)
        admit.set()
        reply, _, _ = await until(front, 3)
        assert reply["error"]["code"] == -32001
        assert client.session.abort_calls == 0 and not backend._controlled
        assert backend.returncode is None and client.session.inputs == ["owned input"]
        client.session.release.set()
        assert (await until(front, 2))[0]["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_sdk_waiting_cancel_does_not_abort_a_replacement_prompt(monkeypatch):
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        entered, finish = asyncio.Event(), asyncio.Event()
        cancelling, cleanup, continue_cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_send = client.session.send_and_wait

        async def first_send(text, **_kwargs):
            client.session.inputs.append(text)
            entered.set()
            await finish.wait()

        async def wait_started():
            cancelling.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await continue_cancel.wait()

        monkeypatch.setattr(backend._started, "wait", wait_started)
        client.session.send_and_wait = first_send
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "first input"}],
        })
        await asyncio.wait_for(entered.wait(), 2)
        await send(front, 3, "session/cancel", {"sessionId": "native-fixture"})
        await asyncio.wait_for(cancelling.wait(), 2)
        finish.set()
        assert (await until(front, 2))[0]["result"]["stopReason"] == "end_turn"
        await asyncio.wait_for(cleanup.wait(), 2)
        client.session.send_and_wait = original_send
        await send(front, 4, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "replacement input"}],
        })
        _, frame = await asyncio.wait_for(anext(front.frames()), 2)
        assert json.loads(frame)["method"] == "session/update"
        continue_cancel.set()
        assert (await until(front, 3))[0]["result"] == {}
        assert client.session.abort_calls == 0
        assert client.session.inputs == ["first input", "replacement input"]
        client.session.release.set()
        assert (await until(front, 4))[0]["result"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_sdk_identical_foreign_input_is_not_mistaken_for_the_admitted_message():
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "same text"}],
        })
        await asyncio.wait_for(backend._started.wait(), 2)
        client.session.emit("user.message", content="same text", source="user")
        client.session.release.set()
        reply, _, _ = await until(front, 2)
        assert reply["error"]["code"] == -32001
        assert backend.returncode is None and client.session.inputs == ["same text"]


@pytest.mark.asyncio
async def test_sdk_queued_prompt_cannot_overtake_observed_foreign_input():
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        client.session.release.set()
        backend.write((json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/prompt", "params": {
                "sessionId": "native-fixture",
                "prompt": [{"type": "text", "text": "must not send"}],
            },
        }) + "\n").encode())
        client.session.emit("user.message", content="foreign input", source="user")
        reply, _, _ = await until(front, 2)
        assert reply["error"]["code"] == -32001
        assert client.session.inputs == [] and backend.returncode is None


@pytest.mark.asyncio
async def test_sdk_replayed_live_admission_does_not_revoke_a_later_prompt():
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        owned = []

        async def send_once(text, **kwargs):
            client.session.inputs.append(text)
            event = Event("user.message", content=text, source="agent-" + kwargs["source"].agent_id)
            owned.append(event)
            client.session.options["on_event"](event)
            client.session.emit("session.idle")

        client.session.send_and_wait = send_once
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "first input"}],
        })
        assert (await until(front, 2))[0]["result"]["stopReason"] == "end_turn"
        client.session.options["on_event"](owned[0])
        await send(front, 3, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "second input"}],
        })
        assert (await until(front, 3))[0]["result"]["stopReason"] == "end_turn"
        assert client.session.inputs == ["first input", "second input"] and backend._controlled


@pytest.mark.asyncio
async def test_sdk_load_replays_native_history_and_refuses_a_second_conversation():
    async with broker() as (client, _backend, _host, front, _port):
        await send(front, 1, "session/load", {
            "sessionId": "native-fixture", "cwd": "fixture", "mcpServers": [],
        })
        result, updates, _ = await until(front, 1)
        assert result["result"] == {}
        assert [item["params"]["update"]["content"]["text"] for item in updates] == [
            "prior literal input", "prior native reply",
        ]
        await send(front, 2, "session/new", {"cwd": "fixture"})
        refused, _, _ = await until(front, 2)
        assert refused["error"]["code"] == -32001
        assert client.resumed == ["native-fixture"] and client.created == []


@pytest.mark.asyncio
async def test_sdk_unknown_acceptance_never_rebinds_or_replays():
    async with broker() as (client, _backend, _host, front, _port):
        async def unknown(**options):
            client.created.append(options)
            raise ConnectionError("response lost after possible admission")
        client.create_session = unknown
        await send(front, 1, "session/new", {"cwd": "fixture"})
        assert (await until(front, 1))[0]["error"]["code"] == -32001
        await send(front, 2, "session/new", {"cwd": "fixture"})
        assert (await until(front, 2))[0]["error"]["code"] == -32001
        assert len(client.created) == 1 and client.resumed == []


@pytest.mark.asyncio
async def test_sdk_unrepresentable_restoration_is_not_reported_as_success():
    async with broker() as (client, _backend, _host, front, _port):
        client.session.get_events.return_value = [
            Event("user.message", content="image input", attachments=[{"type": "blob"}]),
        ]
        await send(front, 1, "session/load", {"sessionId": "native-fixture", "cwd": "fixture"})
        reply, updates, _ = await until(front, 1)
        assert reply["error"]["code"] == -32001 and not updates
        await send(front, 2, "session/new", {"cwd": "fixture"})
        assert (await until(front, 2))[0]["error"]["code"] == -32001
        assert client.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize("attachments", [[], [{"type": "blob"}]])
async def test_sdk_load_rejects_concurrent_prompt_until_history_is_validated(attachments):
    async with broker() as (client, backend, _host, front, _port):
        reading, release = asyncio.Event(), asyncio.Event()

        async def history():
            reading.set()
            await release.wait()
            return [Event("user.message", content="prior input", attachments=attachments)]

        client.session.get_events.side_effect = history
        await send(front, 1, "session/load", {"sessionId": "native-fixture", "cwd": "fixture"})
        await asyncio.wait_for(reading.wait(), 2)
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "must not send"}],
        })
        reply, updates, _ = await until(front, 2)
        assert reply["error"]["code"] == -32602 and not updates
        assert not backend._bound.is_set() and client.session.inputs == []
        release.set()
        loaded, _, _ = await until(front, 1)
        if attachments:
            assert loaded["error"]["code"] == -32001
            assert not backend._bound.is_set()
        else:
            assert loaded["result"] == {} and backend._bound.is_set()
        assert client.session.inputs == [] and client.resumed == ["native-fixture"]


@pytest.mark.asyncio
async def test_sdk_history_overlap_cannot_deduplicate_live_control_loss():
    async with broker() as (client, backend, _host, front, _port):
        reading, release = asyncio.Event(), asyncio.Event()
        foreign = Event("user.message", content="overlapping input", source="user")

        async def history():
            reading.set()
            await release.wait()
            return [foreign]

        client.session.get_events.side_effect = history
        await send(front, 1, "session/load", {"sessionId": "native-fixture", "cwd": "fixture"})
        await asyncio.wait_for(reading.wait(), 2)
        client.session.options["on_event"](foreign)
        release.set()
        assert (await until(front, 1))[0]["error"]["code"] == -32001
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "must not send"}],
        })
        assert (await until(front, 2))[0]["error"]["code"] == -32001
        assert client.session.inputs == [] and not backend._controlled


@pytest.mark.asyncio
async def test_sdk_event_overflow_preserves_process_but_invalidates_control_and_inventory():
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        for _ in range(sdk_backend.MAX_EVENTS + 1):
            client.session.emit("session.idle")
        await asyncio.wait_for(backend._event_task, 2)
        assert backend.returncode is None and not backend.activity()["tasks"]["known"]
        await send(front, 2, "session/prompt", {
            "sessionId": "native-fixture", "prompt": [{"type": "text", "text": "must not send"}],
        })
        assert (await until(front, 2))[0]["error"]["code"] == -32001
        assert client.session.inputs == []


@pytest.mark.asyncio
async def test_sdk_inventory_keeps_future_schedules_separate_and_invalidates_races():
    async with broker() as (client, backend, _host, front, _port):
        client.session.rpc.schedule.list.return_value = Snapshot(entries=[{"id": 7, "nextRunAt": 99}])
        await bind(front)
        client.session.emit("session.idle")
        await asyncio.wait_for(backend._event_task, 2)
        await asyncio.wait_for(backend._inventory_task, 2)
        state = backend.activity()
        assert state["foreground"] == {"known": True, "state": "idle"}
        assert state["tasks"] == {"known": True, "items": []}
        assert state["schedules"] == {"known": True, "items": [{"id": 7, "nextRunAt": 99}]}
        async def raced(**_kwargs):
            client.session.emit("session.schedule_created")
            return Snapshot(tasks=[])
        client.session.rpc.tasks.list.side_effect = raced
        await backend._refresh_inventory()
        assert not backend.activity()["tasks"]["known"]
        assert not backend.activity()["schedules"]["known"]
        client.session.rpc.tasks.list.side_effect = RuntimeError("native method unavailable")
        await backend._refresh_inventory()
        assert backend.activity()["automaticIdleRetirement"] == "unavailable"
        assert backend.returncode is None


@pytest.mark.asyncio
async def test_sdk_streaming_deltas_do_not_trigger_inventory_queries_per_chunk():
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        tasks, schedules = client.session.rpc.tasks.list, client.session.rpc.schedule.list
        tasks.reset_mock()
        schedules.reset_mock()
        for _ in range(20):
            client.session.emit("assistant.message_delta", deltaContent="chunk", messageId="one")
            await asyncio.wait_for(backend._event_task, 2)
        tasks.assert_not_called()
        schedules.assert_not_called()
        client.session.emit("session.idle")
        await asyncio.wait_for(backend._event_task, 2)
        await asyncio.wait_for(backend._inventory_task, 2)
        tasks.assert_awaited_once()
        schedules.assert_awaited_once()


@pytest.mark.asyncio
async def test_sdk_permission_relays_only_exact_controller_selection_and_preserves_managed_policy(monkeypatch):
    class Approve:
        pass
    class Reject:
        pass
    class NoResult:
        pass
    monkeypatch.setitem(sys.modules, "copilot.generated.rpc", SimpleNamespace(
        PermissionDecisionApproveOnce=Approve, PermissionDecisionReject=Reject,
    ))
    monkeypatch.setitem(sys.modules, "copilot.session", SimpleNamespace(PermissionNoResult=NoResult))
    request = SimpleNamespace(kind="shell", to_dict=lambda: {"kind": "shell", "fullCommandText": "literal"})
    async with broker() as (client, backend, _host, front, _port):
        await bind(front)
        permission = asyncio.create_task(backend._permission(request, {"session_id": "native-fixture"}))
        seq, frame = await asyncio.wait_for(anext(front.frames()), 2)
        message = json.loads(frame)
        assert message["method"] == "session/request_permission"
        assert message["params"]["toolCall"]["rawInput"]["fullCommandText"] == "literal"
        await front.ack(seq)
        await front.write((json.dumps({
            "jsonrpc": "2.0", "id": message["id"], "result": {
                "outcome": {"outcome": "selected", "optionId": "allow-once"},
            },
        }) + "\n").encode())
        assert isinstance(await asyncio.wait_for(permission, 2), Approve)
        for invocation in (
            {"session_id": "different"}, {"session_id": "native-fixture", "managed_settings_enabled": True},
        ):
            assert isinstance(await backend._permission(request, invocation), NoResult)
        assert client.session.inputs == []


@pytest.mark.parametrize("argv", [
    ["native", "--headless", "--prompt=seed"],
    ["native", "--headless", "--session-idle-timeout", "10"],
    ["native", "--log-dir", "--headless"],
    ["native", "--acp", "--stdio"],
    ["native", "--headless", "--", "seed"],
])
def test_sdk_refuses_native_seed_and_automatic_retirement_before_spawn(argv):
    with pytest.raises(ValueError):
        sdk_backend.validate_child_argv(argv)


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["missing-sdk", "old-python"])
async def test_sdk_missing_optional_artifact_refuses_before_native_creation(monkeypatch, unavailable):
    spawned = AsyncMock()
    monkeypatch.setattr(launcher, "_spawn_child", spawned)
    monkeypatch.setattr(launcher, "_require_receiving_policy", lambda *_args: None)
    if unavailable == "missing-sdk":
        monkeypatch.setitem(sys.modules, "copilot", None)
    else:
        monkeypatch.setattr(sdk_backend.sys, "version_info", (3, 10))
    with pytest.raises(RuntimeError, match="optional SDK"):
        await launcher.run_host(["native", "--headless"], backend="sdk")
    spawned.assert_not_called()
