"""Tests for the Worktree Manager mux-companion daemon (Phase 3b Sub-slice 3
Step 2). Covers the mapping registry's persistence/monotonicity, the
``mux-status-v1`` compute handler, rendezvous parsing, and the resident
daemon's own idle-exit lifecycle -- all still off any real launch path per
this step's own scope (see ``mux_daemon.py``'s module docstring)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

from worktree_manager import mux_daemon
from worktree_manager import mux_mapping_registry


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


def _start_observation_server(seen: list[dict]) -> CoalescingServer:
    def _compute(kind, payload):
        seen.append({"kind": kind, "payload": dict(payload)})
        return {"applied": True}

    server = CoalescingServer(_compute, linger_seconds=5.0, subscriber_ttl=30.0)
    server.start()
    return server


def _write_status_monitor_lock(path: Path, server: CoalescingServer) -> None:
    rendezvous = server.rendezvous()
    path.write_text(
        json.dumps(
            {
                "managed_mux_endpoint": rendezvous["endpoint"],
                "managed_mux_token": rendezvous["token"],
                "managed_mux_generation": rendezvous["generation"],
            }
        ),
        encoding="utf-8",
    )


def _wait_for(predicate, *, timeout: float = 2.0, delay: float = 0.02) -> None:
    started = time.time()
    while time.time() - started < timeout:
        if predicate():
            return
        time.sleep(delay)
    pytest.fail("timed out waiting for expected condition")


# ---------------------------------------------------------------------------
# _normalize_mapping_entry
# ---------------------------------------------------------------------------


def test_normalize_mapping_entry_requires_core_fields():
    for field in ("project", "worktree_id", "mux_session", "mux_bin"):
        payload = _entry()
        del payload[field]
        with pytest.raises(ValueError, match=field):
            mux_mapping_registry._normalize_mapping_entry(payload)


def test_normalize_mapping_entry_rejects_bad_revision():
    with pytest.raises(ValueError):
        mux_mapping_registry._normalize_mapping_entry(_entry(mapping_revision=-1))
    with pytest.raises(ValueError):
        mux_mapping_registry._normalize_mapping_entry(_entry(mapping_revision="1"))
    with pytest.raises(ValueError):
        mux_mapping_registry._normalize_mapping_entry(_entry(mapping_revision=True))


def test_normalize_mapping_entry_defaults_and_pane_sanitization():
    payload = _entry(panes=[{"pane_id": "%1"}, {"not": "a pane"}, "garbage"])
    del payload["attached_clients"]
    del payload["session_incarnation"]
    del payload["observed_at"]
    entry = mux_mapping_registry._normalize_mapping_entry(payload)
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


def test_registry_remove_prevents_equal_revision_resurrection(tmp_path):
    """Copilot review finding: an explicit revisioned remove leaves a
    tombstone at EXACTLY that revision -- the monotonic ``<``-only guard
    alone would still accept a delayed LIVE register at that same
    (equal) revision, resurrecting the mapping."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=5))
    registry.remove("proj", "wt-1", mapping_revision=6)
    result = registry.register(_entry(mapping_revision=6))
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


def test_registry_remove_is_stale_noop_when_mux_session_mismatches_a_live_entry(tmp_path):
    """#3838: an old launcher's teardown for
    session A racing behind a newer launcher's activation for session B on
    the same (project, worktree_id) must not tombstone B's live mapping --
    even when A's teardown carries no ``mapping_revision`` at all (the
    common real-world shape: `pane-wrapper.sh`/`.ps1` don't always know a
    revision, only their own session name)."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mux_session="session-a", mapping_revision=1))
    registry.register(_entry(mux_session="session-b", mapping_revision=2))

    result = registry.remove("proj", "wt-1", mux_session="session-a")

    assert result["applied"] is False
    assert result["reason"] == "session_mismatch"
    stored = registry.get("proj", "wt-1")
    assert stored["live"] is True
    assert stored["mux_session"] == "session-b"
    assert stored["mapping_revision"] == 2


def test_registry_remove_with_matching_mux_session_still_tombstones(tmp_path):
    """The new guard must not block a normal, correctly-ordered removal --
    only a genuine session-identity mismatch against a still-live entry."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mux_session="session-a", mapping_revision=1))

    result = registry.remove("proj", "wt-1", mux_session="session-a")

    assert result == {"applied": True}
    stored = registry.get("proj", "wt-1")
    assert stored["live"] is False
    assert stored["mapping_revision"] == 2


def test_registry_remove_without_mux_session_preserves_prior_unguarded_behavior(tmp_path):
    """A caller that doesn't know its own session name (older launcher, or
    a generic CLI invocation) must keep the pre-#3838-fix behavior --
    ``mux_session=None`` is opt-out, not a forced guard."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mux_session="session-a", mapping_revision=1))
    registry.register(_entry(mux_session="session-b", mapping_revision=2))

    result = registry.remove("proj", "wt-1")

    assert result == {"applied": True}
    stored = registry.get("proj", "wt-1")
    assert stored["live"] is False


def test_registry_remove_uses_session_incarnation_over_reused_display_name(tmp_path):
    """Copilot review finding on PR #3906: ``mux_session`` (the display
    name) is NOT unique per launch incarnation -- both launchers derive the
    same deterministic ``wt-<worktree_id>``-shaped name for the same
    worktree, so a session recreated for the same worktree reuses the exact
    string. A stale teardown carrying the reused name but the OLD
    incarnation token must still be rejected, even though the name matches."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(
        _entry(mux_session="wt-1", session_incarnation="tmux-session-id-1:100", mapping_revision=1)
    )
    # Same reused display name, but a genuinely NEW real tmux session
    # (recreated after the old one was destroyed).
    registry.register(
        _entry(mux_session="wt-1", session_incarnation="tmux-session-id-2:200", mapping_revision=2)
    )

    stale_teardown = registry.remove(
        "proj", "wt-1", mux_session="wt-1", session_incarnation="tmux-session-id-1:100"
    )

    assert stale_teardown["applied"] is False
    assert stale_teardown["reason"] == "session_mismatch"
    stored = registry.get("proj", "wt-1")
    assert stored["live"] is True
    assert stored["session_incarnation"] == "tmux-session-id-2:200"

    # The CURRENT incarnation's own teardown must still succeed.
    current_teardown = registry.remove(
        "proj", "wt-1", mux_session="wt-1", session_incarnation="tmux-session-id-2:200"
    )
    assert current_teardown == {"applied": True}
    assert registry.get("proj", "wt-1")["live"] is False


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


