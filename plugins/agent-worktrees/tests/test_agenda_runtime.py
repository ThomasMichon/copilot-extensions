"""Synthetic committed observations, real owner database and held control sockets."""

from __future__ import annotations

import copy
import io
import json
import socket
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent_worktrees import agenda_contract as contract
from agent_worktrees import agenda_feed, record_cache, tracking
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_projection as projection
from agent_worktrees.worktrees_runtime import WorktreesRuntime


def request(**values):
    return {
        "contract_version": 1,
        "project": "project",
        "worktree_id": "full-worktree-id",
        "session_id": "head-session",
        **values,
    }


def observation(revision=1, session="head-session", store="a" * 32):
    return {
        "schema": "efforts.agenda-observation",
        "version": 1,
        "source": {"worktree": "full-worktree-id", "session": session, "store_id": store},
        "revision": revision,
        "committed_at": "2026-11-01T07:00:00Z",
        "snapshot": {
            "worktree": "full-worktree-id",
            "revision": revision,
            "status": "working",
            "root": "Complete the assigned change",
            "stack": ["Implement"],
            "next_action": "Run bounded tests",
            "exit_criteria": ["Contract preserved"],
            "declared_outcomes": {"done": "All checks pass"},
            "queued": [],
            "parked": 0,
            "notes": [],
            "cursor_version": 2,
            "deferred": [],
            "due_rechecks": [],
            "next_revisit_at": None,
        },
    }


def publication(owner, body=None, **values):
    return {
        "contract_version": 1,
        "project": "project",
        "expected_generation": owner.store.generation,
        "observation": observation() if body is None else body,
        **values,
    }


@pytest.fixture
def owned(tmp_path, monkeypatch):
    records = tmp_path / "records"
    records.mkdir()
    path = records / "full-worktree-id.yaml"
    path.write_text("synthetic record fixture", encoding="utf-8")
    head = ["head-session"]
    monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda wid, repo=None: records)
    monkeypatch.setattr(
        tracking,
        "load_record",
        lambda path: SimpleNamespace(
            worktree_id="full-worktree-id",
            repo="project",
            resolved_head_session=head[0],
        ),
    )
    elected = [True]
    owner = WorktreesRuntime(tmp_path / "current.sqlite3", elected=lambda: elected[0])
    owner.activate()
    owner.publish(
        "project", [{"id": "full-worktree-id", "last_session_id": "resume-target"}], observed_at=1
    )
    yield owner, head, elected, path
    owner.shutdown()


def project_request(project, **values):
    return request(
        project=project,
        worktree_id=f"{project}-lane",
        session_id=f"head-{project}",
        **values,
    )


def project_observation(project, revision=1):
    body = observation(revision, session=f"head-{project}")
    body["source"]["worktree"] = body["snapshot"]["worktree"] = f"{project}-lane"
    return body


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    directories = {}
    for project in ("alpha", "beta"):
        directory = tmp_path / project / "records"
        directory.mkdir(parents=True)
        directories[project] = directory
        record = tracking.WorktreeRecord(
            worktree_id=f"{project}-lane",
            branch=f"worktree/{project}-lane",
            worktree_path=str(directory),
            repo=project,
            machine="synthetic-machine",
            platform="linux",
            started_at="2026-11-01T00:00:00",
            last_resumed_at=None,
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[tracking.SessionEntry(f"head-{project}", "2026-11-01T00:00:00")],
        )
        tracking.save_record(record, directory / f"{project}-lane.yaml")
    monkeypatch.setattr(
        tracking,
        "_owning_tracking_dir",
        lambda wid, repo=None: directories[repo],
    )
    clock = [10.0]
    wakes = []
    owner = WorktreesRuntime(
        tmp_path / "current.sqlite3",
        now=lambda: clock[0],
        wake=SimpleNamespace(set=lambda: wakes.append(clock[0])),
        elected=lambda: True,
    )
    owner.activate()
    wakes.clear()
    yield owner, clock, wakes
    owner.shutdown()


