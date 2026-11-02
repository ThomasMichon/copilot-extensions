from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from zdd import process_exit

from agent_worktrees import runtime_monitor_retirements as retirements


@pytest.fixture
def native_original_exit():
    return process_exit.observe_original_exit


@pytest.mark.parametrize("nested", [False, True])
def test_retirement_runtime_helpers_follow_the_retained_slot(tmp_path, monkeypatch, nested):
    slot = tmp_path / "versions" / "current"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    (bootstrap / "versioned_runtime.py").write_text("MARKER = 'retained'\n", encoding="utf-8")
    prefix = slot / ".venv" if nested else slot
    prefix.mkdir(exist_ok=True)
    monkeypatch.setattr(retirements.sys, "prefix", str(prefix))
    monkeypatch.setitem(sys.modules, "_monitor_retirement_runtime", None)
    retirements._runtime.cache_clear()
    try:
        assert retirements._runtime().MARKER == "retained"
    finally:
        retirements._runtime.cache_clear()


@pytest.fixture
def retained(tmp_path, monkeypatch, native_original_exit):
    helper = Path(__file__).resolve().parents[3] / "libs/versioned-runtime/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("retirement_test_runtime", helper)
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    root = tmp_path / "runtime"
    for version in ("legacy", "current"):
        slot = root / "versions" / version
        slot.mkdir(parents=True)
        runtime.mark_complete(root, version, payload_hash=version)
    (root / "versions" / "dev").mkdir()
    runtime.activate(root, "current", link_free=True)
    identities, alive = {101: "1001"}, {101}

    def native_exit(pid, expected):
        # These are admitted synthetic native observations, never host PID probes.
        if pid != 101:
            raise ValueError("fixture has no native evidence for this PID")
        if pid not in alive:
            return True
        if pid not in identities:
            raise PermissionError("fixture native identity is unavailable")
        return identities[pid] != expected

    monkeypatch.setattr(process_exit, "observe_original_exit", native_exit)
    monkeypatch.setattr(retirements.locks, "process_start_time", identities.get)
    monkeypatch.setattr(retirements.diagnostics, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(retirements, "_runtime", lambda: runtime)
    return root, runtime, identities, alive


def test_unretained_legacy_slot_survives_installer_exit_and_default_gc(retained):
    root, runtime, identities, alive = retained
    predecessor = {"pid": 101, "start": "1001", "slot": "legacy", "release_claim": None}
    assert retirements.reserve(root, predecessor, runtime=runtime, committed=True)
    # This predecessor holds no process lease; the durable lifecycle reference protects it.
    with runtime._kernel_lease(root, "legacy", kind="retention", shared=False, timeout=0):
        pass
    runtime.gc(root, min_age_days=0)
    assert (root / "versions" / "legacy").is_dir()
    restarted_owner = retirements.ResidentRetirements(root)
    restarted_owner.refresh()
    assert restarted_owner.pending and restarted_owner.error is None
    identities.pop(101)
    alive.remove(101)
    restarted_owner.refresh()
    assert not restarted_owner.pending
    assert "legacy" in runtime.gc(root, min_age_days=0)


def test_unknown_native_identity_keeps_demand_and_runtime_references(retained):
    root, runtime, identities, _alive = retained
    retirements.reserve(
        root,
        {"pid": 101, "start": "1001", "slot": "legacy", "release_claim": None},
        runtime=runtime,
        committed=True,
    )
    identities.pop(101)
    with pytest.raises(RuntimeError, match="native identity"):
        retirements.reconcile(root, runtime=runtime)
    state = retirements.ResidentRetirements(root)
    state.refresh()
    assert state.pending and "native identity" in state.error
    assert runtime._leases.read_runtime_references(root)


def test_dev_claim_is_released_only_after_the_original_resident_exits(retained):
    root, runtime, identities, alive = retained
    claim = runtime.claim_dev(root, "claiming-worktree", previous_version="current")
    retirements.reserve(
        root,
        {"pid": 101, "start": "1001", "slot": "dev", "release_claim": claim},
        runtime=runtime,
        committed=True,
    )
    assert retirements.pending_slot(root, "dev")
    assert retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) == claim
    identities.pop(101)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) is None
    assert not retirements.pending_slot(root, "dev")


@pytest.mark.parametrize("same_owner", [False, True])
def test_changed_dev_claim_is_not_silently_released(retained, same_owner):
    root, runtime, identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    retirements.reserve(
        root,
        {"pid": 101, "start": "1001", "slot": "dev", "release_claim": claim},
        runtime=runtime,
        committed=True,
    )
    owner = "original-owner" if same_owner else "new-owner"
    runtime.claim_dev(
        root, owner, host="new-claim-host", previous_version="current", force=not same_owner
    )
    identities.pop(101)
    alive.remove(101)
    state = retirements.ResidentRetirements(root)
    state.refresh()
    assert state.pending and "Dev claim changed" in state.error
    assert runtime.read_dev_claim(root, strict=True)["owner"] == owner
    assert runtime._leases.read_runtime_references(root)


def test_admission_observes_retirement_without_waiting_for_reference_cleanup(retained):
    root, runtime, identities, alive = retained
    retirements.reserve(
        root,
        {"pid": 101, "start": "1001", "slot": "legacy", "release_claim": None},
        runtime=runtime,
        committed=True,
    )
    identities.pop(101)
    alive.remove(101)
    assert not retirements.reconcile(root, cleanup=False)
    assert runtime._leases.read_runtime_references(root)
    assert not retirements.reconcile(root, runtime=runtime)
    assert not runtime._leases.read_runtime_references(root)


