"""Native enumeration retains its admission holder when a caller cancels."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_bridge.agent_registry_namespace import CliNamespaceResolver, NamespaceAgentInfo
from agent_bridge.agent_registry_cache import AgentRosterCache
from agent_bridge.agent_registry_resolver import AgentResolver


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_provider", [False, True])
async def test_cache_shutdown_retains_native_provider_holder(
    tmp_path: Path, replace_provider: bool,
) -> None:
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = (
        "import pathlib,sys,time;"
        "ready,release=map(pathlib.Path,sys.argv[1:3]);ready.write_text('started');"
        "\nwhile not release.exists(): time.sleep(0.01)"
        "\nprint('[{\"name\":\"late\",\"state\":\"available\"}]')"
    )
    provider = CliNamespaceResolver(
        "test", "unused",
        command=[sys.executable, "-c", script, str(ready), str(release)],
    )
    registry = AgentResolver({}, {})
    registry.register_namespace_resolver(provider)
    cache = AgentRosterCache(registry, refresh_interval=60)
    cache._CANCELLATION_GRACE = 0.02
    scan = cache._refresh_namespace("test")
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        native = provider.pending_list_task
        assert native is not None
        if replace_provider:
            registry.unregister_namespace_resolver("test")
            registry.register_namespace_resolver(
                CliNamespaceResolver("test", "missing-test-provider"),
            )
            cache._reconcile_namespace_set()
            await asyncio.gather(scan, return_exceptions=True)
            assert native in cache._owned_provider_reads
        await asyncio.wait_for(cache.stop(), timeout=2)
        assert scan.done()
        assert not native.done()
        assert native in cache._owned_provider_reads
        release.write_text("finish")
        await asyncio.wait_for(asyncio.shield(native), timeout=5)
        assert native not in cache._owned_provider_reads
        assert cache._entries["test"].rows == []
    finally:
        release.write_text("finish")
        await cache.stop()
        if provider.pending_list_task is not None:
            await asyncio.wait_for(asyncio.shield(provider.pending_list_task), timeout=12)


@pytest.mark.asyncio
async def test_cancelled_waiter_cannot_duplicate_native_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    launches = tmp_path / "launches"
    script = (
        "import os,pathlib,sys,time;"
        "ready,release,launches=map(pathlib.Path,sys.argv[1:4]);"
        "launches.open('a').write(str(os.getpid())+'\\n');"
        "ready.write_text(str(os.getpid()));"
        "\nwhile not release.exists(): time.sleep(0.01)"
        "\nprint('[{\"name\":\"native\",\"state\":\"available\"}]')"
    )
    provider = CliNamespaceResolver(
        "test", "unused",
        command=[sys.executable, "-c", script, str(ready), str(release), str(launches)],
    )
    first = asyncio.create_task(provider.list(timeout=10))
    joiners: list[asyncio.Task[list[NamespaceAgentInfo]]] = []
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        holder = provider._list_task
        assert holder is not None
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert provider._list_task is holder
        assert not holder.done()
        assert provider._list_admission.consecutive_overruns == 0
        assert provider._list_admission.retry_at is None
        joiners = [asyncio.create_task(provider.list(timeout=0.01)) for _ in range(8)]
        await asyncio.sleep(0)
        assert provider._list_task is holder
        assert len(launches.read_text().splitlines()) == 1
        release.write_text("finish")
        results = await asyncio.wait_for(asyncio.gather(*joiners), timeout=5)
        assert all([agent.name for agent in rows] == ["native"] for rows in results)
        assert holder.done()
        assert provider._list_task is None
        assert len(launches.read_text().splitlines()) == 1
    finally:
        release.write_text("finish")
        await asyncio.wait_for(asyncio.gather(first, *joiners, return_exceptions=True), timeout=12)
        if provider._list_task is not None:
            await asyncio.wait_for(asyncio.shield(provider._list_task), timeout=12)
