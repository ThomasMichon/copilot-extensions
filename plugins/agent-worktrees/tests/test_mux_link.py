"""Tests for ``mux_link`` -- the resident manager-observation IPC seam
(Phase 3b Slice 2 Sub-slice 3 Step 1, ``worktree-manager-control-plane``
effort). Mirrors ``test_worktree_status_daemon.py``'s style. Payload shapes
match the documented ``mux-live-v1`` contract in
``efforts/active/worktree-manager-control-plane/phase-3b-substatus-monitor-relocation.md``."""

from __future__ import annotations

import json
import threading
import time

from agent_worktrees import mux_link


def _endpoint_dict(server) -> dict:
    return mux_link.rendezvous_fields(server)


def _obs(
    project="proj",
    worktree_id="wt-1",
    mux_session="wt-1",
    revision=1,
    live=True,
    **extra,
) -> dict:
    payload = {
        "project": project,
        "worktree_id": worktree_id,
        "mux_session": mux_session,
        "mapping_revision": revision,
        "live": live,
    }
    payload.update(extra)
    return payload


# -- ManagedMuxCache ----------------------------------------------------


def test_apply_observation_stores_a_new_mapping():
    cache = mux_link.ManagedMuxCache()
    result = cache.apply_observation(_obs())
    assert result == {"applied": True, "revision": 1}
    entry = cache.get("proj", "wt-1")
    assert entry is not None
    assert entry["project"] == "proj"
    assert entry["mux_session"] == "wt-1"
    assert entry["live"] is True
    assert entry["mapping_revision"] == 1


def test_apply_observation_accepts_a_higher_revision():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(revision=1))
    result = cache.apply_observation(_obs(revision=2, live=False))
    assert result == {"applied": True, "revision": 2}
    assert cache.get("proj", "wt-1")["live"] is False


def test_apply_observation_rejects_a_stale_revision():
    """Copilot review contract: an older ``live: false``/stale-pane event must
    never clobber a newer live mapping that already arrived."""
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(revision=5, live=True))
    result = cache.apply_observation(_obs(revision=3, live=False))
    assert result == {"applied": False, "reason": "stale_revision", "current_revision": 5}
    assert cache.get("proj", "wt-1")["live"] is True  # unchanged


def test_apply_observation_accepts_a_replayed_equal_revision():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(revision=4))
    result = cache.apply_observation(_obs(revision=4, live=False))
    assert result == {"applied": True, "revision": 4}
    assert cache.get("proj", "wt-1")["live"] is False


def test_apply_observation_requires_project_worktree_id_and_mux_session():
    cache = mux_link.ManagedMuxCache()
    for bad in (
        {"worktree_id": "wt-1", "mux_session": "wt-1", "mapping_revision": 1},
        {"project": "proj", "mux_session": "wt-1", "mapping_revision": 1},
        {"project": "proj", "worktree_id": "wt-1", "mapping_revision": 1},
        {"project": "proj", "worktree_id": "", "mux_session": "wt-1", "mapping_revision": 1},
        {"project": "proj", "worktree_id": "wt-1", "mux_session": 5, "mapping_revision": 1},
    ):
        try:
            cache.apply_observation(bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, bad


def test_apply_observation_requires_an_integer_mapping_revision():
    cache = mux_link.ManagedMuxCache()
    for bad_revision in (None, "1", 1.5, True):
        try:
            cache.apply_observation(_obs(revision=bad_revision))
            raised = False
        except ValueError:
            raised = True
        assert raised, bad_revision


def test_apply_observation_requires_a_boolean_live_field():
    cache = mux_link.ManagedMuxCache()
    for bad_live in ("true", 1, 0, None):
        try:
            cache.apply_observation(_obs(live=bad_live))
            raised = False
        except ValueError:
            raised = True
        assert raised, bad_live


def test_apply_observation_rejects_negative_revision():
    cache = mux_link.ManagedMuxCache()
    try:
        cache.apply_observation(_obs(revision=-1))
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_apply_observation_defaults_live_to_true_and_sanitizes_optional_fields():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-2",
            "mux_session": "wt-2",
            "mapping_revision": 1,
            "panes": "not-a-list",
            "session_incarnation": 5,
            "attached_clients": "two",
        }
    )
    entry = cache.get("proj", "wt-2")
    assert entry["live"] is True
    assert entry["panes"] == []
    assert entry["session_incarnation"] == ""
    assert entry["attached_clients"] == 0
    assert isinstance(entry["observed_at"], str) and entry["observed_at"]


def test_apply_observation_preserves_a_caller_supplied_observed_at():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(observed_at="2026-09-17T08:00:00Z"))
    assert cache.get("proj", "wt-1")["observed_at"] == "2026-09-17T08:00:00Z"


def test_apply_observation_normalizes_pane_objects_and_drops_malformed_ones():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        _obs(
            panes=[
                {"pane_id": "%1", "role": "head", "live": True},
                {"pane_id": "%2"},  # role/live default
                {"role": "orphan"},  # missing pane_id -- dropped
                "not-a-dict",  # dropped
                123,  # dropped
            ]
        )
    )
    entry = cache.get("proj", "wt-1")
    assert entry["panes"] == [
        {"pane_id": "%1", "role": "head", "live": True},
        {"pane_id": "%2", "role": "", "live": True},
    ]


