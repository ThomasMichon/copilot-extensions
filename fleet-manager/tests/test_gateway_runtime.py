"""Real TLS/JWT/JWE tests; backend/provider doubles are explicit scoped fixtures."""

import copy
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler

import pytest
from fleet_manager.gateway_auth import Signer
from fleet_manager.gateway_client import (
    Discovery,
    GatewayClient,
    consume_credential,
    post,
)
from fleet_manager.gateway_contract import GatewayError, encode, parse
from fleet_manager.gateway_controller import (
    Controller,
    RelayCredentialProvider,
    RelayPolicy,
)
from fleet_manager.gateway_index import ScopedIndexBackend
from fleet_manager.gateway_server import build_server
from fleet_manager.planning import digest
from gateway_fixtures import (
    BoundedTLSFixtureServer,
    Fixtures,
    SyntheticScopedProvider,
    credential_request,
    public,
    query,
)
from jwcrypto import jwk


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(6)
        assert not thread.is_alive()


@pytest.fixture
def fixture(tmp_path):
    return Fixtures(tmp_path)


def controller(fixture, client):
    return Controller(client, SyntheticScopedProvider(),
                      Signer(fixture.proofs["controller"], "proof", "controller", "controller"),
                      fleet="fleet-a", generation=1, lease_seconds=15)


def controller_keys(fixture):
    return {"keys": [fixture.config.enrollments[3].receipt_jwk]}


