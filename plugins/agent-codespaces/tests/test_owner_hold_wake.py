"""A new hold wakes the Connection Owner instead of waiting out its interval."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_codespaces import connection_owner as owner


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "LIVE_FILE", tmp_path / "connection-owner.live.json")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


class _CountingOwner:
    def __init__(self, *, stop_after: int) -> None:
        self.reconciles = 0
        self.stop_after = stop_after
        self.stop_event = asyncio.Event()

    async def reconcile(self) -> None:
        self.reconciles += 1
        if self.reconciles >= self.stop_after:
            self.stop_event.set()

    async def shutdown(self) -> None:
        return None

    def active_codespaces(self) -> set[str]:
        return set()

    def idle_limit(self, idle: float) -> float:
        return idle


async def _first_reconcile(fake: _CountingOwner) -> None:
    while not fake.reconciles:
        await asyncio.sleep(0.02)


async def test_a_new_hold_wakes_the_owner_before_the_interval_ends(store: Path) -> None:
    """A tenant waits a bounded time for its relay, so a hold placed mid-sleep
    must start a reconcile promptly instead of sitting out the interval."""
    fake = _CountingOwner(stop_after=2)
    task = asyncio.create_task(
        owner.run_owner_daemon(fake, interval=60, stop_event=fake.stop_event)
    )
    await asyncio.wait_for(_first_reconcile(fake), timeout=5)
    owner.hold("cs-new", "tenant-a")
    await asyncio.wait_for(task, timeout=5)
    assert fake.reconciles == 2


async def test_a_contended_hold_store_never_blocks_the_owner_loop(store: Path) -> None:
    """The wait polls holds through a store lock that retries with time.sleep;
    contention must stall only a worker thread, never the Owner's event loop."""
    fake = _CountingOwner(stop_after=10**9)
    task = asyncio.create_task(
        owner.run_owner_daemon(fake, interval=60, stop_event=fake.stop_event)
    )
    await asyncio.wait_for(_first_reconcile(fake), timeout=5)
    lock_file = store / "connection-owner.lock"
    lock_file.write_text("held", "utf-8")
    loop = asyncio.get_running_loop()
    try:
        started = loop.time()
        for _ in range(30):  # 1.5s spans at least one contended poll
            await asyncio.sleep(0.05)
        # A blocked loop would stall here for the lock's 10s retry window.
        assert loop.time() - started < 5
    finally:
        lock_file.unlink()
    fake.stop_event.set()
    await asyncio.wait_for(task, timeout=15)


async def test_a_heartbeat_refresh_does_not_wake_the_owner(store: Path) -> None:
    owner.hold("cs-held", "tenant-a")
    fake = _CountingOwner(stop_after=10**9)
    task = asyncio.create_task(
        owner.run_owner_daemon(fake, interval=60, stop_event=fake.stop_event)
    )
    await asyncio.wait_for(_first_reconcile(fake), timeout=5)
    owner.hold("cs-held", "tenant-a")
    await asyncio.sleep(1.5)
    assert fake.reconciles == 1
    fake.stop_event.set()
    await asyncio.wait_for(task, timeout=5)
