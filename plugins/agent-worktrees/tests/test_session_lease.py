"""Session self-claims are activity leases; finalize is a latch."""

from __future__ import annotations

import os
import time
import types
from pathlib import Path
from typing import cast

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import session_lease, sessions, sweep, tracking, tracking_claims


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: tmp_path)
    return tmp_path


def _touch(state_dir: Path, session_id: str, age_s: float) -> None:
    events = state_dir / session_id / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("{}\n", encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(events, (stamp, stamp))


def _claim(state: str = "active", session_id: str = "sess-1") -> tracking_claims.ResourceClaim:
    return tracking_claims.ResourceClaim(kind="session", ref=f"m/p/wt#{session_id}", state=state)


def test_missing_activity_is_unknown_never_expired(state_dir: Path) -> None:
    rec = types.SimpleNamespace(status="active")
    assert session_lease.last_activity("sess-1") is None
    assert session_lease.lease_expired(rec, _claim()) is None


def test_fresh_lease_is_live_and_stale_lease_is_expired(state_dir: Path) -> None:
    rec = types.SimpleNamespace(status="active")
    _touch(state_dir, "sess-1", 60)
    assert session_lease.lease_expired(rec, _claim()) is False
    _touch(state_dir, "sess-1", session_lease.ACTIVE_LEASE_TTL_S + 60)
    assert session_lease.lease_expired(rec, _claim()) is True


def test_finalized_at_rest_claim_uses_short_grace(state_dir: Path) -> None:
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    finalized = types.SimpleNamespace(status="finalized")
    assert session_lease.lease_expired(finalized, _claim("at-rest")) is True
    assert session_lease.lease_expired(finalized, _claim("active")) is False


def test_env_overrides_ttl(state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_SESSION_LEASE_TTL_S", "30")
    _touch(state_dir, "sess-1", 120)
    assert session_lease.lease_expired(types.SimpleNamespace(status="active"), _claim()) is True
    monkeypatch.setenv("AGENT_WORKTREES_SESSION_LEASE_TTL_S", "bogus")
    assert session_lease.active_ttl() == session_lease.ACTIVE_LEASE_TTL_S


def _finalized(path: Path, *claims: tracking_claims.ResourceClaim) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt", branch="worktree/wt", worktree_path=str(path), repo="p",
        machine="m", platform="wsl", started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
        status="finalized", completed_at=None, sessions=[], resources=list(claims),
    )


def test_expired_idle_session_no_longer_keeps_finalized_active(
    state_dir: Path, tmp_path: Path,
) -> None:
    rec = _finalized(tmp_path / "wt", _claim("at-rest"))
    norm = sessions._normalize_path(rec.worktree_path)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    active = {norm}
    session_lease.drop_expired_finalized(
        [rec], {rec.worktree_path: ["sess-1"]}, active, keep=lambda r: False)
    assert active == set()

    active = {norm}
    session_lease.drop_expired_finalized([rec], {}, active, keep=lambda r: True)
    assert active == {norm}


def test_recent_conversation_or_unknown_session_keeps_finalized_active(
    state_dir: Path, tmp_path: Path,
) -> None:
    rec = _finalized(tmp_path / "wt", _claim("at-rest"))
    norm = sessions._normalize_path(rec.worktree_path)
    _touch(state_dir, "sess-1", 60)
    active = {norm}
    session_lease.drop_expired_finalized([rec], {}, active, keep=lambda r: False)
    assert active == {norm}
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    session_lease.drop_expired_finalized(
        [rec], {rec.worktree_path: ["unknown"]}, active, keep=lambda r: False)
    assert active == {norm}
    assert not session_lease.idle_finalized(_finalized(tmp_path / "wt"))


def _live_session(monkeypatch: pytest.MonkeyPatch) -> tracking_claims.ResourceClaim:
    rec = types.SimpleNamespace(status="active")
    monkeypatch.setattr(sweep, "load_claim_child_record", lambda ref, config: (rec, True))
    monkeypatch.setattr(sessions, "session_id_is_live", lambda r, sid: True)
    return _claim()


def test_session_claim_gone_when_live_process_lease_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _live_session(monkeypatch)
    monkeypatch.setattr(session_lease, "last_activity", lambda sid: 0.0)
    config = cast(cfg.Config, types.SimpleNamespace())
    assert sweep.session_claim_gone(claim, config) is True
    assert sweep.claim_safe(claim, config) is True


def test_session_claim_live_process_with_fresh_or_missing_lease_is_not_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _live_session(monkeypatch)
    config = cast(cfg.Config, types.SimpleNamespace())
    for stamp in (time.time(), None):
        monkeypatch.setattr(session_lease, "last_activity", lambda sid, stamp=stamp: stamp)
        assert sweep.session_claim_gone(claim, config) is False
