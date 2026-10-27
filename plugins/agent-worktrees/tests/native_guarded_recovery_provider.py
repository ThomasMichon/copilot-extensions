"""Isolated real control receiver; never runs a resident monitor."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from agent_worktrees import locks
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import status_monitor_recovery_contract as recovery


def wait_for(path, timeout=15):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"fixture gate did not open: {path.name}")
        time.sleep(0.01)


def serve(root: Path, mode: str):
    state_lock = threading.RLock()
    opened = 0
    health_calls = 0
    draining = mode != "candidate"
    token = "isolated-recovery-fixture"

    def reopen():
        nonlocal opened, draining
        opened += 1
        if mode == "locked-gate":
            (root / "entered").touch()
            wait_for(root / "release")
        draining = False
        return {"draining": False}

    def close_admission():
        nonlocal draining
        draining = True

    def dispatch(action, payload):
        nonlocal health_calls, draining
        with state_lock:
            if action == "health":
                health_calls += 1
                return {
                    "pid": os.getpid(), "status": "draining" if draining else "ready",
                    "runtime_generation": "fixture-runtime", "opened": opened,
                    "health_calls": health_calls,
                    "capabilities": [] if mode == "old-provider" else [recovery.CAPABILITY],
                }
            if action == "drain":
                draining = True
                return {"drained": True, "clean": True, "forced": False}
            if action == "undrain":
                return reopen()
            if action == recovery.ACTION:
                if mode == "before-gate":
                    (root / "entered").touch()
                    wait_for(root / "release")
                result = recovery.handle(
                    payload, directory=root, generation="fixture-runtime", port=server.port,
                    reopen=reopen, close_on_failure=close_admission, lock_timeout=3,
                )
                if mode == "lost-reply":
                    (root / "drop-once").touch()
                if mode == "untyped-reply":
                    return {"draining": False}
                return result
            raise ValueError(f"unexpected fixture action: {action}")

    if mode == "lost-reply":
        setup = control._ControlHandler.setup

        def lose_one_reply(handler):
            setup(handler)
            write = handler.wfile.write

            def send(frame):
                marker = root / "drop-once"
                if marker.exists():
                    marker.unlink()
                    return len(frame)
                return write(frame)

            handler.wfile.write = send

        control._ControlHandler.setup = lose_one_reply
    server = control.ControlServer(dispatch, token=token)
    server.start()
    (root / "ready.json").write_text(json.dumps({
        "pid": os.getpid(), "start": locks.process_start_time(os.getpid()),
        "port": server.port, "token": token,
    }), encoding="utf-8")
    try:
        wait_for(root / "stop", timeout=60)
    finally:
        server.close()


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2])
