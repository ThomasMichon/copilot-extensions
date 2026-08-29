"""Tests for the coordinator-hosted HTTP MCP endpoint (mounted at /mcp)."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue

mcp = pytest.importorskip("mcp")
import httpx2  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402


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
    # mcp 2.0: streamable_http_client returns a 2-tuple and takes headers via an
    # httpx2 client (30s/read-300s matches the old streamablehttp_client default).
    async with httpx2.AsyncClient(
        headers=headers or {}, timeout=httpx2.Timeout(30, read=300), follow_redirects=True
    ) as http_client:
        async with streamable_http_client(f"{url}/mcp", http_client=http_client) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool(tool, args)
                return res


def test_mcp_endpoint_lists_tools(coord):
    import asyncio

    async def go():
        async with httpx2.AsyncClient(
            timeout=httpx2.Timeout(30, read=300), follow_redirects=True
        ) as http_client:
            async with streamable_http_client(f"{coord}/mcp", http_client=http_client) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return (await s.list_tools()).tools

    tools = asyncio.new_event_loop().run_until_complete(go())
    names = sorted(t.name for t in tools)
    assert "dispatch_create" in names
    assert "dispatch_claim" in names
    assert len(names) == 25
    assert {"dispatch_suspend", "dispatch_resume", "dispatch_release"} <= set(
        names
    )
    assert "dispatch_wakes" in names
    assert "dispatch_result" in names
    complete = next(t for t in tools if t.name == "dispatch_complete")
    result_schema = complete.input_schema["properties"]["result"]
    variants = result_schema.get("anyOf", [result_schema])
    assert {variant.get("type") for variant in variants} == {"array", "object"}


def test_mcp_create_visible_over_rest(coord):
    import asyncio
    import json

    res = asyncio.new_event_loop().run_until_complete(
        _call(coord, "dispatch_create", {"title": "via mcp", "dedup_key": "m1", "repo": TEST_REPO})
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


def test_mcp_complete_result_is_visible_over_rest(coord):
    import asyncio
    import json

    client = DispatchClient(coord)
    task = client.create("work")
    owner = client.claim(worker_id="worker-1")["owner"]
    client.start(task["id"], owner)
    result = {"verdict": "accepted", "details": {"count": 2}}

    response = asyncio.new_event_loop().run_until_complete(
        _call(
            coord,
            "dispatch_complete",
            {
                "task_id": task["id"],
                "worker_id": owner,
                "result_ref": "artifact/2",
                "result": result,
            },
        )
    )
    completed = json.loads(response.content[0].text)

    assert completed["result"] == result
    assert client.get(task["id"])["result"] == result
    listed = client.list(status=Status.COMPLETED)[0]
    assert listed["has_result"] is True
    assert "result" not in listed
    retrieved = asyncio.new_event_loop().run_until_complete(
        _call(coord, "dispatch_result", {"task_id": task["id"]})
    )
    assert json.loads(retrieved.content[0].text)["result"] == result


def test_mcp_retry_fill_emits_result_recorded_not_duplicate_completion(
    coord, monkeypatch
):
    import asyncio
    import json

    from agent_dispatch.events import EventBus

    published = []
    original_publish = EventBus.publish

    def capture(self, event):
        published.append(event)
        return original_publish(self, event)

    monkeypatch.setattr(EventBus, "publish", capture)
    client = DispatchClient(coord)
    task = client.create("work")
    owner = client.claim(worker_id="worker-1")["owner"]
    client.start(task["id"], owner)
    client.complete(task["id"], owner)

    response = asyncio.new_event_loop().run_until_complete(
        _call(
            coord,
            "dispatch_complete",
            {
                "task_id": task["id"],
                "worker_id": owner,
                "result": {"ok": True},
            },
        )
    )

    assert json.loads(response.content[0].text)["result"] == {"ok": True}
    types = [event["type"] for event in published]
    assert types.count("task.completed") == 1
    assert types.count("task.result_recorded") == 1


@pytest.mark.parametrize("result", [None, '{"ok":true}'])
def test_mcp_complete_rejects_null_and_double_encoded_result(coord, result):
    import asyncio

    client = DispatchClient(coord)
    task = client.create("work")
    owner = client.claim(worker_id="worker-1")["owner"]
    client.start(task["id"], owner)

    response = asyncio.new_event_loop().run_until_complete(
        _call(
            coord,
            "dispatch_complete",
            {
                "task_id": task["id"],
                "worker_id": owner,
                "result": result,
            },
        )
    )

    assert response.is_error
    assert client.get(task["id"])["status"] == Status.STARTED


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
        _call(coord, "dispatch_create", {"title": "emit me", "repo": TEST_REPO})
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
