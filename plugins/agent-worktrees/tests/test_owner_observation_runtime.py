"""Passive scoped subscriptions over a real private owner index."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace

import pytest
from test_owner_observation_history import event, record

from agent_worktrees import owner_observation_contract as c
from agent_worktrees.owner_observation_history import OwnerObservationHistory
from agent_worktrees.owner_observation_runtime import OwnerObservationRuntime


def request(head="head", after=None):
    return {
        "contract_version": 1,
        "project": "project",
        "worktree_id": "full-worktree",
        "expected_head_session_id": head,
        "after_cursor": after,
    }


@pytest.fixture
def runtime(tmp_path):
    clock = [10.0]
    wakes = []
    store = OwnerObservationHistory(
        tmp_path / "owner.sqlite3",
        resident_generation="a" * 32,
        writer_id="b" * 32,
        authority_check=lambda: True,
    )
    owner = OwnerObservationRuntime(
        store,
        now=lambda: clock[0],
        wake=lambda: wakes.append(1),
        heartbeat=0.01,
    )
    owner.activate()
    yield owner, clock, wakes
    owner.shutdown()


def test_initial_snapshot_then_indexed_delta_and_heartbeat_do_not_poll_records(
    runtime, monkeypatch
):
    owner, _clock, _wakes = runtime
    baseline = owner.initialize(record())
    stream = owner.stream(request(), lambda: False)
    try:
        assert next(stream) == baseline
        current = owner.publish(
            record(1),
            [event(record(1))],
            batch_id="batch:1",
            expected_cursor=baseline["cursor"],
            post_save=True,
        )
        delta = next(stream)
        assert delta["type"] == "delta" and delta["cursor"] == current["cursor"]
        assert delta["base_cursor"] == baseline["cursor"]
        assert delta["facts"] == record(1).facts
        assert delta["entries"][0]["kind"] == "claim-added"
        monkeypatch.setattr(
            owner.history, "delta", lambda *a: pytest.fail("heartbeat reread history")
        )
        heartbeat = next(stream)
        assert heartbeat["type"] == "heartbeat" and heartbeat["cursor"] == delta["cursor"]
        assert "entries" not in heartbeat
        owner.close_admission("draining")
        terminal = next(stream)
        assert terminal["type"] == "going-away" and terminal["reason"] == "draining"
        with pytest.raises(StopIteration):
            next(stream)
    finally:
        stream.close()
    assert owner.active_count() == 0


def test_sampled_count_difference_is_observed_change_not_claim_added(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    current = owner.reconcile(record(2), batch_id="sample:1")
    page = owner.history_page(
        {
            "contract_version": 1,
            "project": "project",
            "worktree_id": "full-worktree",
            "expected_head_session_id": "head",
            "source_generation": initial["source_generation"],
            "before_cursor": None,
            "limit": 64,
        }
    )
    assert page["cursor"] == current["cursor"]
    assert [item["kind"] for item in page["entries"]] == ["observed-change"]
    assert page["entries"][0]["payload"]["before"]["claims"]["held_count"] == 0
    assert page["entries"][0]["payload"]["after"]["claims"]["held_count"] == 2
    assert page["entries"][0]["actor"]["qualification"] == "unknown"
    assert page["entries"][0]["occurred_at"] is None


def test_resume_replays_indexed_events_in_first_atomic_snapshot(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    cursor = initial["cursor"]
    for count in range(1, 4):
        value = record(count)
        cursor = owner.publish(
            value,
            [event(value)],
            batch_id=f"batch:{count}",
            expected_cursor=cursor,
            post_save=True,
        )["cursor"]
    stream = owner.stream(request(after=initial["cursor"]), lambda: False)
    try:
        snapshot = next(stream)
        assert snapshot["cursor"] == snapshot["replay_barrier"] == cursor
        assert snapshot["base_cursor"] == initial["cursor"]
        assert snapshot["replay_complete"] and len(snapshot["entries"]) == 3
        assert snapshot["facts"] == record(3).facts
    finally:
        stream.close()


def test_slow_reader_gets_gap_and_current_snapshot_not_fabricated_event_replay(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        cursor = initial["cursor"]
        for number in range(1, 66):
            value = record(number)
            cursor = owner.publish(
                value,
                [event(value)],
                batch_id=f"batch:{number}",
                expected_cursor=cursor,
                post_save=True,
            )["cursor"]
        gap = next(stream)
        snapshot = next(stream)
        assert gap["type"] == "gap" and gap["reason"] == "cursor-unavailable"
        assert snapshot["type"] == "snapshot" and snapshot["cursor"] == cursor
        assert snapshot["entries"] == [] and not snapshot["replay_complete"]
        query = {
            "contract_version": 1,
            "project": "project",
            "worktree_id": "full-worktree",
            "expected_head_session_id": "head",
            "source_generation": initial["source_generation"],
            "before_cursor": None,
            "limit": 64,
        }
        source = c.Source(
            *(
                snapshot[key]
                for key in (
                    "project",
                    "worktree_id",
                    "source",
                    "source_generation",
                    "resident_generation",
                    "head_session_id",
                )
            )
        )
        retained = {}
        previous_oldest = None
        for page_number in range(65):
            page = owner.history_page(query)
            c.validate_frame(page)
            assert 1 <= len(page["entries"]) <= c.PAGE_ENTRIES
            assert (
                len(
                    c.utf8(
                        c.encode(
                            {
                                key: value
                                for key, value in page.items()
                                if key not in c.HEADER_FIELDS
                            }
                        )
                    )
                )
                + 1
                <= c.BODY_BYTES
            )
            assert len(c.utf8(c.encode(page))) + 1 <= c.FRAME_BYTES
            assert page["cursor"] == cursor
            positions = [c.sequence(item["cursor"], source) for item in page["entries"]]
            assert positions == list(range(positions[0], positions[-1] + 1))
            if previous_oldest is not None:
                assert positions[-1] == previous_oldest - 1
            for item in page["entries"]:
                assert item["event_id"] not in retained
                retained[item["event_id"]] = c.sequence(item["cursor"], source)
            if page_number == 0:
                assert page["has_more"]
            if not page["has_more"]:
                assert page["next_before"] is None
                break
            assert page["next_before"] == page["entries"][0]["cursor"]
            next_before = c.sequence(page["next_before"], source)
            if query["before_cursor"] is not None:
                assert next_before < c.sequence(query["before_cursor"], source)
            previous_oldest = positions[0]
            query = {**query, "before_cursor": page["next_before"]}
        else:
            pytest.fail("owner history paging did not terminate")
        assert len(retained) == 65 and sorted(retained.values()) == list(range(1, 66))
        assert owner.read(request())["cursor"] == cursor
    finally:
        stream.close()


def test_source_admission_subscriber_bound_cancellation_and_demand(runtime):
    owner, clock, wakes = runtime
    unready = owner.stream(request(), lambda: False)
    assert next(unready)["error"]["code"] == "unready"
    with pytest.raises(StopIteration):
        next(unready)
    assert owner.active_count() == 0 and owner.demand_projects() == {"project"}
    assert wakes == [1]
    assert owner.read(request())["error"]["code"] == "unready" and wakes == [1]
    owner.initialize(record())
    streams = [owner.stream(request(), lambda: False) for _ in range(33)]
    try:
        for stream in streams[:32]:
            assert next(stream)["type"] == "snapshot"
        assert next(streams[32])["error"]["code"] == "subscriber-limit"
    finally:
        for stream in streams:
            stream.close()
    assert owner.wait_closed(1)
    owner.close_admission("draining")
    before = dict(owner._demand)
    assert owner.read(request())["error"]["code"] == "draining"
    assert owner._demand == before
    clock[0] += 30.001
    assert owner.demand_projects() == set()


def test_failed_append_notifies_explicit_gap_without_pretending_canonical_rollback(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        with sqlite3.connect(owner.history.path) as conn:
            conn.execute(
                "CREATE TRIGGER fail_events BEFORE INSERT ON owner_observation_entry "
                "BEGIN SELECT RAISE(ABORT,'synthetic append failure'); END",
            )
        canonical_record = record(1)
        with pytest.raises(c.ObservationError) as error:
            owner.publish(
                canonical_record,
                [event(canonical_record)],
                batch_id="batch:1",
                expected_cursor=initial["cursor"],
                post_save=True,
            )
        assert error.value.code == "journal-write-failed"
        assert canonical_record.facts["claims"]["held_count"] == 1
        gap, snapshot = next(stream), next(stream)
        assert gap["reason"] == "journal-write-failed"
        assert snapshot["availability"] == "unavailable" and snapshot["facts"] is None
        assert snapshot["cursor"] == initial["cursor"] and snapshot["completeness"] == "partial"
    finally:
        stream.close()


def test_replaced_resident_retires_held_stream_and_never_requeues_old_owner(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    stream = owner.stream(request(), lambda: False)
    next(stream)
    new = OwnerObservationHistory(
        owner.history.path,
        resident_generation="c" * 32,
        writer_id="d" * 32,
        authority_check=lambda: True,
    )
    new.activate(expected_generation=owner.history.resident_generation)
    try:
        terminal = next(stream)
        assert terminal["type"] == "going-away" and terminal["reason"] == "superseded"
        assert terminal["resident_generation"] == initial["resident_generation"]
        with pytest.raises(StopIteration):
            next(stream)
        with pytest.raises(c.ObservationError):
            owner.activate()
        assert owner.active_count() == 0
    finally:
        stream.close()
        new.close()


def test_temporary_same_resident_reactivation_preserves_cursor_and_source_generation(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    owner.close_admission("unavailable")
    owner.activate()
    assert owner.read(request()) == initial
    owner.shutdown()
    with pytest.raises(c.ObservationError):
        owner.activate()


def test_head_change_clears_active_facts_and_ends_only_old_selection(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    stream = owner.stream(request(), lambda: False)
    next(stream)
    current = record(1, head="new-head")
    owner.publish(
        current,
        [event(current)],
        batch_id="batch:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    try:
        delta = next(stream)
        assert delta["availability"] == "head-changed" and delta["facts"] is None
        assert next(stream)["reason"] == "head-changed"
    finally:
        stream.close()
    fresh = owner.read(request(head="new-head"))
    assert fresh["availability"] == "observed" and fresh["facts"] == current.facts


def oversized_delivery():
    capture = replace(record(2), facts={"details": ["\u6f22" * 1200] * 7})
    item = event(capture)
    assert len(c.utf8(c.encode(asdict(capture), c.BODY_BYTES - 2048))) < c.BODY_BYTES
    c.entry(item, post_save=True)
    c.encode([item], c.BODY_BYTES)
    return capture, item


def test_admitted_delivery_oversize_withholds_old_facts_and_notifies_gap(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    capture, item = oversized_delivery()
    stream = owner.stream(request(), lambda: False)
    try:
        assert next(stream) == initial
        with pytest.raises(c.ObservationError) as error:
            owner.publish(
                capture,
                [item],
                batch_id="oversized:1",
                expected_cursor=initial["cursor"],
                post_save=True,
            )
        assert error.value.code == "cursor-unavailable"
        assert capture.facts["details"] == ["\u6f22" * 1200] * 7
        gap, snapshot = next(stream), next(stream)
        assert gap["type"] == "gap" and gap["reason"] == "cursor-unavailable"
        assert snapshot["availability"] == "unavailable" and snapshot["facts"] is None
        assert snapshot["durability"] == "pending" and snapshot["completeness"] == "partial"
        assert snapshot["cursor"] == initial["cursor"]
        with sqlite3.connect(owner.history.path) as conn:
            assert conn.execute("SELECT count(*) FROM owner_observation_entry").fetchone()[0] == 0
    finally:
        stream.close()


@pytest.mark.parametrize(
    "rejection", ["stale", "wrong-source", "malformed", "bad-cursor", "wire-oversize"]
)
def test_rejected_oversize_submission_cannot_poison_current_facts(runtime, monkeypatch, rejection):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    one = record(1)
    current = owner.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=initial["cursor"], post_save=True
    )
    capture, item = oversized_delivery()
    cursor = current["cursor"]
    if rejection == "stale":
        cursor = initial["cursor"]
    elif rejection == "wrong-source":
        capture = replace(capture, source="another:record")
    elif rejection == "malformed":
        item["evidence_kind"] = "invented"
    elif rejection == "wire-oversize":
        item["payload"]["details"].extend(["\u6f22" * 1200] * 2)
    else:
        cursor = "not-a-position"
    notifications = []
    monkeypatch.setattr(owner, "_changed", notifications.append)
    with pytest.raises(c.ObservationError):
        owner.publish(
            capture, [item], batch_id="invalid:oversized", expected_cursor=cursor, post_save=True
        )
    assert owner.read(request()) == current
    assert notifications == [] and owner.history._failures == {}


def test_qualification_only_reconciliation_advances_cursor_and_replays_observed_changes(runtime):
    owner, _clock, _wakes = runtime
    capture = record()
    initial = owner.initialize(capture)
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        unavailable = replace(capture, availability="unavailable", completeness="partial")
        failed = owner.reconcile(unavailable, batch_id="qualification:unavailable")
        first = next(stream)
        assert first["type"] == "delta" and first["availability"] == "unavailable"
        assert first["facts"] is None and first["cursor"] != initial["cursor"]
        assert first["entries"][0]["kind"] == "observed-change"
        assert first["entries"][0]["payload"]["availability"] == "unavailable"
        assert (
            owner.reconcile(unavailable, batch_id="qualification:same")["cursor"]
            == failed["cursor"]
        )
        recovered = owner.reconcile(capture, batch_id="qualification:observed")
        second = next(stream)
        assert second["type"] == "delta" and second["facts"] == capture.facts
        assert second["base_cursor"] == failed["cursor"]
        assert second["cursor"] == recovered["cursor"] != failed["cursor"]
        page = owner.history.page(
            c.Selection("project", "full-worktree", "head"),
            initial["source_generation"],
            None,
        )
        assert len(page["entries"]) == 2
        assert {item["kind"] for item in page["entries"]} == {"observed-change"}
        assert {item["evidence_kind"] for item in page["entries"]} == {"observed-change"}
        assert page["entries"][0]["record_fence"] == page["entries"][1]["record_fence"]
    finally:
        stream.close()


def test_oversize_failure_rechecks_owner_before_marking_source_unavailable(runtime, monkeypatch):
    from agent_worktrees import owner_observation_history

    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    capture, item = oversized_delivery()
    original = owner_observation_history.delivery_failed
    reached = []

    def replaced(history, selected, current, expected_cursor, error):
        reached.append(error.code)
        with sqlite3.connect(history.path) as conn:
            conn.execute(
                "UPDATE owner_observation_owner SET generation=?,writer=? WHERE singleton=1",
                ("c" * 32, "d" * 32),
            )
        return original(history, selected, current, expected_cursor, error)

    monkeypatch.setattr(owner_observation_history, "delivery_failed", replaced)
    with pytest.raises(c.ObservationError) as error:
        owner.publish(
            capture,
            [item],
            batch_id="superseded:oversize",
            expected_cursor=initial["cursor"],
            post_save=True,
        )
    assert reached == ["oversized"] and error.value.code == "superseded"
    assert owner.history._failures == {}
    with sqlite3.connect(owner.history.path) as conn:
        assert conn.execute("SELECT position FROM owner_observation_journal").fetchone()[0] == 0


def test_identity_guard_bound_is_gap_not_silent_eviction_or_empty_history(runtime, monkeypatch):
    from agent_worktrees import owner_observation_append

    owner, _clock, _wakes = runtime
    monkeypatch.setattr(owner_observation_append, "MAX_GUARDS", 1)
    initial = owner.initialize(record())
    current = owner.publish(
        record(1),
        [event(record(1))],
        batch_id="batch:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    with pytest.raises(c.ObservationError) as error:
        owner.publish(
            record(2),
            [event(record(2))],
            batch_id="batch:2",
            expected_cursor=current["cursor"],
            post_save=True,
        )
    assert error.value.code == "cursor-unavailable"
    snapshot = owner.read(request())
    assert snapshot["cursor"] == current["cursor"]
    assert snapshot["facts"] is None and snapshot["completeness"] == "partial"
    assert (
        len(
            owner.history.page(
                c.Selection("project", "full-worktree", "head"),
                initial["source_generation"],
                None,
            )["entries"]
        )
        == 1
    )
