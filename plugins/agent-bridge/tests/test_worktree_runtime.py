"""Captured worktree caches remain exact, isolated and discovery-free."""

from __future__ import annotations

import asyncio
import copy
import threading

import pytest
from fastapi import FastAPI, Request
from zdd.private_protocol import Deadline, PrivateIPCError

from agent_bridge.app import create_app
from agent_bridge.effective_runtime import ServingRuntimeCapture
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.routes import worktree_probe, worktrees
from agent_bridge.runtime_restore import restore_runtime
from agent_bridge.runtime_transfer import _self, receive_runtime
from agent_bridge.worktree_runtime import capture_worktrees, restore_worktrees
from test_roster_runtime import warm_roster
from test_runtime_restore import captured as captured
from test_runtime_transfer import MemoryChannel, request


def cache_graph(app):
    resolver = app.state.resolver
    resolver.agents["actual"].project = "synthetic-project"
    governance = LoopGovernance(helper=lambda checkpoint: {"status": "ready"})
    cache = worktrees.WorktreeDiscoveryCache()
    cache.start(resolver, governance=governance)
    cache._cache = {"actual": [
        worktrees._WorktreeEntry(
            "venue-1", "actual", "synthetic", "/synthetic/venue", "dev", "active",
            title="retained title", turn_count=7, follow_up=True,
            closure={"schema": 1, "opaque": ["retained"]},
        ),
    ]}
    cache._classify_unsupported.add("actual")
    app.state.worktree_discovery_cache = cache
    app.state.loop_governance = governance
    return cache, resolver, governance


def full_capture(captured):
    app, origins, _capture = captured
    warm_roster(app)
    cache_graph(app)
    return ServingRuntimeCapture(
        app, origins, include_resolver=True, include_relay=True, include_roster=True,
        include_sessions=True, include_worktrees=True, session_deadline=Deadline(10),
    )


@pytest.mark.asyncio
async def test_completed_cache_roundtrip_preserves_entries_and_refuses_discovery(captured, monkeypatch):
    app, _origins, _capture = captured
    cache, resolver, governance = cache_graph(app)
    payload = capture_worktrees(cache, resolver, governance)

    async def forbidden(*args, **kwargs):
        pytest.fail("captured cache performed discovery")

    monkeypatch.setattr(worktrees, "_run_local_ex", forbidden)
    monkeypatch.setattr(worktrees, "_probe_live_worktree", forbidden)
    monkeypatch.setattr(worktrees, "_probe_archived_owner", forbidden)
    restored = restore_worktrees(payload, resolver)
    assert restored is not cache and restored._resolver is None and restored._task is None
    assert restored.private_payload() == payload
    await restored.crawl_if_empty()
    returned = restored.get_all()
    assert returned["actual"][0].turn_count == 7
    assert returned["actual"][0].closure == {"schema": 1, "opaque": ["retained"]}
    returned["actual"][0].closure["opaque"].clear()
    assert restored.get_all()["actual"][0].closure["opaque"] == ["retained"]
    for action in (
        restored.probe_live("venue-1", resolver),
        restored.probe_archived("venue-1", resolver), restored.crawl(resolver),
    ):
        with pytest.raises(PrivateIPCError):
            await action
    with pytest.raises(PrivateIPCError):
        restored.start(resolver, governance=governance)
    with pytest.raises(PrivateIPCError):
        restored.configure(interval=1)
    await restored.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "resolver", "governance", "thread", "loop", "crawl", "backfill",
    "archive", "live", "fresh-start", "periodic-missing", "entries-limit",
    "cancel-requested",
])
async def test_capture_refuses_unowned_or_inflight_cache(captured, monkeypatch, mutation):
    app, _origins, _capture = captured
    cache, resolver, governance = cache_graph(app)
    pending = None
    if mutation in ("backfill", "archive", "live"):
        pending = asyncio.create_task(asyncio.Event().wait())
        if mutation == "backfill":
            cache._backfill_tasks.add(pending)
        elif mutation == "archive":
            cache._archive_probe_inflight["venue-1"] = pending
        else:
            cache._live_probe_inflight["venue-1"] = pending
    elif mutation == "resolver":
        cache._resolver = object()
    elif mutation == "governance":
        cache._governance = object()
    elif mutation == "thread":
        cache._owner_thread = threading.Thread()
    elif mutation == "loop":
        cache._owner_loop = None
    elif mutation == "crawl":
        pending = asyncio.create_task(asyncio.Event().wait())
        cache._refresh.task = pending
    elif mutation == "fresh-start":
        lock = asyncio.Lock()
        await lock.acquire()
        monkeypatch.setitem(worktrees._fresh_start_locks, "venue-1", lock)
    elif mutation == "periodic-missing":
        cache._interval = 5
    elif mutation == "cancel-requested":
        # Python 3.10 has no Task.cancelling(); capture_worktrees() must
        # still refuse a task whose cancellation was requested but that
        # hasn't finished yet, using our own _cancel_requested flag.
        cache._interval = 5
        cache._task = asyncio.create_task(asyncio.Event().wait())
        cache._owner_loop = asyncio.get_running_loop()
        cache._cancel_requested = True
        pending = cache._task
    else:
        cache._cache["actual"] *= 4097
    try:
        with pytest.raises(PrivateIPCError):
            capture_worktrees(cache, resolver, governance)
    finally:
        if pending is not None:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["unknown", "boolean", "interval", "agent", "entry", "extra", "duplicate"])
