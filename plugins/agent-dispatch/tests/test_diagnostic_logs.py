"""Record-age retention and the real local consumer contract; no live service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent_dispatch import diagnostic_logs as logs
from agent_dispatch.logging_setup import configure_file_logging


def record_at(now, message="retained λ 🚀", **extra):
    record = logging.LogRecord("agent-dispatch.test", logging.INFO, "", 0, message, (), None)
    record.created = now.timestamp()
    record.__dict__.update(extra)
    return record


def events(handler):
    return [json.loads(line) for path in handler._files()
            for line in path.read_text(encoding="utf-8").splitlines()]


def seed(path, *stamps):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"ts": stamp.isoformat()}) + "\n"
                            for stamp in stamps), encoding="utf-8")


def test_exact_cutoff_timezone_and_skew(tmp_path):
    now = datetime(2026, 10, 23, 12, tzinfo=timezone(timedelta(hours=-7)))
    cutoff = now - timedelta(days=30)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    seed(handler.path, cutoff - timedelta(microseconds=1), cutoff,
         cutoff + timedelta(microseconds=1), now + timedelta(days=2))
    handler.maintain(force=True)
    assert len(events(handler)) == 3
    assert all(datetime.fromisoformat(e["ts"]) >= cutoff for e in events(handler))
    now -= timedelta(days=1)
    handler.maintain()
    assert len(events(handler)) == 3
    handler.emit(record_at(now, "clock moved backward"))
    assert events(handler)[-1]["ts"].endswith("+00:00")
    assert len(events(handler)) == 4
    with pytest.raises(ValueError, match="aware"):
        logs._utc(datetime(2026, 10, 23))


def test_continuous_writer_rollover_and_idle_maintenance(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now, max_bytes=600)
    for index in range(3):
        handler.emit(record_at(now, f"old-{index}"))
    assert len(handler._files()) >= 2
    now += timedelta(days=30)
    handler.emit(record_at(now, "new-at-boundary"))
    assert len(events(handler)) == 4
    now += timedelta(microseconds=1)
    handler.maintain(force=True)
    assert [e["message"] for e in events(handler)] == ["new-at-boundary"]
    now += timedelta(days=30, microseconds=1)
    handler.maintain(force=True)
    assert events(handler) == []
    assert handler.path.exists()
    handler.emit(record_at(now, "alive after quiet prune"))
    assert events(handler)[0]["message"] == "alive after quiet prune"


def test_restart_prunes_archives_and_never_touches_native_handles(tmp_path):
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    archive = tmp_path / "logs" / "diagnostics" / (
        "coordinator.20260901T000000000000Z-" + "a" * 32 + ".jsonl"
    )
    seed(archive, now - timedelta(days=31))
    native = tmp_path / "logs" / "coordinator-generation.log"
    native.parent.mkdir(parents=True, exist_ok=True)
    with native.open("ab") as inherited_capture:
        inherited_capture.write(b"native stdout\n")
        inherited_capture.flush()
        handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
        handler.emit(record_at(now))
        handler.maintain(force=True)
        inherited_capture.write(b"native stderr\n")
    assert not archive.exists()
    assert native.read_bytes() == b"native stdout\nnative stderr\n"
    assert str(native) in logs.inventory(tmp_path)["logs"][2]["existing"]


def test_corrupt_future_and_foreign_records_are_not_guessed_away(tmp_path, capsys):
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    handler = logs.DiagnosticLog("supervisor", tmp_path, clock=lambda: now)
    handler.path.write_bytes(b'{"ts":"not-a-clock","message":"retain evidence"}\n')
    foreign = handler.directory / "supervisor.operator.jsonl"
    foreign.write_text("operator-owned", encoding="utf-8")
    handler.maintain(force=True)
    assert b"retain evidence" in handler.path.read_bytes()
    assert handler.last_error == "ValueError"
    assert "diagnostics degraded" in capsys.readouterr().err
    assert foreign.read_text(encoding="utf-8") == "operator-owned"
    assert str(foreign) not in logs.inventory(tmp_path)["logs"][1]["archives"]


def test_multiple_handlers_threads_rotate_without_lost_unicode(tmp_path):
    now = datetime.now(timezone.utc)
    handlers = [logs.DiagnosticLog("coordinator", tmp_path, max_bytes=900) for _ in range(4)]

    def write(worker):
        for index in range(12):
            handlers[worker].emit(record_at(now, f"{worker}:{index}:λ🚀"))
            if index % 4 == 0:
                handlers[worker].maintain(force=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(4)))
    result = events(handlers[0])
    assert len(result) == 48
    assert {e["message"] for e in result} == {
        f"{worker}:{index}:λ🚀" for worker in range(4) for index in range(12)
    }
    assert all(h.last_error is None for h in handlers)


def test_real_processes_rotate_and_prune_with_windows_short_handles(tmp_path):
    code = """
