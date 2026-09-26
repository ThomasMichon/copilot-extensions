"""Tests for the Worktree Manager mux-companion daemon (Phase 3b Sub-slice 3
Step 2). Covers the mapping registry's persistence/monotonicity, the
``mux-status-v1`` compute handler, rendezvous parsing, and the resident
daemon's own idle-exit lifecycle -- all still off any real launch path per
this step's own scope (see ``mux_daemon.py``'s module docstring)."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest
from work_coalescing_singleton import client as wcs_client

from worktree_manager import mux_daemon


def _entry(**overrides) -> dict:
    base = {
        "project": "proj",
        "worktree_id": "wt-1",
        "worktree_path": "D:\\Src\\proj.worktrees\\wt-1",
        "mux_session": "wt-1",
        "mux_bin": "psmux",
        "session_incarnation": "sess:1",
        "panes": [{"pane_id": "%1", "role": "head", "live": True}],
        "attached_clients": 1,
        "live": True,
        "mapping_revision": 1,
        "observed_at": "2026-09-25T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _normalize_mapping_entry
# ---------------------------------------------------------------------------


def test_normalize_mapping_entry_requires_core_fields():
    for field in ("project", "worktree_id", "mux_session", "mux_bin"):
        payload = _entry()
        del payload[field]
        with pytest.raises(ValueError, match=field):
            mux_daemon._normalize_mapping_entry(payload)


def test_normalize_mapping_entry_rejects_bad_revision():
    with pytest.raises(ValueError):
        mux_daemon._normalize_mapping_entry(_entry(mapping_revision=-1))
    with pytest.raises(ValueError):
        mux_daemon._normalize_mapping_entry(_entry(mapping_revision="1"))
    with pytest.raises(ValueError):
        mux_daemon._normalize_mapping_entry(_entry(mapping_revision=True))


def test_normalize_mapping_entry_defaults_and_pane_sanitization():
    payload = _entry(panes=[{"pane_id": "%1"}, {"not": "a pane"}, "garbage"])
    del payload["attached_clients"]
    del payload["session_incarnation"]
    del payload["observed_at"]
    entry = mux_daemon._normalize_mapping_entry(payload)
    assert entry["attached_clients"] == 0
    assert entry["session_incarnation"] == ""
    assert entry["observed_at"]
    assert entry["panes"] == [{"pane_id": "%1", "role": "", "live": True}]


# ---------------------------------------------------------------------------
# MuxMappingRegistry
# ---------------------------------------------------------------------------


def test_registry_register_get_snapshot_roundtrip(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    result = registry.register(_entry())
    assert result == {"applied": True, "revision": 1}
    entry = registry.get("proj", "wt-1")
    assert entry["mux_session"] == "wt-1"
    snap = registry.snapshot()
    assert set(snap) == {("proj", "wt-1")}


def test_registry_rejects_stale_revision(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=5))
    result = registry.register(_entry(mapping_revision=3))
    assert result["applied"] is False
    assert result["reason"] == "stale_revision"
    assert result["current_revision"] == 5
    # the newer entry must still be the one on file
    assert registry.get("proj", "wt-1")["mapping_revision"] == 5


def test_registry_survives_a_simulated_daemon_restart(tmp_path):
    path = tmp_path / "mux-mapping.json"
    first = mux_daemon.MuxMappingRegistry(path)
    first.register(_entry(mapping_revision=2))
    second = mux_daemon.MuxMappingRegistry(path)
    assert second.get("proj", "wt-1")["mapping_revision"] == 2


def test_registry_remove_tombstones_entry(tmp_path):
    """Copilot review finding: remove() must persist a tombstone
    (``live: false``), not delete the entry outright -- deleting it would
    lose the monotonic-revision high-water mark, letting a delayed
    out-of-order register at a lower revision resurrect a removed mapping.
    An unversioned remove (no explicit ``mapping_revision``) also bumps the
    stored revision past the entry's own -- otherwise register()'s ``<``-
    only guard would still accept a delayed update at the SAME (unbumped)
    revision (a separate Copilot review finding)."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=5))
    result = registry.remove("proj", "wt-1")
    assert result == {"applied": True}
    entry = registry.get("proj", "wt-1")
    assert entry is not None
    assert entry["live"] is False
    assert entry["mapping_revision"] == 6


