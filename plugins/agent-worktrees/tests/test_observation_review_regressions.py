"""Fail-first resident continuation regressions from the retained-custody review."""
from __future__ import annotations

import argparse
import copy
import subprocess
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_windows_observation_custody as custody
from agent_worktrees import __main__ as core, git_ops as go, push_timeout as pt

owner = custody.owner


def test_delayed_settled_error_cannot_attach_to_new_active_ticket(owner):
    error = pt.ObservationCleanupError("old native cleanup")
    caught, release = threading.Event(), threading.Event()
    failures = []

    def settle():
        error._settled = True
        return True

    error.try_settle = settle

    def old_callback():
        try:
            try:
                owner.run_observation("A", "/repo", "/repo/wt", lambda: custody.fail(error))
            except pt.ObservationCleanupError:
                caught.set()
                assert release.wait(5)
                owner.retain_unclaimed_cleanup("A", "/repo/wt", error)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=old_callback)
    worker.start()
    ticket = None
    try:
        assert caught.wait(5)
        assert owner.settle_pending_once() == 0
        ticket = owner.begin_observation("A", "/repo", "/repo/wt")
        release.set()
        worker.join(5)
        assert not worker.is_alive() and not failures
        owner.settle_pending_once()
        assert owner.observation_counts() == (1, 0)
        assert owner._observations[ticket.key] is ticket and ticket.cleanup is None
    finally:
        release.set()
        worker.join(5)
        if ticket is not None:
            owner.finish_observation(ticket)


@pytest.mark.parametrize("wrong", ["project", "worktree_id"])
@pytest.mark.parametrize("phase", ["pending", "compute"])
def test_pending_bundle_rejects_wrong_cached_identity_without_altering_storage(owner, monkeypatch, wrong, phase):
    cache = custody._status_cache(owner, monkeypatch)
    previous = custody._cached_bundle()
    previous[wrong] = "different"
    stored = (copy.deepcopy(previous), 1.0, 10.0)
    cache._entries[("A", "wt")] = stored
    if phase == "pending":
        custody.pending(owner)

    def compute():
        assert phase == "compute"
        custody.pending(owner)
        raise pt.ObservationCleanupPending("scope became pending")

    result = cache.get_or_refresh("A", "wt", force=True, compute=compute)
    assert result["project"] == "A" and result["worktree_id"] == "wt"
    assert result["as_of"] == 0.0
    assert all(fact["value"] is None and fact["confirmed"] is False for fact in result["facts"].values())
    assert cache._entries[("A", "wt")] == stored


def test_pending_snapshot_marks_cached_final_unknown_but_keeps_bytes_and_age(owner, monkeypatch):
    key = owner._key("/repo/wt")
    saved = (123.0, "FINAL")
    owner._entries[key] = saved
    custody.pending(owner)
    monkeypatch.setattr(core, "_hook_payload_cwd", lambda payload: "/repo/wt")
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail("pending discovery"))
    result = core._resident_hook_decision("snapshot", {}, segment_cache=owner, policy=object())
    assert result == {"segment": owner.pending_segment()}
    assert owner._entries[key] == saved


