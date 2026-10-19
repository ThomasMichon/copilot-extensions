"""Publication concurrency, no-op and last-known-good desired-state contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier, Event, Thread, current_thread

import pytest

from fleet_manager.gateway_reconcile import ManifestStore, ReconcileError


def document(revision: int = 1, *, slots: bool = True) -> dict:
    return {
        "schema": "fleet-manager.gateway-manifest",
        "version": 1,
        "fleet": "fleet-a",
        "gateway": "gateway-a",
        "revision": revision,
        "roles": ["api"],
        "key_sources": [
            {"name": "service-key", "provider": "owner", "reference": "keys/api@v1"}
        ],
        "slots": [
            {
                "name": "api",
                "role": "api",
                "principal": "service",
                "key_source": "service-key",
                "routes": [
                    {"prefix": "/api", "methods": ["GET"], "capabilities": ["http"]}
                ],
            }
        ] if slots else [],
    }


def encode(value: object) -> bytes:
    return json.dumps(value).encode()


def test_explicit_source_and_no_implicit_first_load() -> None:
    calls = []

    def source() -> bytes:
        calls.append(True)
        return encode(document())

    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=source)
    assert not calls
    assert store.status().revision is None
    assert store.status().generation == 0
    with pytest.raises(ReconcileError, match="^not-configured$"):
        store.snapshot()
    assert not calls
    result = store.reload()
    assert calls == [True]
    assert result.changed
    assert store.snapshot() is result.snapshot
    assert store.status().revision == 1
    assert store.status().slots == 1
    assert store.status().last_error is None
    assert result.snapshot.digest == result.snapshot.manifest.digest
    with pytest.raises(FrozenInstanceError):
        result.snapshot.generation = 2


@pytest.mark.parametrize(("fleet", "gateway"), [("", "gateway-a"), ("fleet-a", "../bad")])
def test_owner_identity_is_explicit(fleet: str, gateway: str) -> None:
    with pytest.raises(ReconcileError, match="^invalid-owner$"):
        ManifestStore(fleet=fleet, gateway=gateway, source=lambda: b"{}")


def test_invalid_source_is_not_a_silent_empty_manifest() -> None:
    with pytest.raises(ReconcileError, match="^invalid-source$"):
        ManifestStore(fleet="fleet-a", gateway="gateway-a", source=None)


def test_no_op_preserves_snapshot_and_generation() -> None:
    value = document()
    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=lambda: encode(value))
    first = store.reload()
    value["roles"].reverse()
    second = store.reload()
    assert not second.changed
    assert second.snapshot is first.snapshot
    assert second.snapshot.generation == 1


@pytest.mark.parametrize("field", ["fleet", "gateway"])
def test_owner_cannot_be_replaced(field: str) -> None:
    value = document()
    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=lambda: encode(value))
    before = store.reload().snapshot
    value[field] = "different"
    value["revision"] = 2
    with pytest.raises(ReconcileError, match="^owner-mismatch$"):
        store.reload()
    assert store.snapshot() is before
    assert store.status().last_error == "owner-mismatch"


def test_higher_revision_withdraws_slots_and_changes_key_reference() -> None:
    value = document()
    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=lambda: encode(value))
    first = store.reload().snapshot
    value["revision"] = 2
    value["key_sources"][0]["reference"] = "keys/api@v2"
    second = store.reload().snapshot
    assert second.generation == 2
    assert second.manifest.key_sources[0].reference == "keys/api@v2"
    assert first.manifest.key_sources[0].reference == "keys/api@v1"
    value["revision"] = 3
    value["slots"] = []
    result = store.reload()
    assert result.changed
    assert result.snapshot.generation == 3
    assert not result.snapshot.manifest.slots
    assert store.status().slots == 0


def test_stale_and_same_revision_conflict_preserve_last_known_good() -> None:
    value = document(2)
    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=lambda: encode(value))
    before = store.reload().snapshot
    value["revision"] = 1
    with pytest.raises(ReconcileError, match="^stale-revision$"):
        store.reload()
    value["revision"] = 2
    value["slots"] = []
    with pytest.raises(ReconcileError, match="^revision-conflict$"):
        store.reload()
    assert store.snapshot() is before
    assert store.status().last_error == "revision-conflict"
    value["revision"] = 3
    assert store.reload().changed
    assert store.status().last_error is None


@pytest.mark.parametrize("raw", [b"{", b'{"version":1,"version":2}', b"", None])
def test_malformed_source_does_not_withdraw_slots(raw: object) -> None:
    payload = encode(document())
    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=lambda: payload)
    before = store.reload().snapshot
    payload = raw
    with pytest.raises(ReconcileError, match="^invalid-json$"):
        store.reload()
    assert store.snapshot() is before
    assert store.status().slots == 1


def test_source_io_failure_retains_state_without_leaking_details() -> None:
    fail = False

    def source() -> bytes:
        if fail:
            raise OSError("synthetic-private-source-details")
        return encode(document())

    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=source)
    before = store.reload().snapshot
    fail = True
    with pytest.raises(ReconcileError, match="^source-unavailable$") as caught:
        store.reload()
    assert "synthetic-private" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert store.snapshot() is before
    assert store.status().last_error == "source-unavailable"
    fail = False
    assert not store.reload().changed
    assert store.status().last_error is None


def test_programming_failure_propagates_without_success_fallback() -> None:
    def source() -> bytes:
        raise RuntimeError("owner callback failed")

    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=source)
    with pytest.raises(RuntimeError, match="owner callback failed"):
        store.reload()
    assert store.status().revision is None


def test_slow_source_does_not_block_readers_or_publish_over_newer_revision() -> None:
    entered = Event()
    resume = Event()
    errors: list[str] = []

    def source() -> bytes:
        if current_thread().name == "slow-owner-read":
            entered.set()
            assert resume.wait(3)
            return encode(document(2))
        return encode(document(3))

    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=source)
    store.reload()

    def reload_slow() -> None:
        try:
            store.reload()
        except ReconcileError as exc:
            errors.append(exc.code)

    thread = Thread(target=reload_slow, name="slow-owner-read")
    thread.start()
    try:
        assert entered.wait(3)
        assert store.snapshot().manifest.revision == 3
        assert not store.reload().changed
    finally:
        resume.set()
        thread.join(3)
    assert not thread.is_alive()
    assert errors == ["stale-revision"]
    assert store.snapshot().manifest.revision == 3


def test_concurrent_identical_publications_share_one_generation() -> None:
    barrier = Barrier(8)

    def source() -> bytes:
        barrier.wait(3)
        return encode(document())

    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=source)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.reload(), range(8)))
    assert sum(result.changed for result in results) == 1
    assert len({id(result.snapshot) for result in results}) == 1
    assert store.snapshot().generation == 1


def test_readers_never_see_mixed_manifest_and_identity() -> None:
    value = document()
    store = ManifestStore(fleet="fleet-a", gateway="gateway-a", source=lambda: encode(value))
    store.reload()
    done = Event()
    errors: list[str] = []

    def reader() -> None:
        while not done.is_set():
            snapshot = store.snapshot()
            if snapshot.digest != snapshot.manifest.digest:
                errors.append("mixed")

    thread = Thread(target=reader)
    thread.start()
    try:
        for revision in range(2, 50):
            value = document(revision, slots=bool(revision % 2))
            store.reload()
    finally:
        done.set()
        thread.join(3)
    assert not thread.is_alive()
    assert not errors
    assert store.snapshot().generation == 49
