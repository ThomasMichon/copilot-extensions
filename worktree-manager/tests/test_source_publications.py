"""Deterministic F3 model tests: real F1 validators, no loader or live endpoint."""

from copy import deepcopy

import pytest

from worktree_manager.production_picker.picker_tui.source_publications import (
    _Receipt,
    _SourcePublications,
)
from worktree_manager.production_picker.picker_tui import source_publications as publications

G = "a" * 32
H = "b" * 32
SOURCE = "selected-adapter"
PROJECT = "sample"


def row(wid="tree", revision=10, title="locked", *, observed=None, closure=False):
    value = {
        "id": wid, "title": title, "semantic_revision": revision,
        "fact_provenance": {"session": {"quality": "current"}},
    }
    observation = {
        "id": wid, "facts": {
            "session": {"quality": "current", "observed_at": float(revision),
                        "observation_revision": revision if observed is None else observed},
        },
    }
    if closure:
        value["closure"] = {"status": "blocked"}
        observation["derivations"] = {
            "closure": {"computed_at": "2026-01-01T00:00:00Z", "observation_revision": revision},
        }
    return value, observation


def envelope(revision=10, items=None, *, generation=G, project=PROJECT, count=None):
    items = [row()] if items is None else items
    count = len(items) if count is None else count
    return {
        "ok": True, "contract_version": 2, "project": project,
        "projection": {
            "schema_version": 1, "generation": generation, "revision": revision,
            "roster_count": count, "selected_count": len(items), "observed_at": revision,
            "quality": "current",
        },
        "worktrees": [deepcopy(item[0]) for item in items],
        "observations": [deepcopy(item[1]) for item in items],
    }


def seeded(items=None):
    store = _SourcePublications()
    epoch = store.begin_epoch(SOURCE)
    assert snapshot(store, epoch, envelope(items=items))
    return store, epoch


def snapshot(store, epoch, value, source=SOURCE, project=PROJECT, fresh=True):
    return store.admit_snapshot(source, epoch, value, project=project, fresh=fresh)


def point(store, epoch, value, wid="tree", source=SOURCE, project=PROJECT):
    return store.admit_point(source, epoch, value, project=project, worktree_id=wid)


def state(store):
    return store.status(SOURCE, PROJECT)


def record(store, wid="tree", source=SOURCE, project=PROJECT):
    return store.records()[source, project, wid]


def update(revision=11, items=(), *, deleted=(), order=("tree",), base=10,
           generation=G, project=PROJECT):
    value = envelope(revision, list(items), generation=generation, project=project)
    head = value["projection"]
    head["selected_count"] = head["roster_count"] = len(order)
    return [
        {"type": "update", "contract_version": 2, "project": project,
         "projection": head, "cursor": revision, "base_cursor": base,
         "selected_count": len(order)},
        *({"type": "worktree", "phase": "current", "projection": head, "wt": item[0]}
          for item in items),
        *({"type": "delete", "projection": head, "id": wid} for wid in deleted),
        *({"type": "observation", "projection": head, "observation": item[1]} for item in items),
        *([{"type": "order", "projection": head, "ids": list(order)}] if order else []),
        {"type": "commit", "projection": head, "cursor": revision},
    ]


def feed(store, epoch, frames):
    return store.admit_feed_commit(SOURCE, epoch, PROJECT, frames)


def availability_control(revision=10, value=None, *, generation=G, empty=False):
    return {
        "type": "availability",
        "projection": envelope(revision, [] if empty else None,
                               generation=generation)["projection"],
        "availability": deepcopy(value),
    }


def test_point_floor_survives_twenty_feed_ticks_without_fast_forward():
    store, epoch = seeded([row(), row("other")])
    assert point(store, epoch, envelope(11, [row(revision=11, title="unlocked")], count=2))
    for _ in range(20):
        assert record(store)["title"] == "unlocked"
        assert state(store)["feed_cursor"] == 10
        assert not feed(store, epoch, [{"type": "heartbeat", "generation": G, "cursor": 10}])
    assert point(store, epoch, envelope(11, [row("other", 11, "independent")]),
                 wid="other")
    assert record(store, "other")["title"] == "independent"
    assert state(store)["feed_cursor"] == 10
    assert feed(store, epoch, update(12, [
        row(revision=12, title="new owner"), row("other", 11, "independent"),
    ], order=("other", "tree")))
    assert record(store)["title"] == "new owner"
    assert state(store)["feed_cursor"] == 12
    assert point(store, epoch, envelope(11, [row(revision=11, title="unlocked")]))
    assert record(store)["title"] == "new owner"


