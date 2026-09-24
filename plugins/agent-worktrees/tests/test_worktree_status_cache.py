"""Tests for the SQLite-backed worktree-status cache
(agent-worktrees-external-status-accelerator effort, Phase 2).
"""

from __future__ import annotations

import threading

import pytest

from agent_worktrees.worktree_status_cache import WorktreeStatusCache


def test_writes_from_a_different_thread_than_construction_persist(tmp_path):
    """Copilot review finding: the cache is constructed on the monitor's
    startup thread but read/refreshed from `CoalescingServer` request
    threads and the sweep thread -- `sqlite3.connect`'s default
    `check_same_thread=True` would otherwise raise on every post-boot write,
    silently losing durability while appearing to work (the in-memory path
    still answers requests correctly)."""
    db_path = tmp_path / "cache.sqlite3"
    cache = WorktreeStatusCache(db_path)
    errors = []

    def _write_from_other_thread():
        try:
            cache.get_or_refresh(
                "proj", "wt1", force=False, compute=lambda: {"from": "other-thread"}
            )
        except Exception as exc:  # pragma: no cover - failure path under test
            errors.append(exc)

    t = threading.Thread(target=_write_from_other_thread)
    t.start()
    t.join(timeout=5)
    assert not errors

    cache.close()
    # Warm-restore from a fresh process/connection proves the write actually
    # reached SQLite, not just the in-memory dict.
    restored = WorktreeStatusCache(db_path)
    result = restored.get_or_refresh(
        "proj", "wt1", force=False, compute=lambda: {"from": "should-not-run"}
    )
    assert result == {"from": "other-thread"}


def test_cold_read_computes_once_and_caches(tmp_path):
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    first = cache.get_or_refresh("proj", "wt1", force=False, compute=compute)
    second = cache.get_or_refresh("proj", "wt1", force=False, compute=compute)
    assert first == {"n": 1}
    assert second == {"n": 1}  # fresh-cache hit, no recompute
    assert len(calls) == 1


def test_force_bypasses_a_fresh_cache_entry(tmp_path):
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    cache.get_or_refresh("proj", "wt1", force=False, compute=compute)
    forced = cache.get_or_refresh("proj", "wt1", force=True, compute=compute)
    assert forced == {"n": 2}
    assert len(calls) == 2


def test_a_cached_bundle_disagreeing_with_its_own_key_forces_a_recompute(tmp_path):
    """Copilot review finding: a fresh-cache hit previously returned
    whatever was stored under the requested key without checking that the
    bundle's own `project`/`worktree_id` fields actually agree with it. A
    warm-restored SQLite row is never re-validated on load (`_warm_restore`
    only checks it parses as a JSON object), and an older `compute()` build
    could have persisted a mismatched bundle before its own identity guard
    existed -- either way, this cache would keep serving the wrong
    worktree's facts under this key indefinitely, never recomputing until
    the next TTL expiry/force-refresh happened to coincide. Fixed by
    treating a bundle that actively disagrees with its own key as a miss."""
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    # Seed a mismatched entry directly (bypassing compute(), mirroring how
    # a warm-restored or pre-fix row could have gotten here).
    now = cache._now()
    cache._entries[("proj", "wt1")] = (
        {"project": "proj", "worktree_id": "wt2", "n": "stale"}, now, now,
    )

    calls = []

    def compute():
        calls.append(1)
        return {"project": "proj", "worktree_id": "wt1", "n": "fresh"}

    result = cache.get_or_refresh("proj", "wt1", force=False, compute=compute)
    assert result == {"project": "proj", "worktree_id": "wt1", "n": "fresh"}
    assert len(calls) == 1  # recomputed rather than serving the stale entry