def _predecessor(pid=101, *, slot="legacy", claim=None):
    return {"pid": pid, "start": "1001", "slot": slot, "release_claim": claim}


def test_capture_uses_authenticated_route_not_stale_lock_slot(retained, monkeypatch):
    from agent_worktrees import status_monitor_cutover

    root, _runtime, _identities, _alive = retained
    monkeypatch.setattr(
        retirements.routing, "read_active_endpoint", lambda *a, **kw: SimpleNamespace(pid=101)
    )
    monkeypatch.setattr(
        retirements.locks,
        "read_lock",
        lambda *a: {
            "pid": 202,
            "prefix": str(root / "versions" / "current"),
        },
    )
    monkeypatch.setattr(
        status_monitor_cutover, "routed_monitor_identity", lambda root: (101, "1001", "legacy")
    )
    assert retirements.capture(root) == _predecessor()
    monkeypatch.setattr(
        status_monitor_cutover, "routed_monitor_identity", lambda root: (101, "1001", None)
    )
    with pytest.raises(RuntimeError, match="retention is unconfirmed"):
        retirements.capture(root)


def test_explicit_source_identity_is_not_assigned_an_installation_slot(retained):
    root, _runtime, _identities, _alive = retained
    assert retirements.capture(root, identity=(101, "1001", None)) == _predecessor(slot=None)
    with pytest.raises(RuntimeError, match="identity changed"):
        retirements.capture(root, identity=(101, "9999", "legacy"))


def test_capture_qualifies_nonversioned_source_lock_by_native_birth(retained, monkeypatch):
    root, _runtime, _identities, _alive = retained
    monkeypatch.setattr(retirements.routing, "read_active_endpoint", lambda *a, **kw: None)
    record = {"pid": 101, "start_time": "1001", "prefix": str(root / "source-runtime")}
    monkeypatch.setattr(retirements.locks, "read_lock", lambda *a: record)
    assert retirements.capture(root) == _predecessor(slot=None)
    record["start_time"] = "9999"
    with pytest.raises(RuntimeError, match="retention is unconfirmed"):
        retirements.capture(root)


def test_inventory_rejection_is_nonmutating_and_existing_updates_are_allowed(retained):
    root, runtime, _identities, _alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    directory = root / "status-monitor-retirements"
    directory.mkdir()
    for pid in range(101, 357):
        record = {"version": 1, **_predecessor(pid, slot="dev" if pid == 101 else None)}
        path = directory / (retirements._owner(pid, "1001").split(":")[1] + ".json")
        retirements._write_record(path, record)
    assert retirements.reserve(root, _predecessor(slot="dev", claim=claim), runtime=runtime)
    references = runtime._leases.read_runtime_references(root)
    assert references and retirements.pending_claim(root, claim)
    with pytest.raises(ValueError, match="inventory exceeds"):
        retirements.reserve(root, _predecessor(357), runtime=runtime)
    assert len(retirements._records(root)) == 256
    assert runtime._leases.read_runtime_references(root) == references


def test_failed_reference_publication_leaves_no_intent_to_poison_rollback(retained, monkeypatch):
    root, runtime, _identities, _alive = retained
    publish = runtime._leases.replace_runtime_references
    with monkeypatch.context() as scoped:

        def fail(*args, **kwargs):
            raise OSError("reference write interrupted")

        scoped.setattr(runtime._leases, "replace_runtime_references", fail)
        with pytest.raises(OSError, match="reference write interrupted"):
            retirements.reserve(root, _predecessor(), runtime=runtime)
    assert not retirements._records(root)
    assert not runtime._leases.read_runtime_references(root)
    assert not retirements.cancel(root, _predecessor(), runtime=runtime)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime._leases.replace_runtime_references is publish


def test_record_write_failure_keeps_discoverable_barrier_across_real_gc(retained, monkeypatch):
    root, runtime, identities, alive = retained

    def fail(*args):
        raise OSError("record write interrupted")

    monkeypatch.setattr(retirements, "_write_record", fail)
    with pytest.raises(OSError, match="record write interrupted"):
        retirements.reserve(root, _predecessor(), runtime=runtime)
    assert not retirements._records(root)
    assert runtime._leases.read_runtime_references(root)
    with runtime._kernel_lease(root, "legacy", kind="retention", shared=False, timeout=0):
        pass
    runtime.gc(root, min_age_days=0)
    assert (root / "versions" / "legacy").is_dir()
    assert retirements.pending_slot(root, "legacy")
    assert not retirements.pending_slot(root, "legacy", include_cancelled=False)
    assert retirements.reconcile(root, runtime=runtime)
    identities.pop(101)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert not runtime._leases.read_runtime_references(root)
    assert "legacy" in runtime.gc(root, min_age_days=0)


