"""Tests for the finalize obligation gate (resource-obligation-settlement Ph2)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_worktrees import obligations as ob
from agent_worktrees import tracking
from agent_worktrees.__main__ import build_parser
from agent_worktrees.finalize import (
    _advise_other_live_sessions,
    _assert_obligations_settled,
    _settle_current_session_claim,
)
from agent_worktrees.tracking import ResourceClaim


def _record(*states: str) -> SimpleNamespace:
    """A duck-typed record carrying only the ``resources`` the gate reads."""
    return SimpleNamespace(
        machine="m", repo="p", worktree_id="wt",
        resources=[ResourceClaim(kind="codespace", ref=f"cs-{i}", state=s)
                   for i, s in enumerate(states)],
    )


def _gate(monkeypatch, mode: str) -> None:
    monkeypatch.setenv(ob.GATE_ENV, mode)


def test_no_record_proceeds(monkeypatch):
    _gate(monkeypatch, "block")
    assert _assert_obligations_settled(None, "wt", abandon=False) is True


def test_no_unsettled_proceeds(monkeypatch):
    _gate(monkeypatch, "block")
    rec = _record("at-rest", "released")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True


def test_live_session_claim_never_blocks(monkeypatch):
    """A "session" claim (Phase 8) never hard-blocks finalize, even ``active``."""
    _gate(monkeypatch, "block")
    rec = SimpleNamespace(
        machine="m", repo="p", worktree_id="wt",
        resources=[
            ResourceClaim(kind="session", ref="m/p/wt#sess-1", state="active"),
        ],
    )
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True


def test_live_session_claim_does_not_mask_other_unsettled(monkeypatch, capsys):
    """A live session claim doesn't excuse an unrelated unsettled claim."""
    _gate(monkeypatch, "block")
    rec = SimpleNamespace(
        machine="m", repo="p", worktree_id="wt",
        resources=[
            ResourceClaim(kind="session", ref="m/p/wt#sess-1", state="active"),
            ResourceClaim(kind="codespace", ref="cs-0", state="active"),
        ],
    )
    assert _assert_obligations_settled(rec, "wt", abandon=False) is False
    captured = capsys.readouterr()
    assert "codespace: cs-0" in captured.out
    assert "session:" not in captured.out


def test_advise_other_live_sessions_warns_but_never_blocks(capsys):
    """The advisory pass names OTHER live sessions but always proceeds."""
    rec = SimpleNamespace(
        worktree_id="wt",
        resources=[
            ResourceClaim(kind="session", ref="m/p/wt#current", state="active"),
            ResourceClaim(kind="session", ref="m/p/wt#other", state="active"),
        ],
    )
    _advise_other_live_sessions(rec, "m/p/wt#current")
    captured = capsys.readouterr()
    assert "m/p/wt#other" in captured.out
    assert "m/p/wt#current" not in captured.out


def test_advise_other_live_sessions_silent_when_none_other(capsys):
    rec = SimpleNamespace(
        worktree_id="wt",
        resources=[
            ResourceClaim(kind="session", ref="m/p/wt#current", state="active"),
        ],
    )
    _advise_other_live_sessions(rec, "m/p/wt#current")
    captured = capsys.readouterr()
    assert captured.out == ""


def test_advise_other_live_sessions_handles_no_record():
    _advise_other_live_sessions(None, None)  # must not raise


def _tracking_record(tmp_tracking_dir: Path, **overrides) -> tracking.WorktreeRecord:
    base = dict(
        worktree_id="wt-settle",
        branch="worktree/wt-settle",
        worktree_path="/tmp/wt-settle",
        repo="p",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )
    base.update(overrides)
    rec = tracking.WorktreeRecord(**base)
    tracking.save_record(rec, tmp_tracking_dir / f"{rec.worktree_id}.yaml")
    return rec


def test_settle_current_session_claim_settles_active_claim(
    tmp_tracking_dir: Path, monkeypatch_config,
):
    ref = "m/p/wt-settle#sess-1"
    rec = _tracking_record(
        tmp_tracking_dir,
        resources=[ResourceClaim(kind="session", ref=ref, state="active")],
    )
    yaml_path = tmp_tracking_dir / "wt-settle.yaml"

    result_record, result_ref = _settle_current_session_claim(
        yaml_path, rec, "sess-1",
    )

    assert result_ref == ref
    claim = next(c for c in result_record.resources if c.ref == ref)
    assert claim.state == "at-rest"


