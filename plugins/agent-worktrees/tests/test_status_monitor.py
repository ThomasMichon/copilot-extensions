"""Tests for the resident, coalescing ``status-monitor``.

The monitor consolidates the per-session ``status-updater`` loops into a single
process (the work-coalescing-singleton service tier): one coalesced sweep
refreshes every live ``wt-*`` session's bar, the per-session registry doubles as
a refcount, and the monitor idle-exits when the last session goes. It is opt-in
via ``AGENT_WORKTREES_STATUS_MONITOR``; unset, the per-session updater is
unchanged. These tests drive the pure sweep + registry + routing seams without a
real mux.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import agent_procutil
import pytest
import single_instance_lease

from agent_worktrees import __main__ as m
from agent_worktrees import output
from agent_worktrees import session_catalog
from agent_worktrees import status_monitor_reap_stale
from agent_worktrees import status_monitor_runtime
from agent_worktrees import worktree_identity


def test_status_monitor_registered():
    assert m.COMMAND_MAP["status-monitor"] is m.cmd_status_monitor
    assert m._WORKTREE_VERBS["status-monitor"] == "status-monitor"
    # main() must not try to resolve a project for it (it resolves per-session),
    # and the launcher reap must never kill the resident tracker.
    assert "status-monitor" in m._NO_PROJECT_COMMANDS
    assert "status-monitor" in m._LAUNCHER_REAP_VETOES


def test_status_monitor_exits_immediately_when_lease_already_held(monkeypatch, tmp_path):
    """The atomic exclusivity gate must run BEFORE the existing lock-file
    liveness check, and losing the race must return before any lock-file
    write or further setup -- a losing caller exits in microseconds instead
    of running on as an undetected duplicate until some later periodic
    re-check happens to notice a sibling."""

    class _AlwaysHeld:
        def __init__(self, *_a, **_kw) -> None:
            pass

        def acquire(self) -> None:
            raise single_instance_lease.AlreadyRunningError(tmp_path / "status-monitor.lock", 999)

    monkeypatch.setattr(single_instance_lease, "SingleInstance", _AlwaysHeld)
    write_calls: list = []
    monkeypatch.setattr(m.locks, "write_lock", lambda *a, **kw: write_calls.append((a, kw)))

    args = argparse.Namespace(interval=15, passive=False, control_port=None)
    assert m.cmd_status_monitor(args) == 0
    assert write_calls == [], "must return before the lock-file write, not just before the main loop"


def test_status_monitor_proceeds_when_lease_is_won(monkeypatch, tmp_path):
    """The inverse: winning the atomic gate must not itself block the
    existing lock-file publish (so other tooling -- the status bar, a
    sibling's supersession check -- still sees this monitor's metadata)."""
    acquired: list = []

    class _AlwaysWins:
        def __init__(self, *_a, **_kw) -> None:
            pass

        def acquire(self) -> None:
            acquired.append(True)

        def release(self) -> None:
            pass

    monkeypatch.setattr(single_instance_lease, "SingleInstance", _AlwaysWins)
    write_calls: list = []
    monkeypatch.setattr(m.locks, "write_lock", lambda *a, **kw: write_calls.append((a, kw)) or True)
    # No OTHER monitor owns the lock-file side either, so `_other_current_
    # monitor` is False and we reach the write.
    monkeypatch.setattr(m.locks, "read_lock", lambda *a, **kw: None)
    monkeypatch.setattr(m.locks, "lock_is_live", lambda *a, **kw: False)

    def _raise_to_exit_before_the_loop(*_a, **_kw):
        raise RuntimeError("reached the resident loop -- test stops here by design")

    # `session_catalog.ResidentSessionReconciler` is the first real
    # object construction reached AFTER the gate + lock-file write this
    # test cares about, well before the actual indefinite sweep loop
    # (`threading.Event()`, used for `shutdown_requested`, fires much
    # EARLIER in this function -- before the gate even runs -- so it can't
    # serve as a "stop here" marker for this test).
    monkeypatch.setattr(session_catalog, "ResidentSessionReconciler", _raise_to_exit_before_the_loop)

    args = argparse.Namespace(interval=15, passive=False, control_port=None)
    with pytest.raises(RuntimeError, match="reached the resident loop"):
        m.cmd_status_monitor(args)
    assert acquired == [True], "must acquire the ownership lease exactly once"
    assert len(write_calls) == 1
    assert write_calls[0][0][0] == m._monitor_lock_path(), "wrote the real monitor lock path"


def test_status_monitor_passive_mode_skips_the_lease_entirely(monkeypatch):
    """Passive mode never competes for ownership (matches the pre-existing
    `_other_current_monitor` early return for `passive_mode`) -- it must not
    even attempt to acquire the lease."""
    constructed: list = []

    class _Tracking:
        def __init__(self, *a, **kw) -> None:
            constructed.append((a, kw))

        def acquire(self) -> None:
            raise AssertionError("passive mode must never acquire the lease")

    monkeypatch.setattr(single_instance_lease, "SingleInstance", _Tracking)

    def _raise_to_exit_before_the_loop(*_a, **_kw):
        raise RuntimeError("reached the resident loop -- test stops here by design")

    monkeypatch.setattr(session_catalog, "ResidentSessionReconciler", _raise_to_exit_before_the_loop)
    monkeypatch.setattr(m.locks, "read_lock", lambda *a, **kw: None)
    monkeypatch.setattr(m.locks, "lock_is_live", lambda *a, **kw: False)

    args = argparse.Namespace(interval=15, passive=True, control_port=None)
    with pytest.raises(RuntimeError, match="reached the resident loop"):
        m.cmd_status_monitor(args)
    assert constructed == [], "passive mode must not construct a SingleInstance at all"


def test_status_monitor_lease_does_not_collide_with_metadata_file(tmp_path):
    """Real-interaction regression for a High-severity review finding
    (PR #5412): the ownership lease and the pre-existing metadata lock file
    must be genuinely separate files. `SingleInstance.acquire()` writes its
    own pid bytes into the first bytes of whatever file it locks, and
    `_locks.write_lock()` separately replaces its target file's entire
    contents via `os.replace` (a new inode at the same path) -- sharing one
    path would have either write corrupt the other, and on POSIX a metadata
    replace would strand the held flock on the old inode, silently
    defeating the gate for the next contender that opens the replacement.
    No mocking of `single_instance_lease` here -- this exercises the real
    primitive against a real temp directory, exactly as the review
    requested."""
    lease = single_instance_lease.SingleInstance(
        tmp_path, service="status-monitor", lock_name="status-monitor-singleton.lock"
    )
    lease.acquire()
    try:
        metadata_path = tmp_path / "status-monitor.lock"
        assert metadata_path != lease.lock_path, "lease and metadata must be distinct files"

        # Metadata writes (and the repeated os.replace churn a live monitor
        # does on every sweep) must never disturb the held lease.
        assert m.locks.write_lock(metadata_path, extra={"prefix": "/fake", "mux": True})
        data = m.locks.read_lock(metadata_path)
        assert data is not None and data["prefix"] == "/fake", "metadata must survive acquisition"

        # A second attempt on the SAME lease name, while the first is held,
        # must be rejected -- the actual exclusivity guarantee under test.
        contender = single_instance_lease.SingleInstance(
            tmp_path, service="status-monitor", lock_name="status-monitor-singleton.lock"
        )
        with pytest.raises(single_instance_lease.AlreadyRunningError):
            contender.acquire()

        # ... and a further metadata write/read after the contention attempt
        # must still work -- proving the earlier replace never touched the
        # lease's inode.
        assert m.locks.write_lock(metadata_path, extra={"prefix": "/fake2", "mux": False})
        data2 = m.locks.read_lock(metadata_path)
        assert data2 is not None and data2["prefix"] == "/fake2"
    finally:
        lease.release()


def test_status_monitor_aborts_startup_when_publication_write_fails(monkeypatch):
    """Regression for a High-severity review finding (PR #5412):
    `locks.write_lock()` is best-effort and returns ``False`` (never
    raises) on an I/O failure. Continuing anyway after a failed publish
    would leave us believing we're the published owner when no metadata
    actually says so. Startup must abort (nonzero exit) rather than
    proceed on a failed write, and must still release the lease it
    acquired."""
    released: list[str] = []

    class _TrackRelease:
        def __init__(self, *_a, lock_name=None, **_kw) -> None:
            self._lock_name = lock_name

        def acquire(self) -> None:
            pass

        def release(self) -> None:
            released.append(self._lock_name)

    monkeypatch.setattr(single_instance_lease, "SingleInstance", _TrackRelease)
    monkeypatch.setattr(m.locks, "read_lock", lambda *_a, **_kw: None)
    monkeypatch.setattr(m.locks, "lock_is_live", lambda *_a, **_kw: False)
    monkeypatch.setattr(m.locks, "write_lock", lambda *_a, **_kw: False)

    args = argparse.Namespace(interval=15, passive=False, control_port=None)
    assert m.cmd_status_monitor(args) != 0, "a failed publish must abort startup, not continue"
    assert released == ["status-monitor-singleton.lock"], (
        "the ownership lease must still be released on this failure path"
    )


def test_resident_lifecycle_requests_wait_for_their_deadline():
    assert m._resident_hook_lock_timeout("sessionStart", 4.75) == 3.75
    assert m._resident_hook_lock_timeout("sessionStart", 0.75) == 0.0
    assert m._resident_hook_lock_timeout("preToolUse", 1.75) == 0.05
    assert m._resident_hook_lock_timeout("postToolUse", 0.02) == 0.02


def test_ordinary_hooks_yield_to_waiting_lifecycle_request():
    priority = types.SimpleNamespace(is_set=lambda: True)

    assert m._resident_hook_should_yield("preToolUse", priority) is True
    assert m._resident_hook_should_yield("postToolUse", priority) is True
    assert m._resident_hook_should_yield("sessionStart", priority) is False


def test_identical_resident_lifecycle_requests_coalesce():
    payload = {
        "sessionId": "session-1",
        "cwd": str(m.Path.cwd()),
        "source": "resume",
        "timestamp": 1_800_000_000.25,
        "_agentWorktrees": {"pluginVersion": "1.5.3-dev759"},
    }
    claims = {}

    launch_key, claimed = m._claim_resident_lifecycle(payload, claims, now=10.0)
    duplicate_key, duplicate_claimed = m._claim_resident_lifecycle(payload, claims, now=10.0)

    assert launch_key
    assert duplicate_key == launch_key
    assert claimed is True
    assert duplicate_claimed is False


def test_distinct_resident_lifecycle_requests_do_not_coalesce():
    first = {
        "sessionId": "session-1",
        "cwd": str(m.Path.cwd()),
        "source": "resume",
        "timestamp": 1_800_000_000.25,
        "_agentWorktrees": {"pluginVersion": "1.5.3-dev759"},
    }
    second = {**first, "timestamp": 1_800_000_000.5}
    claims = {}

    first_key, first_claimed = m._claim_resident_lifecycle(first, claims, now=10.0)
    second_key, second_claimed = m._claim_resident_lifecycle(second, claims, now=10.0)

    assert first_key != second_key
    assert first_claimed is True
    assert second_claimed is True


def test_completed_resident_lifecycle_claim_expires():
    payload = {
        "sessionId": "session-1",
        "cwd": str(m.Path.cwd()),
        "source": "resume",
        "timestamp": 1_800_000_000.25,
        "_agentWorktrees": {"pluginVersion": "1.5.3-dev759"},
    }
    launch_key = m._session_lifecycle_launch_key(payload, "1.5.3-dev759")
    claims = {launch_key: 20.0}

    duplicate_key, duplicate_claimed = m._claim_resident_lifecycle(payload, claims, now=19.0)
    retried_key, retried_claimed = m._claim_resident_lifecycle(payload, claims, now=20.0)

    assert duplicate_key == launch_key
    assert duplicate_claimed is False
    assert retried_key == launch_key
    assert retried_claimed is True


def test_completed_resident_lifecycle_claim_is_retained():
    claims = {"launch-key": float("inf")}

    m._release_resident_lifecycle("launch-key", claims, completed=True, now=10.0)

    assert claims == {"launch-key": 10.0 + m._RESIDENT_LIFECYCLE_DEDUPE_S}


def test_failed_resident_lifecycle_claim_is_released():
    claims = {"launch-key": float("inf")}

    m._release_resident_lifecycle("launch-key", claims, completed=False, now=10.0)

    assert claims == {}


def test_monitor_yields_to_waiting_lifecycle_request(monkeypatch):
    states = iter((True, True, False))
    priority = types.SimpleNamespace(is_set=lambda: next(states))
    sleeps = []
    monkeypatch.setattr(m.time, "sleep", sleeps.append)

    m._wait_for_lifecycle_priority(priority)

    assert sleeps == [0.01, 0.01]


def test_reconcile_sessions_registered():
    assert m.COMMAND_MAP["reconcile-sessions"] is m.cmd_reconcile_sessions
    assert m._WORKTREE_VERBS["reconcile-sessions"] == "reconcile-sessions"
    assert "reconcile-sessions" in m._NO_PROJECT_COMMANDS


def test_reconcile_sessions_emits_one_bounded_pass(monkeypatch):
    captured: dict = {}

    class _Reconciler:
        def __init__(self, **kwargs):
            captured["budgets"] = kwargs

        def observe_mux(self, names):
            captured["mux"] = names

        @property
        def has_mux_observation(self):
            return "mux" in captured

        def step(self):
            return {"projection_written": 1}

    monkeypatch.setattr(
        "agent_worktrees.session_catalog.ResidentSessionReconciler",
        _Reconciler,
    )
    monkeypatch.setattr(m.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        output,
        "_json_output",
        lambda value: captured.update({"output": value}),
    )

    assert (
        m.cmd_reconcile_sessions(
            argparse.Namespace(
                record_budget=2,
                session_budget=3,
                projection_budget=4,
            )
        )
        == 0
    )
    assert captured["budgets"] == {
        "record_budget": 2,
        "session_budget": 3,
        "projection_budget": 4,
    }
    assert captured["mux"] == set()
    assert captured["output"] == {
        "projection_written": 1,
        "mux_observed": True,
    }


@pytest.mark.parametrize(
    "val,expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("", True),
        ("nope", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
    ],
)
def test_enabled_env(monkeypatch, val, expected):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", val)
    assert m._status_monitor_enabled() is expected


def test_enabled_env_unset(monkeypatch):
    """Default-on: absent the env var, the monitor is enabled (opt-out)."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    assert m._status_monitor_enabled() is True


def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    assert m._register_session_for_monitor("wt-a", "/w/a") is True
    assert m._register_session_for_monitor("wt-b", "/w/b") is True
    assert m._register_session_for_monitor("", "/w/x") is False  # no session
    assert m._register_session_for_monitor("wt-c", None) is False  # no path
    reg = m._read_monitor_registry(tmp_path / "reg")
    assert reg == {"wt-a": "/w/a", "wt-b": "/w/b"}


@pytest.mark.parametrize(
    "bad", ["../evil", "wt-../../x", "/abs/path", "wt-a/b", "wt-a\\b", "notwt"]
)
def test_registry_rejects_unsafe_session(tmp_path, monkeypatch, bad):
    """``--session`` is untrusted: a traversal / absolute / non-wt name must be
    rejected (never escape the registry dir), so it writes nothing."""
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    assert m._valid_monitor_session(bad) is False
    assert m._register_session_for_monitor(bad, "/w/a") is False
    if reg.exists():
        assert list(reg.iterdir()) == []


def _capture_set(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        m,
        "_monitor_mux_set",
        lambda mux_bin, sess, opt, val: calls.append((sess, opt, val)) or True,
    )
    return calls


def test_sweep_serves_live_registered_and_prunes_gone(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    m._register_session_for_monitor("wt-b", "/w/b")
    m._register_session_for_monitor("wt-gone", "/w/g")

    # wt-a + wt-b are live; wt-gone is not; a non-wt session is ignored.
    monkeypatch.setattr(
        m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1, "wt-b": 0, "other": 1}
    )
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    calls = _capture_set(monkeypatch)

    ctx_done: set[str] = set()
    served = m._monitor_sweep("tmux", "TOK", "PFX", ctx_done)

    assert served == 2  # wt-a, wt-b
    assert not (reg / "wt-gone").exists()  # pruned
    for sess in ("wt-a", "wt-b"):
        assert (sess, "@aw_updater", "TOK") in calls  # won the election
        assert (sess, "@aw_updater_prefix", "PFX") in calls
        assert (sess, "@aw_ctx", "CTX") in calls  # identity once
        assert (sess, "@aw_seg", "SEG") in calls  # disposition
    assert ctx_done == {"wt-a", "wt-b"}
    # no work for the gone or non-wt sessions
    assert not any(s in ("wt-gone", "other") for s, _, _ in calls)


def test_sweep_reuses_project_config_cache_for_served_sessions(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    m._register_session_for_monitor("wt-b", "/w/b")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1, "wt-b": 1})

    def _activate(path, *, force=False):
        m.cfg.set_active_project("proj")

    monkeypatch.setattr(m, "_activate_project_for_path", _activate)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_monitor_maybe_trigger_handoff_cutover", lambda *a, **k: None)
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **k: 0)
    monkeypatch.setattr(m.tracking, "list_records", lambda *a, **k: [])
    calls = _capture_set(monkeypatch)

    loads = {"n": 0}

    def _load_config_once(*args, **kwargs):
        loads["n"] += 1
        return types.SimpleNamespace(machine="host")

    def _render_segment(*args, **kwargs):
        m.cfg.load_config(include_control_plane_related_pr=False)
        return "SEG"

    cache_sessions: dict[str, m.cfg.ConfigCacheSession] = {}

    def _cache_for_project(project: str):
        return cache_sessions.setdefault(project, m.cfg.ConfigCacheSession(ttl=60))

    monkeypatch.setattr(m.cfg, "_load_config_uncached", _load_config_once)
    monkeypatch.setattr(m, "_render_status_segment", _render_segment)
    from agent_worktrees import config_cache

    clock = {"t": 1000.0}
    monkeypatch.setattr(config_cache.time, "monotonic", lambda: clock["t"])

    def _sweep():
        return m._monitor_sweep(
            "tmux", "TOK", "PFX", set(), config_cache_for_project=_cache_for_project,
        )

    assert _sweep() == 2
    assert loads["n"] == 1
    assert ("wt-a", "@aw_seg", "SEG") in calls
    assert ("wt-b", "@aw_seg", "SEG") in calls

    clock["t"] += 30  # next sweep, still inside the 60 s TTL: same cache, no reload
    assert _sweep() == 2
    assert loads["n"] == 1

    clock["t"] += 61  # past the TTL: the next sweep reloads once
    assert _sweep() == 2
    assert loads["n"] == 2


