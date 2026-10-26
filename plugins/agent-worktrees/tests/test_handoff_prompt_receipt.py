"""Prompt receipt atomicity and bounded-output controls; no live services."""

import argparse
import json
import os
import subprocess
import sys

import pytest

from agent_worktrees import activity, handoff_trace
from agent_worktrees.handoff_prompt_receipt import prompt_receipt


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff_trace.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(handoff_trace.cfg, "active_project", lambda: "project")
    return tmp_path


def test_atomic_replay_and_distinct_sessions(store):
    args = ("project", "worktree", "task:one", "session")
    assert prompt_receipt(*args, record=True) == {"received": True, "replayed": False}
    assert prompt_receipt(*args, record=True) == {"received": True, "replayed": True}
    assert prompt_receipt("project", "worktree", "task:one")["received"]
    assert prompt_receipt("project", "worktree", "task:missing")["received"] is False
    assert prompt_receipt("project", "worktree", "task:one", "other", record=True)["replayed"] is False
    assert len(handoff_trace.read_trace("project", "worktree")) == 2


def test_large_history_does_not_buffer_cli_output(store, capsys):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8") as handle:
        for _ in range(2500):
            handle.write('{"event":"unrelated","locator":"task:other"}\n')
    args = argparse.Namespace(
        event="context_handoff_prompt_received", worktree_id="worktree",
        session_id="session", field=["locator=task:one"], receipt_json=True,
    )
    assert activity.cmd_activity_log(args) == 0
    assert len(capsys.readouterr().out) < 128
    assert activity.cmd_activity_log(args) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is True
    assert activity.cmd_activity(argparse.Namespace(
        receipt_locator="task:one", worktree_id="worktree",
    )) == 0
    assert json.loads(capsys.readouterr().out)["received"] is True


def test_oversized_record_is_unavailable_not_silently_absent(store):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"noise": "x" * 100_000}) + "\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="bounded reader"):
        prompt_receipt("project", "worktree", "task:one", "session", record=True)
    assert path.read_bytes() == before


def test_legacy_receipt_migrates_once_and_incomplete_tail_is_recoverable(store):
    legacy = store / "legacy.jsonl"
    legacy.write_text(json.dumps({
        "event": "context_handoff_prompt_received", "worktree_id": "worktree",
        "session_id": "session", "locator": "file:one",
    }) + "\n", encoding="utf-8")
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"interrupted":')
    result = prompt_receipt(
        "project", "worktree", "file:one", "session", record=True, legacy_path=legacy,
    )
    assert result == {"received": True, "replayed": True}
    assert len(handoff_trace.read_trace("project", "worktree")) == 1


def test_complete_record_without_trailing_newline_is_not_appended_twice(store):
    path = handoff_trace.trace_path("project", "worktree")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "event": "context_handoff_prompt_received", "worktree_id": "worktree",
        "session_id": "session", "locator": "task:one",
    }), encoding="utf-8")
    before = path.read_bytes()
    assert prompt_receipt("project", "worktree", "task:one", "session", record=True)["replayed"]
    assert path.read_bytes() == before


def test_persistence_failure_is_not_reported_as_received(store, monkeypatch):
    def fail(_fd):
        raise OSError("controlled fsync failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        prompt_receipt("project", "worktree", "task:one", "session", record=True)


@pytest.mark.parametrize("session", [None, "", "bad\nsession"])
def test_missing_or_invalid_session_cannot_record(store, session):
    with pytest.raises(ValueError):
        prompt_receipt("project", "worktree", "task:one", session, record=True)


def test_unknown_project_is_unavailable_not_a_successful_receipt(store, capsys, monkeypatch):
    monkeypatch.setattr(handoff_trace.cfg, "active_project", lambda: None)
    assert activity.cmd_activity(argparse.Namespace(
        receipt_locator="task:one", worktree_id="worktree",
    )) == 1
    assert capsys.readouterr().out == ""


def test_two_real_processes_contend_for_one_durable_receipt(store):
    # Both readers wait on the same release file before entering the canonical
    # lock. No Python thread/GIL can serialize the test in place of the OS lock.
    release = store / "release"
    script = """
import json, pathlib, sys, time
sys.path[:0] = json.loads(sys.argv[1])
from agent_worktrees import handoff_trace
from agent_worktrees.handoff_prompt_receipt import prompt_receipt
root=pathlib.Path(sys.argv[2])
handoff_trace.cfg.install_dir=lambda:root
(root/sys.argv[3]).write_text('ready')
deadline=time.monotonic()+10
while not (root/'release').exists():
    if time.monotonic()>deadline: raise RuntimeError('barrier timeout')
    time.sleep(.01)
print(json.dumps(prompt_receipt('project','worktree','task:one','session',record=True)))
"""
    children = []
    try:
        for index in range(2):
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, json.dumps(sys.path), str(store), f"ready{index}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
        import time

        deadline = time.monotonic() + 10
        while not all((store / f"ready{i}").exists() for i in range(2)):
            assert time.monotonic() < deadline, "children did not reach barrier"
            time.sleep(.01)
        release.write_text("go")
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert sorted(result["replayed"] for result in results) == [False, True]
        assert len(handoff_trace.read_trace("project", "worktree")) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
