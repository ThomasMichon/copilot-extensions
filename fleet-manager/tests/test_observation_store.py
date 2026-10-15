from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, FrozenInstanceError, replace
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from fleet_manager.observation_store import (
    MAX_PAGE_ROWS, MAX_REPLAY_EVENTS, ObservationStore,
)
from fleet_manager import observation_store
from fleet_manager.observations import (
    MAX_TEXT_BYTES, MAX_UPDATE_BYTES, CurrentUpdate, ObservationCursor, OriginBinding,
)
from fleet_manager.planning import PlanError


BINDING = OriginBinding("machine-a", "installation-a", 1, "producer-a")


def update(binding=BINDING, sequence=1, **changes):
    value = {
        "schema": "fleet-manager.current-update", "version": 1,
        **asdict(binding), "project": "example", "entity": "worktree-a",
        "sequence": sequence, "source_revision": sequence,
        "observed_at": 100, "availability": "current", "reason": None,
        "state": {"status": "active", "title": "Example", "checkout_present": True},
    }
    value.update(changes)
    return CurrentUpdate.from_dict(value)


def store(path):
    return ObservationStore(path, authority="fleet-a", installation="gateway-a")


def ready(tmp_path, binding=BINDING):
    result = store(tmp_path / "observations.db")
    result.bind_origin(binding)
    return result


def read(result, entity="worktree-a", origin="machine-a", project="example"):
    return result.read(origin=origin, project=project, entity=entity)


def seed_updates(result, candidates):
    """Populate read fixtures in one durable transaction, not exercise admission."""
    with result._transaction() as (connection, _store, _head, _floor):
        for candidate in candidates:
            value = candidate.to_dict()
            position = result._append(
                connection, origin=value["origin"], project=value["project"],
                generation=value["generation"], sequence=value["sequence"],
                payload=candidate.encoded,
            )
            connection.execute(
                "INSERT OR REPLACE INTO current VALUES(?,?,?,?,?,?,?,?)",
                (value["origin"], value["project"], value["entity"], value["generation"],
                 value["source_revision"], position, int(value["state"] is not None),
                 candidate.encoded),
            )
            connection.execute(
                "INSERT OR REPLACE INTO streams VALUES(?,?,?,?,?,?,?)",
                (value["origin"], value["project"], value["generation"], value["sequence"],
                 value["source_revision"], position, candidate.encoded),
            )


def test_bulk_read_fixture_matches_admitted_database(tmp_path):
    admitted = ready(tmp_path)
    seeded = store(tmp_path / "seeded.db")
    seeded.bind_origin(BINDING)
    candidates = [
        update(), update(sequence=2, entity="second"),
        update(sequence=3, entity="second", state=None, reason="projection-removed"),
        update(sequence=4, state={"status": "paused"}),
    ]
    for candidate in candidates:
        admitted.apply(candidate)
    seed_updates(seeded, candidates)
    with closing(sqlite3.connect(admitted.path)) as actual, closing(
        sqlite3.connect(seeded.path)
    ) as fixture:
        for table, ordering in (
            ("origins", "origin"), ("current", "origin,project,entity"),
            ("streams", "origin,project"), ("changes", "position"),
        ):
            query = f"SELECT * FROM {table} ORDER BY {ordering}"
            assert actual.execute(query).fetchall() == fixture.execute(query).fetchall()
        query = "SELECT authority,installation,position,floor FROM identity"
        assert actual.execute(query).fetchall() == fixture.execute(query).fetchall()
    for entity in ("worktree-a", "second"):
        assert read(admitted, entity)["row"] == read(seeded, entity)["row"]


