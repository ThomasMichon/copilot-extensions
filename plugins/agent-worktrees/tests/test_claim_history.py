"""Tests for :mod:`claim_history` (Plan Phase 3b, partial slice, of the
``worktree-claims-transitive-finalization`` effort): the durable,
append-only ownership-history ledger for a claimed resource, and its CLI
rendering (``claims history <ref>``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import claim_history, claims_history_cli
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


def test_record_event_never_raises_on_write_failure(monkeypatch):
    monkeypatch.setattr(
        claim_history, "history_path", lambda: Path("/nonexistent-root/x/claim-history.jsonl")
    )
    # Must not raise -- best-effort, same contract as activity.log_event.
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m", event="claimed",
    )


def test_current_session_id_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "sess-xyz")
    assert claim_history.current_session_id() == "sess-xyz"
    monkeypatch.delenv("COPILOT_AGENT_SESSION_ID", raising=False)
    assert claim_history.current_session_id() is None


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
    assert "none recorded" in capsys.readouterr().out


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