def test_valid_delta_still_uses_feed_base_ten_after_point_eleven():
    store, epoch = seeded()
    assert point(store, epoch, envelope(11, [row(revision=11, title="unlocked")]))
    assert feed(store, epoch, update(11, [row(revision=11, title="unlocked")]))
    assert state(store)["feed_cursor"] == 11


def test_source_and_project_keys_are_supplied_by_adapter_not_row_labels():
    store, epoch = seeded()
    remote_epoch = store.begin_epoch("remote")
    remote = envelope(items=[row(title="remote")])
    remote["worktrees"][0]["machine"] = "misleading-label"
    assert snapshot(store, remote_epoch, remote, source="remote")
    assert snapshot(store, epoch, envelope(project="another", items=[row(title="project")]),
                    project="another")
    assert point(store, remote_epoch, envelope(11, [row(revision=11, title="remote fresh")]),
                 source="remote")
    assert record(store)["title"] == "locked"
    assert record(store, source="remote")["title"] == "remote fresh"
    assert record(store, project="another")["title"] == "project"
    assert len(store.records("remote")) == 1


@pytest.mark.parametrize("mismatch", ["project", "id"])
def test_point_rejects_mismatched_requested_identity_atomically(mismatch):
    store, epoch = seeded()
    value = envelope(11, [row(revision=11)])
    if mismatch == "project":
        value["project"] = "another"
    else:
        value["worktrees"][0]["id"] = value["observations"][0]["id"] = "another"
    before = state(store)
    with pytest.raises(ValueError, match="selected"):
        point(store, epoch, value)
    assert state(store) == before


def test_new_generation_point_marks_stale_without_electing_owner():
    store, epoch = seeded()
    assert not point(store, epoch, envelope(1, [row(revision=1)], generation=H))
    assert state(store)["generation"] == G
    assert state(store)["stale"]
    assert record(store)["semantic_revision"] == 10
    assert not snapshot(store, epoch, envelope(1, [row(revision=1)], generation=H), fresh=False)
    assert snapshot(store, epoch, envelope(1, [row(revision=1)], generation=H))
    assert state(store)["generation"] == H
    assert state(store)["floors"] == {"tree": 1}
    assert not state(store)["stale"]


def test_generation_reset_replaces_rows_floors_order_tombstones_and_invalidates_receipts():
    store, epoch = seeded()
    intent = _Receipt("observe", PROJECT, "tree", G, "published", 11, ("session",))
    assert store.note_receipt(SOURCE, epoch, intent)
    assert feed(store, epoch, update(deleted=["tree"], order=()))
    assert state(store)["tombstones"] == {"tree": 11}
    assert snapshot(store, epoch, envelope(1, [row("new", 1)], generation=H))
    assert state(store)["tombstones"] == {}
    assert state(store)["floors"] == {"new": 1}
    assert list(store.records()) == [(SOURCE, PROJECT, "new")]
    assert state(store)["receipts"]["tree", "observe"] == "invalidated"
    assert not point(store, epoch, envelope(12, [row(revision=12)]))
    assert (SOURCE, PROJECT, "tree") not in store.records()


def test_epoch_cancellation_and_same_generation_reconnect_keep_newer_point():
    store, epoch = seeded()
    assert point(store, epoch, envelope(11, [row(revision=11, title="unlocked")]))
    new_epoch = store.begin_epoch(SOURCE)
    before = state(store)
    assert not point(store, epoch, envelope(12, [row(revision=12)]))
    assert not snapshot(store, epoch, envelope(1, [row(revision=1)], generation=H))
    assert not feed(store, epoch, update(12, [row(revision=12)]))
    assert not store.note_receipt(SOURCE, epoch, _Receipt("late", PROJECT, "tree", G, "pending"))
    assert state(store) == before
    assert not feed(store, new_epoch, update(12, [row(revision=12)]))
    assert snapshot(store, new_epoch, envelope())
    assert record(store)["title"] == "unlocked"
    assert state(store)["feed_cursor"] == 10
    assert not state(store)["stale"]