def test_live_session_names_only_includes_currently_live_entries():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        _obs(worktree_id="wt-a", mux_session="wt-a", revision=1, live=True)
    )
    cache.apply_observation(
        _obs(worktree_id="wt-b", mux_session="wt-b", revision=1, live=False)
    )
    assert cache.live_session_names() == {"wt-a"}


def test_snapshot_is_a_defensive_copy():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs())
    snap = cache.snapshot()
    snap[("proj", "wt-1")]["live"] = False
    assert cache.get("proj", "wt-1")["live"] is True  # mutation of the copy didn't leak back


def test_get_and_snapshot_deep_copy_the_panes_list():
    """Copilot review finding: ``dict(entry)`` alone leaves the stored
    ``panes`` list (and each pane dict within it) shared -- a caller
    mutating a returned list/dict would silently corrupt the live cache
    without holding its lock."""
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(panes=[{"pane_id": "%1", "role": "head", "live": True}]))

    got = cache.get("proj", "wt-1")
    got["panes"].append({"pane_id": "INJECTED", "role": "", "live": True})
    got["panes"][0]["role"] = "TAMPERED"
    fresh = cache.get("proj", "wt-1")
    assert len(fresh["panes"]) == 1
    assert fresh["panes"][0]["role"] == "head"

    snap = cache.snapshot()
    snap[("proj", "wt-1")]["panes"][0]["live"] = False
    assert cache.get("proj", "wt-1")["panes"][0]["live"] is True


def test_has_any_live_reflects_current_state():
    cache = mux_link.ManagedMuxCache()
    assert cache.has_any_live() is False
    cache.apply_observation(_obs(live=True))
    assert cache.has_any_live() is True
    cache.apply_observation(_obs(revision=2, live=False))
    assert cache.has_any_live() is False


def test_close_makes_apply_observation_a_safe_no_op():
    """Copilot review finding: ``CoalescingServer.close()`` does not wait
    for an already-dispatched handler thread to finish, so a late handler
    could still call into a cache whose owning runtime has already shut
    down and atomically persist an older snapshot after a replacement
    runtime already persisted a newer revision -- regressing the on-disk
    monotonicity guard across a restart. Once closed, every subsequent
    apply must be a safe no-op, never a write."""
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(revision=1))
    cache.close()
    result = cache.apply_observation(_obs(revision=2))
    assert result == {"applied": False, "reason": "closed"}
    assert cache.get("proj", "wt-1")["mapping_revision"] == 1  # unchanged -- never wrote


def test_close_prevents_a_late_apply_from_persisting_over_a_newer_snapshot(tmp_path):
    """End-to-end proof: after close(), a 'late handler' calling
    apply_observation for a higher revision must not overwrite a snapshot a
    'replacement runtime' already persisted."""
    persist_path = tmp_path / "managed-mux-cache.json"
    old_cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    old_cache.apply_observation(_obs(revision=1))
    old_cache.close()  # simulates InProcessRuntime.shutdown()

    # A replacement runtime starts, warm-loads, and persists a newer revision.
    new_cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    new_cache.apply_observation(_obs(revision=5))

    # The old, closed cache's late handler must not clobber the newer file.
    late_result = old_cache.apply_observation(_obs(revision=2))
    assert late_result == {"applied": False, "reason": "closed"}

    reloaded = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert reloaded.get("proj", "wt-1")["mapping_revision"] == 5  # the newer snapshot survived


def test_stale_live_mapping_is_excluded_from_live_views_but_kept_in_get(monkeypatch):
    """Copilot review finding: a crashed/partitioned Manager mux-companion
    daemon that never reports ``live: false`` must not pin the resident
    monitor's observation as live forever -- an unconfirmed mapping goes
    stale after ``MAPPING_STALE_AFTER_SECONDS`` with no follow-up push."""
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(live=True))
    assert cache.live_session_names() == {"wt-1"}
    assert cache.has_any_live() is True

    monkeypatch.setattr(mux_link, "MAPPING_STALE_AFTER_SECONDS", 0.0)
    time.sleep(0.01)  # ensure real elapsed time exceeds the now-zero threshold
    assert cache.live_session_names() == set()  # stale now, no fresh push arrived
    assert cache.has_any_live() is False
    assert cache.get("proj", "wt-1")["live"] is True  # raw record still reports its true bit


def test_staleness_is_tracked_against_local_receipt_time_not_observed_at(monkeypatch):
    """A skewed/old caller-supplied ``observed_at`` must never make a
    mapping look artificially stale (or fresh) -- freshness is judged
    against this process's own receipt time."""
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(observed_at="2000-01-01T00:00:00Z"))
    assert cache.live_session_names() == {"wt-1"}  # fresh: received just now


