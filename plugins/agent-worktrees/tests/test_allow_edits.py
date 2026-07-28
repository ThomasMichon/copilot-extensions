"""Tests for break-glass edit grants (repos allow-edits)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import allow_edits


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the grant store reads/writes under a tmp dir."""
    monkeypatch.setattr(allow_edits.Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def clock(monkeypatch):
    """Controllable epoch-ms clock."""
    state = {"ms": 1_000_000_000_000}
    monkeypatch.setattr(allow_edits, "_now_ms", lambda: state["ms"])
    return state


def test_clamp_minutes():
    assert allow_edits.clamp_minutes(None) == allow_edits.DEFAULT_MINUTES
    assert allow_edits.clamp_minutes("bogus") == allow_edits.DEFAULT_MINUTES
    assert allow_edits.clamp_minutes(0) == allow_edits.DEFAULT_MINUTES
    assert allow_edits.clamp_minutes(5) == 5
    assert allow_edits.clamp_minutes(999) == allow_edits.MAX_MINUTES


def test_grant_active_and_expiry(home: Path, clock):
    assert allow_edits.is_active("SPO.Core") is False
    g = allow_edits.grant("SPO.Core", "maintaining the target agent's AGENTS.md", 5)
    assert g.minutes == 5
    assert g.expires_at_ms == clock["ms"] + 5 * 60_000
    assert allow_edits.is_active("SPO.Core") is True
    # per-repo isolation
    assert allow_edits.is_active("sunshine") is False
    # advance past expiry
    clock["ms"] += 5 * 60_000 + 1
    assert allow_edits.is_active("SPO.Core") is False


def test_grant_persists_epoch_ms_for_cross_language(home: Path, clock):
    allow_edits.grant("r", "reason enough", 10)
    data = json.loads((home / ".agent-worktrees" / "allow-edits.json").read_text())
    rec = data["grants"]["r"]
    assert isinstance(rec["expires_at_ms"], int)
    assert rec["expires_at_ms"] == clock["ms"] + 10 * 60_000
    assert rec["expires_at_iso"].endswith("Z")


def test_list_prunes_expired(home: Path, clock):
    allow_edits.grant("a", "reason aaa", 5)
    allow_edits.grant("b", "reason bbb", 30)
    clock["ms"] += 10 * 60_000  # a expired, b alive
    active = allow_edits.list_active()
    assert [g.repo for g in active] == ["b"]
    # prune persisted
    data = json.loads((home / ".agent-worktrees" / "allow-edits.json").read_text())
    assert list(data["grants"].keys()) == ["b"]


def test_new_grant_gcs_expired(home: Path, clock):
    """A new entrant garbage-collects already-expired grants (keeps live ones)."""
    allow_edits.grant("old", "will expire soon", 1)
    allow_edits.grant("live", "stays alive a while", 30)
    clock["ms"] += 2 * 60_000  # 'old' now expired, 'live' still valid
    allow_edits.grant("new", "adding this should prune 'old'", 10)
    data = json.loads((home / ".agent-worktrees" / "allow-edits.json").read_text())
    assert sorted(data["grants"].keys()) == ["live", "new"]
    assert "old" not in data["grants"]


def test_revoke(home: Path, clock):
    allow_edits.grant("a", "reason aaa", 30)
    assert allow_edits.revoke("a") is True
    assert allow_edits.is_active("a") is False
    assert allow_edits.revoke("a") is False


def test_corrupt_store_is_ignored(home: Path):
    p = home / ".agent-worktrees" / "allow-edits.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert allow_edits.is_active("x") is False
    # a subsequent grant repairs the store
    allow_edits.grant("x", "reason enough", 10)
    assert allow_edits.is_active("x") is True