def test_sweep_unions_managed_mux_cache_into_served_sessions(tmp_path, monkeypatch):
    """Step 4: Manager-owned sessions join the served set via the managed-mux
    cache, so reconciliation sees the same union the writer loop serves."""
    from agent_worktrees import mux_link

    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "unmanaged",
            active_project=lambda: "unmanaged",
            tracking_dir=lambda: tmp_path,
        ),
    )
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    calls = _capture_set(monkeypatch)
    observed: list[set[str]] = []
    pane_observed: list[tuple[str, str]] = []
    pushed = []
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda *a, **k: "wt-manager-owned")
    monkeypatch.setattr(
        m,
        "_publish_managed_session_status",
        lambda **kwargs: pushed.append(kwargs)
        or (
            {"handled": True, "applied": True, "context_published": True}
            if kwargs["session_name"] == "wt-manager-owned"
            else None
        ),
        raising=False,
    )

    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-manager-owned",
            "worktree_path": "/w/managed",
            "mux_session": "wt-manager-owned",
            "mapping_revision": 1,
            "live": True,
        }
    )

    served = m._monitor_sweep(
        "tmux",
        "T",
        "P",
        set(),
        catalog_observer=observed.append,
        pane_observer=lambda session, path: pane_observed.append((session, path)),
        managed_mux_cache=cache,
    )

    assert served == 2
    assert observed == [{"wt-a", "wt-manager-owned"}]
    assert sorted(pane_observed) == [("wt-a", "/w/a"), ("wt-manager-owned", "/w/managed")]
    assert len(pushed) == 2
    assert any(
        item["session_name"] == "wt-manager-owned" and item["project"] == "proj"
        for item in pushed
    )
    assert not any(sess == "wt-manager-owned" for sess, _, _ in calls)


def test_sweep_without_managed_mux_cache_observes_only_the_direct_scan(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    _capture_set(monkeypatch)
    observed: list[set[str]] = []

    m._monitor_sweep("tmux", "T", "P", set(), catalog_observer=observed.append)

    assert observed == [{"wt-a"}]  # unchanged when managed_mux_cache is None


def test_sweep_routes_manager_owned_status_through_mux_status_v1(tmp_path, monkeypatch):
    from agent_worktrees import mux_link

    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "proj",
            active_project=lambda: "proj",
            tracking_dir=lambda: tmp_path,
        ),
    )
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda *a, **k: "wt-a")
    direct_calls = _capture_set(monkeypatch)
    pushed = []
    monkeypatch.setattr(
        m,
        "_publish_managed_session_status",
        lambda **kwargs: pushed.append(kwargs) or {"handled": True, "applied": True, "context_published": True},
        raising=False,
    )

    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-a",
            "worktree_path": "/w/a",
            "mux_session": "wt-a",
            "mapping_revision": 1,
            "live": True,
        }
    )

    ctx_done: set[str] = set()
    served = m._monitor_sweep(
        "tmux",
        "TOK",
        "PFX",
        ctx_done,
        managed_mux_cache=cache,
    )

    assert served == 1
    assert direct_calls == []
    assert len(pushed) == 1
    assert pushed[0]["project"] == "proj"
    assert pushed[0]["session_name"] == "wt-a"
    assert pushed[0]["values"] == {
        "@aw_updater": "TOK",
        "@aw_updater_prefix": "PFX",
        "@aw_ctx": "CTX",
        "@aw_seg": "SEG",
    }
    assert "wt-a" in ctx_done


def test_sweep_prunes_manager_owned_registry_entries_from_the_unmanaged_lane(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    from agent_worktrees import mux_link

    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-managed", "/w/managed")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-managed": 1})
    monkeypatch.setattr(
        m,
        "_monitor_mux_set",
        lambda *args, **kwargs: pytest.fail("manager-owned sessions must not fall back to direct mux writes"),
    )
    pushed = []
    monkeypatch.setattr(
        m,
        "_publish_managed_session_status",
        lambda **kwargs: pushed.append(kwargs) or {"handled": True, "applied": True, "context_published": True},
        raising=False,
    )
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-managed",
            "worktree_path": "/w/managed",
            "mux_session": "wt-managed",
            "mapping_revision": 1,
            "live": True,
        }
    )
    observed: list[set[str]] = []

    served = m._monitor_sweep(
        "tmux",
        "TOK",
        "PFX",
        set(),
        catalog_observer=observed.append,
        managed_mux_cache=cache,
    )

    assert served == 1
    assert observed == [{"wt-managed"}]
    assert not (reg / "wt-managed").exists()
    assert len(pushed) == 1
    assert pushed[0]["session_name"] == "wt-managed"


def test_sweep_treats_stale_managed_cache_entries_as_unmanaged_again(tmp_path, monkeypatch):
    """A cache entry older than the freshness window no longer proves
    Manager ownership, so the direct lane resumes instead of freezing."""
    from agent_worktrees import mux_link

    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "proj",
            active_project=lambda: "proj",
            tracking_dir=lambda: tmp_path,
        ),
    )
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_publish_managed_session_status", lambda **kwargs: None, raising=False)
    direct_calls = _capture_set(monkeypatch)
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-a",
            "worktree_path": "/w/a",
            "mux_session": "wt-a",
            "mapping_revision": 1,
            "live": True,
        }
    )
    for entry in cache._entries.values():
        entry["received_at"] = time.time() - mux_link.MAPPING_STALE_AFTER_SECONDS - 1

    served = m._monitor_sweep(
        "tmux",
        "TOK",
        "PFX",
        set(),
        managed_mux_cache=cache,
    )

    assert served == 1
    assert ("wt-a", "@aw_updater", "TOK") in direct_calls


def test_sweep_keeps_unmanaged_sessions_on_the_direct_writer_path(tmp_path, monkeypatch):
    """Zero-provider fallback: unmanaged sessions stay on the direct writer."""
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "proj",
            active_project=lambda: "proj",
            tracking_dir=lambda: tmp_path,
        ),
    )
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    direct_calls = _capture_set(monkeypatch)
    monkeypatch.setattr(m, "_publish_managed_session_status", lambda **kwargs: None, raising=False)

    served = m._monitor_sweep(
        "tmux",
        "TOK",
        "PFX",
        set(),
    )

    assert served == 1
    assert ("wt-a", "@aw_updater", "TOK") in direct_calls
    assert ("wt-a", "@aw_seg", "SEG") in direct_calls


def test_sweep_serves_manager_owned_sessions_without_direct_mux_scan(tmp_path, monkeypatch):
    """Step 4: Manager-owned sessions remain visible to session-catalog and
    pane-reaper observers even when their source is only the managed cache."""
    from agent_worktrees import mux_link

    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "proj",
            active_project=lambda: "proj",
            tracking_dir=lambda: tmp_path,
        ),
    )
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda *a, **k: "wt-manager-owned")
    monkeypatch.setattr(
        m,
        "_monitor_mux_set",
        lambda *args, **kwargs: pytest.fail("manager-owned sessions should not fall back to direct mux writes"),
    )
    monkeypatch.setattr(
        m,
        "_publish_managed_session_status",
        lambda **kwargs: {"handled": True, "applied": True, "context_published": True},
        raising=False,
    )
    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-manager-owned",
            "worktree_path": "/w/managed",
            "mux_session": "wt-manager-owned",
            "mapping_revision": 1,
            "live": True,
        }
    )
    observed: list[set[str]] = []
    pane_observed: list[tuple[str, str]] = []

    served = m._monitor_sweep(
        None,  # no mux_bin discovered on this host
        "T",
        "P",
        set(),
        catalog_observer=observed.append,
        pane_observer=lambda session, path: pane_observed.append((session, path)),
        managed_mux_cache=cache,
    )

    assert served == 1
    assert observed == []
    assert pane_observed == [("wt-manager-owned", "/w/managed")]


def test_sweep_invalidates_managed_cache_only_incarnation(tmp_path, monkeypatch):
    from agent_worktrees import mux_link

    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "proj",
            active_project=lambda: "proj",
            tracking_dir=lambda: tmp_path,
        ),
    )
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda *a, **k: "wt-manager-owned")
    published_snapshots = []
    monkeypatch.setattr(
        m,
        "_publish_managed_session_status",
        lambda **kwargs: published_snapshots.append(dict(kwargs["published"]))
        or {"handled": True, "applied": True, "context_published": True},
        raising=False,
    )

    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-manager-owned",
            "worktree_path": "/w/managed",
            "mux_session": "wt-manager-owned",
            "session_incarnation": "sess:2",
            "mapping_revision": 2,
            "live": True,
        }
    )
    ctx_done = {"wt-manager-owned"}
    published = {
        ("wt-manager-owned", "@aw_ctx"): "stale-ctx",
        ("wt-manager-owned", "@aw_seg"): "stale-seg",
    }
    incarnations = {"wt-manager-owned": "sess:1"}

    served = m._monitor_sweep(
        None,
        "T",
        "P",
        ctx_done,
        published=published,
        incarnations=incarnations,
        managed_mux_cache=cache,
    )

    assert served == 1
    assert published_snapshots == [{}]
    assert "wt-manager-owned" in ctx_done
    assert incarnations["wt-manager-owned"] == "sess:2"


def test_sweep_uses_direct_incarnation_when_managed_entry_omits_it(tmp_path, monkeypatch):
    from agent_worktrees import mux_link

    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "proj",
            active_project=lambda: "proj",
            tracking_dir=lambda: tmp_path,
        ),
    )
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-manager-owned": (1, "sess:2")})
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda *a, **k: "wt-manager-owned")
    published_snapshots = []
    monkeypatch.setattr(
        m,
        "_publish_managed_session_status",
        lambda **kwargs: published_snapshots.append(dict(kwargs["published"]))
        or {"handled": True, "applied": True, "context_published": True},
        raising=False,
    )

    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj",
            "worktree_id": "wt-manager-owned",
            "worktree_path": "/w/managed",
            "mux_session": "wt-manager-owned",
            "mapping_revision": 2,
            "live": True,
        }
    )
    ctx_done = {"wt-manager-owned"}
    published = {
        ("wt-manager-owned", "@aw_ctx"): "stale-ctx",
        ("wt-manager-owned", "@aw_seg"): "stale-seg",
    }
    incarnations = {"wt-manager-owned": "sess:1"}

    served = m._monitor_sweep(
        "tmux",
        "T",
        "P",
        ctx_done,
        published=published,
        incarnations=incarnations,
        managed_mux_cache=cache,
    )

    assert served == 1
    assert published_snapshots == [{}]
    assert incarnations["wt-manager-owned"] == "sess:2"


def test_sweep_prefers_live_managed_entry_when_session_names_collide(tmp_path, monkeypatch):
    from agent_worktrees import mux_link

    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    monkeypatch.setattr(
        m,
        "cfg",
        types.SimpleNamespace(
            set_active_project=lambda *_a, **_k: None,
            project_name=lambda: "proj-live",
            active_project=lambda: "proj-live",
            tracking_dir=lambda: tmp_path,
        ),
    )
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m.tracking, "find_worktree_id_by_cwd", lambda path, **_k: "wt-live" if path == "/w/live" else "wt-dead")
    pushed = []
    monkeypatch.setattr(
        m,
        "_publish_managed_session_status",
        lambda **kwargs: pushed.append((kwargs["project"], kwargs["path"], kwargs["session_name"]))
        or {"handled": True, "applied": True, "context_published": True},
        raising=False,
    )

    cache = mux_link.ManagedMuxCache()
    cache.apply_observation(
        {
            "project": "proj-live",
            "worktree_id": "wt-live",
            "worktree_path": "/w/live",
            "mux_session": "wt-shared",
            "mapping_revision": 1,
            "live": True,
        }
    )
    cache.apply_observation(
        {
            "project": "proj-dead",
            "worktree_id": "wt-dead",
            "worktree_path": "/w/dead",
            "mux_session": "wt-shared",
            "mapping_revision": 5,
            "live": False,
        }
    )
    pane_observed = []

    served = m._monitor_sweep(
        None,
        "T",
        "P",
        set(),
        pane_observer=lambda session, path: pane_observed.append((session, path)),
        managed_mux_cache=cache,
    )

    assert served == 1
    assert pane_observed == [("wt-shared", "/w/live")]
    assert pushed == [("proj-live", "/w/live", "wt-shared")]