def test_apply_observation_ignores_a_caller_supplied_received_at():
    """Copilot review finding: the wire payload is untrusted, but
    ``_normalize_entry`` previously copied any caller-supplied
    ``received_at`` verbatim -- a future timestamp could keep a live mapping
    fresh indefinitely, while a past one made it immediately stale,
    contradicting the stated local-receipt freshness contract.
    ``apply_observation`` (the untrusted wire path) must always stamp its
    own receipt time, ignoring anything the caller supplies for this
    internal-only field."""
    cache = mux_link.ManagedMuxCache()
    far_future = time.time() + 10_000_000
    cache.apply_observation(_obs(received_at=far_future))
    entry = cache.get("proj", "wt-1")
    assert entry["received_at"] != far_future
    assert abs(entry["received_at"] - time.time()) < 5  # stamped with real receipt time

    far_past = time.time() - 10_000_000
    cache.apply_observation(_obs(revision=2, received_at=far_past))
    entry2 = cache.get("proj", "wt-1")
    assert entry2["received_at"] != far_past
    assert cache.live_session_names() == {"wt-1"}  # not incorrectly marked stale


# -- persistence -----------------------------------------------------------


def test_cache_survives_a_simulated_daemon_restart_via_persist_path(tmp_path):
    """Contract-level requirement (Step 1 validation): a mapping must
    survive one daemon restart via its runtime snapshot."""
    persist_path = tmp_path / "managed-mux-cache.json"
    first = mux_link.ManagedMuxCache(persist_path=persist_path)
    first.apply_observation(
        _obs(panes=[{"pane_id": "%1", "role": "head", "live": True}], attached_clients=2)
    )

    # Simulate a restart: a brand new cache instance loads the same file.
    second = mux_link.ManagedMuxCache(persist_path=persist_path)
    entry = second.get("proj", "wt-1")
    assert entry is not None
    assert entry["mux_session"] == "wt-1"
    assert entry["panes"] == [{"pane_id": "%1", "role": "head", "live": True}]
    assert entry["attached_clients"] == 2
    assert second.live_session_names() == {"wt-1"}


def test_persist_merges_with_disk_instead_of_blindly_overwriting(tmp_path):
    """Copilot review finding: the resident-monitor handoff can briefly run
    an old (retiring) and a new monitor process concurrently, each with its
    own in-memory ``ManagedMuxCache`` sharing the same ``persist_path``. A
    blind overwrite from one process's own view could clobber a newer
    mapping the other process already persisted for a key this process
    never learned about. ``_persist_locked`` must merge with whatever is
    currently on disk (keeping the higher ``mapping_revision`` per key)
    rather than overwrite wholesale."""
    persist_path = tmp_path / "managed-mux-cache.json"

    # "Old" process: learns about wt-a only.
    old_process_cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    old_process_cache.apply_observation(
        _obs(project="proj", worktree_id="wt-a", mux_session="wt-a", revision=1)
    )

    # "New" process: a second, independent in-memory cache instance (as if
    # a replacement monitor process started up) that warm-loaded the file
    # right after the old process's write above, then learns about a
    # DIFFERENT worktree (wt-b) the old process never knew about, and
    # persists that.
    new_process_cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    new_process_cache.apply_observation(
        _obs(project="proj", worktree_id="wt-b", mux_session="wt-b", revision=1)
    )

    # The old process's in-memory cache is still alive (a late in-flight
    # handler) and pushes one more observation for wt-a -- its own
    # _persist_locked call must not clobber wt-b, which it never learned
    # about, from the file.
    old_process_cache.apply_observation(
        _obs(project="proj", worktree_id="wt-a", mux_session="wt-a", revision=2)
    )

    reloaded = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert reloaded.get("proj", "wt-a")["mapping_revision"] == 2  # old process's later write
    assert reloaded.get("proj", "wt-b") is not None  # new process's mapping survived


def test_persist_merge_keeps_the_higher_revision_when_disk_is_ahead(tmp_path):
    """The other direction of the same merge: if the on-disk file already
    has a *higher* revision for a key than this process's own in-memory
    view (a genuinely stale in-process cache persisting after a newer
    write from elsewhere), the merge must not regress it."""
    persist_path = tmp_path / "managed-mux-cache.json"
    behind_cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    behind_cache.apply_observation(_obs(revision=3))  # writes revision 3 to disk

    ahead_cache = mux_link.ManagedMuxCache(persist_path=persist_path)  # warm-loads revision 3
    ahead_cache.apply_observation(_obs(revision=9))  # writes revision 9 to disk

    # behind_cache's own in-memory view never saw revision 9 -- replay its
    # own (now-stale) observation again, simulating a late in-flight
    # handler flushing state that predates the newer write elsewhere.
    behind_cache.apply_observation(_obs(revision=3))

    reloaded = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert reloaded.get("proj", "wt-1")["mapping_revision"] == 9  # disk's higher revision kept


