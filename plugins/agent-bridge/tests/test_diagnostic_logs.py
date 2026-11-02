"""Bridge's actual application adapter, with no deployed service or native adoption."""

from __future__ import annotations

import argparse
import asyncio
import json
import importlib
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import pytest
from single_instance_lease import OperationLock

from agent_bridge import diagnostic_logs as logs, telemetry


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "owned diagnostic writer did not finish"
        time.sleep(0.01)


@pytest.fixture
def handler(tmp_path, monkeypatch):
    importlib.import_module("agent_bridge.app")
    monkeypatch.setattr(logs, "_last_handler", None)
    result = logs.configure_file_logging(tmp_path)
    try:
        try:
            wait_for(lambda: result.initialized)
        except AssertionError as error:
            raise AssertionError(result.stats()) from error
        yield result
    finally:
        logs.close_file_logging(result)
        assert result.writer.join(2), "test-owned writer still running"
        monkeypatch.setattr(logs, "_last_handler", None)


def record(message="application λ 🚀", *args):
    return logging.LogRecord("agent-bridge.test", logging.INFO, "", 0, message, args, None)


def test_fresh_cli_discovery_is_json_only_read_only_and_names_native_fallbacks(tmp_path):
    root = tmp_path / "fresh"
    environment = dict(os.environ, AGENT_BRIDGE_CONFIG_DIR=str(root),
                       AGENT_BRIDGE_INSTALL_DIR=str(root))
    result = subprocess.run(
        [sys.executable, "-m", "agent_bridge", "logs"], env=environment,
        capture_output=True, timeout=25,  # functional cold-import smoke, within the runner's 30s item cap
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    report = json.loads(result.stdout)
    assert report["root"] == str(root.resolve())
    assert not report["logs"][0]["exists"]
    assert report["logs"][0]["write_health"] == "unknown-from-discovery"
    assert any("agent-bridge-err.log" in p for p in report["logs"][1]["locations"])
    assert report["logs"][1]["retention"] == "not-managed-by-application-handler"
    assert not root.exists()


def test_real_log_consumer_formats_offthread_and_keeps_explicit_attribution(handler):
    from zdd.diagnostics import process_start_time

    item = record("operation %s", "failed")
    item.correlation_id = "synthetic"
    item.private_extra = "not-an-approved-field"
    handler.handle(item)
    wait_for(lambda: handler.store.path.exists() and handler.store.path.stat().st_size > 0)
    event = json.loads(handler.store.path.read_text(encoding="utf-8"))
    assert event["message"] == "operation failed"
    assert event["component"] == "daemon" and event["service"] == "agent-bridge"
    assert event["pid"] == os.getpid()
    assert event["process_start_token"] == process_start_time(os.getpid())
    assert event["runtime_prefix"] == sys.prefix
    assert event["correlation_id"] == "synthetic"
    assert "private_extra" not in event
    assert datetime.fromisoformat(event["ts"]).utcoffset() == timedelta(0)


def test_unavailable_process_start_token_stays_explicitly_unknown(tmp_path, monkeypatch):
    import zdd.diagnostics

    monkeypatch.setattr(zdd.diagnostics, "process_start_time", lambda pid: None)
    monkeypatch.setattr(logs, "_last_handler", None)
    handler = logs.configure_file_logging(tmp_path)
    try:
        handler.handle(record())
        wait_for(lambda: handler.writer.stats()["delivered"] >= 1)
        event = json.loads(handler.store.path.read_text(encoding="utf-8"))
        assert event["pid"] == os.getpid()
        assert event["process_start_token"] is None
    finally:
        logs.close_file_logging(handler)
        assert handler.writer.join(2)
        monkeypatch.setattr(logs, "_last_handler", None)


def test_both_existing_bridge_logger_namespaces_use_one_application_writer(handler):
    logging.getLogger("agent-bridge.test").info("hyphen")
    logging.getLogger("agent_bridge.worktree_head").info("underscore")
    wait_for(lambda: handler.writer.stats()["delivered"] >= 2)
    events = [json.loads(line) for line in handler.store.path.read_text(
        encoding="utf-8").splitlines()]
    assert [event["message"] for event in events] == ["hyphen", "underscore"]
    assert {event["logger"] for event in events} == {
        "agent-bridge.test", "agent_bridge.worktree_head",
    }

@pytest.mark.asyncio
async def test_failed_readiness_diagnostic_survives_ordinary_control_cleanup(handler, tmp_db, tmp_path):
    from agent_bridge.plugin_readiness import PluginReadinessError, wait_plugin_readiness
    from test_plugin_readiness import claimed, expectation, register, reply

    register(tmp_db, tmp_path)
    task = asyncio.create_task(wait_plugin_readiness(tmp_db, "native", expectation(tmp_path)))
    control = await claimed(tmp_db, "native")
    reply(tmp_db, "native", control, status="failed", missing=["private-loadout"])
    with pytest.raises(PluginReadinessError, match="selected-plugin-disabled"):
        await task
    assert tmp_db.deregister_live_session("native")
    assert tmp_db.live_control_state("native", control["id"]) is None
    wait_for(lambda: handler.writer.stats()["delivered"] >= 1)
    events = [json.loads(line) for line in handler.store.path.read_text(encoding="utf-8").splitlines()]
    event = next(event for event in events if event["logger"] == "agent_bridge.plugin_readiness")
    assert f"control_id={control['id']}" in event["message"]
    assert "code=selected-plugin-disabled" in event["message"]
    assert "missing_count=1" in event["message"]
    assert "private-loadout" not in json.dumps(event)
    assert str(tmp_path) not in event["message"]


def test_held_file_mutex_and_handler_lock_do_not_block_event_loop(handler):
    release = threading.Event()
    entered = threading.Event()

    def hold_handler_lock():
        handler.acquire()
        entered.set()
        try:
            release.wait(3)
        finally:
            handler.release()

    thread = threading.Thread(target=hold_handler_lock)
    thread.start()
    assert entered.wait(1)
    try:
        with OperationLock(handler.store.directory / ".daemon.lock"):
            async def serving():
                started = time.monotonic()
                for index in range(20):
                    handler.handle(record("serving %d", index))
                    await asyncio.sleep(0)
                assert time.monotonic() - started < 0.5
                assert not release.is_set()
            asyncio.run(serving())
    finally:
        release.set()
        thread.join(2)
    wait_for(lambda: handler.writer.stats()["delivered"] >= 20)
    events = [json.loads(line) for line in handler.store.path.read_text(
        encoding="utf-8").splitlines()]
    assert len(events) == 20


def test_slow_storage_is_bounded_loss_not_a_serving_or_shutdown_wait(handler, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = handler.store.write_event

    def blocked(event):
        entered.set()
        assert release.wait(3)
        original(event)

    monkeypatch.setattr(handler.store, "write_event", blocked)
    handler.handle(record())
    assert entered.wait(1)
    try:
        async def serving():
            started = time.monotonic()
            for index in range(300):
                handler.handle(record("burst %d", index))
                await asyncio.sleep(0)
            assert time.monotonic() - started < 0.5
        asyncio.run(serving())
        assert handler.writer.stats()["full"] > 0
        started = time.monotonic()
        handler.close()
        assert time.monotonic() - started < 0.2
        assert not handler.writer.join(0)
        with pytest.raises(RuntimeError, match="has not stopped"):
            logs.close_file_logging(handler)
            logs.configure_file_logging(handler.store.directory)
    finally:
        release.set()
    assert handler.writer.join(2)
    assert handler.writer.stats()["abandoned"] > 0


def test_expired_history_and_rotation_are_worker_only(handler, monkeypatch):
    now = datetime.now(timezone.utc)
    handler.store.clock = lambda: now
    handler.store.max_bytes = 600
    archive = handler.store.directory / (
        "daemon.20260901T000000000000Z-" + "a" * 32 + ".jsonl"
    )
    archive.write_text(json.dumps({"ts": (now - timedelta(days=31)).isoformat()}) + "\n",
                       encoding="utf-8")
    scans = []
    original_scan = handler.store._scan
    producer = threading.get_ident()

    def scan(path):
        assert threading.get_ident() != producer
        scans.append(path)
        return original_scan(path)

    monkeypatch.setattr(handler.store, "_scan", scan)
    status_written = threading.Event()
    original_write = handler.store.write_event

    def write(event):
        original_write(event)
        if "diagnostic_buffer_callbacks" in event:
            status_written.set()

    monkeypatch.setattr(handler.store, "write_event", write)
    handler.store._last_maintenance = None
    async def serving():
        for index in range(8):
            handler.handle(record("rotate %d λ🚀", index))
            await asyncio.sleep(0)
        logs.maintain_file_logging()  # same enqueue called by the heartbeat owner
    asyncio.run(serving())
    accepted = handler.writer.stats()["accepted"]
    wait_for(lambda: handler.writer.stats()["delivered"] >= accepted and status_written.is_set())
    assert scans and not archive.exists()
    events = [json.loads(line) for path in handler.store._files()
              for line in path.read_text(encoding="utf-8").splitlines()]
    assert len([event for event in events if "diagnostic_buffer_callbacks" not in event]) == 8
    assert any("diagnostic_buffer_callbacks" in event for event in events)
    assert len(handler.store._files()) >= 2


def test_full_queue_cannot_starve_owned_retention_while_logging_continues(handler, monkeypatch):
    handler.handle(record("old-before-pressure"))
    wait_for(lambda: handler.writer.stats()["delivered"] >= 1)
    entered, release, status_written = threading.Event(), threading.Event(), threading.Event()
    original = handler.store.write_event

    def write(event):
        if event["message"] == "blocked callback":
            entered.set()
            assert release.wait(3)
        original(event)
        if "diagnostic_buffer_callbacks" in event:
            status_written.set()

    monkeypatch.setattr(handler.store, "write_event", write)
    handler.handle(record("blocked callback"))
    assert entered.wait(1)
    now = datetime.now(timezone.utc) + timedelta(days=31)
    handler.store.clock = lambda: now
    try:
        for index in range(128):
            item = record("new-pressure-%d", index)
            item.created = now.timestamp()
            handler.handle(item)
        before = handler.writer.stats()["full"]
        handler.maintain()  # deliberately dropped: no heartbeat/offer can save this pass
        assert handler.writer.stats()["full"] == before + 1
        accepted = handler.writer.stats()["accepted"]
    finally:
        release.set()
    try:
        wait_for(lambda: handler.writer.stats()["delivered"] >= accepted
                 and status_written.is_set(), timeout=10)
    except AssertionError as error:
        raise AssertionError(handler.stats()) from error
    events = [json.loads(line) for path in handler.store._files()
              for line in path.read_text(encoding="utf-8").splitlines()]
    assert all(event["message"] != "old-before-pressure" for event in events)
    assert len([event for event in events if event["message"].startswith("new-pressure-")]) == 128


def test_existing_buffer_mutex_is_nonblocking_and_nonprimitive_arguments_have_placeholders(handler):
    with handler.writer._lock:
        handler.handle(record())
    assert handler.writer.stats()["contended"] == 1
    item = record("%s", object())
    handler.handle(item)
    wait_for(lambda: handler.writer.stats()["delivered"] >= 1)
    assert handler.writer.stats()["invalid"] == 0
    event = json.loads(handler.store.path.read_text(encoding="utf-8"))
    assert event["message"] == "<object>" and event["argument_adaptation"]


def test_formatting_cannot_expand_unbounded_width_or_call_object_hooks(handler):
    handler.handle(record("%1000000000s", "bounded"))
    wait_for(lambda: handler.writer.stats()["sink_failed"] == 1)
    assert not handler.store.path.exists()
    from process_telemetry.buffered_logs import _validate_percent
    with pytest.raises(ValueError, match="mapping key"):
        _validate_percent("%(a(b))10000s")
    _validate_percent("literal %%(a(b))10000s")
    handler.handle(record("%(a(b))10000s", {"a(b)": "bounded"}))
    wait_for(lambda: handler.writer.stats()["sink_failed"] == 2)
    assert not handler.store.path.exists()
    handler.handle(record("%*s", 2**62, "bounded"))
    wait_for(lambda: handler.writer.stats()["sink_failed"] == 3)
    assert not handler.store.path.exists()


def test_actual_registry_failure_with_path_and_exception_is_retained(handler, tmp_path, monkeypatch):
    from agent_bridge.agent_registry import resolve_repo_remote

    registry = tmp_path / "synthetic-registry.yaml"
    registry.write_text("repos: [", encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    assert resolve_repo_remote("synthetic") is None
    wait_for(lambda: handler.writer.stats()["delivered"] >= 1)
    event = json.loads(handler.store.path.read_text(encoding="utf-8"))
    assert event["message"].startswith("Failed to parse repos.yaml at <")
    assert "Path>" in event["message"] and "Error>" in event["message"]
    assert event["argument_adaptation"]
    assert handler.writer.stats()["invalid"] == 0


def test_placeholder_adaptation_never_calls_argument_formatting_or_equality_hooks(handler):
    class Argument:
        def __str__(self):
            raise AssertionError("no str hook")

        def __repr__(self):
            raise AssertionError("no repr hook")

        def __eq__(self, _other):
            raise AssertionError("no equality hook")

    handler.handle(record("operation failed: %s", Argument()))
    wait_for(lambda: handler.writer.stats()["delivered"] >= 1)
    event = json.loads(handler.store.path.read_text(encoding="utf-8"))
    assert event["message"] == "operation failed: <Argument>"


def test_daemon_bootstrap_preserves_primary_failure_and_closes_owner(monkeypatch, handler):
    from agent_bridge import service_start_cli

    monkeypatch.setattr(logs, "configure_file_logging", lambda: handler)
    def fail(*_args, **kwargs):
        kwargs["logging_initializer"]()
        raise ValueError("primary-startup-failure")
    monkeypatch.setattr(service_start_cli, "_run_start", fail)
    with pytest.raises(ValueError, match="primary-startup-failure"):
        service_start_cli._cmd_start(argparse.Namespace())
    assert handler.writer.join(2)
    assert not logs.logging_status()["configured"]


def test_failed_logging_bootstrap_never_replaces_primary_startup_result(monkeypatch, capsys):
    from agent_bridge import service_start_cli

    def unavailable():
        raise OSError("synthetic logger initialization failure")
    monkeypatch.setattr(logs, "configure_file_logging", unavailable)
    def primary(*_args, **kwargs):
        kwargs["logging_initializer"]()
        return "primary"
    monkeypatch.setattr(service_start_cli, "_run_start", primary)
    service_start_cli._cmd_start(argparse.Namespace())
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "diagnostics unavailable" in captured.err


@pytest.mark.parametrize("primary_error", [False, True])
def test_broken_fallback_stderr_cannot_change_primary_startup(monkeypatch, primary_error):
    from agent_bridge import service_start_cli

    class BrokenStderr:
        def write(self, _text):
            raise OSError("synthetic closed stderr")

        def flush(self):
            pass

    def unavailable():
        raise OSError("synthetic diagnostics unavailable")

    calls = []
    def primary(*_args, **kwargs):
        kwargs["logging_initializer"]()
        calls.append("primary")
        if primary_error:
            raise ValueError("primary-result")

    monkeypatch.setattr(logs, "configure_file_logging", unavailable)
    monkeypatch.setattr(service_start_cli, "_run_start", primary)
    monkeypatch.setattr(service_start_cli.sys, "stderr", BrokenStderr())
    if primary_error:
        with pytest.raises(ValueError, match="primary-result"):
            service_start_cli._cmd_start(argparse.Namespace())
    else:
        service_start_cli._cmd_start(argparse.Namespace())
    assert calls == ["primary"]


def test_diagnostic_cleanup_failure_cannot_replace_primary_failure(monkeypatch):
    from agent_bridge import service_start_cli

    monkeypatch.setattr(logs, "configure_file_logging", lambda: object())
    def broken_close(_handler):
        raise OSError("synthetic diagnostic close failure")
    monkeypatch.setattr(logs, "close_file_logging", broken_close)
    def primary(*_args, **kwargs):
        kwargs["logging_initializer"]()
        raise ValueError("primary-result")
    monkeypatch.setattr(service_start_cli, "_run_start", primary)
    with pytest.raises(ValueError, match="primary-result"):
        service_start_cli._cmd_start(argparse.Namespace())


def test_retained_spool_api_preserves_shape_and_never_adopts_legacy_path(tmp_path):
    from process_telemetry import diagnostic_logs as shared

    raw = tmp_path / "legacy.jsonl"
    legacy = telemetry.make_spool_sink(raw)
    event = {"ts": int(time.time() * 1000), "kind": "state", "message": "λ🚀",
             "nested": {"count": 1}}
    legacy(event)
    original = raw.read_bytes()
    (tmp_path / "retained").mkdir()
    sink = telemetry.make_retained_spool_sink(tmp_path)
    sink(event)
    assert json.loads(sink.store.path.read_text(encoding="utf-8")) == event
    assert raw.read_bytes() == original
    assert sink.store.path != raw
    old = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp() * 1000
    sink.store.path.write_text(json.dumps({"ts": old}) + "\n", encoding="utf-8")
    sink.maintain(force=True)
    assert sink.store.path.read_bytes() == b""
    calls = []
    original_append = shared.append_bytes
    try:
        shared.append_bytes = lambda path, data: calls.append((path, data))
        sink(event)
    finally:
        shared.append_bytes = original_append
    assert len(calls) == 1 and calls[0][1].endswith(b"\n")


def test_retained_spool_missing_owner_directory_does_not_create_parents(tmp_path):
    missing = tmp_path / "not-provisioned"
    sink = telemetry.make_retained_spool_sink(missing)
    sink({"kind": "state"})
    sink.maintain(force=True)
    assert not missing.exists()
    assert sink.store.last_error is not None


def test_exception_adapter_never_serializes_raw_exception_args(handler):
    class RedactedError(Exception):
        def __str__(self):
            return "redacted"

    try:
        raise RedactedError("synthetic-private-exception-argument")
    except RedactedError:
        item = record("bounded operation failure")
        item.exc_info = sys.exc_info()
        handler.handle(item)
    wait_for(lambda: handler.store.path.exists() and handler.store.path.stat().st_size > 0)
    raw = handler.store.path.read_text(encoding="utf-8")
    event = json.loads(raw)
    assert event["exception_type"] == "RedactedError"
    assert event["exception_frames"]
    assert "synthetic-private-exception-argument" not in raw
    assert "exception_args" not in event


def test_initializer_is_once_offthread_and_close_never_joins_it():
    from process_telemetry.buffer import BufferedSink

    entered, release = threading.Event(), threading.Event()
    calls = []
    producer = threading.get_ident()

    def initialize():
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3)

    writer = BufferedSink(lambda _event: None, on_start=initialize).start()
    try:
        assert entered.wait(1)
        writer({"kind": "state"})
        started = time.monotonic()
        writer.close()
        assert time.monotonic() - started < 0.2
        assert not writer.join(0)
        assert calls != [producer]
    finally:
        release.set()
    assert writer.join(2)
    assert len(calls) == 1
    assert writer.stats()["abandoned"] == 1


def test_unavailable_storage_maintenance_attempts_are_debounced_on_idle_worker(tmp_path):
    from process_telemetry.buffered_logs import BufferedDiagnosticLog
    from process_telemetry.diagnostic_logs import DiagnosticLog

    now = datetime.now(timezone.utc)
    attempts, requests = [], []
    @contextmanager
    def unavailable(_path):
        attempts.append(threading.get_ident())
        raise PermissionError("synthetic unavailable diagnostics")
        yield

    store = DiagnosticLog(
        "fixture", tmp_path / "unavailable", service="agent-bridge",
        custody=unavailable, clock=lambda: now, create_parent=False,
        initial_maintenance=False,
    )
    original = store.maintain
    def maintain(**kwargs):
        requests.append(None)
        original(**kwargs)
    store.maintain = maintain
    owner = BufferedDiagnosticLog(store)
    try:
        wait_for(lambda: owner.initialized and len(requests) >= 5)
        assert len(attempts) == 1
        assert store.last_error == "PermissionError"
        now += timedelta(seconds=61)
        wait_for(lambda: len(attempts) >= 3)  # one retry plus best-effort status append
        count = len(attempts)
        for _ in range(10):
            store.maintain()
        assert len(attempts) == count
        store.maintain(force=True)
        assert len(attempts) == count + 1
    finally:
        owner.close()
        assert owner.writer.join(2)


def test_existing_worker_persists_loss_counters_as_ordinary_diagnostic_records(handler):
    with handler.writer._lock:
        handler.handle(record())
    handler.store._last_maintenance = None
    handler.maintain()
    wait_for(lambda: handler.writer.stats()["delivered"] >= 1)
    event = json.loads(handler.store.path.read_text(encoding="utf-8"))
    assert event["diagnostic_buffer_callbacks"]["contended"] == 1
    assert event["message"] == "diagnostic logging status"
    assert event["pid"] == os.getpid()


def test_real_retained_spool_processes_rotate_unicode_without_lost_rows(tmp_path):
    (tmp_path / "retained").mkdir()
    code = """
import sys
from agent_bridge.telemetry import make_retained_spool_sink
s = make_retained_spool_sink(sys.argv[1])
s.store.max_bytes = 500
for i in range(12):
    s({"kind":"state","id":sys.argv[2]+":"+str(i),"label":"λ🚀"})
    if i % 4 == 0:
        s.maintain(force=True)
assert s.store.last_error is None, s.store.last_error
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
            out, err = child.communicate(timeout=20)
            assert child.returncode == 0, err.decode("utf-8", errors="replace")
            assert out == b""
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
    sink = telemetry.make_retained_spool_sink(tmp_path)
    events = [json.loads(line) for path in sink.store._files()
              for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 36
    assert {event["id"] for event in events} == {
        f"{worker}:{index}" for worker in range(3) for index in range(12)
    }
    assert all(event["label"] == "λ🚀" for event in events)


def test_existing_owned_worker_maintains_quiet_logs_while_application_backs_off(
    tmp_path, monkeypatch,
):
    from unittest.mock import AsyncMock
    from agent_bridge import app as app_module
    from agent_bridge.agent_registry_resolver import AgentResolver
    from agent_bridge.models import ServiceConfig

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")
    monkeypatch.setattr(app_module, "_HEARTBEAT_INTERVAL_S", 0.05)
    monkeypatch.setattr(app_module, "daemon_resolver", lambda _cfg: AgentResolver({}, {}))
    monkeypatch.setattr(app_module, "start_agent_roster_cache", AsyncMock())
    monkeypatch.setattr(app_module, "stop_agent_roster_cache", AsyncMock())
    monkeypatch.setattr(app_module, "start_worktree_cache", lambda *_a: None)
    monkeypatch.setattr(app_module, "stop_worktree_cache", AsyncMock())
    monkeypatch.setattr(app_module, "_start_credential_relay", AsyncMock(return_value=None))
    monkeypatch.setattr(app_module, "shutdown_ssh", AsyncMock())
    monkeypatch.setattr(app_module, "_governance_backoff", AsyncMock(return_value=True))
    app = app_module.create_app(
        config=ServiceConfig(port=0, db_path=str(tmp_path / "lifespan.db"),
                             enable_credential_relay=False),
        token="synthetic",
    )
    monkeypatch.setattr(logs, "_last_handler", None)
    handler = logs.configure_file_logging(tmp_path)
    wait_for(lambda: handler.initialized)
    handler.handle(record("quiet-before-expiry"))
    wait_for(lambda: handler.store.path.exists() and handler.store.path.stat().st_size > 0)
    status_written = threading.Event()
    original_write = handler.store.write_event

    def write(event):
        original_write(event)
        if "diagnostic_buffer_callbacks" in event:
            status_written.set()

    monkeypatch.setattr(handler.store, "write_event", write)
    handler.store.clock = lambda: datetime.now(timezone.utc) + timedelta(days=31)
    async def run():
        async with app.router.lifespan_context(app):
            for _ in range(800):
                if status_written.is_set() and app_module._governance_backoff.await_count:
                    break
                await asyncio.sleep(0.01)
            events = [json.loads(line) for path in handler.store._files()
                      for line in path.read_text(encoding="utf-8").splitlines()]
            assert any("diagnostic_buffer_callbacks" in event for event in events)
            assert all(event["message"] != "quiet-before-expiry" for event in events)
    try:
        asyncio.run(run())
    finally:
        logs.close_file_logging(handler)
        assert handler.writer.join(2)


def test_bridge_native_stdout_stderr_handles_remain_untouched_during_rotation(handler):
    root = handler.store.directory.parent.parent
    stdout, stderr = root / "agent-bridge.log", root / "agent-bridge-err.log"
    with stdout.open("ab") as out, stderr.open("ab") as err:
        out.write(b"native stdout before\n")
        err.write(b"native stderr before\n")
        out.flush()
        err.flush()
        handler.store.max_bytes = 500
        for index in range(5):
            handler.handle(record("new application %d", index))
        wait_for(lambda: handler.writer.stats()["delivered"] >= 5)
        handler.maintain()
        wait_for(lambda: handler.writer.stats()["delivered"] >= 6)
        out.write(b"native stdout after\n")
        err.write(b"native stderr after\n")
    assert stdout.read_bytes() == b"native stdout before\nnative stdout after\n"
    assert stderr.read_bytes() == b"native stderr before\nnative stderr after\n"


def test_real_start_adopts_after_uvicorn_dictconfig_and_closes_owned_writer(tmp_path, monkeypatch):
    from agent_bridge import __main__ as core, config, service_start_cli, singleton, watchdog, winjob
    from agent_bridge.models import ServiceConfig

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(logs, "_last_handler", None)
    cfg = ServiceConfig(port=0, db_path=str(tmp_path / "startup.db"),
                        enable_credential_relay=False)
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(config, "migrate_config", lambda value: value)
    monkeypatch.setattr(config, "write_default_config", lambda *_a: None)
    monkeypatch.setattr(config, "load_or_create_auth_token", lambda: "synthetic")
    monkeypatch.setattr(core, "_active_endpoint_is_forward", lambda: False)
    monkeypatch.setattr(singleton.SingleInstance, "acquire", lambda _self: None)
    monkeypatch.setattr(singleton.SingleInstance, "release", lambda _self: None)
    monkeypatch.setattr(winjob, "setup_kill_on_close_job", lambda: None)
    monkeypatch.setattr(watchdog, "arm_serving_watchdog", lambda *_a, **_k: None)
    real_config = service_start_cli.uvicorn.Config
    real_logging = logs.configure_file_logging
    stages = []
    owners = []

    def server_config(*args, **kwargs):
        result = real_config(*args, **kwargs)  # actual dictConfig/handler-close boundary
        stages.append("uvicorn configured")
        return result

    def initialize():
        assert stages == ["uvicorn configured"]
        result = real_logging(tmp_path)
        owners.append(result)
        return result

    class Server:
        def __init__(self, _config):
            assert owners

        def run(self, **_kwargs):
            logging.getLogger("agent-bridge").info("post-uvicorn-config")
            wait_for(lambda: owners[0].store.path.exists()
                     and owners[0].store.path.stat().st_size > 0)

    monkeypatch.setattr(service_start_cli.uvicorn, "Config", server_config)
    monkeypatch.setattr(service_start_cli.uvicorn, "Server", Server)
    monkeypatch.setattr(logs, "configure_file_logging", initialize)
    service_start_cli._cmd_start(argparse.Namespace(
        port=0, bind=None, idle_shutdown=None, passive=False,
    ))
    assert not logs.logging_status()["configured"]
    assert owners[0].writer.closed and owners[0].writer.join(2)
    assert json.loads(owners[0].store.path.read_text(encoding="utf-8"))["message"] == (
        "post-uvicorn-config"
    )


@pytest.mark.parametrize("invalid", [1e20, -1e20, "os-range-error"])
def test_out_of_range_timestamp_is_preserved_without_blocking_expiry(tmp_path, invalid):
    from process_telemetry.retained_spool import RetainedSpool, milliseconds_timestamp

    def decoder(event):
        if event["ts"] == "os-range-error":
            raise OSError("synthetic platform timestamp range failure")
        return milliseconds_timestamp(event)

    (tmp_path / "retained").mkdir()
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    sink = RetainedSpool(
        tmp_path, "fixture", service="agent-bridge", custody=logs.operation_custody,
        clock=lambda: now, timestamp_decoder=decoder,
    )
    sink({"ts": int(now.timestamp() * 1000), "id": "expires"})
    unknown = {"ts": invalid, "id": "unknown"}
    sink(unknown)
    assert unknown in [json.loads(line) for line in sink.store.path.read_text(
        encoding="utf-8").splitlines()]
    now += timedelta(days=31)
    sink.maintain(force=True)
    remaining = [json.loads(line) for path in sink.store._files()
                 for line in path.read_text(encoding="utf-8").splitlines()]
    assert remaining == [unknown]
    assert sink.store.last_error == "ValueError"


def test_explicit_millisecond_decoder_exact_cutoff_timezone_and_skew(tmp_path):
    from process_telemetry.retained_spool import RetainedSpool

    (tmp_path / "retained").mkdir()
    now = datetime(2026, 10, 23, 12, tzinfo=timezone(timedelta(hours=-7)))
    original = int(now.timestamp() * 1000)
    sink = RetainedSpool(
        tmp_path, "fixture", service="agent-bridge", custody=logs.operation_custody,
        clock=lambda: now,
    )
    for offset in (-1, 0, 1, 2 * 86400 * 1000):
        sink({"ts": original + offset, "id": offset})
    now += timedelta(days=30)
    sink.maintain(force=True)
    events = [json.loads(line) for line in sink.store.path.read_text(
        encoding="utf-8").splitlines()]
    assert [event["id"] for event in events] == [0, 1, 2 * 86400 * 1000]
    now -= timedelta(days=1)
    sink.maintain(force=True)
    assert len(sink.store.path.read_text(encoding="utf-8").splitlines()) == 3
    sink({"ts": "not-an-epoch-millisecond", "id": "unknown"})
    now += timedelta(days=60)
    sink.maintain(force=True)
    remaining = [json.loads(line) for path in sink.store._files()
                 for line in path.read_text(encoding="utf-8").splitlines()]
    assert remaining == [{"ts": "not-an-epoch-millisecond", "id": "unknown"}]
    assert sink.store.last_error == "ValueError"
