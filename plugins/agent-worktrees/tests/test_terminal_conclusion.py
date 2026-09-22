from __future__ import annotations

import json
import os
import types
from pathlib import Path

import pytest
import yaml

from agent_worktrees import __main__ as cli
from agent_worktrees import session_tracking_cli
from agent_worktrees import git_ops, sessions, tracking
from agent_worktrees import terminal_conclusion as tc


def _git(cwd: Path, *args: str) -> str:
    return git_ops.git(*args, cwd=cwd).stdout.strip()


def _worker(tmp_path: Path, monkeypatch):
    remote = tmp_path / "remote.git"
    anchor = tmp_path / "anchor"
    root = tmp_path / "worktrees"
    tracking_dir = tmp_path / "tracking"
    worktree_id = "worker-20260901-abcd"
    worktree = root / worktree_id
    tracking_dir.mkdir()
    root.mkdir()

    git_ops.git("init", "--bare", "-b", "main", str(remote))
    git_ops.git("init", "-b", "main", str(anchor))
    _git(anchor, "config", "user.email", "test@example.com")
    _git(anchor, "config", "user.name", "Test")
    (anchor / "README.md").write_text("base\n", encoding="utf-8")
    _git(anchor, "add", "-A")
    _git(anchor, "commit", "-m", "initial")
    _git(anchor, "remote", "add", "origin", str(remote))
    _git(anchor, "push", "-u", "origin", "main")
    git_ops.git(
        "worktree",
        "add",
        str(worktree),
        "-b",
        f"worktree/{worktree_id}",
        "origin/main",
        cwd=anchor,
    )
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test")

    record = tracking.create_new_record(
        worktree_id,
        f"worktree/{worktree_id}",
        str(worktree),
        "demo",
        "host",
        "linux",
        tracking_dir,
    )
    record.sessions = [
        tracking.SessionEntry(
            session_id="session-exact",
            started_at="2026-09-01T00:00:00Z",
        )
    ]
    record.head_session = "session-exact"
    tracking.save_record(record, tracking_dir / f"{worktree_id}.yaml")

    monkeypatch.setattr(sessions, "has_mux_session", lambda _worktree: False)
    monkeypatch.setattr(
        sessions,
        "scan_sessions_fast",
        lambda _records: types.SimpleNamespace(active_sessions={}),
    )
    repo = types.SimpleNamespace(
        anchor=str(anchor),
        worktree_root=str(root),
        remote="origin",
        default_branch="main",
    )
    return repo, tracking_dir / f"{worktree_id}.yaml", worktree


def _conclude(record_path: Path, repo, **over):
    return tc.conclude_disposable_worktree(
        record_path,
        repo,
        session_id=over.get("session_id", "session-exact"),
        owner=over.get("owner", "dispatcher"),
        policy=over.get("policy", tc.DISPOSABLE_CLI_POLICY),
        reservation_key=over.get("reservation_key"),
    )


def test_live_session_is_preserved(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    monkeypatch.setattr(sessions, "has_mux_session", lambda _worktree: True)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "live-mux"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"
    assert (worktree / "valuable.txt").exists()


def test_different_lifecycle_head_is_preserved(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)

    result = _conclude(
        record_path,
        repo,
        session_id="different-session",
    )

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "session-mismatch"
    assert record.kind == "session"
    assert record.resolved_head_session == "session-exact"
    assert record.session_entry("session-exact").state == "active"


def test_pending_handoff_preserves_predecessor(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    tracking.open_handoff(
        record,
        "session-exact",
        "handoff-token",
        save=False,
    )
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "pending-handoff"
    assert record.kind == "session"
    # "session-exact" yielded the moment it opened the handoff (gitea
    # aperture-labs#7230) -- no longer "active", but not concluded either.
    assert record.session_entry("session-exact").state == "yielded"
    assert record.pending_handoffs


def test_follow_up_preserves_session_and_worktree(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.follow_up = True
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["reason"] == "follow-up"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"


def test_open_pr_preserves_session_and_worktree(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.pr = tracking.PRRecord(state="open", branch="feature/example", number=7)
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["reason"] == "open-pull-request"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"


def test_non_cli_worktree_preserves_session(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["reason"] == "not-cli-worktree"
    assert record.session_entry("session-exact").state == "active"


def test_mismatched_worktree_path_is_preserved(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.worktree_path = str(Path(record.worktree_path).with_name("other-worker"))
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo)

    assert result["action"] == "skipped"
    assert result["reason"] == "worktree-path-mismatch"


def test_duplicate_worktree_path_is_preserved(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    peer = tracking.create_new_record(
        "peer-worker",
        "worktree/peer-worker",
        record.worktree_path,
        "demo",
        "host",
        "linux",
        record_path.parent,
    )
    tracking.save_record(peer, record_path.parent / "peer-worker.yaml")

    result = _conclude(record_path, repo)

    assert result["action"] == "skipped"
    assert result["reason"] == "worktree-path-conflict"


def test_dispatch_attempt_policy_concludes_exact_acp_allocation(
    tmp_path, monkeypatch
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    record.origin = "delegate"
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine=record.machine.swapcase(),
    )
    tracking.save_record(record, record_path)

    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:1",
    )

    record = tracking.load_record(record_path)
    assert result["action"] == "primed"
    assert record.interface == "acp"
    assert record.kind == "bridge"
    assert record.owner == "dispatcher"
    assert record.dispatch_attempt is not None
    assert cli._worktree_to_dict(record)["dispatch_attempt"] == (
        record.dispatch_attempt.to_dict()
    )


def test_dispatch_attempt_policy_releases_own_stale_session_claim(
    tmp_path, monkeypatch
):
    """Regression: a headless dispatch-created session that crashed/was
    reaped before its own sessionEnd hook could run leaves a `session`-kind
    resource claim wedged `active` forever (copilot-extensions#3179
    follow-up) -- neither agent-bridge's own status nor the pid-lock/mux
    backstop (`_session_is_live`, already confirmed dead by `_worker`'s
    fixture) ever releases it; only a `sessionEnd` hook or this
    dispatch-attempt conclusion does. Since agent-dispatch concluding its
    own exactly-matched attempt IS the definitive "we are done" signal, the
    claim must be released so the worktree can still be primed."""
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    record.origin = "delegate"
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine=record.machine,
    )
    session_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-exact",
    )
    record.resources = [
        tracking.ResourceClaim(
            kind="session",
            ref=session_ref,
            created_at="2026-09-21T12:24:04",
            state="active",
            note="live Copilot session",
        )
    ]
    tracking.save_record(record, record_path)
    assert record.live_resources  # sanity: the fixture really is wedged

    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:1",
    )

    assert result["action"] == "primed"
    record = tracking.load_record(record_path)
    assert record.live_resources == []
    released = next(c for c in record.resources if c.ref == session_ref)
    assert released.state == "released"


def test_dispatch_attempt_policy_releases_only_the_concluding_session_claim(
    tmp_path, monkeypatch
):
    """A record can hold more than one `session`-kind claim (e.g. an
    earlier, separately-tracked session on the same worktree). Concluding
    THIS attempt's session must release only its own claim -- an unrelated
    still-live session claim must keep blocking disposal (outstanding
    obligations), never be swept up by a blanket release."""
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    record.origin = "delegate"
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine=record.machine,
    )
    own_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-exact",
    )
    other_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-other",
    )
    record.resources = [
        tracking.ResourceClaim(kind="session", ref=own_ref, state="active"),
        # Already released (e.g. that session's own clean sessionEnd already
        # ran) -- not live, so it must not block the conclusion, and this
        # test also proves the selective release does not touch it.
        tracking.ResourceClaim(kind="session", ref=other_ref, state="released"),
    ]
    tracking.save_record(record, record_path)

    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:1",
    )

    assert result["action"] == "primed"
    record = tracking.load_record(record_path)
    own_claim = next(c for c in record.resources if c.ref == own_ref)
    other_claim = next(c for c in record.resources if c.ref == other_ref)
    assert own_claim.state == "released"
    assert other_claim.state == "released"