def test_apply_observation_reconciles_in_memory_view_against_a_newer_disk_write(tmp_path):
    """Copilot review finding: the monotonic check previously consulted
    only this process's in-memory entry. If an old (retiring) process's
    in-memory cache is behind a replacement process's already-higher-
    revision write, a genuinely lower-but-newer-to-this-process
    observation could still get accepted into *this* process's own
    in-memory entry even though `_persist_locked` protects the file's
    higher revision -- so this process's own `live_session_names()`/
    `get()` would report a regressed (stale) view even though the on-disk
    record never regressed. `apply_observation` must reconcile against the
    persisted disk state before deciding, and update its own in-memory view
    to match whichever is newer."""
    persist_path = tmp_path / "managed-mux-cache.json"

    old_process_cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    old_process_cache.apply_observation(_obs(revision=1, live=True))

    # A replacement process starts, warm-loads revision 1, then persists a
    # newer live mapping the old process never learns about directly.
    new_process_cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    new_process_cache.apply_observation(_obs(revision=5, live=True))

    # The old process now receives a genuinely-newer-to-IT push (revision 2
    # > its own last-known revision 1) that happens to be a removal --
    # without reconciliation this would incorrectly regress old_process's
    # own in-memory view to "not live", even though revision 5 (live) is
    # already the true current state on disk.
    result = old_process_cache.apply_observation(_obs(revision=2, live=False))

    assert result == {"applied": False, "reason": "stale_revision", "current_revision": 5}
    # The critical assertion: old_process_cache's OWN in-memory view must
    # now correctly reflect the newer (live) state, not the rejected push.
    assert old_process_cache.live_session_names() == {"wt-1"}
    assert old_process_cache.get("proj", "wt-1")["mapping_revision"] == 5
    assert old_process_cache.get("proj", "wt-1")["live"] is True


def test_interprocess_lock_serializes_concurrent_apply_observation_across_instances(tmp_path):
    """Copilot review finding: the read-merge-write in `_persist_locked`
    is not itself atomic across processes -- `os.replace` only makes the
    final *write* atomic. Simulating the documented handoff race with two
    real threads each driving their own independent `ManagedMuxCache`
    instance (standing in for two separate processes) sharing the same
    `persist_path`, both racing to apply an observation for the SAME key at
    increasing revisions, must never lose an update: the final on-disk
    state must reflect the highest revision either instance ever applied,
    and every individual `apply_observation` call must genuinely run
    (never silently skipped)."""
    persist_path = tmp_path / "managed-mux-cache.json"
    applied_revisions = []
    apply_lock = threading.Lock()  # protects the shared `applied_revisions` list only

    def _worker(cache, start_revision, count):
        for i in range(count):
            revision = start_revision + i
            result = cache.apply_observation(_obs(revision=revision))
            with apply_lock:
                applied_revisions.append((revision, result))

    cache_a = mux_link.ManagedMuxCache(persist_path=persist_path)
    cache_b = mux_link.ManagedMuxCache(persist_path=persist_path)

    t1 = threading.Thread(target=_worker, args=(cache_a, 1, 20))
    t2 = threading.Thread(target=_worker, args=(cache_b, 21, 20))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(applied_revisions) == 40
    highest_applied = max(rev for rev, result in applied_revisions if result["applied"])

    reloaded = mux_link.ManagedMuxCache(persist_path=persist_path)
    on_disk = reloaded.get("proj", "wt-1")
    assert on_disk is not None
    # The file must reflect the highest revision that was ever genuinely
    # applied by either "process" -- never regressed by a racing writer.
    assert on_disk["mapping_revision"] == highest_applied


def test_lock_file_helpers_actually_exclude_a_second_acquirer(tmp_path):
    """Direct unit coverage of the platform lock primitives themselves:
    while one file handle holds the lock, a second concurrent attempt on
    the same byte range from another handle must genuinely block until
    the first releases -- not silently proceed as if unlocked."""
    lock_path = tmp_path / "probe.lock"
    fh1 = open(lock_path, "a+b")
    fh2 = open(lock_path, "r+b")
    try:
        mux_link._lock_file(fh1)
        acquired_second = threading.Event()

        def _acquire_second():
            mux_link._lock_file(fh2)  # must block until fh1 releases
            acquired_second.set()
            mux_link._unlock_file(fh2)

        t = threading.Thread(target=_acquire_second, daemon=True)
        t.start()
        # Give the second attempt a real chance to (incorrectly) succeed
        # while fh1 still holds the lock.
        assert not acquired_second.wait(timeout=0.5), (
            "second handle acquired the lock while the first still held it"
        )
        mux_link._unlock_file(fh1)
        assert acquired_second.wait(timeout=15), (
            "second handle never acquired the lock after the first released it"
        )
        t.join(timeout=2)
    finally:
        fh1.close()
        fh2.close()


def test_cache_without_persist_path_does_not_survive_a_restart():
    first = mux_link.ManagedMuxCache()
    first.apply_observation(_obs())
    second = mux_link.ManagedMuxCache()
    assert second.get("proj", "wt-1") is None


