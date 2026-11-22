"""Session self-claims are activity leases; finalize is a latch."""

from __future__ import annotations

import os
import time
import types
from pathlib import Path
from collections.abc import Callable
from typing import cast

import pytest

import agent_worktrees.__main__ as m
import argparse

from agent_worktrees import activity, claim_history, claims_cli, cleanup_gc_cli, cleanup_reap
from agent_worktrees import execution_spaces
from agent_worktrees import git_ops, local_cache_worker, output, prune, reclaim
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
        status="finalized", completed_at=None,
        sessions=[tracking.SessionEntry(session_id="sess-1", started_at="2026-06-01T10:00:00")],
        resources=list(claims), finalized_checkout={"head": "a" * 40, "branch": "worktree/wt"},
    )


def _self_ref(
    rec: tracking.WorktreeRecord, *, machine: str | None = None, project: str = "p",
) -> str:
    return tracking_claims.format_claim_ref(
        machine or rec.execution_machine, project, rec.worktree_id, "sess-1")


def _finalized_self(
    path: Path, state: str = "at-rest", *, saved_at: Path | None = None,
) -> tracking.WorktreeRecord:
    rec = _finalized(path)
    if saved_at is not None:
        # Saving stamps the receiving machine identity the self ref must name.
        tracking.save_record(rec, saved_at)
    rec.resources.append(
        tracking_claims.ResourceClaim(kind="session", ref=_self_ref(rec), state=state))
    if saved_at is not None:
        tracking.save_record(rec, saved_at)
    return rec


def test_expired_idle_session_no_longer_keeps_finalized_active(
    state_dir: Path, tmp_path: Path,
) -> None:
    rec = _finalized_self(tmp_path / "wt")
    norm = sessions._normalize_path(rec.worktree_path)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    active = {norm}
    session_lease.drop_expired_finalized(
        [rec], {rec.worktree_path: ["sess-1"]}, active, keep=lambda r: False)
    assert active == set()

    active = {norm}
    session_lease.drop_expired_finalized([rec], {}, active, keep=lambda r: True)
    assert active == {norm}


def test_unstamped_legacy_finalized_never_expires_out_of_active(
    state_dir: Path, tmp_path: Path,
) -> None:
    rec = _finalized_self(tmp_path / "wt")
    rec.finalized_checkout = None
    norm = sessions._normalize_path(rec.worktree_path)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    active = {norm}
    session_lease.drop_expired_finalized(
        [rec], {rec.worktree_path: ["sess-1"]}, active, keep=lambda r: False)
    assert active == {norm}


def test_recent_conversation_or_unknown_session_keeps_finalized_active(
    state_dir: Path, tmp_path: Path,
) -> None:
    rec = _finalized_self(tmp_path / "wt")
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


def _scan(
    rec: tracking.WorktreeRecord, monkeypatch: pytest.MonkeyPatch, bridge: set[str],
    *, copilot_live: bool = True,
) -> set[str]:
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: bridge)
    monkeypatch.setattr(local_cache_worker, "is_active", lambda path: False)
    ctx = sessions.SessionContext(active_sessions=({
        sessions._normalize_path(rec.worktree_path): ["sess-1"]} if copilot_live else {}))
    return m._build_active_paths([rec], ctx)


