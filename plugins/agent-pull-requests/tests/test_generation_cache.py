"""Actual private file/lock consumers; no provider, daemon or operation effects."""

import copy
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest
from single_instance_lease import OperationLockedError

from agent_pull_requests import generation_cache as storage
from agent_pull_requests.generation_cache import CacheInvalid, CacheUnavailable, GenerationCache
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity
from agent_pull_requests.watch_storage import StateCommitUncertain


@pytest.fixture
def evidence():
    path = Path(__file__).resolve().parents[1] / "docs" / "review-evidence.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def cache(tmp_path):
    value = GenerationCache(tmp_path / "owner", "example-namespace",
                            max_bytes=100000, max_generations=10)
    value.provision()
    return value


def generation(evidence, *, head=None, principal=None):
    value = copy.deepcopy(evidence)
    if principal is not None:
        value["scope"]["principal_id"] = principal
    if head is not None:
        value["sections"]["pull_request"]["data"]["lastMergeSourceCommit"]["commitId"] = head
    return build_generation(value)["generation"]


def test_initial_retained_lookup_and_explicit_scope_cas(cache, evidence):
    first = generation(evidence)
    assert cache.lookup(first["scope"])["found"] is False
    result = cache.publish(first, expected_current=None)
    assert result["status"] == "published"
    assert result["confirmed_current_id"] == first["id"]
    retained = cache.lookup(first["scope"])
    assert retained["generation"] == first
    assert retained["freshness"] == "retained"
    assert retained["generation"]["complete"] is False
    assert retained["generation"]["sections"]["pull_request"]["observed_to"] == (
        evidence["sections"]["pull_request"]["observed_to"]
    )
    second = generation(evidence, head="c" * 40)
    stale = cache.publish(second, expected_current=None)
    assert stale["status"] == "conflict"
    assert stale["observed_revision"]["head_sha"] == "c" * 40
    assert cache.lookup(first["scope"])["current_id"] == first["id"]
    assert cache.publish(second, expected_current=first["id"])["status"] == "published"
    assert cache.lookup(first["scope"], generation_id=first["id"])["generation"] == first


def test_scope_keys_do_not_mix_principal_or_endpoint(cache, evidence):
    first = generation(evidence)
    cache.publish(first, expected_current=None)
    second = generation(evidence, principal="another-effective-principal")
    cache.publish(second, expected_current=None)
    assert cache.lookup(second["scope"])["current_id"] == second["id"]
    assert cache.lookup(first["scope"])["current_id"] == first["id"]
    with pytest.raises(CacheInvalid, match="scope"):
        cache.lookup(second["scope"], generation_id=first["id"])
    endpoint = copy.deepcopy(first["scope"])
    endpoint["endpoint"] = "https://dev.azure.com/another"
    with pytest.raises(CacheInvalid, match="scope"):
        cache.lookup(endpoint, generation_id=first["id"])


@pytest.mark.parametrize("quota", ["generation", "bytes"])
def test_full_cache_keeps_current_and_reports_the_new_observed_head(tmp_path, evidence, quota):
    first = generation(evidence)
    max_bytes = 100000 if quota == "generation" else (
        len(json.dumps(first).encode()) + 1500
    )
    value = GenerationCache(tmp_path / "owner", "example-namespace",
                            max_bytes=max_bytes, max_generations=1 if quota == "generation" else 10)
    value.provision()
    assert value.publish(first, expected_current=None)["status"] == "published"
    second = generation(evidence, head="d" * 40)
    result = value.publish(second, expected_current=first["id"])
    assert result["status"] == "full"
    assert result["observed_revision"]["head_sha"] == "d" * 40
    assert result["confirmed_current_id"] == first["id"]
    assert value.lookup(first["scope"])["generation"] == first


@pytest.mark.parametrize("stage", ["generation", "pointer"])
def test_pre_replace_failure_preserves_current(cache, evidence, monkeypatch, stage):
    first, second = generation(evidence), generation(evidence, head="e" * 40)
    cache.publish(first, expected_current=None)
    original = storage._atomic_write_json

    def fail(path, value):
        if path.parent.name == ("generations" if stage == "generation" else "targets"):
            raise OSError("controlled pre-replace failure")
        original(path, value)

    monkeypatch.setattr(storage, "_atomic_write_json", fail)
    result = cache.publish(second, expected_current=first["id"])
    assert result["status"] == "failed"
    assert result["stage"] == stage
    assert cache.lookup(first["scope"])["current_id"] == first["id"]


