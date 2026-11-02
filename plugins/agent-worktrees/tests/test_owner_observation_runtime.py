"""Passive scoped subscriptions over a real private owner index."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from threading import Event

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
    "rejection", ["stale", "wrong-source", "malformed", "bad-cursor", "malformed-oversize"]
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
    elif rejection == "malformed-oversize":
        item["payload"]["details"].extend(["\u6f22" * 1200] * 2)
        item["kind"] = "invented"
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


@pytest.mark.parametrize("operation", ["initialize", "publish", "reconcile"])
def test_runtime_writer_boundaries_detach_before_waiting_on_operations_lock(
    runtime, monkeypatch, operation
):
    from agent_worktrees import owner_observation_runtime

    owner, _clock, _wakes = runtime
    baseline = owner.initialize(record()) if operation != "initialize" else None
    supplied = record(1)
    item = event(supplied)
    expected = deepcopy(supplied.facts)
    captured = Event()
    function = "prepare" if operation == "publish" else "freeze_record"
    original = getattr(owner_observation_runtime, function)

    def freeze(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.set()
        return result

    monkeypatch.setattr(owner_observation_runtime, function, freeze)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with owner._operations:
            if operation == "publish":
                future = workers.submit(
                    owner.publish,
                    supplied,
                    [item],
                    batch_id="boundary:1",
                    expected_cursor=baseline["cursor"],
                    post_save=True,
                )
            elif operation == "reconcile":
                future = workers.submit(owner.reconcile, supplied, batch_id="boundary:1")
            else:
                future = workers.submit(owner.initialize, supplied)
            assert captured.wait(2)
            supplied.facts["claims"]["held_count"] = 999
            supplied.record_fence["record_revision"] = 999
            item["payload"]["claims"]["held_count"] = 999
        result = future.result(timeout=5)
    assert result["facts"] == expected
    assert owner.history.last_record(c.Selection("project", "full-worktree", "head"))[
        0
    ].record_fence == {
        "record_revision": 1,
    }


@pytest.mark.parametrize("duplicate_batch", [True, False])
def test_duplicate_old_capture_cannot_clear_newer_failed_materialization(runtime, duplicate_batch):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    one, two = record(1), record(2)
    accepted = owner.publish(
        one, [event(one)], batch_id="batch:A", expected_cursor=initial["cursor"], post_save=True
    )
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        with sqlite3.connect(owner.history.path) as conn:
            conn.execute(
                "CREATE TRIGGER fail_newer BEFORE INSERT ON owner_observation_entry "
                "BEGIN SELECT RAISE(ABORT,'newer capture failure'); END",
            )
        with pytest.raises(c.ObservationError):
            owner.publish(
                two,
                [event(two)],
                batch_id="batch:B",
                expected_cursor=accepted["cursor"],
                post_save=True,
            )
        assert next(stream)["type"] == "gap"
        assert next(stream)["facts"] is None
        with sqlite3.connect(owner.history.path) as conn:
            conn.execute("DROP TRIGGER fail_newer")
        duplicate = owner.publish(
            one,
            [event(one)],
            batch_id="batch:A" if duplicate_batch else "batch:A-again",
            expected_cursor=accepted["cursor"],
            post_save=True,
        )
        assert duplicate["cursor"] == accepted["cursor"]
        assert duplicate["availability"] == "unavailable" and duplicate["facts"] is None
        assert owner.read(request())["facts"] is None
        assert next(stream)["type"] == "gap"
        assert next(stream)["facts"] is None
        assert owner.initialize(replace(one, observed_at="2026-11-02T00:00:05Z"))["facts"] is None
        assert next(stream)["type"] == "gap"
        assert next(stream)["facts"] is None
        recovered = owner.publish(
            two,
            [event(two)],
            batch_id="batch:B",
            expected_cursor=accepted["cursor"],
            post_save=True,
        )
        assert recovered["facts"] == two.facts and recovered["cursor"] != accepted["cursor"]
        assert next(stream)["type"] == "gap"
        assert next(stream)["facts"] == two.facts
        assert owner.read(request())["facts"] == two.facts
    finally:
        stream.close()


def test_large_changed_facts_use_truthful_bounded_omission_evidence(runtime):
    owner, _clock, _wakes = runtime
    old = replace(record(), facts={"details": ["a" * 3500] * 5})
    new = replace(record(1), facts={"details": ["b" * 3500] * 5})
    initial = owner.initialize(old)
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        result = owner.reconcile(new, batch_id="large:changed")
        frame = next(stream)
        assert frame["type"] == "delta" and frame["facts"] == new.facts
        assert frame["cursor"] == result["cursor"] != initial["cursor"]
        item = frame["entries"][0]
        assert item["kind"] == item["evidence_kind"] == "observed-change"
        assert item["payload"]["facts_omitted"] is True
        assert "before" not in item["payload"] and "after" not in item["payload"]
        assert (
            item["payload"]["before_sha256"]
            == hashlib.sha256(c.utf8(c.encode(old.facts))).hexdigest()
        )
        assert (
            item["payload"]["after_sha256"]
            == hashlib.sha256(c.utf8(c.encode(new.facts))).hexdigest()
        )
        assert item["payload"]["capture_source_before"] == old.source
        assert item["payload"]["capture_source_after"] == new.source
        assert len(c.utf8(c.encode(frame))) + 1 <= c.FRAME_BYTES
    finally:
        stream.close()


def deep_capture_facts(value):
    facts = {"value": value}
    for _ in range(11):
        facts = {"nested": facts}
    return facts


def test_capture_valid_deep_changed_facts_use_validated_omission_without_gap(runtime):
    from agent_worktrees.owner_observation_runtime import observed_entry

    owner, _clock, _wakes = runtime
    before = replace(record(), facts=deep_capture_facts("before"))
    after = replace(record(1), facts=deep_capture_facts("after"))
    initial = owner.initialize(before)
    item = observed_entry(before, after, "deep:1", initial["source_generation"])
    assert item["payload"]["facts_omitted"]
    c.entry(item)
    full = deepcopy(item)
    full["payload"].update(before=before.facts, after=after.facts)
    with pytest.raises(c.ObservationError, match="nesting"):
        c.entry(full)
    stream = owner.stream(request(), lambda: False)
    try:
        assert next(stream) == initial
        current = owner.reconcile(after, batch_id="deep:1")
        frame = next(stream)
        assert frame["type"] == "delta" and frame["facts"] == after.facts
        assert frame["cursor"] == current["cursor"] != initial["cursor"]
        assert frame["entries"][0]["payload"]["facts_omitted"]
        assert (
            frame["entries"][0]["payload"]["after_sha256"]
            == hashlib.sha256(
                c.utf8(c.encode(after.facts)),
            ).hexdigest()
        )
        assert owner.history._failures == {}
        assert owner.history.snapshot(c.Selection("project", "full-worktree", "head"))[1] is None
    finally:
        stream.close()


def test_deep_fact_fallback_does_not_hide_malformed_entry_skeleton(runtime):
    from agent_worktrees.owner_observation_runtime import observed_entry

    owner, _clock, _wakes = runtime
    before = replace(record(), facts=deep_capture_facts("before"))
    after = replace(record(1), facts=deep_capture_facts("after"))
    initial = owner.initialize(before)
    with pytest.raises(c.ObservationError, match="source record"):
        observed_entry(before, after, "malformed [reference]", initial["source_generation"])
    with pytest.raises(c.ObservationError):
        owner.reconcile(after, batch_id="malformed [reference]")
    assert owner.read(request()) == initial
    assert owner.history._failures == {}


@pytest.mark.parametrize("raced", [False, True])
def test_unrepresentable_reconcile_checks_current_cursor_before_invalidation(
    runtime, monkeypatch, raced
):
    from agent_worktrees import owner_observation_runtime

    owner, _clock, _wakes = runtime
    source_id = "s" * 512
    old = replace(record(), source=source_id, facts={"details": ["a" * 3500] * 5})
    initial = owner.initialize(old)
    proposed = replace(
        record(1),
        source=source_id,
        facts={"details": ["b" * 3500] * 5},
        record_fence={"record_revision": 1, "padding": ["x" * 3500] * 3 + [""]},
    )
    unused = c.BODY_BYTES - 2048 - len(c.utf8(c.encode(asdict(proposed)))) - 1
    assert 0 < unused <= 4096
    fence = deepcopy(proposed.record_fence)
    fence["padding"][-1] = "x" * unused
    proposed = replace(proposed, record_fence=fence)
    assert len(c.utf8(c.encode(asdict(proposed)))) + 1 == c.BODY_BYTES - 2048
    competing = replace(record(2), source=source_id)
    original = owner_observation_runtime.observed_entry

    def capture(*args):
        value = original(*args)
        if raced:
            owner.history.publish(
                competing,
                [event(competing)],
                batch_id="competing:2",
                expected_cursor=initial["cursor"],
                post_save=True,
            )
        return value

    monkeypatch.setattr(owner_observation_runtime, "observed_entry", capture)
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        with pytest.raises(c.ObservationError) as error:
            owner.reconcile(proposed, batch_id="b" * 512)
        if raced:
            assert error.value.code == "stale-cursor"
            assert owner.read(request())["facts"] == competing.facts
            assert owner.history._failures == {}
        else:
            assert error.value.code == "cursor-unavailable"
            assert next(stream)["type"] == "gap"
            assert next(stream)["facts"] is None
            assert owner.read(request())["facts"] is None
    finally:
        stream.close()


@pytest.mark.parametrize("invalid", [None, "stale", "wrong-source", "malformed"])
def test_direct_oversized_entry_is_invalidated_only_after_matching_admission(runtime, invalid):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    current = owner.publish(
        record(1),
        [event(record(1))],
        batch_id="base:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    capture = record(2)
    item = event(capture)
    item["payload"] = {"details": ["x" * 3500] * 9}
    c.entry(item, post_save=True, _check_size=False)
    with pytest.raises(c.ObservationError):
        c.entry(item, post_save=True)
    expected_cursor = current["cursor"]
    if invalid == "stale":
        expected_cursor = initial["cursor"]
    elif invalid == "wrong-source":
        capture = replace(capture, source="record:wrong")
    elif invalid == "malformed":
        item["kind"] = "invented"
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        with pytest.raises(c.ObservationError) as error:
            owner.publish(
                capture,
                [item],
                batch_id="large:entry",
                expected_cursor=expected_cursor,
                post_save=True,
            )
        if invalid is None:
            assert error.value.code == "cursor-unavailable"
            assert next(stream)["type"] == "gap"
            unavailable = next(stream)
            assert unavailable["facts"] is None and unavailable["availability"] == "unavailable"
            assert unavailable["cursor"] == current["cursor"]
        else:
            assert error.value.code in {"stale-cursor", "source-replaced", "invalid-request"}
            assert owner.read(request()) == current
            assert owner.history._failures == {}
    finally:
        stream.close()


@pytest.mark.parametrize("batch_id", [None, "bad batch", "b" * 513, True])
def test_invalid_batch_metadata_cannot_invalidate_oversized_publication(
    runtime, monkeypatch, batch_id
):
    owner, _clock, _wakes = runtime
    current = owner.initialize(record(1))
    capture = record(2)
    item = event(capture)
    item["payload"] = {"details": ["x" * 3500] * 9}
    c.entry(item, post_save=True, _check_size=False)
    with pytest.raises(c.ObservationError) as oversized:
        c.entry(item, post_save=True)
    assert oversized.value.code == "oversized"
    notifications = []
    monkeypatch.setattr(owner, "_changed", notifications.append)
    stream = owner.stream(request(), lambda: False)
    try:
        assert next(stream) == current
        with pytest.raises(c.ObservationError) as error:
            owner.publish(
                capture,
                [item],
                batch_id=batch_id,
                expected_cursor=current["cursor"],
                post_save=True,
            )
        assert error.value.code == "invalid-request"
        assert owner.read(request()) == current
        assert owner.history._failures == {}
        assert owner.history._failure_reasons == {}
        assert notifications == []
        assert next(stream)["type"] == "heartbeat"
    finally:
        stream.close()


@pytest.mark.parametrize("completeness", ["complete", "unknown"])
def test_invalid_reconciliation_batch_cannot_refresh_unchanged_capture(runtime, completeness):
    owner, _clock, _wakes = runtime
    capture = replace(record(1), completeness=completeness)
    current = owner.initialize(capture)
    refreshed = replace(capture, observed_at="2026-11-02T00:00:01Z")
    with pytest.raises(c.ObservationError) as error:
        owner.reconcile(refreshed, batch_id="invalid batch")
    assert error.value.code == "invalid-request"
    assert owner.read(request()) == current
    assert owner.history._failures == {}
    assert owner.history._failure_reasons == {}


@pytest.mark.parametrize("completeness", ["complete", "unknown"])
def test_recovery_gap_does_not_rewrite_capture_completeness_or_emit_fake_reconcile(
    runtime, completeness
):
    owner, _clock, _wakes = runtime
    first = replace(record(1), completeness=completeness)
    newer = replace(record(2), completeness=completeness)
    initial = owner.initialize(first)
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_once BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'failed append'); END",
        )
    with pytest.raises(c.ObservationError):
        owner.publish(
            newer,
            [event(newer)],
            batch_id="recover:1",
            expected_cursor=initial["cursor"],
            post_save=True,
        )
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute("DROP TRIGGER fail_once")
    recovered = owner.publish(
        newer,
        [event(newer)],
        batch_id="recover:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    assert recovered["completeness"] == "partial" and recovered["facts"] == newer.facts
    for batch_id in ("recover:1", "reconcile:another"):
        unchanged = owner.reconcile(newer, batch_id=batch_id)
        assert unchanged["cursor"] == recovered["cursor"]
        assert unchanged["completeness"] == "partial" and unchanged["facts"] == newer.facts
    page = owner.history.page(
        c.Selection("project", "full-worktree", "head"),
        initial["source_generation"],
        None,
    )
    assert len(page["entries"]) == 1
    with sqlite3.connect(owner.history.path) as conn:
        assert (
            conn.execute("SELECT completeness FROM owner_observation_journal").fetchone()[0]
            == completeness
        )


def test_successful_source_replacement_scopes_failure_to_old_journal(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    stream = owner.stream(request(), lambda: False)
    try:
        next(stream)
        with sqlite3.connect(owner.history.path) as conn:
            conn.execute(
                "CREATE TRIGGER fail_old BEFORE INSERT ON owner_observation_entry "
                "BEGIN SELECT RAISE(ABORT,'old journal failure'); END",
            )
        with pytest.raises(c.ObservationError):
            owner.publish(
                record(1),
                [event(record(1))],
                batch_id="old:failed",
                expected_cursor=initial["cursor"],
                post_save=True,
            )
        assert next(stream)["type"] == "gap"
        assert next(stream)["facts"] is None
        replacement = record(2, incarnation="record:new")
        for expected in (None, "c" * 32):
            with pytest.raises(c.ObservationError) as error:
                owner.initialize(replacement, expected_source_generation=expected)
            assert error.value.code == "source-replaced"
        assert owner.read(request())["facts"] is None
        with pytest.raises(c.ObservationError):
            owner.publish(
                replacement,
                [event(replacement)],
                batch_id="wrong:source",
                expected_cursor=initial["cursor"],
                post_save=True,
            )
        assert owner.read(request())["facts"] is None
        current = owner.initialize(
            replacement, expected_source_generation=initial["source_generation"]
        )
        assert current["availability"] == "observed" and current["facts"] == replacement.facts
        assert current["source_generation"] != initial["source_generation"]
        assert next(stream)["reason"] == "source-replaced"
        assert next(stream)["facts"] == replacement.facts
        with sqlite3.connect(owner.history.path) as conn:
            assert (
                conn.execute(
                    "SELECT gap FROM owner_observation_journal WHERE generation=?",
                    (initial["source_generation"],),
                ).fetchone()[0]
                == "journal-write-failed"
            )
        assert (
            owner.history.page(
                c.Selection("project", "full-worktree", "head"),
                initial["source_generation"],
                None,
            )["completeness"]
            == "partial"
        )
    finally:
        stream.close()


def test_qualification_only_large_facts_reconcile_without_duplicating_payload(runtime):
    owner, _clock, _wakes = runtime
    large = replace(record(), facts={"details": ["x" * 3500] * 8})
    initial = owner.initialize(large)
    unavailable = replace(large, availability="unavailable", completeness="partial")
    failed = owner.reconcile(unavailable, batch_id="large:unavailable")
    assert failed["facts"] is None and failed["cursor"] != initial["cursor"]
    recovered = owner.reconcile(large, batch_id="large:observed")
    assert recovered["facts"] == large.facts and recovered["cursor"] != failed["cursor"]
    page = owner.history.page(
        c.Selection("project", "full-worktree", "head"),
        initial["source_generation"],
        None,
    )
    assert len(page["entries"]) == 2
    assert all(
        "before" not in item["payload"] and "after" not in item["payload"]
        for item in page["entries"]
    )


def test_failed_same_fact_qualification_cannot_be_cleared_by_old_duplicate(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    one = record(1)
    accepted = owner.publish(
        one, [event(one)], batch_id="batch:A", expected_cursor=initial["cursor"], post_save=True
    )
    unavailable = replace(one, availability="unavailable", completeness="partial")
    unavailable_entry = event(unavailable, identity="save:qualification-B")
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_qualification BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'qualification failure'); END",
        )
    with pytest.raises(c.ObservationError):
        owner.publish(
            unavailable,
            [unavailable_entry],
            batch_id="batch:B",
            expected_cursor=accepted["cursor"],
            post_save=True,
        )
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute("DROP TRIGGER fail_qualification")
    repeated = owner.publish(
        one, [event(one)], batch_id="batch:A", expected_cursor=accepted["cursor"], post_save=True
    )
    assert repeated["facts"] is None and repeated["durability"] == "pending"
    recovered = owner.publish(
        unavailable,
        [unavailable_entry],
        batch_id="batch:B",
        expected_cursor=accepted["cursor"],
        post_save=True,
    )
    assert recovered["facts"] is None and recovered["availability"] == "unavailable"
    assert recovered["durability"] == "committed" and recovered["cursor"] != accepted["cursor"]


def test_new_resident_requires_current_capture_before_recovering_failed_newer_state(runtime):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_newer BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'capture failed'); END",
        )
    with pytest.raises(c.ObservationError):
        owner.publish(
            record(1),
            [event(record(1))],
            batch_id="batch:B",
            expected_cursor=initial["cursor"],
            post_save=True,
        )
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute("DROP TRIGGER fail_newer")
    replacement = OwnerObservationRuntime(
        OwnerObservationHistory(
            owner.history.path,
            resident_generation="c" * 32,
            writer_id="d" * 32,
            authority_check=lambda: True,
        )
    )
    replacement.activate(expected_generation=owner.history.resident_generation)
    try:
        assert replacement.read(request())["error"]["code"] == "unready"
        latest = replacement.reconcile(record(1), batch_id="requalify:current")
        assert latest["facts"] == record(1).facts
        assert latest["source_generation"] == initial["source_generation"]
        assert latest["completeness"] == "unknown"
    finally:
        replacement.shutdown()


def test_same_cursor_current_timestamp_refresh_reaches_held_reader_without_history_event(runtime):
    owner, _clock, _wakes = runtime
    supplied = record()
    initial = owner.initialize(supplied)
    stream = owner.stream(request(), lambda: False)
    try:
        assert next(stream) == initial
        refreshed = owner.initialize(replace(supplied, observed_at="2026-11-02T00:00:05Z"))
        current = next(stream)
        assert current["type"] == "snapshot"
        assert current["cursor"] == refreshed["cursor"] == initial["cursor"]
        assert current["observed_at"] == "2026-11-02T00:00:05Z"
        assert current["entries"] == []
        page = owner.history.page(
            c.Selection("project", "full-worktree", "head"),
            initial["source_generation"],
            None,
        )
        assert page["entries"] == []
    finally:
        stream.close()


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


@pytest.mark.parametrize(
    "operation",
    [
        "publish-current",
        "publish-stale",
        "publish-wrong-source",
        "initialize-current",
        "initialize-stale",
        "initialize-missing-generation",
        "refresh",
        "reconcile",
        "delivery-oversize",
    ],
)
def test_sqlite_begin_lock_timeout_before_admission_cannot_invalidate_or_notify(
    runtime, monkeypatch, operation
):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    current = owner.publish(
        record(1),
        [event(record(1))],
        batch_id="baseline:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    before_failures = dict(owner.history._failures)
    before_reasons = dict(owner.history._failure_reasons)
    notifications = []
    monkeypatch.setattr(owner, "_changed", notifications.append)
    stream = owner.stream(request(), lambda: False)
    try:
        assert next(stream) == current
        with sqlite3.connect(owner.history.path, timeout=0.1) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with pytest.raises(c.ObservationError) as error:
                if operation == "delivery-oversize":
                    capture = record(2)
                    item = event(capture)
                    item["payload"] = {"details": ["x" * 3500] * 9}
                    owner.publish(
                        capture,
                        [item],
                        batch_id="locked:oversize",
                        expected_cursor=current["cursor"],
                        post_save=True,
                    )
                elif operation.startswith("publish"):
                    capture = (
                        record(2, incarnation="record:wrong")
                        if operation == "publish-wrong-source"
                        else record(2)
                    )
                    cursor = (
                        initial["cursor"] if operation == "publish-stale" else current["cursor"]
                    )
                    owner.publish(
                        capture,
                        [event(capture)],
                        batch_id="locked:publication",
                        expected_cursor=cursor,
                        post_save=True,
                    )
                elif operation == "refresh":
                    owner.initialize(replace(record(1), observed_at="2026-11-02T00:00:05Z"))
                elif operation == "reconcile":
                    owner.reconcile(record(2), batch_id="locked:reconcile")
                else:
                    expected = current["source_generation"]
                    if operation == "initialize-stale":
                        expected = "c" * 32
                    elif operation == "initialize-missing-generation":
                        expected = None
                    owner.initialize(
                        record(2, incarnation="record:replacement"),
                        expected_source_generation=expected,
                    )
            assert error.value.code == "unavailable"
            assert getattr(error.value, "invalidated", False) is False
            blocker.rollback()
        assert owner.read(request()) == current
        assert owner.history._failures == before_failures
        assert owner.history._failure_reasons == before_reasons
        assert notifications == []
        assert owner.active_count() == 1
        assert next(stream)["type"] == "heartbeat"
    finally:
        stream.close()


@pytest.mark.parametrize("operation", ["refresh", "replacement"])
def test_initializer_failure_after_proven_admission_is_qualified_and_reaches_subscriber(
    runtime, operation
):
    from agent_worktrees.owner_observation_failure import MutationFailure

    owner, _clock, _wakes = runtime
    initial = owner.initialize(record(1))
    stream = owner.stream(request(), lambda: False)
    try:
        assert next(stream) == initial
        with sqlite3.connect(owner.history.path) as conn:
            if operation == "refresh":
                conn.execute(
                    "CREATE TRIGGER fail_admitted BEFORE UPDATE OF qualified "
                    "ON owner_observation_journal "
                    "BEGIN SELECT RAISE(ABORT,'admitted refresh failure'); END",
                )
            else:
                conn.execute(
                    "CREATE TRIGGER fail_admitted BEFORE INSERT ON owner_observation_journal "
                    "BEGIN SELECT RAISE(ABORT,'admitted replacement failure'); END",
                )
        with pytest.raises(MutationFailure) as error:
            if operation == "refresh":
                owner.initialize(replace(record(1), observed_at="2026-11-02T00:00:05Z"))
            else:
                owner.initialize(
                    record(2, incarnation="record:replacement"),
                    expected_source_generation=initial["source_generation"],
                )
        assert error.value.invalidated is True
        assert error.value.code == "journal-write-failed"
        assert next(stream)["type"] == "gap"
        failed = next(stream)
        assert (
            failed["cursor"] == initial["cursor"]
            and failed["source_generation"] == initial["source_generation"]
        )
        assert failed["availability"] == "unavailable" and failed["facts"] is None
        with sqlite3.connect(owner.history.path) as conn:
            conn.execute("DROP TRIGGER fail_admitted")
        duplicate_refresh = owner.initialize(record(1))
        assert duplicate_refresh["facts"] is None
    finally:
        stream.close()


def test_pre_admission_lock_timeout_preserves_existing_failed_duplicate_state(
    runtime, monkeypatch
):
    owner, _clock, _wakes = runtime
    initial = owner.initialize(record())
    one = record(1)
    accepted = owner.publish(
        one, [event(one)], batch_id="baseline:1", expected_cursor=initial["cursor"], post_save=True
    )
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute(
            "CREATE TRIGGER failed_newer BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'newer admitted failure'); END",
        )
    with pytest.raises(c.ObservationError):
        owner.publish(
            record(2),
            [event(record(2))],
            batch_id="newer:2",
            expected_cursor=accepted["cursor"],
            post_save=True,
        )
    before = owner.read(request())
    failures = dict(owner.history._failures)
    notifications = []
    monkeypatch.setattr(owner, "_changed", notifications.append)
    with sqlite3.connect(owner.history.path) as conn:
        conn.execute("DROP TRIGGER failed_newer")
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(c.ObservationError) as error:
            owner.publish(
                one,
                [event(one)],
                batch_id="baseline:1",
                expected_cursor=initial["cursor"],
                post_save=True,
            )
        assert error.value.code == "unavailable"
        conn.rollback()
    assert owner.read(request()) == before
    assert owner.history._failures == failures
    assert notifications == []
    assert (
        owner.publish(
            one,
            [event(one)],
            batch_id="baseline:1",
            expected_cursor=initial["cursor"],
            post_save=True,
        )["facts"]
        is None
    )
