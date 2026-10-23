import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from agent_bridge import local_cache_refresh as legacy
from agent_bridge import local_static_preparation as preparation
from agent_bridge import session_lifecycle_cli as owner


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [("ready", 0), ("partial", 3), ("failed", 3)])
async def test_uses_owner_generation_without_compatibility_render(tmp_path, monkeypatch, status, code):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.setattr(owner, "_agent_worktrees_launch_prefix", lambda **kw: ["owner", "retained"])
    monkeypatch.setattr(legacy, "refresh_local_cache", AsyncMock(side_effect=AssertionError("duplicate renderer")))
    monkeypatch.setenv("AGENT_RT_ROOT", "foreign")
    value = {
        "status": status, "changed": 0, "unchanged": 2, "removed": 0,
        "warnings": 0, "blocking": int(status != "ready"), "detail": "",
        "generation": "current", "reused": True,
    }
    run = AsyncMock(return_value=legacy._RunResult(
        "completed", json.dumps({"projections": value}), code,
    ))
    monkeypatch.setattr(preparation, "_run_bounded_result", run)
    result = await preparation.prepare_for_launch(nested)
    assert result.status == status
    assert result.reused and result.generation == "current"
    args, kwargs = run.call_args
    assert args[0] == [
        "owner", "retained", "knowledge", "compose-plugins", "--cwd",
        str(tmp_path.resolve()), "--prepare-projections", "--json",
    ]
    assert kwargs["timeout"] is None
    assert kwargs["retire_group"] is True
    if os.name != "nt":
        assert kwargs["env"]["WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP"] == "1"
    assert "AGENT_RT_ROOT" not in kwargs["env"]


@pytest.mark.asyncio
async def test_standalone_compatibility_is_only_used_without_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, "_agent_worktrees_launch_prefix", lambda **kw: None)
    fallback = AsyncMock(return_value=legacy.RefreshResult("ready"))
    monkeypatch.setattr(legacy, "refresh_local_cache", fallback)
    assert (await preparation.prepare_for_launch(tmp_path)).status == "ready"
    fallback.assert_awaited_once_with(tmp_path)


@pytest.mark.asyncio
async def test_failed_owner_preserves_unknown_publication_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, "_agent_worktrees_launch_prefix", lambda **kw: ["owner"])
    value = {
        "status": "failed", "changed": 0, "unchanged": 0, "removed": 0,
        "warnings": 0, "blocking": 1, "detail": "publication failed", "counts_known": False,
    }
    monkeypatch.setattr(preparation, "_run_bounded_result", AsyncMock(return_value=legacy._RunResult(
        "completed", json.dumps({"projections": value}), 3,
    )))
    result = await preparation.prepare_for_launch(tmp_path)
    assert result.counts_known is False
    assert "counts unavailable" in result.diagnostic
    assert "0 installed" not in result.diagnostic


@pytest.mark.asyncio
async def test_refused_owner_never_falls_back_to_another_identity(tmp_path, monkeypatch):
    from agent_bridge import _peer_launch

    def refused(**kwargs):
        raise _peer_launch.ContextRefused("wrong installation")

    monkeypatch.setattr(owner, "_agent_worktrees_launch_prefix", refused)
    monkeypatch.setattr(legacy, "refresh_local_cache", AsyncMock(side_effect=AssertionError("fallback")))
    assert (await preparation.prepare_for_launch(tmp_path)).status == "unavailable"


@pytest.mark.asyncio
async def test_owner_cancellation_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, "_agent_worktrees_launch_prefix", lambda **kw: ["owner"])
    monkeypatch.setattr(preparation, "_run_bounded_result", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await preparation.prepare_for_launch(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("retire", [False, True])
async def test_owned_group_retires_after_owner_failure(monkeypatch, retire):
    import agent_procutil
    import zdd.diagnostics

    process = SimpleNamespace(
        pid=987654, returncode=23,
        communicate=AsyncMock(return_value=(b"", b"owner failed")),
    )
    monkeypatch.setattr(legacy, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(agent_procutil, "spawn_in_kill_on_close_job", AsyncMock(return_value=(process, None)))
    monkeypatch.setattr(zdd.diagnostics, "process_start_time", lambda pid: "observed-owner")
    cleanup = AsyncMock()
    monkeypatch.setattr(legacy, "_kill_tree", cleanup)
    result = await legacy._run_bounded_result(["owner"], timeout=None, retire_group=retire)
    assert result.returncode == 23
    assert cleanup.await_count == int(retire)
    if retire:
        cleanup.assert_awaited_once_with(process, None, "observed-owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("expected,current,signals", [
    ("darwin:1234.5", 1234.5, True),
    ("darwin:1234.5", 1235.5, False),
    (None, 1234.5, False),
    ("darwin:1234.5", "denied", False),
])
async def test_darwin_birth_identity_keeps_group_cleanup_bound(monkeypatch, expected, current, signals):
    import psutil
    import signal

    def process(pid):
        if current == "denied":
            raise psutil.AccessDenied(pid)
        return SimpleNamespace(create_time=lambda: current)

    monkeypatch.setattr(psutil, "Process", process)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(legacy, "sys", SimpleNamespace(platform="darwin"))
    killed = []
    monkeypatch.setattr(legacy, "os", SimpleNamespace(
        getpgid=lambda pid: 456,
        killpg=lambda pid, sig: killed.append(pid),
    ))
    child = SimpleNamespace(pid=123, returncode=0, wait=AsyncMock(return_value=0))
    await legacy._kill_tree(child, None, expected)
    assert killed == ([123] if signals else [])


@pytest.mark.asyncio
async def test_darwin_custody_unavailable_refuses_before_owner_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, "_agent_worktrees_launch_prefix", lambda **kw: ["owner"])
    monkeypatch.setattr(preparation, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(preparation, "_process_start_time", lambda pid: None)
    monkeypatch.setattr(preparation, "_run_bounded_result", AsyncMock(side_effect=AssertionError("unsafe spawn")))
    result = await preparation.prepare_for_launch(tmp_path)
    assert result.status == "unavailable"
    assert "custody unavailable" in result.detail


@pytest.mark.asyncio
async def test_already_reaped_owner_without_identity_refuses_unbounded_pipe_wait(monkeypatch):
    import agent_procutil

    async def inherited_pipes():
        await asyncio.Event().wait()

    close = Mock()
    process = SimpleNamespace(
        pid=987654, returncode=0, _transport=SimpleNamespace(close=close),
        communicate=AsyncMock(side_effect=inherited_pipes), wait=AsyncMock(return_value=0),
    )
    monkeypatch.setattr(legacy, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(agent_procutil, "spawn_in_kill_on_close_job", AsyncMock(return_value=(process, None)))
    monkeypatch.setattr(legacy, "_process_start_time", lambda pid: None)
    cleanup = AsyncMock()
    monkeypatch.setattr(legacy, "_kill_tree", cleanup)
    result = await asyncio.wait_for(
        legacy._run_bounded_result(["owner"], timeout=None, retire_group=True), timeout=1,
    )
    assert result.status == "failed"
    assert "identity unavailable" in result.detail
    assert "cleanup is unconfirmed" in result.detail
    process.communicate.assert_not_awaited()
    cleanup.assert_not_awaited()
    close.assert_called_once()