@pytest.mark.parametrize("stage", ["generation", "pointer"])
def test_uncertain_commit_requires_same_target_readback_and_durability_retry(
    cache, evidence, monkeypatch, stage,
):
    first, second = generation(evidence), generation(evidence, head="f" * 40)
    cache.publish(first, expected_current=None)
    original = storage._atomic_write_json

    def uncertain(path, value):
        original(path, value)
        if path.parent.name == ("generations" if stage == "generation" else "targets"):
            raise StateCommitUncertain("controlled post-replace failure")

    monkeypatch.setattr(storage, "_atomic_write_json", uncertain)
    result = cache.publish(second, expected_current=first["id"])
    assert result["status"] == "unconfirmed"
    assert result["readback_required"] is True
    assert result["confirmed_current_id"] == first["id"]
    readback = cache.lookup(first["scope"])
    assert readback["current_id"] == (second["id"] if stage == "pointer" else first["id"])
    monkeypatch.setattr(storage, "_atomic_write_json", original)
    assert cache.publish(second, expected_current=readback["current_id"])["status"] == "published"
    assert cache.lookup(first["scope"])["generation"] == second


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_current_object_is_not_empty_success(cache, evidence, damage):
    first = generation(evidence)
    cache.publish(first, expected_current=None)
    path = cache.generations / (first["id"] + ".json")
    if damage == "missing":
        path.unlink()
    else:
        path.write_text('{"schema":"not-a-generation"}', encoding="utf-8")
    with pytest.raises(CacheInvalid):
        cache.lookup(first["scope"])
    with pytest.raises(CacheInvalid):
        cache.publish(generation(evidence, head="1" * 40), expected_current=first["id"])
    with pytest.raises(FileExistsError):
        cache.provision()


def test_descriptor_binding_refuses_foreign_namespace_or_service(cache, evidence):
    other = GenerationCache(cache.owner_root, "another-namespace",
                            max_bytes=100000, max_generations=10)
    with pytest.raises(CacheInvalid, match="descriptor"):
        other.lookup(generation(evidence)["scope"])
    service = GenerationCache(
        cache.owner_root, "example-namespace", max_bytes=100000, max_generations=10,
        service_identity=ReplayServiceIdentity("fleet", "gateway", "slot"),
    )
    with pytest.raises(CacheInvalid, match="descriptor"):
        service.lookup(generation(evidence)["scope"])


@pytest.mark.parametrize("damage", ["boolean_version", "floating_limit"])
def test_descriptor_type_confusion_is_not_valid_empty_state(cache, evidence, damage):
    value = json.loads(cache.descriptor.read_text(encoding="utf-8"))
    if damage == "boolean_version":
        value["version"] = True
    else:
        value["limits"]["max_generations"] = 10.0
    cache.descriptor.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(CacheInvalid, match="descriptor"):
        cache.lookup(generation(evidence)["scope"])


def test_linked_directory_refuses_without_touching_its_target(cache, evidence, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"not-cache-state")
    cache.generations.rmdir()
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(cache.generations))
    else:
        cache.generations.symlink_to(outside, target_is_directory=True)
    with pytest.raises(CacheInvalid):
        cache.lookup(generation(evidence)["scope"])
    with pytest.raises(CacheInvalid):
        cache.rebuild()
    assert sentinel.read_bytes() == b"not-cache-state"


