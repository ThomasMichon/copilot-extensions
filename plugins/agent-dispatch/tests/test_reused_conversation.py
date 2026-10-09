"""A retained allocation resumes its conversation without taking over live work."""

import json
import subprocess

import pytest

from agent_dispatch import bridge, bridge_reclaim, embody
from agent_dispatch.spawn_factories import make_headless_spawn
from tests._helpers import TEST_REPO


def _task(ownership="reused", *, conversation_retired=False):
    return {
        "id": "review-task",
        "repo": TEST_REPO,
        "spawn_worktree": "review-worktree",
        "spawn_worktree_path": "/example/review-worktree",
        "spawn_worktree_ownership": ownership,
        "spawn_session_handle": None,
        "spawn_conversation_retired": conversation_retired,
    }


def _transport(monkeypatch, *, refusal=None, send_busy=False):
    calls = []
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(bridge, "_resolve_agent_record", lambda *a, **kw: None)

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["--json", "resume"]:
            if refusal:
                return subprocess.CompletedProcess(argv, 1, json.dumps(refusal), "refused")
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"session_id": "existing-conversation"}), "",
            )
        if "create" in argv:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"session_id": "replacement-conversation"}), "",
            )
        if argv[1] == "send" or argv[1:3] == ["--json", "send"]:
            if send_busy:
                return subprocess.CompletedProcess(
                    argv, bridge_reclaim._SEND_BUSY_EXIT, "", "session busy",
                )
            return subprocess.CompletedProcess(argv, 0, "sent", "")
        pytest.fail(f"unexpected bridge operation: {argv}")

    monkeypatch.setattr(bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge_reclaim.subprocess, "run", run)
    return calls


def test_missing_handle_resumes_retained_worktree_conversation(monkeypatch):
    calls = _transport(monkeypatch)
    ok, handle = make_headless_spawn()(_task())
    assert ok is True
    assert handle["session"] == "local-body:existing-conversation"
    assert calls[0] == ["bridge", "--json", "resume", "review-worktree", "--strict"]
    position = calls[1].index("send")
    assert calls[1][position:position + 2] == ["send", "existing-conversation"]
    assert not any("create" in call or "--force" in call for call in calls)


def test_first_allocation_still_creates_a_conversation(monkeypatch):
    calls = _transport(monkeypatch)
    ok, handle = make_headless_spawn()(_task("created"))
    assert ok is True
    assert handle["session"] == "local-body:replacement-conversation"
    assert any("create" in call for call in calls)


def test_retained_worktree_never_takes_over_an_interactive_holder(monkeypatch):
    calls = _transport(monkeypatch, refusal={
        "reason": "live_cli_holds_worktree", "session_id": "interactive-holder",
    })
    ok, handle = make_headless_spawn()(_task())
    assert ok is False
    assert handle["deferred"] is True
    assert len(calls) == 1
    assert not any("restart-worktree" in call or "create" in call for call in calls)


def test_resume_failure_is_not_a_success_shaped_fresh_conversation(monkeypatch):
    calls = _transport(monkeypatch, refusal={"reason": "coordinator_unreachable"})
    ok, handle = make_headless_spawn()(_task())
    assert ok is False
    assert "refused" in handle["error"]
    assert len(calls) == 1


def test_busy_send_never_ends_or_replaces_the_resumed_conversation(monkeypatch):
    calls = _transport(monkeypatch, send_busy=True)
    ok, handle = make_headless_spawn()(_task())
    assert ok is False
    assert handle["deferred"] is True
    assert len(calls) == 2
    assert not any(
        "end" in call or "restart-worktree" in call or "create" in call
        for call in calls
    )


def test_gone_carried_body_falls_back_to_worktree_resume_not_create(monkeypatch):
    calls = _transport(monkeypatch)
    monkeypatch.setattr(embody, "local_body_verdict", lambda sid: "gone")
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **kw: False)
    task = {**_task(), "spawn_session_handle": "local-body:stopped-body"}
    ok, handle = make_headless_spawn()(task)
    assert ok is True
    assert handle["session"] == "local-body:existing-conversation"
    assert not any("create" in call for call in calls)


def test_retired_conversation_never_recovers_via_worktree_resume(monkeypatch):
    """copilot-extensions#5699 review: ``worktree_ownership == "reused"`` alone
    also matches a worktree whose carried session was deliberately retired
    (an operator rearm) -- the worktree-resume fallback resumes whatever
    session is latest in that directory, independent of the dropped
    ``spawn_session_handle``, so it would silently resurrect the exact
    conversation the rearm retired. A genuinely fresh conversation must be
    created instead -- never ``--strict resume``."""
    calls = _transport(monkeypatch)
    ok, handle = make_headless_spawn()(_task(conversation_retired=True))
    assert ok is True
    assert handle["session"] == "local-body:replacement-conversation"
    assert any("create" in call for call in calls)
    assert not any(
        call[1:3] == ["--json", "resume"] for call in calls
    )
