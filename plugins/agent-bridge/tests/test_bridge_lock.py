"""Tests for the bridge-lock producer (#4272).

`bridge_lock.write` / `remove_sync` shell to `agent-worktrees session-lock` to
mark/clear a bridge-owned Copilot session's liveness for the worktree picker.
Best-effort: no-op on missing args / missing binstub, never raise.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_bridge import bridge_lock


class _FakeProc:
    async def wait(self):
        return 0


# ── write: guards ──

@pytest.mark.asyncio
async def test_write_noops_without_required_fields():
    with patch.object(bridge_lock, "_agent_worktrees_bin") as m_bin:
        await bridge_lock.write("", "wt", 123)
        await bridge_lock.write("sid", None, 123)
        await bridge_lock.write("sid", "wt", None)
    m_bin.assert_not_called()  # bailed before resolving the binstub


@pytest.mark.asyncio
async def test_write_noops_when_no_binstub():
    with patch.object(bridge_lock, "_agent_worktrees_bin", return_value=None), \
         patch("asyncio.create_subprocess_exec") as m_exec:
        await bridge_lock.write("sid", "wt", 123)
    m_exec.assert_not_called()


@pytest.mark.asyncio
async def test_write_builds_correct_argv():
    with patch.object(bridge_lock, "_agent_worktrees_bin",
                      return_value="/bin/agent-worktrees"), \
         patch("asyncio.create_subprocess_exec",
               return_value=_FakeProc()) as m_exec:
        await bridge_lock.write("sid-a", "wt-a", 4321)
    argv = list(m_exec.call_args.args)
    assert argv == [
        "/bin/agent-worktrees", "session-lock", "write",
        "--session", "sid-a", "--worktree", "wt-a", "--pid", "4321",
    ]


@pytest.mark.asyncio
async def test_write_swallows_spawn_error():
    with patch.object(bridge_lock, "_agent_worktrees_bin",
                      return_value="/bin/agent-worktrees"), \
         patch("asyncio.create_subprocess_exec",
               side_effect=OSError("boom")):
        await bridge_lock.write("sid", "wt", 1)  # must not raise


# ── remove_sync: guards + argv ──

def test_remove_sync_noops_without_session():
    with patch.object(bridge_lock, "_agent_worktrees_bin") as m_bin:
        bridge_lock.remove_sync("")
    m_bin.assert_not_called()


def test_remove_sync_noops_when_no_binstub():
    with patch.object(bridge_lock, "_agent_worktrees_bin", return_value=None), \
         patch("subprocess.Popen") as m_popen:
        bridge_lock.remove_sync("sid")
    m_popen.assert_not_called()


def test_remove_sync_builds_correct_argv():
    with patch.object(bridge_lock, "_agent_worktrees_bin",
                      return_value="/bin/agent-worktrees"), \
         patch("subprocess.Popen") as m_popen:
        bridge_lock.remove_sync("sid-z")
    argv = list(m_popen.call_args.args[0])
    assert argv == [
        "/bin/agent-worktrees", "session-lock", "remove", "--session", "sid-z",
    ]


def test_remove_sync_swallows_spawn_error():
    with patch.object(bridge_lock, "_agent_worktrees_bin",
                      return_value="/bin/agent-worktrees"), \
         patch("subprocess.Popen", side_effect=OSError("boom")):
        bridge_lock.remove_sync("sid")  # must not raise


def test_child_env_scrubs_venv_markers():
    with patch.dict("os.environ", {"VIRTUAL_ENV": "/x", "PYTHONPATH": "/y",
                                   "KEEP": "1"}, clear=False):
        env = bridge_lock._child_env()
    assert "VIRTUAL_ENV" not in env and "PYTHONPATH" not in env
    assert env.get("KEEP") == "1"