def test_reference_write_failure_after_publication_remains_discoverable(retained, monkeypatch):
    root, runtime, _identities, _alive = retained
    publish = runtime._leases.replace_runtime_references
    with monkeypatch.context() as scoped:

        def fail(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError("publication outcome unknown")

        scoped.setattr(runtime._leases, "replace_runtime_references", fail)
        with pytest.raises(OSError, match="outcome unknown"):
            retirements.reserve(root, _predecessor(), runtime=runtime)
    assert not retirements._records(root)
    assert runtime._leases.read_runtime_references(root)
    assert retirements.reserve(root, _predecessor(), runtime=runtime)


def test_canonical_reference_is_durable_before_full_intent_is_fsynced(retained, monkeypatch):
    root, runtime, _identities, _alive = retained
    fsync = os.fsync
    events = []

    def sync(fd):
        events.append("sync")
        fsync(fd)

    publish = runtime._leases.replace_runtime_references

    def references(*args, **kwargs):
        assert not retirements._records(root)
        publish(*args, **kwargs)
        events.append("reference")

    write = retirements._write_record

    def intent(path, record):
        assert "sync" in events and "reference" in events
        assert runtime._leases.read_runtime_references(root)
        events.append("intent")
        write(path, record)

    monkeypatch.setattr(retirements.os, "fsync", sync)
    monkeypatch.setattr(runtime._leases, "replace_runtime_references", references)
    monkeypatch.setattr(retirements, "_write_record", intent)
    assert retirements.reserve(root, _predecessor(), runtime=runtime)
    assert events[-1] == "sync"


def test_reused_own_pid_settles_original_identity(retained, monkeypatch):
    root, runtime, identities, _alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime, committed=True)
    monkeypatch.setattr(retirements.os, "getpid", lambda: 101)
    identities[101] = "2002"
    assert not retirements.reconcile(root, runtime=runtime)
    assert not retirements._records(root)
    assert not runtime._leases.read_runtime_references(root)


def test_native_exit_settles_even_when_birth_remains_readable(retained):
    root, runtime, _identities, alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime, committed=True)
    alive.remove(101)
    assert not retirements.reconcile(root, cleanup=False)
    assert not retirements.reconcile(root, runtime=runtime)
    assert not retirements._records(root)


def test_exited_intent_without_reference_does_not_recreate_deletion_custody(retained):
    root, runtime, _identities, alive = retained
    directory = root / "status-monitor-retirements"
    directory.mkdir()
    path = directory / (retirements._owner(101, "1001").split(":")[1] + ".json")
    retirements._write_record(path, {"version": 1, **_predecessor(), "state": "retiring"})
    alive.remove(101)
    assert "legacy" in runtime.gc(root, min_age_days=0)
    assert not retirements.reconcile(root, runtime=runtime)
    assert not retirements._records(root)
    assert not runtime._leases.read_runtime_references(root)


@pytest.mark.parametrize("state", ["live", "unknown", "self"])
def test_unsettled_intent_repairs_missing_reference_after_native_observation(
    retained, monkeypatch, state
):
    root, runtime, identities, _alive = retained
    directory = root / "status-monitor-retirements"
    directory.mkdir()
    path = directory / (retirements._owner(101, "1001").split(":")[1] + ".json")
    retirements._write_record(path, {"version": 1, **_predecessor(), "state": "retiring"})
    if state == "unknown":
        identities.pop(101)
        with pytest.raises(RuntimeError, match="native identity"):
            retirements.reconcile(root, runtime=runtime)
    else:
        if state == "self":
            monkeypatch.setattr(retirements.os, "getpid", lambda: 101)
        assert retirements.reconcile(root, runtime=runtime) is (state == "live")
    assert runtime._leases.read_runtime_references(root)
    assert retirements.pending_slot(root, "legacy")


