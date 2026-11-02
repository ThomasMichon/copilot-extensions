"""Pure admission, bounded-window and lineage display controls."""

from dataclasses import replace

import pytest

from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, HistoryPage, LiveDelta, MAX_EXPANDED_GROUPS,
    MAX_LANES, Qualification, RelationKind, TimelineRelation,
)
from worktree_manager.companion_timeline_model import ResultCode, TimelineWindow
from _companion_timeline_fixtures import event, position, snapshot, token


def test_atomic_snapshot_delta_deduplication_and_cursor_fences():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    first = tuple(event(source, number) for number in range(3))
    assert model.apply_snapshot(snapshot(source, first)).applied
    assert model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), (event(source, 3),),
    )).applied
    assert [item.event_id for item in model.events] == [f"event-{number}" for number in range(4)]
    duplicate = LiveDelta(source, position(source, "live-1"), position(source, "live-2"), (first[0],))
    assert model.apply_delta(duplicate).applied
    assert model.events[0] == first[0]
    before = model.events
    result = model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-3"), (),
    ))
    assert not result.applied and result.code is ResultCode.CURSOR_MISMATCH
    assert model.events == before


def test_content_proofs_survive_display_eviction_and_unavailability():
    source = token()
    model = TimelineWindow(max_events=2)
    model.bind(source)
    first = tuple(event(source, number) for number in range(3))
    assert model.apply_snapshot(snapshot(source, first)).evicted == 1
    assert first[0] not in model.events
    conflict = replace(first[0], summary="Changed immutable event")
    result = model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), (conflict,),
    ))
    assert result.code is ResultCode.CONTENT_CONFLICT
    unavailable = replace(snapshot(source, (), before=None), availability=Availability.UNAVAILABLE, position=None)
    assert model.apply_snapshot(unavailable).applied and not model.events
    result = model.apply_snapshot(snapshot(source, (conflict,), cursor="live-new"))
    assert result.code is ResultCode.CONTENT_CONFLICT
    assert not model.events


def test_rebind_epoch_and_head_fence_late_pages_without_fake_empty_success():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    model.apply_snapshot(snapshot(source, (event(source, 5),)))
    assert model.request_history(source, position(source, "older-0")).applied
    page = HistoryPage(source, position(source, "older-0"), None, (event(source, 4),), Completeness.COMPLETE)
    renewed = token(1)
    model.bind(renewed)
    result = model.prepend_page(page)
    assert not result.applied and result.code is ResultCode.STALE_TOKEN
    assert model.events[0].event_id == "event-5"
    successor = token(2, head="head-b")
    model.bind(successor)
    assert not model.events
    result = model.apply_snapshot(snapshot(source, (event(source, 5),)))
    assert result.code is ResultCode.STALE_TOKEN
    assert model.sources[successor.identity.source_key].availability is Availability.UNKNOWN
    assert model.bind(renewed).code is ResultCode.STALE_TOKEN


def test_history_prepend_has_exact_pending_boundary_and_retains_live_position():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    model.apply_snapshot(snapshot(source, tuple(event(source, number) for number in range(5, 8))))
    before = position(source, "older-0")
    page = HistoryPage(source, before, None, tuple(event(source, number) for number in range(3, 6)), Completeness.COMPLETE)
    assert not model.prepend_page(page).applied
    assert model.request_history(source, before).applied
    assert model.apply_delta(LiveDelta(source, position(source, "live-0"), position(source, "live-1"), (event(source, 8),))).applied
    assert model.prepend_page(page).applied
    assert [item.event_id for item in model.events] == [f"event-{number}" for number in range(3, 9)]
    assert model.sources[source.identity.source_key].position == position(source, "live-1")
    assert model.sources[source.identity.source_key].before is None


def test_byte_and_proof_limits_are_explicit_and_do_not_evict_evidence():
    source = token()
    first = event(source, 1)
    model = TimelineWindow(max_bytes=first.encoded_size, max_proofs=2)
    model.bind(source)
    assert model.apply_snapshot(snapshot(source, (first,))).applied
    assert model.apply_delta(LiveDelta(source, position(source, "live-0"), position(source, "live-1"), (event(source, 2),))).applied
    assert model.encoded_size <= first.encoded_size
    before = model.events
    result = model.apply_delta(LiveDelta(source, position(source, "live-1"), position(source, "live-2"), (event(source, 3),)))
    assert not result.applied and result.code is ResultCode.LIMIT
    assert model.events == before