def test_registry_remove_prevents_stale_resurrection(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=5))
    registry.remove("proj", "wt-1", mapping_revision=6)
    # A delayed, out-of-order register at the ORIGINAL (now-stale) revision
    # must not resurrect the removed mapping.
    result = registry.register(_entry(mapping_revision=5))
    assert result["applied"] is False
    assert result["reason"] == "stale_revision"
    assert registry.get("proj", "wt-1")["live"] is False


def test_registry_remove_is_idempotent_when_absent(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    result = registry.remove("proj", "never-registered")
    assert result == {"applied": True, "reason": "absent"}


def test_registry_revisioned_remove_without_entry_persists_a_durable_tombstone(tmp_path):
    """Copilot review finding: a revisioned remove for a key with NO
    current entry (e.g. remove(6) racing ahead of an eventual register(5))
    must still persist a fencing tombstone -- otherwise the later register
    finds nothing to reject against and resurrects the mapping."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    result = registry.remove("proj", "never-registered", mapping_revision=6)
    assert result == {"applied": True}
    tombstone = registry.get("proj", "never-registered")
    assert tombstone is not None
    assert tombstone["live"] is False
    assert tombstone["mapping_revision"] == 6

    # a delayed register at the now-stale revision 5 must be rejected
    late = registry.register(
        _entry(project="proj", worktree_id="never-registered", mapping_revision=5)
    )
    assert late["applied"] is False
    assert late["reason"] == "stale_revision"


def test_registry_unversioned_remove_prevents_equal_revision_resurrection(tmp_path):
    """Copilot review finding: register()'s guard only rejects a revision
    strictly LESS than current, so an unversioned remove (the CLI's own
    default) that left the revision unchanged would still accept a delayed
    ``live: true`` update at that SAME revision, resurrecting the mapping."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=5))
    registry.remove("proj", "wt-1")  # unversioned -- no mapping_revision given
    late = registry.register(_entry(mapping_revision=5))
    assert late["applied"] is False
    assert late["reason"] == "stale_revision"
    assert registry.get("proj", "wt-1")["live"] is False


def test_registry_remove_respects_revision_guard(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=5))
    result = registry.remove("proj", "wt-1", mapping_revision=3)
    assert result["applied"] is False
    assert result["reason"] == "stale_revision"
    assert registry.get("proj", "wt-1") is not None


def test_registry_ignores_a_corrupt_snapshot_file(tmp_path):
    path = tmp_path / "mux-mapping.json"
    path.write_text("not json at all {{{", encoding="utf-8")
    registry = mux_daemon.MuxMappingRegistry(path)
    assert registry.snapshot() == {}  # must not raise


