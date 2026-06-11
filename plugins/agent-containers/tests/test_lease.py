"""Tests for the lease broker (docker mocked, paths redirected to tmp)."""

from __future__ import annotations

import pytest

from agent_containers import lease as lease_mod
from agent_containers.config import ContainersConfig
from agent_containers.lifecycle import DockerContainerInfo


@pytest.fixture
def fleet(monkeypatch, tmp_path):
    """Redirect lease state to tmp and stub docker discovery."""
    monkeypatch.setattr(lease_mod, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease_mod, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease_mod, "_LOCK_FILE", tmp_path / "leases.lock")

    containers = [
        DockerContainerInfo("odsp-web-1", "i1", "img", "running", "", fleet="odsp-web"),
        DockerContainerInfo("odsp-web-2", "i2", "img", "exited", "", fleet="odsp-web"),
    ]
    monkeypatch.setattr(lease_mod, "list_containers", lambda config: containers)
    return ContainersConfig()


def test_borrow_picks_running_first(fleet):
    lease = lease_mod.borrow(fleet, "effort-a")
    assert lease.container == "odsp-web-1"
    assert lease.effort == "effort-a"


def test_borrow_excludes_already_leased(fleet):
    lease_mod.borrow(fleet, "effort-a")  # takes odsp-web-1
    lease = lease_mod.borrow(fleet, "effort-b")
    assert lease.container == "odsp-web-2"


def test_borrow_all_leased_raises(fleet):
    lease_mod.borrow(fleet, "a")
    lease_mod.borrow(fleet, "b")
    with pytest.raises(RuntimeError, match="All fleet containers"):
        lease_mod.borrow(fleet, "c")


def test_borrow_specific_container(fleet):
    lease = lease_mod.borrow(fleet, "effort-a", container="odsp-web-2")
    assert lease.container == "odsp-web-2"


def test_borrow_specific_conflict_raises(fleet):
    lease_mod.borrow(fleet, "effort-a", container="odsp-web-1")
    with pytest.raises(RuntimeError, match="leased by effort 'effort-a'"):
        lease_mod.borrow(fleet, "effort-b", container="odsp-web-1")


def test_borrow_same_effort_idempotent(fleet):
    first = lease_mod.borrow(fleet, "effort-a", container="odsp-web-1")
    second = lease_mod.borrow(fleet, "effort-a", container="odsp-web-1")
    assert second.container == "odsp-web-1"
    # acquired_at preserved across re-borrow
    assert second.acquired_at == first.acquired_at


def test_release_by_container(fleet):
    lease_mod.borrow(fleet, "effort-a")
    assert lease_mod.release("odsp-web-1") is True
    assert lease_mod.list_leases() == []


def test_release_by_effort(fleet):
    lease_mod.borrow(fleet, "effort-a")
    assert lease_mod.release("effort-a") is True
    assert lease_mod.get_lease("odsp-web-1") is None


def test_release_missing_returns_false(fleet):
    assert lease_mod.release("nope") is False


def test_reclaim_after_ttl(fleet):
    lease_mod.borrow(fleet, "effort-a")  # leases odsp-web-1
    # With a zero TTL, the lease is immediately considered expired.
    assert lease_mod.list_leases(ttl=0) == []


def test_lease_survives_within_ttl(fleet):
    lease_mod.borrow(fleet, "effort-a")
    # Default generous TTL -> lease persists across reads (and processes).
    leases = lease_mod.list_leases()
    assert len(leases) == 1
    assert leases[0].effort == "effort-a"
