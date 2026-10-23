"""Bounded correlated point sets share one committed, freshly authorized view."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from fleet_manager.observation_reader import ScopedObservationReader
from fleet_manager.observation_store import MAX_POINT_READS, ObservationStore
from fleet_manager.planning import PlanError
from fleet_manager.runtime_admission import AdmissionError, ObservationRead, ObservationScope
from test_observation_reader import ReaderHarness, update
from test_observation_store import seed_updates
from test_runtime_admission import A, B, actor, policy, refuses
from test_runtime_identity import NOW
from test_runtime_identity import keys as keys


@pytest.fixture
def snapshot(keys, tmp_path, monkeypatch):
    harness = ReaderHarness(keys, tmp_path / "observations.db", monkeypatch)
    harness.batches = []
    harness.original_points = harness.store.read_points

    def points(selected):
        harness.batches.append(selected)
        return harness.original_points(selected)

    monkeypatch.setattr(harness.store, "read_points", points)
    return harness


def read(harness, selection=None, *, subject="principal-1", identity=None):
    return harness.reader.snapshot(
        identity or harness.identity(subject),
        selection if selection is not None else ObservationRead("fleet-a", (B, A)),
        role="client", installation="client-a",
    )


def point_keys(scopes):
    return tuple((scope.origin, scope.project, scope.entity) for scope in scopes)


def test_snapshot_projects_canonical_immutable_points_and_isolates_real_principals(snapshot):
    result = read(snapshot)
    assert type(result) is tuple and tuple(item.scope for item in result) == (A, B)
    assert tuple(item.state for item in result) == (
        (("status", "active"),), (("title", "private-origin-b"),),
    )
    second = read(snapshot, ObservationRead("fleet-a", (B,)), subject="principal-2")
    assert second == (result[1],)
    assert snapshot.batches == [point_keys((A, B)), point_keys((B,))]
    assert not snapshot.lookups and not snapshot.owner._issued
    for item in result:
        assert item.source_revision == item.generation == 1
        assert item.coverage == "point-observation"
        with pytest.raises(FrozenInstanceError):
            item.availability = "changed"
    encoded = json.dumps([asdict(item) for item in result])
    for forbidden in (
        "cursor", "position", "sequence", "store", "producer", "installation",
        "account", "enrollment", "permit", "private-origin-a",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize("selection", [
    None, object(), ObservationRead("fleet-a"), ObservationRead("fleet-a", ()),
    ObservationRead("fleet-a", (A, replace(A, fields=("title",)))),
    ObservationRead("fleet-a", tuple(
        replace(A, entity=f"entity-{index}") for index in range(MAX_POINT_READS + 1)
    )),
])
def test_invalid_snapshot_shape_never_opens_fence_or_store(snapshot, selection):
    refuses("unsupported-read", snapshot.reader.snapshot, snapshot.identity(), selection,
            role="client", installation="client-a")
    assert not snapshot.batches and not snapshot.lookups and not snapshot.fence.timeouts


@pytest.mark.parametrize("branch", [
    A,
    replace(B, project=A.project),
    replace(B, entity=A.entity),
    replace(B, fields=("status",)),
    replace(B, fields=("status", "title")),
])
def test_one_forbidden_branch_denies_entire_batch_before_lookup(snapshot, branch):
    permitted = ObservationScope("origin-c", "project-c", "entity-c", ("status",))
    snapshot.current = policy(revision=2, enrollments=(actor(), actor(
        "principal-2", principal="actor-b", account="account-b",
        reads=(B, permitted), operations=(),
    )))
    selection = ObservationRead("fleet-a", (permitted, branch))
    refuses("forbidden-scope", read, snapshot, selection, subject="principal-2")
    assert not snapshot.batches and not snapshot.lookups and not snapshot.owner._issued


def test_snapshot_limits_actual_transaction_and_indexed_lookup_work(snapshot, monkeypatch):
    scopes = tuple(replace(A, entity=f"entity-{index:02}") for index in range(MAX_POINT_READS))
    seed_updates(snapshot.store, [
        update(snapshot.bindings[A.origin], scope, sequence=index + 2)
        for index, scope in enumerate(scopes)
    ])
    snapshot.current = policy(revision=2, enrollments=(actor(reads=scopes),))
    transactions, queries = [], []
    original = snapshot.store._transaction

    @contextmanager
    def transaction(**options):
        transactions.append(options)
        with original(**options) as state:
            connection = state[0]
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
            connection.set_trace_callback(queries.append)
            yield state

    monkeypatch.setattr(snapshot.store, "_transaction", transaction)
    result = read(snapshot, ObservationRead("fleet-a", scopes))
    assert len(result) == MAX_POINT_READS == 64
    assert transactions == [{"write": False}]
    selected = [query for query in queries if " FROM current " in query]
    assert len(selected) == MAX_POINT_READS
    assert all("WHERE origin=" in query and " AND project=" in query
               and " AND entity=" in query for query in selected)
    assert not any(" FROM changes " in query or " FROM streams " in query for query in queries)
    with original(write=False) as (connection, *_):
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT payload FROM current "
            "WHERE origin=? AND project=? AND entity=?", point_keys(scopes)[0],
        ).fetchall()
    assert any("SEARCH current USING INDEX" in entry[-1] for entry in plan)
    assert not snapshot.owner._issued


@pytest.mark.parametrize("points", [
    None, [], (), (("origin-a", "project-a"),),
    (["origin-a", "project-a", "entity-a"],),
    (("origin-a", "project-a", ""),),
    (("origin-a", "project-a", None),),
    point_keys((A, A)),
    tuple(("origin-a", "project-a", f"entity-{i}") for i in range(MAX_POINT_READS + 1)),
])
def test_raw_point_set_validates_every_key_before_transaction(snapshot, monkeypatch, points):
    def opened(**_):
        pytest.fail("invalid point set opened storage")

    monkeypatch.setattr(snapshot.store, "_transaction", opened)
    with pytest.raises(PlanError):
        snapshot.original_points(points)


def test_snapshot_preserves_point_coverage_nulls_and_absence(snapshot):
    missing = replace(A, entity="missing")
    wide = replace(A, fields=("status", "title", "checkout_present"))
    snapshot.current = policy(revision=2, enrollments=(actor(reads=(wide, missing, B)),))
    snapshot.store.apply(update(
        snapshot.bindings[A.origin], A, sequence=2,
        state={"status": "active", "checkout_present": None},
    ))
    result = read(snapshot, ObservationRead("fleet-a", (wide, missing)))
    assert result[0].state == (("checkout_present", None), ("status", "active"))
    assert (result[1].state, result[1].reason, result[1].coverage) == (
        None, "unobserved", "point-observations-only",
    )
    binding = snapshot.bindings[A.origin]
    snapshot.store.apply(update(binding, A, sequence=3, state=None, reason="projection-removed"))
    assert read(snapshot)[0].reason == "projection-removed"
    snapshot.store.bind_origin(replace(binding, generation=2, producer="next"), expected=binding)
    stale = read(snapshot)[0]
    assert stale.generation == 1 and stale.coverage == "origin-resync-required"


@pytest.mark.parametrize("change,code", [
    ("revoked", "revoked-enrollment"), ("generation", "enrollment-changed"),
    ("scope", "forbidden-scope"), ("policy", "stale-policy"),
    ("trust", "stale-identity-trust"), ("expiry", "expired-permit"),
    ("clock", "clock-rollback"), ("unavailable", "authority-unavailable"),
])
def test_snapshot_completion_withholds_whole_prepared_tuple(snapshot, monkeypatch, change, code):
    def changed(points):
        result = snapshot.original_points(points)
        if change == "expiry":
            snapshot.clock.value += 30
        elif change == "clock":
            snapshot.clock.value -= 1
        elif change == "unavailable":
            snapshot.current = None
        else:
            changes = {"revision": 2}
            if change == "revoked":
                changes["enrollments"] = (actor(status="revoked"),)
            elif change == "generation":
                changes["enrollments"] = (actor(generation=2),)
            elif change == "scope":
                changes["enrollments"] = (actor(reads=(B,)),)
            elif change == "policy":
                changes["valid_until"] = NOW
            elif change == "trust":
                changes["identity_trust_until"] = NOW
            snapshot.current = policy(**changes)
        return result

    monkeypatch.setattr(snapshot.store, "read_points", changed)
    refuses(code, read, snapshot)
    assert not snapshot.owner._issued
    assert snapshot.fence.releases == len(snapshot.fence.timeouts)


def test_snapshot_storage_and_final_owner_errors_release_capacity(snapshot, monkeypatch):
    original = snapshot.store._point

    def later(*args):
        if args[1] == B.origin:
            raise PlanError("later-point-failed")
        return original(*args)

    monkeypatch.setattr(snapshot.store, "_point", later)
    with pytest.raises(PlanError, match="later-point-failed"):
        read(snapshot)
    assert not snapshot.owner._issued
    monkeypatch.setattr(snapshot.store, "_point", original)

    def unavailable():
        snapshot.calls += 1
        if snapshot.calls % 2 == 0:
            raise RuntimeError("final-owner-failed")
        return snapshot.current

    snapshot.calls = 0
    snapshot.owner._snapshot = unavailable
    with pytest.raises(RuntimeError, match="final-owner-failed"):
        read(snapshot)
    assert not snapshot.owner._issued


def test_snapshot_unbound_foreign_corrupt_store_and_proof_replay_fail_explicitly(
    snapshot, tmp_path, monkeypatch,
):
    missing = replace(B, origin="unbound")
    snapshot.current = policy(revision=2, enrollments=(actor(reads=(A, missing)),))
    with pytest.raises(PlanError, match="not bound"):
        read(snapshot, ObservationRead("fleet-a", (A, missing)))
    assert not snapshot.owner._issued
    snapshot.current = policy(revision=3)
    foreign = ObservationStore(tmp_path / "foreign.db", authority="foreign", installation="one")
    adapter = ScopedObservationReader(
        authority=snapshot.owner, store=foreign, fence=snapshot.fence,
    )
    monkeypatch.setattr(foreign, "read_points", lambda _: pytest.fail("foreign store queried"))
    refuses("authority-mismatch", adapter.snapshot, snapshot.identity(),
            ObservationRead("fleet-a", (A, B)), role="client", installation="client-a")
    identity = snapshot.identity()
    read(snapshot, identity=identity)
    refuses("identity-reused", read, snapshot, identity=identity)
    assert not snapshot.owner._issued
    with sqlite3.connect(snapshot.store.path) as connection:
        connection.execute(
            "UPDATE current SET payload=? WHERE origin=?", (b"{}", B.origin),
        )
    with pytest.raises(PlanError):
        read(snapshot)
    assert not snapshot.owner._issued


def test_snapshot_uses_the_real_bounded_policy_fence(snapshot):
    identity = snapshot.identity()
    with ThreadPoolExecutor(max_workers=1) as pool, snapshot.fence.lock:
        result = pool.submit(read, snapshot, identity=identity)
        with pytest.raises(AdmissionError, match="authority-busy"):
            result.result(timeout=3)
    assert not snapshot.batches and snapshot.fence.releases == 0


@pytest.mark.parametrize("coherent", [True, False])
def test_separate_process_writer_cannot_split_snapshot_but_can_split_point_reads(
    snapshot, tmp_path, monkeypatch, coherent,
):
    other = replace(A, entity="entity-c", fields=("title",))
    snapshot.store.apply(update(snapshot.bindings[A.origin], other, sequence=2))
    snapshot.current = policy(revision=2, enrollments=(actor(reads=(A, other)),))
    selection = ObservationRead("fleet-a", (A, other))
    ready = tmp_path / "writer-ready"
    initialized = tmp_path / "writer-initialized"
    script = """
