"""Measured cached rendering and owned live-refresh regressions."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge.agent_registry import AgentConfig
from agent_bridge.client import BridgeClient
from agent_bridge.routes.worktree_entry import WorktreeEntry
from agent_bridge.routes import worktrees as wt
from agent_bridge.routes import worktree_refresh as refresh

def resolver(*names: str):
    return SimpleNamespace(agents={
        name: AgentConfig(name=name, project="sample")
        for name in names
    })


def entry(name="local", ident="w1"):
    return wt._WorktreeEntry(
        id=ident, agent_name=name, machine=name, path="/sample",
        branch="topic", status="active", mux_session=True, mux_attached=False,
    )


@pytest.mark.guard
def test_stable_row_owner_preserves_the_complete_serialized_fixture():
    fixture = json.loads((
        Path(__file__).parents[1] / "contract" / "fixtures" / "http" / "current"
        / "worktree-render-worktree_entry-generation-36.json"
    ).read_text(encoding="utf-8"))
    assert entry().to_dict() == fixture["rendered_row"]
    assert wt._WorktreeEntry is WorktreeEntry


def route_client(cache, monkeypatch):
    app = FastAPI()
    app.include_router(wt.router)
    monkeypatch.setattr(wt, "get_cache", lambda: cache)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


@pytest.mark.guard
@pytest.mark.asyncio
async def test_real_route_cold_render_under_two_seconds(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    blocked = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def read(name, cfg, res, *, epoch=None, classify=True, fresh=False):
        calls.append(classify)
        started.set()
        await blocked.wait()
        return [entry()]

    monkeypatch.setattr(cache, "_crawl_agent", read)
    async with route_client(cache, monkeypatch) as client:
        before = time.monotonic()
        responses = await asyncio.gather(*[
            client.get("/api/v1/worktrees?render=true") for _ in range(12)
        ])
        assert time.monotonic() - before < 2
        assert all(r.status_code == 200 for r in responses)
        assert all(r.json()["groups"] == {} for r in responses)
        assert all(r.json()["discovery"]["warming"] for r in responses)
        assert all(r.json()["discovery"]["completeness"] == "unknown" for r in responses)
        fixture = json.loads((
            Path(__file__).parents[1] / "contract" / "fixtures" / "http" / "current"
            / "worktree-render-generation-36.json"
        ).read_text(encoding="utf-8"))
        expected = fixture["cold_projection"]
        body = responses[0].json()
        assert body["groups"] == expected["groups"]
        assert {key: body["discovery"][key] for key in expected["discovery"]} == expected["discovery"]
        await started.wait()
        assert calls == [False]
        blocked.set()
        await cache.crawl(cache._resolver)
        await cache.stop()


@pytest.mark.asyncio
async def test_real_route_large_published_warm_snapshot_under_two_seconds(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    release = asyncio.Event()
    started = asyncio.Event()
    raw = json.dumps({
        "version": 1, "worktrees": [entry(ident=f"w{i}").to_dict() for i in range(10000)],
    })
    calls = 0

    async def source(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            started.set()
            await release.wait()
        return raw, ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    await cache.crawl(cache._resolver)
    slow_refresh = asyncio.create_task(cache.refresh_live())
    await started.wait()
    async with route_client(cache, monkeypatch) as client:
        before = time.monotonic()
        response = await client.get("/api/v1/worktrees?render=true")
        rows = response.json()["groups"]["local"]
        assert time.monotonic() - before < 2
        assert len(rows) == 10000
        assert rows[0]["interactive_cli"] == "at-rest"
        assert rows[0]["session_id"] is None
        assert calls == 2
        assert response.json()["discovery"]["completeness"] == "complete"
        release.set()
        await slow_refresh
        await cache.stop()


@pytest.mark.asyncio
async def test_warm_source_generation_and_failed_peer_preserve_inventory(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local", "peer")
    for name, cfg in cache._resolver.agents.items():
        cfg.project = name
    bodies = {
        "local": json.dumps({"version": 1, "worktrees": [entry().to_dict()]}),
        "peer": json.dumps({"version": 1, "worktrees": [entry("peer", "p1").to_dict()]}),
    }

    async def source(project, args, *, timeout=None):
        return bodies[project], ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    await cache.crawl(cache._resolver)
    first = cache._refresh.metadata()
    assert first["completeness"] == "complete"
    assert first["freshness"] == "fresh"
    bodies["local"] = '{"version":1,"worktrees":[]}'
    bodies["peer"] = "malformed"
    result = await cache.refresh_live()
    assert result["outcome"] == "partial"
    assert cache.get_all()["local"] == []
    assert cache.get_all()["peer"][0].id == "p1"
    metadata = cache._refresh.metadata()
    assert metadata["completeness"] == "incomplete"
    assert metadata["sources"]["peer"]["freshness"] == "stale"
    assert metadata["sources"]["peer"]["diagnostic"] == "source-malformed"
    assert metadata["sources"]["peer"]["generation"] == first["generation"]
    assert metadata["sources"]["local"]["generation"] > first["generation"]
    assert metadata["last_refresh"]["goal_met"] is False
    bodies["peer"] = '{"version":1,"worktrees":[]}'
    await cache.refresh_live()
    assert cache.get_all() == {"local": [], "peer": []}
    assert cache._refresh.metadata()["completeness"] == "complete"
    async with route_client(cache, monkeypatch) as client:
        before = time.monotonic()
        response = await client.get("/api/v1/worktrees?render=true")
        assert time.monotonic() - before < 2
        assert response.json()["groups"] == {"local": [], "peer": []}
        assert response.json()["discovery"]["completeness"] == "complete"


@pytest.mark.asyncio
async def test_live_route_real_ten_second_wait_and_cancelled_caller_keep_holder(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    blocked = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def read(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await blocked.wait()
        return [entry()]

    monkeypatch.setattr(cache, "_crawl_agent", read)
    async with route_client(cache, monkeypatch) as client:
        abandoned = asyncio.create_task(client.get("/api/v1/worktrees?refresh=true"))
        await started.wait()
        holder = cache._refresh.task
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
        before = time.monotonic()
        response = await client.get("/api/v1/worktrees?refresh=true")
        elapsed = time.monotonic() - before
        assert 9.9 <= elapsed < 11
        assert response.json()["refresh"]["outcome"] == "timed-out"
        assert response.json()["refresh"]["pending"] is True
        assert calls == 1
        assert cache._refresh.task is holder
        assert not holder.cancelled()
        # Windows can deliver the wait timer just below ten measured seconds.
        # Establish a real holder overrun instead of inferring one from expiry.
        await asyncio.sleep(0.05)
        later = asyncio.create_task(cache.refresh_live())
        await asyncio.sleep(0)
        blocked.set()
        assert (await later)["outcome"] == "complete"
        assert calls == 1
        assert cache._refresh.generation == 1
        assert cache.get_all()["local"][0].id == "w1"
        assert cache._refresh.metadata()["last_refresh"]["goal_met"] is False


@pytest.mark.guard
@pytest.mark.asyncio
async def test_provider_swap_before_scheduled_holder_starts_warms_replacement(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("old")
    calls = []

    async def read(name, cfg, res, *, epoch=None, classify=True, fresh=False):
        calls.append(name)
        return [entry(name, name)]

    monkeypatch.setattr(cache, "_crawl_agent", read)
    cache.render_snapshot()
    holder = cache._refresh.task
    cache._resolver = resolver("new")
    cache.render_snapshot()
    queued = cache._refresh._live_task
    assert cache._refresh.task is holder
    assert queued is not None
    await queued
    assert "old" not in calls
    assert calls[0] == "new"
    assert list(cache.get_all()) == ["new"]
    await cache.stop()


@pytest.mark.asyncio
async def test_provider_replacement_and_bounded_shutdown_fence_publication(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def read(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [entry()]

    monkeypatch.setattr(cache, "_crawl_agent", read)
    first = asyncio.create_task(cache.crawl(cache._resolver))
    await started.wait()
    holder = cache._refresh.task
    cache._resolver = resolver("replacement")
    cache.render_snapshot()
    assert cache._refresh.task is holder
    assert calls == 1
    before = time.monotonic()
    await cache.stop()
    assert time.monotonic() - before < 0.5
    assert cache._refresh.pending
    assert cache._refresh.task is holder
    release.set()
    await first
    assert cache.get_all() == {}
    assert cache._refresh.task is None
    assert cache._refresh.metadata()["freshness"] == "unavailable"


@pytest.mark.guard
@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_single_replacement_render_warms_after_retired_holder_without_overlap(monkeypatch, live):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("old")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    active = 0

    async def read(name, cfg, res, *, epoch=None, classify=True, fresh=False):
        nonlocal active
        active += 1
        assert active == 1
        calls.append((name, fresh))
        if name == "old":
            started.set()
            await release.wait()
        active -= 1
        return [entry(name, name)]

    async def no_backfill(*args):
        pass

    monkeypatch.setattr(cache, "_crawl_agent", read)
    monkeypatch.setattr(cache, "_backfill_classify", no_backfill)
    old = asyncio.create_task(cache.refresh_live()) if live else None
    if not live:
        cache.render_snapshot()
    await started.wait()
    holder = cache._refresh.task
    cache._resolver = resolver("new")
    async with route_client(cache, monkeypatch) as client:
        response = await client.get("/api/v1/worktrees?render=true")
    assert response.json()["discovery"]["warming"]
    queued = cache._refresh._live_task
    assert queued is not None
    for _ in range(8):
        cache.render_snapshot()
        assert cache._refresh._live_task is queued
    assert cache._refresh.task is holder
    assert calls == [("old", live)]
    release.set()
    await queued
    if old is not None:
        assert (await old)["outcome"] == "superseded"
    assert calls == [("old", live), ("new", False)]
    assert cache.get_all()["new"][0].id == "new"
    assert cache._refresh.generation == 1
    await cache.stop()


@pytest.mark.guard
@pytest.mark.asyncio
@pytest.mark.parametrize("close", [False, True])
async def test_repeated_replacement_retains_queued_chain_until_actual_completion(close):
    owner = refresh.WorktreeRefreshOwner()
    owner.bind(("old",), ["old"], available=True)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def read(epoch):
        calls.append(epoch)
        if len(calls) == 1:
            started.set()
            await release.wait()
        owner.published(epoch, {name: None for name in owner.sources})

    holder = owner.ensure(read)
    await started.wait()
    owner.bind(("middle",), ["middle"], available=True)
    middle = owner.ensure(read, live=True)
    owner.bind(("new",), ["new"], available=True)
    assert owner.needs_refresh()
    newest = owner.ensure(read)
    assert newest is not middle
    assert owner.ensure(read) is newest
    assert owner.task is holder
    waiter = asyncio.create_task(owner.wait(newest))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not newest.cancelled()
    if close:
        await owner.close()
        assert owner.pending
    release.set()
    await newest
    assert middle.done() and holder.done()
    assert calls == ([1] if close else [1, 3])
    assert owner.generation == (0 if close else 1)
    assert not owner.pending


@pytest.mark.guard
@pytest.mark.asyncio
async def test_source_routing_change_fences_the_existing_holder(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    started = asyncio.Event()
    release = asyncio.Event()

    async def source(*args, **kwargs):
        started.set()
        await release.wait()
        return [entry()]

    monkeypatch.setattr(cache, "_crawl_agent", source)
    cache.render_snapshot()
    await started.wait()
    epoch = cache._refresh.epoch
    holder = cache._refresh.task
    cache._resolver.agents["local"].ssh_environment = "alternate"
    cache.render_snapshot()
    queued = cache._refresh._live_task
    assert cache._refresh.epoch > epoch
    assert cache._refresh.task is holder
    release.set()
    await queued
    assert cache.get_all()["local"][0].id == "w1"
    await cache.stop()


@pytest.mark.asyncio
async def test_legacy_cold_route_still_blocks_and_fast_refresh_meets_goal(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    started = asyncio.Event()
    release = asyncio.Event()

    async def read(*args, **kwargs):
        started.set()
        await release.wait()
        return []

    monkeypatch.setattr(cache, "_crawl_agent", read)
    async with route_client(cache, monkeypatch) as client:
        request = asyncio.create_task(client.get("/api/v1/worktrees"))
        await started.wait()
        await asyncio.sleep(0.03)
        assert not request.done()
        release.set()
        assert (await request).json()["groups"] == {"local": []}
        response = await client.get("/api/v1/worktrees?refresh=true")
        assert response.json()["refresh"]["outcome"] == "complete"
        assert response.json()["discovery"]["last_refresh"]["goal_met"] is True
        await cache.stop()


@pytest.mark.asyncio
async def test_unavailable_stale_and_empty_sources_are_distinct(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    assert cache.render_snapshot()["freshness"] == "unavailable"
    cache._resolver = resolver()
    await cache.crawl(cache._resolver)
    assert cache._refresh.metadata()["completeness"] == "complete"
    cache._resolver = resolver("local")

    async def read(*args, **kwargs):
        return [entry()]

    monkeypatch.setattr(cache, "_crawl_agent", read)
    await cache.crawl(cache._resolver)
    cache._refresh.sources["local"].updated -= 40
    assert cache._refresh.metadata()["freshness"] == "stale"


@pytest.mark.guard
@pytest.mark.parametrize("raw,diagnostic", [
    ('{"version":9,"worktrees":[]}', "source-incompatible"),
    ('{"version":1,"worktrees":[],"incomplete":true}', "source-incomplete"),
    ('{"version":true,"worktrees":[]}', "source-malformed"),
    ('{"version":1,"worktrees":[],"error":"unavailable"}', "source-incomplete"),
    ('{"version":1,"worktrees":[],"complete":null}', "source-incomplete"),
    ('{"version":1,"worktrees":[1]}', "source-malformed"),
    ('{"version":1,"worktrees":[{}]}', "source-malformed"),
    ('{}', "source-malformed"),
    ('null', "source-malformed"),
])
def test_invalid_inventory_is_not_confirmed_empty(raw, diagnostic):
    with pytest.raises(refresh.DiscoveryReadError, match=diagnostic):
        refresh.validate_inventory(raw)


@pytest.mark.guard
def test_unsupported_option_detection_does_not_confuse_usage_with_rejection():
    stderr = "usage: sample list [--fresh]\nerror: unrecognized arguments: --classify"
    assert refresh.is_classify_unsupported(stderr)
    assert not refresh.is_option_unsupported(stderr, "--fresh")
    stderr = "usage: sample list [--classify]\nerror: unrecognized arguments: --fresh"
    assert refresh.is_option_unsupported(stderr, "--fresh")
    assert not refresh.is_classify_unsupported(stderr)


@pytest.mark.asyncio
async def test_native_source_budgets_and_optional_classify_fallback_unchanged(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    source = resolver("local")
    calls = []

    async def run(project, args, *, timeout=None):
        calls.append((args, timeout))
        if "--classify" in args:
            return None, "unrecognized arguments: --classify"
        return '{"version":1,"worktrees":[]}', ""

    monkeypatch.setattr(wt, "_run_local_ex", run)
    rows = await cache._crawl_agent("local", source.agents["local"], source)
    assert rows == []
    assert calls[0][1] == 60
    assert calls[1][1] is None
    assert wt._CMD_TIMEOUT == 30
    assert wt._CLASSIFY_CMD_TIMEOUT == 60
    assert "local" in cache._classify_unsupported


@pytest.mark.guard
@pytest.mark.parametrize("response", [
    None, {}, {"machines": [{"name": "example"}], "topology_errors": ["invalid"]},
])
def test_machine_inventory_facade_preserves_rows_and_diagnostics(response: Any) -> None:
    client = BridgeClient(base_url="http://test", token="example")
    client._request = MagicMock(return_value=response)
    rows = response.get("machines", []) if response else []
    errors = response.get("topology_errors", []) if response else []
    assert client.list_machines_with_diagnostics() == (rows, errors)
    assert client.list_machines() == rows
    assert client._request.call_args.args == ("GET", "/api/v1/machines")


@pytest.mark.guard
@pytest.mark.parametrize("version", [0, 35, 36, 37])
@pytest.mark.parametrize("live", [False, True])
def test_client_version_gate_and_unknown_legacy_metadata(version, live):
    client = BridgeClient(base_url="http://test", token="example")
    client._daemon_proto = (version, 1)
    request = MagicMock(return_value={
        "groups": {"local": []},
        **({"discovery": {"version": 1, "completeness": "complete"}} if version >= 37 else {}),
    })
    client._request = request
    result = client.refresh_worktrees() if live else client.list_worktrees_for_render()
    assert request.call_args.kwargs["params"] == (
        {"refresh" if live else "render": "true"} if version >= 37 else None
    )
    if version < 37:
        assert result["discovery"]["freshness"] == "unknown"
        assert result["discovery"]["completeness"] == "unknown"
        assert result["discovery"]["render_latency_contract"] is False
        if live:
            assert result["refresh"]["outcome"] == "unsupported"


@pytest.mark.guard
@pytest.mark.parametrize("metadata", [
    None, {}, {"version": 2, "completeness": "complete"},
    {"version": True, "completeness": "complete"},
])
def test_client_unknown_metadata_generation_is_not_current_evidence(metadata):
    client = BridgeClient(base_url="http://test", token="example")
    client._daemon_proto = (37, 1)
    client._request = MagicMock(return_value={"groups": {}, "discovery": metadata})
    result = client.list_worktrees_for_render()
    assert result["discovery"]["freshness"] == "unknown"
    assert result["discovery"]["completeness"] == "unknown"


@pytest.mark.asyncio
async def test_explicit_live_refresh_bypasses_manager_result_cache(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    calls = []

    async def source(project, args, *, timeout=None):
        calls.append(args)
        ident = "live" if "--fresh" in args else "cached"
        return json.dumps({"version": 1, "worktrees": [{"id": ident}]}), ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    await cache.crawl(cache._resolver)
    assert cache.get_all()["local"][0].id == "cached"
    await cache.refresh_live()
    assert cache.get_all()["local"][0].id == "live"
    assert "--fresh" not in calls[0]
    assert "--fresh" in calls[1]


@pytest.mark.asyncio
async def test_live_refresh_queues_once_behind_cached_holder_without_overlap(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    active = 0

    async def source(name, cfg, res, *, epoch=None, classify=True, fresh=False):
        nonlocal active
        active += 1
        assert active == 1
        calls.append(fresh)
        if not fresh:
            started.set()
            await release.wait()
        active -= 1
        return [entry(ident="live" if fresh else "cached")]

    monkeypatch.setattr(cache, "_crawl_agent", source)
    cache.render_snapshot()
    await started.wait()
    holder = cache._refresh.task
    callers = [asyncio.create_task(cache.refresh_live()) for _ in range(8)]
    await asyncio.sleep(0)
    assert cache._refresh.task is holder
    assert calls == [False]
    assert cache._refresh.metadata()["last_refresh"]["goal_met"] is None
    abandoned = callers.pop()
    abandoned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await abandoned
    release.set()
    results = await asyncio.gather(*callers)
    assert all(result["outcome"] == "complete" for result in results)
    assert calls == [False, True]
    assert cache.get_all()["local"][0].id == "live"
    assert cache._refresh.generation == 2


@pytest.mark.asyncio
async def test_old_manager_cannot_claim_a_live_refresh_and_remains_renderable(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    calls = 0

    async def source(project, args, *, timeout=None):
        nonlocal calls
        calls += 1
        if "--fresh" in args:
            return None, "unrecognized arguments: --fresh"
        return '{"version":1,"worktrees":[{"id":"cached"}]}', ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    await cache.crawl(cache._resolver)
    assert (await cache.refresh_live())["outcome"] == "partial"
    metadata = cache._refresh.metadata()
    assert metadata["sources"]["local"]["diagnostic"] == "source-refresh-unsupported"
    assert metadata["sources"]["local"]["freshness"] == "stale"
    assert cache.get_all()["local"][0].id == "cached"
    before = calls
    await cache.refresh_live()
    assert calls == before
    assert cache.get_all()["local"][0].id == "cached"


@pytest.mark.asyncio
async def test_shutdown_retains_cache_holder_and_queued_live_wait_until_completion(monkeypatch):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def source(name, cfg, res, *, epoch=None, classify=True, fresh=False):
        calls.append(fresh)
        started.set()
        await release.wait()
        return [entry()]

    monkeypatch.setattr(cache, "_crawl_agent", source)
    cache.render_snapshot()
    await started.wait()
    holder = cache._refresh.task
    live = asyncio.create_task(cache.refresh_live())
    await asyncio.sleep(0)
    queued = cache._refresh._live_task
    await cache.stop()
    assert cache._refresh.task is holder
    assert cache._refresh._live_task is queued
    assert cache._refresh.pending
    release.set()
    assert (await live)["outcome"] == "superseded"
    assert calls == [False]
    assert cache.get_all() == {}
    assert not cache._refresh.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", ["--fresh", "--classify"])
async def test_retired_source_option_verdict_cannot_poison_replacement(monkeypatch, rejected):
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    cache._resolver.agents["local"].project = "old"
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def source(project, args, *, timeout=None):
        calls.append(project)
        if project == "old" and len(calls) == 1:
            started.set()
            await release.wait()
            return None, f"usage: sample [--fresh]\nerror: unrecognized arguments: {rejected}"
        return json.dumps({"worktrees": [{"id": project}]}), ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    old = asyncio.create_task(cache.refresh_live())
    await started.wait()
    cache._resolver.agents["local"].project = "new"
    release.set()
    assert (await old)["outcome"] == "superseded"
    assert "local" not in cache._fresh_unsupported
    assert "local" not in cache._classify_unsupported
    assert calls == (["old", "old"] if rejected == "--classify" else ["old"])
    assert (await cache.refresh_live())["outcome"] == "complete"
    assert cache.get_all()["local"][0].id == "new"


@pytest.mark.guard
@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", ["--fresh", "--classify"])
async def test_resolver_swap_before_child_start_preserves_holder_epoch(
    monkeypatch: pytest.MonkeyPatch, rejected: str,
) -> None:
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = resolver("local")
    cache._resolver.agents["local"].project = "old"
    replacement = resolver("local")
    replacement.agents["local"].project = "new"
    original = cache._crawl_agent
    calls: list[str] = []

    async def source(
        project: str, args: list[str], *, timeout: float | None = None,
    ) -> tuple[str | None, str]:
        calls.append(project)
        if project == "old" and len(calls) == 1:
            return None, f"unrecognized arguments: {rejected}"
        return json.dumps({"worktrees": [{"id": project}]}), ""

    def swap_before_start(
        *args: Any, **kwargs: Any,
    ) -> Coroutine[Any, Any, list[wt._WorktreeEntry]]:
        read = original(*args, **kwargs)
        cache._resolver = replacement
        cache._sync_sources(replacement)
        return read

    monkeypatch.setattr(wt, "_run_local_ex", source)
    monkeypatch.setattr(cache, "_crawl_agent", swap_before_start)
    assert (await cache.refresh_live())["outcome"] == "superseded"
    assert "local" not in cache._fresh_unsupported
    assert "local" not in cache._classify_unsupported
    assert calls == (["old", "old"] if rejected == "--classify" else ["old"])
    assert (await cache.refresh_live())["outcome"] == "complete"
    assert cache.get_all()["local"][0].id == "new"
    await cache.stop()
