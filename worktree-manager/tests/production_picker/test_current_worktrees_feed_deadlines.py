"""Real-child deadline recovery, with timer delivery qualified separately."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from test_current_worktrees_feed import snapshot, update
from worktree_manager.production_picker.picker_tui import data_ssh
from worktree_manager.production_picker.picker_tui import loader_current_feed as loader_feed


def _test_budget_factor():
    value = os.environ.get("WORKTREES_TEST_BUDGET_FACTOR", "1")
    if value not in {"1", "2", "3"}:
        raise ValueError("WORKTREES_TEST_BUDGET_FACTOR must be 1, 2 or 3")
    return int(value)


@pytest.mark.parametrize("value,expected", [(None, 1), ("1", 1), ("2", 2), ("3", 3)])
def test_test_budget_factor_explicit_values(monkeypatch, value, expected):
    monkeypatch.delenv("WORKTREES_TEST_BUDGET_FACTOR", raising=False)
    if value is not None:
        monkeypatch.setenv("WORKTREES_TEST_BUDGET_FACTOR", value)
    assert _test_budget_factor() == expected
    assert 0.15 * expected < 0.4 * expected < 2 * expected
    assert 0.1 * expected < 0.4 * expected
    assert 10 * expected > 5 * expected


@pytest.mark.parametrize("value", ["", "0", "4", "-1", "1.5", "nan", "2.0"])
def test_test_budget_factor_rejects_other_values(monkeypatch, value):
    monkeypatch.setenv("WORKTREES_TEST_BUDGET_FACTOR", value)
    with pytest.raises(ValueError, match="must be 1, 2 or 3"):
        _test_budget_factor()


def wait_until(predicate, timeout=5, *, factor=1):
    deadline = time.monotonic() + timeout * factor
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01 * factor)
    pytest.fail("feed did not reach the expected deadline/recovery boundary")


def run_loader(monkeypatch, source, script, *, events=None):
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_STREAM", raising=False)
    monkeypatch.setattr(loader_feed, "feed_argv", lambda _: [sys.executable, "-u", "-c", script])
    loader = data_ssh.LiveLoader([source])
    children = []
    spawn = loader._spawn_stream

    def capture_child(argv):
        if events is not None:
            events.append({"event": "spawn-start", "at": time.monotonic()})
        child = spawn(argv)
        children.append(child)
        if events is not None:
            events.append({"event": "spawn-returned", "at": time.monotonic(), "pid": child.pid})
        return child

    monkeypatch.setattr(loader, "_spawn_stream", capture_child)
    worker = threading.Thread(target=loader._load_one, args=(source, 0))
    worker.start()
    return loader, worker, children


def stop_loader(loader, worker, children, *, factor=1):
    loader.cancel()
    worker.join(timeout=3 * factor)
    assert not worker.is_alive()
    assert loader._procs == [] and loader._held_sources == set()
    assert children and all(child.poll() is not None for child in children)


def _record_state(loader):
    return [{
        "id": value.get("id"), "title": value.get("title"),
        "dirty": (value.get("raw") or {}).get("dirty"),
        "generation": ((value.get("raw") or {}).get("_projection") or {}).get("generation"),
    } for value in loader.records()]


def _fixture_budgets(source, factor):
    return {
        "source_startup": source.timeout, "observer": 5 * factor,
        "publication": loader_feed.INITIAL_TIMEOUT, "idle": loader_feed.IDLE_TIMEOUT,
        "cleanup": 3 * factor,
    }


@pytest.mark.parametrize("idle", [5.0, 0.8])
def test_test_budget_report_records_actual_overrides(monkeypatch, idle):
    monkeypatch.setattr(loader_feed, "INITIAL_TIMEOUT", 0.3)
    monkeypatch.setattr(loader_feed, "IDLE_TIMEOUT", idle)
    source = data_ssh.Source("host", "Win", None, local=True, timeout=4)
    assert _fixture_budgets(source, 2) == {
        "source_startup": 4, "observer": 10, "publication": 0.3, "idle": idle, "cleanup": 6,
    }


def _report(request, factor, started, events, source, loader, worker, children, failure,
            cleanup_failure, armed=None, synthetic_delays=None):
    if not (directory := os.environ.get("F1_NATIVE_REPORT_DIR")):
        return
    directory = Path(directory).resolve()
    assert directory.is_relative_to(Path(__file__).resolve().parents[3] / ".test-venvs")
    assert directory.is_dir()
    key = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
    success = failure is None and cleanup_failure is None
    (directory / f"manager-{key}.json").write_text(json.dumps({
        "nodeid": request.node.nodeid,
        "source_commit": os.environ.get("F1_SOURCE_COMMIT"),
        "production_ancestor": os.environ.get("F1_PRODUCTION_ANCESTOR"),
        "environment_reference": os.environ.get("F1_ENVIRONMENT_REFERENCE"),
        "test_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "interpreter": sys.executable, "python_version": sys.version,
        "budget_factor": factor, "nominal_run_exercised": factor == 1,
        "nominal_qualified": factor == 1 and success,
        "contended_test_pass": success,
        "started_monotonic": started, "finished_monotonic": time.monotonic(),
        "events": events, "watchdog_budgets": armed,
        "fixture_budgets": _fixture_budgets(source, factor),
        "synthetic_delays": synthetic_delays,
        "records": _record_state(loader), "state": loader._state.get(source.cache_key),
        "source_error": loader.error_for_source(source.source_id),
        "children": [{"pid": child.pid, "argv": child.args, "returncode": child.poll()}
                     for child in children],
        "cleanup": {
            "worker_joined": not worker.is_alive(),
            "processes_empty": loader._procs == [], "held_sources_empty": not loader._held_sources,
            "all_children_exited": all(child.poll() is not None for child in children),
        },
        "failure": None if failure is None else {
            "type": type(failure).__name__, "message": str(failure),
        },
        "cleanup_failure": None if cleanup_failure is None else {
            "type": type(cleanup_failure).__name__, "message": str(cleanup_failure),
        },
    }, indent=2), encoding="utf-8")


@pytest.mark.parametrize("local", [True, False])
def test_feed_process_startup_uses_source_budget_not_publication_budget(monkeypatch, local, request):
    factor = _test_budget_factor()
    started, events = time.monotonic(), []
    monkeypatch.setattr(loader_feed, "INITIAL_TIMEOUT", 0.15 * factor)
    frames = snapshot([{"id": "one", "status": "active", "dirty": 2}])
    script = (
        "import json,time\n"
        f"time.sleep({0.4 * factor!r})\n"
        f"for frame in {frames!r}: print(json.dumps(frame),flush=True)\n"
        f"time.sleep({10 * factor!r})\n"
    )
    source = data_ssh.Source("host", "Win", None, local=local, timeout=2 * factor)
    loader, worker, children = run_loader(monkeypatch, source, script, events=events)
    try:
        wait_until(lambda: bool(loader.records()), factor=factor)
        events.append({"event": "records-visible", "at": time.monotonic()})
        assert loader.records()[0]["id"] == "one"
        assert loader.records()[0]["raw"]["dirty"] == 2
        assert loader._state[source.cache_key] == "ready"
        assert not loader.error_for_source(source.source_id)
        assert len(children) == 1
    finally:
        failure, cleanup_failure = sys.exc_info()[1], None
        try:
            stop_loader(loader, worker, children, factor=factor)
        except BaseException as exc:
            cleanup_failure = exc
            raise
        finally:
            _report(request, factor, started, events, source, loader, worker, children,
                    failure, cleanup_failure,
                    synthetic_delays={"startup": 0.4 * factor, "hold": 10 * factor})


@pytest.mark.parametrize(
    "boundary", ["startup", "initial-publication", "publication", "partial-frame", "heartbeat"],
)
def test_feed_transient_deadline_recovers_without_partial_or_empty_overwrite(
    monkeypatch, tmp_path, boundary, request,
):
    """Force one expiry after its wire boundary; do not time healthy retries.

    This qualifies recovery, atomic publication and child custody after a
    watchdog reports expiry, not wall-clock timer delivery. Both children
    remain test-owned until forced expiry or cancellation. The startup and
    heartbeat tests separately exercise the actual production watchdog.
    """
    factor = _test_budget_factor()
    started, events, armed = time.monotonic(), [], []
    monkeypatch.setattr(loader_feed, "INITIAL_TIMEOUT", 0.15 * factor)
    monkeypatch.setattr(loader_feed, "IDLE_TIMEOUT", 0.4 * factor)
    marker = tmp_path / "first-attempt"
    boundary_flushed = tmp_path / "first-boundary-flushed"
    release_retry = tmp_path / "release-retry"
    watchdogs = []

    class ControlledWatchdog:
        def __init__(self, kill, timeout):
            self.kill = kill
            self.initial_budget = timeout
            self.armed = []
            self.expired = False
            self.closed = False
            watchdogs.append(self)

        def arm(self, timeout):
            self.armed.append(timeout)
            armed.append(timeout)
            events.append({"event": "watchdog-armed", "at": time.monotonic(), "budget": timeout})

        def expire(self):
            assert not self.closed and not self.expired
            self.expired = True
            self.kill()

        def close(self):
            self.closed = True

    monkeypatch.setattr(loader_feed, "_Watchdog", ControlledWatchdog)
    first = snapshot([{"id": "one", "status": "active", "dirty": 2}])
    pending = update([{"id": "one", "status": "active", "dirty": 9}])
    second = snapshot(
        [{"id": "two", "status": "active", "dirty": 3}], generation="b" * 32,
    )
    script = (
        "import json,time,pathlib,sys\n"
        f"marker=pathlib.Path({str(marker)!r})\n"
        "second=marker.exists()\nmarker.write_text('started')\n"
        "if not second:\n"
        f" if {boundary!r} not in ('startup','initial-publication'):\n"
        f"  for frame in {first!r}: print(json.dumps(frame),flush=True)\n"
        f" if {boundary!r}=='initial-publication':\n"
        f"  for frame in {first[:-1]!r}: print(json.dumps(frame),flush=True)\n"
        f" if {boundary!r}=='publication':\n"
        f"  for frame in {pending[:-1]!r}: print(json.dumps(frame),flush=True)\n"
        f" if {boundary!r}=='partial-frame':\n"
        "  sys.stdout.write('{\"type\":\"worktree\"'); sys.stdout.flush()\n"
        f" pathlib.Path({str(boundary_flushed)!r}).write_text('flushed')\n"
        " while True: time.sleep(.1)\n"
        "else:\n"
        f" gate=pathlib.Path({str(release_retry)!r})\n"
        " while not gate.exists(): time.sleep(.01)\n"
        f" for frame in {second!r}: print(json.dumps(frame),flush=True)\n"
        " while True:\n"
        f"  time.sleep({0.1 * factor!r})\n"
        "  print(json.dumps({'type':'heartbeat','generation':'b'*32,'cursor':1}),flush=True)\n"
    )
    source = data_ssh.Source("host", "Win", None, local=True, timeout=2 * factor)
    loader, worker, children = run_loader(monkeypatch, source, script, events=events)
    try:
        wait_until(lambda: len(watchdogs) == 1 and boundary_flushed.exists(), factor=factor)
        first_watchdog = watchdogs[0]
        expected_arms = {
            "startup": [],
            "initial-publication": [loader_feed.INITIAL_TIMEOUT],
            "publication": [
                loader_feed.INITIAL_TIMEOUT, loader_feed.IDLE_TIMEOUT,
                loader_feed.INITIAL_TIMEOUT,
            ],
            "partial-frame": [loader_feed.INITIAL_TIMEOUT, loader_feed.IDLE_TIMEOUT],
            "heartbeat": [loader_feed.INITIAL_TIMEOUT, loader_feed.IDLE_TIMEOUT],
        }[boundary]
        wait_until(lambda: first_watchdog.armed == expected_arms, factor=factor)
        assert first_watchdog.initial_budget == source.timeout
        first_watchdog.expire()
        wait_until(lambda: bool(loader.error_for_source(source.source_id)), factor=factor)
        events.append({"event": "deadline-visible", "at": time.monotonic(),
                       "records": _record_state(loader),
                       "error": loader.error_for_source(source.source_id)})
        assert "deadline" in loader.error_for_source(source.source_id)
        assert loader.read_status_for_source(source.source_id)["failure_kind"] == (
            "connection" if boundary == "startup" else "content"
        )
        if boundary in {"startup", "initial-publication"}:
            assert loader.records() == []
            assert loader._state[source.cache_key] == "failed"
            if boundary == "initial-publication":
                assert first_watchdog.armed == [loader_feed.INITIAL_TIMEOUT]
        else:
            assert loader.records()[0]["id"] == "one"
            assert loader.records()[0]["raw"]["dirty"] == 2
            assert loader.records()[0]["raw"]["_availability"]["status"] == "stale"
        release_retry.write_text("release", encoding="utf-8")
        wait_until(lambda: bool(loader.records()) and loader.records()[0]["id"] == "two",
                   factor=factor)
        events.append({"event": "recovery-visible", "at": time.monotonic()})
        assert loader.records()[0]["raw"]["dirty"] == 3
        assert loader.records()[0]["raw"]["_projection"]["generation"] == "b" * 32
        assert not loader.error_for_source(source.source_id)
        assert loader._state[source.cache_key] == "ready"
        assert len(children) == 2 and children[0].poll() is not None
        assert len(watchdogs) == 2 and first_watchdog.closed
        assert not watchdogs[1].expired
    finally:
        failure, cleanup_failure = sys.exc_info()[1], None
        try:
            stop_loader(loader, worker, children, factor=factor)
        except BaseException as exc:
            cleanup_failure = exc
            raise
        finally:
            _report(request, factor, started, events, source, loader, worker, children,
                    failure, cleanup_failure, armed,
                    synthetic_delays={"controlled_expiry": True, "heartbeat": 0.1 * factor})
    assert all(watchdog.closed for watchdog in watchdogs)


def test_feed_heartbeat_expiry_uses_real_watchdog_after_committed_snapshot(monkeypatch):
    """Time actual idle expiry; enrollment/publication are controlled setup."""
    monkeypatch.setattr(loader_feed, "INITIAL_TIMEOUT", 0.15)
    monkeypatch.setattr(loader_feed, "IDLE_TIMEOUT", 0.4)
    real_watchdog = loader_feed._Watchdog
    watchdogs, observed = [], []
    fault = threading.Event()

    class HeartbeatWatchdog:
        def __init__(self, kill, timeout):
            self.kill = kill
            self.initial_budget = timeout
            self.armed = []
            self.timer = None
            watchdogs.append(self)

        @property
        def expired(self):
            return self.timer is not None and self.timer.expired

        def arm(self, timeout):
            self.armed.append(timeout)
            if self.timer is not None:
                self.timer.arm(timeout)
            elif timeout == loader_feed.IDLE_TIMEOUT:
                self.timer = real_watchdog(self.kill, timeout)

        def close(self):
            if self.timer is not None:
                self.timer.close()

    monkeypatch.setattr(loader_feed, "_Watchdog", HeartbeatWatchdog)
    feed_error = loader_feed.CurrentFeedMixin._feed_error

    def capture_expiry(loader, source, generation, reason, **metadata):
        feed_error(loader, source, generation, reason, **metadata)
        observed.append((reason, loader.records(), loader._state[source.cache_key]))
        loader.cancel()
        fault.set()

    monkeypatch.setattr(loader_feed.CurrentFeedMixin, "_feed_error", capture_expiry)
    frames = snapshot([{"id": "one", "status": "active", "dirty": 2}])
    script = (
        "import json,time\n"
        f"for frame in {frames!r}: print(json.dumps(frame),flush=True)\n"
        "while True: time.sleep(.1)\n"
    )
    source = data_ssh.Source("host", "Win", None, local=True, timeout=2)
    loader, worker, children = run_loader(monkeypatch, source, script)
    try:
        assert fault.wait(5), "actual heartbeat watchdog did not report expiry"
        reason, rows, state = observed[0]
        assert "deadline" in reason and watchdogs[0].expired
        assert watchdogs[0].initial_budget == source.timeout
        assert watchdogs[0].armed == [loader_feed.INITIAL_TIMEOUT, loader_feed.IDLE_TIMEOUT]
        assert rows[0]["id"] == "one" and rows[0]["raw"]["dirty"] == 2
        assert rows[0]["raw"]["_availability"]["status"] == "stale" and state == "ready"
        assert len(children) == 1 and children[0].poll() is not None
    finally:
        stop_loader(loader, worker, children)


def test_feed_unterminated_frame_without_watchdog_expiry_is_terminal(monkeypatch):
    source = data_ssh.Source("host", "Win", None, local=True, timeout=2)
    script = "import sys; sys.stdout.write('{\"type\":\"begin\"'); sys.stdout.flush()"
    loader, worker, children = run_loader(monkeypatch, source, script)
    try:
        wait_until(lambda: bool(loader.error_for_source(source.source_id)))
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert "frame bound" in loader.error_for_source(source.source_id)
        assert loader.records() == [] and loader._state[source.cache_key] == "failed"
        assert len(children) == 1
    finally:
        stop_loader(loader, worker, children)


def test_feed_cancellation_during_partial_frame_stops_without_respawn(monkeypatch, tmp_path):
    marker = tmp_path / "partial-flushed"
    script = (
        "import pathlib,sys,time\n"
        "sys.stdout.write('{\"type\":\"begin\"'); sys.stdout.flush()\n"
        f"pathlib.Path({str(marker)!r}).write_text('flushed')\n"
        "time.sleep(10)\n"
    )
    source = data_ssh.Source("host", "Win", None, local=True, timeout=2)
    loader, worker, children = run_loader(monkeypatch, source, script)
    try:
        wait_until(marker.exists)
    finally:
        stop_loader(loader, worker, children)
    assert loader.records() == [] and len(children) == 1
    assert not loader.error_for_source(source.source_id)