def test_project_reconciliation_error_isolated_and_retained_across_undrain(two_projects):
    owner, _clock, _wakes = two_projects
    bodies = {project: project_observation(project) for project in ("alpha", "beta")}
    for project, body in bodies.items():
        owner.publish(project, [{"id": f"{project}-lane"}], observed_at=1)
        assert owner.agendas.publish(publication(owner, body, project=project))["ok"] is True
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute(
            "UPDATE worktrees_agenda_cache SET payload=? WHERE project=?",
            ('{"invalid":true}', "alpha"),
        )
    owner.publish("alpha", [{"id": "alpha-lane"}], observed_at=2)
    for at in (2, 3):
        owner.publish("beta", [{"id": "beta-lane"}], observed_at=at)
        failed = owner.agendas.read(project_request("alpha"))
        assert failed["ok"] is False and failed["error"]["code"] == "unavailable"
        assert "observation" not in failed
        assert owner.agendas.read(project_request("beta"))["observation"] == bodies["beta"]
    assert (
        owner.agendas.publish(
            publication(owner, project_observation("alpha", 2), project="alpha"),
        )["error"]["code"]
        == "unavailable"
    )
    generation = owner.store.generation
    owner.close_admission("draining")
    owner.activate()
    assert owner.store.generation == generation
    assert owner.agendas.read(project_request("alpha"))["error"]["code"] == "unavailable"
    assert owner.agendas.read(project_request("beta"))["availability"] == "observed"
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute(
            "UPDATE worktrees_agenda_cache SET payload=? WHERE project=?",
            (contract.observation(bodies["alpha"]), "alpha"),
        )
    owner.publish("alpha", [{"id": "alpha-lane"}], observed_at=4)
    assert owner.agendas.read(project_request("alpha"))["observation"] == bodies["alpha"]
    assert "alpha" not in owner.agendas._project_errors
    with sqlite3.connect(owner.store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM worktrees_agenda_source").fetchone()[0] == 2


def test_project_failures_reset_for_new_generation_but_cache_requires_requalification(
    two_projects,
):
    owner, _clock, _wakes = two_projects
    for project in ("alpha", "beta"):
        owner.publish(project, [{"id": f"{project}-lane"}], observed_at=1)
        assert (
            owner.agendas.publish(
                publication(owner, project_observation(project), project=project),
            )["ok"]
            is True
        )
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute(
            "UPDATE worktrees_agenda_cache SET payload=? WHERE project=?",
            ('{"invalid":true}', "alpha"),
        )
    owner.publish("alpha", [{"id": "alpha-lane"}], observed_at=2)
    assert "alpha" in owner.agendas._project_errors
    owner.close_admission("superseded")
    successor = WorktreesRuntime(owner.store.db_path)
    successor.activate()
    try:
        assert successor.store.generation != owner.store.generation
        assert successor.agendas._project_errors == {}
        assert successor.agendas.read(project_request("alpha"))["error"]["code"] == "unready"
        successor.publish("beta", [{"id": "beta-lane"}], observed_at=2)
        assert successor.agendas.read(project_request("beta"))["availability"] == "observed"
        successor.publish("alpha", [{"id": "alpha-lane"}], observed_at=2)
        assert successor.agendas.read(project_request("alpha"))["error"]["code"] == "unavailable"
    finally:
        successor.shutdown()


def test_schema_activation_fault_is_not_cleared_by_project_reconciliation(
    two_projects, monkeypatch
):
    from agent_worktrees import agenda_runtime

    owner, _clock, _wakes = two_projects
    owner.publish("beta", [{"id": "beta-lane"}], observed_at=1)
    schema = agenda_runtime._SCHEMA
    monkeypatch.setattr(agenda_runtime, "_SCHEMA", "synthetic invalid SQL")
    owner.close_admission("draining")
    owner.activate()
    assert owner.agendas._activation_error
    owner.publish("beta", [{"id": "beta-lane"}], observed_at=2)
    assert owner.agendas.read(project_request("beta"))["error"]["code"] == "unavailable"
    assert owner.agendas._activation_error
    assert "beta" not in owner.agendas._project_errors
    monkeypatch.setattr(agenda_runtime, "_SCHEMA", schema)
    owner.activate()
    assert owner.agendas._activation_error is None
    assert owner.agendas.read(project_request("beta"))["availability"] == "not-published"


@pytest.mark.parametrize("operation", ["read", "publish", "feed"])
def test_unpublished_project_demand_wakes_and_survives_initial_feed_failure(
    two_projects,
    monkeypatch,
    operation,
):
    from agent_worktrees import status_monitor_runtime

    owner, clock, wakes = two_projects
    monkeypatch.setattr(
        status_monitor_runtime,
        "_ensure_status_monitor",
        lambda: pytest.fail("agenda demand started another resident"),
    )
    owner.publish("beta", [{"id": "beta-lane"}], observed_at=1)
    selected = project_request("alpha")
    body = project_observation("alpha")
    if operation == "read":
        result = owner.agendas.read(selected)
    elif operation == "publish":
        result = owner.agendas.publish(publication(owner, body, project="alpha"))
    else:
        initial = owner.agendas.stream(selected, lambda: False)
        result = next(initial)
        assert result["type"] == "error"
        with pytest.raises(StopIteration):
            next(initial)
        assert owner.agendas.feed.active_count() == 0
    assert result["ok"] is False and result["error"]["code"] == "unready"
    assert owner.demand_projects() == {"alpha"} and len(wakes) == 1
    assert ("alpha", "alpha-lane") not in owner.agendas._rows
    assert owner.agendas.read(selected)["error"]["code"] == "unready"
    assert len(wakes) == 1
    owner.publish("alpha", [{"id": "alpha-lane"}], observed_at=2)
    if operation == "feed":
        stream = owner.agendas.stream(selected, lambda: False)
        try:
            assert next(stream)["availability"] == "not-published"
            assert owner.agendas.publish(publication(owner, body, project="alpha"))["ok"] is True
            assert next(stream)["observation"] == body
        finally:
            stream.close()
    else:
        assert owner.agendas.read(selected)["availability"] == "not-published"
        assert owner.agendas.publish(publication(owner, body, project="alpha"))["ok"] is True
    assert owner.agendas.read(selected)["observation"] == body
    assert len(wakes) == 1
    demand = dict(owner._demand)
    owner.close_admission("draining")
    assert owner.agendas.read(selected)["error"]["code"] == "draining"
    assert (
        owner.agendas.publish(publication(owner, body, project="alpha"))["error"]["code"]
        == "draining"
    )
    rejected = owner.agendas.stream(selected, lambda: False)
    assert next(rejected)["error"]["code"] == "draining"
    rejected.close()
    assert owner._demand == demand and len(wakes) == 1
    clock[0] += 30.001
    assert owner.demand_projects() == set()


def test_complete_project_absent_worktree_is_not_unready(two_projects):
    owner, _clock, wakes = two_projects
    owner.publish("alpha", [], observed_at=1)
    assert owner.agendas.read(project_request("alpha"))["error"]["code"] == "not-found"
    assert (
        owner.agendas.publish(
            publication(owner, project_observation("alpha"), project="alpha"),
        )["error"]["code"]
        == "not-found"
    )
    assert owner.demand_projects() == {"alpha"} and wakes == []


def test_commit_duplicate_conflict_stale_and_immutable_projection(owned):
    owner, _head, _elected, _path = owned
    assert contract.CAPABILITY in owner.health()["capabilities"]
    first = owner.agendas.read(request())
    assert set(first) == contract.REPLY_FIELDS
    assert first["availability"] == "not-published"
    accepted = owner.agendas.publish(publication(owner))
    assert accepted["availability"] == "observed"
    duplicate = owner.agendas.publish(publication(owner))
    assert duplicate == accepted
    changed = observation()
    changed["snapshot"]["next_action"] = "Different content"
    assert (
        owner.agendas.publish(publication(owner, changed))["error"]["code"] == "revision-conflict"
    )
    assert owner.agendas.publish(publication(owner, observation(2)))["ok"] is True
    assert owner.agendas.publish(publication(owner))["error"]["code"] == "stale-revision"
    assert (
        owner.agendas.publish(publication(owner, observation(3, store="b" * 32)))["error"]["code"]
        == "source-changed"
    )
    accepted["observation"]["snapshot"]["root"] = "Do not mutate cache"
    assert (
        owner.agendas.read(request())["observation"]["snapshot"]["root"] != "Do not mutate cache"
    )
    assert (
        "agenda" not in owner.handle({"contract_version": 1, "project": "project"})["worktrees"][0]
    )


@pytest.mark.parametrize("operation", ["publish", "read", "pending-read", "reconcile"])
@pytest.mark.parametrize("lost_authority", ["election", "database"])
def test_final_fence_loss_never_exposes_or_notifies(owned, monkeypatch, operation, lost_authority):
    owner, head, elected, _path = owned
    assert owner.agendas.publish(publication(owner))["ok"] is True
    rows = copy.deepcopy(owner.agendas._rows)
    sequence = owner.agendas._sequence
    notifications = []
    key = ("project", "full-worktree-id")
    if operation != "publish":
        head[0] = "new-head"
    if operation == "pending-read":
        with owner.agendas._pending_lock:
            owner.agendas._pending.add(key)
    monkeypatch.setattr(owner.agendas.feed, "changed", notifications.append)
    original = owner.agendas._fence
    injected = [False]

    def fence(conn=None, expected=None):
        if conn is not None and not conn.in_transaction and not injected[0]:
            injected[0] = True
            with sqlite3.connect(owner.store.db_path) as db:
                durable = json.loads(
                    db.execute(
                        "SELECT payload FROM worktrees_agenda_cache",
                    ).fetchone()[0]
                )
                assert durable["revision"] == (2 if operation == "publish" else 1)
                if lost_authority == "database":
                    db.execute(
                        "UPDATE worktrees_projection_owner SET generation=?, writer_id=? "
                        "WHERE singleton=1",
                        ("c" * 32, "d" * 32),
                    )
            if lost_authority == "election":
                elected[0] = False
        return original(conn, expected)

    monkeypatch.setattr(owner.agendas, "_fence", fence)
    if operation == "reconcile":
        with owner._activation_lock, pytest.raises(contract.AgendaError) as error:
            owner.agendas.reconcile("project")
        assert error.value.code == "superseded"
    else:
        result = (
            owner.agendas.publish(publication(owner, observation(2)))
            if operation == "publish"
            else owner.agendas.read(request(), refresh=operation != "pending-read")
        )
        assert result["ok"] is False and result["error"]["code"] == "superseded"
    assert injected[0]
    assert owner.agendas._rows == rows
    assert owner.agendas._sequence == sequence
    assert notifications == []
    if operation == "pending-read":
        assert key in owner.agendas._pending
    if operation == "publish":
        successor = WorktreesRuntime(owner.store.db_path)
        successor.activate()
        try:
            assert successor.agendas.read(request())["ok"] is False
            successor.publish("project", [{"id": "full-worktree-id"}], observed_at=2)
            assert successor.agendas.read(request())["observation"] == observation(2)
        finally:
            successor.shutdown()


@pytest.mark.parametrize(
    "values,code",
    [
        ({"project": "foreign"}, "unready"),
        ({"expected_generation": "b" * 32}, "superseded"),
        ({"contract_version": True}, "invalid-request"),
        ({"extra": True}, "invalid-request"),
    ],
)
def test_wrong_source_generation_and_fields(owned, values, code):
    owner, *_rest = owned
    assert owner.agendas.publish(publication(owner, **values))["error"]["code"] == code


def test_exact_id_no_suffix_paths_or_resume_target_authority(owned):
    owner, head, _elected, _path = owned
    assert owner.agendas.read(request(worktree_id="id"))["error"]["code"] == "not-found"
    for wid in ("../full-worktree-id", "full/worktree", "full\\worktree", "*"):
        assert owner.agendas.read(request(worktree_id=wid))["error"]["code"] == "invalid-request"
    assert (
        owner.agendas.publish(publication(owner, observation(session="resume-target")))["error"][
            "code"
        ]
        == "head-changed"
    )
    head[0] = None
    assert owner.agendas.read(request())["availability"] == "head-changed"
    assert (
        owner.agendas.publish(publication(owner, observation(session=None)))["error"]["code"]
        == "head-changed"
    )


def test_head_changed_during_publication_rolls_back(owned, monkeypatch):
    owner, head, *_rest = owned
    assert owner.agendas.publish(publication(owner))["ok"] is True
    original = owner.agendas._head
    count = [0]

    def changed(key):
        count[0] += 1
        if count[0] == 2:
            head[0] = "new-head"
        return original(key)

    monkeypatch.setattr(owner.agendas, "_head", changed)
    assert (
        owner.agendas.publish(publication(owner, observation(2)))["error"]["code"]
        == "head-changed"
    )
    with sqlite3.connect(owner.store.db_path) as conn:
        body = json.loads(conn.execute("SELECT payload FROM worktrees_agenda_cache").fetchone()[0])
    assert body["revision"] == 1
    result = owner.agendas.read(request())
    assert result["availability"] == "head-changed" and result["observation"] is None


def test_new_head_late_result_and_roster_removal(owned):
    owner, head, *_rest = owned
    owner.agendas.publish(publication(owner))
    head[0] = "new-head"
    owner.publish("project", [{"id": "full-worktree-id"}], observed_at=2)
    assert owner.agendas.read(request())["availability"] == "head-changed"
    assert (
        owner.agendas.publish(publication(owner, observation(2)))["error"]["code"]
        == "head-changed"
    )
    assert (
        owner.agendas.publish(publication(owner, observation(1, "new-head", "b" * 32)))["ok"]
        is True
    )
    owner.publish("project", [], observed_at=3)
    assert owner.agendas.read(request(session_id="new-head"))["error"]["code"] == "not-found"
    assert not owner.agendas._rows
    with sqlite3.connect(owner.store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM worktrees_agenda_cache").fetchone()[0] == 0


def test_new_owner_requalifies_committed_cache_only_after_current_roster(owned):
    owner, head, _elected, _path = owned
    owner.agendas.publish(publication(owner))
    old_generation = owner.store.generation
    owner.close_admission("superseded")
    successor = WorktreesRuntime(owner.store.db_path)
    successor.activate()
    try:
        assert successor.agendas.read(request())["error"]["code"] == "unready"
        successor.publish("project", [{"id": "full-worktree-id"}], observed_at=2)
        replay = successor.agendas.read(request())
        assert replay["availability"] == "observed" and replay["generation"] != old_generation
        assert successor.agendas.publish(publication(successor)) == replay
        assert owner.agendas.publish(publication(owner, observation(2)))["ok"] is False
        head[0] = "new-head"
        successor.publish("project", [{"id": "full-worktree-id"}], observed_at=3)
        assert successor.agendas.read(request())["availability"] == "head-changed"
    finally:
        successor.shutdown()


def test_cache_fault_does_not_break_roster(owned):
    owner, *_rest = owned
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute(
            "INSERT INTO worktrees_agenda_cache VALUES (?, ?, ?)",
            ("project", "full-worktree-id", '{"bad":true}'),
        )
    owner.publish("project", [{"id": "full-worktree-id"}], observed_at=2)
    assert owner.handle({"contract_version": 1, "project": "project"})["ok"] is True
    assert owner.agendas.read(request())["error"]["code"] == "unavailable"


@contextmanager
def socket_feed(owner, payload=None):
    server = control.ControlServer(
        lambda action, payload: (
            owner.health() if action == "health" else owner.agenda_handle(action, payload)
        ),
        token="synthetic-token",
        stream_handle=owner.stream,
    )
    server.start()
    client = control.ControlClient(f"http://127.0.0.1:{server.port}", token=server.token)
    with socket.create_connection((client.host, client.port), timeout=2) as sock:
        sock.settimeout(3)
        sock.sendall(
            json.dumps(
                {
                    "version": 1,
                    "token": server.token,
                    "action": "agenda-feed",
                    "payload": request() if payload is None else payload,
                }
            ).encode()
            + b"\n"
        )
        reader = sock.makefile("rb")
        try:
            yield client, reader
        finally:
            reader.close()
            owner.close_admission()
    server.close()


def held_terminal(reader):
    for _ in range(3):
        frame = contract.decode(reader.readline(contract.FRAME_BYTES + 1))
        if frame["type"] == "going-away":
            assert reader.readline(contract.FRAME_BYTES + 1) == b""
            return frame
        assert frame["type"] == "heartbeat"
    pytest.fail("held agenda continued heartbeats after owner admission closed")


def test_agenda_schema_initialization_failure_retires_held_feed_but_keeps_roster_healthy(
    owned,
    monkeypatch,
):
    from agent_worktrees import agenda_runtime

    owner, _head, elected, _path = owned
    body = observation(2)
    accepted = owner.agendas.publish(publication(owner, body))
    owner.publish("other-project", [{"id": "other-worktree"}], observed_at=1)
    accepted = owner.agendas.read(request())
    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.02)
    with socket_feed(owner) as (client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}
        with sqlite3.connect(owner.store.db_path) as conn:
            saved = conn.execute("SELECT * FROM worktrees_agenda_cache").fetchall()
            conn.execute("DROP TABLE worktrees_agenda_cache")
            conn.execute(
                "CREATE INDEX worktrees_agenda_cache ON worktrees_agenda_source(project)",
            )
        owner.activate()
        assert elected[0] and owner._admitting
        assert client.health()["worktrees"]["status"] == "ready"
        assert "index" in owner.agendas._activation_error.lower()
        assert owner.agendas.read(request())["error"]["code"] == "unavailable"
        assert owner.handle(
            {
                "contract_version": 1,
                "project": "other-project",
            }
        )["worktrees"] == [{"id": "other-worktree"}]
        assert held_terminal(reader) == {
            "type": "going-away",
            "generation": accepted["generation"],
            "sequence": accepted["sequence"],
            "reason": "unavailable",
        }
    assert owner.agendas.feed.wait_closed(1)
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute("DROP INDEX worktrees_agenda_cache")
        conn.execute(agenda_runtime._SCHEMA)
        conn.executemany("INSERT INTO worktrees_agenda_cache VALUES (?, ?, ?)", saved)
        assert conn.execute("SELECT revision FROM worktrees_agenda_source").fetchone()[0] == 2
    owner.activate()
    assert owner.store.generation == accepted["generation"]
    assert owner.agendas._activation_error is None
    with socket_feed(owner) as (client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}
        assert client._request("agenda-publish", publication(owner, body)) == accepted
        assert (
            client._request(
                "agenda-publish",
                publication(owner, observation(1)),
            )["error"]["code"]
            == "stale-revision"
        )


@pytest.mark.parametrize("failure", ["storage", "registration", "unexpected-registration"])
def test_other_agenda_initialization_failures_close_held_feed_and_recover(
    owned,
    monkeypatch,
    failure,
):
    owner, _head, elected, _path = owned
    accepted = owner.agendas.publish(publication(owner))
    original_fence = owner.agendas._fence
    original_observe = record_cache.observe_writes

    def fail(*args, **kwargs):
        if failure == "unexpected-registration":
            raise TypeError("synthetic observer initialization defect")
        raise OSError("synthetic agenda initialization storage failure")

    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.02)
    with socket_feed(owner) as (_client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}
        if failure == "storage":
            monkeypatch.setattr(owner.agendas, "_fence", fail)
        else:
            owner.agendas._unsubscribe()
            owner.agendas._unsubscribe = None
            monkeypatch.setattr(record_cache, "observe_writes", fail)
        if failure == "unexpected-registration":
            with pytest.raises(TypeError, match="synthetic observer initialization defect"):
                owner.activate()
        else:
            owner.activate()
        assert elected[0] and owner._admitting
        assert owner.agendas._activation_error
        assert held_terminal(reader) == {
            "type": "going-away",
            "generation": accepted["generation"],
            "sequence": accepted["sequence"],
            "reason": "unavailable",
        }
    assert owner.agendas.feed.wait_closed(1)
    monkeypatch.setattr(owner.agendas, "_fence", original_fence)
    monkeypatch.setattr(record_cache, "observe_writes", original_observe)
    owner.activate()
    assert owner.agendas._activation_error is None
    with socket_feed(owner) as (_client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}