def test_fallback_handoff_scan_blocks_pending_project_and_keeps_independent_owner(owner, monkeypatch):
    from agent_worktrees import config_cache

    custody.pending(owner)
    current, acted = ["A"], []
    monkeypatch.setattr(core.cfg, "active_project", lambda: current[0])
    monkeypatch.setattr(core.cfg, "project_name", lambda: current[0])
    monkeypatch.setattr(core.cfg, "set_active_project", lambda project: current.__setitem__(0, project))
    monkeypatch.setattr(core.cfg, "tracking_dir", lambda: Path("unused"))
    monkeypatch.setattr(config_cache, "project_scope", lambda *a, **k: nullcontext())
    monkeypatch.setattr(core, "_monitor_registry_dir", lambda: Path("unused"), raising=False)
    monkeypatch.setattr(core, "_read_monitor_registry", lambda *a: {}, raising=False)
    monkeypatch.setattr(core, "_monitor_list_sessions", lambda *a: {}, raising=False)
    monkeypatch.setattr(core, "_monitor_managed_session_union", lambda *a: ({}, set(), []), raising=False)
    monkeypatch.setattr(core, "_warm_list_cache_for_active_project", lambda **k: 0, raising=False)
    monkeypatch.setattr(core.sessions, "has_mux_session", lambda *a: True)
    monkeypatch.setattr(core.tracking, "list_records", lambda *a: [SimpleNamespace(
        worktree_id="sibling", worktree_path="/repo/wt2" if current[0] == "A" else "/B/wt",
        pending_handoffs=["pending"],
    )])
    monkeypatch.setattr(core, "_monitor_maybe_process_handoff_record",
                        lambda *a, **k: acted.append(current[0]))
    assert core._monitor_sweep("tmux", "token", "prefix", set(), picker_projects={"A", "B"},
                               segment_cache=owner) == 0
    assert acted == ["B"]
    assert owner.observation_counts() == (0, 1)


@pytest.fixture
def updater_command(monkeypatch):
    import shutil
    import single_instance_lease
    from agent_worktrees import status_updater_cli as updater, status_monitor_runtime, update_stage

    state = SimpleNamespace(held=False, attempts=0, allow=False, alive=True, superseded=False,
                            store={}, events=[], after_wait=lambda: None)
    monkeypatch.setattr(go, "_windows_observation", lambda: True)
    monkeypatch.setattr(updater, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "fake-mux")
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(update_stage, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(updater, "_runtime_superseded",
                        lambda *a, prefix=None, **k: state.superseded if prefix is None else prefix != "current")
    monkeypatch.setattr(updater, "_core_helper", lambda name, default: (
        (lambda path: state.events.append(("activate", state.held)))
        if name == "_activate_project_for_path" else default
    ))
    monkeypatch.setattr(updater, "_render_status_context", lambda *a, **k: "CTX")
    monkeypatch.setattr(updater, "_run_status_updater_loop", lambda *a: state.events.append("run") or 0)

    class Lease:
        def __init__(self, *a, **k):
            pass

        def acquire(self):
            state.attempts += 1
            assert state.attempts <= 3, "contender never rechecked eligibility"
            if not state.allow:
                raise single_instance_lease.AlreadyRunningError(Path("predecessor"), 101)
            state.held = True

        def release(self):
            assert state.held
            state.held = False
            state.events.append("released")

    def mux(argv, **kwargs):
        verb = argv[1]
        if verb == "has-session":
            return subprocess.CompletedProcess(argv, 0 if state.alive else 1, "", "")
        if verb == "display-message":
            return subprocess.CompletedProcess(argv, 0, state.store.get(argv[5].strip("#{}"), ""), "")
        if verb == "set-option":
            state.store[argv[4]] = argv[5]
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)

    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(subprocess, "run", mux)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: state.after_wait())
    state.invoke = lambda: updater.cmd_status_updater(argparse.Namespace(
        session="wt-fixture", mux="psmux", path="/repo/wt", interval=15,
    ))
    return state


def test_updater_command_never_discovers_git_before_acquiring_cleanup_lease(updater_command):
    state = updater_command
    state.after_wait = lambda: setattr(state, "allow", True)
    assert state.invoke() == 0
    assert ("activate", False) not in state.events
    assert state.events == [("activate", True), "run", "released"]


@pytest.mark.parametrize("change", ["current-owner", "superseded", "gone", "replacement"])
def test_updater_contention_rechecks_owner_session_and_runtime(updater_command, change):
    state = updater_command

    def wait():
        state.allow = True
        if change == "current-owner":
            state.store.update({"@aw_updater": "99999", "@aw_updater_prefix": "current"})
        elif change == "superseded":
            state.superseded = True
        elif change == "gone":
            state.alive = False

    state.after_wait = wait
    assert state.invoke() == 0
    if change == "replacement":
        assert "run" in state.events and state.attempts == 2
    else:
        assert "run" not in state.events
        assert not state.held and state.attempts == 1
