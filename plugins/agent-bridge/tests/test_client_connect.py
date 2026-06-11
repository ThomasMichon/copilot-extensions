"""Tests for the CLI client connect-grace (stage 1 transient retry)."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from agent_bridge.client import BridgeClient


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
        """Persistent refusal past the grace window exits non-zero."""
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.3)

        def always_fail(_req, timeout=None):
            raise urllib.error.URLError("refused")

        with patch(
            "agent_bridge.client.urllib.request.urlopen", side_effect=always_fail
        ):
            with pytest.raises(SystemExit):
                client._request("GET", "/health")

    def test_no_grace_fails_immediately(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok", connect_grace=0.0)
        calls = {"n": 0}

        def always_fail(_req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("refused")

        with patch(
            "agent_bridge.client.urllib.request.urlopen", side_effect=always_fail
        ):
            with pytest.raises(SystemExit):
                client._request("GET", "/health")
        assert calls["n"] == 1
