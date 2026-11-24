"""Request preflight and actual transport share the negotiated wire encoding."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from ssh_manager.carrier import (
    CarrierProtocolError, Envelope, EnvelopeType, PersistentCarrier,
    decode_envelope, read_envelope,
)

fixtures = importlib.import_module("tests.test_carrier")


@pytest.mark.asyncio
async def test_request_preflight_and_send_use_the_same_negotiated_encoding() -> None:
    received: list[Envelope] = []

    async def handler(
        _number: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        envelope = await read_envelope(reader)
        received.append(envelope)
        await fixtures._write(writer, Envelope(
            EnvelopeType.RESPONSE, request_id=envelope.request_id, payload={"accepted": True},
        ))
        await reader.read()

    server, opener, closer, _count = await fixtures._start_peer(handler, max_frame_size=512)
    carrier = PersistentCarrier("identity", "carrier", opener, closer, max_frame_size=2048)
    lease = await carrier.acquire()
    try:
        payload = {"value": "bounded"}
        encoded = carrier.encode_request(payload, request_id="original-correlation")
        response = await carrier.request(payload, request_id="original-correlation")
        assert response.payload == {"accepted": True}
        assert received == [decode_envelope(encoded[4:])]
        with pytest.raises(CarrierProtocolError, match="exceeds limit 512"):
            carrier.encode_request({"value": "x" * 512}, request_id="oversized")
        assert len(received) == 1
    finally:
        await lease.release()
        await carrier.close()
        server.close()
        await server.wait_closed()
