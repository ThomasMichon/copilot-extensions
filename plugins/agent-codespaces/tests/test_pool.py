"""Tests for the CodeSpace venue pool (inventory + budget + disposition)."""

from __future__ import annotations

import time

from agent_codespaces.lease import Lease
from agent_codespaces.lifecycle import CodespaceInfo
from agent_codespaces.pool import (
    CLEAN,
    DEFAULT_STALE_AFTER,
    FAILED,
    IDLE,
    IN_USE,
    PROVISIONING,
    STALE,
    build_pool,
    derive_disposition,
    is_running,
    machine_cores,
)
from agent_codespaces.status import STATE_PRUNABLE, STATE_RECOVERED


# --- machine_cores -------------------------------------------------------

def test_machine_cores_from_machine_tier():
    assert machine_cores("premiumLinux") == 8
    assert machine_cores("standardLinux32gb") == 4
    assert machine_cores("largePremiumLinux") == 16
    assert machine_cores("basicLinux32gb") == 2


def test_machine_cores_parses_embedded_core_count():
    assert machine_cores("custom16core") == 16


def test_machine_cores_unknown_is_zero():
    assert machine_cores("someWeirdMachine") == 0
    assert machine_cores("") == 0


# --- is_running ----------------------------------------------------------

def test_is_running_states():
    assert is_running("Available") is True
    assert is_running("Provisioning") is True   # transient pending == running
    assert is_running("Shutdown") is False      # stopped -> spends no cores
    assert is_running("Failed") is False        # terminal -> spends no cores


# --- derive_disposition precedence --------------------------------------

def _d(**kw):
    base = dict(
        state="Available", has_live_lease=False, has_beacon=False,
        marker=None, idle_age=None, stale_after=DEFAULT_STALE_AFTER,
    )
    base.update(kw)
    return derive_disposition(**base)


def test_disposition_failed_overrides_all():
    assert _d(state="Failed", has_live_lease=True) == FAILED


def test_disposition_live_lease_is_in_use():
    assert _d(has_live_lease=True) == IN_USE


def test_disposition_beacon_is_in_use_even_without_local_lease():
    assert _d(has_beacon=True) == IN_USE


def test_disposition_pending_is_provisioning_when_unheld():
    assert _d(state="Provisioning") == PROVISIONING
    # ...but a leased box still being provisioned is in-use by its holder.
    assert _d(state="Provisioning", has_live_lease=True) == IN_USE


def test_disposition_markers():
    assert _d(marker=STATE_PRUNABLE) == STALE
    assert _d(marker=STATE_RECOVERED) == CLEAN


def test_disposition_idle_ages_to_stale():
    assert _d(idle_age=10.0) == IDLE
    assert _d(idle_age=DEFAULT_STALE_AFTER + 1) == STALE


def test_disposition_default_is_idle():
    assert _d() == IDLE


# --- build_pool budget accounting ---------------------------------------

def _cs(name, state="Available", machine="premiumLinux", repo="o/r"):
    return CodespaceInfo(
        name=name, display_name=name, repository=repo, branch="main",
        state=state, machine=machine, account="", last_used_at="",
    )


def test_build_pool_budget_counts_only_running_cores():
    now = time.time()
    codespaces = [
        _cs("a", state="Available", machine="premiumLinux"),        # 8
        _cs("b", state="Shutdown", machine="largePremiumLinux"),    # 16, off budget
        _cs("c", state="Available", machine="standardLinux32gb"),   # 4
    ]
    members, budget = build_pool(
        budget_cores=64, now=now, codespaces=codespaces, leases=[], markers={},
    )
    assert budget.total_cores == 64
    assert budget.spent_cores == 12          # 8 + 4 (Shutdown b excluded)
    assert budget.headroom_cores == 52
    assert budget.running_count == 2
    assert budget.total_count == 3


def test_build_pool_unknown_cores_are_surfaced():
    # A machine tier neither in the map nor with a parseable core count.
    cs = CodespaceInfo(
        name="a", display_name="a", repository="o/r", branch="main",
        state="Available", machine="mysteryMachine", account="", last_used_at="",
    )
    _members, budget = build_pool(
        budget_cores=64, codespaces=[cs], leases=[], markers={},
    )
    assert budget.spent_cores == 0
    assert budget.unknown_cores_count == 1


def test_build_pool_derives_in_use_and_allocation_from_lease():
    now = time.time()
    lease = Lease(
        codespace="a", effort="my-effort", pid=123, host="dev6",
        acquired_at=now, heartbeat_at=now,
    )
    members, _budget = build_pool(
        now=now, codespaces=[_cs("a")], leases=[lease], markers={},
    )
    (m,) = members
    assert m.disposition == IN_USE
    assert m.holder_effort == "my-effort"
    assert m.holder_worktree is None
    assert m.holder_owner == "my-effort"
    assert m.holder_host == "dev6"
    d = m.to_dict()
    assert d["allocation"] == {
        "owner": "my-effort", "effort": "my-effort", "worktree": None,
        "host": "dev6", "beacon": None,
    }


def test_build_pool_surfaces_claim_owner_not_null():
    """A #897 claim (effort="", owner in worktree) must read as held by its
    worktree -- not a null allocation (dotfiles #904)."""
    now = time.time()
    wt = "/home/me/wt/type-filters-adoption-7qv"
    claim = Lease(
        codespace="a", effort="", pid=123, host="cloud1",
        acquired_at=now, heartbeat_at=now, worktree=wt,
    )
    members, _budget = build_pool(
        now=now, codespaces=[_cs("a")], leases=[claim], markers={},
    )
    (m,) = members
    assert m.disposition == IN_USE
    # effort is empty on a claim; the owner comes from the worktree.
    assert m.holder_effort is None
    assert m.holder_worktree == wt
    assert m.holder_owner == wt
    assert m.holder_host == "cloud1"
    d = m.to_dict()
    assert d["allocation"]["owner"] == wt
    assert d["allocation"]["effort"] is None
    assert d["allocation"]["worktree"] == wt
    # The key regression guard: a dispatched (claimed) box is NOT null-held.
    assert d["allocation"]["owner"] is not None


def test_build_pool_marks_prunable_as_stale_and_recovered_as_clean():
    codespaces = [_cs("p"), _cs("r", state="Shutdown")]
    members, _ = build_pool(
        codespaces=codespaces, leases=[],
        markers={"p": STATE_PRUNABLE, "r": STATE_RECOVERED},
    )
    by = {m.name: m.disposition for m in members}
    assert by["p"] == STALE
    assert by["r"] == CLEAN


def test_build_pool_ages_idle_box_to_stale_via_last_used():
    now = time.time()
    old = _cs("old")
    # last used 2 days ago, unheld -> stale
    old.last_used_at = _iso(now - 2 * 24 * 3600)
    fresh = _cs("fresh")
    fresh.last_used_at = _iso(now - 60)
    members, _ = build_pool(
        now=now, codespaces=[old, fresh], leases=[], markers={},
    )
    by = {m.name: m.disposition for m in members}
    assert by["old"] == STALE
    assert by["fresh"] == IDLE


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")