def test_sweep_ctx_rendered_once(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    calls = _capture_set(monkeypatch)

    ctx_done: set[str] = set()
    m._monitor_sweep("tmux", "T", "P", ctx_done)
    m._monitor_sweep("tmux", "T", "P", ctx_done)  # second pass

    ctx_sets = [c for c in calls if c[1] == "@aw_ctx"]
    seg_sets = [c for c in calls if c[1] == "@aw_seg"]
    assert len(ctx_sets) == 1  # identity: once
    assert len(seg_sets) == 2  # disposition: every pass


def test_sweep_reuses_segment_and_skips_unchanged_mux_values(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    calls = _capture_set(monkeypatch)

    class Cache:
        count = 0

        def get(self, path):
            self.count += 1
            return "SEG"

    cache = Cache()
    published = {}
    ctx_done = set()
    m._monitor_sweep("tmux", "T", "P", ctx_done, segment_cache=cache, published=published)
    m._monitor_sweep("tmux", "T", "P", ctx_done, segment_cache=cache, published=published)

    assert cache.count == 2
    assert [call for call in calls if call[1] == "@aw_seg"] == [("wt-a", "@aw_seg", "SEG")]


def test_sweep_reuses_session_project_resolution(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    monkeypatch.setattr(m, "_monitor_mux_set", lambda *a, **k: True)
    activations = []

    def activate(path, *, force):
        activations.append((path, force))
        m.cfg.set_active_project("repo-a")

    monkeypatch.setattr(m, "_activate_project_for_path", activate)

    class Cache:
        def get(self, path):
            assert m.cfg.active_project() == "repo-a"
            return "SEG"

    prior = m.cfg.active_project()
    projects = {}
    try:
        m._monitor_sweep("tmux", "T", "P", set(), segment_cache=Cache(), session_projects=projects)
        m._monitor_sweep("tmux", "T", "P", set(), segment_cache=Cache(), session_projects=projects)
    finally:
        m.cfg.set_active_project(prior)

    assert activations == [("/w/a", True)]
    assert projects == {m.os.path.normcase(m.os.path.realpath("/w/a")): "repo-a"}


def test_sweep_skips_invalid_paths_when_pruning_project_cache(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    reg.mkdir()
    (reg / "wt-a").write_text("bad\0path", encoding="utf-8")
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_monitor_mux_set", lambda *a, **k: True)
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    projects = {"stale": "repo-a"}

    assert (
        m._monitor_sweep(
            "tmux", "T", "P", set(), segment_cache=object(), session_projects=projects
        )
        == 1
    )
    assert projects == {}


def test_sweep_retries_failed_mux_publish(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    attempts = []

    def publish(mux, session, option, value):
        attempts.append((session, option, value))
        return len(attempts) > 4

    monkeypatch.setattr(m, "_monitor_mux_set", publish)
    published = {}
    m._monitor_sweep("tmux", "T", "P", set(), published=published)
    assert published == {}
    m._monitor_sweep("tmux", "T", "P", set(), published=published)
    assert len(attempts) == 8


def test_sweep_republishes_for_recreated_mux_session(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    incarnation = ["100"]
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": (1, incarnation[0])})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    calls = _capture_set(monkeypatch)
    published = {}
    incarnations = {}
    ctx_done = set()

    m._monitor_sweep("tmux", "T", "P", ctx_done, published=published, incarnations=incarnations)
    incarnation[0] = "200"
    m._monitor_sweep("tmux", "T", "P", ctx_done, published=published, incarnations=incarnations)

    assert len([call for call in calls if call[1] == "@aw_seg"]) == 2
    assert len([call for call in calls if call[1] == "@aw_ctx"]) == 2


def test_segment_cache_throttles_and_invalidates(monkeypatch):
    renders = []
    lookups = []
    monkeypatch.setattr(
        m,
        "_find_record_for_path",
        lambda path: lookups.append(path) or None,
    )
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_render_status_segment",
        lambda path, **kwargs: renders.append(path) or f"SEG-{len(renders)}",
    )
    cache = m._StatusSegmentCache(ttl=60)
    assert cache.get("/w/a") == "SEG-1"
    assert cache.get("/w/a") == "SEG-1"
    cache.invalidate("/w/a/src")
    assert cache.get("/w/a") == "SEG-2"
    assert renders == ["/w/a", "/w/a"]
    assert lookups == ["/w/a", "/w/a"]


def test_segment_cache_reuses_canonical_target_before_record_lookup(monkeypatch):
    lookups = []
    renders = []
    monkeypatch.setattr(
        m,
        "_find_record_for_path",
        lambda path: lookups.append(path) or types.SimpleNamespace(worktree_path="/w/a"),
    )
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_render_status_segment",
        lambda path, **kwargs: renders.append(path) or f"SEG-{len(renders)}",
    )
    cache = m._StatusSegmentCache(ttl=60)

    assert cache.get("/w/a/src") == "SEG-1"
    assert cache.get("/w/a/src") == "SEG-1"
    assert cache.get("/w/a") == "SEG-1"
    assert lookups == ["/w/a/src"]

    cache.invalidate("/w/a/src/file.py")
    assert cache.get("/w/a/src") == "SEG-2"
    assert lookups == ["/w/a/src", "/w/a/src"]
    assert renders == ["/w/a", "/w/a"]


def test_segment_cache_get_works_without_full_command_surface_loaded(monkeypatch):
    """Real-bug regression (#4341): resident status-monitor's fast-dispatch
    path never calls `_load_full_command_surface()`, so the bare
    `_find_record_for_path`/`_render_status_segment` globals this method used
    to reference directly were never bound there -- a `NameError` silently
    swallowed by `_monitor_sweep`'s blanket `except Exception: pass`,
    rendering every managed session's status segment "" forever. This suite's
    own conftest always pre-loads the full surface (unlike production), which
    is exactly why this went unnoticed -- delete the bare globals to
    reproduce the real unloaded state, and confirm `.get()` still resolves
    correctly via the `status_bar_cli` import + `_self_override`."""
    from agent_worktrees import status_bar_cli

    monkeypatch.delattr(m, "_find_record_for_path", raising=False)
    monkeypatch.delattr(m, "_render_status_segment", raising=False)
    monkeypatch.setattr(status_bar_cli, "_find_record_for_path", lambda path: None)
    monkeypatch.setattr(
        status_bar_cli, "_render_status_segment", lambda path, **kwargs: "REAL-SEGMENT"
    )
    cache = m._StatusSegmentCache(ttl=60)
    assert cache.get("/w/unloaded") == "REAL-SEGMENT"


def test_resident_hook_scopes_and_restores_project(monkeypatch):
    prior = m.cfg.active_project()
    seen = []

    class Policy:
        def pre(self, payload):
            seen.append(m.cfg.active_project())
            return {}

    monkeypatch.setattr(
        m,
        "_activate_project_for_path",
        lambda cwd, force: m.cfg.set_active_project("request-project"),
    )
    try:
        m.cfg.set_active_project("prior-project")
        result = m._resident_hook_decision(
            "preToolUse",
            {"cwd": "/w/a", "toolName": "view"},
            segment_cache=object(),
            policy=Policy(),
        )
        assert result == {}
        assert seen == ["request-project"]
        assert m.cfg.active_project() == "prior-project"
    finally:
        m.cfg.set_active_project(prior)


def test_hook_mutation_targets_are_target_aware():
    class Guard:
        _WRITE_VERBS = re.compile(r"Set-Content|git\s+commit", re.IGNORECASE)

    class Client:
        @staticmethod
        def _load_sibling(name):
            return Guard

    policy = m._ResidentHookPolicy(Client())
    assert policy.mutation_targets(
        {
            "toolName": "edit",
            "cwd": "/w/a",
            "toolArgs": {"path": "/w/b/file.py"},
        }
    ) == ["/w/b/file.py"]
    assert (
        policy.mutation_targets(
            {
                "toolName": "powershell",
                "cwd": "/w/a",
                "toolArgs": {"command": "git status --short"},
            }
        )
        == []
    )
    assert (
        policy.mutation_targets(
            {
                "toolName": "powershell",
                "cwd": "/w/a",
                "toolArgs": {"command": "git commit -m test"},
            }
        )
        is None
    )


def test_anchor_policy_cache_reloads_when_registry_changes(tmp_path, monkeypatch):
    registry = tmp_path / "repos.yaml"
    registry.write_text("one", encoding="utf-8")
    selected_root = tmp_path / "selected"
    seen_roots = []
    monkeypatch.setattr(
        "agent_worktrees.registry_paths.registry_root",
        lambda: selected_root,
    )

    class Anchor:
        calls = 0

        @staticmethod
        def _repos_yaml(root):
            seen_roots.append(root)
            return registry

        @classmethod
        def load_worktree_anchors(cls, root):
            seen_roots.append(root)
            cls.calls += 1
            return [{"name": str(cls.calls), "path": "/repo"}]

    class Client:
        @staticmethod
        def _load_sibling(name):
            return Anchor

    policy = m._ResidentHookPolicy(Client())
    assert policy.anchors()[0]["name"] == "1"
    assert policy.anchors()[0]["name"] == "1"
    registry.write_text("two-two", encoding="utf-8")
    assert policy.anchors()[0]["name"] == "2"
    assert seen_roots and set(seen_roots) == {selected_root}


def test_load_hook_client_module_prefers_selected_runtime_bin(tmp_path, monkeypatch):
    runtime = tmp_path / "cell-runtime"
    target = runtime / "bin" / "hook_client.py"
    target.parent.mkdir(parents=True)
    target.write_text("SELECTED = 'cell-runtime'\n", encoding="utf-8")
    monkeypatch.setattr(m.cfg, "install_dir", lambda: runtime)

    module = m._load_hook_client_module()

    assert module is not None
    assert module.SELECTED == "cell-runtime"


def test_resident_agent_bridge_policy_denies_guarded_write(tmp_path, monkeypatch):
    from agent_worktrees import related, repos

    control = tmp_path / "control"
    guarded = tmp_path / "guarded"
    (control / ".git").mkdir(parents=True)
    (guarded / ".git").mkdir(parents=True)
    entry = types.SimpleNamespace(
        name="guarded",
        delegate="agent-bridge",
        locus=types.SimpleNamespace(
            preferred="machine:devbox",
            machines=["devbox"],
        ),
    )
    monkeypatch.setattr(
        m,
        "_related_config_source_anchors",
        lambda root, **_kwargs: [root],
    )
    monkeypatch.setattr(related, "list_related_grafted", lambda anchors: [entry])
    monkeypatch.setattr(repos, "resolve_path", lambda name: str(guarded))

    hook_client = m._load_hook_client_module()
    assert hook_client is not None
    policy = m._ResidentHookPolicy(hook_client)
    monkeypatch.setattr(policy, "anchors", lambda: [])
    decision = policy.pre(
        {
            "toolName": "edit",
            "cwd": str(control),
            "toolArgs": {"path": str(guarded / "file.py")},
        }
    )
    assert decision["permissionDecision"] == "deny"
    assert "agent-bridge send devbox" in decision["permissionDecisionReason"]


def test_sweep_warms_list_cache_once_per_project(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/p1/a")
    m._register_session_for_monitor("wt-b", "/p1/b")
    m._register_session_for_monitor("wt-c", "/p2/c")
    monkeypatch.setattr(
        m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1, "wt-b": 1, "wt-c": 1}
    )

    project_by_path = {"/p1/a": "p1", "/p1/b": "p1", "/p2/c": "p2"}

    def _activate(path, *, force):
        m.cfg.set_active_project(project_by_path[path])

    warmed: list[str] = []
    monkeypatch.setattr(m, "_activate_project_for_path", _activate)
    monkeypatch.setattr(
        m,
        "_warm_list_cache_for_active_project",
        lambda **kw: warmed.append(m.cfg.project_name()) or 1,
    )
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    _capture_set(monkeypatch)

    assert m._monitor_sweep("tmux", "T", "P", set()) == 3
    assert sorted(warmed) == ["p1", "p2"]


def test_sweep_publishes_each_served_session_to_pane_reconciler(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    m._register_session_for_monitor("wt-b", "/w/b")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux: {"wt-a": 1, "wt-b": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    _capture_set(monkeypatch)
    observed: list[tuple[str, str]] = []

    assert (
        m._monitor_sweep(
            "tmux",
            "T",
            "P",
            set(),
            pane_observer=lambda session, path: observed.append((session, path)),
        )
        == 2
    )
    assert sorted(observed) == [("wt-a", "/w/a"), ("wt-b", "/w/b")]


def test_sweep_picker_root_keeps_project_warm_without_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: tmp_path / "reg")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {})
    warmed: list[str] = []
    monkeypatch.setattr(
        m,
        "_warm_list_cache_for_active_project",
        lambda **kw: warmed.append(m.cfg.project_name()) or 1,
    )

    served = m._monitor_sweep("tmux", "T", "P", set(), picker_projects={"picker-project"})

    assert served == 0
    assert warmed == ["picker-project"]


def test_sweep_without_mux_preserves_registered_sessions(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)

    assert m._monitor_sweep(None, "T", "P", set()) == 0
    assert (reg / "wt-a").exists()


def test_sweep_transient_mux_failure_holds(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    # None == the mux couldn't be enumerated -> transient; must NOT prune/exit.
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: None)
    calls = _capture_set(monkeypatch)

    assert m._monitor_sweep("tmux", "T", "P", set()) == -1
    assert calls == []
    assert (reg / "wt-a").exists()  # registry untouched


def _wire_monitor_handoff_session(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    monkeypatch.setattr(
        m,
        "_find_record_for_path",
        lambda path: types.SimpleNamespace(
            worktree_id="a",
            handoffs=[
                types.SimpleNamespace(
                    token="handoff-1",
                    predecessor="session-1",
                    candidate=None,
                    successor=None,
                    live_cutover=True,
                )
            ],
            pending_handoffs=[
                types.SimpleNamespace(
                    token="handoff-1",
                    predecessor="session-1",
                    candidate=None,
                    successor=None,
                    live_cutover=True,
                )
            ],
        ),
    )
    _capture_set(monkeypatch)


def test_monitor_pending_handoff_request_returns_actionable_request(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_requested":
            return [
                {
                    "handoff_id": "handoff-1",
                    "session_id": "session-1",
                    "session_state": r"C:\state\handoff-request.json",
                    "storage": "file",
                    "predecessor_pid": "77",
                }
            ]
        if event == "handoff_cutover_spawn":
            return []
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)
    monkeypatch.setattr(
        m,
        "_monitor_read_session_state_handoff",
        lambda path: {
            "handoffId": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree": "a",
            "storage": "file",
            "consumed": False,
        },
    )
    monkeypatch.setattr(m.locks, "process_start_time", lambda pid: "old-process")
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
                live_cutover=True,
            )
        ],
    )

    assert m._monitor_pending_handoff_request(record) == {
        "token": "handoff-1",
        "seed": "HANDOFF_SEED",
        "worktree_id": "a",
        "predecessor_session_id": "session-1",
        "predecessor_pid": 77,
        "predecessor_start_time": "old-process",
        "session_state_path": r"C:\state\handoff-request.json",
        "storage": "file",
        "claim_path": str(tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json"),
    }


def test_monitor_pending_handoff_request_ignores_entry_without_live_cutover_armed(
    tmp_path, monkeypatch,
):
    """A handoff recorded purely for lineage tracking (e.g. context-handoff's
    manual-mode consume backstop, or any other caller that records a handoff
    without intending an automatic spawn) must never be treated as an
    auto-spawn candidate merely because the entry exists. This is the
    replacement for PR #3041's original "existence is the gate" fix -- see
    SessionHandoff.live_cutover's own docstring."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_requested":
            return [
                {
                    "handoff_id": "handoff-1",
                    "session_id": "session-1",
                    "session_state": r"C:\state\handoff-request.json",
                    "storage": "file",
                }
            ]
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)

    def _fail_if_claimed(*a, **k):
        raise AssertionError(
            "must never attempt a claim for a handoff not armed for live-cutover"
        )

    monkeypatch.setattr(m, "_monitor_claim_handoff_cutover", _fail_if_claimed)
    monkeypatch.setattr(
        m,
        "_monitor_read_session_state_handoff",
        lambda path: {
            "handoffId": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree": "a",
            "storage": "file",
            "consumed": False,
        },
    )
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
                live_cutover=False,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
                live_cutover=False,
            )
        ],
    )

    assert m._monitor_pending_handoff_request(record) is None


def test_monitor_pending_handoff_request_skips_already_claimed(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_requested":
            return [
                {
                    "handoff_id": "handoff-1",
                    "session_id": "session-1",
                    "session_state": r"C:\state\handoff-request.json",
                }
            ]
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_monitor_read_session_state_handoff",
        lambda path: {
            "handoffId": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree": "a",
            "consumed": False,
        },
    )
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
                live_cutover=True,
            )
        ],
    )

    assert m._monitor_pending_handoff_request(record) is not None
    assert m._monitor_pending_handoff_request(record) is None


def test_monitor_pending_handoff_request_tries_only_once_per_token(tmp_path, monkeypatch):
    """A pending handoff already spawned once -- confirmed or not -- must never
    be spawned again. Before this fix, the picker only skipped a token once a
    ``candidate``/``successor`` was actually confirmed; a spawn attempt whose
    successor never finished starting (so no candidate was ever recorded) left
    the token looking untouched on every later sweep, so a stale/expired claim
    lock (the concurrency mutex, not an outcome record) let the monitor spawn
    an unbounded pile of successor panes for the same token. Confirmed live on
    worktree b431: 20+ stacked successor panes over an hour, none ever
    reaching a confirmed candidate."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(m.handoff_trace, "read_trace", lambda *a, **k: [])

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_requested":
            return [
                {
                    "handoff_id": "handoff-1",
                    "session_id": "session-1",
                    "session_state": r"C:\state\handoff-request.json",
                }
            ]
        if event == "handoff_cutover_spawn":
            # A prior attempt was already logged for this token -- it never
            # confirmed a candidate/successor, but it happened.
            return [{"handoff_token": "handoff-1"}]
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)

    def _fail_if_claimed(*a, **k):
        raise AssertionError("must not re-claim a token already spawned once")

    monkeypatch.setattr(m, "_monitor_claim_handoff_cutover", _fail_if_claimed)
    monkeypatch.setattr(
        m,
        "_monitor_read_session_state_handoff",
        lambda path: {
            "handoffId": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree": "a",
            "consumed": False,
        },
    )
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
            )
        ],
    )

    assert m._monitor_pending_handoff_request(record) is None


