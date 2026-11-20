"""The native assembly admits original ordinary owners, not copied lab inputs."""

from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_dispatch import assignment_local_ordinary as native
from agent_dispatch import assignment_relationship_owner as owner_module
from agent_dispatch import ordinary_bridge, server, worktree_owner_launch
from agent_dispatch.config import Config
from agent_dispatch.queue import TaskError
from agent_dispatch.queue_task_relationships import WRITER
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_ordinary_pool_startup import api as api
from tests.test_ordinary_pool_startup import launch
from tests.test_ordinary_pool_startup import ordinary as ordinary
from tests.test_ordinary_pool_startup import q as q


@pytest.fixture
def local(ordinary, monkeypatch):
    o = ordinary
    o.q._relationship_writer = None
    o.q._ordinary_relationship_writer = native.LocalOrdinaryRelationshipWriter(o.q)
    monkeypatch.setattr(native, "local_execution_machine", lambda: "host-a")
    monkeypatch.setattr(native, "agent_bridge_launch_prefix", lambda: ("bridge",))
    monkeypatch.setattr(
        worktree_owner_launch, "retained_selection", lambda: ("selected-worktrees",)
    )
    o.ground["checkout_managed"] = None
    o.owner_calls = []
    o.resident_available = True

    def capture(argv, **kwargs):
        o.owner_calls.append(argv)
        if argv[0] == "bridge":
            return ordinary_bridge.subprocess.run(argv, **kwargs)
        assert argv[0] == "selected-worktrees"
        if "capabilities" in argv:
            result = {
                "schema": "copilot-extensions.resource-relationship",
                "version": 1,
                "operations": ["reserve", "bind", "inspect", "close"],
                "inspect_absence": True,
                "expected_creation_nonce": True,
                "expected_writer_identity": True,
                "writer_identity": dict(o.writer),
                "writer_scope": "executing-process",
                "writer_contract": WRITER,
                "metadata_version": 2,
                "resident_inspect": 1,
            }
        elif "record" in argv:
            record = copy.deepcopy(o.ground)
            record["session_ids"] = (
                ["native-a", "other-native"]
                if record["session_count"] == 2
                else ["native-a"]
                if record["session_count"]
                else []
            )
            result = {"ok": True, "worktree": record}
        else:
            payload = json.loads(kwargs["input_text"])
            request = o.q.task_relationship(o.task["id"])["request"]
            workspace = native.AssignmentWorkspace.from_dict(request["workspace"])
            if "--resident-only" in argv:
                if not o.resident_available:
                    return None
                result = {
                    "schema": "copilot-extensions.resource-relationship",
                    "version": 1,
                    "ok": True,
                    "owner": workspace.owner_ref,
                    "request_id": payload["request_id"],
                    "creation_nonce": workspace.creation_nonce,
                    "writer_contract": WRITER,
                    "writer_identity": dict(o.writer),
                    "resident": {
                        "generation": "actual-resident",
                        "runtime_generation": "resident-build",
                    },
                }
            else:
                operation = argv[argv.index("resource-relationship") + 1]
                result = o.relationship_owner(workspace, operation, payload)
                result["writer_identity"] = dict(o.writer)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(owner_module, "run_background_capture", capture)
    return o


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_original_assignment_and_native_are_admitted_through_real_callbacks(local):
    result = launch(local)
    original = local.q.task_relationship(local.task["id"])
    reservation = local.q.get_reservation(local.placed["reservation"]["key"])
    assert result["session"] == "local-body:bridge-a"
    assert original["state"] == "bound"
    assert any("--resident-only" in argv for argv in local.owner_calls)
    writer = local.q._ordinary_relationship_writer.bind_request(original["request"])
    assert (
        writer(
            native.AssignmentWorkspace.from_dict(original["request"]["workspace"]),
            "inspect",
            {"request_id": original["request"]["request_id"], "allow_missing": True},
        )["phase"]
        == "bound"
    )
    assert local.q.get_reservation(reservation.key) == reservation
    assert any("--worktree-writer" in argv for argv in local.owner_calls)


