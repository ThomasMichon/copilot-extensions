"""Regression tests for owner-aware rendezvous cleanup."""

from __future__ import annotations

from agent_index import rendezvous


def test_clear_endpoint_removes_non_utf8_record(tmp_path):
    rendezvous.endpoint_file(tmp_path).write_bytes(b"\xff")

    rendezvous.clear_endpoint(tmp_path, owner_pid=1001)

    assert not rendezvous.endpoint_file(tmp_path).exists()
