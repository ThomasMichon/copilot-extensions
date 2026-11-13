"""Native evidence, current-view separation and existing durable-intent semantics."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest
from _native_receipt_fixtures import (
    current_row,
    observe_direct_claim,
    page,
    publish,
    selection,
    setup,
)

from agent_worktrees import owner_observation_contract as c
from agent_worktrees import owner_observation_native_entries as semantic
from agent_worktrees import tracking, tracking_write
from agent_worktrees.owner_observation_native_intake import MAX_PENDING


@pytest.fixture
def native(tmp_path, monkeypatch):
    value = setup(tmp_path, monkeypatch)
    yield value
    value[0].shutdown()


def compute(owner, record_path, verb="claim_add", **extra):
    return owner.native_compute(
        tracking_write.KIND,
        {
            "verb": verb,
            "args": {
                "yaml_path": str(record_path),
                "worktree_id": "native-one",
                "kind": "codespace",
                "ref": "ordinary-child",
                **extra,
            },
        },
    )


def native_entries(owner):
    return [
        entry for entry in page(owner)["entries"] if entry["evidence_kind"] == "persisted-mutation"
    ]


@pytest.mark.parametrize(
    ("verb", "kind"),
    [
        ("claim_add", "claim-added"),
        ("claim_settle", "claim-settled"),
        ("claim_release", "claim-released"),
    ],
)
def test_real_canonical_claims_materialize_native_entries(native, verb, kind):
    owner, path, _home = native
    if verb != "claim_add":
        assert compute(owner, path)["ok"]
        publish(owner, path)
    assert compute(owner, path, verb, disposition="at-rest")["ok"]
    assert owner.native_observations.counts()["native-receipt-queued"] > 0
    current = publish(owner, path)
    entries = native_entries(owner)
    assert entries[-1]["kind"] == kind
    assert entries[-1]["actor"] == {
        "kind": "unknown",
        "id": None,
        "qualification": "unknown",
        "session_id": None,
    }
    assert entries[-1]["record_fence"]["saved_sha256"]
    assert entries[-1]["source_ref"]["record_id"] != "ordinary-child"
    assert current["completeness"] == "partial"
    assert (
        tracking.load_record(path).resources[0].state
        == {
            "claim_add": "active",
            "claim_settle": "at-rest",
            "claim_release": "released",
        }[verb]
    )


@pytest.mark.parametrize("state", ["active", "at-rest", "released"])
def test_real_canonical_claim_release_remove_retains_typed_local_evidence(native, state):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    if state != "active":
        assert compute(owner, path, "claim_settle", disposition=state)["ok"]
    publish(owner, path)

    result = compute(owner, path, "claim_release", remove=True)
    assert result == {"ok": True, "action": "removed"}
    receipt = owner.native_observations._queue[0].receipt
    assert tracking.load_record(path).resources == []
    current = publish(owner, path)
    entry = native_entries(owner)[-1]
    assert entry["kind"] == "claim-released"
    assert entry["evidence_kind"] == "persisted-mutation"
    assert entry["payload"]["before"]["ref"] == "ordinary-child"
    assert entry["payload"]["before"]["state"] == state
    assert entry["payload"]["after"] is None
    assert entry["record_fence"]["saved_sha256"] == receipt.saved_sha256
    assert entry["actor"]["qualification"] == "unknown"
    assert current["facts"]["row"]["claims_assets"]["held_count"] == 0
    assert current["completeness"] == "partial"
    for other_verb in ("claim_add", "claim_settle"):
        assert semantic.entries(receipt, other_verb)[0]["kind"] == "source-event"


def test_late_history_and_retry_do_not_regress_current_facts(native):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    envelope = owner.native_observations._queue[0]
    compute(owner, path, "status_disposition_write", summary="new current facts")
    publish(owner, path)
    first = native_entries(owner)
    with owner.native_observations._condition:
        owner.native_observations._queue.append(envelope)
        owner.native_observations._bytes += envelope.receipt.encoded_bytes
    compute(owner, path, "status_disposition_write", summary="even newer")
    publish(owner, path)
    current = owner.observation_read(selection())
    assert current["facts"]["row"]["summary"] == "even newer"
    ids = [item["event_id"] for item in native_entries(owner)]
    assert len(ids) == len(set(ids)) and first[0]["event_id"] in ids


def test_qualified_disposition_is_native_not_a_sampled_proxy(native):
    owner, path, _home = native
    result = compute(owner, path, "status_disposition_write", summary="real saved summary")
    assert result["summary"] == "real saved summary"
    publish(owner, path)
    entry = native_entries(owner)[-1]
    assert entry["kind"] == "source-event" and entry["native_kind"] == "disposition-saved"
    assert entry["payload"]["after"]["summary"] == "real saved summary"
    assert entry["evidence_kind"] == "persisted-mutation"


def test_queued_native_claim_preserves_sampled_direct_summary_change(native):
    owner, path, _home = native
    before = owner.observation_read(selection())
    assert compute(owner, path)["ok"]
    receipt = owner.native_observations._queue[0].receipt
    assert receipt.saved_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    record = tracking.load_record(path)
    record.summary = "direct writer after the queued claim"
    tracking.save_record(record, path)
    assert owner.native_observations.counts()["native-receipt-queued"] == 1
    assert receipt.saved_sha256 != hashlib.sha256(path.read_bytes()).hexdigest()

    current = publish(owner, path)
    entries = page(owner)["entries"]
    sampled = [entry for entry in entries if entry["kind"] == "observed-change"]
    assert len(sampled) == 1
    assert sampled[0]["payload"]["before_sha256"] == hashlib.sha256(
        c.utf8(c.encode(before["facts"], c.BODY_BYTES))
    ).hexdigest()
    assert sampled[0]["payload"]["after_sha256"] == hashlib.sha256(
        c.utf8(c.encode(current["facts"], c.BODY_BYTES))
    ).hexdigest()
    assert current["facts"]["row"]["summary"] == record.summary
    assert current["facts"]["row"]["claims_assets"]["held_count"] == 1
    persisted = native_entries(owner)
    assert len(persisted) == 1 and persisted[0]["kind"] == "claim-added"
    assert persisted[0]["record_fence"] == {
        "saved_sha256": receipt.saved_sha256,
        "incarnation": receipt.after.identity.incarnation,
    }
    materialized, _cursor, _journal = owner.observations.runtime.history.last_record(
        c.Selection("native-test", "native-one", "head")
    )
    assert materialized.facts == current["facts"]
    assert materialized.record_fence == sampled[0]["record_fence"]
    assert materialized.record_fence != persisted[0]["record_fence"]
    publish(owner, path)
    assert page(owner)["entries"] == entries


@pytest.mark.parametrize("invalid", ["partial", "degraded", "producer", "incarnation"])
def test_unqualified_evidence_cannot_mint_native_transitions(native, invalid):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    envelope = owner.native_observations._queue.popleft()
    owner.native_observations._bytes = 0
    receipt = envelope.receipt
    if invalid == "partial":
        receipt = replace(receipt, coverage="partial")
    elif invalid == "degraded":
        receipt = replace(receipt, exclusion="in-process")
    elif invalid == "producer":
        state = receipt.after
        import json

        data = state.domains[0].value()
        data[0]["has_producer"] = True
        domain = replace(state.domains[0], data=json.dumps(data).encode())
        receipt = replace(receipt, after=replace(state, domains=(domain, *state.domains[1:])))
    else:
        receipt = replace(
            receipt,
            after=replace(
                receipt.after,
                identity=replace(
                    receipt.after.identity,
                    incarnation="foreign",
                ),
            ),
        )
    owner.native_observations._queue.append(replace(envelope, receipt=receipt))
    owner.native_observations._bytes = receipt.encoded_bytes
    publish(owner, path)
    assert native_entries(owner) == []
    assert owner.observation_read(selection())["facts"] is not None


def test_same_generation_reactivation_retires_only_old_native_work(native):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    owner.activate()
    publish(owner, path)
    assert native_entries(owner) == []
    assert owner.observation_read(selection())["type"] == "snapshot"


def test_index_failure_is_not_canonical_failure_and_intent_remains_honest(native):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_native BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'native materialization failure'); END"
        )
    publish(owner, path)
    assert tracking.load_record(path).resources[0].ref == "ordinary-child"
    assert owner.native_observations.status()["error"] is not None
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute("DROP TRIGGER reject_native")
    publish(owner, path)
    assert native_entries(owner)[-1]["kind"] == "claim-added"
    assert owner.observation_read(selection())["completeness"] == "partial"


def test_canonical_error_after_an_actual_save_does_not_discard_its_receipt(native, monkeypatch):
    owner, path, _home = native
    tracking_write.registered_verbs()
    actual = tracking_write._VERBS["status_disposition_write"]

    def transaction(args):
        actual(args)
        raise OSError("failure after canonical persistence")

    monkeypatch.setitem(tracking_write._VERBS, "status_disposition_write", transaction)
    with pytest.raises(OSError):
        compute(owner, path, "status_disposition_write", summary="committed first")
    publish(owner, path)
    assert native_entries(owner)[-1]["payload"]["after"]["summary"] == "committed first"


@pytest.mark.parametrize("count", [0, 2, MAX_PENDING + 2])
def test_multiple_saves_one_compute_and_queue_overflow_remain_partial(native, monkeypatch, count):
    owner, path, _home = native
    before = path.read_bytes()
    tracking_write.registered_verbs()
    actual = tracking_write._VERBS["status_disposition_write"]

    def many(args):
        for number in range(count):
            actual({**args, "summary": str(number)})
        return {"ok": True}

    monkeypatch.setitem(tracking_write._VERBS, "status_disposition_write", many)
    assert compute(owner, path, "status_disposition_write")["ok"]
    assert owner.native_observations.counts()["native-receipt-queued"] <= MAX_PENDING
    assert (owner.native_observations.status()["omitted"] > 0) == (count > MAX_PENDING)
    assert tracking.load_record(path).summary == (str(count - 1) if count else "")
    assert count or path.read_bytes() == before
    publish(owner, path)
    assert min(count, 2) <= len(native_entries(owner)) <= min(count, MAX_PENDING)


def test_original_publish_still_refuses_native_fence_substitution(native):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    envelope = owner.native_observations._queue[0]
    values = semantic.entries(envelope.receipt, envelope.context.verb)
    runtime = owner.observations.runtime
    record, cursor, _journal = runtime.history.last_record(
        c.Selection("native-test", "native-one", "head")
    )
    with pytest.raises(c.ObservationError, match="does not match"):
        runtime.publish(
            record, values, batch_id="not-native", expected_cursor=cursor, post_save=True
        )


def test_same_canonical_effort_transactions_produce_native_bind_and_release(native):
    from test_effort_focus import _effort

    owner, path, _home = native
    root = tracking.load_record(path).worktree_path
    from pathlib import Path

    relative = _effort(Path(root))
    result = compute(
        owner,
        path,
        "effort_bind",
        repo_root=root,
        path=relative,
        participant="Driver",
        slice="Phase 2 - Bind active effort",
        replace=False,
    )
    assert result["ok"], result
    publish(owner, path)
    assert native_entries(owner)[-1]["kind"] == "effort-bound"
    bound = tracking.load_record(path).active_effort
    result = compute(
        owner,
        path,
        "effort_release",
        repo_root=root,
        bound=bound.to_dict(),
        completed=False,
        transfer="next-owner",
    )
    assert result["ok"], result
    publish(owner, path)
    assert native_entries(owner)[-1]["kind"] == "effort-released"
    assert tracking.load_record(path).active_effort is None


def test_capture_head_is_not_rewritten_as_current_head_or_actor(native):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    record = tracking.load_record(path)
    record.sessions.append(tracking.SessionEntry("next-head", "2026-11-02T01:00:00Z"))
    record.head_session = "next-head"
    tracking.save_record(record, path)
    publish(owner, path)
    frames = page(owner)
    entry = next(
        item for item in frames["entries"] if item["evidence_kind"] == "persisted-mutation"
    )
    assert frames["head_session_id"] == "next-head"
    assert entry["head_session_id"] == "head"
    assert entry["actor"]["id"] is None and entry["actor"]["session_id"] is None
    assert entry["payload"]["capture_head_session_id"] == "head"


def test_replaced_record_cannot_adopt_an_old_receipt(native, overwrite_record_nonce):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    overwrite_record_nonce(path, "new-record-incarnation")
    publish(owner, path)
    assert native_entries(owner) == []
    assert owner.observation_read(selection())["completeness"] == "partial"


def test_conflicting_native_retry_never_invalidates_current_facts(native):
    import json

    owner, path, _home = native
    assert compute(owner, path)["ok"]
    envelope = owner.native_observations._queue[0]
    publish(owner, path)
    before = owner.observation_read(selection())
    state = envelope.receipt.after
    data = state.domains[0].value()
    data[0]["note"] = "conflicting retry content"
    domain = replace(state.domains[0], data=json.dumps(data).encode())
    changed = replace(envelope.receipt, after=replace(state, domains=(domain, *state.domains[1:])))
    owner.native_observations._queue.append(replace(envelope, receipt=changed))
    owner.native_observations._bytes = changed.encoded_bytes
    publish(owner, path)
    assert owner.observation_read(selection())["facts"] == before["facts"]
    assert len(native_entries(owner)) == 1
    assert owner.native_observations.counts()["native-receipt-queued"] == 0


def test_native_close_discards_only_notification_work_not_canonical_effects(native):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    owner.close_admission("draining")
    assert owner.native_observations.wait_closed(0.1)
    assert tracking.load_record(path).resources[0].state == "active"
    assert owner.native_observations.status()["omitted"] > 0
    assert owner.native_observations.counts()["native-receipt-queued"] == 0


def test_native_writer_fence_does_not_retire_admitted_reader_runtime(native, monkeypatch):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    runtime = owner.observations.runtime
    monkeypatch.setattr(owner, "_admitted", lambda: True)

    def fence(token):
        if token.kind == "publish":
            monkeypatch.setattr(owner, "_elected", lambda: False)

    runtime.history._after_intent = fence
    publish(owner, path)
    assert runtime._closed is None
    assert owner.observations._closed is None
    assert "not elected" in owner.native_observations.status()["error"]
    assert tracking.load_record(path).resources[0].state == "active"
    with sqlite3.connect(owner.store.db_path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM owner_observation_intent").fetchone()[0] > 0
        )


def test_old_producer_cannot_resolve_or_append_after_actual_private_owner_takeover(
    native, monkeypatch
):
    from agent_worktrees.worktrees_runtime import WorktreesRuntime

    owner, path, _home = native
    assert compute(owner, path)["ok"]
    successor = WorktreesRuntime(owner.store.db_path)
    monkeypatch.setattr(successor.agendas, "roster_changed", lambda project: None)
    try:
        successor.activate()
        owner.native_observations.accepted("native-test")
        successor.observation_read(selection())
        publish(successor, path)
        assert native_entries(successor) == []
        assert successor.observation_read(selection())["completeness"] == "partial"
        assert tracking.load_record(path).resources[0].state == "active"
    finally:
        successor.shutdown()


def test_reordered_receipts_append_history_without_reinstalling_their_old_facts(native):
    owner, path, _home = native
    assert compute(owner, path, ref="first-child")["ok"]
    assert compute(owner, path, ref="second-child")["ok"]
    owner.native_observations._queue.reverse()
    publish(owner, path)
    entries = native_entries(owner)
    assert len(entries) == 2 and len({entry["event_id"] for entry in entries}) == 2
    assert entries[0]["payload"]["after"]["ref"] == "second-child"
    assert entries[1]["payload"]["after"]["ref"] == "first-child"
    assert owner.observation_read(selection())["facts"]["row"]["claims_assets"]["held_count"] == 2


def test_cancelled_observer_cannot_cancel_or_replay_canonical_native_work(native):
    from agent_worktrees.owner_observation_transport import FEED_ACTION

    owner, path, _home = native
    stopped = [False]
    stream = owner.stream(FEED_ACTION, selection(), lambda: stopped[0])
    assert next(stream)["type"] == "snapshot"
    assert compute(owner, path)["ok"]
    stopped[0] = True
    stream.close()
    assert owner.observations.counts()["streams"] == 0
    assert owner.native_observations.counts()["native-receipt-queued"] == 1
    publish(owner, path)
    assert len(native_entries(owner)) == 1
    assert len(tracking.load_record(path).resources) == 1


def test_canonical_identity_rejection_still_writes_nothing_and_captures_nothing(native):
    import yaml

    owner, path, _home = native
    data = yaml.safe_load(path.read_text())
    data["worktree_id"] = "foreign-subject"
    path.write_text(yaml.safe_dump(data))
    before = path.read_bytes()
    result = compute(owner, path)
    assert result["error"] == "rejected"
    assert path.read_bytes() == before
    assert owner.native_observations.counts()["native-receipt-queued"] == 0


def test_reentrant_direct_save_of_b_cannot_borrow_a_native_admission(native, caplog):
    owner, path, _home = native
    other_path = path.with_name("native-two.yaml")
    other = tracking.load_record(path)
    other.worktree_id, other.creation_nonce = "native-two", "second-incarnation"
    tracking.save_record(other, other_path)
    other_selection = {**selection(), "worktree_id": "native-two"}
    owner.observation_read(other_selection)
    owner.publish("native-test", [current_row(path), current_row(other_path)], observed_at=2)

    unsubscribe = observe_direct_claim(other_path)
    try:
        assert compute(owner, path)["ok"]
    finally:
        unsubscribe()
    assert tracking.load_record(other_path).resources[0].ref == "direct-B-child"
    owner.publish("native-test", [current_row(path), current_row(other_path)], observed_at=3)
    current = owner.observation_read(other_selection)
    query = {key: value for key, value in other_selection.items() if key != "after_cursor"}
    query.update(source_generation=current["source_generation"], before_cursor=None, limit=64)
    actual = owner.observation_history(query)
    assert not [
        item for item in actual["entries"] if item["evidence_kind"] == "persisted-mutation"
    ]
    assert len(native_entries(owner)) == 1
    assert owner.native_observations.status()["omitted"] > 0
    assert "admitted record" in caplog.text


def test_admitted_record_id_alias_cannot_qualify_the_saved_full_identity(native):
    owner, path, _home = native
    assert compute(owner, path, worktree_id="one")["ok"]
    publish(owner, path)
    assert tracking.load_record(path).resources[0].state == "active"
    assert native_entries(owner) == []
    assert owner.native_observations.status()["omitted"] > 0


def test_materialization_rechecks_the_admitted_record_path(native):
    owner, path, _home = native
    assert compute(owner, path)["ok"]
    envelope = owner.native_observations._queue.popleft()
    owner.native_observations._bytes = 0
    wrong = replace(envelope.context, path=str(path.with_name("not-admitted.yaml")))
    owner.native_observations._queue.append(replace(envelope, context=wrong))
    owner.native_observations._bytes = envelope.receipt.encoded_bytes
    publish(owner, path)
    assert native_entries(owner) == []
    assert owner.observation_read(selection())["facts"] is not None


@pytest.mark.parametrize("mode", ["nested", "deferred"])
def test_uncertain_nested_or_deferred_compute_never_borrows_admission(native, monkeypatch, mode):
    owner, path, _home = native
    tracking_write.registered_verbs()
    actual = tracking_write._VERBS["status_disposition_write"]

    def nested(args):
        actual(args)
        return compute(owner, path)

    if mode == "nested":
        monkeypatch.setitem(tracking_write._VERBS, "status_disposition_write", nested)
        assert compute(owner, path, "status_disposition_write", summary="saved outer")["ok"]
        assert tracking.load_record(path).summary == "saved outer"
    else:
        with tracking._RecordLock(path, require_sidecar=True):
            assert compute(owner, path)["ok"]
    assert tracking.load_record(path).resources[0].state == "active"
    publish(owner, path)
    assert native_entries(owner) == []
    assert owner.native_observations.status()["omitted"] > 0
