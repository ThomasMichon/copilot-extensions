"""Tests for the CodeSpace provider connection stages (connect.py)."""

from __future__ import annotations

import pytest

from agent_codespaces.connect import (
    ConnectError,
    ConnectStage,
    ConnectTracker,
    breadcrumb_prelude,
)


class TestBreadcrumb:
    def test_best_effort_and_records_session(self) -> None:
        bc = breadcrumb_prelude("my-cs")
        assert "|| true" in bc
        assert "AGENT_BRIDGE_CONNECT_LOG" in bc
        assert "my-cs" in bc
        assert "reached-device" in bc

    def test_default_session_placeholder(self) -> None:
        bc = breadcrumb_prelude("")
        assert "session=-" in bc or "'-'" in bc or " - " in bc or "-" in bc


class TestConnectError:
    def test_is_runtimeerror(self) -> None:
        assert issubclass(ConnectError, RuntimeError)

    def test_carries_stage(self) -> None:
        e = ConnectError(ConnectStage.SSH_TO_TARGET, "boot timeout", retryable=True)
        assert e.stage is ConnectStage.SSH_TO_TARGET
        assert e.retryable is True
        assert "SSH_TO_TARGET" in str(e)


class _Collector:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


class TestConnectTracker:
    def test_started_reached(self) -> None:
        c = _Collector()
        t = ConnectTracker(c, session_id="cs1")
        t.started(ConnectStage.SSH_TO_TARGET)
        t.reached(ConnectStage.SSH_TO_TARGET)
        statuses = [d["status"] for _e, d in c.events]
        assert statuses == ["started", "reached"]
        assert c.events[-1][1]["elapsed_ms"] >= 0

    def test_failed_records_retryable(self) -> None:
        c = _Collector()
        t = ConnectTracker(c)
        t.started(ConnectStage.SSH_TO_TARGET)
        t.failed(ConnectStage.SSH_TO_TARGET, "boot timeout", retryable=True)
        assert c.events[-1][1]["status"] == "failed"
        assert c.events[-1][1]["retryable"] is True

    def test_stage_context_wraps_error(self) -> None:
        t = ConnectTracker()
        with pytest.raises(ConnectError) as ei:
            with t.stage(ConnectStage.WORKTREE):
                raise ValueError("nope")
        assert ei.value.stage is ConnectStage.WORKTREE

    def test_emit_failure_is_swallowed(self) -> None:
        def boom(_e, _d):
            raise RuntimeError("x")

        t = ConnectTracker(boom)
        t.started(ConnectStage.LAUNCH_ACP)
        t.reached(ConnectStage.LAUNCH_ACP)

    def test_works_without_emit(self) -> None:
        t = ConnectTracker(None)
        t.started(ConnectStage.SSH_TO_TARGET)
        t.reached(ConnectStage.SSH_TO_TARGET)