def test_warm_load_ignores_a_corrupt_or_malformed_snapshot_file(tmp_path):
    persist_path = tmp_path / "managed-mux-cache.json"
    persist_path.write_text("not json at all {{{", encoding="utf-8")
    cache = mux_link.ManagedMuxCache(persist_path=persist_path)  # must not raise
    assert cache.snapshot() == {}

    persist_path.write_text(
        '[{"worktree_id": "wt-1"}]', encoding="utf-8"
    )  # missing required fields
    cache2 = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert cache2.snapshot() == {}

    persist_path.write_text(
        '{"wt-1": {"worktree_id": "wt-1"}}', encoding="utf-8"
    )  # old dict-shaped format is no longer valid -- must not raise, just ignored
    cache3 = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert cache3.snapshot() == {}


def test_warm_load_rejects_a_non_finite_received_at_and_falls_back_to_now(tmp_path):
    """Copilot review finding: ``json.loads`` permits non-finite numbers
    (``Infinity``/``-Infinity``/``NaN``), and a plain ``isinstance(x, (int,
    float))`` check accepts them too -- an ``Infinity`` ``received_at``
    would make ``now - received_at <= MAPPING_STALE_AFTER_SECONDS`` true
    forever, letting a corrupt/tampered snapshot defeat the stale-mapping
    safeguard permanently."""
    persist_path = tmp_path / "managed-mux-cache.json"
    persist_path.write_text(
        json.dumps(
            [
                {
                    "project": "proj",
                    "worktree_id": "wt-1",
                    "mux_session": "wt-1",
                    "mapping_revision": 1,
                    "live": True,
                    "panes": [],
                    "session_incarnation": "",
                    "attached_clients": 0,
                    "observed_at": "2026-09-17T08:00:00Z",
                    "received_at": float("inf"),
                }
            ]
        ),
        encoding="utf-8",
    )
    cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    entry = cache.get("proj", "wt-1")
    assert entry is not None
    assert entry["received_at"] != float("inf")
    assert abs(entry["received_at"] - time.time()) < 5  # fell back to real "now"


def test_warm_load_rejects_an_out_of_float_range_received_at_and_falls_back_to_now(tmp_path):
    """Copilot review finding: a syntactically valid but out-of-float-range
    JSON integer (e.g. ``10**1000``) reaches ``math.isfinite`` and raises
    ``OverflowError`` -- ``_warm_load``'s own per-entry ``try/except``
    previously caught only ``ValueError``, so this would abort
    ``ManagedMuxCache.__init__`` entirely (not just skip the one bad entry),
    which ``InProcessRuntime.start()``'s broad ``except Exception`` then
    turns into silently disabling this whole endpoint."""
    huge_int = 10**1000
    persist_path = tmp_path / "managed-mux-cache.json"
    persist_path.write_text(
        json.dumps(
            [
                {
                    "project": "proj",
                    "worktree_id": "wt-1",
                    "mux_session": "wt-1",
                    "mapping_revision": 1,
                    "live": True,
                    "panes": [],
                    "session_incarnation": "",
                    "attached_clients": 0,
                    "observed_at": "2026-09-17T08:00:00Z",
                    "received_at": huge_int,
                }
            ]
        ),
        encoding="utf-8",
    )
    cache = mux_link.ManagedMuxCache(persist_path=persist_path)  # must not raise
    entry = cache.get("proj", "wt-1")
    assert entry is not None
    assert entry["received_at"] != huge_int
    assert abs(entry["received_at"] - time.time()) < 5  # fell back to real "now"


def test_warm_load_keys_entries_by_their_own_project_and_worktree_id(tmp_path):
    """Copilot review finding: the persisted snapshot is a JSON *array* of
    self-describing entries (never a JSON object keyed by a separately-
    trusted string) -- the storage key is always re-derived from each
    entry's own validated ``project``/``worktree_id`` fields, structurally
    eliminating the earlier key-vs-field-mismatch bug class entirely (there
    is no longer a separate outer key that could ever disagree). Also
    proves two different projects' entries sharing the identical
    ``worktree_id`` survive a restart independently, never colliding."""
    persist_path = tmp_path / "managed-mux-cache.json"
    first = mux_link.ManagedMuxCache(persist_path=persist_path)
    first.apply_observation(_obs(project="proj-a", worktree_id="wt-1", revision=1))
    first.apply_observation(_obs(project="proj-b", worktree_id="wt-1", revision=1))

    second = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert second.get("proj-a", "wt-1") is not None
    assert second.get("proj-b", "wt-1") is not None
    assert second.get("proj-a", "wt-1")["project"] == "proj-a"
    assert second.get("proj-b", "wt-1")["project"] == "proj-b"


def test_apply_observation_still_rejects_stale_revision_after_restart(tmp_path):
    persist_path = tmp_path / "managed-mux-cache.json"
    first = mux_link.ManagedMuxCache(persist_path=persist_path)
    first.apply_observation(_obs(revision=5))

    second = mux_link.ManagedMuxCache(persist_path=persist_path)
    result = second.apply_observation(_obs(revision=3, live=False))
    assert result == {"applied": False, "reason": "stale_revision", "current_revision": 5}


