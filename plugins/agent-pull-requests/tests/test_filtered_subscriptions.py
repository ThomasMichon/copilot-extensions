"""Filtered consumers retain independent intent and delivery under the existing owner."""

import json
from pathlib import Path
from typing import Any, TypedDict

import pytest
from pr_filters import PROTOCOL, Definitions, FilterError

from agent_pull_requests.filtered_projection import FilteredProjection, PopulationDemand
from agent_pull_requests.filtered_subscriptions import FilteredSubscriptions
from agent_pull_requests.repository_contract import RepositoryBackpressure, RepositoryIdentity
from agent_pull_requests.repository_subscriptions import RepositoryRequestConflict
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid

Selected = tuple[
    list[float], ReplayStore, RepositoryIdentity, FilteredProjection, FilteredSubscriptions,
]


class StoreFlags(TypedDict, total=False):
    repository_cache: bool
    repository_continuing: bool


CAPABILITIES: dict[str, Any] = {
    "protocol": PROTOCOL, "fields": ["repo", "from", "assigned", "age", "tags", "publication"],
    "groups": [], "age_fields": ["created"],
}


def expression(where: dict[str, Any]) -> dict[str, Any]:
    return {"protocol": PROTOCOL, "where": where}


@pytest.fixture
def selected(tmp_path: Path) -> Selected:
    now = [100.0]
    store = ReplayStore(
        tmp_path, "test", clock=lambda: now[0], repository_cache=True,
        repository_continuing=True, repository_filtered=True,
    )
    store.provision()
    identity = RepositoryIdentity("test", "github", "https://github.com", "reader", "owner/repo")
    projection = FilteredProjection(store, (identity,), capabilities={identity.key: CAPABILITIES})
    return now, store, identity, projection, FilteredSubscriptions(projection)