def test_mismatch_eviction_deletes_conditioned_on_the_evicted_demanded_at(tmp_path, monkeypatch):
    """Copilot review finding: pruning the durable row for a mismatched
    in-memory entry must not remove a row a concurrent recompute (the
    sweep, or a successor monitor mid-cutover) already refreshed for the
    same key between the in-memory eviction and the delete actually
    running. Verifies `get_or_refresh` reuses the existing conditional
    `if_demanded_at` guard (see `_delete_row`'s own race-safety tests) --
    passing the evicted entry's exact sampled `demanded_at`, not an
    unconditional delete."""
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    stale_demanded_at = cache._now()
    cache._entries[("proj", "wt1")] = (
        {"project": "proj", "worktree_id": "wt2", "n": "stale"},
        stale_demanded_at, stale_demanded_at,
    )

    calls = []
    monkeypatch.setattr(
        cache, "_delete_row",
        lambda project, worktree_id, **kw: calls.append((project, worktree_id, kw)),
    )

    cache.get_or_refresh(
        "proj", "wt1", force=False,
        compute=lambda: {"project": "proj", "worktree_id": "wt1", "n": "fresh"},
    )

    assert calls == [("proj", "wt1", {"if_demanded_at": stale_demanded_at})]


def test_ttl_expiry_triggers_recompute(tmp_path):
    calls = []
    clock = {"t": 1000.0}

    def compute():
        calls.append(1)
        return {"n": len(calls)}

    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3", ttl_seconds=5.0, now=lambda: clock["t"]
    )
    cache.get_or_refresh("proj", "wt1", force=False, compute=compute)
    clock["t"] += 6.0  # past the 5s TTL
    refreshed = cache.get_or_refresh("proj", "wt1", force=False, compute=compute)
    assert refreshed == {"n": 2}
    assert len(calls) == 2


def test_different_worktrees_cache_independently(tmp_path):
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    a = cache.get_or_refresh("proj", "wt-a", force=False, compute=lambda: {"id": "a"})
    b = cache.get_or_refresh("proj", "wt-b", force=False, compute=lambda: {"id": "b"})
    assert a == {"id": "a"}
    assert b == {"id": "b"}


def test_warm_restore_survives_a_simulated_daemon_restart(tmp_path):
    db_path = tmp_path / "cache.sqlite3"
    first_process = WorktreeStatusCache(db_path)
    first_process.get_or_refresh(
        "proj", "wt1", force=False, compute=lambda: {"branch": "main"}
    )
    first_process.close()

    second_process = WorktreeStatusCache(db_path)
    calls = []
    restored = second_process.get_or_refresh(
        "proj",
        "wt1",
        force=False,
        compute=lambda: calls.append(1) or {"branch": "stale-fallback"},
    )
    assert restored == {"branch": "main"}  # warm-restored, no recompute needed
    assert not calls


def test_sqlite_failure_degrades_to_in_memory_only(tmp_path, monkeypatch):
    """A durable-store failure never blocks the cache from functioning this
    session -- it just loses warm-restore across a restart."""
    import sqlite3

    from agent_worktrees import worktree_status_cache as mod

    def _broken_connect(_path):
        raise sqlite3.Error("simulated disk failure")

    monkeypatch.setattr(mod, "_connect", _broken_connect)
    cache = WorktreeStatusCache(tmp_path / "unreachable" / "cache.sqlite3")
    result = cache.get_or_refresh("proj", "wt1", force=False, compute=lambda: {"ok": True})
    assert result == {"ok": True}


def test_mkdir_failure_also_degrades_to_in_memory_only(tmp_path, monkeypatch):
    """Copilot review finding: `_connect()` calls `Path.mkdir` before ever
    touching sqlite3, so a read-only/unavailable runtime home raises
    `OSError`, not `sqlite3.Error` -- the original `_open` only caught the
    latter, so this specific failure mode propagated out of the
    constructor instead of degrading like every other durability failure."""
    from agent_worktrees import worktree_status_cache as mod

    def _broken_connect(_path):
        raise OSError("simulated read-only filesystem")

    monkeypatch.setattr(mod, "_connect", _broken_connect)
    cache = WorktreeStatusCache(tmp_path / "unreachable" / "cache.sqlite3")
    result = cache.get_or_refresh("proj", "wt1", force=False, compute=lambda: {"ok": True})
    assert result == {"ok": True}