async def test_restore_rejects_incomplete_or_coerced_observations(captured, mutation):
    app, _origins, _capture = captured
    cache, resolver, governance = cache_graph(app)
    payload = capture_worktrees(cache, resolver, governance)
    if mutation == "unknown":
        payload["unknown"] = "private-value"
    elif mutation == "boolean":
        payload["schema"] = True
    elif mutation == "interval":
        payload["interval"] = -1
    elif mutation == "agent":
        payload["entries"]["missing"] = payload["entries"].pop("actual")
    elif mutation == "entry":
        payload["entries"]["actual"][0]["turn_count"] = "7"
    elif mutation == "extra":
        payload["entries"]["actual"][0]["extra"] = "private-value"
    else:
        payload["entries"]["actual"] *= 2
    with pytest.raises(PrivateIPCError) as caught:
        restore_worktrees(payload, resolver)
    assert "private-value" not in str(caught.value)


@pytest.mark.asyncio
async def test_capture_succeeds_after_restarting_in_on_demand_mode(captured):
    # Regression: stopping a periodic cache, reconfiguring to on-demand
    # (interval=0) and restarting must not leave the prior completed
    # task or its cancel-requested flag in place -- capture_worktrees()
    # refuses any retained completed task regardless of that flag, so
    # on-demand capture would otherwise stay permanently unavailable.
    app, _origins, _capture = captured
    resolver = app.state.resolver
    governance = LoopGovernance(helper=lambda checkpoint: {"status": "ready"})
    cache = worktrees.WorktreeDiscoveryCache(interval=5)
    cache.start(resolver, governance=governance)
    await cache.stop()
    assert cache._task is not None and cache._task.done()
    cache.configure(interval=0)
    cache.start(resolver, governance=governance)
    assert cache._task is None and cache._cancel_requested is False
    payload = capture_worktrees(cache, resolver, governance)
    assert payload["entries"] == {}


@pytest.mark.asyncio
async def test_capture_succeeds_after_stop_start_restart_clears_cancel_flag(captured):
    # Regression: start() must clear _cancel_requested when it creates a
    # replacement periodic task. Otherwise an ordinary in-process
    # restart (stop() then start() again) permanently leaves
    # capture_worktrees() refusing an otherwise perfectly healthy
    # restarted task.
    app, _origins, _capture = captured
    resolver = app.state.resolver
    governance = LoopGovernance(helper=lambda checkpoint: {"status": "ready"})
    cache = worktrees.WorktreeDiscoveryCache(interval=5)
    cache.start(resolver, governance=governance)
    assert cache._task is not None and cache._cancel_requested is False
    await cache.stop()
    assert cache._cancel_requested is True
    cache.start(resolver, governance=governance)
    assert cache._task is not None and cache._cancel_requested is False
    try:
        payload = capture_worktrees(cache, resolver, governance)
        assert payload["entries"] == {}
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_capture_succeeds_with_healthy_periodic_discovery_task(captured):
    # Regression (Python 3.10 compatibility): a healthy, uncancelled
    # periodic-discovery task must not trip capture_worktrees()'s
    # cancellation-in-progress check, which can no longer rely on the
    # 3.11-only Task.cancelling().
    app, _origins, _capture = captured
    cache, resolver, governance = cache_graph(app)
    cache._interval = 5
    cache._task = asyncio.create_task(asyncio.Event().wait())
    cache._owner_loop = asyncio.get_running_loop()
    assert cache._cancel_requested is False
    try:
        payload = capture_worktrees(cache, resolver, governance)
        assert payload["entries"]["actual"][0]["id"] == "venue-1"
    finally:
        cache._task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cache._task


@pytest.mark.asyncio
async def test_capture_binding_and_pending_app_retain_one_exact_cache(captured, monkeypatch):
    source, origins, _capture = captured
    capture = full_capture(captured)
    payload = capture.capture().private_payload()
    restored = restore_runtime(payload)
    assert restored.worktrees.private_payload() == payload["runtime"]["worktrees"]
    assert capture.matches(capture.capture())
    source.state.worktree_discovery_cache._cache["actual"][0].title = "changed"
    assert not capture.matches(capture._last)
    source.state.worktree_discovery_cache._cache["actual"][0].title = "retained title"
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(origins.config_root))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(origins.install_root))
    channel = MemoryChannel(_self())
    request(capture, channel, "c" * 64)
    pending = receive_runtime(channel, channel.peer, namespace="c" * 64, deadline=Deadline(10))
    app = create_app(restored_runtime=pending.runtime)
    async with app.router.lifespan_context(app):
        pending.acknowledge(app)
        assert worktrees.cache_for_app(app) is pending.runtime.worktrees
        pending.verify_resources(app)
        app.state.worktree_discovery_cache = worktrees.WorktreeDiscoveryCache()
        with pytest.raises(PrivateIPCError):
            pending.verify_resources(app)
        app.state.worktree_discovery_cache = pending.runtime.worktrees
        pending.runtime.worktrees._cache["actual"][0].turn_count += 1
        with pytest.raises(PrivateIPCError):
            pending.verify_resources(app)
        assert app.state.ready is False