def test_monitor_pending_handoff_request_counts_failed_spawn_as_attempted(
    tmp_path, monkeypatch,
):
    """A spawn that failed outright (mux/pane-create error, no pane ever
    created) only ever logs ``handoff_successor_spawn_started`` +
    ``handoff_successor_spawn_failed`` -- ``handoff_cutover_spawn`` (the
    success event) never fires. Counting only the success event would leave
    such a token with no attempted-marker at all, letting it retry unbounded
    exactly like the confirmed-candidate gap this whole gate exists to close."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(m.handoff_trace, "read_trace", lambda *a, **k: [])

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_requested":
            return [
                {
                    "handoff_id": "handoff-1",
                    "session_id": "session-1",
                    "session_state": r"C:\state\handoff-request.json",
                }
            ]
        if event == "handoff_successor_spawn_started":
            return [{"handoff_token": "handoff-1"}]
        if event == "handoff_cutover_spawn":
            return []  # the spawn never succeeded -- no success event at all
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)

    def _fail_if_claimed(*a, **k):
        raise AssertionError("must not re-claim a token whose spawn already failed")

    monkeypatch.setattr(m, "_monitor_claim_handoff_cutover", _fail_if_claimed)
    monkeypatch.setattr(
        m,
        "_monitor_read_session_state_handoff",
        lambda path: {
            "handoffId": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree": "a",
            "consumed": False,
        },
    )
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1", predecessor="session-1", candidate=None, successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1", predecessor="session-1", candidate=None, successor=None,
            )
        ],
    )

    assert m._monitor_pending_handoff_request(record) is None


def test_monitor_pending_handoff_request_honors_durable_trace_after_log_eviction(
    tmp_path, monkeypatch,
):
    """The rolling ``activity.jsonl`` log is bounded (age + a 64-event read
    cap); a token whose spawn attempt aged out of it must still be caught via
    the durable per-project trace store, exactly like
    ``_pending_handoff_retire_requests`` already relies on for the retire
    side. Forcing every ``activity.read_events`` call to return empty here
    simulates that eviction -- only ``handoff_trace.read_trace`` carries the
    prior attempt."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(m.cfg, "active_project", lambda: "repo-a")
    monkeypatch.setattr(
        m.handoff_trace,
        "read_trace",
        lambda project, worktree_id: [{
            "event": "handoff_successor_spawn_started",
            "handoff_token": "handoff-1",
        }],
    )

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_requested":
            return [
                {
                    "handoff_id": "handoff-1",
                    "session_id": "session-1",
                    "session_state": r"C:\state\handoff-request.json",
                }
            ]
        return []  # the rolling log has evicted every spawn-attempt event

    monkeypatch.setattr(m.activity, "read_events", read_events)

    def _fail_if_claimed(*a, **k):
        raise AssertionError(
            "must not re-claim a token whose only attempted-record is the "
            "durable trace store"
        )

    monkeypatch.setattr(m, "_monitor_claim_handoff_cutover", _fail_if_claimed)
    monkeypatch.setattr(
        m,
        "_monitor_read_session_state_handoff",
        lambda path: {
            "handoffId": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree": "a",
            "consumed": False,
        },
    )
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1", predecessor="session-1", candidate=None, successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1", predecessor="session-1", candidate=None, successor=None,
            )
        ],
    )

    assert m._monitor_pending_handoff_request(record) is None


def test_monitor_claim_handoff_cutover_acquires_fresh_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    logged = []
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: logged.append((a, k)))
    monkeypatch.setattr(m.locks, "process_start_time", lambda pid: "live-start")

    request = {
        "token": "handoff-1",
        "worktree_id": "a",
        "predecessor_session_id": "session-1",
    }
    claim = m._monitor_claim_handoff_cutover(request)
    claim_path = tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json"

    assert claim == {
        "ok": True,
        "claimed": True,
        "path": str(claim_path),
    }
    assert claim_path.exists()
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    assert payload["pid"] == m.os.getpid()
    assert payload["start_time"] == "live-start"
    assert logged[0][1]["outcome"] == "acquired"


def test_monitor_claim_handoff_cutover_keeps_live_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    logged = []
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: logged.append((a, k)))
    monkeypatch.setattr(m.locks, "process_start_time", lambda pid: "live-start")

    request = {
        "token": "handoff-1",
        "worktree_id": "a",
        "predecessor_session_id": "session-1",
    }
    first = m._monitor_claim_handoff_cutover(request)
    second = m._monitor_claim_handoff_cutover(request)

    assert first == {
        "ok": True,
        "claimed": True,
        "path": str(tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json"),
    }
    assert second == {
        "ok": True,
        "claimed": False,
        "path": str(tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json"),
    }
    assert (tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json").exists()
    assert logged[0][1]["outcome"] == "acquired"
    assert logged[1][1]["outcome"] == "already-claimed"


def test_monitor_claim_handoff_cutover_reclaims_dead_pid_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    logged = []
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: logged.append((a, k)))
    monkeypatch.setattr(
        m.locks,
        "pid_alive",
        lambda pid: False if pid == 777 else True,
    )
    monkeypatch.setattr(
        m.locks,
        "process_start_time",
        lambda pid: "new-start" if pid == m.os.getpid() else "old-start",
    )
    claim_path = tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json"
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "kind": "handoff-cutover-claim",
                "token": "handoff-1",
                "worktree_id": "a",
                "session_id": "session-1",
                "pid": 777,
                "start_time": "old-start",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )

    claim = m._monitor_claim_handoff_cutover(
        {
            "token": "handoff-1",
            "worktree_id": "a",
            "predecessor_session_id": "session-1",
        }
    )

    assert claim == {"ok": True, "claimed": True, "path": str(claim_path)}
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    assert payload["pid"] == m.os.getpid()
    assert payload["start_time"] == "new-start"
    assert logged[-1][1]["outcome"] == "acquired"
    assert logged[-1][1]["claim_reclaimed"] is True
    assert logged[-1][1]["stale_reason"] == "pid-gone"
    assert logged[-1][1]["prior_pid"] == 777


def test_monitor_claim_handoff_cutover_reclaims_expired_live_pid_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS", "60")
    logged = []
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: logged.append((a, k)))
    monkeypatch.setattr(m.locks, "pid_alive", lambda pid: True)
    monkeypatch.setattr(
        m.locks,
        "process_start_time",
        lambda pid: "same-start" if pid != m.os.getpid() else "new-start",
    )
    claim_path = tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json"
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "kind": "handoff-cutover-claim",
                "token": "handoff-1",
                "worktree_id": "a",
                "session_id": "session-1",
                "pid": 777,
                "start_time": "same-start",
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(minutes=10)
                ).isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )

    claim = m._monitor_claim_handoff_cutover(
        {
            "token": "handoff-1",
            "worktree_id": "a",
            "predecessor_session_id": "session-1",
        }
    )

    assert claim == {"ok": True, "claimed": True, "path": str(claim_path)}
    assert logged[-1][1]["claim_reclaimed"] is True
    assert logged[-1][1]["stale_reason"] == "age-expired"
    assert logged[-1][1]["stale_age_seconds"] >= 600


def test_reclaim_does_not_overwrite_a_newer_live_claim_when_restoring(tmp_path):
    """If the displaced content doesn't match what the staleness check
    actually inspected (a third contender republished a genuinely live claim
    at `path` in the interim), restoring must never clobber that live claim
    -- even when something *else* has since occupied `path` a second time
    before the restore itself runs."""
    path = tmp_path / "handoff-1.json"
    stale_claim = {"pid": 777, "token": "handoff-1"}
    path.write_text(json.dumps(stale_claim), encoding="utf-8")

    real_replace = os.replace
    newer_claim = {"pid": 999, "token": "handoff-1", "created_at": "now"}

    def replace_then_reoccupy(src, dst):
        real_replace(src, dst)
        # Simulate a third contender publishing a brand-new live claim at
        # `path` in the exact window between our os.replace and our
        # mismatch-driven restore attempt.
        path.write_text(json.dumps(newer_claim), encoding="utf-8")

    with patch("agent_worktrees.status_monitor_runtime.os.replace", side_effect=replace_then_reoccupy):
        ok, reclaimed, error = m._monitor_reclaim_stale_handoff_cutover_claim(
            path, {"pid": 111, "token": "handoff-1"},
            expected_stale_claim={"pid": 42, "token": "handoff-1"},  # deliberate mismatch
        )

    assert (ok, reclaimed, error) == (True, False, None)
    # The third contender's live claim must survive untouched.
    assert json.loads(path.read_text(encoding="utf-8")) == newer_claim


def test_reclaim_fails_closed_when_unreadable_claim_was_actually_live(tmp_path):
    """A staleness check that found an unreadable/torn file (claim=None,
    reason=age-expired via file mtime) must not let a reclaimer treat a
    *now-readable, valid* claim at the same path as a match -- someone else
    published a genuinely live claim there, not the torn file originally
    assessed."""
    path = tmp_path / "handoff-1.json"
    live_claim = {"pid": 999, "token": "handoff-1"}
    path.write_text(json.dumps(live_claim), encoding="utf-8")

    ok, reclaimed, error = m._monitor_reclaim_stale_handoff_cutover_claim(
        path, {"pid": 111, "token": "handoff-1"},
        expected_stale_claim=None,  # the staleness check found no parseable claim
    )

    assert (ok, reclaimed, error) == (True, False, None)
    assert json.loads(path.read_text(encoding="utf-8")) == live_claim


def test_reclaim_proceeds_when_displaced_content_matches_expected(tmp_path):
    """The ordinary, non-racing path: the displaced content matches exactly
    what the staleness check inspected -- reclaim proceeds and publishes."""
    path = tmp_path / "handoff-1.json"
    stale_claim = {"pid": 777, "token": "handoff-1"}
    path.write_text(json.dumps(stale_claim), encoding="utf-8")

    ok, reclaimed, error = m._monitor_reclaim_stale_handoff_cutover_claim(
        path, {"pid": 111, "token": "handoff-1"},
        expected_stale_claim=stale_claim,
    )

    assert (ok, reclaimed, error) == (True, True, None)
    assert json.loads(path.read_text(encoding="utf-8")) == {"pid": 111, "token": "handoff-1"}


def test_monitor_claim_handoff_cutover_stale_reclaim_is_single_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR_HANDOFF_CLAIM_STALE_SECONDS", "1")
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
    # Only the pre-seeded dead claim's pid (777) is "gone" -- the current
    # process's own pid must read alive, exactly like reality (a process is
    # never dead to itself). Mocking pid_alive to unconditionally return
    # False for *every* pid -- including the reclaiming thread's own live
    # pid -- was the actual root cause of this test's flakiness: it let a
    # slower thread's staleness check see the faster thread's already-
    # published, genuinely-live winning claim as "stale" too (pid-gone),
    # triggering a second, cascading reclaim that could steal the win.
    monkeypatch.setattr(m.locks, "pid_alive", lambda pid: pid != 777)
    monkeypatch.setattr(m.locks, "process_start_time", lambda pid: f"start-{pid}")
    claim_path = tmp_path / "status-monitor-handoffs.d" / "a" / "handoff-1.json"
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "kind": "handoff-cutover-claim",
                "token": "handoff-1",
                "worktree_id": "a",
                "session_id": "session-1",
                "pid": 777,
                "start_time": "dead-start",
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(minutes=10)
                ).isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )
    barrier = threading.Barrier(2)
    original = m._monitor_handoff_claim_staleness

    def synchronized_staleness(path, claim=None):
        info = original(path, claim)
        if info.get("stale"):
            barrier.wait(timeout=5)
        return info

    monkeypatch.setattr(m, "_monitor_handoff_claim_staleness", synchronized_staleness)
    request = {
        "token": "handoff-1",
        "worktree_id": "a",
        "predecessor_session_id": "session-1",
    }
    results = []

    def worker():
        results.append(m._monitor_claim_handoff_cutover(request))

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(results) == 2
    assert sorted(result["claimed"] for result in results) == [False, True]
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    assert payload["pid"] == m.os.getpid()


