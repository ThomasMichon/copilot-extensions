"""Tests for the persistent framed SSH carrier foundation."""

from __future__ import annotations

import asyncio
import contextlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ssh_manager.carrier import (
    CarrierBackpressure,
    CarrierProtocolError,
    CarrierRemoteError,
    CarrierStale,
    CarrierUnavailable,
    Envelope,
    EnvelopeType,
    PersistentCarrier,
    StdioCarrierServer,
    decode_envelope,
    encode_envelope,
    hello_envelope,
    read_envelope,
    negotiated_frame_size,
    validate_hello,
)
from ssh_manager.manager import ConnectionManager


class _StreamProcess:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.stdout = reader
        self.stdin = writer
        self.stderr = None
        self.returncode = None

    async def wait(self) -> int:
        await self.stdin.wait_closed()
        self.returncode = 0
        return 0


async def _write(writer: asyncio.StreamWriter, envelope: Envelope) -> None:
    writer.write(encode_envelope(envelope))
    await writer.drain()


async def _start_peer(handler, *, max_frame_size: int = 1024 * 1024):
    connections = 0

    async def _client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal connections
        connection_number = connections
        connections += 1
        try:
            await _write(
                writer,
                hello_envelope(max_frame_size=max_frame_size),
            )
            validate_hello(await read_envelope(reader))
            await handler(connection_number, reader, writer)
        except (EOFError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def opener():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        return _StreamProcess(reader, writer)

    async def closer(process):
        process.stdin.close()
        with contextlib.suppress(ConnectionError):
            await process.stdin.wait_closed()
        process.returncode = 0

    return server, opener, closer, lambda: connections


def test_frame_round_trip_and_bounds():
    envelope = Envelope(
        EnvelopeType.EVENT,
        payload={"value": "synthetic"},
        subscription_id="sub-1",
        position=12,
    )
    frame = encode_envelope(envelope)
    assert decode_envelope(frame[4:]) == envelope
    with pytest.raises(CarrierProtocolError, match="exceeds limit"):
        encode_envelope(envelope, max_frame_size=8)
    with pytest.raises(CarrierProtocolError, match="valid UTF-8 JSON"):
        decode_envelope(b"not-json")


def test_hello_rejects_incompatible_peer():
    incompatible = Envelope(
        EnvelopeType.HELLO,
        payload={
            "protocol_version": 9,
            "min_protocol_version": 9,
            "max_frame_size": 1024,
        },
    )
    with pytest.raises(CarrierProtocolError, match="no compatible"):
        validate_hello(incompatible)


def test_hello_negotiates_smaller_frame_limit():
    peer = hello_envelope(max_frame_size=256)
    assert negotiated_frame_size(peer, local_max_frame_size=1024) == 256
    with pytest.raises(CarrierProtocolError, match="max_frame_size"):
        validate_hello(
            Envelope(
                EnvelopeType.HELLO,
                payload={
                    "protocol_version": 1,
                    "min_protocol_version": 1,
                    "max_frame_size": 0,
                },
            )
        )


@pytest.mark.asyncio
async def test_peer_frame_limit_bounds_client_output():
    async def handler(_number, _reader, _writer):
        await asyncio.sleep(1)

    server, opener, closer, _count = await _start_peer(
        handler,
        max_frame_size=256,
    )
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        max_frame_size=1024,
    )
    lease = await carrier.acquire()
    try:
        with pytest.raises(CarrierProtocolError, match="exceeds limit 256"):
            await carrier.request({"value": "x" * 512})
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_peer_receive_limit_does_not_bound_inbound_events():
    async def handler(_number, reader, writer):
        request = await read_envelope(reader)
        await _write(
            writer,
            Envelope(
                EnvelopeType.EVENT,
                subscription_id=request.subscription_id,
                payload={"value": "x" * 512},
            ),
        )
        await asyncio.sleep(1)

    server, opener, closer, _count = await _start_peer(
        handler,
        max_frame_size=256,
    )
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        max_frame_size=1024,
    )
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="sub-large-inbound",
    )
    try:
        event = await asyncio.wait_for(subscription.get(), timeout=1)
        assert event.payload["value"] == "x" * 512
    finally:
        await subscription.close()
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_concurrent_requests_are_correlated():
    async def handler(_number, reader, writer):
        async def respond(envelope):
            await asyncio.sleep(float(envelope.payload["delay"]))
            await _write(
                writer,
                Envelope(
                    EnvelopeType.RESPONSE,
                    request_id=envelope.request_id,
                    payload={"name": envelope.payload["name"]},
                ),
            )

        tasks = []
        while len(tasks) < 2:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))
            else:
                tasks.append(asyncio.create_task(respond(envelope)))
        await asyncio.gather(*tasks)
        await asyncio.sleep(1)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    lease = await carrier.acquire()
    try:
        slow, fast = await asyncio.gather(
            carrier.request({"name": "slow", "delay": 0.04}),
            carrier.request({"name": "fast", "delay": 0.0}),
        )
        assert slow.payload["name"] == "slow"
        assert fast.payload["name"] == "fast"
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_remote_error_preserves_code_and_payload():
    async def handler(_number, reader, writer):
        request = await read_envelope(reader)
        await _write(
            writer,
            Envelope(
                EnvelopeType.ERROR,
                request_id=request.request_id,
                payload={
                    "code": "unsupported_version",
                    "message": "update required",
                    "status": 426,
                },
            ),
        )
        await asyncio.sleep(1)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    lease = await carrier.acquire()
    try:
        with pytest.raises(CarrierRemoteError) as exc_info:
            await carrier.request({"operation": "synthetic"})
        assert exc_info.value.code == "unsupported_version"
        assert exc_info.value.payload["status"] == 426
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_stdio_server_streams_async_handler_results():
    async def handler(_request):
        async def results():
            yield Envelope(EnvelopeType.RESPONSE, payload={"accepted": True})
            yield Envelope(EnvelopeType.EVENT, payload={"id": 1})

        return results()

    server = StdioCarrierServer(io.BytesIO(), io.BytesIO(), handler=handler)
    server._queue_envelope = AsyncMock()
    request = Envelope(
        EnvelopeType.REQUEST,
        subscription_id="sub-1",
        payload={"operation": "events"},
    )

    await server._serve_request(request)

    queued = [
        call.args[0] for call in server._queue_envelope.await_args_list
    ]
    assert [item.type for item in queued] == [
        EnvelopeType.RESPONSE,
        EnvelopeType.EVENT,
    ]
    assert all(item.subscription_id == "sub-1" for item in queued)


