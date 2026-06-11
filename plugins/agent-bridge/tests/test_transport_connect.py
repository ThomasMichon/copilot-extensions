"""Tests for transport connection instrumentation (breadcrumb, staged SSH)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.connect import ConnectError, ConnectStage, ConnectTracker
from agent_bridge.transport import (
    SpawnTarget,
    _breadcrumb_prelude,
    _build_remote_cmd,
    spawn_ssh,
)


class TestBreadcrumb:
    def test_prelude_is_best_effort(self) -> None:
        bc = _breadcrumb_prelude("sess-123")
        # Never aborts the command, writes to the connect log, records session.
        assert "|| true" in bc
        assert "AGENT_BRIDGE_CONNECT_LOG" in bc
        assert "sess-123" in bc
        assert "reached-device" in bc

    def test_remote_cmd_includes_breadcrumb_before_binstub(self) -> None:
        target = SpawnTarget(type="ssh", host="h", project="my-project")
        cmd = _build_remote_cmd(target, session_id="sess-9")
        assert "reached-device" in cmd
        # Breadcrumb comes first, then the binstub invocation.
        assert cmd.index("reached-device") < cmd.index("my-project")
        # Binstub args still intact.
        assert "--acp" in cmd and "--new" in cmd

    def test_remote_cmd_breadcrumb_has_no_cd_token(self) -> None:
        # Project mode must not introduce a 'cd ' (regression guard).
        target = SpawnTarget(type="ssh", host="h", project="proj")
        cmd = _build_remote_cmd(target, session_id="s")
        assert "cd " not in cmd

    def test_remote_cmd_legacy_still_has_exec(self) -> None:
        target = SpawnTarget(type="ssh", host="h", cwd="/work")
        cmd = _build_remote_cmd(target, session_id="s")
        assert "reached-device" in cmd
        assert "exec" in cmd
        assert "cd " in cmd  # legacy path cds into cwd


class TestSpawnSshStaged:
    @pytest.fixture
    def mock_manager(self):
        mgr = MagicMock()
        mgr.ensure_connected = AsyncMock()
        proc = MagicMock()
        proc.pid = 4242
        mgr.open_stdio_channel = AsyncMock(return_value=proc)
        return mgr

    @pytest.mark.asyncio
    async def test_single_attempt_no_timeout_fast_fail(self, mock_manager) -> None:
        """Without connect_timeout, a connect failure fails fast (no retry)."""
        mock_manager.ensure_connected = AsyncMock(
            side_effect=ConnectionError("refused")
        )
        target = SpawnTarget(type="ssh", host="badhost", cwd="/w")
        with patch(
            "agent_bridge.transport.get_default_manager", return_value=mock_manager
        ):
            with pytest.raises(ConnectError) as ei:
                await spawn_ssh(target)  # connect_timeout=None -> 1 attempt
        assert ei.value.stage is ConnectStage.SSH_TO_TARGET
        assert ei.value.retryable is True
        # Exactly one attempt -- no patient retry.
        assert mock_manager.ensure_connected.call_count == 1

    @pytest.mark.asyncio
    async def test_connect_error_is_runtimeerror(self, mock_manager) -> None:
        """ConnectError still satisfies legacy except RuntimeError handlers."""
        mock_manager.ensure_connected = AsyncMock(
            side_effect=ConnectionError("ControlMaster failed")
        )
        target = SpawnTarget(type="ssh", host="badhost", cwd="/w")
        with patch(
            "agent_bridge.transport.get_default_manager", return_value=mock_manager
        ):
            with pytest.raises(RuntimeError, match="Failed to establish SSH"):
                await spawn_ssh(target)

    @pytest.mark.asyncio
    async def test_checkpoints_emitted_on_success(self, mock_manager) -> None:
        events: list[tuple[str, dict]] = []
        tracker = ConnectTracker(lambda e, d: events.append((e, d)))
        target = SpawnTarget(type="ssh", host="h", cwd="/w")
        with patch(
            "agent_bridge.transport.get_default_manager", return_value=mock_manager
        ):
            await spawn_ssh(target, tracker=tracker)
        stages = {d["stage_name"] for _e, d in events}
        assert "TARGET_AUTH_ENV" in stages
        assert "SSH_TO_TARGET" in stages
        # ssh stage reached
        ssh_reached = [
            d for _e, d in events
            if d["stage_name"] == "SSH_TO_TARGET" and d["status"] == "reached"
        ]
        assert ssh_reached

    @pytest.mark.asyncio
    async def test_dead_auth_port_emits_failed_checkpoint(self, mock_manager) -> None:
        events: list[tuple[str, dict]] = []
        tracker = ConnectTracker(lambda e, d: events.append((e, d)))
        target = SpawnTarget(
            type="ssh", host="h", cwd="/w",
            auth_hooks=[{"name": "relay", "local_port": 59999}],
        )
        with patch(
            "agent_bridge.transport.get_default_manager", return_value=mock_manager
        ), patch("agent_bridge.transport._check_port_alive", return_value=False):
            await spawn_ssh(target, tracker=tracker)
        auth_failed = [
            d for _e, d in events
            if d["stage_name"] == "TARGET_AUTH_ENV" and d["status"] == "failed"
        ]
        assert auth_failed
        assert auth_failed[0]["retryable"] is False
