"""Known-ID owner lookup must not depend on resident roster publication."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge.agent_registry import AgentConfig
from agent_bridge.routes import worktree_probe, worktrees as wt

pytestmark = pytest.mark.contract("agent_bridge.worktree_native_resume")


@pytest.fixture
def lookup(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache.crawl_if_empty = AsyncMock(side_effect=AssertionError("must not warm a roster"))
    monkeypatch.setattr(wt, "_discovery_cache", cache)
    resolver = SimpleNamespace(agents={
        "owner": AgentConfig(name="owner", project="fixture"),
    })
    row = {
        "id": "wt-example", "path": r"C:\Example\Worktree",
        "branch": "worktree/example", "status": "active",
        "machine": "example", "bound_agent": "example-role",
    }
    calls = []

    async def command(agent, config, resolver, args):
        calls.append(args)
        assert args == ["record", "--worktree-id", "wt-example", "--json"]
        return json.dumps({"ok": True, "worktree": row})

    monkeypatch.setattr(wt, "_run_for_agent", command)
    return SimpleNamespace(cache=cache, resolver=resolver, row=row, calls=calls)


@pytest.mark.asyncio
async def test_cold_exact_lookup_uses_owner_record_without_roster(lookup):
    owner, entry = await worktree_probe.find_worktree_entry("wt-example", lookup.resolver)
    assert owner == "owner"
    assert entry.path == lookup.row["path"] and entry.bound_agent == "example-role"
    lookup.cache.crawl_if_empty.assert_not_called()
    assert len(lookup.calls) == 1
    assert lookup.cache.get_all() == {}, "Metadata probes must not invent a rendered roster"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"id": "other"}, {"path": None}, {"path": ""}, {"branch": None}, {"status": None},
])
async def test_exact_lookup_refuses_incomplete_or_wrong_record(lookup, change):
    lookup.row.update(change)
    assert await worktree_probe.find_worktree_entry("wt-example", lookup.resolver) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    "invalid-json", "[]", '{"ok":false,"error":{"code":"record-missing"}}',
    '{"ok":true,"worktrees":[{"id":"wt-example"}]}',
])
async def test_exact_lookup_never_falls_back_to_advisory_inventory(lookup, monkeypatch, payload):
    runner = AsyncMock(return_value=payload)
    monkeypatch.setattr(wt, "_run_for_agent", runner)
    assert await worktree_probe.find_worktree_entry("wt-example", lookup.resolver) == (None, None)
    runner.assert_awaited_once()
    lookup.cache.crawl_if_empty.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_exact_lookups_share_the_owner_probe(lookup, monkeypatch):
    release = asyncio.Event()

    async def command(*args):
        await release.wait()
        return json.dumps({"ok": True, "worktree": lookup.row})

    runner = AsyncMock(side_effect=command)
    monkeypatch.setattr(wt, "_run_for_agent", runner)
    calls = [
        asyncio.create_task(worktree_probe.find_worktree_entry("wt-example", lookup.resolver))
        for _ in range(3)
    ]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*calls)
    assert all(owner == "owner" and entry.id == "wt-example" for owner, entry in results)
    runner.assert_awaited_once()
