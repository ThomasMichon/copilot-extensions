"""Original native target capture cannot be reconstructed from current config."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from test_generation_associations import generation, state
from test_review_workspace import evidence as evidence
from test_native_read import native as native_reader

from agent_pull_requests import generation_associations as associations
from agent_pull_requests import generation_cache, generation_native_targets as native
from agent_pull_requests import watch_replay_store
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain, _atomic_write_json
from agent_pull_requests.workspace_operations import WorkspaceOperations

Document = dict[str, Any]
__all__ = ["native_reader"]


def owner(root: Path) -> ReplayStore:
    return ReplayStore(
        root.resolve(), "owner", clock=lambda: 1,
        workspace_generation_associations=True, native_generation_associations=True,
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


def capture(store: ReplayStore, value: Document) -> native.NativeGenerationCapture:
    return native.capture(
        store, value["scope"], ("account-a", "example/repository"),
        expires_at=100, checkpoint=lambda: None,
    )


def publish(
    store: ReplayStore, cache: GenerationCache, value: Document,
    captured: native.NativeGenerationCapture | None, previous: str | None = None,
) -> tuple[Document, Document]:
    return associations.publish(
        store, cache, value, native_capture=captured, expected_current=previous,
        expires_at=100, checkpoint=lambda: None,
    )


def test_original_target_and_absence_survive_restart_without_changing_generation(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path)
    value = generation(evidence)
    before = store.path.read_bytes()
    captured = capture(store, value)
    assert store.path.read_bytes() == before
    assert publish(store, cache, value, captured)[0]["status"] == "published"
    reopened = owner(tmp_path)
    reopened._review_transaction(lambda _value: None, expires_at=100, persist=False)
    row = state(reopened)["generation_associations"]["records"][0]
    assert row["native_target"] == {
        "account": "account-a", "repo": "example/repository", "number": value["scope"]["number"],
    }
    assert row["acquisition_epoch"] is row["acquisition_state"] is None
    assert row["publication"] == "confirmed"
    assert cache.lookup(value["scope"])["generation"] == value
    before = reopened.path.read_bytes()
    legacy = ReplayStore(tmp_path.resolve(), "owner", clock=lambda: 1,
                         workspace_generation_associations=True)
    with pytest.raises(ReplayStoreInvalid, match="reader unavailable"):
        legacy._review_transaction(lambda _value: None, expires_at=100, persist=False)
    assert reopened.path.read_bytes() == before


@pytest.mark.parametrize("change", ["absent-created", "closed", "reopened"])
def test_intervening_journal_change_refuses_before_new_observation_or_cache_writes(
    tmp_path: Path, evidence: Document, change: str,
) -> None:
    store, cache = fresh(tmp_path)
    value = generation(evidence)
    if change != "absent-created":
        ConversationStore(store).observe_workspace(value, expires_at=100)
    captured = capture(store, value)
    changed = copy.deepcopy(evidence)
    if change != "absent-created":
        changed["sections"]["pull_request"]["data"]["status"] = "completed"
    ConversationStore(store).observe_workspace(generation(changed, 2), expires_at=100)
    if change == "reopened":
        ConversationStore(store).observe_workspace(generation(evidence, 4), expires_at=100)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="changed during"):
        publish(store, cache, value, captured)
    assert store.path.read_bytes() == before
    assert not list(cache.generations.iterdir())


@pytest.mark.parametrize("field", ["root", "namespace", "service", "workspace", "number", "missing"])
def test_missing_or_foreign_capture_cannot_publish(
    tmp_path: Path, evidence: Document, field: str,
) -> None:
    store, cache = fresh(tmp_path)
    value = generation(evidence)
    captured: native.NativeGenerationCapture | None = capture(store, value)
    if field == "missing":
        captured = None
    else:
        assert captured is not None
        changes: Document = {
            field: tmp_path.parent if field == "root" else 123 if field == "number" else "other",
        }
        captured = replace(captured, **changes)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="pre-acquisition"):
        publish(store, cache, value, captured)
    assert store.path.read_bytes() == before and not list(cache.generations.iterdir())


def test_repeated_id_preserves_original_target_and_new_capture_cannot_rebind(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path)
    value = generation(evidence)
    original = capture(store, value)
    publish(store, cache, value, original)
    repeated = capture(owner(tmp_path), value)
    assert publish(owner(tmp_path), cache, value, repeated, value["id"])[0]["status"] == "published"
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="different original native"):
        publish(store, cache, value, replace(repeated, account="account-b"), value["id"])
    assert store.path.read_bytes() == before
    row = state(store)["generation_associations"]["records"][0]
    assert row["acquisition_epoch"] is None and row["native_target"]["account"] == "account-a"


@pytest.mark.parametrize("boundary", ["reservation", "confirmation", "generation", "pointer"])
def test_uncertain_publication_reconciles_original_capture_and_cas(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    store, cache = fresh(tmp_path)
    value = generation(evidence)
    captured = capture(store, value)
    replay_write = watch_replay_store._atomic_write_json
    calls = 0

    def replay_uncertain(path: Path, document: Document) -> None:
        nonlocal calls
        replay_write(path, document)
        calls += 1
        if calls == (1 if boundary == "reservation" else 2):
            raise StateCommitUncertain("synthetic original acknowledgement loss")

    def cache_uncertain(path: Path, document: Document) -> None:
        _atomic_write_json(path, document)
        if path.parent == (cache.generations if boundary == "generation" else cache.targets):
            raise StateCommitUncertain("synthetic original cache acknowledgement loss")

    if boundary in ("reservation", "confirmation"):
        monkeypatch.setattr(watch_replay_store, "_atomic_write_json", replay_uncertain)
        with pytest.raises(StateCommitUncertain):
            publish(store, cache, value, captured)
    else:
        monkeypatch.setattr(generation_cache, "_atomic_write_json", cache_uncertain)
        assert publish(store, cache, value, captured)[0]["status"] == "unconfirmed"
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", replay_write)
    monkeypatch.setattr(generation_cache, "_atomic_write_json", _atomic_write_json)
    assert publish(owner(tmp_path), cache, value, captured)[0]["status"] == "published"
    result = state(store)
    assert len(result["generation_associations"]["records"]) == 1
    assert len(result["conversations"]["records"][0]["events"]) == 1
    assert result["generation_associations"]["records"][0]["publication"] == "confirmed"


def test_conflict_and_capacity_leave_original_capture_unreserved(
    tmp_path: Path, evidence: Document,
) -> None:
    store, cache = fresh(tmp_path, count=1)
    value = generation(evidence)
    captured = capture(store, value)
    before = store.path.read_bytes()
    assert publish(store, cache, value, captured, "f" * 64)[0]["status"] == "conflict"
    assert store.path.read_bytes() == before
    assert publish(store, cache, value, captured)[0]["status"] == "published"
    second = generation(evidence, 2)
    before = store.path.read_bytes()
    assert publish(store, cache, second, capture(store, second), value["id"])[0]["status"] == "full"
    assert store.path.read_bytes() == before


def test_workspace_capture_precedes_actual_native_discovery_and_export(
    tmp_path: Path, native_reader: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, _request, path, _value = native_reader
    store, cache = fresh(tmp_path / "owner")
    original = native.capture
    captured: list[native.NativeGenerationCapture] = []

    def before_io(*args: Any, **kwargs: Any) -> native.NativeGenerationCapture:
        assert not path.with_suffix(".calls").exists()
        value = original(*args, **kwargs)
        captured.append(value)
        return value

    monkeypatch.setattr(native, "capture", before_io)
    workspace = WorkspaceOperations(
        store, cache, scope=dict(reader.scope), read_owner=reader, binding_ref="selected",
        checkpoint=lambda: None, max_work_seconds=10, conversations=True,
        generation_native_target=("account-a", "project/repository"),
    )
    result = workspace.execute("load", {
        "number": 42, "expected_current": None, "budget_seconds": 10,
    }, expires_at=100, checkpoint=lambda: None)
    assert result["result"]["publication"]["status"] == "published"
    assert len(captured) == 1
    assert path.with_suffix(".calls").read_text().splitlines() == ["capabilities", "read"]
    row = state(store)["generation_associations"]["records"][0]
    assert row["native_target"]["repo"] == "project/repository"