def test_registry_has_any_live(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    assert registry.has_any_live() is False
    registry.register(_entry())
    assert registry.has_any_live() is True
    registry.register(_entry(mapping_revision=2, live=False))
    assert registry.has_any_live() is False


def test_registry_duplicate_snapshot_entries_keep_highest_revision(tmp_path):
    """A hand-corrupted/concurrently-written snapshot could carry two
    records for the same (project, worktree_id) -- mirrors the equivalent
    ``mux_link`` regression test."""
    path = tmp_path / "mux-mapping.json"
    entries = [_entry(mapping_revision=1), _entry(mapping_revision=9)]
    import json

    path.write_text(json.dumps(entries), encoding="utf-8")
    registry = mux_daemon.MuxMappingRegistry(path)
    assert registry.get("proj", "wt-1")["mapping_revision"] == 9


# ---------------------------------------------------------------------------
# register_mapping / remove_mapping / get_mapping module-level helpers
# ---------------------------------------------------------------------------


def test_register_and_remove_mapping_helpers(tmp_path):
    result = mux_daemon.register_mapping(_entry(), root=tmp_path)
    assert result["applied"] is True
    assert mux_daemon.get_mapping("proj", "wt-1", root=tmp_path)["live"] is True
    result = mux_daemon.remove_mapping("proj", "wt-1", root=tmp_path)
    assert result["applied"] is True
    tombstoned = mux_daemon.get_mapping("proj", "wt-1", root=tmp_path)
    assert tombstoned is not None
    assert tombstoned["live"] is False


# ---------------------------------------------------------------------------
# endpoint_from_rendezvous
# ---------------------------------------------------------------------------


def test_endpoint_from_rendezvous_rejects_malformed_or_out_of_range():
    assert mux_daemon.endpoint_from_rendezvous(None) is None
    assert mux_daemon.endpoint_from_rendezvous({}) is None
    assert mux_daemon.endpoint_from_rendezvous({"manager_mux_endpoint": "bad"}) is None
    assert (
        mux_daemon.endpoint_from_rendezvous(
            {"manager_mux_endpoint": "127.0.0.1:99999", "manager_mux_token": "t"}
        )
        is None
    )
    assert (
        mux_daemon.endpoint_from_rendezvous(
            {"manager_mux_endpoint": "127.0.0.1:0", "manager_mux_token": "t"}
        )
        is None
    )
    result = mux_daemon.endpoint_from_rendezvous(
        {"manager_mux_endpoint": "127.0.0.1:5555", "manager_mux_token": "t"}
    )
    assert result == ("127.0.0.1", 5555, "t")


# ---------------------------------------------------------------------------
# apply_status_options
# ---------------------------------------------------------------------------


def test_apply_status_options_runs_set_option_per_key(monkeypatch):
    calls = []

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    entry = _entry()
    ok = mux_daemon.apply_status_options(entry, {"@aw_ctx": "hello", "@aw_seg": "WIP"})
    assert ok is True
    assert len(calls) == 2
    assert calls[0][:4] == ["psmux", "set-option", "-t", "wt-1"]


def test_apply_status_options_false_on_any_failure(monkeypatch):
    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert mux_daemon.apply_status_options(_entry(), {"@aw_ctx": "x"}) is False


def test_apply_status_options_false_on_exception(monkeypatch):
    def _fake_run(argv, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert mux_daemon.apply_status_options(_entry(), {"@aw_ctx": "x"}) is False


# ---------------------------------------------------------------------------
# build_compute (mux-status-v1 handling)
# ---------------------------------------------------------------------------


def _fake_run_factory(*, has_session=True, set_option_ok=True):
    """A ``subprocess.run`` stand-in distinguishing ``has-session`` (the
    session-liveness revalidation probe) from ``set-option`` (the actual
    apply), so tests can control each independently."""

    def _fake_run(argv, **kwargs):
        if "has-session" in argv:
            return subprocess.CompletedProcess(argv, 0 if has_session else 1)
        return subprocess.CompletedProcess(argv, 0 if set_option_ok else 1)

    return _fake_run


def test_compute_rejects_unknown_kind(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    compute = mux_daemon.build_compute(registry)
    with pytest.raises(ValueError):
        compute("some-other-kind", {})


def test_compute_reports_not_live_when_no_mapping(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {"project": "proj", "worktree_id": "missing", "values": {"@aw_ctx": "x"}},
    )
    assert result == {"applied": False, "reason": "not-live"}


def test_compute_reports_not_live_when_mapping_is_tombstoned(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(live=False))
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {"project": "proj", "worktree_id": "wt-1", "values": {"@aw_ctx": "x"}},
    )
    assert result == {"applied": False, "reason": "not-live"}


def test_compute_applies_when_mapping_is_live(tmp_path, monkeypatch):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {"project": "proj", "worktree_id": "wt-1", "values": {"@aw_ctx": "x"}},
    )
    assert result == {"applied": True}


def test_compute_reports_apply_failed(tmp_path, monkeypatch):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(set_option_ok=False))
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {"project": "proj", "worktree_id": "wt-1", "values": {"@aw_ctx": "x"}},
    )
    assert result == {"applied": False, "reason": "apply-failed"}