def test_actor_lanes_overflow_and_expansion_limits_remain_stable():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    rows = tuple(event(source, number, actor=f"actor-{number}") for number in range(40))
    model.apply_snapshot(snapshot(source, rows))
    assert model.lane_count == MAX_LANES
    assert model.lane(rows[-1]) == MAX_LANES - 1
    original_lane = model.lane(rows[0])
    for item in rows[:MAX_EXPANDED_GROUPS]:
        assert model.toggle_expansion(item.ref).applied
    assert model.toggle_expansion(rows[MAX_EXPANDED_GROUPS].ref).code is ResultCode.LIMIT
    assert model.lane(rows[0]) == original_lane


@pytest.mark.parametrize("availability", [Availability.HEAD_CHANGED, Availability.UNAVAILABLE])
def test_current_context_excludes_unobserved_snapshots(availability: Availability) -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    observed = snapshot(source, (event(source, 0),))
    assert model.apply_snapshot(observed).applied
    assert model.context() == (observed.purpose, observed.effort_ref, observed.creation_context)
    assert model.apply_snapshot(replace(
        observed, availability=availability, events=(), position=None, before=None,
    )).applied
    assert model.context() == (None, None, None)
    renewed = token(1, head="head-b")
    assert model.bind(renewed).applied
    current = replace(snapshot(renewed, (event(renewed, 1),)), purpose="Current assignment.")
    assert model.apply_snapshot(current).applied
    assert model.context() == (current.purpose, current.effort_ref, current.creation_context)


def test_unavailable_context_preserves_other_observed_source() -> None:
    source = token()
    other = token(key="other-source")
    model = TimelineWindow()
    model.bind(source)
    observed = snapshot(source, (event(source, 0),))
    assert model.apply_snapshot(observed).applied
    model.bind(other)
    retained = replace(
        snapshot(other, (event(other, 1),)),
        purpose="Other observed purpose.", effort_ref="other-effort",
        creation_context="Other observed creation.",
    )
    assert model.apply_snapshot(retained).applied
    assert model.apply_snapshot(replace(
        observed, availability=Availability.UNAVAILABLE, events=(), position=None, before=None,
    )).applied
    assert model.context() == (retained.purpose, retained.effort_ref, retained.creation_context)
    assert model.sources[other.identity.source_key].availability is Availability.OBSERVED


def test_generation_replacement_releases_only_its_lanes_and_reuses_holes() -> None:
    source = token()
    other = token(key="other-source")
    model = TimelineWindow()
    model.bind(source)
    old_rows = tuple(event(source, number, actor=f"old-{number}") for number in range(30))
    assert model.apply_snapshot(snapshot(source, old_rows)).applied
    model.bind(other)
    retained = event(other, 100, actor="retained")
    assert model.apply_snapshot(snapshot(other, (retained,))).applied
    retained_state = model.sources[other.identity.source_key]
    assert model.lane(retained) == 30
    renewed = token(1, generation="generation-b")
    assert model.bind(renewed).applied
    fresh = tuple(event(renewed, number, actor=f"fresh-{number}") for number in range(2))
    assert model.apply_snapshot(snapshot(renewed, fresh)).applied
    assert [model.lane(row) for row in fresh] == [0, 1]
    assert model.lane(retained) == 30 and model.lane_count == 31
    assert model.sources[other.identity.source_key] == retained_state
    assert model.event(retained.ref) == retained
    assert all(ref.generation != source.identity.generation
               for ref in model._proofs if ref.source_key == source.identity.source_key)
    assert len(set(model._lanes.values())) == len(model._lanes) == 3


def test_full_source_generation_replacement_starts_fresh_without_empty_lanes() -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    rows = tuple(event(source, number, actor=f"old-{number}") for number in range(32))
    assert model.apply_snapshot(snapshot(source, rows)).applied
    assert model.lane_count == MAX_LANES
    renewed = token(1, generation="generation-b")
    assert model.bind(renewed).applied
    fresh = event(renewed, 100, actor="fresh")
    assert model.apply_snapshot(snapshot(renewed, (fresh,))).applied
    assert model.lane(fresh) == 0 and model.lane_count == 1


def test_display_eviction_preserves_actor_lane_reservations() -> None:
    source = token()
    model = TimelineWindow(max_events=1)
    model.bind(source)
    first = event(source, 0, actor="first")
    assert model.apply_snapshot(snapshot(source, (first,))).applied
    second = event(source, 1, actor="second")
    assert model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), (second,),
    )).applied
    assert model.event(first.ref) is None
    assert model.lane(first) == 0 and model.lane(second) == 1 and model.lane_count == 2


