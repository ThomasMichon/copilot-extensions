"""Tests for the finalize obligation gate (resource-obligation-settlement Ph2)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_worktrees import obligations as ob
from agent_worktrees.__main__ import build_parser
from agent_worktrees.finalize import _assert_obligations_settled
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
