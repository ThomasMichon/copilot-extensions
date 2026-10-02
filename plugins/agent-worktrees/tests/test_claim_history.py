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

from agent_worktrees import claim_history, claims_history_cli
from agent_worktrees import config as cfg
from agent_worktrees import finalize
from agent_worktrees import obligations, tracking, tracking_claim_write, tracking_write


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
    # Must not raise -- best-effort, same contract as activity.log_event.
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m", event="claimed",
    )


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
