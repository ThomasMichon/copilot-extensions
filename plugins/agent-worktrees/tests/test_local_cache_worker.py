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
    assert state["result"]["restart_required"] is True
    assert state["result"]["model_admission"] == "unknown"
    assert "3 installed" in (directory / "refresh.log").read_text()


@pytest.mark.parametrize("status,changed,phase,restart", [
    ("ready", 0, "done", False), ("partial", 1, "done", True),
    ("failed", 0, "failed", False), ("timeout", 0, "failed", False),
])
def test_completion_status_preserves_failure_and_admission(
    tmp_path, monkeypatch, status, changed, phase, restart,
):
    directory = tmp_path / "worker"
    directory.mkdir()
    monkeypatch.setattr(lcr, "refresh_local_cache", lambda root: lcr.RefreshResult(status, changed=changed))
    worker.run(tmp_path, directory)
    state = json.loads((directory / "request.json").read_text())
    assert state["phase"] == phase
    assert state["result"]["restart_required"] is restart
    assert state["result"]["model_admission"] == "unknown"


@pytest.mark.parametrize("mutation_started", [False, True])
def test_abnormal_exit_reaches_post_tool_failure_notice(tmp_path, monkeypatch, mutation_started):
    from pathlib import Path
    from test_nudge_status import nudge

    home, root = tmp_path / "home", tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    session = home / ".copilot/session-state/reaper-session"
    session.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(worker.activity, "log_event", lambda *a, **kw: None)
    directory = worker.metadata_directory(root)
    directory.mkdir()
    worker.watch_completion(root, "reaper-session")
    if mutation_started:
        from agent_worktrees import static_preparation
        static_preparation.record_publication(root, changed=True)
    worker._save(directory, {
        "version": 1, "root": str(root), "request_id": "admitted",
        "phase": "running", "owner": worker._owner(os.getpid()),
    })
    worker._reap(SimpleNamespace(wait=lambda: 17), root, directory, "admitted")
    payload = {"sessionId": "reaper-session", "cwd": str(root)}
    notice = nudge.decide(
        payload, home=home, env={"AGENT_WORKTREES_NUDGE": "off"},
    )
    assert "Backup refresh failed" in notice
    if mutation_started:
        assert "Restart/new context is required" in notice and "repair" in notice
    else:
        assert "Restart/new context" not in notice
    assert nudge.decide(payload, home=home, env={"AGENT_WORKTREES_NUDGE": "off"}) is None
    assert not (session / "local-guidance-watch.json").exists()


def test_reaper_preserves_completed_partial_result(tmp_path, monkeypatch):
    monkeypatch.setattr(worker.activity, "log_event", lambda *a, **kw: None)
    state = {
        "version": 1, "root": str(tmp_path), "request_id": "finished",
        "phase": "done", "owner": worker._owner(os.getpid()),
        "completed_at": 1.0, "result": {"status": "partial", "installed": 2},
    }
    worker._save(tmp_path, state)
    worker._log(tmp_path, "original partial result")
    worker._reap(SimpleNamespace(wait=lambda: 1), tmp_path, tmp_path, "finished")
    assert worker._read(tmp_path / "request.json") == state
    assert (tmp_path / "refresh.log").read_text() == "original partial result\n"


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_short_lived_queue_parent_does_not_hide_worker_failure(tmp_path, monkeypatch):
    from pathlib import Path
    from test_nudge_status import nudge
    import subprocess
    import sys
    import time

    home, root = tmp_path / "home", tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    session = home / ".copilot/session-state/orphan-worker"
    session.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    worker.watch_completion(root, "orphan-worker")
    directory = worker.metadata_directory(root)
    staged = tmp_path / "child-runtime" / "agent_worktrees"
    staged.mkdir(parents=True)
    (staged / "__init__.py").write_text(
        f"__path__.append({str(Path(worker.__file__).parent)!r})\n", encoding="utf-8",
    )
    (staged / "local_cache_worker.py").write_text(
        "import os,time\nfrom agent_worktrees import locks\n"
        "parent={'pid':int(os.environ['TEST_PARENT_PID']),'start_time':os.environ['TEST_PARENT_START']}\n"
        "until=time.monotonic()+5\n"
        "while locks.lock_is_live(parent) and time.monotonic()<until: time.sleep(.01)\n"
        "os._exit(17)\n",
        encoding="utf-8",
    )
    driver = (
        "import os,pathlib,sys\n"
        "from agent_worktrees import local_cache_worker as w,status_monitor_runtime as r,locks\n"
        "assert os.environ.get('COPILOT_EXTENSIONS_TEST_CONTAINED')=='1'\n"
        "env=dict(os.environ,PYTHONPATH=sys.argv[3],TEST_PARENT_PID=str(os.getpid()),"
        "TEST_PARENT_START=locks.process_start_time(os.getpid()))\n"
        "r._daemon_environment=lambda:env\nr._daemon_cwd=lambda:sys.argv[1]\n"
        "assert w.queue(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])).status=='queued'\n"
        "os._exit(0)\n"
    )
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    parent = subprocess.run(
        [sys.executable, "-c", driver, str(root), str(directory), str(staged.parent)],
        env=env, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert parent.returncode == 0, parent.stderr
    state = worker._read(directory / "request.json")
    until = time.monotonic() + 6
    while not nudge._worker_definitely_gone(state["owner"]) and time.monotonic() < until:
        time.sleep(.02)
    assert nudge._worker_definitely_gone(state["owner"]), "bounded worker did not exit"
    assert worker._read(directory / "request.json")["phase"] == "queued", "parent reaper unexpectedly survived"
    script = Path(__file__).resolve().parents[1] / "scripts/nudge_status.py"
    result = subprocess.run(
        [sys.executable, str(script)], env=dict(env, AGENT_WORKTREES_NUDGE="off"),
        input=json.dumps({"sessionId": "orphan-worker", "cwd": str(root)}),
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert "Backup refresh failed" in json.loads(result.stdout)["additionalContext"]
    assert not (session / "local-guidance-watch.json").exists()


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


def test_unconfirmed_admission_lock_remains_active_without_request(tmp_path):
    root = tmp_path / "checkout"
    directory = root / ".git" / "agent-worktrees-local-guidance"
    directory.mkdir(parents=True)
    (directory / "local-guidance.lock").write_text("", encoding="utf-8")
    assert worker.is_active(root)


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
