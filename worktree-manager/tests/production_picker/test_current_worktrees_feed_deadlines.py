"""Real child-process coverage for startup, publication and idle deadlines."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from test_current_worktrees_feed import snapshot, update
from worktree_manager.production_picker.picker_tui import data_ssh
from worktree_manager.production_picker.picker_tui import loader_current_feed as loader_feed


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("feed did not reach the expected deadline/recovery boundary")


def run_loader(monkeypatch, source, script):
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_STREAM", raising=False)
    monkeypatch.setattr(loader_feed, "feed_argv", lambda _: [sys.executable, "-u", "-c", script])
    loader = data_ssh.LiveLoader([source])
    children = []
    spawn = loader._spawn_stream

    def capture_child(argv):
        child = spawn(argv)
        children.append(child)
        return child

    monkeypatch.setattr(loader, "_spawn_stream", capture_child)
    worker = threading.Thread(target=loader._load_one, args=(source, 0))
    worker.start()
    return loader, worker, children


def stop_loader(loader, worker, children):
    loader.cancel()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert loader._procs == [] and loader._held_sources == set()
    assert children and all(child.poll() is not None for child in children)


@pytest.mark.parametrize("local", [True, False])
def test_feed_process_startup_uses_source_budget_not_publication_budget(monkeypatch, local):
    monkeypatch.setattr(loader_feed, "INITIAL_TIMEOUT", 0.15)
    frames = snapshot([{"id": "one", "status": "active", "dirty": 2}])
    script = (
        "import json,time\n"
        "time.sleep(.4)\n"
        f"for frame in {frames!r}: print(json.dumps(frame),flush=True)\n"
        "time.sleep(10)\n"
    )
    source = data_ssh.Source("host", "Win", None, local=local, timeout=2)
    loader, worker, children = run_loader(monkeypatch, source, script)
    try:
        wait_until(lambda: bool(loader.records()))
        assert loader.records()[0]["id"] == "one"
        assert loader.records()[0]["raw"]["dirty"] == 2
        assert loader._state[source.cache_key] == "ready"
        assert not loader.error_for_source(source.source_id)
        assert len(children) == 1
    finally:
        stop_loader(loader, worker, children)


@pytest.mark.parametrize(
    "boundary", ["startup", "initial-publication", "publication", "partial-frame", "heartbeat"],
)
def test_feed_transient_deadline_recovers_without_partial_or_empty_overwrite(
    monkeypatch, tmp_path, boundary,
):
    monkeypatch.setattr(loader_feed, "INITIAL_TIMEOUT", 0.15)
    monkeypatch.setattr(loader_feed, "IDLE_TIMEOUT", 0.4)
    marker = tmp_path / "first-attempt"
    armed = []
    arm = loader_feed._Watchdog.arm

    def capture_budget(watchdog, timeout):
        armed.append(timeout)
        arm(watchdog, timeout)

    monkeypatch.setattr(loader_feed._Watchdog, "arm", capture_budget)
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
        " time.sleep(10)\n"
        "else:\n"
        f" for frame in {second!r}: print(json.dumps(frame),flush=True)\n"
        " for _ in range(100):\n"
        "  time.sleep(.1)\n"
        "  print(json.dumps({'type':'heartbeat','generation':'b'*32,'cursor':1}),flush=True)\n"
    )
    source = data_ssh.Source("host", "Win", None, local=True, timeout=2)
    loader, worker, children = run_loader(monkeypatch, source, script)
    try:
        wait_until(lambda: bool(loader.error_for_source(source.source_id)))
        assert "deadline" in loader.error_for_source(source.source_id)
        if boundary in {"startup", "initial-publication"}:
            assert loader.records() == []
            assert loader._state[source.cache_key] == "failed"
            if boundary == "initial-publication":
                assert armed == [loader_feed.INITIAL_TIMEOUT]
        else:
            assert loader.records()[0]["id"] == "one"
            assert loader.records()[0]["raw"]["dirty"] == 2
            assert loader.records()[0]["title"].startswith("[stale]")
        wait_until(lambda: bool(loader.records()) and loader.records()[0]["id"] == "two")
        assert loader.records()[0]["raw"]["dirty"] == 3
        assert loader.records()[0]["raw"]["_projection"]["generation"] == "b" * 32
        assert not loader.error_for_source(source.source_id)
        assert loader._state[source.cache_key] == "ready"
        assert len(children) == 2 and children[0].poll() is not None
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
