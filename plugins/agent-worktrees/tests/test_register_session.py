"""Tests for the register-session command (sessionStart hook entrypoint).

The Copilot CLI delivers session info to the sessionStart hook as a JSON
payload on stdin (COPILOT_AGENT_SESSION_ID is not reliably set in the hook
environment), so the command must read --stdin and resolve the worktree
from the payload cwd.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import activity, git_ops, session_projection, sessions, tracking, worktree_identity
from agent_worktrees.tracking import WorktreeRecord, load_record, save_record


@pytest.mark.parametrize("registration_project", ["test-repo", "other-project"])
def test_register_session_trusted_stamp_uses_uncached_record_under_guard(
    tmp_path, tmp_tracking_dir, monkeypatch_config, monkeypatch, registration_project,
):
    from agent_worktrees import handoff_diagnostics

    cfg.set_active_project(registration_project)
    _save_record(tmp_tracking_dir, "wt-subject", str(tmp_path / "checkout"))
    path = tmp_tracking_dir / "wt-subject.yaml"
    record = tracking._load_record_uncached(path)
    record.creation_nonce = "actual-incarnation"
    save_record(record, path)
    stale = tracking._load_record_uncached(path)
    stale.creation_nonce = "cached-stale-incarnation"
    session_dir = sessions._session_state_dir() / "sid-subject"
    session_dir.mkdir(parents=True)
    (session_dir / "worktree-binding.json").write_text(
        '{"project":"old","creationNonce":"old"}',
    )
    original_register = tracking.register_session
    original_stamp = handoff_diagnostics.stamp_session_state_worktree_binding
    seen = []

    def register_then_poison_cache(*args, **kwargs):
        result = original_register(*args, **kwargs)
        monkeypatch.setattr(tracking, "load_record", lambda *a, **kw: stale)
        return result

    def stamp_while_guarded(*args, **kwargs):
        import os

        key = os.path.normcase(os.path.abspath(path))
        assert tracking._thread_sidecar_counts().get(key, 0) > 0
        record = kwargs["worktree_record"]
        seen.append(record.creation_nonce if record is not None else None)
        return original_stamp(*args, **kwargs)

    monkeypatch.setattr(tracking, "register_session", register_then_poison_cache)
    monkeypatch.setattr(handoff_diagnostics, "stamp_session_state_worktree_binding", stamp_while_guarded)
    assert m.cmd_register_session(_args(
        worktree_id="wt-subject", session_id="sid-subject",
    )) == 0
    result = json.loads((session_dir / "worktree-binding.json").read_text())
    if registration_project == "test-repo":
        assert seen == ["actual-incarnation"]
        assert result["project"] == "test-repo"
        assert result["creationNonce"] == "actual-incarnation"
    else:
        assert seen == [None]
        assert "project" not in result and "creationNonce" not in result


def test_register_session_busy_optional_stamp_never_calls_writer(
    tmp_path, tmp_tracking_dir, monkeypatch_config, monkeypatch,
):
    from agent_worktrees import handoff_diagnostics

    _save_record(tmp_tracking_dir, "wt-subject", str(tmp_path / "checkout"))
    session_dir = sessions._session_state_dir() / "sid-subject"
    session_dir.mkdir(parents=True)
    binding_path = session_dir / "worktree-binding.json"
    binding_path.write_text('{"project":"newer","creationNonce":"newer"}')
    original_lock = tracking._RecordLock
    optional_attempts = []

    class Busy:
        acquired = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def lock(path, *args, **kwargs):
        if kwargs.get("blocking") is False:
            assert kwargs == {"blocking": False, "require_sidecar": True}
            optional_attempts.append(path)
            return Busy()
        return original_lock(path, *args, **kwargs)

    def unexpected_stamp(*args, **kwargs):
        pytest.fail("contended optional stamp must not call the writer")

    monkeypatch.setattr(tracking, "_RecordLock", lock)
    monkeypatch.setattr(handoff_diagnostics, "stamp_session_state_worktree_binding", unexpected_stamp)
    assert m.cmd_register_session(_args(
        worktree_id="wt-subject", session_id="sid-subject",
    )) == 0
    assert optional_attempts == [tmp_tracking_dir / "wt-subject.yaml"]
    assert json.loads(binding_path.read_text()) == {
        "project": "newer", "creationNonce": "newer",
    }


@pytest.fixture(autouse=True)
def synthetic_folder_trust(monkeypatch):
    """These registration-identity fixtures use deliberately nonexistent paths.

    Native entry/trust integration is exercised by test_worktree_trust.py.
    """
    monkeypatch.setattr("agent_worktrees.worktree_trust.ensure_record_trusted", lambda record: False)


def _save_record(tracking_dir: Path, wt_id: str, wt_path: str) -> None:
    rec = WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=wt_path,
        repo="test-repo",
        machine="test",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )
    save_record(rec, tracking_dir / f"{wt_id}.yaml")


def _args(**kw) -> argparse.Namespace:
    base = dict(
        worktree_id=None,
        session_id=None,
        cwd=None,
        stdin=False,
        pid=None,
        pane=None,
        emit_context=False,
        handoff_token=None,
        handoff_candidate_token=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _repo_with_worktree(tmp_path: Path, name: str = "anchor") -> tuple[Path, Path]:
    anchor = tmp_path / name
    worktree = tmp_path / f"{name}-worktrees" / "app-session"
    git_ops.git("init", "-b", "master", str(anchor))
    git_ops.git("config", "user.email", "t@example.com", cwd=anchor)
    git_ops.git("config", "user.name", "Test", cwd=anchor)
    (anchor / "f.txt").write_text("x\n")
    git_ops.git("add", "-A", cwd=anchor)
    git_ops.git("commit", "-m", "init", cwd=anchor)
    worktree.parent.mkdir()
    git_ops.git(
        "worktree", "add", str(worktree), "-b", "app-session", "master",
        cwd=anchor,
    )
    return anchor, worktree


def _write_managed_mux_mapping(
    root: Path,
    *,
    project: str,
    worktree_id: str,
    worktree_path: str,
    mux_session: str,
    live: bool = True,
    mapping_revision: int = 1,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mux-mapping.json").write_text(
        json.dumps(
            [
                {
                    "project": project,
                    "worktree_id": worktree_id,
                    "worktree_path": worktree_path,
                    "mux_session": mux_session,
                    "mux_bin": "psmux",
                    "mapping_revision": mapping_revision,
                    "live": live,
                }
            ]
        ),
        encoding="utf-8",
    )


class TestRegisterSessionStdin:
    def test_session_start_associates_candidate_without_moving_head(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-cutover", "/tmp/src/wt-cutover")
        tracking.register_session("wt-cutover", "old")
        rec = load_record(tmp_tracking_dir / "wt-cutover.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        monkeypatch.setenv("AGENT_WORKTREES_HANDOFF_TOKEN", "task-123")

        rc = m.cmd_register_session(_args(
            worktree_id="wt-cutover",
            session_id="new",
            emit_context=True,
        ))

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-cutover.yaml")
        # "old" yielded the moment it opened the handoff -- head is vacant,
        # not still "old". The
        # candidate association itself still does not hand head to "new";
        # that requires a separate, deliberate link/bind.
        assert rec.resolved_head_session is None
        assert rec.session_entry("old").state == "yielded"
        assert rec.handoffs[0].candidate == "new"
        assert rec.handoffs[0].state == "pending"

    def test_session_start_emits_stage_9_on_candidate_association(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 Stage 9: recognizing a handoff-candidate token in the
        sessionStart hook must emit handoff_successor_session_start_bound
        AFTER stage 4's session_started, carrying the same launch_id, so the
        ordered cutover trace never appears to move backwards."""
        _save_record(tmp_tracking_dir, "wt-cutover", "/tmp/src/wt-cutover")
        tracking.register_session("wt-cutover", "old")
        rec = load_record(tmp_tracking_dir / "wt-cutover.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        monkeypatch.setenv("AGENT_WORKTREES_HANDOFF_TOKEN", "task-123")
        successor_state = sessions._session_state_dir() / "new"
        successor_state.mkdir(parents=True, exist_ok=True)
        (successor_state / "handoff-request.json").write_text(
            json.dumps({"handoffId": "task-123", "sessionId": "new"}),
            encoding="utf-8",
        )

        recorded: list[tuple[str, dict]] = []
        real_log_event = activity.log_event

        def _capture(event, **kwargs):
            recorded.append((event, kwargs))
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _capture)

        rc = m.cmd_register_session(_args(
            worktree_id="wt-cutover", session_id="new", launch_id="flow-9",
        ))

        assert rc == 0
        events = [ev for ev, _ in recorded]
        assert events.index("session_started") < events.index(
            "handoff_successor_session_start_bound"
        )
        matches = [kw for ev, kw in recorded if ev == "handoff_successor_session_start_bound"]
        assert len(matches) == 1
        assert matches[0]["worktree_id"] == "wt-cutover"
        assert matches[0]["session_id"] == "new"
        assert matches[0]["handoff_token"] == "task-123"
        assert matches[0]["predecessor_session_id"] == "old"
        assert matches[0]["launch_id"] == "flow-9"
        stamped = json.loads((successor_state / "handoff-request.json").read_text(encoding="utf-8"))
        assert stamped["predecessor_session_id"] == "old"

    def test_session_start_emits_stage_10_and_11_on_handoff_claim(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 Stages 10/11: the sessionStart hook's ``--handoff-token``
        path (the real head-transfer, distinct from stage 9's candidate
        association) must emit handoff_successor_claimed and
        handoff_pickup_confirmed_predecessor_closing together, exactly once,
        at tracking.register_session()'s link_handoff() call site."""
        _save_record(tmp_tracking_dir, "wt-claim", "/tmp/src/wt-claim")
        tracking.register_session("wt-claim", "old")
        rec = load_record(tmp_tracking_dir / "wt-claim.yaml")
        tracking.open_handoff(rec, "old", "task-456")

        recorded: list[tuple[str, dict]] = []
        real_log_event = activity.log_event

        def _capture(event, **kwargs):
            recorded.append((event, kwargs))
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _capture)

        rc = m.cmd_register_session(_args(
            worktree_id="wt-claim", session_id="new",
            handoff_token="task-456", launch_id="flow-10",
        ))

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-claim.yaml")
        assert rec.resolved_head_session == "new"
        assert rec.handoffs[0].state == "linked"

        claimed = [kw for ev, kw in recorded if ev == "handoff_successor_claimed"]
        closing = [
            kw for ev, kw in recorded
            if ev == "handoff_pickup_confirmed_predecessor_closing"
        ]
        assert len(claimed) == 1
        assert claimed[0]["worktree_id"] == "wt-claim"
        assert claimed[0]["session_id"] == "new"
        assert claimed[0]["handoff_token"] == "task-456"
        assert claimed[0]["predecessor_session_id"] == "old"
        assert claimed[0]["launch_id"] == "flow-10"
        assert len(closing) == 1
        assert closing[0]["worktree_id"] == "wt-claim"
        assert closing[0]["session_id"] == "old"
        assert closing[0]["successor_session_id"] == "new"
        assert closing[0]["handoff_token"] == "task-456"
        assert closing[0]["launch_id"] == "flow-10"

    def test_re_registering_a_linked_handoff_does_not_re_emit_stage_10_11(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """A second sessionStart hook call for the same already-linked
        successor (e.g. a resumed session) must not re-emit Stage 10/11 --
        link_handoff() is idempotent and so must the derived trace be."""
        _save_record(tmp_tracking_dir, "wt-dup", "/tmp/src/wt-dup")
        tracking.register_session("wt-dup", "old")
        rec = load_record(tmp_tracking_dir / "wt-dup.yaml")
        tracking.open_handoff(rec, "old", "task-789")
        rc = m.cmd_register_session(_args(
            worktree_id="wt-dup", session_id="new", handoff_token="task-789",
        ))
        assert rc == 0

        recorded: list[str] = []
        real_log_event = activity.log_event

        def _capture(event, **kwargs):
            recorded.append(event)
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _capture)

        rc = m.cmd_register_session(_args(
            worktree_id="wt-dup", session_id="new", handoff_token="task-789",
        ))

        assert rc == 0
        assert "handoff_successor_claimed" not in recorded
        assert "handoff_pickup_confirmed_predecessor_closing" not in recorded

    def test_claim_after_a_confirmed_predecessor_retire_emits_stage_10_and_13(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 review finding: the resident monitor's own retire flow can
        already have stamped Stage 11 (`handoff_predecessor_retire`,
        outcome="gone") for this token BEFORE the successor's own
        `--handoff-token` claim runs (retirement is gated on candidate
        presence, not on this link). In that case this call site must emit
        Stage 10 only (a second Stage 11 record for the same handoff would be
        a duplicate) -- but since BOTH confirmations (pane gone + head
        transferred) now exist, Stage 13 (`handoff_complete`) must fire."""
        _save_record(tmp_tracking_dir, "wt-already-retired", "/tmp/src/wt-ar")
        tracking.register_session("wt-already-retired", "old")
        rec = load_record(tmp_tracking_dir / "wt-already-retired.yaml")
        tracking.open_handoff(rec, "old", "task-gone")
        activity.log_event(
            "handoff_predecessor_retire", worktree_id="wt-already-retired",
            session_id="old", handoff_token="task-gone", outcome="gone",
        )

        recorded: list[tuple[str, dict]] = []
        real_log_event = activity.log_event

        def _capture(event, **kwargs):
            recorded.append((event, kwargs))
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _capture)

        rc = m.cmd_register_session(_args(
            worktree_id="wt-already-retired", session_id="new",
            handoff_token="task-gone", launch_id="flow-13",
        ))

        assert rc == 0
        events = [ev for ev, _ in recorded]
        assert events.count("handoff_successor_claimed") == 1
        assert "handoff_pickup_confirmed_predecessor_closing" not in events
        complete = [kw for ev, kw in recorded if ev == "handoff_complete"]
        assert len(complete) == 1
        assert complete[0]["launch_id"] == "flow-13"
        assert complete[0]["worktree_id"] == "wt-already-retired"
        assert complete[0]["session_id"] == "old"
        assert complete[0]["successor_session_id"] == "new"
        assert complete[0]["handoff_token"] == "task-gone"

    def test_stage_13_does_not_fire_when_only_the_pane_is_confirmed_gone(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """A confirmed predecessor retire alone -- with the handoff still only
        a candidate, never actually linked -- must NOT emit Stage 13; the
        successor has to be authoritative head, not merely a candidate."""
        _save_record(tmp_tracking_dir, "wt-candidate-only", "/tmp/src/wt-co")
        tracking.register_session("wt-candidate-only", "old")
        rec = load_record(tmp_tracking_dir / "wt-candidate-only.yaml")
        tracking.open_handoff(rec, "old", "task-cand")
        tracking.register_session(
            "wt-candidate-only", "new", candidate_token="task-cand",
        )
        rec = load_record(tmp_tracking_dir / "wt-candidate-only.yaml")
        tracking.associate_handoff_candidate(rec, "task-cand", "new")
        activity.log_event(
            "handoff_predecessor_retire", worktree_id="wt-candidate-only",
            session_id="old", handoff_token="task-cand", outcome="gone",
        )

        m._maybe_emit_stage_13("wt-candidate-only", "task-cand")

        assert activity.read_events(
            worktree_id="wt-candidate-only", event="handoff_complete",
        ) == []

    def test_stage_13_fires_on_the_retire_side_when_claim_already_happened(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """The reverse ordering: the successor already claimed the head
        (Stage 10/11 already recorded) before the predecessor's pane is
        confirmed gone. Stage 13 must fire from the retire side in that
        case, exactly once."""
        _save_record(tmp_tracking_dir, "wt-claim-first", "/tmp/src/wt-cf")
        tracking.register_session("wt-claim-first", "old")
        rec = load_record(tmp_tracking_dir / "wt-claim-first.yaml")
        tracking.open_handoff(rec, "old", "task-claim-first")
        m.cmd_register_session(_args(
            worktree_id="wt-claim-first", session_id="new",
            handoff_token="task-claim-first",
        ))
        assert activity.read_events(
            worktree_id="wt-claim-first", event="handoff_complete",
        ) == []

        activity.log_event(
            "handoff_predecessor_retire", worktree_id="wt-claim-first",
            session_id="old", handoff_token="task-claim-first", outcome="gone",
        )
        m._maybe_emit_stage_13("wt-claim-first", "task-claim-first")
        m._maybe_emit_stage_13("wt-claim-first", "task-claim-first")

        complete = activity.read_events(
            worktree_id="wt-claim-first", event="handoff_complete",
        )
        assert len(complete) == 1
        assert complete[0]["session_id"] == "old"
        assert complete[0]["successor_session_id"] == "new"

    def test_stage_13_claim_is_atomic_against_a_concurrent_double_call(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 review finding: the successor's sessionStart process and the
        resident monitor's retire process run concurrently and could both
        pass a plain read_events() check before either logs, double-recording
        Stage 13. The dedup must be an atomic exclusive-create claim file, not
        a read-before-write check -- run real concurrent callers (synchronized
        with a barrier so they race on the SAME instant, not sequentially) and
        confirm only one wins. A sequential-call test would pass even against
        the old read-before-write implementation and prove nothing about the
        actual race."""
        import threading

        _save_record(tmp_tracking_dir, "wt-race", "/tmp/src/wt-race")
        tracking.register_session("wt-race", "old")
        rec = load_record(tmp_tracking_dir / "wt-race.yaml")
        tracking.open_handoff(rec, "old", "task-race")
        m.cmd_register_session(_args(
            worktree_id="wt-race", session_id="new", handoff_token="task-race",
        ))
        activity.log_event(
            "handoff_predecessor_retire", worktree_id="wt-race",
            session_id="old", handoff_token="task-race", outcome="gone",
        )

        n_threads = 8
        barrier = threading.Barrier(n_threads)

        def _racer():
            barrier.wait()
            m._maybe_emit_stage_13("wt-race", "task-race")

        threads = [threading.Thread(target=_racer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        complete = activity.read_events(worktree_id="wt-race", event="handoff_complete")
        assert len(complete) == 1

    def test_stage_13_claim_keys_are_collision_resistant(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 review finding: the claim key must not be derived from the
        lossy character-sanitized `_monitor_handoff_claim_segment()` (which
        maps distinct tokens like "task:1" and "task_1" onto the same on-disk
        name) -- two DIFFERENT tokens that collide under that sanitization
        must each still get their own Stage 13 record."""
        _save_record(tmp_tracking_dir, "wt-collide", "/tmp/src/wt-collide")
        tracking.register_session("wt-collide", "old-a")
        tracking.register_session("wt-collide", "old-b")
        rec = load_record(tmp_tracking_dir / "wt-collide.yaml")
        tracking.open_handoff(rec, "old-a", "task:1")
        rec = load_record(tmp_tracking_dir / "wt-collide.yaml")
        tracking.open_handoff(rec, "old-b", "task_1")
        m.cmd_register_session(_args(
            worktree_id="wt-collide", session_id="new-a", handoff_token="task:1",
        ))
        m.cmd_register_session(_args(
            worktree_id="wt-collide", session_id="new-b", handoff_token="task_1",
        ))
        for predecessor, token in (("old-a", "task:1"), ("old-b", "task_1")):
            activity.log_event(
                "handoff_predecessor_retire", worktree_id="wt-collide",
                session_id=predecessor, handoff_token=token, outcome="gone",
            )

        m._maybe_emit_stage_13("wt-collide", "task:1")
        m._maybe_emit_stage_13("wt-collide", "task_1")

        complete = activity.read_events(worktree_id="wt-collide", event="handoff_complete")
        assert {c["handoff_token"] for c in complete} == {"task:1", "task_1"}

    def test_stage_13_claim_is_retryable_after_a_detected_log_failure(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 review finding: `open(..., "x")` must not permanently consume
        the claim before the event is known to be durable -- if
        `activity.log_event_failure_count()` shows the write was dropped, the
        claim must be rolled back so a later retry can still succeed."""
        _save_record(tmp_tracking_dir, "wt-retryable", "/tmp/src/wt-retryable")
        tracking.register_session("wt-retryable", "old")
        rec = load_record(tmp_tracking_dir / "wt-retryable.yaml")
        tracking.open_handoff(rec, "old", "task-retryable")
        m.cmd_register_session(_args(
            worktree_id="wt-retryable", session_id="new",
            handoff_token="task-retryable",
        ))
        activity.log_event(
            "handoff_predecessor_retire", worktree_id="wt-retryable",
            session_id="old", handoff_token="task-retryable", outcome="gone",
        )

        real_log_event = activity.log_event
        real_count = activity.log_event_failure_count()
        count_holder = {"n": real_count}

        def _dropping_log_event(event, **kwargs):
            if event == "handoff_complete":
                count_holder["n"] += 1  # simulate a swallowed logger I/O failure
                return
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _dropping_log_event)
        monkeypatch.setattr(activity, "log_event_failure_count", lambda: count_holder["n"])

        m._maybe_emit_stage_13("wt-retryable", "task-retryable")
        assert activity.read_events(
            worktree_id="wt-retryable", event="handoff_complete",
        ) == []

        monkeypatch.setattr(activity, "log_event", real_log_event)
        monkeypatch.setattr(activity, "log_event_failure_count", lambda: real_count)
        m._maybe_emit_stage_13("wt-retryable", "task-retryable")

        complete = activity.read_events(
            worktree_id="wt-retryable", event="handoff_complete",
        )
        assert len(complete) == 1

    def test_a_losing_claimant_retries_after_the_winners_rollback(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 review finding (HIGH): a losing caller must not simply give
        up on `FileExistsError` -- if the current holder's log write fails and
        it rolls its claim back, that rollback can happen between the loser's
        existence check and its own return, silently losing Stage 13 forever
        unless the loser gets a chance to retry. Simulate the holder's claim
        vanishing (the rollback) mid-window and confirm the losing caller
        still succeeds."""
        import threading

        _save_record(tmp_tracking_dir, "wt-loser-retry", "/tmp/src/wt-lr")
        tracking.register_session("wt-loser-retry", "old")
        rec = load_record(tmp_tracking_dir / "wt-loser-retry.yaml")
        tracking.open_handoff(rec, "old", "task-loser-retry")
        m.cmd_register_session(_args(
            worktree_id="wt-loser-retry", session_id="new",
            handoff_token="task-loser-retry",
        ))
        activity.log_event(
            "handoff_predecessor_retire", worktree_id="wt-loser-retry",
            session_id="old", handoff_token="task-loser-retry", outcome="gone",
        )

        digest = m.hashlib.sha256(b"wt-loser-retry\x00task-loser-retry").hexdigest()
        claim_path = m._monitor_handoff_claim_root() / "stage13" / f"{digest}.json"
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        claim_path.touch()  # simulate another caller already holding the claim

        def _drop_claim_after_a_beat():
            import time as _time
            _time.sleep(m._STAGE13_CLAIM_RETRY_DELAY_S * 0.3)
            claim_path.unlink()  # simulate the holder's rollback

        threading.Thread(target=_drop_claim_after_a_beat).start()

        m._maybe_emit_stage_13("wt-loser-retry", "task-loser-retry")

        complete = activity.read_events(
            worktree_id="wt-loser-retry", event="handoff_complete",
        )
        assert len(complete) == 1

    def test_resolves_worktree_from_stdin_cwd(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-x", "/tmp/src/wt-x")
        payload = '{"sessionId":"sess-1","cwd":"/tmp/src/wt-x/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))
        assert rc == 0

        rec = load_record(tmp_tracking_dir / "wt-x.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-1"]

    def test_only_new_session_source_requests_complete_projection_creation(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-x", "/tmp/src/wt-x")
        original = m.tracking.register_session
        observed: list[bool] = []

        def capture(*args, **kwargs):
            observed.append(kwargs["initial_projection"])
            return original(*args, **kwargs)

        monkeypatch.setattr(m.tracking, "register_session", capture)
        for source in ("new", "resume"):
            payload = json.dumps({
                "sessionId": f"sess-{source}",
                "cwd": "/tmp/src/wt-x/sub",
                "source": source,
            })
            monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))
            assert m.cmd_register_session(_args(stdin=True)) == 0

        assert observed == [True, False]

    def test_adopts_host_created_linked_worktree(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        anchor, foreign = _repo_with_worktree(tmp_path)
        config = cfg.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="test-project",
            repos={
                "test-project": cfg.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "anchor.worktrees"),
                    default_branch="master",
                )
            },
        )
        monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
        monkeypatch.setattr(m, "_activate_project_for_path", lambda *_a, **_k: "test")
        m.cfg.set_active_project(config.repo_name)
        payload = json.dumps({
            "sessionId": "app-session-id",
            "cwd": str(foreign / "subdir"),
            "source": "github-app",
        })
        (foreign / "subdir").mkdir()
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        record = load_record(tmp_tracking_dir / "app-session.yaml")
        assert Path(record.worktree_path).resolve() == foreign.resolve()
        assert record.branch == "app-session"
        assert record.repo == config.repo_name
        assert record.interface == "cli"
        assert record.origin == "user"
        assert record.checkout_managed is False
        assert [entry.session_id for entry in record.sessions] == ["app-session-id"]

    def test_does_not_adopt_worktree_from_different_anchor(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        configured_anchor, _ = _repo_with_worktree(tmp_path, "configured")
        _other_anchor, foreign = _repo_with_worktree(tmp_path, "other")
        config = cfg.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="test-project",
            repos={
                "test-project": cfg.RepoConfig(
                    anchor=str(configured_anchor),
                    worktree_root=str(tmp_path / "configured.worktrees"),
                )
            },
        )
        monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
        m.cfg.set_active_project(config.repo_name)

        assert m._adopt_linked_worktree(foreign) is None
        assert not (tmp_tracking_dir / "app-session.yaml").exists()

    def test_does_not_adopt_colliding_worktree_id(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        anchor, foreign = _repo_with_worktree(tmp_path)
        config = cfg.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="test-project",
            repos={
                "test-project": cfg.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "anchor.worktrees"),
                )
            },
        )
        _save_record(
            tmp_tracking_dir, "app-session", str(tmp_path / "different-path")
        )
        monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
        m.cfg.set_active_project(config.repo_name)

        assert m._adopt_linked_worktree(foreign) is None
        record = load_record(tmp_tracking_dir / "app-session.yaml")
        assert Path(record.worktree_path) == tmp_path / "different-path"

    def test_does_not_adopt_detached_worktree(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        anchor, _foreign = _repo_with_worktree(tmp_path)
        detached = tmp_path / "detached-worktree"
        git_ops.git("worktree", "add", "--detach", str(detached), "master", cwd=anchor)
        config = cfg.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="linux",
            repo_name="test-project",
            repos={
                "test-project": cfg.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "anchor.worktrees"),
                )
            },
        )
        monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)
        m.cfg.set_active_project(config.repo_name)

        assert m._adopt_linked_worktree(detached) is None
        assert not (tmp_tracking_dir / "detached-worktree.yaml").exists()

    def test_explicit_worktree_id_takes_precedence(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-y", "/tmp/src/wt-y")
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))
        rc = m.cmd_register_session(
            _args(worktree_id="wt-y", session_id="sess-2", stdin=True)
        )
        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-y.yaml")
        assert [s.session_id for s in rec.sessions] == ["sess-2"]

    def test_records_pane_from_explicit_arg(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-pane", "/tmp/src/wt-pane")
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))

        rc = m.cmd_register_session(
            _args(worktree_id="wt-pane", session_id="sess-pane", pane="%12")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-pane.yaml")
        assert rec.sessions is not None
        assert rec.sessions[0].pane_id == "%12"

    def test_records_pane_from_mux_env(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-env", "/tmp/src/wt-env")
        monkeypatch.setenv("TMUX_PANE", "%13")
        monkeypatch.delenv("PSMUX_PANE", raising=False)
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))

        rc = m.cmd_register_session(
            _args(worktree_id="wt-env", session_id="sess-env")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-env.yaml")
        assert rec.sessions is not None
        assert rec.sessions[0].pane_id == "%13"

    def test_reregistration_updates_existing_pane(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-update", "/tmp/src/wt-update")
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))
        m.cmd_register_session(
            _args(worktree_id="wt-update", session_id="sess-update", pane="%1")
        )

        rc = m.cmd_register_session(
            _args(worktree_id="wt-update", session_id="sess-update", pane="%2")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-update.yaml")
        assert rec.sessions is not None
        assert len(rec.sessions) == 1
        assert rec.sessions[0].pane_id == "%2"

    def test_unknown_cwd_is_silent_noop(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-z", "/tmp/src/wt-z")
        payload = '{"sessionId":"sess-3","cwd":"/tmp/unrelated"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))
        rc = m.cmd_register_session(_args(stdin=True))
        assert rc == 0  # silent no-op, never an error
        rec = load_record(tmp_tracking_dir / "wt-z.yaml")
        assert rec.sessions == []

    def test_unknown_cwd_still_ensures_resident_monitor(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
        _save_record(tmp_tracking_dir, "wt-z", "/tmp/src/wt-z")
        payload = '{"sessionId":"sess-3","cwd":"/tmp/unrelated"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))
        ensured: list[bool] = []
        monkeypatch.setattr(
            m, "_ensure_status_monitor", lambda: ensured.append(True) or True)

        assert m.cmd_register_session(_args(stdin=True)) == 0
        assert ensured == [True]

    def test_no_session_id_is_silent_noop(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))
        monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
        rc = m.cmd_register_session(_args(worktree_id="wt-none", stdin=True))
        assert rc == 0


def test_register_session_is_a_no_project_command():
    """main() must not balk before dispatch.

    The Copilot CLI runs plugin hooks from the *plugin install dir*, not a
    worktree, so register-session cannot require CWD-based project resolution
    -- otherwise main() balks (cmd_help_unrouted) and the handler never runs,
    leaving sessions[] empty (the #662 regression).  It resolves its own
    project from the payload cwd instead.
    """
    assert "register-session" in m._NO_PROJECT_COMMANDS


class TestRegisterSessionProjectResolution:
    """The handler resolves project context from the payload cwd itself, since
    it is a no-project command (main() sets no active project for it)."""

    def test_activates_project_from_payload_cwd(self, monkeypatch):
        seen: list[tuple[str | None, bool]] = []
        monkeypatch.setattr(
            m,
            "_activate_project_for_path",
            lambda c, *, force=False: seen.append((c, force)),
        )
        # Lookup returns None -> silent no-op; we only assert the activation.
        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda c: None)
        payload = '{"sessionId":"s","cwd":"/tmp/src/wt-x/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        assert seen == [("/tmp/src/wt-x/sub", True)]

    def test_lookup_error_is_silent_noop(self, monkeypatch):
        """A cwd outside any adopted project makes find_worktree_id_by_cwd
        raise (cfg.tracking_dir() -> project_name() RuntimeError).  The handler
        must swallow it and stay a silent no-op, never surfacing an error."""
        monkeypatch.setattr(
            m, "_activate_project_for_path", lambda c, *, force=False: None
        )

        def boom(_c):
            raise RuntimeError("no active project")

        monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", boom)
        payload = '{"sessionId":"s","cwd":"/tmp/outside"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0


class TestBareResumeSessionBinding:
    def _set_binding(self, monkeypatch, session_id="target-session"):
        monkeypatch.setenv(m._SESSION_BIND_PROJECT, "test-project")
        monkeypatch.setenv(m._SESSION_BIND_WORKTREE, "wt-bound")
        monkeypatch.setenv(m._SESSION_BIND_SESSION, session_id)
        monkeypatch.setattr(
            m, "_resolve_active_project",
            lambda project: (project, Path("/tmp/project")),
        )

    def test_target_resume_binds_even_when_payload_cwd_is_home(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-bound", "/tmp/src/wt-bound")
        self._set_binding(monkeypatch)
        monkeypatch.setattr(
            m.sys, "stdin",
            io.StringIO('{"sessionId":"target-session","cwd":"/home/user"}'),
        )

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-bound.yaml")
        assert [s.session_id for s in rec.sessions] == ["target-session"]

    def test_temporary_home_session_does_not_consume_binding(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-bound", "/tmp/src/wt-bound")
        self._set_binding(monkeypatch)
        monkeypatch.setattr(
            m.sys, "stdin",
            io.StringIO('{"sessionId":"temporary-session","cwd":"/home/user"}'),
        )

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-bound.yaml")
        assert rec.sessions == []

    def test_session_end_uses_matching_binding(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-bound", "/tmp/src/wt-bound")
        self._set_binding(monkeypatch)
        m.tracking.register_session("wt-bound", "target-session")
        monkeypatch.setattr(m, "_capture_session_title", lambda *_: True)

        rc = m.cmd_deregister_session(
            argparse.Namespace(worktree_id=None, session_id="target-session")
        )

        assert rc == 0
        rec = load_record(tmp_tracking_dir / "wt-bound.yaml")
        assert rec.sessions[0].ended_at is not None


class TestMuxRecoveredSessionBinding:
    def test_home_cwd_recovers_worktree_pane_and_pid(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-mux", "/tmp/src/wt-mux")
        monkeypatch.setattr(
            m.sys,
            "stdin",
            io.StringIO('{"sessionId":"sess-mux","cwd":"/home/user"}'),
        )
        monkeypatch.setattr(
            m, "_activate_project_for_path", lambda _cwd, *, force=False: None
        )
        monkeypatch.setattr(
            m.tracking, "find_worktree_id_by_cwd", lambda _cwd: None
        )
        monkeypatch.setattr(
            m.sessions,
            "mux_binding_for_session",
            lambda _sid: {
                "worktree_id": "wt-mux",
                "session_name": "wt-wt-mux",
                "pane_id": "%17",
                "pane_pid": 200,
                "copilot_pid": 300,
            },
        )
        monkeypatch.setattr(
            m, "_activate_project_for_worktree_id", lambda _wt: "test-project"
        )
        monkeypatch.delenv("TMUX_PANE", raising=False)
        monkeypatch.delenv("PSMUX_PANE", raising=False)

        assert m.cmd_register_session(_args(stdin=True)) == 0

        rec = load_record(tmp_tracking_dir / "wt-mux.yaml")
        assert rec.sessions[0].session_id == "sess-mux"
        assert rec.sessions[0].pane_id == "%17"
        assert rec.sessions[0].pid == 300

    def test_emits_authoritative_mux_context(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch, capfd
    ):
        _save_record(tmp_tracking_dir, "wt-mux", "/tmp/src/wt-mux")
        monkeypatch.setattr(
            m.sys,
            "stdin",
            io.StringIO('{"sessionId":"sess-mux","cwd":"/home/user"}'),
        )
        monkeypatch.setattr(
            m, "_activate_project_for_path", lambda _cwd, *, force=False: None
        )
        monkeypatch.setattr(
            m.tracking, "find_worktree_id_by_cwd", lambda _cwd: None
        )
        monkeypatch.setattr(
            m.sessions,
            "mux_binding_for_session",
            lambda _sid: {
                "worktree_id": "wt-mux",
                "session_name": "wt-wt-mux",
                "pane_id": "%17",
                "pane_pid": 200,
                "copilot_pid": 300,
            },
        )
        monkeypatch.setattr(
            m, "_activate_project_for_worktree_id", lambda _wt: "test-project"
        )
        monkeypatch.delenv("TMUX_PANE", raising=False)
        monkeypatch.delenv("PSMUX_PANE", raising=False)

        assert m.cmd_register_session(
            _args(stdin=True, emit_context=True)
        ) == 0

        out = json.loads(capfd.readouterr().out)
        context = out["additionalContext"]
        assert "inside mux session wt-wt-mux, pane %17" in context
        assert "worktree id is wt-mux" in context
        assert "run task commands from /tmp/src/wt-mux" in context


class TestActivateProjectForWorktree:
    def test_activates_unique_owning_project(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "projects"
        record = root / "alpha" / "worktrees" / "wt-owned.yaml"
        record.parent.mkdir(parents=True)
        record.write_text("worktree_id: wt-owned\n", encoding="utf-8")
        monkeypatch.setattr(
            m.inst,
            "read_projects_registry",
            lambda: {"projects": {"alpha": {}, "beta": {}}},
        )
        monkeypatch.setattr(
            m.cfg, "project_dir", lambda name=None: root / str(name)
        )
        m.cfg.set_active_project(None)

        assert m._activate_project_for_worktree_id("wt-owned") == "alpha"
        assert m.cfg.active_project() == "alpha"

    def test_ambiguous_worktree_id_fails_closed(
        self, tmp_path: Path, monkeypatch
    ):
        root = tmp_path / "projects"
        for project in ("alpha", "beta"):
            record = root / project / "worktrees" / "wt-duplicate.yaml"
            record.parent.mkdir(parents=True)
            record.write_text("worktree_id: wt-duplicate\n", encoding="utf-8")
        monkeypatch.setattr(
            m.inst,
            "read_projects_registry",
            lambda: {"projects": {"alpha": {}, "beta": {}}},
        )
        monkeypatch.setattr(
            m.cfg, "project_dir", lambda name=None: root / str(name)
        )
        m.cfg.set_active_project(None)

        assert m._activate_project_for_worktree_id("wt-duplicate") is None
        assert m.cfg.active_project() is None


def test_deregister_session_worktree_is_optional_for_hook_inference():
    args = m.build_parser().parse_args(
        ["deregister-session", "--session-id", "session-1"]
    )
    assert args.worktree_id is None


class TestDeregisterSessionStdin:
    def test_resident_binding_uses_only_transported_origin_environment(self, monkeypatch):
        from agent_worktrees import session_binding_cli

        keys = (
            session_binding_cli._session_bind_session_key(),
            session_binding_cli._session_bind_worktree_key(),
            session_binding_cli._session_bind_project_key(),
        )
        for key, value in zip(keys, ("native-session", "stale-worktree", "stale-project")):
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            session_binding_cli, "_resolve_active_project", lambda project: (project, None),
        )
        assert session_binding_cli._activate_hook_session_binding(
            "native-session", {}, resident=True,
        ) is None
        origin = dict(zip(keys, ("native-session", "original-worktree", "original-project")))
        assert session_binding_cli._activate_hook_session_binding(
            "native-session", {"_agentWorktrees": {"environment": origin}}, resident=True,
        ) == "original-worktree"

    def test_resident_old_source_end_preserves_current_same_uuid_activation(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch,
    ):
        from agent_worktrees import native_source_context, obligations, session_binding_cli

        sid = "00000000-0000-4000-8000-000000000001"
        epoch = {"platform": "linux", "environment_id": "a" * 32, "boot_id": sid}
        token = native_source_context.identity(sid, 101, "100", epoch)
        monkeypatch.setattr(native_source_context, "epoch", lambda: epoch)
        monkeypatch.setattr(
            native_source_context, "capture",
            lambda: pytest.fail("resident must use original transported ancestry"),
        )
        _save_record(tmp_tracking_dir, "wt-end", "/tmp/src/wt-end")
        m.tracking.register_session(
            "wt-end", sid, started_at="old", source_identity=token,
        )
        m.tracking.register_session(
            "wt-end", sid, started_at="current", source_identity="native:current",
        )
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "wrong-resident-session")
        monkeypatch.setattr(
            session_binding_cli, "_capture_session_title",
            lambda *_: pytest.fail("historical End title write"),
        )
        holder = []
        args = argparse.Namespace(
            worktree_id="wt-end", session_id=None, cwd=None, stdin=False,
            resident_environment=True, source_bound=True, result_holder=holder,
            hook_payload={
                "sessionId": sid, "reason": "user_exit",
                "timestamp": "2026-08-27T11:00:00Z",
                "_agentWorktrees": {"nativeSource": {
                    "version": 1, "qualified": True, **epoch,
                    "ancestors": [{"pid": 101, "start_time": "100"}],
                }},
            },
        )
        assert m.cmd_deregister_session(args) == 0
        assert holder[-1]["lifecycle"] == "historical"
        assert m.cmd_deregister_session(args) == 0
        assert holder[-1]["lifecycle"] == "duplicate"
        record = load_record(tmp_tracking_dir / "wt-end.yaml")
        entry = record.session_entry(sid)
        assert entry.ended_at is None
        assert entry.activations[-1].ended_at is None
        assert entry.activations[0].end_reason == "user_exit"
        ref = m.tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id, session=sid,
        )
        assert next(item for item in record.resources if item.ref == ref).state == obligations.ACTIVE

    def test_warm_end_uses_original_binding_with_same_uuid_in_two_records(
        self, tmp_path, tmp_tracking_dir, monkeypatch_config, monkeypatch,
    ):
        import importlib.util
        from types import SimpleNamespace
        from agent_worktrees import native_source_context, obligations, session_binding_cli
        from agent_worktrees.hook_ipc import HookIpcServer

        sid = "00000000-0000-4000-8000-000000000001"
        epoch = {"platform": "linux", "environment_id": "a" * 32, "boot_id": sid}
        origin = {
            "version": 1, "qualified": True, **epoch,
            "ancestors": [{"pid": 101, "start_time": "100"}],
        }
        monkeypatch.setattr(native_source_context, "epoch", lambda: epoch)
        execution_platform = cfg.detect_platform()
        monkeypatch.setattr(cfg, "detect_platform", lambda: execution_platform)
        monkeypatch.setattr(m.sys, "platform", "linux")
        monkeypatch.setattr(session_binding_cli, "_resolve_active_project", lambda project: (project, None))
        for wt_id, source in (
            ("aaa-other", "native:other"),
            ("zzz-bound", native_source_context.identity(sid, 101, "100", epoch)),
        ):
            _save_record(tmp_tracking_dir, wt_id, f"/path/to/{wt_id}")
            tracking.register_session(wt_id, sid, source_identity=source)
        unrelated = tmp_tracking_dir / "aaa-other.yaml"
        before = unrelated.read_bytes()
        for key, value in (
            (session_binding_cli._session_bind_session_key(), sid),
            (session_binding_cli._session_bind_worktree_key(), "zzz-bound"),
            (session_binding_cli._session_bind_project_key(), "test-repo"),
        ):
            monkeypatch.setenv(key, value)
        script = Path(__file__).resolve().parents[1] / "scripts" / "hook_client.py"
        spec = importlib.util.spec_from_file_location("bound_end_client", script)
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
        monkeypatch.setattr(client, "_capture_native_source", lambda: origin)
        monkeypatch.setattr(client, "_probe_terminal", lambda: pytest.fail("End terminal probe"))
        server = HookIpcServer(lambda kind, payload, deadline: m._resident_hook_decision(
            kind, payload, segment_cache=SimpleNamespace(), policy=SimpleNamespace(),
        ))
        server.start()
        try:
            runtime = tmp_path / ".agent-worktrees"
            runtime.mkdir()
            (runtime / "status-monitor.lock").write_text(json.dumps(server.rendezvous()))
            result = client.decide("sessionEnd", {
                "sessionId": sid, "reason": "user_exit", "cwd": str(tmp_path / "outside"),
            }, home=tmp_path)
        finally:
            server.close()
        assert result["lifecycle"] == "ended"
        assert unrelated.read_bytes() == before
        record = tracking.load_record(tmp_tracking_dir / "zzz-bound.yaml")
        assert record.sessions[0].ended_at is not None
        assert next(item for item in record.resources if item.kind == "session").state == obligations.RELEASED

    @pytest.mark.parametrize("resident", [False, True])
    @pytest.mark.parametrize("source_identity", [None, "native:qualified"])
    def test_source_aware_end_defers_legacy_and_bound_authority_to_writer(
        self, tmp_tracking_dir, monkeypatch_config, monkeypatch, resident, source_identity,
    ):
        from agent_worktrees import native_source_context, obligations, tracking_session_deregistration_write

        sid = "00000000-0000-4000-8000-000000000001"
        epoch = {"platform": "linux", "environment_id": "a" * 32, "boot_id": sid}
        monkeypatch.setattr(native_source_context, "epoch", lambda: epoch)
        _save_record(tmp_tracking_dir, "wt-legacy-end", "/path/to/worktree")
        tracking.register_session(
            "wt-legacy-end", sid, started_at="2026-10-19T10:00:00",
            source_identity=source_identity,
        )
        path = tmp_tracking_dir / "wt-legacy-end.yaml"
        before = path.read_bytes()
        stops = []
        monkeypatch.setattr(tracking_session_deregistration_write, "stop_fsmonitor_daemon", stops.append)
        holder = []
        assert m.cmd_deregister_session(argparse.Namespace(
            worktree_id="wt-legacy-end", session_id=None, cwd=None, stdin=False,
            resident_environment=resident, source_bound=True, result_holder=holder,
            hook_payload={
                "sessionId": sid, "reason": "user_exit", "timestamp": "2026-10-19T11:00:00Z",
                "_agentWorktrees": {"nativeSource": {
                    "version": 1, "qualified": True, **epoch,
                    "ancestors": [{"pid": 101, "start_time": "100"}],
                }},
            },
        )) == 0
        record = tracking.load_record(path)
        entry = record.session_entry(sid)
        claim = next(item for item in record.resources if item.kind == "session")
        if source_identity is None:
            assert entry.ended_at is not None
            assert entry.activations[0].source_identity is None
            assert entry.activations[0].end_reason is None
            assert claim.state == obligations.RELEASED
            assert stops == [record.worktree_path]
        else:
            assert holder[-1]["lifecycle"] == "unconfirmed"
            assert path.read_bytes() == before
            assert claim.state == obligations.ACTIVE
            assert stops == []

    @pytest.mark.parametrize("replacement_at", [None, "before-read", "after-read"])
    def test_title_persistence_revalidates_same_uuid_ended_activation(
        self, tmp_tracking_dir, tmp_session_state_dir, monkeypatch_config,
        monkeypatch, replacement_at,
    ):
        from agent_worktrees import native_lifecycle_source, obligations, session_binding_cli, session_title

        sid = "00000000-0000-4000-8000-000000000001"
        wt_id = "wt-title-fence"
        _save_record(tmp_tracking_dir, wt_id, "/path/to/worktree")
        tracking.register_session(
            wt_id, sid, started_at="2026-10-19T10:00:00", source_identity="native:original",
        )
        session_dir = tmp_session_state_dir / sid
        session_dir.mkdir()
        workspace = session_dir / "workspace.yaml"
        workspace.write_text("summary: Original execution title\n")
        monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_session_state_dir)
        monkeypatch.setattr(native_lifecycle_source, "end_identity", lambda *_: "native:original")
        original_capture = session_title.capture_session_title
        original_read = session_title.yaml.safe_load

        def replace():
            tracking.register_session(
                wt_id, sid, started_at="2026-10-19T12:00:00",
                source_identity="native:replacement",
            )
            workspace.write_text("summary: Replacement execution title\n")

        def capture(worktree_id, session_id, **kwargs):
            if replacement_at == "before-read":
                replace()
            return original_capture(worktree_id, session_id, **kwargs)

        def read(stream):
            data = original_read(stream)
            if replacement_at == "after-read" and getattr(stream, "name", None) == str(workspace):
                replace()
            return data

        monkeypatch.setattr(session_binding_cli, "_capture_session_title", capture)
        monkeypatch.setattr(session_title.yaml, "safe_load", read)
        holder = []
        assert m.cmd_deregister_session(argparse.Namespace(
            worktree_id=wt_id, session_id=None, cwd=None, stdin=False,
            resident_environment=True, source_bound=True, result_holder=holder,
            hook_payload={"sessionId": sid, "reason": "user_exit", "timestamp": "2026-10-19T11:00:00Z"},
        )) == 0
        assert holder[-1]["lifecycle"] == "ended"
        record = tracking.load_record(tmp_tracking_dir / f"{wt_id}.yaml")
        ref = tracking.format_claim_ref(record.machine, record.repo, wt_id, session=sid)
        claim = next(item for item in record.resources if item.ref == ref)
        if replacement_at is None:
            assert record.title == "Original execution title"
            assert claim.state == obligations.RELEASED
        else:
            assert record.title is None
            assert record.sessions[0].activations[-1].source_identity == "native:replacement"
            assert record.sessions[0].activations[-1].ended_at is None
            assert claim.state == obligations.ACTIVE

    def test_session_end_emits_stage_12_via_the_existing_session_ended_event(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """#2457 Stage 12: no new hook is needed -- the sessionEnd hook's
        existing `session_ended` event is already mapped to Stage 12
        (`session_end_bound`) by Phase 1's HANDOFF_STAGE_MAP, and
        `cmd_deregister_session` already emits it unconditionally. Confirm the
        real emission carries the stage stamp end-to-end (not just the map
        entry in isolation)."""
        _save_record(tmp_tracking_dir, "wt-stage12", "/tmp/src/wt-stage12")
        m.tracking.register_session("wt-stage12", "session-12")

        recorded: list[tuple[str, dict]] = []
        real_log_event = activity.log_event

        def _capture(event, **kwargs):
            recorded.append((event, kwargs))
            return real_log_event(event, **kwargs)

        monkeypatch.setattr(activity, "log_event", _capture)

        args = argparse.Namespace(
            worktree_id="wt-stage12",
            session_id="session-12",
            cwd=None,
            stdin=False,
            launch_id="flow-12",
        )

        assert m.cmd_deregister_session(args) == 0

        matches = [kw for ev, kw in recorded if ev == "session_ended"]
        assert len(matches) == 1
        assert matches[0]["worktree_id"] == "wt-stage12"
        assert matches[0]["session_id"] == "session-12"
        assert matches[0]["launch_id"] == "flow-12"
        logged = [
            r for r in activity.read_events(worktree_id="wt-stage12", event="session_ended")
        ]
        assert len(logged) == 1
        assert logged[0]["stage"] == 12
        assert logged[0]["stage_name"] == "session_end_bound"

    def test_session_end_payload_closes_activation_with_event_timestamp(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-end", "/tmp/src/wt-end")
        m.tracking.register_session(
            "wt-end", "session-end", started_at="2026-08-27T10:00:00"
        )
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "wrong-session")
        monkeypatch.setattr(
            m.sys,
            "stdin",
            io.StringIO(json.dumps({
                "sessionId": "session-end",
                "cwd": "/tmp/src/wt-end",
                "timestamp": "2026-08-27T11:00:00Z",
                "source": "exit",
            })),
        )
        args = argparse.Namespace(
            worktree_id=None,
            session_id=None,
            cwd=None,
            stdin=True,
            launch_id=None,
        )

        assert m.cmd_deregister_session(args) == 0

        entry = load_record(
            tmp_tracking_dir / "wt-end.yaml"
        ).session_entry("session-end")
        assert entry.ended_at == "2026-08-27T11:00:00"
        assert load_record(
            tmp_tracking_dir / "wt-end.yaml"
        ).session_entry("wrong-session") is None
        assert entry.activations[-1].end_source == "hook:exit"
        assert entry.activations[-1].end_recorded_at

    def test_session_end_resolves_exact_registered_session_when_cwd_is_home(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        path = tmp_tracking_dir / "wt-end.yaml"
        _save_record(tmp_tracking_dir, "wt-end", "/tmp/src/wt-end")
        m.tracking.register_session("wt-end", "session-end")
        monkeypatch.setattr(
            m, "_activate_project_for_path", lambda _cwd, *, force=False: None
        )
        monkeypatch.setattr(
            m.tracking, "find_worktree_id_by_cwd", lambda _cwd: None
        )
        monkeypatch.setattr(
            m, "_find_tracking_file_by_session", lambda _sid: path
        )
        monkeypatch.setattr(
            m, "_activate_project_for_worktree_id", lambda _wid: "test"
        )
        monkeypatch.setattr(
            m.sys,
            "stdin",
            io.StringIO(
                '{"sessionId":"session-end","cwd":"/home/user"}'
            ),
        )
        args = argparse.Namespace(
            worktree_id=None,
            session_id=None,
            cwd=None,
            stdin=True,
            launch_id=None,
        )

        assert m.cmd_deregister_session(args) == 0
        assert load_record(path).session_entry("session-end").ended_at

    def test_explicit_worktree_id_activates_project_before_resolution(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-end", "/tmp/src/wt-end")
        m.tracking.register_session("wt-end", "session-end")
        m.cfg.set_active_project(None)

        def activate(_wid):
            m.cfg.set_active_project("test-project")
            return "test-project"

        monkeypatch.setattr(
            m, "_activate_project_for_worktree_id", activate
        )
        monkeypatch.setattr(
            worktree_identity, "_resolve_worktree_id", lambda wid: wid
        )
        monkeypatch.setattr(m, "_capture_session_title", lambda *_: True)
        args = argparse.Namespace(
            worktree_id="wt-end",
            session_id="session-end",
            cwd=None,
            stdin=False,
            launch_id=None,
        )

        assert m.cmd_deregister_session(args) == 0
        assert load_record(
            tmp_tracking_dir / "wt-end.yaml"
        ).session_entry("session-end").ended_at

    @pytest.mark.guard
    def test_exact_session_fallback_scans_record_names_deterministically(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch, monkeypatch_config
    ):
        tracking_dir = tmp_tracking_dir
        _save_record(tracking_dir, "z-record", "/tmp/z")
        _save_record(tracking_dir, "a-record", "/tmp/a")
        m.cfg.set_active_project("test-project")
        config = m.cfg.Config(
            srcroot=str(tmp_path), machine="test", platform="wsl", repo_name="test-repo",
            repos={"test-repo": m.cfg.RepoConfig(
                anchor=str(tmp_path / "anchor"), worktree_root=str(tracking_dir),
            )},
        )
        monkeypatch.setattr(m.cfg, "load_project_config", lambda name, **_k: config)
        for worktree_id in ("z-record", "a-record"):
            monkeypatch.setattr(
                m.cfg, "tracking_dir", lambda: tracking_dir
            )
            m.tracking.register_session(worktree_id, "same-session")
        monkeypatch.setattr(m, "_all_tracking_dirs", lambda: [tracking_dir])

        found = m._find_tracking_file_by_session("same-session")

        assert found == tracking_dir / "a-record.yaml"


class TestRegisterSessionReseedsStatusUpdater:
    """sessionStart must re-seed the status-bar updater so an attached
    long-lived session recovers its bar after a deploy retires the old updater
    (the launcher only spawns it at psmux create/join) -- dotfiles #915."""

    def test_reseeds_with_payload_cwd(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-x", "/tmp/src/wt-x")
        seen: list[tuple[str, str | None]] = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda wt, path: seen.append((wt, path)) or True,
        )
        payload = '{"sessionId":"sess-1","cwd":"/tmp/src/wt-x/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        # Seeds the resolved worktree id against the payload cwd (the worktree).
        assert seen == [("wt-x", "/tmp/src/wt-x/sub")]

    def test_manager_owned_session_skips_registry_and_only_ensures_monitor(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch, tmp_path: Path
    ):
        _save_record(tmp_tracking_dir, "wt-managed", "/tmp/src/wt-managed")
        manager_root = tmp_path / "manager-root"
        _write_managed_mux_mapping(
            manager_root,
            project="test-project",
            worktree_id="wt-managed",
            worktree_path="/tmp/src/wt-managed",
            mux_session="wt-managed",
        )
        monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(manager_root))
        monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
        ensured: list[bool] = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda *_args, **_kwargs: pytest.fail("manager-owned sessions must not spawn status-updater"),
        )
        monkeypatch.setattr(
            m,
            "_register_session_for_monitor",
            lambda *_args, **_kwargs: pytest.fail("manager-owned sessions must not reseed status-monitor.d"),
        )
        monkeypatch.setattr(
            m, "_ensure_status_monitor", lambda: ensured.append(True) or True
        )
        monkeypatch.setattr(
            m,
            "_activate_project_for_worktree_id",
            lambda _wid: cfg.set_active_project("test-project") or "test-project",
        )
        payload = '{"sessionId":"sess-managed","cwd":"/tmp/src/wt-managed/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))
        rc = m.cmd_register_session(_args(stdin=True))
        assert rc == 0
        assert ensured

    def test_reseed_falls_back_to_record_path_when_cwd_absent(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """With an explicit --worktree-id and no payload cwd, the reseed must
        use the tracking record's path -- never the hook's install-dir cwd."""
        _save_record(tmp_tracking_dir, "wt-y", "/tmp/src/wt-y")
        seen: list[tuple[str, str | None]] = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda wt, path: seen.append((wt, path)) or True,
        )
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(""))

        rc = m.cmd_register_session(
            _args(worktree_id="wt-y", session_id="sess-2", stdin=True)
        )

        assert rc == 0
        assert seen == [("wt-y", "/tmp/src/wt-y")]

    def test_no_reseed_when_registration_is_a_noop(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        """A cwd outside any tracked worktree never registers -- and must never
        spawn an updater for a session that has no worktree/bar."""
        _save_record(tmp_tracking_dir, "wt-z", "/tmp/src/wt-z")
        seen: list = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda wt, path: seen.append((wt, path)) or True,
        )
        payload = '{"sessionId":"sess-3","cwd":"/tmp/unrelated"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        assert seen == []

    def test_monitor_disabled_unmanaged_session_still_spawns_status_updater(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        _save_record(tmp_tracking_dir, "wt-unmanaged", "/tmp/src/wt-unmanaged")
        monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
        seen: list[tuple[str, str | None]] = []
        monkeypatch.setattr(
            m, "_spawn_status_updater",
            lambda wt, path: seen.append((wt, path)) or True,
        )
        monkeypatch.setattr(
            m,
            "_register_session_for_monitor",
            lambda *_args, **_kwargs: pytest.fail("monitor-disabled mode must not register via the monitor"),
        )
        payload = '{"sessionId":"sess-um","cwd":"/tmp/src/wt-unmanaged/sub"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))

        rc = m.cmd_register_session(_args(stdin=True))

        assert rc == 0
        assert seen == [("wt-unmanaged", "/tmp/src/wt-unmanaged/sub")]

    def test_unbound_start_emits_projection_recovery_context(
        self,
        tmp_tracking_dir: Path,
        monkeypatch_config,
        monkeypatch,
        capsys,
    ):
        _save_record(tmp_tracking_dir, "wt-z", "/tmp/src/wt-z")
        payload = '{"sessionId":"sess-3","cwd":"/tmp/unrelated"}'
        monkeypatch.setattr(m.sys, "stdin", io.StringIO(payload))
        monkeypatch.setattr(
            session_projection,
            "recovery_report",
            lambda session_id, cwd=None: {
                "session_id": session_id,
                "status": "bound-elsewhere",
                "restored": False,
                "relations": [],
                "recommended_action": "verify-and-bind",
            },
        )
        monkeypatch.setattr(
            session_projection,
            "render_recovery_context",
            lambda report: "validated recovery pointer",
        )

        rc = m.cmd_register_session(_args(stdin=True, emit_context=True))

        assert rc == 0
        assert json.loads(capsys.readouterr().out) == {
            "additionalContext": "validated recovery pointer"
        }
