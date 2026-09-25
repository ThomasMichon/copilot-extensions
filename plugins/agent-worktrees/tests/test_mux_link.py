"""Tests for ``mux_link`` -- the resident manager-observation IPC seam
(Phase 3b Slice 2 Sub-slice 3 Step 1, ``worktree-manager-control-plane``
effort). Mirrors ``test_worktree_status_daemon.py``'s style."""

from __future__ import annotations

from agent_worktrees import mux_link


def _endpoint_dict(server) -> dict:
    return mux_link.rendezvous_fields(server)


def _obs(worktree_id="wt-1", session="wt-1", revision=1, live=True, **extra) -> dict:
    payload = {
        "worktree_id": worktree_id,
        "session": session,
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
    assert entry["session"] == "wt-1"
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


def test_apply_observation_requires_worktree_id_and_session():
    cache = mux_link.ManagedMuxCache()
    for bad in (
        {"session": "wt-1", "mapping_revision": 1},
        {"worktree_id": "wt-1", "mapping_revision": 1},
        {"worktree_id": "", "session": "wt-1", "mapping_revision": 1},
        {"worktree_id": "wt-1", "session": 5, "mapping_revision": 1},
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
            "worktree_id": "wt-2",
            "session": "wt-2",
            "mapping_revision": 1,
            "panes": "not-a-list",
            "incarnation": 5,
            "attached_clients": "two",
        }
    )
    entry = cache.get("wt-2")
    assert entry["live"] is True
    assert entry["panes"] == []
    assert entry["incarnation"] == ""
    assert entry["attached_clients"] == 0


def test_live_session_names_only_includes_currently_live_entries():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs(worktree_id="wt-a", session="wt-a", revision=1, live=True))
    cache.apply_observation(_obs(worktree_id="wt-b", session="wt-b", revision=1, live=False))
    assert cache.live_session_names() == {"wt-a"}


def test_snapshot_is_a_defensive_copy():
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(_obs())
    snap = cache.snapshot()
    snap["wt-1"]["live"] = False
    assert cache.get("wt-1")["live"] is True  # mutation of the copy didn't leak back


def test_has_any_live_reflects_current_state():
    cache = mux_link.ManagedMuxCache()
    assert cache.has_any_live() is False
    cache.apply_observation(_obs(live=True))
    assert cache.has_any_live() is True
    cache.apply_observation(_obs(revision=2, live=False))
    assert cache.has_any_live() is False


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
    assert (
        mux_link.endpoint_from_rendezvous({"managed_mux_endpoint": "bad"}) is None
    )
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

    result = mux_link.mux_live_via_daemon(
        None, key="wt-1", payload=_obs(), fallback=fallback
    )
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