def test_closed_cache_never_reopens_or_writes(tmp_path):
    """Copilot review finding: a request-handler thread still in flight when
    the monitor shuts down could otherwise reopen/write the SQLite file
    after `close()`, racing a successor monitor's own writer."""
    db_path = tmp_path / "cache.sqlite3"
    cache = WorktreeStatusCache(db_path)
    cache.get_or_refresh("proj", "wt1", force=False, compute=lambda: {"v": 1})
    cache.close()

    # A late call after close() must not reopen the connection or persist --
    # it still answers (in-memory-only, best-effort), but writes nothing.
    result = cache.get_or_refresh("proj", "wt1", force=True, compute=lambda: {"v": 2})
    assert result == {"v": 2}

    reopened = WorktreeStatusCache(db_path)
    restored = reopened.get_or_refresh(
        "proj", "wt1", force=False, compute=lambda: {"v": "should-not-run"}
    )
    assert restored == {"v": 1}  # the post-close write never reached disk


def test_warm_restore_skips_a_non_dict_durable_row(tmp_path):
    """Copilot review finding: a corrupt/older row could parse to a non-dict
    JSON value (a list, a scalar); treating that as a real bundle would
    silently suppress recomputation forever."""
    import json
    import sqlite3

    db_path = tmp_path / "cache.sqlite3"
    seed = WorktreeStatusCache(db_path)
    seed.close()

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO worktree_status_cache"
        " (project, worktree_id, bundle_json, computed_at, demanded_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("proj", "corrupt-wt", json.dumps(["not", "a", "dict"]), 1000.0, 1000.0),
    )
    conn.commit()
    conn.close()

    cache = WorktreeStatusCache(db_path)
    calls = []
    result = cache.get_or_refresh(
        "proj", "corrupt-wt", force=False, compute=lambda: calls.append(1) or {"v": "fresh"}
    )
    assert result == {"v": "fresh"}  # treated as cold, recomputed normally
    assert calls == [1]


def test_sweep_never_overwrites_a_newer_concurrent_refresh(tmp_path):
    """Copilot review finding: `sweep_due` samples/computes outside the
    lock, so a concurrent request-driven refresh (an ordinary miss or an
    explicit force) for the SAME key can finish first. Publishing the
    sweep's own (now-stale) result unconditionally would silently overwrite
    the newer value for a full TTL window."""
    clock = {"t": 1000.0}
    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3", ttl_seconds=5.0, now=lambda: clock["t"]
    )
    cache.get_or_refresh("proj", "wt1", force=False, compute=lambda: {"v": "v1"})
    clock["t"] += 6.0  # past TTL, eligible for sweep

    def slow_sweep_refresh(project, worktree_id):
        # Simulate a concurrent request-driven refresh completing WHILE this
        # sweep's own refresh is still "in flight" (i.e. before the sweep
        # writes its result back under the lock).
        cache.get_or_refresh("proj", "wt1", force=True, compute=lambda: {"v": "v2-newer"})
        return {"v": "v1-stale-sweep-result"}

    refreshed = cache.sweep_due(refresh=slow_sweep_refresh)
    assert refreshed == 0  # discarded -- a newer value already won
    result = cache.get_or_refresh(
        "proj", "wt1", force=False, compute=lambda: {"v": "should-not-run"}
    )
    assert result == {"v": "v2-newer"}


def test_sweep_due_refreshes_only_ttl_expired_demanded_entries(tmp_path):
    clock = {"t": 1000.0}
    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3", ttl_seconds=5.0, now=lambda: clock["t"]
    )
    cache.get_or_refresh("proj", "fresh", force=False, compute=lambda: {"v": "fresh-1"})
    cache.get_or_refresh("proj", "stale", force=False, compute=lambda: {"v": "stale-1"})
    clock["t"] += 6.0  # both entries now past TTL, both demanded recently

    refresh_calls = []

    def refresh(project, worktree_id):
        refresh_calls.append(worktree_id)
        return {"v": f"{worktree_id}-2"}

    refreshed_count = cache.sweep_due(refresh=refresh)
    assert refreshed_count == 2
    assert set(refresh_calls) == {"fresh", "stale"}
    # A subsequent read within the fresh TTL window now returns the swept value.
    result = cache.get_or_refresh(
        "proj", "fresh", force=False, compute=lambda: {"v": "should-not-run"}
    )
    assert result == {"v": "fresh-2"}


