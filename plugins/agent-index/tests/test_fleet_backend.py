"""Real cold-store authorization and TLS proofs; not live fleet offload."""

from __future__ import annotations

import copy
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from fleet_backend_fixtures import BackendFixture
from fleet_manager.gateway_auth import verify
from fleet_manager.gateway_client import post
from fleet_manager.gateway_contract import GatewayError

from agent_index.fleet_backend import build_fleet_app, fleet_ingress
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, validate_source_store


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    return BackendFixture(tmp_path)


def receipt(backend, response):
    return verify(
        response["receipt"], backend.backend_keys, "index-issuer", "gateway-cell",
    )


def app_client(backend):
    return TestClient(build_fleet_app(backend.policy, backend.native), base_url="https://fixture")


def execute(backend, client, body=None):
    body = backend.body() if body is None else body
    admission = client.post("/v1/index/admit", json=body)
    assert admission.status_code == 200
    admitted = receipt(backend, admission.json())
    return client.post("/v1/index/query", json=body | {"admission": admitted["admission"]})


@pytest.mark.parametrize("mode", ["vector", "fts", "hybrid"])
def test_native_source_prefilter_and_no_legacy_id_partition(backend, monkeypatch, mode):
    assert backend.store.ensure_fts_index()
    before = backend.store._source_domain.receipt()

    def legacy_partition(*args, **kwargs):
        pytest.fail("Gateway retrieval entered legacy vector ID partitions")

    monkeypatch.setattr(backend.vector_table, "search", legacy_partition)
    hits = backend.engine.search_authorized(
        "needle", families=("git:alpha",), limit=10, mode=mode,
    )
    assert {hit.source for hit in hits} == {"git:alpha", "git:alpha:commits"}
    assert all("forbidden" not in hit.content for hit in hits)
    assert backend.engine.search_authorized("needle", families=(), mode=mode) == []
    assert backend.engine.search_authorized(
        "needle", families=("git:missing",), mode=mode,
    ) == []
    assert backend.store._source_domain.receipt() == before


def test_source_alias_and_commit_family_with_real_tls(backend):
    assert backend.store.ensure_fts_index()
    with fleet_ingress(backend.policy, backend.native):
        body = backend.body()
        admitted = receipt(backend, post(
            backend.origin, "/v1/index/admit", body, str(backend.root / "cert.pem"),
        ))
        response = post(
            backend.origin, "/v1/index/query", body | {"admission": admitted["admission"]},
            str(backend.root / "cert.pem"),
        )
        result = receipt(backend, response)
        assert result["scope"]["sources"] == ["docs"]
        assert {hit["source"] for hit in result["result"]["hits"]} == {"docs"}
        assert {hit["stored_source"] for hit in result["result"]["hits"]} == {
            "git:alpha", "git:alpha:commits",
        }


@pytest.mark.parametrize("sources,selector", [
    (("docs",), "other"), ((), None), (("not-offered",), None),
])
def test_denied_scope_never_creates_engine_or_reads_store(backend, sources, selector):
    client = app_client(backend)
    assert client.post(
        "/v1/index/admit", json=backend.body(sources, selector=selector),
    ).status_code == 403
    assert backend.factory_calls == []


@pytest.mark.parametrize("changes", [
    {"generation": 2}, {"installation_id": "other"}, {"service_id": "other"},
    {"operation": "index.similar"}, {"operation": "index.clusters"},
    {"operation": "index.status"}, {"operation": "reindex"},
])
def test_offer_and_operation_boundaries(backend, changes):
    client = app_client(backend)
    assert client.post(
        "/v1/index/admit", json=backend.body(changes=changes),
    ).status_code == 403
    assert backend.factory_calls == []


