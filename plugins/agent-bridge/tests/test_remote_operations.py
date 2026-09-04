"""Tests for narrow remote Bridge operations over the shared SSH carrier."""

from __future__ import annotations

import argparse
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from ssh_manager import (
    CarrierBackpressure,
    CarrierRemoteError,
    CarrierUnavailable,
    Envelope,
    EnvelopeType,
)

from agent_bridge.app import create_app
from agent_bridge import __main__ as main_module
from agent_bridge.client import BridgeClientError
from agent_bridge.models import ServiceConfig
from agent_bridge.remote_operations import (
    CarrierRequestRouter,
    RemoteBridgeError,
    RemoteOperationService,
    validate_caller_id,
    validate_continuity_id,
)


class _FakeStream:
    def __init__(self, events):
        self._events = iter(events)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._events)

    def close(self):
        self.closed = True


class _RemoteClient:
    def __init__(self) -> None:
        self.status_calls = []
        self.resolve_calls = []
        self.create_calls = []
        self.stop_calls = []
        self.end_calls = []
        self.message_calls = []
        self.ack_calls = []
        self.streams = []
        self.cursor_info = {
            "last_acked_id": 4,
            "head_id": 5,
            "continuity_id": "epoch-a",
            "cursor_registered": True,
            "invalidation": None,
        }

    def get_session_status(self, session_id, *, caller_id):
        self.status_calls.append((session_id, caller_id))
        return {"session_id": session_id, "status": "idle", "head_id": 5}

    def daemon_supports(self, _version):
        return True

    def daemon_protocol(self):
        return (10, 1)

    def resolve_live_session(self, target):
        self.resolve_calls.append(target)
        return {"session_id": "live-a", "worktree_id": target, "status": "live"}

    def start_session(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"session_id": "created-a"}

    def submit_prompt(self, session_id, prompt, **kwargs):
        self.create_calls.append((session_id, prompt, kwargs))
        return {"accepted": True}

    def stop_session(self, session_id, **kwargs):
        self.stop_calls.append((session_id, kwargs))

    def end_session(self, session_id, **kwargs):
        self.end_calls.append((session_id, kwargs))

    def send_live_message(self, session_id, **kwargs):
        self.message_calls.append((session_id, kwargs))
        return {"message_id": "message-a"}

    def get_cursor_info(self, session_id, *, caller_id):
        return dict(self.cursor_info)

    def ack_cursor(
        self, session_id, last_id, *, caller_id, continuity_id=None
    ):
        self.ack_calls.append(
            (session_id, last_id, caller_id, continuity_id)
        )
        return last_id

    def stream_events(
        self,
        session_id,
        *,
        caller_id,
        controlled,
        continuity_id,
        after=None,
        transient=False,
    ):
        stream = _FakeStream(
            [
                {
                    "id": "5",
                    "event": "assistant.turn_end",
                    "data": {"stop_reason": "end_turn"},
                    "timestamp": 123.0,
                    "continuity_id": "epoch-a",
                }
            ]
        )
        self.streams.append(stream)
        return stream

    def refresh_endpoint(self):
        return False


def test_caller_id_is_required_distinct_from_legacy_default() -> None:
    assert validate_caller_id("supervisor.lane-a") == "supervisor.lane-a"
    with pytest.raises(RemoteBridgeError, match="reserved"):
        validate_caller_id("__default__")
    with pytest.raises(RemoteBridgeError, match="safe ASCII"):
        validate_caller_id("consumer with spaces")


def test_continuity_id_is_normalized_and_safe() -> None:
    assert validate_continuity_id(" epoch-a ") == "epoch-a"
    with pytest.raises(RemoteBridgeError, match="safe ASCII"):
        validate_continuity_id(" ")
    with pytest.raises(RemoteBridgeError, match="safe ASCII"):
        validate_continuity_id("epoch a")


@pytest.mark.asyncio
async def test_unsupported_version_fails_before_client_resolution() -> None:
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return _RemoteClient()

    response = await CarrierRequestRouter(factory)(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="request-1",
            payload={
                "operation": "session.status",
                "version": 99,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )

    assert isinstance(response, Envelope)
    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "unsupported_version"
    assert calls == 0


@pytest.mark.asyncio
async def test_status_and_live_resolution_proxy_exact_authority() -> None:
    client = _RemoteClient()
    router = CarrierRequestRouter(lambda: client)

    status = await router(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="status",
            payload={
                "operation": "session.status",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )
    live = await router(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="live",
            payload={
                "operation": "live_session.resolve",
                "version": 1,
                "session_id": "live-a",
            },
        )
    )

    assert status.payload["result"]["session_id"] == "session-a"
    assert live.payload["result"] == {
        "session_id": "live-a",
        "worktree_id": "live-a",
        "status": "live",
    }
    assert client.status_calls == [("session-a", "consumer-a")]
    assert client.resolve_calls == ["live-a"]


@pytest.mark.asyncio
async def test_mutating_operations_proxy_structured_bridge_calls() -> None:
    client = _RemoteClient()
    router = CarrierRequestRouter(lambda: client)

    create = await router(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="create",
            payload={
                "operation": "session.create",
                "version": 2,
                "agent": "task-worker",
                "prompt": "do the work",
                "caller_id": "fleet-task-a",
            },
        )
    )
    stop = await router(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="stop",
            payload={
                "operation": "session.stop",
                "version": 2,
                "session_id": "created-a",
                "reap_host": True,
            },
        )
    )
    end = await router(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="end",
            payload={
                "operation": "session.end",
                "version": 2,
                "session_id": "created-a",
                "if_idle": True,
            },
        )
    )
    sent = await router(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="send",
            payload={
                "operation": "live_session.send",
                "version": 2,
                "target": "worktree-a",
                "sender": "agent-dispatch-steer",
                "message": "resume",
                "kind": "prompt",
                "expected_session_id": "live-a",
                "idempotency_key": "wake-task-a",
            },
        )
    )

    assert create.payload["result"] == {"session_id": "created-a"}
    assert stop.payload["result"] == {"stopped": True}
    assert end.payload["result"] == {"ended": True}
    assert sent.payload["result"] == {"message_id": "message-a"}
    assert client.create_calls == [
        {
            "agent": "task-worker",
            "caller_id": "fleet-task-a",
            "force_new": True,
        },
        ("created-a", "do the work", {"caller_id": "fleet-task-a"}),
    ]
    assert client.stop_calls == [
        ("created-a", {"force": False, "reap_host": True})
    ]
    assert client.end_calls == [
        ("created-a", {"force": False, "if_idle": True})
    ]
    assert client.resolve_calls[-1] == "worktree-a"
    assert client.message_calls == [
        (
            "live-a",
            {
                "sender": "agent-dispatch-steer",
                "body": "resume",
                "kind": "prompt",
                "wait": False,
                "idempotency_key": "wake-task-a",
                "expected_session_id": "live-a",
            },
        )
    ]


