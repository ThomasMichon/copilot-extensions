"""Explicit one-hour candidates; confirmed staging stays with health_slots."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from process_telemetry.hourly import hour_start

from . import health_hourly as hourly, health_slots as slots
from .killswitch_mutation import _lock_byte, _unlock_byte

COLLECTION_SECONDS = 45
_RUN_LOCK = threading.Lock()


@contextmanager
def admission(store: Path, *, uncertain=lambda: False):
    """Nonblocking existing byte lock, without creating or replacing its file."""
    from agent_procutil.owned_process import OwnedCleanupError

    path = slots._plain(store / "collection.lock")
    if not _RUN_LOCK.acquire(blocking=False):
        raise slots.HealthError("collection-overlap")
    stream = None
    locked = fenced = retained = False

    def fence(value: bytes):
        stream.seek(0)
        if stream.write(value) != 1:
            raise OSError("collection fence write incomplete")
        stream.flush()
        os.fsync(stream.fileno())

    def begin_exports():
        nonlocal fenced
        fenced = True
        fence(b"\1")

    def release():
        if fenced and not uncertain():
            fence(b"\0")
        if locked:
            _unlock_byte(stream)
        if stream is not None:
            stream.close()
        _RUN_LOCK.release()

    try:
        before = path.stat()
        if before.st_size != 1:
            raise slots.HealthError("invalid-collection-lock")
        stream = path.open("r+b", buffering=0)
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise slots.HealthError("collection-lock-changed")
        try:
            _lock_byte(stream)
        except OSError:
            raise slots.HealthError("collection-overlap") from None
        locked = True
        slots._plain(path)
        current = path.stat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise slots.HealthError("collection-lock-changed")
        stream.seek(0)
        if stream.read(1) != b"\0":
            raise slots.HealthError("collection-cleanup-unconfirmed")
        try:
            yield begin_exports
        except OwnedCleanupError as error:
            retained = True
            error._retain_on_settled(release)
            raise
    finally:
        if not retained:
            release()


def collect(store: Path, hour: str | None = None) -> dict:
    """Return one candidate; never write a slot or infer past health."""
    deadline = time.monotonic() + COLLECTION_SECONDS
    started_ms = time.time_ns() // 1_000_000
    current_hour = started_ms // slots.HOUR_MS
    requested = hour_start(hour) // slots.HOUR_MS if hour is not None else current_hour
    if not current_hour - slots.SLOTS < requested <= current_hour:
        raise slots.HealthError("collection-hour-outside-window")
    store = slots._plain(store, directory=True)
    policy, policy_hash = slots._read(store / "policy.json", slots.validate_policy)
    if policy["version"] != 2:
        raise slots.HealthError("collection-store-unsupported")
    with admission(store) as begin_exports:
        candidate = collect_target(
            store, requested, current_hour=current_hour, started_ms=started_ms,
            deadline=deadline, policy=policy, policy_hash=policy_hash, begin_exports=begin_exports,
        )
    if time.monotonic() >= deadline - .25:
        raise slots.HealthError("collection-timeout")
    completed_ms = time.time_ns() // 1_000_000
    if completed_ms < started_ms or completed_ms // slots.HOUR_MS != current_hour:
        raise slots.HealthError("collection-hour-changed")
    return candidate


def collect_target(store, requested, *, current_hour, started_ms, deadline, policy, policy_hash, begin_exports):
    """Collect under an existing admission and parent budget, without staging."""
    from . import health_observe, service_reports

    target = store / f"{requested % slots.SLOTS:03d}.json"
    previous, _hash = slots._read(target, hourly.validate_record, optional=True, maximum=hourly.MAX_BYTES)
    if previous is not None and (not slots._owner(previous, policy) or previous["hour"] > requested):
        raise slots.HealthError("collection-slot-conflict")
    previous = previous if previous is not None and previous["hour"] == requested else None
    observation = previous["observation"] if previous else None
    services = {row["instance"]: row for row in previous["services"]} if previous else {}
    coverage = {
        "observation": "retained" if observation is not None else "not-requested",
        "services": "retained" if services else "not-requested",
    }
    selection = policy["collection"]
    now_ms = time.time_ns() // 1_000_000
    if now_ms < started_ms or now_ms // slots.HOUR_MS != current_hour:
        raise slots.HealthError("collection-hour-changed")
    if time.monotonic() >= deadline - .25:
        raise slots.HealthError("collection-timeout")
    if requested == current_hour and selection["bridge_port"] is not None:
        if deadline - time.monotonic() <= health_observe.TIMEOUT_SECONDS + .25:
            raise slots.HealthError("collection-timeout")
        fresh = health_observe.observe_bridge(selection["bridge_port"], policy["publisher"], policy["continuity"])
        if fresh["hour"] != requested:
            raise slots.HealthError("collection-hour-changed")
        observation = {
            **fresh, "domains": {
                **(observation["domains"] if observation else {}),
                "local_listener": fresh["domains"]["local_listener"],
            },
        }
        coverage["observation"] = "collected"
    if requested < current_hour and selection["services"]:
        canonical_hour = datetime.fromtimestamp(requested * 3600, timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
        begin_exports()
        rows = service_reports.collect(selection["services"], canonical_hour, now_ms=started_ms, deadline=deadline - .25)
        coverage["services"] = "collected"
        for row in rows:
            old = services.get(row["instance"])
            if old is not None and old["service"] != row["service"]:
                raise slots.HealthError("collection-service-conflict")
            if old is not None and hourly.has_service_evidence(old) and not hourly.has_service_evidence(row):
                coverage["services"] = "partial"
                continue
            services[row["instance"]] = row
    finished_ms = time.time_ns() // 1_000_000
    if finished_ms < started_ms or finished_ms // slots.HOUR_MS != current_hour:
        raise slots.HealthError("collection-hour-changed")
    current_policy, current_hash = slots._read(store / "policy.json", slots.validate_policy)
    if current_hash != policy_hash or current_policy != policy:
        raise slots.HealthError("store-policy-changed")
    return hourly.validate_record({
        "schema": hourly.SCHEMA, "version": 1,
        "publisher": policy["publisher"], "continuity": policy["continuity"],
        "hour": requested, "revision": previous["revision"] + 1 if previous else 1,
        "parent": previous["revision"] if previous else None,
        "observed_at_ms": finished_ms, "observation": observation,
        "services": list(services.values()), "coverage": coverage,
    })
