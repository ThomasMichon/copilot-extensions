"""Tests for the Phase 3 opt-in local CLI-mode launch surface
(agent-bridge-cli-mode-sessions).

``_launch_cli_mode_session`` is the testable core of
``agent-bridge live-sessions cli-mode launch``: it reserves the worktree's
next CLI-mode session (Phase 2), then hands off to ``agent-worktrees
embody`` -- the existing DETACHED, mux-wrapped (tmux/psmux), resume-aware,
cross-platform interactive-``copilot`` launch path -- rather than a bare
foreground subprocess, so the launched session gets genuine reattach for
free. These tests fake both the bridge client and the process runner so no
real HTTP server, ``agent-worktrees``, or ``copilot`` process is needed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_bridge.__main__ import _launch_cli_mode_session
from agent_bridge.client import BridgeClientError


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


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


def _embody_stdout(**fields: Any) -> str:
    payload = {"ok": True, "worktree_id": "wt-A", "session": "wt-wt-A", "created": True}
    payload.update(fields)
    return json.dumps(payload)


def test_launch_reserves_then_embodies_the_worktree() -> None:
    client = _FakeClient(
        reservation={"reservation_id": "r1", "claimed_by_session_id": None},
        final_reservation={"reservation_id": "r1", "claimed_by_session_id": "sid-1"},
    )
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        seen_argv.append(list(argv))
        return _FakeCompletedProcess(0, stdout=_embody_stdout())

    outcome = _launch_cli_mode_session(client, "wt-A", run=fake_run)

    assert client.create_calls == [("wt-A", 300.0)]
    argv = seen_argv[0]
    assert argv[:4] == ["agent-worktrees", "embody", "--worktree-id", "wt-A"]
    assert "--json" in argv
    assert "--driver" in argv and "cli-mode" in argv  # default driver stamp
    assert outcome["session"] == "wt-wt-A"
    assert outcome["exit_code"] == 0
    # The reservation reported back reflects the post-embody (claimed) state,
    # proving the daemon's ordinary registration/claim path (Phase 2) is what
    # binds the resulting session rather than this surface duplicating it.
    assert outcome["reservation"]["claimed_by_session_id"] == "sid-1"


def test_launch_forwards_seed_and_driver_and_verify_timeout() -> None:
    client = _FakeClient(reservation={"reservation_id": "r1"})
    seen_argv: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        seen_argv.append(list(argv))
        return _FakeCompletedProcess(0, stdout=_embody_stdout())

    _launch_cli_mode_session(
        client, "wt-A", driver="my-agent", seed="hello there",
        verify_timeout=45.0, run=fake_run,
    )

    argv = seen_argv[0]
    assert "--seed" in argv and "hello there" in argv
    assert "--driver" in argv and "my-agent" in argv
    assert "--verify-timeout" in argv and "45.0" in argv


def test_launch_propagates_active_reservation_conflict() -> None:
    client = _FakeClient(create_error=BridgeClientError(409, "active"))

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        raise AssertionError("embody must not be invoked when reserve fails")

    with pytest.raises(BridgeClientError) as excinfo:
        _launch_cli_mode_session(client, "wt-A", run=fake_run)
    assert excinfo.value.status == 409


def test_launch_surfaces_nonzero_embody_exit_code() -> None:
    client = _FakeClient(reservation={"reservation_id": "r1"})

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(3, stdout="")

    outcome = _launch_cli_mode_session(client, "wt-A", run=fake_run)
    assert outcome["exit_code"] == 3
    assert outcome["session"] is None


def test_launch_tolerates_non_json_embody_stdout() -> None:
    client = _FakeClient(reservation={"reservation_id": "r1"})

    def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(1, stdout="not json")

    outcome = _launch_cli_mode_session(client, "wt-A", run=fake_run)
    assert outcome["embody"] == {}
    assert outcome["session"] is None
