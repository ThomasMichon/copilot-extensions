"""Tests for the daemon startup dead-port sweep + start/stop lifecycle records."""

from __future__ import annotations

import types
from pathlib import Path

from agent_dispatch import server
from zdd import lifecycle, routing


def _cfg():
    return types.SimpleNamespace(host="127.0.0.1")


def _reset(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "routing_dir", lambda: tmp_path)
    server._lifecycle_started = False


def test_publish_sweeps_and_records_start_then_stop(tmp_path: Path, monkeypatch):
    _reset(monkeypatch, tmp_path)
    swept: list = []
    monkeypatch.setattr(
        routing, "reap_stale_active", lambda *a, **k: swept.append(True)
    )
    server._publish_routing(_cfg(), 9999)
    assert swept == [True]                       # startup dead-port sweep ran
    assert server._lifecycle_started is True
    starts = [e for e in lifecycle.read_events(tmp_path)
              if e["action"] == "start" and e["service"] == "agent-dispatch"]
    assert len(starts) == 1 and starts[0]["port"] == 9999

    server._clear_routing()
    assert any(e["action"] == "stop" for e in lifecycle.read_events(tmp_path))


def test_passive_records_nothing(tmp_path: Path, monkeypatch):
    _reset(monkeypatch, tmp_path)
    server._publish_routing(_cfg(), 9999, passive=True)
    server._clear_routing()  # never logged START -> no orphan STOP
    assert lifecycle.read_events(tmp_path) == []


def test_stop_emitted_even_after_demotion(tmp_path: Path, monkeypatch):
    # A coordinator that logged START but was demoted (active -> a different pid)
    # must still emit STOP on shutdown: start/stop pair up regardless of ownership.
    _reset(monkeypatch, tmp_path)
    server._publish_routing(_cfg(), 9999)
    # Simulate a successor flipping the table to a different pid (we are demoted).
    routing.publish_active(tmp_path, bind="127.0.0.1", port=8888, pid=999999,
                           demote_existing=True)
    server._clear_routing()
    stops = [e for e in lifecycle.read_events(tmp_path) if e["action"] == "stop"]
    assert len(stops) == 1


def test_lifecycle_wiring_is_fail_open(tmp_path: Path, monkeypatch):
    server._lifecycle_started = False

    def _boom():
        raise OSError("routing dir unavailable")

    monkeypatch.setattr(server, "routing_dir", _boom)
    # Neither publish nor clear may propagate an exception.
    server._publish_routing(_cfg(), 9999)
    server._clear_routing()


def test_failed_start_record_suppresses_stop(tmp_path: Path, monkeypatch):
    # If the START record cannot be written (record is fail-open, returns None),
    # _lifecycle_started stays False so shutdown does not emit an orphan STOP.
    _reset(monkeypatch, tmp_path)
    monkeypatch.setattr(routing, "reap_stale_active", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "record", lambda *a, **k: None)
    server._publish_routing(_cfg(), 9999)
    assert server._lifecycle_started is False
    server._clear_routing()  # no START recorded -> no STOP attempt
