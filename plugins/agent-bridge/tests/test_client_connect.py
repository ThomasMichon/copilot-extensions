"""Tests for the CLI client connect-grace (stage 1 transient retry)."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from agent_bridge.client import BridgeClient, BridgeConnectionError


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


class TestConnectGrace:
    def test_retries_then_succeeds(self) -> None:
        """A transient connection refusal within the grace window is retried."""
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=2.0)

        calls = {"n": 0}

        def flaky(_req, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("connection refused")
            return _FakeResp({"ok": True})

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=flaky):
            result = client._request("GET", "/health")

        assert result == {"ok": True}
        assert calls["n"] == 3  # two failures, then success

    def test_gives_up_after_grace(self) -> None:
        """Persistent refusal past the grace window raises BridgeConnectionError.

        #23: it must NOT sys.exit -- a SystemExit (BaseException) tunnels
        through the streaming engine's `except Exception` reconnect guards and
        kills a live dispatch. Raising a catchable Exception lets the engine
        reconnect, and one-shot commands surface it via main()'s top-level guard.
        """
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.3)

        def always_fail(_req, timeout=None):
            raise urllib.error.URLError("refused")

        with patch(
            "agent_bridge.client.urllib.request.urlopen", side_effect=always_fail
        ):
            with pytest.raises(BridgeConnectionError) as ei:
                client._request("GET", "/health")
        assert "127.0.0.1:0" in str(ei.value)

    def test_no_grace_fails_immediately(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.0)
        calls = {"n": 0}

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        with patch(
            "agent_bridge.client.urllib.request.urlopen", side_effect=always_fail
        ):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")
        assert calls["n"] == 1


class TestReresolveOnRejection:
    """follow-the-cutover: on a connection rejection, follow a dynamic-port cutover by
    re-resolving the routing table (listener-verified) instead of hammering the
    remembered-but-dead port."""

    def test_reresolves_to_new_port_and_succeeds(self) -> None:
        # Old port refuses; the re-resolver reports the daemon moved to a new
        # port; the request switches to it and succeeds -- no grace needed.
        new_base = "http://127.0.0.1:47000"
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=0.0,
            reresolve=lambda: new_base,
        )
        seen: list[str] = []

        def by_port(req, timeout=None):
            seen.append(req.full_url)
            if req.full_url.startswith("http://127.0.0.1:57585"):
                raise urllib.error.URLError("refused")
            return _FakeResp({"ok": True})

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=by_port):
            result = client._request("GET", "/health")

        assert result == {"ok": True}
        assert client._base == new_base  # switched permanently for later calls
        assert seen[0].startswith("http://127.0.0.1:57585")  # tried old first
        assert seen[-1].startswith("http://127.0.0.1:47000")  # then the new one

    def test_reresolve_same_port_falls_through_to_grace(self) -> None:
        # The re-resolver returns the SAME port (no move) -> no switch; degrade
        # through the (zero) grace window to a clean BridgeConnectionError.
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=0.0,
            reresolve=lambda: "http://127.0.0.1:57585",
        )

        def always_fail(_req, timeout=None):
            raise urllib.error.URLError("refused")

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")

    def test_reresolve_none_falls_through_to_grace(self) -> None:
        # The re-resolver finds no live endpoint (dead advertised port healed to
        # None) -> no switch; clean failure after grace, not an infinite loop.
        calls = {"n": 0}
        client = BridgeClient(
            "http://127.0.0.1:57585",
            "tok",
            connect_grace=0.0,
            reresolve=lambda: None,
        )

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")
        # One initial attempt + at most the re-resolve attempt -- bounded, no loop.
        assert calls["n"] <= 2

    def test_no_reresolver_is_unchanged_behavior(self) -> None:
        # A directly-constructed client (no reresolve) behaves exactly as before.
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.0)

        def always_fail(_req, timeout=None):
            raise urllib.error.URLError("refused")

        with patch("agent_bridge.client.urllib.request.urlopen", side_effect=always_fail):
            with pytest.raises(BridgeConnectionError):
                client._request("GET", "/health")