@pytest.mark.parametrize(
    "change", ["signature", "expired", "issuer", "audience", "fleet", "corpus"],
)
def test_real_signature_and_binding_denial_before_retrieval(backend, change):
    body = backend.body()
    claims = jwt.decode(body["grant"], options={"verify_signature": False})
    key = backend.gateway_key
    if change == "signature":
        key = backend.backend_key
    elif change == "expired":
        claims.update(
            iat=int(time.time()) - 30, nbf=int(time.time()) - 30,
            exp=int(time.time()) - 1,
        )
    elif change in ("issuer", "audience"):
        claims["iss" if change == "issuer" else "aud"] = "other"
    elif change == "fleet":
        claims["fleet"] = "other"
    else:
        claims["scope"]["corpus"] = "other"
    body["grant"] = jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "gateway"})
    client = app_client(backend)
    assert client.post("/v1/index/admit", json=body).status_code == 403
    assert backend.factory_calls == []


def test_admission_one_use_restart_and_content_binding(backend):
    assert backend.store.ensure_fts_index()
    client = app_client(backend)
    body = backend.body(selector="docs")
    admitted = receipt(backend, client.post("/v1/index/admit", json=body).json())
    selected = body | {"admission": admitted["admission"]}
    fresh = app_client(backend)
    assert fresh.post("/v1/index/query", json=selected).status_code == 403
    assert backend.factory_calls == []
    tampered = copy.deepcopy(selected)
    tampered["request"]["parameters"]["q"] = "altered after signing"
    assert client.post("/v1/index/query", json=tampered).status_code == 403
    assert backend.factory_calls == []
    assert client.post("/v1/index/query", json=selected).status_code == 200
    assert client.post("/v1/index/query", json=selected).status_code == 403
    assert len(backend.factory_calls) == 1


def test_empty_declared_family_and_no_public_legacy_routes(backend):
    assert backend.store.ensure_fts_index()
    client = app_client(backend)
    response = execute(backend, client, backend.body(("empty",), selector="empty"))
    assert response.status_code == 200
    assert receipt(backend, response.json())["result"]["hits"] == []
    for route in ("/search", "/similar", "/clusters", "/status", "/reindex", "/drain", "/docs"):
        assert client.get(route).status_code == 404
        assert client.post(route).status_code == 404
    with pytest.raises(GatewayError):
        post(backend.origin, "/v1/index/query", backend.body(), str(backend.root / "cert.pem"))


def test_other_principal_scope_cannot_retrieve_first_family(backend):
    assert backend.store.ensure_fts_index()
    client = app_client(backend)
    body = backend.body(("other",), selector="other")
    claims = jwt.decode(body["grant"], options={"verify_signature": False})
    claims["scope"]["principal"] = "bob"
    body["grant"] = jwt.encode(
        claims, backend.gateway_key, algorithm="EdDSA", headers={"kid": "gateway"},
    )
    response = execute(backend, client, body)
    assert response.status_code == 200
    result = receipt(backend, response.json())
    assert result["scope"]["principal"] == "bob"
    assert {hit["stored_source"] for hit in result["result"]["hits"]} == {"git:beta"}
    assert {hit["source"] for hit in result["result"]["hits"]} == {"other"}


@pytest.mark.parametrize("change", ["undeclared", "trust", "generation-expired", "signer"])
def test_explicit_policy_validation_and_expired_offer(backend, change):
    if change == "undeclared":
        backend.value["source_bindings"]["docs"]["declared_source"] = "ambient-source"
    elif change == "trust":
        backend.value["gateway_keys"] = {"keys": []}
    elif change == "generation-expired":
        backend.value["backend"]["expires_at"] = int(time.time()) - 1
    else:
        backend.value["signing_kid"] = "wrong"
    backend.save()
    if change == "generation-expired":
        client = app_client(backend)
        assert client.post("/v1/index/admit", json=backend.body()).status_code == 403
        assert backend.factory_calls == []
    else:
        with pytest.raises(GatewayError):
            build_fleet_app(backend.policy, backend.native)


def test_native_passive_and_draining_cannot_bypass_admission(backend):
    client = app_client(backend)
    backend.native.state.promoted = False
    assert execute(backend, client).status_code == 403
    assert backend.factory_calls == []
    backend.native.state.promoted = True
    backend.native.state.drain_gate.set_draining(True)
    assert execute(backend, client).status_code == 503
    assert backend.factory_calls == []


