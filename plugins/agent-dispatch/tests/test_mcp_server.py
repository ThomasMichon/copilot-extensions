"""Tests for the local stdio MCP shim (DispatchTools + build_server)."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.mcp_server import DispatchTools, build_server
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def server_url(tmp_path):
    import uvicorn

    app = create_app(TaskQueue(tmp_path / "tasks.db"))
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
    yield url
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def tools(server_url):
    # a fixed identity so claim/worktree_status are deterministic
    return DispatchTools(
        client_factory=lambda: DispatchClient(server_url),
        identity_resolver=lambda: ("m1", "wt-1"),
    )


def test_create_find_show(tools):
    t = tools.create("do a thing", prompt="go", dedup_key="k1")
    assert t["status"] == Status.QUEUED
    assert any(r["id"] == t["id"] for r in tools.find("thing"))
    assert tools.show(t["id"])["title"] == "do a thing"


def test_dedup_via_create(tools):
    a = tools.create("dup", dedup_key="same")
    b = tools.create("dup", dedup_key="same")
    assert a["id"] == b["id"]


def test_claim_uses_resolved_identity(tools):
    t = tools.create("work")
    claimed = tools.claim()
    assert claimed is not None
    assert claimed["id"] == t["id"]
    assert claimed["owner"] == "m1/wt-1"  # composed from the resolved identity


def test_full_lifecycle(tools):
    t = tools.create("work")
    owner = tools.claim()["owner"]
    assert tools.start(t["id"], owner)["status"] == Status.STARTED
    result = {"verdict": "accepted", "checks": [1, 2, 3]}
    done = tools.complete(
        t["id"], owner, result_ref="pr/1", result=result
    )
    assert done["status"] == Status.COMPLETED
    assert done["result_ref"] == "pr/1"
    assert done["result"] == result
    assert tools.show(t["id"])["result"] == result
    listed = tools.list(status=Status.COMPLETED)[0]
    assert listed["has_result"] is True
    assert "result" not in listed
    assert tools.result(t["id"])["result"] == result


def test_suspended_lifecycle(tools, monkeypatch):
    from agent_dispatch import bridge

    monkeypatch.setattr(
        bridge, "resume_steered_owner", lambda *_args, **_kwargs: True
    )
    t = tools.create("work")
    owner = tools.claim()["owner"]
    tools.start(t["id"], owner)
    parked = tools.suspend(t["id"], owner, "waiting for input")
    assert parked["status"] == Status.SUSPENDED
    resumed = tools.resume(t["id"], owner)
    assert resumed["status"] == Status.STARTED
    assert resumed["resume_woken"] is None
    assert resumed["resume_wake_status"] == "pending"
    tools.suspend(t["id"], owner, "waiting again")
    assert tools.release(t["id"], owner)["status"] == Status.QUEUED


def test_worktree_status_inbox(tools):
    tools.create("for-me", target_worktree="wt-1")
    r = tools.worktree_status()
    assert r["machine"] == "m1"
    assert any(t["title"] == "for-me" for t in r["assigned"])


def test_propose_approve(tools):
    t = tools.create("draft", proposed=True)
    assert t["status"] == Status.PROPOSED
    assert tools.claim() is None  # proposed is not claimable
    assert tools.approve(t["id"])["status"] == Status.QUEUED


def test_payload_spill_and_read(tools):
    big = "x" * 6000
    t = tools.create("big", payload=big)
    assert t["payload_ref"].startswith("blob:")
    assert tools.payload(t["id"])["payload"] == big


def test_worktree_status_without_identity(server_url):
    tools = DispatchTools(
        client_factory=lambda: DispatchClient(server_url),
        identity_resolver=lambda: (None, None),
    )
    assert "error" in tools.worktree_status()


def test_build_server_registers_tools():
    import asyncio

    pytest.importorskip("mcp", reason="requires the optional 'mcp' extra")
    mcp = build_server(
        DispatchTools(client_factory=lambda: None, identity_resolver=lambda: (None, None))
    )
    registered = asyncio.new_event_loop().run_until_complete(mcp.list_tools())
    names = {t.name for t in registered}
    assert {
        "dispatch_create",
        "dispatch_claim",
        "dispatch_complete",
        "dispatch_payload",
        "dispatch_result",
    } <= names
    assert {"dispatch_suspend", "dispatch_resume", "dispatch_release"} <= names
    assert "dispatch_wakes" in names
    assert "dispatch_rearm_spawn" in names
    complete = next(t for t in registered if t.name == "dispatch_complete")
    result_schema = complete.input_schema["properties"]["result"]
    variants = result_schema.get("anyOf", [result_schema])
    assert {variant.get("type") for variant in variants} == {"array", "object"}


def test_rearm_spawn(tools, server_url):
    task = tools.create("work")
    client = DispatchClient(server_url)
    for _ in range(3):
        reservation = client.reserve_spawn(task["id"])["reservation"]
        client.fail_spawn(reservation["key"], detail="down")

    result = tools.rearm_spawn(
        task["id"], permit=True, reason="transport repaired"
    )

    assert result["rearmed"] == 3
