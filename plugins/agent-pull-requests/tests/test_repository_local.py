"""A stalled local response never owns the listener or writer indefinitely."""

import asyncio
import os
import socket
import sys

import pytest

from agent_pull_requests.watch_local import LocalIngress
from fleet_manager.gateway_service_handler import BoundedServiceResponse


@pytest.mark.skipif(sys.platform != "linux", reason="Linux OS peer admission")
@pytest.mark.parametrize("withdraw", [False, True])
def test_stalled_local_response_is_aborted_and_exact_connection_joined(tmp_path, withdraw):
    async def scenario():
        stop, entered = asyncio.Event(), asyncio.Event()
        async def handler(_request):
            entered.set()
            return BoundedServiceResponse(200, (), b"x" * 1_048_576)
        ingress = LocalIngress(
            path=tmp_path / "local.sock", namespace="test",
            peers={os.getuid(): "reader"}, handler=handler, operation_seconds=1,
        )
        owner = asyncio.create_task(ingress.run(stop))
        peer = socket.socket(socket.AF_UNIX)
        peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        peer.setblocking(False)
        try:
            await asyncio.wait_for(ingress.connected.wait(), 2)
            loop = asyncio.get_running_loop()
            await loop.sock_connect(peer, str(ingress.path))
            await loop.sock_sendall(peer, b"GET /v1/repositories HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await asyncio.wait_for(entered.wait(), 2)
            if withdraw:
                stop.set()
            else:
                deadline = loop.time() + 3
                while ingress._tasks and loop.time() < deadline:
                    await asyncio.sleep(.02)
                assert not ingress._tasks
        finally:
            stop.set()
            await asyncio.wait_for(owner, 3)
            peer.close()
        assert not ingress._tasks and not ingress.path.exists()
    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux OS peer admission")
def test_withdrawal_aborts_partial_headers_before_waiting_for_listener_closure(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        async def handler(_request):
            pytest.fail("partial headers reached the handler")
        ingress = LocalIngress(
            path=tmp_path / "partial.sock", namespace="test",
            peers={os.getuid(): "reader"}, handler=handler, operation_seconds=30,
        )
        owner = asyncio.create_task(ingress.run(stop))
        peer = socket.socket(socket.AF_UNIX)
        peer.setblocking(False)
        try:
            await asyncio.wait_for(ingress.connected.wait(), 2)
            await asyncio.get_running_loop().sock_connect(peer, str(ingress.path))
            await asyncio.get_running_loop().sock_sendall(peer, b"GET /v1/")
            while not ingress._requests:
                await asyncio.sleep(.01)
            stop.set()
            await asyncio.wait_for(asyncio.shield(owner), 2)
            assert not ingress._tasks
        finally:
            stop.set()
            peer.close()
            await asyncio.wait_for(owner, 3)
    asyncio.run(scenario())
