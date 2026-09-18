"""Tests for the durable per-project handoff-cutover trace store."""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import activity
from agent_worktrees import handoff_trace
from agent_worktrees import tracking


@pytest.fixture
def patch_install_dir(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the trace store into a tmp install dir."""
    monkeypatch.setattr(
        "agent_worktrees.config.install_dir", lambda: tmp_path / ".agent-worktrees"
    )
    return tmp_path / ".agent-worktrees"


def test_trace_path_is_namespaced_by_project(patch_install_dir: Path):
    p1 = handoff_trace.trace_path("proj-a", "wt-1")
    p2 = handoff_trace.trace_path("proj-b", "wt-1")
    assert p1 != p2
    assert p1.name == "wt-1.jsonl"
    assert p1.parent.name == "proj-a"
    assert p2.parent.name == "proj-b"


def test_append_event_writes_and_reads_back(patch_install_dir: Path):
    ok = handoff_trace.append_event(
        "proj-a", "wt-1", {"event": "handoff_requested", "stage": 6}
    )
    assert ok is True
    events = handoff_trace.read_trace("proj-a", "wt-1")
    assert len(events) == 1
    assert events[0]["event"] == "handoff_requested"
    assert events[0]["stage"] == 6


def test_append_event_no_ops_without_project_or_worktree_id(patch_install_dir: Path):
    assert handoff_trace.append_event(None, "wt-1", {"event": "x"}) is False
    assert handoff_trace.append_event("proj-a", None, {"event": "x"}) is False
    assert handoff_trace.read_trace("proj-a", "wt-1") == []


def test_two_projects_same_worktree_id_do_not_interleave(patch_install_dir: Path):
    handoff_trace.append_event("proj-a", "wt-1", {"event": "a-event"})
    handoff_trace.append_event("proj-b", "wt-1", {"event": "b-event"})
    a_events = handoff_trace.read_trace("proj-a", "wt-1")
    b_events = handoff_trace.read_trace("proj-b", "wt-1")
    assert [e["event"] for e in a_events] == ["a-event"]
    assert [e["event"] for e in b_events] == ["b-event"]


@pytest.mark.parametrize(
    "bad_value",
    ["..", ".", "../escape", "a/../../etc", "a/b", "a\\b", "..\\escape", "\0evil", ""],
)
def test_trace_path_rejects_unsafe_project(patch_install_dir: Path, bad_value: str):
    with pytest.raises(ValueError):
        handoff_trace.trace_path(bad_value, "wt-1")


@pytest.mark.parametrize(
    "bad_value",
    ["..", ".", "../escape", "a/../../etc", "a/b", "a\\b", "..\\escape", "\0evil", ""],
)
def test_trace_path_rejects_unsafe_worktree_id(patch_install_dir: Path, bad_value: str):
    with pytest.raises(ValueError):
        handoff_trace.trace_path("proj-a", bad_value)


def test_append_event_no_ops_on_unsafe_identifiers(patch_install_dir: Path, tmp_path: Path):
    """A malicious project/worktree_id must never escape the trace root."""
    assert handoff_trace.append_event("../../escape", "wt-1", {"event": "x"}) is False
    assert handoff_trace.append_event("proj-a", "../../escape", {"event": "x"}) is False
    # No file was written anywhere outside the configured install dir.
    escaped = tmp_path.parent / "escape.jsonl"
    assert not escaped.exists()


def test_read_trace_returns_empty_on_unsafe_identifiers(patch_install_dir: Path):
    assert handoff_trace.read_trace("../../escape", "wt-1") == []
    assert handoff_trace.read_trace("proj-a", "../../escape") == []


def test_read_trace_missing_file_returns_empty(patch_install_dir: Path):
    assert handoff_trace.read_trace("proj-a", "no-such-worktree") == []


def test_read_trace_skips_unparseable_lines(patch_install_dir: Path):
    path = handoff_trace.trace_path("proj-a", "wt-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event": "good"}\nnot-json\n{"event": "also-good"}\n')
    events = handoff_trace.read_trace("proj-a", "wt-1")
    assert [e["event"] for e in events] == ["good", "also-good"]


def test_read_trace_skips_invalid_utf8_but_keeps_later_valid_lines(
    patch_install_dir: Path,
):
    path = handoff_trace.trace_path("proj-a", "wt-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b'{"event": "good"}\n')
        f.write(b"\xff\xfe not valid utf-8\n")
        f.write(b'{"event": "also-good"}\n')
    events = handoff_trace.read_trace("proj-a", "wt-1")
    assert [e["event"] for e in events] == ["good", "also-good"]


def test_remove_trace_deletes_file_and_lock(patch_install_dir: Path):
    handoff_trace.append_event("proj-a", "wt-1", {"event": "x"})
    path = handoff_trace.trace_path("proj-a", "wt-1")
    lock_path = path.with_suffix(path.suffix + ".lock")
    assert path.exists()
    assert lock_path.exists()

    handoff_trace.remove_trace("proj-a", "wt-1")

    assert not path.exists()
    assert not lock_path.exists()
    assert handoff_trace.read_trace("proj-a", "wt-1") == []


def test_remove_trace_no_ops_on_missing_or_unsafe_identifiers(patch_install_dir: Path):
    # Missing project/worktree_id, unknown worktree, and unsafe identifiers
    # must never raise.
    handoff_trace.remove_trace(None, "wt-1")
    handoff_trace.remove_trace("proj-a", None)
    handoff_trace.remove_trace("proj-a", "no-such-worktree")
    handoff_trace.remove_trace("../../escape", "wt-1")
    handoff_trace.remove_trace("proj-a", "../../escape")


def test_concurrent_appends_produce_no_interleaved_or_dropped_lines(
    patch_install_dir: Path,
):
    """Multiple threads appending in parallel must not corrupt or drop lines.

    A thread-based stand-in for the real multi-process concurrency (the
    Python CLI, the launcher hook client, the status monitor, and
    context-handoff's Node process all appending to the same file) -- the
    lock this exercises is the same cross-process advisory lock used against
    real concurrent processes, so a corruption here would also manifest
    across processes.
    """
    n_threads = 8
    n_per_thread = 25
    barrier = threading.Barrier(n_threads)

    def worker(idx: int) -> None:
        barrier.wait()
        for i in range(n_per_thread):
            handoff_trace.append_event(
                "proj-a", "wt-1", {"event": "e", "thread": idx, "i": i}
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = handoff_trace.read_trace("proj-a", "wt-1")
    assert len(events) == n_threads * n_per_thread
    seen = {(e["thread"], e["i"]) for e in events}
    assert len(seen) == n_threads * n_per_thread


_SUBPROCESS_WORKER = """
import sys

from agent_worktrees import config as cfg
from agent_worktrees import handoff_trace

cfg.install_dir = lambda: __import__("pathlib").Path(sys.argv[1])
proc_idx = int(sys.argv[2])
n_per_proc = int(sys.argv[3])
for i in range(n_per_proc):
    handoff_trace.append_event(
        "proj-a", "wt-1", {"event": "e", "proc": proc_idx, "i": i}
    )
"""


def test_concurrent_appends_across_real_processes_produce_no_corruption(
    patch_install_dir: Path,
):
    """Independent OS processes racing the same lock must not corrupt lines.

    The thread-based test above proves the lock mechanism is *sound*, but
    only a real cross-process race exercises the actual `fcntl.flock` /
    `msvcrt.locking` contention this primitive depends on (threads within one
    interpreter never truly race the file the way the CLI, the launcher hook
    client, the status monitor, and context-handoff's Node process do). This
    spawns genuinely separate ``python -c`` subprocesses -- the OS-appropriate
    lock path (``fcntl`` on POSIX, ``msvcrt`` on Windows) is selected
    automatically by ``handoff_trace._append_lock``, so this same test
    exercises the Windows lock path when run on a Windows CI runner.
    """
    import subprocess
    import sys as _sys

    n_procs = 4
    n_per_proc = 20
    install_dir = str(patch_install_dir)

    procs = [
        subprocess.Popen(
            [_sys.executable, "-c", _SUBPROCESS_WORKER, install_dir, str(idx), str(n_per_proc)]
        )
        for idx in range(n_procs)
    ]
    results = [p.wait(timeout=60) for p in procs]
    assert results == [0] * n_procs

    events = handoff_trace.read_trace("proj-a", "wt-1")
    assert len(events) == n_procs * n_per_proc
    seen = {(e["proc"], e["i"]) for e in events}
    assert len(seen) == n_procs * n_per_proc


def _trace_record(tmp_tracking_dir: Path, wt_id: str = "wt-trace") -> None:
    record = tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=f"/tmp/src/{wt_id}",
        repo="test-project",
        machine="test",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )
    tracking.save_record(record, tmp_tracking_dir / f"{wt_id}.yaml")


def _trace_args(**kwargs):
    base = dict(trace_target="wt-trace", project=None, token=None, json=False)
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_cmd_handoff_trace_marks_missing_later_stages(
    tmp_tracking_dir: Path, monkeypatch_config, capfd
):
    _trace_record(tmp_tracking_dir)
    tracking.register_session("wt-trace", "old-sess")
    record = tracking.load_record(tmp_tracking_dir / "wt-trace.yaml")
    tracking.open_handoff(record, "old-sess", "task-1")
    activity.log_event("worktree_created", worktree_id="wt-trace")
    activity.log_event("mux_attached", worktree_id="wt-trace", launch_id="launch-1")
    activity.log_event("copilot_invoked", worktree_id="wt-trace", launch_id="launch-1")
    activity.log_event(
        "session_started",
        worktree_id="wt-trace",
        session_id="old-sess",
        launch_id="launch-1",
    )
    activity.log_event("status_reported", worktree_id="wt-trace", session_id="old-sess")
    activity.log_event(
        "handoff_requested",
        worktree_id="wt-trace",
        session_id="old-sess",
        handoff_id="task-1",
    )
    activity.log_event(
        "handoff_cutover_claim",
        worktree_id="wt-trace",
        session_id="old-sess",
        handoff_token="task-1",
        outcome="acquired",
    )

    rc = m.cmd_handoff_trace(_trace_args())

    assert rc == 0
    out = capfd.readouterr().out
    assert "stage  7: handoff_host_acknowledged: observed via" in out
    assert "stage  8: handoff_successor_spawn_started: never observed" in out
    assert "stage 13: handoff_complete: never observed" in out


def test_cmd_handoff_trace_resolves_session_id_across_projects_without_active_project(
    tmp_tracking_dir: Path, monkeypatch, monkeypatch_config, capfd
):
    from agent_worktrees import config as cfg
    from agent_worktrees import handoff_diagnostics

    _trace_record(tmp_tracking_dir, wt_id="wt-cross")
    tracking.register_session("wt-cross", "sess-1")
    record = tracking.load_record(tmp_tracking_dir / "wt-cross.yaml")
    tracking.open_handoff(record, "sess-1", "task-cross")
    activity.log_event(
        "handoff_requested",
        worktree_id="wt-cross",
        session_id="sess-1",
        handoff_id="task-cross",
    )
    monkeypatch.setattr(handoff_diagnostics, "_all_tracking_dirs", lambda: [tmp_tracking_dir])
    monkeypatch.setattr(
        handoff_diagnostics, "_project_for_tracking_file", lambda path: "test-project"
    )
    cfg.set_active_project(None)

    rc = m.cmd_handoff_trace(_trace_args(trace_target="sess-1", json=True))

    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["project"] == "test-project"
    assert payload["worktree_id"] == "wt-cross"
    assert payload["selector_kind"] == "session"
    assert payload["handoff_token"] == "task-cross"