@pytest.mark.asyncio
async def test_mutating_operations_require_protocol_version_two() -> None:
    response = await CarrierRequestRouter(lambda: _RemoteClient())(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="create",
            payload={
                "operation": "session.create",
                "version": 1,
                "agent": "task-worker",
                "prompt": "do the work",
                "caller_id": "fleet-task-a",
            },
        )
    )

    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "unsupported_version"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "field", "value"),
    [
        ("session.stop", "force", "false"),
        ("session.stop", "reap_host", 1),
        ("session.end", "if_idle", None),
        ("live_session.send", "expected_session_id", {"session": "live-a"}),
        ("live_session.send", "idempotency_key", "not safe"),
    ],
)
async def test_mutating_operations_reject_malformed_options(
    operation: str, field: str, value: object
) -> None:
    payload = {
        "operation": operation,
        "version": 2,
        "session_id": "created-a",
        field: value,
    }
    if operation == "live_session.send":
        payload.update(
            {
                "target": "worktree-a",
                "sender": "agent-dispatch-steer",
                "message": "resume",
                "kind": "prompt",
            }
        )

    response = await CarrierRequestRouter(lambda: _RemoteClient())(
        Envelope(
            EnvelopeType.REQUEST,
            request_id=f"{operation}-{field}",
            payload=payload,
        )
    )

    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_cancelled_create_reclaims_late_session() -> None:
    started = threading.Event()
    release = threading.Event()

    class _SlowCreateClient(_RemoteClient):
        def start_session(self, **kwargs):
            started.set()
            release.wait(timeout=5)
            return {"session_id": "late-session"}

    client = _SlowCreateClient()
    task = asyncio.create_task(
        CarrierRequestRouter(lambda: client)(
            Envelope(
                EnvelopeType.REQUEST,
                request_id="create",
                payload={
                    "operation": "session.create",
                    "version": 2,
                    "agent": "task-worker",
                    "prompt": "do the work",
                    "caller_id": "fleet-task-a",
                },
            )
        )
    )
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.end_calls == [
        ("late-session", {"force": True})
    ]


@pytest.mark.asyncio
async def test_cancelled_create_preserves_cancellation_when_cleanup_fails() -> None:
    started = threading.Event()
    release = threading.Event()

    class _CleanupFailureClient(_RemoteClient):
        def start_session(self, **kwargs):
            started.set()
            release.wait(timeout=5)
            return {"session_id": "late-session"}

        def end_session(self, session_id, **kwargs):
            raise TimeoutError("cleanup timed out")

    task = asyncio.create_task(
        CarrierRequestRouter(lambda: _CleanupFailureClient())(
            Envelope(
                EnvelopeType.REQUEST,
                request_id="create",
                payload={
                    "operation": "session.create",
                    "version": 2,
                    "agent": "task-worker",
                    "prompt": "do the work",
                    "caller_id": "fleet-task-a",
                },
            )
        )
    )
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_remote_client_validates_live_message_guards() -> None:
    client = RemoteOperationService(SimpleNamespace())

    with pytest.raises(RemoteBridgeError, match="expected_session_id"):
        await client.send_live_message(
            "host-a",
            "worktree-a",
            sender="agent-dispatch-steer",
            message="resume",
            kind="prompt",
            expected_session_id="not safe",
        )


@pytest.mark.asyncio
async def test_event_subscription_preserves_ids_names_and_payloads() -> None:
    client = _RemoteClient()
    result = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            subscription_id="sub-a",
            replayable=True,
            position=4,
            payload={
                "operation": "session.events",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )

    accepted = await anext(result)
    event = await anext(result)
    await result.aclose()

    assert accepted.type is EnvelopeType.RESPONSE
    assert accepted.payload["last_acked_id"] == 4
    assert event.type is EnvelopeType.EVENT
    assert event.position is None
    assert event.payload == {
        "kind": "event",
        "id": 5,
        "event": "assistant.turn_end",
        "data": {"stop_reason": "end_turn"},
        "timestamp": 123.0,
        "continuity_id": "epoch-a",
    }
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_event_subscription_rejects_legacy_hosting_daemon() -> None:
    client = _RemoteClient()
    client.daemon_supports = lambda _version: False
    client.daemon_protocol = lambda: (9, 1)

    response = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            subscription_id="sub-a",
            replayable=True,
            position=4,
            payload={
                "operation": "session.events",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )

    assert isinstance(response, Envelope)
    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "unsupported_version"
    assert response.payload["status"] == 426
    assert client.ack_calls == []
    assert client.streams == []


@pytest.mark.asyncio
@pytest.mark.parametrize("continuity_id", ["", " ", "epoch a", "x" * 129])
async def test_event_subscription_rejects_invalid_continuity(
    continuity_id,
) -> None:
    client = _RemoteClient()

    response = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            subscription_id="sub-a",
            payload={
                "operation": "session.events",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
                "continuity_id": continuity_id,
            },
        )
    )

    assert isinstance(response, Envelope)
    assert response.type is EnvelopeType.ERROR
    assert response.payload["status"] == 400
    assert response.payload["code"] == "invalid_request"
    assert client.streams == []


