"""Real opt-in loader/owner boundaries, independent of installed services."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from process_telemetry import SinkSlot

from agent_bridge import diagnostic_logs, telemetry


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def offer(event):
    writer = telemetry._slot.sink
    before = writer.stats()["accepted"]
    def admitted():
        if writer.stats()["accepted"] == before:
            telemetry.emit(event)
        return writer.stats()["accepted"] > before
    wait_for(admitted)


@pytest.fixture(autouse=True)
def isolated_slot(monkeypatch):
    slot = SinkSlot()
    monkeypatch.setattr(telemetry, "_slot", slot)
    yield slot
    slot.set(None)
    if slot._last_buffer is not None:
        assert slot._last_buffer.join(2)


def config(tmp_path, data=None):
    directory = tmp_path / "bucket"
    (directory / "retained").mkdir(parents=True)
    path = tmp_path / "telemetry.json"
    path.write_text(json.dumps(
        data if data is not None else {"retained_spool": {"directory": str(directory)}}
    ), encoding="utf-8")
    return path, directory


def rows(directory):
    return [json.loads(line) for path in (directory / "retained").glob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]


def test_actual_config_loads_buffered_sink_without_adopting_legacy(
    tmp_path, isolated_slot, monkeypatch,
):
    from process_telemetry import diagnostic_logs as shared

    path, directory = config(tmp_path)
    legacy = tmp_path / "legacy.jsonl"
    telemetry.make_spool_sink(legacy)({"ts": 1, "name": "legacy"})
    before = legacy.read_bytes()
    original_append = shared.append_bytes
    producer = threading.get_ident()
    def append(path, data):
        assert threading.get_ident() != producer
        return original_append(path, data)
    monkeypatch.setattr(shared, "append_bytes", append)
    assert telemetry.load_sink_from_config(path)
    event = {"ts": int(time.time() * 1000), "kind": "state", "name": "λ🚀",
             "nested": {"count": 1}}
    offer(event)
    wait_for(lambda: isolated_slot.sink.stats()["delivered"] == 1)
    assert rows(directory) == [event]
    assert legacy.read_bytes() == before
    assert (directory / "retained" / "agent-bridge-telemetry.jsonl").exists()


@pytest.mark.parametrize("data", [
    {"retained_spool": None}, {"retained_spool": {}},
    {"retained_spool": {"directory": "relative-private-value"}},
    {"retained_spool": {"directory": 42}},
    {"retained_spool": {"directory": "private\0value"}},
    {"retained_spool": {"directory": "private", "extra": True}},
    {"retained_spool": {}, "sink": "private.module:factory"},
    {"retained_spool": {}, "spool": "private-value"},
])
def test_invalid_opt_in_is_content_free_and_does_not_replace_legacy(
    tmp_path, data, caplog,
):
    path, directory = config(tmp_path, data)
    seen = []
    telemetry.set_telemetry_sink(seen.append)
    assert not telemetry.load_sink_from_config(path)
    telemetry.emit({"kind": "still-legacy"})
    assert seen == [{"kind": "still-legacy"}]
    assert rows(directory) == []
    assert "configured retained telemetry unavailable" in caplog.text
    assert "private" not in caplog.text


def test_missing_namespace_degrades_on_worker_without_creating_parents(
    tmp_path, monkeypatch, isolated_slot, caplog,
):
    missing = tmp_path / "private-missing"
    path = tmp_path / "telemetry.json"
    path.write_text(json.dumps({"retained_spool": {"directory": str(missing)}}))
    assert telemetry.load_sink_from_config(path)
    wait_for(lambda: isolated_slot.sink.stats()["sink_failed"] >= 1)
    assert not missing.exists()
    assert "private-missing" not in caplog.text
    assert "configured retained telemetry unavailable" in caplog.text


def test_construction_and_storage_stay_off_loop_and_close_never_joins(
    tmp_path, monkeypatch, isolated_slot,
):
    path, directory = config(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = telemetry.make_retained_spool_sink
    owner_thread = threading.get_ident()

    def slow_factory(base):
        assert threading.get_ident() != owner_thread
        entered.set()
        assert release.wait(3)
        return original(base)

    monkeypatch.setattr(telemetry, "make_retained_spool_sink", slow_factory)
    async def run():
        assert telemetry.load_sink_from_config(path)
        writer = isolated_slot.sink
        await asyncio.sleep(0.05)
        assert entered.is_set()
        started = time.monotonic()
        for index in range(128):
            telemetry.emit({"kind": "state", "index": index})
        telemetry.close_configured_retained_sink()
        assert time.monotonic() - started < 0.5
        assert writer.closed and not writer.join(0)
        assert not telemetry.load_sink_from_config(path)
        release.set()
        wait_for(lambda: writer.join(0))
        assert telemetry.load_sink_from_config(path)
        offer({"kind": "new"})
        wait_for(lambda: isolated_slot.sink.stats()["delivered"] == 1)
    try:
        asyncio.run(run())
    finally:
        release.set()
    assert [event["kind"] for event in rows(directory)] == ["new"]


def test_startup_and_quiet_iteration_expire_exact_age(tmp_path, monkeypatch):
    from process_telemetry.retained_spool import RetainedSpool

    path, directory = config(tmp_path)
    now = datetime(2026, 10, 24, tzinfo=timezone(timedelta(hours=7)))
    cutoff = int((now - timedelta(days=30)).timestamp() * 1000)
    active = directory / "retained" / "agent-bridge-telemetry.jsonl"
    active.write_bytes("".join(json.dumps({"ts": cutoff + delta, "id": delta}) + "\n"
                               for delta in (-1, 0, 1)).encode("utf-8"))
    archive = active.with_name(
        "agent-bridge-telemetry.20260101T000000000000Z-" + "a" * 32 + ".jsonl"
    )
    archive.write_text(json.dumps({"ts": cutoff - 1, "id": "expired-archive"}) + "\n")
    monkeypatch.setattr(telemetry, "make_retained_spool_sink", lambda base: RetainedSpool(
        base, "agent-bridge-telemetry", service="agent-bridge",
        custody=diagnostic_logs.operation_custody, clock=lambda: now,
    ))
    assert telemetry.load_sink_from_config(path)
    expected = "".join(json.dumps({"ts": cutoff + delta, "id": delta}) + "\n"
                       for delta in (0, 1))
    wait_for(lambda: active.stat().st_size == len(expected.encode("utf-8")))
    assert [event["id"] for event in rows(directory)] == [0, 1]
    assert not archive.exists()
    now += timedelta(seconds=61)
    wait_for(lambda: active.stat().st_size == 0)
    assert rows(directory) == []


def test_real_cli_discovery_is_read_only_and_never_imports_custom_factory(tmp_path, monkeypatch):
    path, directory = config(tmp_path)
    environment = dict(os.environ, AGENT_BRIDGE_CONFIG_DIR=str(tmp_path),
                       AGENT_BRIDGE_INSTALL_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "agent_bridge", "logs"], env=environment,
        capture_output=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0 and result.stderr == b""
    report = json.loads(result.stdout)["telemetry"]
    assert report["active"] == str(directory / "retained" / "agent-bridge-telemetry.jsonl")
    assert report["write_health"] == "unknown-from-discovery"
    assert rows(directory) == []
    path.write_text(json.dumps({"sink": "private.custom.module:factory"}))
    monkeypatch.setattr(telemetry, "_default_config_path", lambda: path)
    assert telemetry.configured_sink_inventory()["configuration"] == "legacy-or-custom"


def test_real_daemon_owner_clears_only_configured_buffer(tmp_path, monkeypatch):
    from agent_bridge import service_start_cli

    path, _ = config(tmp_path)
    writers = []
    def primary(*_args, **_kwargs):
        assert telemetry.load_sink_from_config(path)
        writers.append(telemetry._slot.sink)
        path.write_text(json.dumps({"retained_spool": {}, "sink": "private:factory"}))
        assert not telemetry.load_sink_from_config(path)
        assert telemetry._slot.sink is writers[0]
        raise ValueError("primary")
    monkeypatch.setattr(service_start_cli, "_run_start", primary)
    with pytest.raises(ValueError, match="primary"):
        service_start_cli._cmd_start(argparse.Namespace())
    assert writers[0].closed
    assert not telemetry.has_sink()
    telemetry.set_telemetry_sink(lambda event: None)
    telemetry.close_configured_retained_sink()
    assert telemetry.has_sink()


def test_discovery_metadata_failure_stays_unknown_and_payload_free(tmp_path, monkeypatch):
    from process_telemetry import configured_spool

    path, directory = config(tmp_path)
    monkeypatch.setattr(telemetry, "_default_config_path", lambda: path)
    def inaccessible(*_args):
        raise PermissionError("private-filesystem-detail")
    monkeypatch.setattr(configured_spool, "location", inaccessible)
    report = telemetry.configured_sink_inventory()
    assert report["metadata"] == "unavailable"
    assert report["namespace_preprovisioned"] is None
    assert report["write_health"] == "unknown-from-discovery"
    assert "private" not in json.dumps(report)
    assert rows(directory) == []


def test_configured_cleanup_failure_is_optional(tmp_path, monkeypatch):
    path, _ = config(tmp_path)
    assert telemetry.load_sink_from_config(path)
    def unavailable():
        raise RuntimeError("synthetic cleanup failure")
    with monkeypatch.context() as patch:
        patch.setattr(telemetry._slot, "set", lambda _sink: unavailable())
        telemetry.close_configured_retained_sink()


def test_cleanup_cannot_clear_a_concurrent_replacement(tmp_path, monkeypatch):
    path, _ = config(tmp_path)
    assert telemetry.load_sink_from_config(path)
    slot = telemetry._slot
    attempted = threading.Event()
    underlying = slot._configuration_lock
    main_thread = threading.get_ident()
    class ObservedLock:
        def __enter__(self):
            if threading.get_ident() != main_thread:
                attempted.set()
            return underlying.__enter__()

        def __exit__(self, *args):
            return underlying.__exit__(*args)
    monkeypatch.setattr(slot, "_configuration_lock", ObservedLock())
    replacement = lambda event: None
    with slot._configuration_lock:
        cleanup = threading.Thread(target=telemetry.close_configured_retained_sink)
        cleanup.start()
        assert attempted.wait(1)
        telemetry.set_telemetry_sink(replacement)
    cleanup.join(1)
    assert not cleanup.is_alive()
    assert slot.sink is replacement


def test_missing_ts_is_stamped_at_admission_before_delayed_drain(tmp_path, monkeypatch):
    from process_telemetry import buffer

    path, directory = config(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = telemetry.make_retained_spool_sink
    clock = [time.time()]
    first_stamp = int(clock[0] * 1000)
    def slow_factory(base):
        entered.set()
        assert release.wait(3)
        return original(base)
    monkeypatch.setattr(telemetry, "make_retained_spool_sink", slow_factory)
    monkeypatch.setattr(buffer.time, "time", lambda: clock[0])
    try:
        assert telemetry.load_sink_from_config(path)
        assert entered.wait(1)
        missing = {"kind": "missing"}
        offer(missing)
        supplied = {"kind": "supplied", "ts": first_stamp + 123}
        offer(supplied)
        clock[0] += 3600
        release.set()
        wait_for(lambda: telemetry._slot.sink.stats()["delivered"] == 2)
    finally:
        release.set()
    assert "ts" not in missing
    assert rows(directory) == [
        {"kind": "missing", "ts": first_stamp}, supplied,
    ]


def test_generated_timestamp_shares_budget_and_never_probes_rejected_keys(tmp_path):
    from process_telemetry.buffer import snapshot

    path, _ = config(tmp_path)
    assert telemetry.load_sink_from_config(path)
    probed = []
    class Key:
        def __hash__(self):
            return hash("ts")

        def __eq__(self, other):
            probed.append(None)
            raise AssertionError("producer must not compare arbitrary keys")
    telemetry.emit({Key(): 1})
    assert probed == []
    assert telemetry._slot.sink.stats()["invalid"] == 1
    assert snapshot({"x": 1}, max_bytes=160) == {"x": 1}
    with pytest.raises(ValueError, match="event limit"):
        snapshot({"x": 1}, max_bytes=160, stamp_missing_ts=True)


def test_delivery_control_retries_only_unaccepted_offers(tmp_path, monkeypatch):
    path, directory = config(tmp_path)
    assert telemetry.load_sink_from_config(path)
    writer = telemetry._slot.sink
    original = telemetry.emit
    calls = []
    def first_contended(event):
        calls.append(None)
        if len(calls) == 1:
            with writer._lock:
                original(event)
        else:
            original(event)
    monkeypatch.setattr(telemetry, "emit", first_contended)
    offer({"kind": "one"})
    wait_for(lambda: writer.stats()["delivered"] == 1)
    assert writer.stats()["contended"] >= 1
    assert writer.stats()["accepted"] == 1
    assert len(rows(directory)) == 1


def test_real_configured_processes_append_complete_unicode_rows(tmp_path):
    path, directory = config(tmp_path)
    script = """
import sys,time
from agent_bridge import telemetry as t
original=t.make_retained_spool_sink
def bounded(base):
    sink=original(base)
    sink.store.max_bytes=256
    return sink
t.make_retained_spool_sink=bounded
assert t.load_sink_from_config(sys.argv[1])
w=t._slot.sink
end=time.monotonic()+10
for i in range(24):
    before=w.stats()["accepted"]
    event={"ts":int(time.time()*1000),"writer":sys.argv[2],"i":i,"text":"λ🚀"}
    while w.stats()["accepted"]==before:
        t.emit(event)
        assert time.monotonic()<end
        if w.stats()["accepted"]==before: time.sleep(.001)
while w.stats()["delivered"]<24:
    assert time.monotonic()<end
    time.sleep(.01)
t.clear_telemetry_sink()
assert w.join(2)
"""
    children = []
    try:
        for index in range(3):
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, str(path), str(index)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
        for child in children:
            out, err = child.communicate(timeout=15)
            assert child.returncode == 0, (out, err)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
    events = rows(directory)
    assert len(events) == 72
    assert len(list((directory / "retained").glob("*.jsonl"))) > 1
    assert len({(event["writer"], event["i"]) for event in events}) == 72
    assert all(event["text"] == "λ🚀" for event in events)