import logging, sys
from agent_dispatch.diagnostic_logs import DiagnosticLog
from pathlib import Path
h = DiagnosticLog("coordinator", Path(sys.argv[1]), max_bytes=900)
for i in range(15):
    r = logging.LogRecord("agent-dispatch.test", 20, "", 0, sys.argv[2]+":"+str(i)+":λ🚀", (), None)
    h.emit(r)
    if i % 5 == 0:
        h.maintain(force=True)
assert h.last_error is None, h.last_error
"""
    children = []
    try:
        for worker in range(3):
            children.append(subprocess.Popen(
                [sys.executable, "-c", code, str(tmp_path), str(worker)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, stderr.decode("utf-8", errors="replace")
            assert stdout == b""
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
    handler = logs.DiagnosticLog("coordinator", tmp_path)
    result = events(handler)
    assert len(result) == 45
    assert {e["message"] for e in result} == {
        f"{worker}:{index}:λ🚀" for worker in range(3) for index in range(15)
    }
    assert len({e["pid"] for e in result}) == 3
    assert all(e["process_start_token"] for e in result)


def test_real_cli_fresh_discovery_is_read_only_and_json_clean(tmp_path):
    environment = dict(os.environ, AGENT_DISPATCH_INSTALL_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "agent_dispatch", "logs"],
        capture_output=True, env=environment, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    report = json.loads(result.stdout)
    assert report["root"] == str(tmp_path.resolve())
    assert not report["logs"][0]["exists"]
    assert report["logs"][0]["write_health"] == "unknown-from-discovery"
    assert not list(tmp_path.iterdir())


def test_existing_logger_consumer_attribution_idempotence_and_exclusion(tmp_path, capsys):
    package = logging.getLogger("agent-dispatch")
    old_handlers, old_level, old_propagate = package.handlers[:], package.level, package.propagate
    package.handlers = []
    try:
        path = configure_file_logging("coordinator", install_dir=tmp_path)
        assert configure_file_logging("supervisor", install_dir=tmp_path) == path
        assert len(package.handlers) == 1
        try:
            raise ValueError("bounded failure")
        except ValueError:
            logging.getLogger("agent-dispatch.worker").exception(
                "operation failed", extra={"correlation_id": "synthetic", "secret_extra": "exclude-me"},
            )
        event = json.loads(path.read_text(encoding="utf-8"))
        assert event["component"] == "coordinator"
        assert event["pid"] == os.getpid()
        assert event["runtime_prefix"] == sys.prefix
        assert event["process_start_token"]
        assert event["correlation_id"] == "synthetic"
        assert "ValueError: bounded failure" in event["exception"]
        assert "secret_extra" not in event
        assert "exclude-me" not in path.read_text(encoding="utf-8")
        assert capsys.readouterr().out == ""
    finally:
        for handler in package.handlers:
            handler.close()
        package.handlers = old_handlers
        package.setLevel(old_level)
        package.propagate = old_propagate


def test_logging_failure_preserves_primary_operation(tmp_path, capsys):
    root = tmp_path / "not-directory"
    root.write_bytes(b"occupied")
    handler = logs.DiagnosticLog("coordinator", root)
    handler.emit(record_at(datetime.now(timezone.utc)))
    handler.maintain(force=True)
    assert handler.last_error is not None
    assert capsys.readouterr().err.count("diagnostics degraded") == 1


def test_coordinator_quiet_maintenance_runs_before_governance_backoff(tmp_path, monkeypatch):
    from agent_dispatch import coordinator_loops

    now = datetime.now(timezone.utc)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    handler.emit(record_at(now))
    now += timedelta(days=31)
    package = logging.getLogger("agent-dispatch")
    package.addHandler(handler)
    calls = []
    event_loop_thread = threading.get_ident()
    scan_threads = []
    original_scan = handler._scan

    def scan(path):
        scan_threads.append(threading.get_ident())
        assert threading.get_ident() != event_loop_thread
        return original_scan(path)

    monkeypatch.setattr(handler, "_scan", scan)

    async def sleep(_interval):
        if calls:
            raise asyncio.CancelledError
        calls.append("slept")

    async def backoff(*_args, **_kwargs):
        assert events(handler) == []
        return True

    monkeypatch.setattr(coordinator_loops.asyncio, "sleep", sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(coordinator_loops._gc_loop(
                None, 1.0, None,
                health=SimpleNamespace(current_interval=1.0, base_interval=1.0),
                governance_backoff=backoff,
            ))
        assert scan_threads
    finally:
        package.removeHandler(handler)
        handler.close()


def test_supervisor_quiet_maintenance_runs_before_governance_backoff(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_daemon

    now = datetime.now(timezone.utc)
    handler = logs.DiagnosticLog("supervisor", tmp_path, clock=lambda: now)
    handler.emit(record_at(now))
    now += timedelta(days=31)
    package = logging.getLogger("agent-dispatch")
    package.addHandler(handler)
    daemon = supervisor_daemon.SupervisorDaemon(SimpleNamespace(), machine="synthetic")
    monkeypatch.setattr(daemon, "_recheck_governance",
                        lambda _: {"status": "blocked", "reason": "synthetic"})
    monkeypatch.setattr(daemon, "shutdown", lambda: True)
    monkeypatch.setattr(supervisor_daemon, "daemon_audit", lambda *_a, **_k: None)
    try:
        assert daemon.serve(once=True, single_instance=False) == 0
        assert events(handler) == []
    finally:
        package.removeHandler(handler)
        handler.close()


def test_unavailable_identity_does_not_block_logging(tmp_path, monkeypatch):
    from agent_dispatch import companion

    def unavailable(_pid):
        raise OSError("synthetic unavailable OS identity")

    monkeypatch.setattr(companion, "process_start_token", unavailable)
    handler = logs.DiagnosticLog("coordinator", tmp_path)
    handler.emit(record_at(datetime.now(timezone.utc)))
    assert events(handler)[0]["process_start_token"] is None


def test_failed_replacement_keeps_records_and_reports_degraded(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    seed(handler.path, now - timedelta(days=31), now)
    original = handler.path.read_bytes()

    def locked(*_args):
        raise PermissionError("synthetic Windows reader denies delete sharing")

    monkeypatch.setattr(logs.os, "replace", locked)
    handler.maintain(force=True)
    assert handler.path.read_bytes() == original
    assert handler.last_error == "PermissionError"
    assert not list(handler.directory.glob("*.prune-*"))


def test_restart_reclaims_only_component_owned_abandoned_prunes(tmp_path):
    now = datetime.now(timezone.utc)
    directory = tmp_path / "logs" / "diagnostics"
    directory.mkdir(parents=True)
    abandoned = directory / (".coordinator.jsonl.prune-" + "a" * 32)
    abandoned.write_bytes(b"interrupted retained copy")
    archived_copy = directory / (
        ".coordinator.20260901T000000000000Z-" + "b" * 32 + ".jsonl.prune-" + "c" * 32
    )
    archived_copy.write_bytes(b"interrupted archive copy")
    other_owner = directory / (".supervisor.jsonl.prune-" + "a" * 32)
    other_owner.write_bytes(b"other component")
    foreign = directory / ".coordinator.jsonl.prune-operator"
    foreign.write_bytes(b"not a recognized temporary")
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    assert not abandoned.exists()
    assert not archived_copy.exists()
    assert other_owner.exists() and foreign.exists()
    assert handler.last_error is None


def test_unchanged_unexpired_archives_are_not_reparsed_or_copied(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    archive = handler.directory / (
        "coordinator.20261001T000000000000Z-" + "a" * 32 + ".jsonl"
    )
    seed(archive, now - timedelta(days=1))
    scans = []
    copies = []
    original = handler._scan
    original_open = type(archive).open

    def scan(path):
        scans.append(path)
        return original(path)

    def open_path(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "x" in mode:
            copies.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(handler, "_scan", scan)
    monkeypatch.setattr(type(archive), "open", open_path)
    handler.maintain(force=True)
    assert scans == [archive]
    assert not list(handler.directory.glob(".*.prune-*"))
    for _ in range(3):
        handler.maintain(force=True)
    assert scans == [archive]
    assert copies == []
    now += timedelta(days=29)
    handler.maintain(force=True)
    assert archive.exists()  # exact cutoff equality remains
    assert scans == [archive]
    now += timedelta(microseconds=1)
    handler.maintain(force=True)
    assert not archive.exists()
    assert scans == [archive]  # cached expiry, only the necessary rewrite/read


def test_changed_segment_invalidates_expiry_cache(tmp_path):
    now = datetime.now(timezone.utc)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    seed(handler.path, now)
    handler.maintain(force=True)
    seed(handler.path, now, now - timedelta(days=31))
    handler.maintain(force=True)
    assert len(events(handler)) == 1
    assert datetime.fromisoformat(events(handler)[0]["ts"]) == now


def test_discovery_honors_actual_lifecycle_routing_override(tmp_path, monkeypatch):
    root, routing = tmp_path / "installation", tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    routing.mkdir()
    for name in ("lifecycle.log", "lifecycle.log.1"):
        (routing / name).write_bytes(b"synthetic")
    report = logs.inventory()
    location = next(item for item in report["logs"]
                    if item["owner"].startswith("zdd lifecycle"))
    assert location["existing"] == [
        str(routing / "lifecycle.log"), str(routing / "lifecycle.log.1"),
    ]
    assert location["retention"] == "not-managed-by-application-handler"
    assert not root.exists()


def test_event_loop_emission_does_no_history_work_when_expiry_is_due(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    archive = handler.directory / (
        "coordinator.20260901T000000000000Z-" + "a" * 32 + ".jsonl"
    )
    seed(archive, now - timedelta(days=31))
    scans = []

    def scan(path):
        scans.append(path)
        raise AssertionError("history scan on the event-loop emission path")

    monkeypatch.setattr(handler, "_scan", scan)

    async def coroutine():
        handler.emit(record_at(now, "request coroutine remains independent of history"))
        await asyncio.sleep(0)

    asyncio.run(coroutine())
    assert scans == []
    assert handler.last_error is None
    assert archive.exists()  # only the off-thread owner may prune this history
    assert json.loads(handler.path.read_text(encoding="utf-8"))["message"].startswith("request")


def test_restart_rolls_unterminated_tail_without_corrupting_next_event(tmp_path):
    now = datetime.now(timezone.utc)
    path = tmp_path / "logs" / "diagnostics" / "coordinator.jsonl"
    seed(path, now)
    original = path.read_bytes() + b'{"ts":"interrupted'
    path.write_bytes(original)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    handler.emit(record_at(now, "readable after interrupted append"))
    assert json.loads(path.read_text(encoding="utf-8"))["message"] == (
        "readable after interrupted append"
    )
    archives = [p for p in handler._files() if p != path]
    assert len(archives) == 1
    assert archives[0].read_bytes() == original


def test_actual_lifespan_prunes_with_task_gc_disabled_without_reconciling(tmp_path, monkeypatch):
    from agent_dispatch import coordinator
    from agent_dispatch.queue import TaskQueue

    now = datetime.now(timezone.utc)
    handler = logs.DiagnosticLog("coordinator", tmp_path, clock=lambda: now)
    handler.emit(record_at(now))
    now += timedelta(days=31)
    monkeypatch.setattr(logs, "MAINTENANCE_SECONDS", 0.01)
    monkeypatch.setattr(coordinator, "_self_retire_settings", lambda: (False, 30, 3))
    monkeypatch.setattr(coordinator, "_abandoned_passive_reap_settings",
                        lambda: (False, 120, 600))
    monkeypatch.setattr(coordinator, "_self_update_settings", lambda: (False, 60, 3, 600))
    calls = []

    async def unexpected_gc(*_args, **_kwargs):
        calls.append("queue reconciliation")
        raise AssertionError("disabled task GC must not reconcile")

    monkeypatch.setattr(coordinator, "_run_supervised_cycle", unexpected_gc)
    app = coordinator.create_app(
        TaskQueue(tmp_path / "tasks.db"), sweep_interval=0,
        wake_interval=0, verification_interval=0, enable_mcp=False,
    )
    package = logging.getLogger("agent-dispatch")
    package.addHandler(handler)

    async def run():
        async with app.router.lifespan_context(app):
            for _ in range(200):
                if events(handler) == []:
                    break
                await asyncio.sleep(0.01)
            assert events(handler) == []
            assert calls == []
            assert app.state.loop_health["liveness_gc"].total_runs == 0

    try:
        asyncio.run(run())
    finally:
        package.removeHandler(handler)
        handler.close()


def test_explicit_retained_spool_api_preserves_fields_and_old_factory(tmp_path):
    from agent_dispatch import telemetry

    (tmp_path / "retained").mkdir()
    raw = tmp_path / "legacy.jsonl"
    event = {"ts": int(datetime.now(timezone.utc).timestamp() * 1000),
             "kind": "state", "name": "λ🚀", "nested": {"count": 1}}
    telemetry.make_spool_sink(raw)(event)
    original = raw.read_bytes()
    sink = telemetry.make_retained_spool_sink(tmp_path)
    sink(event)
    assert json.loads(sink.store.path.read_text(encoding="utf-8")) == event
    assert raw.read_bytes() == original
    assert sink.inventory()["active"] != str(raw)


def test_explicit_retained_spool_never_creates_parent(tmp_path):
    from agent_dispatch import telemetry

    missing = tmp_path / "owner-not-provisioned"
    sink = telemetry.make_retained_spool_sink(missing)
    sink({"kind": "state"})
    assert not missing.exists()
    assert sink.store.last_error is not None
