"""Tests for the rendezvous endpoint-advertisement selection helper."""

from __future__ import annotations

from agent_vault.service import advertised_endpoint


def test_posix_prefers_unix_socket():
    assert advertised_endpoint(
        is_windows=False,
        unix_bound=True,
        socket_path="/run/agent-vault-service.sock",
        tcp_bound=True,
        tcp_address="127.0.0.1:19999",
    ) == ("unix", "/run/agent-vault-service.sock")


def test_windows_uses_tcp():
    assert advertised_endpoint(
        is_windows=True,
        unix_bound=False,
        socket_path="/run/x.sock",
        tcp_bound=True,
        tcp_address="127.0.0.1:19999",
    ) == ("tcp", "127.0.0.1:19999")


def test_windows_prefers_named_pipe_over_tcp():
    assert advertised_endpoint(
        is_windows=True,
        unix_bound=False,
        socket_path="/run/x.sock",
        pipe_bound=True,
        pipe_address=r"\\.\pipe\agent-vault",
        tcp_bound=True,
        tcp_address="127.0.0.1:19999",
    ) == ("pipe", r"\\.\pipe\agent-vault")


def test_posix_ignores_pipe_and_uses_unix():
    assert advertised_endpoint(
        is_windows=False,
        unix_bound=True,
        socket_path="/run/agent-vault.sock",
        pipe_bound=True,
        pipe_address=r"\\.\pipe\agent-vault",
        tcp_bound=True,
        tcp_address="127.0.0.1:19999",
    ) == ("unix", "/run/agent-vault.sock")


def test_posix_without_unix_falls_back_to_tcp():
    assert advertised_endpoint(
        is_windows=False,
        unix_bound=False,
        socket_path="/run/x.sock",
        tcp_bound=True,
        tcp_address="127.0.0.1:52731",
    ) == ("tcp", "127.0.0.1:52731")


def test_nothing_bound_returns_none():
    assert (
        advertised_endpoint(
            is_windows=True,
            unix_bound=False,
            socket_path="/run/x.sock",
            tcp_bound=False,
            tcp_address=None,
        )
        is None
    )
