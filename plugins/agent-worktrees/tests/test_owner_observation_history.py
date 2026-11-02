"""Private SQLite source materialization, retention and failure semantics."""

from __future__ import annotations

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from threading import Event

import pytest
from agent_procutil.owned_process import run_caller_owned
from test_owner_observation_contract import entry

from agent_worktrees import owner_observation_contract as c
from agent_worktrees.owner_observation_history import CanonicalObservation, OwnerObservationHistory


def record(value=0, *, project="project", head="head", incarnation="record:one"):
    return CanonicalObservation(
        project,
        "full-worktree",
        incarnation,
        head,
        {"claims": {"held_count": value}, "active_effort": None},
        {"record_revision": value},
        "2026-11-02T00:00:00Z",
    )


def selected(value=None):
    value = record() if value is None else value
    return c.Selection(value.project, value.worktree_id, value.head_session_id)


def event(value, *, kind="claim-added", evidence="persisted-mutation", identity=None):
    item = entry(kind, evidence, value.record_fence)
    item["source_ref"]["record_id"] = identity or f"save:{value.record_fence['record_revision']}"
    item["payload"] = deepcopy(value.facts)
    return item


@pytest.fixture
def history(tmp_path):
    alive = [True]
    store = OwnerObservationHistory(
        tmp_path / "owner.sqlite3",
        resident_generation="a" * 32,
        writer_id="b" * 32,
        authority_check=lambda: alive[0],
    )
    store.activate()
    yield store, alive
    store.close()


class AdmissionCrash(BaseException):
    pass


def take_over(old, generation="c", writer="d"):
    new = OwnerObservationHistory(
        old.path,
        resident_generation=generation * 32,
        writer_id=writer * 32,
        authority_check=lambda: True,
    )
    new.activate(expected_generation=old.resident_generation)
    return new


def intent_count(store):
    with sqlite3.connect(store.path) as conn:
        return conn.execute("SELECT count(*) FROM owner_observation_intent").fetchone()[0]


def force_admitted_failure(store, baseline, capture, reason):
    if reason == "journal-write-failed":
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "CREATE TRIGGER fail_materialization BEFORE INSERT ON owner_observation_entry "
                "BEGIN SELECT RAISE(ABORT,'admitted append failure'); END"
            )
    item = event(capture)
    if reason == "cursor-unavailable":
        item["payload"] = {"details": ["x" * 3500] * 9}
    try:
        with pytest.raises(c.ObservationError) as error:
            store.publish(
                capture,
                [item],
                batch_id="failed:2",
                expected_cursor=baseline["cursor"],
                post_save=True,
            )
        assert error.value.code == reason
    finally:
        if reason == "journal-write-failed":
            with sqlite3.connect(store.path) as conn:
                conn.execute("DROP TRIGGER fail_materialization")


