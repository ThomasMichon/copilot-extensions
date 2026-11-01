"""Actual singleton release/self-update with retained synthetic metadata I/O."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from typing import Any

import pytest

from agent_dispatch import supervisor_daemon
from tests.test_pool_publication_custody import _apply, owner as _owner_fixture

owner = _owner_fixture


@pytest.mark.parametrize("mode", ["release", "self-update"])
def test_pending_metadata_io_does_not_hold_release_or_bounded_self_update(
    owner: Any, monkeypatch: Any, tmp_path: Any, mode: str,
) -> None:
    daemon, queue, calls = owner
    request = daemon.prepare_pool_metadata_request()
    started, finish, spawned = Event(), Event(), Event()
    writes = []

    def slow(request: Any, *, boundary: Any) -> Any:
        with boundary():
            writes.append(request.to_dict())
            started.set()
            assert finish.wait(2)
            return _apply(queue, request)

    def wind_down() -> bool:
        if mode == "self-update":
            return daemon._maybe_self_update()
        assert not daemon.shutdown()
        daemon.release_singleton()
        return True

    daemon.client.send_pool_metadata = slow
    daemon._reconcile_call_budget = 0.05
    daemon.self_update_enabled = True
    daemon.self_update_poll_interval = 0
    daemon._self_update_last_check = float("-inf")
    daemon._self_update_install_dir = lambda: tmp_path
    daemon._self_update_stale_target = lambda *_: tmp_path / "next"
    daemon._self_update_argv = ["supervise", "serve"]
    daemon._self_update_spawn = lambda *_: spawned.set()
    monkeypatch.setattr(supervisor_daemon, "native_restart_unit", lambda: None)
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    try:
        with pytest.raises(TimeoutError):
            daemon.send_pool_metadata_request()
        assert started.is_set()
        flight = daemon._pool_metadata_flight
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(wind_down).result(timeout=0.5)
        assert not flight.future.done()
        assert flight.future in daemon._probe_inflight
        assert daemon._lock.acquisition is None
        assert daemon._pool_metadata_closed.is_set()
        assert spawned.is_set() == (mode == "self-update")
        assert daemon._pool_publisher_custody.pending is request
        finish.set()
        result = flight.future.result(timeout=1)
        calls_before = len(calls)
        daemon._reconcile_call_budget = 1
        assert daemon.send_pool_metadata_request() == result
        assert len(calls) == calls_before and writes == [request.to_dict()]
        assert daemon._lock.acquisition is None and daemon._pool_metadata_closed.is_set()
    finally:
        finish.set()