def test_live_copilot_keeps_activity_despite_expired_lease_or_bridge_state(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _finalized_self(tmp_path / "wt")
    norm = sessions._normalize_path(rec.worktree_path)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    _touch(state_dir, "bridge-sess", 5)
    assert _scan(rec, monkeypatch, {"wt"}) == {norm}
    assert _scan(rec, monkeypatch, set()) == {norm}


def test_cached_bound_hint_cannot_mark_activity_without_a_matching_copilot(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _finalized_self(tmp_path / "wt")
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    monkeypatch.setattr(m, "_fresh_bound_live_hint", lambda r: True)
    assert _scan(rec, monkeypatch, set(), copilot_live=False) == set()


def test_expired_self_claim_is_admitted_only_on_positive_expiry(
    state_dir: Path, tmp_path: Path,
) -> None:
    rec = _finalized_self(tmp_path / "wt")
    claim = rec.resources[0]
    assert not session_lease.expired_finalized_self_claim(rec, claim)  # unknown activity
    _touch(state_dir, "sess-1", 60)
    assert not session_lease.expired_finalized_self_claim(rec, claim)  # fresh
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    assert session_lease.expired_finalized_self_claim(rec, claim)
    foreign = tracking_claims.ResourceClaim(kind="session", ref="m/p/other#sess-1", state="at-rest")
    worktree = tracking_claims.ResourceClaim(kind="worktree", ref="m/p/wt", state="at-rest")
    assert not session_lease.expired_finalized_self_claim(rec, foreign)
    assert not session_lease.expired_finalized_self_claim(rec, worktree)
    untracked = _finalized_self(tmp_path / "wt")
    untracked.sessions = []
    assert not session_lease.expired_finalized_self_claim(untracked, untracked.resources[0])
    rec.status = "active"
    assert not session_lease.expired_finalized_self_claim(rec, claim)


def test_expired_finalized_session_claim_no_longer_blocks_cleanup(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tdir = tmp_path / "tracking"
    tdir.mkdir()
    rec = _finalized_self(tmp_path / "wt", saved_at=tdir / "wt.yaml")
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
    rec = _finalized_self(tmp_path / "wt")
    _touch(state_dir, "sess-1", 60)
    assert tracking_claims.sweep_abandoned_obligations(
        rec, gone_of=lambda c: True, safe_of=lambda c: True, save=False) == []
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    swept = tracking_claims.sweep_abandoned_obligations(
        rec, gone_of=lambda c: True, safe_of=lambda c: True, save=False)
    assert [c.state for c in swept] == ["released"]


@pytest.mark.parametrize(
    "owner", [{"machine": "other-machine"}, {"project": "other-project"}],
    ids=["other-machine", "other-project"],
)
def test_foreign_owner_session_claim_never_expires_by_lease(
    state_dir: Path, tmp_path: Path, owner: dict[str, str],
) -> None:
    rec = _finalized(tmp_path / "wt")
    ref = _self_ref(rec, **owner)
    foreign = tracking_claims.ResourceClaim(kind="session", ref=ref, state="at-rest")
    rec.resources.append(foreign)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    assert session_lease.lease_expired(rec, foreign) is True
    assert not session_lease.expired_finalized_self_claim(rec, foreign)
    assert session_lease.release_expired_finalized(rec) == []
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    assert not prune.cleanup_disposition(rec, info).cleanable
    assert retirement_safety.retirement_blocker(rec) is not None
    assert tracking_claims.sweep_abandoned_obligations(
        rec, gone_of=lambda c: True, safe_of=lambda c: True, save=False) == []
    assert not session_lease.idle_finalized(rec, ["sess-1"])


def test_activity_stat_failure_is_unknown_not_expired(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_dir = state_dir / "sess-1"
    session_dir.mkdir()
    workspace = session_dir / "workspace.yaml"
    workspace.write_text("{}\n", encoding="utf-8")
    stamp = time.time() - session_lease.ACTIVE_LEASE_TTL_S * 10
    os.utime(workspace, (stamp, stamp))
    assert session_lease.last_activity("sess-1") == pytest.approx(stamp)

    real_stat = Path.stat

    def _stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self.name == "events.jsonl":
            raise PermissionError(13, "denied", str(self))
        return real_stat(self)

    monkeypatch.setattr(Path, "stat", _stat)
    assert session_lease.last_activity("sess-1") is None
    rec = _finalized_self(tmp_path / "wt")
    assert session_lease.lease_expired(rec, rec.resources[0]) is None
    assert not session_lease.expired_finalized_self_claim(rec, rec.resources[0])


def test_claims_sweep_apply_releases_expired_finalized_self_claim(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tdir = tmp_path / "tracking"
    tdir.mkdir()
    rec_path = tdir / "wt.yaml"
    rec = _finalized_self(tmp_path / "wt", saved_at=rec_path)
    monkeypatch.setattr(cfg, "load_config", lambda: types.SimpleNamespace())
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tdir)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", lambda r, c: None)
    monkeypatch.setattr(sweep, "load_claim_child_record", lambda ref, config: (rec, True))
    monkeypatch.setattr(sessions, "session_id_is_live", lambda r, sid: True)
    history: list[tuple[str, str]] = []
    monkeypatch.setattr(
        claim_history, "record_event",
        lambda **kw: history.append((str(kw["event"]), str(kw["note"]))))
    monkeypatch.setattr(activity, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(output, "_json_output", lambda payload: None)
    args = argparse.Namespace(apply=True, json=True)

    _touch(state_dir, "sess-1", 60)
    assert claims_cli._claims_sweep(args) == 0
    assert [c.state for c in tracking.load_record(rec_path).resources] == ["at-rest"]
    assert history == []

    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    assert claims_cli._claims_sweep(args) == 0
    reloaded = tracking.load_record(rec_path)
    assert [c.state for c in reloaded.resources] == ["released"]
    assert history == [("released", "lease-expired")]
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    assert prune.cleanup_disposition(reloaded, info).cleanable


class _RenewingLock:
    """A record lock whose acquisition races a session renewal."""

    def __init__(self, renew: Callable[[], None], real: type[tracking._RecordLock]) -> None:
        self._renew: Callable[[], None] | None = renew
        self._real = real

    def __call__(self, path: Path, **kwargs: bool) -> tracking._RecordLock:
        renew, self._renew = self._renew, None
        if renew is not None:
            renew()
        return self._real(path, **kwargs)


@pytest.mark.parametrize("renewal", ["fresh-activity", "reopened-active"])
def test_claims_sweep_rejudges_session_claim_renewed_before_commit(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, renewal: str,
) -> None:
    tdir = tmp_path / "tracking"
    tdir.mkdir()
    rec_path = tdir / "wt.yaml"
    _finalized_self(tmp_path / "wt", saved_at=rec_path)
    monkeypatch.setattr(cfg, "load_config", lambda: types.SimpleNamespace())
    monkeypatch.setattr(cfg, "tracking_dir", lambda: tdir)
    monkeypatch.setattr(execution_spaces, "require_record_mutation", lambda r, c: None)
    monkeypatch.setattr(
        sweep, "load_claim_child_record",
        lambda ref, config: (tracking.load_record(rec_path), True))
    monkeypatch.setattr(sessions, "session_id_is_live", lambda r, sid: True)
    history: list[str] = []
    monkeypatch.setattr(claim_history, "record_event", lambda **kw: history.append(str(kw["event"])))
    monkeypatch.setattr(activity, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(output, "_json_output", lambda payload: None)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)

    def _renew() -> None:
        _touch(state_dir, "sess-1", 5)
        if renewal == "reopened-active":
            latest = tracking.load_record(rec_path)
            latest.resources[0].state = "active"
            tracking.save_record(latest, rec_path)

    monkeypatch.setattr(tracking, "_RecordLock", _RenewingLock(_renew, tracking._RecordLock))
    assert claims_cli._claims_sweep(argparse.Namespace(apply=True, json=True)) == 0
    expected = "active" if renewal == "reopened-active" else "at-rest"
    assert [c.state for c in tracking.load_record(rec_path).resources] == [expected]
    assert history == []


def _revalidation_env(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pane: bool,
) -> tuple[tracking.WorktreeRecord, Path, list[str]]:
    tdir = tmp_path / "tracking"
    tdir.mkdir()
    (tmp_path / "wt").mkdir()
    rec = _finalized_self(tmp_path / "wt", saved_at=tdir / "wt.yaml")
    rec.checkout_managed = False
    tracking.save_record(rec, tdir / "wt.yaml")
    norm = sessions._normalize_path(rec.worktree_path)
    monkeypatch.setattr(execution_spaces, "require_cleanup_identity", lambda r, a: None)
    monkeypatch.setattr(m, "_hosted_session_blocks_cleanup", lambda r: False)
    monkeypatch.setattr(m, "_apply_tracking_override", lambda r, info: info)
    panes = {sessions.mux_session_name(rec.worktree_id)} if pane else set()
    monkeypatch.setattr(sessions, "_list_mux_sessions", lambda: panes)
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: set())
    monkeypatch.setattr(local_cache_worker, "is_active", lambda path: False)
    monkeypatch.setattr(
        sessions, "scan_sessions_fast",
        lambda records: sessions.SessionContext(active_sessions={norm: ["sess-1"]}))

    def _classify(path: str, branch: str, **kw: object) -> git_ops.WorktreeStateInfo:
        active = cast(set[str], kw["active_paths"])
        state = (git_ops.WorktreeState.ACTIVE if sessions._normalize_path(path) in active
                 else git_ops.WorktreeState.COMPLETED)
        return git_ops.WorktreeStateInfo(state=state)

    monkeypatch.setattr(git_ops, "classify_worktree", _classify)
    return rec, tdir, []


def _revalidate(
    rec: tracking.WorktreeRecord, tdir: Path, reaped: list[str],
) -> cleanup_gc_cli.RevalidationResult:
    def _reap(
        latest: tracking.WorktreeRecord, info: git_ops.WorktreeStateInfo,
    ) -> tuple[int, list[str]]:
        reaped.append(latest.worktree_id)
        return 0, []

    repo = cast(cfg.RepoConfig, types.SimpleNamespace(
        anchor=str(tdir), remote="origin", default_branch="dev"))
    return cleanup_gc_cli._revalidate_cleanup_safety(
        rec.worktree_id, repo=repo, tracking_path=tdir, reap=_reap)


def test_revalidation_preserves_open_copilot_even_when_its_idle_lease_expired(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec, tdir, reaped = _revalidation_env(state_dir, tmp_path, monkeypatch, pane=True)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    ctx = sessions.scan_sessions_fast([rec])
    assert m._build_active_paths([rec], ctx) == {sessions._normalize_path(rec.worktree_path)}
    result = _revalidate(rec, tdir, reaped)
    assert not result.cleanable
    assert reaped == []


@pytest.mark.parametrize("pane", [True, False], ids=["pane", "no-pane"])
def test_revalidation_refuses_lease_renewed_before_lock(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pane: bool,
) -> None:
    rec, tdir, reaped = _revalidation_env(state_dir, tmp_path, monkeypatch, pane=pane)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    assert prune.cleanup_disposition(rec, info).cleanable
    _touch(state_dir, "sess-1", 5)
    result = _revalidate(rec, tdir, reaped)
    assert not result.cleanable
    assert reaped == []


def test_reap_does_not_release_lease_renewed_after_disposition(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tdir = tmp_path / "tracking"
    tdir.mkdir()
    rec = _finalized_self(tmp_path / "wt", saved_at=tdir / "wt.yaml")
    rec.checkout_managed = False
    tracking.save_record(rec, tdir / "wt.yaml")
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    assert prune.cleanup_disposition(rec, info).cleanable
    _touch(state_dir, "sess-1", 5)
    retired: list[str] = []

    def _retire(r: tracking.WorktreeRecord, p: Path) -> bool:
        retired.append(r.worktree_id)
        return True

    monkeypatch.setattr(tracking, "retire_record", _retire)
    repo = cast(cfg.RepoConfig, types.SimpleNamespace(anchor=str(tmp_path), default_branch="dev"))
    failures, _warnings = cleanup_reap.reap_worktree(rec, info, repo, tdir)
    assert failures
    assert retired == []
    assert [c.state for c in tracking.load_record(tdir / "wt.yaml").resources] == ["at-rest"]


def test_lease_scan_normalizes_session_paths_once_and_skips_ineligible(
    state_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _touch(state_dir, "sess-1", session_lease.FINALIZED_AT_REST_GRACE_S + 60)
    finalized = [_finalized_self(tmp_path / f"fin-{i}") for i in range(3)]
    plain = []
    for i in range(200):
        rec = _finalized(tmp_path / f"live-{i}")
        rec.status = "active"
        plain.append(rec)
    records = [*plain, *finalized]
    sessions_map = {str(r.worktree_path): ["sess-1"] for r in records}
    active = {sessions._normalize_path(str(r.worktree_path)) for r in records}
    real = sessions._normalize_path
    calls: list[str] = []

    def _counting(path: str) -> str:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(sessions, "_normalize_path", _counting)
    session_lease.drop_expired_finalized(records, sessions_map, active, keep=lambda r: False)
    # One normalization per eligible record plus one grouping pass -- never
    # a per-record scan of the whole session map.
    assert len(calls) == len(finalized) + len(sessions_map)
    assert active == {real(str(r.worktree_path)) for r in plain}
