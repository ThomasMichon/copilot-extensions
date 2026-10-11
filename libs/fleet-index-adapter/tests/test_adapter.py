"""Tests for fleet_index_adapter.route().

Two tiers, per the routing-foundation architecture's own proof requirement
("mocks cover only local edge cases"):

- A minimal stand-in HTTP backend (``_FakeBackend``) for fast, deterministic
  coverage of every bound and error path.
- A genuine integration test against the real, unmodified
  ``agent_index.server.build_app()`` FastAPI app, served over a real
  OS-assigned TCP port by a real ``uvicorn.Server`` -- not a mocked
  transport -- proving the full request/response contract end-to-end.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest
from fleet_contracts import (
    ConnectorRegistration,
    ContractError,
    RouteRequest,
    SearchParameters,
    ServiceOffer,
    TargetRef,
)

from fleet_index_adapter import BackendUnavailable, route


def registration(adapter="index-v1", operations=("health", "index.search")):
    return ConnectorRegistration(
        "lab", TargetRef("static-ssh", "ssh-a", "worker-a"), "connector-a", 4, 9999999999,
        (ServiceOffer("index", "index-install-a", adapter, operations),),
    )


def search_request(**overrides):
    reg = registration()
    base = dict(
        fleet_id=reg.fleet_id, target=reg.target, connector_id=reg.connector_id,
        generation=reg.generation, service_id="index", installation_id="index-install-a",
        request_id="request-a", operation="index.search",
        parameters=SearchParameters("needle", limit=5, source="docs"),
    )
    base.update(overrides)
    return RouteRequest(**base)


def health_request(**overrides):
    reg = registration()
    base = dict(
        fleet_id=reg.fleet_id, target=reg.target, connector_id=reg.connector_id,
        generation=reg.generation, service_id="index", installation_id="index-install-a",
        request_id="request-b", operation="health", parameters=None,
    )
    base.update(overrides)
    return RouteRequest(**base)


class _FakeBackend(BaseHTTPRequestHandler):
    """A minimal stand-in for agent-index's own HTTP surface."""

    response_body: bytes = b"{}"
    response_status: int = 200
    seen_paths: ClassVar[list[str]] = []  # reset per-test by the fixture

    def do_GET(self) -> None:
        type(self).seen_paths.append(self.path)
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, log_format: str, *args: object) -> None:
        pass  # keep test output quiet


@pytest.fixture
def fake_backend():
    _FakeBackend.seen_paths = []
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
    _set_response(200, {"status": "ok", "plugin": "agent-index"})
    response = route(health_request(), registration().services[0], base_url=base_url)
    assert response.health.available is True
    assert response.operation == "health"
    assert response.request_id == "request-b"


def test_health_route_maps_non_ok_status(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"status": "passive"})
    response = route(health_request(), registration().services[0], base_url=base_url)
    assert response.health.available is False
    assert response.health.detail == "passive"


def test_search_route_maps_existing_hit_shape(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {
        "query": "needle", "available": True,
        "hits": [{
            "id": "chunk-1", "chunk_id": "chunk-1", "score": 0.75,
            "file_path": "docs/example.md", "line_start": 1, "line_end": 3,
            "source": "git:repo", "chunk_type": "markdown", "language": "markdown",
            "content": "hello",
        }],
    })
    response = route(search_request(), registration().services[0], base_url=base_url)
    assert response.operation == "index.search"
    assert len(response.hits) == 1
    assert response.hits[0].chunk_id == "chunk-1"
    assert response.hits[0].score == 0.75


def test_search_route_sends_existing_query_parameters(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"query": "needle", "available": True, "hits": []})
    route(search_request(), registration().services[0], base_url=base_url)
    assert len(_FakeBackend.seen_paths) == 1
    parsed = urlparse(_FakeBackend.seen_paths[0])
    assert parsed.path == "/search"
    params = parse_qs(parsed.query)
    assert params["q"] == ["needle"]
    assert params["limit"] == ["5"]
    assert params["source"] == ["docs"]
    assert "language" not in params
    assert "repo" not in params


def test_search_route_rejects_oversized_or_malformed_hit(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"available": True, "hits": [{"content": "x" * 5000}]})
    with pytest.raises(BackendUnavailable):
        route(search_request(), registration().services[0], base_url=base_url)


def test_search_route_rejects_missing_hit_list(fake_backend):
    _server, base_url = fake_backend
    _set_response(200, {"available": True})
    with pytest.raises(BackendUnavailable):
        route(search_request(), registration().services[0], base_url=base_url)


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
    offer = ServiceOffer("index", "index-install-a", "health-v1", ("health",))
    with pytest.raises(ContractError):
        route(health_request(), offer, base_url=base_url)


def test_route_rejects_operation_not_in_offer(fake_backend):
    _server, base_url = fake_backend
    offer = ServiceOffer("index", "index-install-a", "index-v1", ("health",))
    with pytest.raises(ContractError):
        route(search_request(), offer, base_url=base_url)


# --- Real agent-index integration (see conftest.py for how its package is
# reached without a packaged runtime dependency on it) ---------------------

uvicorn = pytest.importorskip("uvicorn")


@pytest.fixture
def live_agent_index(monkeypatch):
    from agent_index.search import engine as search_engine
    from agent_index.server import build_app

    class FakeEngine:
        def search(self, query: str, **kwargs: object) -> list[SimpleNamespace]:
            return [SimpleNamespace(
                chunk_id="chunk-live", score=0.9, file_path="README.md",
                line_start=1, line_end=2, source="git:repo",
                chunk_type="markdown", language="markdown",
                content="real backend content",
            )]

    monkeypatch.setattr(search_engine, "create_search_engine", lambda: FakeEngine())

    app = build_app()  # passive=False (default) -> promoted, /health reports "ok"
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not getattr(server, "started", False) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start a live agent-index instance in time"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _live_registration() -> ConnectorRegistration:
    return ConnectorRegistration(
        "lab", TargetRef("static-ssh", "ssh-a", "worker-a"), "connector-a", 1, 9999999999,
        (ServiceOffer("index", "index-install-a", "index-v1", ("health", "index.search")),),
    )


def test_real_agent_index_search_round_trip(live_agent_index):
    reg = _live_registration()
    request = RouteRequest(
        reg.fleet_id, reg.target, reg.connector_id, reg.generation, "index", "index-install-a",
        "request-live", "index.search", SearchParameters("needle"),
    )
    response = route(request, reg.services[0], base_url=live_agent_index)
    assert response.operation == "index.search"
    assert len(response.hits) == 1
    assert response.hits[0].chunk_id == "chunk-live"
    assert response.hits[0].content == "real backend content"


def test_real_agent_index_health_round_trip(live_agent_index):
    reg = _live_registration()
    request = RouteRequest(
        reg.fleet_id, reg.target, reg.connector_id, reg.generation, "index", "index-install-a",
        "request-health", "health", None,
    )
    response = route(request, reg.services[0], base_url=live_agent_index)
    assert response.operation == "health"
    assert response.health.available is True