def test_compute_invalidates_a_mapping_whose_mux_session_has_died(tmp_path, monkeypatch):
    """Copilot review finding: a persisted mapping's ``live`` bit alone must
    not be trusted -- a mux session that was torn down (e.g. while the
    daemon was down, or since the mapping was last observed) must be
    revalidated against the real mux server before being written to."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=3))
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(has_session=False))
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {"project": "proj", "worktree_id": "wt-1", "values": {"@aw_ctx": "x"}},
    )
    assert result == {"applied": False, "reason": "not-live"}
    # the dead mapping must be invalidated (tombstoned), not left "live"
    invalidated = registry.get("proj", "wt-1")
    assert invalidated["live"] is False


def test_status_push_key_requires_project_worktree_id_and_rendered_at():
    with pytest.raises(ValueError, match="project"):
        mux_daemon.status_push_key({"worktree_id": "wt-1", "rendered_at": "t"})
    with pytest.raises(ValueError, match="worktree_id"):
        mux_daemon.status_push_key({"project": "proj", "rendered_at": "t"})
    with pytest.raises(ValueError, match="rendered_at"):
        mux_daemon.status_push_key({"project": "proj", "worktree_id": "wt-1"})


def test_status_push_key_differs_across_distinct_renders():
    """Copilot review finding: two DIFFERENT renders of the same worktree
    must never coalesce onto the same in-flight execution (CoalescingServer
    joins same-key requests onto one result, discarding a joiner's own
    payload) -- the key must differ whenever rendered_at differs."""
    key_a = mux_daemon.status_push_key(
        {"project": "proj", "worktree_id": "wt-1", "rendered_at": "2026-09-25T00:00:00Z"}
    )
    key_b = mux_daemon.status_push_key(
        {"project": "proj", "worktree_id": "wt-1", "rendered_at": "2026-09-25T00:00:01Z"}
    )
    assert key_a != key_b


def test_two_distinct_renders_never_coalesce_over_the_wire(tmp_path, monkeypatch):
    """End-to-end proof: two concurrent mux-status-v1 pushes for the SAME
    worktree but with DIFFERENT rendered_at (keyed via status_push_key) both
    actually reach apply_status_options with their own distinct values --
    neither is silently discarded by coalescing."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    applied_values: list[dict] = []
    release = threading.Event()

    real_apply = mux_daemon.apply_status_options

    def _tracking_apply(entry, values):
        applied_values.append(dict(values))
        release.wait(timeout=2)  # hold the first call in-flight
        return real_apply(entry, values)

    monkeypatch.setattr(mux_daemon, "apply_status_options", _tracking_apply)
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())

    server = mux_daemon.start_server(mux_daemon.build_compute(registry))
    server.start()
    try:
        rv = mux_daemon.rendezvous_fields(server)
        host, port, token = mux_daemon.endpoint_from_rendezvous(
            {
                "manager_mux_endpoint": rv["manager_mux_endpoint"],
                "manager_mux_token": rv["manager_mux_token"],
            }
        )

        def _push(rendered_at, value, results, idx):
            payload = {
                "project": "proj",
                "worktree_id": "wt-1",
                "values": {"@aw_ctx": value},
                "rendered_at": rendered_at,
            }
            results[idx] = wcs_client.request(
                host,
                port,
                token,
                kind=mux_daemon.KIND,
                key=mux_daemon.status_push_key(payload),
                payload=payload,
                request_deadline_s=5.0,
                client_id=wcs_client.new_client_id(),
            )

        results: dict = {}
        t1 = threading.Thread(
            target=_push, args=("2026-09-25T00:00:00Z", "first", results, 0)
        )
        t1.start()
        time.sleep(0.1)  # let the first push become the owner and start blocking
        release_thread = threading.Thread(target=lambda: (time.sleep(0.2), release.set()))
        release_thread.start()
        _push("2026-09-25T00:00:01Z", "second", results, 1)
        t1.join(timeout=3)
        release_thread.join(timeout=3)

        assert results[0] == {"applied": True}
        assert results[1] == {"applied": True}
        # BOTH distinct payloads must have actually reached apply -- neither
        # discarded by coalescing onto the other's execution.
        assert {"@aw_ctx": "first"} in applied_values
        assert {"@aw_ctx": "second"} in applied_values
    finally:
        server.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"worktree_id": "wt-1", "values": {}},
        {"project": "proj", "values": {}},
        {"project": "proj", "worktree_id": "wt-1"},
        {"project": "proj", "worktree_id": "wt-1", "values": "not-a-dict"},
    ],
)
def test_compute_rejects_malformed_payload(tmp_path, payload):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    compute = mux_daemon.build_compute(registry)
    with pytest.raises(ValueError):
        compute(mux_daemon.KIND, payload)


# ---------------------------------------------------------------------------
# End-to-end: real CoalescingServer serving mux-status-v1 over a real socket
# ---------------------------------------------------------------------------


