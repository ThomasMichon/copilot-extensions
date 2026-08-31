"""Tests for the coordinator-hosted HTTP MCP endpoint (mounted at /mcp)."""

from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace

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
    assert len(names) == 27
    assert {"dispatch_suspend", "dispatch_resume", "dispatch_release"} <= set(
        names
    )
    assert "dispatch_wakes" in names
    assert "dispatch_result" in names
    assert "dispatch_rearm_spawn" in names
    assert "dispatch_emitter_side_load" in names
    complete = next(t for t in tools if t.name == "dispatch_complete")
    result_schema = complete.input_schema["properties"]["result"]
    variants = result_schema.get("anyOf", [result_schema])
    assert {variant.get("type") for variant in variants} == {"array", "object"}


def test_hosted_emitter_side_load_routes_to_registration_owner(tmp_path, monkeypatch):
    import asyncio

    from agent_dispatch import remote_dispatch

    queue = TaskQueue(tmp_path / "tasks.db")
    queue.register_registration(
        "emitter",
        {
            "id": "reviews",
            "command": ["review-source", "discover"],
            "interval_seconds": 60,
            "side_load": {"command": ["review-source", "{change_ref}"]},
        },
        reg_id="emitter-reviews",
        machine="host-b",
    )
    calls = []
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(
        remote_dispatch,
        "browse_remote",
        lambda machine, argv, timeout=None: (
            calls.append((machine, argv, timeout))
            or SimpleNamespace(
                returncode=0,
                stdout='{"registration_id":"emitter-reviews","created":[]}',
                stderr="",
            )
        ),
    )
    url, stop = _boot(create_app(queue))
    try:
        result = asyncio.new_event_loop().run_until_complete(
            _call(
                url,
                "dispatch_emitter_side_load",
                {"registration_id": "emitter-reviews", "change_ref": "o/n#7"},
            )
        )
    finally:
        stop()
    assert not result.is_error
    assert calls == [
        (
            "host-b",
            [
                "agent-dispatch",
                "emitter",
                "side-load",
                "emitter-reviews",
                "o/n#7",
                "--env",
                "default",
            ],
            120,
        )
    ]


def test_hosted_emitter_side_load_can_create_proposed_task(tmp_path, monkeypatch):
    import asyncio

    from agent_dispatch import remote_dispatch

    queue = TaskQueue(tmp_path / "tasks.db")
    queue.register_registration(
        "emitter",
        {
            "id": "reviews",
            "command": ["review-source", "discover"],
            "interval_seconds": 60,
            "side_load": {"command": ["review-source", "{change_ref}"]},
        },
        reg_id="emitter-reviews",
        machine="host-a",
    )
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    from agent_dispatch.producers import emitter

    original_side_load = emitter.run_side_load
    monkeypatch.setattr(
        emitter,
        "run_side_load",
        lambda client, registration, change_ref, **kwargs: original_side_load(
            client,
            registration,
            change_ref,
            **kwargs,
            runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout='{"title":"review","repo":"o/n","proposed":true}',
                stderr="",
            ),
        ),
    )
    url, stop = _boot(create_app(queue))
    try:
        result = asyncio.new_event_loop().run_until_complete(
            _call(
                url,
                "dispatch_emitter_side_load",
                {"registration_id": "emitter-reviews", "change_ref": "o/n#7"},
            )
        )
    finally:
        stop()
    assert not result.is_error
    assert queue.list(status="proposed")[0].source == "emitter"


def test_mcp_rearm_spawn(coord):
    import asyncio
    import json

    client = DispatchClient(coord)
    task = client.create("work")
    for _ in range(3):
        reservation = client.reserve_spawn(task["id"])["reservation"]
        client.fail_spawn(reservation["key"], detail="down")

    response = asyncio.new_event_loop().run_until_complete(
        _call(
            coord,
            "dispatch_rearm_spawn",
            {
                "task_id": task["id"],
                "permit": True,
                "reason": "transport repaired",
            },
        )
    )
    result = json.loads(response.content[0].text)
    assert result["rearmed"] == 3


def test_mcp_rearm_spawn_reaches_rest_sse(coord):
    import asyncio
    import threading

    client = DispatchClient(coord)
    task = client.create("work")
    for _ in range(3):
        reservation = client.reserve_spawn(task["id"])["reservation"]
        client.fail_spawn(reservation["key"], detail="down")

    seen = []

    def watch():
        with DispatchClient(coord) as watcher:
            for event in watcher.stream_events():
                seen.append(event)
                if event.get("type") == "spawn.rearmed":
                    break

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    time.sleep(0.5)
    asyncio.new_event_loop().run_until_complete(
        _call(
            coord,
            "dispatch_rearm_spawn",
            {
                "task_id": task["id"],
                "permit": True,
                "reason": "transport repaired",
            },
        )
    )
    thread.join(timeout=5)
    assert any(event.get("type") == "spawn.rearmed" for event in seen)


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


def test_mcp_complete_rejects_null_result(coord):
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
                "result": None,
            },
        )
    )

    assert response.is_error
    assert client.get(task["id"])["status"] == Status.STARTED


def test_mcp_complete_normalizes_json_object_string(coord):
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
                "result": '{"ok":true}',
            },
        )
    )

    assert not response.is_error
    assert client.result(task["id"])["result"] == {"ok": True}


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
