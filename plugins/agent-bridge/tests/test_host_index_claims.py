"""Tests for generation-scoped session-host claims (effort
agent-bridge-unified-zdd-cutover, Phase 2).

``HostIndex`` already durably tracks each session-host's location; these
tests cover the claim/release/recover primitive layered on top of it via
``zdd.claims``.
"""

from __future__ import annotations

import os
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


# -- cross-process write safety (PR #4543 review) -----------------------


def test_a_second_instances_write_is_not_lost_by_a_stale_first_instance(tmp_path: Path):
    """The exact race two daemon generations hit during a cutover: two
    ``HostIndex`` instances over the same file, one (the old generation)
    holding a stale in-memory snapshot from before the other (the new
    generation) registered a fresh record. The stale instance's own later
    write must not silently drop the fresh one."""
    path = tmp_path / "hosts.json"
    old_generation = HostIndex(path)
    _register(old_generation, "s1")  # old generation's own session

    # New generation opens its own instance (loads the same on-disk state)
    # and registers a session the old generation never saw.
    new_generation = HostIndex(path)
    _register(new_generation, "s2")

    # Old generation now performs its own exit-contract release -- a plain
    # mutation, same as any other -- while its in-memory snapshot still only
    # knows about "s1".
    old_generation.claim("s1", generation="old-gen", owner_pid=1, pid_alive=lambda p: True)
    released = old_generation.release_all("old-gen")
    assert released == ["s1"]

    # s2 (the new generation's own write) must have survived the old
    # generation's later flush.
    reloaded = HostIndex(path)
    assert reloaded.get("s1") is not None
    assert reloaded.get("s2") is not None


def test_claim_reloads_latest_state_across_instances(tmp_path: Path):
    """A claim decision must be made against the LATEST on-disk claim state,
    not a stale in-memory snapshot from before another process's claim."""
    path = tmp_path / "hosts.json"
    a = HostIndex(path)
    _register(a, "s1")

    b = HostIndex(path)
    b.claim("s1", generation="gen-b", owner_pid=os.getpid(), pid_alive=lambda p: True)

    # `a`'s in-memory snapshot predates `b`'s claim -- its own claim attempt
    # must still see `b`'s live claim (via a reload), not silently win.
    with pytest.raises(ClaimConflict):
        a.claim("s1", generation="gen-a", owner_pid=os.getpid(), pid_alive=lambda p: True)


def test_concurrent_writers_from_two_instances_do_not_lose_updates(tmp_path: Path):
    """A genuinely overlapping-in-time write from two ``HostIndex`` instances
    over the same file (PR #4543 review: the sequential tests above prove
    reload-before-write is correct, but would still pass even if the
    cross-process lock were removed entirely -- this test forces real
    temporal overlap via a barrier, so it only passes if the lock actually
    excludes one writer while the other is mid reload-mutate-flush)."""
    import threading

    path = tmp_path / "hosts.json"
    seed = HostIndex(path)
    for i in range(20):
        _register(seed, f"s{i}")

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _writer(idx: int) -> None:
        try:
            barrier.wait(timeout=5.0)
            hi = HostIndex(path)
            for i in range(20):
                hi.set_resume_flag(f"s{i}", value=(idx == 0))
        except BaseException as exc:  # noqa: BLE001 -- surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, errors
    assert not any(t.is_alive() for t in threads)

    # Every record must still be present -- neither writer's flush dropped
    # the other's records, regardless of which one's `resume_on_reattach`
    # value "won" the final write for each session.
    final = HostIndex(path)
    assert len(final.all()) == 20
    for i in range(20):
        assert final.get(f"s{i}") is not None