@pytest.mark.parametrize("reason", ["cursor-unavailable", "journal-write-failed"])
@pytest.mark.parametrize("completeness", ["complete", "unknown"])
def test_known_gap_survives_takeover_fresh_capture_recovery_and_another_restart(
    history, reason, completeness
):
    old, _alive = history
    one = replace(record(1), completeness=completeness)
    initial = old.initialize(record())
    baseline = old.publish(
        one,
        [event(one)],
        batch_id="accepted:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    two = replace(record(2), completeness=completeness)
    force_admitted_failure(old, baseline, two, reason)
    assert old.snapshot(selected())[0]["facts"] is None
    new = take_over(old)
    try:
        with pytest.raises(c.ObservationError) as error:
            new.snapshot(selected())
        assert error.value.code == "unready"
        qualified = new.initialize(one)
        assert qualified["facts"] == one.facts and qualified["completeness"] == "partial"
        assert qualified["durability"] == "committed"
        assert new.snapshot(selected())[1]["reason"] == reason
        assert new.last_record(selected())[0].completeness == completeness
        duplicate = new.publish(
            one,
            [event(one)],
            batch_id="accepted:1",
            expected_cursor=initial["cursor"],
            post_save=True,
        )
        assert duplicate == qualified
        repaired = new.publish(
            two,
            [event(two)],
            batch_id="recovery:2",
            expected_cursor=qualified["cursor"],
            post_save=True,
        )
        assert repaired["facts"] == two.facts and repaired["completeness"] == "partial"
        assert intent_count(new) == 0
        newer = take_over(new, "e", "f")
        try:
            refreshed = newer.initialize(two)
            assert refreshed["completeness"] == "partial"
            assert newer.last_record(selected())[0].completeness == completeness
            assert (
                newer.page(selected(), baseline["source_generation"], None)["completeness"]
                == "partial"
            )
            assert newer.snapshot(selected())[1]["reason"] == reason
            with pytest.raises(c.ObservationError) as retired:
                old.publish(
                    two,
                    [event(two)],
                    batch_id="late:old",
                    expected_cursor=baseline["cursor"],
                    post_save=True,
                )
            assert retired.value.code == "superseded"
        finally:
            newer.close()
    finally:
        new.close()


@pytest.mark.parametrize(
    "operation", ["publish", "refresh", "initialize", "replacement", "overflow"]
)
def test_crash_after_admission_commit_reopens_as_source_qualified_uncertainty(history, operation):
    old, _alive = history
    before = (
        None
        if operation == "initialize"
        else old.initialize(replace(record(1), completeness="complete"))
    )
    capture = replace(
        record(
            2 if operation in {"publish", "overflow", "replacement"} else 1,
            incarnation="record:B" if operation == "replacement" else "record:one",
        ),
        completeness="complete",
    )
    tokens = []

    def crash(token):
        tokens.append(token)
        assert intent_count(old) == 1
        raise AdmissionCrash()

    old._after_intent = crash
    with pytest.raises(AdmissionCrash):
        if operation in {"publish", "overflow"}:
            item = event(capture)
            if operation == "overflow":
                item["payload"] = {"details": ["x" * 3500] * 9}
            old.publish(
                capture,
                [item],
                batch_id="admitted:2",
                expected_cursor=before["cursor"],
                post_save=True,
            )
        else:
            old.initialize(
                capture,
                expected_source_generation=None if before is None else before["source_generation"],
            )
    assert len(tokens) == 1 and intent_count(old) == 1
    new = take_over(old)
    try:
        if operation == "publish":
            from agent_worktrees.owner_observation_runtime import OwnerObservationRuntime

            runtime = OwnerObservationRuntime(new, heartbeat=0.01)
            runtime.activate()
            ready = runtime.reconcile(capture, batch_id="fresh:reconcile")
            assert ready["facts"] == capture.facts and ready["completeness"] == "partial"
            stream = runtime.stream(
                {
                    "contract_version": 1,
                    "project": "project",
                    "worktree_id": "full-worktree",
                    "expected_head_session_id": "head",
                    "after_cursor": None,
                },
                lambda: False,
            )
            try:
                assert next(stream)["facts"] == capture.facts
                assert next(stream)["type"] == "gap"
            finally:
                stream.close()
        else:
            current = (
                capture if operation != "overflow" else replace(record(1), completeness="complete")
            )
            ready = new.initialize(
                current,
                expected_source_generation=None if before is None else before["source_generation"],
            )
            assert ready["facts"] == current.facts and ready["completeness"] == "partial"
        assert new.last_record(selected(capture))[0].completeness == "complete"
        with pytest.raises(c.ObservationError) as retired:
            old.initialize(capture)
        assert retired.value.code == "superseded"
    finally:
        new.close()


def test_failed_gap_persistence_leaves_durable_intent_and_no_committed_update(history):
    old, _alive = history
    initial = old.initialize(replace(record(1), completeness="complete"))
    with sqlite3.connect(old.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_stage_two BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'materialization failed'); END"
        )
        conn.execute(
            "CREATE TRIGGER fail_gap BEFORE INSERT ON owner_observation_gap "
            "BEGIN SELECT RAISE(ABORT,'gap persistence failed'); END"
        )
    with pytest.raises(c.ObservationError, match="gap persistence failed") as error:
        old.publish(
            record(2),
            [event(record(2))],
            batch_id="failed:2",
            expected_cursor=initial["cursor"],
            post_save=True,
        )
    assert error.value.admitted and error.value.invalidated
    assert not error.value.gap_persisted and intent_count(old) == 1
    with sqlite3.connect(old.path) as conn:
        assert conn.execute("SELECT count(*) FROM owner_observation_entry").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM owner_observation_gap").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_gap")
        conn.execute("DROP TRIGGER fail_stage_two")
    new = take_over(old)
    try:
        current = new.initialize(replace(record(1), completeness="complete"))
        assert current["completeness"] == "partial" and current["facts"] == record(1).facts
        assert new.snapshot(selected())[1]["reason"] == "cursor-unavailable"
    finally:
        new.close()


