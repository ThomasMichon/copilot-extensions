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
    done = tools.complete(t["id"], owner, result_ref="pr/1")
    assert done["status"] == Status.COMPLETED
    assert done["result_ref"] == "pr/1"


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
    names = {t.name for t in asyncio.new_event_loop().run_until_complete(mcp.list_tools())}
    assert {"dispatch_create", "dispatch_claim", "dispatch_complete", "dispatch_payload"} <= names
