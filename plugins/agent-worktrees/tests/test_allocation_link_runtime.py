"""Real-ledger restart, finite worker and resident drain custody."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import allocation_link_discovery as discovery
from agent_worktrees import allocation_link_runtime as linker
from agent_worktrees import owned_worktree_remote as remote, tracking
from test_owned_worktree_transactions import call, observation, reserve
from test_resource_producer import owner as owner


@pytest.fixture
def runtime():
    clock = [100.0]
    elected = [True]
    value = linker.AllocationLinkRuntime(
        elected=lambda: elected[0], context_lock=threading.RLock(),
        wake=threading.Event(), now=lambda: clock[0],
    )
    value.activate()
    yield value, clock, elected
    value.shutdown_and_wait()


def step(runtime, *, timeout=5):
    runtime.tick()
    worker = runtime._worker
    assert worker is not None
    worker.join(timeout)
    assert not worker.is_alive()


def test_restart_discovers_original_pending_then_acknowledges_and_closes_same_claim(owner, runtime, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    value, clock, _ = runtime
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    monkeypatch.setattr(discovery, "current_records", lambda project: [tracking.load_record(path)])
    seen = []

    def receive(operation, metadata, **kwargs):
        seen.append((operation, metadata["token"]))
        return observation(metadata, "confirmed" if operation == "confirm" else "allocated")

    monkeypatch.setattr(remote, "request", receive)
    value.discover()
    assert value.health()["allocationLinker"]["pending"] == 1
    step(value)
    assert tracking.load_record(path).resources[0].producer["phase"] == "bound"
    step(value)
    assert tracking.load_record(path).resources[0].producer["phase"] == "confirmed"
    assert value.has_demand()
    clock[0] += 60
    monkeypatch.setattr(remote, "request", lambda op, meta, **kw: observation(meta, "archived"))
    step(value)
    claim = tracking.load_record(path).resources[0]
    assert claim.producer["phase"] == "closed" and not claim.is_unsettled
    assert seen == [("inspect", pending["token"]), ("confirm", pending["token"])]
    assert not value.has_demand()


def test_nonblocking_worker_keeps_record_and_project_locks_free_during_network(owner, runtime, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    value, _, _ = runtime
    entered, finish = threading.Event(), threading.Event()

    def receive(operation, metadata, **kwargs):
        entered.set()
        assert finish.wait(5)
        return observation(metadata)

    monkeypatch.setattr(remote, "request", receive)
    value.observe("p", [tracking.load_record(path)])
    try:
        value.tick()
        assert entered.wait(5)
        assert value.active_count() == 1
        assert value._context_lock.acquire(timeout=1)
        value._context_lock.release()
        with tracking._RecordLock(path, timeout=1, require_sidecar=True):
            assert tracking.load_record(path).resources[0].producer["phase"] == "pending"
        assert call(path, "inspect", {"token": pending["token"]})["phase"] == "pending"
        value.tick()
        assert value.active_count() == 1
    finally:
        finish.set()


@pytest.mark.parametrize("cutover", ["drain", "superseded"])
def test_cutover_fences_inflight_commit_and_shutdown_waits_for_exact_worker(owner, runtime, monkeypatch, cutover):
    path, _ = owner
    reserve(path)
    value, _, elected = runtime
    entered, finish = threading.Event(), threading.Event()
    before = path.read_bytes()

    def receive(operation, metadata, **kwargs):
        entered.set()
        assert finish.wait(5)
        return observation(metadata)

    monkeypatch.setattr(remote, "request", receive)
    value.observe("p", [tracking.load_record(path)])
    value.tick()
    try:
        assert entered.wait(5)
        if cutover == "drain":
            value.close_admission()
            value.activate()  # A rollback cannot admit the previous epoch's result.
        else:
            elected[0] = False
        with ThreadPoolExecutor(max_workers=1) as pool:
            done = pool.submit(value.shutdown_and_wait)
            assert not done.done()
            finish.set()
            done.result(timeout=5)
        assert value.active_count() == 0 and path.read_bytes() == before
    finally:
        finish.set()


def test_unreachable_and_missing_receiving_evidence_keep_responsibility_with_backoff(owner, runtime, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    value, clock, _ = runtime
    count = []

    def unavailable(operation, metadata, **kwargs):
        count.append(operation)
        raise RuntimeError("unavailable")

    monkeypatch.setattr(remote, "request", unavailable)
    value.observe("p", [tracking.load_record(path)])
    step(value)
    value.tick()
    assert count == ["inspect"]
    assert call(path, "inspect", {"token": pending["token"]})["phase"] == "pending"
    clock[0] += 10
    monkeypatch.setattr(remote, "request", lambda op, meta, **kw: {
        **observation(meta), "status": "unknown", "allocation_finished": False,
    })
    step(value)
    assert tracking.load_record(path).resources[0].producer["phase"] == "pending"
    assert value.health()["allocationLinker"]["errors"] == 1 and value.has_demand()
    clock[0] += 20
    monkeypatch.setattr(remote, "request", lambda op, meta, **kw: observation(meta))
    step(value)
    assert tracking.load_record(path).resources[0].producer["phase"] == "bound"
    assert value.health()["allocationLinker"]["errors"] == 0


def test_discovery_uses_registered_source_repos_as_well_as_adopted_projects(monkeypatch):
    monkeypatch.setattr(discovery.installer, "read_projects_registry", lambda **kw: {"projects": {"harness": {}}})
    monkeypatch.setattr(discovery.repos, "_repos_yaml_path", lambda: "registered")
    monkeypatch.setattr(discovery, "read_registry", lambda path, **kw: SimpleNamespace(
        repos={"source-only": object()},
    ))
    assert discovery.registered_projects() == {"harness", "source-only"}


def test_corrupt_current_ledger_is_not_silently_discovered_as_an_empty_project(tmp_path, monkeypatch):
    root = tmp_path / "worktrees"
    root.mkdir()
    (root / "owner.yaml").write_text("not: [valid", encoding="utf-8")
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project: root)
    with pytest.raises(discovery.AllocationDiscoveryError) as failure:
        discovery.current_records("p")
    assert isinstance(failure.value.__cause__, yaml.YAMLError)


def test_shared_foreign_execution_space_is_not_admitted_as_local_work(owner, runtime, monkeypatch):
    path, _ = owner
    reserve(path)
    value, _, _ = runtime
    monkeypatch.setattr(discovery, "locally_owned", lambda record: False)
    assert value.observe("p", [tracking.load_record(path)])
    value.tick()
    assert value._worker is None
    assert value.health()["allocationLinker"]["pending"] == 0


def test_missing_roster_entry_is_not_proof_of_settled_responsibility(owner, runtime, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    value, _, _ = runtime
    value.observe("p", [tracking.load_record(path)])
    value.observe("p", [])
    assert value.health()["allocationLinker"]["pending"] == 1
    assert value.has_demand()
    monkeypatch.setattr(remote, "request", lambda op, meta: observation(meta, "cancelled"))
    assert call(path, "cancel", {"token": pending["token"]})["phase"] == "closed"
    value.observe("p", [tracking.load_record(path)])
    assert value.health()["allocationLinker"]["pending"] == 0


def test_malformed_or_unverifiable_current_claim_is_visible_and_rediscovered(owner, runtime, monkeypatch):
    path, _ = owner
    reserve(path)
    value, clock, _ = runtime
    record = tracking.load_record(path)
    record.resources[0].producer["request_id"] = ""
    monkeypatch.setattr(discovery, "registered_projects", lambda: set())
    assert not value.observe("p", [record])
    assert value.health()["allocationLinker"]["errors"] == 1
    clock[0] += 30
    monkeypatch.setattr(discovery, "current_records", lambda project: [tracking.load_record(path)])
    value.discover()
    assert value.health()["allocationLinker"]["pending"] == 1
    assert value.health()["allocationLinker"]["errors"] == 0


def test_discovery_failure_is_visible_and_prevents_false_idle_exit(runtime, monkeypatch):
    value, _, _ = runtime

    def invalid():
        raise ValueError("registry is invalid")

    monkeypatch.setattr(discovery, "registered_projects", invalid)
    value.discover()
    assert value.has_demand()
    assert value.health()["allocationLinker"]["errors"] == 1
    monkeypatch.setattr(discovery, "registered_projects", lambda: set())
    value.discover()
    assert not value.has_demand()


def test_initial_discovery_is_bounded_and_drain_stops_new_admission(runtime, monkeypatch):
    value, _, _ = runtime
    seen = []
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"a", "b", "c"})
    monkeypatch.setattr(discovery, "current_records", lambda project: seen.append(project) or [])
    value.discover(budget=1)
    assert seen == ["a"] and value.has_demand()
    value.close_admission()
    value.discover()
    assert seen == ["a"]
    value.activate()
    value.discover()
    assert seen == ["a", "b", "c"] and not value.has_demand()


def test_unavailable_project_does_not_starve_other_registered_ledgers(runtime, monkeypatch):
    value, clock, _ = runtime
    seen = []
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"a", "b"})

    def records(project):
        seen.append(project)
        if project == "a":
            raise OSError("unavailable")
        return []

    monkeypatch.setattr(discovery, "current_records", records)
    value.discover(budget=1)
    value.discover(budget=1)
    value.discover(budget=1)
    assert seen == ["a", "b"] and value.has_demand()
    clock[0] += 30
    monkeypatch.setattr(discovery, "current_records", lambda project: [])
    value.discover()
    assert not value.has_demand()


def test_retirement_keeps_route_until_worker_settles_despite_repeated_interrupts(owner, runtime, monkeypatch):
    from agent_worktrees import status_monitor_retirement as retirement

    path, _ = owner
    reserve(path)
    value, _, _ = runtime
    entered, finish = threading.Event(), threading.Event()
    wait_entered = threading.Event()
    interrupts, released = [], []

    def receive(operation, metadata, **kwargs):
        entered.set()
        assert finish.wait(5)
        return observation(metadata)

    monkeypatch.setattr(remote, "request", receive)
    value.observe("p", [tracking.load_record(path)])
    value.tick()
    assert entered.wait(5)
    wait = value._finished.wait

    def interrupted_wait(*args, **kwargs):
        wait_entered.set()
        if len(interrupts) < 2:
            interrupts.append(True)
            raise KeyboardInterrupt("retirement interrupted")
        return wait(*args, **kwargs)

    monkeypatch.setattr(value._finished, "wait", interrupted_wait)
    monkeypatch.setattr(retirement.status_monitor_cutover, "clear_route_if_owner", lambda pid: released.append(True))
    monkeypatch.setattr(retirement.locks, "read_lock", lambda path: None)
    monkeypatch.setattr(retirement.resident_push, "reset", lambda: None)
    surface = SimpleNamespace(
        shutdown=lambda: None, close=lambda: None, wait_idle=lambda: None,
        wait_closed=lambda timeout: None, shutdown_and_wait=lambda: None,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        done = pool.submit(
            retirement.retire_monitor,
            close_admission=value.close_admission, repositories_runtime=surface,
            worktrees_runtime=SimpleNamespace(shutdown=lambda: None, feed=surface),
            close_request_surfaces=lambda: None, control_server=surface, receipts=surface,
            worktree_status_runtime=surface, managed_mux_runtime=surface,
            lock=path.parent / "monitor.lock", original_error=None, allocation_linker=value,
        )
        try:
            assert wait_entered.wait(5)
            assert not done.done() and released == []
        finally:
            finish.set()
        with pytest.raises(KeyboardInterrupt, match="retirement interrupted"):
            done.result(timeout=5)
    assert len(interrupts) == 2 and released == [True] and value.active_count() == 0


def test_closed_inflight_claim_cannot_recreate_orphan_retry_demand(owner, runtime, monkeypatch):
    path, _ = owner
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: path.parent)
    pending = reserve(path)
    value, _, _ = runtime
    entered, finish = threading.Event(), threading.Event()
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    value.discover()

    def receive(operation, metadata, **kwargs):
        if operation == "cancel":
            return observation(metadata, "cancelled")
        entered.set()
        assert finish.wait(5)
        return observation(metadata)

    monkeypatch.setattr(remote, "request", receive)
    value.tick()
    try:
        assert entered.wait(5)
        assert call(path, "cancel", {"token": pending["token"]})["phase"] == "closed"
        value.observe("p", [tracking.load_record(path)])
        assert value.health()["allocationLinker"]["pending"] == 0
    finally:
        finish.set()
    value._worker.join(5)
    assert not value._worker.is_alive()
    assert value._retry == {} and value._claim_errors == {}
    assert not value.has_demand()


def test_replaced_candidate_does_not_inherit_the_previous_attempt_result(owner, runtime):
    from dataclasses import replace

    path, _ = owner
    reserve(path)
    value, _, _ = runtime
    value.observe("p", [tracking.load_record(path)])
    key, old = next(iter(value._candidates.items()))
    replacement = replace(old, path=path.parent / "other.yaml")
    value._candidates[key] = replacement
    value._complete_attempt(key, old, {"phase": "closed"}, "old attempt refused")
    assert value._candidates[key] == replacement
    assert value._retry == {} and value._claim_errors == {}


def test_current_loop_governance_is_rechecked_after_network_before_commit(owner, runtime, monkeypatch):
    from agent_worktrees import __main__ as core, loop_governance

    path, _ = owner
    reserve(path)
    before = path.read_bytes()
    value, _, _ = runtime
    allowed = [True]
    checkpoints = []

    def decision(checkpoint):
        checkpoints.append(checkpoint)
        return {"status": "ready" if allowed[0] else "deferred", "reason": "runtime policy changed"}

    governance = loop_governance.LoopGovernance(helper=decision)

    def authority():
        core._status_monitor_recheck(governance, "pre-mutation:allocation-link")
        return True

    def receive(operation, metadata, **kwargs):
        allowed[0] = False
        return observation(metadata)

    monkeypatch.setattr(value, "_elected", authority)
    monkeypatch.setattr(remote, "request", receive)
    value.observe("p", [tracking.load_record(path)])
    step(value)
    assert checkpoints == ["pre-mutation:allocation-link"] * 2
    assert path.read_bytes() == before
    assert value.health()["allocationLinker"]["errors"] == 1