def test_dispatch_attempt_policy_unrelated_live_claim_blocks_without_persisting(
    tmp_path, monkeypatch
):
    """An unrelated still-LIVE session claim must keep blocking disposal
    (outstanding obligations) -- and, per Copilot review on PR #3198, the
    concluding session's own claim must NOT be persisted as released
    either: the first phase's release is provisional/in-memory only (just
    enough to evaluate that phase's own preservation gate), so a call that
    ultimately reports `skipped` never leaves an irreversible partial
    mutation behind."""
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    record.origin = "delegate"
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine=record.machine,
    )
    own_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-exact",
    )
    other_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-other",
    )
    record.resources = [
        tracking.ResourceClaim(kind="session", ref=own_ref, state="active"),
        tracking.ResourceClaim(kind="session", ref=other_ref, state="active"),
    ]
    tracking.save_record(record, record_path)

    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:1",
    )

    assert result["action"] == "skipped"
    assert result["reason"] == "outstanding-obligations"
    record = tracking.load_record(record_path)
    own_claim = next(c for c in record.resources if c.ref == own_ref)
    other_claim = next(c for c in record.resources if c.ref == other_ref)
    assert own_claim.state == "active"  # never persisted -- conclusion did not succeed
    assert other_claim.state == "active"


def test_dispatch_attempt_policy_does_not_persist_release_when_dirty_work_blocks(
    tmp_path, monkeypatch
):
    """The first phase's provisional release must not survive a later
    skip discovered only during the unlocked git inspection (dirty
    uncommitted work) -- the claim must remain exactly as it was on disk."""
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")
    record = tracking.load_record(record_path)
    record.interface = "acp"
    record.origin = "delegate"
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine=record.machine,
    )
    own_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-exact",
    )
    record.resources = [
        tracking.ResourceClaim(kind="session", ref=own_ref, state="active"),
    ]
    tracking.save_record(record, record_path)

    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:1",
    )

    assert result["action"] == "skipped"
    assert result["reason"] == "dirty-work"
    record = tracking.load_record(record_path)
    own_claim = next(c for c in record.resources if c.ref == own_ref)
    assert own_claim.state == "active"
    assert (worktree / "valuable.txt").read_text(encoding="utf-8") == "keep\n"



def test_dispatch_attempt_policy_does_not_release_claim_on_reservation_mismatch(
    tmp_path, monkeypatch
):
    """No definitive reason to end the session -> the claim stays put. A
    mismatched reservation means this call is not the attempt's own
    authoritative conclusion, so it must not release anything."""
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine=record.machine,
    )
    session_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-exact",
    )
    record.resources = [
        tracking.ResourceClaim(
            kind="session", ref=session_ref, state="active",
        )
    ]
    tracking.save_record(record, record_path)

    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:2",
    )

    assert result["action"] == "skipped"
    assert result["reason"] == "reservation-mismatch"
    record = tracking.load_record(record_path)
    assert record.live_resources  # untouched


def test_disposable_cli_policy_does_not_release_session_claims(
    tmp_path, monkeypatch
):
    """The self-release path is scoped to dispatch-attempt conclusions only
    -- an ordinary disposable-cli sweep carries no such authoritative
    per-attempt provenance to release someone else's session claim on."""
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    session_ref = tracking.format_claim_ref(
        record.machine, "demo", record.worktree_id, session="session-exact",
    )
    record.resources = [
        tracking.ResourceClaim(
            kind="session", ref=session_ref, state="active",
        )
    ]
    tracking.save_record(record, record_path)

    result = _conclude(record_path, repo, policy=tc.DISPOSABLE_CLI_POLICY)

    assert result["action"] == "skipped"
    assert result["reason"] == "outstanding-obligations"
    record = tracking.load_record(record_path)
    assert record.live_resources  # untouched