def scoped_backend(fixture, retrieved, *, slow_response=None, result_signer=None, requests=None):
    """Only this prefiltered callback touches the two-family/two-corpus fixture store."""
    rows = [
        {"source": "source-a", "corpus": "corpus-a", "content": "permitted"},
        {"source": "source-b", "corpus": "corpus-a", "content": "other-source"},
        {"source": "source-b", "corpus": "corpus-b", "content": "other-corpus"},
    ]

    def retrieve(scope, parameters):
        # The filter is applied to the retrieval candidates, not returned hits.
        candidates = [r for r in rows if r["corpus"] == scope.corpus and r["source"] in scope.sources]
        retrieved.append((scope, candidates))
        return {"hits": candidates[:parameters["limit"]]}

    api = ScopedIndexBackend(fixture.config.backends[0],
                             Signer(fixture.backend_key, "backend", "backend-issuer", "backend-a"),
                             gateway_keys=fixture.gateway_keys, gateway_issuer="fixture-issuer",
                             gateway_subject="gateway-install", fleet="fleet-a", retrieve=retrieve)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            if requests is not None:
                requests.append(self.path)
            try:
                body = parse(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/v1/index/admit":
                    result = api.admit(body)
                elif self.path == "/v1/index/query":
                    if result_signer is not None:
                        api.signer = result_signer
                    result = api.execute(body)
                else:
                    raise GatewayError("unsupported-route")
                raw = encode(result)
                self.send_response(200)
            except GatewayError:
                raw = b'{"state":"DENIED"}'
                self.send_response(403)
            if slow_response == "headers" and self.path == "/v1/index/query":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                self.wfile.flush()
            else:
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
            if slow_response and self.path == "/v1/index/query":
                for byte in raw:
                    try:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                    except OSError:
                        break
                    time.sleep(0.03)
                return
            self.wfile.write(raw)

    import ssl
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(fixture.value["tls"]["certificate"], fixture.value["tls"]["key"])
    server = BoundedTLSFixtureServer(("127.0.0.1", 0), Handler, tls)
    return server


def test_real_scoped_tls_query_and_denied_inventory_paths(fixture):
    retrieved = []
    with running(scoped_backend(fixture, retrieved)) as backend:
        fixture.value["backends"][0]["endpoint"] = f"https://127.0.0.1:{backend.server_port}"
        with running(build_server(fixture.config, fixture.signer)) as gateway:
            alice = fixture.client("alice", gateway.server_port)
            assert alice.connect()["supported"] == ["index.search"]
            result = alice.query(query())
            assert result["result"]["hits"] == [{"source": "source-a", "corpus": "corpus-a", "content": "permitted"}]
            assert len(retrieved) == 1
            bob = fixture.client("bob", gateway.server_port)
            bob.connect()
            for client, value in ((alice, query("source-b")), (alice, query(corpus="corpus-b")),
                                  (bob, query()), (alice, query("source-a/commit/guess"))):
                with pytest.raises(GatewayError):
                    client.query(value)
            for operation in ("index.similar", "index.clusters", "index.status", "reindex", "drain", "deploy"):
                with pytest.raises(GatewayError, match="unsupported-operation"):
                    alice.query(query(operation=operation))
            assert len(retrieved) == 1


def test_legacy_backend_explicitly_unsupported_no_transport(fixture):
    fixture.value["backends"][0]["contract"] = "index-v1"
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        client = fixture.client("alice", gateway.server_port)
        assert client.connect()["supported"] == []
        with pytest.raises(GatewayError, match="backend-unsupported"):
            client.query(query())


@pytest.mark.parametrize("revocation", ["admission-key", "admission-subject", "result-key"])
def test_revoked_backend_admission_and_result_receipts_are_refused(fixture, revocation, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    result_signer = None
    if revocation == "result-key":
        key = Ed25519PrivateKey.generate()
        fixture.value["backends"][0]["jwks"]["keys"].append(public(key, "revoked-result"))
        result_signer = Signer(key, "revoked-result", "backend-issuer", "backend-a")
        fixture.value["revoked"] = ["revoked-result"]
    else:
        fixture.value["revoked"] = ["backend" if revocation == "admission-key" else "backend-a"]
    retrieved, requests, delegated = [], [], []
    real_sign = Signer.sign

    def sign(self, claims, *args, **kwargs):
        if claims.get("contract") == "fleet-index-scoped-v1":
            delegated.append(claims)
        return real_sign(self, claims, *args, **kwargs)

    monkeypatch.setattr(Signer, "sign", sign)
    with running(scoped_backend(fixture, retrieved, result_signer=result_signer, requests=requests)) as backend:
        fixture.value["backends"][0]["endpoint"] = f"https://127.0.0.1:{backend.server_port}"
        with running(build_server(fixture.config, fixture.signer)) as gateway:
            client = fixture.client("alice", gateway.server_port)
            connected = client.connect()
            if revocation == "admission-subject":
                assert connected["supported"] == []
            with pytest.raises(GatewayError):
                client.query(query())
    assert len(retrieved) == (1 if revocation == "result-key" else 0)
    assert len(requests) == {"admission-key": 1, "admission-subject": 0, "result-key": 2}[revocation]
    if revocation == "admission-subject":
        assert delegated == []


def test_real_subscription_sealed_reply_and_ack(fixture, capsys):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))
        provider.start()
        value = credential_request()
        assert worker.request_credential(value)["state"] == "PENDING"
        with pytest.raises(GatewayError, match="duplicate-request"):
            worker.request_credential(value)
        assert provider.step() == "READY"
        result = worker.credential_result(value["request_id"], digest(value))
        assert "SYNTHETIC-SECRET" not in json.dumps(result)
        material = consume_credential(worker, value, result, recipient_key=fixture.recipient,
                                      controller_keys=controller_keys(fixture), controller="controller",
                                      controller_generation=1)
        assert material.token == "SYNTHETIC-SECRET-NEVER-PRODUCTION"
        assert "SYNTHETIC" not in repr(material)
        assert worker.credential_result(value["request_id"], digest(value))["state"] == "ACKED"
        with pytest.raises(GatewayError, match="ack-uncertain"):
            consume_credential(worker, value, result, recipient_key=fixture.recipient,
                               controller_keys=controller_keys(fixture), controller="controller",
                               controller_generation=1)
        provider.close()
    captured = capsys.readouterr()
    assert "SYNTHETIC" not in captured.out + captured.err