def test_dirty_domain_refuses_http_retrieval_until_owner_validation(backend):
    assert backend.store.ensure_fts_index()
    client = app_client(backend)
    with pytest.raises(RuntimeError, match="fixture interruption"):
        with backend.store._source_domain.write(backend.store.content_store.db):
            raise RuntimeError("fixture interruption before effect")
    response = execute(backend, client)
    assert response.status_code == 503
    assert "hits" not in response.json()
    validate_source_store(backend.store)
    assert execute(backend, client).status_code == 200


def test_unselected_layout_never_falls_back_to_legacy(backend, monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT")
    with pytest.raises(SourceLayoutError):
        backend.engine.search_authorized("needle", families=("git:alpha",))
    assert execute(backend, app_client(backend)).status_code == 503


def test_insecure_app_mount_is_denied_without_store_access(backend):
    client = TestClient(build_fleet_app(backend.policy, backend.native))
    assert client.post("/v1/index/admit", json=backend.body()).status_code == 403
    assert backend.factory_calls == []


@pytest.mark.parametrize("phase", ["initial", "keep-alive"])
def test_partial_tls_headers_release_capacity_and_allow_authorized_request(
    backend, monkeypatch, phase,
):
    import socket
    import ssl
    from http.client import HTTPResponse

    from fleet_manager.gateway_contract import encode

    from agent_index import fleet_http

    monkeypatch.setattr(fleet_http, "INITIAL_REQUEST_TIMEOUT", 2)
    protocols = []
    original = fleet_http.FleetHTTPProtocol.connection_made

    def connected(self, transport):
        original(self, transport)
        protocols.append(self)

    monkeypatch.setattr(fleet_http.FleetHTTPProtocol, "connection_made", connected)
    tls = ssl.create_default_context(cafile=str(backend.root / "cert.pem"))
    peers = []
    assert backend.store.ensure_fts_index()
    started = time.monotonic()
    try:
        with fleet_ingress(backend.policy, backend.native):
            for i in range(32):
                raw = socket.create_connection(
                    (backend.policy.host, backend.policy.port), timeout=1,
                )
                peer = tls.wrap_socket(raw, server_hostname=backend.policy.host)
                peers.append(peer)
                if phase == "keep-alive" and i == 0:
                    body = encode(backend.body())
                    peer.sendall(
                        b"POST /v1/index/admit HTTP/1.1\r\nHost: fixture\r\n"
                        b"Content-Type: application/json\r\nContent-Length: "
                        + str(len(body)).encode() + b"\r\n\r\n" + body,
                    )
                    response = HTTPResponse(peer)
                    response.begin()
                    assert response.status == 200
                    response.read()
                    response.close()
                peer.sendall(b"POST /v1/index/admit HTTP/1.1\r\nX-Incomplete: ")
            deadline = time.monotonic() + 1
            while len(protocols) < 32 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(protocols) == 32
            assert all(not protocol.transport.is_closing() for protocol in protocols)
            with pytest.raises(GatewayError, match="transport-unavailable"):
                post(
                    backend.origin, "/v1/index/admit", backend.body(),
                    str(backend.root / "cert.pem"),
                )
            assert backend.factory_calls == []
            blocked = list(protocols[:32])
            deadline = time.monotonic() + 3
            while (
                any(not protocol.transport.is_closing() for protocol in blocked)
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert all(protocol.transport.is_closing() for protocol in blocked)
            assert all(protocol._header_deadline is None for protocol in blocked)
            body = backend.body()
            admitted = receipt(backend, post(
                backend.origin, "/v1/index/admit", body, str(backend.root / "cert.pem"),
            ))
            response = post(
                backend.origin, "/v1/index/query", body | {"admission": admitted["admission"]},
                str(backend.root / "cert.pem"),
            )
            assert receipt(backend, response)["result"]["hits"]
        assert time.monotonic() - started < 6
        assert all(protocol.transport.is_closing() for protocol in protocols)
    finally:
        for peer in peers:
            peer.close()


@pytest.mark.parametrize(
    "phase", ["deadline", "shutdown", "plaintext", "disconnect", "partial-hello"],
)
def test_raw_pre_tls_admission_is_bounded_and_owned(backend, monkeypatch, phase):
    import socket

    from agent_index import fleet_http, fleet_tls

    monkeypatch.setattr(fleet_http, "INITIAL_REQUEST_TIMEOUT", 2)
    protocols = []
    original = fleet_tls.TLSAdmission.protocol

    def constructed(self, **kwargs):
        protocol = original(self, **kwargs)
        protocols.append(protocol)
        return protocol

    monkeypatch.setattr(fleet_tls.TLSAdmission, "protocol", constructed)
    peers = []
    assert backend.store.ensure_fts_index()
    started = time.monotonic()
    try:
        with fleet_ingress(backend.policy, backend.native):
            count = 32 if phase == "deadline" else 1
            for _ in range(count):
                peers.append(socket.create_connection(
                    (backend.policy.host, backend.policy.port), timeout=1,
                ))
            deadline = time.monotonic() + 1
            while (
                len(protocols) < count or any(protocol.task is None for protocol in protocols)
            ) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(protocols) == count
            owner = protocols[0].admission
            assert len(owner.connections) == count
            assert all(protocol.http is None for protocol in protocols)
            assert all(protocol.task is not None for protocol in protocols)
            if phase == "deadline":
                overflow = socket.create_connection(
                    (backend.policy.host, backend.policy.port), timeout=1,
                )
                peers.append(overflow)
                assert overflow.recv(1) == b""
                assert len(owner.connections) == 32
                assert sum(protocol.task is not None for protocol in protocols) == 32
            elif phase == "plaintext":
                peers[0].sendall(b"GET /status HTTP/1.1\r\nHost: fixture\r\n\r\n")
            elif phase == "disconnect":
                peers[0].close()
            elif phase == "partial-hello":
                peers[0].sendall(b"\x16\x03\x03\x00")
            if phase != "shutdown":
                deadline = time.monotonic() + 3
                while owner.connections and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert not owner.connections
                held = list(protocols)
                for peer in peers:
                    if peer.fileno() >= 0:
                        try:
                            assert peer.recv(1) == b""
                        except ConnectionResetError:
                            pass
                assert all(
                    protocol.deadline is None and protocol.http is None for protocol in held
                )
                body = backend.body()
                admitted = receipt(backend, post(
                    backend.origin, "/v1/index/admit", body, str(backend.root / "cert.pem"),
                ))
                result = receipt(backend, post(
                    backend.origin, "/v1/index/query", body | {"admission": admitted["admission"]},
                    str(backend.root / "cert.pem"),
                ))
                assert result["result"]["hits"]
        assert time.monotonic() - started < 6
        assert not owner.connections
        assert all(protocol.task is None or protocol.task.done() for protocol in protocols)
        assert all(protocol.deadline is None for protocol in protocols)
    finally:
        for peer in peers:
            peer.close()


def test_tls_and_first_headers_share_absolute_deadline(backend, monkeypatch):
    import socket
    import ssl

    from agent_index import fleet_http, fleet_tls

    monkeypatch.setattr(fleet_http, "INITIAL_REQUEST_TIMEOUT", 2)
    protocols = []
    original = fleet_tls.TLSAdmission.protocol

    def constructed(self, **kwargs):
        protocol = original(self, **kwargs)
        protocols.append(protocol)
        return protocol

    monkeypatch.setattr(fleet_tls.TLSAdmission, "protocol", constructed)
    tls = ssl.create_default_context(cafile=str(backend.root / "cert.pem"))
    with fleet_ingress(backend.policy, backend.native):
        started = time.monotonic()
        with socket.create_connection(
            (backend.policy.host, backend.policy.port), timeout=1,
        ) as raw:
            deadline = time.monotonic() + 1
            while (not protocols or protocols[0].task is None) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert protocols and protocols[0].task is not None
            time.sleep(1.2)
            with tls.wrap_socket(raw, server_hostname=backend.policy.host) as peer:
                peer.sendall(b"POST /v1/index/admit HTTP/1.1\r\nX-Incomplete: ")
                assert peer.recv(1) == b""
        assert time.monotonic() - started < 2.7
        assert protocols[0].http is not None
        assert protocols[0].http_lost
        assert protocols[0].deadline is None
        assert backend.factory_calls == []
