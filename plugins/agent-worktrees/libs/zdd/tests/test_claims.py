"""Tests for generation-scoped claim decision logic (zdd.claims)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from zdd.claims import ClaimConflict, decide_acquire, generation_id, is_recoverable


@dataclass
class _Rec:
    owner_generation: str = ""
    owner_pid: int = 0


def test_generation_id_is_stable_and_distinct():
    a = generation_id(version="1.0", pid=111, started_at=100.0)
    b = generation_id(version="1.0", pid=112, started_at=100.0)
    assert a != b
    assert a == generation_id(version="1.0", pid=111, started_at=100.0)


def test_generation_id_defaults_use_current_pid():
    gid = generation_id(version="1.0")
    assert f"-{__import__('os').getpid()}-" in gid


# -- is_recoverable -----------------------------------------------------


def test_none_record_is_recoverable():
    assert is_recoverable(None, pid_alive=lambda p: True) is True


def test_never_claimed_record_is_recoverable():
    rec = _Rec(owner_generation="", owner_pid=0)
    assert is_recoverable(rec, pid_alive=lambda p: True) is True


def test_live_owner_is_not_recoverable():
    rec = _Rec(owner_generation="gen-a", owner_pid=123)
    assert is_recoverable(rec, pid_alive=lambda p: p == 123) is False


def test_dead_owner_is_recoverable():
    rec = _Rec(owner_generation="gen-a", owner_pid=123)
    assert is_recoverable(rec, pid_alive=lambda p: False) is True


# -- decide_acquire -------------------------------------------------------


def test_acquire_free_record():
    decide_acquire(
        None, key="s1", generation="gen-b", owner_pid=999,
        pid_alive=lambda p: True,
    )  # no raise


def test_idempotent_reacquire_by_same_generation():
    rec = _Rec(owner_generation="gen-a", owner_pid=123)
    decide_acquire(
        rec, key="s1", generation="gen-a", owner_pid=123,
        pid_alive=lambda p: True,
    )  # no raise, even though "live"


def test_conflict_when_live_generation_holds_claim():
    rec = _Rec(owner_generation="gen-a", owner_pid=123)
    with pytest.raises(ClaimConflict) as exc_info:
        decide_acquire(
            rec, key="s1", generation="gen-b", owner_pid=456,
            pid_alive=lambda p: p == 123,
        )
    assert exc_info.value.key == "s1"
    assert exc_info.value.held_by_generation == "gen-a"
    assert exc_info.value.held_by_pid == 123


def test_recover_when_owning_generation_is_dead():
    rec = _Rec(owner_generation="gen-a", owner_pid=123)
    decide_acquire(
        rec, key="s1", generation="gen-b", owner_pid=456,
        pid_alive=lambda p: False,
    )  # no raise -- stale claim recovered without a live handshake


def test_force_overrides_a_live_conflict():
    rec = _Rec(owner_generation="gen-a", owner_pid=123)
    decide_acquire(
        rec, key="s1", generation="gen-b", owner_pid=456,
        pid_alive=lambda p: True, force=True,
    )  # no raise despite a live owner
