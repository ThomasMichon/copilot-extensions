"""Tests for durable session/worktree association -- it must
always be durably recorded, and a session that yields to a handoff must open
the head position for the *next* session, not just its own formally-linked
successor.

Two independent halves:
  1. `handoff_diagnostics.stamp_session_state_worktree_binding` -- the
     session-state-folder half of "every session that starts in a worktree
     is recorded" (the worktree's own tracking YAML already accumulates
     every session in `record.sessions`; this is the durable session-state
     mirror of that same fact).
  2. `tracking`'s new "yielded" session state + the relaxed `register_session`
     head-claim gating that lets an ordinary next session supersede a
     yielded (not formally concluded) predecessor.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from agent_worktrees import handoff_diagnostics, sessions, tracking
from agent_worktrees import config as cfg
from agent_worktrees.tracking import (
    SessionLifecycleError,
    WorktreeRecord,
    load_record,
    save_record,
)


def _save_record(tracking_dir: Path, wt_id: str, wt_path: str, machine: str = "test") -> None:
    rec = WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=wt_path,
        repo="test-repo",
        machine=machine,
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


# -- stamp_session_state_worktree_binding ------------------------------------


class TestStampSessionStateWorktreeBinding:
    def test_writes_a_durable_binding_record(self, tmp_path, monkeypatch):
        session_dir = sessions._session_state_dir() / "sid-1"
        session_dir.mkdir(parents=True, exist_ok=True)

        result = handoff_diagnostics.stamp_session_state_worktree_binding(
            "sid-1", "wt-a", worktree_dir="/tmp/src/wt-a", machine="book2",
        )

        path = handoff_diagnostics.session_state_worktree_binding_path("sid-1")
        assert path.exists()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["sessionId"] == "sid-1"
        assert on_disk["worktreeId"] == "wt-a"
        assert on_disk["worktreeDir"] == "/tmp/src/wt-a"
        assert on_disk["machine"] == "book2"
        assert on_disk["registeredAt"]
        assert result == on_disk

    def test_never_creates_the_session_state_dir(self, tmp_path):
        # An active session already owns its session-state dir (mirrors
        # context-handoff's writeSessionStateHandoff convention) -- a session
        # id this process has never heard of is a silent no-op, not an error.
        result = handoff_diagnostics.stamp_session_state_worktree_binding(
            "never-registered", "wt-a", worktree_dir="/tmp/src/wt-a",
        )
        assert result is None
        path = handoff_diagnostics.session_state_worktree_binding_path(
            "never-registered"
        )
        assert not path.exists()

    def test_no_op_without_a_session_id_or_worktree_id(self):
        assert handoff_diagnostics.stamp_session_state_worktree_binding(
            None, "wt-a",
        ) is None
        assert handoff_diagnostics.stamp_session_state_worktree_binding(
            "sid-1", None,
        ) is None

    def test_degrades_safe_on_write_failure(self, monkeypatch):
        session_dir = sessions._session_state_dir() / "sid-2"
        session_dir.mkdir(parents=True, exist_ok=True)

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        result = handoff_diagnostics.stamp_session_state_worktree_binding(
            "sid-2", "wt-a", worktree_dir="/tmp/src/wt-a",
        )
        assert result is None


# -- yielded state + relaxed head-claim gating -------------------------------


@pytest.fixture
def qualified_binding(tmp_path, tmp_tracking_dir, monkeypatch_config):
    cfg.set_active_project("test-repo")
    _save_record(tmp_tracking_dir, "wt-subject", str(tmp_path / "checkout"))
    tracking.register_session("wt-subject", "sid-subject")
    tracking.register_session("wt-subject", "other-head")
    record_path = tmp_tracking_dir / "wt-subject.yaml"
    record = tracking._load_record_uncached(record_path)
    record.creation_nonce = "subject-incarnation"
    tracking.set_head_session(record, "other-head", save=False)
    save_record(record, record_path)
    session_dir = sessions._session_state_dir() / "sid-subject"
    session_dir.mkdir(parents=True, exist_ok=True)
    return record_path, tracking._load_record_uncached(record_path), session_dir


def test_trusted_binding_qualifies_actual_non_head_record(qualified_binding, monkeypatch):
    record_path, snapshot, session_dir = qualified_binding
    assert snapshot.resolved_head_session == "other-head"
    assert snapshot.session_entry("sid-subject") is not None
    original = Path.replace
    held_at_replace = []

    def replace_while_guarded(path, target):
        key = os.path.normcase(os.path.abspath(record_path))
        held_at_replace.append(tracking._thread_sidecar_counts().get(key, 0) > 0)
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace_while_guarded)
    result = handoff_diagnostics.stamp_session_state_worktree_binding(
        "sid-subject", "wt-subject", worktree_record=snapshot,
    )
    assert result["project"] == "test-repo"
    assert result["creationNonce"] == "subject-incarnation"
    assert json.loads((session_dir / "worktree-binding.json").read_text()) == result
    assert held_at_replace == [True]


def test_trusted_binding_accepts_exact_pending_candidate(qualified_binding):
    record_path, record, _ = qualified_binding
    tracking.open_handoff(record, "other-head", "task-subject")
    record = tracking._load_record_uncached(record_path)
    record.handoffs[-1].candidate = "sid-pending"
    save_record(record, record_path)
    (sessions._session_state_dir() / "sid-pending").mkdir(parents=True)
    result = handoff_diagnostics.stamp_session_state_worktree_binding(
        "sid-pending", "wt-subject",
        worktree_record=tracking._load_record_uncached(record_path),
    )
    assert result["creationNonce"] == "subject-incarnation"


@pytest.mark.parametrize("invalid", [
    "project", "worktree", "nonce", "lifecycle", "head",
    "session", "removed-association", "missing-nonce", "legacy",
])
def test_trusted_binding_invalid_or_legacy_rewrite_removes_stale_fields(
    qualified_binding, invalid,
):
    record_path, snapshot, session_dir = qualified_binding
    binding_path = session_dir / "worktree-binding.json"
    binding_path.write_text(json.dumps({"project": "old", "creationNonce": "old"}))
    if invalid in {"project", "worktree", "nonce", "lifecycle", "head"}:
        field, value = {
            "project": ("repo", "wrong-project"),
            "worktree": ("worktree_id", "wrong-worktree"),
            "nonce": ("creation_nonce", "wrong-incarnation"),
            "lifecycle": ("lifecycle_revision", snapshot.lifecycle_revision + 1),
            "head": ("head_revision", snapshot.head_revision + 1),
        }[invalid]
        snapshot = replace(snapshot, **{field: value})
    elif invalid in {"session", "removed-association"}:
        current = tracking._load_record_uncached(record_path)
        current.sessions = [] if invalid == "removed-association" else [
            replace(current.sessions[0], session_id="unrelated")
        ]
        save_record(current, record_path)
    elif invalid == "missing-nonce":
        current = tracking._load_record_uncached(record_path)
        current.creation_nonce = ""
        save_record(current, record_path)
    else:
        snapshot = None
    result = handoff_diagnostics.stamp_session_state_worktree_binding(
        "sid-subject", "wt-subject", worktree_dir="legacy-path",
        machine="test", worktree_record=snapshot,
    )
    assert result["worktreeDir"] == "legacy-path"
    assert result["machine"] == "test"
    assert "project" not in result and "creationNonce" not in result
    assert json.loads(binding_path.read_text()) == result


def test_trusted_binding_contention_preserves_qualified_file(qualified_binding, monkeypatch):
    _, snapshot, session_dir = qualified_binding
    binding_path = session_dir / "worktree-binding.json"
    before = '{"project":"newer","creationNonce":"newer"}'
    binding_path.write_text(before)

    class Busy:
        acquired = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def busy_lock(path, **kwargs):
        assert kwargs == {"blocking": False, "require_sidecar": True}
        return Busy()

    monkeypatch.setattr(tracking, "_RecordLock", busy_lock)
    assert handoff_diagnostics.stamp_session_state_worktree_binding(
        "sid-subject", "wt-subject", worktree_record=snapshot,
    ) is None
    assert binding_path.read_text() == before


def test_trusted_binding_absent_session_dir_is_not_created(qualified_binding):
    _, snapshot, _ = qualified_binding
    path = sessions._session_state_dir() / "absent-subject"
    assert handoff_diagnostics.stamp_session_state_worktree_binding(
        "absent-subject", "wt-subject", worktree_record=snapshot,
    ) is None
    assert not path.exists()


@pytest.mark.parametrize("failure", ["read", "write"])
def test_trusted_binding_io_failure_is_best_effort_without_overwrite(
    qualified_binding, monkeypatch, failure,
):
    _, snapshot, session_dir = qualified_binding
    binding_path = session_dir / "worktree-binding.json"
    before = '{"project":"newer","creationNonce":"newer"}'
    binding_path.write_text(before)

    def fail(*args, **kwargs):
        raise OSError("synthetic I/O failure")

    if failure == "read":
        monkeypatch.setattr(tracking, "_load_record_uncached", fail)
    else:
        monkeypatch.setattr(Path, "write_text", fail)
    assert handoff_diagnostics.stamp_session_state_worktree_binding(
        "sid-subject", "wt-subject", worktree_record=snapshot,
    ) is None
    assert binding_path.read_text() == before


class TestYieldedPredecessorAutoClaim:
    def test_opening_a_handoff_yields_the_predecessor(self, tmp_tracking_dir, monkeypatch_config):
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")

        tracking.open_handoff(rec, "old", "task-123")

        assert rec.session_entry("old").state == "yielded"
        # A yielded (not concluded) predecessor no longer resolves as head --
        # this is what opens the position for the next session to claim.
        assert rec.resolved_head_session is None

    def test_ordinary_next_session_auto_claims_head_after_yield(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")

        # An entirely ORDINARY new session -- no handoff_token, no
        # candidate_token, exactly what a manually-opened pane or a plain
        # /consume-handoff resume looks like.
        tracking.register_session("wt-1", "new")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session == "new"
        # The stale pending handoff is superseded, not left dangling.
        assert rec.handoffs[0].state == "cancelled"

    def test_yielded_predecessor_does_not_conclude(self, tmp_tracking_dir, monkeypatch_config):
        # "yielded" is deliberately distinct from "handed-off"/"concluded" --
        # the predecessor may still be alive/resumable.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        save_record(rec, tmp_tracking_dir / "wt-1.yaml")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("old").state not in ("handed-off", "concluded")

    def test_a_genuinely_concluded_predecessors_pending_handoff_still_gates(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A pending handoff whose predecessor already explicitly concluded
        # (conclude_session, not merely open_handoff) represents a specific,
        # in-flight formal cutover -- an unrelated ordinary session must
        # still NOT silently hijack it (unchanged from before this fix).
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.conclude_session(
            rec, "old", state="handed-off", handoff_token="expected",
        )

        tracking.register_session("wt-1", "new")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session is None
        assert rec.handoffs[0].state == "pending"

    def test_explicit_bind_still_supersedes_a_concluded_predecessors_handoff(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.conclude_session(
            rec, "old", state="handed-off", handoff_token="expected",
        )
        tracking.register_session("wt-1", "new")

        tracking.register_session("wt-1", "new", source="bind")

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session == "new"
        assert rec.handoffs[0].state == "cancelled"

    def test_stale_cancelled_token_degrades_instead_of_raising(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A live-cutover successor whose token was already superseded by an
        # unrelated ordinary session (a genuine race this fix newly permits)
        # must never hard-fail the sessionStart hook -- consuming a specific
        # handoff's charter is a separate concern from this session's own
        # registration.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        # An unrelated ordinary session wins the race first.
        tracking.register_session("wt-1", "interloper")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.handoffs[0].state == "cancelled"

        # The "real" successor arrives moments later still carrying the
        # now-stale token -- must not raise.
        linked = tracking.register_session(
            "wt-1", "real-successor", handoff_token="task-123",
        )
        assert linked is None
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.session_entry("real-successor") is not None

    def test_candidate_mismatch_still_raises_for_a_genuine_conflict(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A real candidate-mismatch conflict (two sessions both trying to
        # claim the SAME still-pending token) must still be rejected -- the
        # degrade-on-stale-token path above must not swallow genuine misuse.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")
        tracking.register_session(
            "wt-1", "candidate", candidate_token="task-123",
        )
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.associate_handoff_candidate(rec, "task-123", "candidate")

        with pytest.raises(SessionLifecycleError, match="associated with candidate"):
            tracking.register_session(
                "wt-1", "other", source="bind", handoff_token="task-123",
            )

    def test_candidate_token_registration_never_auto_claims_either(
        self, tmp_tracking_dir, monkeypatch_config,
    ):
        # A session carrying a candidate_token is (likely) the intended
        # successor, but merely registering with that token must not itself
        # claim head -- only a deliberate later bind/link may.
        _save_record(tmp_tracking_dir, "wt-1", "/tmp/wt-1")
        tracking.register_session("wt-1", "old")
        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        tracking.open_handoff(rec, "old", "task-123")

        tracking.register_session(
            "wt-1", "candidate", candidate_token="task-123",
        )

        rec = load_record(tmp_tracking_dir / "wt-1.yaml")
        assert rec.resolved_head_session is None
        assert rec.handoffs[0].state == "pending"
