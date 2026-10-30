"""Opt-in real CLI session/PID corroboration, with no model request or daemon."""

import asyncio
import os
from pathlib import Path

from agent_procutil import no_window_kwargs
import pytest

from agent_bridge.acp_client import AcpClient
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget

pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")


@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_native_acp_session_pid_owns_its_exact_hold(tmp_path, monkeypatch):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Set COPILOT_NATIVE_TEST_EXECUTABLE for isolated offline CLI qualification")
    assert Path(executable).is_absolute() and Path(executable).is_file()
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "plugins" / "agent-worktrees" / "src"))
    from agent_worktrees import (
        conditional_head_replacement, native_lifecycle_source, native_source_context, sessions,
    )

    home, workspace = tmp_path / "cli-home", tmp_path / "empty-workspace"
    home.mkdir()
    workspace.mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_BRIDGE", "AGENT_WORKTREES", "WORKTREE_", "OTEL_"))
        and key not in {
            "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE", "PSMUX_PANE",
            "WT_SESSION", "TERM_PROGRAM",
        }
    }
    environment.update(
        COPILOT_HOME=str(home), COPILOT_OFFLINE="true",
        COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:1", COPILOT_PROVIDER_TYPE="openai",
        COPILOT_PROVIDER_MODEL_ID="gpt-4.1", COPILOT_MODEL="gpt-4.1", USE_TGREP="false",
    )
    process = await asyncio.create_subprocess_exec(
        executable, "--acp", "--stdio", "--no-auto-update", "--disable-builtin-mcps",
        "--no-custom-instructions", "--no-remote", "--no-color", "--no-ask-user",
        "--log-dir", str(tmp_path / "logs"),
        cwd=str(workspace), env=environment,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, **no_window_kwargs(),
    )
    client = AcpClient()
    try:
        await asyncio.wait_for(client.start(process), 30)
        native = await asyncio.wait_for(client.new_session(str(workspace)), 30)
        session = Session("test-bridge", "test", SpawnTarget(cwd=str(workspace)), preserve_target=True)
        session.client = client
        session.acp_session_id = native
        pid = session.pid
        assert type(pid) is int and pid > 0
        observed = native_source_context.process(pid)
        epoch = native_source_context.epoch()
        witness = {"version": 1, "qualified": True, **epoch,
                   "ancestors": [{"pid": pid, "start_time": observed["start_time"]}]}
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: home / "session-state")
        expected = native_source_context.identity(native, pid, observed["start_time"], epoch)
        assert expected
        assert native_lifecycle_source.start_identity(native, witness) == expected
        recorded = conditional_head_replacement._candidate_cwd(native)
        assert recorded.resolve() == workspace.resolve()
        assert session.turn_count == 0
    finally:
        try:
            await asyncio.wait_for(client.shutdown(), 15)
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.wait(), 10)