def test_register_managed_mapping_auto_assigns_revision_and_publishes_live(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *a, **k: True)
    monkeypatch.setattr(
        mux_daemon,
        "publish_live_observation",
        lambda entry, **_kwargs: observed.append(dict(entry)) or {"applied": True},
    )

    first = _entry()
    del first["mapping_revision"]
    result = mux_daemon.register_managed_mapping(first, root=tmp_path)
    assert result == {"applied": True, "revision": 1}

    second = _entry(attached_clients=2)
    del second["mapping_revision"]
    result2 = mux_daemon.register_managed_mapping(second, root=tmp_path)
    assert result2 == {"applied": True, "revision": 2}

    assert [entry["mapping_revision"] for entry in observed] == [1, 2]
    assert observed[-1]["attached_clients"] == 2


def test_register_managed_mapping_allocates_revision_under_registry_lock(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *a, **k: True)
    monkeypatch.setattr(
        mux_daemon,
        "publish_live_observation",
        lambda entry, **_kwargs: observed.append(dict(entry)) or {"applied": True},
    )
    barrier = threading.Barrier(2)
    results = [None, None]

    def _worker(index: int, attached_clients: int):
        payload = _entry(attached_clients=attached_clients)
        del payload["mapping_revision"]
        barrier.wait()
        results[index] = mux_daemon.register_managed_mapping(payload, root=tmp_path)

    first = threading.Thread(target=_worker, args=(0, 1))
    second = threading.Thread(target=_worker, args=(1, 2))
    first.start()
    second.start()
    first.join()
    second.join()

    revisions = {result["revision"] for result in results}
    assert revisions == {1, 2}
    assert mux_daemon.get_mapping("proj", "wt-1", root=tmp_path)["mapping_revision"] == 2


def test_remove_managed_mapping_publishes_a_tombstone(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *a, **k: True)
    monkeypatch.setattr(
        mux_daemon,
        "publish_live_observation",
        lambda entry, **_kwargs: observed.append(dict(entry)) or {"applied": True},
    )

    mux_daemon.register_managed_mapping(_entry(), root=tmp_path)
    result = mux_daemon.remove_managed_mapping("proj", "wt-1", root=tmp_path)

    assert result == {"applied": True}
    assert observed[-1]["live"] is False


def test_manager_owned_launch_and_teardown_publish_live_and_tombstone_over_wire(tmp_path, monkeypatch):
    """Final Step 6 scenario: a Manager-owned launch publishes a live
    observation to the resident monitor over the real wire, and teardown
    clears it with a tombstone without reviving the legacy updater lane."""
    lock = tmp_path / "status-monitor.lock"
    observed: list[dict] = []
    server = _start_observation_server(observed)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: lock)
    monkeypatch.setattr(mux_daemon, "ensure_daemon_running", lambda *a, **k: True)
    _write_status_monitor_lock(lock, server)
    try:
        payload = _entry()
        del payload["mapping_revision"]
        result = mux_daemon.register_managed_mapping(payload, root=tmp_path)
        assert result == {"applied": True, "revision": 1}
        _wait_for(lambda: any(item["payload"].get("live") is True for item in observed))

        remove_result = mux_daemon.remove_managed_mapping("proj", "wt-1", root=tmp_path)
        assert remove_result == {"applied": True}
        _wait_for(lambda: any(item["payload"].get("live") is False for item in observed))
    finally:
        server.close()


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


def test_mux_live_with_boot_waits_for_status_monitor_endpoint_after_ensure(tmp_path):
    seen = []

    def _compute(kind, payload):
        seen.append((kind, payload))
        return {"applied": True, "revision": payload["mapping_revision"]}

    server = CoalescingServer(_compute, linger_seconds=5.0, subscriber_ttl=30.0)
    server.start()
    try:
        lock = tmp_path / "status-monitor.lock"
        rendezvous = server.rendezvous()

        def _ensure():
            def _publish():
                time.sleep(0.05)
                lock.write_text(
                    json.dumps(
                        {
                            "managed_mux_endpoint": rendezvous["endpoint"],
                            "managed_mux_token": rendezvous["token"],
                            "managed_mux_generation": rendezvous["generation"],
                        }
                    ),
                    encoding="utf-8",
                )

            threading.Thread(target=_publish, daemon=True).start()
            return True

        result = mux_daemon.mux_live_with_boot(
            read_lock_data=lambda: mux_daemon.read_lock_data(lock),
            ensure_monitor=_ensure,
            payload=_entry(),
            fallback=lambda: {"from": "fallback"},
            boot_wait_s=1.0,
            poll_interval_s=0.01,
        )
    finally:
        server.close()

    assert result == {"applied": True, "revision": 1}
    assert len(seen) == 1
    assert seen[0][0] == mux_daemon.LIVE_KIND
    assert seen[0][1]["worktree_id"] == "wt-1"


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


def _fake_run_factory(
    *,
    has_session=True,
    set_option_ok=True,
    list_clients_lines=None,
    list_clients_ok=True,
):
    """A ``subprocess.run`` stand-in distinguishing ``has-session`` (the
    session-liveness revalidation probe), ``list-clients`` (the
    attached-clients refresh probe), and ``set-option`` (the actual apply),
    so tests can control each independently. ``list_clients_lines``
    defaults to an empty listing (zero attached clients) when
    ``list_clients_ok`` is ``True``."""

    def _fake_run(argv, **kwargs):
        if "has-session" in argv:
            return subprocess.CompletedProcess(argv, 0 if has_session else 1)
        if "list-clients" in argv:
            if not list_clients_ok:
                return subprocess.CompletedProcess(argv, 1, stdout="")
            lines = list_clients_lines if list_clients_lines is not None else []
            stdout = "\n".join(lines) + ("\n" if lines else "")
            return subprocess.CompletedProcess(argv, 0, stdout=stdout)
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
        {
            "project": "proj",
            "worktree_id": "missing",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )
    assert result == {"applied": False, "reason": "not-live"}