def test_snapshot_cannot_rewind_complete_feed_cursor():
    store, epoch = seeded()
    assert feed(store, epoch, update(11, [row(revision=11)]))
    assert not snapshot(store, store.begin_epoch(SOURCE), envelope())
    assert state(store)["feed_cursor"] == 11


@pytest.mark.parametrize("kind", ["snapshot", "feed", "point"])
def test_equal_revision_conflict_has_no_partial_mutation(kind):
    store, epoch = seeded([row(), row("other")])
    assert point(store, epoch, envelope(11, [row(revision=11, title="point")]))
    before_records, before_status = store.records(), state(store)
    conflicting = [row("other", 11, "would mutate"), row(revision=11, title="conflict")]
    with pytest.raises(ValueError, match="conflict"):
        if kind == "snapshot":
            snapshot(store, epoch, envelope(11, conflicting))
        elif kind == "feed":
            feed(store, epoch, update(11, conflicting, order=("other", "tree")))
        else:
            point(store, epoch, envelope(11, [conflicting[1]]))
    assert store.records() == before_records
    assert state(store) == before_status


def test_duplicate_equal_revision_is_idempotent_but_observation_conflict_is_not():
    store, epoch = seeded()
    assert point(store, epoch, envelope())
    value = envelope()
    value["observations"][0]["facts"]["session"]["observed_at"] = 999
    with pytest.raises(ValueError, match="conflict"):
        point(store, epoch, value)
    assert record(store)["_observations"]["session"]["observed_at"] == 10


def test_point_missing_is_not_deletion_and_older_complete_absence_cannot_delete_point():
    store, epoch = seeded()
    assert point(store, epoch, envelope(12, [row(revision=12)]))
    assert not point(store, epoch, envelope(13, []))
    assert snapshot(store, epoch, envelope(11, []))
    assert record(store)["semantic_revision"] == 12
    assert state(store)["feed_cursor"] == 11
    assert snapshot(store, epoch, envelope(13, []))
    assert not store.records()
    assert state(store)["tombstones"] == {"tree": 13}
    assert point(store, epoch, envelope(12, [row(revision=12)]))
    assert not store.records()


def test_delete_only_commits_at_complete_barrier_and_rejects_equal_resurrection():
    store, epoch = seeded()
    frames = update(deleted=["tree"], order=())
    before = state(store)
    with pytest.raises(ValueError, match="incomplete"):
        feed(store, epoch, frames[:-1])
    assert state(store) == before
    assert feed(store, epoch, frames)
    with pytest.raises(ValueError, match="conflict"):
        point(store, epoch, envelope(11, [row(revision=11)]))
    with pytest.raises(ValueError, match="tombstone"):
        point(store, epoch, envelope(12, [row(revision=10)]))
    assert point(store, epoch, envelope(12, [row(revision=12, title="recreated")]))
    assert list(store.records()) == [(SOURCE, PROJECT, "tree")]


def test_deletion_older_than_point_preserves_row_then_newer_deletion_wins():
    store, epoch = seeded()
    assert point(store, epoch, envelope(12, [row(revision=12)]))
    assert feed(store, epoch, update(11, deleted=["tree"], order=()))
    assert record(store)["semantic_revision"] == 12
    assert snapshot(store, epoch, envelope(13, []))
    assert not store.records()


def test_exact_order_front_insertion_reorder_empty_and_point_only_tail():
    store, epoch = seeded([row("a"), row("b")])
    assert point(store, epoch, envelope(12, [row("point", 12)]), wid="point")
    assert feed(store, epoch, update(11, [row("front", 11)], order=("front", "b", "a")))
    assert [key[2] for key in store.records()] == ["front", "b", "a", "point"]
    assert snapshot(store, epoch, envelope(13, []))
    assert not store.records()


def test_real_f1_closure_derivations_and_partial_fact_observation_are_preserved():
    store, epoch = seeded([row(closure=True)])
    value = envelope(11, [row(closure=True)])
    value["observations"][0]["derivations"]["closure"] = {
        "computed_at": "2026-01-01T00:01:00Z", "observation_revision": 11,
    }
    assert point(store, epoch, value)
    assert record(store)["_observation_derivations"]["closure"]["observation_revision"] == 11
    assert record(store)["_observations"]["session"]["observation_revision"] == 10
    assert record(store)["semantic_revision"] == 10


