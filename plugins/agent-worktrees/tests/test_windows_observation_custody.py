"""Injected Windows custody and unchanged ordinary POSIX routing."""

from __future__ import annotations

import subprocess
import copy
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as core, git_ops as go, push_timeout as pt


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(go, "_windows_observation", lambda: True, raising=False)
    monkeypatch.setattr(core.cfg, "project_name", lambda: "A")
    return core._StatusSegmentCache(15)


def fail(error):
    raise error


def pending(owner, path="/repo/wt", error=None):
    error = error or pt.ObservationCleanupError("pending")
    if not hasattr(error, "try_settle"):
        error.try_settle = lambda: False
    with pytest.raises(pt.ObservationCleanupError):
        owner.run_observation("A", "/repo", path, lambda: fail(error))
    return error


def test_carrier_gates_aliases_but_independent_anchor_progresses(owner):
    error = pending(owner)
    assert owner.observation_counts() == (0, 1)
    with pytest.raises(pt.ObservationCleanupPending):
        owner.run_observation("alias", "/repo", "/repo/wt/subdir", lambda: pytest.fail("alias probed"))
    assert owner.run_observation("B", "/independent", "/independent/wt", lambda: "B") == "B"
    assert owner.observation_counts() == (0, 1)
    assert next(iter(owner._observations.values())).cleanup is error


def test_pending_keeps_last_good_bytes_ages_and_survives_invalidation(owner, monkeypatch):
    path = "/repo/wt"
    key = owner._key(path)
    owner._entries[key] = (1.0, "last-good")
    owner._aliases[owner._key(path + "/nested")] = key
    pending(owner, path)
    monkeypatch.setattr(core, "_find_record_for_path", lambda *a: pytest.fail("pending lookup"))
    owner.invalidate(path)
    owner.invalidate_all()
    assert owner.get(path + "/nested") == owner.pending_segment()
    assert owner._entries[key] == (1.0, "last-good")
    assert owner.observation_counts() == (0, 1)


def test_late_callback_transfers_custody_after_close(owner):
    ticket = owner.begin_observation("A", "/repo", "/repo/wt")
    owner.close_observation_admission()
    error = pt.ObservationCleanupError("late")
    owner.retain_cleanup(ticket, error)
    owner.finish_observation(ticket)
    assert owner.observation_counts() == (0, 1)
    assert next(iter(owner._observations.values())).cleanup is error
    with pytest.raises(pt.ObservationCleanupPending):
        owner.begin_observation("B", "/B", "/B/wt")


def test_one_settlement_step_does_not_renew_D_or_start_work(owner, monkeypatch):
    sequence = iter([False, True])
    error = pt.ObservationCleanupError("retained")
    error.try_settle = lambda: next(sequence)
    pending(owner, error=error)
    monkeypatch.setattr(go.time, "monotonic", lambda: pytest.fail("renewed D"))
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("cleanup Git"))
    assert owner.settle_pending_once() == 1
    assert owner.observation_counts() == (0, 1)
    assert owner.settle_pending_once() == 0
    assert owner.observation_counts() == (0, 0)


def test_native_empty_is_not_enough_until_root_and_readers_finish():
    calls = []

    class Job:
        def try_terminate_and_confirm_empty(self):
            calls.append("job")
            return True

    class Process:
        stdin = stdout = stderr = None
        completed = False

        def communicate(self, *, timeout):
            assert timeout == 0
            calls.append("readers")
            if not self.completed:
                self.completed = True
                raise subprocess.TimeoutExpired("reader", 0)
            return "", ""

        def wait(self, *, timeout):
            assert timeout == 0
            calls.append("root")

    error = pt.ObservationCleanupError("retained")
    error.job, error.process = Job(), Process()
    assert error.try_settle() is False
    assert error.try_settle() is True
    assert calls == ["job", "readers", "readers", "root"]


def test_ordinary_posix_classification_keeps_baseline_work(monkeypatch, tmp_path):
    monkeypatch.setattr(go, "_windows_observation", lambda: False, raising=False)
    (tmp_path / ".git").mkdir()
    calls = []

    def git(*args, **options):
        calls.append((args, options))
        stdout = {"rev-parse": "tracked\n", "status": " M changed\n",
                  "rev-list": "1 0\n", "--no-pager": "subject\n"}[args[0]]
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(go, "git", git)
    info = go.classify_worktree(str(tmp_path), "tracked")
    assert info.state == go.WorktreeState.DIRTY
    assert len(calls) == 4
    assert "timeout" not in calls[0][1]
    assert [options["timeout"] for _, options in calls[1:]] == [15.0] * 3
    assert all("observation_deadline" not in options and "kill_tree" not in options
               for _, options in calls)