def test_update_and_intent_resolution_rollback_together(history):
    old, _alive = history
    initial = old.initialize(record(1))
    with sqlite3.connect(old.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_resolution BEFORE DELETE ON owner_observation_intent "
            "BEGIN SELECT RAISE(ABORT,'resolution failed'); END"
        )
    with pytest.raises(c.ObservationError):
        old.publish(
            record(2),
            [event(record(2))],
            batch_id="update:2",
            expected_cursor=initial["cursor"],
            post_save=True,
        )
    assert intent_count(old) == 1
    with sqlite3.connect(old.path) as conn:
        assert conn.execute("SELECT count(*) FROM owner_observation_entry").fetchone()[0] == 0
        assert (
            c.decode(
                c.utf8(conn.execute("SELECT facts FROM owner_observation_journal").fetchone()[0])
            )
            == record(1).facts
        )
        conn.execute("DROP TRIGGER fail_resolution")
    repaired = old.publish(
        record(2),
        [event(record(2))],
        batch_id="update:2",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    assert repaired["facts"] == record(2).facts and repaired["completeness"] == "partial"
    assert intent_count(old) == 0


def test_takeover_between_intent_and_materialization_fences_old_commit_and_resolution(history):
    old, _alive = history
    initial = old.initialize(record(1))
    successors = []

    def elect(token):
        successors.append(take_over(old))

    old._after_intent = elect
    with pytest.raises(c.ObservationError) as error:
        old.publish(
            record(2),
            [event(record(2))],
            batch_id="late:2",
            expected_cursor=initial["cursor"],
            post_save=True,
        )
    assert error.value.code == "superseded"
    new = successors[0]
    try:
        current = new.initialize(record(1))
        assert current["completeness"] == "partial"
        with sqlite3.connect(new.path) as conn:
            assert conn.execute("SELECT count(*) FROM owner_observation_entry").fetchone()[0] == 0
        assert intent_count(new) == 0
    finally:
        new.close()


def test_failed_replacement_range_does_not_contaminate_predecessor_history_after_takeover(history):
    old, _alive = history
    one = replace(record(1), completeness="complete")
    before = old.initialize(one)
    before = old.publish(
        one, [event(one)], batch_id="old:1", expected_cursor=before["cursor"], post_save=True
    )
    page = old.page(selected(), before["source_generation"], None)
    old._after_intent = lambda token: (_ for _ in ()).throw(AdmissionCrash())
    replacement = replace(record(2, incarnation="record:B"), completeness="complete")
    with pytest.raises(AdmissionCrash):
        old.initialize(replacement, expected_source_generation=before["source_generation"])
    new = take_over(old)
    try:
        ready = new.initialize(replacement, expected_source_generation=before["source_generation"])
        assert ready["facts"] == replacement.facts and ready["completeness"] == "partial"
        archived = new.page(selected(), before["source_generation"], None)
        assert archived == {**page, "resident_generation": new.resident_generation}
        assert archived["completeness"] == "complete"
    finally:
        new.close()


@pytest.mark.parametrize(
    "rejection", ["cursor", "source", "batch", "event", "metadata", "replacement", "intent-write"]
)
def test_rejected_admission_cannot_commit_intent_or_history_gap(history, rejection):
    old, _alive = history
    initial = old.initialize(record())
    current = old.publish(
        record(1),
        [event(record(1))],
        batch_id="accepted:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    capture = record(2, incarnation="record:wrong" if rejection == "source" else "record:one")
    item = event(capture, identity="save:1" if rejection == "event" else None)
    if rejection == "metadata":
        item["actor"]["qualification"] = "invented"
    if rejection == "intent-write":
        with sqlite3.connect(old.path) as conn:
            conn.execute(
                "CREATE TRIGGER reject_intent BEFORE INSERT ON owner_observation_intent "
                "BEGIN SELECT RAISE(ABORT,'intent rejected'); END"
            )
    with pytest.raises(c.ObservationError):
        if rejection == "replacement":
            old.initialize(record(2, incarnation="record:B"), expected_source_generation="e" * 32)
        else:
            old.publish(
                capture,
                [item],
                batch_id="bad batch" if rejection == "batch" else "new:2",
                expected_cursor=initial["cursor"] if rejection == "cursor" else current["cursor"],
                post_save=True,
            )
    assert intent_count(old) == 0 and old.snapshot(selected())[0] == current
    with sqlite3.connect(old.path) as conn:
        assert conn.execute("SELECT count(*) FROM owner_observation_gap").fetchone()[0] == 0


def test_missing_legacy_intent_coverage_is_unknown_not_invented_complete(history):
    old, _alive = history
    capture = replace(record(1), completeness="complete")
    old.initialize(capture)
    with sqlite3.connect(old.path) as conn:
        conn.execute("DROP TABLE owner_observation_intent")
    new = take_over(old)
    try:
        current = new.initialize(capture)
        assert current["completeness"] == "unknown"
        assert new.last_record(selected())[0].completeness == "complete"
        assert new.snapshot(selected())[1]["reason"] == "cursor-unavailable"
    finally:
        new.close()


def test_admission_signature_preserves_nullable_types_and_source_identity(history):
    from agent_worktrees.owner_observation_intent import row_signature

    store, _alive = history
    store.initialize(record())
    with store._read() as conn:
        row = store._row(conn, selected(), _pending=False)
    assert row_signature({**row, "head": None}) != row_signature({**row, "head": "null"})
    assert row_signature(row) != row_signature({**row, "source": "record:different"})


def test_reply_failure_after_atomic_success_does_not_invent_lost_history(history, monkeypatch):
    store, _alive = history
    initial = store.initialize(replace(record(1), completeness="complete"))
    capture = replace(record(2), completeness="complete")

    def failed_reply(*args, **kwargs):
        raise OSError("private reply read failed")

    with monkeypatch.context() as patch:
        patch.setattr(store, "snapshot", failed_reply)
        with pytest.raises(c.ObservationError, match="transaction committed") as error:
            store.publish(
                capture,
                [event(capture)],
                batch_id="committed:2",
                expected_cursor=initial["cursor"],
                post_save=True,
            )
        assert error.value.admitted and not error.value.invalidated
    current, gap = store.snapshot(selected())
    assert current["facts"] == capture.facts and current["completeness"] == "complete"
    assert gap is None and intent_count(store) == 0


def test_initial_snapshot_is_explicit_retained_range_not_canonical_history(history):
    store, _alive = history
    snapshot = store.initialize(record())
    assert snapshot["type"] == "snapshot" and snapshot["facts"] == record().facts
    assert snapshot["entries"] == [] and snapshot["completeness"] == "unknown"
    assert snapshot["replay_barrier"] == snapshot["cursor"] == snapshot["history_floor"]
    assert snapshot["history_scope"] == "retained-range"


def test_postsave_append_snapshot_barrier_replay_and_duplicate_batch(history):
    store, _alive = history
    first = store.initialize(record())
    one = record(1)
    accepted = store.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=first["cursor"], post_save=True
    )
    replay, gap = store.snapshot(selected(one), first["cursor"])
    assert gap is None and replay["replay_complete"]
    assert replay["facts"] == one.facts and len(replay["entries"]) == 1
    assert replay["entries"][0]["cursor"] == replay["cursor"] == accepted["cursor"]
    assert replay["entries"][0]["evidence_kind"] == "persisted-mutation"
    assert (
        store.publish(
            one, [event(one)], batch_id="batch:1", expected_cursor=first["cursor"], post_save=True
        )
        == accepted
    )
    two = record(2)
    current = store.publish(
        two, [event(two)], batch_id="batch:2", expected_cursor=accepted["cursor"], post_save=True
    )
    # A late retry returns current state; it cannot reinstall the old capture.
    retried = store.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=first["cursor"], post_save=True
    )
    assert retried["cursor"] == current["cursor"] and retried["facts"] == two.facts


