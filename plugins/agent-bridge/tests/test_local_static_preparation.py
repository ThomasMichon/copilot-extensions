import asyncio
import json
from unittest.mock import AsyncMock

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
    assert "AGENT_RT_ROOT" not in kwargs["env"]


@pytest.mark.asyncio
async def test_standalone_compatibility_is_only_used_without_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, "_agent_worktrees_launch_prefix", lambda **kw: None)
    fallback = AsyncMock(return_value=legacy.RefreshResult("ready"))
    monkeypatch.setattr(legacy, "refresh_local_cache", fallback)
    assert (await preparation.prepare_for_launch(tmp_path)).status == "ready"
    fallback.assert_awaited_once_with(tmp_path)


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
