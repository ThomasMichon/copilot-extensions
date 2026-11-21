"""Fresh baseline publication retains actual generation identities across interrupted IO."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, cast

import pytest

from test_review_workspace import evidence as evidence

from agent_pull_requests import generation_associations as associations
from agent_pull_requests import conversation_journal, generation_cache, watch_replay_store
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain, _atomic_write_json

Document = dict[str, Any]


def generation(evidence: Document, second: int = 0) -> Document:
    value = copy.deepcopy(evidence)
    for section in value["sections"].values():
        section["observed_from"] = f"2026-10-30T00:00:{second:02d}.100001Z"
        section["observed_to"] = f"2026-10-30T00:00:{second:02d}.900001Z"
    return cast(Document, build_generation(value)["generation"])


def owner(root: Path) -> ReplayStore:
    return ReplayStore(
        root.resolve(), "owner", clock=lambda: 1, workspace_generation_associations=True,
    )


def fresh(root: Path, count: int = 8) -> tuple[ReplayStore, GenerationCache]:
    store = owner(root)
    store.provision(conversations=True)
    cache = GenerationCache(root.resolve(), "owner", max_bytes=4 * 1024 * 1024, max_generations=count)
    associations.provision(
        store, cache, expires_at=100, checkpoint=lambda: None, lifecycle_checkpoint=lambda: None,
    )
    cache.provision()
    return store, cache


def publish(
    store: ReplayStore, cache: GenerationCache, value: Document, previous: str | None = None,
) -> tuple[Document, Document]:
    return associations.publish(
        store, cache, value, expected_current=previous, expires_at=100, checkpoint=lambda: None,
    )


def state(store: ReplayStore) -> Document:
    return cast(Document, json.loads(store.path.read_bytes()))


def test_observation_selection_still_refuses_before_reading_unprovisioned_state(
    tmp_path: Path,
) -> None:
    store = ReplayStore(tmp_path.resolve(), "owner", clock=lambda: 1)
    with pytest.raises(ValueError, match="comment mapping selection"):
        ConversationStore(store).observe_workspace({}, expires_at=100, comment_mappings=cast(bool, 1))
    assert not store.path.exists()


def test_core_provision_cannot_backfill_an_existing_cache(
    tmp_path: Path, evidence: Document,
) -> None:
    store = owner(tmp_path)
    store.provision(conversations=True)
    cache = GenerationCache(tmp_path.resolve(), "owner", max_bytes=4 * 1024 * 1024, max_generations=8)
    cache.provision()
    value = generation(evidence)
    cache.publish(value, expected_current=None)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="fresh baseline"):
        associations.provision(
            store, cache, expires_at=100, checkpoint=lambda: None,
            lifecycle_checkpoint=lambda: None,
        )
    assert store.path.read_bytes() == before
    assert cache.lookup(value["scope"])["generation"] == value


def test_distinct_same_cursor_generations_retain_original_associations_after_restart(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path)
    first = generation(evidence)
    changed = copy.deepcopy(evidence)
    for section in changed["sections"].values():
        section["observed_from"] = "2026-10-30T00:00:00.200001Z"
        section["observed_to"] = "2026-10-30T00:00:00.800001Z"
    second = build_generation(changed)["generation"]
    assert first["id"] != second["id"]
    before = cache.expected_descriptor
    assert publish(store, cache, first)[0]["status"] == "published"
    assert publish(store, cache, second, first["id"])[0]["status"] == "published"
    reopened = owner(tmp_path)
    persisted = reopened._review_transaction(lambda value: value, expires_at=100, persist=False)
    rows = persisted["generation_associations"]["records"]
    assert [row["generation_id"] for row in rows] == [first["id"], second["id"]]
    assert rows[0]["cursor"] == rows[1]["cursor"] == {"epoch": 1, "sequence": 1}
    assert rows[0]["observation_id"] == rows[1]["observation_id"]
    assert all(row["publication"] == "confirmed" for row in rows)
    assert len(persisted["conversations"]["records"][0]["events"]) == 1
    for value in (first, second):
        assert cache.lookup(value["scope"], generation_id=value["id"])["generation"] == value
    assert cache.expected_descriptor == before
    assert cache.lookup(first["scope"])["current_id"] == second["id"]
    assert not any(word in json.dumps(rows) for word in ("Reply", "Review this change"))


@pytest.mark.parametrize("boundary", [1, 2])
def test_uncertain_replay_replace_retries_original_publication_without_another_event(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch, boundary: int,
) -> None:
    store, cache = fresh(tmp_path)
    original = watch_replay_store._atomic_write_json
    calls = 0

    def uncertain(path: Path, value: Document) -> None:
        nonlocal calls
        original(path, value)
        calls += 1
        if calls == boundary:
            raise StateCommitUncertain("synthetic acknowledgement loss")

    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", uncertain)
    value = generation(evidence)
    with pytest.raises(StateCommitUncertain):
        publish(store, cache, value)
    retained = state(store)
    assert len(retained["generation_associations"]["records"]) == 1
    if boundary == 1:
        assert not list(cache.generations.iterdir())
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", original)
    assert publish(owner(tmp_path), cache, value)[0]["status"] == "published"
    result = state(store)
    assert result["generation_associations"]["records"][0]["publication"] == "confirmed"
    assert len(result["conversations"]["records"][0]["events"]) == 1


@pytest.mark.parametrize("stage", ["generation", "pointer"])
def test_uncertain_cache_replace_keeps_pending_custody_and_reconciles_the_original_cas(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    store, cache = fresh(tmp_path)
    original = _atomic_write_json

    def uncertain(path: Path, value: Document) -> None:
        original(path, value)
        selected = cache.generations if stage == "generation" else cache.targets
        if path.parent == selected:
            raise StateCommitUncertain("synthetic cache acknowledgement loss")

    monkeypatch.setattr(generation_cache, "_atomic_write_json", uncertain)
    value = generation(evidence)
    result, _ = publish(store, cache, value)
    assert result["status"] == "unconfirmed"
    row = state(store)["generation_associations"]["records"][0]
    assert row["publication"] == "pending"
    with pytest.raises(ReplayStoreInvalid, match="original CAS"):
        publish(owner(tmp_path), cache, value, "f" * 64)
    monkeypatch.setattr(generation_cache, "_atomic_write_json", original)
    assert publish(owner(tmp_path), cache, value)[0]["status"] == "published"
    assert state(store)["generation_associations"]["records"][0]["cursor"] == row["cursor"]


def test_reservation_failure_and_capacity_refuse_before_cache_publication(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cache = fresh(tmp_path, count=1)
    original = watch_replay_store._atomic_write_json
    before = store.path.read_bytes()

    def fail(_path: Path, _value: Document) -> None:
        raise OSError("synthetic reservation failure")

    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", fail)
    with pytest.raises(OSError, match="reservation"):
        publish(store, cache, generation(evidence))
    assert store.path.read_bytes() == before and not list(cache.generations.iterdir())
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", original)
    first = generation(evidence)
    publish(store, cache, first)
    before = store.path.read_bytes()
    result, observed = publish(store, cache, generation(evidence, 2), first["id"])
    assert result["status"] == "full" and observed["status"] == "not_observed"
    assert store.path.read_bytes() == before
    assert len(list(cache.generations.iterdir())) == 1
    assert cache.lookup(first["scope"])["current_id"] == first["id"]


def test_definite_conflict_preserves_capacity_for_corrected_cas_after_restart(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path, count=1)
    value = generation(evidence)
    before = store.path.read_bytes()
    result, observed = publish(store, cache, value, "f" * 64)
    assert result["status"] == "conflict" and observed["status"] == "not_observed"
    assert store.path.read_bytes() == before and not list(cache.generations.iterdir())
    assert state(store)["generation_associations"]["records"] == []
    assert publish(owner(tmp_path), cache, value)[0]["status"] == "published"
    rows = state(store)["generation_associations"]["records"]
    assert len(rows) == 1 and rows[0]["publication"] == "confirmed"
    assert len(state(store)["conversations"]["records"][0]["events"]) == 1


def test_conflict_unavailable_and_stale_observations_never_gain_confirmed_associations(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path)
    first = generation(evidence, 4)
    assert publish(store, cache, first)[0]["status"] == "published"
    stale = generation(evidence, 2)
    assert publish(store, cache, stale, first["id"])[1]["status"] == "stale_observation"
    missing = copy.deepcopy(evidence)
    missing["sections"]["pull_request"]["data"].pop("status")
    assert publish(store, cache, generation(missing, 6), first["id"])[1]["status"] == "unavailable"
    conflict = generation(evidence, 8)
    assert publish(store, cache, conflict, "f" * 64)[0]["status"] == "conflict"
    rows = state(store)["generation_associations"]["records"]
    assert [(row["generation_id"], row["publication"]) for row in rows] == [
        (first["id"], "confirmed"),
    ]
    assert len(list(cache.generations.iterdir())) == 1


def test_original_generation_cannot_be_relabelled_after_reopen(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path)
    first = generation(evidence)
    publish(store, cache, first)
    changed = copy.deepcopy(evidence)
    changed["sections"]["pull_request"]["data"]["status"] = "completed"
    ConversationStore(store).observe_workspace(generation(changed, 2), expires_at=100)
    ConversationStore(store).observe_workspace(generation(evidence, 4), expires_at=100)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="epoch"):
        publish(store, cache, first, first["id"])
    assert store.path.read_bytes() == before
    assert state(store)["generation_associations"]["records"][0]["cursor"]["epoch"] == 1


def test_old_reader_and_foreign_cache_refuse_without_rewriting_original_state(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path)
    publish(store, cache, generation(evidence))
    before = store.path.read_bytes()
    old = ReplayStore(tmp_path.resolve(), "owner", clock=lambda: 1)
    with pytest.raises(ReplayStoreInvalid, match="reader unavailable"):
        old._review_transaction(lambda document: document, expires_at=100, persist=False)
    foreign = GenerationCache(
        (tmp_path / "other").resolve(), "owner", max_bytes=cache.max_bytes,
        max_generations=cache.max_generations,
    )
    with pytest.raises(ValueError, match="original cache"):
        publish(store, foreign, generation(evidence, 2))
    assert store.path.read_bytes() == before and not foreign.owner_root.exists()


def test_withdrawn_cache_checkpoint_preserves_pending_identity_without_publication(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cache = fresh(tmp_path, count=1)
    original = watch_replay_store._atomic_write_json

    def withdrawn() -> None:
        raise RuntimeError("synthetic original cache budget withdrawn")

    def reserve(path: Path, value: Document) -> None:
        original(path, value)
        cache.checkpoint = withdrawn

    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", reserve)
    value = generation(evidence)
    with pytest.raises(RuntimeError, match="cache budget"):
        publish(store, cache, value)
    pending = state(store)["generation_associations"]["records"][0]
    assert pending["publication"] == "pending" and not list(cache.generations.iterdir())
    cache.checkpoint = None
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", original)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="association capacity"):
        publish(store, cache, generation(evidence, 2))
    assert store.path.read_bytes() == before and not list(cache.generations.iterdir())
    assert publish(owner(tmp_path), cache, value)[0]["status"] == "published"
    assert state(store)["generation_associations"]["records"][0]["cursor"] == pending["cursor"]


def test_concurrent_original_cas_admits_one_confirmed_publication(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path)
    first = generation(evidence)
    changed = copy.deepcopy(evidence)
    for section in changed["sections"].values():
        section["observed_from"] = "2026-10-30T00:00:00.200001Z"
        section["observed_to"] = "2026-10-30T00:00:00.800001Z"
    second = build_generation(changed)["generation"]
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(publish, owner(tmp_path), cache, value)
            for value in (first, second)
        ]
        results = [future.result(timeout=3)[0] for future in futures]
    assert sorted(result["status"] for result in results) == ["conflict", "published"]
    rows = state(store)["generation_associations"]["records"]
    assert [row["publication"] for row in rows] == ["confirmed"]
    assert len(state(store)["conversations"]["records"][0]["events"]) == 1
    assert len(list(cache.generations.iterdir())) == 1


def test_association_validation_bounds_record_projection_work_at_capacity(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cache = fresh(tmp_path, count=10000)
    publish(store, cache, generation(evidence))
    document = state(store)
    original = document["conversations"]["records"][0]
    records = []
    for number in range(1, 257):
        record = copy.deepcopy(original)
        record["current"]["key"]["number"] = number
        records.append(record)
    document["conversations"]["records"] = records
    template = document["generation_associations"]["records"][0]
    associations_at_capacity = []
    for number in range(10000):
        association = copy.deepcopy(template)
        association["scope"]["number"] = number % len(records) + 1
        association["generation_id"] = f"{number:064x}"
        associations_at_capacity.append(association)
    document["generation_associations"]["records"] = associations_at_capacity
    conversions = 0
    linear_budget = 8 * len(records) + 2 * len(associations_at_capacity)

    def bounded(value: Any) -> Any:
        nonlocal conversions
        conversions += 1
        assert conversions <= linear_budget, "association validation repeated a linear PR scan"
        return asdict(value)

    monkeypatch.setattr(conversation_journal, "asdict", bounded)
    associations.validate(document, store.namespace)
    assert conversions <= linear_budget