@pytest.mark.asyncio
async def test_event_ack_requires_continuity_for_non_empty_log() -> None:
    client = _RemoteClient()

    response = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            request_id="ack",
            payload={
                "operation": "session.events.ack",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
                "last_id": 5,
            },
        )
    )

    assert isinstance(response, Envelope)
    assert response.type is EnvelopeType.ERROR
    assert response.payload["status"] == 400
    assert response.payload["code"] == "invalid_request"
    assert response.payload["details"]["continuity_id"] == "epoch-a"
    assert client.ack_calls == []


@pytest.mark.asyncio
async def test_event_subscription_survives_hosting_bridge_cutover(
    monkeypatch,
) -> None:
    class _CutoverClient(_RemoteClient):
        def __init__(self) -> None:
            super().__init__()
            self.stream_calls = 0
            self.refreshes = 0

        def stream_events(self, *args, **kwargs):
            self.stream_calls += 1
            if self.stream_calls == 1:
                return _FakeStream([])
            if self.stream_calls == 2:
                raise BridgeClientError(404, {"detail": "not registered yet"})
            return super().stream_events(*args, **kwargs)

        def refresh_endpoint(self):
            self.refreshes += 1
            return True

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "agent_bridge.remote_operations.asyncio.sleep", no_sleep
    )
    client = _CutoverClient()
    result = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            subscription_id="sub-a",
            replayable=True,
            position=4,
            payload={
                "operation": "session.events",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )

    await anext(result)
    event = await anext(result)
    await result.aclose()

    assert event.type is EnvelopeType.EVENT
    assert event.payload["id"] == 5
    assert client.stream_calls == 3
    assert client.refreshes == 2


@pytest.mark.asyncio
async def test_hosting_stream_reconnect_resumes_after_forwarded_event(
    monkeypatch,
) -> None:
    class _ResumeClient(_RemoteClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = []

        def stream_events(self, *args, **kwargs):
            self.calls.append(kwargs)
            event_id = 5 if len(self.calls) == 1 else 6
            return _FakeStream(
                [
                    {
                        "id": str(event_id),
                        "event": "assistant.turn_end",
                        "data": {"sequence": event_id},
                        "timestamp": 123.0,
                        "continuity_id": "epoch-a",
                    }
                ]
            )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "agent_bridge.remote_operations.asyncio.sleep", no_sleep
    )
    client = _ResumeClient()
    result = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            subscription_id="sub-a",
            replayable=True,
            position=4,
            payload={
                "operation": "session.events",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )

    await anext(result)
    first = await anext(result)
    second = await anext(result)
    await result.aclose()

    assert [first.payload["id"], second.payload["id"]] == [5, 6]
    assert client.calls[0]["after"] == 4
    assert client.calls[0]["transient"] is False
    assert client.calls[1]["after"] == 5
    assert client.calls[1]["transient"] is True


@pytest.mark.asyncio
async def test_clean_cutover_rechecks_hosting_protocol(monkeypatch) -> None:
    class _RollbackClient(_RemoteClient):
        def __init__(self) -> None:
            super().__init__()
            self.current_version = 11
            self.refreshes = 0

        def daemon_supports(self, version):
            return self.current_version >= version

        def daemon_protocol(self):
            return (self.current_version, 1)

        def stream_events(self, *args, **kwargs):
            return _FakeStream([])

        def refresh_endpoint(self):
            self.refreshes += 1
            self.current_version = 10
            return False

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "agent_bridge.remote_operations.asyncio.sleep", no_sleep
    )
    client = _RollbackClient()
    result = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            subscription_id="sub-a",
            replayable=True,
            position=4,
            payload={
                "operation": "session.events",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )

    await anext(result)
    error = await anext(result)
    await result.aclose()

    assert error.type is EnvelopeType.ERROR
    assert error.payload["code"] == "unsupported_version"
    assert client.refreshes == 1


@pytest.mark.asyncio
async def test_cursor_invalidation_is_explicit_before_stream_open() -> None:
    client = _RemoteClient()
    client.cursor_info["invalidation"] = {
        "prior_last_acked_id": 8,
        "prior_head_id": 9,
        "prior_continuity_id": "old",
        "current_continuity_id": "new",
    }

    response = await CarrierRequestRouter(lambda: client)(
        Envelope(
            EnvelopeType.REQUEST,
            subscription_id="sub-a",
            payload={
                "operation": "session.events",
                "version": 1,
                "session_id": "session-a",
                "caller_id": "consumer-a",
            },
        )
    )

    assert isinstance(response, Envelope)
    assert response.type is EnvelopeType.ERROR
    assert response.payload["code"] == "cursor_invalidated"
    assert response.payload["details"]["action"] == "full_reconcile"
    assert response.payload["details"]["head_id"] == 5
    assert response.payload["details"]["continuity_id"] == "epoch-a"
    assert response.payload["details"]["current_continuity_id"] == "new"
    assert client.streams == []


@pytest.mark.asyncio
async def test_local_subscription_uses_initial_position_only(
    monkeypatch,
) -> None:
    class _Subscription:
        def __init__(self) -> None:
            self.position = 4

        async def get(self):
            return Envelope(
                EnvelopeType.RESPONSE,
                payload={
                    "last_acked_id": 4,
                    "continuity_id": "epoch-a",
                },
            )

        async def close(self):
            pass

    class _Carrier:
        def __init__(self) -> None:
            self.calls = []
            self.subscription = _Subscription()

        async def subscribe(self, payload, **kwargs):
            self.calls.append((payload, kwargs))
            return self.subscription

    class _Lease:
        def __init__(self) -> None:
            self.carrier = _Carrier()
            self.released = False

        async def release(self):
            self.released = True

    class _Resolver:
        def resolve_ssh_environment(self, host):
            return object(), SimpleNamespace(
                alias="example-host",
                user=None,
                port=22,
                shell="bash",
                name="linux",
            )

    lease = _Lease()

    async def acquire(*args, **kwargs):
        return lease

    monkeypatch.setattr(
        "agent_bridge.carrier.acquire_remote_carrier", acquire
    )
    service = RemoteOperationService(_Resolver())

    subscription = await service.subscribe_events(
        "example-host",
        "session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
    )

    payload, kwargs = lease.carrier.calls[0]
    assert "after" not in payload
    assert kwargs["position"] == 4
    assert lease.carrier.subscription.position is None
    await subscription.close()
    assert lease.released is True