@pytest.mark.parametrize("ordinary", [True], indirect=True)
@pytest.mark.parametrize("change", ["resident", "allocation", "native", "foreign-checkout"])
def test_missing_or_unaccounted_participants_refuse_before_native_create(local, change):
    if change == "resident":
        local.resident_available = False
    else:
        original = local.q._ordinary_relationship_writer._record

        def changed(workspace, reservation, record):
            if change == "allocation":
                record["dispatch_attempt"]["reservation_key"] = "foreign"
            elif change == "native":
                record.update(session_count=1, last_session_id="unaccounted-native")
            else:
                record["checkout_managed"] = False
            original(workspace, reservation, record)

        local.q._ordinary_relationship_writer._record = changed
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="unresolved"):
        launch(local)
    retained = local.q.get_reservation(local.placed["reservation"]["key"])
    assert retained.task_relationship_event_id is not None
    assert retained.agent_info is None
    assert retained.worktree_writer_requirement is not None
    assert not any(argv[2] in {"create", "send"} for argv, _ in local.calls)
    assert local.q.task_relationship(local.task["id"])["state"] == "pending"


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_actual_journal_not_future_bridge_selection_admits_original_native(local):
    launch(local)
    request = local.q.task_relationship(local.task["id"])["request"]
    # A compatible current CLI and the original retained Bridge build are
    # separate participants; they need not be the same build.
    local.writer["runtime_generation"] = "b" * 32
    writer = local.q._ordinary_relationship_writer.bind_request(request)
    writer(
        native.AssignmentWorkspace.from_dict(request["workspace"]),
        "inspect",
        {"request_id": request["request_id"], "allow_missing": True},
    )
    assert any("--worktree-writer" in argv for argv in local.owner_calls)
    assert (
        local.q.get_reservation(request["reservation_key"]).worktree_writer_requirement[
            "identity"
        ]["runtime_generation"]
        == "a" * 32
    )


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_request_mutation_and_additional_native_keep_original_claim(local):
    launch(local)
    request = local.q.task_relationship(local.task["id"])["request"]
    original_claims = copy.deepcopy(local.relationship_owner.claims)
    changed = {**request, "generation": request["generation"] + 1}
    with pytest.raises(TaskError, match="original queue"):
        local.q._ordinary_relationship_writer.bind_request(changed)
    writer = local.q._ordinary_relationship_writer.bind_request(request)
    local.ground["session_count"] = 2
    with pytest.raises(TaskError, match="unaccounted native"):
        writer(
            native.AssignmentWorkspace.from_dict(request["workspace"]),
            "inspect",
            {"request_id": request["request_id"], "allow_missing": True},
        )
    assert local.relationship_owner.claims == original_claims
    assert local.q.task_relationship(local.task["id"])["state"] == "bound"


@pytest.mark.parametrize("ordinary", [True], indirect=True)
@pytest.mark.parametrize("native_id", ["native-a", "foreign-yielded-native", None])
def test_null_head_never_substitutes_for_the_indexed_session_identity(local, native_id):
    launch(local)
    request = local.q.task_relationship(local.task["id"])["request"]
    before = copy.deepcopy(local.relationship_owner.claims)
    original = local.q._ordinary_relationship_writer._record

    def yielded(workspace, reservation, record):
        record["last_session_id"] = None
        if native_id is None:
            record.pop("session_ids")
        else:
            record["session_ids"] = [native_id]
        original(workspace, reservation, record)

    local.q._ordinary_relationship_writer._record = yielded
    writer = local.q._ordinary_relationship_writer.bind_request(request)
    workspace = native.AssignmentWorkspace.from_dict(request["workspace"])
    payload = {"request_id": request["request_id"], "allow_missing": True}
    if native_id == "native-a":
        assert writer(workspace, "inspect", payload)["phase"] == "bound"
    else:
        with pytest.raises(TaskError, match="native"):
            writer(workspace, "inspect", payload)
    assert local.relationship_owner.claims == before