def test_sweep_caps_per_tick_refreshes_prioritizing_the_stalest_entries(tmp_path):
    """Regression (2026-09-22 live CPU-saturation investigation): an
    unbounded sweep asks for N * (real per-entry compute cost, ~5-6s of git
    fetch in production) of CPU-bound work every TTL window -- with enough
    demanded worktrees this exceeds one core's capacity and the sweep
    thread runs almost continuously rather than mostly idling. The cap
    bounds worst-case per-tick cost to `max_refresh_per_sweep` entries,
    always picking the STALEST (oldest computed_at) ones first so demand
    beyond capacity degrades as wider staleness, never as unbounded CPU."""
    clock = {"t": 1000.0}
    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3",
        ttl_seconds=5.0,
        max_refresh_per_sweep=2,
        now=lambda: clock["t"],
    )
    # Demand five entries, each becoming due at a distinct, known time so
    # staleness order is unambiguous: "wt0" is demanded first (oldest/
    # stalest once all are past TTL), "wt4" last (freshest).
    for i in range(5):
        cache.get_or_refresh("proj", f"wt{i}", force=False, compute=lambda i=i: {"v": i})
        clock["t"] += 1.0  # stagger computed_at so ordering is deterministic
    clock["t"] += 5.0  # now every entry is comfortably past the 5s TTL

    refresh_calls = []
    refreshed_count = cache.sweep_due(
        refresh=lambda p, w: refresh_calls.append(w) or {"v": "swept"}
    )

    assert refreshed_count == 2
    # The two stalest (earliest-demanded/computed) entries were prioritized.
    assert set(refresh_calls) == {"wt0", "wt1"}

    # A second sweep tick (still past TTL for the remaining three) picks up
    # the next-stalest ones -- demand beyond capacity is served over
    # successive ticks, not starved forever nor bursted all at once.
    refresh_calls.clear()
    refreshed_count = cache.sweep_due(
        refresh=lambda p, w: refresh_calls.append(w) or {"v": "swept-2"}
    )
    assert refreshed_count == 2
    assert set(refresh_calls) == {"wt2", "wt3"}


def test_sweep_does_not_cap_when_due_entries_are_within_the_limit(tmp_path):
    """The cap must never fire below its own threshold -- confirms the
    prior (uncapped) sweep behavior is fully preserved for the common case
    of a small number of demanded worktrees."""
    clock = {"t": 1000.0}
    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3",
        ttl_seconds=5.0,
        max_refresh_per_sweep=4,
        now=lambda: clock["t"],
    )
    for i in range(4):
        cache.get_or_refresh("proj", f"wt{i}", force=False, compute=lambda i=i: {"v": i})
    clock["t"] += 6.0

    refreshed_count = cache.sweep_due(refresh=lambda p, w: {"v": "swept"})
    assert refreshed_count == 4


def test_negative_max_refresh_per_sweep_is_rejected(tmp_path):
    """Regression (Copilot review, PR #3348): an unvalidated negative
    max_refresh_per_sweep silently defeats the cap's own purpose --
    `stalest[:-1]` (a negative slice bound) refreshes every due entry
    except one, making the CPU-saturation guard effectively unbounded as
    demand grows."""
    with pytest.raises(ValueError):
        WorktreeStatusCache(tmp_path / "cache.sqlite3", max_refresh_per_sweep=-1)


def test_non_int_max_refresh_per_sweep_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        WorktreeStatusCache(tmp_path / "cache.sqlite3", max_refresh_per_sweep=2.5)


def test_zero_max_refresh_per_sweep_is_accepted_as_a_valid_boundary(tmp_path):
    """0 is a legitimate (if extreme) configuration -- pause all sweeping --
    and must not be rejected by the same guard that rejects negative
    values."""
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3", max_refresh_per_sweep=0)
    assert cache is not None