def test_warm_loaded_entry_carries_forward_its_original_received_at(tmp_path, monkeypatch):
    """A restart must not reset the freshness clock -- a genuinely stale
    mapping (per receipt time) must stay stale across a restart instead of
    looking artificially fresh again."""
    persist_path = tmp_path / "managed-mux-cache.json"
    first = mux_link.ManagedMuxCache(persist_path=persist_path)
    first.apply_observation(_obs())
    assert first.live_session_names() == {"wt-1"}

    monkeypatch.setattr(mux_link, "MAPPING_STALE_AFTER_SECONDS", 0.0)
    time.sleep(0.01)
    assert first.live_session_names() == set()  # now stale on the original instance

    second = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert second.live_session_names() == set()  # still stale after "restart"


# -- _push_key (coalescing-key safety) --------------------------------------


def test_push_key_differs_for_different_revisions_of_the_same_worktree():
    """Copilot review finding: ``CoalescingServer`` coalesces concurrent
    requests sharing one ``(kind, key)`` onto a single execution -- a key
    that stayed constant across different observations for the same
    worktree (e.g. a bare ``worktree_id``) could let two concurrent pushes
    carrying two different ``mapping_revision``s coalesce onto one
    execution, silently dropping the second real observation while its
    caller still received the first's response as if its own had
    succeeded."""
    key1 = mux_link._push_key(_obs(revision=1))
    key2 = mux_link._push_key(_obs(revision=2))
    assert key1 != key2


def test_push_key_is_identical_for_a_genuine_retry_of_the_same_revision():
    key1 = mux_link._push_key(_obs(revision=7))
    key2 = mux_link._push_key(_obs(revision=7))
    assert key1 == key2


def test_push_key_differs_across_projects_for_the_same_worktree_id_and_revision():
    """Copilot review finding: ``worktree_id`` alone is not guaranteed
    unique across two different projects' concurrent pushes at the same
    revision number -- omitting ``project`` from the key could let those
    two unrelated pushes coalesce, returning one project's payload for the
    other."""
    key_a = mux_link._push_key(_obs(project="proj-a", worktree_id="wt-1", revision=1))
    key_b = mux_link._push_key(_obs(project="proj-b", worktree_id="wt-1", revision=1))
    assert key_a != key_b


def test_push_key_is_injective_despite_a_colon_in_project_or_worktree_id():
    # Mirrors worktree_status_daemon.coalescing_key's own injective-key test:
    # neither `project`/`worktree_id` nor the literal separator is
    # restricted against `:`, so a naive join could collide.
    key_a = mux_link._push_key({"project": "p", "worktree_id": "a", "mapping_revision": 23})
    key_b = mux_link._push_key({"project": "p", "worktree_id": "a:2", "mapping_revision": 3})
    key_c = mux_link._push_key({"project": "p:2", "worktree_id": "a", "mapping_revision": 3})
    assert len({key_a, key_b, key_c}) == 3


