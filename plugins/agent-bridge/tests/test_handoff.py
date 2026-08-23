"""Tests for context-aware in-place session handoff (session_manager)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import SessionManager
from agent_bridge.transport import SpawnTarget


def _mock_agent_proc():
    proc = MagicMock()
    proc.proc = MagicMock()
    proc.proc.pid = 12345
    proc.proc.returncode = None
    proc.proc.stdin = MagicMock()
    proc.proc.stdout = MagicMock()
    proc.proc.stderr = MagicMock()
    proc.proc.stderr.readline = AsyncMock(return_value=b"")
    return proc


@pytest.fixture
def _patch_spawn():
    with patch("agent_bridge.session_manager.spawn") as mock_spawn:
        mock_spawn.return_value = _mock_agent_proc()
        yield mock_spawn


@pytest.fixture
def _patch_acp(mock_acp_client):
    # Session Hosts are always on (dotfiles#1478): a local start now connects via
    # _connect_via_session_host, which can't stand up in a unit test. Stub it to
    # hand back the mock client + a stable acp id so the predecessor's initial
    # start lands IDLE. (The handoff successor uses the classic spawn/AcpClient
    # path, which _patch_spawn + this AcpClient patch still cover -- including the
    # failing-successor test that re-patches AcpClient.)
    async def _fake_host_connect(self, target, **kwargs):
        return mock_acp_client, mock_acp_client.acp_session_id

    with patch("agent_bridge.session_manager.AcpClient") as mock_cls, \
            patch.object(
                SessionManager, "_connect_via_session_host", _fake_host_connect
            ):
        mock_cls.return_value = mock_acp_client
        yield mock_cls


def _events(session, event_type):
    return [
        e for e in session.event_log.get_events() if e.event == event_type
    ]


class TestHandoffPrimitive:
    """The bridge-native in-place handoff of a hosted session."""

    @pytest.mark.asyncio
    async def test_handoff_spawns_seeded_successor(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp,
        mock_acp_client,
    ) -> None:
        mock_acp_client.send_prompt = AsyncMock(return_value={
            "response_text": "## Objective\nShip the thing\n## Next steps\nDo X",
            "stop_reason": "end_turn",
        })
        pred = await session_manager.start_session(
            spawn_target, caller_id="wt-1"
        )

        succ = await session_manager.handoff_session(pred.session_id)

        # A distinct successor session was created in the same worktree.
        assert succ.session_id != pred.session_id
        assert succ.caller_id == pred.caller_id == "wt-1"
        assert succ.status in (SessionStatus.IDLE, SessionStatus.RUNNING)

        # The brief was authored and the changeover announced on both streams.
        assert _events(pred, "handoff_brief")
        pred_ho = _events(pred, "session_handoff")
        succ_ho = _events(succ, "session_handoff")
        assert pred_ho and succ_ho
        assert pred_ho[0].data["rolled_from"] == pred.session_id
        assert pred_ho[0].data["rolled_to"] == succ.session_id
        assert pred_ho[0].data["worktree_id"] == "wt-1"
        assert "Ship the thing" in pred_ho[0].data["summary"]

        # The successor was seeded with the brief as its opening turn.
        if succ._prompt_task is not None:
            await succ._prompt_task
        user_msgs = _events(succ, "user_message")
        assert user_msgs
        assert "CONTINUATION BRIEF" in user_msgs[0].data["content"]
        assert "Ship the thing" in user_msgs[0].data["content"]

    @pytest.mark.asyncio
    async def test_handoff_persists_two_way_link(
        self, session_manager, tmp_db, spawn_target, _patch_spawn, _patch_acp,
    ) -> None:
        pred = await session_manager.start_session(spawn_target, caller_id="wt-2")
        succ = await session_manager.handoff_session(pred.session_id)

        pred_row = tmp_db.get_session(pred.session_id)
        succ_row = tmp_db.get_session(succ.session_id)
        assert pred_row["successor_id"] == succ.session_id
        assert pred_row["handoff_at"] is not None
        assert succ_row["predecessor_id"] == pred.session_id

    @pytest.mark.asyncio
    async def test_predecessor_retired_after_handoff(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp,
    ) -> None:
        pred = await session_manager.start_session(spawn_target, caller_id="wt-3")
        await session_manager.handoff_session(pred.session_id)
        assert pred.status == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_handoff_synthesizes_brief_when_child_silent(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp,
        mock_acp_client,
    ) -> None:
        # Child returns an empty reply -> fallback synthesized brief is used.
        mock_acp_client.send_prompt = AsyncMock(return_value={
            "response_text": "", "stop_reason": "end_turn",
        })
        pred = await session_manager.start_session(spawn_target, caller_id="wt-4")
        succ = await session_manager.handoff_session(pred.session_id)

        brief_events = _events(pred, "handoff_brief")
        assert brief_events
        assert "synthesized handoff" in brief_events[0].data["brief"]
        assert _events(succ, "session_handoff")

    @pytest.mark.asyncio
    async def test_failed_successor_spawn_retains_predecessor(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp,
    ) -> None:
        pred = await session_manager.start_session(spawn_target, caller_id="wt-5")
        # Now make the successor's start FAIL. Session Hosts are always on
        # (dotfiles#1478), so a local successor starts via
        # _connect_via_session_host -- override the fixture's succeeding stub with
        # one that raises, so only the successor's start fails.
        async def _failing_host_connect(self, target, **kwargs):
            raise RuntimeError("boom")

        with patch.object(
            SessionManager, "_connect_via_session_host", _failing_host_connect
        ):
            with pytest.raises(RuntimeError, match="failed to start"):
                await session_manager.handoff_session(pred.session_id)

        # Predecessor is NOT retired -- the worktree keeps a live head.
        assert pred.status == SessionStatus.IDLE
        assert _events(pred, "handoff_failed")

    @pytest.mark.asyncio
    async def test_handoff_refused_for_command_agent(
        self, session_manager, _patch_spawn, _patch_acp,
    ) -> None:
        target = SpawnTarget(
            type="command",
            cwd="/workspaces/repo",
            spawn_command=["gh", "codespace", "ssh", "-c", "cs-name"],
        )
        sess = await session_manager.start_session(target, agent_name="cs-agent")
        with pytest.raises(ValueError, match="single-checkout"):
            await session_manager.handoff_session(sess.session_id)

    @pytest.mark.asyncio
    async def test_handoff_refused_mid_turn(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp,
    ) -> None:
        pred = await session_manager.start_session(spawn_target, caller_id="wt-6")
        pred.status = SessionStatus.RUNNING
        with pytest.raises(ValueError, match="not idle"):
            await session_manager.handoff_session(pred.session_id)


class TestHandoffGroundLayerLineage:
    """Phase 4 (worktree-self-knowledge): a bridge/NF handoff writes the
    succession into the agent-worktrees GROUND LAYER (not just the bridge DB)
    and seeds the ACP successor with its role + the worktree history digest."""

    @staticmethod
    def _local_wt_target() -> SpawnTarget:
        # A local target carrying a ground-layer worktree id activates the
        # Phase 4 writes (the default fixture has no worktree_id, so it doesn't).
        return SpawnTarget(type="local", cwd="/tmp/test-dir", worktree_id="wt-gl")

    @pytest.mark.asyncio
    async def test_handoff_writes_ground_layer_and_seeds_role(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp,
        mock_acp_client,
    ) -> None:
        mock_acp_client.send_prompt = AsyncMock(return_value={
            "response_text": "## Objective\nShip it", "stop_reason": "end_turn",
        })
        target = self._local_wt_target()

        with patch("agent_bridge.worktree_lineage.register_session") as m_reg, \
                patch("agent_bridge.worktree_lineage.link_succession") as m_link, \
                patch("agent_bridge.worktree_lineage.note_handoff") as m_note, \
                patch(
                    "agent_bridge.worktree_lineage.session_role",
                    return_value={"role": "head", "head_session": "acp"},
                ), \
                patch(
                    "agent_bridge.worktree_lineage.history_digest",
                    return_value="focus: ship it",
                ):
            pred = await session_manager.start_session(target, caller_id="wt-gl")
            succ = await session_manager.handoff_session(pred.session_id)

            # 4a: the ACP session was registered into the ground layer at start
            # (fired for both the predecessor and the successor start).
            assert m_reg.call_count >= 1
            assert m_reg.call_args_list[0].args[0] == "wt-gl"

            # 4b: succession + handoff note written to the ground layer.
            assert m_link.call_count == 1
            link_args = m_link.call_args.args
            assert link_args[0] == "wt-gl"  # worktree id
            assert m_note.call_count == 1
            assert m_note.call_args.args[0] == "wt-gl"

        # 4c: the successor's opening turn carries the lineage header + digest.
        if succ._prompt_task is not None:
            await succ._prompt_task
        user_msgs = _events(succ, "user_message")
        assert user_msgs
        content = user_msgs[0].data["content"]
        assert "place in this worktree's lineage" in content
        assert "focus: ship it" in content
        # ...and still carries the warm brief.
        assert "CONTINUATION BRIEF" in content

    @pytest.mark.asyncio
    async def test_no_ground_layer_write_without_worktree_id(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp,
    ) -> None:
        # The default fixture target has no worktree_id -> no ground-layer write.
        with patch("agent_bridge.worktree_lineage.link_succession") as m_link, \
                patch("agent_bridge.worktree_lineage.register_session") as m_reg:
            pred = await session_manager.start_session(spawn_target, caller_id="wt-x")
            await session_manager.handoff_session(pred.session_id)
            assert m_link.call_count == 0
            assert m_reg.call_count == 0