def test_observation_only_feed_preserves_semantics_and_covers_only_sampled_facts():
    item = row(closure=True)
    item[0]["fact_provenance"]["record"] = {"quality": "current"}
    item[1]["facts"]["record"] = {
        "quality": "current", "observed_at": 10, "observation_revision": 10,
    }
    store, epoch = seeded([item])
    frames = update()
    observation = deepcopy(item[1])
    observation["facts"]["session"].update(observed_at=11, observation_revision=11)
    frames.insert(1, {"type": "observation", "projection": frames[0]["projection"],
                      "observation": observation})
    assert feed(store, epoch, frames)
    assert record(store)["semantic_revision"] == 10
    assert record(store)["_observations"]["session"]["observation_revision"] == 11
    assert record(store)["_observations"]["record"]["observation_revision"] == 10
    assert record(store)["_observation_derivations"]["closure"]["observation_revision"] == 10
    assert store.note_receipt(
        SOURCE, epoch, _Receipt("partial", PROJECT, "tree", G, "published", 11,
                               ("record", "session")),
    )
    assert state(store)["receipts"]["tree", "partial"] == "pending"


def test_unavailable_fact_has_no_success_timestamp_and_heartbeat_does_not_renew_it():
    store, epoch = seeded()
    value = envelope(11, [row(revision=11)])
    value["worktrees"][0]["fact_provenance"]["session"]["quality"] = "unavailable"
    value["observations"][0]["facts"]["session"].update(quality="unavailable", observed_at=None)
    assert point(store, epoch, value)
    assert not feed(store, epoch, [{"type": "heartbeat", "generation": G, "cursor": 10}])
    assert record(store)["_observations"]["session"]["observed_at"] is None


@pytest.mark.parametrize("field", ["semantic", "observation", "closure"])
def test_higher_publication_cannot_hide_fact_version_regression(field):
    store, epoch = seeded([row(closure=True)])
    value = envelope(12, [row(revision=11, closure=True)])
    if field == "semantic":
        value["worktrees"][0]["semantic_revision"] = 9
    elif field == "observation":
        value["observations"][0]["facts"]["session"]["observation_revision"] = 9
    else:
        value["observations"][0]["derivations"]["closure"]["observation_revision"] = 9
    before = store.records()
    with pytest.raises(ValueError, match="regressed"):
        point(store, epoch, value)
    assert store.records() == before


@pytest.mark.parametrize("status", ["pending", "degraded"])
def test_pending_degraded_receipts_never_become_proof(status):
    store, epoch = seeded()
    assert store.note_receipt(SOURCE, epoch, _Receipt("request", PROJECT, "tree", G, status))
    assert point(store, epoch, envelope(12, [row(revision=12)]))
    assert state(store)["receipts"]["tree", "request"] == status


def test_receipt_needs_matching_row_generation_and_covered_fact_not_higher_cursor():
    store, epoch = seeded([row(), row("other")])
    receipt = _Receipt("request", PROJECT, "tree", G, "published", 11, ("session",))
    assert store.note_receipt(SOURCE, epoch, receipt)
    assert feed(store, epoch, update(12, [row("other", 12)], order=("tree", "other")))
    assert state(store)["feed_cursor"] == 12
    assert state(store)["receipts"]["tree", "request"] == "pending"
    assert not feed(store, epoch, [{"type": "heartbeat", "generation": G, "cursor": 12}])
    value = envelope(13, [row()])
    value["observations"][0]["facts"]["session"].update(
        observation_revision=13, observed_at=13,
    )
    assert point(store, epoch, value)
    assert state(store)["receipts"]["tree", "request"] == "confirmed"


def test_not_found_receipt_requires_revision_qualified_tombstone():
    store, epoch = seeded()
    receipt = _Receipt("absence", PROJECT, "tree", G, "not-found", 11)
    assert store.note_receipt(SOURCE, epoch, receipt)
    assert not point(store, epoch, envelope(11, []))
    assert state(store)["receipts"]["tree", "absence"] == "pending"
    assert feed(store, epoch, update(deleted=["tree"], order=()))
    assert state(store)["receipts"]["tree", "absence"] == "confirmed"


