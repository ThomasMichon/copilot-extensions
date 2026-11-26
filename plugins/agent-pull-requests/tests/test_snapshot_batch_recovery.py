"""Original-fence recovery and original-ID readback under the actual replay lock."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests import snapshot_batch_recovery as recovery
from agent_pull_requests import watch_replay_store as storage
from agent_pull_requests.snapshot_batch_state import BatchAdmission, STATE_FIELD, VERSION_FIELD, validate
from agent_pull_requests.watch_replay_store import ReplayCallerExpired, ReplayStore, ReplayStoreInvalid

KEY = "a" * 64
EXPIRES = 2000


def _store(root: Path, clock: list[float], *, selected: bool = True) -> ReplayStore:
    return ReplayStore(
        root, "test", clock=lambda: clock[0], repository_cache=True,
        repository_continuing=True, github_snapshot_batches=selected,
    )


def _pending(store: ReplayStore, *, recovery_id: str = "1" * 32) -> recovery.RecoverySelection:
    pending = BatchAdmission(store).begin(KEY, expires_at=EXPIRES)
    assert pending is not None
    return recovery.RecoverySelection(KEY, pending, recovery_id, "retain-unknown")


def _recover(store: ReplayStore, selection: recovery.RecoverySelection) -> recovery.RecoveryRecord:
    return recovery.recover(store, selection, lifecycle_checkpoint=lambda: None, expires_at=EXPIRES)


def test_v2_retains_original_scope_and_replay_while_later_observations_are_independent(
    tmp_path: Path,
) -> None:
    clock = [1000.]
    store = _store(tmp_path, clock)
    store.provision()
    initial = json.loads(store.path.read_bytes())
    assert initial[VERSION_FIELD] == 1
    assert initial[STATE_FIELD] == {"scopes": []}
    admission = BatchAdmission(store)
    completed = _pending(store)
    admission.finish(KEY, completed.pending, {
        "resource": "graphql-primary", "not_before": 1050., "reason": "primary-limit",
    }, expires_at=EXPIRES)
    clock[0] = 1100.
    original = _pending(store)
    assert admission.begin("b" * 64, expires_at=EXPIRES) is not None
    before = json.loads(store.path.read_bytes())
    clock[0] = 1150.
    result = _recover(store, original)
    after = json.loads(store.path.read_bytes())
    assert after.pop(VERSION_FIELD) == 2 and before.pop(VERSION_FIELD) == 1
    original_state = before.pop(STATE_FIELD)
    migrated_state = after.pop(STATE_FIELD)
    assert before == after
    assert result == {
        **original_state["scopes"][0],
        "recovery_id": original.recovery_id, "recorded_at": 1150., "disposition": "outcome-unknown",
    }
    assert migrated_state == {
        "scopes": [
            {**original_state["scopes"][0], "pending": None, "observed_at": 1150.},
            original_state["scopes"][1],
        ],
        "recoveries": [result],
    }
    with pytest.raises(ValueError, match="invalid bounded snapshot batch state"):
        validate(migrated_state)
    persisted = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        _store(tmp_path, clock, selected=False).sweep_timeouts(expires_at=EXPIRES)
    assert store.path.read_bytes() == persisted

    restarted = _store(tmp_path, clock)
    admission = BatchAdmission(restarted)
    clock[0] = 1149.
    assert not admission.allowed(KEY, query=True, expires_at=EXPIRES)
    clock[0] = 1150.
    throttled = _pending(restarted, recovery_id="2" * 32)
    admission.finish(KEY, throttled.pending, {
        "resource": "shared-secondary", "not_before": 1200., "reason": "secondary-limit",
    }, expires_at=EXPIRES)
    assert not admission.allowed(KEY, query=False, expires_at=EXPIRES)
    assert admission.begin(KEY, expires_at=EXPIRES) is None
    clock[0] = 1200.
    valid = _pending(restarted)
    admission.finish(KEY, valid.pending, None, expires_at=EXPIRES)
    unresolved = _pending(restarted)
    assert unresolved.pending != original.pending
    persisted = store.path.read_bytes()
    with pytest.raises(ValueError, match="use original readback"):
        _recover(restarted, original)
    assert recovery.read_recovery(restarted, original, expires_at=EXPIRES) == result
    with pytest.raises(ValueError, match="readback selection differs"):
        recovery.read_recovery(restarted, replace(original, pending=unresolved.pending), expires_at=EXPIRES)
    assert not BatchAdmission(_store(tmp_path, clock)).allowed(KEY, query=False, expires_at=EXPIRES)
    assert store.path.read_bytes() == persisted


def test_failed_recovery_does_not_change_fence_or_provision_a_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.]
    store = _store(tmp_path, clock)
    store.provision()
    original = _pending(store)
    before = store.path.read_bytes()
    for different in (replace(original, key="b" * 64), replace(original, pending="f" * 32)):
        with pytest.raises(ValueError, match="fence differs"):
            _recover(store, different)
    assert recovery.read_recovery(store, original, expires_at=EXPIRES) is None
    clock[0] = 999.
    with pytest.raises(ValueError, match="backwards"):
        _recover(store, original)
    clock[0] = 1000.
    before_validate = store._validate_owner

    def expire_after_transition(document: dict[str, Any]) -> None:
        before_validate(document)
        if document[VERSION_FIELD] == 2:
            clock[0] = EXPIRES

    monkeypatch.setattr(store, "_validate_owner", expire_after_transition)
    with pytest.raises(ReplayCallerExpired):
        _recover(store, original)
    assert store.path.read_bytes() == before


def test_lost_committed_response_can_only_be_reconciled_by_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.]
    store = _store(tmp_path, clock)
    store.provision()
    original = _pending(store)
    write = storage._atomic_write_json

    def lose_response(*args: Any) -> None:
        write(*args)
        raise OSError("synthetic lost result after durable commit")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_atomic_write_json", lose_response)
        with pytest.raises(OSError):
            _recover(store, original)
    before = store.path.read_bytes()
    record = recovery.read_recovery(_store(tmp_path, clock), original, expires_at=EXPIRES)
    assert record is not None and record["pending"] == original.pending
    assert record["disposition"] == "outcome-unknown"
    assert store.path.read_bytes() == before


def test_capacity_refuses_without_pruning_prior_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.]
    store = _store(tmp_path, clock)
    store.provision()
    monkeypatch.setattr(recovery, "MAX_RECOVERIES", 1)
    first = _pending(store)
    accepted = _recover(store, first)
    later = _pending(store, recovery_id="2" * 32)
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="capacity exhausted"):
        _recover(store, later)
    assert recovery.read_recovery(store, first, expires_at=EXPIRES) == accepted
    assert recovery.read_recovery(store, later, expires_at=EXPIRES) is None
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("failure", ["withdrawn", "backwards"])
def test_commit_rechecks_current_lifecycle_and_clock_without_clearing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    clock = [1000.]
    store = _store(tmp_path, clock)
    store.provision()
    original = _pending(store)
    before = store.path.read_bytes()
    validate_owner = store._validate_owner
    withdrawn = False

    def interrupt_after_transition(document: dict[str, Any]) -> None:
        nonlocal withdrawn
        validate_owner(document)
        if document[VERSION_FIELD] == 2:
            if failure == "backwards":
                clock[0] = 999.
            else:
                withdrawn = True

    def lifecycle() -> None:
        if withdrawn:
            raise PermissionError("fixture owner withdrawn")

    monkeypatch.setattr(store, "_validate_owner", interrupt_after_transition)
    with pytest.raises((PermissionError, ValueError)):
        recovery.recover(
            store, original, lifecycle_checkpoint=lifecycle, expires_at=EXPIRES,
        )
    assert store.path.read_bytes() == before