def test_canonical_immutable_bounded_current_shape():
    source = update().to_dict()
    accepted = CurrentUpdate.from_dict(source)
    source["state"]["title"] = "changed later"
    assert accepted.to_dict()["state"]["title"] == "Example"
    with pytest.raises(FrozenInstanceError):
        accepted.encoded = b"{}"
    with pytest.raises(PlanError, match="canonical"):
        CurrentUpdate(json.dumps(accepted.to_dict()).encode())
    with pytest.raises(PlanError, match="byte bound"):
        CurrentUpdate(b"x" * (MAX_UPDATE_BYTES + 1))
    for changes in (
        {"version": True}, {"sequence": True}, {"source_revision": 0},
        {"observed_at": float("inf")}, {"availability": "ready"},
        {"availability": "stale"}, {"state": None}, {"unexpected": 1},
        {"state": {"history": []}}, {"state": {"status": ["active"]}},
        {"state": {"checkout_present": 1}},
        {"state": {"title": "x" * (MAX_TEXT_BYTES + 1)}},
    ):
        with pytest.raises(PlanError):
            update(**changes)
    assert update(state={"title": "x" * MAX_TEXT_BYTES})


def test_origin_admission_compare_and_set_and_producer_fence(tmp_path):
    result = store(tmp_path / "observations.db")
    with pytest.raises(PlanError, match="not bound"):
        result.apply(update())
    first = result.bind_origin(BINDING)
    assert result.bind_origin(BINDING) == first
    successor = replace(BINDING, generation=2, producer="producer-b")
    with pytest.raises(PlanError, match="binding changed"):
        result.bind_origin(successor)
    with pytest.raises(PlanError, match="next generation"):
        result.bind_origin(replace(successor, generation=3), expected=BINDING)
    result.bind_origin(successor, expected=BINDING)
    for candidate in (update(), update(replace(successor, producer="wrong-producer")),
                      update(replace(successor, installation="wrong-installation"))):
        with pytest.raises(PlanError, match="binding mismatch"):
            result.apply(candidate)
    with pytest.raises(PlanError, match="next generation"):
        result.bind_origin(BINDING, expected=successor)
    assert result.apply(update(successor)) > first


def test_update_slot_and_replay_commit_together_with_content_bound_retries(tmp_path):
    result = ready(tmp_path)
    before = read(result)["cursor"]
    first = update()
    position = result.apply(first)
    assert result.apply(first) == position
    result.apply(update(sequence=2))
    assert result.apply(first) == position
    with pytest.raises(PlanError, match="conflicting content"):
        result.apply(update(state={"status": "different"}))
    reopened = store(result.path)
    current = read(reopened)
    assert current["row"]["update"]["sequence"] == 2
    events = reopened.replay(before)
    assert [entry["update"]["sequence"] for entry in events["events"]] == [1, 2]
    assert events["cursor"] == current["cursor"]
    assert reopened.replay(events["cursor"])["events"] == []
    with closing(sqlite3.connect(result.path)) as connection:
        assert connection.execute("SELECT count(*) FROM changes").fetchone()[0] == 3


def test_gaps_regression_and_same_source_revision_conflict_are_not_new_state(tmp_path):
    result = ready(tmp_path)
    first = update(source_revision=5)
    position = result.apply(first)
    for candidate in (
        update(sequence=3, source_revision=6),
        update(sequence=2, source_revision=4),
        update(sequence=2, source_revision=5, state={"status": "different"}),
        update(sequence=2, source_revision=5, observed_at=101),
    ):
        with pytest.raises(PlanError):
            result.apply(candidate)
        assert read(result)["row"]["position"] == position
    degraded = update(sequence=2, source_revision=5, availability="stale", reason="source-unready")
    result.apply(degraded)
    assert read(result)["row"]["update"]["availability"] == "stale"


def test_origin_transition_preserves_old_facts_without_current_coverage(tmp_path):
    result = ready(tmp_path)
    result.apply(update())
    before = read(result)["cursor"]
    successor = replace(BINDING, generation=2, producer="producer-b")
    result.bind_origin(successor, expected=BINDING)
    current = read(result)
    assert current["binding"] == successor
    assert current["row"]["coverage"] == "origin-resync-required"
    event = result.replay(before)["events"][0]
    assert event["kind"] == "origin-bound" and event["binding"] == asdict(successor)
    result.apply(update(successor))
    assert read(result)["row"]["coverage"] == "point-observation"