def test_compute_reports_not_live_when_mapping_is_tombstoned(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(live=False))
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )
    assert result == {"applied": False, "reason": "not-live"}


def test_compute_dead_session_cleanup_does_not_tombstone_a_racing_newer_session(
    tmp_path, monkeypatch
):
    """Copilot review finding on PR #3906: the dead-mux-session cleanup path
    inside ``build_compute`` called ``registry.remove()`` directly with only
    a ``mapping_revision`` guard, bypassing the new session-identity guard --
    a concurrent CLI ``register()`` replacing this mapping with a NEWER live
    session at the same revision (an equal-revision live replacement
    ``register()`` itself accepts as a benign refresh) could still be
    tombstoned by this cleanup path. Uses distinct ``session_incarnation``
    values (a live tmux session_id:created probe in production), since the
    display session name alone is reused deterministically across launch
    incarnations for the same worktree and cannot distinguish them."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mux_session="session-a", session_incarnation="incarn-a", mapping_revision=1))

    def _fake_alive(mux_bin, session):
        # Simulate the race: between build_compute's own ``entry =
        # registry.get(...)`` snapshot (still session-a) and this liveness
        # probe, a concurrent CLI call replaces the mapping with session-b
        # at the SAME revision.
        registry.register(
            _entry(mux_session="session-b", session_incarnation="incarn-b", mapping_revision=1)
        )
        return False

    monkeypatch.setattr(mux_daemon, "_mux_session_alive", _fake_alive)
    compute = mux_daemon.build_compute(registry)

    result = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )

    assert result == {"applied": False, "reason": "not-live"}
    stored = registry.get("proj", "wt-1")
    assert stored is not None
    assert stored["live"] is True
    assert stored["mux_session"] == "session-b"
    assert stored["session_incarnation"] == "incarn-b"


def test_compute_rejects_missing_rendered_at(tmp_path):
    """Copilot review finding: a wire caller that bypasses status_push_key
    and submits a payload with no rendered_at sits outside the
    mux-status-v1 contract entirely (it cannot participate in the ordering
    fence) -- reject it before ever looking up the mapping."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    compute = mux_daemon.build_compute(registry)
    with pytest.raises(ValueError, match="rendered_at"):
        compute(
            mux_daemon.KIND,
            {"project": "proj", "worktree_id": "wt-1", "values": {"@aw_ctx": "x"}},
        )


def test_compute_applies_when_mapping_is_live(tmp_path, monkeypatch):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )
    assert result == {"applied": True}


def test_compute_reports_apply_failed(tmp_path, monkeypatch):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(set_option_ok=False))
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
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
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )
    assert result == {"applied": False, "reason": "not-live"}
    # the dead mapping must be invalidated (tombstoned), not left "live"
    invalidated = registry.get("proj", "wt-1")
    assert invalidated["live"] is False


def test_republish_refreshes_clients_without_changed_status_options(tmp_path, monkeypatch):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(attached_clients=0))
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run_factory(list_clients_lines=["/dev/pts/1: wt-1", "/dev/pts/2: wt-1"]),
    )
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )
    assert result == {"applied": True}
    refreshed = registry.get("proj", "wt-1")
    assert refreshed["attached_clients"] == 0
    observations = []

    def publish(entry, **kwargs):
        observations.append(entry["attached_clients"])
        return {"applied": True}

    monkeypatch.setattr(mux_daemon, "publish_live_observation", publish)
    assert mux_daemon._republish_live_mappings(registry, ensure_monitor=False)
    assert registry.get("proj", "wt-1")["attached_clients"] == 2
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(list_clients_lines=[]))
    # No further status push: rendered values have not changed.
    assert mux_daemon._republish_live_mappings(registry, ensure_monitor=False)
    assert registry.get("proj", "wt-1")["attached_clients"] == 0
    assert observations == [2, 0]
    assert refreshed["mapping_revision"] == 1


def test_republish_leaves_attached_clients_unchanged_on_probe_failure(tmp_path, monkeypatch):
    """A transient list-clients probe hiccup (unknown count) must never
    stomp a previously-observed, real attached-client count with a
    misleading 0."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(attached_clients=3))
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(list_clients_ok=False))
    monkeypatch.setattr(
        mux_daemon, "publish_live_observation", lambda *a, **kw: {"applied": True}
    )
    assert mux_daemon._republish_live_mappings(registry, ensure_monitor=False)
    refreshed = registry.get("proj", "wt-1")
    assert refreshed["attached_clients"] == 3


def test_slow_attachment_probe_does_not_block_status_and_is_drained(tmp_path, monkeypatch):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    entered = threading.Event()
    release = threading.Event()

    def run(argv, **kwargs):
        if "list-clients" in argv:
            entered.set()
            assert release.wait(timeout=10)
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        mux_daemon, "publish_live_observation", lambda *a, **kw: {"applied": True}
    )
    runtime = mux_daemon.MuxDaemonRuntime(mux_daemon.registry_path(tmp_path))
    runtime.start()
    assert runtime.server is not None

    def observe():
        with runtime.loop_mutation():
            mux_daemon._republish_live_mappings(runtime.registry, ensure_monitor=False)

    observer = threading.Thread(target=observe)
    observer.start()
    try:
        assert entered.wait(timeout=3)
        rv = mux_daemon.rendezvous_fields(runtime.server)
        host, port, token = mux_daemon.endpoint_from_rendezvous(rv)
        payload = {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "unchanged"},
            "rendered_at": "2026-09-25T00:00:00Z",
        }
        assert wcs_client.request(
            host, port, token,
            kind=mux_daemon.KIND,
            key=mux_daemon.status_push_key(payload),
            payload=payload,
            request_deadline_s=1.0,
            client_id=wcs_client.new_client_id(),
        ) == {"applied": True}
        assert observer.is_alive()
        assert runtime.drain({"timeout": 0})["drained"] is False
        release.set()
        observer.join(timeout=5)
        assert not observer.is_alive()
        assert runtime.drain({"timeout": 0})["drained"] is True
    finally:
        release.set()
        observer.join(timeout=5)
        runtime.shutdown()


def test_republish_skips_registry_write_when_attached_clients_already_matches(tmp_path, monkeypatch):
    """No in-place refresh write (and no revision/observed_at churn) when
    the probed count already matches what's stored."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(attached_clients=1, observed_at="2026-09-25T00:00:00Z"))
    monkeypatch.setattr(
        subprocess, "run", _fake_run_factory(list_clients_lines=["/dev/pts/1: wt-1"])
    )
    original_update = registry.update_attached_clients
    calls = []

    def _tracking_update(*args, **kwargs):
        calls.append((args, kwargs))
        return original_update(*args, **kwargs)

    monkeypatch.setattr(registry, "update_attached_clients", _tracking_update)
    monkeypatch.setattr(
        mux_daemon, "publish_live_observation", lambda *a, **kw: {"applied": True}
    )
    assert mux_daemon._republish_live_mappings(registry, ensure_monitor=False)
    assert calls == []
    refreshed = registry.get("proj", "wt-1")
    assert refreshed["attached_clients"] == 1
    assert refreshed["observed_at"] == "2026-09-25T00:00:00Z"