@pytest.mark.asyncio
async def test_replayable_subscription_resumes_from_latest_position():
    seen_positions: list[int | str | None] = []
    second_connected = asyncio.Event()

    async def handler(number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))
                continue
            seen_positions.append(envelope.position)
            if number == 0:
                await _write(
                    writer,
                    Envelope(
                        EnvelopeType.EVENT,
                        subscription_id=envelope.subscription_id,
                        payload={"kind": "synthetic"},
                        position=7,
                    ),
                )
                return
            second_connected.set()
            await asyncio.sleep(1)

    server, opener, closer, count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        heartbeat_interval=0.01,
        stale_timeout=0.2,
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="sub-1",
        position=3,
    )
    try:
        event = await asyncio.wait_for(subscription.get(), timeout=1)
        assert event.position == 7
        await asyncio.wait_for(second_connected.wait(), timeout=1)
        assert count() >= 2
        assert seen_positions[:2] == [3, 7]
    finally:
        await subscription.close()
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_reconnect_resumes_after_buffered_event_position():
    restore_positions: list[int | str | None] = []
    reconnected = asyncio.Event()

    async def handler(number, reader, writer):
        request = await read_envelope(reader)
        restore_positions.append(request.position)
        if number == 0:
            await _write(
                writer,
                Envelope(
                    EnvelopeType.EVENT,
                    subscription_id=request.subscription_id,
                    payload={"sequence": 7},
                    position=7,
                ),
            )
            return
        await _write(
            writer,
            Envelope(
                EnvelopeType.EVENT,
                subscription_id=request.subscription_id,
                payload={"sequence": 8},
                position=8,
            ),
        )
        reconnected.set()
        await asyncio.sleep(1)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        position=3,
    )
    try:
        await asyncio.wait_for(reconnected.wait(), timeout=1)
        first = await asyncio.wait_for(subscription.get(), timeout=1)
        second = await asyncio.wait_for(subscription.get(), timeout=1)
        assert restore_positions[:2] == [3, 7]
        assert [first.position, second.position] == [7, 8]
    finally:
        await subscription.close()
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_reconnect_can_discard_buffer_before_authoritative_replay():
    reconnected = asyncio.Event()
    restore_positions = []

    async def handler(number, reader, writer):
        request = await read_envelope(reader)
        restore_positions.append(request.position)
        await _write(
            writer,
            Envelope(
                EnvelopeType.EVENT,
                subscription_id=request.subscription_id,
                payload={
                    "source": "buffered" if number == 0 else "replayed"
                },
                position=7,
            ),
        )
        if number == 0:
            return
        reconnected.set()
        await asyncio.sleep(1)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        position=3,
        retain_buffered_on_reconnect=False,
    )
    try:
        await asyncio.wait_for(reconnected.wait(), timeout=1)
        event = await asyncio.wait_for(subscription.get(), timeout=1)
        assert event.payload["source"] == "replayed"
        assert restore_positions[:2] == [3, 3]
        assert subscription.position == 7
    finally:
        await subscription.close()
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_reconnect_restores_all_subscriptions_with_tiny_output_queue():
    seen: dict[int, list[str | None]] = {0: [], 1: []}
    restored = asyncio.Event()

    async def handler(number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))
                continue
            seen[number].append(envelope.subscription_id)
            if len(seen[number]) == 2:
                if number == 0:
                    return
                restored.set()

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        max_queued_frames=1,
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    lease = await carrier.acquire()
    first = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="sub-1",
    )
    await asyncio.sleep(0.01)
    second = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="sub-2",
    )
    try:
        await asyncio.wait_for(restored.wait(), timeout=1)
        assert seen[1] == ["sub-1", "sub-2"]
        assert carrier.diagnostics()["state"] == "healthy"
    finally:
        await first.close()
        await second.close()
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_new_subscription_during_reconnect_is_sent_once():
    seen: list[str | None] = []

    async def handler(number, reader, writer):
        if number == 0:
            await reader.read()
            return
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))
                continue
            seen.append(envelope.subscription_id)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    lease = await carrier.acquire()
    await lease.release()
    await carrier.invalidate_transport("test reconnect")
    subscription = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="sub-new",
    )
    try:
        await asyncio.sleep(0.05)
        assert seen == ["sub-new"]
    finally:
        await subscription.close()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_subscription_waits_for_reconnect_restoration():
    seen: dict[int, list[str | None]] = {0: [], 1: []}

    async def handler(number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))
                continue
            seen[number].append(envelope.subscription_id)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    lease = await carrier.acquire()
    old = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="old",
    )
    restoration_started = asyncio.Event()
    release_restoration = asyncio.Event()
    original_restore = carrier._restore_subscriptions

    async def blocked_restore(process):
        restoration_started.set()
        await release_restoration.wait()
        await original_restore(process)

    carrier._restore_subscriptions = blocked_restore
    await carrier.invalidate_transport("test reconnect")
    await asyncio.wait_for(restoration_started.wait(), timeout=1)
    new_task = asyncio.create_task(
        carrier.subscribe(
            {"operation": "events"},
            subscription_id="new",
        )
    )
    await asyncio.sleep(0.02)
    assert not new_task.done()
    release_restoration.set()
    new = await asyncio.wait_for(new_task, timeout=1)
    try:
        await asyncio.sleep(0.05)
        assert seen[1] == ["old", "new"]
    finally:
        await old.close()
        await new.close()
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_subscription_backpressure_isolated_from_requests():
    cancel_seen = asyncio.Event()

    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))
            elif envelope.type is EnvelopeType.CANCEL:
                cancel_seen.set()
            elif envelope.subscription_id:
                await _write(
                    writer,
                    Envelope(
                        EnvelopeType.EVENT,
                        subscription_id=envelope.subscription_id,
                        payload={"index": 1},
                    ),
                )
                await _write(
                    writer,
                    Envelope(
                        EnvelopeType.EVENT,
                        subscription_id=envelope.subscription_id,
                        payload={"index": 2},
                    ),
                )
            else:
                await _write(
                    writer,
                    Envelope(
                        EnvelopeType.RESPONSE,
                        request_id=envelope.request_id,
                        payload={"ok": True},
                    ),
                )

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="slow",
        queue_size=1,
    )
    try:
        await asyncio.sleep(0.05)
        response = await carrier.request({"operation": "status"})
        assert response.payload == {"ok": True}
        assert carrier.diagnostics()["last_error"] == "subscription backpressure"
        with pytest.raises(CarrierBackpressure, match="queue is full"):
            await subscription.get()
        await asyncio.wait_for(cancel_seen.wait(), timeout=1)
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_terminated_subscription_releases_buffer_budget():
    async def handler(_number, reader, writer):
        envelope = await read_envelope(reader)
        await _write(
            writer,
            Envelope(
                EnvelopeType.EVENT,
                subscription_id=envelope.subscription_id,
                payload={"value": "buffered"},
            ),
        )
        await asyncio.sleep(1)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="buffered",
    )
    try:
        for _ in range(100):
            if carrier.diagnostics()["buffered_event_bytes"]:
                break
            await asyncio.sleep(0.01)
        assert carrier.diagnostics()["buffered_event_bytes"] > 0
        await subscription.close()
        assert carrier.diagnostics()["buffered_event_bytes"] == 0
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_close_during_startup_reaps_late_process():
    opened = asyncio.Event()
    allow_open = asyncio.Event()
    closed: list[_StreamProcess] = []

    async def handler(_number, _reader, _writer):
        await asyncio.sleep(1)

    server, peer_opener, peer_closer, _count = await _start_peer(handler)

    async def opener():
        opened.set()
        await allow_open.wait()
        return await peer_opener()

    async def closer(process):
        closed.append(process)
        await peer_closer(process)

    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    acquire_task = asyncio.create_task(carrier.acquire())
    await opened.wait()
    close_task = asyncio.create_task(carrier.close())
    allow_open.set()
    with pytest.raises(CarrierUnavailable, match="retired"):
        await acquire_task
    await close_task
    assert len(closed) == 1
    assert carrier.diagnostics()["state"] == "closed"
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_cancelled_acquire_releases_logical_client():
    opened = asyncio.Event()

    async def opener():
        opened.set()
        await asyncio.Future()

    async def closer(_process):
        raise AssertionError("no process opened")

    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    task = asyncio.create_task(carrier.acquire())
    await opened.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert carrier.diagnostics()["logical_clients"] == 0
    await carrier.close()


