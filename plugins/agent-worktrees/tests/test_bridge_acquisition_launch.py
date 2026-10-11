"""Bridge launch has native authority and does not use filesystem resume fallback."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from agent_worktrees import bridge_acquisition_launch as launch, embody_resume

pytestmark = pytest.mark.contract("agent_worktrees.bridge_acquisition_launch")


@pytest.mark.parametrize("intent,expected", [("fresh", None), ("native-a", "native-a")])
def test_exact_native_resume_without_filesystem_fallback(monkeypatch, intent, expected):
    args = argparse.Namespace(bridge_acquisition=intent, seed=None, copilot_args=[])
    record = SimpleNamespace(resolved_head_session="native-a")
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    monkeypatch.setattr(launch, "session_liveness", lambda _sid: "dead")
    assert launch.refusal(args, record, "worktree-a") is None
    assert embody_resume.resume_target(args, record, None, make_new=False) == expected


@pytest.mark.parametrize("state", ["live", "unknown"])
def test_fresh_is_not_permission_to_ignore_a_live_or_unknown_native_head(monkeypatch, state):
    args = argparse.Namespace(bridge_acquisition="fresh", seed=None, copilot_args=[])
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    monkeypatch.setattr(launch, "session_liveness", lambda _sid: state)
    assert "stopped" in launch.refusal(args, SimpleNamespace(resolved_head_session="native-a"), "worktree-a")


def test_wrong_native_identity_and_live_mux_refuse(monkeypatch):
    args = argparse.Namespace(bridge_acquisition="native-b", seed=None, copilot_args=[])
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    assert "identity" in launch.refusal(args, SimpleNamespace(resolved_head_session="native-a"), "worktree-a")
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: True)
    assert "mux" in launch.refusal(args, SimpleNamespace(), "worktree-a")