def test_publication_text_handoffs_preserve_utf8_in_index_and_replayed_entries(history):
    store, _alive = history
    baseline = store.initialize(record())
    one = record(1)
    one.facts["label"] = "\u6f22\U0001f680" * 100
    current = store.publish(
        one,
        [event(one)],
        batch_id="unicode:1",
        expected_cursor=baseline["cursor"],
        post_save=True,
    )
    replay, gap = store.snapshot(selected(one), baseline["cursor"])
    assert gap is None and current["facts"] == one.facts
    assert replay["entries"][0]["payload"] == one.facts
    page = store.page(selected(one), current["source_generation"], None)
    assert page["entries"][0]["payload"] == one.facts


def test_digest_is_not_a_wire_envelope_and_separately_valid_inputs_can_publish(history):
    store, _alive = history
    initial = store.initialize(record())
    large = replace(
        record(1),
        facts={"details": ["x" * 3500] * 4},
        record_fence={"record_revision": 1, "evidence": ["y" * 3500] * 4},
    )
    item = event(large, identity="large:1")
    item["payload"] = {}
    c.entry(item, post_save=True)
    c.encode(asdict(large), c.BODY_BYTES - 2048)
    c.encode([item], c.BODY_BYTES)
    with pytest.raises(c.ObservationError) as error:
        c.encode([asdict(large), [item], True])
    assert error.value.code == "oversized"
    accepted = store.publish(
        large,
        [item],
        batch_id="large:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    assert accepted["facts"] == large.facts
    replay, gap = store.snapshot(selected(large), initial["cursor"])
    assert gap is None and replay["entries"][0]["record_fence"] == large.record_fence


@pytest.mark.parametrize("operation", ["initialize", "publish"])
def test_history_freezes_nested_caller_data_before_waiting_on_owner_lock(
    history, monkeypatch, operation
):
    from agent_worktrees import owner_observation_history

    store, _alive = history
    baseline = store.initialize(record()) if operation == "publish" else None
    supplied = record(1)
    supplied_entry = event(supplied)
    expected = deepcopy(supplied.facts)
    expected_fence = deepcopy(supplied.record_fence)
    captured = Event()
    function = "prepare" if operation == "publish" else "freeze_record"
    original = getattr(owner_observation_history, function)

    def freeze(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.set()
        return result

    monkeypatch.setattr(owner_observation_history, function, freeze)
    with ThreadPoolExecutor(max_workers=1) as workers:
        with store._lock:
            if operation == "publish":
                future = workers.submit(
                    store.publish,
                    supplied,
                    [supplied_entry],
                    batch_id="capture:1",
                    expected_cursor=baseline["cursor"],
                    post_save=True,
                )
            else:
                future = workers.submit(store.initialize, supplied)
            assert captured.wait(2)
            supplied.facts["claims"]["held_count"] = 999
            supplied.record_fence["record_revision"] = 999
            supplied_entry["payload"]["claims"]["held_count"] = 999
        result = future.result(timeout=5)
    assert result["facts"] == expected
    frozen, _cursor, _journal = store.last_record(selected())
    assert frozen.record_fence == expected_fence
    if operation == "publish":
        page = store.page(selected(), result["source_generation"], None)
        assert page["entries"][0]["record_fence"] == expected_fence
        assert page["entries"][0]["payload"] == expected


@pytest.mark.parametrize("change_summary", [False, True])
def test_payload_locator_is_content_not_source_record_identity(history, change_summary):
    store, _alive = history
    baseline = store.initialize(record())
    one = record(1)
    original = event(one)
    accepted = store.publish(
        one,
        [original],
        batch_id="event:1",
        expected_cursor=baseline["cursor"],
        post_save=True,
    )
    changed = deepcopy(original)
    changed["source_ref"]["payload_ref"] = "payload:relocated"
    if change_summary:
        changed["summary"] = "changed summary"
    with pytest.raises(c.ObservationError) as error:
        store.publish(
            one,
            [changed],
            batch_id="event:another",
            expected_cursor=accepted["cursor"],
            post_save=True,
        )
    assert error.value.code == "event-conflict"
    page = store.page(selected(), accepted["source_generation"], None)
    assert len(page["entries"]) == 1
    assert page["entries"][0]["source_ref"]["payload_ref"] is None


@pytest.mark.parametrize("qualifier", ["availability", "completeness"])
def test_initialize_and_reidentified_duplicate_do_not_silently_change_qualifiers(
    history, qualifier
):
    store, _alive = history
    initial = store.initialize(record())
    one = record(1)
    item = event(one)
    accepted = store.publish(
        one,
        [item],
        batch_id="batch:1",
        expected_cursor=initial["cursor"],
        post_save=True,
    )
    changed = replace(
        one, **{qualifier: "unavailable" if qualifier == "availability" else "complete"}
    )
    with pytest.raises(c.ObservationError) as error:
        store.initialize(changed)
    assert error.value.code == "stale-record"
    with pytest.raises(c.ObservationError) as error:
        store.publish(
            changed,
            [item],
            batch_id="duplicate:qualifier",
            expected_cursor=accepted["cursor"],
            post_save=True,
        )
    assert error.value.code == "event-conflict"
    assert store.snapshot(selected())[0] == accepted


def test_batch_and_source_identity_conflicts_never_overwrite_current_capture(history):
    store, _alive = history
    baseline = store.initialize(record())
    one = record(1)
    current = store.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=baseline["cursor"], post_save=True
    )
    changed = event(one)
    changed["summary"] = "different"
    for batch, expected in (("batch:1", "batch-conflict"), ("batch:other", "event-conflict")):
        with pytest.raises(c.ObservationError) as error:
            store.publish(
                one, [changed], batch_id=batch, expected_cursor=current["cursor"], post_save=True
            )
        assert error.value.code == expected
    assert store.snapshot(selected())[0] == current


def test_reidentified_same_native_record_is_idempotent_not_a_new_history_event(history):
    store, _alive = history
    baseline = store.initialize(record())
    one = record(1)
    current = store.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=baseline["cursor"], post_save=True
    )
    repeated = store.publish(
        one,
        [event(one)],
        batch_id="another-delivery",
        expected_cursor=current["cursor"],
        post_save=True,
    )
    assert repeated["cursor"] == current["cursor"]
    page = store.page(selected(), current["source_generation"], None)
    assert len(page["entries"]) == 1


