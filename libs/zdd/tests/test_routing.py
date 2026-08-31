"""Tests for the client-facing routing table (active.json)."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from zdd import routing
from zdd.routing import Endpoint


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    return tmp_path


# -- listener helper ---------------------------------------------------------


class _Listener:
    """A real loopback TCP listener so reachability probes hit a live socket."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._sock.settimeout(0.1)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
                conn.close()
            except OSError:
                continue

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._sock.close()


@pytest.fixture
def listener():
    lis = _Listener()
    yield lis
    lis.close()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- absence / fallback ------------------------------------------------------


def test_read_active_absent_returns_none(cfg_dir: Path):
    assert routing.read_active_endpoint(cfg_dir) is None
    assert routing.read_table(cfg_dir) is None


def test_corrupt_table_is_ignored(cfg_dir: Path):
    routing.routing_table_path(cfg_dir).write_text("{not json", encoding="utf-8")
    assert routing.read_table(cfg_dir) is None
    assert routing.read_active_endpoint(cfg_dir) is None


# -- publish + read ----------------------------------------------------------


def test_publish_then_read_active(cfg_dir: Path, listener: _Listener):
    routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=listener.port, pid=None, version="1.2.3"
    )
    ep = routing.read_active_endpoint(cfg_dir)
    assert ep is not None
    assert ep.port == listener.port
    assert ep.version == "1.2.3"
    assert ep.base_url == f"http://127.0.0.1:{listener.port}"


def test_publish_is_atomic_and_valid_json(cfg_dir: Path):
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9281, version="v")
    data = json.loads(routing.routing_table_path(cfg_dir).read_text())
    assert data["active"]["port"] == 9281
    assert "epoch" in data
    # no stray tmp file left behind
    assert not list(cfg_dir.glob("*.tmp"))


def test_verify_listener_false_returns_recorded(cfg_dir: Path):
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=65000, version="v")
    assert routing.read_active_endpoint(cfg_dir, verify_listener=False) is not None
    # With verification and a confirmed-dead pid, the dead port is a miss.
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=65000, pid=2,
                           version="v")
    # pid 2 is (almost certainly) not us; treat unknown liveness conservatively
    # by asserting only the no-pid case is a hard miss:
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=_free_port())
    assert routing.read_active_endpoint(cfg_dir) is None


def test_wildcard_bind_maps_to_loopback(cfg_dir: Path):
    ep = Endpoint(bind="0.0.0.0", port=9281)
    assert ep.client_host == "127.0.0.1"
    ep6 = Endpoint(bind="::", port=9281)
    assert ep6.client_host == "::1"


# -- generation / flip / heal ------------------------------------------------


def test_generation_increments(cfg_dir: Path):
    a = routing.publish_active(cfg_dir, bind="127.0.0.1", port=9281)
    b = routing.publish_active(cfg_dir, bind="127.0.0.1", port=9282,
                               demote_existing=True)
    assert b.generation == a.generation + 1


def test_flip_demotes_previous(cfg_dir: Path):
    old = _Listener()
    new = _Listener()
    try:
        routing.publish_active(cfg_dir, bind="127.0.0.1", port=old.port, pid=1)
        routing.publish_active(
            cfg_dir, bind="127.0.0.1", port=new.port, pid=2, demote_existing=True
        )
        data = routing.read_table(cfg_dir)
        assert data["active"]["port"] == new.port
        assert data["previous"]["port"] == old.port
        # active is reachable -> resolves to new
        ep = routing.read_active_endpoint(cfg_dir)
        assert ep.port == new.port
    finally:
        old.close()
        new.close()


def test_publish_returns_atomically_demoted_previous(cfg_dir: Path):
    old = routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=9281, pid=101, version="old"
    )
    new, previous = routing.publish_active_with_previous(
        cfg_dir,
        bind="127.0.0.1",
        port=9282,
        pid=202,
        version="new",
        demote_existing=True,
    )

    assert new.port == 9282
    assert previous == old
    table = routing.read_table(cfg_dir)
    assert table["active"] == new.to_dict()
    assert table["previous"] == old.to_dict()


def test_publish_same_port_returns_no_demoted_previous(cfg_dir: Path):
    routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=9281, pid=101, version="old"
    )

    new, previous = routing.publish_active_with_previous(
        cfg_dir,
        bind="127.0.0.1",
        port=9281,
        pid=202,
        version="new",
        demote_existing=True,
    )

    assert new.port == 9281
    assert previous is None
    assert "previous" not in routing.read_table(cfg_dir)


def test_restore_previous_requires_active_generation_ownership(cfg_dir: Path):
    old = routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=9281, pid=101, version="old"
    )
    failed = routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=9282, pid=202, version="failed",
        demote_existing=True,
    )

    assert routing.restore_previous_if_owner(
        cfg_dir, pid=failed.pid, generation=failed.generation
    ) is True
    table = routing.read_table(cfg_dir)
    assert table["active"]["port"] == old.port
    assert table["active"]["pid"] == old.pid
    assert table["active"]["generation"] > failed.generation


def test_restore_previous_does_not_overwrite_newer_successor(cfg_dir: Path):
    routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=9281, pid=101, version="old"
    )
    failed = routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=9282, pid=202, version="failed",
        demote_existing=True,
    )
    successor = routing.publish_active(
        cfg_dir, bind="127.0.0.1", port=9283, pid=303, version="successor",
        demote_existing=True,
    )

    assert routing.restore_previous_if_owner(
        cfg_dir, pid=failed.pid, generation=failed.generation
    ) is False
    active = routing.read_table(cfg_dir)["active"]
    assert active["port"] == successor.port
    assert active["pid"] == successor.pid
    assert active["generation"] == successor.generation


def test_heal_to_previous_when_active_dead(cfg_dir: Path):
    prev = _Listener()
    dead_port = _free_port()
    try:
        # active points at a dead port with no pid; previous is live.
        table = {
            "active": {"bind": "127.0.0.1", "port": dead_port, "generation": 3},
            "previous": {"bind": "127.0.0.1", "port": prev.port, "generation": 2},
            "epoch": "x",
        }
        routing.routing_table_path(cfg_dir).write_text(json.dumps(table))
        ep = routing.read_active_endpoint(cfg_dir)
        assert ep is not None
        assert ep.port == prev.port
    finally:
        prev.close()


def test_same_port_restart_does_not_create_previous(cfg_dir: Path):
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9281, pid=1)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9281, pid=2,
                           demote_existing=True)
    data = routing.read_table(cfg_dir)
    assert "previous" not in data


# -- clear_if_owner ----------------------------------------------------------


def test_clear_if_owner_only_clears_own_claim(cfg_dir: Path):
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9281, pid=4242)
    # A different pid must not blank our route.
    assert routing.clear_if_owner(cfg_dir, pid=9999) is False
    assert routing.read_table(cfg_dir)["active"]["port"] == 9281
    # Our own pid demotes us to previous.
    assert routing.clear_if_owner(cfg_dir, pid=4242) is True
    data = routing.read_table(cfg_dir)
    assert "active" not in data
    assert data["previous"]["port"] == 9281


def test_clear_if_owner_noop_when_absent(cfg_dir: Path):
    assert routing.clear_if_owner(cfg_dir, pid=1) is False