def test_daemon_serves_mux_status_v1_end_to_end(tmp_path, monkeypatch):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())
    server = mux_daemon.start_server(mux_daemon.build_compute(registry))
    server.start()
    try:
        rv = mux_daemon.rendezvous_fields(server)
        host, port, token = mux_daemon.endpoint_from_rendezvous(
            {
                "manager_mux_endpoint": rv["manager_mux_endpoint"],
                "manager_mux_token": rv["manager_mux_token"],
            }
        )
        client_id = wcs_client.new_client_id()
        payload = {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        }
        result = wcs_client.request(
            host,
            port,
            token,
            kind=mux_daemon.KIND,
            key=mux_daemon.status_push_key(payload),
            payload=payload,
            request_deadline_s=2.0,
            client_id=client_id,
        )
        assert result == {"applied": True}
    finally:
        server.close()


# ---------------------------------------------------------------------------
# Lock file read/write + liveness probe
# ---------------------------------------------------------------------------


def test_write_and_read_lock_data_roundtrip(tmp_path):
    path = tmp_path / "mux-daemon.lock"
    assert mux_daemon.write_lock_data(path, {"manager_mux_endpoint": "127.0.0.1:1"}) is True
    data = mux_daemon.read_lock_data(path)
    assert data["manager_mux_endpoint"] == "127.0.0.1:1"
    assert "pid" in data and "created_at" in data


def test_read_lock_data_absent_or_corrupt(tmp_path):
    assert mux_daemon.read_lock_data(tmp_path / "missing.lock") is None
    corrupt = tmp_path / "corrupt.lock"
    corrupt.write_text("{not json", encoding="utf-8")
    assert mux_daemon.read_lock_data(corrupt) is None


def test_daemon_is_live_false_for_absent_or_unreachable_endpoint(tmp_path):
    assert mux_daemon._daemon_is_live(None) is False
    assert (
        mux_daemon._daemon_is_live(
            {"manager_mux_endpoint": "127.0.0.1:1", "manager_mux_token": "t"}
        )
        is False
    )