def test_own_pid_with_matching_birth_still_settles_positive_native_exit(retained, monkeypatch):
    root, runtime, _identities, alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime, committed=True)
    alive.remove(101)
    monkeypatch.setattr(retirements.os, "getpid", lambda: 101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert not retirements._records(root)


def test_cancel_preserves_dev_claim_and_pending_claim_is_exact_and_readonly(retained):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = _predecessor(slot="dev", claim=claim)
    retirements.reserve(root, predecessor, runtime=runtime)
    assert retirements.pending_claim(root, claim)
    assert not retirements.pending_claim(root, {**claim, "host": "changed-host"})
    with pytest.raises(RuntimeError, match="custody changed"):
        retirements.cancel(root, _predecessor(slot="dev"), runtime=runtime)
    assert retirements.pending_claim(root, claim)
    alive.remove(101)
    assert retirements.cancel(root, predecessor, runtime=runtime)
    assert not retirements.pending_claim(root, claim)
    assert runtime.read_dev_claim(root, strict=True) == claim
    assert not runtime._leases.read_runtime_references(root)
    assert not retirements.cancel(root, predecessor, runtime=runtime)


@pytest.fixture
def prepared_release(retained, monkeypatch):
    from single_instance_lease import SingleInstance

    root, runtime, identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    runtime.mark_complete(root, "dev", payload_hash="dev", dev_owner=claim["owner"])
    runtime.activate(root, "dev", link_free=True, dev_owner=claim["owner"])
    retirements.reserve(root, _predecessor(slot="dev", claim=claim), runtime=runtime)
    endpoint = retirements.routing.Endpoint("127.0.0.1", 61001, 101, "old", 1, "1001")
    table = {"active": endpoint.to_dict(), "previous": None}
    monkeypatch.setattr(retirements.routing, "read_table", lambda *a, **kw: table)
    with SingleInstance(root / "status-monitor-routing", lock_name="cutover.lock") as guard:
        yield root, runtime, identities, alive, claim, table, guard


@pytest.mark.parametrize("exited", [False, True])
def test_prepared_release_retry_cancels_durably_without_grant_or_claim_release(
    prepared_release, monkeypatch, exited,
):
    root, runtime, _identities, alive, claim, table, guard = prepared_release
    references = runtime._leases.read_runtime_references(root)
    syncs = []
    sync = retirements.os.fsync
    monkeypatch.setattr(
        retirements.os, "fsync", lambda fd: (syncs.append(fd), sync(fd))[-1],
    )
    monkeypatch.setattr(
        runtime, "_release_dev", lambda *a: pytest.fail("Retry released the dev claim"),
    )
    if exited:
        alive.remove(101)
        table["active"] = None
    assert retirements.cancel_prepared_release(root, claim, guard=guard, runtime=runtime)
    assert syncs
    assert runtime.current_version(root) == "dev"
    assert runtime.read_dev_claim(root, strict=True) == claim
    assert not retirements.pending_claim(root, claim)
    assert not retirements.pending_slot(root, "dev", include_cancelled=False)
    if exited:
        assert not retirements._records(root)
        assert not runtime._leases.read_runtime_references(root)
    else:
        assert retirements._records(root)[0][1]["state"] == "cancelled"
        assert runtime._leases.read_runtime_references(root) == references
        assert retirements.pending_slot(root, "dev")
        assert retirements.reconcile(root, runtime=runtime)
        assert runtime.read_dev_claim(root, strict=True) == claim
        assert runtime._leases.read_runtime_references(root) == references


@pytest.mark.parametrize("blocker", [
    "committed", "foreign-record", "foreign-current-claim", "numbered-current",
    "changed-route", "gone-with-foreign-route", "missing-live-route",
    "unknown-witness", "breadcrumb", "late-breadcrumb",
])
def test_prepared_release_retry_refusal_preserves_custody(
    prepared_release, monkeypatch, blocker,
):
    from zdd import breadcrumb

    root, runtime, identities, alive, claim, table, guard = prepared_release
    witness = breadcrumb.breadcrumb_path(root / "status-monitor-routing")
    if blocker == "committed":
        retirements.commit(root, _predecessor(slot="dev", claim=claim), runtime=runtime)
    elif blocker == "foreign-record":
        path, record = retirements._records(root)[0]
        retirements._write_record(path, {
            **record, "release_claim": {**claim, "host": "foreign-host"},
        })
    elif blocker == "foreign-current-claim":
        runtime.claim_dev(root, "foreign-owner", previous_version="current", force=True)
    elif blocker == "numbered-current":
        runtime.activate(root, "current", link_free=True)
    elif blocker in {"changed-route", "gone-with-foreign-route"}:
        table["active"]["pid"] = 202
        table["active"]["process_start_time"] = "2002"
        if blocker == "gone-with-foreign-route":
            alive.remove(101)
    elif blocker == "missing-live-route":
        table["active"] = None
    elif blocker == "unknown-witness":
        identities.pop(101)
    elif blocker == "breadcrumb":
        witness.write_text("publication in progress", encoding="utf-8")
    elif blocker == "late-breadcrumb":
        read = retirements.routing.read_table

        def reading(*args, **kwargs):
            witness.write_text("publication in progress", encoding="utf-8")
            return read(*args, **kwargs)

        monkeypatch.setattr(retirements.routing, "read_table", reading)
    records = [(path, path.read_bytes()) for path, _record in retirements._records(root)]
    references = runtime._leases.read_runtime_references(root)
    current_claim = runtime.read_dev_claim(root, strict=True)
    assert not retirements.cancel_prepared_release(root, claim, guard=guard, runtime=runtime)
    assert [(path, path.read_bytes()) for path, _record in retirements._records(root)] == records
    assert runtime._leases.read_runtime_references(root) == references
    assert runtime.read_dev_claim(root, strict=True) == current_claim


@pytest.mark.parametrize("proof", ["duck-typed", "unheld", "wrong-path"])
def test_prepared_release_retry_requires_actual_held_cutover_guard(prepared_release, proof):
    from single_instance_lease import SingleInstance

    root, runtime, _identities, _alive, claim, _table, guard = prepared_release
    if proof == "duck-typed":
        supplied = SimpleNamespace(held=True, lock_path=guard.lock_path)
    else:
        supplied = SingleInstance(
            root / "status-monitor-routing",
            lock_name="foreign.lock" if proof == "wrong-path" else "cutover.lock",
        )
    if proof == "wrong-path":
        supplied.acquire()
    references = runtime._leases.read_runtime_references(root)
    try:
        with pytest.raises(ValueError, match="held cutover guard"):
            retirements.cancel_prepared_release(root, claim, guard=supplied, runtime=runtime)
    finally:
        if proof == "wrong-path":
            supplied.release()
    assert retirements._records(root)[0][1]["state"] == "prepared"
    assert runtime._leases.read_runtime_references(root) == references
    assert runtime.read_dev_claim(root, strict=True) == claim


def test_cancel_failed_reference_cleanup_keeps_record(retained, monkeypatch):
    root, runtime, _identities, alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime)
    alive.remove(101)

    def fail(*args, **kwargs):
        raise OSError("cleanup interrupted")

    monkeypatch.setattr(runtime._leases, "replace_runtime_references", fail)
    with pytest.raises(OSError, match="cleanup interrupted"):
        retirements.cancel(root, _predecessor(), runtime=runtime)
    assert retirements.pending_slot(root, "legacy")
    assert retirements._records(root)[0][1]["state"] == "cancelled"
    assert runtime._leases.read_runtime_references(root)


def test_cancel_changed_reference_custody_is_preserved(retained):
    root, runtime, _identities, _alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime)
    owner = retirements._reference_owner(101, "1001")
    runtime._leases.replace_runtime_references(root, owner, ["current"], expected=["legacy"])
    with pytest.raises(RuntimeError, match="references changed"):
        retirements.cancel(root, _predecessor(), runtime=runtime)
    with pytest.raises(RuntimeError, match="references changed"):
        retirements.pending_slot(root, "legacy")
    assert retirements._records(root)[0][1]["slot"] == "legacy"
    assert runtime._leases.read_runtime_references(root)[owner] == ["current"]