import sqlite3, sys
from pathlib import Path
from fleet_manager.observation_store import ObservationStore
from fleet_manager.observations import OriginBinding
store = ObservationStore(sys.argv[1], authority="gateway-a", installation="gateway-a")
original = sqlite3.connect
class Connection(sqlite3.Connection):
    def commit(self):
        Path(sys.argv[2]).write_text("ready", encoding="ascii")
        return super().commit()
def connect(*args, **kwargs):
    return original(*args, factory=Connection, **kwargs)
sqlite3.connect = connect
binding = OriginBinding("origin-a", "installation", 1, "producer")
Path(sys.argv[3]).write_text("initialized", encoding="ascii")
if sys.stdin.readline().strip() != "apply":
    raise RuntimeError("missing update command")
store.bind_origin(
    OriginBinding("origin-a", "installation", 2, "successor"), expected=binding,
)
print("committed")
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(snapshot.store.path), str(ready), str(initialized)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    def wait_for(path):
        until = time.monotonic() + 5
        while not path.exists():
            assert child.poll() is None, child.communicate()
            assert time.monotonic() < until, f"writer did not reach {path.name}"
            time.sleep(0.01)

    def start_writer():
        child.stdin.write("apply\n")
        child.stdin.flush()
        wait_for(ready)
        return child

    def finish(child):
        out, err = child.communicate(timeout=5)
        assert child.returncode == 0, err
        assert out.strip() == "committed"

    original = snapshot.store._point

    def point(*args):
        found = original(*args)
        if args[3] == A.entity:
            child = start_writer()
            assert child.poll() is None
        return found

    try:
        wait_for(initialized)
        if coherent:
            monkeypatch.setattr(snapshot.store, "_point", point)
            result = read(snapshot, selection)
            assert tuple(item.coverage for item in result) == ("point-observation",) * 2
            monkeypatch.setattr(snapshot.store, "_point", original)
            finish(child)
        else:
            first = snapshot.store.read(origin=A.origin, project=A.project, entity=A.entity)
            finish(start_writer())
            second = snapshot.store.read(
                origin=other.origin, project=other.project, entity=other.entity,
            )
            assert first["row"]["coverage"] == "point-observation"
            assert second["row"]["coverage"] == "origin-resync-required"
        for scope in (A, other):
            current = snapshot.store.read(
                origin=scope.origin, project=scope.project, entity=scope.entity,
            )
            assert current["binding"].generation == 2
            assert current["row"]["coverage"] == "origin-resync-required"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