def test_unknown_references_are_not_followed_and_source_scope_is_exact():
    source = token()
    model = TimelineWindow()
    model.bind(source)
    parent = event(source, 0)
    child = replace(event(source, 1), relations=(
        TimelineRelation(RelationKind.PARENT, Qualification.REPORTED, parent.ref),
    ))
    model.apply_snapshot(snapshot(source, (parent, child)))
    assert model.children(parent.ref) == (child,)
    other = replace(source, identity=replace(source.identity, project="other"))
    assert model.bind(other).code is ResultCode.STALE_TOKEN
    assert model.events == (parent, child)


def test_history_eviction_preserves_pinned_viewport_and_live_head():
    source = token()
    model = TimelineWindow(max_events=3)
    model.bind(source)
    original = tuple(event(source, number) for number in range(3, 6))
    model.apply_snapshot(snapshot(source, original))
    model.pin_viewport(original[0].ref)
    model.request_history(source, position(source, "older-0"))
    result = model.prepend_page(HistoryPage(
        source, position(source, "older-0"), None,
        tuple(event(source, number) for number in range(3)), Completeness.COMPLETE,
    ))
    assert result.applied and result.evicted == 3
    assert model.event(original[0].ref) == original[0]
    assert model.event(original[-1].ref) == original[-1]
    assert len(model.events) == 3


def test_more_than_5000_lifetime_events_keep_the_visible_window_bounded():
    source = token()
    model = TimelineWindow(max_events=64)
    model.bind(source)
    first = event(source, 0)
    model.apply_snapshot(snapshot(source, (first,)))
    current = position(source, "live-0")
    for start in range(1, 5501, 64):
        next_position = position(source, f"live-{start}")
        rows = tuple(event(source, number) for number in range(start, min(5501, start + 64)))
        assert model.apply_delta(LiveDelta(source, current, next_position, rows)).applied
        current = next_position
        assert len(model.events) <= 64 and model.encoded_size <= model.max_bytes
    assert len(model._proofs) == 5501
    assert not model.reconciliation_required
    assert model.events[-1].event_id == "event-5500"
    assert model.event(first.ref) is None
    result = model.apply_delta(LiveDelta(
        source, current, position(source, "conflicting-replay"), (replace(first, summary="Changed evicted event"),),
    ))
    assert result.code is ResultCode.CONTENT_CONFLICT


def test_integrity_limit_requires_explicit_baseline_reconciliation_not_epoch_eviction():
    source = token()
    model = TimelineWindow(max_events=2, max_proofs=3)
    model.bind(source)
    rows = tuple(event(source, number) for number in range(3))
    model.apply_snapshot(snapshot(source, rows))
    before = model.events
    result = model.apply_delta(LiveDelta(
        source, position(source, "live-0"), position(source, "live-1"), (event(source, 3),),
    ))
    assert result.code is ResultCode.LIMIT and not result.applied
    assert model.events == before and len(model._proofs) == 3
    assert source.identity.source_key in model.reconciliation_required
    assert model.sources[source.identity.source_key].availability is Availability.UNAVAILABLE
    renewed = token(1)
    assert not model.bind(renewed).applied
    assert len(model._proofs) == 3
    assert not model.request_history(source, position(source, "older-0")).applied
    # A deliberately new observer baseline is not a silent in-generation proof
    # eviction. The parent must first obtain this owner-qualified current snapshot.
    fresh = TimelineWindow(max_events=2)
    fresh.bind(renewed)
    assert fresh.apply_snapshot(snapshot(renewed, (event(renewed, 3),), cursor="current-owner-baseline")).applied
    assert fresh.events[-1].event_id == "event-3"
    assert model.events == before


@pytest.mark.parametrize("refresh,expected", [
    ((0, 1, 2), (1, 2)),
    ((1, 2, 3), (2, 3)),
    ((4, 5), (4, 5)),
    ((0, 1), (1, 2)),
])
def test_snapshot_refresh_preserves_provider_order_after_eviction(
    refresh: tuple[int, ...], expected: tuple[int, ...],
) -> None:
    source = token()
    model = TimelineWindow(max_events=2)
    model.bind(source)
    # Deliberately reversed timestamps and opaque IDs cannot order these rows.
    rows = tuple(replace(
        event(source, number), event_id=identifier,
        observed_at=event(source, 10 - number).observed_at,
    ) for number, identifier in enumerate(("z", "a", "m", "b", "x", "c")))
    initial = snapshot(source, rows[:3])
    assert model.apply_snapshot(initial).applied
    assert model.events == rows[1:3]
    refreshed = snapshot(
        source, tuple(rows[number] for number in refresh),
        cursor="live-0" if refresh == (0, 1, 2) else "opaque-refresh",
    )
    assert model.apply_snapshot(refreshed).applied
    assert model.events == tuple(rows[number] for number in expected)
    assert model.apply_snapshot(refreshed).applied
    assert model.events == tuple(rows[number] for number in expected)