def test_explicit_windows_only_request_refuses_posix_before_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(go, "_windows_observation", lambda: False, raising=False)
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("unsupported probe"))
    (tmp_path / ".git").mkdir()
    with pytest.raises(pt.ObservationUnavailable):
        go.classify_worktree(str(tmp_path), "tracked", observation_deadline=99999999)


def test_snapshot_pending_never_activates_or_renders(owner, monkeypatch):
    pending(owner)
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail("pending activation"))
    result = core._resident_hook_decision(
        "snapshot", {"cwd": "/repo/wt"}, segment_cache=owner, policy=object(),
    )
    assert result == {"segment": owner.pending_segment()}
    assert owner.observation_counts() == (0, 1)


def test_segment_callback_itself_retains_failed_cleanup(owner, monkeypatch):
    failure = pt.ObservationCleanupError("render")
    monkeypatch.setattr(core, "_find_record_for_path", lambda path: None)
    monkeypatch.setattr(core, "_render_status_segment", lambda *a, **k: fail(failure))
    monkeypatch.setattr(core.cfg, "load_config", lambda **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo"),
    ))
    assert owner.get("/repo/wt") == owner.pending_segment()
    assert next(iter(owner._observations.values())).cleanup is failure
    assert owner._entries == {}


def test_bundle_raw_callback_rethrows_before_later_facts(owner, monkeypatch):
    from agent_worktrees import worktree_status_compute as compute

    failure = pt.ObservationCleanupError("bundle")
    record = SimpleNamespace(worktree_id="wt", worktree_path="/repo/wt", session_turns=0)
    monkeypatch.setattr(compute.cfg, "project_dir", lambda project: Path("unused"))
    monkeypatch.setattr(compute.tracking, "load_record_by_id", lambda *a, **k: record)
    monkeypatch.setattr(compute.cfg, "load_config", lambda **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo"),
    ))
    monkeypatch.setattr(compute.worktree_git_facts, "compute", lambda *a, **k: fail(failure))
    with pytest.raises(pt.ObservationCleanupError) as error:
        compute.compute("A", "wt", cleanup_owner=owner)
    assert error.value is failure
    assert owner.observation_counts() == (0, 1)


def _cached_bundle(project="A", worktree_id="wt"):
    return {
        "project": project, "worktree_id": worktree_id, "machine": "test",
        "started_at": 1.0, "as_of": 1.0,
        "facts": {"git_state": {"value": {"state": "wip"}, "confirmed": True, "observed_at": 1.0}},
    }


def _status_cache(owner, monkeypatch):
    from agent_worktrees.worktree_status_cache import WorktreeStatusCache

    monkeypatch.setattr(WorktreeStatusCache, "_warm_restore", lambda self: None)
    cache = WorktreeStatusCache(
        Path("not-opened.sqlite"), ttl_seconds=1, now=lambda: 10.0,
        cleanup_owner=owner,
    )
    monkeypatch.setattr(cache, "_persist", lambda *a: None)
    monkeypatch.setattr(cache, "_delete_row", lambda *a, **k: None)
    return cache


def test_bundle_request_and_background_preserve_bytes_and_allow_B(owner, monkeypatch):
    cache = _status_cache(owner, monkeypatch)
    old = _cached_bundle()
    cache._entries[("A", "wt")] = (copy.deepcopy(old), 1.0, 10.0)
    cache._entries[("B", "other")] = (_cached_bundle("B", "other"), 1.0, 10.0)
    pending(owner)
    value = cache.get_or_refresh("A", "wt", force=True, compute=lambda: pytest.fail("pending compute"))
    assert value["facts"]["git_state"]["confirmed"] is False
    assert value["as_of"] == 1.0
    assert cache._entries[("A", "wt")] == (old, 1.0, 10.0)
    calls = []

    def refresh(project, worktree_id):
        assert project == "B"
        calls.append(project)
        return _cached_bundle(project, worktree_id)

    assert cache.sweep_due(refresh=refresh) == 1
    assert calls == ["B"]
    cache._demand_ttl = 0
    cache._entries[("A", "wt")] = (old, 1.0, 1.0)
    cache.sweep_due(refresh=lambda *a: pytest.fail("pending expiry refresh"))
    assert cache._entries[("A", "wt")] == (old, 1.0, 1.0)
    assert owner.observation_counts() == (0, 1)