def test_daemon_is_live_true_for_a_real_running_server(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    server = mux_daemon.start_server(mux_daemon.build_compute(registry))
    server.start()
    try:
        rv = mux_daemon.rendezvous_fields(server)
        assert mux_daemon._daemon_is_live(rv) is True
    finally:
        server.close()


def test_ensure_daemon_running_propagates_root_to_spawned_child(tmp_path, monkeypatch):
    """Copilot review finding: a caller-supplied non-default ``root`` must
    reach the spawned ``mux-daemon run`` child too -- otherwise
    ``ensure_daemon_running`` waits on a lock under ``root`` while the
    child publishes its own lock under the default installation root, so
    this helper reports failure despite successfully spawning a daemon."""
    captured: dict = {}

    def _fake_spawn(argv):
        captured["argv"] = argv
        return False  # spawn "succeeds" at the OS level is irrelevant here

    monkeypatch.setattr(mux_daemon, "_spawn_detached", _fake_spawn)
    mux_daemon.ensure_daemon_running(tmp_path, boot_wait_s=0.05)
    assert any(arg == f"--root={tmp_path}" for arg in captured["argv"])


def test_ensure_daemon_running_end_to_end_with_a_real_subprocess(tmp_path):
    """A genuine integration test: spawn the real ``mux-daemon run`` CLI as
    a subprocess (not mocked) and confirm ``ensure_daemon_running`` finds it
    live under the exact scratch root it was told to use."""
    import sys

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "worktree_manager",
            "mux-daemon",
            "run",
            f"--root={tmp_path}",
        ],
        cwd=str(Path(__file__).resolve().parent.parent / "src"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 10
        live = False
        while time.time() < deadline:
            data = mux_daemon.read_lock_data(mux_daemon.lock_path(tmp_path))
            if data is not None and mux_daemon._daemon_is_live(data):
                live = True
                break
            time.sleep(0.1)
        assert live, "the real subprocess never published a live rendezvous under the given root"
        # ensure_daemon_running must recognize this already-running daemon
        # as live (no second spawn needed) -- proves it reads the SAME lock
        # path the child actually published under.
        assert mux_daemon.ensure_daemon_running(tmp_path) is True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# run_daemon_foreground idle-exit lifecycle
# ---------------------------------------------------------------------------


def test_run_daemon_foreground_idle_exits_promptly(tmp_path):
    """No live mapping, no subscriber -- the daemon must idle-exit within
    its own bounded loop rather than running forever."""
    rc = mux_daemon.run_daemon_foreground(
        tmp_path, idle_after_s=0.05, poll_interval_s=0.01, max_iterations=50
    )
    assert rc == 0
    # the lock file must be cleaned up on exit
    assert not mux_daemon.lock_path(tmp_path).exists()


def test_run_daemon_foreground_publishes_a_live_rendezvous_while_running(tmp_path):
    """Start the daemon on a background thread, confirm its lock file
    publishes a genuinely-reachable endpoint while a live mapping keeps it
    from idle-exiting, then let it wind down."""
    registry = mux_daemon.MuxMappingRegistry(mux_daemon.registry_path(tmp_path))
    registry.register(_entry())

    result: dict = {}

    def _run():
        result["rc"] = mux_daemon.run_daemon_foreground(
            tmp_path, idle_after_s=0.2, poll_interval_s=0.02, max_iterations=200
        )

    thread = threading.Thread(target=_run)
    thread.start()
    try:
        deadline = time.time() + 5
        data = None
        while time.time() < deadline:
            data = mux_daemon.read_lock_data(mux_daemon.lock_path(tmp_path))
            if data is not None and mux_daemon._daemon_is_live(data):
                break
            time.sleep(0.02)
        assert data is not None
        assert mux_daemon._daemon_is_live(data) is True
        # now remove the live mapping so the daemon can idle-exit
        registry.remove("proj", "wt-1")
    finally:
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert result.get("rc") == 0


def test_run_daemon_foreground_only_unlinks_its_own_generations_lock(tmp_path, monkeypatch):
    """Copilot review finding: a retiring daemon must not unconditionally
    unlink the lock file -- if a REPLACEMENT daemon publishes its own
    (different) generation in the narrow window between this daemon's own
    shutdown and its lock cleanup, that live replacement's lock must
    survive. Simulated deterministically by injecting the replacement's
    write exactly at that window, rather than racing real timing."""
    lock = mux_daemon.lock_path(tmp_path)
    real_shutdown = mux_daemon.MuxDaemonRuntime.shutdown

    def _shutdown_then_simulate_replacement(self):
        real_shutdown(self)
        mux_daemon.write_lock_data(lock, {"generation_id": "someone-elses-generation"})

    monkeypatch.setattr(
        mux_daemon.MuxDaemonRuntime, "shutdown", _shutdown_then_simulate_replacement
    )
    rc = mux_daemon.run_daemon_foreground(
        tmp_path, idle_after_s=0.02, poll_interval_s=0.01, max_iterations=10
    )
    assert rc == 0
    # the replacement's lock must still be there, untouched
    data = mux_daemon.read_lock_data(lock)
    assert data is not None
    assert data["generation_id"] == "someone-elses-generation"


def test_ensure_daemon_running_serializes_concurrent_first_callers(tmp_path, monkeypatch):
    """Copilot review finding: two concurrent first callers must not both
    spawn their own daemon -- the whole check-then-spawn sequence is
    serialized, and a caller that loses the race must simply observe the
    winner's daemon as already live rather than spawning a second one."""
    spawn_calls: list[list[str]] = []
    spawn_lock = threading.Lock()

    def _fake_spawn(argv):
        with spawn_lock:
            spawn_calls.append(argv)
        # Simulate the spawned daemon actually starting shortly after.
        def _start_real_daemon():
            mux_daemon.run_daemon_foreground(
                tmp_path, idle_after_s=2.0, poll_interval_s=0.02, max_iterations=200
            )

        threading.Thread(target=_start_real_daemon, daemon=True).start()
        return True

    monkeypatch.setattr(mux_daemon, "_spawn_detached", _fake_spawn)

    results: list[bool] = [False, False]

    def _call(idx):
        results[idx] = mux_daemon.ensure_daemon_running(tmp_path, boot_wait_s=5.0)

    t1 = threading.Thread(target=_call, args=(0,))
    t2 = threading.Thread(target=_call, args=(1,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert results == [True, True]
    assert len(spawn_calls) == 1, "exactly one daemon should have been spawned"

