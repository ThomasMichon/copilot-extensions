"""Consoleless native Git alias/descendant fixture for the bounded observation lane."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


def marker(root, name, data):
    (root / name).write_text(json.dumps(data), encoding="utf-8")


def run_alias(root: Path, mode: str):
    if mode == "leaf":
        marker(root, "leaf.json", {"pid": os.getpid()})
        print("held stdout", flush=True)
        print("held stderr", file=sys.stderr, flush=True)
        time.sleep(60)
        return
    child = subprocess.Popen(
        [sys.executable, __file__, str(root), "leaf"],
        stdout=sys.stdout, stderr=sys.stderr, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    marker(root, "alias.json", {"pid": os.getpid(), "child": child.pid})
    if mode == "held-pipe":
        deadline = time.monotonic() + 10
        while not (root / "alias-release").exists():
            assert time.monotonic() < deadline, "alias release gate expired"
            time.sleep(0.01)
        return
    child.wait(timeout=60)


def observe(root: Path, mode: str, python: str):
    from agent_procutil import JobHandle
    from agent_worktrees import git_ops as go, push_timeout as pt

    if mode == "before-resume":
        import ctypes
        from ctypes import wintypes
        from agent_procutil import _windows_caller_owned as native

        api = native._api()
        api.GetProcessIdOfThread.argtypes = [wintypes.HANDLE]
        api.GetProcessIdOfThread.restype = wintypes.DWORD

        def held_before_resume(thread):
            pid = api.GetProcessIdOfThread(thread)
            if not pid:
                raise ctypes.WinError(ctypes.get_last_error())
            marker(root, "suspended.json", {"pid": pid})
            time.sleep(60)
            raise AssertionError("caller was not terminated before first resume")

        api.ResumeThread = held_before_resume
        mode = "stall"

    cancelled = mode == "cancel"
    if cancelled:
        mode = "stall"
        communicate = pt._ObservationProcess.communicate

        def interrupt_after_reader_start(process, *, timeout):
            if process.cleaning:
                return communicate(process, timeout=timeout)
            for reader in process.readers:
                reader.attempted = True
                reader.thread.start()
            deadline = min(process.deadline, time.monotonic() + 5)
            while not (root / "leaf.json").exists():
                assert time.monotonic() < deadline, "cancellation descendant did not start"
                time.sleep(0.01)
            while not (root / "cancel-release").exists():
                assert time.monotonic() < deadline, "native cancellation gate expired"
                time.sleep(0.01)
            raise KeyboardInterrupt("native observation fixture cancellation")

        pt._ObservationProcess.communicate = interrupt_after_reader_start

    captured = []
    cleanup = []
    spawn = pt._spawn_observation
    terminate = JobHandle.terminate_and_wait

    def record_spawn(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        captured.append((process, job, kwargs["deadline"], kwargs["command_deadline"]))
        marker(root, "spawned.json", {"pid": process.native.pid})
        return process, job

    def record_cleanup(job, timeout_seconds):
        terminate(job, timeout_seconds)
        cleanup.append({"budget": timeout_seconds, "confirmed_empty": job.handle is None})

    pt._spawn_observation = record_spawn
    JobHandle.terminate_and_wait = record_cleanup
    script = str(Path(__file__).resolve())
    alias = "!" + " ".join(shlex.quote(value) for value in (
        python, script, str(root), mode,
    ))
    start = time.monotonic()
    deadline = go._observation_deadline()
    error = None
    try:
        go.git("-c", "alias.observation-fixture=" + alias, "observation-fixture",
               cwd=root, timeout=15, kill_tree=True, observation_deadline=deadline)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        error = exc
    elapsed = time.monotonic() - start
    assert isinstance(error, KeyboardInterrupt if cancelled else subprocess.TimeoutExpired)
    assert len(captured) == 1 and cleanup
    process, job, retained_deadline, command_deadline = captured[0]
    result = {
        "elapsed": elapsed, "same_deadline": retained_deadline == deadline,
        "cleanup_reserve": deadline - command_deadline,
        "empty_job": job.handle is None, "native_handle_closed": process.native._handle is None,
        "readers_joined": all(not reader.thread.is_alive() and reader.done.is_set() for reader in process.readers),
        "cleanup": cleanup, "started": bool(captured), "cancelled": cancelled,
    }
    marker(root, "result.json", result)
    assert elapsed <= 15 and retained_deadline == deadline
    assert deadline - command_deadline >= 5
    assert result["empty_job"] and result["native_handle_closed"] and result["readers_joined"]
    assert all(item["confirmed_empty"] and 0 < item["budget"] <= (15 if cancelled else 5) for item in cleanup)


if __name__ == "__main__":
    workspace = Path(sys.argv[1])
    operation = sys.argv[2]
    if operation in {"leaf", "held-pipe", "stall"}:
        run_alias(workspace, operation)
    else:
        observe(workspace, sys.argv[3], sys.argv[4])
