"""Tests for the dynamic-endpoint bind (dotfiles #694).

Dynamic (OS-assigned ephemeral) bind is now the **default**: a primary daemon
with no pinned port binds ephemeral and advertises it via ``active.json``. A
pinned port (``--port`` or a positive ``config.yaml`` ``port``) binds fixed;
``AGENT_BRIDGE_DYNAMIC_PORT`` forces the decision either way. These cover the two
pure helpers that gate and perform the bind.
"""

from __future__ import annotations

import socket

import agent_bridge.__main__ as m


def test_dynamic_is_default_when_unpinned(monkeypatch):
    # No env override, no --port, config port unset (0) -> dynamic by default.
    monkeypatch.delenv("AGENT_BRIDGE_DYNAMIC_PORT", raising=False)
    assert m._dynamic_bind_requested(cfg_port=0, explicit_port=False) is True


def test_config_pinned_port_binds_fixed(monkeypatch):
    # A positive config port is a pin -> fixed bind (e.g. an existing 9280).
    monkeypatch.delenv("AGENT_BRIDGE_DYNAMIC_PORT", raising=False)
    assert m._dynamic_bind_requested(cfg_port=9280, explicit_port=False) is False


def test_explicit_port_binds_fixed(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_DYNAMIC_PORT", raising=False)
    assert m._dynamic_bind_requested(cfg_port=0, explicit_port=True) is False


def test_env_forces_dynamic_even_when_pinned(monkeypatch):
    for val in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("AGENT_BRIDGE_DYNAMIC_PORT", val)
        assert m._dynamic_bind_requested(cfg_port=9280, explicit_port=True) is True


def test_env_forces_fixed_even_when_unpinned(monkeypatch):
    # Rollback switch: force the legacy fixed bind even with no pin.
    for val in ("0", "false", "off", "no"):
        monkeypatch.setenv("AGENT_BRIDGE_DYNAMIC_PORT", val)
        assert m._dynamic_bind_requested(cfg_port=0, explicit_port=False) is False


def test_empty_env_is_ignored(monkeypatch):
    # An empty value is not a force either way -> fall through to pin logic.
    monkeypatch.setenv("AGENT_BRIDGE_DYNAMIC_PORT", "")
    assert m._dynamic_bind_requested(cfg_port=0, explicit_port=False) is True
    assert m._dynamic_bind_requested(cfg_port=9280, explicit_port=False) is False


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