@pytest.mark.asyncio
async def test_service_preserves_v1_for_reads_and_uses_v2_for_mutations(
    monkeypatch,
) -> None:
    class _Carrier:
        def __init__(self) -> None:
            self.calls = []

        async def request(self, payload, **_kwargs):
            self.calls.append(payload)
            operation = payload["operation"]
            if operation == "session.status":
                result = {"status": "idle"}
            else:
                result = {"ended": True}
            return Envelope(
                EnvelopeType.RESPONSE,
                payload={"result": result},
            )

    class _Lease:
        def __init__(self) -> None:
            self.carrier = _Carrier()

        async def release(self):
            pass

    class _Resolver:
        def resolve_ssh_environment(self, _host):
            return object(), SimpleNamespace(
                alias="example-host",
                user=None,
                port=22,
                shell="bash",
                name="linux",
            )

    lease = _Lease()

    async def acquire(*_args, **_kwargs):
        return lease

    monkeypatch.setattr(
        "agent_bridge.carrier.acquire_remote_carrier", acquire
    )
    service = RemoteOperationService(_Resolver())

    await service.session_status(
        "example-host", "session-a", "consumer-a"
    )
    await service.end_session("example-host", "session-a")

    assert [call["version"] for call in lease.carrier.calls] == [1, 2]