def test_cancel_failed_record_removal_keeps_exact_retry_custody(retained, monkeypatch):
    root, runtime, _identities, alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime)
    alive.remove(101)
    path = retirements._records(root)[0][0]
    unlink = Path.unlink
    with monkeypatch.context() as scoped:

        def fail(self, *args, **kwargs):
            if self == path:
                raise OSError("record removal interrupted")
            return unlink(self, *args, **kwargs)

        scoped.setattr(Path, "unlink", fail)
        with pytest.raises(OSError, match="record removal interrupted"):
            retirements.cancel(root, _predecessor(), runtime=runtime)
    assert retirements.pending_slot(root, "legacy")
    assert retirements._records(root)[0][1]["state"] == "cancelled"
    assert not runtime._leases.read_runtime_references(root)
    assert retirements.cancel(root, _predecessor(), runtime=runtime)


def test_prepared_exit_keeps_retention_and_never_grants_dev_release(retained):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = _predecessor(slot="dev", claim=claim)
    retirements.reserve(root, predecessor, runtime=runtime)
    alive.remove(101)
    assert retirements.reconcile(root, cleanup=False)
    assert retirements.reconcile(root, runtime=runtime)
    assert retirements._records(root)[0][1]["state"] == "prepared"
    assert runtime.read_dev_claim(root, strict=True) == claim
    assert runtime._leases.read_runtime_references(root)


def test_prepared_self_keeps_demand_until_parent_commit_or_cancel(retained, monkeypatch):
    root, runtime, _identities, _alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime)
    monkeypatch.setattr(retirements.os, "getpid", lambda: 101)
    assert retirements.reconcile(root, cleanup=False)
    assert retirements.reconcile(root, runtime=runtime)
    assert retirements.cancel(root, _predecessor(), runtime=runtime)
    assert not retirements.reconcile(root, cleanup=False)
    assert not retirements.reconcile(root, runtime=runtime)
    assert retirements._records(root)[0][1]["state"] == "cancelled"
    assert runtime._leases.read_runtime_references(root)


def test_commit_enables_exact_deferred_grant_and_cannot_be_downgraded(retained):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = _predecessor(slot="dev", claim=claim)
    retirements.reserve(root, predecessor, runtime=runtime)
    with pytest.raises(RuntimeError, match="custody changed"):
        retirements.commit(root, _predecessor(slot="dev"), runtime=runtime)
    assert retirements.commit(root, predecessor, runtime=runtime)
    assert retirements.commit(root, predecessor, runtime=runtime)
    assert retirements.reserve(root, predecessor, runtime=runtime, committed=False)
    assert retirements._records(root)[0][1]["state"] == "retiring"
    with pytest.raises(RuntimeError, match="cannot be cancelled"):
        retirements.cancel(root, predecessor, runtime=runtime)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) is None
    assert not retirements._records(root)


def test_reselected_dev_refuses_retiring_claim_release(retained):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    retirements.reserve(
        root, _predecessor(slot="dev", claim=claim), runtime=runtime, committed=True
    )
    runtime.mark_complete(root, "dev", payload_hash="dev", dev_owner="original-owner")
    runtime.activate(root, "dev", link_free=True, dev_owner="original-owner")
    alive.remove(101)
    with pytest.raises(RuntimeError, match="Dev runtime is selected"):
        retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) == claim
    assert runtime._leases.read_runtime_references(root)
    assert retirements._records(root)[0][1]["state"] == "retiring"


def test_cancelled_live_runtime_keeps_code_then_exits_without_releasing_claim(retained):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = _predecessor(slot="dev", claim=claim)
    retirements.reserve(root, predecessor, runtime=runtime)
    assert retirements.cancel(root, predecessor, runtime=runtime)
    assert not retirements.pending_claim(root, claim)
    assert retirements.pending_slot(root, "dev")
    assert not retirements.pending_slot(root, "dev", include_cancelled=False)
    assert retirements.reconcile(root, runtime=runtime)
    assert runtime._leases.read_runtime_references(root)
    with pytest.raises(RuntimeError, match="cannot be committed"):
        retirements.commit(root, predecessor, runtime=runtime)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) == claim
    assert not runtime._leases.read_runtime_references(root)
    assert not retirements.pending_claim(root, claim)


def test_partial_cancel_after_old_exit_cannot_resurrect_release_grant(retained, monkeypatch):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = _predecessor(slot="dev", claim=claim)
    retirements.reserve(root, predecessor, runtime=runtime)
    alive.remove(101)
    path = retirements._records(root)[0][0]
    unlink = Path.unlink
    with monkeypatch.context() as scoped:

        def fail(self, *args, **kwargs):
            if self == path:
                raise OSError("cancelled record removal interrupted")
            return unlink(self, *args, **kwargs)

        scoped.setattr(Path, "unlink", fail)
        with pytest.raises(OSError, match="removal interrupted"):
            retirements.cancel(root, predecessor, runtime=runtime)
    assert retirements._records(root)[0][1]["state"] == "cancelled"
    assert not runtime._leases.read_runtime_references(root)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) == claim
    assert not retirements._records(root)