def test_status_push_key_requires_project_worktree_id_and_rendered_at():
    with pytest.raises(ValueError, match="project"):
        mux_daemon.status_push_key({"worktree_id": "wt-1", "rendered_at": "t", "values": {}})
    with pytest.raises(ValueError, match="worktree_id"):
        mux_daemon.status_push_key({"project": "proj", "rendered_at": "t", "values": {}})
    with pytest.raises(ValueError, match="rendered_at"):
        mux_daemon.status_push_key({"project": "proj", "worktree_id": "wt-1", "values": {}})
    with pytest.raises(ValueError, match="values"):
        mux_daemon.status_push_key(
            {"project": "proj", "worktree_id": "wt-1", "rendered_at": "t"}
        )


def test_status_push_key_differs_across_distinct_renders():
    """Copilot review finding: two DIFFERENT renders of the same worktree
    must never coalesce onto the same in-flight execution (CoalescingServer
    joins same-key requests onto one result, discarding a joiner's own
    payload) -- the key must differ whenever rendered_at differs."""
    key_a = mux_daemon.status_push_key(
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "rendered_at": "2026-09-25T00:00:00Z",
            "values": {"@aw_ctx": "x"},
        }
    )
    key_b = mux_daemon.status_push_key(
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "rendered_at": "2026-09-25T00:00:01Z",
            "values": {"@aw_ctx": "x"},
        }
    )
    assert key_a != key_b


def test_status_push_key_differs_when_only_values_differ():
    """Copilot review finding: rendered_at alone is not a guaranteed-unique
    render identity -- two genuinely different payloads sharing one
    (coarse clock resolution, or a caller bug) must still get distinct
    keys, since the values themselves differ."""
    key_a = mux_daemon.status_push_key(
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "rendered_at": "2026-09-25T00:00:00Z",
            "values": {"@aw_ctx": "first"},
        }
    )
    key_b = mux_daemon.status_push_key(
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "rendered_at": "2026-09-25T00:00:00Z",
            "values": {"@aw_ctx": "second"},
        }
    )
    assert key_a != key_b


def test_status_push_key_identical_for_a_genuine_retry():
    """The one case that SHOULD coalesce: an identical retry (same
    rendered_at, same values) gets the same key, so a concurrent duplicate
    request safely joins the original instead of doing redundant work."""
    payload = {
        "project": "proj",
        "worktree_id": "wt-1",
        "rendered_at": "2026-09-25T00:00:00Z",
        "values": {"@aw_ctx": "x"},
    }
    assert mux_daemon.status_push_key(payload) == mux_daemon.status_push_key(dict(payload))


def test_compute_discards_a_stale_render_arriving_after_a_newer_one(tmp_path, monkeypatch):
    """Copilot review finding: giving distinct renders distinct coalescing
    keys means CoalescingServer can run them concurrently, with no
    guarantee of completion order. An OLDER render finishing AFTER a newer
    one has already applied must be discarded, not allowed to overwrite the
    newer state."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())
    compute = mux_daemon.build_compute(registry)

    newer = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "newer"},
            "rendered_at": "2026-09-25T00:00:05Z",
        },
    )
    assert newer == {"applied": True}

    older = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "older"},
            "rendered_at": "2026-09-25T00:00:01Z",
        },
    )
    assert older == {"applied": False, "reason": "stale-render"}


def test_ordering_fence_survives_a_simulated_daemon_restart(tmp_path, monkeypatch):
    """Copilot review finding: the last-applied-render high-water mark must
    be durable, not merely in-memory -- a fresh build_compute() closure
    (simulating a daemon restart) sharing the SAME on-disk registry must
    still discard a delayed render older than what was applied before the
    (simulated) restart."""
    path = tmp_path / "mux-mapping.json"
    registry_before = mux_daemon.MuxMappingRegistry(path)
    registry_before.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())
    compute_before = mux_daemon.build_compute(registry_before)
    result = compute_before(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "before-restart"},
            "rendered_at": "2026-09-25T00:00:05Z",
        },
    )
    assert result == {"applied": True}

    # Simulate a daemon restart: a brand-new registry + build_compute
    # instance over the SAME persisted file, with no in-memory state
    # carried over.
    registry_after = mux_daemon.MuxMappingRegistry(path)
    compute_after = mux_daemon.build_compute(registry_after)
    stale = compute_after(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "delayed-stale"},
            "rendered_at": "2026-09-25T00:00:01Z",
        },
    )
    assert stale == {"applied": False, "reason": "stale-render"}


def test_registry_record_applied_render_persists_and_is_noop_when_superseded(tmp_path):
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=1))
    registry.record_applied_render("proj", "wt-1", "2026-09-25T00:00:05Z", mapping_revision=1)
    assert registry.get("proj", "wt-1")["last_status_rendered_at"] == "2026-09-25T00:00:05Z"

    # A register() at a genuinely HIGHER revision is a new incarnation --
    # it must start with a fresh (reset) ordering fence, per the sibling
    # "higher-revision registration" fix below. A late record_applied_render
    # still targeting the OLD (now-superseded) revision must remain a
    # no-op against this new incarnation.
    registry.register(_entry(mapping_revision=2))
    assert registry.get("proj", "wt-1")["last_status_rendered_at"] is None
    registry.record_applied_render("proj", "wt-1", "2026-09-25T00:00:01Z", mapping_revision=1)
    assert registry.get("proj", "wt-1")["last_status_rendered_at"] is None


def test_register_preserves_last_status_rendered_at_across_re_registration(tmp_path):
    """Copilot review finding safeguard: a register() payload never carries
    last_status_rendered_at itself (it is about mapping identity, not
    status ordering) -- a re-register at a HIGHER mapping_revision for the
    SAME underlying session must not silently reset the fence to None."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry(mapping_revision=1))
    registry.record_applied_render("proj", "wt-1", "2026-09-25T00:00:05Z", mapping_revision=1)
    # A re-register that keeps the SAME revision semantics conceptually
    # (e.g. refreshing attached_clients) should not lose the fence.
    registry.register(_entry(mapping_revision=1, attached_clients=2))
    assert registry.get("proj", "wt-1")["last_status_rendered_at"] == "2026-09-25T00:00:05Z"


