"""Actual native discovery must outlive neither its hosting admission nor close."""

import asyncio
from contextlib import asynccontextmanager, ExitStack
from concurrent.futures import ThreadPoolExecutor
import os
import threading
from types import SimpleNamespace

import pytest

from agent_bridge import app as bridge_app
from agent_bridge import app_agent_roster
from agent_bridge.agent_registry_cache import AgentRosterCache
from agent_bridge.agent_registry_discovery import DiscoveryResult
from agent_bridge.agent_registry_resolver import AgentResolver
from agent_bridge.models import ServiceConfig
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, daemon_test_lifecycle, inspect_instances, instance_environment,
    reserve_instance,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1",
        reason="actual admission/native work requires canonical containment",
    ),
]


@pytest.mark.parametrize(
    "mode", ["normal", "cancel", "startup", "production", "replacement", "sample", "clean", "late"],
)
async def test_host_admission_retains_blocked_native_cache_work(tmp_path, monkeypatch, mode):
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    native = []
    first_cache = []
    pool = ThreadPoolExecutor(max_workers=1) if mode == "sample" else None
    if mode == "clean":
        release.set()

    def blocked(*_args, **_kwargs):
        native.append(threading.current_thread())
        entered.set()
        try:
            assert release.wait(15)
            return DiscoveryResult(ok=True)
        finally:
            returned.set()

    monkeypatch.setattr(
        AgentResolver, "_scan_provider_report",
        blocked if mode != "sample" else lambda *_a, **_kw: DiscoveryResult(ok=True),
    )
    monkeypatch.setattr(AgentRosterCache, "_CANCELLATION_GRACE", .02)
    monkeypatch.setattr(AgentRosterCache, "_DISCOVERY_SHUTDOWN_GRACE", .02)

    async def warm(_self, **_kwargs):
        return True

    monkeypatch.setattr(AgentRosterCache, "wait_until_warm", warm)
    resolver = AgentResolver({}, {})
    ready, stop = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def inner(app):
        if mode == "late":
            app.state.agent_roster_cache = AgentRosterCache(resolver, refresh_interval=60)
            app.state.agent_roster_cache.start()
        else:
            await app_agent_roster.start_agent_roster_cache(
                app, resolver, SimpleNamespace(agent_roster_cache_interval=60),
            )
        first_cache.append(app.state.agent_roster_cache)
        if pool is not None:
            sample = pool.submit(blocked)
            app.state.agent_roster_cache._provider_work.samples.add(sample)
        try:
            if mode == "startup":
                assert await asyncio.to_thread(entered.wait, 3)
                raise RuntimeError("cache startup refused")
            if mode == "replacement":
                assert await asyncio.to_thread(entered.wait, 3)
                await app_agent_roster.start_agent_roster_cache(
                    app, resolver, SimpleNamespace(agent_roster_cache_interval=60),
                )
                assert app.state.agent_roster_cache is not first_cache[0]
            yield
        finally:
            await app_agent_roster.stop_agent_roster_cache(app)

    monkeypatch.setattr(bridge_app, "_lifespan", inner)
    app = bridge_app.create_app(
        config=ServiceConfig(db_path=str(tmp_path / "host.db"), enable_credential_relay=False),
        token="cache-fixture",
    )
    registry = tmp_path / "instances"
    with ExitStack() as stack:
        if mode == "production":
            monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", raising=False)
            receipt = reserve_instance(DaemonInstanceSpec(
                "agent-bridge", tmp_path, __file__, registry_root=registry,
            ))
            environment = instance_environment(receipt)
        else:
            environment = stack.enter_context(daemon_test_lifecycle(
                owner="cache-host-fixture", registry_root=registry,
            ))
        for name, value in environment.items():
            monkeypatch.setenv(name, value)

        async def run():
            async with app.router.lifespan_context(app):
                ready.set()
                await stop.wait()

        host = asyncio.create_task(run())
        try:
            if mode != "startup":
                await asyncio.wait_for(ready.wait(), 3)
            assert await asyncio.to_thread(entered.wait, 3)
            stop.set()
            if mode == "clean":
                await asyncio.wait_for(asyncio.shield(host), 5)
                assert inspect_instances(registry)[0]["state"] == "finished"
                return
            if mode == "cancel":
                host.cancel()
            await asyncio.sleep(.15)
            if mode == "cancel":
                host.cancel()
                await asyncio.sleep(.05)
            assert native[0].is_alive()
            assert not host.done(), "a cancelled discovery facade is not native retirement"
            assert inspect_instances(registry)[0]["state"] == "running"
            if mode == "production":
                record = inspect_instances(registry)[0]
                assert record["purpose"] == "production" and record["ttl"] == "0"
        finally:
            stop.set()
            release.set()
            if mode == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(host), 5)
            elif mode == "startup":
                with pytest.raises(RuntimeError, match="cache startup refused"):
                    await asyncio.wait_for(asyncio.shield(host), 5)
            else:
                await asyncio.wait_for(asyncio.shield(host), 5)
            assert await asyncio.to_thread(returned.wait, 3)
            if pool is not None:
                pool.shutdown(wait=True)
            else:
                for thread in native:
                    await asyncio.to_thread(thread.join, 3)
        assert inspect_instances(registry)[0]["state"] == "finished"
        if mode == "late":
            assert len(native) == 1, "late attachment must adopt, never replay discovery"
        if mode != "sample":
            assert not first_cache[0].discovery_ever_ok, "stopped discovery must not publish late"


@pytest.mark.parametrize("stopping", [False, True])
async def test_retiring_or_superseded_generation_never_starts_native_work(monkeypatch, stopping):
    resolver = AgentResolver({}, {})

    def refused(*_args, **_kwargs):
        pytest.fail("retired discovery must not dispatch native work")

    monkeypatch.setattr(resolver, "_scan_provider_report", refused)
    cache = AgentRosterCache(resolver)
    custody = app_agent_roster._RosterHosting(cache)
    cache._stopping = stopping
    cache._discovery_generation = 1
    await cache._do_scan_discovery(0)
    assert not custody.pending


async def test_late_queued_discovery_is_cancelled_without_dispatch(monkeypatch):
    resolver = AgentResolver({}, {})

    def refused(*_args, **_kwargs):
        pytest.fail("late attachment must not start a queued discovery")

    monkeypatch.setattr(resolver, "_scan_provider_report", refused)
    cache = AgentRosterCache(resolver)
    cache._discovery_generation = 1
    prior = cache._discovery_task = asyncio.create_task(cache._do_scan_discovery(1))
    custody = app_agent_roster._RosterHosting(cache)
    await custody.stop(settle=True)
    assert prior.cancelled() and not custody.pending
