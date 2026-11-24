"""Durable per-account query/secondary holds never expire unresolved work."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.snapshot_batch_state import BatchAdmission, adopt, scope
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid


def store(root: Path, clock: list[float], *, selected: bool = True) -> ReplayStore:
    return ReplayStore(
        root, "test", clock=lambda: clock[0], repository_cache=True,
        repository_continuing=True, github_snapshot_batches=selected,
    )


@pytest.mark.parametrize("resource", ["graphql-primary", "shared-secondary"])
def test_backoff_survives_restart_other_repository_and_scalar_rollback(
    tmp_path: Path, resource: str,
) -> None:
    clock = [1000.]
    current = store(tmp_path, clock)
    current.provision()
    identity = RepositoryIdentity("test", "github", "https://api.github.com", "reader", "owner/first")
    other = RepositoryIdentity("test", "github", "https://api.github.com", "reader", "owner/second")
    key = scope(identity)
    assert key == scope(other)
    alias = RepositoryIdentity("test", "github", "https://github.com", "Reader", "owner/third")
    assert key == scope(alias)
    admission = BatchAdmission(current)
    operation = admission.begin(key, expires_at=2000)
    assert operation
    admission.finish(key, operation, {
        "resource": resource, "not_before": 1500., "reason": "provider-throttled",
    }, expires_at=2000)
    restarted = BatchAdmission(store(tmp_path, clock))
    assert not restarted.allowed(scope(other), query=True, expires_at=2000)
    assert restarted.allowed(key, query=False, expires_at=2000) == (resource == "graphql-primary")
    assert restarted.begin(key, expires_at=2000) is None
    clock[0] = 1500.
    assert restarted.allowed(key, query=True, expires_at=2000)
    before = current.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        store(tmp_path, clock, selected=False).sweep_timeouts(expires_at=2000)
    assert current.path.read_bytes() == before


def test_interrupted_admission_never_expires_or_moves_to_another_pr(tmp_path: Path) -> None:
    clock = [1000.]
    current = store(tmp_path, clock)
    current.provision()
    key = "a" * 64
    operation = BatchAdmission(current).begin(key, expires_at=2000)
    clock[0] = 1_000_000.
    admission = BatchAdmission(store(tmp_path, clock))
    assert admission.begin(key, expires_at=1_000_100) is None
    assert not admission.allowed(key, query=False, expires_at=1_000_100)
    with pytest.raises(ValueError, match="differs"):
        admission.finish(key, "b" * 32, None, expires_at=1_000_100)
    assert operation
    clock[0] = 999.
    with pytest.raises(ValueError, match="backwards"):
        admission.finish(key, operation, None, expires_at=2000)
    assert not admission.allowed(key, query=True, expires_at=2000)


def test_explicit_adoption_preserves_owner_and_requires_actual_lifecycle(tmp_path: Path) -> None:
    clock = [1000.]
    original = store(tmp_path, clock, selected=False)
    original.provision()
    before = json.loads(original.path.read_bytes())
    selected = store(tmp_path, clock)
    with pytest.raises(ReplayStoreInvalid):
        selected.sweep_timeouts(expires_at=2000)
    checks: list[int] = []
    assert adopt(selected, lifecycle_checkpoint=lambda: checks.append(1), expires_at=2000) == {
        "status": "adopted",
    }
    after = json.loads(original.path.read_bytes())
    assert after.pop("github_snapshot_batches_version") == 1
    assert after.pop("github_snapshot_batches") == {"scopes": []}
    assert after == before and checks == [1, 1]
    assert adopt(selected, lifecycle_checkpoint=lambda: None, expires_at=2000)["status"] == "already_adopted"


def test_failed_reservation_cannot_start_query_and_capacity_never_evicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import snapshot_batch_state as state
    from agent_pull_requests import watch_replay_store as storage
    clock = [1000.]
    current = store(tmp_path, clock)
    current.provision()
    admission = BatchAdmission(current)
    monkeypatch.setattr(state, "MAX_SCOPES", 1)
    assert admission.begin("a" * 64, expires_at=2000)
    before = current.path.read_bytes()
    with pytest.raises(ValueError, match="capacity"):
        admission.begin("b" * 64, expires_at=2000)
    assert current.path.read_bytes() == before
    def fail(*_args: Any) -> None:
        raise OSError("synthetic commit failure")
    monkeypatch.setattr(storage, "_atomic_write_json", fail)
    with pytest.raises(OSError):
        admission.begin("a" * 64, expires_at=2000)
    assert current.path.read_bytes() == before


def test_native_cutoff_crossed_by_commit_clears_only_the_proven_prelaunch_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import snapshot_batch_state as state
    from agent_pull_requests import watch_replay_store as storage
    from agent_pull_requests.native_command import NativeCommandNotStarted
    clock, native_clock = [1000.], [10.]
    current = store(tmp_path, clock)
    current.provision()
    original = storage._atomic_write_json
    def write(*args: Any) -> None:
        original(*args)
        native_clock[0] = 21.
    monkeypatch.setattr(state, "monotonic", lambda: native_clock[0])
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    with pytest.raises(NativeCommandNotStarted):
        BatchAdmission(current).begin("a" * 64, expires_at=2000, native_deadline=20.)
    assert BatchAdmission(current).allowed("a" * 64, query=True, expires_at=2000)
