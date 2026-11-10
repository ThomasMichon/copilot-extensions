"""Continuing cache delivery is independent, durable and never another forge reader."""

import pytest

from agent_pull_requests.repository_contract import RepositoryBackpressure, RepositoryIdentity
from agent_pull_requests.repository_projection import RepositoryProjection
from agent_pull_requests.repository_subscriptions import (
    RepositoryHistoryGap, RepositoryRequestConflict, RepositorySubscriptions,
)
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid


@pytest.fixture
def selected(tmp_path):
    now = [100.0]
    store = ReplayStore(
        tmp_path, "test", clock=lambda: now[0],
        repository_cache=True, repository_continuing=True,
    )
    store.provision()
    identity = RepositoryIdentity("test", "github", "https://github.com", "reader", "owner/repo")
    projection = RepositoryProjection(store, (identity,))
    return now, store, identity, projection, RepositorySubscriptions(projection)


def observe(selected, head="a"):
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


def execute(selected, consumer, operation, payload):
    return selected[-1].execute(
        selected[2].key, consumer, operation, payload, expires_at=10000,
    )


def test_independent_continuing_consumers_ack_restart_and_original_request_reconciliation(selected):
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
    new = (now, restored, identity, RepositoryProjection(restored, (identity,)), None)
    new = (*new[:-1], RepositorySubscriptions(new[-2]))
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


def test_cancel_and_expiry_retain_delivered_pending_until_ack_and_report_history_gap(selected, monkeypatch):
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


def test_undelivered_ack_and_future_cursor_refuse_without_advancing(selected):
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


def test_current_prior_and_rollback_layouts_refuse_without_recreation(selected):
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


def test_expiry_delivers_no_new_events_but_retains_original_pending_ack(selected):
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


def test_byte_backpressure_reserves_ack_progress_and_evicts_only_settled_history(selected, monkeypatch):
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


def test_subscription_precedes_first_observation_and_keeps_original_baseline(selected):
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


def test_query_pages_by_encoded_bytes_without_losing_current_rows(selected, monkeypatch):
    import copy
    import json
    from agent_pull_requests import repository_subscriptions
    observe(selected)
    def populate(document):
        repository = document["repository_cache"]["repositories"][0]
        original = repository["pulls"][0]
        repository["pulls"] = [
            {**copy.deepcopy(original), "number": number} for number in range(1, 17)
        ]
    selected[1]._review_transaction(populate, expires_at=10000)
    monkeypatch.setattr(repository_subscriptions, "RESPONSE_BYTES", 10000)
    numbers, after = [], 0
    while True:
        page = selected[3].query(selected[2].key, after=after, expires_at=10000)
        assert page["pulls"] and len(json.dumps(page).encode()) < 10000 - 8192
        numbers.extend(pull["number"] for pull in page["pulls"])
        if not page["more"]:
            break
        after = page["pulls"][-1]["number"]
    assert numbers == list(range(1, 17))


def test_large_settled_history_trimming_does_not_reencode_full_cache_per_eviction(selected, monkeypatch):
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
    original, full_encodes = json.dumps, []
    def measured(value, **kwargs):
        if isinstance(value, dict) and set(value) == set(state):
            full_encodes.append(len(value["events"]))
        return original(value, **kwargs)
    monkeypatch.setattr(repository_state, "MAX_CACHE_BYTES", 8192)
    monkeypatch.setattr(repository_state, "METADATA_RESERVE_BYTES", 4096)
    monkeypatch.setattr(repository_state.json, "dumps", measured)
    _trim_bytes(state, 100, 1001, 2)
    assert 0 < len(full_encodes) <= 3
    assert 0 < len(state["events"]) < 20 and state["events"][-1]["sequence"] == 1000
    assert len(original(state).encode()) <= 4096
