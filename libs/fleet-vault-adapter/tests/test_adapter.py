"""Tests for fleet_vault_adapter.route().

A minimal stand-in HTTP backend (``_FakeBackend``) gives fast, deterministic
coverage of every bound and error path -- the same tier
``fleet-index-adapter``'s suite uses for its own stand-in-backend tests. This
package has no real, publicly-documented backend to integrate-test against
(unlike agent-index's own `/search`) -- see the README's "Relationship to an
existing Gateway" section -- so there is no live-integration tier here.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from fleet_contracts import (
    ConnectorRegistration,
    ContractError,
    CredentialParameters,
    RouteRequest,
    ServiceOffer,
    TargetRef,
)

from fleet_vault_adapter import BackendUnavailable, route


def registration(adapter="vault-v1", operations=("health", "credential.get")):
    return ConnectorRegistration(
        "lab", TargetRef("static-ssh", "ssh-a", "worker-a"), "connector-a", 4, 9999999999,
        (ServiceOffer("vault", "vault-install-a", adapter, operations),),
    )


def credential_request(**overrides):
    reg = registration()
    base = {
        "fleet_id": reg.fleet_id, "target": reg.target, "connector_id": reg.connector_id,
        "generation": reg.generation, "service_id": "vault", "installation_id": "vault-install-a",
        "request_id": "request-a", "operation": "credential.get",
        "parameters": CredentialParameters("get-azure-token", resource="https://management.azure.com/"),
    }
    base.update(overrides)
    return RouteRequest(**base)


def health_request(**overrides):
    reg = registration()
    base = {
        "fleet_id": reg.fleet_id, "target": reg.target, "connector_id": reg.connector_id,
        "generation": reg.generation, "service_id": "vault", "installation_id": "vault-install-a",
        "request_id": "request-b", "operation": "health", "parameters": None,
    }
    base.update(overrides)
    return RouteRequest(**base)


class _FakeBackend(BaseHTTPRequestHandler):
    """A minimal stand-in for a Gateway-fronted vault backend."""

    response_body: bytes = b"{}"
    response_status: int = 200
    seen_requests: ClassVar[list[tuple[str, bytes]]] = []

    def do_GET(self) -> None:
        type(self).seen_requests.append((self.path, b""))
        self._respond()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).seen_requests.append((self.path, body))
        self._respond()

    def _respond(self) -> None:
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, log_format: str, *args: object) -> None:
        pass  # keep test output quiet


@pytest.fixture
def fake_backend():
    _FakeBackend.seen_requests = []
    _FakeBackend.response_status = 200
    _FakeBackend.response_body = b"{}"
    server = HTTPServer(("127.0.0.1", 0), _FakeBackend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _set_response(status: int, body: object) -> None:
    _FakeBackend.response_status = status
    _FakeBackend.response_body = json.dumps(body).encode()


def test_health_route_maps_ok_status(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"status": "ok"})
    response = route(health_request(), registration().services[0], base_url=base_url)
    assert response.health.available is True
    assert response.operation == "health"


def test_health_route_maps_non_ok_status(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"status": "draining"})
    response = route(health_request(), registration().services[0], base_url=base_url)
    assert response.health.available is False
    assert response.health.detail == "draining"


def test_credential_route_maps_available_credential(fake_backend):
    _server, base_url = fake_backend
    _set_response(
        200, {"available": True, "fields": {"token": "secret", "expires_on": "1700000000"}},
    )
    response = route(credential_request(), registration().services[0], base_url=base_url)
    assert response.operation == "credential.get"
    assert response.credential.available is True
    assert dict(response.credential.fields) == {"token": "secret", "expires_on": "1700000000"}


def test_credential_route_maps_unavailable_credential_as_an_ordinary_response(fake_backend):
    _server, base_url = fake_backend
    _set_response(404, {"available": False, "reason": "no_cached_token"})
    response = route(credential_request(), registration().services[0], base_url=base_url)
    assert response.credential.available is False
    assert response.credential.reason == "no_cached_token"
    assert response.credential.fields == ()


def test_credential_route_sends_action_and_present_fields_only(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"available": False, "reason": "no_cached_token"})
    request = credential_request(
        parameters=CredentialParameters("get-github-token", host="github.com"),
    )
    route(request, registration().services[0], base_url=base_url)
    assert len(_FakeBackend.seen_requests) == 1
    path, raw_body = _FakeBackend.seen_requests[0]
    assert path == "/relay"
    body = json.loads(raw_body)
    assert body == {"action": "get-github-token", "fields": {"host": "github.com"}}


def test_credential_route_rejects_oversized_or_malformed_credential(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"available": True, "fields": {"token": "x" * 9000}})
    with pytest.raises(BackendUnavailable):
        route(credential_request(), registration().services[0], base_url=base_url)


def test_credential_route_rejects_missing_available_field(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"fields": {"token": "x"}})
    with pytest.raises(BackendUnavailable):
        route(credential_request(), registration().services[0], base_url=base_url)


def test_credential_route_rejects_available_with_no_fields(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"available": True, "fields": {}})
    with pytest.raises(BackendUnavailable):
        route(credential_request(), registration().services[0], base_url=base_url)


def test_backend_unreachable_raises_backend_unavailable():
    with pytest.raises(BackendUnavailable):
        route(
            health_request(), registration().services[0],
            base_url="http://127.0.0.1:1", timeout=0.5,
        )


def test_malformed_json_response_raises_backend_unavailable(fake_backend):
    _server, base_url = fake_backend
    _FakeBackend.response_status = 200
    _FakeBackend.response_body = b"not json"
    with pytest.raises(BackendUnavailable):
        route(health_request(), registration().services[0], base_url=base_url)


def test_route_rejects_offer_not_matching_adapter_kind(fake_backend):
    _server, base_url = fake_backend
    offer = ServiceOffer("vault", "vault-install-a", "health-v1", ("health",))
    with pytest.raises(ContractError):
        route(health_request(), offer, base_url=base_url)


def test_route_rejects_operation_not_in_offer(fake_backend):
    _server, base_url = fake_backend
    offer = ServiceOffer("vault", "vault-install-a", "vault-v1", ("health",))
    with pytest.raises(ContractError):
        route(credential_request(), offer, base_url=base_url)