def test_sweep_does_not_retry_after_verification_timeout(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    _wire_monitor_handoff_session(tmp_path, monkeypatch)

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_requested":
            return [
                {
                    "handoff_id": "handoff-1",
                    "session_id": "session-1",
                    "session_state": r"C:\state\handoff-request.json",
                }
            ]
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_monitor_read_session_state_handoff",
        lambda path: {
            "handoffId": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree": "a",
            "consumed": False,
        },
    )
    triggered = []

    def trigger(item):
        assert m.Path(item["claim_path"]).exists()
        triggered.append(item)
        return 4, {"ok": False, "candidate_status": "session-association-timeout"}

    monkeypatch.setattr(
        m,
        "_monitor_trigger_handoff_cutover",
        trigger,
    )

    assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    assert len(triggered) == 1


def test_monitor_pending_handoff_predecessor_retire_uses_logged_predecessor_binding(
    monkeypatch,
):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m.handoff_trace, "read_trace", lambda *a, **k: [])

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_cutover_spawn":
            return [{
                "handoff_token": "handoff-1",
                "session_id": "session-1",
                "old_pane": "%9",
                "expected_mux_session": "wt-a",
                "predecessor_copilot_pid": 77,
                "predecessor_copilot_start_time": "old-process",
            }]
        if event == "handoff_predecessor_retire":
            return []
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate="successor-1",
                successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate="successor-1",
                successor=None,
            )
        ],
    )

    assert m._monitor_pending_handoff_predecessor_retire(record) == {
        "handoff_token": "handoff-1",
        "worktree_id": "a",
        "predecessor_session_id": "session-1",
        "predecessor_pid": 77,
        "predecessor_start_time": "old-process",
        "retire_pane": "%9",
        "mux_session": "wt-a",
        "successor_session_id": "successor-1",
        "retire_reason": "monitor-successor-candidate",
    }


def test_monitor_pending_handoff_predecessor_retire_retries_after_failed_attempt(
    monkeypatch,
):
    """A retire attempt that failed (e.g. "left-running" on an identity
    mismatch) must stay retryable -- only a genuinely successful retirement
    ("gone") counts as handled. Before this fix, ANY logged
    ``handoff_predecessor_retire`` event -- success or failure -- permanently
    suppressed every future attempt, silently stranding the pane forever."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m.handoff_trace, "read_trace", lambda *a, **k: [])

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_cutover_spawn":
            return [{
                "handoff_token": "handoff-1",
                "session_id": "session-1",
                "old_pane": "%9",
                "expected_mux_session": "wt-a",
                "predecessor_copilot_pid": 77,
                "predecessor_copilot_start_time": "old-process",
            }]
        if event == "handoff_predecessor_retire":
            return [{
                "handoff_token": "handoff-1",
                "outcome": "left-running",
                "method": "process-identity-mismatch",
            }]
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate="successor-1",
                successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate="successor-1",
                successor=None,
            )
        ],
    )

    assert m._monitor_pending_handoff_predecessor_retire(record) is not None


def test_monitor_pending_handoff_predecessor_retire_skips_after_success(
    monkeypatch,
):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    monkeypatch.setattr(m.handoff_trace, "read_trace", lambda *a, **k: [])

    def read_events(**kwargs):
        event = kwargs.get("event")
        if event == "handoff_cutover_spawn":
            return [{
                "handoff_token": "handoff-1",
                "session_id": "session-1",
                "old_pane": "%9",
                "expected_mux_session": "wt-a",
                "predecessor_copilot_pid": 77,
                "predecessor_copilot_start_time": "old-process",
            }]
        if event == "handoff_predecessor_retire":
            return [{"handoff_token": "handoff-1", "outcome": "gone", "method": "graceful"}]
        return []

    monkeypatch.setattr(m.activity, "read_events", read_events)
    record = types.SimpleNamespace(
        worktree_id="a",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate="successor-1",
                successor=None,
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate="successor-1",
                successor=None,
            )
        ],
    )

    assert m._monitor_pending_handoff_predecessor_retire(record) is None


def test_monitor_trigger_handoff_cutover_retires_confirmed_successor(monkeypatch):
    captured_binding_call = {}

    def binding(sid, *, expected_session_name=None):
        captured_binding_call["sid"] = sid
        captured_binding_call["expected_session_name"] = expected_session_name
        return {
            "pane_id": "%9",
            "copilot_pid": 77,
            "copilot_start_time": "old-process",
            "session_name": "wt-a",
        }

    monkeypatch.setattr(m.sessions, "mux_binding_for_session", binding)
    monkeypatch.setattr(m.sessions, "mux_session_name", lambda wt_id: f"wt-{wt_id}")
    captured = {}

    def spawn(args):
        captured["spawn"] = args
        return 0, {
            "ok": True,
            "session": "wt-a",
            "old_pane": "%9",
            "new_pane": "%10",
            "candidate_session": "successor-1",
        }

    def retire(request):
        captured["retire"] = request
        return 0, {"ok": True}

    monkeypatch.setattr(m, "_handoff_cutover_spawn_result", spawn)
    monkeypatch.setattr(m, "_monitor_retire_handoff_predecessor", retire)

    rc, response = m._monitor_trigger_handoff_cutover(
        {
            "token": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree_id": "a",
            "predecessor_session_id": "session-1",
            "predecessor_pid": 77,
            "predecessor_start_time": "old-process",
        }
    )

    assert rc == 0
    assert response["candidate_session"] == "successor-1"
    # The binding lookup is scoped to THIS worktree's mux session (never a
    # bare, unscoped session_id.startswith("wt-") match across every worktree).
    assert captured_binding_call == {"sid": "session-1", "expected_session_name": "wt-a"}
    assert captured["spawn"].old_pane == "%9"
    assert captured["spawn"].expected_copilot_pid == 77
    assert captured["spawn"].expected_copilot_start_time == "old-process"
    assert captured["retire"] == {
        "handoff_token": "handoff-1",
        "worktree_id": "a",
        "predecessor_session_id": "session-1",
        "predecessor_pid": 77,
        "predecessor_start_time": "old-process",
        "retire_pane": "%9",
        "mux_session": "wt-a",
        "successor_session_id": "successor-1",
        "retire_reason": "monitor-successor-candidate",
    }


def test_monitor_trigger_handoff_cutover_trusts_fresh_binding_over_bad_hint(
    monkeypatch,
):
    """Regression (#handoff-cutover-lifecycle-journal): a caller-supplied
    ``predecessor_pid`` hint (historically context-handoff's own Node
    extension-host pid, never the actual `copilot` process) must NOT gate
    acceptance of a freshly resolved, session-scoped mux binding -- requiring
    agreement with that hint is exactly what silently and permanently
    stranded every predecessor pane past the first handoff on a worktree
    (the wrong pid got recorded into ``handoff_cutover_spawn`` and later
    failed the retire step's identity check forever)."""
    monkeypatch.setattr(
        m.sessions,
        "mux_binding_for_session",
        lambda sid, **kw: {
            "pane_id": "%9",
            "copilot_pid": 12345,  # the REAL copilot pid
            "copilot_start_time": "real-start",
            "session_name": "wt-a",
        },
    )
    monkeypatch.setattr(m.sessions, "mux_session_name", lambda wt_id: f"wt-{wt_id}")
    captured = {}
    monkeypatch.setattr(
        m,
        "_handoff_cutover_spawn_result",
        lambda args: (captured.__setitem__("spawn", args), (0, {"ok": True}))[1],
    )

    m._monitor_trigger_handoff_cutover(
        {
            "token": "handoff-1",
            "seed": "HANDOFF_SEED",
            "worktree_id": "a",
            "predecessor_session_id": "session-1",
            # A wrong hint (e.g. an MCP-extension host pid), deliberately
            # disagreeing with the binding above.
            "predecessor_pid": 999999,
            "predecessor_start_time": "wrong-start",
        }
    )

    assert captured["spawn"].old_pane == "%9"
    assert captured["spawn"].expected_copilot_pid == 12345
    assert captured["spawn"].expected_copilot_start_time == "real-start"


def test_monitor_retire_handoff_predecessor_preserves_identity_guard(
    monkeypatch,
):
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda raw: raw)
    monkeypatch.setattr(m.sessions, "mux_session_for_pane", lambda pane: "wt-a")
    monkeypatch.setattr(
        m.sessions,
        "mux_binding_for_session",
        lambda sid: {
            "pane_id": "%9",
            "copilot_pid": 77,
            "copilot_start_time": "new-process",
        },
    )
    monkeypatch.setattr(
        m.sessions,
        "mux_retire_pane",
        lambda *a, **k: pytest.fail("must not retire an unverified process"),
    )
    monkeypatch.setattr(m.activity, "log_event", lambda *a, **k: None)

    rc, result = m._monitor_retire_handoff_predecessor(
        {
            "handoff_token": "handoff-1",
            "worktree_id": "a",
            "predecessor_session_id": "session-1",
            "predecessor_pid": 77,
            "predecessor_start_time": "old-process",
            "retire_pane": "%9",
            "mux_session": "wt-a",
            "successor_session_id": "successor-1",
            "retire_reason": "monitor-successor-linked",
        }
    )

    assert rc == 1
    assert result["method"] == "process-identity-mismatch"


def test_sweep_triggers_pending_handoff_cutover_once(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_monitor_handoff_session(tmp_path, monkeypatch)
    request = {
        "token": "handoff-1",
        "seed": "HANDOFF_SEED",
        "worktree_id": "a",
        "predecessor_session_id": "session-1",
        "session_state_path": r"C:\state\handoff-request.json",
        "storage": "file",
    }
    triggered = []

    monkeypatch.setattr(m, "_monitor_pending_handoff_request", lambda record: request)
    monkeypatch.setattr(
        m,
        "_monitor_trigger_handoff_cutover",
        lambda item: triggered.append(item),
    )

    assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    assert triggered == [request]


def test_sweep_triggers_pending_handoff_cutover_for_dormant_record(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "repo-a")
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: m.Path("/tracking"))
    monkeypatch.setattr(
        m, "_activate_project_for_path", lambda *a, **k: m.cfg.set_active_project("repo-a")
    )
    monkeypatch.setattr(
        m,
        "_find_record_for_path",
        lambda path: types.SimpleNamespace(
            worktree_id="a",
            worktree_path=path,
            handoffs=[],
            pending_handoffs=[],
        ),
    )
    dormant = types.SimpleNamespace(
        worktree_id="dormant",
        worktree_path="/w/dormant",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
                state="pending",
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-1",
                predecessor="session-1",
                candidate=None,
                successor=None,
                state="pending",
            )
        ],
    )
    monkeypatch.setattr(m.tracking, "list_records", lambda path: [dormant])
    _capture_set(monkeypatch)
    triggered = []
    mux_checked = []

    def pending(record):
        if getattr(record, "worktree_id", None) == "dormant":
            return {
                "token": "handoff-1",
                "seed": "HANDOFF_SEED",
                "worktree_id": "dormant",
                "predecessor_session_id": "session-1",
            }
        return None

    monkeypatch.setattr(m, "_monitor_pending_handoff_request", pending)
    monkeypatch.setattr(
        m,
        "_monitor_pending_handoff_predecessor_retire",
        lambda record, **kwargs: None,
    )
    monkeypatch.setattr(
        m,
        "_monitor_trigger_handoff_cutover",
        lambda item: triggered.append(item),
    )

    def has_mux_session(worktree_id):
        # A dormant worktree missed by this tick's served-pane pass can still
        # have a genuinely live mux session -- that is what this test is
        # exercising -- so report it as live.
        mux_checked.append(worktree_id)
        return worktree_id == "dormant"

    monkeypatch.setattr(m.sessions, "has_mux_session", has_mux_session)

    prior = m.cfg.active_project()
    try:
        assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    finally:
        m.cfg.set_active_project(prior)
    assert triggered == [{
        "token": "handoff-1",
        "seed": "HANDOFF_SEED",
        "worktree_id": "dormant",
        "predecessor_session_id": "session-1",
    }]
    assert "dormant" in mux_checked


def test_sweep_skips_pending_handoff_cutover_when_not_actually_in_mux(tmp_path, monkeypatch):
    """A pending handoff on a worktree with no live mux session must never
    trigger live cutover choreography, even though its ledger still carries
    pending-handoff state (#handoff-cutover-head-misalignment follow-up)."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {})
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "repo-a")
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: m.Path("/tracking"))
    monkeypatch.setattr(
        m, "_activate_project_for_path", lambda *a, **k: m.cfg.set_active_project("repo-a")
    )
    truly_dormant = types.SimpleNamespace(
        worktree_id="not-in-mux",
        worktree_path="/w/not-in-mux",
        handoffs=[
            types.SimpleNamespace(
                token="handoff-2",
                predecessor="session-2",
                candidate=None,
                successor=None,
                state="pending",
            )
        ],
        pending_handoffs=[
            types.SimpleNamespace(
                token="handoff-2",
                predecessor="session-2",
                candidate=None,
                successor=None,
                state="pending",
            )
        ],
    )
    monkeypatch.setattr(m.tracking, "list_records", lambda path: [truly_dormant])
    _capture_set(monkeypatch)
    triggered = []

    monkeypatch.setattr(
        m,
        "_monitor_pending_handoff_request",
        lambda record: {
            "token": "handoff-2",
            "seed": "HANDOFF_SEED",
            "worktree_id": "not-in-mux",
            "predecessor_session_id": "session-2",
        },
    )
    monkeypatch.setattr(
        m,
        "_monitor_pending_handoff_predecessor_retire",
        lambda record, **kwargs: None,
    )
    monkeypatch.setattr(
        m,
        "_monitor_trigger_handoff_cutover",
        lambda item: triggered.append(item),
    )
    # No live mux session exists anywhere for this worktree.
    monkeypatch.setattr(m.sessions, "has_mux_session", lambda worktree_id: False)

    prior = m.cfg.active_project()
    try:
        assert m._monitor_sweep("tmux", "T", "P", set()) == 0
    finally:
        m.cfg.set_active_project(prior)
    assert triggered == []


def test_sweep_never_scans_dormant_handoffs_without_a_mux_binary(tmp_path, monkeypatch):
    """Without a mux binary at all, the dormant-worktree scan must not run --
    mirroring the served-pane pass's own ``if mux_bin:`` gate."""
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {})
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    monkeypatch.setattr(m.cfg, "project_name", lambda: "repo-a")
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: m.Path("/tracking"))
    monkeypatch.setattr(
        m, "_activate_project_for_path", lambda *a, **k: m.cfg.set_active_project("repo-a")
    )

    def list_records_should_not_be_called(path):
        raise AssertionError("dormant-worktree scan must not run without a mux binary")

    monkeypatch.setattr(m.tracking, "list_records", list_records_should_not_be_called)
    _capture_set(monkeypatch)

    prior = m.cfg.active_project()
    try:
        assert m._monitor_sweep(None, "T", "P", set()) == 0
    finally:
        m.cfg.set_active_project(prior)