@pytest.mark.parametrize("ordinary", [True], indirect=True)
@pytest.mark.parametrize("binding", ["machine", "supervisor.env"])
def test_native_writer_uses_durable_machine_binding_from_neutral_service_cwd(
    local,
    monkeypatch,
    binding,
):
    from agent_dispatch import identity, ordinary_pool_startup

    value = "HOST-A\n" if binding == "machine" else "AGENT_DISPATCH_SUPERVISE_MACHINE='HOST-A'\n"
    (local.install_root / binding).write_text(value, encoding="utf-8")
    monkeypatch.chdir(local.install_root)
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISE_MACHINE", "ambient-other-host")
    monkeypatch.setattr(
        native, "local_execution_machine", ordinary_pool_startup.local_execution_machine
    )

    def no_project():
        pytest.fail("native relationship admission probed project identity from the service cwd")

    monkeypatch.setattr(identity, "resolve_machine", no_project)
    assert launch(local)["session"] == "local-body:bridge-a"
    request = local.q.task_relationship(local.task["id"])["request"]
    writer = local.q._ordinary_relationship_writer.bind_request(request)
    assert (
        writer(
            native.AssignmentWorkspace.from_dict(request["workspace"]),
            "inspect",
            {"request_id": request["request_id"], "allow_missing": True},
        )["phase"]
        == "bound"
    )


@pytest.mark.parametrize("ordinary", [True], indirect=True)
@pytest.mark.parametrize("binding", ["foreign", "directory", "invalid-utf8"])
def test_binding_drift_during_native_admission_never_falls_back_to_project(
    local,
    monkeypatch,
    binding,
):
    from agent_dispatch import identity, ordinary_pool_startup

    launch(local)
    request = local.q.task_relationship(local.task["id"])["request"]
    before = copy.deepcopy(local.relationship_owner.claims)
    path = local.install_root / "machine"
    if binding == "foreign":
        path.write_text("other-host\n", encoding="utf-8")
    elif binding == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff")
    monkeypatch.setattr(
        native, "local_execution_machine", ordinary_pool_startup.local_execution_machine
    )
    monkeypatch.setattr(
        identity, "resolve_machine", lambda: pytest.fail("binding failure fell back")
    )
    with pytest.raises(TaskError, match="host-local|binding is unreadable"):
        local.q._ordinary_relationship_writer.bind_request(request)
    assert local.relationship_owner.claims == before
    assert local.q.task_relationship(local.task["id"])["state"] == "bound"


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_yield_does_not_falsely_retire_the_original_native_or_reservation(local):
    launch(local)
    request = local.q.task_relationship(local.task["id"])["request"]
    local.client.start_pool_task(
        local.task["id"],
        "host-a/worktree-a",
        owner_session_id="native-a",
        expected_generation=local.task["generation"],
    )
    local.q.yield_task(local.task["id"], "host-a/worktree-a")
    before = local.q.get_reservation(request["reservation_key"])
    assert local.q.reconcile_task_relationship(local.task["id"])["state"] == "bound"
    after = local.q.get_reservation(request["reservation_key"])
    assert after == before
    assert after.agent_info is not None and after.session_handle == "local-body:bridge-a"
    assert not any(operation == "close" for operation, _ in local.relationship_owner.calls)


