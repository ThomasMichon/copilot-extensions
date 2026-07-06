"""Tests for the coordinator-hosted HTTP MCP endpoint (mounted at /mcp)."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue

mcp = pytest.importorskip("mcp")
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402


def _boot(app):
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    probe = DispatchClient(url)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            probe.health()
            break
        except Exception:
            time.sleep(0.05)
    else:
        probe.close()
        raise RuntimeError("coordinator did not start")
    probe.close()

    def stop():
        server.should_exit = True
        thread.join(timeout=5)

    return url, stop


@pytest.fixture
def coord(tmp_path):
    url, stop = _boot(create_app(TaskQueue(tmp_path / "tasks.db")))
    yield url
    stop()


async def _call(url, tool, args, headers=None):
    async with streamablehttp_client(f"{url}/mcp", headers=headers or {}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return res


def test_mcp_endpoint_lists_tools(coord):
    import asyncio

    async def go():
        async with streamablehttp_client(f"{coord}/mcp") as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return sorted(t.name for t in (await s.list_tools()).tools)

    names = asyncio.new_event_loop().run_until_complete(go())
    assert "dispatch_create" in names
    assert "dispatch_claim" in names
    assert len(names) == 16


def test_mcp_create_visible_over_rest(coord):
    import asyncio
    import json

    res = asyncio.new_event_loop().run_until_complete(
        _call(coord, "dispatch_create", {"title": "via mcp", "dedup_key": "m1"})
    )
    task = json.loads(res.content[0].text)
    assert task["status"] == Status.QUEUED
    # the REST client sees the same task
    got = DispatchClient(coord).get(task["id"])
    assert got["title"] == "via mcp"


def test_mcp_claim_uses_header_identity(coord):
    import asyncio
    import json

    # seed a task via REST
    DispatchClient(coord).create("work")
    res = asyncio.new_event_loop().run_until_complete(
        _call(
            coord,
            "dispatch_claim",
            {},
            headers={"X-Agent-Machine": "host-a", "X-Agent-Worktree": "wt-1"},
        )
    )
    claimed = json.loads(res.content[0].text)
    assert claimed["owner"] == "host-a/wt-1"  # composed from the request headers


def test_mcp_claim_without_identity_errors(coord):
    import asyncio
    import json

    DispatchClient(coord).create("work")
    res = asyncio.new_event_loop().run_until_complete(_call(coord, "dispatch_claim", {}))
    payload = json.loads(res.content[0].text)
    assert "error" in payload


def test_mcp_events_reach_rest_sse(coord):
    """A task created via the MCP endpoint publishes to the shared event bus."""
    import asyncio
    import threading

    seen = []

    def watch():
        client = DispatchClient(coord)
        for event in client.stream_events():
            seen.append(event)
            if event.get("type") == "task.created":
                break

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    time.sleep(0.5)  # let the subscriber attach
    asyncio.new_event_loop().run_until_complete(
        _call(coord, "dispatch_create", {"title": "emit me"})
    )
    t.join(timeout=5)
    assert any(e.get("type") == "task.created" for e in seen)


def test_mcp_disabled_when_requested(tmp_path):
    # enable_mcp=False -> no /mcp mount; REST still serves.
    url, stop = _boot(create_app(TaskQueue(tmp_path / "tasks.db"), enable_mcp=False))
    try:
        import httpx

        assert DispatchClient(url).health()["status"] == "ok"
        # /mcp should 404 (not mounted)
        r = httpx.post(f"{url}/mcp", json={}, headers={"Accept": "application/json"})
        assert r.status_code == 404
    finally:
        stop()
