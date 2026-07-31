"""Tests for the dynamic-endpoint bind plumbing (dotfiles #694).

The daemon binds an OS-assigned ephemeral port only when opt-in via
``AGENT_BRIDGE_DYNAMIC_PORT`` and no explicit ``--port`` pin; the default path
is unchanged (uvicorn binds the fixed ``cfg.port``). These cover the two pure
helpers that gate and perform the dynamic bind.
"""

from __future__ import annotations

import socket

import agent_bridge.__main__ as m


def test_dynamic_off_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_DYNAMIC_PORT", raising=False)
    assert m._dynamic_bind_requested(explicit_port=False) is False


def test_dynamic_on_when_flag_truthy(monkeypatch):
    for val in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("AGENT_BRIDGE_DYNAMIC_PORT", val)
        assert m._dynamic_bind_requested(explicit_port=False) is True


def test_dynamic_off_when_flag_falsey(monkeypatch):
    for val in ("0", "false", "", "off", "no"):
        monkeypatch.setenv("AGENT_BRIDGE_DYNAMIC_PORT", val)
        assert m._dynamic_bind_requested(explicit_port=False) is False


def test_explicit_pin_always_wins(monkeypatch):
    # An explicit --port pin disables dynamic even with the flag set.
    monkeypatch.setenv("AGENT_BRIDGE_DYNAMIC_PORT", "1")
    assert m._dynamic_bind_requested(explicit_port=True) is False


def test_bind_listen_socket_ephemeral_assigns_real_port():
    sock = m._bind_listen_socket("127.0.0.1", 0)
    try:
        host, port = sock.getsockname()[:2]
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
    finally:
        sock.close()


def test_bind_listen_socket_is_a_real_socket():
    sock = m._bind_listen_socket("127.0.0.1", 0)
    try:
        assert isinstance(sock, socket.socket)
        # Listening is possible on the returned bound socket.
        sock.listen(1)
    finally:
        sock.close()
