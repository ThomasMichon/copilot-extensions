"""Peer close frames must reach bounded application-owned channel cleanup."""

import asyncio

import pytest

from fleet_manager.gateway_service_channel import ClientSocket, send
from gateway_service_transport_fixtures import eventually, running
from test_gateway_service_transport_limits import admitted


def stall_close_writer(monkeypatch, writer):
    close = type(writer).close
    entered, canceled = asyncio.Event(), asyncio.Event()

    async def stalled(selected, *args, **kwargs):
        if selected is writer:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                canceled.set()
        else:
            await close(selected, *args, **kwargs)

    monkeypatch.setattr(type(writer), "close", stalled)
    monkeypatch.setattr("fleet_manager.gateway_service_channel.SEND_TIMEOUT", 0.1)
    return entered, canceled


@pytest.mark.parametrize("mode", ["close", "protocol-error"])
def test_peer_close_cannot_retain_gateway_handler_or_generation(tmp_path, monkeypatch, mode):
    async def scenario():
        async with running(tmp_path) as rig:
            ticket = await admitted(rig)
            async with rig.session.ws_connect(rig.origin + "/_fleet/service-channel") as peer:
                await send(peer, {"ticket": ticket})
                await peer.receive()
                channel = rig.state.channels["service-a"]
                entered, canceled = stall_close_writer(monkeypatch, channel.ws._writer)
                if mode == "close":
                    await asyncio.wait_for(peer.close(), 2)
                else:
                    # Reserved opcode, masked empty client frame.
                    peer._writer.transport.write(b"\x83\x80\x00\x00\x00\x00")
                await eventually(lambda: "service-a" not in rig.state.channels)
                assert entered.is_set() and canceled.is_set()
                assert channel.closed and not channel.pending
                assert rig.state.active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["close", "protocol-error"])
def test_peer_close_cannot_retain_connector_connected_state(tmp_path, monkeypatch, mode):
    async def scenario():
        receive = ClientSocket.receive
        sockets = []

        async def observe(socket, *args, **kwargs):
            if not sockets:
                sockets.append(socket)
            return await receive(socket, *args, **kwargs)

        monkeypatch.setattr(ClientSocket, "receive", observe)
        async with running(tmp_path) as rig:
            connector = await rig.connect()
            entered, canceled = stall_close_writer(monkeypatch, sockets[0]._writer)
            channel = rig.state.channels["service-a"]
            if mode == "close":
                await channel.close()
                await asyncio.wait_for(rig.tasks[-1], 2)
            else:
                # Reserved opcode, unmasked empty server frame.
                channel.ws._writer.transport.write(b"\x83\x00")
                await eventually(lambda: rig.tasks[-1].done())
                with pytest.raises(asyncio.TimeoutError):
                    await rig.tasks[-1]
            assert entered.is_set() and canceled.is_set()
            assert not connector.connected.is_set() and not connector.active
    asyncio.run(scenario())
