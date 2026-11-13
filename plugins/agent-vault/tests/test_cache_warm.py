"""Unlock-triggered warming is bounded, non-prompting and generation-owned."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
import json
import threading
import time
from types import SimpleNamespace

import pytest

from agent_vault import cache, cache_sources, cache_warm, cli, extensions, service, service_cache
from agent_vault.service_connections import VaultConnections, settle_owned

from .test_service_cache import cached_service as cached_service

CacheFixture = tuple[service.VaultService, cache.PersistentCache, str, list[float]]


@pytest.fixture
def warming_service(
    cached_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> CacheFixture:
    _vault, persistent, _kpdb, _clock = cached_service
    monkeypatch.setattr(cache_warm, "get_cache", lambda: persistent)
    return cached_service


async def drain(vault: service.VaultService, kpdb: str) -> dict[str, object]:
    runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
    try:
        runtime.tick()
        assert runtime.future is not None
        await settle_owned(runtime.future)
        return vault._cache_warm.status(kpdb, "Allowed")
    finally:
        await runtime.close()


@pytest.mark.parametrize("source", ["provider", "password", "prompt"])
def test_successful_unlock_paths_warm_only_configured_targets_without_renewing_password(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
) -> None:
    vault, _persistent, kpdb, clock = warming_service
    registry = extensions.get_registry()
    registry.register_cache_source(
        lambda _machine: [
            "entry",
            ("entry", "Password"),
            ("other", "username"),
        ]
    )
    calls: list[tuple[str, str]] = []

    def read(_database: str, entry: str, field: str) -> str:
        calls.append((entry, field))
        clock[0] += 1
        return "value:" + entry + ":" + field

    vault.cli.clear_password(kpdb)
    vault._password_set_at.pop(kpdb)
    monkeypatch.setattr(
        vault.cli, "verify_password", lambda _db, password: password == "fixture-master"
    )
    monkeypatch.setattr(vault.cli, "get_entry", read)
    request: dict[str, object] = {"action": "unlock", "kpdb": kpdb, "group": "Allowed"}
    if source == "provider":
        registry.register_unlock_provider(lambda _context: "fixture-master")
    elif source == "password":
        request["password"] = "fixture-master"
    else:
        request["prompt"] = True
        monkeypatch.setattr(service, "prompt_password", lambda *_args: "fixture-master")
    assert vault.handle_request(request) == {"ok": True}
    assert calls == []
    assert vault._cache_warm.status(kpdb, "Allowed")["state"] == "queued"
    status = asyncio.run(drain(vault, kpdb))
    assert status["state"] == "complete"
    assert (status["selected"], status["cached"], status["failed"]) == (2, 2, 0)
    assert calls == [("Allowed/entry", "password"), ("Allowed/other", "username")]
    assert vault._password_set_at[kpdb] == 1000.0
    assert kpdb not in json.dumps(status) and "fixture-master" not in json.dumps(status)
    monkeypatch.setattr(
        cli,
        "send_command",
        lambda request: vault.handle_request(
            {
                **request,
                "group": "Allowed",
            }
        ),
    )
    assert cli.cmd_ping(SimpleNamespace()) == 0
    rendered = capsys.readouterr().out
    assert "cache warm=complete (2/2 cached" in rendered
    assert "fixture-master" not in rendered and "value:Allowed/" not in rendered

    clock[0] = 1010.0
    monkeypatch.setattr(
        vault, "ensure_unlocked", lambda *_a, **_kw: pytest.fail("warm hit unlocked")
    )
    response = vault.handle_request({"action": "get", "entry": "entry", "group": "Allowed"})
    assert response["value"] == "value:Allowed/entry:password"
    assert not vault.cli.has_password(kpdb)


def test_ordinary_reads_and_failed_unlocks_do_not_schedule_warming(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["configured"])
    assert vault.handle_request({"action": "get", "entry": "ordinary", "group": "Allowed"})["ok"]
    assert vault._cache_warm.status(kpdb, "Allowed")["state"] == "idle"
    vault.cli.clear_password(kpdb)
    vault._password_set_at.pop(kpdb)
    monkeypatch.setattr(vault.cli, "verify_password", lambda *_args: False)
    assert not vault.handle_request(
        {
            "action": "unlock",
            "kpdb": kpdb,
            "group": "Allowed",
            "password": "wrong",
        }
    )["ok"]
    assert vault._cache_warm.status(kpdb, "Allowed")["state"] == "idle"


def test_unlocks_coalesce_but_a_new_unlock_with_the_same_clock_gets_a_fresh_budget(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    monkeypatch.setattr(vault.cli, "verify_password", lambda *_args: True)
    calls: list[str] = []
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda _db, entry, _field: calls.append(entry) or "value"
    )
    explicit = {"action": "unlock", "kpdb": kpdb, "group": "Allowed", "password": "fixture-master"}
    repeated = {"action": "unlock", "kpdb": kpdb, "group": "Allowed"}
    assert vault.handle_request(explicit)["ok"]
    vault._password_handoff_deadlines[kpdb] = 1005.0
    assert vault.handle_request(repeated)["ok"]
    assert vault._password_set_at[kpdb] == 1000.0
    assert vault._password_handoff_deadlines[kpdb] == 1005.0
    vault._cache_warm._pending[(kpdb, "Allowed")].deadline = time.monotonic() - 1
    assert vault.handle_request(explicit)["ok"]

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            runtime.tick()
            assert runtime.future is not None
            await settle_owned(runtime.future)
            assert vault._cache_warm.status(kpdb, "Allowed")["state"] == "complete"
            assert vault.handle_request(repeated)["ok"]
            runtime.tick()
            await settle_owned(runtime.future)
            assert calls == ["Allowed/entry"]
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_source_and_entry_failures_have_truthful_partial_credential_free_status(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service

    def broken(_machine: str | None) -> Iterator[str]:
        yield "good"
        raise RuntimeError("private source diagnostic")

    registry = extensions.get_registry()
    registry.register_cache_source(broken)
    registry.register_cache_source(lambda _machine: ["bad", "missing"])

    def read(_database: str, entry: str, _field: str) -> str | None:
        if entry == "Allowed/bad":
            raise RuntimeError("private backend diagnostic")
        return "private synthetic value" if entry == "Allowed/good" else None

    monkeypatch.setattr(vault.cli, "get_entry", read)
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)
    status = asyncio.run(drain(vault, kpdb))
    assert status["state"] == "partial"
    assert (status["selected"], status["cached"], status["failed"], status["failed_sources"]) == (
        3,
        1,
        2,
        1,
    )
    assert status["remaining"] == 0
    assert "private" not in json.dumps(status) + caplog.text
    assert kpdb not in json.dumps(status)


def test_pending_rotation_is_preserved_and_never_reported_as_warmed(
    warming_service: CacheFixture,
) -> None:
    vault, persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    assert persistent.begin_replace("Allowed/entry", "Password", "candidate", 2)
    journal = persistent.pending_replace("Allowed/entry", "Password")
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)
    status = asyncio.run(drain(vault, kpdb))
    assert (status["state"], status["cached"], status["failed"]) == ("partial", 0, 1)
    assert persistent.pending_replace("Allowed/entry", "Password") == journal


def test_expiry_during_backend_custody_cannot_reinvoke_an_unlock_provider(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, clock = warming_service
    registry = extensions.get_registry()
    registry.register_cache_source(lambda _machine: ["entry"])
    providers: list[object] = []
    registry.register_unlock_provider(
        lambda context: providers.append(context) or "fixture-master"
    )
    monkeypatch.setattr(vault.cli, "verify_password", lambda *_args: True)
    monkeypatch.setattr(service, "prompt_password", lambda *_args: pytest.fail("warming prompted"))

    @contextlib.contextmanager
    def expiring_custody(*_args: object) -> Iterator[None]:
        clock[0] = 1010.0
        yield

    monkeypatch.setattr(service_cache, "backend_custody", expiring_custody)
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)
    status = asyncio.run(drain(vault, kpdb))
    assert status["state"] == "partial" and status["cached"] == 0
    assert providers == [] and not vault.cli.has_password(kpdb)
    assert kpdb not in vault._password_set_at


def test_expired_work_budget_refuses_cache_construction_and_source_callbacks(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(
        lambda _machine: pytest.fail("expired source ran")
    )
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)
    vault._cache_warm._pending[(kpdb, "Allowed")].deadline = time.monotonic() - 1
    monkeypatch.setattr(
        cache_warm, "get_cache", lambda: pytest.fail("expired work constructed cache")
    )
    status = asyncio.run(drain(vault, kpdb))
    assert status["state"] == "partial" and status["reason"] == "deadline"


def test_target_budget_stops_additional_backend_operations(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    yielded: list[int] = []
    reads: list[str] = []

    def configured(_machine: str | None) -> Iterator[str]:
        for index in range(10):
            yielded.append(index)
            yield "entry-" + str(index)

    extensions.get_registry().register_cache_source(configured)
    monkeypatch.setattr(cache_warm, "MAX_TARGETS", 2)
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda _db, entry, _field: reads.append(entry) or "value"
    )
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)
    status = asyncio.run(drain(vault, kpdb))
    assert status["state"] == "partial" and status["truncated"] is True
    assert status["selected"] == status["cached"] == 2
    assert yielded == [0, 1] and reads == ["Allowed/entry-0", "Allowed/entry-1"]


def test_queue_capacity_refuses_another_context_without_displacing_admitted_work(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    monkeypatch.setattr(cache_warm, "MAX_CONTEXTS", 2)
    assert vault._cache_warm.enqueue(kpdb, "First", confirmed=True).state == "queued"
    assert vault._cache_warm.enqueue(kpdb, "Second").state == "queued"
    refused = vault._cache_warm.enqueue(kpdb, "Third")
    assert refused.state == "not-queued" and refused.reason == "capacity"
    assert vault._cache_warm.status(kpdb, "Third")["reason"] == "capacity"
    assert vault._cache_warm.take().group == "First"
    assert vault._cache_warm.take().group == "Second"
    assert vault._cache_warm.take() is None
    vault._cache_warm.close()


def test_completed_context_can_be_reused_at_capacity_without_losing_unlock_identity(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    monkeypatch.setattr(cache_warm, "MAX_CONTEXTS", 1)
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            for group in ("Allowed", "Other"):
                if group == "Other":
                    assert vault._cache_warm.enqueue(kpdb, group).state == "queued"
                runtime.tick()
                assert runtime.future is not None
                await settle_owned(runtime.future)
                assert vault._cache_warm.status(kpdb, group)["state"] == "complete"
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_cutover_and_cancelled_closure_retain_native_work_until_it_really_settles(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry", "second"])
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    calls: list[str] = []

    def read(_database: str, entry: str, _field: str) -> str:
        calls.append(entry)
        entered.set()
        try:
            assert release.wait(5)
            return "value"
        finally:
            exited.set()

    monkeypatch.setattr(vault.cli, "get_entry", read)
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)

    async def exercise() -> None:
        connections = VaultConnections(vault, service.handle_client)
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, connections)
        closing: asyncio.Task[None] | None = None
        try:
            runtime.tick()
            assert await asyncio.to_thread(entered.wait, 2)
            assert runtime.future in connections.work and runtime.future in connections.workers
            health = await asyncio.wait_for(
                asyncio.to_thread(
                    vault.handle_request,
                    {"action": "ping", "health_only": True},
                ),
                1,
            )
            assert health["ok"]
            generation = service.ServiceGeneration(vault, connections, [], [])
            released: list[bool] = []
            generation.writer = SimpleNamespace(release=lambda: released.append(True))
            generation.has_writer = True
            generation.retiring = {"runtime_generation": "synthetic-next"}
            await generation._retire()
            assert generation.has_writer and released == []
            closing = asyncio.create_task(runtime.close())
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done() and not exited.is_set()
            assert runtime.future in connections.work
        finally:
            release.set()
            if closing is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await closing
            else:
                await runtime.close()
        assert exited.is_set() and closing is not None and closing.cancelled()
        assert not connections.work and not connections.workers
        assert calls == ["Allowed/entry"]
        assert vault._cache_warm.enqueue(kpdb, "Allowed").reason == "service-closing"

    asyncio.run(exercise())


def test_existing_serving_housekeeping_drives_warming_and_settles_it_on_exit(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)
    original_sleep = asyncio.sleep
    ticks: list[bool] = []

    class Generation:
        def __init__(self, *_args: object) -> None:
            pass

        async def start(self, _port: int) -> None:
            pass

        async def tick(self) -> None:
            ticks.append(True)
            assert len(ticks) < 200
            if vault._cache_warm.status(kpdb, "Allowed")["state"] == "complete":
                vault._shutdown = True
            await original_sleep(0.01)

    async def brief_wait(_seconds: float) -> None:
        await original_sleep(0.01)

    monkeypatch.setattr(service, "ServiceGeneration", Generation)
    monkeypatch.setattr(service.asyncio, "sleep", brief_wait)
    asyncio.run(service._serve(vault, 0, [], [], None))
    assert vault._cache_warm.closed
    assert vault._cache_warm.status(kpdb, "Allowed")["cached"] == 1


def test_deadline_crossing_backend_custody_refuses_the_final_native_read(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    monotonic = [0.0]
    clock = SimpleNamespace(monotonic=lambda: monotonic[0])
    monkeypatch.setattr(cache_warm, "time", clock)
    monkeypatch.setattr(cache_sources, "time", clock)
    monkeypatch.setattr(
        service_cache,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, time=time.time, time_ns=time.time_ns),
    )
    vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True)
    request = vault._cache_warm._pending[(kpdb, "Allowed")]
    custody: list[bool] = []
    reads: list[str] = []

    @contextlib.contextmanager
    def late_custody(*_args: object) -> Iterator[None]:
        custody.append(True)
        monotonic[0] = request.deadline + 1
        yield

    monkeypatch.setattr(service_cache, "backend_custody", late_custody)
    monkeypatch.setattr(
        vault.cli,
        "get_entry",
        lambda _db, entry, _field: reads.append(entry) or "not-admitted",
    )
    status = asyncio.run(drain(vault, kpdb))
    assert custody == [True] and reads == []
    assert status["state"] == "partial" and status["reason"] == "deadline"
    assert (status["selected"], status["cached"], status["failed"], status["remaining"]) == (
        1,
        0,
        0,
        1,
    )