def test_classify_pending_returns_valid_unknown_without_probes(owner, monkeypatch):
    pending(owner)
    monkeypatch.setattr(core.tracking, "list_records", lambda *a, **k: [
        SimpleNamespace(worktree_id="wt", worktree_path="/repo/wt"),
    ])
    monkeypatch.setattr(core.cfg, "project_dir", lambda project: Path("unused"))
    monkeypatch.setattr(core.sessions, "scan_sessions_fast", lambda *a: pytest.fail("pending PID scan"))
    result = core._classify_daemon_compute(
        "classify", {"project": "A", "all": True}, cleanup_owner=owner,
    )
    assert result["wt"]["state"] == "unknown"


def test_warm_roster_gate_retains_publication_and_never_gathers(owner, monkeypatch):
    from agent_worktrees import worktrees_producer

    pending(owner)
    errors = []
    runtime = SimpleNamespace(publication_failed=lambda *args: errors.append(args))
    monkeypatch.setattr(worktrees_producer, "_publish_active_project",
                        lambda *a: pytest.fail("pending roster gather"))
    with pytest.raises(pt.ObservationCleanupPending):
        worktrees_producer.publish_active_project(runtime, cleanup_owner=owner)
    assert errors[0][0] == "A"
    assert owner.observation_counts() == (0, 1)


def test_fallback_warm_list_never_renews_pending_cache(owner, monkeypatch):
    from agent_worktrees import list_cli

    pending(owner)
    monkeypatch.setattr(list_cli.list_cache, "recent_demands", lambda *a: pytest.fail("pending gather"))
    assert list_cli._warm_list_cache_for_active_project(cleanup_owner=owner) == 0


def test_status_client_pending_data_never_enters_direct_fallback(monkeypatch):
    from agent_worktrees import worktree_status_daemon as daemon

    data = core._StatusSegmentCache.degraded_bundle("A", "wt", _cached_bundle())
    monkeypatch.setattr(daemon.wcs_client, "new_client_id", lambda: "client")
    monkeypatch.setattr(daemon.wcs_client, "request", lambda *a, **k: data)
    monkeypatch.setattr(daemon.wcs_client, "release", lambda *a, **k: None)
    result = daemon.status_via_daemon(
        {"worktree_status_endpoint": "127.0.0.1:1234", "worktree_status_token": "test"},
        key="A:wt", payload={"project": "A", "worktree_id": "wt"},
        fallback=lambda: pytest.fail("known pending direct fallback"),
    )
    assert result["facts"]["git_state"]["confirmed"] is False


def test_atomic_scope_race_and_independent_scope_progress(owner):
    entered, release = threading.Event(), threading.Event()
    errors = []
    failure = pt.ObservationCleanupError("concurrent")

    def compute():
        entered.set()
        assert release.wait(15), "test-only barrier headroom exhausted"
        raise failure

    def worker():
        try:
            owner.run_observation("A", "/repo", "/repo/wt", compute)
        except pt.ObservationCleanupError as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert entered.wait(15), "test-only barrier headroom exhausted"
        with pytest.raises(pt.ObservationCleanupPending):
            owner.begin_observation("alias", "/repo", "/repo/wt")
        assert owner.run_observation("B", "/B", "/B/wt", lambda: "B") == "B"
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive()
    assert errors == [failure] and owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("retire", ["gone", "token", "superseded", "cancel"])
def test_updater_retains_native_custody_and_lease_through_retirement(owner, monkeypatch, retire):
    import single_instance_lease
    from agent_worktrees import status_updater_cli as updater, status_monitor_runtime

    events = []
    settled = False
    rendered = 0
    attempts = 0

    class Lease:
        def __init__(self, *args, **kwargs):
            pass

        def acquire(self):
            events.append("lease")

        def release(self):
            assert settled
            events.append("released")

    error = pt.ObservationCleanupError("updater")
    if retire == "cancel":
        error.operation_error = KeyboardInterrupt()

    def step():
        nonlocal attempts, settled
        attempts += 1
        events.append("settle")
        settled = attempts == 3
        return settled

    def render(*args, **kwargs):
        nonlocal rendered
        rendered += 1
        raise error

    def state():
        assert rendered == 0 or settled, "liveness probe while cleanup pending"
        return "gone" if settled and retire == "gone" else "alive"

    def owns():
        assert rendered == 0 or settled, "ownership probe while cleanup pending"
        return not (settled and retire == "token")

    def superseded():
        assert rendered == 0 or settled, "runtime probe while cleanup pending"
        return settled and retire == "superseded"

    error.try_settle = step
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(updater, "_render_status_segment", render)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)

    def run():
        return updater._run_status_updater_loop(
            "/repo/wt", 15, state, owns, superseded,
            lambda key, value: events.append(value),
        )

    if retire == "cancel":
        with pytest.raises(KeyboardInterrupt):
            updater._with_updater_lease("session", run)
    else:
        assert updater._with_updater_lease("session", run) == 0
    assert rendered == 1 and attempts == 3
    assert events[0] == "lease" and events[-1] == "released"


