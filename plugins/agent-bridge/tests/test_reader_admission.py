"""Wall circuit transitions and provider fallback use deterministic clocks."""

from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge import agent_registry_namespace as namespace
from agent_bridge.agent_registry_namespace import (
    CliNamespaceResolver, NamespaceAgentInfo, NamespaceListIncomplete, NamespaceResolver,
)
from agent_bridge.reader_admission import ReaderAdmission


def test_streak_reset_probe_exclusivity_and_full_failed_probe_cooldown() -> None:
    circuit = ReaderAdmission()
    circuit.complete(0, timed_out=True, native_success=False)
    circuit.complete(1, timed_out=False, native_success=False)
    assert circuit.consecutive_overruns == 0
    circuit.complete(2, timed_out=True, native_success=False)
    assert circuit.retry_at is None
    circuit.complete(3, timed_out=False, native_success=True)
    assert circuit.consecutive_overruns == 0
    circuit.complete(4, timed_out=True, native_success=False)
    circuit.complete(5, timed_out=True, native_success=False)
    assert circuit.retry_at == 65
    assert not circuit.admit(64.999)
    assert circuit.admit(65)
    assert not circuit.admit(65)
    circuit.complete(70, timed_out=False, native_success=False)
    assert circuit.retry_at == 130
    assert not circuit.admit(129.999)
    assert circuit.admit(130)
    circuit.complete(131, timed_out=False, native_success=True)
    assert circuit.retry_at is None
    assert circuit.consecutive_overruns == 0
    assert circuit.admit(131)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_enabled", [False, True])
async def test_native_timeout_suppression_and_successful_recovery(
    monkeypatch: pytest.MonkeyPatch, fallback_enabled: bool,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    now = [10.0]
    monkeypatch.setattr(namespace, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(namespace.shutil, "which", lambda name: "provider")
    fallback = Mock(spec=NamespaceResolver) if fallback_enabled else None
    if fallback is not None:
        fallback.list = AsyncMock(return_value=[])
    provider = CliNamespaceResolver("test", "provider", fallback)
    run = Mock(side_effect=subprocess.TimeoutExpired(["provider"], 3))
    monkeypatch.setattr(namespace.subprocess, "run", run)

    async def request() -> None:
        if fallback_enabled:
            assert await provider.list(timeout=3) == []
        else:
            with pytest.raises(NamespaceListIncomplete):
                await provider.list(timeout=3)

    await request()
    assert provider._list_admission.retry_at is None
    await request()
    assert provider._list_admission.retry_at == 70
    await request()
    assert run.call_count == 2
    now[0] = 69.999
    await request()
    assert run.call_count == 2
    now[0] = 70
    run.side_effect = None
    run.return_value = subprocess.CompletedProcess(
        [], 0, json.dumps([{"name": "recovered", "state": "available"}]), "",
    )
    rows = await provider.list(timeout=3)
    assert [row.name for row in rows] == ["recovered"]
    assert run.call_count == 3
    assert provider._list_admission.retry_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_failure", ["timeout", "exit", "parse"])
async def test_failed_probe_fallback_does_not_close_or_skip_cooldown(
    monkeypatch: pytest.MonkeyPatch, probe_failure: str,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    now = [0.0]
    monkeypatch.setattr(namespace, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(namespace.shutil, "which", lambda name: "provider")
    fallback = Mock(spec=NamespaceResolver)
    fallback.list = AsyncMock(return_value=[])
    provider = CliNamespaceResolver("test", "provider", fallback)
    run = Mock(side_effect=subprocess.TimeoutExpired(["provider"], 3))
    monkeypatch.setattr(namespace.subprocess, "run", run)
    await provider.list(timeout=3)
    await provider.list(timeout=3)
    now[0] = 60
    if probe_failure != "timeout":
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess(
            [], 1 if probe_failure == "exit" else 0, "not json", "",
        )
    assert await provider.list(timeout=3) == []
    assert provider._list_admission.retry_at == 120
    now[0] = 119.999
    assert await provider.list(timeout=3) == []
    assert run.call_count == 3


@pytest.mark.asyncio
async def test_concurrent_recovery_callers_share_one_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    monkeypatch.setattr(namespace, "time", SimpleNamespace(monotonic=lambda: 60.0))
    provider = CliNamespaceResolver("test", "provider")
    provider._list_admission.retry_at = 60
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def enumerate_once(*, timeout: float | None, ttl: float) -> list[NamespaceAgentInfo]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        provider._list_native_success = True
        return []

    monkeypatch.setattr(provider, "_enumerate_once", enumerate_once)
    requests = [asyncio.create_task(provider.list()) for _ in range(8)]
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert calls == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(*requests), timeout=2)
        assert calls == 1
        assert provider._list_admission.retry_at is None
    finally:
        release.set()
        await asyncio.gather(*requests, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_failure_and_success_reset_provider_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    monkeypatch.setattr(namespace.shutil, "which", lambda name: "provider")
    provider = CliNamespaceResolver("test", "provider")
    timeout = subprocess.TimeoutExpired(["provider"], 3)
    run = Mock(side_effect=[
        timeout,
        subprocess.CompletedProcess([], 1, "", ""),
        timeout,
        subprocess.CompletedProcess([], 0, "[]", ""),
        timeout,
        timeout,
    ])
    monkeypatch.setattr(namespace.subprocess, "run", run)
    for _ in range(3):
        with pytest.raises(NamespaceListIncomplete):
            await provider.list(timeout=3)
        assert provider._list_admission.retry_at is None
    assert await provider.list(timeout=3) == []
    assert provider._list_admission.consecutive_overruns == 0
    with pytest.raises(NamespaceListIncomplete):
        await provider.list(timeout=3)
    assert provider._list_admission.retry_at is None
    with pytest.raises(NamespaceListIncomplete):
        await provider.list(timeout=3)
    assert provider._list_admission.retry_at is not None
    assert run.call_count == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_command", [False, True])
async def test_missing_provider_preserves_legitimate_absence_during_cooldown(
    monkeypatch: pytest.MonkeyPatch, explicit_command: bool,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    monkeypatch.setattr(namespace, "time", SimpleNamespace(monotonic=lambda: 10.0))
    monkeypatch.setattr(namespace.shutil, "which", lambda name: None)
    run = Mock()
    monkeypatch.setattr(namespace.subprocess, "run", run)
    provider = CliNamespaceResolver(
        "test", "missing-provider",
        command=["missing-executable", "--provider"] if explicit_command else None,
    )
    provider._list_admission.retry_at = 60
    assert await provider.list() == []
    assert provider._list_admission.retry_at == 60
    run.assert_not_called()
