"""Real resolver-process lifetime and original-host TLS verification."""

import socket
import time
import errno
from types import SimpleNamespace

import pytest
from fleet_manager import gateway_server, gateway_transport
from fleet_manager.gateway_client import post
from fleet_manager.gateway_contract import GatewayError
from fleet_manager.gateway_server import build_server
from gateway_fixtures import Fixtures, query
from test_gateway_runtime import running, scoped_backend, wait_gateway_slots


def connect_fixture(monkeypatch, *, first_result=errno.EINPROGRESS, expire=False,
                    clock_step=0, connect_cost=0, ready_after=None):
    clock = [100.0]
    connections = []
    started = []
    selected = []

    class Connection:
        def __init__(self, family):
            self.family = family
            self.closed = False
            self.timeout = None
            connections.append(self)

        def setblocking(self, value):
            assert value is False

        def settimeout(self, value):
            self.timeout = value

        def connect_ex(self, address):
            started.append((self.family, address, clock[0]))
            clock[0] += connect_cost
            if len(started) == 1:
                if first_result is None:
                    raise OSError("synthetic unreachable address")
                return first_result
            return errno.EINPROGRESS if expire else 0

        def getsockopt(self, level, option):
            assert (level, option) == (socket.SOL_SOCKET, socket.SO_ERROR)
            return 0

        def close(self):
            assert self not in selected
            self.closed = True

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def register(self, connection, events):
            selected.append(connection)

        def unregister(self, connection):
            selected.remove(connection)

        def get_map(self):
            return {id(connection): SimpleNamespace(fileobj=connection)
                    for connection in selected}

        def select(self, wait):
            assert 0 <= wait <= 101 - clock[0]
            if ready_after is not None:
                clock[0] += ready_after
                return [(SimpleNamespace(fileobj=selected[-1]),
                         gateway_transport.selectors.EVENT_WRITE)]
            clock[0] += wait
            return []

    def monotonic():
        clock[0] += clock_step
        return clock[0]

    monkeypatch.setattr(gateway_transport, "socket", SimpleNamespace(
        socket=lambda family, *args: Connection(family),
        AF_INET=socket.AF_INET, AF_INET6=socket.AF_INET6,
        SOCK_STREAM=socket.SOCK_STREAM, IPPROTO_TCP=socket.IPPROTO_TCP,
        SOL_SOCKET=socket.SOL_SOCKET, SO_ERROR=socket.SO_ERROR,
    ))
    monkeypatch.setattr(gateway_transport, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(gateway_transport.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(gateway_transport, "_addresses", lambda *args: [
        (socket.AF_INET6, ("::1", 443, 0, 0)),
        (socket.AF_INET6, ("::2", 443, 0, 0)),
        (socket.AF_INET, ("127.0.0.1", 443)),
    ])
    connection = gateway_transport.DeadlineHTTPSConnection(
        "localhost", 443, context=None, deadline=101,
    )
    return connection, connections, started, clock


@pytest.mark.parametrize("first_result,delay", [
    (errno.EINPROGRESS, gateway_transport.ADDRESS_DELAY),
    (10035, gateway_transport.ADDRESS_DELAY),
    (errno.EHOSTUNREACH, 0),
    (None, 0),
])
def test_stalled_first_family_cannot_spend_alternate_family_deadline(
        monkeypatch, first_result, delay):
    owner, connections, started, clock = connect_fixture(
        monkeypatch, first_result=first_result,
    )
    winner = owner._connect_address(("localhost", 443))
    try:
        assert [item[0] for item in started] == [socket.AF_INET6, socket.AF_INET]
        assert clock[0] == 100 + delay
        assert connections[0].closed and not winner.closed
        assert winner.timeout == 101 - clock[0]
        assert owner.host == "localhost"
    finally:
        winner.close()


def test_all_pending_addresses_closed_at_original_deadline(monkeypatch):
    owner, connections, started, clock = connect_fixture(monkeypatch, expire=True)
    with pytest.raises(GatewayError, match="transport-unavailable"):
        owner._connect_address(("localhost", 443))
    assert clock[0] == 101
    assert len(started) == 3
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize("families", [
    (socket.AF_INET6, socket.AF_INET6, socket.AF_INET),
    (socket.AF_INET, socket.AF_INET, socket.AF_INET6),
    (socket.AF_INET6, socket.AF_INET, socket.AF_INET, socket.AF_INET),
    (socket.AF_INET, socket.AF_INET, socket.AF_INET),
])
def test_address_interleaving_preserves_first_family_and_unequal_tails(monkeypatch, families):
    owner, connections, started, _ = connect_fixture(monkeypatch, expire=True)
    resolved = [
        (family, (f"::a{index}", 443, 0, 0) if family == socket.AF_INET6
         else (f"127.0.0.{index + 1}", 443))
        for index, family in enumerate(families)
    ]
    monkeypatch.setattr(gateway_transport, "_addresses", lambda *args: resolved)
    primary = [item for item in resolved if item[0] == families[0]]
    alternate = [item for item in resolved if item[0] != families[0]]
    expected = []
    while primary or alternate:
        if primary:
            expected.append(primary.pop(0))
        if alternate:
            expected.append(alternate.pop(0))
    with pytest.raises(GatewayError, match="transport-unavailable"):
        owner._connect_address(("localhost", 443))
    assert [(family, target) for family, target, _ in started] == expected
    assert all(connection.closed for connection in connections)


def test_wait_uses_one_current_clock_sample_and_unregisters_before_close(monkeypatch):
    owner, connections, _, _ = connect_fixture(
        monkeypatch, expire=True, clock_step=0.02,
    )
    with pytest.raises(GatewayError, match="transport-unavailable"):
        owner._connect_address(("localhost", 443))
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize("mode,elapsed,accepted", [
    ("immediate", 0.4, True), ("immediate", 1, False),
    ("ready", 0.1, True), ("ready", 1, False),
])
def test_connection_winner_rechecks_current_deadline_after_connect_or_select(
        monkeypatch, mode, elapsed, accepted):
    owner, connections, _, clock = connect_fixture(
        monkeypatch, first_result=0 if mode == "immediate" else errno.EINPROGRESS,
        connect_cost=elapsed if mode == "immediate" else 0,
        ready_after=elapsed if mode == "ready" else None,
    )
    if accepted:
        winner = owner._connect_address(("localhost", 443))
        try:
            assert winner.timeout == pytest.approx(101 - clock[0])
            assert winner.timeout < 1 and not winner.closed
        finally:
            winner.close()
    else:
        with pytest.raises(GatewayError, match="transport-unavailable"):
            owner._connect_address(("localhost", 443))
        assert all(connection.closed for connection in connections)


def test_named_backend_resolves_and_keeps_tls_hostname(tmp_path):
    fixture = Fixtures(tmp_path)
    retrieved = []
    with running(scoped_backend(fixture, retrieved)) as backend:
        fixture.value["backends"][0]["endpoint"] = f"https://localhost:{backend.server_port}"
        with running(build_server(fixture.config, fixture.signer)) as gateway:
            client = fixture.client("alice", gateway.server_port)
            client.connect()
            assert client.query(query())["result"]["hits"][0]["content"] == "permitted"
    assert len(retrieved) == 1


def test_resolved_address_does_not_replace_certificate_hostname(tmp_path, monkeypatch):
    fixture = Fixtures(tmp_path)
    monkeypatch.setattr(gateway_transport, "_addresses",
                        lambda host, port, deadline: [(socket.AF_INET, ("127.0.0.1", port))])
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        with pytest.raises(GatewayError, match="transport-unavailable"):
            post(f"https://not-in-certificate.invalid:{gateway.server_port}",
                 "/v1/challenge", {}, str(fixture.root / "tls.pem"))


def test_stalled_system_resolver_reaped_worker_released_and_shutdown_bounded(tmp_path, monkeypatch):
    fixture = Fixtures(tmp_path)
    fixture.value["backends"][0]["endpoint"] = "https://localhost:443"
    monkeypatch.setattr(gateway_server, "REQUEST_TIMEOUT", 1)
    processes = []
    original = gateway_transport.Popen

    def stalled_resolver(argv, **kwargs):
        # A real owned child blocks inside getaddrinfo, not a mocked timeout.
        argv = list(argv)
        argv[3] = ("import socket,time; "
                   "socket.getaddrinfo = lambda *a, **k: time.sleep(60);\n" + argv[3])
        child = original(argv, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(gateway_transport, "Popen", stalled_resolver)
    started = time.monotonic()
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        client = fixture.client("alice", gateway.server_port)
        client.connect()
        for _ in range(2):
            with pytest.raises(GatewayError, match="transport-unavailable"):
                client.query(query())
            wait_gateway_slots(gateway, 16)
            assert processes[-1].poll() is not None
            assert fixture.client("alice", gateway.server_port).connect()["state"] == "ADMITTED"
    assert len(processes) == 2
    assert all(process.poll() is not None for process in processes)
    assert time.monotonic() - started < 4