def test_compute_rechecks_revision_immediately_before_applying(tmp_path, monkeypatch):
    """Copilot review finding: the interprocess lock is only held for each
    individual registry operation -- a concurrent CLI register/remove can
    still supersede the mapping between an earlier fetch and the actual
    write. Simulated deterministically: registry.get() returns the ORIGINAL
    entry once (for compute's own initial fetch), then a superseded
    (removed) view for the immediate-before-apply recheck."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())

    real_get = registry.get
    call_count = {"n": 0}

    def _get_then_supersede(project, worktree_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_get(project, worktree_id)
        # Simulate a concurrent remove landing between the initial fetch
        # and the immediate-before-apply recheck.
        return None

    monkeypatch.setattr(registry, "get", _get_then_supersede)
    compute = mux_daemon.build_compute(registry)
    result = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )
    assert result == {"applied": False, "reason": "not-live"}
    assert call_count["n"] == 2


def test_shutdown_waits_for_an_in_flight_handler_before_closing(tmp_path, monkeypatch):
    """CoalescingServer.close() does not join
    already-running handler threads -- a handler mid-apply must be fenced
    (waited for) before shutdown proceeds, so run_daemon_foreground never
    releases its single-instance lease while a stale handler could still
    be writing."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    release = threading.Event()
    entered = threading.Event()

    def _slow_run(argv, **kw):
        if "set-option" in argv:
            entered.set()
            release.wait(timeout=3)
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", _slow_run)

    runtime = mux_daemon.MuxDaemonRuntime(mux_daemon.registry_path(tmp_path))
    runtime.start()
    assert runtime.server is not None
    try:
        rv = mux_daemon.rendezvous_fields(runtime.server)
        host, port, token = mux_daemon.endpoint_from_rendezvous(
            {
                "manager_mux_endpoint": rv["manager_mux_endpoint"],
                "manager_mux_token": rv["manager_mux_token"],
            }
        )
        payload = {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        }

        push_result: dict = {}

        def _push():
            push_result["r"] = wcs_client.request(
                host,
                port,
                token,
                kind=mux_daemon.KIND,
                key=mux_daemon.status_push_key(payload),
                payload=payload,
                request_deadline_s=5.0,
                client_id=wcs_client.new_client_id(),
            )

        push_thread = threading.Thread(target=_push)
        push_thread.start()
        assert entered.wait(timeout=3), "handler never reached the in-flight apply"

        shutdown_result: dict = {}

        def _shutdown():
            start = time.time()
            runtime.shutdown()
            shutdown_result["elapsed"] = time.time() - start

        shutdown_thread = threading.Thread(target=_shutdown)
        shutdown_thread.start()
        # shutdown() must actually be BLOCKED waiting on the in-flight
        # handler right now, not racing past it.
        time.sleep(0.2)
        assert shutdown_thread.is_alive()

        release.set()
        push_thread.join(timeout=5)
        shutdown_thread.join(timeout=5)
        assert not shutdown_thread.is_alive()
        assert push_result["r"] == {"applied": True}
    finally:
        if runtime.server is not None:
            runtime.shutdown()


def test_compute_revalidates_the_mapping_fresh_immediately_before_applying(tmp_path, monkeypatch):
    """Copilot review finding: a status push must not write to a mapping
    that has been removed/superseded since an EARLIER lookup -- the
    revalidation must happen immediately before the write, not from a
    snapshot taken earlier in the caller's own flow."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    monkeypatch.setattr(subprocess, "run", _fake_run_factory())
    compute = mux_daemon.build_compute(registry)

    # Remove the mapping BETWEEN an earlier snapshot and this compute call
    # -- simulates a concurrent CLI remove racing with an in-flight push.
    registry.remove("proj", "wt-1")

    result = compute(
        mux_daemon.KIND,
        {
            "project": "proj",
            "worktree_id": "wt-1",
            "values": {"@aw_ctx": "x"},
            "rendered_at": "2026-09-25T00:00:00Z",
        },
    )
    assert result == {"applied": False, "reason": "not-live"}


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
    assert "pid" in data and "created_at" in data and "start_time" in data


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


def test_manager_daemon_restart_republishes_live_mapping_without_relaunch(tmp_path, monkeypatch):
    """Final Step 6 scenario: restarting the companion daemon with a live
    mapping already on disk republishes that mapping to the resident monitor
    again, rather than requiring a session relaunch or a per-session updater."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    lock = tmp_path / "status-monitor.lock"
    observed: list[dict] = []
    server = _start_observation_server(observed)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: lock)
    _write_status_monitor_lock(lock, server)
    try:
        assert mux_daemon.run_daemon_foreground(
            tmp_path, poll_interval_s=0.01, max_iterations=3
        ) == 0
        _wait_for(lambda: len(observed) >= 1)

        assert mux_daemon.run_daemon_foreground(
            tmp_path, poll_interval_s=0.01, max_iterations=3
        ) == 0
        _wait_for(lambda: len(observed) >= 2)
    finally:
        server.close()