@pytest.mark.asyncio
async def test_cancelled_subscription_setup_releases_lease(monkeypatch) -> None:
    started = asyncio.Event()

    class _Carrier:
        async def subscribe(self, *_args, **_kwargs):
            started.set()
            await asyncio.Future()

    class _Lease:
        def __init__(self) -> None:
            self.carrier = _Carrier()
            self.released = False

        async def release(self):
            self.released = True

    class _Resolver:
        def resolve_ssh_environment(self, _host):
            return object(), SimpleNamespace(
                alias="example-host",
                user=None,
                port=22,
                shell="bash",
                name="linux",
            )

    lease = _Lease()

    async def acquire(*_args, **_kwargs):
        return lease

    monkeypatch.setattr(
        "agent_bridge.carrier.acquire_remote_carrier", acquire
    )
    service = RemoteOperationService(_Resolver())
    task = asyncio.create_task(
        service.subscribe_events(
            "example-host",
            "session-a",
            caller_id="consumer-a",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lease.released is True


@pytest.mark.asyncio
async def test_failed_carrier_restart_returns_structured_503(
    monkeypatch,
) -> None:
    class _Carrier:
        async def request(self, *_args, **_kwargs):
            raise CarrierUnavailable("transport lost", reconnectable=True)

        async def ensure_started(self):
            raise CarrierUnavailable("restart failed", reconnectable=True)

    class _Lease:
        def __init__(self) -> None:
            self.carrier = _Carrier()
            self.released = False

        async def release(self):
            self.released = True

    class _Resolver:
        def resolve_ssh_environment(self, _host):
            return object(), SimpleNamespace(
                alias="example-host",
                user=None,
                port=22,
                shell="bash",
                name="linux",
            )

    lease = _Lease()

    async def acquire(*_args, **_kwargs):
        return lease

    monkeypatch.setattr(
        "agent_bridge.carrier.acquire_remote_carrier", acquire
    )
    service = RemoteOperationService(_Resolver(), request_timeout=0.01)

    with pytest.raises(RemoteBridgeError) as exc:
        await service.session_status(
            "example-host", "session-a", caller_id="consumer-a"
        )

    assert exc.value.status == 503
    assert exc.value.code == "carrier_unavailable"
    assert exc.value.reconnectable is True
    assert lease.released is True


@pytest.mark.asyncio
async def test_stalled_carrier_restart_returns_structured_504(
    monkeypatch,
) -> None:
    class _Carrier:
        async def request(self, *_args, **_kwargs):
            raise CarrierUnavailable("transport lost", reconnectable=True)

        async def ensure_started(self):
            await asyncio.Future()

    class _Lease:
        def __init__(self) -> None:
            self.carrier = _Carrier()
            self.released = False

        async def release(self):
            self.released = True

    class _Resolver:
        def resolve_ssh_environment(self, _host):
            return object(), SimpleNamespace(
                alias="example-host",
                user=None,
                port=22,
                shell="bash",
                name="linux",
            )

    lease = _Lease()

    async def acquire(*_args, **_kwargs):
        return lease

    monkeypatch.setattr(
        "agent_bridge.carrier.acquire_remote_carrier", acquire
    )
    service = RemoteOperationService(_Resolver(), request_timeout=0.01)

    with pytest.raises(RemoteBridgeError) as exc:
        await service.session_status(
            "example-host", "session-a", caller_id="consumer-a"
        )

    assert exc.value.status == 504
    assert exc.value.code == "remote_operation_timeout"
    assert exc.value.reconnectable is True
    assert lease.released is True


@pytest.mark.asyncio
async def test_multi_environment_machine_key_requires_exact_alias() -> None:
    windows = SimpleNamespace(
        alias="host-win",
        user=None,
        port=22,
        shell="powershell",
        name="windows",
    )
    wsl = SimpleNamespace(
        alias="host-wsl",
        user=None,
        port=22,
        shell="bash",
        name="wsl",
    )
    machine = SimpleNamespace(
        key="host",
        ssh_environments=[windows, wsl],
    )

    class _Resolver:
        def resolve_ssh_environment(self, _host):
            return machine, wsl

    service = RemoteOperationService(_Resolver())

    with pytest.raises(RemoteBridgeError) as exc:
        await service.session_status(
            "host", "session-a", caller_id="consumer-a"
        )

    assert exc.value.status == 400
    assert exc.value.code == "ambiguous_host"
    assert exc.value.details["ssh_aliases"] == ["host-win", "host-wsl"]


@pytest.mark.asyncio
async def test_machine_key_that_is_exact_alias_selects_that_environment(
    monkeypatch,
) -> None:
    windows = SimpleNamespace(
        alias="host",
        user=None,
        port=22,
        shell="powershell",
        name="windows",
    )
    wsl = SimpleNamespace(
        alias="host-wsl",
        user=None,
        port=22,
        shell="bash",
        name="wsl",
    )
    machine = SimpleNamespace(
        key="host",
        ssh_environments=[windows, wsl],
    )
    captured = {}

    class _Resolver:
        def resolve_ssh_environment(self, _host):
            return machine, wsl

    async def acquire(alias, remote_platform, **kwargs):
        captured["alias"] = alias
        captured["remote_platform"] = remote_platform
        return object()

    monkeypatch.setattr(
        "agent_bridge.carrier.acquire_remote_carrier", acquire
    )

    lease = await RemoteOperationService(_Resolver())._lease("host")

    assert lease is not None
    assert captured == {
        "alias": "carrier:host",
        "remote_platform": "windows",
    }


class _ApiSubscription:
    def __init__(self) -> None:
        self.last_acked_id = 4
        self.continuity_id = "epoch-a"
        self.closed = False
        self._items = iter(
            [
                Envelope(
                    EnvelopeType.EVENT,
                    payload={
                        "kind": "event",
                        "id": 5,
                        "event": "assistant.turn_end",
                        "data": {"stop_reason": "end_turn"},
                        "timestamp": 123.0,
                        "continuity_id": "epoch-a",
                    },
                )
            ]
        )

    async def get(self):
        try:
            return next(self._items)
        except StopIteration as exc:
            raise CarrierRemoteError(
                {
                    "code": "cursor_invalidated",
                    "message": "event log rebuilt",
                    "status": 409,
                    "details": {"action": "full_reconcile"},
                }
            ) from exc

    async def close(self):
        self.closed = True


class _ApiService:
    def __init__(self) -> None:
        self.subscription = _ApiSubscription()
        self.acks = []
        self.commands = []

    async def session_status(self, host, session_id, caller_id):
        return {
            "session_id": session_id,
            "status": "idle",
            "caller_id": caller_id,
        }

    async def resolve_live_session(self, host, session_id):
        return {"session_id": session_id, "status": "live"}

    async def create_session(self, host, **kwargs):
        self.commands.append(("create", host, kwargs))
        return {"session_id": "created-a"}

    async def stop_session(self, host, session_id, **kwargs):
        self.commands.append(("stop", host, session_id, kwargs))

    async def end_session(self, host, session_id, **kwargs):
        self.commands.append(("end", host, session_id, kwargs))

    async def send_live_message(self, host, target, **kwargs):
        self.commands.append(("send", host, target, kwargs))
        return {"message_id": "message-a"}

    async def subscribe_events(self, *args, **kwargs):
        return self.subscription

    async def acknowledge(
        self, host, session_id, *, caller_id, last_id, continuity_id
    ):
        self.acks.append(
            (host, session_id, caller_id, last_id, continuity_id)
        )
        return last_id


@pytest.fixture
def remote_app(tmp_path):
    app = create_app(
        config=ServiceConfig(
            port=0,
            bind="127.0.0.1",
            db_path=str(tmp_path / "remote-api.db"),
        ),
        token="test-token",
    )
    app.state.remote_operations = _ApiService()
    return app


def test_remote_api_is_authenticated_and_streams_control(
    remote_app,
) -> None:
    with TestClient(remote_app) as client:
        missing = client.get(
            "/api/v1/remote/example-host/sessions/session-a/status",
            params={"caller_id": "consumer-a"},
        )
        assert missing.status_code == 401

        headers = {"Authorization": "Bearer test-token"}
        status = client.get(
            "/api/v1/remote/example-host/sessions/session-a/status",
            params={"caller_id": "consumer-a"},
            headers=headers,
        )
        assert status.status_code == 200
        assert status.json()["session_id"] == "session-a"

        with client.stream(
            "GET",
            "/api/v1/remote/example-host/sessions/session-a/events",
            params={"caller_id": "consumer-a", "after": 4},
            headers=headers,
        ) as response:
            text = "".join(response.iter_text())
        assert "id: 5" in text
        assert "event: assistant.turn_end" in text
        assert "event: bridge_control" in text
        assert '"code":"cursor_invalidated"' in text
        assert remote_app.state.remote_operations.subscription.closed is True


def test_remote_api_exposes_mutating_command_contract(remote_app) -> None:
    headers = {"Authorization": "Bearer " + "test-" + "token"}
    with TestClient(remote_app) as client:
        created = client.post(
            "/api/v1/remote/example-host/sessions",
            headers=headers,
            json={
                "agent": "task-worker",
                "prompt": "do the work",
                "caller_id": "fleet-task-a",
            },
        )
        stopped = client.post(
            "/api/v1/remote/example-host/sessions/session-a/stop",
            headers=headers,
            json={"reap_host": True},
        )
        ended = client.post(
            "/api/v1/remote/example-host/sessions/session-a/end",
            headers=headers,
            json={"if_idle": True},
        )
        sent = client.post(
            "/api/v1/remote/example-host/live-sessions/worktree-a/messages",
            headers=headers,
            json={
                "sender": "agent-dispatch-steer",
                "message": "resume",
                "kind": "prompt",
                "expected_session_id": "session-a",
            },
        )

    assert created.json() == {"session_id": "created-a"}
    assert stopped.status_code == 204
    assert ended.status_code == 204
    assert sent.json() == {"message_id": "message-a"}
    assert [command[0] for command in remote_app.state.remote_operations.commands] == [
        "create",
        "stop",
        "end",
        "send",
    ]


def test_remote_api_multiplexes_subscriptions_over_one_stream(
        remote_app,
) -> None:
        first = _ApiSubscription()
        second = _ApiSubscription()
        second._items = iter(
            [
                Envelope(
                    EnvelopeType.EVENT,
                    payload={
                        "kind": "event",
                        "id": 8,
                        "event": "session_state_changed",
                        "data": {"status": "idle"},
                        "timestamp": 124.0,
                        "continuity_id": "epoch-b",
                    },
                )
            ]
        )
        subscriptions = iter([first, second])
        remote_app.state.remote_operations.subscribe_events = AsyncMock(
            side_effect=lambda *args, **kwargs: next(subscriptions)
        )

        with TestClient(remote_app) as client:
            with client.stream(
                "POST",
                "/api/v1/remote/events",
                json={
                    "subscriptions": [
                        {
                            "host": "host-a",
                            "session_id": "session-a",
                            "caller_id": "lane-a",
                        },
                        {
                            "host": "host-a",
                            "session_id": "session-b",
                            "caller_id": "lane-a",
                        },
                    ]
                },
                headers={"Authorization": "Bearer " + "test-token"},
            ) as response:
                text = "".join(response.iter_text())
        assert response.status_code == 200
        assert response.status_code == 200
        assert text.count("event: bridge_event") == 2
        assert '"session_id":"session-a"' in text
        assert '"session_id":"session-b"' in text
        assert '"event":"assistant.turn_end"' in text
        assert '"event":"session_state_changed"' in text
        assert first.closed is True
        assert second.closed is True


def test_remote_api_multiplex_forwards_tool_progress_as_keepalive(
    remote_app,
) -> None:
    subscription = _ApiSubscription()
    subscription._items = iter(
        [
            Envelope(
                EnvelopeType.EVENT,
                payload={
                    "kind": "tool_progress",
                    "data": {"tool": "example"},
                },
            ),
            Envelope(
                EnvelopeType.EVENT,
                payload={
                    "kind": "event",
                    "id": 5,
                    "event": "assistant.turn_end",
                    "data": {"stop_reason": "end_turn"},
                    "timestamp": 123.0,
                    "continuity_id": "epoch-a",
                },
            ),
        ]
    )
    remote_app.state.remote_operations.subscribe_events = AsyncMock(
        return_value=subscription
    )

    with TestClient(remote_app) as client:
        with client.stream(
            "POST",
            "/api/v1/remote/events",
            json={
                "subscriptions": [
                    {
                        "host": "host-a",
                        "session_id": "session-a",
                        "caller_id": "lane-a",
                    }
                ]
            },
            headers={"Authorization": "Bearer " + "test-token"},
        ) as response:
            text = "".join(response.iter_text())

    assert response.status_code == 200
    assert ': tool_progress {"tool":"example"}' in text
    assert text.count("event: bridge_event") == 1
    assert subscription.closed is True


def test_remote_api_multiplex_rejects_duplicate_identity(remote_app) -> None:
        subscription = {
            "host": "host-a",
            "session_id": "session-a",
            "caller_id": "lane-a",
        }
        with TestClient(remote_app) as client:
            response = client.post(
                "/api/v1/remote/events",
                json={"subscriptions": [subscription, subscription]},
                headers={"Authorization": "Bearer " + "test-token"},
            )

        assert response.status_code == 422


def test_remote_api_multiplex_returns_identified_initial_control(
    remote_app,
) -> None:
    remote_app.state.remote_operations.subscribe_events = AsyncMock(
        side_effect=RemoteBridgeError(
            409,
            "cursor_invalidated",
            "cursor replaced",
            details={
                "head_id": 3,
                "current_continuity_id": "epoch-b",
            },
        )
    )
    with TestClient(remote_app) as client:
        with client.stream(
            "POST",
            "/api/v1/remote/events",
            json={
                "subscriptions": [
                    {
                        "host": "host-a",
                        "session_id": "session-a",
                        "caller_id": "lane-a",
                    }
                ]
            },
            headers={"Authorization": "Bearer " + "test-token"},
        ) as response:
            text = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: bridge_control" in text
    assert '"code":"cursor_invalidated"' in text
    assert '"host":"host-a"' in text
    assert '"session_id":"session-a"' in text
    assert '"caller_id":"lane-a"' in text


def test_remote_api_stream_closes_on_daemon_shutdown(remote_app) -> None:
    remote_app.state.uvicorn_server = SimpleNamespace(should_exit=True)

    with TestClient(remote_app) as client:
        with client.stream(
            "GET",
            "/api/v1/remote/example-host/sessions/session-a/events",
            params={"caller_id": "consumer-a", "after": 4},
            headers={"Authorization": "Bearer " + "test-token"},
        ) as response:
            text = "".join(response.iter_text())

    assert text == ""
    assert remote_app.state.remote_operations.subscription.closed is True


def test_remote_api_stream_rejects_empty_continuity(remote_app) -> None:
    with TestClient(remote_app) as client:
        response = client.get(
            "/api/v1/remote/example-host/sessions/session-a/events",
            params={
                "caller_id": "consumer-a",
                "continuity_id": "",
            },
            headers={"Authorization": "Bearer " + "test-token"},
        )

    assert response.status_code == 422


def test_remote_api_returns_retryable_status_while_initializing(
    remote_app,
) -> None:
    with TestClient(remote_app) as client:
        remote_app.state.ready = False
        response = client.get(
            "/api/v1/remote/example-host/sessions/session-a/status",
            params={"caller_id": "consumer-a"},
            headers={"Authorization": "Bearer " + "test-token"},
        )

    assert response.status_code == 503
    assert "initializing" in response.json()["detail"]


def test_remote_api_preserves_retryability_in_http_errors(remote_app) -> None:
    remote_app.state.remote_operations.session_status = AsyncMock(
        side_effect=RemoteBridgeError(
            503,
            "carrier_unavailable",
            "transport lost",
            reconnectable=True,
        )
    )

    with TestClient(remote_app) as client:
        response = client.get(
            "/api/v1/remote/example-host/sessions/session-a/status",
            params={"caller_id": "consumer-a"},
            headers={"Authorization": "Bearer " + "test-token"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["reconnectable"] is True


def test_remote_api_maps_backpressure_to_control(remote_app) -> None:
    class _BackpressureSubscription(_ApiSubscription):
        async def get(self):
            raise CarrierBackpressure("event queue full")

    remote_app.state.remote_operations.subscription = (
        _BackpressureSubscription()
    )

    with TestClient(remote_app) as client:
        with client.stream(
            "GET",
            "/api/v1/remote/example-host/sessions/session-a/events",
            params={"caller_id": "consumer-a", "after": 4},
            headers={"Authorization": "Bearer " + "test-token"},
        ) as response:
            text = "".join(response.iter_text())

    assert "event: bridge_control" in text
    assert '"code":"consumer_backpressure"' in text
    assert remote_app.state.remote_operations.subscription.closed is True


def test_remote_api_preserves_retryability_in_stream_control(
    remote_app,
) -> None:
    class _UnavailableSubscription(_ApiSubscription):
        async def get(self):
            raise CarrierUnavailable("transport lost", reconnectable=True)

    remote_app.state.remote_operations.subscription = (
        _UnavailableSubscription()
    )

    with TestClient(remote_app) as client:
        with client.stream(
            "GET",
            "/api/v1/remote/example-host/sessions/session-a/events",
            params={"caller_id": "consumer-a", "after": 4},
            headers={"Authorization": "Bearer " + "test-token"},
        ) as response:
            text = "".join(response.iter_text())

    assert "event: bridge_control" in text
    assert '"code":"carrier_unavailable"' in text
    assert '"reconnectable":true' in text
    assert remote_app.state.remote_operations.subscription.closed is True


def test_remote_api_maps_subscription_setup_backpressure_to_control(
    remote_app,
) -> None:
    remote_app.state.remote_operations.subscribe_events = AsyncMock(
        side_effect=CarrierBackpressure("event queue full")
    )

    with TestClient(remote_app) as client:
        with client.stream(
            "GET",
            "/api/v1/remote/example-host/sessions/session-a/events",
            params={"caller_id": "consumer-a", "after": 4},
            headers={"Authorization": "Bearer " + "test-token"},
        ) as response:
            text = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: bridge_control" in text
    assert '"code":"consumer_backpressure"' in text
    assert '"action":"full_reconcile"' in text


def test_remote_cursor_ack_stays_caller_scoped(remote_app) -> None:
    with TestClient(remote_app) as client:
        response = client.post(
            "/api/v1/remote/example-host/sessions/session-a/cursor",
            json={
                "caller_id": "consumer-a",
                "last_id": 5,
                "continuity_id": "epoch-a",
            },
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert remote_app.state.remote_operations.acks == [
        ("example-host", "session-a", "consumer-a", 5, "epoch-a")
    ]


def test_remote_cli_status_defaults_to_text_output(monkeypatch, capsys) -> None:
    class _CliClient:
        def get_remote_session_status(self, *_args, **_kwargs):
            return {
                "session_id": "session-a",
                "status": "idle",
                "last_acked_id": 4,
                "head_id": 5,
            }

    monkeypatch.setattr(
        main_module, "_get_client", lambda **kwargs: _CliClient()
    )
    args = argparse.Namespace(
        remote_action="status",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
    )

    main_module._cmd_remote(args)

    assert capsys.readouterr().out.strip() == (
        "session-a [idle] cursor=4/5"
    )


def test_remote_cli_events_ack_after_output(monkeypatch, capsys) -> None:
    class _CliStream(_FakeStream):
        headers = {"X-Agent-Bridge-Continuity": "epoch-a"}

    class _CliClient:
        def __init__(self) -> None:
            self.acks = []
            self.calls = []
            self.refreshes = 0

        def stream_remote_events(self, *args, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _CliStream(
                    [
                        {
                            "id": "5",
                            "event": "assistant.turn_end",
                            "data": {"stop_reason": "end_turn"},
                            "timestamp": 123.0,
                            "continuity_id": "epoch-a",
                        }
                    ]
                )
            return _CliStream(
                [
                    {
                        "event": "bridge_control",
                        "data": {
                            "code": "cursor_invalidated",
                            "action": "full_reconcile",
                        },
                    }
                ]
            )

        def ack_remote_cursor(
            self,
            host,
            session_id,
            last_id,
            *,
            caller_id,
            continuity_id,
        ):
            self.acks.append(
                (host, session_id, last_id, caller_id, continuity_id)
            )
            return last_id

        def refresh_endpoint(self):
            self.refreshes += 1
            return True

    client = _CliClient()
    monkeypatch.setattr(main_module, "_get_client", lambda **kwargs: client)
    args = argparse.Namespace(
        remote_action="events",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
    )

    monkeypatch.setattr("time.sleep", lambda _delay: None)
    with pytest.raises(SystemExit) as exc:
        main_module._cmd_remote(args)

    output = capsys.readouterr().out
    assert exc.value.code == 2
    assert '5 assistant.turn_end {"stop_reason":"end_turn"}' in output
    assert client.acks == [
        ("example-host", "session-a", 5, "consumer-a", "epoch-a")
    ]
    assert client.calls[1]["after"] == 5
    assert client.calls[1]["continuity_id"] == "epoch-a"
    assert client.refreshes == 1


def test_remote_cli_events_reconnects_after_active_stream_reset(
    monkeypatch, capsys
) -> None:
    class _ResetStream:
        headers = {"X-Agent-Bridge-Continuity": "epoch-a"}

        def __iter__(self):
            raise OSError("connection reset")

        def close(self):
            pass

    class _CliClient:
        def __init__(self) -> None:
            self.calls = 0
            self.refreshes = 0

        def stream_remote_events(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _ResetStream()
            return _FakeStream(
                [{"event": "bridge_control", "data": {"code": "gap"}}]
            )

        def refresh_endpoint(self):
            self.refreshes += 1

    client = _CliClient()
    monkeypatch.setattr(main_module, "_get_client", lambda **kwargs: client)
    monkeypatch.setattr("time.sleep", lambda _delay: None)
    args = argparse.Namespace(
        remote_action="events",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
        json=True,
    )

    with pytest.raises(SystemExit) as exc:
        main_module._cmd_remote(args)

    assert exc.value.code == 2
    assert client.calls == 2
    assert client.refreshes == 1
    assert '"code": "gap"' in capsys.readouterr().out


def test_remote_cli_events_defers_to_durable_cursor_after_ambiguous_ack(
    monkeypatch,
) -> None:
    class _CliStream(_FakeStream):
        headers = {"X-Agent-Bridge-Continuity": "epoch-a"}

    class _CliClient:
        def __init__(self) -> None:
            self.stream_calls = []
            self.ack_calls = 0

        def stream_remote_events(self, *args, **kwargs):
            self.stream_calls.append(kwargs)
            if len(self.stream_calls) == 1:
                return _CliStream(
                    [
                        {
                            "id": "5",
                            "event": "assistant.turn_end",
                            "data": {},
                            "continuity_id": "epoch-a",
                        }
                    ]
                )
            return _CliStream(
                [{"event": "bridge_control", "data": {"code": "gap"}}]
            )

        def ack_remote_cursor(self, *args, **kwargs):
            self.ack_calls += 1
            raise BridgeClientError(
                503,
                {
                    "code": "remote_bridge_unavailable",
                    "message": "response lost after commit",
                },
            )

        def refresh_endpoint(self):
            return True

    client = _CliClient()
    monkeypatch.setattr(main_module, "_get_client", lambda **kwargs: client)
    monkeypatch.setattr("time.sleep", lambda _delay: None)
    args = argparse.Namespace(
        remote_action="events",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
        json=True,
    )

    with pytest.raises(SystemExit) as exc:
        main_module._cmd_remote(args)

    assert exc.value.code == 2
    assert client.ack_calls == 1
    assert client.stream_calls[0]["after"] == 4
    assert client.stream_calls[1]["after"] is None


def test_remote_cli_events_maps_ack_invalidation_to_control(
    monkeypatch, capsys
) -> None:
    class _CliStream(_FakeStream):
        headers = {"X-Agent-Bridge-Continuity": "epoch-a"}

    class _CliClient:
        def stream_remote_events(self, *args, **kwargs):
            return _CliStream(
                [
                    {
                        "id": "5",
                        "event": "assistant.turn_end",
                        "data": {},
                        "continuity_id": "epoch-a",
                    }
                ]
            )

        def ack_remote_cursor(self, *args, **kwargs):
            raise BridgeClientError(
                409,
                {
                    "code": "cursor_invalidated",
                    "action": "full_reconcile",
                },
            )

    monkeypatch.setattr(
        main_module, "_get_client", lambda **kwargs: _CliClient()
    )
    args = argparse.Namespace(
        remote_action="events",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
        json=True,
    )

    with pytest.raises(SystemExit) as exc:
        main_module._cmd_remote(args)

    assert exc.value.code == 2
    assert '"control": {' in capsys.readouterr().out


def test_remote_cli_events_maps_setup_invalidation_to_control(
    monkeypatch, capsys
) -> None:
    class _CliClient:
        def stream_remote_events(self, *args, **kwargs):
            raise BridgeClientError(
                409,
                {
                    "code": "cursor_invalidated",
                    "action": "full_reconcile",
                },
            )

    monkeypatch.setattr(
        main_module, "_get_client", lambda **kwargs: _CliClient()
    )
    args = argparse.Namespace(
        remote_action="events",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
        json=True,
    )

    with pytest.raises(SystemExit) as exc:
        main_module._cmd_remote(args)

    assert exc.value.code == 2
    assert '"control": {' in capsys.readouterr().out


def test_remote_cli_events_retries_transient_setup_error(monkeypatch) -> None:
    class _CliClient:
        def __init__(self) -> None:
            self.calls = 0
            self.refreshes = 0

        def stream_remote_events(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise BridgeClientError(
                    503, {"code": "carrier_unavailable"}
                )
            return _FakeStream(
                [{"event": "bridge_control", "data": {"code": "gap"}}]
            )

        def refresh_endpoint(self):
            self.refreshes += 1

    client = _CliClient()
    monkeypatch.setattr(main_module, "_get_client", lambda **kwargs: client)
    monkeypatch.setattr("time.sleep", lambda _delay: None)
    args = argparse.Namespace(
        remote_action="events",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
        json=True,
    )

    with pytest.raises(SystemExit) as exc:
        main_module._cmd_remote(args)

    assert exc.value.code == 2
    assert client.calls == 2
    assert client.refreshes == 1


def test_remote_cli_events_does_not_retry_broken_stdout(monkeypatch) -> None:
    class _CliClient:
        def __init__(self) -> None:
            self.calls = 0

        def stream_remote_events(self, *args, **kwargs):
            self.calls += 1
            return _FakeStream(
                [
                    {
                        "id": "5",
                        "event": "assistant.turn_end",
                        "data": {},
                        "continuity_id": "epoch-a",
                    }
                ]
            )

    client = _CliClient()
    monkeypatch.setattr(main_module, "_get_client", lambda **kwargs: client)
    monkeypatch.setattr(
        main_module,
        "_json_out",
        lambda _event: (_ for _ in ()).throw(BrokenPipeError()),
    )
    args = argparse.Namespace(
        remote_action="events",
        host="example-host",
        session_id="session-a",
        caller_id="consumer-a",
        after=4,
        continuity_id="epoch-a",
        json=True,
    )

    with pytest.raises(BrokenPipeError):
        main_module._cmd_remote(args)

    assert client.calls == 1


@pytest.mark.parametrize(
    "argv",
    [
        [
            "remote",
            "status",
            "example-host",
            "session-a",
            "--caller-id",
            "consumer-a",
            "--json",
        ],
        ["remote", "live-session", "example-host", "session-a", "--json"],
        [
            "remote",
            "events",
            "example-host",
            "session-a",
            "--caller-id",
            "consumer-a",
            "--json",
        ],
    ],
)
def test_remote_leaf_commands_accept_json(argv) -> None:
    assert main_module.build_parser().parse_args(argv).json is True