def test_explicit_projection_removal_is_not_entity_or_obligation_retirement(tmp_path):
    result = ready(tmp_path)
    assert read(result)["row"] is None
    result.apply(update())
    result.apply(update(sequence=2, state=None, reason="projection-removed"))
    assert read(result)["row"]["update"]["reason"] == "projection-removed"
    assert result.page(origin=BINDING.origin, project="example")["rows"] == []
    result.apply(update(sequence=3))
    assert len(result.page(origin=BINDING.origin, project="example")["rows"]) == 1


def test_scoped_point_pages_and_replay_never_return_other_origins_or_projects(tmp_path):
    result = ready(tmp_path)
    other = replace(BINDING, origin="container-b")
    result.bind_origin(other)
    initial = read(result)["cursor"]
    result.apply(update(entity="first"))
    result.apply(update(sequence=2, entity="second"))
    result.apply(update(other, entity="private-other-origin"))
    result.apply(update(project="other", entity="private-other-project"))
    first = result.page(origin=BINDING.origin, project="example", limit=1)
    assert first["rows"][0]["update"]["entity"] == "first"
    assert first["next_entity"] == "first"
    second = result.page(origin=BINDING.origin, project="example", limit=1,
                         after_entity=first["next_entity"], at=first["cursor"])
    assert second["rows"][0]["update"]["entity"] == "second"
    assert second["next_entity"] is None
    replay = result.replay(initial, limit=1)
    assert replay["more"] and replay["events"][0]["update"]["entity"] == "first"
    replay = result.replay(replay["cursor"], limit=1)
    assert not replay["more"] and replay["events"][0]["update"]["entity"] == "second"
    assert read(result, "private-other-project")["row"] is None
    with pytest.raises(PlanError, match="scope mismatch"):
        result.page(origin=other.origin, project="example", at=first["cursor"])
    with pytest.raises(PlanError, match="continuation requires"):
        result.page(origin=BINDING.origin, project="example", after_entity="first")
    result.apply(update(sequence=3, entity="third"))
    with pytest.raises(PlanError, match="restart the snapshot"):
        result.page(origin=BINDING.origin, project="example", at=first["cursor"])


@pytest.mark.parametrize("retention", [
    4,
    pytest.param(MAX_REPLAY_EVENTS, marks=pytest.mark.observation_retention_full),
], ids=["small-window", "production-window"])
def test_bounded_replay_expires_visibly_but_latest_retry_remains_idempotent(
    tmp_path, monkeypatch, retention,
):
    monkeypatch.setattr(observation_store, "MAX_REPLAY_EVENTS", retention)
    result = ready(tmp_path)
    original = read(result)["cursor"]
    first = update()
    result.apply(first)
    seed_updates(result, (
        update(sequence=sequence) for sequence in range(2, retention + 1)
    ))
    for sequence in range(retention + 1, retention + 3):
        latest = update(sequence=sequence)
        position = result.apply(latest)
    with closing(sqlite3.connect(result.path)) as connection:
        assert connection.execute("SELECT count(*) FROM changes").fetchone()[0] == retention
    with pytest.raises(PlanError, match="fresh snapshot"):
        result.replay(original)
    with pytest.raises(PlanError, match="outside replay retention"):
        result.apply(first)
    assert result.apply(latest) == position
    fresh = read(result)
    assert result.replay(fresh["cursor"])["events"] == []


@pytest.mark.parametrize("method", ["page", "replay"])
def test_numeric_page_limits_are_exact_and_never_silently_clipped(tmp_path, method):
    result = ready(tmp_path)
    for limit in (0, -1, True, MAX_PAGE_ROWS + 1):
        with pytest.raises(PlanError, match="limit"):
            if method == "page":
                result.page(origin=BINDING.origin, project="example", limit=limit)
            else:
                result.replay(read(result)["cursor"], limit=limit)