def test_dispatch_attempt_policy_rejects_wrong_reservation(
    tmp_path, monkeypatch
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    record = tracking.load_record(record_path)
    record.interface = "acp"
    record.dispatch_attempt = tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine=record.machine,
    )
    tracking.save_record(record, record_path)

    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:2",
    )

    assert result["action"] == "skipped"
    assert result["reason"] == "reservation-mismatch"
    assert tracking.load_record(record_path).resolved_head_session == "session-exact"


def test_newer_dispatch_provenance_is_preserved_but_not_trusted(
    tmp_path, monkeypatch
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    raw = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    raw["dispatch_attempt"] = {
        "schema_version": 2,
        "task_id": "task-1",
        "reservation_key": "dispatch-task:task-1:1",
        "attempt": 1,
        "driver": "dispatcher",
        "supervisor": "supervisor-1",
        "creator_machine": "host",
        "ownership": "created",
    }
    record_path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )

    record = tracking.load_record(record_path)
    assert record.dispatch_attempt is None
    assert record.dispatch_attempt_opaque is True
    tracking.save_record(record, record_path)

    saved = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    assert saved["dispatch_attempt"] == raw["dispatch_attempt"]
    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:1",
    )
    assert result["action"] == "skipped"
    assert result["reason"] == "dispatch-provenance-missing"


def test_blank_dispatch_provenance_is_preserved_but_not_trusted(
    tmp_path, monkeypatch
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    raw = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    raw["dispatch_attempt"] = {
        "task_id": "task-1",
        "reservation_key": "dispatch-task:task-1:1",
        "attempt": 1,
        "driver": "   ",
        "supervisor": "host-a",
        "creator_machine": "host-a",
        "ownership": "created",
    }
    record_path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )

    loaded = tracking.load_record(record_path)
    assert loaded.dispatch_attempt is None
    assert loaded.dispatch_attempt_opaque is True

    tracking.save_record(loaded, record_path)
    saved = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    assert saved["dispatch_attempt"] == raw["dispatch_attempt"]
    result = _conclude(
        record_path,
        repo,
        policy=tc.DISPATCH_ATTEMPT_POLICY,
        reservation_key="dispatch-task:task-1:1",
    )
    assert result["action"] == "skipped"
    assert result["reason"] == "dispatch-provenance-missing"


def test_dispatch_provenance_strings_are_normalized(tmp_path, monkeypatch):
    _repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    raw = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    raw["dispatch_attempt"] = {
        "task_id": " task-1 ",
        "reservation_key": " dispatch-task:task-1:1 ",
        "attempt": 1,
        "driver": " dispatcher ",
        "supervisor": " supervisor-1 ",
        "creator_machine": " host-a ",
        "ownership": "created",
    }
    record_path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )

    loaded = tracking.load_record(record_path)
    assert loaded.dispatch_attempt == tracking.DispatchAttempt(
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="dispatcher",
        supervisor="supervisor-1",
        creator_machine="host-a",
    )


def test_lifecycle_change_during_git_inspection_blocks_priming(
    tmp_path,
    monkeypatch,
):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    original_current_branch = git_ops.current_branch
    changed = False

    def change_lifecycle(path):
        nonlocal changed
        if not changed:
            changed = True
            record = tracking.load_record(record_path)
            record.sessions.append(
                tracking.SessionEntry(
                    session_id="successor-session",
                    started_at="2026-09-01T00:01:00Z",
                )
            )
            tracking.set_head_session(
                record,
                "successor-session",
                save=False,
            )
            tracking.save_record(record, record_path)
        return original_current_branch(path)

    monkeypatch.setattr(git_ops, "current_branch", change_lifecycle)

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "lifecycle-changed"
    assert record.kind == "session"
    assert record.resolved_head_session == "successor-session"
    assert worktree.exists()


def test_behind_branch_is_primed_without_rewriting_head(
    tmp_path,
    monkeypatch,
):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    anchor = Path(repo.anchor)
    (anchor / "upstream.txt").write_text("new\n", encoding="utf-8")
    _git(anchor, "add", "upstream.txt")
    _git(anchor, "commit", "-m", "advance upstream")
    _git(anchor, "push", "origin", "main")
    before = _git(worktree, "rev-parse", "HEAD")

    result = _conclude(record_path, repo)

    assert result["action"] == "primed"
    assert result["reconciled"] is False
    assert _git(worktree, "rev-parse", "HEAD") == before
    assert record_path.exists()


def test_dirty_author_work_is_preserved(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "dirty-work"
    assert record.kind == "session"
    assert record.session_entry("session-exact").state == "active"
    assert (worktree / "valuable.txt").read_text(encoding="utf-8") == "keep\n"


def test_committed_author_work_is_preserved(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")
    _git(worktree, "add", "valuable.txt")
    _git(worktree, "commit", "-m", "valuable work")
    before = _git(worktree, "rev-parse", "HEAD")

    result = _conclude(record_path, repo)

    assert result["action"] == "skipped"
    assert result["reason"] == "local-commits"
    assert _git(worktree, "rev-parse", "HEAD") == before
    assert tracking.load_record(record_path).kind == "session"


def test_missing_checkout_with_committed_branch_is_preserved(
    tmp_path,
    monkeypatch,
):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    (worktree / "valuable.txt").write_text("keep\n", encoding="utf-8")
    _git(worktree, "add", "valuable.txt")
    _git(worktree, "commit", "-m", "valuable work")
    git_ops.git(
        "worktree",
        "remove",
        str(worktree),
        "--force",
        cwd=repo.anchor,
    )

    result = _conclude(record_path, repo)

    assert result["action"] == "skipped"
    assert result["reason"] == "local-commits"
    assert tracking.load_record(record_path).kind == "session"
    assert (
        git_ops.git(
            "show-ref",
            "--verify",
            "refs/heads/worktree/worker-20260901-abcd",
            cwd=repo.anchor,
            check=False,
        ).returncode
        == 0
    )


def test_generated_overlay_is_preserved_as_dirty_work(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    overlay = worktree / ".github" / "copilot" / "settings.local.json"
    overlay.parent.mkdir(parents=True)
    managed = {
        "example": {
            "source": {"source": "directory", "path": "/path/to/marketplace"}
        }
    }
    overlay.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": managed,
                "_agentWorktreesMarketplaceOverrides": {
                    "version": 1,
                    "marketplaces": managed,
                },
            }
        ),
        encoding="utf-8",
    )

    result = _conclude(record_path, repo)

    record = tracking.load_record(record_path)
    assert result["action"] == "skipped"
    assert result["reason"] == "dirty-work"
    assert overlay.exists()
    assert record.kind == "session"
    assert record.status == "active"
    assert record.session_entry("session-exact").state == "active"


