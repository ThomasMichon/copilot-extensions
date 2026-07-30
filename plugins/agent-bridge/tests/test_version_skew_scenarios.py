"""End-to-end **version-skew scenarios** (dotfiles #632, Stage 3).

Where `test_wire_compat.py` statically guards the tolerant-reader invariant and
`test_protocol_negotiation.py` unit-tests the negotiation primitives, this module
exercises the two skew directions as **named, readable scenarios** against real
routes — the regression anchor for "the suite is correct *while* skewed":

1. **newer client → older daemon** — a client gates a version-introduced feature
   on the daemon's advertised support and degrades gracefully when the daemon is
   older (or predates protocol advertisement) instead of blind-sending.
2. **newer client → older daemon (tolerant reader)** — a newer client sends a
   request carrying fields an older daemon does not know; the daemon **ignores**
   them (200, not 422) rather than rejecting the whole request.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.client import BridgeClient
from agent_bridge.models import ServiceConfig


def _app(tmp_path):
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
        session_host_enabled=False,
    )
    return create_app(config=cfg, token="test-token")


# -- Direction 1: newer client gates on an older daemon's advertised support ---


def test_newer_client_degrades_against_older_daemon():
    # An "older daemon" that predates protocol advertisement: /health omits the
    # protocol fields. A client that needs a hypothetical protocol >= 2 feature
    # must gate off and take a fallback, not blind-send.
    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = lambda: {"status": "ok", "draining": False}  # type: ignore[method-assign]

    needed = 2
    if c.daemon_supports(needed):
        used_feature = True
    else:
        used_feature = False  # graceful fallback path

    assert used_feature is False
    assert c.daemon_protocol() == (0, 0)  # unversioned -> gate off


def test_newer_client_uses_feature_when_daemon_new_enough():
    c = BridgeClient("http://127.0.0.1:0", "t")
    c.health = lambda: {  # type: ignore[method-assign]
        "status": "ok", "protocol_version": 5, "min_protocol_version": 1,
    }
    assert c.daemon_supports(2) is True  # daemon new enough -> use the feature


# -- Direction 2: older daemon tolerates a newer client's unknown fields -------


def test_daemon_ignores_unknown_request_fields(tmp_path):
    # A newer client adds a field an older daemon's model does not define. The
    # tolerant-reader invariant (no wire model forbids extras) means the daemon
    # ignores it and still succeeds -- it must NOT 422 the whole request.
    with TestClient(_app(tmp_path)) as c:
        c.headers["Authorization"] = "Bearer test-token"
        resp = c.post(
            "/api/v1/providers/codespaces",
            json={
                "agents": [{"name": "cs-x", "spawn_command": ["echo"]}],
                "protocol_version": 99,
                "a_field_from_a_future_client": {"nested": "value"},
            },
        )
    assert resp.status_code == 200  # unknown field ignored, not rejected


def test_older_caller_without_protocol_still_negotiates(tmp_path):
    # An older provider client omits protocol_version entirely; registration still
    # succeeds and the daemon still advertises its own version back.
    from agent_bridge.protocol import HTTP_PROTOCOL_VERSION

    with TestClient(_app(tmp_path)) as c:
        c.headers["Authorization"] = "Bearer test-token"
        resp = c.post(
            "/api/v1/providers/codespaces",
            json={"agents": [{"name": "cs-y", "spawn_command": ["echo"]}]},
        )
    assert resp.status_code == 200
    assert resp.json()["protocol_version"] == HTTP_PROTOCOL_VERSION
