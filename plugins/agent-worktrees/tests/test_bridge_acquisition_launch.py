"""Bridge launch has native authority and does not use filesystem resume fallback."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from agent_worktrees import bridge_acquisition_launch as launch, embody_resume

pytestmark = pytest.mark.contract("agent_worktrees.bridge_acquisition_launch")


def test_explicit_native_admission_flag_is_not_a_fresh_create_alias():
    parser = argparse.ArgumentParser()
    launch.add_arguments(parser, parser.add_mutually_exclusive_group())
    args = parser.parse_args([
        "--bridge-acquisition", "fresh", "--require-native-admission",
    ])
    assert "existing" in launch.refusal(args, SimpleNamespace(), "worktree-a")

def test_real_embody_parser_accepts_native_admission_modifier():
    from agent_worktrees.__main__ import build_parser

    args = build_parser().parse_args([
        "embody", "--worktree-id", "worktree-a", "--bridge-acquisition", "native-a",
        "--require-native-admission", "--dry-run", "--json",
    ])
    assert args.bridge_acquisition == "native-a"
    assert args.require_native_admission is True
    assert args.dry_run is True


@pytest.mark.parametrize("other", ["--fresh", "--resume-head"])
def test_real_embody_parser_still_rejects_conflicting_intents(other):
    from agent_worktrees.__main__ import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "embody", "--bridge-acquisition", "native-a", other,
        ])


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


@pytest.mark.parametrize("intent", ["native-a", "fresh"])
def test_finalizing_record_refuses_both_preflight_and_locked_admission(monkeypatch, intent):
    args = argparse.Namespace(bridge_acquisition=intent, seed=None, copilot_args=[])
    record = SimpleNamespace(resolved_head_session="native-a", status="active")
    monkeypatch.setattr(launch.sessions, "has_mux_session", lambda _wt: False)
    monkeypatch.setattr(launch, "session_liveness", lambda _sid: "dead")
    assert launch.refusal(args, record, "worktree-a") is None
    record.status = "finalizing"
    assert "finalizing" in launch.refusal(args, record, "worktree-a")