def test_receipt_is_private_optional_bounded_and_does_not_accept_f2_wire():
    store, epoch = seeded()
    with pytest.raises(ValueError, match="unsupported receipt wire"):
        store.note_receipt(SOURCE, epoch, {"receipt_version": 1})
    with pytest.raises(ValueError, match="not publication proof"):
        store.note_receipt(SOURCE, epoch, _Receipt("bad", PROJECT, "tree", G, "pending", 11))
    for i in range(128):
        assert store.note_receipt(SOURCE, epoch, _Receipt(str(i), PROJECT, "tree", G, "pending"))
    with pytest.raises(ValueError, match="bound"):
        store.note_receipt(SOURCE, epoch, _Receipt("overflow", PROJECT, "tree", G, "pending"))
    assert store.note_receipt(
        SOURCE, epoch, _Receipt("0", PROJECT, "tree", G, "published", 11, ("session",)),
    )
    assert state(store)["receipts"]["tree", "0"] == "pending"
    with pytest.raises(ValueError, match="conflicting receipt"):
        store.note_receipt(
            SOURCE, epoch, _Receipt("0", PROJECT, "tree", G, "published", 12, ("session",)),
        )


def test_inputs_and_returned_values_have_no_mutable_aliases():
    store, epoch = seeded()
    value = envelope(11, [row(revision=11)])
    saved = deepcopy(value)
    assert point(store, epoch, value)
    assert value == saved
    value["worktrees"][0]["title"] = "caller mutation"
    value["observations"][0]["facts"]["session"]["observed_at"] = 999
    result = store.records()
    with pytest.raises(TypeError):
        result[SOURCE, PROJECT, "tree"]["title"] = "result mutation"
    with pytest.raises(TypeError):
        result[SOURCE, PROJECT, "tree"]["_observations"]["session"]["observed_at"] = 888
    with pytest.raises(TypeError):
        state(store)["floors"]["tree"] = 999
    frames = update(12, [row(revision=12)])
    original = deepcopy(frames)
    assert feed(store, epoch, frames)
    assert frames == original
    frames[1]["wt"]["title"] = "late mutation"
    assert record(store)["title"] == "locked"
    assert result[SOURCE, PROJECT, "tree"]["semantic_revision"] == 11


@pytest.mark.parametrize("bad", [
    "version", "metadata", "missing-observation", "unknown-field", "quality", "incomplete",
])
def test_unsupported_or_incomplete_snapshot_fails_atomically(bad):
    store, epoch = seeded()
    value = envelope(1, [row(revision=1)], generation=H)
    if bad == "version":
        value["contract_version"] = 3
    elif bad == "metadata":
        value["worktrees"][0]["_projection"] = {}
    elif bad == "missing-observation":
        del value["observations"]
    elif bad == "unknown-field":
        value["unsupported"] = True
    elif bad == "quality":
        value["projection"]["quality"] = "unknown"
    else:
        value["projection"]["selected_count"] = 2
        value["projection"]["roster_count"] = 2
    before = state(store)
    with pytest.raises((ValueError, TypeError)):
        snapshot(store, epoch, value)
    assert state(store) == before


def test_filtered_snapshot_cannot_reset_generation_or_assert_absence():
    store, epoch = seeded()
    before = state(store)
    with pytest.raises(ValueError, match="full source/project"):
        snapshot(store, epoch, envelope(1, [row(revision=1)], generation=H, count=2))
    assert state(store) == before


def test_bounds_and_legacy_negotiation_refuse_without_synthesized_success():
    store, epoch = seeded()
    for value in (
        {**envelope(), "contract_version": 1},
        {**envelope(), "worktrees": [{}] * 10_001},
        envelope(11, [row(title="x" * 4097)]),
    ):
        with pytest.raises(ValueError):
            point(store, epoch, value)
    assert state(store)["feed_cursor"] == 10
    assert not point(_SourcePublications(), epoch, envelope())