def test_repeated_conclusion_is_idempotent(tmp_path, monkeypatch):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)

    first = _conclude(record_path, repo)
    second = _conclude(record_path, repo)

    assert first["action"] == "primed"
    assert second["action"] == "already-primed"
    assert second["session"]["action"] == "already-concluded"


def test_terminal_managed_record_rejects_new_session_activation(
    tmp_path,
    monkeypatch,
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    _conclude(record_path, repo)
    monkeypatch.setattr(
        tracking.cfg,
        "tracking_dir",
        lambda: record_path.parent,
    )

    with pytest.raises(
        tracking.SessionLifecycleError,
        match="terminal and managed",
    ):
        tracking.register_session(
            "worker-20260901-abcd",
            "late-session",
        )


def test_managed_gc_can_later_remove_primed_tree(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    _conclude(record_path, repo)
    config = types.SimpleNamespace(default_repo=repo, repo_name="demo")
    monkeypatch.setattr(cli.cfg, "load_config", lambda: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: record_path.parent)
    monkeypatch.setattr(
        cli.sessions, "_list_mux_sessions", lambda: {}
    )
    monkeypatch.setattr(
        cli.sessions, "_mux_session_activity", lambda: {}
    )
    monkeypatch.setattr(
        cli.sessions,
        "scan_sessions_fast",
        lambda _records: types.SimpleNamespace(active_sessions={}),
    )
    monkeypatch.setattr(cli, "_build_active_paths", lambda *_args, **_kw: set())

    report = cli.sweep_managed_worktrees(min_idle_secs=0)

    assert [entry["id"] for entry in report["removed"]] == [
        "worker-20260901-abcd"
    ]
    assert not worktree.exists()
    assert not record_path.exists()


def test_managed_gc_preserves_detached_head_commit(tmp_path, monkeypatch):
    repo, record_path, worktree = _worker(tmp_path, monkeypatch)
    _conclude(record_path, repo)
    detached = _git(worktree, "rev-parse", "HEAD")
    _git(worktree, "checkout", "--detach", detached)
    (worktree / "detached.txt").write_text("valuable\n", encoding="utf-8")
    _git(worktree, "add", "detached.txt")
    _git(worktree, "commit", "-m", "detached valuable work")
    detached_commit = _git(worktree, "rev-parse", "HEAD")
    config = types.SimpleNamespace(default_repo=repo, repo_name="demo")
    monkeypatch.setattr(cli.cfg, "load_config", lambda: config)
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: record_path.parent)
    monkeypatch.setattr(cli.sessions, "_list_mux_sessions", lambda: {})
    monkeypatch.setattr(cli.sessions, "_mux_session_activity", lambda: {})
    monkeypatch.setattr(
        cli.sessions,
        "scan_sessions_fast",
        lambda _records: types.SimpleNamespace(active_sessions={}),
    )
    monkeypatch.setattr(cli, "_build_active_paths", lambda *_args, **_kw: set())

    report = cli.sweep_managed_worktrees(min_idle_secs=0)

    assert report["removed"] == []
    assert report["skipped"] == [
        {
            "id": "worker-20260901-abcd",
            "reason": "recheck-branch-drift",
        }
    ]
    assert worktree.exists()
    assert _git(worktree, "rev-parse", "HEAD") == detached_commit
    assert record_path.exists()


def test_cli_remove_is_idempotent_when_record_is_already_gone(
    monkeypatch,
    capfd,
):
    monkeypatch.setattr(
        session_tracking_cli, "_find_tracking_file_exact", lambda _raw: None
    )
    args = types.SimpleNamespace(
        worktree_id="worker-gone",
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 0

    payload = json.loads(capfd.readouterr().out)
    assert payload == {
        "version": 1,
        "worktree_id": "worker-gone",
        "action": "already-removed",
        "managed_gc_eligible": False,
    }


def test_cli_remove_rejects_malformed_exact_id(capfd):
    args = types.SimpleNamespace(
        worktree_id="../worker",
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 1

    payload = json.loads(capfd.readouterr().out)
    assert payload["error"] == "Invalid exact worktree id: '../worker'"


def test_exact_tracking_lookup_rejects_cross_project_collision(
    tmp_path,
    monkeypatch,
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for directory in (first, second):
        (directory / "worker-collision.yaml").write_text(
            "worktree_id: worker-collision\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(session_tracking_cli, "_all_tracking_dirs", lambda: [first, second])

    with pytest.raises(RuntimeError, match="ambiguous across projects"):
        session_tracking_cli._find_tracking_file_exact("worker-collision")


def test_terminal_conclusion_rejects_embedded_identity_mismatch(
    tmp_path,
    monkeypatch,
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    mismatched = record_path.with_name("different-worker.yaml")
    record_path.rename(mismatched)

    with pytest.raises(RuntimeError, match="does not match its filename"):
        _conclude(mismatched, repo)


def test_cli_remove_rejects_embedded_identity_mismatch(
    tmp_path,
    monkeypatch,
    capfd,
):
    record_path = tmp_path / "requested-worker.yaml"
    record_path.write_text("worktree_id: other-worker\n", encoding="utf-8")
    monkeypatch.setattr(
        session_tracking_cli,
        "_find_tracking_file_exact",
        lambda _raw: record_path,
    )
    monkeypatch.setattr(
        session_tracking_cli,
        "_project_for_tracking_file",
        lambda _path: "demo",
    )
    monkeypatch.setattr(
        cli.cfg,
        "load_project_config",
        lambda _project: types.SimpleNamespace(repo_name="demo"),
    )
    monkeypatch.setattr(
        cli.tracking,
        "load_record",
        lambda _path: types.SimpleNamespace(worktree_id="other-worker"),
    )
    args = types.SimpleNamespace(
        worktree_id="requested-worker",
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 1

    payload = json.loads(capfd.readouterr().out)
    assert "identity mismatch" in payload["error"]


def test_cli_remove_is_idempotent_when_record_disappears_after_lookup(
    tmp_path,
    monkeypatch,
    capfd,
):
    record_path = tmp_path / "worker-gone.yaml"
    record_path.write_text("worktree_id: worker-gone\n", encoding="utf-8")
    monkeypatch.setattr(
        session_tracking_cli,
        "_find_tracking_file_exact",
        lambda _raw: record_path,
    )
    monkeypatch.setattr(
        session_tracking_cli,
        "_project_for_tracking_file",
        lambda _path: "demo",
    )
    monkeypatch.setattr(
        cli.cfg,
        "load_project_config",
        lambda _project: types.SimpleNamespace(repo_name="demo"),
    )
    monkeypatch.setattr(
        cli.tracking,
        "load_record",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError()),
    )
    args = types.SimpleNamespace(
        worktree_id="worker-gone",
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 0

    payload = json.loads(capfd.readouterr().out)
    assert payload["action"] == "already-removed"


def test_cli_remove_is_idempotent_when_record_disappears_during_conclusion(
    tmp_path,
    monkeypatch,
    capfd,
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    config = types.SimpleNamespace(repo_name="demo")
    monkeypatch.setattr(
        session_tracking_cli,
        "_find_tracking_file_exact",
        lambda _raw: record_path,
    )
    monkeypatch.setattr(
        session_tracking_cli,
        "_project_for_tracking_file",
        lambda _path: "demo",
    )
    monkeypatch.setattr(cli.cfg, "load_project_config", lambda _project: config)
    monkeypatch.setattr(cli, "_repo_for_record", lambda _config, _record: repo)

    def disappear(*_args, **_kwargs):
        record_path.unlink()
        raise FileNotFoundError

    monkeypatch.setattr(
        cli.terminal_conclusion,
        "conclude_disposable_worktree",
        disappear,
    )
    args = types.SimpleNamespace(
        worktree_id="worker-20260901-abcd",
        session_id="session-exact",
        owner="dispatcher",
        policy=tc.DISPOSABLE_CLI_POLICY,
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 0

    payload = json.loads(capfd.readouterr().out)
    assert payload["action"] == "already-removed"


def test_cli_remove_runs_exact_managed_sweep_after_eligibility(
    tmp_path,
    monkeypatch,
    capfd,
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    config = types.SimpleNamespace(repo_name="demo")
    captured = {}
    monkeypatch.setattr(
        session_tracking_cli,
        "_find_tracking_file_exact",
        lambda _raw: record_path,
    )
    monkeypatch.setattr(
        session_tracking_cli,
        "_project_for_tracking_file",
        lambda _path: "demo",
    )
    monkeypatch.setattr(cli.cfg, "load_project_config", lambda _project: config)
    monkeypatch.setattr(cli, "_repo_for_record", lambda _config, _record: repo)
    monkeypatch.setattr(
        cli.terminal_conclusion,
        "conclude_disposable_worktree",
        lambda *_args, **_kwargs: {
            "worktree_id": "worker-20260901-abcd",
            "action": "primed",
            "managed_gc_eligible": True,
        },
    )

    def sweep(**kwargs):
        captured.update(kwargs)
        return {
            "removed": [
                {
                    "id": "worker-20260901-abcd",
                    "reason": "completed",
                }
            ],
            "skipped": [],
        }

    monkeypatch.setattr(cli, "sweep_managed_worktrees", sweep)
    args = types.SimpleNamespace(
        worktree_id="worker-20260901-abcd",
        session_id="session-exact",
        owner="dispatcher",
        policy=tc.DISPOSABLE_CLI_POLICY,
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 0

    payload = json.loads(capfd.readouterr().out)
    assert payload["action"] == "removed"
    assert payload["managed_gc_eligible"] is False
    assert captured["worktree_ids"] == {"worker-20260901-abcd"}
    assert captured["min_idle_secs"] == 0
    assert captured["config"] is config
    assert captured["tracking_path"] == record_path.parent


def test_cli_remove_surfaces_fresh_managed_sweep_skip(
    tmp_path,
    monkeypatch,
    capfd,
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    config = types.SimpleNamespace(repo_name="demo")
    monkeypatch.setattr(
        session_tracking_cli,
        "_find_tracking_file_exact",
        lambda _raw: record_path,
    )
    monkeypatch.setattr(
        session_tracking_cli,
        "_project_for_tracking_file",
        lambda _path: "demo",
    )
    monkeypatch.setattr(cli.cfg, "load_project_config", lambda _project: config)
    monkeypatch.setattr(cli, "_repo_for_record", lambda _config, _record: repo)
    monkeypatch.setattr(
        cli.terminal_conclusion,
        "conclude_disposable_worktree",
        lambda *_args, **_kwargs: {
            "worktree_id": "worker-20260901-abcd",
            "action": "primed",
            "managed_gc_eligible": True,
        },
    )
    monkeypatch.setattr(
        cli,
        "sweep_managed_worktrees",
        lambda **_kwargs: {
            "removed": [],
            "skipped": [
                {
                    "id": "worker-20260901-abcd",
                    "reason": "recheck-live-session",
                }
            ],
        },
    )
    args = types.SimpleNamespace(
        worktree_id="worker-20260901-abcd",
        session_id="session-exact",
        owner="dispatcher",
        policy=tc.DISPOSABLE_CLI_POLICY,
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 1

    payload = json.loads(capfd.readouterr().out)
    assert payload["error"] == (
        "managed teardown skipped: recheck-live-session"
    )


def test_cli_remove_accepts_concurrent_exact_removal(
    tmp_path,
    monkeypatch,
    capfd,
):
    repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
    config = types.SimpleNamespace(repo_name="demo")
    monkeypatch.setattr(
        session_tracking_cli,
        "_find_tracking_file_exact",
        lambda _raw: record_path,
    )
    monkeypatch.setattr(
        session_tracking_cli,
        "_project_for_tracking_file",
        lambda _path: "demo",
    )
    monkeypatch.setattr(cli.cfg, "load_project_config", lambda _project: config)
    monkeypatch.setattr(cli, "_repo_for_record", lambda _config, _record: repo)
    monkeypatch.setattr(
        cli.terminal_conclusion,
        "conclude_disposable_worktree",
        lambda *_args, **_kwargs: {
            "worktree_id": "worker-20260901-abcd",
            "action": "primed",
            "managed_gc_eligible": True,
        },
    )

    def sweep(**_kwargs):
        record_path.unlink()
        return {"removed": [], "skipped": []}

    monkeypatch.setattr(cli, "sweep_managed_worktrees", sweep)
    args = types.SimpleNamespace(
        worktree_id="worker-20260901-abcd",
        session_id="session-exact",
        owner="dispatcher",
        policy=tc.DISPOSABLE_CLI_POLICY,
        remove=True,
    )

    assert cli.cmd_conclude_disposable(args) == 0

    payload = json.loads(capfd.readouterr().out)
    assert payload["action"] == "already-removed"
    assert payload["managed_gc_eligible"] is False


def test_short_wait_does_not_break_a_fresh_lifecycle_lock(
    tmp_path,
    monkeypatch,
    capsys,
):
    lock_path = tmp_path / ".finalize.lock"
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    os.utime(lock_path, (100.0, 100.0))
    monkeypatch.setattr(
        "agent_worktrees.finalize.time.time",
        lambda: 110.0,
    )
    lock = tc.finalize.FinalizeLock(
        lock_path,
        timeout=0.01,
        stale_after=60.0,
    )

    try:
        lock.acquire()
    except TimeoutError:
        pass
    else:
        raise AssertionError("fresh lifecycle lock should not be acquired")

    assert lock_path.exists()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_reused_pid_does_not_keep_a_stale_lifecycle_lock(
    tmp_path,
    monkeypatch,
):
    lock_path = tmp_path / ".finalize.lock"
    lock_path.write_text("123:111:token", encoding="utf-8")
    monkeypatch.setattr("agent_worktrees.finalize_lock.locks.pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        "agent_worktrees.finalize_lock.locks.process_start_time",
        lambda _pid: "222",
    )
    lock = tc.finalize.FinalizeLock(
        lock_path,
        timeout=0.1,
        stale_after=3600,
    )

    lock.acquire()
    try:
        assert lock_path.read_text(encoding="utf-8") == lock.token
    finally:
        lock.release()


def _pair_sibling(
    tmp_path,
    monkeypatch,
    *,
    project,
    harness_machine,
    harness_project,
    harness_worktree_id,
    dirty=False,
):
    """Build a real second git worktree + tracking record, paired back to
    the given harness identity, in its OWN project's tracking directory
    (``cfg.project_dir(project)``) -- exactly what
    `terminal_conclusion._resolve_pair_sibling` resolves via
    ``record.pair_ref``. Monkeypatches ``tc.cfg.project_dir`` so this
    project name resolves under ``tmp_path``."""
    remote = tmp_path / f"{project}-remote.git"
    anchor = tmp_path / f"{project}-anchor"
    root = tmp_path / f"{project}-worktrees"
    project_root = tmp_path / f".{project}"
    tracking_dir = project_root / "worktrees"
    worktree_id = f"{project}-wt"
    worktree = root / worktree_id
    tracking_dir.mkdir(parents=True)
    root.mkdir()

    git_ops.git("init", "--bare", "-b", "main", str(remote))
    git_ops.git("init", "-b", "main", str(anchor))
    _git(anchor, "config", "user.email", "test@example.com")
    _git(anchor, "config", "user.name", "Test")
    (anchor / "README.md").write_text("base\n", encoding="utf-8")
    _git(anchor, "add", "-A")
    _git(anchor, "commit", "-m", "initial")
    _git(anchor, "remote", "add", "origin", str(remote))
    _git(anchor, "push", "-u", "origin", "main")
    git_ops.git(
        "worktree",
        "add",
        str(worktree),
        "-b",
        f"worktree/{worktree_id}",
        "origin/main",
        cwd=anchor,
    )
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test")

    record = tracking.create_new_record(
        worktree_id,
        f"worktree/{worktree_id}",
        str(worktree),
        project,
        harness_machine,
        "linux",
        tracking_dir,
    )
    record.pair_id = "pair1"
    record.pair_role = "knowledge"
    record.pair_ref = tracking.format_claim_ref(
        harness_machine, harness_project, harness_worktree_id,
    )
    record.pair_kind = "worktree"
    record_path = tracking_dir / f"{worktree_id}.yaml"
    tracking.save_record(record, record_path)

    if dirty:
        (worktree / "wip.txt").write_text("uncommitted\n", encoding="utf-8")

    original_project_dir = tc.cfg.project_dir
    monkeypatch.setattr(
        tc.cfg,
        "project_dir",
        lambda name=None: project_root if name == project else original_project_dir(name),
    )
    original_load_config = tc.cfg.load_config
    this_project = project
    sibling_config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(
            remote="origin", default_branch="main", anchor=str(anchor),
        ),
    )
    monkeypatch.setattr(
        tc.cfg,
        "load_config",
        lambda project=None: (
            sibling_config if project == this_project else original_load_config(project=project)
        ),
    )
    return record, record_path, worktree


class TestReciprocalDisposal:
    """Regression (catch-22 follow-up to #3198/#3207): a dispatch attempt's
    own paired knowledge sibling must be treated as ONE disposable unit with
    the harness worktree it concludes -- `is_paired` unconditionally
    blocking `conclude_disposable_worktree` forever (with no release path
    at all) left every dispatch-created pair permanently wedged. A
    concluding dispatch attempt now disposes of BOTH halves together
    (`_dispatch_pair_clearance` + `_conclude_pair_sibling`), never just the
    harness side (which would orphan the knowledge sibling), and never
    unconditionally (a paired human/CLI conclusion, or an unmatched
    dispatch attempt, still preserves exactly as before)."""

    def _harness(self, tmp_path, monkeypatch, *, pair_ref, pair_kind="worktree"):
        repo, record_path, worktree = _worker(tmp_path, monkeypatch)
        record = tracking.load_record(record_path)
        record.origin = "delegate"
        record.dispatch_attempt = tracking.DispatchAttempt(
            task_id="task-1",
            reservation_key="dispatch-task:task-1:1",
            attempt=1,
            driver="dispatcher",
            supervisor="supervisor-1",
            creator_machine=record.machine,
        )
        record.pair_id = "pair1"
        record.pair_role = "harness"
        record.pair_ref = pair_ref
        record.pair_kind = pair_kind
        tracking.save_record(record, record_path)
        return repo, record_path, worktree

    def test_concludes_both_halves_of_a_clean_pair(self, tmp_path, monkeypatch):
        sibling, sibling_path, _sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
        )
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", sibling.worktree_id,
            ),
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "primed"
        assert result["pair_sibling"]["action"] == "primed"
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "bridge"
        assert harness_record.status == "complete"
        # Discharged: both halves are jointly concluded, so the pairing
        # link itself is cleared -- otherwise the managed sweep's own
        # paired-worktree guard would strand both records forever.
        assert not harness_record.is_paired
        sibling_record = tracking.load_record(sibling_path)
        assert sibling_record.kind == "bridge"
        assert sibling_record.status == "complete"
        assert not sibling_record.is_paired

    def test_blocks_on_sibling_with_unpushed_commits(self, tmp_path, monkeypatch):
        """A sibling with no porcelain dirtiness but local-only commits ahead
        of its own upstream must still block -- exactly like the harness
        side's own ahead-count gate."""
        sibling, sibling_path, sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
        )
        (sibling_worktree / "unpushed.txt").write_text("wip\n", encoding="utf-8")
        _git(sibling_worktree, "add", "-A")
        _git(sibling_worktree, "commit", "-m", "local-only work")
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", sibling.worktree_id,
            ),
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "skipped"
        assert result["reason"] == "pair-sibling-local-commits"
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "session"
        sibling_record = tracking.load_record(sibling_path)
        assert sibling_record.kind == "session"

    def test_conclude_pair_sibling_revalidates_full_identity_under_lock(
        self, tmp_path, monkeypatch,
    ):
        """Regression (Copilot review finding): `_conclude_pair_sibling`
        must re-run the FULL reciprocal-pair validation while holding the
        sibling's own lock, not just re-check `worktree_id == path.stem`.
        Simulates a race by retargeting the sibling's role after the
        (unlocked) preliminary clearance already ran."""
        sibling, sibling_path, _sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
        )
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", sibling.worktree_id,
            ),
        )
        record = tracking.load_record(record_path)
        original_resolve = tc._resolve_pair_sibling
        stale_resolution = (sibling, sibling_path)

        # A concurrent rewrite breaks the reciprocal link between the
        # preliminary (unlocked) resolve and the locked re-check below.
        # Force the preliminary resolve to return the STALE (pre-mutation)
        # result so the locked block is the one that must catch it.
        monkeypatch.setattr(tc, "_resolve_pair_sibling", lambda _r: stale_resolution)
        mutated_sibling = tracking.load_record(sibling_path)
        mutated_sibling.pair_role = "harness"  # no longer complementary
        tracking.save_record(mutated_sibling, sibling_path)

        outcome = tc._conclude_pair_sibling(record)
        monkeypatch.setattr(tc, "_resolve_pair_sibling", original_resolve)

        assert outcome == {
            "action": "skipped",
            "reason": "pair-sibling-identity-mismatch",
        }
        sibling_record = tracking.load_record(sibling_path)
        assert sibling_record.kind == "session"

    def test_blocks_on_dirty_knowledge_sibling(self, tmp_path, monkeypatch):
        sibling, sibling_path, _sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
            dirty=True,
        )
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", sibling.worktree_id,
            ),
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "skipped"
        assert result["reason"] == "pair-sibling-dirty-work"
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "session"
        sibling_record = tracking.load_record(sibling_path)
        assert sibling_record.kind == "session"

    def test_unresolvable_sibling_blocks_the_whole_pair(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tc.cfg, "project_dir", lambda name=None: tmp_path / f".{name}",
        )
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", "does-not-exist",
            ),
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "skipped"
        assert result["reason"] == "pair-sibling-unresolved"

    def test_non_reciprocal_link_blocks_the_whole_pair(self, tmp_path, monkeypatch):
        """Regression (Copilot review finding): a record whose `pair_ref`
        resolves to an EXISTING worktree that is not genuinely its
        reciprocal sibling (e.g. a stale/malformed link pointing at some
        unrelated tracked worktree that never links back) must never be
        treated as safe to dispose of -- this is a destructive path."""
        unrelated, _unrelated_path, _unrelated_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="some-other-harness",  # does NOT match this test's harness
            harness_worktree_id="not-this-worktree",
        )
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", unrelated.worktree_id,
            ),
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "skipped"
        assert result["reason"] == "pair-sibling-unresolved"
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "session"
        unrelated_record = tracking.load_record(_unrelated_path)
        assert unrelated_record.kind == "session"

    def test_disposable_cli_policy_still_blocks_unconditionally_on_pairing(
        self, tmp_path, monkeypatch,
    ):
        """The reciprocal-disposal fix is scoped to dispatch-attempt
        conclusions only -- a plain CLI/human conclusion of a paired
        worktree must keep behaving exactly as before (unconditional
        block), never silently reach for a sibling to dispose of too."""
        sibling, _sibling_path, _sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
        )
        repo, record_path, _worktree = _worker(tmp_path, monkeypatch)
        record = tracking.load_record(record_path)
        record.pair_id = "pair1"
        record.pair_role = "harness"
        record.pair_ref = tracking.format_claim_ref(
            "host", "knowledge-proj", sibling.worktree_id,
        )
        record.pair_kind = "worktree"
        tracking.save_record(record, record_path)

        result = _conclude(record_path, repo, policy=tc.DISPOSABLE_CLI_POLICY)

        assert result["action"] == "skipped"
        assert result["reason"] == "outstanding-obligations"
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "session"

    def test_unit_pair_clearance_is_scoped_to_dispatch_attempt_policy(
        self, tmp_path, monkeypatch,
    ):
        """Direct unit check: `_dispatch_pair_clearance` must return
        ``(False, None)`` -- deferring entirely to `_preservation_reason`'s
        ordinary unconditional `is_paired` block -- for a paired record
        under ANY policy other than `DISPATCH_ATTEMPT_POLICY`, even one
        whose (irrelevant, for this policy) dispatch provenance would
        otherwise match."""
        sibling, _sibling_path, _sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
        )
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", sibling.worktree_id,
            ),
        )
        record = tracking.load_record(record_path)

        pair_clear, block_reason = tc._dispatch_pair_clearance(
            record,
            policy=tc.DISPOSABLE_CLI_POLICY,
            reservation_key="dispatch-task:task-1:1",
            owner="dispatcher",
        )

        assert (pair_clear, block_reason) == (False, None)
        assert tc._preservation_reason(
            record, policy=tc.DISPOSABLE_CLI_POLICY, pair_clear=pair_clear,
        ) == "outstanding-obligations"

    def test_blocks_on_sibling_active_lifecycle_head(self, tmp_path, monkeypatch):
        """A sibling with an ENDED-but-still-resumable tracked lifecycle
        head must block -- independent of `_session_is_live`, which only
        detects a currently-running process."""
        sibling, sibling_path, _sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
        )
        sibling_record = tracking.load_record(sibling_path)
        sibling_record.sessions = [
            tracking.SessionEntry(
                session_id="sibling-session", started_at="2026-09-01T00:00:00Z",
            )
        ]
        sibling_record.head_session = "sibling-session"
        tracking.save_record(sibling_record, sibling_path)
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", sibling.worktree_id,
            ),
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "skipped"
        assert result["reason"] == "pair-sibling-active-lifecycle-head"
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "session"

    def test_blocks_on_missing_sibling_checkout_with_unpushed_branch(
        self, tmp_path, monkeypatch,
    ):
        """Regression (Copilot review finding): even when the sibling's own
        worktree directory is entirely gone, its branch may still exist
        (git worktrees share refs/objects with their anchor) with unpushed
        commits -- this must still block, mirroring the harness side's own
        missing-checkout fallback."""
        sibling, sibling_path, sibling_worktree = _pair_sibling(
            tmp_path,
            monkeypatch,
            project="knowledge-proj",
            harness_machine="host",
            harness_project="demo",
            harness_worktree_id="worker-20260901-abcd",
        )
        (sibling_worktree / "unpushed.txt").write_text("wip\n", encoding="utf-8")
        _git(sibling_worktree, "add", "-A")
        _git(sibling_worktree, "commit", "-m", "local-only work")
        import shutil as _shutil

        _shutil.rmtree(sibling_worktree)

        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref(
                "host", "knowledge-proj", sibling.worktree_id,
            ),
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "skipped"
        assert result["reason"] == "pair-sibling-local-commits"
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "session"

    def test_anchor_pair_concludes_harness_and_discharges_link_only(
        self, tmp_path, monkeypatch,
    ):
        """An anchor-kind pair (a non-worktree-class/singleton knowledge
        repo) has no disposable sibling worktree at all -- only the
        harness side concludes and discharges its own pairing link; the
        (nonexistent, shared) anchor is never touched."""
        repo, record_path, _worktree = self._harness(
            tmp_path,
            monkeypatch,
            pair_ref=tracking.format_claim_ref("host", "knowledge-proj", "anchor"),
            pair_kind="anchor",
        )

        result = _conclude(
            record_path,
            repo,
            policy=tc.DISPATCH_ATTEMPT_POLICY,
            reservation_key="dispatch-task:task-1:1",
        )

        assert result["action"] == "primed"
        assert result["pair_sibling"] == {
            "action": "not-applicable",
            "reason": "anchor-pair",
        }
        harness_record = tracking.load_record(record_path)
        assert harness_record.kind == "bridge"
        assert harness_record.status == "complete"
        assert not harness_record.is_paired