@pytest.mark.asyncio
async def test_cancelled_request_sends_remote_cancel():
    seen: list[EnvelopeType] = []
    request_seen = asyncio.Event()
    cancel_seen = asyncio.Event()

    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            seen.append(envelope.type)
            if envelope.type is EnvelopeType.REQUEST:
                request_seen.set()
            elif envelope.type is EnvelopeType.CANCEL:
                cancel_seen.set()
            elif envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    lease = await carrier.acquire()
    task = asyncio.create_task(
        carrier.request({"operation": "slow"}, timeout=None)
    )
    try:
        await request_seen.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancel_seen.wait(), timeout=1)
        assert seen[:2] == [EnvelopeType.REQUEST, EnvelopeType.CANCEL]
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_cancelled_subscription_start_rolls_back_registration():
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        lambda: asyncio.Future(),
        lambda _process: asyncio.sleep(0),
    )
    send_started = asyncio.Event()

    async def started():
        return None

    class BlockingQueue:
        frame_count = 0
        byte_count = 0

        async def put(self, frame):
            envelope = decode_envelope(frame[4:])
            if envelope.type is EnvelopeType.CANCEL:
                return
            send_started.set()
            await asyncio.Future()

    carrier.ensure_started = started
    carrier._writer_queue = BlockingQueue()
    task = asyncio.create_task(
        carrier.subscribe(
            {"operation": "events"},
            subscription_id="cancelled",
        )
    )
    await send_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert carrier.diagnostics()["active_subscriptions"] == 0
    await carrier.close()