def test_resident_monitor_restart_republishes_live_mapping_to_new_generation(tmp_path, monkeypatch):
    """Final Step 6 scenario: when the resident monitor restarts and publishes a
    fresh managed-mux generation, the companion daemon republishes every live
    mapping so status can resume without relaunching the mux session."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    lock = tmp_path / "status-monitor.lock"
    first_seen: list[dict] = []
    second_seen: list[dict] = []
    first = _start_observation_server(first_seen)
    second = None
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: lock)
    _write_status_monitor_lock(lock, first)
    daemon_result: dict = {}

    def _run():
        daemon_result["rc"] = mux_daemon.run_daemon_foreground(
            tmp_path, poll_interval_s=0.01, max_iterations=60
        )

    thread = threading.Thread(target=_run)
    thread.start()
    try:
        _wait_for(lambda: len(first_seen) >= 1)
        second = _start_observation_server(second_seen)
        _write_status_monitor_lock(lock, second)
        _wait_for(lambda: len(second_seen) >= 1)
    finally:
        first.close()
        if second is not None:
            second.close()
        # Generous relative to the nominal ~0.6s budget
        # (60 iterations * 0.01s poll): on a heavily loaded machine, each
        # iteration's real wall-clock cost (registry/lock I/O, OS
        # scheduling contention) can run well past the poll interval --
        # a tight join timeout here was observed to leave the thread still
        # running past a 5s wait, turning a slow-but-healthy daemon loop
        # into a flaky `KeyError: 'rc'` instead of a clear timeout signal.
        thread.join(timeout=20)

    assert thread.is_alive() is False, (
        "daemon thread did not finish within the join timeout -- "
        "see this test's own comment on machine-load sensitivity"
    )
    assert daemon_result["rc"] == 0


def test_live_mapping_republished_on_a_backstop_cadence_without_a_restart(
    tmp_path, monkeypatch
):
    """Real-bug regression: before this fix, a live mapping was only ever
    republished when the resident monitor's own lock *generation* changed --
    i.e. only right after a monitor restart. With no restart, the mapping
    aged out of the monitor's cache 45s after the last push and every
    managed session's status went silently, permanently blank. The daemon
    must also republish on a bounded backstop cadence, independent of any
    generation change, so a long-lived session with no monitor restart never
    goes stale."""
    registry = mux_daemon.MuxMappingRegistry(tmp_path / "mux-mapping.json")
    registry.register(_entry())
    lock = tmp_path / "status-monitor.lock"
    observed: list[dict] = []
    server = _start_observation_server(observed)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: lock)
    _write_status_monitor_lock(lock, server)
    daemon_result: dict = {}

    def _run():
        daemon_result["rc"] = mux_daemon.run_daemon_foreground(
            tmp_path,
            poll_interval_s=0.01,
            # 60 iterations keeps the daemon's own per-iteration file I/O
            # (write_lock_data + read_lock_data every loop, independent of
            # poll_interval_s) bounded, while still giving the 0.05s
            # backstop cadence ~12 chances to fire -- far more than the 2
            # observations this test needs.
            max_iterations=60,
            # Same lock/generation the whole run -- only the backstop cadence
            # (never a generation change) can explain a second observation.
            backstop_interval_s=0.05,
        )

    thread = threading.Thread(target=_run)
    thread.start()
    try:
        # Generous timeouts throughout: this exercises a real background
        # thread, a real local TCP server, and real file I/O -- on a heavily
        # loaded shared machine (many concurrent, unrelated processes) these
        # can each individually stall well past what the daemon's own actual
        # (sub-second) workload would ever need. Widening the wait budget
        # costs nothing when the daemon finishes quickly, and is the only
        # way to avoid a false failure when it doesn't.
        _wait_for(lambda: len(observed) >= 1, timeout=30.0)
        # Wait for the absolute count, not a delta from a captured snapshot:
        # on a fast run the daemon can already be well past 2 observations
        # by the time this thread is next scheduled, making any captured
        # "first_count" stale and racing this test's own bounded (60
        # iteration) daemon lifetime for a delta that may never arrive.
        # The unchanged generation the whole run means only the backstop
        # cadence can ever produce a 2nd (or later) observation, so >= 2
        # alone already proves the backstop path fired.
        _wait_for(lambda: len(observed) >= 2, timeout=30.0)
    finally:
        # Join the daemon thread BEFORE closing the fake observation server.
        # The request/response ``observed.append(...)`` that satisfies the
        # _wait_for above happens server-side mid-RPC -- the client
        # (wcs_client.request) still has its own follow-up ``release()``
        # call (a SEPARATE connection) to make for that same round-trip
        # before run_daemon_foreground's current iteration is done. Closing
        # the server before the thread is joined risks tearing its
        # listening socket down while that in-flight ``release()`` is
        # still dialing it, and on a loaded Windows box that raced
        # half-open connect can hang for the OS's full TCP connect timeout
        # instead of failing fast -- exactly the "daemon_result never gets
        # set" symptom this guards against. ``max_iterations`` already
        # bounds the daemon's own runtime deterministically, so joining
        # first (instead of relying on close() to unstick it) is both
        # correct and sufficient.
        thread.join(timeout=60)
        server.close()

    assert daemon_result.get("rc") == 0
    assert len(observed) >= 2


def test_live_mapping_republish_due_generation_change_or_backstop_cadence():
    """Unit coverage for the extracted pure decision helper: due on a fresh
    generation, on the backstop cadence elapsing with an unchanged
    generation, and not due otherwise."""
    due = mux_mapping_registry.live_mapping_republish_due
    # No monitor observed at all -> never due.
    assert due(None, None, None, now=100.0, backstop_interval_s=20.0) is False
    # First observation of a generation -> due immediately.
    assert due("gen-1", None, None, now=100.0, backstop_interval_s=20.0) is True
    # Same generation, never republished yet -> due.
    assert due("gen-1", "gen-1", None, now=100.0, backstop_interval_s=20.0) is True
    # Same generation, republished recently -> not due yet.
    assert due("gen-1", "gen-1", 95.0, now=100.0, backstop_interval_s=20.0) is False
    # Same generation, backstop interval elapsed -> due again.
    assert due("gen-1", "gen-1", 79.0, now=100.0, backstop_interval_s=20.0) is True
    # New generation, even if the backstop just fired -> due (restart path).
    assert due("gen-2", "gen-1", 99.9, now=100.0, backstop_interval_s=20.0) is True


def test_ensure_status_monitor_running_scrubs_session_credentials(monkeypatch):
    """This call can run from a launcher/pane-teardown process carrying
    ``GH_TOKEN``/``GITHUB_TOKEN``/an AHP token, and the resident status-
    monitor it (re)starts is long-lived -- it must not inherit them just
    because ``engine_client._engine_environment()`` only strips
    Python-parent variables, not auth tokens."""
    from worktree_manager import engine_client

    monkeypatch.setenv("GH_TOKEN", "secret-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-github")
    monkeypatch.setenv("AGENT_WORKTREES_AHP_AUTH_TOKEN", "secret-ahp")
    monkeypatch.setenv("SOME_OTHER_VAR", "kept")
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["agent-worktrees"])

    captured: dict = {}

    class _FakeCompletedProcess:
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeCompletedProcess()

    monkeypatch.setattr(mux_daemon.subprocess, "run", _fake_run)

    assert mux_daemon._ensure_status_monitor_running() is True
    env = captured["env"]
    assert env is not None
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert "AGENT_WORKTREES_AHP_AUTH_TOKEN" not in env
    assert env.get("SOME_OTHER_VAR") == "kept"


def test_runtime_health_reports_version_attached_clients_and_busy(tmp_path):
    """Phase 1 of copilot-extensions#5001 (``worktree-manager daemons
    status``) needs each resident daemon's own self-reported version and
    load, not just a bare ready/draining flag."""
    from worktree_manager import __version__

    runtime = mux_daemon.MuxDaemonRuntime(mux_daemon.registry_path(tmp_path))
    runtime.start()
    try:
        assert runtime.server is not None
        health = runtime.health()
        assert health == {
            "status": "ready",
            "version": __version__,
            "attached_clients": 0,
            "busy": False,
        }

        runtime.server.subscribe("client-1")
        runtime.begin_drain()
        health = runtime.health()
        assert health["status"] == "draining"
        assert health["attached_clients"] == 1
    finally:
        runtime.shutdown()


def test_health_over_the_real_wire_excludes_its_own_probe_from_load(tmp_path):
    """A health REQUEST is itself an accepted handler and a touched
    subscriber for its own duration (``CoalescingServer``'s accept-time
    counter / ``touch()``), so calling ``runtime.health()`` directly
    in-process (the test above) can never exercise that inflation -- only a
    real round trip over the wire can. With exactly one genuinely attached
    (persistent) subscriber and no other in-flight request, a real
    ``mux-cutover-health-v1`` call must still report ``attached_clients ==
    1`` and ``busy is False``, not 2/True from double-counting its own
    transient probe connection."""
    runtime = mux_daemon.MuxDaemonRuntime(mux_daemon.registry_path(tmp_path))
    runtime.start()
    try:
        assert runtime.server is not None
        runtime.server.subscribe("persistent-session")

        rv = mux_daemon.rendezvous_fields(runtime.server)
        host, port, token = mux_daemon.endpoint_from_rendezvous(
            {
                "manager_mux_endpoint": rv["manager_mux_endpoint"],
                "manager_mux_token": rv["manager_mux_token"],
            }
        )
        client_id = wcs_client.new_client_id()
        try:
            health = wcs_client.request(
                host,
                port,
                token,
                kind=mux_daemon.mux_daemon_cutover.health_kind(),
                key="control",
                payload={},
                request_deadline_s=5.0,
                client_id=client_id,
            )
        finally:
            wcs_client.release(host, port, token, client_id, timeout=5.0)

        assert health["attached_clients"] == 1
        assert health["busy"] is False
    finally:
        runtime.shutdown()


def test_health_excludes_every_concurrently_in_flight_control_request(tmp_path):
    """Two concurrently in-flight cutover-control requests (e.g. two
    overlapping ``daemons status`` health probes, or a probe racing an
    in-flight drain) must each be excluded from reported load -- not just a
    flat "one" contribution, which would double-count the other request
    whenever more than one control request happens to be in flight at
    once."""
    runtime = mux_daemon.MuxDaemonRuntime(mux_daemon.registry_path(tmp_path))
    runtime.start()
    try:
        assert runtime.server is not None
        runtime.server.subscribe("persistent-session")
        # Simulate two concurrently in-flight control requests directly:
        # each is an accepted handler plus a touched subscriber for its own
        # duration, exactly like a real one.
        runtime.server.subscribe("probe-1")
        runtime.server.subscribe("probe-2")
        runtime.server._on_request_accepted()
        runtime.server._on_request_accepted()
        runtime._begin_control_request()
        runtime._begin_control_request()

        health = runtime.health(exclude_current_request=True)

        assert health["attached_clients"] == 1
        assert health["busy"] is False
    finally:
        runtime.server._on_request_finished()
        runtime.server._on_request_finished()
        runtime.shutdown()


def test_health_requests_use_a_unique_key_to_avoid_coalescing(tmp_path, monkeypatch):
    """``ControlClient.health()`` must never coalesce with another
    concurrent health request: ``CoalescingServer`` joins any two calls
    sharing the same ``(kind, key)`` into a single ``_compute`` execution,
    which would mean only ONE of two truly concurrent wire-level health
    probes actually increments ``_control_requests_in_flight`` -- exactly
    the double-counting bug this key uniqueness avoids. Confirm each call
    gets its own, never-repeated key."""
    seen_keys: list[str] = []

    def _fake_request(self, kind, payload=None, *, key="control"):
        seen_keys.append(key)
        return {}

    monkeypatch.setattr(mux_daemon.mux_daemon_cutover.ControlClient, "_request", _fake_request)
    # Stub the token loader (not the file system): __init__ would otherwise
    # read/create a real token file at the given root, which this test's
    # mocked _request never needs and which can fail on a host where that
    # path isn't writable.
    monkeypatch.setattr(
        mux_daemon.mux_daemon_cutover, "load_or_create_control_token", lambda root=None: "tok"
    )
    client = mux_daemon.mux_daemon_cutover.ControlClient("http://127.0.0.1:1", root=tmp_path)

    client.health()
    client.health()

    assert len(seen_keys) == 2
    assert seen_keys[0] != seen_keys[1]
    assert all(
        key.startswith(mux_daemon.mux_daemon_cutover.health_kind()) for key in seen_keys
    )


def test_scrub_session_credentials_is_case_insensitive():
    """Copilot review finding on PR #3839: Windows environment-variable
    names are case-insensitive, so a parent carrying `gh_token`,
    `github_token`, or a differently-cased AHP token must still be
    scrubbed -- an exact-case set-membership check silently misses those."""
    env = {
        "gh_token": "secret-gh-lower",
        "Github_Token": "secret-github-mixed",
        "agent_worktrees_ahp_auth_token": "secret-ahp-lower",
        "GH_TOKEN": "secret-gh-upper",
        "SOME_OTHER_VAR": "kept",
    }

    scrubbed = mux_daemon._scrub_session_credentials(env)

    assert scrubbed == {"SOME_OTHER_VAR": "kept"}


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


def test_spawn_detached_scrubs_auth_env(monkeypatch):
    captured = {}

    def _fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = dict(kwargs["env"])
        class _Proc:
            pass
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("AGENT_WORKTREES_AHP_AUTH_TOKEN", "ahp-token")

    assert mux_daemon._spawn_detached(["python", "-m", "worktree_manager"]) is True
    assert "GH_TOKEN" not in captured["env"]
    assert "GITHUB_TOKEN" not in captured["env"]
    assert "AGENT_WORKTREES_AHP_AUTH_TOKEN" not in captured["env"]


def test_spawn_detached_windows_kwargs_avoid_console_flash(monkeypatch):
    """Regression test (2026-09-26 flashing-console bug): the daemon's own
    subsequent per-tick ``subprocess.run([mux_bin, ...])`` calls (in
    ``apply_status_options``/``_mux_session_alive``) are console-subsystem
    children. A parent spawned with ``DETACHED_PROCESS`` gives such children
    no console to inherit, so each one allocates -- and flashes -- its own.
    Assert the Windows branch never sets ``DETACHED_PROCESS`` and does set
    ``CREATE_NO_WINDOW``, matching ``agent_procutil.windowless_daemon_kwargs``'s
    documented contract. ``_is_windows()`` reads ``os.name``, not
    ``sys.platform`` -- patch that."""
    captured = {}

    def _fake_popen(argv, **kwargs):
        captured["kwargs"] = kwargs
        class _Proc:
            pass
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)

    assert mux_daemon._spawn_detached(["python", "-m", "worktree_manager"]) is True
    flags = captured["kwargs"].get("creationflags", 0)
    # These constants only exist on the ``subprocess`` module on Windows;
    # mirror agent_procutil's own portable fallbacks so this test can run
    # (and correctly assert Windows-shaped kwargs) on any host OS.
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    detached_process = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    assert flags & create_no_window
    assert not flags & detached_process


def test_spawn_detached_posix_kwargs_unaffected(monkeypatch):
    """POSIX has no console-subsystem concept; confirm the fix left its
    ``start_new_session`` behavior alone."""
    captured = {}

    def _fake_popen(argv, **kwargs):
        captured["kwargs"] = kwargs
        class _Proc:
            pass
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)

    assert mux_daemon._spawn_detached(["python", "-m", "worktree_manager"]) is True
    assert captured["kwargs"].get("start_new_session") is True
    assert "creationflags" not in captured["kwargs"]


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


def test_run_daemon_foreground_stands_down_when_lease_already_held(tmp_path):
    """Copilot review finding: any invocation path -- not just callers
    going through ensure_daemon_running -- must be unable to start a
    second daemon. Holding the single-instance lease directly (simulating
    a genuinely running daemon) proves a second run_daemon_foreground call
    stands down immediately: no server started, no lock file touched."""
    lease = mux_daemon._acquire_daemon_lease(tmp_path)
    assert lease is not None
    try:
        rc = mux_daemon.run_daemon_foreground(
            tmp_path, idle_after_s=0.02, poll_interval_s=0.01, max_iterations=5
        )
        assert rc == 0
        # never even published a rendezvous of its own
        assert mux_daemon.read_lock_data(mux_daemon.lock_path(tmp_path)) is None
    finally:
        mux_daemon._release_daemon_lease(lease)


def test_run_daemon_foreground_racing_instances_only_one_actually_runs(tmp_path):
    """Copilot review finding (the direct-run bypass): two genuinely
    concurrent run_daemon_foreground calls for the SAME root must not both
    become live -- exactly one wins the single-instance lease and starts a
    server; the loser returns immediately without ever publishing (or
    disturbing) a rendezvous."""
    results: list[int] = [None, None]

    def _run(idx):
        results[idx] = mux_daemon.run_daemon_foreground(
            tmp_path, idle_after_s=0.3, poll_interval_s=0.02, max_iterations=200
        )

    t1 = threading.Thread(target=_run, args=(0,))
    t2 = threading.Thread(target=_run, args=(1,))
    t1.start()
    t2.start()

    # Confirm exactly one of them actually became a live, reachable daemon.
    deadline = time.time() + 5
    live = False
    while time.time() < deadline:
        data = mux_daemon.read_lock_data(mux_daemon.lock_path(tmp_path))
        if data is not None and mux_daemon._daemon_is_live(data):
            live = True
            break
        time.sleep(0.02)
    assert live, "neither racing instance ever became live"

    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()
    assert results == [0, 0]
    # the winner's own idle-exit cleanup must have removed its rendezvous
    assert mux_daemon.read_lock_data(mux_daemon.lock_path(tmp_path)) is None


def test_ensure_daemon_running_serializes_concurrent_first_callers(tmp_path, monkeypatch):
    """Copilot review finding: two concurrent first callers may each spawn
    a child (this function no longer needs to prevent that itself -- see
    its own docstring), but only ONE daemon may ever actually become live:
    both children race for the same single-instance lease, so both
    ensure_daemon_running calls must still observe success (whichever
    daemon wins), and at most one spawn call's child may ever have
    published a rendezvous at once."""
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
    assert 1 <= len(spawn_calls) <= 2