def test_two_writers_retain_expected_predecessor_after_real_lock_contention(
    cache, evidence, monkeypatch,
):
    first = generation(evidence)
    second, third = generation(evidence, head="2" * 40), generation(evidence, head="3" * 40)
    cache.publish(first, expected_current=None)
    entered, release = threading.Event(), threading.Event()
    original = storage._atomic_write_json
    results = []

    def paused(path, value):
        if path.parent == cache.targets and value["generation_id"] == second["id"]:
            entered.set()
            if not release.wait(10):
                raise RuntimeError("controlled writer was not released")
        original(path, value)

    monkeypatch.setattr(storage, "_atomic_write_json", paused)

    def writer():
        results.append(cache.publish(second, expected_current=first["id"]))

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert entered.wait(10)
        with pytest.raises(OperationLockedError):
            cache.publish(third, expected_current=first["id"])
        with pytest.raises(OperationLockedError):
            cache.lookup(first["scope"])
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert results[0]["status"] == "published"
    stale = cache.publish(third, expected_current=first["id"])
    assert stale["status"] == "conflict"
    assert stale["confirmed_current_id"] == second["id"]


def test_cache_only_rebuild_cannot_erase_replay_or_unknown_operation_state(cache, evidence):
    replay = cache.owner_root / "watch-replay.json"
    operations = cache.owner_root / "review-operations.json"
    replay.write_bytes(b'{"registration":"pending"}')
    operations.write_bytes(b'{"action":"unknown","platform_id":"synthetic-platform-id"}')
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (replay, operations)]
    first = generation(evidence)
    cache.publish(first, expected_current=None)
    cache.publish(generation(evidence, head="4" * 40), expected_current=first["id"])
    cache.rebuild()
    assert cache.lookup(first["scope"])["found"] is False
    assert list(cache.generations.iterdir()) == []
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (replay, operations)]


def test_uncertain_rebuild_clear_does_not_retire_still_needed_objects(
    cache, evidence, monkeypatch,
):
    first = generation(evidence)
    cache.publish(first, expected_current=None)
    original = storage._atomic_write_json

    def uncertain(path, value):
        original(path, value)
        if path.parent == cache.targets and value["generation_id"] is None:
            raise StateCommitUncertain("controlled clear uncertainty")

    monkeypatch.setattr(storage, "_atomic_write_json", uncertain)
    with pytest.raises(StateCommitUncertain):
        cache.rebuild()
    assert (cache.generations / (first["id"] + ".json")).is_file()
    monkeypatch.setattr(storage, "_atomic_write_json", original)
    cache.rebuild()
    assert cache.lookup(first["scope"])["found"] is False
    assert list(cache.generations.iterdir()) == []


def test_owner_permission_loss_between_object_and_pointer_is_not_reclassified(
    cache, evidence, monkeypatch,
):
    first, second = generation(evidence), generation(evidence, head="5" * 40)
    cache.publish(first, expected_current=None)
    denied = PermissionError("existing service writer authority withdrawn")
    state = {"allowed": True}

    def checkpoint():
        if not state["allowed"]:
            raise denied

    cache.checkpoint = checkpoint
    original = storage._atomic_write_json

    def withdraw(path, value):
        original(path, value)
        if path.parent == cache.generations:
            state["allowed"] = False

    monkeypatch.setattr(storage, "_atomic_write_json", withdraw)
    with pytest.raises(PermissionError) as failure:
        cache.publish(second, expected_current=first["id"])
    assert failure.value is denied
    cache.checkpoint = None
    assert cache.lookup(first["scope"])["current_id"] == first["id"]


def test_unprovisioned_cache_never_bootstraps_on_read(tmp_path, evidence):
    value = GenerationCache(tmp_path / "owner", "namespace", max_bytes=100000, max_generations=10)
    with pytest.raises(CacheUnavailable):
        value.lookup(generation(evidence)["scope"])
    assert not value.root.exists()


def test_oversized_scope_pointer_refuses_before_writing_either_artifact(cache, evidence):
    first = generation(evidence)
    cache.publish(first, expected_current=None)
    before = {
        str(path.relative_to(cache.root)): path.read_bytes()
        for directory in (cache.generations, cache.targets)
        for path in directory.iterdir()
    }
    large = generation(evidence, principal="x" * 17000)
    with pytest.raises(ValueError, match="pointer exceeds"):
        cache.publish(large, expected_current=None)
    after = {
        str(path.relative_to(cache.root)): path.read_bytes()
        for directory in (cache.generations, cache.targets)
        for path in directory.iterdir()
    }
    assert after == before
    assert cache.lookup(first["scope"])["generation"] == first
    cache.rebuild()
    assert cache.lookup(first["scope"])["found"] is False
