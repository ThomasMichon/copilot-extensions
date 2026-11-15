"""Foreground requests cannot be starved by successive warming targets."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agent_vault import cache, cache_warm, extensions, service
from agent_vault.service_connections import settle_owned

from .test_service_cache import cached_service as cached_service


class ContentionLock:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.depth = threading.local()
        self.waiting = threading.Event()
        self.allow_foreground = threading.Event()
        self.first_released = threading.Event()
        self.barged = threading.Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        depth = getattr(self.depth, "value", 0)
        name = threading.current_thread().name
        if depth == 0:
            if name == "foreground-reader":
                self.waiting.set()
                assert self.allow_foreground.wait(5)
            elif (
                name.startswith("vault-cache-warm")
                and self.first_released.is_set()
                and not self.allow_foreground.is_set()
            ):
                self.barged.set()
        acquired = self.lock.acquire(blocking, timeout)
        if acquired:
            self.depth.value = depth + 1
        return acquired

    def release(self) -> None:
        self.depth.value -= 1
        self.lock.release()
        if self.depth.value == 0 and threading.current_thread().name.startswith(
            "vault-cache-warm"
        ):
            self.first_released.set()

    def __enter__(self) -> ContentionLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


@pytest.mark.parametrize("foreground_fails", [False, True])
def test_waiting_ordinary_request_precedes_the_next_warm_target_even_after_failure(
    cached_service: tuple[service.VaultService, cache.PersistentCache, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
    foreground_fails: bool,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    monkeypatch.setattr(cache_warm, "get_cache", lambda: persistent)
    extensions.get_registry().register_cache_source(
        lambda _machine: [f"entry-{index}" for index in range(8)]
    )
    entered, finish_first = threading.Event(), threading.Event()
    reads: list[str] = []
    responses: list[object] = []
    lock = ContentionLock()
    monkeypatch.setattr(vault, "_credential_lock", lock)

    def read(_database: str, entry: str, _field: str) -> str:
        reads.append(entry)
        if entry == "Allowed/entry-0":
            entered.set()
            assert finish_first.wait(5)
        elif entry == "Allowed/foreground" and foreground_fails:
            raise RuntimeError("synthetic foreground failure")
        return "fixture-value"

    def foreground() -> None:
        try:
            responses.append(
                vault.handle_request(
                    {
                        "action": "get",
                        "entry": "foreground",
                        "group": "Allowed",
                    }
                )
            )
        except RuntimeError as error:
            responses.append(str(error))

    monkeypatch.setattr(vault.cli, "get_entry", read)
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        reader = threading.Thread(target=foreground, name="foreground-reader")
        barged = False
        try:
            runtime.tick()
            assert await asyncio.to_thread(entered.wait, 2)
            reader.start()
            assert await asyncio.to_thread(lock.waiting.wait, 2)
            finish_first.set()
            assert await asyncio.to_thread(lock.first_released.wait, 2)
            # Delay OS lock admission so scheduling luck cannot hide barging.
            barged = await asyncio.to_thread(lock.barged.wait, 0.2)
        finally:
            finish_first.set()
            lock.allow_foreground.set()
            if reader.ident is not None:
                await asyncio.to_thread(reader.join, 5)
                assert not reader.is_alive()
            if runtime.future is not None:
                await settle_owned(runtime.future)
            await runtime.close()
        assert not barged, "warming reacquired credential custody ahead of an ordinary request"

    asyncio.run(exercise())
    assert reads[:2] == ["Allowed/entry-0", "Allowed/foreground"]
    assert len(reads) == 9
    if foreground_fails:
        assert responses == ["synthetic foreground failure"]
    else:
        assert len(responses) == 1 and responses[0]["ok"]
        assert responses[0]["value"] == "fixture-value"
    assert vault._cache_warm.status(kpdb, "Allowed")["cached"] == 8


def test_closure_wakes_a_worker_waiting_for_foreground_admission(
    cached_service: tuple[service.VaultService, cache.PersistentCache, str, list[float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    monkeypatch.setattr(cache_warm, "get_cache", lambda: persistent)
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    monkeypatch.setattr(vault.cli, "get_entry", lambda *_a: pytest.fail("closing worker read"))
    waiting = threading.Event()
    original_wait = vault._cache_warm.wait_for_foreground

    def observe(deadline: float) -> bool:
        waiting.set()
        return original_wait(deadline)

    monkeypatch.setattr(vault._cache_warm, "wait_for_foreground", observe)
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            with vault._cache_warm.foreground():
                runtime.tick()
                assert await asyncio.to_thread(waiting.wait, 2)
                await asyncio.wait_for(runtime.close(), 1)
        finally:
            await runtime.close()

    asyncio.run(exercise())
    status = vault._cache_warm.status(kpdb, "Allowed")
    assert status["state"] == "partial" and status["reason"] == "service-closing"
    assert status["remaining"] == 1