def test_foreign_unsupported_or_modified_database_is_preserved_and_refused(tmp_path):
    result = ready(tmp_path)
    with pytest.raises(PlanError, match="identity mismatch"):
        ObservationStore(result.path, authority="other", installation="gateway-a")
    with closing(sqlite3.connect(result.path)) as connection:
        connection.execute("CREATE TABLE foreign_table(value TEXT)")
    with pytest.raises(PlanError, match="schema"):
        store(result.path)
    with closing(sqlite3.connect(result.path)) as connection:
        assert connection.execute("SELECT count(*) FROM foreign_table").fetchone()[0] == 0
        connection.execute("DROP TABLE foreign_table")
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(PlanError, match="schema"):
        read(result)


def test_transaction_failure_exposes_neither_update_nor_cursor(tmp_path, monkeypatch):
    result = ready(tmp_path)
    before = read(result)["cursor"]
    append = result._append
    def fail(connection, **fields):
        append(connection, **fields)
        raise RuntimeError("injected failure after accepted-change insertion")
    monkeypatch.setattr(result, "_append", fail)
    with pytest.raises(RuntimeError):
        result.apply(update())
    assert read(result)["cursor"] == before
    assert read(result)["row"] is None
    assert result.replay(before)["events"] == []


def test_concurrent_identical_admissions_have_one_durable_position(tmp_path):
    result = ready(tmp_path)
    candidate = update()
    with ThreadPoolExecutor(max_workers=4) as pool:
        positions = list(pool.map(lambda _: result.apply(candidate), range(8)))
    assert len(set(positions)) == 1
    with closing(sqlite3.connect(result.path)) as connection:
        assert connection.execute("SELECT count(*) FROM changes").fetchone()[0] == 2


def test_point_access_is_indexed_and_decodes_only_selected_current_slot(tmp_path, monkeypatch):
    result = ready(tmp_path)
    result.apply(update(entity="worktree-0001"))
    connect = sqlite3.connect
    budgets, statements = [], []
    measuring = True
    def measured_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        if measuring:
            budget = [0]
            budgets.append(budget)
            def progress():
                budget[0] += 1
                return 0
            connection.set_progress_handler(progress, 1)
            connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, "connect", measured_connection)
    read(result, "worktree-0001")
    measuring = False
    seed_updates(result, (
        update(sequence=sequence, entity=f"worktree-{sequence:04}")
        for sequence in range(2, 257)
    ))
    with closing(connect(result.path)) as connection:
        assert connection.execute("SELECT count(*) FROM current").fetchone()[0] == 256
        assert connection.execute("SELECT count(*) FROM changes").fetchone()[0] == 257
    measuring = True
    decoded = []
    original = result._decode_update
    def decode_one(payload):
        decoded.append(len(payload))
        return original(payload)
    monkeypatch.setattr(result, "_decode_update", decode_one)
    row = read(result, "worktree-0001")["row"]
    assert row["update"]["entity"] == "worktree-0001"
    assert len(decoded) == 1 and max(decoded) <= MAX_UPDATE_BYTES
    assert max(budget[0] for budget in budgets) < 1000
    assert abs(budgets[0][0] - budgets[1][0]) <= 32
    assert not any("FROM changes" in sql or "FROM streams" in sql for sql in statements)
    with closing(connect(result.path)) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT payload FROM current "
            "WHERE origin=? AND project=? AND entity=?",
            (BINDING.origin, "example", "worktree-0001"),
        ).fetchall()
        assert all("SCAN" not in entry[-1] for entry in plan)


def test_encoded_aggregate_bound_is_distinct_from_individual_field_limits():
    with pytest.raises(PlanError, match="byte bound"):
        update(state={field: "\u00e9" * (MAX_TEXT_BYTES // 2) for field in (
            "status", "title", "branch", "codename", "platform", "summary",
        )})


