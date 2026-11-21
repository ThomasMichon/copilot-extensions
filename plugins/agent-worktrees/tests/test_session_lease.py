"""Session self-claims are activity leases; finalize is a latch."""

from __future__ import annotations

import os
import time
import types
from pathlib import Path
from typing import cast

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import cleanup_reap, git_ops, local_cache_worker, prune, reclaim
from agent_worktrees import config as cfg
from agent_worktrees import retirement_safety, session_lease, sessions, sweep, tracking
from agent_worktrees import tracking_claims


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


def _scan(rec: tracking.WorktreeRecord, monkeypatch: pytest.MonkeyPatch, bridge: set[str]) -> set[str]:
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: bridge)
    monkeypatch.setattr(local_cache_worker, "is_active", lambda path: False)
    ctx = sessions.SessionContext(active_sessions={
        sessions._normalize_path(rec.worktree_path): ["sess-1"]})
    return m._build_active_paths([rec], ctx)


def test_unregistered_bridge_session_keeps_finalized_active_despite_expired_lease(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _finalized(tmp_path / "wt", _claim("at-rest"))
    norm = sessions._normalize_path(rec.worktree_path)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    _touch(state_dir, "bridge-sess", 5)
    assert _scan(rec, monkeypatch, {"wt"}) == {norm}
    assert _scan(rec, monkeypatch, set()) == set()


def test_fresh_bound_live_hint_keeps_finalized_active_despite_expired_lease(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _finalized(tmp_path / "wt", _claim("at-rest"))
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    monkeypatch.setattr(m, "_fresh_bound_live_hint", lambda r: True)
    assert _scan(rec, monkeypatch, set()) == {sessions._normalize_path(rec.worktree_path)}


def test_expired_self_claim_is_admitted_only_on_positive_expiry(
    state_dir: Path, tmp_path: Path,
) -> None:
    claim = _claim("at-rest")
    rec = _finalized(tmp_path / "wt", claim)
    assert not session_lease.expired_finalized_self_claim(rec, claim)  # unknown activity
    _touch(state_dir, "sess-1", 60)
    assert not session_lease.expired_finalized_self_claim(rec, claim)  # fresh
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    assert session_lease.expired_finalized_self_claim(rec, claim)
    foreign = tracking_claims.ResourceClaim(kind="session", ref="m/p/other#sess-1", state="at-rest")
    worktree = tracking_claims.ResourceClaim(kind="worktree", ref="m/p/wt", state="at-rest")
    assert not session_lease.expired_finalized_self_claim(rec, foreign)
    assert not session_lease.expired_finalized_self_claim(rec, worktree)
    rec.status = "active"
    assert not session_lease.expired_finalized_self_claim(rec, claim)


def test_expired_finalized_session_claim_no_longer_blocks_cleanup(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tdir = tmp_path / "tracking"
    tdir.mkdir()
    rec = _finalized(tmp_path / "wt", _claim("at-rest"))
    rec.checkout_managed = False
    tracking.save_record(rec, tdir / "wt.yaml")
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    _touch(state_dir, "sess-1", 60)
    assert not prune.cleanup_disposition(rec, info).cleanable
    assert retirement_safety.retirement_blocker(rec) is not None

    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    assert prune.cleanup_disposition(rec, info).cleanable
    assert retirement_safety.retirement_blocker(rec) is None
    released: list[list[str]] = []
    real_retire = tracking.retire_record

    def _retire(record: tracking.WorktreeRecord, path: Path) -> bool:
        released.append([c.state for c in tracking.load_record(path / "wt.yaml").resources])
        return real_retire(record, path)

    monkeypatch.setattr(tracking, "retire_record", _retire)
    repo = cast(cfg.RepoConfig, types.SimpleNamespace(anchor=str(tmp_path), default_branch="dev"))
    failures, warnings = cleanup_reap.reap_worktree(rec, info, repo, tdir)
    assert (failures, warnings) == (0, [])
    assert released == [["released"]]


def test_sweep_releases_only_positively_expired_finalized_session_claims(
    state_dir: Path, tmp_path: Path,
) -> None:
    rec = _finalized(tmp_path / "wt", _claim("at-rest"))
    _touch(state_dir, "sess-1", 60)
    assert tracking_claims.sweep_abandoned_obligations(
        rec, gone_of=lambda c: True, safe_of=lambda c: True, save=False) == []
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    swept = tracking_claims.sweep_abandoned_obligations(
        rec, gone_of=lambda c: True, safe_of=lambda c: True, save=False)
    assert [c.state for c in swept] == ["released"]