def test_sweep_does_not_double_trigger_same_pending_handoff(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_monitor_handoff_session(tmp_path, monkeypatch)
    request = {"token": "handoff-1"}
    triggered = []
    seen = {"done": False}

    def pending(record):
        if seen["done"]:
            return None
        return request

    monkeypatch.setattr(m, "_monitor_pending_handoff_request", pending)
    monkeypatch.setattr(
        m,
        "_monitor_trigger_handoff_cutover",
        lambda item: triggered.append(item) or seen.__setitem__("done", True),
    )

    assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    assert triggered == [request]


def test_sweep_leaves_sessions_without_pending_handoff_untouched(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    monkeypatch.setattr(
        m,
        "_find_record_for_path",
        lambda path: types.SimpleNamespace(
            worktree_id="a",
            handoffs=[],
            pending_handoffs=[],
        ),
    )
    _capture_set(monkeypatch)
    spawned = []
    monkeypatch.setattr(
        m,
        "_monitor_pending_handoff_request",
        lambda record: None,
    )
    monkeypatch.setattr(
        m,
        "_monitor_trigger_handoff_cutover",
        lambda item: spawned.append(item),
    )

    assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    assert spawned == []


def test_sweep_monitor_opt_out_skips_proactive_handoff_cutover(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
    _wire_monitor_handoff_session(tmp_path, monkeypatch)
    monkeypatch.setattr(
        m,
        "_monitor_pending_handoff_request",
        lambda record: None,
    )
    _capture_set(monkeypatch)
    spawned = []
    monkeypatch.setattr(
        m,
        "_monitor_trigger_handoff_cutover",
        lambda item: spawned.append(item),
    )

    assert m._monitor_sweep("tmux", "T", "P", set()) == 1
    assert spawned == []


def test_ensure_monitor_noop_when_live(tmp_path, monkeypatch):
    """A live, current-runtime monitor lock suppresses a duplicate spawn."""
    from agent_worktrees import locks

    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    spawned: list[list[str]] = []
    monkeypatch.setattr(m, "_spawn_detached", lambda argv: spawned.append(argv))

    # This test process is a live pid; its sys.prefix is not under a versions/
    # slot, so _runtime_superseded() is False -> treated as a live current owner.
    locks.write_lock(lock, extra={"prefix": m.os.path.realpath(m.sys.prefix)})
    m._ensure_status_monitor()
    assert spawned == []  # no duplicate

    locks.remove_lock(lock)
    m._ensure_status_monitor()
    assert len(spawned) == 1  # spawned when absent


def test_ensure_replaces_muxless_owner_when_mux_is_available(tmp_path, monkeypatch):
    from agent_worktrees import locks
    import shutil

    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    locks.write_lock(
        lock,
        extra={"prefix": m.os.path.realpath(m.sys.prefix), "mux": False},
    )
    monkeypatch.setattr(shutil, "which", lambda name: "psmux" if name == "psmux" else None)
    spawned: list[list[str]] = []
    monkeypatch.setattr(m, "_spawn_detached", lambda argv: spawned.append(argv) or True)

    assert m._ensure_status_monitor() is True
    assert len(spawned) == 1


def test_status_updater_delegates_when_enabled(monkeypatch):
    """With the monitor opted in, the per-session updater registers + ensures the
    monitor and returns without running its own loop."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    registered: list[tuple[str, str]] = []
    ensured: list[bool] = []
    monkeypatch.setattr(
        m,
        "_register_session_for_monitor",
        lambda sess, path: registered.append((sess, path)) or True,
    )
    monkeypatch.setattr(m, "_ensure_status_monitor", lambda: ensured.append(True) or True)
    # If it fell through to the real loop it would call _render_status_segment;
    # make that explode so a regression is loud.
    monkeypatch.setattr(m, "_render_status_segment", _boom)

    rc = m.cmd_status_updater(
        argparse.Namespace(session="wt-a", mux="tmux", path="/w/a", interval=5)
    )
    assert rc == 0
    assert registered == [("wt-a", "/w/a")]
    assert ensured == [True]


def test_status_updater_falls_back_when_monitor_cannot_start(monkeypatch):
    """If the monitor can't be ensured, the per-session updater must still run --
    a session is never left without a status bar (a-la-carte inline fallback)."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    monkeypatch.setattr(m, "_register_session_for_monitor", lambda s, p: True)
    monkeypatch.setattr(m, "_ensure_status_monitor", lambda: False)  # spawn failed
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    # Prove we REACHED the per-session loop (did not early-return via the monitor
    # path) by short-circuiting it at its first self-retire check.
    reached = []
    monkeypatch.setattr(
        m, "_runtime_superseded", lambda *a, **k: bool(reached.append(True)) or True
    )

    rc = m.cmd_status_updater(
        argparse.Namespace(session="wt-a", mux="tmux", path="/w/a", interval=5)
    )
    assert rc == 0
    assert reached  # fell through into the per-session loop


def test_activate_force_clears_prior_project_on_unresolved(monkeypatch):
    """Under force, an unresolved path must NOT leave a prior session's project
    active (else the monitor renders one session with another's context)."""
    from agent_worktrees import config as cfg

    monkeypatch.setattr(m, "_git_toplevel", lambda p: None)  # unresolved
    try:
        cfg.set_active_project("prev")
        m._activate_project_for_path("/no/repo", force=True)
        assert cfg.active_project() is None  # cleared under force
        # without force, an already-active project is left untouched
        cfg.set_active_project("prev")
        m._activate_project_for_path("/no/repo", force=False)
        assert cfg.active_project() == "prev"
    finally:
        cfg.set_active_project(None)


def _boom(*a, **k):  # pragma: no cover - only fires on regression
    raise AssertionError("per-session loop ran despite monitor being enabled")


# ---------------------------------------------------------------------------
# _restart_status_monitor -- the auto-update cutover seam (consolidated-status-
# daemon Phase 1, dotfiles#1696): reap a superseded monitor + spawn the current
# one so a deploy never leaves live sessions' bars frozen.
# ---------------------------------------------------------------------------


def _wire_restart(monkeypatch, *, lock_data, live, superseded, spawn_ok=True):
    """Stub the lock read/liveness/supersession/spawn/terminate seams."""
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: "/tmp/mon.lock")
    import agent_worktrees.locks as _locks

    monkeypatch.setattr(_locks, "read_lock", lambda p: lock_data)
    monkeypatch.setattr(_locks, "lock_is_live", lambda d: live)
    removed = {"n": 0}

    def _rm(p):
        removed["n"] += 1

    monkeypatch.setattr(_locks, "remove_lock", _rm)
    monkeypatch.setattr(m, "_runtime_superseded", lambda **k: superseded)
    spawned = {"argv": None}

    def _spawn(argv):
        spawned["argv"] = argv
        return spawn_ok

    monkeypatch.setattr(m, "_spawn_detached", _spawn)
    import agent_worktrees.procs as _procs

    reaped = {"pid": None}

    def _term(pid):
        reaped["pid"] = pid
        return True

    monkeypatch.setattr(_procs, "terminate_pid", _term)
    import agent_worktrees.stale_runtime_reap as _srr

    monkeypatch.setattr(_srr, "reap", lambda cfg: [])
    return spawned, reaped, removed


def test_restart_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
    spawned, _r, _rm = _wire_restart(monkeypatch, lock_data=None, live=False, superseded=False)
    r = m._restart_status_monitor()
    assert r["enabled"] is False
    assert r["spawned"] is False
    assert spawned["argv"] is None  # never spawned when opted out


def test_restart_reaps_superseded_and_spawns(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, removed = _wire_restart(
        monkeypatch, lock_data={"pid": 4242, "prefix": "/old/slot"}, live=True, superseded=True
    )
    r = m._restart_status_monitor()
    assert r["reaped"] == 4242  # old monitor reaped
    assert reaped["pid"] == 4242
    assert removed["n"] >= 1  # stale lock cleared
    assert r["spawned"] is True
    assert spawned["argv"][-1] == "status-monitor"  # current one spawned


def test_restart_leaves_current_monitor_alone(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, _rm = _wire_restart(
        monkeypatch, lock_data={"pid": 999, "prefix": "/cur/slot"}, live=True, superseded=False
    )
    r = m._restart_status_monitor()
    assert r["already_current"] is True
    assert r["spawned"] is False  # no duplicate spawn
    assert reaped["pid"] is None  # never reap a current monitor
    assert spawned["argv"] is None


def test_restart_clears_dead_lock_then_spawns(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    spawned, reaped, removed = _wire_restart(
        monkeypatch, lock_data={"pid": 1, "prefix": "/x"}, live=False, superseded=False
    )  # lock present but dead
    r = m._restart_status_monitor()
    assert removed["n"] >= 1  # dead lock cleared
    assert reaped["pid"] is None  # nothing live to reap
    assert r["spawned"] is True
    assert spawned["argv"][-1] == "status-monitor"


def test_cmd_restart_always_exits_zero(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_restart(monkeypatch, lock_data=None, live=False, superseded=False)
    rc = m.cmd_status_monitor_restart(argparse.Namespace())
    assert rc == 0
    assert "status-monitor:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# stale_runtime_reap.reap() wiring into _restart_status_monitor -- #4268: a
# one-shot CLI verb invocation mid-flight on a superseded runtime slot has no
# self-check of its own (only the resident monitor loop rechecks
# `_runtime_superseded` each tick), so it can wedge and pile up across every
# deploy it survives unless the cutover reap also sweeps for it, not just the
# monitor's own known lock pid. See test_stale_runtime_reap.py for the pure
# `reap()`/`summary_bits()`/`summary_suffix()` unit tests.
# ---------------------------------------------------------------------------


def test_restart_reports_stale_runtime_reaped_alongside_monitor_reap(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_restart(
        monkeypatch, lock_data={"pid": 4242, "prefix": "/old/slot"}, live=True, superseded=True
    )
    import agent_worktrees.stale_runtime_reap as _srr

    monkeypatch.setattr(_srr, "reap", lambda cfg: [111, 333])

    r = m._restart_status_monitor()

    assert r["stale_runtime_reaped"] == [111, 333]
    assert r["reaped"] == 4242  # the monitor's own lock-pid reap still runs too


def test_restart_sweeps_stale_runtime_even_when_monitor_already_current(monkeypatch):
    # The general sweep is independent of the singleton monitor's own state --
    # a wedged one-shot verb invocation can exist on an old slot even when the
    # CURRENT monitor already owns the host.
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_restart(
        monkeypatch, lock_data={"pid": 999, "prefix": "/cur/slot"}, live=True, superseded=False
    )
    import agent_worktrees.stale_runtime_reap as _srr

    monkeypatch.setattr(_srr, "reap", lambda cfg: [111])

    r = m._restart_status_monitor()

    assert r["already_current"] is True
    assert r["stale_runtime_reaped"] == [111]


def test_cmd_restart_reports_stale_runtime_reap_count(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_WORKTREES_STATUS_MONITOR", raising=False)
    _wire_restart(monkeypatch, lock_data=None, live=False, superseded=False)
    import agent_worktrees.stale_runtime_reap as _srr

    monkeypatch.setattr(_srr, "reap", lambda cfg: [111, 222])

    rc = m.cmd_status_monitor_restart(argparse.Namespace())

    assert rc == 0
    out = capsys.readouterr().out
    assert "reaped 2 stale-runtime process(es)" in out


# ---------------------------------------------------------------------------
# schedule_delayed_daemon_health_reap / status-monitor-reap-stale --
# copilot-extensions#5453's own-sanctioned async backstop: a cutover/restart
# attempt that times out or rolls back ambiguously can leave either a stray
# duplicate daemon or zero live daemons, with nothing today reconverging on
# exactly one owner without a human running `doctor --apply-daemon-health`
# by hand. This schedules that SAME identity-verified repair automatically,
# on a delay, without ever blocking the installer or the caller.
# ---------------------------------------------------------------------------


def test_schedule_delayed_daemon_health_reap_spawns_reap_stale_with_delay(monkeypatch):
    spawned = {"argv": None}

    def _spawn(argv):
        spawned["argv"] = argv
        return True

    monkeypatch.setattr(m, "_spawn_detached", _spawn)

    ok = status_monitor_reap_stale.schedule_delayed_daemon_health_reap(delay_seconds=42.5)

    assert ok is True
    assert spawned["argv"][-3:] == ["status-monitor-reap-stale", "--delay-seconds", "42.5"]


def test_schedule_delayed_daemon_health_reap_uses_default_delay(monkeypatch):
    spawned = {"argv": None}
    monkeypatch.setattr(m, "_spawn_detached", lambda argv: spawned.update(argv=argv) or True)

    status_monitor_reap_stale.schedule_delayed_daemon_health_reap()

    assert spawned["argv"][-2] == "--delay-seconds"
    assert float(spawned["argv"][-1]) == status_monitor_reap_stale.DEFAULT_DELAY_SECONDS


def test_schedule_delayed_daemon_health_reap_never_raises(monkeypatch):
    def _boom(argv):
        raise OSError("no fork slots")

    monkeypatch.setattr(m, "_spawn_detached", _boom)

    assert status_monitor_reap_stale.schedule_delayed_daemon_health_reap() is False


def test_cmd_reap_stale_sleeps_then_applies_daemon_health(monkeypatch, capsys):
    slept = {"seconds": None}
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: slept.update(seconds=s))

    applied = {"calls": 0}

    def _fake_doctor_report(*, apply):
        applied["calls"] += 1
        assert apply is True
        return {"findings": [{"kind": "duplicate_resident"}]}

    from agent_worktrees import daemon_health
    monkeypatch.setattr(daemon_health, "doctor_report", _fake_doctor_report)

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=7.0)
    )

    assert rc == 0
    assert slept["seconds"] == 7.0
    assert applied["calls"] == 1
    assert "1 finding(s)" in capsys.readouterr().out


def test_cmd_reap_stale_is_a_clean_no_op_when_nothing_to_repair(monkeypatch, capsys):
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: None)
    from agent_worktrees import daemon_health
    monkeypatch.setattr(daemon_health, "doctor_report", lambda *, apply: {"findings": []})

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=0.0)
    )

    assert rc == 0
    assert "0 finding(s)" in capsys.readouterr().out


def test_cmd_reap_stale_never_raises_on_repair_failure(monkeypatch, capsys):
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: None)

    def _boom(*, apply):
        raise RuntimeError("routing dir unreadable")

    from agent_worktrees import daemon_health
    monkeypatch.setattr(daemon_health, "doctor_report", _boom)

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=0.0)
    )

    assert rc == 0  # advisory, best-effort -- never fails the caller
    assert "non-fatal" in capsys.readouterr().out


def _patch_cutover_lock(monkeypatch, *, acquirable: bool):
    """Stub ``status_monitor_cutover._acquire_cutover_lock`` so tests can
    simulate the lock being free (``acquirable=True``, returns a fake lease
    with a no-op ``release()``) or already held by a genuine concurrent
    cutover (``acquirable=False``, raises ``AlreadyRunningError`` exactly
    like the real ``SingleInstance.acquire()`` does)."""
    from agent_worktrees import status_monitor_cutover as smc
    from single_instance_lease import AlreadyRunningError

    released = {"n": 0}

    class _FakeLease:
        def release(self):
            released["n"] += 1

    def _acquire(lock_root, *, timeout_s=0.0, poll_s=0.2):
        if acquirable:
            return _FakeLease()
        raise AlreadyRunningError(holder_pid=4242)

    monkeypatch.setattr(smc, "_acquire_cutover_lock", _acquire)
    return released


