"""Cache stamps retain classified state through short-lived writer contention."""

from __future__ import annotations

import multiprocessing
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_worktrees import tracking
from agent_worktrees.picker_support import data_local, derive

pytestmark = pytest.mark.contract("agent_worktrees.tracking.cache_stamp_contention")


def _hold_record(path: str, ready, release) -> None:
    with tracking._RecordLock(Path(path), require_sidecar=True):
        record = tracking.load_record(Path(path))
        record.resume_count += 1
        tracking.save_record(record, Path(path))
        ready.set()
        if not release.wait(10):
            raise TimeoutError("test did not release the record holder")


@pytest.fixture
def record_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda _: tmp_path)
    path = tmp_path / "wt-stamp.yaml"
    tracking.save_record(
        tracking.WorktreeRecord(
            worktree_id="wt-stamp",
            branch="worktree/wt-stamp",
            worktree_path=str(tmp_path / "checkout"),
            repo="test-project",
            machine="test-host",
            platform="windows",
            started_at="2026-10-08T00:00:00",
            last_resumed_at="2026-10-08T00:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            git_state="completed",
            session_turns=1,
            session_summary="preserve summary",
        ),
        path,
    )
    return path


def test_background_stamp_survives_cross_process_contention(
    record_path, monkeypatch,
):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(
        target=_hold_record, args=(str(record_path), ready, release),
    )
    contended = threading.Event()
    original_lock = tracking._RecordLock

    class ObservedLock(original_lock):
        def _sidecar_try(self):
            acquired = super()._sidecar_try()
            if not acquired:
                contended.set()
            return acquired

    monkeypatch.setattr(tracking, "_RecordLock", ObservedLock)
    queue = tracking._StampWriteQueue()
    monkeypatch.setattr(tracking, "_STAMP_QUEUE", queue)
    holder.start()
    try:
        assert ready.wait(10), "holder did not acquire the sidecar"
        assert tracking.stamp_session_state("wt-stamp", git_state="active")
        assert contended.wait(5), "stamp never competed for the held sidecar"
        release.set()
        queue.flush()
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
    assert holder.exitcode == 0
    record = tracking.load_record(record_path)
    assert record.git_state == "active"
    assert record.resume_count == 1
    assert (record.session_turns, record.session_summary) == (1, "preserve summary")
    monkeypatch.setattr(data_local.sessions, "worktree_session_lock_state", lambda _: (False, []))
    raw = {}
    data_local._overlay_cached_state(raw, record)
    assert derive._state(raw) == "ACTIVE"


def test_background_stamp_timeout_is_bounded_and_reported(
    record_path, monkeypatch, caplog,
):
    acquired, release = threading.Event(), threading.Event()

    def hold():
        with tracking._RecordLock(record_path):
            acquired.set()
            assert release.wait(5)

    holder = threading.Thread(target=hold)
    queue = tracking._StampWriteQueue()
    monkeypatch.setattr(tracking, "_STAMP_QUEUE", queue)
    holder.start()
    try:
        assert acquired.wait(2)
        started = time.monotonic()
        tracking.stamp_session_state("wt-stamp", git_state="active")
        queue.flush()
        assert time.monotonic() - started < 2
        assert tracking.load_record(record_path).git_state == "completed"
        assert "cache stamp" in caplog.text.lower()
        assert "wt-stamp" in caplog.text
        assert "timed out" in caplog.text.lower()
    finally:
        release.set()
        holder.join(5)
    tracking.stamp_session_state("wt-stamp", git_state="active")
    queue.flush()
    assert tracking.load_record(record_path).git_state == "active"


def test_synchronous_stamp_remains_nonblocking(record_path):
    acquired, release = threading.Event(), threading.Event()

    def hold():
        with tracking._RecordLock(record_path):
            acquired.set()
            assert release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert acquired.wait(2)
        started = time.monotonic()
        assert not tracking.stamp_session_state("wt-stamp", git_state="active", sync=True)
        assert time.monotonic() - started < 0.2
        assert tracking.load_record(record_path).git_state == "completed"
    finally:
        release.set()
        holder.join(5)


def test_cli_exit_drains_a_contended_stamp(record_path):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(
        target=_hold_record, args=(str(record_path), ready, release),
    )
    marker = record_path.parent / "stamp-contended"
    script = """
import sys
from pathlib import Path
from agent_worktrees import tracking
path, marker = map(Path, sys.argv[1:])
tracking._owning_tracking_dir = lambda _: path.parent
original = tracking._RecordLock
class ObservedLock(original):
    def _sidecar_try(self):
        acquired = super()._sidecar_try()
        if not acquired:
            marker.write_text("contended")
        return acquired
tracking._RecordLock = ObservedLock
tracking.stamp_session_state("wt-stamp", git_state="active")
"""
    writer = None
    holder.start()
    try:
        assert ready.wait(10)
        writer = subprocess.Popen(
            [sys.executable, "-c", script, str(record_path), str(marker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert writer.poll() is None, "writer exited before competing for the lock"
            assert time.monotonic() < deadline, "writer never reached the lock"
            time.sleep(0.01)
        release.set()
        stdout, stderr = writer.communicate(timeout=10)
        assert writer.returncode == 0, (stdout, stderr)
        assert "timed out" not in stderr
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
        if writer is not None and writer.poll() is None:
            writer.kill()
            writer.communicate(timeout=5)
    assert holder.exitcode == 0
    assert tracking.load_record(record_path).git_state == "active"


def test_pending_newer_stamp_wins_after_wait(record_path, monkeypatch):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(
        target=_hold_record, args=(str(record_path), ready, release),
    )
    contended = threading.Event()
    original_lock = tracking._RecordLock

    class ObservedLock(original_lock):
        def _sidecar_try(self):
            acquired = super()._sidecar_try()
            if not acquired:
                contended.set()
            return acquired

    monkeypatch.setattr(tracking, "_RecordLock", ObservedLock)
    queue = tracking._StampWriteQueue()
    monkeypatch.setattr(tracking, "_STAMP_QUEUE", queue)
    holder.start()
    try:
        assert ready.wait(10)
        tracking.stamp_session_state("wt-stamp", git_state="wip", turns=2)
        assert contended.wait(5)
        tracking.stamp_session_state("wt-stamp", git_state="active", turns=3)
        release.set()
        queue.flush()
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
    assert holder.exitcode == 0
    record = tracking.load_record(record_path)
    assert (record.git_state, record.session_turns) == ("active", 3)
    assert record.resume_count == 1


def test_background_stamp_write_error_is_reported(record_path, monkeypatch, caplog):
    def fail_save(*args, **kwargs):
        raise OSError("test write failure")

    monkeypatch.setattr(tracking, "save_record", fail_save)
    queue = tracking._StampWriteQueue()
    monkeypatch.setattr(tracking, "_STAMP_QUEUE", queue)
    tracking.stamp_session_state("wt-stamp", git_state="active")
    queue.flush()
    assert tracking.load_record(record_path).git_state == "completed"
    assert "Cache stamp for wt-stamp failed" in caplog.text
    assert "test write failure" in caplog.text
