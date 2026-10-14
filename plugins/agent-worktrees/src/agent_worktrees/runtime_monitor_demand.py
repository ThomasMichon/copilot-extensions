"""Retain promoted monitor demand through an owning runtime transition."""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from work_coalescing_singleton import client
from zdd import routing

from . import locks, worktree_status_daemon

_RENEW_INTERVAL_S = 5.0


@contextmanager
def retain_transition_demand(root: Path, pid: int, *, rendezvous_timeout: float = 5.0):
    deadline = time.monotonic() + rendezvous_timeout
    while True:
        record = locks.read_lock(root / "status-monitor.lock")
        endpoint = worktree_status_daemon.endpoint_from_rendezvous(record)
        if isinstance(record, dict) and record.get("pid") == pid and endpoint is not None:
            break
        active = routing.read_active_endpoint(
            root / "status-monitor-routing", verify_listener=False,
        )
        if active is None or active.pid != pid or time.monotonic() >= deadline:
            raise client.DaemonUnavailable(
                f"Promoted monitor demand rendezvous does not match its route "
                f"(expected={pid}, observed={(record or {}).get('pid')})",
            )
        time.sleep(0.05)
    identity = client.new_client_id()
    stopped = threading.Event()
    failures: list[client.DaemonUnavailable] = []

    def renew() -> None:
        while not stopped.wait(_RENEW_INTERVAL_S):
            try:
                client.subscribe(*endpoint, identity)
            except client.DaemonUnavailable as error:
                failures.append(error)
                return

    heartbeat = threading.Thread(
        target=renew, name="runtime-monitor-transition-demand", daemon=True,
    )
    client.subscribe(*endpoint, identity)
    failed = False
    try:
        heartbeat.start()
        yield
    except BaseException:
        failed = True
        raise
    finally:
        stopped.set()
        if heartbeat.ident is not None:
            heartbeat.join()
        client.release(*endpoint, identity)
        if failures:
            if failed:
                print(
                    f"Monitor transition demand failed: {failures[0]}",
                    file=sys.stderr, flush=True,
                )
            else:
                raise failures[0]
