"""Tests for ``resolve-handoff-successor`` (issue #4557): the sanctioned
repair for a terminal managed worktree whose real handoff successor was
never ``register-session``'d.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_worktrees import output
from agent_worktrees import handoff_successor_repair_cli as repair_cli
from agent_worktrees import session_tracking_cli, sessions, tracking
from agent_worktrees.tracking import SessionEntry, WorktreeRecord, load_record, save_record

_SEED_TEXT = (
    "Task: finish the thing | Resume: /consume-handoff to take over | "
    "Recovery: context-handoff task:abc123"
)


def _terminal_bridge_record(tmp_tracking_dir: Path, *, worktree_path: str) -> WorktreeRecord:
    rec = WorktreeRecord(
        worktree_id="wt-1",
        branch="worktree/wt-1",
        worktree_path=worktree_path,
        repo="test-repo",
        machine="test",
        platform="wsl",
        started_at="2026-01-01T00:00:00",
        last_resumed_at="2026-01-01T00:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at="2026-01-02T00:00:00",
        sessions=[SessionEntry("predecessor", "2026-01-01T00:00:00")],
        kind="bridge",
    )
    save_record(rec, tmp_tracking_dir / "wt-1.yaml")
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    tracking.open_handoff(rec, "predecessor", "tok-1", save=True)
    return load_record(tmp_tracking_dir / "wt-1.yaml")


def _run(monkeypatch, tracking_dir: Path, **ns) -> dict:
    captured: dict = {}
    monkeypatch.setattr(session_tracking_cli, "_all_tracking_dirs", lambda: [tracking_dir])
    monkeypatch.setattr(output, "_json_output", lambda data: captured.update(data))
    rc = repair_cli.cmd_resolve_handoff_successor(argparse.Namespace(json=True, **ns))
    captured["_rc"] = rc
    return captured


def _fake_meta(cwd: str, *, live: bool = False) -> dict:
    return {
        "id": "successor", "name": "", "cwd": cwd, "branch": "",
        "created_at": "", "updated_at": "", "event_count": 3, "turn_count": 2,
        "live": live,
    }


def _user_event(text: str) -> dict:
    return {"type": "user.message", "data": {"content": text}}


def _wire_evidence(
    monkeypatch, *, cwd: str, live: bool = False,
    events: list[dict] | None = None, valid_id: str | None = "successor",
):
    """Patch the sessions module's evidence surface for a candidate id."""
    monkeypatch.setattr(sessions, "validate_session_id", lambda sid: valid_id)
    monkeypatch.setattr(sessions, "_session_meta", lambda *a, **k: _fake_meta(cwd, live=live))
    default_events = [_user_event(_SEED_TEXT), _user_event("did the actual work")]
    monkeypatch.setattr(
        sessions, "read_session_transcript",
        lambda sid: events if events is not None else default_events,
    )


def test_resolves_stale_pending_handoff_with_matching_evidence(
    tmp_tracking_dir: Path, monkeypatch,
):
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    _wire_evidence(monkeypatch, cwd="/tmp/wt-1")

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] == 0
    assert out["pending_handoffs"] == []
    assert out["resolved_head_session"] is None
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is not None
    assert rec.session_entry("successor").state == "concluded"
    assert rec.handoffs[0].state == "linked"
    assert rec.handoffs[0].successor == "successor"


def test_refuses_when_cwd_does_not_match(tmp_tracking_dir: Path, monkeypatch):
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    _wire_evidence(monkeypatch, cwd="/tmp/somewhere-else")

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None
    assert rec.handoffs[0].state == "pending"


def test_refuses_without_a_real_session_transcript(tmp_tracking_dir: Path, monkeypatch):
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    monkeypatch.setattr(sessions, "validate_session_id", lambda sid: None)
    monkeypatch.setattr(sessions, "_session_meta", lambda *a, **k: None)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="ghost",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.handoffs[0].state == "pending"


def test_refuses_on_traversal_successor_id(tmp_tracking_dir: Path, monkeypatch):
    """issue-#4594-review: an unvalidated ``--successor`` must never reach
    the session-state filesystem lookup."""
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    monkeypatch.setattr(sessions, "validate_session_id", lambda sid: None)
    called = []
    monkeypatch.setattr(
        sessions, "_session_meta", lambda *a, **k: called.append(1) or None,
    )

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="../../etc/passwd",
    )

    assert out["_rc"] != 0
    assert called == [], "must refuse before ever looking up the raw id on disk"


def test_refuses_when_successor_session_is_still_live(tmp_tracking_dir: Path, monkeypatch):
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    _wire_evidence(monkeypatch, cwd="/tmp/wt-1", live=True)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_without_a_handoff_seed_first_turn(tmp_tracking_dir: Path, monkeypatch):
    """A cwd match alone doesn't prove the session consumed a handoff --
    require its first turn to actually be the handoff seed."""
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    _wire_evidence(
        monkeypatch, cwd="/tmp/wt-1",
        events=[_user_event("just an unrelated session"), _user_event("more work")],
    )

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_when_only_the_seed_and_no_real_work(tmp_tracking_dir: Path, monkeypatch):
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    _wire_evidence(monkeypatch, cwd="/tmp/wt-1", events=[_user_event(_SEED_TEXT)])

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_on_non_terminal_worktree(tmp_tracking_dir: Path, monkeypatch):
    rec = WorktreeRecord(
        worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
        repo="test-repo", machine="test", platform="wsl",
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        sessions=[SessionEntry("predecessor", "2026-01-01T00:00:00")], kind="bridge",
    )
    save_record(rec, tmp_tracking_dir / "wt-1.yaml")
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    tracking.open_handoff(rec, "predecessor", "tok-1", save=True)
    _wire_evidence(monkeypatch, cwd="/tmp/wt-1")

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_when_successor_already_tracked(tmp_tracking_dir: Path, monkeypatch):
    rec = _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    rec.sessions.append(SessionEntry("successor", "2026-01-01T01:00:00"))
    save_record(rec, tmp_tracking_dir / "wt-1.yaml")
    _wire_evidence(monkeypatch, cwd="/tmp/wt-1")

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0


def test_refuses_on_non_pending_token(tmp_tracking_dir: Path, monkeypatch):
    _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    _wire_evidence(monkeypatch, cwd="/tmp/wt-1")

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="unknown-token", successor="successor",
    )

    assert out["_rc"] != 0


def test_verb_refuses_when_worktree_path_changed_since_evidence_check(
    tmp_tracking_dir: Path,
):
    """review-#4594: a concurrent record repair/move between the caller's
    unlocked cwd verification and this locked dispatch must not let a
    stale-path evidence match through."""
    from agent_worktrees import tracking_session_lifecycle_write as verb_mod

    rec = _terminal_bridge_record(tmp_tracking_dir, worktree_path="/tmp/wt-1")
    yaml_path = tmp_tracking_dir / "wt-1.yaml"
    # Simulate the record's worktree_path having moved after the CLI verified
    # the successor's cwd against the OLD path but before this call.
    rec.worktree_path = "/tmp/wt-1-moved"
    save_record(rec, yaml_path)

    result = verb_mod.apply_resolve_handoff_successor({
        "worktree_id": "wt-1",
        "yaml_path": str(yaml_path),
        "handoff_token": "tok-1",
        "successor_id": "successor",
        "expected_worktree_path": "/tmp/wt-1",
    })

    assert result.get("error") == "lifecycle"
    rec = load_record(yaml_path)
    assert rec.session_entry("successor") is None
    assert rec.handoffs[0].state == "pending"
