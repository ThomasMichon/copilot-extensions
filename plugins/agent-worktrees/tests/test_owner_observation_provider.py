"""Selected resident snapshots, not canonical mutation or agenda file history."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_worktrees import owner_observation_contract as c
from agent_worktrees import config as cfg
from agent_worktrees import owner_observation_transport as wire
from agent_worktrees.owner_observation_history import OwnerObservationHistory
from agent_worktrees.worktrees_projection_index import ProjectionError, ProjectionUnavailable
from agent_worktrees.worktrees_runtime import WorktreesRuntime


def request(head="head", worktree="full-worktree", project="project", after=None):
    return {
        "contract_version": 1,
        "project": project,
        "worktree_id": worktree,
        "expected_head_session_id": head,
        "after_cursor": after,
    }


def row(title="current", head="head", nonce="record-one", count=1, **extra):
    return {
        "id": "full-worktree",
        "repo": "project",
        "creation_nonce": nonce,
        "last_session_id": head,
        "session_count": 1,
        "title": title,
        "status": "active",
        "claims_assets": {"held_count": count, "by_kind": {"worktree": count}},
        "fact_provenance": {"record": {"quality": "current", "observed_at": 1.0}},
        **extra,
    }


@pytest.fixture
def owner(tmp_path, monkeypatch):
    runtime = WorktreesRuntime(tmp_path / "resident.sqlite3", wake=threading.Event())
    # Agenda's separate canonical-head path is outside this provider fixture.
    monkeypatch.setattr(runtime.agendas, "roster_changed", lambda project: None)
    runtime.activate()
    runtime.observations.runtime.heartbeat = 0.01
    yield runtime
    runtime.shutdown()


def enroll(owner, **values):
    assert owner.observation_read(request())["error"]["code"] == "unready"
    owner.publish("project", [row(**values)], observed_at=1)
    result = owner.observation_read(request())
    assert result["type"] == "snapshot"
    return result


def history(owner, snapshot, before=None, limit=64):
    payload = request()
    payload.pop("after_cursor")
    payload.update(
        source_generation=snapshot["source_generation"], before_cursor=before, limit=limit
    )
    return owner.observation_history(payload)


def test_capability_is_running_receiver_and_selected_demand_not_package_or_read_compute(owner):
    assert set(wire.CAPABILITIES) <= set(owner.health()["capabilities"])
    assert owner.health()["worktree_observations"]["status"] == "ready"
    assert owner.health()["worktree_observations"]["generation"] == owner.store.generation
    owner.publish("project", [row()], observed_at=1)
    assert owner.observation_read(request())["error"]["code"] == "unready"
    assert "project" in owner.demand_projects()
    with sqlite3.connect(owner.store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM owner_observation_journal").fetchone()[0] == 0
    owner.publish("project", [row()], observed_at=2)
    current = owner.observation_read(request())
    assert current["facts"]["row"]["title"] == "current"
    assert current["completeness"] == "partial"
    assert current["resident_generation"] == owner.store.generation


def test_receiver_heartbeat_default_fits_the_passive_client_idle_budget(tmp_path):
    runtime = WorktreesRuntime(tmp_path / "resident.sqlite3")
    try:
        runtime.activate()
        assert runtime.observations.runtime is not None
        assert runtime.observations.runtime.heartbeat == wire.HEARTBEAT_SECONDS
        assert 0 < wire.HEARTBEAT_SECONDS <= 1
        health = runtime.health()
        assert health["worktree_observations"]["generation"] == health["worktrees"]["generation"]
    finally:
        runtime.shutdown()


def test_observation_identity_never_adopts_a_unique_roster_suffix(owner):
    initial = enroll(owner)
    legacy = owner.store.read("project", worktree_id="worktree", contract_version=2)
    assert legacy["worktrees"][0]["id"] == "full-worktree"
    alias = request(worktree="worktree")
    assert owner.observation_read(alias)["error"]["code"] == "unready"
    owner.publish("project", [row()], observed_at=2)
    result = owner.observation_read(alias)
    assert result["type"] == "error" and result["error"]["code"] == "not-found"
    payload = {key: value for key, value in alias.items() if key != "after_cursor"}
    payload.update(source_generation=initial["source_generation"], before_cursor=None, limit=64)
    assert owner.observation_history(payload)["error"]["code"] == "not-found"
    stream = owner.stream(wire.FEED_ACTION, alias, lambda: False)
    try:
        assert next(stream)["error"]["code"] == "not-found"
    finally:
        stream.close()
    with sqlite3.connect(owner.store.db_path) as conn:
        assert conn.execute(
            "SELECT count(*) FROM owner_observation_journal WHERE worktree=?", ("worktree",)
        ).fetchone()[0] == 0
    assert owner.observation_read(request())["source_generation"] == initial["source_generation"]


def test_exact_observation_identity_survives_another_id_with_the_same_suffix(owner):
    initial = enroll(owner)
    owner.publish(
        "project", [row(), row(id="prefix-full-worktree", nonce="another-record")],
        observed_at=2,
    )
    current = owner.observation_read(request())
    assert current["type"] == "snapshot"
    assert current["worktree_id"] == current["facts"]["row"]["id"] == "full-worktree"
    assert current["source_generation"] == initial["source_generation"]


def test_postcommit_observed_changes_are_detached_and_never_claim_events(owner):
    initial = enroll(owner)
    supplied = row(title="\u6f22\U0001f680", count=2, active_effort={"slug": "one", "slice": "S2"})
    owner.publish("project", [supplied], observed_at=2)
    supplied["claims_assets"]["held_count"] = 999
    current = owner.observation_read(request())
    assert current["facts"]["row"]["claims_assets"]["held_count"] == 2
    assert current["cursor"] != initial["cursor"]
    page = history(owner, initial)
    assert page["completeness"] == "partial" and len(page["entries"]) == 1
    entry = page["entries"][0]
    assert entry["kind"] == entry["evidence_kind"] == "observed-change"
    assert entry["actor"]["qualification"] == "unknown"
    assert entry["occurred_at"] is None
    assert entry["payload"]["facts_omitted"] is True
    assert (
        entry["payload"]["after_sha256"]
        == hashlib.sha256(c.utf8(c.encode(current["facts"]))).hexdigest()
    )
    assert current["facts"]["row"]["title"] == "\u6f22\U0001f680"
    c.validate_frame(current)
    c.validate_frame(page)


def test_timestamp_only_refresh_and_heartbeats_do_not_advance_event_cursor(owner):
    initial = enroll(owner, active_effort={"slug": "one", "updated_at": "2026-11-02T00:00:01Z"})
    selected = c.Selection("project", "full-worktree", "head")
    fence = owner.observations.runtime.history.last_record(selected)[0].record_fence
    stream = owner.stream(wire.FEED_ACTION, request(), lambda: False)
    try:
        assert next(stream) == initial
        refresh = row(active_effort={"slug": "one", "updated_at": "2026-11-02T00:00:02Z"})
        refresh["fact_provenance"]["record"]["observed_at"] = 2.0
        refresh.update(activity_at="2026-11-02T00:00:02Z", status_note_at="2026-11-02T00:00:02Z")
        owner.publish("project", [refresh], observed_at=2)
        frame = next(stream)
        assert frame["type"] == "snapshot" and frame["cursor"] == initial["cursor"]
        assert frame["observed_at"] != initial["observed_at"]
        for _ in range(3):
            heartbeat = next(stream)
            assert heartbeat["type"] == "heartbeat" and heartbeat["cursor"] == initial["cursor"]
        assert history(owner, initial)["entries"] == []
        assert owner.observations.runtime.history.last_record(selected)[0].record_fence == fence
    finally:
        stream.close()


@pytest.mark.parametrize("versioned", [False, True])
def test_reporter_naming_is_neither_observation_facts_nor_record_incarnation(owner, versioned):
    def naming(name):
        return {
            "machine": name,
            **({
                "machine_identity_version": 1, "environment": "wsl", "platform": "wsl",
                "execution_machine": name + "-slot",
            } if versioned else {}),
        }

    initial = enroll(owner, **naming("original"))
    owner.publish("project", [row(title="meaningful change", **naming("original"))], observed_at=2)
    before = owner.observation_read(request())
    retained = deepcopy(history(owner, initial)["entries"])
    assert len(retained) == 1
    for at, names in ((3, naming("renamed")), (4, {})):
        owner.publish("project", [row(title="meaningful change", **names)], observed_at=at)
        current = owner.observation_read(request())
        assert current["source_generation"] == initial["source_generation"]
        assert current["cursor"] == before["cursor"]
        assert history(owner, initial)["entries"] == retained
        assert not {
            "machine", "platform", "environment", "execution_machine", "machine_identity_version",
        } & current["facts"]["row"].keys()
    owner.publish("project", [row(title="meaningful change", nonce="record-two")], observed_at=5)
    assert owner.observation_read(request())["source_generation"] != initial["source_generation"]


@pytest.mark.parametrize("change", ["head", "source", "removed", "publication-failed"])
def test_scope_changes_never_keep_old_active_facts(owner, change):
    initial = enroll(owner)
    stream = owner.stream(wire.FEED_ACTION, request(), lambda: False)
    try:
        assert next(stream)["facts"] is not None
        if change == "head":
            owner.publish("project", [row(head="next-head")], observed_at=2)
            value = next(stream)
            assert value["availability"] == "head-changed" and value["facts"] is None
            assert next(stream)["reason"] == "head-changed"
        elif change == "source":
            owner.publish("project", [row(nonce="record-two")], observed_at=2)
            assert next(stream)["type"] == "gap"
            value = next(stream)
            assert value["type"] == "snapshot"
            assert value["source_generation"] != initial["source_generation"]
        else:
            if change == "removed":
                owner.publish("project", [], observed_at=2)
            else:
                owner.publication_failed("project", OSError("private source unavailable"))
            terminal = next(stream)
            assert terminal["type"] == "going-away"
            assert terminal["reason"] == ("not-found" if change == "removed" else "unavailable")
            assert owner.observation_read(request())["type"] == "error"
    finally:
        stream.close()
    assert owner.observations.counts()["streams"] == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"head_yielded": True},
        {"execution_leg": {"state": "active", "blob": {"session_id": "head"}}},
        {"execution_leg": {"opaque": True, "state": "unknown"}},
        {"fact_provenance": None},
    ],
)
def test_resume_or_unqualified_head_is_explicit_unknown_not_label_authority(owner, extra):
    initial = enroll(owner, **extra)
    assert initial["head_session_id"] is None
    assert initial["availability"] == "unknown" and initial["facts"] is None


def test_missing_incarnation_is_not_a_fabricated_observed_source(owner):
    owner.observation_read(request())
    value = row()
    value.pop("creation_nonce")
    owner.publish("project", [value], observed_at=1)
    assert owner.observation_read(request())["error"]["code"] == "unqualified-source"
    assert owner.handle({"contract_version": 1, "project": "project"})["ok"]


def test_optional_activation_failure_leaves_roster_and_current_path_usable(
    tmp_path, monkeypatch, caplog
):
    def failed(*args, **kwargs):
        raise OSError("private optional activation failure")

    monkeypatch.setattr(OwnerObservationHistory, "activate", failed)
    runtime = WorktreesRuntime(tmp_path / "resident.sqlite3")
    try:
        runtime.activate()
        runtime.publish("project", [{"id": "ordinary"}], observed_at=1)
        assert runtime.handle({"contract_version": 1, "project": "project"})["worktrees"] == [
            {"id": "ordinary", "platform": cfg.detect_platform()}
        ]
        assert runtime.observation_read(request())["error"]["code"] == "unavailable"
        assert not set(wire.CAPABILITIES) & set(runtime.health()["capabilities"])
        assert runtime.health()["worktrees"]["status"] == "ready"
        assert runtime.health()["worktree_observations"]["generation"] == runtime.store.generation
        assert "optional worktree observation index unavailable" in caplog.text
    finally:
        runtime.shutdown()


def test_index_failure_after_roster_commit_is_optional_not_false_roster_failure(owner):
    initial = enroll(owner)
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_optional BEFORE INSERT ON owner_observation_entry "
            "BEGIN SELECT RAISE(ABORT,'optional index failure'); END"
        )
    owner.publish("project", [row(title="new")], observed_at=2)
    roster = owner.handle({"contract_version": 1, "project": "project"})
    assert roster["ok"] and roster["worktrees"][0]["title"] == "new"
    assert owner.observation_read(request())["type"] == "error"
    assert initial["facts"]["row"]["title"] == "current"


def test_unrepresentable_selected_facts_do_not_block_or_truncate_ordinary_roster(owner):
    enroll(owner)
    value = row(active_effort={"items": ["x" * 3500] * 9})
    owner.publish("project", [value], observed_at=2)
    result = owner.observation_read(request())
    assert result["type"] == "error" and result["error"]["code"] == "oversized"
    roster = owner.handle({"contract_version": 1, "project": "project"})
    assert roster["ok"] and roster["worktrees"][0]["active_effort"] == value["active_effort"]


def test_rejected_roster_transaction_does_not_publish_observation_history(owner):
    initial = enroll(owner)
    with pytest.raises(ProjectionError, match="duplicate current worktree identity"):
        owner.publish("project", [row(), row()], observed_at=2)
    assert owner.observation_read(request()) == initial
    assert history(owner, initial)["entries"] == []


def test_source_change_between_intent_and_update_cannot_ack_old_capture(owner):
    enroll(owner)
    runtime = owner.observations.runtime

    def race(token):
        owner.store.publish("project", [row(title="raced", head="new-head")], observed_at=3)

    runtime.history._after_intent = race
    owner.publish("project", [row(title="pending")], observed_at=2)
    assert owner.observation_read(request())["type"] == "error"
    runtime.history._after_intent = None
    owner.publish("project", [row(title="raced", head="new-head")], observed_at=4)
    current = owner.observation_read(request(head="new-head"))
    assert current["facts"]["row"]["title"] == "raced"
    assert current["head_session_id"] == "new-head"
    assert current["completeness"] == "partial"


def test_point_read_does_not_ack_a_changed_head_across_its_snapshot(owner, monkeypatch):
    enroll(owner)
    runtime = owner.observations.runtime
    original = runtime.history.snapshot
    raced = [False]

    def snapshot(*args, **kwargs):
        result = original(*args, **kwargs)
        if not raced[0]:
            raced[0] = True
            owner.store.publish("project", [row(head="next-head")], observed_at=2)
            owner.observations.accepted("project")
        return result

    monkeypatch.setattr(runtime.history, "snapshot", snapshot)
    result = owner.observation_read(request())
    assert result["type"] == "error" and result["error"]["code"] == "stale-source"
    assert owner.observation_read(request(head="next-head"))["head_session_id"] == "next-head"


def test_publication_barrier_is_counted_until_optional_qualification_finishes(owner):
    enroll(owner)
    server = SimpleNamespace(current_read_count=lambda action: 0)
    with owner.observations.publication("project"):
        assert owner.observation_counts(server)["worktree-observations-publication"] == 1
        assert owner.observation_read(request())["error"]["code"] == "unready"
        with pytest.raises(c.ObservationError):
            owner.observation_wait_closed(0)
    assert owner.observation_counts(server)["worktree-observations-publication"] == 0


def test_takeover_requires_new_accepted_rows_and_old_owner_cannot_publish(owner, monkeypatch):
    old = enroll(owner)
    successor = WorktreesRuntime(owner.store.db_path)
    monkeypatch.setattr(successor.agendas, "roster_changed", lambda project: None)
    try:
        successor.activate()
        assert owner.observation_read(request())["error"]["code"] == "superseded"
        assert successor.observation_read(request())["error"]["code"] == "unready"
        successor.publish("project", [row(title="new owner")], observed_at=2)
        current = successor.observation_read(request())
        assert current["resident_generation"] != old["resident_generation"]
        assert current["facts"]["row"]["title"] == "new owner"
        assert current["completeness"] == "partial"
    finally:
        successor.shutdown()


def test_history_is_point_only_and_selection_is_exact(owner):
    initial = enroll(owner)
    assert owner.observation_read(request(worktree="worktree"))["error"]["code"] == "unready"
    frames = list(owner.stream(wire.HISTORY_ACTION, request(), lambda: False))
    assert frames[0]["error"]["code"] == "unsupported-action"
    page = history(owner, initial)
    assert page["type"] == "history-page"
    invalid = {**request(), "source_generation": initial["source_generation"]}
    assert owner.observation_history(invalid)["error"]["code"] == "invalid-request"


def test_subscriber_bound_cancellation_and_drain_counts_are_owned(owner):
    enroll(owner)
    streams = []
    server = SimpleNamespace(current_read_count=lambda action: 0)
    try:
        for _ in range(32):
            stream = owner.stream(wire.FEED_ACTION, request(), lambda: False)
            assert next(stream)["type"] == "snapshot"
            streams.append(stream)
        rejected = owner.stream(wire.FEED_ACTION, request(), lambda: False)
        assert next(rejected)["error"]["code"] == "subscriber-limit"
        rejected.close()
        assert owner.observation_counts(server)[wire.FEED_ACTION] == 32
        with pytest.raises(c.ObservationError):
            owner.observation_wait_closed(0)
        owner.close_admission("draining")
        for stream in streams:
            assert next(stream)["reason"] == "draining"
            stream.close()
        owner.observation_wait_closed(0.1)
        assert not owner.observation_busy(server)
        assert owner.observation_counts(server)["worktree-observations-demand"] == 0
    finally:
        for stream in streams:
            stream.close()


def test_history_pagination_and_retention_keep_contract_bounds(owner):
    initial = enroll(owner)
    for number in range(1, 5):
        owner.publish("project", [row(title=str(number))], observed_at=number + 1)
    current = owner.observation_read(request())
    last = history(owner, current, limit=2)
    assert len(last["entries"]) == 2 and last["has_more"]
    first = history(owner, current, before=last["next_before"], limit=2)
    ids = [item["event_id"] for item in first["entries"] + last["entries"]]
    assert len(ids) == len(set(ids)) == 4 and not first["has_more"]
    assert last["cursor"] == current["cursor"] != initial["cursor"]
    assert len(wire.encode_result(last)) <= wire.POINT_BYTES


def test_selected_facts_ignore_timestamp_fields_but_keep_typed_content(owner):
    initial = enroll(owner, active_effort={"value": True})
    for value in (1, 1.0):
        owner.publish("project", [row(active_effort={"value": value})], observed_at=2)
    page = history(owner, initial)
    assert len(page["entries"]) == 2
    assert all(item["kind"] == "observed-change" for item in page["entries"])


def test_publication_reads_owned_copies_not_mutated_caller_rows(owner):
    assert owner.observation_read(request())["type"] == "error"
    original = row(active_effort={"slice": ["first"]})
    expected = deepcopy(original)
    owner.publish("project", [original], observed_at=1)
    original["active_effort"]["slice"].append("caller edit")
    assert (
        owner.observation_read(request())["facts"]["row"]["active_effort"]
        == expected["active_effort"]
    )


def test_provider_does_not_read_canonical_records_or_take_record_locks(owner, monkeypatch):
    from agent_worktrees import tracking

    def forbidden(*args, **kwargs):
        pytest.fail("optional roster observations touched canonical tracking")

    monkeypatch.setattr(tracking, "load_record", forbidden)
    monkeypatch.setattr(tracking, "list_records", forbidden)
    monkeypatch.setattr(tracking, "_RecordLock", forbidden)
    initial = enroll(owner)
    owner.publish("project", [row(count=2)], observed_at=2)
    assert owner.observation_read(request())["facts"]["row"]["claims_assets"]["held_count"] == 2
    assert history(owner, initial)["entries"][0]["kind"] == "observed-change"


def test_passive_health_and_reads_do_not_vouch_for_lost_election(owner, monkeypatch):
    enroll(owner)
    monkeypatch.setattr(owner, "_elected", lambda: False)
    assert owner.observation_status()["status"] == "superseded"
    assert not set(wire.CAPABILITIES) & set(owner.health()["capabilities"])
    assert owner.observation_read(request())["error"]["code"] == "superseded"


def test_writer_fence_preserves_admitted_observation_reads_without_new_writes(owner, monkeypatch):
    initial = enroll(owner)
    owner.publish("project", [row(title="accepted")], observed_at=2)
    current = owner.observation_read(request())
    page = history(owner, initial)
    stream = owner.stream(wire.FEED_ACTION, request(), lambda: False)
    try:
        assert next(stream) == current
        monkeypatch.setattr(owner, "_admitted", lambda: True)
        monkeypatch.setattr(owner, "_elected", lambda: False)
        for _ in range(2):
            with pytest.raises(ProjectionUnavailable, match="no longer elected"):
                owner.publish("project", [row(title="not accepted")], observed_at=3)
            assert owner.observation_status()["status"] == "ready"
            assert owner.observation_read(request()) == current
            assert history(owner, initial) == page
            assert next(stream)["type"] == "heartbeat"
        selected = c.Selection("project", "full-worktree", "head")
        record = owner.observations.runtime.history.last_record(selected)[0]
        with pytest.raises(c.ObservationError, match="not elected"):
            owner.observations.runtime.initialize(record)
        monkeypatch.setattr(owner, "_admitted", lambda: False)
        assert next(stream)["reason"] == "superseded"
        assert owner.observation_read(request())["error"]["code"] == "superseded"
    finally:
        stream.close()


@pytest.mark.parametrize("operation", ["initialize", "publish"])
def test_mid_materialization_writer_loss_preserves_unrelated_admitted_readers(
    owner, monkeypatch, operation,
):
    election = {"writer": True, "reader": True}
    monkeypatch.setattr(owner, "_elected", lambda: election["writer"])
    monkeypatch.setattr(owner, "_admitted", lambda: election["reader"])
    enroll(owner)
    if operation == "initialize":
        target, unrelated = "new-worktree", "full-worktree"
    else:
        target, unrelated = "full-worktree", "other-worktree"
    owner.observation_read(request(worktree=target))
    owner.observation_read(request(worktree=unrelated))
    rows = [row(), row(id=unrelated)] if operation == "publish" else [row()]
    owner.publish("project", rows, observed_at=2)
    unaffected = owner.observation_read(request(worktree=unrelated))
    assert unaffected["type"] == "snapshot"
    page_payload = {
        **{key: value for key, value in request(worktree=unrelated).items() if key != "after_cursor"},
        "source_generation": unaffected["source_generation"],
        "before_cursor": None, "limit": 64,
    }
    page = owner.observation_history(page_payload)
    stream = owner.stream(wire.FEED_ACTION, request(worktree=unrelated), lambda: False)
    checkpoints = []

    def lose_writer(token):
        if token.worktree == target:
            checkpoints.append(token.kind)
            election["writer"] = False

    owner.observations.runtime.history._after_intent = lose_writer
    try:
        assert next(stream) == unaffected
        supplied = (
            [row(), row(id=target, title="new")]
            if operation == "initialize"
            else [row(title="new"), row(id=unrelated)]
        )
        owner.publish("project", supplied, observed_at=3)
        assert checkpoints == [operation]
        assert owner.observation_status()["status"] == "ready"
        assert owner.observation_read(request(worktree=target))["error"]["code"] == "writer-fenced"
        current = owner.observation_read(request(worktree=unrelated))
        assert current["facts"] == unaffected["facts"]
        assert current["cursor"] == unaffected["cursor"]
        retained = owner.observation_history(page_payload)
        assert retained["entries"] == page["entries"] and retained["cursor"] == page["cursor"]
        assert next(stream)["type"] in {"snapshot", "heartbeat"}
        with sqlite3.connect(owner.store.db_path) as conn:
            assert conn.execute(
                "SELECT count(*) FROM owner_observation_intent WHERE worktree=?", (target,),
            ).fetchone()[0] == 1
        election["reader"] = False
        assert owner.observation_read(request(worktree=unrelated))["error"]["code"] == "superseded"
        assert next(stream)["reason"] == "superseded"
    finally:
        owner.observations.runtime.history._after_intent = None
        stream.close()
