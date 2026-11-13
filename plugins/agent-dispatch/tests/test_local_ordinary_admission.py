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
    monkeypatch.setattr(native, "resolve_machine", lambda: "host-a")
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