@pytest.mark.parametrize(
    "error_type",
    [
        projection.ProjectionError,
        projection.ProjectionUnavailable,
        projection.ProjectionOwnershipChanged,
        sqlite3.OperationalError,
        OSError,
    ],
)
def test_activation_failure_retires_already_held_agenda_and_allows_same_generation_recovery(
    owned,
    monkeypatch,
    error_type,
):
    owner, _head, elected, _path = owned
    body = observation(2)
    accepted = owner.agendas.publish(publication(owner, body))
    original_activate = owner.store.activate

    def fail(**kwargs):
        raise error_type("synthetic activation failure")

    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.02)
    with socket_feed(owner) as (_client, reader):
        snapshot = contract.decode(reader.readline())
        assert snapshot == {"type": "snapshot", **accepted}
        monkeypatch.setattr(owner.store, "activate", fail)
        with pytest.raises((projection.ProjectionError, projection.ProjectionUnavailable)):
            owner.activate()
        assert elected[0] is True and owner._admitting is False
        terminal = held_terminal(reader)
        assert terminal == {
            "type": "going-away",
            "generation": snapshot["generation"],
            "sequence": snapshot["sequence"],
            "reason": "unavailable",
        }
    assert owner.agendas.feed.wait_closed(1)
    monkeypatch.setattr(owner.store, "activate", original_activate)
    owner.activate()
    assert owner.store.generation == accepted["generation"]
    with socket_feed(owner) as (client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}
        assert client._request("agenda-publish", publication(owner, body)) == accepted
        assert (
            client._request(
                "agenda-publish",
                publication(owner, observation(1)),
            )["error"]["code"]
            == "stale-revision"
        )