def test_legacy_record_without_state_does_not_invent_commitment(retained):
    root, runtime, _identities, alive = retained
    directory = root / "status-monitor-retirements"
    directory.mkdir()
    path = directory / (retirements._owner(101, "1001").split(":")[1] + ".json")
    retirements._write_record(path, {"version": 1, **_predecessor()})
    alive.remove(101)
    assert retirements._records(root)[0][1]["state"] == "prepared"
    assert retirements.reconcile(root, runtime=runtime)
    assert runtime._leases.read_runtime_references(root)


@pytest.mark.parametrize("committed", [False, True])
def test_unrevoked_slot_without_claim_blocks_new_release(retained, committed):
    root, runtime, _identities, _alive = retained
    retirements.reserve(root, _predecessor(slot="dev"), runtime=runtime, committed=committed)
    assert retirements.pending_slot(root, "dev", include_cancelled=False)
    assert not retirements.pending_claim(root, {"owner": "original-owner"})


def test_cancelled_retry_preserves_identity_slot_and_requires_fresh_prepared_intent(retained):
    root, runtime, _identities, alive = retained
    old_claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    old = _predecessor(slot="dev", claim=old_claim)
    retirements.reserve(root, old, runtime=runtime)
    assert retirements.cancel(root, old, runtime=runtime)
    references = runtime._leases.read_runtime_references(root)
    with pytest.raises(RuntimeError, match="prepared retry"):
        retirements.reserve(root, old, runtime=runtime, committed=True)
    with pytest.raises(RuntimeError, match="custody changed"):
        retirements.reserve(root, _predecessor(), runtime=runtime)
    assert retirements._records(root)[0][1]["state"] == "cancelled"
    assert retirements.reserve(root, _predecessor(slot="dev"), runtime=runtime, committed=False)
    assert runtime._leases.read_runtime_references(root) == references
    assert retirements._records(root)[0][1]["state"] == "prepared"
    assert retirements.pending_slot(root, "dev", include_cancelled=False)
    with pytest.raises(RuntimeError, match="custody changed"):
        retirements.cancel(root, old, runtime=runtime)
    new_claim = runtime.claim_dev(
        root, "original-owner", host="refreshed-host", previous_version="current"
    )
    new = _predecessor(slot="dev", claim=new_claim)
    assert retirements.reserve(root, new, runtime=runtime)
    assert retirements.pending_claim(root, new_claim)
    assert not retirements.pending_claim(root, old_claim)
    alive.remove(101)
    assert retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) == new_claim
    assert retirements.commit(root, new, runtime=runtime)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) is None


def test_completed_retirement_commit_observes_settlement_without_recreating_refs(
    retained, monkeypatch
):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = _predecessor(slot="dev", claim=claim)
    retirements.reserve(root, predecessor, runtime=runtime)
    assert retirements.commit(root, predecessor, runtime=runtime)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) is None

    def fail(*args, **kwargs):
        raise AssertionError("completed retirement must not recreate references")

    monkeypatch.setattr(runtime._leases, "replace_runtime_references", fail)
    assert retirements.commit(root, predecessor, runtime=runtime) is False
    assert not runtime._leases.read_runtime_references(root)
    assert not retirements._records(root)


@pytest.mark.parametrize("directory_exists", [False, True])
def test_missing_live_record_cannot_be_reported_as_committed(retained, directory_exists):
    root, runtime, _identities, _alive = retained
    if directory_exists:
        (root / "status-monitor-retirements").mkdir()
    with pytest.raises(RuntimeError, match="missing before confirmed exit"):
        retirements.commit(root, _predecessor(), runtime=runtime)


def test_missing_exited_record_with_reference_is_not_complete(retained):
    root, runtime, _identities, alive = retained
    retirements.reserve(root, _predecessor(), runtime=runtime, committed=True)
    retirements._records(root)[0][0].unlink()
    alive.remove(101)
    with pytest.raises(RuntimeError, match="still owns runtime references"):
        retirements.commit(root, _predecessor(), runtime=runtime)
    assert runtime._leases.read_runtime_references(root)


@pytest.mark.parametrize("changed", [False, True])
def test_missing_exited_record_with_unsettled_dev_claim_refuses_completion(retained, changed):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    if changed:
        runtime.claim_dev(
            root, "original-owner", host="refreshed-host", previous_version="current"
        )
    alive.remove(101)
    with pytest.raises(RuntimeError, match="dev grant is not settled"):
        retirements.commit(root, _predecessor(slot="dev", claim=claim), runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) is not None


def test_missing_completed_dev_record_still_refuses_selected_dev(retained, monkeypatch):
    root, runtime, _identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = _predecessor(slot="dev", claim=claim)
    retirements.reserve(root, predecessor, runtime=runtime, committed=True)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) is None
    monkeypatch.setattr(runtime, "current_version", lambda root: "dev")
    with pytest.raises(RuntimeError, match="dev grant is not settled"):
        retirements.commit(root, predecessor, runtime=runtime)


@pytest.mark.parametrize("native_state", ["self", "unknown"])
def test_orphan_barrier_never_grants_dev_release_or_artificial_self_demand(
    retained, monkeypatch, native_state
):
    root, runtime, identities, alive = retained
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")

    def fail(*args):
        raise OSError("full intent interrupted")

    monkeypatch.setattr(retirements, "_write_record", fail)
    with pytest.raises(OSError, match="intent interrupted"):
        retirements.reserve(
            root, _predecessor(slot="dev", claim=claim), runtime=runtime, committed=True
        )
    if native_state == "self":
        monkeypatch.setattr(retirements.os, "getpid", lambda: 101)
        assert not retirements.reconcile(root, runtime=runtime)
    else:
        identities.pop(101)
        state = retirements.ResidentRetirements(root)
        state.refresh()
        assert state.pending and "native identity" in state.error
    assert runtime._leases.read_runtime_references(root)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert runtime.read_dev_claim(root, strict=True) == claim
    assert not runtime._leases.read_runtime_references(root)