def test_retention_pages_keep_identities_floor_and_explicit_gap(tmp_path):
    store = OwnerObservationHistory(
        tmp_path / "owner.sqlite3",
        resident_generation="a" * 32,
        writer_id="b" * 32,
        authority_check=lambda: True,
        max_entries=2,
    )
    store.activate()
    initial = store.initialize(record())
    cursor = initial["cursor"]
    original = None
    for number in range(1, 5):
        value = record(number)
        result = store.publish(
            value,
            [event(value)],
            batch_id=f"batch:{number}",
            expected_cursor=cursor,
            post_save=True,
        )
        if original is None:
            original = store.snapshot(selected(), cursor)[0]["entries"][0]["event_id"]
        cursor = result["cursor"]
    page = store.page(selected(), initial["source_generation"], None, 1)
    assert len(page["entries"]) == 1 and page["has_more"]
    older = store.page(selected(), initial["source_generation"], page["next_before"], 1)
    assert len(older["entries"]) == 1 and not older["has_more"]
    assert store.snapshot(selected())[0]["cursor"] == cursor
    baseline, gap = store.snapshot(selected(), initial["cursor"])
    assert baseline["replay_complete"] is False and baseline["entries"] == []
    assert gap["type"] == "gap" and gap["reason"] == "retention"
    # The identity fence survives eviction of the corresponding display body.
    changed = event(record(4), identity="save:1")
    changed["summary"] = "replacement old event"
    with pytest.raises(c.ObservationError) as error:
        store.publish(
            record(4), [changed], batch_id="late-conflict", expected_cursor=cursor, post_save=True
        )
    assert error.value.code == "event-conflict"
    assert original is not None
    store.close()