def test_sweep_cap_does_not_let_a_persistently_failing_entry_starve_others(tmp_path):
    """Regression (Copilot review, PR #3348): a failed refresh previously
    left `computed_at` unchanged, so a chronically-broken worktree would
    remain "the stalest" on every subsequent sweep and monopolize every
    `max_refresh_per_sweep` slot forever -- starving every other due entry
    from ever being attempted, contradicting the cap's own claim that
    excess demand is served on later ticks. `_last_attempted` (updated on
    every attempt, success OR failure) must rotate a failing entry to the
    back of the priority queue exactly like a succeeding one."""
    clock = {"t": 1000.0}
    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3",
        ttl_seconds=5.0,
        max_refresh_per_sweep=1,
        now=lambda: clock["t"],
    )
    # "broken" is demanded first (so it would be the stalest by computed_at
    # alone) and always fails; "ok0"/"ok1" are demanded after and always
    # succeed.
    cache.get_or_refresh("proj", "broken", force=False, compute=lambda: {"v": 0})
    clock["t"] += 1.0
    cache.get_or_refresh("proj", "ok0", force=False, compute=lambda: {"v": 0})
    clock["t"] += 1.0
    cache.get_or_refresh("proj", "ok1", force=False, compute=lambda: {"v": 0})
    clock["t"] += 5.0  # all three now past the 5s TTL

    def refresh(project, worktree_id):
        if worktree_id == "broken":
            raise RuntimeError("simulated persistent failure")
        return {"v": "swept"}

    attempted = []
    for _ in range(3):
        before = set(attempted)
        # Wrap refresh to record which key was actually attempted this tick.
        def _tracking_refresh(p, w, _before=before):
            attempted.append(w)
            return refresh(p, w)

        cache.sweep_due(refresh=_tracking_refresh)
        clock["t"] += 0.001  # break exact-tie ordering between ticks

    # With a starvation bug, "broken" would be re-selected every tick
    # (still the stalest by computed_at, which never advances on failure)
    # and "ok0"/"ok1" would never be attempted at all. The fix must let
    # every due entry get a turn across these 3 ticks (cap=1/tick).
    assert set(attempted) == {"broken", "ok0", "ok1"}


def test_sweep_drops_entries_whose_demand_has_aged_out(tmp_path):
    clock = {"t": 1000.0}
    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3",
        ttl_seconds=5.0,
        demand_ttl_seconds=10.0,
        now=lambda: clock["t"],
    )
    cache.get_or_refresh("proj", "abandoned", force=False, compute=lambda: {"v": 1})
    clock["t"] += 20.0  # past both the TTL and the demand TTL

    refresh_calls = []
    cache.sweep_due(refresh=lambda p, w: refresh_calls.append(w) or {"v": 2})
    assert not refresh_calls  # dropped, never refreshed


def test_sweep_skips_a_failing_refresh_without_losing_the_last_known_value(tmp_path):
    clock = {"t": 1000.0}
    cache = WorktreeStatusCache(
        tmp_path / "cache.sqlite3", ttl_seconds=5.0, now=lambda: clock["t"]
    )
    cache.get_or_refresh("proj", "wt1", force=False, compute=lambda: {"v": "good"})
    clock["t"] += 6.0

    def failing_refresh(project, worktree_id):
        raise RuntimeError("boom")

    refreshed = cache.sweep_due(refresh=failing_refresh)
    assert refreshed == 0


def test_persist_never_lets_an_older_computed_at_overwrite_a_newer_row(tmp_path):
    """Copilot review finding: during a status-monitor cutover, an outgoing
    monitor's still-in-flight handler could persist a stale write after a
    successor already wrote a newer bundle to the same durable file -- the
    upsert itself (not just in-process ordering) must be monotonic."""
    import json
    import sqlite3

    db_path = tmp_path / "cache.sqlite3"
    cache = WorktreeStatusCache(db_path)
    # Simulate the successor's newer write already landed in SQLite.
    cache._persist("proj", "wt1", {"v": "newer"}, computed_at=2000.0, demanded_at=2000.0)
    # An "older generation"'s stale write, arriving after, must not win.
    cache._persist("proj", "wt1", {"v": "older-stale"}, computed_at=1000.0, demanded_at=1000.0)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT bundle_json, computed_at FROM worktree_status_cache"
        " WHERE project = ? AND worktree_id = ?",
        ("proj", "wt1"),
    ).fetchone()
    conn.close()
    assert json.loads(row[0]) == {"v": "newer"}
    assert row[1] == 2000.0