def test_serve_selects_native_ordinary_explicitly_without_launching(monkeypatch, tmp_path):
    monkeypatch.setattr(server.telemetry, "load_sink_from_config", lambda: True)
    queues = []

    def app(queue, **kwargs):
        queues.append(queue)
        return SimpleNamespace(state=SimpleNamespace())

    monkeypatch.setattr(server, "create_app", app)
    monkeypatch.setattr(
        native, "agent_bridge_launch_prefix", lambda: pytest.fail("startup observed Bridge")
    )
    cfg = Config(
        db_path=str(tmp_path / "queue.db"),
        relationship_admission="local-ordinary",
        relationship_authority="local",
    )
    server.build_app(cfg)
    assert queues[0]._relationship_writer is None
    assert isinstance(
        queues[0]._ordinary_relationship_writer, native.LocalOrdinaryRelationshipWriter
    )
    assert queues[0]._ordinary_relationship_writer.queue is queues[0]
    assert queues[0]._standing_relationship_writer is not None
    requested = queues[0].create("manual", repo="example")
    assert queues[0].claim_one("manual-worker", task_id=requested.id).id == requested.id
    from tests.test_pool_placement import place, pool, publish

    publish(queues[0], pool(capacity=2))
    cli, _ = place(queues[0], queues[0].create("CLI", repo="example"))
    assert cli.task_relationship_required is False
    ordinary, _ = queues[0].reserve_spawn(
        queues[0].create("ordinary", repo="example").id,
        reserved_by="allocator",
        driver="ordinary-bridge",
        pool_catalog_revision=queues[0].pool_catalog_snapshot().revision,
    )
    assert ordinary.task_relationship_required is True
    server.build_app(replace(cfg, relationship_admission=None, relationship_authority=None))
    assert queues[1]._relationship_writer is None
    with pytest.raises(ValueError, match="local queue authority"):
        server.build_app(replace(cfg, relationship_authority="central"))
    assert len(queues) == 2


@pytest.mark.parametrize("spec", [
    "local-ordinary-instance=",
    'local-ordinary-instance={"registration_id":"worker","machine":"host-a"}',
    'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"*"}',
    'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"linux","env":"wsl"}',
    'local-ordinary-instance={"registration_id":"worker","machine":null,"env":"linux"}',
    'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"linux","all":true}',
])
def test_invalid_capture_refuses_before_queue_construction(tmp_path, spec):
    database = tmp_path / "never-created.db"
    with pytest.raises(ValueError, match="exact pool instance"):
        server.build_app(Config(db_path=str(database), relationship_authority="local", relationship_admission=spec))
    assert not database.exists()


def test_exact_winner_is_prospective_across_reconfiguration(tmp_path, monkeypatch):
    from agent_dispatch.assignment_admission import configured_writer
    from tests.test_pool_placement import pool, publish

    monkeypatch.setattr(server.telemetry, "load_sink_from_config", lambda: True)
    monkeypatch.setattr(server, "require_worker_admission", lambda: None)
    monkeypatch.setattr(server, "create_app", lambda queue, **_: SimpleNamespace(state=SimpleNamespace(queue=queue)))
    cfg = Config(db_path=str(tmp_path / "queue.db"))
    old = server.build_app(cfg).state.queue
    revision = publish(
        old, pool(capacity=4, priority=1), pool(name="other"), pool(env="wsl"), pool(machine="host-b"),
    )

    def reserve(queue, task=None, driver="ordinary-bridge"):
        return queue.reserve_spawn(
            (task or queue.create("work", repo="example")).id, reserved_by="allocator",
            driver=driver, pool_catalog_revision=revision,
        )

    selector = 'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"linux"}'
    # Coordinators predating scoped capture route both unfamiliar values to the factory parser.
    for spec in (selector, "local-ordinary-drain"):
        with pytest.raises(ValueError, match="module:factory"):
            configured_writer(spec, "local")
    cfg = replace(cfg, relationship_authority="local", relationship_admission=selector)
    with pytest.raises(ValueError, match="local queue authority"):
        server.build_app(replace(cfg, relationship_authority="central"))
    legacy, _ = reserve(old)
    queue = server.build_app(cfg).state.queue
    replay, created = reserve(queue, queue.get(legacy.task_id))
    assert not created and replay == legacy and not replay.task_relationship_required
    with pytest.raises(TaskError, match="legacy reservation"):
        queue.record_spawn_worktree(legacy.key, "late", creation_nonce="late")
    cli, _ = reserve(queue, driver="cli")
    assert not cli.task_relationship_required
    selected, _ = reserve(queue)
    assert selected.task_relationship_required
    assert queue.task_pool_assignment(selected.task_id).registration_id == "worker"
    drained = server.build_app(replace(cfg, relationship_admission="local-ordinary-drain")).state.queue
    assert reserve(drained, drained.get(selected.task_id)) == (selected, False)
    drain_new, _ = reserve(drained)
    assert not drain_new.task_relationship_required
    for expected in [("other", "host-a", "linux"), ("worker", "host-a", "wsl"), ("worker", "host-b", "linux")]:
        other, _ = reserve(queue)
        assignment = queue.task_pool_assignment(other.task_id)
        assert (assignment.registration_id, assignment.machine, assignment.env) == expected
        assert not other.task_relationship_required


