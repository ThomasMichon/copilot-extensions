"""Normalized host feed over the existing authenticated carrier, one hop only."""

from __future__ import annotations

import asyncio

from ssh_manager import Envelope, EnvelopeType

from .client import BridgeClientError
from .remote_errors import RemoteBridgeError
from .status_schema import CAPABILITY


async def _open_stream(client, after, epoch):
    owner = asyncio.create_task(asyncio.to_thread(
        client._stream_sse, "/api/v1/status/events",
        params={"after": str(after), "epoch": epoch},
    ))
    cancelled = None
    while True:
        try:
            stream = await asyncio.shield(owner)
            break
        except asyncio.CancelledError as exc:
            cancelled = exc
    if cancelled is not None:
        stream.close()
        raise cancelled
    return stream


async def prepare_stream(envelope, client):
    health = await asyncio.to_thread(client.health)
    capability = health.get("capabilities", {}).get(CAPABILITY)
    if type(capability) is not int or capability != 1:
        raise RemoteBridgeError(
            426, "host_status_unsupported",
            "The serving host must advertise host_status_feed v1; update the provider before subscribing",
        )
    after = envelope.position
    epoch = envelope.payload.get("epoch")
    if (
        type(after) is not int or not 0 <= after <= (1 << 63) - 1
        or (epoch is not None and (not isinstance(epoch, str) or not 1 <= len(epoch) <= 128))
    ):
        raise RemoteBridgeError(400, "invalid_request", "Invalid host status cursor")
    if epoch is None:
        info = await asyncio.to_thread(client._request, "GET", "/api/v1/status")
        after, epoch = info["head"], info["epoch"]
    # Read-only validation before acknowledging the stream. Old providers and
    # supported rollback refuse explicitly; no effectful probe/fallback exists.
    await asyncio.to_thread(
        client._request, "GET", "/api/v1/status/feed",
        params={"after": str(after), "epoch": epoch, "limit": "1"},
    )
    async def frames():
        from .remote_operations import _EOF, _next_sse
        stream = None
        try:
            yield Envelope(
                EnvelopeType.RESPONSE, payload={"epoch": epoch, "cursor": after},
            )
            stream = await _open_stream(client, after, epoch)
            while True:
                event = await asyncio.to_thread(_next_sse, stream)
                if event is _EOF:
                    return
                if event["event"] == "bridge_control":
                    control = event.get("data") or {}
                    yield Envelope(EnvelopeType.ERROR, payload={
                        "code": control.get("code", "status_replay_gap"), "status": 409,
                        "message": "Host status stream requires reconciliation or reconnect",
                    })
                    return
                if event["event"] in {"status_page", "_heartbeat"}:
                    yield Envelope(EnvelopeType.EVENT, payload=event)
        except BridgeClientError as exc:
            yield Envelope(EnvelopeType.ERROR, payload={
                "code": "host_status_unavailable", "status": exc.status,
                "message": "Host status stream is unavailable",
            })
        finally:
            if stream is not None:
                stream.close()

    return frames()