def test_ordinary_posix_updater_does_not_acquire_new_lease(monkeypatch):
    import single_instance_lease
    from agent_worktrees import status_updater_cli as updater

    monkeypatch.setattr(go, "_windows_observation", lambda: False)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", lambda *a, **k: pytest.fail("new POSIX lease"))
    assert updater._with_updater_lease("session", lambda: 37) == 37


@pytest.mark.parametrize("hint", ["known", "removed", "unknown"])
def test_served_snapshot_and_warm_roster_share_one_carrier(owner, monkeypatch, hint):
    from agent_worktrees import config_cache, worktrees_producer

    pending(owner)
    current = ["A"]
    rendered, published, stale = [], [], []
    paths = {"sA": "/repo/wt", "sB": "/B/wt"}
    monkeypatch.setattr(core.cfg, "project_name", lambda: current[0])
    monkeypatch.setattr(core.cfg, "active_project", lambda: current[0])
    inside = [False]

    class ProjectLock:
        def __enter__(self):
            inside[0] = True

        def __exit__(self, *args):
            inside[0] = False

    def select(project):
        assert inside[0], "project effect moved outside existing lock"
        current[0] = project

    monkeypatch.setattr(core.cfg, "set_active_project", select)
    monkeypatch.setattr(core.cfg, "load_config", lambda *a, **k: SimpleNamespace(
        default_repo=SimpleNamespace(anchor="/repo" if current[0] == "A" else "/B"),
    ))
    monkeypatch.setattr(core.cfg, "project_dir", lambda project: Path("unused"))
    monkeypatch.setattr(config_cache, "project_scope", lambda *a, **k: nullcontext())
    monkeypatch.setattr(core, "_monitor_registry_dir", lambda: Path("unused"), raising=False)
    monkeypatch.setattr(core, "_read_monitor_registry", lambda *a: paths.copy(), raising=False)
    monkeypatch.setattr(core, "_monitor_managed_session_union", lambda *a: ({}, set(), list(paths.items())), raising=False)
    monkeypatch.setattr(core, "_manager_owned_mapping_for_session", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(core, "_publish_managed_session_status", lambda **k: None, raising=False)
    monkeypatch.setattr(core, "_monitor_mux_set", lambda *a: True, raising=False)
    monkeypatch.setattr(core, "_find_record_for_path", lambda path: SimpleNamespace(worktree_path=path))

    def render(path, **kwargs):
        assert inside[0] and kwargs["persist_title"]
        assert path == "/B/wt"
        rendered.append(path)
        return "WIP B"

    def gather(runtime, project):
        assert inside[0] and project == "B"
        published.append(project)
        return 1

    monkeypatch.setattr(core, "_render_status_segment", render)
    monkeypatch.setattr(worktrees_producer, "_publish_active_project", gather)
    runtime = SimpleNamespace(publication_failed=lambda project, error: stale.append(project))
    sessions = {owner._key(path): project for project, path in zip(("A", "B"), paths.values())}
    if hint == "removed":
        sessions.pop(owner._key("/repo/wt"))
        owner.invalidate("/repo/wt")
    elif hint == "unknown":
        paths["sC"] = "/unknown/wt"
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail(
        "project discovery before unresolved custody gate",
    ))
    assert core._monitor_sweep(
        None, "token", "prefix", set(paths),
        segment_cache=owner, session_projects=sessions, project_lock=ProjectLock(),
        roster_publisher=lambda: worktrees_producer.publish_active_project(runtime, cleanup_owner=owner),
    ) == len(paths)
    assert rendered == ["/B/wt"] and published == ["B"] and stale == ["A"]
    assert owner.observation_counts() == (0, 1)


