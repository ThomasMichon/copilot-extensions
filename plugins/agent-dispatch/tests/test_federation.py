"""Tests for the federation rendezvous interface + the coordinator-hosted
backend (:mod:`agent_dispatch.federation`)."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.federation import CoordinatorRendezvous, Rendezvous
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def rv(tmp_path):
    """A CoordinatorRendezvous wired to a real coordinator on an ephemeral port
    (a sync httpx client can't drive an ASGI transport, so we run a server)."""
    import uvicorn

    app = create_app(TaskQueue(tmp_path / "tasks.db"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    client = DispatchClient(url)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            client.health()
            break
        except Exception:  # server still starting up
            time.sleep(0.05)
    else:
        client.close()
        raise RuntimeError("coordinator did not start")

    yield CoordinatorRendezvous(client)

    client.close()
    server.should_exit = True
    thread.join(timeout=5)


# -- Protocol conformance ----------------------------------------------------


def test_backend_satisfies_the_protocol(rv):
    # runtime_checkable Protocol: the backend structurally is a Rendezvous.
    assert isinstance(rv, Rendezvous)


# -- awareness plane ---------------------------------------------------------


def test_register_then_discover_peers(rv):
    entry = rv.register("host-a", capabilities=["logger"])
    assert entry["instance"] == "host-a"
    assert entry["role"] == "peer"
    peers = rv.discover_peers()
    assert [p["instance"] for p in peers] == ["host-a"]


def test_discover_peers_filters_by_role(rv):
    rv.register("peer-1", role="peer")
    rv.register("sat-1", role="satellite")
    assert [p["instance"] for p in rv.discover_peers(role="satellite")] == ["sat-1"]


def test_heartbeat_keeps_entry_and_updates_status(rv):
    rv.register("host-a")
    entry = rv.heartbeat("host-a", status={"wt-a": {"turn_state": "active"}})
    assert entry["status"] == {"wt-a": {"turn_state": "active"}}


def test_heartbeat_unknown_raises(rv):
    from agent_dispatch.client import DispatchError

    with pytest.raises(DispatchError) as exc:
        rv.heartbeat("never-registered")
    assert exc.value.args[0].startswith("HTTP 404")


def test_deregister_returns_bool(rv):
    rv.register("host-a")
    assert rv.deregister("host-a") is True
    assert rv.deregister("host-a") is False
    assert rv.discover_peers() == []


# -- claim plane -------------------------------------------------------------


def test_discover_coordinator_none_when_absent(rv):
    rv.register("peer-1", role="peer")
    assert rv.discover_coordinator() is None


def test_discover_coordinator_picks_highest_epoch(rv):
    rv.register("c-old", role="coordinator", epoch=2)
    rv.register("c-new", role="coordinator", epoch=5)
    coord = rv.discover_coordinator()
    assert coord["instance"] == "c-new"
    assert coord["epoch"] == 5


def test_register_carries_epoch_and_distinct_machine(rv):
    entry = rv.register(
        "wheatley/wt-boards", role="coordinator", epoch=4, machine="wheatley"
    )
    assert entry["epoch"] == 4
    assert entry["machine"] == "wheatley"
    assert entry["instance"] == "wheatley/wt-boards"
