"""Source projection provenance must survive Bridge cache publication."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil._interpreter import physical_executable

from agent_bridge.agent_registry import AgentConfig
from agent_bridge.routes import worktree_refresh as refresh
from agent_bridge.routes import worktrees as wt

pytestmark = [pytest.mark.guard, pytest.mark.asyncio]


def envelope(**extra: object) -> dict:
    return {
        "ok": True,
        "contract_version": 1,
        "projection": {"quality": "current", "observed_at": time.time() - 12},
        "worktrees": [{"id": "good"}],
        **extra,
    }


def reporter(name: str = "current", environment: str = "wsl") -> dict:
    return {
        "version": 1, "status": "resolved", "hostname": "reporter-host",
        "machine": name, "environment": environment, "execution_machine": name + "-slot",
    }


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.asyncio
async def test_reporter_naming_overrides_stored_names_without_retargeting_agent(
    environment: str,
) -> None:
    body = envelope(
        reporting_context=reporter(environment=environment),
        worktrees=[{"id": "legacy-worktree", "machine": "old-host"}],
    )
    row = wt._parse_worktree_list(json.dumps(body), "configured-agent")[0]
    assert row.machine == "current-slot"
    assert row.agent_name == "configured-agent"
    assert row.id == "legacy-worktree"
    assert body["worktrees"][0]["machine"] == "old-host"
    body["reporting_context"] = {"version": 1, "status": "unavailable"}
    row = wt._parse_worktree_list(json.dumps(body), "configured-agent")[0]
    assert row.machine == "" and row.agent_name == "configured-agent"
    del body["reporting_context"]
    assert wt._parse_worktree_list(json.dumps(body), "configured-agent")[0].machine == "old-host"
    del body["worktrees"][0]["machine"]
    assert wt._parse_worktree_list(json.dumps(body), "configured-agent")[0].machine == "configured-agent"


@pytest.mark.parametrize("context", [
    None, [], {}, {"version": True, "status": "unavailable"},
    {"version": 2, "status": "unavailable"},
    {"version": 1, "status": "unavailable", "machine": "retained"},
    {**reporter(), "environment": []}, {**reporter(), "environment": "future"},
    {**reporter(), "hostname": "x\nbad"}, {**reporter(), "machine": ""},
    {**reporter(), "execution_machine": "x" * 257},
])
@pytest.mark.asyncio
async def test_invalid_reporting_context_does_not_replace_last_good_inventory(
    monkeypatch: pytest.MonkeyPatch, context: object,
) -> None:
    cache = wt.WorktreeDiscoveryCache()
    resolver = SimpleNamespace(agents={"route": AgentConfig(name="route", project="sample")})
    body = envelope(reporting_context=reporter())

    async def source(*args, **kwargs) -> tuple[str, str]:
        return json.dumps(body), ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    try:
        await cache.crawl(resolver)
        before = cache._refresh.metadata()["sources"]["route"]
        body = envelope(reporting_context=context, worktrees=[{"id": "replacement"}])
        await cache.crawl(resolver)
        assert cache.get_all()["route"][0].machine == "current-slot"
        assert cache.get_all()["route"][0].id == "good"
        after = cache._refresh.metadata()["sources"]["route"]
        assert after["diagnostic"] == "source-reporting-malformed"
        assert after["generation"] == before["generation"]
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_real_source_stdout_refresh_renames_and_withdraws_without_row_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = wt.WorktreeDiscoveryCache()
    resolver = SimpleNamespace(agents={"route": AgentConfig(name="route", project="sample")})
    body = envelope(reporting_context=reporter("first"), worktrees=[{
        "id": "legacy-worktree", "machine": "old-host", "path": "unchanged-checkout",
    }])
    original_rows = json.dumps(body["worktrees"], sort_keys=True)
    calls = []
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None

    async def source(project: str, args: list[str], **kwargs) -> tuple[str | None, str]:
        calls.append((project, args))
        script = Path(__file__).parent / "fixtures" / "worktree_list_source.py"
        return await wt._exec_ex([interpreter, str(script), json.dumps(body)])

    monkeypatch.setattr(wt, "_run_local_ex", source)
    try:
        for context, expected in (
            (reporter("first"), "first-slot"), (reporter("renamed"), "renamed-slot"),
            ({"version": 1, "status": "unavailable"}, ""),
        ):
            body["reporting_context"] = context
            await cache.crawl(resolver)
            row = cache.get_all()["route"][0]
            assert row.machine == expected
            assert row.agent_name == "route" and row.id == "legacy-worktree"
            assert row.path == "unchanged-checkout"
            assert json.dumps(body["worktrees"], sort_keys=True) == original_rows
        assert len(calls) == 3
    finally:
        await cache.stop()


@pytest.mark.parametrize("degraded", [
    {"availability": {"status": "advisory", "reason": "resident unavailable"}},
    {"availability": {"status": "stale", "reason": "publication failed"}},
    {"projection": {"quality": "advisory", "observed_at": time.time()}},
    {"projection": {"quality": "current", "observed_at": 1}},
    {"projection": {"quality": "current", "observed_at": True}},
])
async def test_degraded_empty_projection_preserves_last_good_rows(
    monkeypatch: pytest.MonkeyPatch, degraded: dict,
) -> None:
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = SimpleNamespace(agents={
        "local": AgentConfig(name="local", project="sample"),
    })
    body = envelope()

    async def source(*args, **kwargs) -> tuple[str, str]:
        return json.dumps(body), ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    await cache.crawl(cache._resolver)
    before = cache._refresh.metadata()["sources"]["local"]
    assert 11 <= before["age_seconds"] < 15
    body = envelope(worktrees=[], **degraded)
    await cache.crawl(cache._resolver)
    after = cache._refresh.metadata()["sources"]["local"]
    assert cache.get_all()["local"][0].id == "good"
    assert after["generation"] == before["generation"]
    assert after["age_seconds"] >= before["age_seconds"]
    assert after["freshness"] == "stale"
    assert after["completeness"] == "incomplete"
    await cache.stop()


async def test_current_roster_read_is_not_acknowledged_live_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = SimpleNamespace(agents={
        "local": AgentConfig(name="local", project="sample"),
    })

    async def source(*args, **kwargs) -> tuple[str, str]:
        return json.dumps(envelope()), ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    await cache.crawl(cache._resolver)
    generation = cache._refresh.generation
    result = await cache.refresh_live()
    assert result["outcome"] == "partial"
    evidence = cache._refresh.metadata()
    assert evidence["sources"]["local"]["diagnostic"] == "source-refresh-unsupported"
    assert evidence["sources"]["local"]["generation"] == generation
    assert evidence["last_refresh"]["goal_met"] is False
    assert cache.get_all()["local"][0].id == "good"
    await cache.stop()


async def test_projection_age_is_not_renewed_by_repeated_reads() -> None:
    raw = json.dumps(envelope())
    first = refresh.validate_inventory(raw)
    second = refresh.validate_inventory(raw)
    assert first is not None and second is not None
    assert abs(first - second) < 0.1


async def test_python310_async_timeout_preserves_pending_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LegacyAsyncTimeout(Exception):
        pass

    owner = refresh.WorktreeRefreshOwner()
    owner.bind("source", ["local"], available=True)
    release = asyncio.Event()

    async def read(epoch: int) -> None:
        await release.wait()

    async def expire(*args, **kwargs) -> None:
        raise LegacyAsyncTimeout

    task = owner.ensure(read)
    monkeypatch.setattr(asyncio, "TimeoutError", LegacyAsyncTimeout)
    monkeypatch.setattr(asyncio, "wait_for", expire)
    try:
        assert (await owner.wait(task))["outcome"] == "timed-out"
        assert owner.pending
        assert owner.ensure(read) is task
    finally:
        release.set()
        await task


@pytest.mark.parametrize("version", [None, True, "1", 2, 1])
@pytest.mark.parametrize("fresh", [False, True])
async def test_declared_roster_contract_requires_valid_version_and_projection(
    version: object, fresh: bool,
) -> None:
    raw = json.dumps({"ok": True, "contract_version": version, "worktrees": []})
    with pytest.raises(refresh.DiscoveryReadError):
        refresh.validate_inventory(raw, fresh=fresh)


async def test_fast_legacy_source_ages_while_a_peer_remains_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(refresh, "time", SimpleNamespace(
        monotonic=lambda: clock.now, time=time.time,
    ))
    cache = wt.WorktreeDiscoveryCache()
    cache._resolver = SimpleNamespace(agents={
        name: AgentConfig(name=name, project=name) for name in ("fast", "slow")
    })
    fast_read = asyncio.Event()

    async def source(project: str, *args, **kwargs) -> tuple[str, str]:
        if project == "fast":
            fast_read.set()
        else:
            await fast_read.wait()
            clock.now += 45
        return json.dumps({"worktrees": [{"id": project}]}), ""

    monkeypatch.setattr(wt, "_run_local_ex", source)
    await cache.crawl(cache._resolver)
    evidence = cache._refresh.metadata()["sources"]
    assert evidence["fast"]["age_seconds"] == 45
    assert evidence["fast"]["freshness"] == "stale"
    assert evidence["slow"]["age_seconds"] == 0
    assert evidence["slow"]["freshness"] == "fresh"
    assert cache.get_all()["fast"][0].id == "fast"
    await cache.stop()
