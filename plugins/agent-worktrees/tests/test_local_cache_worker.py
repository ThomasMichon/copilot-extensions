from __future__ import annotations

import json
import os
import threading
from types import SimpleNamespace

import pytest

from agent_worktrees import local_cache_refresh as lcr, local_cache_worker as worker


@pytest.mark.parametrize("kind", ["ordinary", "linked", "relative", "bare"])
def test_metadata_resolution_never_spawns_git(tmp_path, monkeypatch, kind):
    root = tmp_path / "checkout"
    root.mkdir()
    if kind == "bare":
        (root / "HEAD").write_text("ref: refs/heads/main\n")
        (root / "objects").mkdir()
        git_dir = root
    elif kind == "ordinary":
        git_dir = root / ".git"
        git_dir.mkdir()
    else:
        git_dir = tmp_path / "admin"
        git_dir.mkdir()
        target = str(git_dir) if kind == "linked" else "..\\admin" if os.name == "nt" else "../admin"
        (root / ".git").write_text(f"gitdir: {target}\n")
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn Git"))
    assert lcr._worker_directory(root) == git_dir / "agent-worktrees-local-guidance"


def test_darwin_birth_identity_allows_queue_and_acknowledgement(tmp_path, monkeypatch):
    import ctypes

    class Query:
        def __call__(self, pid, flavor, argument, buffer, size):
            info = buffer._obj
            info.pid, info.start_seconds, info.start_microseconds = pid, 1234, 567
            return size

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: SimpleNamespace(proc_pidinfo=Query()))
    monkeypatch.setattr(worker.locks.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=os.getpid(), wait=lambda: 0))
    monkeypatch.setattr(lcr, "refresh_local_cache", lambda root: lcr.RefreshResult("ready", changed=1))
    directory = tmp_path / "worker"
    assert worker.queue(tmp_path, directory).status == "queued"
    state = json.loads((directory / "request.json").read_text())
    assert state["owner"]["start_time"] == "1234:567"
    assert worker.run(tmp_path, directory, state["request_id"]) == 0
    assert json.loads((directory / "request.json").read_text())["phase"] == "done"


def test_dispatch_reservation_survives_the_pre_acknowledgement_gap(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    results = []
    errors = []
    directory = tmp_path / "worker"
    monkeypatch.setattr(worker, "_owner", lambda pid: {"pid": pid, "start_time": "fixture"})
    monkeypatch.setattr(worker.locks, "lock_is_live", lambda owner: True)
    monkeypatch.setattr(worker, "windowless_daemon_kwargs", lambda **kwargs: {})

    def spawn(argv, **kwargs):
        calls.append(argv)
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(pid=123, wait=lambda: 0)

    monkeypatch.setattr(worker.subprocess, "Popen", spawn)

    def first():
        try:
            results.append(worker.queue(tmp_path, directory))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        blocked = worker.queue(tmp_path, directory)
        assert blocked.status == "queued"
        assert "reserved" in blocked.detail
    finally:
        release.set()
        thread.join(5)
    assert not errors
    assert len(calls) == 1
    assert results[0].status == "queued"
    still_pending = worker.queue(tmp_path, directory)
    assert still_pending.status == "queued"
    assert "already active" in still_pending.detail
    assert len(calls) == 1
    state = json.loads((directory / "request.json").read_text())
    assert state["owner"]["pid"] == 123
    assert state["request_id"] == calls[0][-1]


def test_rendering_keeps_no_administrative_lease_open(tmp_path, monkeypatch):
    directory = tmp_path / "worker"
    directory.mkdir()

    def refresh(root):
        gate = worker._gate(directory)
        gate.acquire()
        gate.release()
        state = json.loads((directory / "request.json").read_text())
        assert state["phase"] == "running"
        return lcr.RefreshResult("ready", changed=3)

    monkeypatch.setattr(lcr, "refresh_local_cache", refresh)
    assert worker.run(tmp_path, directory) == 0
    state = json.loads((directory / "request.json").read_text())
    assert state["phase"] == "done"
    assert state["result"]["installed"] == 3
    assert "3 installed" in (directory / "refresh.log").read_text()


def test_latest_result_is_a_bounded_utf8_snapshot(tmp_path):
    worker._log(tmp_path, chr(233) * 20_000)
    path = tmp_path / "refresh.log"
    assert len(path.read_bytes()) <= worker._MAX_LOG_BYTES
    assert "[truncated]" in path.read_text(encoding="utf-8")
    worker._log(tmp_path, "new result")
    assert path.read_text() == "new result\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows readers deny file replacement while open")