def test_unsupported_feed_epoch_owner_base_order_and_duplicate_barriers_are_atomic():
    for mutation in ("unknown", "generation", "base", "order", "extra-commit",
                     "unknown-field", "phase"):
        store, epoch = seeded()
        frames = update(11, [row(revision=11)])
        if mutation == "unknown":
            frames.insert(1, {"type": "future-frame"})
        elif mutation == "generation":
            frames = update(11, [row(revision=11)], generation=H)
        elif mutation == "base":
            frames[0]["base_cursor"] = 11
        elif mutation == "order":
            frames[-2]["ids"] = ["another"]
        elif mutation == "extra-commit":
            frames.append(frames[-1])
        elif mutation == "phase":
            frames[1]["phase"] = "unsupported"
        else:
            frames[0]["future"] = True
        before = state(store)
        with pytest.raises(ValueError):
            feed(store, epoch, frames)
        assert state(store) == before


@pytest.mark.parametrize("known", [False, True])
def test_complete_absence_blocks_delayed_point_for_known_and_unseen_identity(known):
    store, epoch = seeded(items=[row()] if known else [])
    if known:
        assert feed(store, epoch, update(deleted=["tree"], order=()))
    assert snapshot(store, epoch, envelope(13, []))
    if known:
        assert state(store)["tombstones"] == {"tree": 13}
    assert point(store, epoch, envelope(12, [row(revision=12)]))
    assert not store.records()
    with pytest.raises(ValueError, match="conflict"):
        point(store, epoch, envelope(13, [row(revision=13)]))
    with pytest.raises(ValueError, match="tombstone"):
        point(store, epoch, envelope(14, [row(revision=12)]))
    assert point(store, epoch, envelope(14, [row(revision=14)]))


@pytest.mark.parametrize("change", ["order", "insert", "remove"])
def test_equal_cursor_snapshot_compares_feed_roster_not_point_overlay(change):
    store, epoch = seeded([row("a"), row("b")])
    assert point(store, epoch, envelope(11, [row("a", 11)]), wid="a")
    new_epoch = store.begin_epoch(SOURCE)
    values = {"order": [row("b"), row("a")], "insert": [row("a"), row("b"), row("c")],
              "remove": [row("a")]}
    before_records, before_status = store.records(), state(store)
    with pytest.raises(ValueError, match="roster/order"):
        snapshot(store, new_epoch, envelope(items=values[change]))
    assert store.records() == before_records and state(store) == before_status
    assert snapshot(store, new_epoch, envelope(items=[row("a"), row("b")]))
    assert record(store, "a")["semantic_revision"] == 11


def test_snapshot_preserves_current_quality_stale_availability_at_equal_revision():
    store, epoch = seeded()
    value = envelope()
    value["availability"] = {"status": "stale", "reason": "owner publication failed"}
    assert snapshot(store, epoch, value)
    assert state(store)["stale"]
    assert dict(record(store)["_availability"]) == value["availability"]
    assert snapshot(store, epoch, envelope())
    assert not state(store)["stale"] and record(store)["_availability"] is None


@pytest.mark.parametrize("kind", ["snapshot", "feed"])
def test_replace_validates_rows_against_previous_absence_fences(kind):
    store, epoch = seeded(items=[])
    assert snapshot(store, epoch, envelope(13, []))
    before_records, before_status = store.records(), state(store)
    with pytest.raises(ValueError, match="tombstone"):
        if kind == "snapshot":
            snapshot(store, epoch, envelope(14, [row(revision=12)]))
        else:
            feed(store, epoch, update(14, [row(revision=12)], base=13))
    assert store.records() == before_records and state(store) == before_status
    assert snapshot(store, epoch, envelope(14, [row(revision=14)]))


def test_partition_availability_follows_feed_and_point_revision_not_delayed_evidence():
    store, epoch = seeded()
    warning = {"status": "stale", "reason": "publication failed"}
    frames = update(11, [row(revision=11)])
    frames[0]["availability"] = warning
    assert feed(store, epoch, frames) and state(store)["stale"]
    assert feed(store, epoch, update(12, [row(revision=12)], base=11))
    assert not state(store)["stale"]
    delayed = envelope(11, [row(revision=11)])
    delayed["availability"] = warning
    assert point(store, epoch, delayed) and not state(store)["stale"]
    current = envelope(13, [row(revision=13)])
    current["availability"] = warning
    assert point(store, epoch, current) and state(store)["stale"]
    assert point(store, epoch, envelope(13, [row(revision=13)])) and state(store)["stale"]
    assert point(store, epoch, envelope(12, [row(revision=12)])) and state(store)["stale"]
    assert point(store, epoch, envelope(14, [row(revision=14)]))
    assert not state(store)["stale"]