@pytest.mark.parametrize("replacement_fails", [False, True])
def test_writer_replacement_retires_held_old_generation_and_requalifies_new_generation(
    owned,
    monkeypatch,
    replacement_fails,
):
    owner, _head, elected, _path = owned
    body = observation(2)
    accepted = owner.agendas.publish(publication(owner, body))
    old_store = owner.store
    takeover = projection.WorktreesProjection(owner.store.db_path)
    original_activate = projection.WorktreesProjection.activate
    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.02)
    try:
        with socket_feed(owner) as (_client, reader):
            snapshot = contract.decode(reader.readline())
            assert snapshot == {"type": "snapshot", **accepted}
            takeover.activate(
                expected_generation=old_store.generation, authority_check=lambda: True
            )
            if replacement_fails:

                def activate(store, **kwargs):
                    if store is not old_store:
                        raise projection.ProjectionUnavailable("synthetic replacement failure")
                    return original_activate(store, **kwargs)

                monkeypatch.setattr(projection.WorktreesProjection, "activate", activate)
                with pytest.raises(projection.ProjectionUnavailable):
                    owner.activate()
            else:
                owner.activate()
            assert elected[0] is True and owner.store is not old_store
            terminal = held_terminal(reader)
            assert terminal == {
                "type": "going-away",
                "generation": snapshot["generation"],
                "sequence": snapshot["sequence"],
                "reason": "superseded",
            }
        assert owner.agendas.feed.wait_closed(1)
        monkeypatch.setattr(projection.WorktreesProjection, "activate", original_activate)
        owner.activate()
        assert owner.store.generation != accepted["generation"]
        assert owner.agendas.read(request())["error"]["code"] == "unready"
        owner.publish("project", [{"id": "full-worktree-id"}], observed_at=2)
        current = owner.agendas.read(request())
        assert current["observation"] == body
        with socket_feed(owner) as (client, reader):
            assert contract.decode(reader.readline()) == {"type": "snapshot", **current}
            assert (
                client._request(
                    "agenda-publish",
                    publication(owner, observation(1)),
                )["error"]["code"]
                == "stale-revision"
            )
        with pytest.raises(projection.ProjectionUnavailable):
            old_store.activate(
                expected_generation=owner.store.generation, authority_check=lambda: True
            )
    finally:
        takeover.close()