@pytest.mark.parametrize("filename", ["request.json", "refresh.log"])
def test_snapshot_publication_waits_for_a_transient_reader(tmp_path, monkeypatch, filename):
    path = tmp_path / filename
    path.write_text("old")
    opened = threading.Event()
    release = threading.Event()

    def reader():
        with path.open("rb"):
            opened.set()
            assert release.wait(5)

    thread = threading.Thread(target=reader)
    thread.start()
    replace = os.replace
    attempts = []

    def observed_replace(source, destination):
        attempts.append(True)
        try:
            return replace(source, destination)
        finally:
            release.set()

    monkeypatch.setattr(worker.os, "replace", observed_replace)
    try:
        assert opened.wait(5)
        worker._write(path, "new")
    finally:
        release.set()
        thread.join(5)
    assert path.read_text() == "new"
    assert len(attempts) >= 2


def test_snapshot_publication_surfaces_persistent_denial(tmp_path, monkeypatch):
    path = tmp_path / "request.json"
    path.write_text("old")
    calls = []

    def denied(source, destination):
        calls.append(True)
        raise PermissionError("persistent denial")

    monkeypatch.setattr(worker.os, "replace", denied)
    monkeypatch.setattr(worker.time, "sleep", lambda delay: None)
    with pytest.raises(PermissionError, match="persistent denial"):
        worker._write(path, "new")
    assert len(calls) == 5
    assert path.read_text() == "old"
    assert list(tmp_path.iterdir()) == [path]


def test_queued_worker_protects_finalize_and_cleanup(tmp_path, monkeypatch):
    from agent_worktrees import __main__ as main, finalize

    root = tmp_path / "checkout"
    directory = root / ".git" / "agent-worktrees-local-guidance"
    directory.mkdir(parents=True)
    worker._save(directory, {
        "version": 1, "root": str(root.resolve()), "request_id": "fixture",
        "phase": "queued", "owner": worker._owner(os.getpid()),
    })
    record = SimpleNamespace(worktree_path=str(root), worktree_id="fixture")
    assert finalize._has_live_session(record)
    monkeypatch.setattr(main, "_hosted_session_blocks_cleanup", lambda rec: False)
    monkeypatch.setattr(main.sessions, "_list_mux_sessions", lambda: set())
    monkeypatch.setattr(main, "_fresh_bound_live_hint", lambda rec: False)
    monkeypatch.setattr(main.reclaim, "live_bridge_worktrees", lambda: set())
    active = main._build_active_paths([record], SimpleNamespace(active_sessions={}))
    assert main.sessions._normalize_path(str(root)) in active
    monkeypatch.setattr(worker.locks, "lock_is_live", lambda owner: False)
    assert not worker.is_active(root)


def test_wrong_request_cannot_claim_an_existing_worker(tmp_path, monkeypatch):
    directory = tmp_path / "worker"
    directory.mkdir()
    worker._save(directory, {
        "version": 1, "root": str(tmp_path), "request_id": "current",
        "phase": "queued", "owner": worker._owner(os.getpid()),
    })
    def forbidden(root):
        raise AssertionError("must not render")

    monkeypatch.setattr(lcr, "refresh_local_cache", forbidden)
    try:
        worker.run(tmp_path, directory, "wrong")
    except ValueError as exc:
        assert "not confirmed" in str(exc)
    else:
        raise AssertionError("foreign request was admitted")
