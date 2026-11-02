"""Child-owned registration before an asynchronous passive opens its endpoint."""

from __future__ import annotations

import json
import os
import time

from zdd import breadcrumb, diagnostics, publication_history, routing

ENV = "AGENT_WORKTREES_MONITOR_PREPARATION"
CAPABILITY = "status-monitor-passive-cancel-v1"
ACTION = "cancel-preparation"
_ATTEMPT = ("pid", "owner_process_start_time", "started_at", "publication_witness", "new_port")


def launch_environment(directory, env):
    env.pop(ENV, None)
    record = breadcrumb.read_breadcrumb(directory)
    if isinstance(record, dict) and record.get("self_retirement") is True:
        if record.get("state") != "started" or record.get("error") is not None:
            raise ValueError("Passive preparation witness is not open")
        env[ENV] = json.dumps({key: record.get(key) for key in _ATTEMPT}, separators=(",", ":"))


def register(root, port) -> bool:
    raw = os.environ.get(ENV)
    if raw is None:
        return True
    expected = json.loads(raw)
    if not isinstance(expected, dict) or set(expected) != set(_ATTEMPT) or expected["new_port"] != port:
        raise ValueError("Passive preparation startup identity is invalid")
    directory = root / "status-monitor-routing"
    with routing.routing_lock(directory, deadline=time.monotonic() + 1):
        record = breadcrumb.read_breadcrumb(directory)
        if (
            not isinstance(record, dict) or record.get("self_retirement") is not True
            or record.get("state") != "started" or record.get("error") is not None
            or any(record.get(key) != value for key, value in expected.items())
            or breadcrumb.owner_exited(record) is not False
        ):
            return False
        pid, start = os.getpid(), diagnostics.process_start_time(os.getpid())
        if start is None:
            raise ValueError("Passive preparation native birth is unavailable")
        if record.get("new_pid") not in (None, pid):
            raise ValueError("Passive preparation was registered by another child")
        breadcrumb.write_breadcrumb(
            directory, state="started", old=record.get("old"), new_port=port, new_pid=pid,
            new_process_start_time=start, pid=record["pid"], started_at=record["started_at"],
            publication_witness=record.get("publication_witness"), self_retirement=True,
        )
    return True


def cancel(payload, *, root, port, passive) -> bool:
    if not passive or set(payload) != {"pid", "start", "port", "started_at", "publication_witness"}:
        return False
    pid, start = os.getpid(), diagnostics.process_start_time(os.getpid())
    if (payload["pid"], payload["start"], payload["port"]) != (pid, start, port) or start is None:
        return False
    directory = root / "status-monitor-routing"
    record = breadcrumb.read_breadcrumb(directory)
    table = routing.read_table(directory, strict=True) or {}
    return (
        isinstance(record, dict) and record.get("self_retirement") is True
        and (record.get("new_pid"), record.get("new_process_start_time"), record.get("new_port")) == (pid, start, port)
        and all(record.get(key) == payload[key] for key in ("started_at", "publication_witness"))
        and publication_history.candidate_never_published(table, record, pid, start)
    )
