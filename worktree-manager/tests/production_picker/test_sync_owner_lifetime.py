"""Application exit settles local Sync without replaying another selected item."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time

import pytest

from worktree_manager import engine_client, engine_sync
from worktree_manager.production_picker.picker_tui import data_local, engine, maintenance


@pytest.mark.parametrize("exit_kind", ["idle", "owner"])
@pytest.mark.parametrize("stream", [False, True])
def test_real_picker_exit_settles_sync_worker_and_observer(
    tmp_path, monkeypatch, exit_kind, stream
):
    ready = tmp_path / "ready"
    alive = tmp_path / "alive"
    provider = (
        "import json,sys,time\nfrom pathlib import Path\n"
        "if 'sync' not in sys.argv:\n"
        " print('{}');sys.exit(0)\n"
        f"Path({str(ready)!r}).write_text(json.dumps(sys.argv))\n"
        "while True:\n"
        f" Path({str(alive)!r}).write_text(str(time.monotonic()))\n"
        " if '--stream' in sys.argv:\n"
        "  print(json.dumps({'type':'progress','msg':'Waiting for nested work'}),flush=True)\n"
        " time.sleep(.03)\n"
    )
    command = [sys.executable, "-B", "-c", provider]
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: command)
    monkeypatch.setattr(engine_sync, "supports_progress", lambda _command: stream)
    monkeypatch.setattr(maintenance.context, "project", lambda: "demo")
    monkeypatch.setattr(engine, "IDLE_TIMEOUT_SECS", 60)
    # Keep first-paint chrome and real app/screen teardown, without a live roster.
    monkeypatch.setattr(engine.PickerScreen, "on_mount", lambda self: self._setup_skeleton())
    owner_closed = threading.Event()
    captured = []

    async def exercise():
        app = engine.PickerApp(data_local, live=True, lifetime_cancelled=owner_closed.is_set)
        async with app.run_test(size=(96, 36)) as pilot:
            screen = app.query_one(engine.PickerScreen)
            screen.real_ops = True
            screen._src_local = lambda: ("fixture", "Win")
            rows = [
                {
                    "raw": {"id": item},
                    "selection_id": item,
                    "id4": item,
                    "title": item,
                    "machine": "fixture",
                    "env": "Win",
                }
                for item in ("first", "later")
            ]
            screen._run_op_progress("Sync", "sync", rows, armed=True)
            captured.append(screen.executor)
            deadline = time.monotonic() + 10
            while not (ready.exists() and alive.exists()) and time.monotonic() < deadline:
                await pilot.pause(0.05)
            assert ready.exists() and alive.exists(), screen.executor.result("first")
            assert "does not cancel" in screen.footer(96).plain
            if exit_kind == "idle":
                app._last_input_activity = time.monotonic() - 3600
                app._check_idle_timeout()
            else:
                owner_closed.set()
                app._check_owner_lifetime()
        assert app.binding_error is None

    asyncio.run(exercise())
    executor = captured[0]
    assert not executor._thread.is_alive() and executor.is_done()
    result = executor.result("first")
    assert result["outcome"] == "unknown" and "owner closed" in result["reason"]
    assert executor.state("later") == maintenance.PENDING and executor.result("later") is None
    argv = json.loads(ready.read_text())
    assert argv[argv.index("--worktree-id") + 1] == "first"
    assert ("--stream" in argv) is stream
    value = alive.read_text()
    time.sleep(0.15)
    assert alive.read_text() == value
    assert not any(
        thread.name in ("sync-progress", "maint-exec") for thread in threading.enumerate()
    )
