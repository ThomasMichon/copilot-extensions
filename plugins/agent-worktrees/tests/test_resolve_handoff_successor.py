"""Tests for ``resolve-handoff-successor`` (issue #4557): the sanctioned
repair for a terminal managed worktree whose real handoff successor was
never ``register-session``'d.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import output
from agent_worktrees import handoff_successor_repair_cli as repair_cli
from agent_worktrees import session_tracking_cli, sessions
from agent_worktrees.tracking_lifecycle import create_new_record, open_handoff
from agent_worktrees.tracking import SessionEntry, WorktreeRecord, load_record, save_record

_SEED_TEXT = (
    "Task: finish the thing | Resume: /consume-handoff to take over | "
    "Recovery: context-handoff task:abc123"
)


@pytest.fixture(autouse=True)
def registered_project(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_tracking_dir.parent)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    config = cfg.Config(
        srcroot=str(tmp_tracking_dir), machine="test", platform="wsl", repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(
            anchor=str(tmp_tracking_dir), worktree_root=str(tmp_tracking_dir / "trees"),
        )},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **kwargs: config)


def _terminal_bridge_record(tmp_tracking_dir: Path, *, worktree_path: str) -> WorktreeRecord:
    rec = create_new_record(
        "wt-1", "worktree/wt-1", worktree_path, "test-repo", "test", "wsl",
        tmp_tracking_dir, kind="bridge",
    )
    rec.status = "finalized"
    rec.completed_at = "2026-01-02T00:00:00"
    rec.sessions = [SessionEntry("predecessor", "2026-01-01T00:00:00")]
    save_record(rec, tmp_tracking_dir / "wt-1.yaml")
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    open_handoff(rec, "predecessor", "tok-1", save=True)
    return load_record(tmp_tracking_dir / "wt-1.yaml")


def _run(monkeypatch: pytest.MonkeyPatch, tracking_dir: Path, **ns: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(session_tracking_cli, "_all_tracking_dirs", lambda: [tracking_dir])
    monkeypatch.setattr(output, "_json_output", lambda data: captured.update(data))
    rc = repair_cli.cmd_resolve_handoff_successor(argparse.Namespace(json=True, **ns))
    captured["_rc"] = rc
    return captured


def _fake_meta(cwd: str, *, live: bool = False) -> dict[str, object]:
    return {
        "id": "successor", "name": "", "cwd": cwd, "branch": "",
        "created_at": "", "updated_at": "", "event_count": 3, "turn_count": 2,
        "live": live,
    }


def _user_event(text: str) -> dict[str, object]:
    return {"type": "user.message", "data": {"content": text}}


def _wire_evidence(
    monkeypatch: pytest.MonkeyPatch, *, cwd: str, live: bool = False,
    events: list[dict[str, object]] | None = None, valid_id: str | None = "successor",
) -> None:
    """Patch the sessions module's evidence surface for a candidate id."""
    monkeypatch.setattr(sessions, "validate_session_id", lambda sid: valid_id)
    monkeypatch.setattr(sessions, "_session_meta", lambda *a, **k: _fake_meta(cwd, live=live))
    default_events = [_user_event(_SEED_TEXT), _user_event("did the actual work")]
    monkeypatch.setattr(
        sessions, "read_session_transcript",
        lambda sid: events if events is not None else default_events,
    )


def test_resolves_stale_pending_handoff_with_matching_evidence(
    tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=checkout)
    _wire_evidence(monkeypatch, cwd=checkout)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] == 0
    assert out["pending_handoffs"] == []
    assert out["resolved_head_session"] is None
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    successor = rec.session_entry("successor")
    assert successor is not None and successor.state == "concluded"
    assert rec.handoffs[0].state == "linked"
    assert rec.handoffs[0].successor == "successor"


@pytest.mark.parametrize("foreign", ["platform", "namespace"])
def test_resolve_cli_reports_foreign_authority_without_success(
    tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch, foreign: str,
) -> None:
    rec = _terminal_bridge_record(tmp_tracking_dir, worktree_path=str(tmp_tracking_dir / "trees" / "wt-1"))
    before = rec.yaml_path.read_bytes()
    config = cfg.load_project_config("test-repo")
    if foreign == "platform":
        foreign_config = replace(config, platform="windows")
        expected = "receiving configuration belongs to a different execution platform"
    else:
        foreign_config = replace(config, repos={"test-repo": replace(
            config.default_repo, worktree_root=str(tmp_tracking_dir / "other-space"),
        )})
        expected = "record conflicts with the receiving worktree namespace"
    monkeypatch.setattr(cfg, "load_project_config", lambda *args, **kwargs: foreign_config)
    _wire_evidence(monkeypatch, cwd=rec.worktree_path)
    result = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )
    assert result["_rc"] == 1
    assert "pending_handoffs" not in result and "ok" not in result
    assert expected in result["error"]
    assert rec.yaml_path.read_bytes() == before


