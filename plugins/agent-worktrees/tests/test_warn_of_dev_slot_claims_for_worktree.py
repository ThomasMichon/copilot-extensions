"""Tests for ``finalize._warn_of_dev_slot_claims_for_worktree`` -- the
mutable-dev-slot pattern's finalize-time safety net (docs/patterns/mutable-dev-slot.md).
Mirrors ``_warn_of_codespace_claims_for_worktree``'s exact posture: read-only,
never releases anything itself, and must never raise.
"""
from __future__ import annotations

import json

from agent_worktrees import finalize

_SCHEMA = "copilot-extensions.dev-slot-claim"


def _write_claim(home, plugin: str, owner: str, *, previous_version="1.0.0",
                  schema=_SCHEMA):
    root = home / f".{plugin}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "dev-claim.json").write_text(json.dumps({
        "schema": schema,
        "version": 1,
        "owner": owner,
        "host": "some-host",
        "pid": 1234,
        "claimed_at": "2026-01-01T00:00:00Z",
        "previous_version": previous_version,
    }), encoding="utf-8")


def test_warns_and_lists_release_hints_without_releasing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(finalize.Path, "home", classmethod(lambda cls: tmp_path))
    _write_claim(tmp_path, "agent-codespaces", "D:\\wt\\abc", previous_version="0.4.0-dev163")

    finalize._warn_of_dev_slot_claims_for_worktree("wt-123", "D:\\wt\\abc")

    out = capsys.readouterr().out
    assert "agent-codespaces" in out
    assert "0.4.0-dev163" in out


def test_no_claims_is_silent(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(finalize.Path, "home", classmethod(lambda cls: tmp_path))
    finalize._warn_of_dev_slot_claims_for_worktree("wt-123", "D:\\wt\\abc")
    assert capsys.readouterr().out == ""


def test_a_different_owners_claim_is_not_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(finalize.Path, "home", classmethod(lambda cls: tmp_path))
    _write_claim(tmp_path, "agent-bridge", "D:\\wt\\someone-else")
    finalize._warn_of_dev_slot_claims_for_worktree("wt-123", "D:\\wt\\abc")
    assert capsys.readouterr().out == ""


def test_multiple_owned_claims_are_all_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(finalize.Path, "home", classmethod(lambda cls: tmp_path))
    _write_claim(tmp_path, "agent-codespaces", "D:\\wt\\abc")
    _write_claim(tmp_path, "agent-bridge", "D:\\wt\\abc")
    finalize._warn_of_dev_slot_claims_for_worktree("wt-123", "D:\\wt\\abc")
    out = capsys.readouterr().out
    assert "agent-codespaces" in out
    assert "agent-bridge" in out


def test_malformed_json_is_never_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(finalize.Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / ".agent-codespaces"
    root.mkdir(parents=True, exist_ok=True)
    (root / "dev-claim.json").write_text("not json", encoding="utf-8")
    finalize._warn_of_dev_slot_claims_for_worktree("wt-123", "D:\\wt\\abc")  # must not raise


def test_wrong_schema_is_ignored(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(finalize.Path, "home", classmethod(lambda cls: tmp_path))
    _write_claim(tmp_path, "agent-codespaces", "D:\\wt\\abc", schema="something-else")
    finalize._warn_of_dev_slot_claims_for_worktree("wt-123", "D:\\wt\\abc")
    assert capsys.readouterr().out == ""


def test_never_writes_or_deletes_the_claim_file(monkeypatch, tmp_path):
    monkeypatch.setattr(finalize.Path, "home", classmethod(lambda cls: tmp_path))
    _write_claim(tmp_path, "agent-codespaces", "D:\\wt\\abc")
    claim_path = tmp_path / ".agent-codespaces" / "dev-claim.json"
    before = claim_path.read_bytes()

    finalize._warn_of_dev_slot_claims_for_worktree("wt-123", "D:\\wt\\abc")

    assert claim_path.exists()
    assert claim_path.read_bytes() == before