@pytest.mark.asyncio
async def test_request_owners_use_app_cache_not_global_or_another_app(captured, monkeypatch):
    source, _origins, _capture = captured
    cache, resolver, governance = cache_graph(source)
    frozen = restore_worktrees(capture_worktrees(cache, resolver, governance), resolver)
    app = FastAPI()
    app.state.resolver = resolver
    app.state.worktree_discovery_cache = frozen
    request_obj = Request({"type": "http", "app": app})

    def forbidden():
        pytest.fail("request escaped to the process-global cache")

    monkeypatch.setattr(worktrees, "get_cache", forbidden)
    rows = await worktrees.list_worktrees(request_obj, render=False, refresh=False)
    assert rows["groups"]["actual"][0]["id"] == "venue-1"
    assert await worktree_probe.owning_agent("venue-1", request_obj) == (
        "actual", resolver.agents["actual"],
    )
    owner, entry = await worktree_probe.find_worktree_entry("venue-1", resolver, cache=frozen)
    assert owner == "actual" and entry.id == "venue-1"
    app.state.worktree_discovery_cache = None
    with pytest.raises(RuntimeError, match="ownership is invalid"):
        worktrees.cache_for_app(app)


@pytest.mark.asyncio
async def test_empty_captured_cache_is_not_permission_to_discover(captured):
    app, _origins, _capture = captured
    cache, resolver, governance = cache_graph(app)
    cache._cache.clear()
    frozen = restore_worktrees(capture_worktrees(cache, resolver, governance), resolver)
    await frozen.crawl_if_empty()
    assert frozen.get_all() == {} and frozen._task is None
    changed = copy.deepcopy(frozen.private_payload())
    changed["classify_unsupported"].append("actual")
    with pytest.raises(PrivateIPCError):
        restore_worktrees(changed, resolver)


@pytest.mark.asyncio
async def test_refresh_evidence_keeps_absolute_age_without_granting_refresh(captured):
    app, _origins, _capture = captured
    cache, resolver, governance = cache_graph(app)
    owner = cache._refresh
    owner.published(owner.epoch, {"actual": None}, observed={"actual": 123.0})
    owner.last_attempt, owner.last_duration, owner.last_outcome = 120.0, 3.0, "complete"
    cache._fresh_unsupported.add("actual")
    frozen = restore_worktrees(capture_worktrees(cache, resolver, governance), resolver)
    assert frozen._refresh.sources["actual"].updated == 123.0
    assert frozen.render_snapshot()["sources"]["actual"]["freshness"] == "stale"
    assert frozen._fresh_unsupported == {"actual"}
    assert frozen._refresh.task is None and frozen._refresh.token is None
    with pytest.raises(PrivateIPCError):
        await frozen.refresh_live()
    with pytest.raises(PrivateIPCError):
        frozen._refresh.ensure(lambda epoch: None)
    frozen._refresh.sources["actual"].updated += 1
    with pytest.raises(PrivateIPCError):
        frozen.private_payload()


@pytest.mark.asyncio
async def test_capture_after_real_warm_and_backfill_completion(captured, monkeypatch):
    app, _origins, _capture = captured
    cache, resolver, governance = cache_graph(app)
    release, entered = asyncio.Event(), asyncio.Event()

    async def crawl(_resolver, *, classify, epoch):
        cache._refresh.published(epoch, {"actual": None}, observed={"actual": 123.0})

    async def backfill(_resolver):
        entered.set()
        await release.wait()

    monkeypatch.setattr(cache, "_do_crawl", crawl)
    monkeypatch.setattr(cache, "_backfill_classify", backfill)
    holder = cache._ensure_warm()
    with pytest.raises(PrivateIPCError):
        capture_worktrees(cache, resolver, governance)
    await holder
    await entered.wait()
    assert holder.done() and not cache._refresh._warm_callbacks
    with pytest.raises(PrivateIPCError):
        capture_worktrees(cache, resolver, governance)
    release.set()
    await asyncio.gather(*cache._backfill_tasks)
    await asyncio.sleep(0)
    snapshot = capture_worktrees(cache, resolver, governance)
    assert snapshot["refresh"]["sources"]["actual"]["updated"] == 123.0
    assert restore_worktrees(snapshot, resolver).private_payload() == snapshot