def test_partition_availability_is_separate_from_reload_and_generation_reset_staleness():
    store, epoch = seeded()
    warning = envelope(11, [row(revision=11)])
    warning["availability"] = {"status": "stale", "reason": "publication failed"}
    assert point(store, epoch, warning) and state(store)["stale"]
    epoch = store.begin_epoch(SOURCE)
    assert snapshot(store, epoch, envelope())
    assert state(store)["stale"]
    assert feed(store, epoch, update(12, [row(revision=12)]))
    assert not state(store)["stale"]
    assert not point(store, epoch, envelope(1, [row(revision=1)], generation=H))
    assert point(store, epoch, envelope(13, [row(revision=13)]))
    assert state(store)["stale"]
    assert snapshot(store, epoch, envelope(13, [row(revision=13)]))
    assert not state(store)["stale"]


def test_twenty_heartbeats_copy_no_partition_or_rows_and_preserve_all_state(monkeypatch):
    store, epoch = seeded([row(str(i)) for i in range(1000)])
    assert point(store, epoch, envelope(11, [row("0", 11)]), wid="0")
    assert store.note_receipt(
        SOURCE, epoch, _Receipt("waiting", PROJECT, "0", G, "published", 12, ("session",)),
    )
    before_records, before_status = store.records(), state(store)
    partition = store._partitions[SOURCE, PROJECT]
    rows, receiver_rows = partition.rows, partition.feed.rows
    copied = []
    original = publications.deepcopy
    with monkeypatch.context() as patch:
        def count_copy(value):
            copied.append(type(value).__name__)
            return original(value)
        patch.setattr(publications, "deepcopy", count_copy)
        for _ in range(20):
            assert not feed(store, epoch, [{"type": "heartbeat", "generation": G, "cursor": 10}])
    assert copied == []
    assert store._partitions[SOURCE, PROJECT] is partition
    assert partition.rows is rows and partition.feed.rows is receiver_rows
    assert store.records() == before_records and state(store) == before_status


def test_heartbeat_noop_still_checks_shape_epoch_generation_cursor_and_barrier():
    store, epoch = seeded()
    valid = {"type": "heartbeat", "generation": G, "cursor": 10}
    before = state(store)
    for bad in ({**valid, "generation": H}, {**valid, "cursor": 11},
                {**valid, "unsupported": True}, {**valid, "cursor": None}):
        with pytest.raises(ValueError):
            feed(store, epoch, [bad])
        assert state(store) == before
    with pytest.raises(ValueError):
        feed(store, epoch, [valid, valid])
    store._partitions[SOURCE, PROJECT].feed._begin = {"type": "update"}
    with pytest.raises(ValueError, match="barrier"):
        feed(store, epoch, [valid])
    store._partitions[SOURCE, PROJECT].feed._begin = None
    new_epoch = store.begin_epoch(SOURCE)
    assert not feed(store, epoch, [valid])
    assert not feed(store, new_epoch, [valid])


def test_availability_control_is_metadata_only_and_clears_at_unchanged_cursor():
    store, epoch = seeded()
    assert store.note_receipt(
        SOURCE, epoch, _Receipt("waiting", PROJECT, "tree", G, "published", 11, ("session",)),
    )
    before_records, before_status = store.records(), state(store)
    warning = {"status": "stale", "reason": "owner publication failed"}
    assert feed(store, epoch, [availability_control(value=warning)])
    assert dict(record(store)["_availability"]) == warning
    assert dict(state(store)) == {**before_status, "stale": True}
    assert publications._content(record(store)) == publications._content(
        before_records[SOURCE, PROJECT, "tree"],
    )
    assert feed(store, epoch, [availability_control()])
    assert store.records() == before_records and state(store) == before_status


