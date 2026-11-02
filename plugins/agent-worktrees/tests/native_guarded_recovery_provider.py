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
    candidate = mode.startswith("candidate")
    draining = not candidate
    published = not candidate
    shutdown_requested = threading.Event()
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
        nonlocal health_calls, draining, published
        with state_lock:
            if action == "health":
                health_calls += 1
                return {
                    "pid": os.getpid(), "status": "draining" if draining else "ready",
                    "runtime_generation": "fixture-runtime", "opened": opened,
                    "health_calls": health_calls,
                    "published": published,
                    "worktrees": {"status": "ready" if published else "unready"},
                    "repositories": {"admitting": published and not draining},
                    "capabilities": [] if mode == "old-provider" else [recovery.CAPABILITY],
                }
            if action == "promote":
                (root / "promote-requested").touch()
                if mode == "candidate-refuse":
                    return {"adopted": False}
                directory = Path((root / "route-root").read_text(encoding="utf-8"))
                route = recovery.active_endpoint(directory)
                if route.pid != os.getpid() or route.port != server.port:
                    return {"adopted": False}
                if mode != "candidate-unready":
                    published, draining = True, False
                return {"adopted": True}
            if action == "shutdown":
                route = recovery.active_endpoint(root)
                health = (control.ControlClient(route.base_url, token=token).health()
                          if route.pid != os.getpid() else {"status": "draining"})
                (root / "successor-at-shutdown.json").write_text(json.dumps(health), encoding="utf-8")
                (root / "shutdown-requested").touch()
                shutdown_requested.set()
                return {"shutdown": True}
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
    ready = root / "ready.json.tmp"
    ready.write_text(json.dumps({
        "pid": os.getpid(), "start": locks.process_start_time(os.getpid()),
        "port": server.port, "token": token,
    }), encoding="utf-8")
    ready.replace(root / "ready.json")
    closed = mode == "closed-listener"
    if closed:
        server.close()
        (root / "listener-closed").touch()
    try:
        deadline = time.monotonic() + 60
        while not shutdown_requested.is_set() and not (root / "stop").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("fixture lifetime expired")
            time.sleep(0.01)
        if shutdown_requested.is_set() and mode == "retirement-gate":
            wait_for(root / "release")
    finally:
        if not closed:
            server.close()
    (root / "retirement-settled").touch()


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2])