def test_two_source_generations_do_not_mix_pages_or_infer_retirement(history):
    store, _alive = history
    old = store.initialize(record())
    one = record(1)
    old = store.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=old["cursor"], post_save=True
    )
    changed = record(1, incarnation="record:replacement")
    with pytest.raises(c.ObservationError):
        store.initialize(changed)
    new = store.initialize(changed, expected_source_generation=old["source_generation"])
    assert new["source_generation"] != old["source_generation"]
    archived = store.page(selected(), old["source_generation"], None)
    assert len(archived["entries"]) == 1
    latest, gap = store.snapshot(selected(), old["cursor"])
    assert latest["source_generation"] == new["source_generation"]
    assert gap["reason"] == "source-replaced"
    assert store.snapshot(selected())[0]["cursor"] == new["cursor"]


def test_new_resident_requalifies_explicit_record_before_exposing_durable_cache(history):
    old, _alive = history
    baseline = old.initialize(record())
    one = record(1)
    baseline = old.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=baseline["cursor"], post_save=True
    )
    new = OwnerObservationHistory(
        old.path,
        resident_generation="c" * 32,
        writer_id="d" * 32,
        authority_check=lambda: True,
    )
    new.activate(expected_generation=old.resident_generation)
    with pytest.raises(c.ObservationError) as error:
        new.snapshot(selected())
    assert error.value.code == "unready"
    current = new.initialize(one)
    assert current["resident_generation"] != baseline["resident_generation"]
    assert current["source_generation"] == baseline["source_generation"]
    assert current["cursor"] == baseline["cursor"]
    with pytest.raises(c.ObservationError):
        old.activate(expected_generation=new.resident_generation)
    with pytest.raises(c.ObservationError) as error:
        old.snapshot(selected())
    assert error.value.code == "superseded"
    new.close()