@pytest.mark.parametrize("ignore_custody", [False, True])
def test_actual_monitor_shutdown_retains_custody_and_negative_control(
    owner, monkeypatch, ignore_custody, operation="shutdown", recovery_directory=None,
    cancellation=None,
):
    import shutil
    import single_instance_lease
    from agent_worktrees import (
        status_monitor_cli as monitor, status_monitor_runtime, status_monitor_cutover,
        worktrees_runtime, repository_map, worktree_status_daemon, mux_link,
        handoff_prompt_receipt_control as receipt_control, resident_push,
        registry_paths, loop_governance, session_catalog, pane_reaper, locks, tracking_write,
        status_monitor_drain,
    )

    settled = [False]
    attempts = []
    error = pt.ObservationCleanupError("shutdown")
    if cancellation is not None:
        error.operation_error = cancellation

    def step():
        attempts.append(1)
        settled[0] = len(attempts) == 3
        return settled[0]

    error.try_settle = step
    if cancellation is None:
        pending(owner, error=error)
    else:
        worker = threading.Thread(target=lambda: pending(owner, error=error))
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
    if ignore_custody:
        monkeypatch.setattr(owner, "observation_counts", lambda: (0, 0))

    class Lease:
        def __init__(self, *a, **k):
            pass

        def acquire(self):
            pass

    class Runtime:
        server = None
        cache = None
        feed = SimpleNamespace(active_count=lambda: 0, wait_closed=lambda *a: None)

        def __init__(self, *a, **k):
            pass

        def start(self, *a, **k):
            pass

        def close_admission(self, *a):
            pass

        def activate(self):
            pass

        def lock_extra(self):
            return {}

        def active_work_count(self):
            return 0

        def health(self):
            return {
                "worktrees": {"status": "unready" if operation == "guarded" else "shutdown"},
                "repositories": {"admitting": False, "refreshing": False},
                "capabilities": [],
            }

        def shutdown(self):
            pass

        def shutdown_and_wait(self):
            self.shutdown()

    class Control:
        port = 1234

        def __init__(self, handle, **kwargs):
            self.handle = handle

        def start(self):
            if operation == "guarded":
                import os
                from zdd import routing
                from agent_worktrees import status_monitor_recovery_contract as recovery

                owner.close_observation_admission()
                health = self.handle("health", {})
                assert recovery.CAPABILITY in health["capabilities"]
                route = routing.publish_active(
                    recovery_directory, bind="127.0.0.1", port=self.port, pid=os.getpid(),
                    process_start_time=locks.process_start_time(os.getpid()), version="fixture",
                )
                receiver = recovery.ReceiverIdentity(
                    route.pid, route.process_start_time, health["runtime_generation"],
                )
                payload = {"contractVersion": 1, "expectedReceiver": receiver.to_dict(),
                           "expectedRoute": route.to_dict()}
                wrong = copy.deepcopy(payload)
                wrong["expectedReceiver"]["runtimeGeneration"] += "-wrong"
                assert self.handle(recovery.ACTION, wrong)["status"] == "refused"
                assert owner._closing_observations
                result = self.handle(recovery.ACTION, payload)
                assert result["status"] == "undrained" and result["receiver"] == receiver.to_dict()
                assert result["route"] == route.to_dict() and not owner._closing_observations
            if operation == "undrain":
                owner.close_observation_admission()
                assert self.handle("undrain", {}) == {"draining": False}
                assert not owner._closing_observations, "undrain left observation admission closed"
            if operation == "force-race":
                owner._observations.clear()
                enter = threading.Event()
                acquired = threading.Event()
                close = owner.close_observation_admission

                def acquire():
                    assert enter.wait(15)
                    pending(owner, error=error)
                    acquired.set()

                def close_with_barrier():
                    enter.set()
                    assert acquired.wait(15)
                    close()

                monkeypatch.setattr(owner, "close_observation_admission", close_with_barrier)
                worker = threading.Thread(target=acquire)
                worker.start()
                try:
                    result = self.handle("drain", {"force": True, "timeout": 0})
                    assert result["drained"] is False, "force accepted custody admitted before close"
                finally:
                    enter.set()
                    worker.join(15)
                assert not worker.is_alive()
            if operation != "cancellation":
                assert self.handle("shutdown", {})["shutdown"]

        def current_read_count(self, *a):
            return 0

        def close(self):
            pass

    receipts = SimpleNamespace(
        close_admission=lambda: None, active_count=lambda: 0, wait_idle=lambda: None,
        open_admission=lambda: None,
        health=lambda: {"capabilities": [], "promptReceipt": {}},
    )
    if recovery_directory is not None:
        monkeypatch.setattr("agent_worktrees.runtime_identity.PROCESS_GENERATION", "fixture-runtime")
        monkeypatch.setattr(status_monitor_cutover, "routing_dir", lambda: recovery_directory)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(core, "_monitor_lock_path", lambda: Path("unused.lock"), raising=False)
    monkeypatch.setattr(core, "_aw_runtime_home", lambda: Path("unused"), raising=False)
    monkeypatch.setattr(core, "_runtime_superseded", lambda **k: False, raising=False)
    monkeypatch.setattr(core, "_StatusSegmentCache", lambda ttl: owner)
    monkeypatch.setattr(core, "_load_hook_client_module", lambda: None)
    monkeypatch.setattr(core, "_ResidentHookPolicy", lambda client: SimpleNamespace(ready=lambda: False))
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(loop_governance, "LoopGovernance", lambda: SimpleNamespace(
        recheck=lambda *a, **k: pytest.fail("monitor continued after stored cancellation"),
    ) if cancellation is not None else object())
    monkeypatch.setattr(session_catalog, "ResidentSessionReconciler", lambda **k: object())
    monkeypatch.setattr(pane_reaper, "ResidentPaneReconciler", lambda **k: object())
    monkeypatch.setattr(receipt_control, "for_runtime", lambda *a, **k: receipts)
    monkeypatch.setattr(worktrees_runtime, "WorktreesRuntime", Runtime)
    monkeypatch.setattr(repository_map, "RepositoryMapRuntime", Runtime)
    monkeypatch.setattr(worktree_status_daemon, "InProcessRuntime", Runtime)
    monkeypatch.setattr(mux_link, "InProcessRuntime", Runtime)
    monkeypatch.setattr(status_monitor_cutover, "ControlServer", Control)
    monkeypatch.setattr(status_monitor_cutover, "active_generation_for_pid", lambda pid: None)
    monkeypatch.setattr(status_monitor_cutover, "publish_route", lambda *a, **k: None)
    def clear_route(*args):
        assert settled[0], "early native custody release"

    monkeypatch.setattr(status_monitor_cutover, "clear_route_if_owner", clear_route)
    monkeypatch.setattr(resident_push, "bind", lambda *a: None)
    monkeypatch.setattr(resident_push, "reset", lambda: None)
    monkeypatch.setattr(locks, "read_lock", lambda *a: None)
    monkeypatch.setattr(locks, "lock_is_live", lambda *a: False)
    monkeypatch.setattr(locks, "write_lock", lambda *a, **k: True)
    monkeypatch.setattr(tracking_write, "has_inflight_write", lambda: False)
    monkeypatch.setattr(tracking_write, "_ensure_verb_modules_loaded", lambda: None)
    monkeypatch.setattr(tracking_write, "start_server", lambda *a: fail(OSError("injected")))
    monkeypatch.setattr(status_monitor_drain.lifecycle, "record", lambda *a, **k: True)
    from agent_worktrees import classify_daemon
    monkeypatch.setattr(classify_daemon, "start_server", lambda *a: fail(OSError("injected")))
    monkeypatch.setattr(monitor, "_wake_interruptible_wait", lambda *a: None)
    monkeypatch.setattr(monitor, "_SELF_RETIRE_MAX_GRACE_S", -1)
    monkeypatch.setattr(go, "git", lambda *a, **k: pytest.fail("shutdown Git"))
    args = SimpleNamespace(interval=15, passive=False, control_port=None)
    if ignore_custody:
        with pytest.raises(AssertionError, match="early native"):
            monitor.cmd_status_monitor(args)
    else:
        if cancellation is not None:
            with pytest.raises(type(cancellation)) as caught:
                monitor.cmd_status_monitor(args)
            assert caught.value is cancellation
        else:
            assert monitor.cmd_status_monitor(args) == 0
        assert len(attempts) == 3 and owner.observation_counts() == (0, 0)