@pytest.mark.parametrize("reason", ["draining", "unready", "unavailable", "disconnected"])
def test_temporary_admission_closure_retires_held_stream_without_retiring_owner(
    owned,
    monkeypatch,
    reason,
):
    owner, *_rest = owned
    accepted = owner.agendas.publish(publication(owner))
    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.02)
    with socket_feed(owner) as (_client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}
        owner.close_admission(reason)
        assert held_terminal(reader) == {
            "type": "going-away",
            "generation": accepted["generation"],
            "sequence": accepted["sequence"],
            "reason": reason,
        }
    owner.activate()
    with socket_feed(owner) as (_client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}


def test_shutdown_retires_already_held_agenda_and_cannot_reactivate(owned, monkeypatch):
    owner, *_rest = owned
    accepted = owner.agendas.publish(publication(owner))
    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.02)
    with socket_feed(owner) as (_client, reader):
        assert contract.decode(reader.readline()) == {"type": "snapshot", **accepted}
        owner.shutdown()
        assert held_terminal(reader) == {
            "type": "going-away",
            "generation": accepted["generation"],
            "sequence": accepted["sequence"],
            "reason": "closed",
        }
    with pytest.raises(projection.ProjectionUnavailable):
        owner.activate()


@pytest.fixture
def captured_control_requests(monkeypatch):
    sent = []
    connect = control.socket.create_connection

    class Capture:
        def __init__(self, sock):
            self.sock = sock

        def __enter__(self):
            self.sock.__enter__()
            return self

        def __exit__(self, *args):
            return self.sock.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.sock, name)

        def sendall(self, data):
            sent.append(bytes(data))
            return self.sock.sendall(data)

    monkeypatch.setattr(
        control.socket,
        "create_connection",
        lambda *args, **kwargs: Capture(connect(*args, **kwargs)),
    )
    return sent


def test_unicode_control_publication_fits_utf8_bounds_and_reaches_held_feed(
    owned,
    captured_control_requests,
    monkeypatch,
    capsys,
):
    from agent_worktrees import agenda_cli

    owner, *_rest = owned
    body = observation()
    text = "\u6f22\U0001f680" * 500
    body["snapshot"]["notes"] = [text] * 9
    assert len(text.encode("utf-8")) == 3500
    encoded_body = contract.observation(body)
    assert (
        contract.BODY_BYTES - 1024 <= len(encoded_body.encode("utf-8")) + 1 <= contract.BODY_BYTES
    )
    with socket_feed(owner) as (client, reader):
        assert contract.decode(reader.readline())["availability"] == "not-published"
        envelope = {
            "version": 1,
            "token": client.token,
            "action": "agenda-publish",
            "payload": publication(owner, body),
        }
        expected = contract.encode(envelope).encode("utf-8") + b"\n"
        escaped = json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n"
        assert len(expected) <= contract.FRAME_BYTES < len(escaped)
        monkeypatch.setattr(agenda_cli, "_control_client", lambda: client)
        monkeypatch.setattr(agenda_cli.cfg, "project_name", lambda: "project")
        monkeypatch.setattr(agenda_cli.sys, "stdin", io.StringIO(encoded_body + "\n"))
        assert agenda_cli.cmd_agenda(SimpleNamespace(command="agenda-publish", json=True)) == 0
        output = capsys.readouterr()
        accepted = json.loads(output.out)
        assert not output.err and accepted["ok"] is True
        assert accepted["observation"] == body
        publications = [
            raw
            for raw in captured_control_requests
            if json.loads(raw)["action"] == "agenda-publish"
        ]
        assert publications == [expected]
        assert text.encode("utf-8") in publications[0]
        assert b"\\u6f22" not in publications[0]
        assert (
            client._request(
                "agenda-read",
                request(),
                max_response_bytes=contract.FRAME_BYTES,
                total_timeout=1.0,
            )
            == accepted
        )
        while True:
            raw = reader.readline(contract.FRAME_BYTES + 1)
            frame = contract.decode(raw)
            if frame["type"] != "heartbeat":
                break
        assert frame == {"type": "update", **accepted}
        assert len(raw) <= contract.FRAME_BYTES
        assert raw == contract.encode(frame).encode("utf-8") + b"\n"
        assert text.encode("utf-8") in raw
    assert owner.agendas.feed.wait_closed(1)