def test_failed_ancillary_append_has_gap_no_fake_complete_or_canonical_rollback(history, tmp_path):
    store, _alive = history
    baseline = store.initialize(record())
    canonical = tmp_path / "canonical.json"
    canonical.write_text(json.dumps(record(1).facts), encoding="utf-8")
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_entries BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'synthetic append failure'); END",
        )
    with pytest.raises(c.ObservationError) as error:
        store.publish(
            record(1),
            [event(record(1))],
            batch_id="batch:1",
            expected_cursor=baseline["cursor"],
            post_save=True,
        )
    assert error.value.code == "journal-write-failed"
    assert json.loads(canonical.read_text()) == record(1).facts
    snapshot, gap = store.snapshot(selected())
    assert snapshot["cursor"] == baseline["cursor"] and snapshot["facts"] is None
    assert snapshot["availability"] == "unavailable" and snapshot["completeness"] == "partial"
    assert gap["reason"] == "journal-write-failed"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM owner_observation_entry").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_entries")
    recovered = store.publish(
        record(1),
        [event(record(1))],
        batch_id="batch:1",
        expected_cursor=baseline["cursor"],
        post_save=True,
    )
    assert recovered["facts"] == record(1).facts and recovered["completeness"] == "partial"


def test_unknown_head_source_and_mutable_inputs_stay_qualified(history):
    store, _alive = history
    unknown = record(head=None)
    snapshot = store.initialize(unknown)
    assert snapshot["availability"] == "unknown" and snapshot["facts"] is None
    # Store capture is detached from caller-owned mutable dictionaries.
    unknown.facts["claims"]["held_count"] = 100
    previous, _cursor, _journal = store.last_record(selected(unknown))
    assert previous.facts["claims"]["held_count"] == 0