def test_cleanup_after_verified_job_close_retries_only_readers_and_root(monkeypatch):
    from agent_procutil import owned_process

    calls = []

    class Job:
        def terminate_and_wait(self, timeout):
            calls.append("verified-job-close")

        def try_terminate_and_confirm_empty(self):
            pytest.fail("closed handle was used as a new emptiness query")

    class Process:
        stdin = stdout = stderr = None
        returncode = 0
        attempts = 0

        def communicate(self, *, timeout):
            self.attempts += 1
            if self.attempts <= 2:
                raise subprocess.TimeoutExpired("reader", timeout)
            assert timeout == 0
            calls.append("readers")
            return "", ""

        def wait(self, *, timeout):
            assert timeout == 0
            calls.append("root")

    monkeypatch.setattr(pt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(owned_process, "require_caller_owned_support", lambda: None)
    monkeypatch.setattr(pt, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(pt, "_spawn_observation", lambda *a, **k: (Process(), Job()))
    with pytest.raises(pt.ObservationCleanupError) as error:
        pt.run_bounded(["git"], cwd="worktree", env={}, timeout=15,
                       observation_deadline=go.time.monotonic() + 15)
    deadline = error.value.observation_deadline
    assert error.value.try_settle() is True
    assert error.value.observation_deadline == deadline
    assert calls == ["verified-job-close", "readers", "root"]


def test_late_additional_custody_during_settlement_is_not_dropped(owner):
    first, second = pt.ObservationCleanupError("first"), pt.ObservationCleanupError("late")
    late_steps = iter([False, True])
    second.try_settle = lambda: next(late_steps)

    def first_step():
        owner.retain_unclaimed_cleanup("A", "/repo/wt", second)
        return True

    first.try_settle = first_step
    pending(owner, error=first)
    owner.close_observation_admission()
    assert owner.settle_pending_once() == 1
    assert owner.settle_pending_once() == 1
    assert owner.settle_pending_once() == 0


def test_classify_pending_protocol_data_never_runs_direct_fallback(monkeypatch):
    from agent_worktrees import classify_daemon

    data = {"wt": {"state": "unknown", "fetch_requested": False, "fetch_failed": False}}
    monkeypatch.setattr(classify_daemon.wcs_client, "request", lambda *a, **k: data)
    result = classify_daemon.classify_via_daemon(
        {"classify_endpoint": "127.0.0.1:1234", "classify_token": "test"},
        key="A", payload={"project": "A"},
        fallback=lambda: pytest.fail("known pending classification fallback"),
    )
    assert result is data


@pytest.mark.parametrize("timeout_network", [False, True])
def test_ordinary_posix_default_resolution_keeps_baseline_options(monkeypatch, timeout_network):
    from agent_worktrees import status_bar_cli

    monkeypatch.setattr(go, "_windows_observation", lambda: False)
    monkeypatch.setattr(status_bar_cli, "_core_helper", lambda name, local: local)
    calls = []

    def git(*args, **options):
        calls.append((args, options))
        if timeout_network and args[0] == "ls-remote":
            raise subprocess.TimeoutExpired(args, 10)
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(go, "git", git)
    assert status_bar_cli._resolve_remote_default_branch(
        ".", "origin", config_default="main", allow_remote=True,
    ) is None
    assert [args[0] for args, _ in calls] == [
        "rev-parse", "symbolic-ref", "ls-remote", "rev-parse", "rev-parse",
    ]
    assert [options.get("timeout") for _, options in calls] == [None, None, 10, None, None]
    assert all("observation_deadline" not in options and "kill_tree" not in options
               for _, options in calls)


def test_isolated_consumer_import_has_the_actual_shared_settlement_api():
    import agent_procutil

    assert callable(agent_procutil.JobHandle.try_terminate_and_confirm_empty)


def test_explicit_windows_deadline_refuses_posix_before_detect_override(monkeypatch):
    from agent_worktrees import status_bar_cli

    monkeypatch.setattr(go, "_windows_observation", lambda: False)
    monkeypatch.setattr(status_bar_cli, "_core_helper", lambda *a: pytest.fail(
        "unsupported observation reached override",
    ))
    with pytest.raises(pt.ObservationUnavailable):
        status_bar_cli._detect_upstream_branch(
            ".", "origin", "main", observation_deadline=123,
        )


def test_updater_negative_control_detects_swallowed_custody(monkeypatch):
    from agent_worktrees import status_updater_cli as updater

    original = pt.ObservationCleanupError("retained")
    rendered = []

    class WrongCatch(subprocess.SubprocessError):
        pass

    def state():
        assert not rendered, "continued observing after discarded cleanup custody"
        return "alive"

    def render(*args, **kwargs):
        rendered.append(1)
        raise original

    monkeypatch.setattr(pt, "ObservationCleanupError", WrongCatch)
    monkeypatch.setattr(updater, "_render_status_segment", render)
    monkeypatch.setattr(updater.time, "sleep", lambda *a: None)
    with pytest.raises(AssertionError, match="discarded cleanup custody"):
        updater._run_status_updater_loop(
            "/repo/wt", 15, state, lambda: True, lambda: False, lambda *a: None,
        )


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_settlement_preserves_every_ticket(owner, interruption):
    first = pt.ObservationCleanupError("A")
    second = pt.ObservationCleanupError("B")
    first.try_settle = lambda: fail(interruption())
    second.try_settle = lambda: True
    pending(owner, error=first)
    with pytest.raises(pt.ObservationCleanupError):
        owner.run_observation("B", "/B", "/B/wt", lambda: fail(second))
    with pytest.raises(interruption):
        owner.settle_pending_once()
    assert owner.observation_counts() == (0, 2)
    assert all(not ticket.settling for ticket in owner._observations.values())
    first.try_settle = lambda: True
    assert owner.settle_pending_once() == 0


@pytest.mark.parametrize("path", ["/repo/wt2", "/unresolved/wt"])
def test_snapshot_gates_sibling_and_unknown_path_before_discovery(owner, monkeypatch, path):
    pending(owner)
    monkeypatch.setattr(core, "_hook_payload_cwd", lambda payload: path)
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: pytest.fail(
        "snapshot discovered Git before retained project hint",
    ))
    assert core._resident_hook_decision(
        "snapshot", {}, segment_cache=owner, policy=object(),
    ) == {"segment": owner.pending_segment()}
    assert owner.observation_counts() == (0, 1)


def test_blocked_project_cannot_fill_refresh_sample_cap(owner, monkeypatch):
    cache = _status_cache(owner, monkeypatch)
    pending(owner)
    for index in range(cache._max_refresh_per_sweep):
        key = ("A", f"blocked-{index}")
        cache._entries[key] = (_cached_bundle(*key), 1.0, 10.0)
    cache._entries[("B", "healthy")] = (_cached_bundle("B", "healthy"), 2.0, 10.0)
    calls = []

    def refresh(project, worktree_id):
        calls.append(project)
        return _cached_bundle(project, worktree_id)

    assert cache.sweep_due(refresh=refresh) == 1
    assert calls == ["B"]
    assert owner.observation_counts() == (0, 1)


def test_updater_replacement_waits_for_previous_generation_release(monkeypatch):
    import single_instance_lease
    from agent_worktrees import status_updater_cli as updater, status_monitor_runtime

    calls = []
    released = [False]

    class Lease:
        def __init__(self, *a, **k):
            pass

        def acquire(self):
            calls.append("acquire")
            if not released[0]:
                raise single_instance_lease.AlreadyRunningError(Path("predecessor"), 101)

        def release(self):
            calls.append("replacement-release")

    def wait(seconds):
        calls.append("predecessor-release")
        released[0] = True

    monkeypatch.setattr(go, "_windows_observation", lambda: True)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(updater.time, "sleep", wait)
    assert updater._with_updater_lease("session", lambda: calls.append("replacement-run")) is None
    assert calls == [
        "acquire", "predecessor-release", "acquire", "replacement-run", "replacement-release",
    ]


def test_actual_monitor_undrain_reopens_without_dropping_pending(owner, monkeypatch):
    test_actual_monitor_shutdown_retains_custody_and_negative_control(
        owner, monkeypatch, False, operation="undrain",
    )


def test_actual_monitor_force_sample_follows_admission_close(owner, monkeypatch):
    test_actual_monitor_shutdown_retains_custody_and_negative_control(
        owner, monkeypatch, False, operation="force-race",
    )


def test_two_updater_generations_do_not_lose_replacement_during_supersession(monkeypatch):
    import single_instance_lease
    from agent_worktrees import status_updater_cli as updater, status_monitor_runtime

    held = threading.Lock()
    entered, retire, retired = threading.Event(), threading.Event(), threading.Event()
    events, failures = [], []

    class Lease:
        def __init__(self, *a, **k):
            pass

        def acquire(self):
            if not held.acquire(blocking=False):
                raise single_instance_lease.AlreadyRunningError(Path("previous"), 101)

        def release(self):
            events.append("release")
            held.release()

    def previous_work():
        entered.set()
        assert retire.wait(15)
        events.append("previous-cleanup-confirmed")

    def previous():
        try:
            updater._with_updater_lease("same-session", previous_work)
        except BaseException as error:
            failures.append(error)
        finally:
            retired.set()

    def wait(seconds):
        retire.set()
        assert retired.wait(15)

    monkeypatch.setattr(go, "_windows_observation", lambda: True)
    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: Path("unused"))
    monkeypatch.setattr(updater.time, "sleep", wait)
    worker = threading.Thread(target=previous)
    worker.start()
    try:
        assert entered.wait(15)
        updater._with_updater_lease("same-session", lambda: events.append("replacement"))
    finally:
        retire.set()
        worker.join(15)
    assert not worker.is_alive() and not failures
    assert events == ["previous-cleanup-confirmed", "release", "replacement", "release"]


def test_actual_monitor_guarded_dispatch_uses_existing_undrain_and_native_route(owner, monkeypatch, tmp_path):
    test_actual_monitor_shutdown_retains_custody_and_negative_control(
        owner, monkeypatch, False, operation="guarded", recovery_directory=tmp_path,
    )
