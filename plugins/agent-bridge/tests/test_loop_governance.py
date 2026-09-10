from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agent_bridge.routes import worktrees as worktrees_module
from agent_bridge.routes.worktrees import WorktreeDiscoveryCache, _WorktreeEntry


class _Governance:
    def __init__(self, states: list[dict[str, object]]) -> None:
        self._states = iter(states)
        self.calls: list[str] = []

    def recheck(self, checkpoint: str) -> dict[str, object]:
        self.calls.append(checkpoint)
        return next(self._states)


def _resolver():
    agent_cfg = MagicMock()
    agent_cfg.project = "agent-worktrees"
    agent_cfg.worktree_discovery = True
    agent_cfg.host = None
    resolver = MagicMock()
    resolver.agents = {"local": agent_cfg}
    return resolver


@pytest.mark.asyncio
async def test_worktree_discovery_backs_off_at_iteration_boundary_without_mutating(
    monkeypatch,
):
    cache = WorktreeDiscoveryCache(interval=5)
    cache._governance = _Governance([
        {"status": "backoff", "reason": "maintenance-active"},
    ])
    resolver = _resolver()
    crawls: list[str] = []

    async def fake_crawl(_resolver_obj) -> None:
        crawls.append("crawl")

    async def fake_sleep(delay: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(cache, "crawl", fake_crawl)
    monkeypatch.setattr(worktrees_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await cache._loop(resolver)

    assert cache._governance.calls == ["iteration-boundary:worktree-discovery"]
    assert crawls == []


@pytest.mark.asyncio
async def test_worktree_discovery_rechecks_before_cache_update_and_discards_inflight_results(
    monkeypatch,
):
    cache = WorktreeDiscoveryCache(interval=5)
    governance = _Governance([
        {"status": "revalidation-required", "reason": "generation-changed"},
    ])
    cache._governance = governance
    resolver = _resolver()

    async def fake_crawl_agent(name, config, resolver_obj):
        return [
            _WorktreeEntry(
                id="wt-1",
                agent_name=name,
                machine="local",
                path="/w/1",
                branch="main",
                status="active",
            )
        ]

    monkeypatch.setattr(cache, "_crawl_agent", fake_crawl_agent)

    with pytest.raises(RuntimeError, match="governance changed before cache update"):
        await cache.crawl(resolver)

    assert governance.calls == ["pre-mutation:update-worktree-cache"]
    assert cache.get_all() == {}


@pytest.mark.asyncio
async def test_worktree_discovery_proceeds_when_iteration_and_pre_mutation_checks_stay_current(
    monkeypatch,
):
    cache = WorktreeDiscoveryCache(interval=5)
    governance = _Governance([
        {"status": "ready", "reason": "baseline-established", "baseline": {}},
        {"status": "ready", "reason": "current", "baseline": {}},
    ])
    cache._governance = governance
    resolver = _resolver()

    async def fake_crawl_agent(name, config, resolver_obj):
        return [
            _WorktreeEntry(
                id="wt-1",
                agent_name=name,
                machine="local",
                path="/w/1",
                branch="main",
                status="active",
            )
        ]

    sleeps = {"count": 0}

    async def fake_sleep(delay: float) -> None:
        sleeps["count"] += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(cache, "_crawl_agent", fake_crawl_agent)
    monkeypatch.setattr(worktrees_module.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await cache._loop(resolver)

    groups = cache.get_all()
    assert governance.calls == [
        "iteration-boundary:worktree-discovery",
        "pre-mutation:update-worktree-cache",
    ]
    assert groups["local"][0].id == "wt-1"