def test_unidentifiable_legacy_orphan_retains_barrier_and_reports_error(retained):
    root, runtime, identities, alive = retained
    owner = retirements._owner(101, "1001")
    runtime._leases.replace_runtime_references(root, owner, ["legacy"], expected=[])
    identities.pop(101)
    alive.remove(101)
    state = retirements.ResidentRetirements(root)
    state.refresh()
    assert state.pending and "Unidentifiable" in state.error
    assert runtime._leases.read_runtime_references(root)[owner] == ["legacy"]
    runtime.gc(root, min_age_days=0)
    assert (root / "versions" / "legacy").is_dir()


def _reference_bytes(runtime, owners):
    return len(
        json.dumps(
            {
                "schema": runtime._leases._REFERENCES_SCHEMA,
                "version": 1,
                "owners": owners,
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("ascii")
    )


@pytest.mark.parametrize("capacity", ["owners", "bytes"])
def test_shared_reference_capacity_rejection_cannot_poison_intent_or_cancel(retained, capacity):
    root, runtime, _identities, _alive = retained
    owners = {}
    limit = runtime._leases._REFERENCES_LIMIT
    for index in range(256):
        prefix = f"other:{index}:"
        owner = prefix if capacity == "owners" else prefix + "x" * (256 - len(prefix))
        if (
            capacity == "bytes"
            and _reference_bytes(runtime, {**owners, owner: ["current"]}) > limit
        ):
            break
        runtime._leases.replace_runtime_references(root, owner, ["current"], expected=[])
        owners[owner] = ["current"]
    if capacity == "bytes":
        padding = [
            "padding:" + "x" * length
            for length in range(249)
            if _reference_bytes(runtime, {**owners, "padding:" + "x" * length: ["current"]})
            <= limit
        ]
        if padding:
            owner = padding[-1]
            runtime._leases.replace_runtime_references(root, owner, ["current"], expected=[])
            owners[owner] = ["current"]
        assert len(owners) < 256
        assert (
            _reference_bytes(
                runtime, {**owners, retirements._reference_owner(101, "1001"): ["legacy"]}
            )
            > limit
        )
    else:
        assert len(owners) == 256
    with pytest.raises(ValueError, match="runtime reference evidence exceeds"):
        retirements.reserve(root, _predecessor(), runtime=runtime)
    assert not retirements._records(root)
    assert not retirements.cancel(root, _predecessor(), runtime=runtime)
    assert not retirements.cancel(root, _predecessor(), runtime=runtime)
    assert runtime._leases.read_runtime_references(root) == owners
    path = (
        root
        / "status-monitor-retirements"
        / (retirements._owner(101, "1001").split(":")[1] + ".json")
    )
    predecessor = _predecessor(slot="current")
    retirements._write_record(path, {"version": 1, **predecessor, "state": "prepared"})
    assert retirements.cancel(root, predecessor, runtime=runtime)
    assert not retirements._records(root)
    assert runtime._leases.read_runtime_references(root) == owners


def test_matching_legacy_hashed_reference_does_not_need_new_capacity(retained):
    root, runtime, _identities, alive = retained
    directory = root / "status-monitor-retirements"
    directory.mkdir()
    owner = retirements._owner(101, "1001")
    runtime._leases.replace_runtime_references(root, owner, ["legacy"], expected=[])
    path = directory / (owner.split(":")[1] + ".json")
    retirements._write_record(path, {"version": 1, **_predecessor(), "state": "retiring"})
    for index in range(255):
        runtime._leases.replace_runtime_references(
            root, f"other:{index}", ["current"], expected=[]
        )
    assert retirements.reserve(root, _predecessor(), runtime=runtime)
    assert len(runtime._leases.read_runtime_references(root)) == 256
    assert retirements._reference_owner(
        101, "1001"
    ) not in runtime._leases.read_runtime_references(root)
    alive.remove(101)
    assert not retirements.reconcile(root, runtime=runtime)
    assert owner not in runtime._leases.read_runtime_references(root)
@pytest.mark.parametrize("boundary", ["original", "gone", "foreign", "witness", "busy", "unknown"])
def test_abandoned_prepublication_intent_is_cancelled_only_under_original_custody(
    tmp_path, monkeypatch, boundary,
):
    from contextlib import nullcontext
    from single_instance_lease import SingleInstance
    from zdd import breadcrumb, routing

    root = tmp_path
    directory = root / "status-monitor-routing"
    predecessor = {"pid": 101, "start": "1001", "slot": None, "release_claim": None}
    monkeypatch.setattr(retirements.locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr(retirements.diagnostics, "_pid_alive", lambda pid: boundary != "gone")
    monkeypatch.setattr("zdd.diagnostics.process_start_time", lambda pid: "1001")

    def native_exit(pid, expected):
        assert (pid, expected) == (101, "1001")
        if boundary == "unknown":
            raise PermissionError("fixture native identity is unavailable")
        return boundary == "gone"

    monkeypatch.setattr(process_exit, "observe_original_exit", native_exit)
    if boundary != "gone":
        routing.publish_active(
            directory, bind="127.0.0.1", port=61001, pid=303 if boundary == "foreign" else 101,
            process_start_time="1001",
        )
    if boundary == "witness":
        breadcrumb.write_breadcrumb(directory, state="started", new_pid=202, self_retirement=True)
    if boundary == "unknown":
        monkeypatch.setattr(retirements.locks, "process_start_time", lambda pid: None)
    retirements.reserve(root, predecessor)
    original = retirements._records(root)
    held = SingleInstance(directory, service="fixture", lock_name="cutover.lock")
    with held if boundary == "busy" else nullcontext():
        retirements.cancel_abandoned_preparations(root)
    if boundary in {"original", "gone"}:
        assert not retirements._records(root)
    else:
        assert retirements._records(root) == original
    with SingleInstance(directory, service="fixture", lock_name="cutover.lock"):
        pass


def test_readonly_retirement_refresh_cannot_forget_failed_cleanup(tmp_path, monkeypatch):
    inventory = retirements.ResidentRetirements(tmp_path)
    monkeypatch.setattr(retirements, "cancel_abandoned_preparations", lambda root: None)

    def fail(root, **kwargs):
        raise RuntimeError("changed dev claim")

    monkeypatch.setattr(retirements, "reconcile", fail)
    inventory.refresh()
    assert inventory.pending and inventory.error == "changed dev claim"
    monkeypatch.setattr(retirements, "reconcile", lambda root, **kwargs: False)
    inventory.refresh(cleanup=False)
    assert inventory.pending and inventory.error == "changed dev claim"
    inventory.refresh(cleanup=True)
    assert not inventory.pending and inventory.error is None


@pytest.mark.parametrize("error", [
    PermissionError("native access denied"), OSError("native query unavailable"),
    ValueError("native evidence malformed"), NotImplementedError("native query unsupported"),
])
def test_exit_state_unknown_never_uses_tolerant_pid_or_birth_checks(monkeypatch, error):
    def unavailable(pid, expected):
        assert (pid, expected) == (101, "1001")
        raise error

    monkeypatch.setattr(process_exit, "observe_original_exit", unavailable)
    monkeypatch.setattr(
        retirements.locks, "process_start_time",
        lambda _: pytest.fail("unknown native evidence must not use a tolerant birth probe"),
    )
    monkeypatch.setattr(
        retirements.diagnostics, "_pid_alive",
        lambda _: pytest.fail("unknown native evidence must not fall back to PID liveness"),
    )
    assert retirements._exit_state(_predecessor()) is None


def _darwin_observation(monkeypatch, native_original_exit, state):
    import ctypes
    import errno

    from zdd import darwin_identity

    class Query:
        def __call__(self, pid, flavor, argument, pointer, size):
            assert (pid, flavor, argument) == (101, 3, 0)
            if state in {"denied", "unknown"}:
                ctypes.set_errno(errno.EPERM if state == "denied" else 0)
                return 0
            info = ctypes.cast(pointer, ctypes.POINTER(darwin_identity._BsdInfo)).contents
            info.pid = pid + 1 if state == "malformed" else pid
            info.status = 5 if state == "zombie" else 2
            info.start_seconds = 1001
            info.start_microseconds = 22
            return size

    monkeypatch.setattr(
        darwin_identity.ctypes, "CDLL", lambda *a, **k: SimpleNamespace(proc_pidinfo=Query()),
    )
    monkeypatch.setattr(process_exit, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(process_exit, "observe_original_exit", native_original_exit)


@pytest.mark.parametrize("state", ["zombie", "live", "denied", "unknown", "malformed"])
def test_darwin_native_exit_controls_real_retirement_custody(
    retained, monkeypatch, native_original_exit, state,
):
    root, runtime, identities, alive = retained
    identities[101] = "1001:22"
    claim = runtime.claim_dev(root, "original-owner", previous_version="current")
    predecessor = {**_predecessor(slot="dev", claim=claim), "start": "1001:22"}
    retirements.reserve(root, predecessor, runtime=runtime, committed=True)
    references = runtime._leases.read_runtime_references(root)
    _darwin_observation(monkeypatch, native_original_exit, state)
    assert 101 in alive  # A zombie remains visible to kill(0) and birth-only probes.
    inventory = retirements.ResidentRetirements(root)
    inventory.refresh()
    if state == "zombie":
        assert not inventory.pending and inventory.error is None
        assert not retirements._records(root)
        assert not runtime._leases.read_runtime_references(root)
        assert runtime.read_dev_claim(root, strict=True) is None
    else:
        assert inventory.pending
        assert bool(inventory.error) is (state != "live")
        assert runtime._leases.read_runtime_references(root) == references
        assert runtime.read_dev_claim(root, strict=True) == claim


def test_windows_native_access_denial_is_unknown_not_retired(monkeypatch, native_original_exit):
    def denied(_pid):
        raise PermissionError("native Windows process query denied")

    monkeypatch.setattr(process_exit, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(process_exit, "_windows_process", denied)
    monkeypatch.setattr(process_exit, "observe_original_exit", native_original_exit)
    monkeypatch.setattr(retirements.locks, "process_start_time", lambda _: None)
    monkeypatch.setattr(retirements.diagnostics, "_pid_alive", lambda _: False)
    assert retirements._exit_state(_predecessor()) is None


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows current-process observation")
def test_native_windows_live_self_is_not_an_exited_retirement():
    from zdd import diagnostics

    pid = os.getpid()
    birth = diagnostics.process_start_time(pid)
    assert birth is not None
    assert retirements._exit_state({"pid": pid, "start": birth}) is False