def test_snapshot_refresh_preserves_loaded_backward_history_and_live_bottom() -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    rows = tuple(event(source, number) for number in range(9))
    assert model.apply_snapshot(snapshot(source, rows[5:8])).applied
    assert model.request_history(source, position(source, "older-0")).applied
    assert model.prepend_page(HistoryPage(
        source, position(source, "older-0"), position(source, "older-1"),
        rows[3:6], Completeness.PARTIAL,
    )).applied
    refreshed = snapshot(source, rows[5:9], cursor="live-1")
    assert model.apply_snapshot(refreshed).applied
    assert model.events == rows[3:9]
    assert model.sources[source.identity.source_key].before == position(source, "older-1")
    assert model.apply_snapshot(refreshed).applied and model.events == rows[3:9]


def test_conflicting_snapshot_order_is_visible_and_atomic() -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    rows = tuple(event(source, number) for number in range(3))
    assert model.apply_snapshot(snapshot(source, rows)).applied
    assert model.request_history(source, position(source, "older-0")).applied
    state = model.sources[source.identity.source_key]
    result = model.apply_snapshot(snapshot(source, (rows[2], rows[1]), cursor="live-1"))
    assert not result.applied and result.code is ResultCode.CONTENT_CONFLICT
    assert "order" in result.reason
    assert model.events == rows and model.sources[source.identity.source_key] == state


@pytest.mark.parametrize("mode", ["same-position", "after-delta", "after-history"])
def test_matching_history_request_survives_observed_snapshot_refresh(mode: str) -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    rows = tuple(event(source, number) for number in range(7))
    assert model.apply_snapshot(snapshot(source, rows[3:6])).applied
    before = position(source, "older-0")
    if mode == "after-history":
        assert model.request_history(source, before).applied
        before = position(source, "older-1")
        assert model.prepend_page(HistoryPage(
            source, position(source, "older-0"), before, (rows[2],), Completeness.PARTIAL,
        )).applied
    assert model.request_history(source, before).applied
    if mode == "after-delta":
        assert model.apply_delta(LiveDelta(
            source, position(source, "live-0"), position(source, "live-1"), (rows[6],),
        )).applied
        refreshed = snapshot(source, rows[4:7], cursor="live-1")
    else:
        refreshed = snapshot(source, rows[3:6])
    assert model.apply_snapshot(refreshed).applied
    assert model.sources[source.identity.source_key].requested_before == before
    page = HistoryPage(source, before, None, (rows[1],), Completeness.COMPLETE)
    assert model.prepend_page(page).applied
    assert model.events[0] == rows[1]
    assert model.events[-1] == (rows[6] if mode == "after-delta" else rows[5])
    assert model.sources[source.identity.source_key].requested_before is None


@pytest.mark.parametrize("change", ["boundary", "availability", "head", "generation"])
def test_changed_history_scope_invalidates_pending_page(change: str) -> None:
    source = token()
    model = TimelineWindow()
    model.bind(source)
    observed = snapshot(source, (event(source, 3),))
    assert model.apply_snapshot(observed).applied
    before = position(source, "older-0")
    assert model.request_history(source, before).applied
    if change == "boundary":
        assert model.apply_snapshot(replace(
            observed, before=position(source, "different-boundary"),
        )).applied
    elif change == "availability":
        assert model.apply_snapshot(replace(
            observed, availability=Availability.UNAVAILABLE, events=(), position=None, before=None,
        )).applied
    else:
        renewed = token(1, head="head-b") if change == "head" else token(1, generation="generation-b")
        assert model.bind(renewed).applied
    assert model.sources[source.identity.source_key].requested_before is None
    result = model.prepend_page(HistoryPage(
        source, before, None, (event(source, 2),), Completeness.COMPLETE,
    ))
    assert not result.applied and result.code is ResultCode.STALE_TOKEN
