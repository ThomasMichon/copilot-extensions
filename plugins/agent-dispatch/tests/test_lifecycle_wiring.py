"""Tests for the daemon startup dead-port sweep + start/stop lifecycle records."""

from __future__ import annotations

import os
import types
from pathlib import Path

from agent_dispatch import server
from zdd import lifecycle, routing


def _cfg():
    return types.SimpleNamespace(host="127.0.0.1")


def _reset(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server, "routing_dir", lambda: tmp_path)
    server._lifecycle_started = False
    server._wake_route_owned = False
    server._last_missing_active_heal_attempt = 0.0


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


def test_wake_drain_follows_active_routing_owner(tmp_path: Path, monkeypatch):
    _reset(monkeypatch, tmp_path)
    assert server._owns_active_route() is False
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=9999, pid=os.getpid()
    )
    assert server._owns_active_route() is True
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=9998, pid=os.getpid() + 1
    )
    assert server._owns_active_route() is False


def test_wake_drain_retains_last_owner_during_routing_read_failure(
    tmp_path: Path, monkeypatch
):
    _reset(monkeypatch, tmp_path)
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=9999, pid=os.getpid()
    )
    assert server._owns_active_route() is True
    monkeypatch.setattr(routing, "read_table", lambda _path: (_ for _ in ()).throw(
        OSError("routing unavailable")
    ))

    assert server._owns_active_route() is True


def test_wake_drain_does_not_promote_passive_on_routing_read_failure(
    tmp_path: Path, monkeypatch
):
    _reset(monkeypatch, tmp_path)
    monkeypatch.setattr(routing, "read_table", lambda _path: (_ for _ in ()).throw(
        OSError("routing unavailable")
    ))

    assert server._owns_active_route() is False


def test_wake_drain_self_heals_missing_active_by_promoting_live_previous(
    tmp_path: Path, monkeypatch
):
    """A clean shutdown's ``previous``-only table must not strand wake delivery.

    ``clear_if_owner`` demotes a shutting-down coordinator's own claim to
    ``previous`` and leaves no ``active`` behind, trusting a successor to
    publish itself. ``reap_stale_active`` otherwise runs only at a new
    coordinator's startup or the start of a cutover -- neither of which
    happens if the successor never starts. This confirmed-live bug (see
    dotfiles#2143, 2026-09-21) stranded a pending wake for 15+ minutes with
    ``last_error: "bridge delivery unavailable"`` because no live process ever
    re-checked route ownership. ``_owns_active_route`` must notice a missing
    ``active`` and trigger the same promotion the wake-drain loop already
    polls through, so any live coordinator self-heals without a human
    manually restarting one.
    """
    _reset(monkeypatch, tmp_path)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9999, pid=os.getpid())
    # Simulate our own clean shutdown leaving only `previous` (clear_if_owner's
    # shape) -- no successor ever published an `active`.
    assert routing.clear_if_owner(tmp_path, pid=os.getpid()) is True
    data = routing.read_table(tmp_path)
    assert "active" not in data and data["previous"]["pid"] == os.getpid()

    # A real listener stands in for "previous is actually still alive" --
    # reap_stale_active only promotes a previous it can confirm is listening.
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    live_port = sock.getsockname()[1]
    try:
        table = routing.read_table(tmp_path)
        table["previous"]["port"] = live_port
        routing.routing_table_path(tmp_path).write_text(__import__("json").dumps(table))

        assert server._owns_active_route() is True
        healed = routing.read_table(tmp_path)
        assert healed["active"]["pid"] == os.getpid()
        assert healed["active"]["port"] == live_port
    finally:
        sock.close()


def test_wake_drain_missing_active_self_heal_is_throttled(
    tmp_path: Path, monkeypatch
):
    _reset(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        routing, "reap_stale_active",
        lambda *a, **k: calls.append(1) or {"reaped": False, "promoted_port": None},
    )
    assert server._owns_active_route() is False
    assert server._owns_active_route() is False
    assert len(calls) == 1  # second call within the throttle window is skipped


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
