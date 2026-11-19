"""Continuing cache delivery is independent, durable and never another forge reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_pull_requests.repository_contract import RepositoryBackpressure, RepositoryIdentity
from agent_pull_requests.repository_projection import RepositoryProjection
from agent_pull_requests.repository_subscriptions import (
    RepositoryHistoryGap, RepositoryRequestConflict, RepositoryRequestExpired,
    RepositorySubscriptions,
)
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid

Selection = tuple[
    list[float], ReplayStore, RepositoryIdentity, RepositoryProjection, RepositorySubscriptions,
]


@pytest.fixture
def selected(tmp_path: Path) -> Selection:
    now = [100.0]
    store = ReplayStore(
        tmp_path, "test", clock=lambda: now[0],
        repository_cache=True, repository_continuing=True,
    )
    store.provision()
    identity = RepositoryIdentity("test", "github", "https://github.com", "reader", "owner/repo")
    projection = RepositoryProjection(store, (identity,))
    return now, store, identity, projection, RepositorySubscriptions(projection)


def observe(selected: Selection, head: str = "a") -> dict[str, Any]:
    _now, _store, identity, projection, _registry = selected
    snapshot = decode_snapshot({
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "provider": identity.provider, "api_base": identity.endpoint, "repo": identity.repo,
        "pr": 7, "pr_state": "open", "merged": False, "head_sha": head, "base_ref": "dev",
        "mergeable": None, "checks_state": "", "title": "Synthetic", "draft": False, "reviews": [],
    }, identity.repo, 7)
    return projection.apply(
        {identity.key: {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}},
        {(identity.key, identity.repo, 7): snapshot}, expires_at=10000,
    )


def execute(
    selected: Selection, consumer: str, operation: str, payload: dict[str, Any],
) -> dict[str, Any]:
    return selected[-1].execute(
        selected[2].key, consumer, operation, payload, expires_at=10000,
    )


def recover(
    selected: Selection, consumer: str, operation: str, payload: dict[str, Any], **authority: Any,
) -> dict[str, Any]:
    return selected[-1].execute(
        selected[2].key, consumer, operation, payload,
        expires_at=int(selected[0][0]) + 1000, **authority,
    )


def test_original_request_lookup_is_read_only_scoped_and_preserves_cutoff_after_reopen(
    selected: Selection,
) -> None:
    from dataclasses import replace
    now, old, identity, _projection, _registry = selected
    before = old.path.read_bytes()
    absent = execute(selected, "first", "inspect-request", {"request_id": "original"})
    assert absent == {
        "status": "not_found", "request_id": "original", "subscription": None, "now": 100,
    }
    assert old.path.read_bytes() == before
    payload = {"request_id": "original", "ttl": 3600, "not_after": 110}
    original = execute(selected, "first", "subscribe", payload)["subscription"]
    assert original["expires_at"] == 110 and original["request"]["not_after"] == 110
    restored = ReplayStore(
        old.root, "test", clock=lambda: now[0], repository_cache=True, repository_continuing=True,
    )
    projection = RepositoryProjection(restored, (identity,))
    reopened = now, restored, identity, projection, RepositorySubscriptions(projection)
    before = restored.path.read_bytes()
    found = execute(reopened, "first", "inspect-request", {"request_id": "original"})
    assert found["subscription"] == original and found["status"] == "active"
    assert execute(reopened, "second", "inspect-request", {
        "request_id": "original",
    })["status"] == "not_found"
    other = replace(identity, repo="other/repository")
    foreign = RepositorySubscriptions(RepositoryProjection(restored, (identity, other)))
    assert foreign.execute(
        other.key, "first", "inspect-request", {"request_id": "original"}, expires_at=10000,
    )["status"] == "not_found"
    assert restored.path.read_bytes() == before
    now[0] = 110
    assert execute(reopened, "first", "inspect-request", {
        "request_id": "original",
    })["status"] == "expired"
    assert execute(reopened, "first", "subscribe", payload)["subscription"] == original
    execute(reopened, "first", "cancel", {"registration_id": original["registration_id"]})
    assert execute(reopened, "first", "inspect-request", {
        "request_id": "original",
    })["status"] == "cancelled"
    for changed in ({"not_after": 111}, {"ttl": 1}, {"after": 0}):
        with pytest.raises(RepositoryRequestConflict):
            execute(reopened, "first", "subscribe", payload | changed)
    with pytest.raises(RepositoryRequestConflict):
        execute(reopened, "first", "subscribe", {"request_id": "original", "ttl": 3600})


def test_cutoff_keeps_shorter_ttl_and_legacy_request_shape(selected: Selection) -> None:
    bounded = execute(selected, "first", "subscribe", {
        "request_id": "bounded", "ttl": 2, "not_after": 110,
    })["subscription"]
    legacy = execute(selected, "first", "subscribe", {
        "request_id": "legacy", "ttl": 20,
    })["subscription"]
    assert bounded["expires_at"] == 102
    assert legacy["expires_at"] == 120 and legacy["request"] == {"after": None, "ttl": 20}


@pytest.mark.parametrize("cutoff", [0, -1, True, 110.0, "110", None, 2**53])
def test_invalid_cutoff_refuses_without_changing_original_state(
    selected: Selection, cutoff: object,
) -> None:
    before = selected[1].path.read_bytes()
    with pytest.raises(ValueError):
        execute(selected, "first", "subscribe", {
            "request_id": "original", "ttl": 60, "not_after": cutoff,
        })
    assert selected[1].path.read_bytes() == before


@pytest.mark.parametrize("now", [110, 111])
def test_new_subscription_at_or_after_original_cutoff_is_refused(
    selected: Selection, now: int,
) -> None:
    selected[0][0] = now
    before = selected[1].path.read_bytes()
    with pytest.raises(RepositoryRequestExpired):
        execute(selected, "first", "subscribe", {
            "request_id": "late", "ttl": 60, "not_after": 110,
        })
    assert selected[1].path.read_bytes() == before


def test_delayed_creation_rechecks_cutoff_after_actual_writer_lock(selected: Selection) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import threading
    entered = threading.Event()
    before = selected[1].path.read_bytes()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with selected[1]._lock():
            pending = pool.submit(recover, selected, "first", "subscribe", {
                "request_id": "delayed", "ttl": 60, "not_after": 110,
            }, checkpoint=entered.set)
            assert entered.wait(2)
            selected[0][0] = 110
        with pytest.raises(RepositoryRequestExpired):
            pending.result(timeout=2)
    assert selected[1].path.read_bytes() == before


def test_creation_rechecks_cutoff_before_commit_not_only_at_transaction_entry(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import repository_subscriptions
    original = repository_subscriptions._page
    def crosses_cutoff(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(*args, **kwargs)
        selected[0][0] = 110
        return result
    monkeypatch.setattr(repository_subscriptions, "_page", crosses_cutoff)
    before = selected[1].path.read_bytes()
    with pytest.raises(RepositoryRequestExpired):
        execute(selected, "first", "subscribe", {
            "request_id": "delayed", "ttl": 60, "not_after": 110,
        })
    assert selected[1].path.read_bytes() == before


def test_lost_committed_result_is_inspected_without_repeating_subscribe(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_replay_store
    original = watch_replay_store._atomic_write_json
    def lost_result(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise ConnectionError("committed result lost")
    with monkeypatch.context() as patch:
        patch.setattr(watch_replay_store, "_atomic_write_json", lost_result)
        with pytest.raises(ConnectionError):
            execute(selected, "first", "subscribe", {
                "request_id": "lost", "ttl": 60, "not_after": 110,
            })
    before = selected[1].path.read_bytes()
    recovered = execute(selected, "first", "inspect-request", {"request_id": "lost"})
    assert recovered["status"] == "active" and recovered["subscription"]["expires_at"] == 110
    assert selected[1].path.read_bytes() == before


def test_cutoff_crossed_after_atomic_write_retains_original_expired_receipt(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_replay_store
    original = watch_replay_store._atomic_write_json
    def crosses_cutoff(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        selected[0][0] = 110
    with monkeypatch.context() as patch:
        patch.setattr(watch_replay_store, "_atomic_write_json", crosses_cutoff)
        with pytest.raises(RepositoryRequestExpired):
            execute(selected, "first", "subscribe", {
                "request_id": "committed", "ttl": 60, "not_after": 110,
            })
    recovered = execute(selected, "first", "inspect-request", {"request_id": "committed"})
    assert recovered["status"] == "expired"
    assert recovered["subscription"]["request"] == {"after": None, "ttl": 60, "not_after": 110}


def test_collected_receipt_cannot_be_recreated_by_late_original_bounded_request(
    selected: Selection,
) -> None:
    payload = {"request_id": "original", "ttl": 60, "not_after": 110}
    recover(selected, "first", "subscribe", payload)
    selected[0][0] = 90000
    recover(selected, "first", "subscribe", {"request_id": "trigger", "ttl": 60})
    before = selected[1].path.read_bytes()
    assert recover(selected, "first", "inspect-request", {
        "request_id": "original",
    })["status"] == "not_found"
    with pytest.raises(RepositoryRequestExpired):
        recover(selected, "first", "subscribe", payload)
    assert selected[1].path.read_bytes() == before


def test_expired_delivered_unacknowledged_receipt_survives_collection_and_remains_recoverable(
    selected: Selection,
) -> None:
    observe(selected)
    original = recover(selected, "first", "subscribe", {
        "request_id": "original", "ttl": 60, "not_after": 110, "after": 0,
    })["subscription"]
    registration = {"registration_id": original["registration_id"]}
    event = recover(selected, "first", "events", registration)["events"][-1]
    selected[0][0] = 90000
    recover(selected, "first", "subscribe", {"request_id": "trigger", "ttl": 60})
    found = recover(selected, "first", "inspect-request", {"request_id": "original"})
    assert found["status"] == "expired" and found["subscription"]["registration_id"] == original["registration_id"]
    recover(selected, "first", "acknowledge", registration | {"event_id": event["event_id"]})
    recover(selected, "first", "subscribe", {"request_id": "later-trigger", "ttl": 60})
    assert recover(selected, "first", "inspect-request", {
        "request_id": "original",
    })["status"] == "not_found"


def test_prior_strict_request_reader_refuses_bounded_state_without_rewriting(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import repository_state
    execute(selected, "first", "subscribe", {"request_id": "legacy", "ttl": 60})
    original = repository_state._fields
    def prior_fields(value: object, names: str) -> dict[str, Any]:
        return original(value, "after ttl" if names == "after ttl not_after" else names)
    with monkeypatch.context() as patch:
        patch.setattr(repository_state, "_fields", prior_fields)
        assert selected[1].observation_targets(expires_at=10000) == ()
    execute(selected, "first", "subscribe", {
        "request_id": "bounded", "ttl": 60, "not_after": 110,
    })
    before = selected[1].path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(repository_state, "_fields", prior_fields)
        with pytest.raises(ValueError):
            selected[1].observation_targets(expires_at=10000)
    assert selected[1].path.read_bytes() == before
    assert execute(selected, "first", "inspect-request", {
        "request_id": "bounded",
    })["status"] == "active"


@pytest.mark.parametrize("cutoff", [True, 0, 100, 2**53])
def test_invalid_persisted_cutoff_is_not_adopted_or_repaired(
    selected: Selection, cutoff: object,
) -> None:
    import json
    execute(selected, "first", "subscribe", {
        "request_id": "bounded", "ttl": 60, "not_after": 110,
    })
    document = json.loads(selected[1].path.read_bytes())
    document["repository_cache"]["registrations"][0]["request"]["not_after"] = cutoff
    selected[1].path.write_text(json.dumps(document))
    before = selected[1].path.read_bytes()
    with pytest.raises(ValueError):
        execute(selected, "first", "inspect-request", {"request_id": "bounded"})
    assert selected[1].path.read_bytes() == before


def test_independent_continuing_consumers_ack_restart_and_original_request_reconciliation(
    selected: Selection,
) -> None:
    observe(selected)
    payload = {"request_id": "original", "ttl": 3600, "after": 0}
    first = execute(selected, "first", "subscribe", payload)["subscription"]
    second = execute(selected, "second", "subscribe", payload)["subscription"]
    assert first["registration_id"] != second["registration_id"]
    batch = execute(selected, "first", "events", {"registration_id": first["registration_id"]})
    ids = [event["event_id"] for event in batch["events"]]
    execute(selected, "first", "acknowledge", {
        "registration_id": first["registration_id"], "event_id": ids[-1],
    })
    assert not execute(selected, "first", "events", {"registration_id": first["registration_id"]})["events"]
    assert [event["event_id"] for event in execute(
        selected, "second", "events", {"registration_id": second["registration_id"]},
    )["events"]] == ids
    now, old, identity, _projection, _registry = selected
    restored = ReplayStore(
        old.root, "test", clock=lambda: now[0], repository_cache=True, repository_continuing=True,
    )
    projection = RepositoryProjection(restored, (identity,))
    new = now, restored, identity, projection, RepositorySubscriptions(projection)
    same = execute(new, "first", "subscribe", payload)["subscription"]
    assert same["registration_id"] == first["registration_id"]
    assert same["acknowledged"] == batch["events"][-1]["sequence"]
    observe(new, "b")
    later = execute(new, "first", "events", {"registration_id": first["registration_id"]})
    assert len(later["events"]) == 1 and later["events"][0]["kind"] == "change"
    assert later["events"][0]["event_id"] not in ids
    with pytest.raises(RepositoryRequestConflict):
        execute(new, "first", "subscribe", payload | {"ttl": 1})
    with pytest.raises(KeyError):
        execute(new, "second", "cancel", {"registration_id": first["registration_id"]})


def test_cancel_and_expiry_retain_delivered_pending_until_ack_and_report_history_gap(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import repository_projection
    observe(selected)
    monkeypatch.setattr(repository_projection, "MAX_EVENTS", 3)
    row = execute(selected, "first", "subscribe", {
        "request_id": "original", "ttl": 1, "after": 0,
    })["subscription"]
    registration = {"registration_id": row["registration_id"]}
    event = execute(selected, "first", "events", registration | {"limit": 1})["events"][0]
    execute(selected, "first", "cancel", registration)
    selected[0][0] = 102
    with pytest.raises(RepositoryBackpressure):
        observe(selected, "b")
    pending = execute(selected, "first", "events", registration)
    assert pending["status"] == "cancelled" and pending["events"][0]["event_id"] == event["event_id"]
    ack = registration | {"event_id": event["event_id"]}
    execute(selected, "first", "acknowledge", ack)
    observe(selected, "b")
    assert not execute(selected, "first", "events", registration)["events"]
    assert execute(selected, "first", "acknowledge", ack)["status"] == "acknowledged"
    with pytest.raises(RepositoryHistoryGap):
        execute(selected, "new", "subscribe", {"request_id": "old-cursor", "ttl": 60, "after": 0})


def test_undelivered_ack_and_future_cursor_refuse_without_advancing(selected: Selection) -> None:
    observe(selected)
    row = execute(selected, "first", "subscribe", {
        "request_id": "original", "ttl": 60, "after": 0,
    })["subscription"]
    events = selected[1]._review_transaction(
        lambda value: value["repository_cache"]["events"], expires_at=10000, persist=False,
    )
    with pytest.raises(ValueError):
        execute(selected, "first", "acknowledge", {
            "registration_id": row["registration_id"], "event_id": events[-1]["event_id"],
        })
    with pytest.raises(ValueError):
        execute(selected, "first", "events", {"registration_id": row["registration_id"], "after": 100})
    assert execute(selected, "first", "inspect", {
        "registration_id": row["registration_id"],
    })["subscription"]["acknowledged"] == 0


def test_current_prior_and_rollback_layouts_refuse_without_recreation(selected: Selection) -> None:
    _now, store, _identity, _projection, _registry = selected
    original = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        ReplayStore(store.root, "test", clock=store.clock, repository_cache=True).observation_targets(expires_at=10000)
    assert store.path.read_bytes() == original
    with pytest.raises(FileExistsError):
        store.provision()
    prior = ReplayStore(store.root / "prior", "test", clock=store.clock, repository_cache=True)
    prior.provision()
    before = prior.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        ReplayStore(
            prior.root, "test", clock=store.clock, repository_cache=True, repository_continuing=True,
        ).observation_targets(expires_at=10000)
    assert prior.path.read_bytes() == before


def test_expiry_delivers_no_new_events_but_retains_original_pending_ack(selected: Selection) -> None:
    observe(selected)
    row = execute(selected, "first", "subscribe", {
        "request_id": "expires", "ttl": 1, "after": 0,
    })["subscription"]
    registration = {"registration_id": row["registration_id"]}
    event = execute(selected, "first", "events", registration | {"limit": 1})["events"][0]
    selected[0][0] = 102
    pending = execute(selected, "first", "events", registration)
    assert pending["status"] == "expired"
    assert [item["event_id"] for item in pending["events"]] == [event["event_id"]]
    execute(selected, "first", "acknowledge", registration | {"event_id": event["event_id"]})
    assert not execute(selected, "first", "events", registration)["events"]


def test_byte_backpressure_reserves_ack_progress_and_evicts_only_settled_history(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import repository_state
    monkeypatch.setattr(repository_state, "MAX_CACHE_BYTES", 16384)
    monkeypatch.setattr(repository_state, "METADATA_RESERVE_BYTES", 4096)
    observe(selected)
    row = execute(selected, "first", "subscribe", {"request_id": "bounded", "ttl": 3600})["subscription"]
    blocked = False
    for index in range(50):
        try:
            observe(selected, f"head-{index}")
        except RepositoryBackpressure:
            blocked = True
            break
    assert blocked
    registration = {"registration_id": row["registration_id"]}
    pending = execute(selected, "first", "events", registration)
    assert pending["events"]
    execute(selected, "first", "acknowledge", registration | {
        "event_id": pending["events"][-1]["event_id"],
    })
    observe(selected, "resumed")
    current = selected[3].query(selected[2].key, expires_at=10000)
    assert current["pulls"][0]["facts"]["head_sha"] == "resumed"


def test_subscription_precedes_first_observation_and_keeps_original_baseline(selected: Selection) -> None:
    registration = execute(selected, "early", "subscribe", {
        "request_id": "early", "ttl": 3600,
    })
    assert registration["baseline"]["inventory"]["outcome"] == "unknown"
    assert registration["baseline"]["pulls"] == []
    observe(selected)
    pending = execute(selected, "early", "events", {
        "registration_id": registration["subscription"]["registration_id"],
    })
    assert any(event["kind"] == "baseline" for event in pending["events"])


def test_query_pages_by_encoded_bytes_without_losing_current_rows(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    import json
    from agent_pull_requests import repository_subscriptions
    observe(selected)
    def populate(document: dict[str, Any]) -> None:
        repository = document["repository_cache"]["repositories"][0]
        original = repository["pulls"][0]
        repository["pulls"] = [
            {**copy.deepcopy(original), "number": number} for number in range(1, 17)
        ]
    selected[1]._review_transaction(populate, expires_at=10000)
    monkeypatch.setattr(repository_subscriptions, "RESPONSE_BYTES", 10000)
    numbers: list[int] = []
    after = 0
    while True:
        page = selected[3].query(selected[2].key, after=after, expires_at=10000)
        assert page["pulls"] and len(json.dumps(page).encode()) < 10000 - 8192
        numbers.extend(pull["number"] for pull in page["pulls"])
        if not page["more"]:
            break
        after = page["pulls"][-1]["number"]
    assert numbers == list(range(1, 17))


def test_large_settled_history_trimming_does_not_reencode_full_cache_per_eviction(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    import json
    from agent_pull_requests import repository_state
    from agent_pull_requests.repository_projection import _trim_bytes
    observe(selected)
    state = selected[1]._review_transaction(
        lambda value: copy.deepcopy(value["repository_cache"]), expires_at=10000, persist=False,
    )
    event = state["events"][-1]
    state["events"] = [
        {**copy.deepcopy(event), "sequence": sequence, "event_id": f"{sequence:032x}"}
        for sequence in range(1, 1001)
    ]
    state["next_sequence"] = 1001
    original = json.dumps
    full_encodes: list[int] = []
    def measured(value: Any, **kwargs: Any) -> str:
        if isinstance(value, dict) and set(value) == set(state):
            full_encodes.append(len(value["events"]))
        return original(value, **kwargs)
    monkeypatch.setattr(repository_state, "MAX_CACHE_BYTES", 8192)
    monkeypatch.setattr(repository_state, "METADATA_RESERVE_BYTES", 4096)
    monkeypatch.setattr(json, "dumps", measured)
    _trim_bytes(state, 100, 1001, 2)
    assert 0 < len(full_encodes) <= 3
    assert 0 < len(state["events"]) < 20 and state["events"][-1]["sequence"] == 1000
    assert len(original(state).encode()) <= 4096


def test_subscription_page_accounts_for_its_actual_registration_envelope(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    import json
    from agent_pull_requests import repository_subscriptions
    observe(selected)
    def populate(document: dict[str, Any]) -> None:
        repository = document["repository_cache"]["repositories"][0]
        original = repository["pulls"][0]
        repository["pulls"] = [
            {**copy.deepcopy(original), "number": number} for number in range(1, 17)
        ]
    selected[1]._review_transaction(populate, expires_at=10000)
    monkeypatch.setattr(repository_subscriptions, "RESPONSE_BYTES", 14000)
    result = execute(selected, "consumer" * 16, "subscribe", {
        "request_id": "\U0001f642" * 128, "ttl": 3600,
    })
    assert result["baseline"]["pulls"] and result["baseline"]["more"]
    assert len(json.dumps(result).encode()) <= 14000 - 8192
    assert execute(selected, "consumer" * 16, "inspect", {
        "registration_id": result["subscription"]["registration_id"],
    })["status"] == "active"


def test_event_page_accounts_for_registration_id_before_admitting_delivery(
    selected: Selection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from agent_pull_requests import repository_subscriptions
    observe(selected)
    row = execute(selected, "reader", "subscribe", {
        "request_id": "original", "ttl": 3600, "after": 0,
    })["subscription"]
    monkeypatch.setattr(repository_subscriptions, "RESPONSE_BYTES", 14000)
    def enlarge(document: dict[str, Any]) -> None:
        events = document["repository_cache"]["events"]
        candidate = {"status": "active", "events": events, "more": True}
        padding = 14000 - 8192 - len(json.dumps(candidate).encode())
        assert padding > 0
        events[-1]["facts"]["title"] += "x" * padding
    selected[1]._review_transaction(enlarge, expires_at=10000)
    registration = {"registration_id": row["registration_id"]}
    first = execute(selected, "reader", "events", registration)
    assert first["events"] and first["more"]
    assert len(json.dumps(first).encode()) <= 14000 - 8192
    execute(selected, "reader", "acknowledge", registration | {
        "event_id": first["events"][-1]["event_id"],
    })
    last = execute(selected, "reader", "events", registration)
    assert len(last["events"]) == 1 and not last["more"]