@pytest.mark.parametrize("field,value", [("source", "source-b"), ("account", "foreign"),
                                       ("audience", "query-a"), ("scopes", ["write"]),
                                       ("recipient", "bob"), ("action", "fill"), ("job", "other-job")])
def test_credential_worker_binding_denials(fixture, field, value):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        request = credential_request()
        request["parameters"][field] = value
        with pytest.raises(GatewayError):
            worker.request_credential(request)
        query_client = fixture.client("alice", gateway.server_port)
        query_client.connect()
        with pytest.raises(GatewayError):
            query_client.request_credential(credential_request())


def test_wrong_recipient_reply_replay_disconnect_and_unknown(fixture):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        provider.step()
        result = worker.credential_result(value["request_id"], digest(value))
        for changes in ({"controller_generation": 2}, {"controller": "other"},
                        {"request_digest": "wrong"}, {"recipient_jkt": "wrong"}):
            altered = copy.deepcopy(result)
            altered["binding"].update(changes)
            with pytest.raises(GatewayError):
                consume_credential(worker, value, altered, recipient_key=fixture.recipient,
                                   controller_keys=controller_keys(fixture), controller="controller",
                                   controller_generation=1)
        with pytest.raises(GatewayError):
            consume_credential(worker, value, result, recipient_key=jwk.JWK.generate(kty="RSA", size=2048),
                               controller_keys=controller_keys(fixture), controller="controller",
                               controller_generation=1)
        with pytest.raises(GatewayError, match="reply-out-of-order"):
            provider.client.reply(provider.subscription, "worker", value["request_id"], result["receipt"])
        assert worker.credential_result(value["request_id"], digest(value))["state"] == "UNKNOWN"
        provider.close()
        assert worker.credential_result(value["request_id"], digest(value))["state"] == "UNKNOWN"
        with pytest.raises(GatewayError, match="controller-unavailable"):
            worker.request_credential(credential_request("next"))


def test_gateway_restart_has_no_recovery_or_native_fallback(fixture):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        before = worker.epoch
        # Replace only the fixture's resident state; old deliveries/epochs cannot succeed.
        from fleet_manager.gateway_server import Gateway
        gateway.gateway = Gateway(fixture.config, fixture.signer, origin=gateway.gateway.origin)
        with pytest.raises(GatewayError, match="remote-denied"):
            worker.credential_result("unknown", "digest")
        worker.connect()
        assert worker.epoch != before
        assert worker.credential_result("unknown", "digest")["state"] == "UNKNOWN"


def test_relay_provider_requires_explicit_custodian_permission_verifier():
    calls = []

    class SyntheticRelay:
        def supports(self, action, fields):
            return action == "get-azure-token"
        async def resolve(self, action, fields, *, timeout):
            calls.append((action, fields))
            return "SYNTHETIC-BROAD-TOKEN"

    policy = RelayPolicy("source-a", "account-a", "ingestion-a", ("read",), "get-azure-token",
                         {"resource": "ingestion-a"})
    provider = RelayCredentialProvider(SyntheticRelay(), (policy,), lambda raw, policy: None)
    import asyncio
    assert asyncio.run(provider.acquire(credential_request()["parameters"], timeout=1)) is None
    assert calls == [("get-azure-token", {"resource": "ingestion-a"})]
    with pytest.raises(GatewayError):
        RelayCredentialProvider(SyntheticRelay(), (RelayPolicy(
            "s", "a", "u", ("read",), "get", {"url": "arbitrary"}),), lambda raw, p: None)


def test_real_transport_rejects_anonymous_and_tls_wrong_ca(fixture, tmp_path):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        origin = f"https://127.0.0.1:{gateway.server_port}"
        with pytest.raises(GatewayError):
            post(origin, "/v1/session", {"body": {}}, str(fixture.root / "tls.pem"))
        other = Fixtures(tmp_path / "other")
        with pytest.raises(GatewayError, match="transport-unavailable"):
            post(origin, "/v1/session", {}, str(other.root / "tls.pem"))