def test_push_key_requires_project_worktree_id_and_integer_mapping_revision():
    for bad in (
        {"worktree_id": "wt-1", "mapping_revision": 1},
        {"project": "", "worktree_id": "wt-1", "mapping_revision": 1},
        {"project": "proj", "mapping_revision": 1},
        {"project": "proj", "worktree_id": "", "mapping_revision": 1},
        {"project": "proj", "worktree_id": "wt-1"},
        {"project": "proj", "worktree_id": "wt-1", "mapping_revision": "1"},
        {"project": "proj", "worktree_id": "wt-1", "mapping_revision": True},
    ):
        try:
            mux_link._push_key(bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, bad


def test_mux_live_via_daemon_concurrent_different_revisions_never_coalesce():
    """End-to-end proof for the coalescing-key finding: two concurrent
    pushes for the same worktree at two different revisions must **both**
    actually reach ``apply_observation`` -- never silently coalesced so
    only one of them ever runs. (Revision 1's own outcome legitimately
    depends on real scheduling order against revision 2's unblocked push --
    it may either apply before revision 2's or be correctly rejected as
    stale by the monotonic-revision guard once revision 2 already landed.
    What must never happen is revision 1 being silently dropped *without*
    `apply_observation` ever running for it -- that's what a shared
    coalescing key would cause.)"""
    cache = mux_link.ManagedMuxCache()
    gate = threading.Event()
    entered_first = threading.Event()
    invocations = []

    real_apply = cache.apply_observation

    def _gated_apply(payload):
        invocations.append(payload["mapping_revision"])
        if payload["mapping_revision"] == 1:
            entered_first.set()
            gate.wait(timeout=2)
        return real_apply(payload)

    cache.apply_observation = _gated_apply
    server = mux_link.start_server(mux_link.build_compute(cache))
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        results = {}

        def _push(revision):
            results[revision] = mux_link.mux_live_via_daemon(
                lock_data,
                payload=_obs(revision=revision),
                fallback=lambda: {"from": "fallback"},
                request_deadline_s=3.0,
            )

        t1 = threading.Thread(target=_push, args=(1,))
        t1.start()
        assert entered_first.wait(timeout=2)
        t2 = threading.Thread(target=_push, args=(2,))
        t2.start()
        t2.join(timeout=3)
        gate.set()
        t1.join(timeout=3)

        # The whole point: apply_observation actually ran for *both*
        # revisions -- a shared coalescing key would have joined revision
        # 1's caller onto revision 2's single execution, leaving this list
        # with only one entry.
        assert sorted(invocations) == [1, 2]
        assert results[2] == {"applied": True, "revision": 2}
        assert results[1] in (
            {"applied": True, "revision": 1},
            {"applied": False, "reason": "stale_revision", "current_revision": 2},
        )
        # The cache's actual final state reflects the later revision -- both
        # observations were genuinely applied, neither silently dropped.
        assert cache.get("proj", "wt-1")["mapping_revision"] == 2
    finally:
        server.close()


# -- compute / wire wrappers ---------------------------------------------


def test_build_compute_rejects_unexpected_kind():
    cache = mux_link.ManagedMuxCache()
    compute = mux_link.build_compute(cache)
    try:
        compute("something_else", _obs())
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_build_compute_applies_through_to_the_cache():
    cache = mux_link.ManagedMuxCache()
    compute = mux_link.build_compute(cache)
    result = compute(mux_link.KIND, _obs())
    assert result == {"applied": True, "revision": 1}
    assert cache.get("proj", "wt-1") is not None


def test_kind_matches_the_documented_mux_live_v1_contract():
    assert mux_link.KIND == "mux-live-v1"


def test_rendezvous_fields_are_namespaced_and_parseable():
    server = mux_link.start_server(mux_link.build_compute(mux_link.ManagedMuxCache()))
    server.start()
    try:
        fields = _endpoint_dict(server)
        assert set(fields) == {
            "managed_mux_transport",
            "managed_mux_endpoint",
            "managed_mux_token",
            "managed_mux_generation",
        }
        endpoint = mux_link.endpoint_from_rendezvous(fields)
        assert endpoint is not None
        host, port, token = endpoint
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
        assert token == fields["managed_mux_token"]
    finally:
        server.close()


def test_endpoint_from_rendezvous_rejects_malformed_or_absent_data():
    assert mux_link.endpoint_from_rendezvous(None) is None
    assert mux_link.endpoint_from_rendezvous({}) is None
    assert mux_link.endpoint_from_rendezvous({"managed_mux_endpoint": "bad"}) is None
    assert (
        mux_link.endpoint_from_rendezvous(
            {"managed_mux_endpoint": "127.0.0.1:9", "managed_mux_token": ""}
        )
        is None
    )


def test_mux_live_via_daemon_uses_fallback_when_no_lock_data():
    calls = {"fallback": 0}

    def fallback():
        calls["fallback"] += 1
        return {"from": "fallback"}

    result = mux_link.mux_live_via_daemon(None, payload=_obs(), fallback=fallback)
    assert result == {"from": "fallback"}
    assert calls["fallback"] == 1


def test_mux_live_via_daemon_pushes_to_a_live_daemon():
    cache = mux_link.ManagedMuxCache()
    server = mux_link.start_server(mux_link.build_compute(cache))
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        result = mux_link.mux_live_via_daemon(
            lock_data,
            payload=_obs(),
            fallback=lambda: {"from": "fallback"},
        )
        assert result == {"applied": True, "revision": 1}
        assert cache.get("proj", "wt-1") is not None
    finally:
        server.close()


def test_mux_live_via_daemon_registers_and_releases_a_client_id():
    observed_counts = []

    def _compute(kind, payload):
        observed_counts.append(server.subscriber_count())
        return {"applied": True, "revision": 1}

    server = mux_link.start_server(_compute)
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        mux_link.mux_live_via_daemon(
            lock_data, payload=_obs(), fallback=lambda: {"from": "fallback"}
        )
        assert observed_counts == [1]
        assert server.subscriber_count() == 0
    finally:
        server.close()


def test_mux_live_with_boot_uses_default_request_deadline(monkeypatch):
    expected_deadline = mux_link.REQUEST_DEADLINE_S
    observed = {}

    def request(*args, **kwargs):
        observed["request_deadline_s"] = kwargs["request_deadline_s"]
        return {"applied": True, "revision": 1}

    def release(*args, **kwargs):
        observed["release_timeout"] = kwargs["timeout"]

    monkeypatch.setattr(mux_link.wcs_client, "new_client_id", lambda: "client")
    monkeypatch.setattr(mux_link.wcs_client, "request", request)
    monkeypatch.setattr(mux_link.wcs_client, "release", release)

    result = mux_link.mux_live_with_boot(
        read_lock_data=lambda: {
            "managed_mux_endpoint": "127.0.0.1:1234",
            "managed_mux_token": "token",
        },
        ensure_monitor=None,
        payload=_obs(),
        fallback=lambda: {"from": "fallback"},
    )

    assert result == {"applied": True, "revision": 1}
    assert observed == {
        "request_deadline_s": expected_deadline,
        "release_timeout": expected_deadline,
    }


def test_mux_live_with_boot_falls_back_when_no_daemon_ever_appears(monkeypatch):
    monkeypatch.setattr(mux_link, "BOOT_WAIT_S", 0.05)
    calls = {"ensure": 0, "fallback": 0}

    def ensure_monitor():
        calls["ensure"] += 1
        return True

    result = mux_link.mux_live_with_boot(
        read_lock_data=lambda: None,
        ensure_monitor=ensure_monitor,
        payload=_obs(),
        fallback=lambda: calls.__setitem__("fallback", calls["fallback"] + 1)
        or {"from": "fallback"},
        boot_wait_s=0.05,
        poll_interval_s=0.01,
    )
    assert result == {"from": "fallback"}
    assert calls["ensure"] == 1
    assert calls["fallback"] == 1


# -- InProcessRuntime -----------------------------------------------------


def test_in_process_runtime_starts_and_shuts_down_cleanly():
    runtime = mux_link.InProcessRuntime()
    runtime.start()
    try:
        assert runtime.server is not None
        assert runtime.cache is not None
        assert set(runtime.lock_extra()) == {
            "managed_mux_transport",
            "managed_mux_endpoint",
            "managed_mux_token",
            "managed_mux_generation",
        }
    finally:
        runtime.shutdown()
    assert runtime.server is None
    assert runtime.cache is None
    assert runtime.lock_extra() == {}


def test_in_process_runtime_shutdown_closes_the_cache_not_just_the_server():
    """Copilot review finding: shutdown() must close the cache itself (not
    just the server) so a late, already-dispatched handler thread cannot
    still write through it after this runtime has shut down."""
    runtime = mux_link.InProcessRuntime()
    runtime.start()
    cache = runtime.cache
    runtime.shutdown()
    result = cache.apply_observation(_obs())
    assert result == {"applied": False, "reason": "closed"}


def test_in_process_runtime_live_session_names_empty_until_something_pushes():
    runtime = mux_link.InProcessRuntime()
    runtime.start()
    try:
        assert runtime.live_session_names() == set()
        runtime.cache.apply_observation(_obs())
        assert runtime.live_session_names() == {"wt-1"}
    finally:
        runtime.shutdown()


def test_in_process_runtime_has_active_demand_counts_a_live_mapping_or_subscriber():
    runtime = mux_link.InProcessRuntime()
    runtime.start()
    try:
        assert runtime.has_active_demand() is False
        runtime.cache.apply_observation(_obs(live=True))
        assert runtime.has_active_demand() is True
        runtime.cache.apply_observation(_obs(revision=2, live=False))
        assert runtime.has_active_demand() is False

        client_id = "probe-client"
        runtime.server.subscribe(client_id)
        try:
            assert runtime.has_active_demand() is True
        finally:
            runtime.server.release(client_id)
        assert runtime.has_active_demand() is False
    finally:
        runtime.shutdown()


def test_in_process_runtime_before_start_reports_empty_and_inactive():
    runtime = mux_link.InProcessRuntime()
    assert runtime.lock_extra() == {}
    assert runtime.live_session_names() == set()
    assert runtime.has_active_demand() is False
    runtime.shutdown()  # never started; must not raise


def test_in_process_runtime_persists_and_reloads_across_a_restart(tmp_path):
    persist_path = tmp_path / "managed-mux-cache.json"
    first = mux_link.InProcessRuntime()
    first.start(persist_path)
    try:
        first.cache.apply_observation(_obs())
    finally:
        first.shutdown()

    second = mux_link.InProcessRuntime()
    second.start(persist_path)
    try:
        assert second.live_session_names() == {"wt-1"}
    finally:
        second.shutdown()


def test_in_process_runtime_start_closes_the_partially_started_server_on_failure(monkeypatch):
    """Copilot review finding: ``CoalescingServer.__init__`` already
    binds+listens its loopback socket, so a failure in ``server.start()``
    (thread spawn) after construction previously only cleared the
    reference -- leaving an unadvertised, still-bound TCP server running.
    ``start()`` must close it through the same path a normal shutdown uses."""
    real_start = mux_link.CoalescingServer.start
    started_servers = []

    def _boom(self):
        started_servers.append(self)
        raise RuntimeError("thread spawn failed")

    monkeypatch.setattr(mux_link.CoalescingServer, "start", _boom)

    runtime = mux_link.InProcessRuntime()
    runtime.start()

    assert runtime.server is None
    assert runtime.cache is None
    assert len(started_servers) == 1
    # The server that failed to start must have been closed, not leaked --
    # closing an un-started CoalescingServer must itself not raise/hang (see
    # `CoalescingServer.close`'s own `self._started` guard).
    monkeypatch.setattr(mux_link.CoalescingServer, "start", real_start)
    # A second, real start() must still work cleanly afterward.
    runtime.start()
    try:
        assert runtime.server is not None
    finally:
        runtime.shutdown()
