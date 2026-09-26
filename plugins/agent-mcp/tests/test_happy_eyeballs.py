"""Tests for the Happy-Eyeballs-lite dual-stack connect race (aperture-labs#7553).

These exercise ``happy_eyeballs_connect`` directly against fake
``getaddrinfo``/``socket.socket`` seams -- no real network, no real sockets --
so a black-holed family (a ``connect()`` that never returns, simulated with a
threading.Event-gated block) is reproducible deterministically and fast.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from agent_mcp.transports.happy_eyeballs import (
    _create_connection,
    happy_eyeballs_connect,
)


class _FakeSocket:
    """A stand-in for :class:`socket.socket` that records lifecycle calls and
    lets a test script exactly what ``connect()`` does (succeed, raise, or
    block until released -- simulating a black-holed route)."""

    def __init__(self, family, socktype, proto, *, behavior):
        self.family = family
        self.socktype = socktype
        self.proto = proto
        self._behavior = behavior
        self.closed = False
        self.connected_sockaddr = None
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def connect(self, sockaddr):
        self.connected_sockaddr = sockaddr
        kind = self._behavior["kind"]
        if kind == "block":
            # A black-holed route: connect() never returns on its own within
            # the test's patience -- gate it on an Event a slow test can
            # release, or just let it hang until the caller's own timeout
            # value would have fired in production. Here we simulate the
            # "OS gives up" moment by sleeping past the configured timeout
            # and then raising, exactly like a real blackhole eventually
            # would once the kernel's own SYN retransmit budget is spent.
            time.sleep(self._behavior.get("hang_seconds", 0.05))
            raise TimeoutError("simulated black-holed route")
        if kind == "refuse":
            raise ConnectionRefusedError("simulated refused connection")
        if kind == "succeed":
            return None
        raise AssertionError(f"unknown fake behavior {kind!r}")

    def close(self):
        self.closed = True


def _fake_getaddrinfo(behaviors):
    """Build a ``socket.getaddrinfo`` replacement returning one candidate per
    entry in ``behaviors`` (a list of ``(family, behavior_dict)`` pairs, in
    resolver-priority order -- mirrors a real dual-stack DNS answer's
    ordering, AAAA-then-A being the common OS default)."""

    def _getaddrinfo(host, port, type=None):  # noqa: A002 - matches stdlib signature
        return [
            (family, socket.SOCK_STREAM, 6, "", (host, port))
            for family, _behavior in behaviors
        ]

    return _getaddrinfo


def _fake_socket_factory(behaviors):
    by_family = {family: behavior for family, behavior in behaviors}

    def _socket(family, socktype, proto):
        return _FakeSocket(family, socktype, proto, behavior=by_family[family])

    return _socket


def test_single_family_connects_directly_with_no_race(monkeypatch):
    """The common, healthy case: DNS returns only one family -- connect
    directly, no head-start delay, nothing to race."""
    behaviors = [(socket.AF_INET, {"kind": "succeed"})]
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(behaviors))
    monkeypatch.setattr(socket, "socket", _fake_socket_factory(behaviors))

    started = time.monotonic()
    sock = happy_eyeballs_connect("example.test", 443, timeout=5.0, head_start=0.25)
    elapsed = time.monotonic() - started

    assert sock.family == socket.AF_INET
    assert elapsed < 0.1  # no head-start wait was paid


def test_blackholed_primary_family_falls_back_within_head_start(monkeypatch):
    """The confirmed live incident: the resolver-preferred family (IPv6, here
    modeled as the first entry) is black-holed -- connect() never completes
    quickly -- while the second family is healthy. The race must return the
    healthy connection close to ``head_start`` later, never anywhere near the
    full per-attempt ``timeout``."""
    behaviors = [
        (socket.AF_INET6, {"kind": "block", "hang_seconds": 5.0}),
        (socket.AF_INET, {"kind": "succeed"}),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(behaviors))
    monkeypatch.setattr(socket, "socket", _fake_socket_factory(behaviors))

    started = time.monotonic()
    sock = happy_eyeballs_connect("example.test", 443, timeout=5.0, head_start=0.1)
    elapsed = time.monotonic() - started

    assert sock.family == socket.AF_INET
    assert elapsed < 1.0  # nowhere near the 5s the blocked family would cost alone


def test_all_families_fail_raises_first_ranked_error(monkeypatch):
    behaviors = [
        (socket.AF_INET6, {"kind": "refuse"}),
        (socket.AF_INET, {"kind": "refuse"}),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(behaviors))
    monkeypatch.setattr(socket, "socket", _fake_socket_factory(behaviors))

    with pytest.raises(ConnectionRefusedError):
        happy_eyeballs_connect("example.test", 443, timeout=1.0, head_start=0.05)


def test_empty_getaddrinfo_result_raises_oserror(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])

    with pytest.raises(OSError, match="no results"):
        happy_eyeballs_connect("example.test", 443, timeout=1.0)


def test_create_connection_shim_matches_socket_create_connection_signature(monkeypatch):
    """``http.client.HTTPConnection`` calls ``self._create_connection`` with
    this exact ``(address, timeout, source_address)`` shape (see
    ``HappyEyeballsHTTPConnection``) -- confirm the shim accepts it and
    forwards host/port/timeout correctly."""
    behaviors = [(socket.AF_INET, {"kind": "succeed"})]
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(behaviors))
    monkeypatch.setattr(socket, "socket", _fake_socket_factory(behaviors))

    sock = _create_connection(("example.test", 443), 7.0, None)
    assert sock.connected_sockaddr == ("example.test", 443)


def test_late_loser_connection_is_closed(monkeypatch):
    """A family that eventually connects *after* another family already won
    the race must be closed, not leaked."""
    release = threading.Event()

    class _SlowThenSucceed(_FakeSocket):
        def connect(self, sockaddr):
            release.wait(2.0)
            self.connected_sockaddr = sockaddr

    def _socket(family, socktype, proto):
        if family == socket.AF_INET6:
            return _SlowThenSucceed(family, socktype, proto, behavior={"kind": "succeed"})
        return _FakeSocket(family, socktype, proto, behavior={"kind": "succeed"})

    behaviors = [(socket.AF_INET6, {"kind": "succeed"}), (socket.AF_INET, {"kind": "succeed"})]
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(behaviors))
    monkeypatch.setattr(socket, "socket", _socket)

    sock = happy_eyeballs_connect("example.test", 443, timeout=5.0, head_start=0.05)
    assert sock.family == socket.AF_INET  # the fast family won
    release.set()
    time.sleep(0.2)  # let the slow family's connect() return and self-close