@pytest.mark.parametrize("action", ["health", "worktrees-read", control.RECEIPT_ACTION])
def test_legacy_control_request_encoding_remains_ascii_escaped(action, captured_control_requests):
    server = control.ControlServer(
        lambda action, payload: {"ok": True, "echo": payload},
        token="synthetic-token",
    )
    server.start()
    try:
        client = control.ControlClient(f"http://127.0.0.1:{server.port}", token=server.token)
        payload = {"text": "\u6f22\U0001f680"}
        assert client._request(action, payload) == {"ok": True, "echo": payload}
        envelope = {"version": 1, "token": server.token, "action": action, "payload": payload}
        assert captured_control_requests == [
            json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n",
        ]
    finally:
        server.close()


@pytest.mark.parametrize(
    "fault", ["text", "collection", "depth", "nonfinite", "frame", "body", "schema"]
)
def test_agenda_control_encoding_does_not_weaken_validation_bounds(owned, fault):
    owner, *_rest = owned
    body = observation()
    if fault == "text":
        body["snapshot"]["notes"] = ["\U0001f680" * 1025]
    elif fault == "collection":
        body["snapshot"]["notes"] = ["x"] * 129
    elif fault == "depth":
        value = "x"
        for _ in range(20):
            value = [value]
        body["snapshot"]["deferred"] = value
    elif fault == "nonfinite":
        body["snapshot"]["notes"] = [float("nan")]
    elif fault == "frame":
        body["snapshot"]["notes"] = ["x" * 4000] * 9
    elif fault == "body":
        body["snapshot"]["notes"] = ["x" * 4000] * 8 + ["x" * 1000]
        size = len(json.dumps(body, separators=(",", ":")).encode("utf-8")) + 1
        assert contract.BODY_BYTES < size < contract.FRAME_BYTES - 1024
    else:
        body["schema"] = "malformed-schema"
    with socket_feed(owner) as (client, reader):
        assert contract.decode(reader.readline())["availability"] == "not-published"
        if fault in {"body", "schema"}:
            rejected = client._request("agenda-publish", publication(owner, body))
            assert rejected["ok"] is False and rejected["error"]["code"] == "invalid-request"
        else:
            with pytest.raises(contract.AgendaError):
                client._request("agenda-publish", publication(owner, body))
        assert owner.agendas.read(request())["availability"] == "not-published"


def test_current_new_head_waits_on_held_socket_for_its_first_publication(owned, monkeypatch):
    owner, head, *_rest = owned
    previous = observation()
    assert owner.agendas.publish(publication(owner, previous))["ok"] is True
    head[0] = "head-b"
    owner.publish("project", [{"id": "full-worktree-id"}], observed_at=2)
    selected = request(session_id="head-b")
    waiting = owner.agendas.read(selected)
    assert waiting["availability"] == "not-published" and waiting["observation"] is None
    assert owner.agendas.read(request())["availability"] == "head-changed"
    assert owner.agendas._rows[("project", "full-worktree-id")]["body"] == previous
    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.01)
    with socket_feed(owner, selected) as (client, reader):
        snapshot = contract.decode(reader.readline(contract.FRAME_BYTES + 1))
        assert snapshot == {"type": "snapshot", **waiting}
        heartbeat = contract.decode(reader.readline(contract.FRAME_BYTES + 1))
        assert heartbeat == {
            "type": "heartbeat",
            "generation": snapshot["generation"],
            "sequence": snapshot["sequence"],
        }
        assert owner.agendas.feed.active_count() == 1
        current = observation(1, "head-b", "b" * 32)
        accepted = client._request("agenda-publish", publication(owner, current))
        assert accepted["ok"] is True
        while True:
            update = contract.decode(reader.readline(contract.FRAME_BYTES + 1))
            if update["type"] != "heartbeat":
                break
        assert update == {"type": "update", **accepted}
        assert update["sequence"] > snapshot["sequence"]
        assert update["availability"] == "observed"
        assert update["observation"] == current
    assert owner.agendas.feed.wait_closed(1)


def test_actual_held_control_socket_commit_update_heartbeat_and_drain(owned, monkeypatch):
    owner, *_rest = owned
    monkeypatch.setattr(agenda_feed, "HEARTBEAT_SECONDS", 0.01)
    with socket_feed(owner) as (client, reader):
        first = contract.decode(reader.readline(contract.FRAME_BYTES + 1))
        assert first["type"] == "snapshot" and first["availability"] == "not-published"
        reply = client._request("agenda-publish", publication(owner))
        while True:
            raw = reader.readline(contract.FRAME_BYTES + 1)
            update = contract.decode(raw)
            if update["type"] != "heartbeat":
                break
        assert len(raw) <= contract.FRAME_BYTES
        assert update == {"type": "update", **reply}
        monkeypatch.setattr(
            owner.agendas, "_head", lambda key: pytest.fail("heartbeat read a record")
        )
        heartbeat = contract.decode(reader.readline())
        assert heartbeat == {
            "type": "heartbeat",
            "generation": reply["generation"],
            "sequence": reply["sequence"],
        }
        owner.close_admission("draining")
        while True:
            notice = contract.decode(reader.readline())
            if notice["type"] != "heartbeat":
                break
        assert notice == {
            "type": "going-away",
            "generation": reply["generation"],
            "sequence": reply["sequence"],
            "reason": "draining",
        }
    assert owner.agendas.feed.wait_closed(1)