def execute(
    selected: Selected, consumer: str, operation: str, payload: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = selected[-1].execute(
        selected[2].key, consumer, operation, payload, expires_at=10000,
    )
    return result


def subscribe(
    selected: Selected, consumer: str, where: dict[str, Any], **changes: Any,
) -> dict[str, Any]:
    return execute(selected, consumer, "subscribe", {
        "request_id": "original", "ttl": 60, "filter": expression(where), **changes,
    })


def facts(**changes: Any) -> dict[str, Any]:
    return {
        "repo": "owner/repo", "from": ["alice"], "assigned": ["bob"], "created_at": 95,
        "activity_at": None, "tags": ["ready"], "publication": "published", **changes,
    }


def observe(
    selected: Selected, pulls: dict[int, dict[str, Any]] | None = None, *,
    outcome: str = "complete", error: str = "", demand: PopulationDemand | None = None,
) -> dict[str, Any]:
    now, _store, identity, projection, _registry = selected
    demand = demand or projection.demands(expires_at=10000)[identity.key]
    result: dict[str, Any] = projection.apply(
        identity.key, demand,
        {"outcome": outcome, "error": error, "pages": 1,
         "pulls": [{"number": number, "facts": value} for number, value in (pulls or {}).items()]},
        observed_at=int(now[0]), expires_at=10000,
    )
    return result


def registration(result: dict[str, Any]) -> dict[str, Any]:
    return {"registration_id": result["subscription"]["registration_id"]}


def events(selected: Selected, consumer: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = execute(
        selected, consumer, "events", registration(result),
    )["events"]
    return pending


def test_union_acquisition_independent_membership_ack_and_original_restart(
    selected: Selected,
) -> None:
    first = subscribe(selected, "authors", {"from": {"users": ["alice"]}})
    second = subscribe(selected, "tags", {"tags": {"any": ["ready"]}})
    demand = selected[3].demands(expires_at=10000)
    assert len(demand) == 1
    combined = demand[selected[2].key]["filter"]
    assert combined.evaluate(facts(), now=100) == "match"
    observe(selected, {7: facts(), 8: facts(tags=[])})
    first_events = events(selected, "authors", first)
    second_events = events(selected, "tags", second)
    assert {e["number"] for e in first_events if e["kind"] == "entry"} == {7, 8}
    assert {e["number"] for e in second_events if e["kind"] == "entry"} == {7}
    assert {e["event_id"] for e in first_events}.isdisjoint(e["event_id"] for e in second_events)
    execute(selected, "authors", "acknowledge", {
        **registration(first), "event_id": first_events[-1]["event_id"],
    })
    assert not events(selected, "authors", first)
    assert events(selected, "tags", second) == second_events
    now, old, identity, _projection, _registry = selected
    restored = ReplayStore(
        old.root, "test", clock=lambda: now[0], repository_cache=True,
        repository_continuing=True, repository_filtered=True,
    )
    projection = FilteredProjection(restored, (identity,), capabilities={identity.key: CAPABILITIES})
    renewed = now, restored, identity, projection, FilteredSubscriptions(projection)
    retry = subscribe(renewed, "authors", {"from": {"users": ["alice"]}})
    assert registration(retry) == registration(first)
    assert retry["baseline"] == first["baseline"]
    assert not events(renewed, "authors", retry)
    with pytest.raises(KeyError):
        execute(renewed, "tags", "cancel", registration(first))
    with pytest.raises(RepositoryRequestConflict):
        subscribe(renewed, "authors", {"from": {"users": ["carol"]}})


def test_clock_partial_facts_and_complete_departure_are_distinct(selected: Selected) -> None:
    age = subscribe(selected, "age", {"age": {"field": "created", "min_seconds": 5, "max_seconds": 10}})
    other = subscribe(selected, "other", {"publication": ["published"]})
    observe(selected, {7: facts()})
    selected[0][0] = 106
    observe(selected, outcome="unavailable", error="provider_unavailable")
    age_events = events(selected, "age", age)
    assert any(e["kind"] == "departure" and e["number"] == 7 for e in age_events)
    assert not any(e["kind"] == "departure" for e in events(selected, "other", other))
    observe(selected, {7: facts(publication=None)}, outcome="partial", error="facts_missing")
    assert not any(e["kind"] == "departure" for e in events(selected, "other", other))
    observe(selected)
    assert any(e["kind"] == "departure" for e in events(selected, "other", other))


def test_expiry_cancel_and_inflight_withdrawal_preserve_only_delivered_pending(
    selected: Selected,
) -> None:
    first = subscribe(selected, "first", {"repo": ["owner/repo"]}, ttl=1)
    second = subscribe(selected, "second", {"repo": ["owner/repo"]})
    demand = selected[3].demands(expires_at=10000)[selected[2].key]
    observe(selected, {7: facts()}, demand=demand)
    pending = events(selected, "first", first)
    selected[0][0] = 102
    execute(selected, "second", "cancel", registration(second))
    assert not selected[3].demands(expires_at=10000)
    assert observe(selected, {8: facts()}, demand=demand)["status"] == "withdrawn"
    assert events(selected, "first", first) == pending
    assert not events(selected, "second", second)
    with pytest.raises(RepositoryRequestConflict, match="inactive membership"):
        execute(selected, "second", "query", registration(second))
    execute(selected, "first", "acknowledge", {
        **registration(first), "event_id": pending[-1]["event_id"],
    })
    assert not events(selected, "first", first)
    with pytest.raises(RepositoryRequestConflict, match="cancelled"):
        execute(selected, "second", "renew", {**registration(second), "expires_at": 200})


def test_absolute_renewal_is_retry_safe_and_never_resets_baseline_or_ack(
    selected: Selected,
) -> None:
    first = subscribe(selected, "first", {"repo": ["owner/repo"]}, ttl=1)
    demand = selected[3].demands(expires_at=10000)[selected[2].key]
    selected[0][0] = 102
    renewed = execute(selected, "first", "renew", {**registration(first), "expires_at": 160})
    assert renewed["status"] == "active" and renewed["expires_at"] == 160
    assert observe(selected, {7: facts()}, demand=demand)["status"] == "withdrawn"
    selected[0][0] = 110
    assert execute(selected, "first", "renew", {
        **registration(first), "expires_at": 160,
    })["expires_at"] == 160
    assert subscribe(selected, "first", {"repo": ["owner/repo"]}, ttl=1)["baseline"] == first["baseline"]
    with pytest.raises(ValueError, match="horizon"):
        execute(selected, "first", "renew", {**registration(first), "expires_at": 100000})


def test_inert_definitions_are_revision_bound_and_retries_survive_changed_configuration(
    selected: Selected,
) -> None:
    first = Definitions.parse({"protocol": PROTOCOL, "sets": {"team": expression({"from": {"users": ["alice"]}})}})
    selected[3].definitions = first
    assert not selected[3].demands(expires_at=10000)
    original = execute(selected, "first", "subscribe", {"request_id": "named", "ttl": 60, "definition": "team"})
    selected[3].definitions = Definitions.parse({"protocol": PROTOCOL, "sets": {}})
    retry = execute(selected, "first", "subscribe", {"request_id": "named", "ttl": 60, "definition": "team"})
    assert retry == original
    with pytest.raises(FilterError, match="unknown named"):
        execute(selected, "second", "subscribe", {"request_id": "new", "ttl": 60, "definition": "team"})


def test_nested_scope_and_driver_refusal_leave_existing_store_unchanged(
    selected: Selected,
) -> None:
    before = selected[1].path.read_bytes()
    for where in (
        {"any": [{"repo": ["owner/repo"]}, {"repo": ["foreign/repo"]}]},
        {"from": {"groups": ["team"]}},
        {"age": {"field": "activity", "max_seconds": 60}},
    ):
        with pytest.raises(FilterError):
            subscribe(selected, "first", where)
    assert selected[1].path.read_bytes() == before
    flags_cases: tuple[StoreFlags, ...] = (
        {}, {"repository_cache": True}, {"repository_cache": True, "repository_continuing": True},
    )
    for flags in flags_cases:
        with pytest.raises(ReplayStoreInvalid):
            ReplayStore(selected[1].root, "test", clock=selected[1].clock, **flags).observation_targets(
                expires_at=10000,
            )
    assert selected[1].path.read_bytes() == before
    old = ReplayStore(selected[1].root / "prior", "test", clock=selected[1].clock,
                      repository_cache=True, repository_continuing=True)
    old.provision()
    prior = old.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid):
        ReplayStore(old.root, "test", clock=old.clock, repository_cache=True, repository_continuing=True,
                    repository_filtered=True).observation_targets(expires_at=10000)
    assert old.path.read_bytes() == prior


def test_unsettled_backpressure_still_permits_ack_and_resume(
    selected: Selected, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import filtered_state
    first = subscribe(selected, "first", {"repo": ["owner/repo"]})
    monkeypatch.setattr(filtered_state, "MAX_EVENTS", 2)
    observe(selected, {7: facts()})
    original = selected[1].path.read_bytes()
    with pytest.raises(RepositoryBackpressure):
        observe(selected, {7: facts(tags=["changed"])})
    assert selected[1].path.read_bytes() == original
    pending = events(selected, "first", first)
    execute(selected, "first", "acknowledge", {**registration(first), "event_id": pending[-1]["event_id"]})
    observe(selected, {7: facts(tags=["changed"])})
    assert [e["kind"] for e in events(selected, "first", first)] == ["change"]


def test_unknown_facts_do_not_remove_previously_accepted_membership(selected: Selected) -> None:
    first = subscribe(selected, "first", {"tags": {"any": ["ready"]}})
    observe(selected, {7: facts()})
    observe(selected, {7: facts(tags=None)})
    result = execute(selected, "first", "query", registration(first))
    assert [p["number"] for p in result["baseline"]["pulls"]] == [7]
    assert result["baseline"]["inventory"]["outcome"] == "partial"
    assert not any(e["kind"] == "departure" for e in events(selected, "first", first))


@pytest.mark.parametrize("available", [False, True])
def test_warm_cache_join_during_acquisition_keeps_its_own_change_revision(
    selected: Selected, available: bool,
) -> None:
    first = subscribe(selected, "first", {"repo": ["owner/repo"]})
    observe(selected, {7: facts()})
    captured = selected[3].demands(expires_at=10000)[selected[2].key]
    joined = subscribe(selected, "joined", {"repo": ["owner/repo"]})
    assert joined["baseline"]["pulls"][0]["facts"]["tags"] == ["ready"]
    observe(selected, {7: facts(tags=["changed"])}, demand=captured)
    assert not events(selected, "joined", joined)
    if available:
        observe(selected, {7: facts(tags=["changed"])})
    else:
        observe(selected, outcome="unavailable", error="provider_unavailable")
    assert [(e["kind"], e["number"]) for e in events(selected, "joined", joined)
            if e["kind"] != "coverage"] == [("change", 7)]
    assert len([e for e in events(selected, "first", first) if e["kind"] == "change"]) == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_inactive_members_do_not_pin_cache_but_keep_delivered_evidence(
    selected: Selected, cancel: bool,
) -> None:
    first = subscribe(selected, "first", {"repo": ["owner/repo"]}, ttl=1)
    observe(selected, {n: facts() for n in range(1, 201)})
    pending = events(selected, "first", first)
    if cancel:
        execute(selected, "first", "cancel", registration(first))
    else:
        selected[0][0] = 102
    second = subscribe(selected, "second", {"repo": ["owner/repo"]})
    observe(selected, {n: facts() for n in range(201, 401)})
    execute(selected, "second", "cancel", registration(second))
    third = subscribe(selected, "third", {"repo": ["owner/repo"]})
    observe(selected, {n: facts() for n in range(401, 601)})
    assert execute(selected, "third", "query", {**registration(third), "limit": 16})["baseline"]["pulls"][0]["number"] == 401
    assert events(selected, "first", first) == pending
    assert subscribe(selected, "first", {"repo": ["owner/repo"]}, ttl=1)["baseline"] == first["baseline"]
    execute(selected, "first", "acknowledge", {**registration(first), "event_id": pending[-1]["event_id"]})
    if not cancel:
        execute(selected, "first", "renew", {**registration(first), "expires_at": 200})
        observe(selected, {500: facts()})
        renewed = execute(selected, "first", "query", registration(first))["baseline"]
        assert [p["number"] for p in renewed["pulls"]] == [500]
        after = None
        while True:
            page = execute(selected, "first", "events", {
                **registration(first), **({"after": after} if after is not None else {}),
            })
            if any(e["kind"] == "entry" and e["number"] == 500 for e in page["events"]):
                break
            assert page["more"]
            after = page["events"][-1]["sequence"]


def test_native_identity_and_aggregate_limits_refuse_before_mutating_demand(
    selected: Selected,
) -> None:
    from pr_filters.acquisition import PROTOCOL as ACQUISITION_PROTOCOL

    now, store, _identity, _projection, _registry = selected
    identity = RepositoryIdentity("test", "azure-devops", "https://dev.azure.com/example", "reader", "owner/repo")
    projection = FilteredProjection(store, (identity,), capabilities={identity.key: {
        **CAPABILITIES, "acquisition": {
            "protocol": ACQUISITION_PROTOCOL, "user_ids": "uuid-lowercase",
            "query_fields": {"from": "creatorId", "assigned": "reviewerId"}, "max_query_branches": 64,
        },
    }})
    selected = now, store, identity, projection, FilteredSubscriptions(projection)
    before = store.path.read_bytes()
    with pytest.raises(FilterError, match="canonical provider user"):
        subscribe(selected, "bad", {"from": {"users": ["not-a-uuid"]}})
    assert store.path.read_bytes() == before
    users = [f"{number:08x}-1111-1111-1111-111111111111" for number in range(128)]
    first = subscribe(selected, "first", {"from": {"users": users[:64]}}, ttl=1)
    before = store.path.read_bytes()
    with pytest.raises(FilterError, match="query branch limit"):
        subscribe(selected, "second", {"from": {"users": users[64:]}})
    assert store.path.read_bytes() == before
    now[0] = 102
    subscribe(selected, "second", {"from": {"users": users[64:]}})
    before = store.path.read_bytes()
    with pytest.raises(FilterError, match="query branch limit"):
        execute(selected, "first", "renew", {**registration(first), "expires_at": 200})
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("cancel", [False, True])
def test_broad_demand_cannot_mask_an_unexecutable_union_after_withdrawal(
    selected: Selected, cancel: bool,
) -> None:
    from pr_filters.acquisition import PROTOCOL as ACQUISITION_PROTOCOL

    now, store, _identity, _projection, _registry = selected
    identity = RepositoryIdentity("test", "azure-devops", "https://dev.azure.com/example", "reader", "owner/repo")
    projection = FilteredProjection(store, (identity,), capabilities={identity.key: {
        **CAPABILITIES, "acquisition": {
            "protocol": ACQUISITION_PROTOCOL, "user_ids": "uuid-lowercase",
            "query_fields": {"from": "creatorId", "assigned": "reviewerId"}, "max_query_branches": 64,
        },
    }})
    selected = now, store, identity, projection, FilteredSubscriptions(projection)
    broad = subscribe(selected, "broad", {"repo": ["owner/repo"]}, ttl=1)
    users = [f"{number:08x}-1111-1111-1111-111111111111" for number in range(128)]
    subscribe(selected, "first", {"from": {"users": users[:64]}})
    before = store.path.read_bytes()
    with pytest.raises(FilterError, match="independent withdrawal"):
        subscribe(selected, "second", {"from": {"users": users[64:]}})
    assert store.path.read_bytes() == before
    if cancel:
        execute(selected, "broad", "cancel", registration(broad))
    else:
        now[0] = 102
    demand = projection.demands(expires_at=10000)[identity.key]
    assert len(demand["registrations"]) == 1
    observe(selected, {7: facts(**{"from": [users[0]]})}, demand=demand)


@pytest.mark.timeout(60)
def test_initial_large_fanout_commits_resumable_progress_before_event_backpressure(
    selected: Selected,
) -> None:
    registrations = [subscribe(selected, f"consumer-{n}", {"repo": ["owner/repo"]}) for n in range(21)]
    population = {n: facts() for n in range(1, 201)}
    observe(selected, population)
    assert len(execute(selected, "consumer-0", "query", registration(registrations[0]))["baseline"]["pulls"]) == 16
    assert not execute(selected, "consumer-20", "query", registration(registrations[20]))["baseline"]["pulls"]
    after = None
    while True:
        page = execute(selected, "consumer-0", "events", {
            **registration(registrations[0]), **({"after": after} if after is not None else {}),
        })
        assert page["events"]
        if not page["more"]:
            break
        after = page["events"][-1]["sequence"]
    execute(selected, "consumer-0", "acknowledge", {
        **registration(registrations[0]), "event_id": page["events"][-1]["event_id"],
    })
    observe(selected, population)
    assert execute(selected, "consumer-20", "query", registration(registrations[20]))["baseline"]["pulls"]


def test_warm_cache_query_does_not_borrow_unprojected_nonmatching_facts(
    selected: Selected,
) -> None:
    subscribe(selected, "first", {"repo": ["owner/repo"]})
    observe(selected, {7: facts()})
    captured = selected[3].demands(expires_at=10000)[selected[2].key]
    joined = subscribe(selected, "joined", {"tags": {"any": ["ready"]}})
    observe(selected, {7: facts(tags=[])}, demand=captured)
    page = execute(selected, "joined", "query", registration(joined))["baseline"]
    assert page["pulls"] == joined["baseline"]["pulls"]
    assert page["pulls"][0]["facts"]["tags"] == ["ready"]
    assert not events(selected, "joined", joined)
    observe(selected, {7: facts(tags=[])})
    assert not execute(selected, "joined", "query", registration(joined))["baseline"]["pulls"]
    assert any(e["kind"] == "departure" for e in events(selected, "joined", joined))


def test_complete_absence_cannot_seed_disproved_membership_during_an_outage(
    selected: Selected,
) -> None:
    subscribe(selected, "first", {"repo": ["owner/repo"]})
    observe(selected, {7: facts()})
    observe(selected)
    joined = subscribe(selected, "joined", {"repo": ["owner/repo"]})
    assert not joined["baseline"]["pulls"]
    observe(selected, outcome="unavailable", error="provider_unavailable")
    assert not execute(selected, "joined", "query", registration(joined))["baseline"]["pulls"]


def test_byte_backpressure_accounts_for_shared_cache_before_partial_projection(
    selected: Selected, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import filtered_state

    registrations = [subscribe(selected, f"consumer-{n}", {"repo": ["owner/repo"]}) for n in range(2)]
    monkeypatch.setattr(filtered_state, "METADATA_RESERVE_BYTES", 0)
    monkeypatch.setattr(filtered_state, "MAX_CACHE_BYTES", 27000)
    population = {7: facts(tags=[str(n) + "x" * 250 for n in range(20)])}
    observe(selected, population)
    assert execute(selected, "consumer-0", "query", registration(registrations[0]))["baseline"]["pulls"]
    assert not execute(selected, "consumer-1", "query", registration(registrations[1]))["baseline"]["pulls"]
    before = selected[1].path.read_bytes()
    observe(selected, population)
    assert selected[1].path.read_bytes() == before
    assert not execute(selected, "consumer-1", "query", registration(registrations[1]))["baseline"]["pulls"]
    for consumer, registration_value in enumerate(registrations):
        if consumer:
            observe(selected, population)
        pending = events(selected, f"consumer-{consumer}", registration_value)
        assert pending
        execute(selected, f"consumer-{consumer}", "acknowledge", {
            **registration(registration_value), "event_id": pending[-1]["event_id"],
        })
    assert execute(selected, "consumer-1", "query", registration(registrations[1]))["baseline"]["pulls"]


def test_repeated_partial_acquisitions_bound_members_without_disproving_prior_membership(
    selected: Selected,
) -> None:
    first = subscribe(selected, "first", {"repo": ["owner/repo"]})
    for start in (1, 201):
        observe(selected, {n: facts() for n in range(start, start + 200)},
                outcome="partial", error="page_limit")
    other = subscribe(selected, "other", {"tags": {"any": ["new"]}})
    later = {n: facts(tags=["new"]) for n in range(401, 601)}
    observe(selected, later, outcome="partial", error="page_limit")
    result = execute(selected, "first", "query", registration(first))["baseline"]
    assert result["inventory"]["outcome"] == "partial"
    assert result["inventory"]["error"] == "membership_limit"
    assert result["pulls"][0]["number"] == 1
    pending = events(selected, "first", first)
    assert not any(e["kind"] == "departure" for e in pending)
    assert execute(selected, "other", "query", registration(other))["baseline"]["pulls"][0]["number"] == 401
    observe(selected, later)
    result = execute(selected, "first", "query", registration(first))["baseline"]
    assert result["inventory"]["outcome"] == "complete"
    assert result["pulls"][0]["number"] == 401


@pytest.mark.parametrize(("outcome", "error"), [
    ("partial", "page_limit"), ("unavailable", "provider_unavailable"),
])
def test_cached_pr_enters_and_leaves_age_window_without_fresh_acquisition(
    selected: Selected, outcome: str, error: str,
) -> None:
    watched = subscribe(selected, "age", {"age": {"field": "created", "min_seconds": 5, "max_seconds": 10}})
    observe(selected, {7: facts(created_at=100)})
    assert not execute(selected, "age", "query", registration(watched))["baseline"]["pulls"]
    selected[0][0] = 106
    observe(selected, outcome=outcome, error=error)
    entered = execute(selected, "age", "query", registration(watched))["baseline"]
    assert entered["pulls"][0]["number"] == 7
    assert entered["pulls"][0]["observed_at"] == 100
    assert entered["inventory"]["outcome"] == outcome
    assert any(e["kind"] == "entry" and e["number"] == 7 for e in events(selected, "age", watched))
    selected[0][0] = 111
    observe(selected, outcome=outcome, error=error)
    assert not execute(selected, "age", "query", registration(watched))["baseline"]["pulls"]
    assert any(e["kind"] == "departure" and e["number"] == 7 for e in events(selected, "age", watched))


def test_hot_first_subscriber_cannot_starve_later_projection_after_ack(
    selected: Selected, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import filtered_state

    first = subscribe(selected, "first", {"repo": ["owner/repo"]})
    later = subscribe(selected, "later", {"repo": ["owner/repo"]})
    monkeypatch.setattr(filtered_state, "MAX_EVENTS", 2)
    observe(selected, {7: facts()})
    pending = events(selected, "first", first)
    assert not execute(selected, "later", "query", registration(later))["baseline"]["pulls"]
    execute(selected, "first", "acknowledge", {**registration(first), "event_id": pending[-1]["event_id"]})
    observe(selected, {7: facts(tags=["changed"])})
    assert execute(selected, "later", "query", registration(later))["baseline"]["pulls"]


def test_configured_sponsor_cancellation_survives_receipt_retirement(selected: Selected) -> None:
    now, _store, identity, projection, registry = selected
    projection.sponsored_requests = frozenset({("sponsor", "startup")})
    payload = {"request_id": "startup", "ttl": 1, "filter": expression({"repo": ["owner/repo"]})}
    original = registry.execute(identity.key, "sponsor", "subscribe", payload, expires_at=1000000)
    registry.execute(identity.key, "sponsor", "cancel", registration(original), expires_at=1000000)
    now[0] = 86600
    registry.execute(identity.key, "other", "subscribe", {
        "request_id": "new", "ttl": 60, "filter": expression({"repo": ["owner/repo"]}),
    }, expires_at=1000000)
    retry = registry.execute(identity.key, "sponsor", "subscribe", payload, expires_at=1000000)
    assert retry["status"] == "cancelled"
    assert registration(retry) == registration(original)


def test_warm_baseline_pages_within_the_persisted_baseline_limit(selected: Selected) -> None:
    subscribe(selected, "first", {"repo": ["owner/repo"]})
    tags = [f"{number:03d}" + "x" * 247 for number in range(250)]
    observe(selected, {number: facts(tags=tags) for number in range(1, 17)})
    joined = subscribe(selected, "joined", {"repo": ["owner/repo"]})
    assert joined["baseline"]["more"]
    pulls = joined["baseline"]["pulls"]
    remaining = execute(selected, "joined", "query", {
        **registration(joined), "after": pulls[-1]["number"],
    })["baseline"]
    assert [p["number"] for p in [*pulls, *remaining["pulls"]]] == list(range(1, 17))


def test_slow_consumer_does_not_pin_other_consumers_acknowledged_event_space(
    selected: Selected, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import filtered_state

    slow = subscribe(selected, "slow", {"repo": ["owner/repo"]})
    fast = subscribe(selected, "fast", {"repo": ["owner/repo"]})
    monkeypatch.setattr(filtered_state, "MAX_EVENTS", 4)
    observe(selected, {7: facts()})
    retained = events(selected, "slow", slow)
    acknowledged = events(selected, "fast", fast)
    execute(selected, "fast", "acknowledge", {**registration(fast), "event_id": acknowledged[-1]["event_id"]})
    observe(selected, {7: facts(tags=["changed"])})
    pending = events(selected, "slow", slow)
    assert pending[:len(retained)] == retained
    assert [event["kind"] for event in events(selected, "fast", fast)] == ["change"]


def test_blocked_large_projection_does_not_prevent_smaller_owner_from_progressing(
    selected: Selected, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import filtered_state

    broad = subscribe(selected, "broad", {"repo": ["owner/repo"]})
    narrow = subscribe(selected, "narrow", {"tags": {"any": ["special"]}})
    monkeypatch.setattr(filtered_state, "MAX_EVENTS", 3)
    observe(selected, {7: facts()})
    retained = events(selected, "broad", broad)
    initial = events(selected, "narrow", narrow)
    execute(selected, "narrow", "acknowledge", {**registration(narrow), "event_id": initial[-1]["event_id"]})
    observe(selected, {7: facts(tags=["changed"]), 8: facts(tags=["special"])})
    assert events(selected, "broad", broad) == retained
    result = execute(selected, "narrow", "query", registration(narrow))["baseline"]
    assert [pull["number"] for pull in result["pulls"]] == [8]


def test_cache_catchup_preserves_the_timestamp_of_the_facts_it_copies(selected: Selected) -> None:
    subscribe(selected, "first", {"repo": ["owner/repo"]})
    observe(selected, {7: facts()})
    captured = selected[3].demands(expires_at=10000)[selected[2].key]
    joined = subscribe(selected, "joined", {"repo": ["owner/repo"]})
    selected[0][0] = 102
    observe(selected, {7: facts(tags=["changed"])}, demand=captured)
    selected[0][0] = 103
    observe(selected, outcome="unavailable", error="provider_unavailable")
    result = execute(selected, "joined", "query", registration(joined))["baseline"]
    assert result["pulls"][0]["facts"]["tags"] == ["changed"]
    assert result["pulls"][0]["observed_at"] == 102
    assert result["inventory"]["outcome"] == "unavailable"


@pytest.mark.parametrize(("outcome", "returned"), [
    ("unavailable", False), ("partial", False), ("partial", True),
])
def test_blocked_departures_survive_restart_ack_and_incomplete_scan(
    selected: Selected, monkeypatch: pytest.MonkeyPatch, outcome: str, returned: bool,
) -> None:
    from agent_pull_requests import filtered_state

    broad = subscribe(selected, "broad", {"repo": ["owner/repo"]})
    narrow = subscribe(selected, "narrow", {"tags": {"any": ["special"]}})
    monkeypatch.setattr(filtered_state, "MAX_EVENTS", 4)
    observe(selected, {7: facts(), 8: facts()})
    retained = events(selected, "broad", broad)
    initial = events(selected, "narrow", narrow)
    execute(selected, "narrow", "acknowledge", {**registration(narrow), "event_id": initial[-1]["event_id"]})
    observe(selected)
    assert events(selected, "broad", broad) == retained
    before = execute(selected, "broad", "query", registration(broad))["baseline"]
    assert [pull["number"] for pull in before["pulls"]] == [7, 8]

    now, old, identity, _projection, _registry = selected
    restored = ReplayStore(
        old.root, "test", clock=lambda: now[0], repository_cache=True,
        repository_continuing=True, repository_filtered=True,
    )
    projection = FilteredProjection(restored, (identity,), capabilities={identity.key: CAPABILITIES})
    renewed = now, restored, identity, projection, FilteredSubscriptions(projection)
    execute(renewed, "broad", "acknowledge", {**registration(broad), "event_id": retained[-1]["event_id"]})
    observe(renewed, {7: facts()} if returned else {}, outcome=outcome, error="provider_incomplete")
    current = execute(renewed, "broad", "query", registration(broad))["baseline"]
    assert [pull["number"] for pull in current["pulls"]] == ([7] if returned else [])
    pending = events(renewed, "broad", broad)
    assert {event["number"] for event in pending if event["kind"] == "departure"} == (
        {8} if returned else {7, 8}
    )
    assert not {event["event_id"] for event in pending} & {event["event_id"] for event in retained}
    observe(renewed, {7: facts()} if returned else {}, outcome=outcome, error="provider_incomplete")
    assert events(renewed, "broad", broad) == pending


@pytest.mark.parametrize(("returned", "unknown"), [(False, False), (False, True), (True, True)])
def test_partial_nonmatch_survives_projection_rollback_and_cache_eviction(
    selected: Selected, monkeypatch: pytest.MonkeyPatch, returned: bool, unknown: bool,
) -> None:
    from agent_pull_requests import filtered_state

    broad = subscribe(selected, "broad", {"tags": {"any": ["special"]}})
    narrow = subscribe(selected, "narrow", {"publication": ["draft"]})
    monkeypatch.setattr(filtered_state, "MAX_EVENTS", 4)
    observe(selected, {1007: facts(tags=["special"]), 1008: facts(tags=["special"])})
    retained = events(selected, "broad", broad)
    initial = events(selected, "narrow", narrow)
    execute(selected, "narrow", "acknowledge", {
        **registration(narrow), "event_id": initial[-1]["event_id"],
    })
    observe(selected, {1007: facts(), 1008: facts()}, outcome="partial", error="page_limit")
    assert events(selected, "broad", broad) == retained
    ready = events(selected, "narrow", narrow)
    execute(selected, "narrow", "acknowledge", {
        **registration(narrow), "event_id": ready[-1]["event_id"],
    })
    if unknown:
        observe(selected, {1007: facts(tags=None), 1008: facts(tags=None)},
                outcome="partial", error="facts_missing")
        ready = events(selected, "narrow", narrow)
        execute(selected, "narrow", "acknowledge", {
            **registration(narrow), "event_id": ready[-1]["event_id"],
        })
    for start in (1, 201):
        observe(selected, {number: facts() for number in range(start, start + 200)},
                outcome="partial", error="page_limit")
    cache = json.loads(selected[1].path.read_bytes())["repository_cache"]["repositories"][0]["pulls"]
    assert not {1007, 1008} & {pull["number"] for pull in cache}
    assert [pull["number"] for pull in execute(
        selected, "broad", "query", registration(broad),
    )["baseline"]["pulls"]] == [1007, 1008]
    now, old, identity, _projection, _registry = selected
    restored = ReplayStore(
        old.root, "test", clock=lambda: now[0], repository_cache=True,
        repository_continuing=True, repository_filtered=True,
    )
    projection = FilteredProjection(restored, (identity,), capabilities={identity.key: CAPABILITIES})
    renewed = now, restored, identity, projection, FilteredSubscriptions(projection)
    execute(renewed, "broad", "acknowledge", {
        **registration(broad), "event_id": retained[-1]["event_id"],
    })
    observe(renewed, {1007: facts(tags=["special"])} if returned else {},
            outcome="partial" if returned else "unavailable", error="provider_incomplete")
    assert [pull["number"] for pull in execute(
        renewed, "broad", "query", registration(broad),
    )["baseline"]["pulls"]] == ([1007] if returned else [])
    assert {event["number"] for event in events(renewed, "broad", broad)
            if event["kind"] == "departure"} == ({1008} if returned else {1007, 1008})
