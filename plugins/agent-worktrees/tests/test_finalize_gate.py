"""Tests for the finalize obligation gate (resource-obligation-settlement Ph2)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_worktrees import obligations as ob
from agent_worktrees.finalize import _assert_obligations_settled
from agent_worktrees.tracking import ResourceClaim


def _record(*states: str) -> SimpleNamespace:
    """A duck-typed record carrying only the ``resources`` the gate reads."""
    return SimpleNamespace(
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


def test_off_mode_skips_even_with_unsettled(monkeypatch):
    _gate(monkeypatch, "off")
    rec = _record("active")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True


def test_warn_mode_proceeds_and_lists(monkeypatch, capsys):
    _gate(monkeypatch, "warn")
    rec = _record("active", "at-rest")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True
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
    assert _assert_obligations_settled(rec, "wt", abandon=True) is True
    combined = capsys.readouterr()
    assert "abandon" in (combined.err + combined.out).lower()


def test_only_unsettled_claims_count(monkeypatch):
    # at-rest + released are settled -> a record with only those never blocks.
    _gate(monkeypatch, "block")
    rec = _record("at-rest", "released", "at-rest")
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True


# ── self-heal (never-wedge, dotfiles#1161) ───────────────────────────────────

def _worktree_record(*states):
    from agent_worktrees.tracking import ResourceClaim
    return SimpleNamespace(
        resources=[ResourceClaim(kind="worktree", ref=f"m/p/c{i}", state=s)
                   for i, s in enumerate(states)])


def test_gate_self_heals_gone_and_safe_then_proceeds(monkeypatch, capsys):
    _gate(monkeypatch, "block")
    rec = _worktree_record("active")
    from agent_worktrees import config as cfg
    from agent_worktrees import sweep

    def _fake_heal(record, config, *, path=None, save=True):
        record.resources[0].state = "abandoned"   # provably gone + safe
        return [record.resources[0]]

    monkeypatch.setattr(sweep, "self_heal", _fake_heal)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cfg, "tracking_dir",
                        lambda: __import__("pathlib").Path("."))
    # Under block, the lone blocking claim is auto-reclaimed -> finalize proceeds.
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True
    out = capsys.readouterr()
    assert "auto-reclaimed" in (out.err + out.out).lower()


def test_gate_still_blocks_when_self_heal_reclaims_nothing(monkeypatch):
    _gate(monkeypatch, "block")
    rec = _worktree_record("active")
    from agent_worktrees import config as cfg
    from agent_worktrees import sweep
    monkeypatch.setattr(sweep, "self_heal", lambda *a, **k: [])
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cfg, "tracking_dir",
                        lambda: __import__("pathlib").Path("."))
    # A genuinely-active claim that the sweep spares still blocks.
    assert _assert_obligations_settled(rec, "wt", abandon=False) is False


def test_gate_self_heal_failure_never_breaks_finalize(monkeypatch):
    _gate(monkeypatch, "block")
    rec = _worktree_record("at-rest")   # nothing unsettled
    from agent_worktrees import config as cfg
    monkeypatch.setattr(cfg, "load_config",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    # load_config blowing up is swallowed; the gate still evaluates normally.
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True