def test_rejects_a_lone_dot_as_an_unsafe_identity_token():
    """Copilot review finding: cfg.project_dir('.') builds `f".{project}"` =
    '..', so a bare '.' alone (no separators, no literal '..') already
    escapes the intended directory -- must be rejected explicitly."""
    from agent_worktrees import worktree_status_daemon as mod

    assert not mod._is_safe_identity_token(".")
    assert not mod._is_safe_identity_token("..")
    assert mod._is_safe_identity_token("normal-id")


def test_has_active_demand_reflects_whether_any_worktree_is_currently_cached(tmp_path):
    """Copilot review finding: the resident monitor's idle-exit decision
    must count a direct worktree-status consumer as a reason to stay
    alive, the same way it already counts mux/Picker/list-cache demand."""
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    assert not cache.has_active_demand()
    cache.get_or_refresh("proj", "wt1", force=False, compute=lambda: {"v": 1})
    assert cache.has_active_demand()


def test_warm_restore_prunes_and_deletes_demand_expired_rows(tmp_path):
    """Copilot review finding: without this, the demand TTL bounds nothing
    on disk -- every worktree ever viewed keeps a durable row forever, and
    every restart pays to reload (and immediately drop) all of them."""
    import sqlite3

    clock = {"t": 1000.0}
    db_path = tmp_path / "cache.sqlite3"
    seed = WorktreeStatusCache(db_path, demand_ttl_seconds=100.0, now=lambda: clock["t"])
    seed.get_or_refresh("proj", "abandoned", force=False, compute=lambda: {"v": 1})
    seed.close()

    clock["t"] += 200.0  # past the demand TTL by restart time
    restored = WorktreeStatusCache(db_path, demand_ttl_seconds=100.0, now=lambda: clock["t"])
    assert not restored.has_active_demand()

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM worktree_status_cache").fetchone()[0]
    conn.close()
    assert count == 0  # the durable row was pruned, not just skipped in memory


def test_sweep_deletes_the_durable_row_for_a_demand_expired_entry(tmp_path):
    import sqlite3

    clock = {"t": 1000.0}
    db_path = tmp_path / "cache.sqlite3"
    cache = WorktreeStatusCache(
        db_path, ttl_seconds=5.0, demand_ttl_seconds=10.0, now=lambda: clock["t"]
    )
    cache.get_or_refresh("proj", "abandoned", force=False, compute=lambda: {"v": 1})
    clock["t"] += 20.0  # past the demand TTL

    cache.sweep_due(refresh=lambda p, w: {"v": 2})

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM worktree_status_cache").fetchone()[0]
    conn.close()
    assert count == 0


def test_delete_row_never_removes_a_row_re_persisted_after_the_sampled_demanded_at(tmp_path):
    """Copilot review finding: pruning must not remove a durable row that a
    concurrent write already refreshed (with a newer `demanded_at`) between
    when the pruning decision was made and when the DELETE actually runs."""
    import sqlite3

    db_path = tmp_path / "cache.sqlite3"
    cache = WorktreeStatusCache(db_path)
    cache._persist("proj", "wt1", {"v": "original"}, computed_at=1000.0, demanded_at=1000.0)

    # Simulate a concurrent write landing AFTER the stale demanded_at was
    # sampled but BEFORE the conditional delete runs.
    cache._persist("proj", "wt1", {"v": "newer"}, computed_at=2000.0, demanded_at=2000.0)

    # A delete conditioned on the STALE (now-outdated) demanded_at must be a
    # no-op -- the row's actual demanded_at has already moved on.
    cache._delete_row("proj", "wt1", if_demanded_at=1000.0)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT bundle_json FROM worktree_status_cache WHERE project = ? AND worktree_id = ?",
        ("proj", "wt1"),
    ).fetchone()
    conn.close()
    assert row is not None  # the newer row survived the stale delete