def test_direct_discovery_normalizes_trailing_slash_for_real_proofs(fixture):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        origin = f"https://127.0.0.1:{gateway.server_port}"
        discovery = Discovery(origin + "/", str(fixture.root / "tls.pem"),
                              "fixture-issuer", "gateway-a", "gateway-install", fixture.gateway_keys)
        assert discovery.endpoint == origin
        client = GatewayClient(discovery, "alice", fixture.identity_proofs["alice"], lambda: fixture.access("alice"))
        assert client.connect()["state"] == "ADMITTED"


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_upstream_drip_uses_whole_request_deadline_and_releases_worker(fixture, monkeypatch, phase):
    from fleet_manager import gateway_server

    monkeypatch.setattr(gateway_server, "REQUEST_TIMEOUT", 1)
    retrieved = []
    with running(scoped_backend(fixture, retrieved, slow_response=phase)) as backend:
        fixture.value["backends"][0]["endpoint"] = f"https://127.0.0.1:{backend.server_port}"
        started = time.monotonic()
        with running(build_server(fixture.config, fixture.signer)) as gateway:
            client = fixture.client("alice", gateway.server_port)
            client.connect()
            with pytest.raises(GatewayError, match="transport-unavailable"):
                client.query(query())
            assert len(retrieved) == 1
            wait_gateway_slots(gateway, 16)
            assert fixture.client("alice", gateway.server_port).connect()["state"] == "ADMITTED"
        assert time.monotonic() - started < 3


def test_real_transport_ack_loss_never_returns_plaintext(fixture):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        provider.step()
        result = worker.credential_result(value["request_id"], digest(value))
    # Actual socket is now closed, not a mocked acknowledgment response.
    with pytest.raises(GatewayError, match="ack-uncertain"):
        consume_credential(worker, value, result, recipient_key=fixture.recipient,
                           controller_keys=controller_keys(fixture), controller="controller",
                           controller_generation=1)


def test_credential_expiring_during_real_ack_never_returns_material(fixture, monkeypatch):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        provider.step()
        result = worker.credential_result(value["request_id"], digest(value))
        real_ack = worker.acknowledge
        acknowledgements = []
        expired_time = time.time() + 120
        with monkeypatch.context() as clock:
            def delayed_ack(request_id, request_digest):
                ack = real_ack(request_id, request_digest)
                acknowledgements.append(ack["state"])
                clock.setattr(time, "time", lambda: expired_time)
                return ack

            clock.setattr(worker, "acknowledge", delayed_ack)
            with pytest.raises(GatewayError, match="credential-expired"):
                consume_credential(worker, value, result, recipient_key=fixture.recipient,
                                   controller_keys=controller_keys(fixture), controller="controller",
                                   controller_generation=1)
        assert acknowledgements == ["ACKED"]
        assert worker.credential_result(value["request_id"], digest(value))["state"] == "ACKED"
        provider.close()


def test_dispatched_controller_disconnect_is_unknown(fixture):
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        worker = fixture.client("worker", gateway.server_port)
        worker.connect()
        provider = controller(fixture, fixture.client("controller", gateway.server_port))
        provider.start()
        value = credential_request()
        worker.request_credential(value)
        assert provider.client.poll(provider.subscription)["state"] == "DISPATCHED"
        provider.close()
        assert worker.credential_result(value["request_id"], digest(value))["state"] == "UNKNOWN"


def test_fixture_half_open_tls_does_not_block_owned_teardown(fixture):
    import socket
    raw = None
    start = time.monotonic()
    try:
        with running(scoped_backend(fixture, [])) as server:
            raw = socket.create_connection(("127.0.0.1", server.server_port), timeout=1)
            time.sleep(0.1)
        assert time.monotonic() - start < 5
    finally:
        if raw is not None:
            raw.close()


