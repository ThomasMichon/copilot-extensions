"""Tests for the checkpointed connection pipeline (connect.py)."""

from __future__ import annotations

import pytest

from agent_bridge.connect import (
    STAGE_POLICIES,
    ConnectError,
    ConnectStage,
    ConnectTracker,
    stage_policy,
)


class TestStagePolicies:
    def test_all_stages_have_policies(self) -> None:
        for stage in ConnectStage:
            assert stage in STAGE_POLICIES
            assert STAGE_POLICIES[stage].stage is stage

    def test_policy_profile_matches_design(self) -> None:
        # CONNECT_BRIDGE: transient on restart -> patient + retryable.
        assert stage_policy(ConnectStage.CONNECT_BRIDGE).patient
        assert stage_policy(ConnectStage.CONNECT_BRIDGE).retryable
        # BRIDGE_TO_SSHMGR: reliable -> fail fast, not retryable.
        assert not stage_policy(ConnectStage.BRIDGE_TO_SSHMGR).patient
        assert not stage_policy(ConnectStage.BRIDGE_TO_SSHMGR).retryable
        # SSH_TO_TARGET: boot/WoL -> patient + retryable.
        assert stage_policy(ConnectStage.SSH_TO_TARGET).patient
        assert stage_policy(ConnectStage.SSH_TO_TARGET).retryable
        # Auth/env, binstub: instant fail, not retryable.
        assert not stage_policy(ConnectStage.TARGET_AUTH_ENV).retryable
        assert not stage_policy(ConnectStage.TARGET_BINSTUB).retryable
        # Worktree + launch: propagate, no retries.
        assert not stage_policy(ConnectStage.WORKTREE).retryable
        assert not stage_policy(ConnectStage.LAUNCH_ACP).retryable

    def test_stage_order(self) -> None:
        assert [int(s) for s in ConnectStage] == [1, 2, 3, 4, 5, 6, 7]


class TestConnectError:
    def test_is_runtimeerror(self) -> None:
        # Subclassing RuntimeError keeps existing except-paths working.
        assert issubclass(ConnectError, RuntimeError)

    def test_retryable_defaults_to_policy(self) -> None:
        e = ConnectError(ConnectStage.SSH_TO_TARGET, "boom")
        assert e.retryable is True
        e2 = ConnectError(ConnectStage.TARGET_AUTH_ENV, "dead")
        assert e2.retryable is False

    def test_retryable_override(self) -> None:
        e = ConnectError(ConnectStage.SSH_TO_TARGET, "auth rejected", retryable=False)
        assert e.retryable is False

    def test_message_names_stage(self) -> None:
        e = ConnectError(ConnectStage.LAUNCH_ACP, "timed out")
        assert "LAUNCH_ACP" in str(e)
        assert e.stage is ConnectStage.LAUNCH_ACP


class _Collector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


class TestConnectTracker:
    def test_started_reached_emit_checkpoints(self) -> None:
        c = _Collector()
        t = ConnectTracker(c, session_id="s1")
        t.started(ConnectStage.SSH_TO_TARGET, "host=foo")
        t.reached(ConnectStage.SSH_TO_TARGET)
        kinds = [(e[1]["stage_name"], e[1]["status"]) for e in c.events]
        assert kinds == [
            ("SSH_TO_TARGET", "started"),
            ("SSH_TO_TARGET", "reached"),
        ]
        assert all(e[0] == ConnectTracker.EVENT for e in c.events)

    def test_reached_includes_elapsed(self) -> None:
        c = _Collector()
        t = ConnectTracker(c)
        t.started(ConnectStage.WORKTREE)
        t.reached(ConnectStage.WORKTREE)
        reached = c.events[-1][1]
        assert "elapsed_ms" in reached
        assert reached["elapsed_ms"] >= 0

    def test_failed_includes_retryable(self) -> None:
        c = _Collector()
        t = ConnectTracker(c)
        t.started(ConnectStage.SSH_TO_TARGET)
        t.failed(ConnectStage.SSH_TO_TARGET, "refused")
        failed = c.events[-1][1]
        assert failed["status"] == "failed"
        assert failed["retryable"] is True
        assert failed["detail"] == "refused"

    def test_stage_context_success(self) -> None:
        c = _Collector()
        t = ConnectTracker(c)
        with t.stage(ConnectStage.WORKTREE):
            pass
        statuses = [e[1]["status"] for e in c.events]
        assert statuses == ["started", "reached"]

    def test_stage_context_wraps_unexpected_error(self) -> None:
        c = _Collector()
        t = ConnectTracker(c)
        with pytest.raises(ConnectError) as ei:
            with t.stage(ConnectStage.WORKTREE):
                raise ValueError("disk full")
        assert ei.value.stage is ConnectStage.WORKTREE
        assert "disk full" in str(ei.value)
        assert c.events[-1][1]["status"] == "failed"

    def test_stage_context_preserves_inner_connect_error_stage(self) -> None:
        c = _Collector()
        t = ConnectTracker(c)
        with pytest.raises(ConnectError) as ei:
            with t.stage(ConnectStage.WORKTREE):
                # A deeper stage failed -- its stage must be preserved.
                raise ConnectError(ConnectStage.TARGET_AUTH_ENV, "no relay")
        assert ei.value.stage is ConnectStage.TARGET_AUTH_ENV

    def test_emit_failure_never_breaks_tracking(self) -> None:
        def boom(_e, _d):
            raise RuntimeError("emit broke")

        t = ConnectTracker(boom)
        # Must not raise despite the failing emit callback.
        t.started(ConnectStage.SSH_TO_TARGET)
        t.reached(ConnectStage.SSH_TO_TARGET)

    def test_works_without_emit(self) -> None:
        t = ConnectTracker(None, session_id="x")
        t.started(ConnectStage.LAUNCH_ACP)
        t.reached(ConnectStage.LAUNCH_ACP)