def test_committed_record_notification_invalidates_head_without_agenda_poll(owned):
    owner, head, _elected, path = owned
    stream = owner.agendas.stream(request(), lambda: False)
    owner.agendas.publish(publication(owner))
    assert next(stream)["availability"] == "observed"
    head[0] = "new-head"
    record_cache.store(path, SimpleNamespace(repo="project", worktree_id="full-worktree-id"))
    changed = next(stream)
    assert changed["type"] == "update" and changed["availability"] == "head-changed"
    assert changed["observation"] is None
    stream.close()


def test_subscriber_bound_coalescing_and_election(owned):
    owner, _head, elected, _path = owned
    streams = [owner.agendas.stream(request(), lambda: False) for _ in range(33)]
    try:
        for stream in streams[:32]:
            assert next(stream)["type"] == "snapshot"
        assert next(streams[32])["error"]["code"] == "subscriber-limit"
        for revision in range(1, 20):
            assert owner.agendas.publish(publication(owner, observation(revision)))["ok"] is True
        latest = next(streams[0])
        assert latest["observation"]["revision"] == 19
        elected[0] = False
        assert (
            owner.agendas.publish(publication(owner, observation(20)))["error"]["code"]
            == "superseded"
        )
        owner.close_admission("superseded")
        assert next(streams[0])["type"] == "going-away"
    finally:
        for stream in streams:
            stream.close()
    assert owner.agendas.feed.active_count() == 0


def test_real_record_head_resolution_hides_yielded_resume_target(owned, monkeypatch):
    owner, _head, _elected, path = owned
    record = tracking.WorktreeRecord(
        worktree_id="full-worktree-id",
        branch="worktree/full-worktree-id",
        worktree_path=str(path.parent),
        repo="project",
        machine="synthetic-machine",
        platform="linux",
        started_at="2026-11-01T00:00:00",
        last_resumed_at=None,
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[tracking.SessionEntry("head-session", "2026-11-01T00:00:00", state="yielded")],
    )
    monkeypatch.setattr(tracking, "load_record", lambda path: record)
    assert record.resolved_head_session is None
    assert owner.agendas.read(request())["availability"] == "head-changed"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"revision":1,"revision":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"[] trailing",
        b"\xff",
        b"[" * 100 + b"0" + b"]" * 100,
        b" " * (contract.BODY_BYTES + 1),
    ],
)
def test_strict_bounded_decode(raw):
    with pytest.raises(contract.AgendaError):
        contract.decode(raw, contract.BODY_BYTES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", True),
        ("committed_at", "2026-11-01T00:00:00"),
        ("committed_at", "2026-11-01T00:00:00-07:00"),
        ("schema", "unknown"),
    ],
)
def test_strict_committed_metadata(field, value):
    body = observation()
    body[field] = value
    with pytest.raises(contract.AgendaError):
        contract.observation(body)


def test_body_and_frame_exact_measured_bounds():
    body = observation()
    for size in range(1, 20):
        candidate = copy.deepcopy(body)
        candidate["snapshot"]["notes"] = ["x" * 4000] * size
        measured = len(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()) + 1
        if measured <= contract.BODY_BYTES:
            assert len(contract.observation(candidate).encode()) + 1 == measured
        else:
            with pytest.raises(contract.AgendaError, match="byte bound"):
                contract.observation(candidate)


def test_sqlite_failure_preserves_previous_commit_and_reports_fault(owned):
    owner, *_rest = owned
    accepted = owner.agendas.publish(publication(owner))
    with sqlite3.connect(owner.store.db_path) as conn:
        conn.execute(
            "CREATE TRIGGER refuse_agenda BEFORE INSERT ON worktrees_agenda_cache "
            "BEGIN SELECT RAISE(ABORT, 'synthetic cache fault'); END"
        )
    failed = owner.agendas.publish(publication(owner, observation(2)))
    assert failed["ok"] is False and "synthetic cache fault" in failed["error"]["message"]
    assert owner.agendas.read(request()) == accepted


def test_generation_fence_refuses_old_read_and_write_without_election_guess(owned):
    owner, *_rest = owned
    owner.agendas.publish(publication(owner))
    successor = WorktreesRuntime(owner.store.db_path)
    successor.activate()
    try:
        assert owner.agendas.read(request())["error"]["code"] == "superseded"
        assert (
            owner.agendas.publish(publication(owner, observation(2)))["error"]["code"]
            == "superseded"
        )
    finally:
        successor.shutdown()


def test_election_lost_during_publish_rolls_back(owned, monkeypatch):
    owner, _head, elected, _path = owned
    owner.agendas.publish(publication(owner))
    original = owner.agendas._head
    count = [0]

    def head(key):
        count[0] += 1
        if count[0] == 2:
            elected[0] = False
        return original(key)

    monkeypatch.setattr(owner.agendas, "_head", head)
    assert (
        owner.agendas.publish(publication(owner, observation(2)))["error"]["code"] == "superseded"
    )
    with sqlite3.connect(owner.store.db_path) as conn:
        assert (
            json.loads(conn.execute("SELECT payload FROM worktrees_agenda_cache").fetchone()[0])[
                "revision"
            ]
            == 1
        )


def test_control_server_strict_duplicate_input_and_wrong_token(owned):
    owner, *_rest = owned
    server = control.ControlServer(
        lambda action, payload: owner.agenda_handle(action, payload),
        token="synthetic-token",
        stream_handle=owner.stream,
    )
    server.start()
    try:
        for token, expected in (("wrong", None), (server.token, "invalid-request")):
            raw = (
                '{"version":1,"token":'
                + json.dumps(token)
                + ',"action":"agenda-read","payload":{"contract_version":1,'
                '"project":"project","project":"foreign","worktree_id":"full-worktree-id",'
                '"session_id":"head-session"}}\n'
            ).encode()
            with socket.create_connection(("127.0.0.1", server.port), timeout=1) as sock:
                sock.sendall(raw)
                received = sock.recv(contract.FRAME_BYTES)
            if expected is None:
                assert received == b""
            else:
                assert json.loads(received)["result"]["error"]["code"] == expected
    finally:
        server.close()