def test_snapshot_to_replay_boundary_preserves_an_intervening_commit(tmp_path):
    result = ready(tmp_path)
    result.apply(update())
    snapshot = result.page(origin=BINDING.origin, project="example")
    next_position = result.apply(update(sequence=2, state={"status": "paused"}))
    replay = result.replay(snapshot["cursor"])
    assert [event["position"] for event in replay["events"]] == [next_position]
    assert replay["events"][0]["update"]["state"] == {"status": "paused"}
    assert replay["cursor"] == read(result)["cursor"]


@pytest.mark.parametrize("location", ["current", "changes"])
def test_corrupt_payload_cannot_disclose_another_scope(tmp_path, location):
    result = ready(tmp_path)
    before = read(result)["cursor"]
    result.apply(update())
    foreign = update(project="different").encoded
    with closing(sqlite3.connect(result.path)) as connection:
        connection.execute(f"UPDATE {location} SET payload=?", (foreign,))
        connection.commit()
    with pytest.raises(PlanError, match="identity mismatch"):
        if location == "current":
            read(result)
        else:
            result.replay(before)


def test_cursor_rejects_another_database_or_a_future_position(tmp_path):
    result = ready(tmp_path)
    cursor = read(result)["cursor"]
    for candidate in (replace(cursor, store="another-store"), replace(cursor, position=100)):
        with pytest.raises(PlanError, match="cursor"):
            result.replay(candidate)
    with pytest.raises(PlanError):
        ObservationCursor(cursor.store, "*", "example", 0)


PROCESS = """
import json,os,sys
from dataclasses import asdict
from fleet_manager.observation_store import ObservationStore
from fleet_manager.observations import CurrentUpdate
s=ObservationStore(sys.argv[1],authority='fleet-a',installation='gateway-a')
u=CurrentUpdate.from_dict(json.loads(sys.argv[2]))
"""


def child(result, candidate, body):
    return subprocess.run(
        [sys.executable, "-I", "-c", PROCESS + body, str(result.path),
         json.dumps(candidate.to_dict())],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("after_commit", [False, True])
def test_installed_separate_process_crash_and_retry_boundary(tmp_path, after_commit):
    result = ready(tmp_path)
    before = read(result)["cursor"]
    if after_commit:
        body = "s.apply(u)\nos._exit(27)\n"
    else:
        body = """
original=s._append
def append(connection,**fields):
    original(connection,**fields)
    os._exit(27)
s._append=append
s.apply(u)
"""
    crashed = child(result, update(), body)
    assert crashed.returncode == 27, crashed.stderr
    current = read(store(result.path))
    assert (current["row"] is not None) == after_commit
    assert (current["cursor"] != before) == after_commit
    retried = child(result, update(), "print(s.apply(u))")
    assert retried.returncode == 0, retried.stderr
    assert len(result.replay(before)["events"]) == 1
    assert read(result)["row"]["position"] == int(retried.stdout.strip())


def test_installed_machine_and_container_origins_and_independent_query_clients(tmp_path):
    result = store(tmp_path / "observations.db")
    for origin in ("machine-a", "machine-b", "container-a"):
        binding = replace(BINDING, origin=origin, installation=f"install-{origin}")
        pushed = child(result, update(binding), "s.bind_origin(u.binding)\nprint(s.apply(u))")
        assert pushed.returncode == 0, pushed.stderr
    request = update(replace(BINDING, installation="install-machine-a"))
    body = """
value=s.read(origin=u.binding.origin,project='example',entity='worktree-a')
print(json.dumps(value,default=asdict))
"""
    first, second = child(result, request, body), child(result, request, body)
    assert first.returncode == second.returncode == 0, (first.stderr, second.stderr)
    assert json.loads(first.stdout) == json.loads(second.stdout)
    observed = json.loads(first.stdout)["row"]["update"]
    assert observed["origin"] == "machine-a" and observed["state"]["status"] == "active"
