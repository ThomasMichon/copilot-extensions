"""Overlapping installer/child writes retain one bound, crash-durable witness."""

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import pytest

from zdd import breadcrumb, routing


_REGISTER_CHILD = r"""
import json, os, sys, time
from pathlib import Path
from zdd import breadcrumb, diagnostics, routing, routing_persistence

root = Path(sys.argv[1])
held = sys.argv[2] == "held"

def wait_for(name):
    deadline = time.monotonic() + 15
    while not (root / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(name)
        time.sleep(0.01)

birth = diagnostics.process_start_time(os.getpid())
assert birth
(root / "child-identity").write_text(json.dumps({"pid": os.getpid(), "birth": birth}), encoding="utf-8")
wait_for("start")
initial = breadcrumb.read_breadcrumb(root)
original_replace = (
    routing_persistence.windows_replace if sys.platform == "win32" else os.replace
)

def pause_replace(source, target):
    if target == breadcrumb.breadcrumb_path(root):
        (root / "paused").write_text("candidate temp is flushed", encoding="utf-8")
        wait_for("release")
    original_replace(source, target)

if sys.platform == "win32":
    routing_persistence.windows_replace = pause_replace
else:
    os.replace = pause_replace

def register():
    breadcrumb.write_breadcrumb(
        root, state="started", pid=initial["pid"],
        started_at=initial["started_at"], self_retirement=True,
        new_pid=os.getpid(), new_port=initial["new_port"],
        publication_witness=initial["publication_witness"],
        new_process_start_time=birth, _routing_locked=held,
    )

if held:
    with routing.routing_lock(root, deadline=time.monotonic() + 5):
        register()
else:
    register()
print("CHILD_SUCCESS", flush=True)
"""


@pytest.mark.parametrize("child_lock", ["default", "held"])
def test_parent_waits_for_overlapping_child_registration(tmp_path, monkeypatch, child_lock):
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", _REGISTER_CHILD, str(tmp_path), child_lock],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    thread = None
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "child-identity").exists():
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        identity = json.loads((tmp_path / "child-identity").read_text(encoding="utf-8"))
        candidate_pid = identity["pid"]
        initial = breadcrumb.write_breadcrumb(
            tmp_path, state="started", self_retirement=True, new_pid=candidate_pid,
            new_port=61002, publication_witness="original-publication",
        )
        (tmp_path / "start").touch()
        deadline = time.monotonic() + 10
        while not (tmp_path / "paused").exists():
            assert child.poll() is None, "registrar exited before its write boundary"
            assert time.monotonic() < deadline, "registrar never reached flushed temp"
            time.sleep(0.01)
        pending = tmp_path / "cutover.json.tmp"
        child_bytes = pending.read_bytes()
        staged = json.loads(child_bytes)
        assert staged["new_pid"] == candidate_pid
        assert staged["new_process_start_time"] == identity["birth"]
        attempted = threading.Event()
        finished = threading.Event()
        outcomes = []
        file_lock = routing._file_routing_lock

        @contextmanager
        def observe_lock(*args, **kwargs):
            attempted.set()
            with file_lock(*args, **kwargs):
                yield

        monkeypatch.setattr(routing, "_file_routing_lock", observe_lock)

        def parent_phase():
            try:
                outcomes.append(breadcrumb.write_breadcrumb(
                    tmp_path, state="flipped", self_retirement=True,
                    started_at=initial["started_at"], new_pid=candidate_pid,
                    new_port=initial["new_port"],
                    publication_witness=initial["publication_witness"],
                    new_process_start_time=None,
                ))
            except BaseException as exc:
                outcomes.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=parent_phase)
        thread.start()
        assert attempted.wait(2), "parent did not attempt the shared routing lock"
        assert not finished.wait(0.2), "parent bypassed the child's held write authority"
        assert pending.read_bytes() == child_bytes, "parent collided with the child's temp"
        assert breadcrumb.read_breadcrumb(tmp_path) == initial
        (tmp_path / "release").touch()
        thread.join(timeout=10)
        assert not thread.is_alive()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
        assert stdout.strip() == "CHILD_SUCCESS"
        assert len(outcomes) == 1 and isinstance(outcomes[0], dict), outcomes
        final = breadcrumb.read_breadcrumb(tmp_path)
        assert final == outcomes[0]
        assert final["state"] == "flipped"
        assert final["pid"] == initial["pid"]
        assert final["owner_process_start_time"] == initial["owner_process_start_time"]
        assert final["new_process_start_time"] == staged["new_process_start_time"]
        assert not pending.exists()
    finally:
        (tmp_path / "start").touch()
        (tmp_path / "release").touch()
        if thread is not None:
            thread.join(timeout=10)
        child.communicate(timeout=10)
