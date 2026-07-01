"""Tests for the CodeSpace lease broker (state redirected to tmp)."""

from __future__ import annotations

import pytest

from agent_codespaces import lease as lease_mod


@pytest.fixture
def leases(monkeypatch, tmp_path):
    """Redirect lease state to a tmp dir so tests never touch real state."""
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")
    # ensure_runtime_dir() targets the real RUNTIME_DIR; stub it to a no-op so
    # the broker only writes under tmp_path.
    monkeypatch.setattr(lease_mod, "ensure_runtime_dir", lambda: None)
    return tmp_path


def test_borrow_records_lease(leases):
    lease = lease_mod.borrow("effort-a", "cs-one")
    assert lease.codespace == "cs-one"
    assert lease.effort == "effort-a"
    assert lease_mod.get_lease("cs-one").effort == "effort-a"


def test_borrow_conflict_raises(leases):
    lease_mod.borrow("effort-a", "cs-one")
    with pytest.raises(RuntimeError, match="leased by effort 'effort-a'"):
        lease_mod.borrow("effort-b", "cs-one")


def test_borrow_force_takes_over(leases):
    lease_mod.borrow("effort-a", "cs-one")
    lease = lease_mod.borrow("effort-b", "cs-one", force=True)
    assert lease.effort == "effort-b"
    assert lease_mod.get_lease("cs-one").effort == "effort-b"


def test_borrow_same_effort_idempotent(leases):
    first = lease_mod.borrow("effort-a", "cs-one")
    second = lease_mod.borrow("effort-a", "cs-one")
    assert second.codespace == "cs-one"
    # acquired_at preserved across re-borrow by the same effort
    assert second.acquired_at == first.acquired_at


def test_force_takeover_resets_acquired_at(leases):
    first = lease_mod.borrow("effort-a", "cs-one")
    taken = lease_mod.borrow("effort-b", "cs-one", force=True)
    # A new effort's forced takeover starts a fresh acquisition.
    assert taken.acquired_at >= first.acquired_at
    assert taken.effort == "effort-b"


def test_borrow_requires_name(leases):
    with pytest.raises(RuntimeError, match="requires a CodeSpace name"):
        lease_mod.borrow("effort-a", "")


def test_release_by_codespace(leases):
    lease_mod.borrow("effort-a", "cs-one")
    assert lease_mod.release("cs-one") is True
    assert lease_mod.list_leases() == []


def test_release_by_effort(leases):
    lease_mod.borrow("effort-a", "cs-one")
    assert lease_mod.release("effort-a") is True
    assert lease_mod.get_lease("cs-one") is None


def test_release_missing_returns_false(leases):
    assert lease_mod.release("nope") is False


def test_heartbeat_refreshes(leases):
    lease_mod.borrow("effort-a", "cs-one")
    assert lease_mod.heartbeat("cs-one") is True
    assert lease_mod.heartbeat("cs-absent") is False


def test_multiple_codespaces_independent(leases):
    lease_mod.borrow("effort-a", "cs-one")
    lease_mod.borrow("effort-b", "cs-two")
    active = {le.codespace: le.effort for le in lease_mod.list_leases()}
    assert active == {"cs-one": "effort-a", "cs-two": "effort-b"}


def test_reclaim_after_ttl(leases):
    lease_mod.borrow("effort-a", "cs-one")
    # A negative TTL means any non-negative age is past expiry -- deterministic
    # regardless of clock resolution.
    assert lease_mod.list_leases(ttl=-1) == []


def test_lease_survives_within_ttl(leases):
    lease_mod.borrow("effort-a", "cs-one")
    active = lease_mod.list_leases()
    assert len(active) == 1
    assert active[0].effort == "effort-a"
