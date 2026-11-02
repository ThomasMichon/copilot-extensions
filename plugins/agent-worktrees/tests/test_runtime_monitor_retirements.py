from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import runtime_monitor_retirements as retirements


@pytest.fixture
def retained(tmp_path, monkeypatch):
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


def test_failed_reference_publication_keeps_recoverable_durable_intent(retained, monkeypatch):
    root, runtime, _identities, _alive = retained
    publish = runtime._leases.replace_runtime_references
    with monkeypatch.context() as scoped:

        def fail(*args, **kwargs):
            raise OSError("reference write interrupted")

        scoped.setattr(runtime._leases, "replace_runtime_references", fail)
        with pytest.raises(OSError, match="reference write interrupted"):
            retirements.reserve(root, _predecessor(), runtime=runtime)
    assert len(retirements._records(root)) == 1
    assert not runtime._leases.read_runtime_references(root)
    assert retirements.reconcile(root, runtime=runtime)
    assert runtime._leases.read_runtime_references(root)
    assert runtime._leases.replace_runtime_references is publish


def test_record_write_failure_publishes_no_undiscoverable_reference(retained, monkeypatch):
    root, runtime, _identities, _alive = retained

    def fail(*args):
        raise OSError("record write interrupted")

    monkeypatch.setattr(retirements, "_write_record", fail)
    with pytest.raises(OSError, match="record write interrupted"):
        retirements.reserve(root, _predecessor(), runtime=runtime)
    assert not retirements._records(root)
    assert not runtime._leases.read_runtime_references(root)


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
    assert len(retirements._records(root)) == 1
    assert runtime._leases.read_runtime_references(root)
    assert retirements.reserve(root, _predecessor(), runtime=runtime)


def test_record_intent_is_fsynced_before_reference_publication(retained, monkeypatch):
    root, runtime, _identities, _alive = retained
    fsync = os.fsync
    events = []

    def sync(fd):
        events.append("sync")
        fsync(fd)

    publish = runtime._leases.replace_runtime_references

    def references(*args, **kwargs):
        assert "sync" in events
        assert len(retirements._records(root)) == 1
        publish(*args, **kwargs)

    monkeypatch.setattr(retirements.os, "fsync", sync)
    monkeypatch.setattr(runtime._leases, "replace_runtime_references", references)
    assert retirements.reserve(root, _predecessor(), runtime=runtime)


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
    owner = retirements._owner(101, "1001")
    runtime._leases.replace_runtime_references(root, owner, ["current"], expected=["legacy"])
    with pytest.raises(RuntimeError, match="references changed"):
        retirements.cancel(root, _predecessor(), runtime=runtime)
    assert retirements.pending_slot(root, "legacy")
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
