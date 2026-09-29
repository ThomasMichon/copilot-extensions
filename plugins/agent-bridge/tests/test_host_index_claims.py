"""Tests for generation-scoped session-host claims (effort
agent-bridge-unified-zdd-cutover, Phase 2).

``HostIndex`` already durably tracks each session-host's location; these
tests cover the claim/release/recover primitive layered on top of it via
``zdd.claims``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_bridge.session_host.host_index import ClaimConflict, HostIndex, HostRecord


def _idx(tmp_path: Path) -> HostIndex:
    return HostIndex(tmp_path / "hosts.json")


def _register(idx: HostIndex, session_id: str = "s1") -> None:
    idx.register(HostRecord(session_id=session_id, port=9000, host_pid=111, child_pid=222))


def test_claim_on_never_claimed_record_succeeds(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx)
    rec = idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    assert rec.owner_generation == "gen-a"
    assert rec.owner_pid == 500
    # Persisted durably.
    reloaded = HostIndex(tmp_path / "hosts.json")
    assert reloaded.get("s1").owner_generation == "gen-a"


def test_claim_missing_session_raises_keyerror(tmp_path: Path):
    idx = _idx(tmp_path)
    with pytest.raises(KeyError):
        idx.claim("nope", generation="gen-a", owner_pid=1, pid_alive=lambda p: True)


def test_idempotent_reclaim_by_same_generation(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx)
    idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    # Re-claiming with the same generation/pid is a no-op success, even
    # though the "owner" would otherwise look live.
    rec = idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    assert rec.owner_generation == "gen-a"


def test_claim_conflicts_with_a_live_different_generation(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx)
    idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    with pytest.raises(ClaimConflict) as exc_info:
        idx.claim("s1", generation="gen-b", owner_pid=600, pid_alive=lambda p: p == 500)
    assert exc_info.value.key == "s1"
    assert exc_info.value.held_by_generation == "gen-a"
    assert exc_info.value.held_by_pid == 500
    # The conflicting claim never touched the record.
    assert idx.get("s1").owner_generation == "gen-a"


def test_claim_recovers_a_stale_claim_without_a_live_handshake(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx)
    idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    # gen-a's owner_pid (500) is now dead -- gen-b may take over freely.
    rec = idx.claim("s1", generation="gen-b", owner_pid=600, pid_alive=lambda p: False)
    assert rec.owner_generation == "gen-b"
    assert rec.owner_pid == 600


def test_force_overrides_a_live_conflict(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx)
    idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    rec = idx.claim(
        "s1", generation="gen-b", owner_pid=600, pid_alive=lambda p: True, force=True,
    )
    assert rec.owner_generation == "gen-b"


def test_release_only_by_the_owning_generation(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx)
    idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    # A different (or stale) generation cannot release someone else's claim.
    assert idx.release("s1", "gen-b") is False
    assert idx.get("s1").owner_generation == "gen-a"
    assert idx.release("s1", "gen-a") is True
    assert idx.get("s1").owner_generation == ""
    assert idx.get("s1").owner_pid == 0


def test_release_missing_session_is_false(tmp_path: Path):
    idx = _idx(tmp_path)
    assert idx.release("nope", "gen-a") is False


def test_release_all_sweeps_every_record_the_generation_holds(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx, "s1")
    _register(idx, "s2")
    _register(idx, "s3")
    idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    idx.claim("s2", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    idx.claim("s3", generation="gen-b", owner_pid=999, pid_alive=lambda p: True)

    released = idx.release_all("gen-a")
    assert set(released) == {"s1", "s2"}
    assert idx.get("s1").owner_generation == ""
    assert idx.get("s2").owner_generation == ""
    # gen-b's own claim is untouched.
    assert idx.get("s3").owner_generation == "gen-b"


def test_claims_owned_by(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx, "s1")
    _register(idx, "s2")
    idx.claim("s1", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    idx.claim("s2", generation="gen-b", owner_pid=600, pid_alive=lambda p: True)
    owned = idx.claims_owned_by("gen-a")
    assert [r.session_id for r in owned] == ["s1"]


def test_recoverable_claims_finds_dead_and_never_claimed(tmp_path: Path):
    idx = _idx(tmp_path)
    _register(idx, "s1")  # never claimed
    _register(idx, "s2")
    _register(idx, "s3")
    idx.claim("s2", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)
    idx.claim("s3", generation="gen-a", owner_pid=500, pid_alive=lambda p: True)

    # Only pid 500 is alive.
    recoverable = idx.recoverable_claims(pid_alive=lambda p: p == 500)
    # s1 (never claimed) is recoverable; s2/s3 (live owner) are not.
    assert {r.session_id for r in recoverable} == {"s1"}

    # Now the owning generation's pid dies too.
    recoverable = idx.recoverable_claims(pid_alive=lambda p: False)
    assert {r.session_id for r in recoverable} == {"s1", "s2", "s3"}