def test_cmd_reap_stale_ensures_a_monitor_when_zero_candidates_survive(monkeypatch, capsys):
    """The repair pass only audits/terminates EXISTING live candidates -- a
    rollback that left ZERO live monitors (the other documented ambiguous
    outcome, copilot-extensions#5453) must not stay down forever just
    because there was nothing for the identity-bound repair to terminate."""
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: None)
    from agent_worktrees import daemon_health
    monkeypatch.setattr(daemon_health, "doctor_report", lambda *, apply: {"findings": []})
    monkeypatch.setattr(daemon_health, "_candidates", lambda: [])
    released = _patch_cutover_lock(monkeypatch, acquirable=True)

    ensured = {"calls": 0}
    monkeypatch.setattr(
        status_monitor_runtime, "_ensure_status_monitor",
        lambda: ensured.update(calls=ensured["calls"] + 1) or True,
    )

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=0.0)
    )

    assert rc == 0
    assert ensured["calls"] == 1
    assert released["n"] == 1  # the acquired lease was always released
    assert "ensured" in capsys.readouterr().out


def test_cmd_reap_stale_does_not_ensure_when_a_candidate_already_survives(monkeypatch):
    """A clean repair leaving a live candidate must NOT trigger an extra
    ensure-monitor spawn -- that would risk a duplicate."""
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: None)
    from agent_worktrees import daemon_health
    monkeypatch.setattr(
        daemon_health, "doctor_report",
        lambda *, apply: {"findings": [{"kind": "duplicate_resident"}]},
    )
    monkeypatch.setattr(daemon_health, "_candidates", lambda: [object()])
    _patch_cutover_lock(monkeypatch, acquirable=True)

    ensured = {"calls": 0}
    monkeypatch.setattr(
        status_monitor_runtime, "_ensure_status_monitor",
        lambda: ensured.update(calls=ensured["calls"] + 1) or True,
    )

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=0.0)
    )

    assert rc == 0
    assert ensured["calls"] == 0


def test_cmd_reap_stale_does_not_ensure_while_a_cutover_is_in_progress(monkeypatch):
    """A genuinely concurrent cutover (lock already held elsewhere) must be
    left entirely alone -- that attempt owns ensuring a monitor exists once
    it finishes, and racing it here (even a re-check) could start a
    duplicate right as it promotes its own successor. The non-blocking
    acquire attempt is the whole point: never wait on it, just back off."""
    from agent_worktrees import daemon_health
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: None)
    monkeypatch.setattr(daemon_health, "doctor_report", lambda *, apply: {"findings": []})
    candidates_called = {"n": 0}
    monkeypatch.setattr(
        daemon_health, "_candidates",
        lambda: (candidates_called.update(n=candidates_called["n"] + 1), [])[1],
    )
    _patch_cutover_lock(monkeypatch, acquirable=False)

    ensured = {"calls": 0}
    monkeypatch.setattr(
        status_monitor_runtime, "_ensure_status_monitor",
        lambda: ensured.update(calls=ensured["calls"] + 1) or True,
    )

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=0.0)
    )

    assert rc == 0
    assert ensured["calls"] == 0
    assert candidates_called["n"] == 0  # never even re-checked while busy


def test_cmd_reap_stale_ensure_failure_is_non_fatal(monkeypatch, capsys):
    monkeypatch.setattr(status_monitor_reap_stale.time, "sleep", lambda s: None)
    from agent_worktrees import daemon_health
    monkeypatch.setattr(daemon_health, "doctor_report", lambda *, apply: {"findings": []})
    monkeypatch.setattr(daemon_health, "_candidates", lambda: [])
    _patch_cutover_lock(monkeypatch, acquirable=True)

    def _boom():
        raise OSError("no fork slots")

    monkeypatch.setattr(status_monitor_runtime, "_ensure_status_monitor", _boom)

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=0.0)
    )

    assert rc == 0
    assert "non-fatal" in capsys.readouterr().out


def test_ensure_monitor_helper_never_calls_doctor_report_while_holding_the_lock(
    monkeypatch,
):
    """Regression for the TOCTOU race a review finding identified: the
    recheck-and-ensure step must use the lock-free ``_candidates()`` probe,
    never ``daemon_health.doctor_report()``/``apply_daemon_health()`` --
    both try to acquire this SAME cutover guard themselves (non-blocking,
    per-call), which would self-block/no-op against the lease this
    function is already holding, silently defeating the atomicity this
    helper exists to provide."""
    from agent_worktrees import daemon_health
    monkeypatch.setattr(daemon_health, "_candidates", lambda: [])

    def _boom(*, apply):
        raise AssertionError(
            "must not call doctor_report() while already holding the cutover lock"
        )

    monkeypatch.setattr(daemon_health, "doctor_report", _boom)
    _patch_cutover_lock(monkeypatch, acquirable=True)
    monkeypatch.setattr(status_monitor_runtime, "_ensure_status_monitor", lambda: True)

    outcome = status_monitor_reap_stale._ensure_monitor_if_zero_candidates_under_cutover_guard()

    assert outcome == "ensured"


def test_delay_seconds_rejects_non_finite_and_negative_values():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    status_monitor_reap_stale.add_parsers(sub)
    for bad in ("inf", "-inf", "nan", "-1"):
        with pytest.raises(SystemExit):
            parser.parse_args(["status-monitor-reap-stale", "--delay-seconds", bad])


def test_cmd_reap_stale_falls_back_to_default_for_a_hand_built_non_finite_namespace(
    monkeypatch,
):
    """argparse already rejects non-finite/negative values for a real CLI
    invocation (see the test above); this guards the lower-level function
    itself against a direct, non-argparse call (e.g. a hand-built
    Namespace) with the same OverflowError/hang a raw inf/nan would
    otherwise cause in time.sleep()."""
    slept = {"seconds": None}
    monkeypatch.setattr(
        status_monitor_reap_stale.time, "sleep", lambda s: slept.update(seconds=s)
    )
    from agent_worktrees import daemon_health
    monkeypatch.setattr(daemon_health, "doctor_report", lambda *, apply: {"findings": []})

    rc = status_monitor_reap_stale.cmd_status_monitor_reap_stale(
        argparse.Namespace(delay_seconds=float("inf"))
    )

    assert rc == 0
    assert slept["seconds"] == status_monitor_reap_stale.DEFAULT_DELAY_SECONDS


def test_reap_stale_registered_and_exposes_delay_flag():
    assert m.COMMAND_MAP["status-monitor-reap-stale"] is m.cmd_status_monitor_reap_stale
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    status_monitor_reap_stale.add_parsers(sub)
    args = parser.parse_args(["status-monitor-reap-stale", "--delay-seconds", "5"])
    assert args.delay_seconds == 5.0


def test_installer_after_update_schedules_async_reap_regardless_of_outcome(monkeypatch):
    """The installer seam (status_monitor_cutover.activate_after_update) must
    schedule the async backstop whenever it actually attempts a restart or
    cutover -- never only on success, since an ambiguous rollback is exactly
    the case this backstop exists for -- and must never block on it."""
    from agent_worktrees import status_monitor_cutover as smc

    scheduled = {"n": 0}
    monkeypatch.setattr(
        status_monitor_reap_stale, "schedule_delayed_daemon_health_reap",
        lambda *a, **k: scheduled.update(n=scheduled["n"] + 1),
    )
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: False)
    monkeypatch.setattr(
        status_monitor_runtime, "_restart_status_monitor", lambda: {"spawned": True}
    )

    summary = smc.activate_after_update(monitor_was_live=True)

    assert summary["action"] == "restart"
    assert scheduled["n"] == 1


def test_installer_after_update_does_not_schedule_when_disabled(monkeypatch):
    from agent_worktrees import status_monitor_cutover as smc

    scheduled = {"n": 0}
    monkeypatch.setattr(
        status_monitor_reap_stale, "schedule_delayed_daemon_health_reap",
        lambda *a, **k: scheduled.update(n=scheduled["n"] + 1),
    )
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)

    summary = smc.activate_after_update(monitor_was_live=True)

    assert summary["enabled"] is False
    assert scheduled["n"] == 0


def test_installer_after_update_does_not_schedule_when_nothing_was_live(monkeypatch):
    """No prior monitor and no routed monitor: a genuine cold start with
    nothing to reconcile -- scheduling the backstop here would just be a
    wasted detached process for every ordinary first-ever activation."""
    from agent_worktrees import status_monitor_cutover as smc

    scheduled = {"n": 0}
    monkeypatch.setattr(
        status_monitor_reap_stale, "schedule_delayed_daemon_health_reap",
        lambda *a, **k: scheduled.update(n=scheduled["n"] + 1),
    )
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(smc, "_monitor_lock_is_live", lambda: False)

    summary = smc.activate_after_update(monitor_was_live=False)

    assert summary["action"] == "noop"
    assert scheduled["n"] == 0


def test_installers_invoke_monitor_cutover_after_activation():
    # Graceful-cutover Phase 1 contract: BOTH runtime installers must invoke the
    # post-activation cutover helper, or a live status-monitor silently regresses
    # to a hard restart / frozen bars path. Pin it so an installer refactor can't
    # drop it.
    from pathlib import Path

    scripts = Path(m.__file__).resolve().parents[2] / "scripts"
    for name in ("install.ps1", "install.sh"):
        text = (scripts / name).read_text("utf-8")
        assert "status_monitor_cutover" in text, (
            f"{name} must invoke the status-monitor cutover helper after "
            "activating the new runtime slot"
        )


def test_status_monitor_backs_off_at_iteration_boundary_without_mutating(
    tmp_path, monkeypatch
):
    from agent_worktrees import status_monitor_cutover as smc

    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    monkeypatch.setattr(m, "_load_hook_client_module", lambda: None)
    writes: list[dict | None] = []
    monkeypatch.setattr(
        m.locks,
        "write_lock",
        lambda _path, extra=None: writes.append(extra) or True,
    )
    monkeypatch.setattr(smc, "publish_route", lambda *a, **k: None)
    monkeypatch.setattr(smc, "active_generation_for_pid", lambda pid: None)
    monkeypatch.setattr(smc, "clear_route_if_owner", lambda pid: False)
    runtime_states = iter([False, True])
    monkeypatch.setattr(m, "_runtime_superseded", lambda **_kw: next(runtime_states))
    monkeypatch.setattr(
        m,
        "_monitor_sweep",
        lambda *args, **kwargs: pytest.fail("sweep must not run while backing off"),
    )
    sleeps: list[float] = []
    monkeypatch.setattr(m.time, "sleep", sleeps.append)
    governance_calls: list[str] = []

    class _Governance:
        def recheck(self, checkpoint):
            governance_calls.append(checkpoint)
            return {"status": "backoff", "reason": "maintenance-active"}

    monkeypatch.setattr(
        m.loop_governance_mod,
        "LoopGovernance",
        lambda: _Governance(),
    )

    assert m.cmd_status_monitor(argparse.Namespace(interval=5)) == 0
    assert governance_calls == ["iteration-boundary"]
    assert sleeps == [m._GOVERNANCE_BACKOFF_SECONDS]
    assert len(writes) == 2  # startup ownership stamp only; no loop renewal


def test_status_monitor_binds_and_unbinds_resident_push(tmp_path, monkeypatch):
    """resident_push must be bound to THIS run's real wake-event/segment-cache
    at startup (so a `status_disposition_write` verb served in-process during
    this monitor's lifetime can push an immediate refresh instead of waiting
    out `interval` -- see resident_push.py/test_resident_push.py) and
    explicitly un-bound again on shutdown, isolated via the same
    single-iteration governance-backoff exit `test_status_monitor_backs_off_
    at_iteration_boundary_without_mutating` uses."""
    from agent_worktrees import resident_push
    from agent_worktrees import status_monitor_cutover as smc

    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    monkeypatch.setattr(m, "_load_hook_client_module", lambda: None)
    monkeypatch.setattr(m.locks, "write_lock", lambda _path, extra=None: True)
    monkeypatch.setattr(smc, "publish_route", lambda *a, **k: None)
    monkeypatch.setattr(smc, "active_generation_for_pid", lambda pid: None)
    monkeypatch.setattr(smc, "clear_route_if_owner", lambda pid: False)
    runtime_states = iter([False, True])
    monkeypatch.setattr(m, "_runtime_superseded", lambda **_kw: next(runtime_states))
    monkeypatch.setattr(
        m,
        "_monitor_sweep",
        lambda *args, **kwargs: pytest.fail("sweep must not run while backing off"),
    )
    monkeypatch.setattr(m.time, "sleep", lambda *_a, **_k: None)

    class _Governance:
        def recheck(self, checkpoint):
            return {"status": "backoff", "reason": "test-exit"}

    monkeypatch.setattr(m.loop_governance_mod, "LoopGovernance", lambda: _Governance())

    bound = []
    real_bind = resident_push.bind

    def _spy_bind(wake_event, segment_cache):
        bound.append((wake_event, segment_cache))
        real_bind(wake_event, segment_cache)

    monkeypatch.setattr(resident_push, "bind", _spy_bind)
    reset_calls = []
    real_reset = resident_push.reset

    def _spy_reset():
        reset_calls.append(True)
        real_reset()

    monkeypatch.setattr(resident_push, "reset", _spy_reset)

    assert m.cmd_status_monitor(argparse.Namespace(interval=5)) == 0

    assert len(bound) == 1
    wake_event, segment_cache = bound[0]
    assert isinstance(wake_event, threading.Event)
    assert hasattr(segment_cache, "invalidate")  # the real _StatusSegmentCache
    assert reset_calls == [True]
    # Un-bound at shutdown -- a stray notify() after this monitor exits must
    # be a safe no-op, never touch a wake-event/cache from a dead run.
    assert resident_push._wake_event is None
    assert resident_push._segment_cache is None


def test_classify_daemon_started_published_in_lock_and_closed_on_exit(
    tmp_path, monkeypatch
):
    """Phase 4d (#2323): the resident monitor must actually start the
    classify-coalescing server, publish its rendezvous fields into the SAME
    lock write as the hook server's own fields, and close it on exit -- a
    regression here would silently force every ``list --json --classify``
    caller onto the (still-correct, but un-accelerated) lease-guarded path.
    Exercises the real `cmd_status_monitor` startup/shutdown path (mirrors
    `test_status_monitor_backs_off_at_iteration_boundary_without_mutating`'s
    isolation technique -- an immediate governance backoff that exits the
    loop after exactly one iteration) with a REAL `classify_daemon` server,
    never this host's own real resident monitor or lock file.
    """
    from agent_worktrees import status_monitor_cutover as smc

    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    monkeypatch.setattr(m, "_load_hook_client_module", lambda: None)
    writes: list[dict | None] = []
    monkeypatch.setattr(
        m.locks,
        "write_lock",
        lambda _path, extra=None: writes.append(extra) or True,
    )
    monkeypatch.setattr(smc, "publish_route", lambda *a, **k: None)
    monkeypatch.setattr(smc, "active_generation_for_pid", lambda pid: None)
    monkeypatch.setattr(smc, "clear_route_if_owner", lambda pid: False)
    runtime_states = iter([False, True])
    monkeypatch.setattr(m, "_runtime_superseded", lambda **_kw: next(runtime_states))
    monkeypatch.setattr(
        m,
        "_monitor_sweep",
        lambda *args, **kwargs: pytest.fail("sweep must not run in this test"),
    )
    sleeps: list[float] = []
    monkeypatch.setattr(m.time, "sleep", sleeps.append)

    class _Governance:
        def recheck(self, checkpoint):
            return {"status": "backoff", "reason": "test-exit"}

    monkeypatch.setattr(m.loop_governance_mod, "LoopGovernance", lambda: _Governance())

    from agent_worktrees import classify_daemon

    closed = {"n": 0}
    real_close = classify_daemon.CoalescingServer.close

    def _spy_close(self):
        closed["n"] += 1
        real_close(self)

    monkeypatch.setattr(classify_daemon.CoalescingServer, "close", _spy_close)

    assert m.cmd_status_monitor(argparse.Namespace(interval=5)) == 0

    assert len(writes) == 2  # early ownership stamp, then the servers-included stamp
    early_stamp, servers_stamp = writes
    # The very first write is the bare single-active ownership claim, made
    # before either server exists -- it never carries rendezvous fields.
    assert "classify_endpoint" not in early_stamp
    assert "hook_endpoint" not in early_stamp
    assert isinstance(servers_stamp, dict)
    assert "classify_endpoint" in servers_stamp
    assert "classify_token" in servers_stamp
    assert "classify_generation" in servers_stamp
    # Never collides with the hook server's own namespace.
    assert "hook_endpoint" not in servers_stamp
    assert "worktree_status_endpoint" in servers_stamp
    assert "worktree_status_token" in servers_stamp
    assert "worktree_status_generation" in servers_stamp
    assert "managed_mux_endpoint" in servers_stamp
    assert "managed_mux_token" in servers_stamp
    assert "managed_mux_generation" in servers_stamp
    assert "tracking_write_endpoint" in servers_stamp
    assert "tracking_write_token" in servers_stamp
    assert "tracking_write_generation" in servers_stamp
    assert closed["n"] == 4  # classify_server + worktree_status_server + managed_mux_server + tracking_write_server


