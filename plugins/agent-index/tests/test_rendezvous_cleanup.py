"""Regression tests for owner-aware rendezvous cleanup."""

from __future__ import annotations

import os

import pytest

from agent_index import rendezvous


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


def test_clear_endpoint_removes_non_utf8_record(tmp_path):
    rendezvous.endpoint_file(tmp_path).write_bytes(b"\xff")

    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)

    assert not rendezvous.endpoint_file(tmp_path).exists()


def test_clear_endpoint_rechecks_owner_before_unlink(monkeypatch, tmp_path):
    rendezvous.write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    owner = rendezvous.read_endpoint(tmp_path)
    successor = rendezvous.Endpoint("tcp", "127.0.0.1:41002", pid=2002)
    records = iter((owner, successor))
    monkeypatch.setattr(rendezvous, "read_endpoint", lambda _runtime_dir: next(records))

    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)

    assert rendezvous.endpoint_file(tmp_path).exists()


def test_posix_zombie_is_not_alive(monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX process-state test")

    class ProcStat:
        @staticmethod
        def read_text(*, encoding):
            assert encoding == "ascii"
            return "123 (python worker) Z 1 123 123 0"

    monkeypatch.setattr(rendezvous, "Path", lambda _value: ProcStat())
    monkeypatch.setattr(
        rendezvous.os,
        "kill",
        lambda *_args: pytest.fail("zombie liveness must not fall through to os.kill"),
    )

    assert rendezvous.pid_alive(123) is False
