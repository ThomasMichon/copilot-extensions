"""Tests for the rendezvous endpoint-advertisement selection helper."""

from __future__ import annotations

from agent_vault import rendezvous
from agent_vault.service import advertised_endpoint


def test_clear_endpoint_removes_owned_record(tmp_path):
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)

    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)

    assert rendezvous.read_endpoint(tmp_path) is None


def test_clear_endpoint_preserves_successor_owned_record(tmp_path):
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)

    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)

    endpoint = rendezvous.read_endpoint(tmp_path)
    assert endpoint is not None
    assert endpoint.pid == 2002
    assert endpoint.tcp_host_port == ("127.0.0.1", 41002)


def test_posix_prefers_unix_socket():
    assert advertised_endpoint(
        is_windows=False,
        unix_bound=True,
        socket_path="/run/agent-vault-service.sock",
        tcp_bound=True,
        tcp_address="127.0.0.1:19999",
    ) == ("unix", "/run/agent-vault-service.sock", [])


def test_windows_uses_tcp():
    assert advertised_endpoint(
        is_windows=True,
        unix_bound=False,
        socket_path="/run/x.sock",
        tcp_bound=True,
        tcp_address="127.0.0.1:19999",
    ) == ("tcp", "127.0.0.1:19999", [])


def test_windows_prefers_named_pipe_and_carries_tcp_alt():
    # #3426: the pipe stays primary, but the TCP endpoint rides along as an
    # alternate so a WSL guest that can't open the pipe still finds a port.
    assert advertised_endpoint(
        is_windows=True,
        unix_bound=False,
        socket_path="/run/x.sock",
        pipe_bound=True,
        pipe_address=r"\\.\pipe\agent-vault",
        tcp_bound=True,
        tcp_address="127.0.0.1:52731",
    ) == ("pipe", r"\\.\pipe\agent-vault", [("tcp", "127.0.0.1:52731")])


def test_windows_pipe_without_tcp_has_no_alt():
    assert advertised_endpoint(
        is_windows=True,
        unix_bound=False,
        socket_path="/run/x.sock",
        pipe_bound=True,
        pipe_address=r"\\.\pipe\agent-vault",
        tcp_bound=False,
        tcp_address=None,
    ) == ("pipe", r"\\.\pipe\agent-vault", [])


def test_posix_ignores_pipe_and_uses_unix():
    # POSIX is unchanged: no TCP alt (WSL->Linux is not a multi-machine system path).
    assert advertised_endpoint(
        is_windows=False,
        unix_bound=True,
        socket_path="/run/agent-vault.sock",
        pipe_bound=True,
        pipe_address=r"\\.\pipe\agent-vault",
        tcp_bound=True,
        tcp_address="127.0.0.1:19999",
    ) == ("unix", "/run/agent-vault.sock", [])


def test_posix_without_unix_falls_back_to_tcp():
    assert advertised_endpoint(
        is_windows=False,
        unix_bound=False,
        socket_path="/run/x.sock",
        tcp_bound=True,
        tcp_address="127.0.0.1:52731",
    ) == ("tcp", "127.0.0.1:52731", [])


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
