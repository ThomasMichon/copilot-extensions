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
    tmp_path: Path,
) -> Iterator[SimpleNamespace]:
    revision = publish(q, pool())
    requested = task(q)
    requests, calls = [], []
    client = controls.connected(api, requests)
    placed = client.place_pool_task(
        requested.id, reserved_by="allocator", catalog_revision=revision, driver="ordinary-bridge",
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
    ground = {
        "id": "worktree-a", "repo": "example", "path": str(admission_owner.workspace),
        "status": "active", "creation_nonce": "allocation-a", "session_count": 0,
        "last_session_id": None, "execution_leg": None, "execution_leg_opaque": False,
        "session_backend": None, "session_backend_opaque": False, "sessions_indexed": True,
        "dispatch_attempt_opaque": False,
        "dispatch_attempt": {"task_id": requested.id},
    }

    def create_workspace(**kwargs: Any) -> dict:
        assert q.get(requested.id).status == Status.CLAIMED and not calls
        allocations.append(kwargs)
        return {
            "worktree": ground["id"], "project": ground["repo"], "path": ground["path"],
        }

    monkeypatch.setattr(embody, "create_worktree", create_workspace)
    monkeypatch.setattr(ordinary_assignment, "read_owner", lambda *a, **k: dict(ground))

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        if argv[2] == "create":
            assert not kwargs.get("input") and "--prompt-file" not in argv
            assert q.get(requested.id).status == Status.CLAIMED
            ground.update(session_count=1, last_session_id="native-a", session_live=True)
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
                "creation": {"worktree": {"creation_nonce": "allocation-a"}},
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
    )
    client.close()


def launch(ordinary: SimpleNamespace) -> dict:
    seed = startup_seed(ordinary.task, ordinary.client, route=" --shared")
    return ordinary_bridge.start(
        ordinary.task, client=ordinary.client, agent="local", project="example", prompt=seed,
    )


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
