"""Loopback-trusted bearer auth: the localhost console needs no token."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.auth import (
    BearerAuthMiddleware,
    host_is_loopback,
    loopback_is_trusted,
    request_is_trusted_local,
)


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True),
    ("127.0.0.1:6355", True),
    ("localhost", True),
    ("localhost:45153", True),
    ("[::1]", True),
    ("[::1]:6355", True),
    ("127.5.4.3:1", True),
    ("evil.com", False),          # DNS-rebinding host is not a loopback literal
    ("evil.com:6355", False),
    ("192.168.1.4:6355", False),  # LAN bind still needs the token
    ("", False),
    (None, False),
])
def test_host_is_loopback(host, expected):
    assert host_is_loopback(host) is expected


def test_loopback_trusted_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_REQUIRE_LOOPBACK_AUTH", raising=False)
    assert loopback_is_trusted() is True
    assert request_is_trusted_local({"host": "127.0.0.1:45153"}) is True
    assert request_is_trusted_local({"host": "evil.com"}) is False


def test_loopback_can_be_forced_strict(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_REQUIRE_LOOPBACK_AUTH", "1")
    assert loopback_is_trusted() is False
    assert request_is_trusted_local({"host": "127.0.0.1"}) is False


def _app():
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, token="secret-token")

    @app.get("/api/v1/thing")
    async def thing():  # noqa: ANN202
        return {"ok": True}

    return app


def test_loopback_request_skips_token(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_REQUIRE_LOOPBACK_AUTH", raising=False)
    with TestClient(_app()) as c:
        # No Authorization header, but a loopback Host -> allowed.
        r = c.get("/api/v1/thing", headers={"Host": "127.0.0.1:45153"})
        assert r.status_code == 200 and r.json() == {"ok": True}


def test_non_loopback_request_requires_token():
    with TestClient(_app()) as c:
        r = c.get("/api/v1/thing", headers={"Host": "evil.com"})
        assert r.status_code == 401
        r2 = c.get("/api/v1/thing", headers={"Host": "evil.com", "Authorization": "Bearer secret-token"})
        assert r2.status_code == 200


def test_strict_mode_requires_token_even_on_loopback(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_REQUIRE_LOOPBACK_AUTH", "1")
    with TestClient(_app()) as c:
        r = c.get("/api/v1/thing", headers={"Host": "127.0.0.1"})
        assert r.status_code == 401
