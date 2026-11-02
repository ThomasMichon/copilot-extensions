from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
    assert retirements.reserve(root, predecessor, runtime=runtime)
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
        root, {"pid": 101, "start": "1001", "slot": "legacy", "release_claim": None},
        runtime=runtime,
    )
    identities.pop(101)
    assert retirements.reconcile(root, runtime=runtime)
    assert runtime._leases.read_runtime_references(root)


def test_dev_claim_is_released_only_after_the_original_resident_exits(retained):
    root, runtime, identities, alive = retained
    claim = runtime.claim_dev(root, "claiming-worktree", previous_version="current")
    retirements.reserve(
        root, {"pid": 101, "start": "1001", "slot": "dev", "release_claim": claim},
        runtime=runtime,
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
        root, {"pid": 101, "start": "1001", "slot": "dev", "release_claim": claim},
        runtime=runtime,
    )
    owner = "original-owner" if same_owner else "new-owner"
    runtime.claim_dev(root, owner, host="new-claim-host", previous_version="current", force=not same_owner)
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
        root, {"pid": 101, "start": "1001", "slot": "legacy", "release_claim": None},
        runtime=runtime,
    )
    identities.pop(101)
    alive.remove(101)
    assert not retirements.reconcile(root, cleanup=False)
    assert runtime._leases.read_runtime_references(root)
    assert not retirements.reconcile(root, runtime=runtime)
    assert not runtime._leases.read_runtime_references(root)
