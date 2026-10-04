"""Tests for :mod:`claim_history` (Plan Phase 3b, partial slice, of the
``worktree-claims-transitive-finalization`` effort): the durable,
append-only ownership-history ledger for a claimed resource, and its CLI
rendering (``claims history <ref>``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import (
    claim_history,
    claims_history_cli,
    finalize,
    obligations,
    tracking,
    tracking_claim_write,
    tracking_write,
)
from agent_worktrees import config as cfg


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


# ── record_event / history_for_ref primitives ────────────────────────────

def test_record_event_is_a_noop_for_an_unsupported_kind():
    claim_history.record_event(
        kind="codespace", ref="cs-1", worktree_id="wt-a", machine="m",
        event="claimed",
    )
    assert claim_history.history_for_ref("cs-1") == []
    assert not claim_history.history_path().exists()


def test_record_event_appends_a_pr_kind_entry():
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m",
        event="claimed", session_id="sess-1", note="opened",
    )
    events = claim_history.history_for_ref("o/r#1")
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "pr"
    assert e["ref"] == "o/r#1"
    assert e["worktree_id"] == "wt-a"
    assert e["machine"] == "m"
    assert e["event"] == "claimed"
    assert e["session_id"] == "sess-1"
    assert e["note"] == "opened"
    assert "ts" in e


def test_history_for_ref_filters_by_ref_and_preserves_order():
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m", event="claimed",
    )
    claim_history.record_event(
        kind="pr", ref="o/r#2", worktree_id="wt-b", machine="m", event="claimed",
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m", event="settled",
    )
    events = claim_history.history_for_ref("o/r#1")
    assert [e["event"] for e in events] == ["claimed", "settled"]


def test_history_for_ref_returns_empty_list_for_missing_file():
    assert claim_history.history_for_ref("o/r#404") == []


def test_history_for_ref_skips_unparseable_lines(monkeypatch, tmp_path: Path):
    path = tmp_path / "claim-history.jsonl"
    path.write_text(
        "not json at all\n"
        + json.dumps({"ref": "o/r#1", "event": "claimed"}) + "\n"
    )
    monkeypatch.setattr(claim_history, "history_path", lambda: path)
    events = claim_history.history_for_ref("o/r#1")
    assert len(events) == 1
    assert events[0]["event"] == "claimed"


def test_history_for_ref_skips_parseable_non_dict_lines(monkeypatch, tmp_path: Path):
    """A line can be valid JSON ([]/null/a string) without being an object
    -- `.get()` on any of those must never raise."""
    path = tmp_path / "claim-history.jsonl"
    path.write_text(
        json.dumps([]) + "\n"
        + json.dumps(None) + "\n"
        + json.dumps("just a string") + "\n"
        + json.dumps({"ref": "o/r#1", "event": "claimed"}) + "\n"
    )
    monkeypatch.setattr(claim_history, "history_path", lambda: path)
    events = claim_history.history_for_ref("o/r#1")
    assert len(events) == 1
    assert events[0]["event"] == "claimed"


def test_record_event_never_raises_on_write_failure(monkeypatch):
    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(claim_history.handoff_trace, "_append_lock", _boom)
    before = claim_history.write_failure_count()
    # Must not raise -- best-effort, same contract as activity.log_event.
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m", event="claimed",
    )
    # ... but a failed write is still observable, not silently lost.
    assert claim_history.write_failure_count() == before + 1


def test_history_for_ref_tolerates_invalid_utf8(tmp_path: Path, monkeypatch):
    """Invalid UTF-8 raises UnicodeDecodeError, not OSError -- a naive
    strict-decode read would crash `claims history` instead of skipping
    the damaged line and returning the readable ones."""
    path = tmp_path / "claim-history.jsonl"
    with open(path, "wb") as handle:
        handle.write(b"\xff\xfe not valid utf-8\n")
        handle.write(json.dumps({"ref": "o/r#1", "event": "claimed"}).encode() + b"\n")
    monkeypatch.setattr(claim_history, "history_path", lambda: path)
    events = claim_history.history_for_ref("o/r#1")
    assert len(events) == 1
    assert events[0]["event"] == "claimed"


def test_current_session_id_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-xyz")
    assert claim_history.current_session_id() == "sess-xyz"
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    assert claim_history.current_session_id() is None


def test_record_claim_released_convenience(monkeypatch):
    claim = tracking.ResourceClaim(kind="pr", ref="o/r#8", state=obligations.RELEASED)
    claim_history.record_claim_released(claim, worktree_id="wt-a", machine="m", note="x")
    events = claim_history.history_for_ref("o/r#8")
    assert events[0]["event"] == "released"
    assert events[0]["note"] == "x"


def test_record_pr_event_convenience(monkeypatch):
    claim_history.record_pr_event(
        "o/r#8", worktree_id="wt-a", machine="m", event="claimed",
    )
    events = claim_history.history_for_ref("o/r#8")
    assert events[0]["kind"] == "pr"
    assert events[0]["event"] == "claimed"


# ── Wiring: tracking_claim_write's three verbs feed claim_history ───────

@pytest.fixture
def record_path(tmp_tracking_dir: Path) -> Path:
    path = tmp_tracking_dir / "wt-claim.yaml"
    tracking.create_new_record(
        "wt-claim", "worktree/wt-claim", "/tmp/wt-claim", "example",
        "machine-x", "wsl", tmp_tracking_dir,
    )
    return path


def test_claim_add_feeds_history_for_pr_kind(record_path):
    tracking_claim_write.apply_claim_add({
        "worktree_id": "wt-claim", "yaml_path": str(record_path),
        "kind": "pr", "ref": "o/r#9",
    })
    events = claim_history.history_for_ref("o/r#9")
    assert len(events) == 1
    assert events[0]["event"] == "claimed"
    assert events[0]["worktree_id"] == "wt-claim"
    assert events[0]["machine"] == "machine-x"


def test_claim_add_uses_the_caller_supplied_session_id(record_path):
    """The verb normally runs in the resident daemon, whose own
    environment explicitly strips COPILOT_AGENT_SESSION_ID -- the CLI
    caller's session must therefore be threaded through `args`, not
    re-resolved inside the verb from (the daemon's own) environment."""
    tracking_claim_write.apply_claim_add({
        "worktree_id": "wt-claim", "yaml_path": str(record_path),
        "kind": "pr", "ref": "o/r#9", "session_id": "caller-session-123",
    })
    events = claim_history.history_for_ref("o/r#9")
    assert events[0]["session_id"] == "caller-session-123"


def test_claim_add_does_not_feed_history_for_non_pr_kind(record_path):
    tracking_claim_write.apply_claim_add({
        "worktree_id": "wt-claim", "yaml_path": str(record_path),
        "kind": "codespace", "ref": "cs-1",
    })
    assert claim_history.history_for_ref("cs-1") == []


def test_claim_release_feeds_history(record_path):
    tracking_claim_write.apply_claim_add({
        "worktree_id": "wt-claim", "yaml_path": str(record_path),
        "kind": "pr", "ref": "o/r#9",
    })
    tracking_claim_write.apply_claim_release({
        "worktree_id": "wt-claim", "yaml_path": str(record_path), "ref": "o/r#9",
    })
    events = claim_history.history_for_ref("o/r#9")
    assert [e["event"] for e in events] == ["claimed", "released"]


def test_claim_settle_feeds_history_with_disposition_as_note(record_path):
    tracking_claim_write.apply_claim_add({
        "worktree_id": "wt-claim", "yaml_path": str(record_path),
        "kind": "pr", "ref": "o/r#9",
    })
    tracking_claim_write.apply_claim_settle({
        "worktree_id": "wt-claim", "yaml_path": str(record_path),
        "ref": "o/r#9", "disposition": obligations.AT_REST,
    })
    events = claim_history.history_for_ref("o/r#9")
    assert events[-1]["event"] == "settled"
    assert events[-1]["note"] == obligations.AT_REST


def test_release_all_resources_does_not_itself_feed_history(record_path):
    """`release_all_resources(save=False)` must NOT emit a history event on
    its own -- it is routinely called with ``save=False`` (finalize folds
    the persist into its own later save), and emitting history before
    anything is durable would misrecord a transition a later save failure
    could silently undo. The caller (`finalize.py`) is responsible for
    recording history only after its own save is confirmed -- see
    `test_finalize_feeds_claim_history_after_release` below."""
    rec = tracking.load_record(record_path)
    rec.resources = [
        tracking.ResourceClaim(kind="pr", ref="o/r#3", state=obligations.ACTIVE),
    ]
    tracking.release_all_resources(rec, save=False)
    assert claim_history.history_for_ref("o/r#3") == []


# ── Wiring: a context-handoff cutover feeds an *implicit* reassignment
# (worktree-claims-transitive-finalization Phase 3b -- "an agent-bridge
# session rebind to a worktree" changes who is actually working a PR right
# now without any explicit claim verb ever firing) ──────────────────────

def test_link_handoff_default_save_feeds_history_after_its_own_save(record_path):
    """``link_handoff``'s default ``save=True`` is self-contained: it may
    record_pr_claims_reassigned() itself, but only AFTER its own
    ``save_record`` already confirmed."""
    rec = tracking.load_record(record_path)
    rec.sessions = [
        tracking.SessionEntry("old-session", "t"),
        tracking.SessionEntry("new-session", "t"),
    ]
    tracking.save_record(rec, record_path)
    rec = tracking.load_record(record_path)
    tracking.add_resource_claim(
        rec,
        tracking.ResourceClaim(kind="pr", ref="o/r#9", state=obligations.ACTIVE),
        save=False,
    )
    tracking.open_handoff(rec, "old-session", "token", save=False)
    tracking.save_record(rec, record_path)
    rec = tracking.load_record(record_path)
    tracking.link_handoff(rec, "token", "new-session")  # save=True (default)
    events = claim_history.history_for_ref("o/r#9")
    assert [e["event"] for e in events] == ["reassigned"]
    assert events[0]["session_id"] == "new-session"
    assert events[0]["worktree_id"] == "wt-claim"
    assert "old-session -> new-session" in events[0]["note"]


def test_link_handoff_save_false_never_feeds_history_itself(record_path):
    """A ``save=False`` caller defers its own save (e.g. a batched daemon
    verb transaction) -- ``link_handoff`` must NEVER record history before
    the caller's own save is confirmed, mirroring
    ``test_release_all_resources_does_not_itself_feed_history``. A save
    failure (or the caller's whole transaction aborting after this call)
    must never leave a false "reassigned" entry for a link that never
    durably persisted; the caller owns calling
    ``record_pr_claims_reassigned`` itself, once ITS OWN save confirms."""
    rec = tracking.load_record(record_path)
    rec.sessions = [
        tracking.SessionEntry("old-session", "t"),
        tracking.SessionEntry("new-session", "t"),
    ]
    tracking.add_resource_claim(
        rec,
        tracking.ResourceClaim(kind="pr", ref="o/r#9", state=obligations.ACTIVE),
        save=False,
    )
    tracking.open_handoff(rec, "old-session", "token", save=False)
    tracking.link_handoff(rec, "token", "new-session", save=False)
    assert claim_history.history_for_ref("o/r#9") == []


def test_record_pr_claims_reassigned_feeds_history_for_pr_kind_claims():
    rec = tracking.WorktreeRecord(
        worktree_id="wt-claim", branch="b", worktree_path="/tmp/wt-claim",
        repo="r", machine="machine-x", platform="wsl",
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
    )
    rec.resources = [
        tracking.ResourceClaim(kind="pr", ref="o/r#9", state=obligations.ACTIVE),
    ]
    tracking.record_pr_claims_reassigned(
        rec,
        predecessor_session_id="old-session",
        successor_session_id="new-session",
        note="context-handoff linked",
    )
    events = claim_history.history_for_ref("o/r#9")
    assert [e["event"] for e in events] == ["reassigned"]
    assert events[0]["session_id"] == "new-session"
    assert events[0]["worktree_id"] == "wt-claim"
    assert events[0]["machine"] == "machine-x"
    assert "old-session -> new-session" in events[0]["note"]


def test_record_pr_claims_reassigned_is_a_noop_with_no_active_pr_claim():
    rec = tracking.WorktreeRecord(
        worktree_id="wt-claim", branch="b", worktree_path="/tmp/wt-claim",
        repo="r", machine="machine-x", platform="wsl",
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
    )
    tracking.record_pr_claims_reassigned(
        rec, predecessor_session_id="old", successor_session_id="new", note="x",
    )
    assert claim_history.history_for_ref("o/r#9") == []


def test_record_pr_claims_reassigned_ignores_a_released_pr_claim():
    """Only a still-ACTIVE claim implies "someone is actively working this
    PR" -- an already-released claim must not be reported as reassigned."""
    rec = tracking.WorktreeRecord(
        worktree_id="wt-claim", branch="b", worktree_path="/tmp/wt-claim",
        repo="r", machine="machine-x", platform="wsl",
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
    )
    rec.resources = [
        tracking.ResourceClaim(kind="pr", ref="o/r#9", state=obligations.RELEASED),
    ]
    tracking.record_pr_claims_reassigned(
        rec, predecessor_session_id="old", successor_session_id="new", note="x",
    )
    assert claim_history.history_for_ref("o/r#9") == []


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def test_finalize_feeds_claim_history_after_release(tmp_path: Path, monkeypatch):
    """Real end-to-end proof: `finalize.validate_and_finalize` releasing a
    worktree's pr-kind claim feeds claim_history ONLY after its own save
    (`tracking.update_status`) is confirmed -- matching
    `test_release_all_resources_does_not_itself_feed_history` above, this
    is the other half of that invariant."""
    tracking_d = tmp_path / ".proj" / "worktrees"
    tracking_d.mkdir(parents=True)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tracking_d)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / ".proj")

    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "base", str(origin), cwd=tmp_path)
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    _git("init", "-q", "-b", "base", cwd=anchor)
    _git("config", "user.email", "t@x.com", cwd=anchor)
    _git("config", "user.name", "T", cwd=anchor)
    (anchor / "base.txt").write_text("base\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "base", cwd=anchor)
    _git("remote", "add", "origin", str(origin), cwd=anchor)
    _git("push", "-q", "origin", "base", cwd=anchor)

    repo_cfg = cfg.RepoConfig(
        anchor=str(anchor), worktree_root=str(tmp_path),
        default_branch="base", remote="origin",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="m", platform="linux",
        repo_name="proj", repos={"proj": repo_cfg},
    )

    rec = tracking.WorktreeRecord(
        worktree_id="wt-fin", branch="worktree/wt-fin",
        worktree_path=str(tmp_path / "gone-wt-fin"),
        repo="o/r", machine="m", platform="linux",
        started_at="2026-10-01T00:00:00", last_resumed_at="2026-10-01T00:00:00",
        resume_count=0, title=None, status="active", completed_at=None,
        resources=[tracking.ResourceClaim(kind="pr", ref="o/r#7", state=obligations.AT_REST)],
    )
    tracking.save_record(rec, tracking_d / "wt-fin.yaml")

    assert finalize.validate_and_finalize("wt-fin", config) is True

    events = claim_history.history_for_ref("o/r#7")
    assert [e["event"] for e in events] == ["released"]
    assert events[0]["note"] == "finalized"
    assert events[0]["worktree_id"] == "wt-fin"


# ── CLI rendering ─────────────────────────────────────────────────────

def _ns(**kwargs):
    import argparse
    return argparse.Namespace(**kwargs)


def test_cli_missing_ref_errors(capsys):
    rc = claims_history_cli.cmd_claims_history(
        _ns(json=False), None, json_error=lambda *a, **k: 2, json_output=lambda *a: None,
    )
    assert rc == 2
    assert "missing" in capsys.readouterr().out


def test_cli_renders_empty_history(capsys):
    rc = claims_history_cli.cmd_claims_history(
        _ns(json=False), "o/r#404",
        json_error=lambda *a, **k: 2, json_output=lambda *a: None,
    )
    assert rc == 0
    assert "no covered transition recorded" in capsys.readouterr().out


def test_cli_renders_populated_history(capsys):
    claim_history.record_event(
        kind="pr", ref="o/r#5", worktree_id="wt-a", machine="m",
        event="claimed", session_id="sess-1",
    )
    rc = claims_history_cli.cmd_claims_history(
        _ns(json=False), "o/r#5",
        json_error=lambda *a, **k: 2, json_output=lambda *a: None,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "claimed" in out
    assert "wt-a" in out
    assert "sess-1" in out


def test_cli_json_mode(capsys):
    claim_history.record_event(
        kind="pr", ref="o/r#5", worktree_id="wt-a", machine="m", event="claimed",
    )
    captured: dict = {}

    def _json_output(payload):
        captured["payload"] = payload

    rc = claims_history_cli.cmd_claims_history(
        _ns(json=True), "o/r#5", json_error=lambda *a, **k: 2, json_output=_json_output,
    )
    assert rc == 0
    assert captured["payload"]["ref"] == "o/r#5"
    assert len(captured["payload"]["events"]) == 1


# ── _merge_events (the --remote local+mirrored merge) ───────────────────

def test_merge_events_preserves_a_repeated_local_transition_at_second_granularity():
    """``record_event`` timestamps only to the second, so a claim released
    and re-claimed by the same worktree/session within one second produces
    two "claimed" entries that share identical DISPLAY fields but distinct
    durable identities (different ``seq``). Every local event is kept
    as-is regardless -- merge only ever adds from the remote side."""
    local = [
        {"ts": "2026-10-03T12:00:00+00:00", "event": "claimed", "worktree_id": "wt-a",
         "machine": "m", "seq": 0, "ledger_id": "ledger-a"},
        {"ts": "2026-10-03T12:00:00+00:00", "event": "released", "worktree_id": "wt-a",
         "machine": "m", "seq": 1, "ledger_id": "ledger-a"},
        {"ts": "2026-10-03T12:00:00+00:00", "event": "claimed", "worktree_id": "wt-a",
         "machine": "m", "seq": 2, "ledger_id": "ledger-a"},
    ]
    merged = claims_history_cli._merge_events(local, remote=[])
    assert [e["event"] for e in merged] == ["claimed", "released", "claimed"]


def test_merge_events_collapses_only_an_identity_matched_remote_copy():
    """Matching DISPLAY fields never proves a remote event is the local
    event's own mirror -- only a matching ``(ledger_id, seq)`` identity
    does. An identical-looking copy from a genuinely different ledger
    incarnation must surface as a distinct event, not collapse."""
    local = [
        {"ts": "2026-10-03T12:00:00+00:00", "event": "claimed", "worktree_id": "wt-a",
         "machine": "m", "seq": 0, "ledger_id": "ledger-a"},
    ]
    self_mirror = dict(local[0])  # same identity -- collapses
    other_ledger = {**local[0], "ledger_id": "ledger-b"}  # different incarnation -- distinct
    merged = claims_history_cli._merge_events(local, [self_mirror, other_ledger])
    assert len(merged) == 2


def test_merge_events_keeps_every_remote_event_when_local_has_no_ledger_id():
    """A local ledger that has never been mirrored (no sidecar yet) can't
    durably vouch for ANY remote event as its own -- every remote event
    must surface rather than being guessed away by display-field luck."""
    local = [
        {"ts": "2026-10-03T12:00:00+00:00", "event": "claimed", "worktree_id": "wt-a",
         "machine": "m", "seq": 0, "ledger_id": None},
    ]
    remote = [dict(local[0], ledger_id="ledger-b")]
    merged = claims_history_cli._merge_events(local, remote)
    assert len(merged) == 2

