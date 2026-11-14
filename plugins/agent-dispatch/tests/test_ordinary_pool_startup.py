"""Ordinary first-turn admission over real placed claims and HTTP storage."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import bridge, identity, ordinary_bridge
from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.ordinary_agent_storage import reservation_native_facts
from agent_dispatch.ordinary_assignment import current_task
from agent_dispatch.ordinary_invocation import startup_seed
from agent_dispatch.pool_startup_contract import pool_startup_contract
from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.queue_pool_placement import pool_placement_contract
from agent_dispatch.supervisor_pool_startup import supervise_pool_startup
from tests import test_pool_placement_client as controls
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_pool_placement import pool, publish, task

q = controls.q
api = controls.api


@pytest.fixture
def ordinary(
    q: TaskQueue, api: TestClient, monkeypatch: pytest.MonkeyPatch, admission_owner: SimpleNamespace,
    tmp_path: Path, request: pytest.FixtureRequest,
) -> Iterator[SimpleNamespace]:
    relationship_owner = None
    governed = getattr(request, "param", False)
    if governed:
        from tests.test_assignment_relationships import GroundOwner
        relationship_owner = GroundOwner()
        relationship_owner.queue = q
        q._relationship_writer = relationship_owner
        q._relationship_authority = "local"
    revision = publish(q, pool())
    requested = task(q, **({"require_verification": True} if governed else {}))
    requests, calls = [], []
    client = controls.connected(api, requests)
    placed = client.place_pool_task(
        requested.id, reserved_by="allocator", catalog_revision=revision, driver="ordinary-bridge",
        **({"session_origin_transport": "prefer-origin-v1"} if getattr(request, "param", False) == "origin-v1" else {}),
    )
    snapshot = {**asdict(q.get(requested.id)), "spawn_reservation_key": placed["reservation"]["key"]}
    install_root = tmp_path / "dispatch-install"
    install_root.mkdir()
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(install_root))
    monkeypatch.setattr(identity, "resolve_machine", lambda: "host-a")
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *args, **kwargs: None)
    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["bridge"])
    from agent_dispatch import embody, ordinary_assignment

    allocations = []
    creation_records = []
    ground = {
        "id": "worktree-a", "repo": "example", "path": str(admission_owner.workspace),
        "status": "active", "creation_nonce": "allocation-a", "session_count": 0,
        "last_session_id": None, "execution_leg": None, "execution_leg_opaque": False,
        "session_backend": None, "session_backend_opaque": False, "sessions_indexed": True,
        "dispatch_attempt_opaque": False,
        "dispatch_attempt": {"task_id": requested.id},
    }

    def create_workspace(**kwargs: Any) -> dict:
        assert q.get(requested.id).status == Status.CLAIMED
        assert not any(argv[2] in {"create", "send"} for argv, _ in calls)
        allocations.append(kwargs)
        ground["dispatch_attempt"] = {
            "task_id": kwargs["task_id"], "reservation_key": kwargs["reservation_key"],
            "attempt": kwargs["attempt"], "driver": kwargs["driver"],
            "supervisor": kwargs["supervisor"], "creator_machine": "host-a", "ownership": "created",
            **({"cohort": kwargs["allocation_cohort"]} if kwargs.get("cohort_transport") == "cohort-v1" else {}),
        }
        return {
            "worktree": ground["id"], "project": ground["repo"], "path": ground["path"],
            **({"creation_nonce": ground["creation_nonce"]} if governed else {}),
        }

    monkeypatch.setattr(embody, "create_worktree", create_workspace)
    monkeypatch.setattr(ordinary_assignment, "read_owner", lambda *a, **k: dict(ground))

    writer = {"installation_root": "/worktrees", "runtime_generation": "a" * 32}

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        if argv[2] == "version":
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "schema": "agent-bridge.cli-contracts", "version": 1,
                "contracts": {
                    "agent-bridge.create": {"version": 1, "capabilities": ["keyed-worktree-writer"]},
                    "agent-bridge.status": {
                        "version": 1, "capabilities": ["health-json", "exact-session-worktree-writer"],
                    },
                    "agent-bridge.resume": {"version": 1, "capabilities": ["keyed-worktree-writer"]},
                },
            }), "")
        if argv[2] == "status":
            assert governed
            value = {
                "service": "agent-bridge", "status": "ok", "ready": True, "draining": False,
                "slot": {"role": "active", "pid": 42, "active": {"pid": 42, "generation": 7}},
                "capabilities": {
                    "worktree_record_delegation": 1, "retained_workspace_creation_writer": 1,
                    "retained_workspace_creation_writer_inspection": 1,
                },
                "worktree_record_writer": writer,
            }
            if "--full" in argv:
                from agent_dispatch.queue_task_relationships import WRITER
                stored = q.get_reservation(placed["reservation"]["key"])
                value = {
                    "session_id": "bridge-a", "acp_session_id": "native-a",
                    "durable_session_id": "native-a", "worktree_id": ground["id"],
                    "project": ground["repo"], "target_dir": ground["path"],
                    "worktree_writer_observation": {
                        "schema": "copilot-extensions.creation-worktree-writer", "version": 1,
                        "session_id": "bridge-a", "native_session_id": "native-a",
                        "bridge_instance": {"installation_root": "/bridge", "pid": 42, "generation": 7},
                        "project": ground["repo"], "worktree_id": ground["id"],
                        "worktree_path": ground["path"],
                        "worktree_writer": stored.worktree_writer_requirement,
                        "writer_scope": "executing-process", "writer_contract": WRITER,
                        "retained_session_writes": 1,
                    },
                }
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        if argv[2] == "create":
            assert "--prompt-file" not in argv
            if governed:
                retained = q.get_reservation(placed["reservation"]["key"])
                assert json.loads(kwargs["input"]) == retained.worktree_writer_requirement
                assert argv[argv.index("--worktree-writer-file") + 1] == "-"
                assert q.task_relationship(requested.id)["state"] == "bound"
            else:
                assert not kwargs.get("input")
            assert q.get(requested.id).status == Status.CLAIMED
            if not creation_records:
                creation_records.append(dict(ground))
            ground.update(session_count=1, last_session_id="native-a", session_live=True)
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
                "creation": {
                    "version": 1, "status": "complete", "worktree": creation_records[0],
                    "native_session_id": "native-a", "turn_index": None,
                },
                "connection": {
                    "worktree_id": "worktree-a", "repo": "example",
                    "native_session_id": "native-a", "target_dir": str(admission_owner.workspace),
                },
            }), "")
        assert argv[2] == "send" and "--native-invocation-file" in argv
        invocation = json.loads(kwargs["input"])
        stored = q.get_reservation(placed["reservation"]["key"])
        assert stored.driver == "ordinary-bridge" and stored.execution_machine == "host-a"
        assert stored.session_handle == "local-body:bridge-a"
        assert stored.agent_info["startup_invocation"]["prompt"] == invocation["prompt"]
        if "--receipt-only" in argv:
            assert q.get(requested.id).status == Status.STARTED
            assert q.pool_startup(requested.id).startup_attempts == 0
        else:
            assert q.get(requested.id).status == Status.CLAIMED and q.get(requested.id).owner is None
            assert q.pool_startup(requested.id).startup_attempts == 1
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "version": 1, "session_id": "bridge-a", "native_session_id": "native-a",
            "invocation_key": invocation["invocation_key"], "outcome": "delivered",
            "queued": False, "admission": "accepted", "turn_index": 0,
        }), "")

    monkeypatch.setattr(ordinary_bridge, "subprocess", SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError,
    ))
    yield SimpleNamespace(
        q=q, api=api, task=snapshot, client=client, requests=requests, calls=calls, placed=placed,
        install_root=install_root,
        allocations=allocations, ground=ground,
        creation_records=creation_records,
        writer=writer, relationship_owner=relationship_owner,
    )
    client.close()


def launch(ordinary: SimpleNamespace) -> dict:
    seed = startup_seed(ordinary.task, ordinary.client, route=" --shared")
    return ordinary_bridge.start(
        ordinary.task, client=ordinary.client, agent="local", project="example", prompt=seed,
    )


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_before_start_source_closeout_targets_the_original_ordinary_native(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import owner_wake_delivery
    from agent_dispatch.queue_relevance import RelevanceScope
    from tests.test_task_relevance import report

    o = ordinary
    result = launch(o)
    assert result["native_session_id"] == "native-a"
    key = o.placed["reservation"]["key"]
    stored = o.q.get_reservation(key)
    o.q.record_spawn(
        key, session_handle=stored.session_handle, worktree=stored.worktree,
        startup_complete=True, startup_revision=stored.startup_revision,
    )
    with o.q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET source='emitter', origin_ref='review-source' WHERE id=?",
            (o.task["id"],),
        )
    before = o.q.get(o.task["id"])
    accepted = report(
        o.q, before.id, scope=RelevanceScope(before.repo, before.source, before.origin_ref),
    )
    [wake] = o.q.list_wakes(before.id)
    assert accepted["wake_operation_id"] == wake.id
    assert (wake.owner, wake.owner_session_id) == ("host-a/worktree-a", "native-a")
    calls = []

    def send(bridge_id: str, native_id: str, prompt: str, **kwargs: Any) -> bool:
        calls.append((bridge_id, native_id, prompt, kwargs))
        return True

    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(owner_wake_delivery, "native_prompt", send)
    assert owner_wake_delivery.deliver_owner_wake(
        o.q, wake.owner, wake.owner_session_id, before.id, wake.message, wake.id,
    )
    [sent] = calls
    assert sent[:2] == ("bridge-a", "native-a")
    assert "do not start or resume the goal" in sent[2]
    assert sent[3]["idempotency_key"] == wake.id
    assert o.q.get(before.id).status == "claimed"
    assert o.q.get(before.id).owner is o.q.get(before.id).owner_session_id is None
    assert not o.q.attachment_history(before.id)


@pytest.mark.parametrize("ordinary", ["origin-v1"], indirect=True)
def test_governed_create_composes_sealed_origin_file_with_writer_stdin(ordinary, monkeypatch):
    from agent_dispatch import session_origin

    monkeypatch.setattr(session_origin, "retained_selection", lambda: ("bridge",))
    monkeypatch.setattr(session_origin, "require_support", lambda _prefix, **kwargs: None)
    original = ordinary_bridge.subprocess.run
    seen = []

    def run(argv, **kwargs):
        if argv[2] == "create":
            path = Path(argv[argv.index("--dispatch-origin-file") + 1])
            origin = json.loads(path.read_text(encoding="utf-8"))
            stored = ordinary.q.get_reservation(ordinary.placed["reservation"]["key"])
            assert origin == stored.session_origin
            assert json.loads(kwargs["input"]) == stored.worktree_writer_requirement
            seen.append(path)
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    result = launch(ordinary)
    assert result["native_session_id"] == "native-a"
    assert len(seen) == 1 and not seen[0].exists()
    assert any(argv[2] == "send" for argv, _ in ordinary.calls)


def test_original_cohort_receipt_reaches_ordinary_initial_prompt(ordinary, monkeypatch):
    from agent_dispatch import embody, worktree_cohort

    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["/fixture/python", "-m", "agent_worktrees"])
    monkeypatch.setattr(worktree_cohort, "_supports_cohort", lambda _prefix: True)
    launch(ordinary)
    stored = ordinary.q.get_reservation(ordinary.placed["reservation"]["key"])
    assert stored.allocation_cohort_transport == "cohort-v1"
    assert ordinary.creation_records[0]["dispatch_attempt"]["cohort"] == stored.allocation_cohort
    assert any(argv[2] == "send" for argv, _ in ordinary.calls)


@pytest.mark.parametrize("change", ["missing", "foreign", "ownership"])
def test_cohort_receipt_keeps_original_allocation_checks(ordinary, monkeypatch, change):
    from copy import deepcopy
    from agent_dispatch import embody, worktree_cohort
    from agent_dispatch.ordinary_pool_startup import created_workspace_record

    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: ["/fixture/python", "-m", "agent_worktrees"])
    monkeypatch.setattr(worktree_cohort, "_supports_cohort", lambda _prefix: True)
    launch(ordinary)
    stored = ordinary.client.get_reservation(ordinary.placed["reservation"]["key"])
    record = deepcopy(ordinary.creation_records[0])
    if change == "missing":
        record["dispatch_attempt"].pop("cohort")
    elif change == "foreign":
        record["dispatch_attempt"]["cohort"]["pool"]["registration_id"] = "foreign"
    else:
        record["dispatch_attempt"]["ownership"] = "reused"
    info = {
        "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
        "creation": {
            "version": 1, "status": "complete", "native_session_id": "native-a",
            "turn_index": None, "worktree": record,
        },
        "connection": {"native_session_id": "native-a", "worktree_id": "worktree-a", "repo": "example"},
    }
    with pytest.raises(TaskError, match="original sessionless allocation"):
        created_workspace_record(ordinary.task, stored, info)


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_governed_start_retains_selected_writer_before_first_relationship(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    observed = []
    owner = o.q._relationship_writer

    def admit(workspace: Any, operation: str, payload: dict) -> dict:
        retained = o.q.get_reservation(o.placed["reservation"]["key"])
        assert retained.worktree_writer_requirement == {
            "version": 1, "identity": o.writer, "creation_nonce": o.ground["creation_nonce"],
        }
        assert retained.task_relationship_event_id is not None
        assert not any(argv[2] == "create" for argv, _ in o.calls)
        observed.append(operation)
        return owner(workspace, operation, payload)

    monkeypatch.setattr(o.q, "_relationship_writer", admit)
    assert launch(o)["native_session_id"] == "native-a"
    assert observed == ["reserve", "bind"]
    assert [argv[2] for argv, _ in o.calls][:2] == ["version", "status"]
    assert len(o.allocations) == 1
    retained = o.q.get_reservation(o.placed["reservation"]["key"])
    assert retained.agent_info["worktree_writer_requirement"] == retained.worktree_writer_requirement


@pytest.mark.parametrize("ordinary", [True], indirect=True)
@pytest.mark.parametrize("versions", [None, [], [True], ["1"]])
def test_governed_start_refuses_old_preparation_owner_before_workspace_effect(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, versions: object,
) -> None:
    o = ordinary
    monkeypatch.setattr(o.client, "pool_placement_contract", lambda: {
        **pool_placement_contract(), "prepared_writer_requirement_versions": versions,
    })
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="prepared ordinary writer"):
        launch(o)
    assert not o.allocations and not o.calls and not o.relationship_owner.calls


@pytest.mark.parametrize("ordinary", [True], indirect=True)
@pytest.mark.parametrize("change", ["cli", "identity"])
def test_governed_start_refuses_unqualified_selected_bridge_before_workspace_effect(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    o = ordinary
    run = ordinary_bridge.subprocess.run

    def unsupported(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result = run(argv, **kwargs)
        value = json.loads(result.stdout)
        if change == "cli" and argv[2] == "version":
            value["contracts"]["agent-bridge.resume"]["capabilities"] = []
        elif change == "identity" and argv[2] == "status":
            value["worktree_record_writer"]["runtime_generation"] = True
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", unsupported)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="unconfirmed"):
        launch(o)
    assert not o.allocations and not o.relationship_owner.calls


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_governed_lost_relationship_reply_reuses_original_requirement_after_selection_moves(
    ordinary: SimpleNamespace,
) -> None:
    o = ordinary
    o.relationship_owner.lose_reply = "reserve"
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="unresolved"):
        launch(o)
    original = o.q.get_reservation(o.placed["reservation"]["key"])
    requirement = original.worktree_writer_requirement
    o.writer.update(installation_root="/new", runtime_generation="b" * 32)
    assert launch(o)["native_session_id"] == "native-a"
    retained = o.q.get_reservation(original.key)
    assert retained.creation_key == original.creation_key
    assert retained.worktree_writer_requirement == requirement
    assert len(o.allocations) == 1 and len(o.relationship_owner.claims) == 1


@pytest.mark.parametrize("ordinary", [True], indirect=True)
@pytest.mark.parametrize("field", ["identity", "creation_nonce"])
def test_governed_explicit_requirement_cannot_replace_observed_preparation(
    ordinary: SimpleNamespace, field: str,
) -> None:
    o = ordinary
    requirement = {
        "version": 1, "identity": dict(o.writer), "creation_nonce": o.ground["creation_nonce"],
    }
    if field == "identity":
        requirement[field]["runtime_generation"] = "b" * 32
    else:
        requirement[field] = "different"
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="differs"):
        ordinary_bridge.start(
            o.task, client=o.client, agent="local", project="example",
            prompt=startup_seed(o.task, o.client), worktree_writer=requirement,
        )
    assert len(o.allocations) == (0 if field == "identity" else 1)
    assert not o.relationship_owner.calls
    assert o.q.get_reservation(o.placed["reservation"]["key"]).agent_info is None


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_governed_preparation_still_requires_independent_relationship_admission(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary

    def unqualified(*args: Any) -> dict:
        raise TaskError("participating cohort is unconfirmed")

    monkeypatch.setattr(o.q, "_relationship_writer", unqualified)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="unresolved"):
        launch(o)
    retained = o.q.get_reservation(o.placed["reservation"]["key"])
    assert retained.worktree_writer_requirement["identity"] == o.writer
    assert retained.task_relationship_event_id is not None
    assert not o.relationship_owner.calls
    assert not any(argv[2] in {"create", "send"} for argv, _ in o.calls)


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_governed_selection_movement_during_preparation_refuses_first_native_create(
    ordinary: SimpleNamespace,
) -> None:
    o = ordinary

    def moved(operation: str, result: dict) -> None:
        if operation == "bind":
            o.writer.update(runtime_generation="b" * 32)

    o.relationship_owner.before_reply = moved
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="differs from the admitted writer"):
        launch(o)
    retained = o.q.get_reservation(o.placed["reservation"]["key"])
    assert retained.worktree_writer_requirement["identity"]["runtime_generation"] == "a" * 32
    assert retained.agent_info is None and len(o.allocations) == 1
    assert not any(argv[2] in {"create", "send"} for argv, _ in o.calls)


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_governed_historical_workspace_is_not_retrospectively_pinned(ordinary: SimpleNamespace) -> None:
    from agent_dispatch.ordinary_pool_startup import prepare_owned_workspace

    o = ordinary
    reservation = o.client.get_reservation(o.placed["reservation"]["key"])
    prepare_owned_workspace(
        o.task, reservation, client=o.client, project="example",
        machine="host-a", no_pair=False, timeout=180,
    )
    before = o.q.get_reservation(reservation["key"])
    assert before.worktree_writer_requirement is None
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="lacks its qualified writer"):
        launch(o)
    assert o.q.get_reservation(reservation["key"]) == before
    assert not o.calls and len(o.allocations) == 1


def released_pool_seed(task_id: str, *, generation: int, route: str = "",
                       repo: str | None = None, all_repos: bool = False) -> str:
    from agent_dispatch.embody_prompts import AUTOPILOT_CHARTER_NAME, _charter_read_step

    ad = "agent-dispatch" + route
    lane = " --all-repos" if all_repos else (f" --repo {repo}" if repo else "")
    return (
        f"You are a dispatched autopilot worker. Task {task_id} is already allocator-CLAIMED "
        f"at generation {generation}; it is not queued for a new worker claim. "
        f"Use `{ad}` for every command so routing stays with the original coordinator. "
        "Use this worktree's own machine/worktree identity, not a synthetic worker id. "
        f"{_charter_read_step(ad, task_charter=AUTOPILOT_CHARTER_NAME, concise=False)}"
        f"Read `{ad} show {task_id}` and evaluate the charter's contract-net checks, "
        f"including `{ad} list{lane}` for the duplicate sweep. Do not acquire another "
        "claim or evaluation lease. Only if the task is still at the assigned generation, "
        "CLAIMED and unowned, and you independently ACCEPT its contract, use "
        f"`{ad} start {task_id} --pool-generation {generation}` with the actual retained "
        "native session identity supplied below. Startup or prompt delivery is not acceptance. "
        "If you cannot accept, retain the assignment and report the blocker; do not use "
        "legacy yield/abandon as a substitute for pool-aware decline and workspace closeout. "
        f"After acceptance, take steering with `{ad} steer take {task_id} --all`, record "
        f"status beats with `{ad} progress {task_id} --phase <phase> --summary "
        f'"<one line>", and only submit with `{ad} complete {task_id} --result-ref <ref>` '
        "once the charter's actual goal and completion gates are met. Preserve all holds, "
        "workspace/PR obligations and effect custody."
    )


def test_real_ordinary_create_store_send_and_affirmative_worker_start(ordinary: SimpleNamespace) -> None:
    o = ordinary
    with pytest.raises(DispatchError):
        o.client.start_pool_task(o.task["id"], "host-a/worktree-a",
                                owner_session_id="native-a", expected_generation=o.task["generation"])
    result = launch(o)
    assert len(o.allocations) == 1
    assert {
        key: o.allocations[0][key]
        for key in ("task_id", "reservation_key", "attempt", "driver", "supervisor")
    } == {
        "task_id": o.task["id"], "reservation_key": o.placed["reservation"]["key"],
        "attempt": 1, "driver": "ordinary-bridge", "supervisor": "allocator",
    }
    assert o.q.get_reservation(o.placed["reservation"]["key"]).worktree_ownership == "created"
    assert [argv[2] for argv, _ in o.calls] == ["create", "send"]
    assert "--worktree-id" in o.calls[0][0]
    prompt = json.loads(o.calls[-1][1]["input"])["prompt"]
    assert "--evaluation" not in prompt and "claim --task" not in prompt
    assert f"start {o.task['id']} --pool-generation 1" in prompt
    assert "--owner-session-id native-a" in prompt and "synthetic worker id" in prompt
    assert "agent-dispatch --shared list --repo example" in prompt
    assert "legacy yield/abandon" in prompt
    assert result["session"] == "local-body:bridge-a"
    o.client.record_spawn(
        o.placed["reservation"]["key"], session_handle=result["session"], worktree=result["worktree"],
        startup_complete=True, startup_revision=1, session_info=result["session_info"],
    )
    assert o.q.get(o.task["id"]).status == Status.CLAIMED
    with controls.connected(o.api, [], control=False) as worker:
        for _ in range(2):
            accepted = worker.start_pool_task(
                o.task["id"], "HOST-A/worktree-a",
                owner_session_id="native-a", expected_generation=1,
            )
            assert accepted["status"] == Status.STARTED
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 0
    assert len(o.q.attachment_history(o.task["id"])) == 1
    assert o.q.get_reservation(o.placed["reservation"]["key"]).driver == "ordinary-bridge"
    assert "ordinary-bridge" in pool_startup_contract()["supported_drivers"]


def test_pool_no_pair_is_applied_by_the_existing_allocation_owner(ordinary: SimpleNamespace) -> None:
    o = ordinary
    seed = startup_seed(o.task, o.client, route=" --shared")
    ordinary_bridge.start(
        o.task, client=o.client, agent="local", project="example", prompt=seed, no_pair=True,
    )
    assert o.allocations[0]["no_pair"] is True
    assert "--worktree-id" in o.calls[0][0]


def test_ambiguous_allocation_stops_before_bridge_without_another_key(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import embody

    o = ordinary

    def unavailable(**kwargs: Any) -> dict:
        assert kwargs["creation_key"] == o.placed["reservation"]["creation_key"]
        raise embody.EmbodyUnavailable("original keyed owner outcome is unconfirmed")

    monkeypatch.setattr(embody, "create_worktree", unavailable)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="unconfirmed"):
        launch(o)
    assert not o.calls
    assert o.q.get(o.task["id"]).status == Status.CLAIMED
    assert o.q.get_reservation(o.placed["reservation"]["key"]).creation_key == (
        o.placed["reservation"]["creation_key"]
    )


@pytest.mark.parametrize("failure", ["create-reply", "uncommitted-storage"])
def test_owned_native_recovers_same_key_after_lost_reply_or_storage(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    o = ordinary
    run = ordinary_bridge.subprocess.run
    store = o.client.record_created_agent
    failed = False

    def create(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal failed
        result = run(argv, **kwargs)
        if failure == "create-reply" and argv[2] == "create" and not failed:
            failed = True
            raise subprocess.TimeoutExpired(argv, 180)
        return result

    def storage(*args: Any, **kwargs: Any) -> dict:
        nonlocal failed
        if failure == "uncommitted-storage" and not failed:
            failed = True
            raise DispatchError(502, "storage did not commit")
        return store(*args, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", create)
    monkeypatch.setattr(o.client, "record_created_agent", storage)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        launch(o)
    original = o.q.get_reservation(o.placed["reservation"]["key"])
    assert original.agent_info is None and original.worktree_ownership == "created"
    assert o.ground["session_live"] is True and not any(
        argv[2] == "send" for argv, _ in o.calls
    )
    result = launch(o)
    assert result["native_session_id"] == "native-a"
    assert len(o.allocations) == 1
    assert [argv[2] for argv, _ in o.calls] == ["create", "create", "send"]
    assert all(
        argv[argv.index("--creation-key") + 1] == original.creation_key
        for argv, _ in o.calls if argv[2] == "create"
    )
    assert o.q.get_reservation(original.key).agent_info["connection"]["native_session_id"] == "native-a"


def test_owned_creation_rechecks_hold_after_hosting_lookup(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import hosting_policy, ordinary_pool_startup

    o = ordinary
    key = o.placed["reservation"]["key"]
    original = ordinary_pool_startup.prepare_owned_workspace(
        o.task, o.client.get_reservation(key), client=o.client, project="example",
        machine="host-a", no_pair=False, timeout=180,
    )
    assert original["worktree_ownership"] == "created" and original["agent_info"] is None

    def hosting(**kwargs: Any) -> None:
        assert kwargs["worktree_id"] == original["worktree"]
        o.q.set_hold(o.task["id"], reason="operator", actor="operator")

    monkeypatch.setattr(hosting_policy, "require_execution_harness", hosting)
    with pytest.raises(ordinary_bridge.BridgeCarriedSessionBusy, match="admission fence"):
        launch(o)
    retained = o.q.get_reservation(key)
    assert not o.calls and len(o.allocations) == 1
    assert retained.agent_info is None and retained.worktree_ownership == "created"
    assert retained.creation_key == original["creation_key"]
    assert o.ground["session_count"] == 0


@pytest.mark.parametrize("failure", ["foreign-holder", "changed-admission"])
def test_owned_creation_does_not_resume_or_prompt_an_unproven_holder(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    o = ordinary
    run = ordinary_bridge.subprocess.run

    def refuse(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result = run(argv, **kwargs)
        assert argv[2] == "create"
        if failure == "foreign-holder":
            return subprocess.CompletedProcess(argv, 1, "", "foreign_live_holder")
        value = json.loads(result.stdout)
        value["creation"]["native_session_id"] = "foreign-native"
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", refuse)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        launch(o)
    assert [argv[2] for argv, _ in o.calls] == ["create"]
    assert o.q.get_reservation(o.placed["reservation"]["key"]).agent_info is None
    assert o.q.get(o.task["id"]).status == Status.CLAIMED


def test_ordinary_worker_decline_keeps_distinct_bridge_and_native_custody(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import __main__ as cli, pool_worker_decline_cli

    o = ordinary
    result = launch(o)
    key = o.placed["reservation"]["key"]
    o.client.record_spawn(
        key, session_handle=result["session"], worktree=result["worktree"],
        startup_complete=True, startup_revision=1, session_info=result["session_info"],
    )
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "native-a")
    monkeypatch.setattr(cli, "_identity", lambda args: ("host-a", "worktree-a"))
    monkeypatch.setattr(cli, "_client", lambda *a, **k: controls.connected(o.api, [], control=False))
    own_finalizations = []
    monkeypatch.setattr(pool_worker_decline_cli, "_finalize",
                        lambda binding, worktree: own_finalizations.append((binding, worktree)))
    args = cli.build_parser().parse_args([
        "--shared", "decline", o.task["id"], "--pool-generation", "1",
        "--owner-session-id", "native-a", "--exclude-self", "worktree",
        "--note", "This worker lacks the required dependency.",
    ])
    assert args.func(args) == 0
    assert own_finalizations == [({
        "task_id": o.task["id"], "generation": 1, "startup_revision": 1,
        "reservation_key": key, "native_id": "native-a",
    }, "worktree-a")]
    assert o.q.pool_worker_decline(o.task["id"]).native_id == "native-a"
    assert o.q.get_reservation(key).session_handle == "local-body:bridge-a"
    assert o.q.get(o.task["id"]).status == Status.CLAIMED


@pytest.mark.parametrize("machine", [None, "host-b"])
def test_wrong_or_unknown_launch_host_never_creates_or_sends(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, machine: str | None,
) -> None:
    monkeypatch.setattr(identity, "resolve_machine", lambda: machine)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="assigned execution machine"):
        launch(ordinary)
    assert not ordinary.calls
    assert ordinary.q.get(ordinary.task["id"]).status == Status.CLAIMED
    assert ordinary.q.get_reservation(ordinary.placed["reservation"]["key"]).session_handle is None


@pytest.mark.parametrize("binding", ["machine", "supervisor.env"])
def test_installed_binding_admits_neutral_service_start_without_project_discovery(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, binding: str,
) -> None:
    value = "HOST-A\n" if binding == "machine" else "AGENT_DISPATCH_SUPERVISE_MACHINE='HOST-A'\n"
    (ordinary.install_root / binding).write_text(value, encoding="utf-8")
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISE_MACHINE", "ambient-other-host")
    monkeypatch.chdir(ordinary.install_root)

    def no_project() -> str:
        raise AssertionError("durable service identity must not probe a project")

    monkeypatch.setattr(identity, "resolve_machine", no_project)
    assert launch(ordinary)["session"] == "local-body:bridge-a"
    assert [argv[2] for argv, _ in ordinary.calls] == ["create", "send"]
    stored = ordinary.q.get_reservation(ordinary.placed["reservation"]["key"])
    assert stored.execution_machine == "host-a"


@pytest.mark.parametrize("machine", ["host-b", "host-a-wsl"])
def test_installed_binding_mismatch_does_not_fall_back_to_matching_project(
    ordinary: SimpleNamespace, machine: str,
) -> None:
    (ordinary.install_root / "machine").write_text(machine, encoding="utf-8")
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="assigned execution machine"):
        launch(ordinary)
    assert not ordinary.calls
    assert ordinary.q.get(ordinary.task["id"]).status == Status.CLAIMED


@pytest.mark.parametrize("failure", ["directory", "invalid-utf8"])
def test_unreadable_installed_binding_does_not_fall_back_or_create(
    ordinary: SimpleNamespace, failure: str,
) -> None:
    path = ordinary.install_root / "machine"
    if failure == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"\xff")
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="binding is unreadable"):
        launch(ordinary)
    assert not ordinary.calls
    assert ordinary.q.get_reservation(ordinary.placed["reservation"]["key"]).session_handle is None


@pytest.mark.parametrize("change", ["generation", "hold", "assignment", "driver", "startup"])
def test_changed_incarnation_refuses_before_native_creation(ordinary: SimpleNamespace, change: str) -> None:
    o = ordinary
    if change == "generation":
        o.task["generation"] += 1
    elif change == "hold":
        o.q.set_hold(o.task["id"], reason="operator hold", actor="operator")
    else:
        with o.q._connect() as conn:
            if change == "assignment":
                conn.execute("UPDATE task_pool_assignments SET task_generation=2 WHERE task_id=?", (o.task["id"],))
            elif change == "startup":
                conn.execute("UPDATE spawn_reservations SET startup_revision=2 WHERE key=?",
                             (o.placed["reservation"]["key"],))
            else:
                conn.execute("UPDATE spawn_reservations SET driver='cli' WHERE key=?", (o.placed["reservation"]["key"],))
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        launch(o)
    assert not o.calls
    assert o.q.get_reservation(o.placed["reservation"]["key"]).agent_info is None


@pytest.mark.parametrize("contract_change", [
    {"supported_drivers": ["cli"]}, {"ordinary_initial_start_versions": []},
    {"ordinary_initial_start_versions": [True]}, {"ordinary_initial_start_versions": [2]},
])
def test_old_partial_or_rolled_back_contract_never_issues_ordinary_placement(contract_change: dict) -> None:
    requests = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={**pool_placement_contract(), **contract_change})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(send)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.place_pool_task("task", reserved_by="allocator", catalog_revision=3, driver="ordinary-bridge")
    assert len(requests) == 1 and requests[0].method == "GET"


def test_rollback_between_seed_and_start_never_creates(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    seed = startup_seed(o.task, o.client)
    monkeypatch.setattr(o.client, "pool_placement_contract", lambda: {
        **pool_placement_contract(), "supported_drivers": ["cli"],
    })
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="unqualified"):
        ordinary_bridge.start(o.task, client=o.client, agent="local", project="example", prompt=seed)
    assert not o.calls and o.q.get(o.task["id"]).status == Status.CLAIMED


def test_ordinary_pool_claim_is_retained_not_legacy_recovered(ordinary: SimpleNamespace) -> None:
    o = ordinary
    reservation = asdict(o.q.get_reservation(o.placed["reservation"]["key"]))
    owner = SimpleNamespace(client=o.client)
    before = o.q.get(o.task["id"]), o.q.get_reservation(reservation["key"])
    assert supervise_pool_startup(owner, reservation, o.task, now=10000) is False
    assert (o.q.get(o.task["id"]), o.q.get_reservation(reservation["key"])) == before
    assert not o.calls


def test_assignment_read_and_reopen_preserve_pool_native_facts(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    launch(o)
    assert current_task(o.client, o.task)["status"] == Status.CLAIMED
    reopened = type(o.q)(o.q.db_path)
    monkeypatch.setattr(reopened, "_now", o.q._now)
    facts = reservation_native_facts(reopened, o.placed["reservation"]["key"])
    assert facts["reservation_pool_bound"] is True
    assert facts["reservation_pool_assignment"] == o.placed["assignment"]
    assert facts["reservation_native_session_id"] == "native-a"
    assert facts["ordinary_assignment_error"] is None


@pytest.mark.parametrize("complete", [False, True])
def test_ordinary_initial_delivery_keeps_its_pool_slot(ordinary: SimpleNamespace, complete: bool) -> None:
    o = ordinary
    result = launch(o)
    if complete:
        o.client.record_spawn(
            o.placed["reservation"]["key"], session_handle=result["session"], worktree=result["worktree"],
            startup_complete=True, startup_revision=1, session_info=result["session_info"],
        )
    before = o.q.get_reservation(o.placed["reservation"]["key"])
    another = task(o.q)
    with pytest.raises(DispatchError, match="capacity"):
        o.client.place_pool_task(
            another.id, reserved_by="other-allocator", catalog_revision=o.q.pool_catalog_snapshot().revision,
            driver="ordinary-bridge",
        )
    assert o.q.get_reservation(before.key) == before
    assert o.q.get(another.id).status == Status.QUEUED and o.q.task_pool_assignment(another.id) is None


@pytest.mark.parametrize("replacement", ["cli", "restricted-bridge", None])
def test_pool_workspace_cannot_relabel_the_placed_driver(
    ordinary: SimpleNamespace, replacement: str | None,
) -> None:
    o = ordinary
    before = o.q.get_reservation(o.placed["reservation"]["key"])
    with pytest.raises(TaskError, match="placed startup driver"):
        o.q.record_spawn_worktree(before.key, "worktree", driver=replacement, execution_machine="host-a")
    assert o.q.get_reservation(before.key) == before


def test_exact_initial_receipt_replay_survives_worker_acceptance(ordinary: SimpleNamespace) -> None:
    o = ordinary
    result = launch(o)
    original = json.loads(o.calls[-1][1]["input"])
    o.client.start_pool_task(o.task["id"], "host-a/worktree-a",
                            owner_session_id="native-a", expected_generation=1)
    o.calls.clear()
    assert launch(o) == result
    assert len(o.calls) == 1 and "--receipt-only" in o.calls[0][0]
    assert json.loads(o.calls[0][1]["input"]) == original
    assert o.q.get(o.task["id"]).status == Status.STARTED
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 0


@pytest.mark.parametrize("direction", ["upgrade", "rollback"])
def test_initial_receipt_recovery_preserves_released_prompt_across_versions(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, direction: str,
) -> None:
    from agent_dispatch import embody_prompts, ordinary_invocation

    o = ordinary
    current = embody_prompts.pool_worker_prompt
    prepare = ordinary_invocation.prepare

    def released_reader(task: dict, reservation: dict, info: dict, prompt: str, **kwargs: Any) -> dict:
        # The pre-change reader regenerated and compared the full initial invocation.
        original = info.get("startup_invocation")
        generated = prepare(task, reservation, {k: v for k, v in info.items() if k != "startup_invocation"},
                            prompt, **kwargs)
        if original is not None and not ordinary_invocation.same_facts(original, generated):
            raise TaskError("ordinary startup invocation facts or exact prompt changed")
        return generated

    monkeypatch.setattr(embody_prompts, "pool_worker_prompt",
                        released_pool_seed if direction == "upgrade" else current)
    monkeypatch.setattr(ordinary_invocation, "prepare", released_reader if direction == "upgrade" else prepare)
    seed = startup_seed(o.task, o.client, route=" --shared")
    result = launch(o)
    original = json.loads(o.calls[-1][1]["input"])
    o.client.start_pool_task(o.task["id"], "host-a/worktree-a",
                            owner_session_id="native-a", expected_generation=1)
    before = o.q.get_reservation(o.placed["reservation"]["key"]), o.q.get(o.task["id"])
    o.calls.clear()
    monkeypatch.setattr(embody_prompts, "pool_worker_prompt",
                        current if direction == "upgrade" else released_pool_seed)
    monkeypatch.setattr(ordinary_invocation, "prepare", prepare if direction == "upgrade" else released_reader)
    assert startup_seed(o.task, o.client, route=" --shared") == seed
    assert launch(o) == result
    assert len(o.calls) == 1 and "--receipt-only" in o.calls[0][0]
    assert json.loads(o.calls[0][1]["input"]) == original
    assert (o.q.get_reservation(o.placed["reservation"]["key"]), o.q.get(o.task["id"])) == before


def test_committed_storage_unknown_retains_native_without_sending(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    original = o.client.record_created_agent

    def lose_reply(*args: Any, **kwargs: Any) -> dict:
        original(*args, **kwargs)
        raise DispatchError(502, "committed response lost")

    monkeypatch.setattr(o.client, "record_created_agent", lose_reply)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="no prompt sent"):
        launch(o)
    assert [argv[2] for argv, _ in o.calls] == ["create"]
    retained = o.q.get_reservation(o.placed["reservation"]["key"])
    assert retained.session_handle == "local-body:bridge-a" and retained.execution_machine == "host-a"
    assert o.q.get(o.task["id"]).status == Status.CLAIMED
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 1


@pytest.mark.parametrize("machine", [None, "", "host-b", True])
def test_storage_cannot_invent_the_assigned_host(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, machine: object,
) -> None:
    o = ordinary
    original = o.client.record_created_agent

    def wrong_host(*args: Any, **kwargs: Any) -> dict:
        return original(*args, **{**kwargs, "execution_machine": machine})

    monkeypatch.setattr(o.client, "record_created_agent", wrong_host)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        launch(o)
    assert len(o.calls) == 1
    assert o.q.get_reservation(o.placed["reservation"]["key"]).agent_info is None


def test_storage_cannot_convert_a_cli_assignment(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    original = o.client.record_created_agent

    def change_driver(*args: Any, **kwargs: Any) -> dict:
        with o.q._connect() as conn:
            conn.execute("UPDATE spawn_reservations SET driver='cli' WHERE key=?", (args[0],))
        return original(*args, **kwargs)

    monkeypatch.setattr(o.client, "record_created_agent", change_driver)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="driver changed"):
        launch(o)
    retained = o.q.get_reservation(o.placed["reservation"]["key"])
    assert len(o.calls) == 1 and retained.agent_info is None and retained.driver == "cli"


def test_storage_deadline_cannot_deliver_a_late_first_prompt(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    original = o.client.record_created_agent

    def expired(*args: Any, **kwargs: Any) -> dict:
        monkeypatch.setattr(o.q, "_now", lambda now=None: 700.0 if now is None else now)
        return original(*args, **kwargs)

    monkeypatch.setattr(o.client, "record_created_agent", expired)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="deadline expired"):
        launch(o)
    retained = o.q.get_reservation(o.placed["reservation"]["key"])
    assert len(o.calls) == 1 and retained.agent_info is None
    assert o.q.get(o.task["id"]).status == Status.CLAIMED
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 1


@pytest.mark.parametrize("now", [700.0, 701.0])
def test_initially_expired_claim_runs_no_native_command(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, now: float,
) -> None:
    o = ordinary
    instant = now
    monkeypatch.setattr(o.q, "_now", lambda now=None: instant if now is None else now)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="deadline expired"):
        launch(o)
    assert not o.calls
    retained = o.q.get_reservation(o.placed["reservation"]["key"])
    assert retained.worktree is None and retained.agent_info is None and retained.session_handle is None
    assert o.q.get(o.task["id"]).status == Status.CLAIMED
    facts = o.client.get_reservation(retained.key)
    assert facts["ordinary_assignment_error"] == "pool start decision deadline expired"
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 1


def test_initial_claim_just_before_deadline_still_runs(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ordinary.q, "_now", lambda now=None: 699.0 if now is None else now)
    assert launch(ordinary)["native_session_id"] == "native-a"
    assert [argv[2] for argv, _ in ordinary.calls] == ["create", "send"]


def test_expired_retained_claim_observes_receipt_but_never_resumes(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    launch(o)
    o.calls.clear()
    monkeypatch.setattr(o.q, "_now", lambda now=None: 700.0 if now is None else now)

    def missing_receipt(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        o.calls.append((argv, kwargs))
        assert argv[2] == "send" and "--receipt-only" in argv
        return subprocess.CompletedProcess(argv, 69, json.dumps({
            "outcome": "refused_unavailable", "reason": "native_receipt_missing",
        }), "")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", missing_receipt)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="deadline expired"):
        launch(o)
    assert len(o.calls) == 1 and "--receipt-only" in o.calls[0][0]
    assert o.q.get_reservation(o.placed["reservation"]["key"]).session_handle == "local-body:bridge-a"
