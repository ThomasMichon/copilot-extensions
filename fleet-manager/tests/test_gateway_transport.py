"""Real resolver-process lifetime and original-host TLS verification."""

import socket
import time

import pytest
from fleet_manager import gateway_server, gateway_transport
from fleet_manager.gateway_client import post
from fleet_manager.gateway_contract import GatewayError
from fleet_manager.gateway_server import build_server
from gateway_fixtures import Fixtures, query
from test_gateway_runtime import running, scoped_backend, wait_gateway_slots


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