def test_settle_current_session_claim_never_resurrects_released(
    tmp_tracking_dir: Path, monkeypatch_config,
):
    """A `sessionEnd`-released claim must stay released, never bounce back."""
    ref = "m/p/wt-settle#sess-1"
    rec = _tracking_record(
        tmp_tracking_dir,
        resources=[ResourceClaim(kind="session", ref=ref, state="released")],
    )
    yaml_path = tmp_tracking_dir / "wt-settle.yaml"

    result_record, result_ref = _settle_current_session_claim(
        yaml_path, rec, "sess-1",
    )

    assert result_ref == ref
    claim = next(c for c in result_record.resources if c.ref == ref)
    assert claim.state == "released"


def test_settle_current_session_claim_no_session_id_is_noop(
    tmp_tracking_dir: Path, monkeypatch_config,
):
    rec = _tracking_record(tmp_tracking_dir, resources=[])
    yaml_path = tmp_tracking_dir / "wt-settle.yaml"

    result_record, result_ref = _settle_current_session_claim(yaml_path, rec, None)

    assert result_ref is None
    assert result_record is rec


def test_settle_current_session_claim_no_record_is_noop(tmp_tracking_dir: Path):
    result_record, result_ref = _settle_current_session_claim(
        tmp_tracking_dir / "missing.yaml", None, "sess-1",
    )
    assert result_record is None
    assert result_ref is None


def test_off_mode_cannot_bypass_creator_ownership(monkeypatch):
    _gate(monkeypatch, "off")
    rec = _record("active")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is False


def test_warn_mode_cannot_bypass_creator_ownership(monkeypatch, capsys):
    _gate(monkeypatch, "warn")
    rec = _record("active", "at-rest")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is False
    cap = capsys.readouterr()
    combined = (cap.err + cap.out).lower()
    assert "unsettled" in combined and "cs-0" in combined


def test_block_is_the_default_mode(monkeypatch, capsys):
    monkeypatch.delenv(ob.GATE_ENV, raising=False)
    rec = _record("active")
    # Default is now block: an unsettled obligation refuses finalize.
    assert _assert_obligations_settled(rec, "wt", abandon=False) is False
    out = capsys.readouterr()
    assert "blocked" in (out.err + out.out).lower()


def test_block_mode_refuses_unsettled(monkeypatch, capsys):
    _gate(monkeypatch, "block")
    rec = _record("active")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is False
    out = capsys.readouterr()
    assert "blocked" in (out.err + out.out).lower()


def test_block_mode_abandon_overrides(monkeypatch, capsys):
    _gate(monkeypatch, "block")
    rec = _record("active")
    assert _assert_obligations_settled(
        rec, "wt", abandon=True, handoff_to="operator-flow") is True
    combined = capsys.readouterr()
    assert "abandon" in (combined.err + combined.out).lower()


def test_block_mode_abandon_requires_affirmative_handoff(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_WORKTREES_OBLIGATION_GATE", "block")
    rec = _record("active")
    assert _assert_obligations_settled(rec, "wt", abandon=True) is False
    combined = capsys.readouterr()
    assert "affirmative handoff" in (combined.err + combined.out).lower()


def test_finalize_parser_accepts_named_handoff_target():
    args = build_parser().parse_args([
        "finalize", "wt", "--abandon", "--handoff-to", "operator-flow"])
    assert args.abandon is True
    assert args.handoff_to == "operator-flow"


def test_pending_resource_creation_cannot_be_handed_off(monkeypatch, capsys):
    _gate(monkeypatch, "block")
    rec = SimpleNamespace(machine="m", repo="p", worktree_id="wt", resources=[
        ResourceClaim(
            kind="workdir", ref="pending-run:abc", state="active")
    ])
    assert _assert_obligations_settled(
        rec, "wt", abandon=True, handoff_to="operator-flow") is False
    combined = capsys.readouterr()
    assert "in-flight resource creation" in (
        combined.err + combined.out).lower()


def test_only_unsettled_claims_count(monkeypatch):
    # at-rest + released are settled -> a record with only those never blocks.
    _gate(monkeypatch, "block")
    rec = _record("at-rest", "released", "at-rest")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True


# ── self-heal (never-wedge, dotfiles#1161) ───────────────────────────────────

def _worktree_record(*states):
    from agent_worktrees.tracking import ResourceClaim
    return SimpleNamespace(
        machine="m", repo="p", worktree_id="wt",
        resources=[ResourceClaim(kind="worktree", ref=f"m/p/c{i}", state=s)
                   for i, s in enumerate(states)])


def test_gate_never_auto_reclaims_creator_obligations(monkeypatch):
    _gate(monkeypatch, "block")
    rec = _worktree_record("active")
    from agent_worktrees import sweep
    monkeypatch.setattr(
        sweep, "self_heal",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("finalize must not auto-reclaim")))
    assert _assert_obligations_settled(rec, "wt", abandon=False) is False


def test_gate_settled_claims_proceed_without_reclaim(monkeypatch):
    _gate(monkeypatch, "block")
    rec = _worktree_record("at-rest")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True
