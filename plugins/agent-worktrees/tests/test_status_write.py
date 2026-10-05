"""Worktree status-core write behavior."""

from __future__ import annotations

import argparse

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import tracking


@pytest.fixture
def status_env(tmp_path, tmp_tracking_dir, monkeypatch):
    record = tracking.WorktreeRecord(
        worktree_id="wt-status",
        branch="worktree/wt-status",
        worktree_path=str(tmp_path),
        repo="example",
        machine="machine",
        platform="wsl",
        started_at="2026-08-28T00:00:00",
        last_resumed_at="2026-08-28T00:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at="2026-08-28T01:00:00",
        sessions=[],
    )
    path = tmp_tracking_dir / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    monkeypatch.setattr(main.cfg, "load_config", lambda: object())
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda: tmp_tracking_dir)
    monkeypatch.setattr(
        main,
        "_infer_worktree_id",
        lambda _worktree_id, _config=None: record.worktree_id,
    )
    monkeypatch.setattr(main, "_resolve_worktree_id", lambda worktree_id: worktree_id)
    return path


def test_follow_up_reactivates_finalized_worktree(status_env):
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Begin the next change",
        follow_up=True,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "active"
    assert record.completed_at is None
    assert record.follow_up is True
    assert record.summary == "Begin the next change"


def test_summary_only_preserves_finalized_status(status_env):
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Keep the existing disposition",
        follow_up=None,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "finalized"
    assert record.completed_at == "2026-08-28T01:00:00"


def test_activity_write_is_independent_of_summary_and_title(status_env):
    """#3307 worktrees-pivot-ux-overhaul follow-up: --activity writes the
    current-sub-task field without disturbing summary/title, and stamps its
    own activity_at."""
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Overall recap",
        activity=None,
        follow_up=None,
    ) == 0
    assert main._cmd_status_write(
        args,
        summary=None,
        activity="Running the retry-budget tests",
        follow_up=None,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.summary == "Overall recap"
    assert record.activity == "Running the retry-budget tests"
    assert record.activity_at is not None


def test_paused_does_not_reactivate_finalized_worktree(status_env):
    """Unlike `follow_up=True`, `paused=True` is purely informational and
    must never flip a finalized worktree back to active."""
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Leave this open on purpose",
        paused=True,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "finalized"
    assert record.completed_at == "2026-08-28T01:00:00"
    assert record.paused is True
    assert record.summary == "Leave this open on purpose"


def test_unpaused_prints_an_explicit_confirmation(status_env, capsys):
    """`--unpaused` must print an explicit confirmation that the flag was
    cleared, not just the unrelated follow-up flag (`resolved`) -- otherwise
    it is indistinguishable from a write that never touched `paused` at
    all."""
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(args, summary=None, paused=True) == 0
    capsys.readouterr()  # discard the --paused confirmation above

    assert main._cmd_status_write(args, summary=None, paused=False) == 0
    out = capsys.readouterr().out
    assert "(unpaused)" in out

    record = tracking.load_record(status_env)
    assert record.paused is False


def test_every_status_write_requires_verb_v2_even_without_paused(status_env, monkeypatch):
    """Every status write must avoid a resident v1 full-record writer, not
    only ones touching `paused`: a v1 daemon's old full-record writer
    silently drops `paused`/`paused_revision` (fields it predates), so a
    plain summary/title/follow-up write reaching that daemon during
    rolling skew would erase an already-persisted pause set moments
    earlier by a (correctly v2-gated) `--paused` write."""
    from agent_worktrees import tracking_write

    args = argparse.Namespace(worktree_id=None)
    captured_versions = []
    real_dispatch = tracking_write.dispatch

    def _spy_dispatch(verb, payload, **kwargs):
        captured_versions.append(kwargs.get("min_version"))
        return real_dispatch(verb, payload, **kwargs)

    monkeypatch.setattr(tracking_write, "dispatch", _spy_dispatch)

    assert main._cmd_status_write(args, summary="plain write", paused=None) == 0
    assert main._cmd_status_write(args, summary=None, paused=True) == 0

    assert captured_versions == [2, 2]


def test_status_history_disambiguates_pausing_from_unpausing(status_env, capsys):
    """`status --history`'s plain-text rendering must not show the same
    `(paused)` label for both a --paused and a --unpaused entry -- the
    `changed` field name alone can't distinguish set from clear."""
    args = argparse.Namespace(worktree_id=None)

    # summary=None on both so `changed` is exactly `["paused"]` on each
    # write -- isolates the assertion to the `paused` column alone.
    assert main._cmd_status_write(args, summary=None, paused=True) == 0
    assert main._cmd_status_write(args, summary=None, paused=False) == 0
    capsys.readouterr()  # discard both write confirmations above

    assert main._cmd_status_history(
        argparse.Namespace(worktree_id=None, limit=None, json=False),
    ) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    paused_glyph_lines = [line for line in lines if "(paused)" in line]
    assert len(paused_glyph_lines) == 2
    assert "\u23f8" in paused_glyph_lines[0]
    assert "\u23f8" not in paused_glyph_lines[1]


def test_first_write_in_session_emits_stage_5_status_reported(status_env, monkeypatch):
    """Stage 5 (status_reported): the first status-report write in a session
    marks "Copilot did something here"."""
    from agent_worktrees import activity

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-1")
    args = argparse.Namespace(worktree_id=None)

    main._cmd_status_write(args, summary="First summary")

    events = activity.read_events(worktree_id="wt-status", event="status_reported")
    assert len(events) == 1
    assert events[0]["stage"] == 5
    assert events[0]["stage_name"] == "status_reported"
    assert events[0]["session_id"] == "sess-1"


def test_second_write_in_same_session_does_not_duplicate_stage_5(
    status_env, monkeypatch,
):
    from agent_worktrees import activity

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-1")
    args = argparse.Namespace(worktree_id=None)

    main._cmd_status_write(args, summary="First summary")
    main._cmd_status_write(args, summary="Second summary")

    events = activity.read_events(worktree_id="wt-status", event="status_reported")
    assert len(events) == 1


def test_write_without_session_id_does_not_emit_stage_5(status_env, monkeypatch):
    from agent_worktrees import activity

    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    args = argparse.Namespace(worktree_id=None)

    main._cmd_status_write(args, summary="No session")

    assert activity.read_events(worktree_id="wt-status", event="status_reported") == []


def test_ambiguous_write_outcome_is_reported_not_swallowed(status_env, monkeypatch):
    """agent-worktrees-authoritative-daemon Phase 3: a write whose daemon
    request was sent and then failed is genuinely ambiguous (the mutation may
    already be committed server-side) -- `_cmd_status_write` must surface
    `AmbiguousWriteOutcome` as a reported command failure, never silently
    retry or swallow it (`tracking_write.dispatch`'s own contract)."""
    from agent_worktrees import tracking_write

    def _raise(*_args, **_kwargs):
        raise tracking_write.AmbiguousWriteOutcome("request sent, no response")

    monkeypatch.setattr(tracking_write, "dispatch", _raise)
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(args, summary="Will not land") == 1

    # Refused before any mutation -- the record must be untouched.
    record = tracking.load_record(status_env)
    assert record.summary != "Will not land"
