"""Private SQLite source materialization, retention and failure semantics."""

from __future__ import annotations

import json
import sqlite3
import sys
from copy import deepcopy
from dataclasses import asdict, replace

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