def test_refuses_when_cwd_does_not_match(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=str(tmp_tracking_dir / "trees" / "wt-1"))
    _wire_evidence(monkeypatch, cwd="/tmp/somewhere-else")

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None
    assert rec.handoffs[0].state == "pending"


def test_refuses_without_a_real_session_transcript(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=str(tmp_tracking_dir / "trees" / "wt-1"))
    monkeypatch.setattr(sessions, "validate_session_id", lambda sid: None)
    monkeypatch.setattr(sessions, "_session_meta", lambda *a, **k: None)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="ghost",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.handoffs[0].state == "pending"


def test_refuses_on_traversal_successor_id(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """issue-#4594-review: an unvalidated ``--successor`` must never reach
    the session-state filesystem lookup."""
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=str(tmp_tracking_dir / "trees" / "wt-1"))
    monkeypatch.setattr(sessions, "validate_session_id", lambda sid: None)
    called: list[int] = []

    def lookup(*args: object, **kwargs: object) -> None:
        called.append(1)

    monkeypatch.setattr(sessions, "_session_meta", lookup)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="../../etc/passwd",
    )

    assert out["_rc"] != 0
    assert called == [], "must refuse before ever looking up the raw id on disk"


def test_refuses_when_successor_session_is_still_live(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=checkout)
    _wire_evidence(monkeypatch, cwd=checkout, live=True)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_without_a_handoff_seed_first_turn(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cwd match alone doesn't prove the session consumed a handoff --
    require its first turn to actually be the handoff seed."""
    checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=checkout)
    _wire_evidence(
        monkeypatch, cwd=checkout,
        events=[_user_event("just an unrelated session"), _user_event("more work")],
    )

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_when_only_the_seed_and_no_real_work(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=checkout)
    _wire_evidence(monkeypatch, cwd=checkout, events=[_user_event(_SEED_TEXT)])

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_on_non_terminal_worktree(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    rec = _terminal_bridge_record(tmp_tracking_dir, worktree_path=checkout)
    rec.status = "active"
    rec.completed_at = None
    save_record(rec)
    _wire_evidence(monkeypatch, cwd=checkout)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0
    rec = load_record(tmp_tracking_dir / "wt-1.yaml")
    assert rec.session_entry("successor") is None


def test_refuses_when_successor_already_tracked(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    rec = _terminal_bridge_record(tmp_tracking_dir, worktree_path=checkout)
    assert rec.sessions is not None
    rec.sessions.append(SessionEntry("successor", "2026-01-01T01:00:00"))
    save_record(rec, tmp_tracking_dir / "wt-1.yaml")
    _wire_evidence(monkeypatch, cwd=checkout)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="tok-1", successor="successor",
    )

    assert out["_rc"] != 0


def test_refuses_on_non_pending_token(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    _terminal_bridge_record(tmp_tracking_dir, worktree_path=checkout)
    _wire_evidence(monkeypatch, cwd=checkout)

    out = _run(
        monkeypatch, tmp_tracking_dir,
        worktree_id="wt-1", token="unknown-token", successor="successor",
    )

    assert out["_rc"] != 0


def test_verb_refuses_when_worktree_path_changed_since_evidence_check(
    tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """review-#4594: a concurrent record repair/move between the caller's
    unlocked cwd verification and this locked dispatch must not let a
    stale-path evidence match through."""
    from agent_worktrees import tracking_session_lifecycle_write as verb_mod

    original_checkout = str(tmp_tracking_dir / "trees" / "wt-1")
    rec = _terminal_bridge_record(tmp_tracking_dir, worktree_path=original_checkout)
    yaml_path = tmp_tracking_dir / "wt-1.yaml"
    # Simulate the record's worktree_path having moved after the CLI verified
    # the successor's cwd against the OLD path but before this call.
    rec.worktree_path = str(tmp_tracking_dir / "moved" / "wt-1")
    save_record(rec, yaml_path)
    config = cfg.load_project_config("test-repo")
    config = replace(config, repos={"test-repo": replace(
        config.default_repo, worktree_root=str(tmp_tracking_dir / "moved"),
    )})
    monkeypatch.setattr(cfg, "load_project_config", lambda *args, **kwargs: config)

    result = verb_mod.apply_resolve_handoff_successor({
        "worktree_id": "wt-1",
        "yaml_path": str(yaml_path),
        "handoff_token": "tok-1",
        "successor_id": "successor",
        "expected_worktree_path": original_checkout,
    })

    assert result.get("error") == "lifecycle"
    rec = load_record(yaml_path)
    assert rec.session_entry("successor") is None
    assert rec.handoffs[0].state == "pending"