def test_cli_publisher_and_feed_relay_actual_held_socket(owned, monkeypatch, capsys):
    from agent_worktrees import agenda_cli

    owner, *_rest = owned
    server = control.ControlServer(
        lambda action, payload: (
            owner.health() if action == "health" else owner.agenda_handle(action, payload)
        ),
        token="synthetic-token",
        stream_handle=owner.stream,
    )
    server.start()
    client = control.ControlClient(f"http://127.0.0.1:{server.port}", token=server.token)
    monkeypatch.setattr(agenda_cli, "_control_client", lambda: client)
    monkeypatch.setattr(agenda_cli.cfg, "project_name", lambda: "project")
    emit = agenda_cli._emit

    def emitted(frame):
        emit(frame)
        if frame["type"] == "snapshot":
            import io

            monkeypatch.setattr(agenda_cli.sys, "stdin", io.StringIO(json.dumps(observation())))
            # Keep the publisher reply separate from the consumer's JSON lines.
            monkeypatch.setattr(agenda_cli, "_emit", lambda reply: None)
            assert agenda_cli.cmd_agenda(SimpleNamespace(command="agenda-publish", json=True)) == 0
            monkeypatch.setattr(agenda_cli, "_emit", emitted)
        elif frame["type"] == "update":
            owner.close_admission("draining")

    monkeypatch.setattr(agenda_cli, "_emit", emitted)
    try:
        assert (
            agenda_cli.cmd_agenda(
                SimpleNamespace(
                    command="agenda-feed",
                    worktree="full-worktree-id",
                    session="head-session",
                    json=True,
                )
            )
            == 0
        )
        frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert [frame["type"] for frame in frames] == ["snapshot", "update", "going-away"]
        assert frames[1]["observation"] == observation()
        assert owner.agendas.feed.wait_closed(1)
    finally:
        owner.close_admission()
        server.close()


def test_unreadable_changed_record_never_reexposes_cached_head(owned, monkeypatch):
    owner, _head, _elected, path = owned
    owner.agendas.publish(publication(owner))
    record_cache.store(path, SimpleNamespace(repo="project", worktree_id="full-worktree-id"))

    def unavailable(key):
        raise OSError("synthetic head read failure")

    monkeypatch.setattr(owner.agendas, "_head", unavailable)
    for _ in range(2):
        assert owner.agendas.read(request(), refresh=False)["error"]["code"] == "unavailable"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "unknown"),
        ("status", []),
        ("parked", True),
        ("declared_outcomes", []),
        ("next_revisit_at", "tomorrow"),
    ],
)
def test_committed_snapshot_types_and_recognized_values(field, value):
    body = observation()
    body["snapshot"][field] = value
    with pytest.raises(contract.AgendaError):
        contract.observation(body)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-11-03T10:30:00-07:00",
        "2026-11-03T10:30:00+05:30",
    ],
)
def test_revisit_offset_is_accepted_and_preserved_in_publication(owned, timestamp):
    owner, *_rest = owned
    body = observation()
    body["snapshot"]["next_revisit_at"] = timestamp
    assert json.loads(contract.observation(body))["snapshot"]["next_revisit_at"] == timestamp
    accepted = owner.agendas.publish(publication(owner, body))
    assert accepted["ok"] is True
    assert accepted["observation"]["snapshot"]["next_revisit_at"] == timestamp
    assert owner.agendas.read(request())["observation"] == body


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-11-03T10:30:00",
        "2026-11-03",
        "not-a-timestamp",
    ],
)
def test_revisit_naive_or_malformed_timestamp_is_rejected_without_cache_change(owned, timestamp):
    owner, *_rest = owned
    accepted = owner.agendas.publish(publication(owner))
    body = observation(2)
    body["snapshot"]["next_revisit_at"] = timestamp
    with pytest.raises(contract.AgendaError):
        contract.observation(body)
    rejected = owner.agendas.publish(publication(owner, body))
    assert rejected["ok"] is False and rejected["error"]["code"] == "invalid-request"
    assert owner.agendas.read(request()) == accepted


def test_unused_optional_projection_does_not_load_records_on_roster_publish(owned, monkeypatch):
    owner, *_rest = owned
    monkeypatch.setattr(
        owner.agendas, "_head", lambda key: pytest.fail("unused agenda loaded a head")
    )
    owner.publish("project", [{"id": "full-worktree-id"}], observed_at=2)
    assert owner.handle({"contract_version": 1, "project": "project"})["ok"] is True


def test_sequence_ceiling_refuses_changed_cache_but_keeps_idempotent_reply(owned):
    owner, *_rest = owned
    owner.agendas.publish(publication(owner))
    owner.agendas._sequence = contract.MAX_SEQUENCE
    assert owner.agendas.publish(publication(owner))["sequence"] == contract.MAX_SEQUENCE
    assert (
        owner.agendas.publish(publication(owner, observation(2)))["error"]["code"] == "unavailable"
    )
    with sqlite3.connect(owner.store.db_path) as conn:
        assert (
            json.loads(conn.execute("SELECT payload FROM worktrees_agenda_cache").fetchone()[0])[
                "revision"
            ]
            == 1
        )


def test_returning_head_retains_revision_and_store_fences(owned):
    owner, head, *_rest = owned
    assert owner.agendas.publish(publication(owner, observation(5)))["ok"] is True
    head[0] = "other-head"
    assert (
        owner.agendas.publish(publication(owner, observation(1, "other-head", "b" * 32)))["ok"]
        is True
    )
    head[0] = "head-session"
    assert (
        owner.agendas.publish(publication(owner, observation(1)))["error"]["code"]
        == "stale-revision"
    )
    assert (
        owner.agendas.publish(publication(owner, observation(6, store="c" * 32)))["error"]["code"]
        == "source-changed"
    )
    assert owner.agendas.publish(publication(owner, observation(5)))["ok"] is True


def test_source_fences_are_bounded_and_pruned_with_roster(owned, monkeypatch):
    from agent_worktrees import agenda_runtime

    owner, head, *_rest = owned
    monkeypatch.setattr(agenda_runtime, "MAX_SOURCES_PER_WORKTREE", 2)
    assert owner.agendas.publish(publication(owner))["ok"] is True
    head[0] = "other-head"
    assert owner.agendas.publish(publication(owner, observation(1, "other-head")))["ok"] is True
    head[0] = "third-head"
    assert (
        owner.agendas.publish(publication(owner, observation(1, "third-head")))["error"]["code"]
        == "source-limit"
    )
    owner.publish("project", [], observed_at=2)
    with sqlite3.connect(owner.store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM worktrees_agenda_source").fetchone()[0] == 0
