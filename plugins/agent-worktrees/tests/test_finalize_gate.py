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


def test_warn_is_the_default_mode(monkeypatch):
    monkeypatch.delenv(ob.GATE_ENV, raising=False)
    rec = _record("active")
    # Default (warn) proceeds.
    assert _assert_obligations_settled(rec, "wt", abandon=False) is True


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
