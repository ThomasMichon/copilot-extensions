"""Exercise the actual lifespan loop, not a cached enablement proxy."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agent_dispatch import coordinator, self_retire, self_update
from agent_dispatch.queue import TaskQueue


@pytest.mark.parametrize("shared", [False, True])
def test_ready_coordinator_rechecks_inhibition_without_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shared: bool,
) -> None:
    from zdd import routing

    root = tmp_path / ".agent-dispatch"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", "0")
    switch = (
        tmp_path / ".copilot-extensions" / "bootstrap-killswitch.json"
        if shared else root / "action-controls.json"
    )
    switch.parent.mkdir(parents=True)
    switch.write_text(
        '{"active": true}' if shared
        else '{"coordinator-replacement": {"disabled": true}}',
    )
    monkeypatch.setattr(coordinator, "_self_update_settings", lambda: (True, 0.01, 1, 0.0))
    monkeypatch.setattr(routing, "read_table", lambda _root: {
        "active": {
            "pid": os.getpid(), "generation": 1, "bind": "127.0.0.1",
            "port": 12345, "version": "old",
        },
    })
    monkeypatch.setattr(self_retire, "is_superseded", lambda *_args: False)
    target = tmp_path / "next-python"
    monkeypatch.setattr(self_update, "stale_target", lambda *_args: target)

    async def governance(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(coordinator, "_governance_backoff", governance)
    real_sleep = asyncio.sleep
    polls = 0
    spawned: list[Path] = []

    async def scenario() -> None:
        observed = asyncio.Event()

        def spawn(selected: Path) -> None:
            spawned.append(selected)
            observed.set()

        async def sleep(seconds: float) -> None:
            nonlocal polls
            task = asyncio.current_task()
            name = task.get_coro().__qualname__ if task is not None else ""
            if "_self_update_loop" in name:
                if seconds == 0.01:
                    polls += 1
                    if polls <= 2:
                        assert spawned == []
                    elif polls == 3:
                        switch.write_text('{"active": false}' if shared else "{}")
                    else:
                        await asyncio.Event().wait()
                await real_sleep(0)
            else:
                await real_sleep(seconds)

        monkeypatch.setattr(coordinator, "_spawn_self_deploy", spawn)
        monkeypatch.setattr(asyncio, "sleep", sleep)
        queue = TaskQueue(tmp_path / "test.db")
        app = coordinator.create_app(queue, enable_mcp=False, verification_interval=0)
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(observed.wait(), timeout=3)
            assert spawned == [target]
            assert polls >= 3

    asyncio.run(scenario())
