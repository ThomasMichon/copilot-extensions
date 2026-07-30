"""HTTP protocol-version advertisement + client capability gating (dotfiles #632).

The forward-compat half of *version-skew-tolerant-contracts*: the daemon
advertises its HTTP wire-contract version on ``/health``, and a (possibly newer)
client gates a version-introduced capability on that support instead of
blind-sending to an older daemon.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.client import BridgeClient
from agent_bridge.models import ServiceConfig
from agent_bridge.protocol import (
    HTTP_PROTOCOL_MIN_SUPPORTED,
    HTTP_PROTOCOL_VERSION,
    UNVERSIONED,
)


def _app(tmp_path):
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        session_host_enabled=False,
    )
    return create_app(config=cfg, token="test-token")


def test_health_advertises_protocol(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/health").json()
    assert body["protocol_version"] == HTTP_PROTOCOL_VERSION
    assert body["min_protocol_version"] == HTTP_PROTOCOL_MIN_SUPPORTED


def _client(health_body: dict) -> BridgeClient:
    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = lambda: health_body  # type: ignore[method-assign]
    return c


def test_daemon_protocol_reads_advertised_versions():
    c = _client({"status": "ok", "protocol_version": 3, "min_protocol_version": 2})
    assert c.daemon_protocol() == (3, 2)


def test_unversioned_daemon_reports_zero():
    # A daemon predating protocol advertisement omits the fields -> (0, 0), so
    # every versioned-capability check degrades off rather than assuming support.
    c = _client({"status": "ok", "draining": False})
    assert c.daemon_protocol() == (UNVERSIONED, UNVERSIONED)
    assert c.daemon_supports(1) is False


def test_daemon_supports_gates_on_version():
    c = _client({"protocol_version": 2, "min_protocol_version": 1})
    assert c.daemon_supports(1) is True
    assert c.daemon_supports(2) is True
    assert c.daemon_supports(3) is False  # newer client feature, older daemon


def test_malformed_protocol_field_degrades_off():
    c = _client({"protocol_version": "not-an-int"})
    assert c.daemon_protocol() == (UNVERSIONED, UNVERSIONED)


def test_daemon_protocol_memoized():
    calls = {"n": 0}

    def _health():
        calls["n"] += 1
        return {"protocol_version": 1, "min_protocol_version": 1}

    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = _health  # type: ignore[method-assign]
    c.daemon_protocol()
    c.daemon_supports(1)
    c.daemon_supports(1)
    assert calls["n"] == 1  # cached for the client's lifetime
    c.daemon_protocol(refresh=True)
    assert calls["n"] == 2  # explicit refresh re-fetches


# -- Provider-registration version exchange (cross-plugin skew surface) -------


def _register(client: TestClient, body: dict):
    return client.post("/api/v1/providers/codespaces", json=body)


def test_provider_registration_advertises_bridge_protocol(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.headers["Authorization"] = "Bearer test-token"
        resp = _register(c, {
            "agents": [{"name": "cs-x", "spawn_command": ["echo"]}],
            "protocol_version": 1,
        })
    assert resp.status_code == 200
    data = resp.json()
    # The bridge advertises its own HTTP contract version + range so the
    # provider can negotiate capability across skew (dotfiles #632).
    assert data["protocol_version"] == HTTP_PROTOCOL_VERSION
    assert data["min_protocol_version"] == HTTP_PROTOCOL_MIN_SUPPORTED


def test_provider_protocol_version_recorded_and_surfaced(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.headers["Authorization"] = "Bearer test-token"
        _register(c, {
            "agents": [{"name": "cs-y", "spawn_command": ["echo"]}],
            "protocol_version": 7,
        })
        listing = c.get("/api/v1/providers").json()["providers"]
    codespaces = next(p for p in listing if p["name"] == "codespaces")
    assert codespaces["protocol_version"] == 7


def test_provider_without_protocol_version_still_registers(tmp_path):
    # An older provider that predates protocol negotiation omits the field; the
    # bridge records it as unversioned (None) and still registers it (tolerant).
    with TestClient(_app(tmp_path)) as c:
        c.headers["Authorization"] = "Bearer test-token"
        resp = _register(c, {
            "agents": [{"name": "cs-z", "spawn_command": ["echo"]}],
        })
        assert resp.status_code == 200
        assert resp.json()["protocol_version"] == HTTP_PROTOCOL_VERSION
        listing = c.get("/api/v1/providers").json()["providers"]
    codespaces = next(p for p in listing if p["name"] == "codespaces")
    assert codespaces["protocol_version"] is None
