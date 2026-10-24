"""Record-age retention and the real local consumer contract; no live service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
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
    assert str(native) in logs.inventory(tmp_path)["logs"][-1]["existing"]


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