@pytest.mark.parametrize("ordinary", ["scoped"], indirect=True)
def test_scoped_original_receipt_and_source_closeout_survive_drain(local, monkeypatch):
    from agent_dispatch import submission_readiness_observation
    from agent_dispatch.ordinary_capture import configured_capture
    from agent_dispatch.queue_relevance import RelevanceScope
    from tests.test_task_relevance import report

    queue = local.q
    assert local.placed["reservation"]["task_relationship_required"]
    queue._ordinary_capture = configured_capture("local-ordinary-drain", "local")
    assert local.client.health()["task_relationships"]["scope"] == "local-ordinary-drain"
    launch(local)
    key = local.placed["reservation"]["key"]
    reservation = queue.get_reservation(key)
    assert reservation.worktree_writer_requirement == {
        "version": 1, "identity": local.writer, "creation_nonce": reservation.creation_nonce,
    }
    assert reservation.agent_info["worktree_writer_requirement"] == reservation.worktree_writer_requirement
    queue.record_spawn(
        key, session_handle=reservation.session_handle, worktree=reservation.worktree,
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    task = queue.get(local.task["id"])
    original = queue.task_relationship(task.id)
    assert original["state"] == "bound"
    worker = "host-a/worktree-a"
    local.client.start_pool_task(task.id, worker, owner_session_id="native-a", expected_generation=task.generation)
    report(queue, task.id, scope=RelevanceScope(task.repo, task.source, task.origin_ref))
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    result = queue.prepare_relevance_closeout(
        task.id, request_id="closed-source", worker_id=worker, native_id="native-a",
        expected_generation=queue.get(task.id).generation,
    )
    assert result["relationship"] == {"state": "closed", "event_id": original["request"]["event_id"]}
    assert queue.get_reservation(key).worktree_writer_requirement == reservation.worktree_writer_requirement


@pytest.mark.parametrize("initially_selected", [False, True])
def test_retry_of_same_allocation_keeps_initial_capture(q, initially_selected):
    from agent_dispatch.ordinary_capture import OrdinaryCapture
    from tests.test_pool_placement import pool, publish
    from tests.test_pool_startup_policy import exhaust, renew

    winner = pool()
    revision = publish(q, winner)
    selected = OrdinaryCapture("local-ordinary-instance", winner.identity)
    drain = OrdinaryCapture("local-ordinary-drain")
    q._ordinary_relationship_writer = native.LocalOrdinaryRelationshipWriter(q)
    q._ordinary_capture = selected if initially_selected else drain
    task = q.create("work", repo="example")
    options = {"reserved_by": "allocator", "driver": "ordinary-bridge"}
    original, _ = q.reserve_spawn(task.id, pool_catalog_revision=revision, **options)
    assert original.task_relationship_required is initially_selected
    queued = exhaust(q, q.get(task.id), original)
    q.request_spawn_release(original.key, finished_startup_revision=1, now=701)
    q.retire_spawn(original.key, exact_absence=True, now=702)
    renew(q, queued.not_before)
    q._ordinary_capture = drain if initially_selected else selected
    retry, created = q.reserve_spawn(
        task.id, pool_catalog_revision=q.pool_catalog_snapshot().revision,
        now=queued.not_before, **options,
    )
    assert created and retry.key != original.key and retry.allocation_reservation_key == original.key
    assert retry.task_relationship_required is initially_selected