def test_self_retire_softly_closes_admission_before_hard_closing_on_final_exit(
    tmp_path, monkeypatch
):
    """The single-shot-caller admission-discipline contract, wired end to
    end through the real resident monitor: once this daemon observes itself
    superseded (``self_retire.is_superseded``), ``_enter_drain_only_state``
    must soft-close each live request surface (``CoalescingServer.
    close_admission`` -- socket stays open, new callers get a structured
    rejection) rather than immediately hard-closing it. The hard ``close()``
    that actually tears the socket down remains reserved for the real final
    exit in this function's own ``finally`` block, once the bounded
    self-retire confirmation count is reached. A regression that reverts to
    hard-closing immediately on the first superseded observation would make
    ``close()`` fire before the loop's own break -- this test pins the
    ordering, not just the call count.
    """
    from agent_worktrees import classify_daemon, self_retire
    from agent_worktrees import status_monitor_cutover as smc

    lock = tmp_path / "status-monitor.lock"
    monkeypatch.setattr(m, "_monitor_lock_path", lambda: lock)
    monkeypatch.setattr(m, "_load_hook_client_module", lambda: None)
    monkeypatch.setattr(m.locks, "write_lock", lambda _path, extra=None: True)
    monkeypatch.setattr(smc, "publish_route", lambda *a, **k: None)
    monkeypatch.setattr(smc, "clear_route_if_owner", lambda pid: False)
    # A fixed, non-None generation makes `self_retire_generation` non-None
    # from the very first iteration, so the self-retire branch (not the
    # unrelated `runtime_superseded()` early-exit) drives this test.
    monkeypatch.setattr(smc, "active_generation_for_pid", lambda pid: 42)
    monkeypatch.setattr(self_retire, "is_superseded", lambda *a, **k: True)
    monkeypatch.setattr(
        m,
        "_monitor_sweep",
        lambda *args, **kwargs: pytest.fail("sweep must not run once admission is closed"),
    )
    # Skip the real interval wait between iterations -- this test only
    # needs the bounded (2-confirmation) self-retire loop to run its
    # course quickly, not to exercise real wake/backstop timing.
    monkeypatch.setattr(m.status_monitor_cli, "_wake_interruptible_wait", lambda *a, **k: None)

    events: list[tuple[str, str]] = []
    real_close_admission = classify_daemon.CoalescingServer.close_admission
    real_close = classify_daemon.CoalescingServer.close

    def _spy_close_admission(self, reason="superseded"):
        events.append(("close_admission", reason))
        real_close_admission(self, reason)

    def _spy_close(self):
        events.append(("close", ""))
        real_close(self)

    monkeypatch.setattr(classify_daemon.CoalescingServer, "close_admission", _spy_close_admission)
    monkeypatch.setattr(classify_daemon.CoalescingServer, "close", _spy_close)

    assert m.cmd_status_monitor(argparse.Namespace(interval=2)) == 0

    kinds = [kind for kind, _ in events]
    # Soft-close (admission rejection, socket stays open) happens at least
    # once per confirmation iteration, strictly before any hard close.
    assert kinds.count("close_admission") >= 1
    assert kinds.index("close_admission") < kinds.index("close")
    assert all(reason == "superseded" for kind, reason in events if kind == "close_admission")
    # The hard close at real exit covers every live request surface exactly
    # once each (classify_server + worktree_status_server + managed_mux_server
    # + tracking_write_server), same invariant as the sibling test above --
    # soft-closing never skips the eventual real teardown.
    assert kinds.count("close") == 4


def test_sweep_rechecks_before_publish_and_retains_registered_session_on_generation_change(
    tmp_path, monkeypatch
):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_monitor_maybe_trigger_handoff_cutover", lambda *_a: None)
    mux_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        m,
        "_monitor_mux_set",
        lambda mux_bin, sess, opt, val: mux_calls.append((sess, opt, val)) or True,
    )
    segment_cache = type("Cache", (), {"get": staticmethod(lambda _path: "SEG")})()
    governance_calls: list[str] = []

    class _Governance:
        def recheck(self, checkpoint):
            governance_calls.append(checkpoint)
            return {"status": "revalidation-required", "reason": "generation-changed"}

    with pytest.raises(m._StatusMonitorGovernanceDeferred):
        m._monitor_sweep(
            "tmux",
            "T",
            "P",
            set(),
            segment_cache=segment_cache,
            governance=_Governance(),
        )

    assert governance_calls == ["pre-mutation:publish-status"]
    assert (reg / "wt-a").exists()
    assert mux_calls == []


def test_sweep_rechecks_before_render_status_and_retains_registered_session(
    tmp_path, monkeypatch
):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_monitor_maybe_trigger_handoff_cutover", lambda *_a: None)
    monkeypatch.setattr(
        m,
        "_monitor_mux_set",
        lambda *args, **kwargs: pytest.fail("mux publish must not run after render guard"),
    )
    governance_calls: list[str] = []

    class _Governance:
        def recheck(self, checkpoint):
            governance_calls.append(checkpoint)
            return {"status": "revalidation-required", "reason": "generation-changed"}

    with pytest.raises(m._StatusMonitorGovernanceDeferred):
        m._monitor_sweep(
            "tmux",
            "T",
            "P",
            set(),
            governance=_Governance(),
        )

    assert governance_calls == ["pre-mutation:render-status"]
    assert (reg / "wt-a").exists()


def test_sweep_proceeds_when_iteration_and_pre_mutation_checks_stay_current(
    tmp_path, monkeypatch
):
    reg = tmp_path / "reg"
    monkeypatch.setattr(m, "_monitor_registry_dir", lambda: reg)
    m._register_session_for_monitor("wt-a", "/w/a")
    monkeypatch.setattr(m, "_monitor_list_sessions", lambda mux_bin: {"wt-a": 1})
    monkeypatch.setattr(m, "_activate_project_for_path", lambda *a, **k: None)
    monkeypatch.setattr(m, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(m, "_render_status_segment", lambda *a, **k: "SEG")
    monkeypatch.setattr(m, "_monitor_maybe_trigger_handoff_cutover", lambda *_a: None)
    monkeypatch.setattr(m, "_warm_list_cache_for_active_project", lambda **kw: 0)
    mux_calls = _capture_set(monkeypatch)
    governance_calls: list[str] = []

    class _Governance:
        def recheck(self, checkpoint):
            governance_calls.append(checkpoint)
            return {"status": "ready", "reason": "current", "baseline": {}}

    ctx_done: set[str] = set()
    assert (
        m._monitor_sweep(
            "tmux",
            "T",
            "P",
            ctx_done,
            governance=_Governance(),
        )
        == 1
    )
    assert "wt-a" in ctx_done
    assert ("wt-a", "@aw_seg", "SEG") in mux_calls
    assert "pre-mutation:publish-status" in governance_calls


# --- windowless daemon spawn (the "headed status-monitor" DefTerm bug) --------


def test_windowless_python_prefers_pythonw_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_procutil,
        "os",
        types.SimpleNamespace(**{**vars(agent_procutil.os), "name": "nt"}),
    )
    (tmp_path / "python.exe").write_text("")
    pyw = tmp_path / "pythonw.exe"
    pyw.write_text("")
    monkeypatch.setattr(m.sys, "executable", str(tmp_path / "python.exe"))
    assert m._windowless_python() == str(pyw)


def test_windowless_python_falls_back_without_pythonw(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_procutil,
        "os",
        types.SimpleNamespace(**{**vars(agent_procutil.os), "name": "nt"}),
    )
    py = tmp_path / "python.exe"
    py.write_text("")
    monkeypatch.setattr(m.sys, "executable", str(py))
    assert m._windowless_python() == str(py)  # no pythonw sibling -> fall back


def test_windowless_python_noop_off_windows(monkeypatch):
    monkeypatch.setattr(
        agent_procutil,
        "os",
        types.SimpleNamespace(**{**vars(agent_procutil.os), "name": "posix"}),
    )
    monkeypatch.setattr(m.sys, "executable", "/usr/bin/python3")
    assert m._windowless_python() == "/usr/bin/python3"


def test_spawn_detached_uses_console_python_windowless_daemon(monkeypatch):
    seen: dict = {}

    def _fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        seen["env"] = kwargs["env"]
        return object()

    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "other-secret")
    monkeypatch.setenv("AGENT_WORKTREES_AHP_AUTH_TOKEN", "handoff-secret")
    monkeypatch.setenv("SAFE_VALUE", "kept")
    monkeypatch.setattr(m.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        m,
        "windowless_daemon_kwargs",
        lambda **_kw: {"windowless_daemon": True},
    )
    assert m._spawn_detached([m.sys.executable, "-m", "agent_worktrees", "status-monitor"]) is True
    assert seen["argv"][0] == m.sys.executable
    assert seen["argv"][1:] == ["-m", "agent_worktrees", "status-monitor"]
    assert seen["kwargs"]["windowless_daemon"] is True
    assert seen["env"]["SAFE_VALUE"] == "kept"
    assert "GH_TOKEN" not in seen["env"]
    assert "GITHUB_TOKEN" not in seen["env"]
    assert "AGENT_WORKTREES_AHP_AUTH_TOKEN" not in seen["env"]


def test_spawn_detached_scrubs_session_id_and_roots_cwd_at_install_dir(
    monkeypatch, tmp_path
):
    """A resident, host-wide daemon must not inherit the ephemeral session
    id or cwd of whichever CLI invocation happened to ensure it -- it
    outlives all of them. Its cwd is the stable install root
    (marketplace-cell root, or legacy ``~/.agent-worktrees``), not a
    version slot that churns on every update.

    ``COPILOT_EXTENSIONS_CONTEXT``/``COPILOT_PLUGIN_ROOT`` must survive
    unchanged (Copilot review finding on PR #3906): the daemon itself calls
    ``cfg.install_dir()`` throughout its own lifetime, and
    ``registry_paths.registry_root()`` treats an absent context as legacy
    mode -- stripping it would make a cell-spawned daemon resolve its OWN
    monitor lock/state under the legacy root instead of its cell's, even
    though its cwd was correctly cell-rooted at spawn time."""
    seen: dict = {}

    def _fake_popen(argv, **kwargs):
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "some-cell")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/some/installed-plugin/path")
    monkeypatch.setenv("copilot_agent_session_id", "mixed-case-should-also-be-scrubbed")
    monkeypatch.setenv("SAFE_VALUE", "kept")
    monkeypatch.setattr(m.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(m, "windowless_daemon_kwargs", lambda **_kw: {})

    install_root = tmp_path / "cell-root"
    install_root.mkdir(parents=True)
    monkeypatch.setattr(m.cfg, "install_dir", lambda: install_root)

    assert m._spawn_detached([m.sys.executable, "-m", "agent_worktrees", "status-monitor"]) is True
    env = seen["kwargs"]["env"]
    assert env["COPILOT_EXTENSIONS_CONTEXT"] == "some-cell"
    assert env["COPILOT_PLUGIN_ROOT"] == "/some/installed-plugin/path"
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "copilot_agent_session_id" not in env
    assert env["SAFE_VALUE"] == "kept"
    assert seen["kwargs"]["cwd"] == str(install_root)


def test_daemon_cwd_degrades_to_home_when_install_dir_is_unresolved(monkeypatch):
    from agent_worktrees import status_monitor_runtime as smr

    monkeypatch.setattr(m.cfg, "install_dir", lambda: (_ for _ in ()).throw(Exception("boom")))
    assert smr._daemon_cwd() == os.path.expanduser("~")


def test_daemon_cwd_degrades_to_home_when_install_dir_does_not_exist(monkeypatch, tmp_path):
    from agent_worktrees import status_monitor_runtime as smr

    missing = tmp_path / "not-installed"
    monkeypatch.setattr(m.cfg, "install_dir", lambda: missing)
    assert smr._daemon_cwd() == os.path.expanduser("~")


def test_headless_child_guard_ors_no_window():
    from conftest import _headless_creationflags

    assert _headless_creationflags(0) & 0x08000000


def test_headless_child_guard_respects_explicit_new_console():
    from conftest import _headless_creationflags

    flags = _headless_creationflags(0x00000010)
    assert not (flags & 0x08000000)
    assert flags & 0x00000010


class TestWaitForTrackingWriteIdle:
    """2026-09-26 PR review round 4: the tracking_write server must not be
    closed while a write is still executing, on *any* shutdown path (not
    just the empty-strike idle-exit branch) -- extracted as its own
    function specifically so this deadline/poll logic is directly
    unit-testable without driving the full cmd_status_monitor lifecycle.
    """

    def test_returns_immediately_when_never_busy(self):
        from agent_worktrees import status_monitor_cli

        sleeps = []
        status_monitor_cli._wait_for_tracking_write_idle(
            lambda: False,
            now=lambda: 100.0,
            sleep=sleeps.append,
        )
        assert sleeps == []

    def test_polls_until_busy_clears(self):
        from agent_worktrees import status_monitor_cli

        busy_calls = {"n": 0}

        def _busy():
            busy_calls["n"] += 1
            return busy_calls["n"] < 3

        sleeps = []
        status_monitor_cli._wait_for_tracking_write_idle(
            _busy,
            grace_s=10.0,
            poll_interval_s=0.1,
            now=lambda: 100.0,  # deadline never reached at this fixed time
            sleep=sleeps.append,
        )
        assert busy_calls["n"] == 3
        assert sleeps == [0.1, 0.1]

    def test_gives_up_at_the_grace_deadline_even_if_still_busy(self):
        from agent_worktrees import status_monitor_cli

        clock = {"t": 100.0}

        def _now():
            return clock["t"]

        def _sleep(interval):
            clock["t"] += interval

        status_monitor_cli._wait_for_tracking_write_idle(
            lambda: True,  # never clears on its own
            grace_s=0.25,
            poll_interval_s=0.1,
            now=_now,
            sleep=_sleep,
        )
        # Gave up once the deadline passed -- never spun forever on a
        # permanently-busy/wedged compute.
        assert clock["t"] >= 100.25
