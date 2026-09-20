"""Tests for the Phase 3 opt-in local CLI-mode launch surface
(agent-bridge-cli-mode-sessions).

``_launch_cli_mode_session`` is the testable core of
``agent-bridge live-sessions cli-mode launch``: it reserves the worktree's
next CLI-mode session (Phase 2), then runs a standard, muxed, interactive
``copilot`` process bound to it -- foreground, inherited stdio, no new
execution or terminal protocol. These tests fake both the bridge client and
the process runner so no real HTTP server or ``copilot`` process is needed.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_bridge.__main__ import _launch_cli_mode_session
from agent_bridge.client import BridgeClientError


class _FakeCompletedProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class _FakeClient:
    def __init__(
        self,
        *,
        reservation: dict[str, Any] | None = None,
        final_reservation: dict[str, Any] | None = None,
        create_error: BridgeClientError | None = None,
    ) -> None:
        self._reservation = reservation or {}
        self._final_reservation = final_reservation
        self._create_error = create_error
        self.create_calls: list[tuple[str, float]] = []
        self.get_calls: list[str] = []

    def create_cli_mode_reservation(
        self, worktree_id: str, *, ttl_seconds: float = 300.0,
    ) -> dict[str, Any]:
        self.create_calls.append((worktree_id, ttl_seconds))
        if self._create_error is not None:
            raise self._create_error
        return self._reservation

    def get_cli_mode_reservation(self, worktree_id: str) -> dict[str, Any]:
        self.get_calls.append(worktree_id)
        if self._final_reservation is not None:
            return self._final_reservation
        return self._reservation


def test_launch_reserves_then_runs_copilot_in_cwd() -> None:
    client = _FakeClient(
        reservation={"reservation_id": "r1", "claimed_by_session_id": None},
        final_reservation={"reservation_id": "r1", "claimed_by_session_id": "sid-1"},
    )
    seen_argv: list[list[str]] = []
    seen_kwargs: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        seen_argv.append(list(argv))
        seen_kwargs.append(kwargs)
        return _FakeCompletedProcess(0)

    outcome = _launch_cli_mode_session(
        client, "wt-A", "/some/worktree", run=fake_run,
    )

    assert client.create_calls == [("wt-A", 300.0)]
    assert seen_kwargs[0]["cwd"] == "/some/worktree"
    assert seen_argv[0][0]  # resolved copilot executable, non-empty
    assert outcome["exit_code"] == 0
    # The reservation reported back reflects the post-launch (claimed) state,
    # proving the daemon's ordinary registration/claim path (Phase 2) is what
    # this surface relies on rather than duplicating it.
    assert outcome["reservation"]["claimed_by_session_id"] == "sid-1"


def test_launch_forwards_extra_copilot_args() -> None:
    client = _FakeClient(reservation={"reservation_id": "r1"})
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        seen_argv.append(list(argv))
        return _FakeCompletedProcess(0)

    _launch_cli_mode_session(
        client, "wt-A", "/w", copilot_args=["--resume", "abc"], run=fake_run,
    )

    assert seen_argv[0][-2:] == ["--resume", "abc"]


def test_launch_propagates_active_reservation_conflict() -> None:
    client = _FakeClient(create_error=BridgeClientError(409, "active"))

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        raise AssertionError("copilot must not be spawned when reserve fails")

    with pytest.raises(BridgeClientError) as excinfo:
        _launch_cli_mode_session(client, "wt-A", "/w", run=fake_run)
    assert excinfo.value.status == 409


def test_launch_surfaces_nonzero_copilot_exit_code() -> None:
    client = _FakeClient(reservation={"reservation_id": "r1"})

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(7)

    outcome = _launch_cli_mode_session(client, "wt-A", "/w", run=fake_run)
    assert outcome["exit_code"] == 7