def wait_gateway_slots(server, expected):
    deadline = time.monotonic() + 2
    while server._slots._value != expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server._slots._value == expected


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_slow_drip_request_releases_slot_and_bounds_shutdown(fixture, monkeypatch, phase):
    import socket
    import ssl
    from fleet_manager import gateway_server

    monkeypatch.setattr(gateway_server, "REQUEST_TIMEOUT", 0.5)
    tls = ssl.create_default_context(cafile=str(fixture.root / "tls.pem"))
    stop = threading.Event()
    writes = []
    thread = None
    connection = None
    started = time.monotonic()
    try:
        with running(build_server(fixture.config, fixture.signer)) as gateway:
            raw = socket.create_connection(("127.0.0.1", gateway.server_port), timeout=1)
            connection = tls.wrap_socket(raw, server_hostname="127.0.0.1")
            prefix = b"POST /v1/challenge HTTP/1.1\r\n"
            if phase == "headers":
                prefix += b"X-Slow: "
            else:
                prefix += b"Content-Type: application/json\r\nContent-Length: 1024\r\n\r\n"
            connection.sendall(prefix)

            def drip():
                while not stop.wait(0.03):
                    try:
                        connection.sendall(b" ")
                        writes.append(True)
                    except OSError:
                        return

            thread = threading.Thread(target=drip)
            thread.start()
            wait_gateway_slots(gateway, 15)
            wait_gateway_slots(gateway, 16)
            assert len(writes) >= 3
            assert fixture.client("alice", gateway.server_port).connect()["state"] == "ADMITTED"
            # A second live slow peer must not keep orderly close waiting indefinitely.
            thread.join(2)
            assert not thread.is_alive()
            connection.close()
            raw = socket.create_connection(("127.0.0.1", gateway.server_port), timeout=1)
            connection = tls.wrap_socket(raw, server_hostname="127.0.0.1")
            connection.sendall(prefix)
            thread = threading.Thread(target=drip)
            thread.start()
            wait_gateway_slots(gateway, 15)
        assert time.monotonic() - started < 3
    finally:
        stop.set()
        if connection is not None:
            connection.close()
        if thread is not None:
            thread.join(2)
            assert not thread.is_alive()


def test_actual_gateway_half_open_tls_does_not_block_healthy_admission(fixture):
    import socket
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        raw = socket.create_connection(("127.0.0.1", gateway.server_port), timeout=1)
        try:
            wait_gateway_slots(gateway, 15)
            client = fixture.client("alice", gateway.server_port)
            started = time.monotonic()
            assert client.connect()["state"] == "ADMITTED"
            assert time.monotonic() - started < 3
            raw.settimeout(0.1)
            with pytest.raises(socket.timeout):
                raw.recv(1)
        finally:
            raw.close()
        wait_gateway_slots(gateway, 16)


def test_actual_gateway_handshakes_are_slot_bounded(fixture):
    import socket
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        probes = []
        try:
            for expected in range(15, -1, -1):
                probes.append(socket.create_connection(("127.0.0.1", gateway.server_port), timeout=1))
                wait_gateway_slots(gateway, expected)
            with socket.create_connection(("127.0.0.1", gateway.server_port), timeout=1) as overflow:
                assert overflow.recv(1) == b""
            assert gateway._slots._value == 0
        finally:
            for probe in probes:
                probe.close()
        wait_gateway_slots(gateway, 16)
        assert fixture.client("alice", gateway.server_port).connect()["state"] == "ADMITTED"


def test_actual_gateway_ipv6_verified_tls_and_authentication(fixture):
    import socket
    if not socket.has_ipv6:
        pytest.skip("native IPv6 unavailable")
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        pytest.skip("native IPv6 loopback unavailable")
    fixture.value["tls"]["host"] = "::1"
    with running(build_server(fixture.config, fixture.signer)) as gateway:
        assert gateway.address_family == socket.AF_INET6
        discovery = Discovery(f"https://[::1]:{gateway.server_port}", str(fixture.root / "tls.pem"),
                              "fixture-issuer", "gateway-a", "gateway-install", fixture.gateway_keys)
        client = GatewayClient(discovery, "alice", fixture.identity_proofs["alice"], lambda: fixture.access("alice"))
        assert client.connect()["state"] == "ADMITTED"
