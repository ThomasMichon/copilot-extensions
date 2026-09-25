"""Tests for ``mux_link`` -- the resident manager-observation IPC seam
(Phase 3b Slice 2 Sub-slice 3 Step 1, ``worktree-manager-control-plane``
effort). Mirrors ``test_worktree_status_daemon.py``'s style. Payload shapes
match the documented ``mux-live-v1`` contract in
``efforts/active/worktree-manager-control-plane/phase-3b-substatus-monitor-relocation.md``."""

from __future__ import annotations

import json
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
    entry = cache.get("wt-1")
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
    assert cache.get("wt-1")["live"] is False


def test_apply_observation_rejects_a_stale_revision():
    """Copilot review contract: an older ``live: false``/stale-pane event must
    never clobber a newer live mapping that already arrived."""
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(revision=5, live=True))
    result = cache.apply_observation(_obs(revision=3, live=False))
    assert result == {"applied": False, "reason": "stale_revision", "current_revision": 5}
    assert cache.get("wt-1")["live"] is True  # unchanged


def test_apply_observation_accepts_a_replayed_equal_revision():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(revision=4))
    result = cache.apply_observation(_obs(revision=4, live=False))
    assert result == {"applied": True, "revision": 4}
    assert cache.get("wt-1")["live"] is False


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
    entry = cache.get("wt-2")
    assert entry["live"] is True
    assert entry["panes"] == []
    assert entry["session_incarnation"] == ""
    assert entry["attached_clients"] == 0
    assert isinstance(entry["observed_at"], str) and entry["observed_at"]


def test_apply_observation_preserves_a_caller_supplied_observed_at():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(observed_at="2026-09-17T08:00:00Z"))
    assert cache.get("wt-1")["observed_at"] == "2026-09-17T08:00:00Z"


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
    entry = cache.get("wt-1")
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
    snap["wt-1"]["live"] = False
    assert cache.get("wt-1")["live"] is True  # mutation of the copy didn't leak back


def test_get_and_snapshot_deep_copy_the_panes_list():
    """Copilot review finding: ``dict(entry)`` alone leaves the stored
    ``panes`` list (and each pane dict within it) shared -- a caller
    mutating a returned list/dict would silently corrupt the live cache
    without holding its lock."""
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(panes=[{"pane_id": "%1", "role": "head", "live": True}]))

    got = cache.get("wt-1")
    got["panes"].append({"pane_id": "INJECTED", "role": "", "live": True})
    got["panes"][0]["role"] = "TAMPERED"
    fresh = cache.get("wt-1")
    assert len(fresh["panes"]) == 1
    assert fresh["panes"][0]["role"] == "head"

    snap = cache.snapshot()
    snap["wt-1"]["panes"][0]["live"] = False
    assert cache.get("wt-1")["panes"][0]["live"] is True


def test_has_any_live_reflects_current_state():
    cache = mux_link.ManagedMuxCache()
    assert cache.has_any_live() is False
    cache.apply_observation(_obs(live=True))
    assert cache.has_any_live() is True
    cache.apply_observation(_obs(revision=2, live=False))
    assert cache.has_any_live() is False


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
    assert cache.get("wt-1")["live"] is True  # raw record still reports its true bit


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
    entry = cache.get("wt-1")
    assert entry["received_at"] != far_future
    assert abs(entry["received_at"] - time.time()) < 5  # stamped with real receipt time

    far_past = time.time() - 10_000_000
    cache.apply_observation(_obs(revision=2, received_at=far_past))
    entry2 = cache.get("wt-1")
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
    entry = second.get("wt-1")
    assert entry is not None
    assert entry["mux_session"] == "wt-1"
    assert entry["panes"] == [{"pane_id": "%1", "role": "head", "live": True}]
    assert entry["attached_clients"] == 2
    assert second.live_session_names() == {"wt-1"}


def test_cache_without_persist_path_does_not_survive_a_restart():
    first = mux_link.ManagedMuxCache()
    first.apply_observation(_obs())
    second = mux_link.ManagedMuxCache()
    assert second.get("wt-1") is None


def test_warm_load_ignores_a_corrupt_or_malformed_snapshot_file(tmp_path):
    persist_path = tmp_path / "managed-mux-cache.json"
    persist_path.write_text("not json at all {{{", encoding="utf-8")
    cache = mux_link.ManagedMuxCache(persist_path=persist_path)  # must not raise
    assert cache.snapshot() == {}

    persist_path.write_text(
        '{"wt-1": {"worktree_id": "wt-1"}}', encoding="utf-8"
    )  # missing required fields
    cache2 = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert cache2.snapshot() == {}


def test_warm_load_rejects_an_entry_whose_key_disagrees_with_its_worktree_id(tmp_path):
    """Copilot review finding: accepting a snapshot entry under the wrong
    outer key would let a later legitimate update for the entry's *actual*
    worktree_id bypass this stale record's revision guard entirely, since
    apply_observation only ever looks up self._entries[worktree_id]."""
    persist_path = tmp_path / "managed-mux-cache.json"
    persist_path.write_text(
        json.dumps(
            {
                "wt-a": {
                    "project": "proj",
                    "worktree_id": "wt-b",  # mismatched key vs. field
                    "mux_session": "wt-b",
                    "mapping_revision": 99,
                    "live": True,
                    "panes": [],
                    "session_incarnation": "",
                    "attached_clients": 0,
                    "observed_at": "2026-09-17T08:00:00Z",
                    "received_at": time.time(),
                }
            }
        ),
        encoding="utf-8",
    )
    cache = mux_link.ManagedMuxCache(persist_path=persist_path)
    assert cache.snapshot() == {}  # rejected entirely, neither key nor field trusted
    # A legitimate wt-b observation must not be blocked by the rejected entry.
    result = cache.apply_observation(
        _obs(worktree_id="wt-b", mux_session="wt-b", revision=1)
    )
    assert result == {"applied": True, "revision": 1}


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
    assert cache.get("wt-1") is not None


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

    result = mux_link.mux_live_via_daemon(None, key="wt-1", payload=_obs(), fallback=fallback)
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
            key="wt-1",
            payload=_obs(),
            fallback=lambda: {"from": "fallback"},
        )
        assert result == {"applied": True, "revision": 1}
        assert cache.get("wt-1") is not None
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
            lock_data, key="wt-1", payload=_obs(), fallback=lambda: {"from": "fallback"}
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
        key="wt-1",
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
        key="wt-1",
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