@pytest.mark.asyncio
async def test_subscription_progress_deadline_marks_carrier_degraded():
    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        heartbeat_interval=0.01,
        stale_timeout=0.2,
    )
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        subscription_id="stale",
        progress_timeout=0.03,
    )
    try:
        with pytest.raises(CarrierStale, match="progress expired"):
            await asyncio.wait_for(subscription.get(), timeout=1)
        assert carrier.diagnostics()["state"] == "degraded"
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_inbound_events_refresh_subscription_progress():
    async def handler(_number, reader, writer):
        request = await read_envelope(reader)
        for position in range(10):
            await _write(
                writer,
                Envelope(
                    EnvelopeType.EVENT,
                    subscription_id=request.subscription_id,
                    payload={"kind": "progress"},
                    position=position,
                ),
            )
            await asyncio.sleep(0.01)

    server, opener, closer, _count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        heartbeat_interval=0.01,
        stale_timeout=1,
    )
    lease = await carrier.acquire()
    subscription = await carrier.subscribe(
        {"operation": "events"},
        progress_timeout=0.03,
    )
    try:
        await asyncio.sleep(0.07)
        event = await asyncio.wait_for(subscription.get(), timeout=1)
        assert event.payload["kind"] == "progress"
        assert not subscription.closed
    finally:
        await subscription.close()
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_stale_transport_reconnects_with_bounded_backoff():
    reconnected = asyncio.Event()

    async def handler(number, reader, _writer):
        if number:
            reconnected.set()
        while True:
            await read_envelope(reader)

    server, opener, closer, count = await _start_peer(handler)
    carrier = PersistentCarrier(
        "identity",
        "carrier",
        opener,
        closer,
        heartbeat_interval=0.01,
        stale_timeout=0.04,
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    lease = await carrier.acquire()
    try:
        await asyncio.wait_for(reconnected.wait(), timeout=1)
        assert count() >= 2
        assert carrier.diagnostics()["reconnect_count"] >= 1
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_stale_reader_cannot_close_replacement_transport():
    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, base_opener, closer, _count = await _start_peer(handler)
    processes = []

    async def opener():
        process = await base_opener()
        processes.append(process)
        return process

    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    lease = await carrier.acquire()
    old_process = processes[0]
    old_process.returncode = 0
    await carrier.ensure_started()
    replacement = processes[1]
    await closer(old_process)
    await asyncio.sleep(0.05)
    try:
        assert carrier._process is replacement
        assert carrier.diagnostics()["state"] == "healthy"
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_manager_deduplicates_identity_and_retires_when_idle():
    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, opener, closer, count = await _start_peer(handler)
    manager = ConnectionManager()
    identity = "same-normalized-identity"
    manager._connections = {
        "alias-a": SimpleNamespace(connection_identity=identity),
        "alias-b": SimpleNamespace(connection_identity=identity),
    }

    async def open_channel(_host, _command, **_kwargs):
        return await opener()

    async def close_channel(_host, process, **_kwargs):
        await closer(process)

    manager.open_stdio_channel = open_channel
    manager.close_stdio_channel = close_channel
    lease_a, lease_b = await asyncio.gather(
        manager.acquire_carrier("alias-a", "carrier", idle_timeout=0.02),
        manager.acquire_carrier("alias-b", "carrier", idle_timeout=0.02),
    )
    assert lease_a.carrier is lease_b.carrier
    assert count() == 1
    assert manager.carrier_diagnostics()["logical_clients"] == 2

    await lease_a.release()
    await lease_b.release()
    for _ in range(100):
        if manager.carrier_diagnostics()["total"] == 0:
            break
        await asyncio.sleep(0.01)
    assert manager.carrier_diagnostics()["total"] == 0
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_manager_isolates_carriers_with_different_port_forwards():
    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, opener, closer, count = await _start_peer(handler)
    manager = ConnectionManager()
    identity = "same-normalized-identity"
    manager._connections = {
        "alias-a": SimpleNamespace(
            connection_identity=identity,
            port_forwards=["-R 9001:localhost:9001"],
        ),
        "alias-b": SimpleNamespace(
            connection_identity=identity,
            port_forwards=["-R 9002:localhost:9002"],
        ),
    }

    async def open_channel(_host, _command, **_kwargs):
        return await opener()

    async def close_channel(_host, process, **_kwargs):
        await closer(process)

    manager.open_stdio_channel = open_channel
    manager.close_stdio_channel = close_channel
    lease_a = await manager.acquire_carrier("alias-a", "carrier")
    lease_b = await manager.acquire_carrier("alias-b", "carrier")
    try:
        assert lease_a.carrier is not lease_b.carrier
        assert count() == 2
    finally:
        await lease_a.release()
        await lease_b.release()
        await lease_a.carrier.close()
        await lease_b.carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_manager_starts_unrelated_carriers_concurrently():
    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, opener, closer, _count = await _start_peer(handler)
    manager = ConnectionManager()
    manager._connections = {
        "host-a": SimpleNamespace(connection_identity="identity-a"),
        "host-b": SimpleNamespace(connection_identity="identity-b"),
    }
    a_started = asyncio.Event()
    release_a = asyncio.Event()
    b_started = asyncio.Event()

    async def open_channel(host, _command, **_kwargs):
        if host == "host-a":
            a_started.set()
            await release_a.wait()
        else:
            b_started.set()
        return await opener()

    async def close_channel(_host, process, **_kwargs):
        await closer(process)

    manager.open_stdio_channel = open_channel
    manager.close_stdio_channel = close_channel
    acquire_a = asyncio.create_task(manager.acquire_carrier("host-a", "carrier"))
    await a_started.wait()
    acquire_b = asyncio.create_task(manager.acquire_carrier("host-b", "carrier"))
    await asyncio.wait_for(b_started.wait(), timeout=0.1)
    release_a.set()
    lease_a, lease_b = await asyncio.gather(acquire_a, acquire_b)
    try:
        assert lease_a.carrier is not lease_b.carrier
    finally:
        await lease_a.release()
        await lease_b.release()
        await lease_a.carrier.close()
        await lease_b.carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_manager_reconnects_shared_carrier_after_alias_disconnect():
    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, opener, closer, count = await _start_peer(handler)
    manager = ConnectionManager()
    identity = "same-normalized-identity"
    manager._connections = {
        "alias-a": SimpleNamespace(
            connection_identity=identity,
            child_processes=[],
            master_process=None,
            multiplexed=False,
            socket_path=SimpleNamespace(exists=lambda: False),
        ),
        "alias-b": SimpleNamespace(
            connection_identity=identity,
            child_processes=[],
            master_process=None,
            multiplexed=False,
            socket_path=SimpleNamespace(exists=lambda: False),
        ),
        "alias-c": SimpleNamespace(
            connection_identity=identity,
            child_processes=[],
            master_process=None,
            multiplexed=False,
            socket_path=SimpleNamespace(exists=lambda: False),
        ),
    }

    async def open_channel(host, _command, **_kwargs):
        process = await opener()
        manager._connections[host].child_processes.append(process)
        return process

    async def close_channel(host, process, **_kwargs):
        await closer(process)
        info = manager._connections.get(host)
        if info is not None and process in info.child_processes:
            info.child_processes.remove(process)

    manager.open_stdio_channel = open_channel
    manager.close_stdio_channel = close_channel
    lease = await manager.acquire_carrier(
        "alias-a",
        "carrier",
        reconnect_initial=0.01,
        reconnect_max=0.02,
    )
    try:
        await manager._disconnect_unlocked("alias-b")
        await asyncio.sleep(0.02)
        assert count() == 1
        assert lease.carrier.diagnostics()["state"] == "healthy"

        await manager._disconnect_unlocked("alias-a")
        for _ in range(100):
            if (
                count() >= 2
                and lease.carrier.diagnostics()["state"] == "healthy"
            ):
                break
            await asyncio.sleep(0.01)
        assert count() >= 2
        assert "alias-c" in manager._connections
        assert lease.carrier.diagnostics()["state"] == "healthy"
    finally:
        await lease.release()
        await manager.disconnect_all()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_invalidation_during_handshake_retries_acquisition():
    first_connected = asyncio.Event()
    release_first = asyncio.Event()
    connections = 0

    async def client(reader, writer):
        nonlocal connections
        connections += 1
        if connections == 1:
            first_connected.set()
            await release_first.wait()
            writer.close()
            await writer.wait_closed()
            return
        await _write(writer, hello_envelope())
        validate_hello(await read_envelope(reader))
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server = await asyncio.start_server(client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def opener():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        return _StreamProcess(reader, writer)

    async def closer(process):
        process.stdin.close()
        with contextlib.suppress(ConnectionError):
            await process.stdin.wait_closed()
        process.returncode = 0

    carrier = PersistentCarrier("identity", "carrier", opener, closer)
    acquire_task = asyncio.create_task(carrier.acquire())
    await first_connected.wait()
    await carrier.invalidate_transport("backing alias disconnected")
    release_first.set()
    lease = await asyncio.wait_for(acquire_task, timeout=1)
    try:
        assert connections == 2
        assert carrier.diagnostics()["state"] == "healthy"
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_manager_replaces_carrier_retiring_during_acquire(tmp_path):
    async def handler(_number, reader, writer):
        while True:
            envelope = await read_envelope(reader)
            if envelope.type is EnvelopeType.HEARTBEAT:
                await _write(writer, Envelope(EnvelopeType.HEARTBEAT))

    server, opener, closer, count = await _start_peer(handler)
    manager = ConnectionManager()
    identity = "same-normalized-identity"
    manager._connections = {
        "alias": SimpleNamespace(
            connection_identity=identity,
            child_processes=[],
            multiplexed=False,
            master_process=None,
            socket_path=tmp_path / "unused.sock",
        )
    }

    async def open_channel(host, _command, **_kwargs):
        process = await opener()
        manager._connections[host].child_processes.append(process)
        return process

    async def close_channel(_host, process, **_kwargs):
        await closer(process)

    manager.open_stdio_channel = open_channel
    manager.close_stdio_channel = close_channel
    first = await manager.acquire_carrier("alias", "carrier")
    await first.carrier.close()
    replacement = await manager.acquire_carrier("alias", "carrier")
    try:
        assert replacement.carrier is not first.carrier
        assert count() == 2
    finally:
        await first.release()
        await replacement.release()
        await manager.disconnect_all()
        server.close()
        await server.wait_closed()
