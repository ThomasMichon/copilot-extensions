"""Tests for the acp-connect stdio<->ACP-WebSocket relay client (URL parsing)."""

from __future__ import annotations

import pytest

from agent_bridge.acp_connect import _parse_ws_url


def test_parse_ws_basic():
    host, port, target, tls = _parse_ws_url("ws://127.0.0.1:9281/acp/SPO.Core")
    assert host == "127.0.0.1"
    assert port == 9281
    assert target == "/acp/SPO.Core"
    assert tls is False


def test_parse_ws_default_port():
    host, port, target, tls = _parse_ws_url("ws://localhost/acp/foo")
    assert (host, port, target, tls) == ("localhost", 80, "/acp/foo", False)


def test_parse_wss_default_port_and_tls():
    host, port, target, tls = _parse_ws_url("wss://example.com/acp/bar")
    assert host == "example.com"
    assert port == 443
    assert target == "/acp/bar"
    assert tls is True


def test_parse_ws_preserves_query():
    _, _, target, _ = _parse_ws_url("ws://h:1/acp/session/abc?x=1")
    assert target == "/acp/session/abc?x=1"


def test_parse_ws_empty_path_defaults_root():
    _, _, target, _ = _parse_ws_url("ws://h:1")
    assert target == "/"


@pytest.mark.parametrize("url", ["http://h/acp/x", "https://h/acp/x", "tcp://h", "h/acp/x"])
def test_parse_rejects_non_ws_scheme(url):
    with pytest.raises(ValueError):
        _parse_ws_url(url)