def test_private_process_takeover_fences_old_writer_without_service_or_claim_calls(history):
    old, _alive = history
    old.initialize(record())
    script = (
        "from pathlib import Path\n"
        "from agent_worktrees.owner_observation_history import OwnerObservationHistory\n"
        f"store=OwnerObservationHistory(Path({str(old.path)!r}),"
        "resident_generation='c'*32,writer_id='d'*32,authority_check=lambda:True)\n"
        "store.activate(expected_generation='a'*32)\n"
        "print('private-owner-activated')\n"
    )
    result = run_caller_owned(
        [sys.executable, "-B", "-c", script],
        timeout=10,
        max_stdout_bytes=1024,
        max_stderr_bytes=4096,
    )
    assert result.returncode == 0 and "private-owner-activated" in result.stdout
    with pytest.raises(c.ObservationError) as error:
        old.snapshot(selected())
    assert error.value.code == "superseded"


def test_head_change_requires_current_cursor_not_timestamp_guess(history):
    store, _alive = history
    baseline = store.initialize(record())
    one = record(1, head="next-head")
    current = store.publish(
        one, [event(one)], batch_id="batch:1", expected_cursor=baseline["cursor"], post_save=True
    )
    old_selected = store.snapshot(selected())[0]
    assert old_selected["availability"] == "head-changed" and old_selected["facts"] is None
    with pytest.raises(c.ObservationError) as error:
        store.publish(
            record(2),
            [event(record(2))],
            batch_id="late-old-head",
            expected_cursor=baseline["cursor"],
            post_save=True,
        )
    assert error.value.code == "stale-cursor"
    assert store.snapshot(selected(one))[0] == current


def test_postcommit_owner_loss_does_not_acknowledge_old_resident(history, monkeypatch):
    old, _alive = history
    baseline = old.initialize(record())
    original = old._fence
    injected = [False]

    def fence(conn):
        if not conn.in_transaction and not injected[0]:
            injected[0] = True
            with sqlite3.connect(old.path) as other:
                other.execute(
                    "UPDATE owner_observation_owner SET generation=?,writer=? WHERE singleton=1",
                    ("c" * 32, "d" * 32),
                )
        return original(conn)

    monkeypatch.setattr(old, "_fence", fence)
    with pytest.raises(c.ObservationError) as error:
        old.publish(
            record(1),
            [event(record(1))],
            batch_id="batch:1",
            expected_cursor=baseline["cursor"],
            post_save=True,
        )
    assert injected[0] and error.value.code == "superseded"
    new = OwnerObservationHistory(
        old.path,
        resident_generation="c" * 32,
        writer_id="d" * 32,
        authority_check=lambda: True,
    )
    new.activate()
    try:
        with pytest.raises(c.ObservationError):
            new.snapshot(selected())
        assert new.initialize(record(1))["facts"] == record(1).facts
        assert len(new.page(selected(), baseline["source_generation"], None)["entries"]) == 1
    finally:
        new.close()