@pytest.mark.parametrize("point_stale", [False, True])
def test_availability_control_cannot_override_newer_point_or_snapshot_floor(point_stale):
    store, epoch = seeded()
    warning = {"status": "stale", "reason": "owner publication failed"}
    value = envelope(11, [row(revision=11, title="point")])
    if point_stale:
        value["availability"] = warning
    assert point(store, epoch, value)
    assert snapshot(store, epoch, envelope())
    before_records, before_status = store.records(), state(store)
    assert feed(store, epoch, [availability_control(value=None if point_stale else warning)])
    assert store.records() == before_records and state(store) == before_status
    assert snapshot(store, epoch, envelope(12, [row(revision=12)]))
    with pytest.raises(ValueError, match="publication"):
        feed(store, epoch, [availability_control(value=warning)])


def test_availability_control_rejects_malformed_projection_scope_and_mixed_frames_atomically():
    store, epoch = seeded()
    before_records, before_status = store.records(), state(store)
    for bad in (
        availability_control(generation=H), availability_control(11),
        {**availability_control(), "projection": None},
        availability_control(value={"status": "unknown", "reason": "bad"}),
        availability_control(value={"status": "stale", "reason": ""}),
        {**availability_control(), "project": "another"},
        {**availability_control(), "unsupported": True},
    ):
        with pytest.raises(ValueError):
            feed(store, epoch, [bad])
        assert store.records() == before_records and state(store) == before_status
    with pytest.raises(ValueError):
        feed(store, epoch, [availability_control(), availability_control()])
    with pytest.raises(ValueError):
        feed(store, epoch, [availability_control(), *update(11, [row(revision=11)])])
    assert store.records() == before_records and state(store) == before_status
    assert not store.admit_feed_commit(SOURCE, epoch, "another", [availability_control()])


def test_availability_control_preserves_reset_fence_and_checks_receiver_barrier_epoch_project():
    store, epoch = seeded()
    assert not point(store, epoch, envelope(1, [row(revision=1)], generation=H))
    before = state(store)
    assert feed(store, epoch, [availability_control()])
    assert state(store) == before and state(store)["stale"]
    receiver = store._partitions[SOURCE, PROJECT].feed
    receiver._begin = {"type": "update"}
    with pytest.raises(ValueError, match="barrier"):
        feed(store, epoch, [availability_control()])
    receiver._begin = None
    receiver._project = "another"
    with pytest.raises(ValueError, match="project"):
        feed(store, epoch, [availability_control()])
    receiver._project = PROJECT
    new_epoch = store.begin_epoch(SOURCE)
    assert not feed(store, epoch, [availability_control()])
    assert not feed(store, new_epoch, [availability_control()])


def test_availability_control_preserves_empty_roster_tombstones_and_absence_receipts():
    store, epoch = seeded()
    assert feed(store, epoch, update(deleted=["tree"], order=()))
    assert store.note_receipt(SOURCE, epoch, _Receipt("absence", PROJECT, "tree", G,
                                                    "not-found", 12))
    before = state(store)
    warning = {"status": "stale", "reason": "owner publication failed"}
    assert feed(store, epoch, [availability_control(11, warning, empty=True)])
    assert not store.records() and dict(state(store)) == {**before, "stale": True}
    assert feed(store, epoch, [availability_control(11, empty=True)])
    assert state(store) == before


@pytest.mark.parametrize("size", [100, 1000])
def test_full_roster_order_work_has_no_per_row_linear_membership(size, monkeypatch):
    counts = {"membership_calls": 0, "equality_checks": 0}

    class CountedOrder(list):
        def __contains__(self, value):
            counts["membership_calls"] += 1
            for member in self:
                counts["equality_checks"] += 1
                if member == value:
                    return True
            return False

    partition_type = publications._Partition

    def partition(*args, **kwargs):
        value = partition_type(*args, **kwargs)
        value.order = CountedOrder(value.order)
        return value

    monkeypatch.setattr(publications, "_Partition", partition)
    items = [row(str(i)) for i in range(size)]
    store, epoch = seeded(items)
    fresh = dict(counts)
    store._partitions[SOURCE, PROJECT].order = CountedOrder(
        store._partitions[SOURCE, PROJECT].order,
    )
    counts.update(membership_calls=0, equality_checks=0)
    assert snapshot(store, epoch, envelope(11, items))
    refreshed = dict(counts)
    assert [key[2] for key in store.records()] == [str(i) for i in range(size)]
    print(f"order-work rows={size} fresh={fresh} refresh={refreshed}")
    assert fresh == refreshed == {"membership_calls": 0, "equality_checks": 0}
