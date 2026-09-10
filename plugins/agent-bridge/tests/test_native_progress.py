"""Only identity-bound, allowlisted preparation phases reach native status."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge.native_manager import NativeManager


@pytest.mark.asyncio
async def test_progress_is_visible_while_provider_readiness_is_pending(tmp_path, monkeypatch):
    output = asyncio.StreamReader()
    process = SimpleNamespace(
        returncode=None, stdout=output,
        stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
        wait=AsyncMock(return_value=0),
    )

    def close():
        process.returncode = 0
        output.feed_eof()

    process.stdin = SimpleNamespace(close=close)
    spawned = asyncio.Event()

    async def spawn(*a, **k):
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    manager = NativeManager(tmp_path / "controller", lambda: ["provider"])
    initial = await manager.start({
        "requestId": "request-one", "codespace": "example-space", "owner": str(tmp_path),
        "cwd": "/workspaces/example", "command": "exec copilot",
    })
    await asyncio.wait_for(spawned.wait(), 2)
    identity = {"executionId": initial["executionId"], "generation": initial["generation"]}

    async def feed(**data):
        output.feed_data((json.dumps({"event": "progress", **identity, **data}) + "\n").encode())
        await asyncio.sleep(0)
        return manager.store().get(identity["executionId"], identity["generation"])

    try:
        row = await feed(phase="local-config", status="started", detail="secret-must-not-be-published")
        assert row["data"]["phase"] == "preparing/local-config/started"
        assert (await manager.status(identity["executionId"], identity["generation"]))["phase"] == "preparing/local-config/started"
        assert not manager.tasks[identity["executionId"]].done()
        assert "secret-must-not-be-published" not in json.dumps(row)
        for invalid in (
            {"generation": "wrong", "phase": "owner-admission", "status": "reached"},
            {"phase": "secret-unknown-phase", "status": "started"},
            {"phase": {"secret": "payload"}, "status": "started"},
            {"phase": "local-config", "status": "secret-unknown-status"},
        ):
            row = await feed(**invalid)
            assert row["data"]["phase"] == "preparing/local-config/started"
        manager.store().update(identity["executionId"], identity["generation"], state="stopping", phase="stopping")
        row = await feed(phase="ssh-to-target", status="reached")
        assert row["state"] == row["data"]["phase"] == "stopping"
    finally:
        await manager.shutdown()


@pytest.mark.parametrize("state,launched", [
    ("ready", True), ("stopping", False), ("stopped", False),
    ("starting", True), ("unrepresented", True),
])
def test_late_preparation_progress_cannot_overwrite_lifecycle(tmp_path, state, launched):
    manager = NativeManager(tmp_path / "controller", lambda: ["provider"])
    store = manager.store(create=True)
    store.reserve("execution", "generation", "request", "example-space", "owner", "hash", {})
    store.update("execution", "generation", state=state, launchRequested=launched, phase="preserved")
    manager._save_progress(store, "execution", "generation", "target-auth-env", "started")
    assert store.get("execution", "generation")["data"]["phase"] == "preserved"
